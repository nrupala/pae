use anyhow::Result;
use axum::{
    http::{header, HeaderValue, Method},
    routing::{delete, get, post, put},
    Router,
};
use std::net::SocketAddr;
use std::sync::Arc;
use tower_http::cors::CorsLayer;
use tower_http::trace::TraceLayer;
use tracing_subscriber::{layer::SubscriberExt, util::SubscriberInitExt};

mod api;
mod crypto;
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

/// Build the Axum router. Factored out of `main` so the HTTP-layer
/// integration tests below can construct the app without binding a socket.
fn create_app(store: Arc<storage::Store>) -> Router {
    // Routes backed by the versioning store
    let version_store = Arc::new(versioning::store::VersionStore::new());
    let versioned_routes = Router::new()
        .route("/api/v1/version", post(api::versioning_api::append_version))
        .route("/api/v1/version/history", post(api::versioning_api::get_history))
        .route("/api/v1/version/integrity/{entity_id}", get(api::versioning_api::verify_integrity))
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
        .with_state(store);

    // Routes without shared state (stateless compute)
    let stateless_routes = Router::new()
        .route("/health", get(api::health::check))
        .route("/api/v1/portfolio/risk", post(api::portfolio::compute_risk))
        .route("/api/v1/portfolio/metrics", post(api::portfolio::compute_metrics))
        .route("/api/v1/portfolio/stress", post(api::portfolio::stress_test))
        .route("/api/v1/portfolio/correlation", post(api::portfolio::correlation_matrix))
        .route("/api/v1/portfolio/montecarlo", post(api::portfolio::monte_carlo))
        .route("/api/v1/version/snapshot", post(api::versioning_api::get_snapshot));

    Router::new()
        .merge(versioned_routes)
        .merge(storage_routes)
        .merge(crypto_routes)
        .merge(stateless_routes)
        .layer(cors_layer())
        .layer(TraceLayer::new_for_http())
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

    let app = create_app(store);

    let port: u16 = std::env::var("PAE_ENGINE_PORT")
        .unwrap_or_else(|_| "3001".to_string())
        .parse()
        .map_err(|e| anyhow::anyhow!("Invalid PAE_ENGINE_PORT: {e}"))?;

    let addr = SocketAddr::from(([0, 0, 0, 0], port));
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

    /// deny_unknown_fields: a "passphrase" field on /encrypt is rejected.
    /// (axum 0.8 maps serde deserialization failures to 422.)
    #[tokio::test]
    async fn encrypt_rejects_passphrase_field() {
        let server = test_server();
        let resp = server
            .post("/api/v1/crypto/encrypt")
            .json(&serde_json::json!({
                "plaintext": "hello",
                "key_b64": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
                "passphrase": "must-not-be-accepted",
            }))
            .await;
        assert_eq!(
            resp.status_code().as_u16(),
            422,
            "encrypt must reject unknown fields"
        );
    }

    /// deny_unknown_fields: a "passphrase" field on /decrypt is rejected.
    /// (axum 0.8 maps serde deserialization failures to 422.)
    #[tokio::test]
    async fn decrypt_rejects_passphrase_field() {
        let server = test_server();
        let resp = server
            .post("/api/v1/crypto/decrypt")
            .json(&serde_json::json!({
                "ciphertext_b64": "AA==",
                "nonce_b64": "AAAAAAAAAAAAAAAA",
                "key_b64": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
                "passphrase": "must-not-be-accepted",
            }))
            .await;
        assert_eq!(
            resp.status_code().as_u16(),
            422,
            "decrypt must reject unknown fields"
        );
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
}
