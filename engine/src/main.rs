use anyhow::Result;
use axum::{
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

#[tokio::main]
async fn main() -> Result<()> {
    dotenvy::dotenv().ok();

    tracing_subscriber::registry()
        .with(tracing_subscriber::EnvFilter::try_from_default_env()
            .unwrap_or_else(|_| "pae_engine=info,tower_http=info".into()))
        .with(tracing_subscriber::fmt::layer())
        .init();

    // Shared state: version store (in-memory; production uses SQLite with WAL)
    let version_store = Arc::new(versioning::store::VersionStore::new());

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

    // Routes backed by the versioning store
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
        .with_state(store);

    // Routes without shared state (stateless compute)
    let stateless_routes = Router::new()
        .route("/health", get(api::health::check))
        .route("/api/v1/portfolio/risk", post(api::portfolio::compute_risk))
        .route("/api/v1/portfolio/metrics", post(api::portfolio::compute_metrics))
        .route("/api/v1/portfolio/stress", post(api::portfolio::stress_test))
        .route("/api/v1/portfolio/correlation", post(api::portfolio::correlation_matrix))
        .route("/api/v1/portfolio/montecarlo", post(api::portfolio::monte_carlo))
        .route("/api/v1/crypto/derive-key", post(api::crypto_api::derive_key))
        .route("/api/v1/crypto/encrypt", post(api::crypto_api::encrypt))
        .route("/api/v1/crypto/decrypt", post(api::crypto_api::decrypt))
        .route("/api/v1/version/snapshot", post(api::versioning_api::get_snapshot));

    let app = Router::new()
        .merge(versioned_routes)
        .merge(storage_routes)
        .merge(stateless_routes)
        .layer(CorsLayer::permissive())
        .layer(TraceLayer::new_for_http());

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
