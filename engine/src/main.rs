// Copyright (C) 2026 2351485 ALBERTA INC.
// SPDX-License-Identifier: AGPL-3.0-or-later

use anyhow::Result;
use axum::{
    body::Body,
    extract::{Request, State},
    http::{header, HeaderValue, Method, StatusCode, Uri},
    response::{IntoResponse, Response},
    routing::{any, delete, get, post, put},
    Router,
};
use std::net::SocketAddr;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Duration;
use tokio::io::{AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader};
use tokio::net::TcpStream;
use tower_http::cors::CorsLayer;
use tower_http::trace::TraceLayer;
use tracing_subscriber::{layer::SubscriberExt, util::SubscriberInitExt};

mod api;
mod crypto;
mod num_ffi;
mod risk;
mod storage;
mod versioning;

/// Restrictive CORS layer: the engine serves the local PAE UI, not the
/// open web. The allowed origin is configurable via `PAE_CORS_ORIGIN`
/// (default `http://localhost:3000`); only GET/POST with a JSON content
/// type are accepted.
///
/// Replaces the old `CorsLayer::permissive()`, which left the key+data
/// oracle endpoints (/encrypt, /decrypt) reachable from any origin.
fn cors_layer() -> CorsLayer {
    let origin: HeaderValue = std::env::var("PAE_CORS_ORIGIN")
        .unwrap_or_else(|_| "http://localhost:3000".to_string())
        .parse()
        .unwrap_or_else(|_| HeaderValue::from_static("http://localhost:3000"));
    CorsLayer::new()
        .allow_origin(origin)
        .allow_methods([Method::GET, Method::POST])
        .allow_headers([header::CONTENT_TYPE])
}

/// Runtime wiring that `main` takes from the environment and the
/// HTTP-layer integration tests set directly.
struct AppConfig {
    /// Directory of built dashboard files served at `/` (`PAE_UI_DIR`).
    /// The native bundle sets this so the engine serves the dashboard
    /// itself — the product is a single origin, no separate web server.
    /// None = API only (development, tests).
    ui_dir: Option<PathBuf>,
    /// Base URL of the analytics service (`PAE_ANALYTICS_URL`), target
    /// of the /api/v1/analytics and /api/v1/dashboard proxy routes.
    analytics_base: String,
}

impl AppConfig {
    fn from_env() -> Self {
        Self {
            ui_dir: std::env::var("PAE_UI_DIR")
                .ok()
                .filter(|s| !s.is_empty())
                .map(PathBuf::from),
            analytics_base: std::env::var("PAE_ANALYTICS_URL")
                .unwrap_or_else(|_| "http://127.0.0.1:3002".to_string()),
        }
    }
}

/// Build the Axum router from the process environment.
/// Used by the HTTP-layer integration tests; `main` builds via
/// [`build_app`] so it can log the resolved configuration first.
#[allow(dead_code)]
fn create_app(store: Arc<storage::Store>) -> Router {
    build_app(store, &AppConfig::from_env())
}

/// Build the Axum router. Factored out of `main` so the HTTP-layer
/// integration tests below can construct the app without binding a socket.
fn build_app(store: Arc<storage::Store>, cfg: &AppConfig) -> Router {
    // Routes backed by the versioning store. The store is persistent
    // (Phase 3B): history is reloaded from the SQLite database and
    // every append is written through, so the decision journal and
    // other versioned records survive engine restarts. If the reload
    // fails the engine still starts — with an empty in-memory store
    // and a loud error in the log — rather than refusing to serve.
    let version_store = Arc::new(
        versioning::store::VersionStore::open_persistent(store.clone()).unwrap_or_else(|e| {
            tracing::error!(
                error = %e,
                "failed to load version records from the database; \
                 version history is in-memory only for this run"
            );
            versioning::store::VersionStore::new()
        }),
    );
    let versioned_routes = Router::new()
        .route("/api/v1/version", post(api::versioning_api::append_version))
        .route("/api/v1/version/history", post(api::versioning_api::get_history))
        .route("/api/v1/version/integrity/{entity_id}", get(api::versioning_api::verify_integrity))
        .route("/api/v1/version/snapshot", post(api::versioning_api::get_snapshot))
        .route("/api/v1/version/diff", post(api::versioning_api::get_diff))
        .with_state(version_store);

    // Routes backed by the SQLite persistence store (holdings, portfolios, import)
    let storage_routes = Router::new()
        .route("/api/v1/holdings", get(api::holdings_api::list_holdings))
        .route("/api/v1/holdings", post(api::holdings_api::create_holding))
        .route("/api/v1/holdings/{id}", put(api::holdings_api::update_holding))
        .route("/api/v1/holdings/{id}", delete(api::holdings_api::delete_holding))
        .route("/api/v1/portfolios", get(api::holdings_api::list_portfolios))
        .route("/api/v1/portfolios", post(api::holdings_api::create_portfolio))
        .route("/api/v1/import/csv", post(api::import_api::import_csv))
        .route("/api/v1/import/confirm", post(api::import_api::confirm_import))
        .with_state(store.clone());

    // Crypto routes. NOTE (2026-10-05, zero-knowledge fix):
    // POST /api/v1/crypto/derive-key was DELETED — it took a raw
    // passphrase server-side, inverting the threat model. Key derivation
    // now happens exclusively client-side (ui/src/crypto/vault-client.ts)
    // against the parameters served by GET /api/v1/crypto/kdf-params.
    // /encrypt and /decrypt remain key+data oracles BY DESIGN for
    // local-first deployment (caller supplies key+data); see
    // docs/THREAT_MODEL.md for the hosted-deployment caveat.
    let crypto_routes = Router::new()
        .route("/api/v1/crypto/kdf-params", get(api::crypto_api::kdf_params))
        .route("/api/v1/crypto/encrypt", post(api::crypto_api::encrypt))
        .route("/api/v1/crypto/decrypt", post(api::crypto_api::decrypt))
        .route("/api/v1/crypto/dek-envelope", get(api::crypto_api::get_dek_envelope))
        .route("/api/v1/crypto/dek-envelope", post(api::crypto_api::set_dek_envelope))
        .with_state(store);

    // Routes without shared state (stateless compute)
    let stateless_routes = Router::new()
        .route("/health", get(api::health::check))
        .route("/api/v1/portfolio/risk", post(api::portfolio::compute_risk))
        .route("/api/v1/portfolio/metrics", post(api::portfolio::compute_metrics))
        .route("/api/v1/portfolio/stress", post(api::portfolio::stress_test))
        .route("/api/v1/portfolio/correlation", post(api::portfolio::correlation_matrix))
        .route("/api/v1/portfolio/montecarlo", post(api::portfolio::monte_carlo))
        .route("/api/v1/analytics/bond", post(api::bonds::bond_analytics));

    let app = Router::new()
        .merge(versioned_routes)
        .merge(storage_routes)
        .merge(crypto_routes)
        .merge(stateless_routes)
        .merge(analytics_proxy_routes(cfg.analytics_base.clone()));

    // When a UI directory is configured, the engine serves the
    // dashboard itself as the fallback for unmatched paths — the
    // product is a single origin, no separate web server. API paths
    // never fall through to the UI (see serve_ui), so unknown API
    // routes keep returning 404.
    let app = match &cfg.ui_dir {
        Some(dir) => app.fallback_service(ui_service(dir.clone())),
        None => app,
    };

    app.layer(cors_layer()).layer(TraceLayer::new_for_http())
}

// ---------------------------------------------------------------------------
// Dashboard static files (PAE_UI_DIR)
// ---------------------------------------------------------------------------

/// Fallback service that serves the built dashboard from `dir`.
fn ui_service(dir: PathBuf) -> axum::routing::MethodRouter {
    any(serve_ui).with_state(Arc::new(dir))
}

/// Serve one dashboard file, or `index.html` for client-side routes.
///
/// Rules: GET/HEAD only; `/api/` and `/health` never resolve to UI
/// files; any `..` path segment is rejected; an existing file is
/// served as-is; an extensionless path that is not a file is treated
/// as a client-side route and gets `index.html` (SPA fallback); a
/// missing path *with* an extension is a real 404.
async fn serve_ui(State(root): State<Arc<PathBuf>>, method: Method, uri: Uri) -> Response {
    if method != Method::GET && method != Method::HEAD {
        return StatusCode::NOT_FOUND.into_response();
    }
    let raw_path = uri.path();
    if raw_path.starts_with("/api/") || raw_path == "/health" {
        return StatusCode::NOT_FOUND.into_response();
    }
    let rel = raw_path.trim_start_matches('/');
    if rel.split('/').any(|seg| seg == "..") {
        return StatusCode::NOT_FOUND.into_response();
    }
    let root = root.as_path();
    let head_only = method == Method::HEAD;
    if rel.is_empty() {
        return serve_file(&root.join("index.html"), head_only).await;
    }
    let target = root.join(rel);
    if target.is_file() {
        return serve_file(&target, head_only).await;
    }
    let last_segment = rel.rsplit('/').next().unwrap_or("");
    if !last_segment.contains('.') {
        let index = root.join("index.html");
        if index.is_file() {
            return serve_file(&index, head_only).await;
        }
    }
    StatusCode::NOT_FOUND.into_response()
}

async fn serve_file(path: &Path, head_only: bool) -> Response {
    match tokio::fs::read(path).await {
        Ok(bytes) => {
            let len = bytes.len();
            let body = if head_only {
                Body::empty()
            } else {
                Body::from(bytes)
            };
            Response::builder()
                .status(StatusCode::OK)
                .header(header::CONTENT_TYPE, content_type_for(path))
                .header(header::CONTENT_LENGTH, len)
                .body(body)
                .expect("static response is well-formed")
        }
        Err(_) => StatusCode::NOT_FOUND.into_response(),
    }
}

fn content_type_for(path: &Path) -> &'static str {
    match path.extension().and_then(|e| e.to_str()) {
        Some("html") => "text/html; charset=utf-8",
        Some("js") | Some("mjs") => "text/javascript; charset=utf-8",
        Some("css") => "text/css; charset=utf-8",
        Some("json") | Some("map") => "application/json",
        Some("webmanifest") => "application/manifest+json",
        Some("svg") => "image/svg+xml",
        Some("png") => "image/png",
        Some("jpg") | Some("jpeg") => "image/jpeg",
        Some("gif") => "image/gif",
        Some("ico") => "image/x-icon",
        Some("woff") => "font/woff",
        Some("woff2") => "font/woff2",
        Some("ttf") => "font/ttf",
        Some("wasm") => "application/wasm",
        Some("txt") => "text/plain; charset=utf-8",
        _ => "application/octet-stream",
    }
}

// ---------------------------------------------------------------------------
// Analytics reverse proxy (/api/v1/analytics/*, /api/v1/dashboard/*)
// ---------------------------------------------------------------------------

#[derive(Clone)]
struct AnalyticsProxy {
    /// e.g. "http://127.0.0.1:3002" — plain HTTP; the analytics
    /// service is a loopback companion process, never a remote host.
    base: String,
}

/// Routes the dashboard needs from the analytics service, proxied
/// through the engine origin so the product is same-origin. The
/// engine's own `/api/v1/analytics/bond` route is an exact match and
/// takes precedence over the wildcard below.
fn analytics_proxy_routes(base: String) -> Router {
    Router::new()
        .route("/api/v1/analytics/{*rest}", any(proxy_to_analytics))
        .route("/api/v1/dashboard/{*rest}", any(proxy_to_analytics))
        .with_state(AnalyticsProxy { base })
}

async fn proxy_to_analytics(State(proxy): State<AnalyticsProxy>, req: Request) -> Response {
    let (parts, body) = req.into_parts();
    let body_bytes = match axum::body::to_bytes(body, 64 * 1024 * 1024).await {
        Ok(b) => b,
        Err(_) => {
            return (StatusCode::PAYLOAD_TOO_LARGE, "request body too large").into_response();
        }
    };
    let path_and_query = parts
        .uri
        .path_and_query()
        .map(|pq| pq.as_str().to_string())
        .unwrap_or_else(|| parts.uri.path().to_string());
    match forward_http(
        &proxy.base,
        &parts.method,
        &path_and_query,
        &parts.headers,
        &body_bytes,
    )
    .await
    {
        Ok(resp) => resp,
        Err(e) => Response::builder()
            .status(StatusCode::BAD_GATEWAY)
            .header(header::CONTENT_TYPE, "application/json")
            .body(Body::from(
                serde_json::json!({"error": format!("analytics service unavailable: {e}")})
                    .to_string(),
            ))
            .expect("gateway response is well-formed"),
    }
}

/// Hop-by-hop headers are never forwarded in either direction.
fn is_hop_by_hop(name: &str) -> bool {
    matches!(
        name.to_ascii_lowercase().as_str(),
        "host"
            | "connection"
            | "content-length"
            | "transfer-encoding"
            | "keep-alive"
            | "proxy-authorization"
            | "proxy-connection"
            | "te"
            | "trailer"
            | "upgrade"
    )
}

fn parse_http_authority(base: &str) -> Option<(String, String, u16)> {
    let rest = base.strip_prefix("http://")?;
    let authority = rest.split('/').next()?;
    let (host, port) = match authority.split_once(':') {
        Some((h, p)) => (h.to_string(), p.parse().ok()?),
        None => (authority.to_string(), 80),
    };
    Some((authority.to_string(), host, port))
}

/// Minimal HTTP/1.1 forward over a raw TCP stream. The upstream is
/// the loopback analytics service, so a full HTTP client crate would
/// be dead weight; this handles Content-Length and chunked responses
/// and nothing else.
async fn forward_http(
    base: &str,
    method: &Method,
    path_and_query: &str,
    headers: &header::HeaderMap,
    body: &[u8],
) -> Result<Response, String> {
    let (authority, host, port) = parse_http_authority(base)
        .ok_or_else(|| format!("unsupported analytics base URL '{base}' (http:// only)"))?;

    let stream = tokio::time::timeout(
        Duration::from_secs(5),
        TcpStream::connect((host.as_str(), port)),
    )
    .await
    .map_err(|_| format!("connect to analytics at {authority} timed out"))?
    .map_err(|e| format!("connect to analytics at {authority}: {e}"))?;
    let (read_half, mut write_half) = stream.into_split();

    let mut raw = Vec::with_capacity(body.len() + 512);
    raw.extend_from_slice(format!("{method} {path_and_query} HTTP/1.1\r\n").as_bytes());
    raw.extend_from_slice(format!("Host: {authority}\r\n").as_bytes());
    for (name, value) in headers.iter() {
        if is_hop_by_hop(name.as_str()) {
            continue;
        }
        if let Ok(v) = value.to_str() {
            raw.extend_from_slice(format!("{name}: {v}\r\n").as_bytes());
        }
    }
    raw.extend_from_slice(format!("Content-Length: {}\r\n", body.len()).as_bytes());
    raw.extend_from_slice(b"Connection: close\r\n\r\n");
    raw.extend_from_slice(body);
    write_half
        .write_all(&raw)
        .await
        .map_err(|e| format!("write to analytics: {e}"))?;
    write_half
        .flush()
        .await
        .map_err(|e| format!("flush to analytics: {e}"))?;

    tokio::time::timeout(
        Duration::from_secs(60),
        read_upstream_response(read_half),
    )
    .await
    .map_err(|_| "analytics response timed out".to_string())?
}

async fn read_upstream_response(
    read_half: tokio::net::tcp::OwnedReadHalf,
) -> Result<Response, String> {
    let mut reader = BufReader::new(read_half);

    let mut status_line = String::new();
    reader
        .read_line(&mut status_line)
        .await
        .map_err(|e| format!("read analytics status: {e}"))?;
    let status_code: u16 = status_line
        .split_whitespace()
        .nth(1)
        .and_then(|s| s.parse().ok())
        .ok_or_else(|| format!("malformed analytics status line: {status_line:?}"))?;

    let mut resp_headers: Vec<(String, String)> = Vec::new();
    loop {
        let mut line = String::new();
        let n = reader
            .read_line(&mut line)
            .await
            .map_err(|e| format!("read analytics headers: {e}"))?;
        if n == 0 || line == "\r\n" || line == "\n" {
            break;
        }
        if let Some((name, value)) = line.trim_end().split_once(':') {
            resp_headers.push((name.trim().to_string(), value.trim().to_string()));
        }
    }

    let header_get = |want: &str| -> Option<String> {
        resp_headers
            .iter()
            .find(|(n, _)| n.eq_ignore_ascii_case(want))
            .map(|(_, v)| v.clone())
    };

    let body: Vec<u8> = if header_get("transfer-encoding")
        .map(|v| v.to_ascii_lowercase().contains("chunked"))
        .unwrap_or(false)
    {
        read_chunked_body(&mut reader).await?
    } else if let Some(len) = header_get("content-length").and_then(|v| v.parse::<usize>().ok()) {
        let mut buf = vec![0u8; len];
        reader
            .read_exact(&mut buf)
            .await
            .map_err(|e| format!("read analytics body: {e}"))?;
        buf
    } else {
        let mut buf = Vec::new();
        reader
            .read_to_end(&mut buf)
            .await
            .map_err(|e| format!("read analytics body: {e}"))?;
        buf
    };

    let mut builder = Response::builder().status(
        StatusCode::from_u16(status_code).unwrap_or(StatusCode::BAD_GATEWAY),
    );
    for (name, value) in &resp_headers {
        if is_hop_by_hop(name) {
            continue;
        }
        builder = builder.header(name.as_str(), value.as_str());
    }
    builder
        .body(Body::from(body))
        .map_err(|e| format!("build proxied response: {e}"))
}

async fn read_chunked_body(
    reader: &mut BufReader<tokio::net::tcp::OwnedReadHalf>,
) -> Result<Vec<u8>, String> {
    let mut out = Vec::new();
    loop {
        let mut size_line = String::new();
        reader
            .read_line(&mut size_line)
            .await
            .map_err(|e| format!("read chunk size: {e}"))?;
        let size_str = size_line.trim().split(';').next().unwrap_or("0");
        let size = usize::from_str_radix(size_str, 16)
            .map_err(|_| format!("malformed chunk size: {size_line:?}"))?;
        if size == 0 {
            // Trailers (if any) end at the first empty line.
            loop {
                let mut trailer = String::new();
                let n = reader
                    .read_line(&mut trailer)
                    .await
                    .map_err(|e| format!("read chunk trailers: {e}"))?;
                if n == 0 || trailer == "\r\n" || trailer == "\n" {
                    break;
                }
            }
            break;
        }
        let start = out.len();
        out.resize(start + size, 0);
        reader
            .read_exact(&mut out[start..])
            .await
            .map_err(|e| format!("read chunk data: {e}"))?;
        let mut crlf = [0u8; 2];
        reader
            .read_exact(&mut crlf)
            .await
            .map_err(|e| format!("read chunk terminator: {e}"))?;
    }
    Ok(out)
}

#[tokio::main]
async fn main() -> Result<()> {
    dotenvy::dotenv().ok();

    tracing_subscriber::registry()
        .with(tracing_subscriber::EnvFilter::try_from_default_env()
            .unwrap_or_else(|_| "pae_engine=info,tower_http=info".into()))
        .with(tracing_subscriber::fmt::layer())
        .init();

    // Shared state: encrypted SQLite persistence layer (WAL mode).
    // Path is configurable so deployments can point at a persistent volume;
    // defaults to ~/.pae/pae.db, falling back to ./pae.db if HOME is unset.
    let db_path = std::env::var("PAE_DB_PATH").unwrap_or_else(|_| {
        match std::env::var("HOME") {
            Ok(home) => format!("{home}/.pae/pae.db"),
            Err(_) => "pae.db".to_string(),
        }
    });
    if let Some(parent) = std::path::Path::new(&db_path).parent() {
        std::fs::create_dir_all(parent).ok();
    }
    let store = Arc::new(
        storage::Store::open(&db_path)
            .map_err(|e| anyhow::anyhow!("Failed to open PAE database at {db_path}: {e}"))?,
    );
    tracing::info!("PAE storage initialized at {}", db_path);

    let cfg = AppConfig::from_env();
    if let Some(dir) = &cfg.ui_dir {
        tracing::info!("Serving dashboard from {}", dir.display());
    }
    tracing::info!(
        "Analytics proxy target: {} (override with PAE_ANALYTICS_URL)",
        cfg.analytics_base
    );
    let app = build_app(store, &cfg);

    // Bind loopback by default: the engine is a local-first companion
    // process. Set PAE_ENGINE_HOST=0.0.0.0 to expose it deliberately.
    let host: std::net::IpAddr = std::env::var("PAE_ENGINE_HOST")
        .unwrap_or_else(|_| "127.0.0.1".to_string())
        .parse()
        .map_err(|e| anyhow::anyhow!("Invalid PAE_ENGINE_HOST: {e}"))?;
    let port: u16 = std::env::var("PAE_ENGINE_PORT")
        .unwrap_or_else(|_| "3001".to_string())
        .parse()
        .map_err(|e| anyhow::anyhow!("Invalid PAE_ENGINE_PORT: {e}"))?;

    let addr = SocketAddr::from((host, port));
    tracing::info!("PAE Engine listening on {}", addr);

    let listener = tokio::net::TcpListener::bind(addr).await?;

    // Graceful shutdown on SIGINT/SIGTERM
    let shutdown_signal = async {
        let ctrl_c = tokio::signal::ctrl_c();
        #[cfg(unix)]
        let mut sigterm = tokio::signal::unix::signal(
            tokio::signal::unix::SignalKind::terminate(),
        ).expect("failed to register SIGTERM handler");

        #[cfg(unix)]
        tokio::select! {
            _ = ctrl_c => { tracing::info!("Received SIGINT, shutting down..."); }
            _ = sigterm.recv() => { tracing::info!("Received SIGTERM, shutting down..."); }
        }

        #[cfg(not(unix))]
        {
            ctrl_c.await.ok();
            tracing::info!("Received shutdown signal, shutting down...");
        }
    };

    axum::serve(listener, app)
        .with_graceful_shutdown(shutdown_signal)
        .await?;

    tracing::info!("PAE Engine shut down cleanly");
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum_test::TestServer;

    fn test_server() -> TestServer {
        let store = Arc::new(
            storage::Store::open_in_memory().expect("in-memory store for tests"),
        );
        TestServer::new(create_app(store)).expect("test server")
    }

    /// The passphrase-bearing derive-key endpoint must be gone entirely.
    #[tokio::test]
    async fn derive_key_endpoint_returns_404() {
        let server = test_server();
        let resp = server
            .post("/api/v1/crypto/derive-key")
            .json(&serde_json::json!({ "passphrase": "hunter2" }))
            .await;
        assert_eq!(
            resp.status_code().as_u16(),
            404,
            "POST /api/v1/crypto/derive-key must not exist"
        );
    }

    /// deny_unknown_fields: key-material fields on /encrypt are rejected.
    /// (axum 0.8 maps serde deserialization failures to 422.)
    #[tokio::test]
    async fn encrypt_rejects_key_material_fields() {
        let server = test_server();
        let base = serde_json::json!({
            "plaintext": "hello",
            "key_b64": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        });
        for field in ["passphrase", "kek", "dek", "unwrapped_dek"] {
            let mut body = base.clone();
            body[field] = serde_json::json!("must-not-be-accepted");
            let resp = server.post("/api/v1/crypto/encrypt").json(&body).await;
            assert_eq!(
                resp.status_code().as_u16(),
                422,
                "encrypt must reject field '{field}'"
            );
        }
    }

    /// deny_unknown_fields: key-material fields on /decrypt are rejected.
    #[tokio::test]
    async fn decrypt_rejects_key_material_fields() {
        let server = test_server();
        let base = serde_json::json!({
            "ciphertext_b64": "AA==",
            "nonce_b64": "AAAAAAAAAAAAAAAA",
            "key_b64": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        });
        for field in ["passphrase", "kek", "dek", "unwrapped_dek"] {
            let mut body = base.clone();
            body[field] = serde_json::json!("must-not-be-accepted");
            let resp = server.post("/api/v1/crypto/decrypt").json(&body).await;
            assert_eq!(
                resp.status_code().as_u16(),
                422,
                "decrypt must reject field '{field}'"
            );
        }
    }

    /// deny_unknown_fields: key-material fields on /dek-envelope are rejected.
    #[tokio::test]
    async fn dek_envelope_rejects_key_material_fields() {
        let server = test_server();
        for field in ["passphrase", "kek", "dek", "unwrapped_dek"] {
            let mut body = serde_json::json!({
                "envelope": "{\"v\":2,\"wrapped_dek_b64\":\"AA==\",\"dek_nonce_b64\":\"AAAAAAAAAAAAAAAA\"}",
            });
            body[field] = serde_json::json!("must-not-be-accepted");
            let resp = server
                .post("/api/v1/crypto/dek-envelope")
                .json(&body)
                .await;
            assert_eq!(
                resp.status_code().as_u16(),
                422,
                "dek-envelope must reject field '{field}'"
            );
        }
    }

    /// The oracle endpoints still work for well-formed bodies.
    #[tokio::test]
    async fn encrypt_accepts_valid_body() {
        let server = test_server();
        let resp = server
            .post("/api/v1/crypto/encrypt")
            .json(&serde_json::json!({
                "plaintext": "hello",
                "key_b64": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
            }))
            .await;
        assert_eq!(resp.status_code().as_u16(), 200);
        let body: serde_json::Value = resp.json();
        assert!(body.get("ciphertext_b64").is_some());
        assert!(body.get("nonce_b64").is_some());
    }

    /// kdf-params serves the production spec and a stable per-database salt.
    #[tokio::test]
    async fn kdf_params_shape_and_stable_salt() {
        let server = test_server();
        let r1 = server.get("/api/v1/crypto/kdf-params").await;
        assert_eq!(r1.status_code().as_u16(), 200);
        let p1: serde_json::Value = r1.json();
        assert_eq!(p1["algorithm"], "argon2id");
        assert_eq!(p1["version"], 19);
        assert_eq!(p1["memory_kib"], 65536);
        assert_eq!(p1["iterations"], 600000);
        assert_eq!(p1["parallelism"], 4);
        assert_eq!(p1["output_bytes"], 32);
        let salt1 = p1["salt_b64"].as_str().expect("salt_b64 is a string");

        let r2 = server.get("/api/v1/crypto/kdf-params").await;
        assert_eq!(r2.status_code().as_u16(), 200);
        let p2: serde_json::Value = r2.json();
        assert_eq!(
            p2["salt_b64"].as_str().unwrap(),
            salt1,
            "kdf salt must be stable across calls"
        );
    }

    /// GET /dek-envelope on a fresh vault returns {"envelope": null}.
    #[tokio::test]
    async fn dek_envelope_absent_returns_null() {
        let server = test_server();
        let resp = server.get("/api/v1/crypto/dek-envelope").await;
        assert_eq!(resp.status_code().as_u16(), 200);
        let body: serde_json::Value = resp.json();
        assert!(body["envelope"].is_null(), "fresh vault has no envelope");
    }

    /// POST stores the envelope verbatim; GET returns it back unchanged.
    /// The engine treats it as opaque bytes (round-trip integrity only).
    #[tokio::test]
    async fn dek_envelope_set_then_get_roundtrip() {
        let server = test_server();
        let envelope = "{\"v\":2,\"alg\":\"AES-256-GCM\",\"kid\":\"vault\",\
            \"wrapped_dek_b64\":\"QUJDREVGR0g=\",\"dek_nonce_b64\":\"AAAAAAAAAAAAAAAA\"}";
        let set_resp = server
            .post("/api/v1/crypto/dek-envelope")
            .json(&serde_json::json!({ "envelope": envelope }))
            .await;
        assert_eq!(set_resp.status_code().as_u16(), 204);

        let get_resp = server.get("/api/v1/crypto/dek-envelope").await;
        assert_eq!(get_resp.status_code().as_u16(), 200);
        let body: serde_json::Value = get_resp.json();
        assert_eq!(
            body["envelope"].as_str().expect("envelope is a string"),
            envelope,
            "envelope must round-trip verbatim"
        );
    }

    /// POST rejects malformed envelopes with 400 (not 422 — the body shape
    /// is fine, the envelope content is not).
    #[tokio::test]
    async fn dek_envelope_rejects_malformed() {
        let server = test_server();
        for (name, envelope) in [
            ("non-json", "not-json{{{"),
            ("non-object", "[1,2,3]"),
            ("wrong-version", "{\"v\":1}"),
            ("missing-v", "{\"wrapped_dek_b64\":\"AA==\",\"dek_nonce_b64\":\"AA==\"}"),
            (
                "empty-wrap-fields",
                "{\"v\":2,\"wrapped_dek_b64\":\"\",\"dek_nonce_b64\":\"AAAAAAAAAAAAAAAA\"}",
            ),
        ] {
            let resp = server
                .post("/api/v1/crypto/dek-envelope")
                .json(&serde_json::json!({ "envelope": envelope }))
                .await;
            assert_eq!(
                resp.status_code().as_u16(),
                400,
                "dek-envelope must reject {name}"
            );
        }
    }

    /// POST /api/v1/analytics/bond prices a par bond through the C core:
    /// YTM must equal the coupon rate and the price must round-trip.
    #[tokio::test]
    async fn bond_endpoint_par_bond() {
        let server = test_server();
        let cash_flows: Vec<serde_json::Value> = (1..=10)
            .map(|t| {
                serde_json::json!({
                    "time_years": t as f64,
                    "amount": if t == 10 { 105.0 } else { 5.0 },
                })
            })
            .collect();
        let resp = server
            .post("/api/v1/analytics/bond")
            .json(&serde_json::json!({ "cash_flows": cash_flows, "price": 100.0 }))
            .await;
        assert_eq!(resp.status_code().as_u16(), 200);
        let body: serde_json::Value = resp.json();
        assert!((body["ytm_annual"].as_f64().unwrap() - 0.05).abs() < 1e-9);
        assert!((body["npv"].as_f64().unwrap() - 100.0).abs() < 1e-6);
        assert!((body["macaulay_duration_years"].as_f64().unwrap() - 8.107_822).abs() < 1e-4);
    }

    /// POST /api/v1/analytics/bond rejects a request with both/neither of
    /// price and yield_annual.
    #[tokio::test]
    async fn bond_endpoint_rejects_ambiguous_pricing_input() {
        let server = test_server();
        let cf = serde_json::json!([{ "time_years": 1.0, "amount": 105.0 }]);
        for payload in [
            serde_json::json!({ "cash_flows": cf, "price": 100.0, "yield_annual": 0.05 }),
            serde_json::json!({ "cash_flows": cf }),
        ] {
            let resp = server.post("/api/v1/analytics/bond").json(&payload).await;
            assert_eq!(resp.status_code().as_u16(), 400);
        }
    }

    fn configured_server(cfg: &AppConfig) -> TestServer {
        let store = Arc::new(
            storage::Store::open_in_memory().expect("in-memory store for tests"),
        );
        TestServer::new(build_app(store, cfg)).expect("test server")
    }

    fn test_ui_dir() -> tempfile::TempDir {
        let dir = tempfile::tempdir().expect("temp ui dir");
        std::fs::write(dir.path().join("index.html"), "<h1>PAE dashboard</h1>")
            .expect("write index");
        std::fs::write(dir.path().join("app.js"), "console.log('pae');").expect("write js");
        std::fs::create_dir_all(dir.path().join("styles")).expect("styles dir");
        std::fs::write(dir.path().join("styles/app.css"), "body{}").expect("write css");
        dir
    }

    /// With PAE_UI_DIR set, the engine serves the dashboard itself:
    /// real files as-is, extensionless routes fall back to index.html
    /// (SPA), missing files with extensions 404, and API 404s are
    /// never swallowed by the UI fallback.
    #[tokio::test]
    async fn ui_static_serving_and_spa_fallback() {
        let dir = test_ui_dir();
        let cfg = AppConfig {
            ui_dir: Some(dir.path().to_path_buf()),
            analytics_base: "http://127.0.0.1:3002".to_string(),
        };
        let server = configured_server(&cfg);

        let index = server.get("/").await;
        assert_eq!(index.status_code().as_u16(), 200);
        assert!(index.text().contains("PAE dashboard"));

        let js = server.get("/app.js").await;
        assert_eq!(js.status_code().as_u16(), 200);
        assert!(js
            .headers()
            .get("content-type")
            .unwrap()
            .to_str()
            .unwrap()
            .contains("javascript"));

        let css = server.get("/styles/app.css").await;
        assert_eq!(css.status_code().as_u16(), 200);

        // Client-side route: no such file, no extension -> index.html.
        let spa = server.get("/portfolio/demo").await;
        assert_eq!(spa.status_code().as_u16(), 200);
        assert!(spa.text().contains("PAE dashboard"));

        // Missing asset *with* an extension is a real 404.
        let missing = server.get("/nope.js").await;
        assert_eq!(missing.status_code().as_u16(), 404);

        // The UI fallback must not resurrect removed API endpoints.
        let api = server
            .post("/api/v1/crypto/derive-key")
            .json(&serde_json::json!({ "passphrase": "hunter2" }))
            .await;
        assert_eq!(api.status_code().as_u16(), 404);
    }

    /// Without a UI directory the app stays API-only (404 at `/`).
    #[tokio::test]
    async fn ui_absent_by_default() {
        let cfg = AppConfig {
            ui_dir: None,
            analytics_base: "http://127.0.0.1:3002".to_string(),
        };
        let server = configured_server(&cfg);
        let resp = server.get("/").await;
        assert_eq!(resp.status_code().as_u16(), 404);
    }

    /// The analytics proxy forwards method, path+query and body, and
    /// relays the upstream status and body verbatim.
    #[tokio::test]
    async fn analytics_proxy_forwards_and_relays() {
        use tokio::io::{AsyncReadExt, AsyncWriteExt};

        let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
            .await
            .expect("bind stub analytics");
        let addr = listener.local_addr().unwrap();
        let stub = tokio::spawn(async move {
            let (mut sock, _) = listener.accept().await.expect("accept");
            let mut buf = Vec::new();
            let mut chunk = [0u8; 1024];
            // Read headers, then the Content-Length body.
            let body_len = loop {
                let n = sock.read(&mut chunk).await.expect("read request");
                assert!(n > 0, "stub got EOF before full request");
                buf.extend_from_slice(&chunk[..n]);
                if let Some(pos) = buf.windows(4).position(|w| w == b"\r\n\r\n") {
                    let head = String::from_utf8_lossy(&buf[..pos]).to_string();
                    let len: usize = head
                        .lines()
                        .find_map(|l| {
                            l.to_ascii_lowercase()
                                .strip_prefix("content-length:")
                                .map(|v| v.trim().to_string())
                        })
                        .and_then(|v| v.parse().ok())
                        .unwrap_or(0);
                    if buf.len() >= pos + 4 + len {
                        break len;
                    }
                }
            };
            let text = String::from_utf8_lossy(&buf).to_string();
            assert!(
                text.starts_with("POST /api/v1/analytics/risk?portfolio_id=demo HTTP/1.1"),
                "stub saw request line: {}",
                text.lines().next().unwrap_or("")
            );
            assert!(text.contains("\"x\":1"), "stub saw JSON body: {text}");
            let _ = body_len;
            let payload = "{\"var_95\":0.5}";
            let resp = format!(
                "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
                payload.len(),
                payload
            );
            sock.write_all(resp.as_bytes()).await.expect("write stub response");
            text
        });

        let cfg = AppConfig {
            ui_dir: None,
            analytics_base: format!("http://{addr}"),
        };
        let server = configured_server(&cfg);
        let resp = server
            .post("/api/v1/analytics/risk?portfolio_id=demo")
            .json(&serde_json::json!({ "x": 1 }))
            .await;
        assert_eq!(resp.status_code().as_u16(), 200);
        let body: serde_json::Value = resp.json();
        assert_eq!(body["var_95"], 0.5);
        stub.await.expect("stub task");
    }

    /// With analytics down, proxied routes answer an honest 502
    /// instead of hanging or fabricating a response.
    #[tokio::test]
    async fn analytics_proxy_502_when_analytics_down() {
        let cfg = AppConfig {
            ui_dir: None,
            // Port 1 is closed on loopback by definition.
            analytics_base: "http://127.0.0.1:1".to_string(),
        };
        let server = configured_server(&cfg);
        let resp = server
            .post("/api/v1/analytics/risk?portfolio_id=demo")
            .json(&serde_json::json!({}))
            .await;
        assert_eq!(resp.status_code().as_u16(), 502);
    }
}
