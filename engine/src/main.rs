use anyhow::Result;
use axum::{
    routing::{get, post},
    Router,
};
use std::net::SocketAddr;
use tower_http::cors::CorsLayer;
use tower_http::trace::TraceLayer;
use tracing_subscriber::{layer::SubscriberExt, util::SubscriberInitExt};

mod api;
mod crypto;
mod risk;
mod versioning;

#[tokio::main]
async fn main() -> Result<()> {
    dotenvy::dotenv().ok();

    tracing_subscriber::registry()
        .with(tracing_subscriber::EnvFilter::try_from_default_env()
            .unwrap_or_else(|_| "pae_engine=info,tower_http=info".into()))
        .with(tracing_subscriber::fmt::layer())
        .init();

    let app = Router::new()
        .route("/health", get(api::health::check))
        .route("/api/v1/portfolio/risk", post(api::portfolio::compute_risk))
        .route("/api/v1/portfolio/metrics", post(api::portfolio::compute_metrics))
        .route("/api/v1/portfolio/stress", post(api::portfolio::stress_test))
        .route("/api/v1/portfolio/correlation", post(api::portfolio::correlation_matrix))
        .route("/api/v1/portfolio/montecarlo", post(api::portfolio::monte_carlo))
        .route("/api/v1/crypto/derive-key", post(api::crypto_api::derive_key))
        .route("/api/v1/crypto/encrypt", post(api::crypto_api::encrypt))
        .route("/api/v1/crypto/decrypt", post(api::crypto_api::decrypt))
        .route("/api/v1/version", post(api::versioning_api::append_version))
        .route("/api/v1/version/history", post(api::versioning_api::get_history))
        .route("/api/v1/version/snapshot", post(api::versioning_api::get_snapshot))
        .route("/api/v1/version/integrity/:entity_id", get(api::versioning_api::verify_integrity))
        .layer(CorsLayer::permissive())
        .layer(TraceLayer::new_for_http());

    let port: u16 = std::env::var("PAE_ENGINE_PORT")
        .unwrap_or_else(|_| "3001".to_string())
        .parse()?;

    let addr = SocketAddr::from(([0, 0, 0, 0], port));
    tracing::info!("PAE Engine listening on {}", addr);

    let listener = tokio::net::TcpListener::bind(addr).await?;
    axum::serve(listener, app).await?;

    Ok(())
}
