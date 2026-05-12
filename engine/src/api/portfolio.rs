use axum::Json;
use serde::{Deserialize, Serialize};

use crate::risk::{
    correlation, metrics, montecarlo, stress,
};

// --- Request/Response Types ---

#[derive(Deserialize)]
pub struct PortfolioInput {
    pub holdings: Vec<Holding>,
    pub benchmark: Option<String>,
}

#[derive(Deserialize, Clone)]
pub struct Holding {
    pub symbol: String,
    pub weight: f64,
    pub returns: Vec<f64>,
    pub yield_pct: Option<f64>,
    pub cost_basis: Option<f64>,
    pub market_value: f64,
}

#[derive(Serialize)]
pub struct RiskResponse {
    pub var_95: f64,
    pub var_99: f64,
    pub cvar_95: f64,
    pub max_drawdown: f64,
    pub beta: Option<f64>,
    pub sharpe: f64,
    pub sortino: f64,
    pub volatility: f64,
}

#[derive(Serialize)]
pub struct MetricsResponse {
    pub total_return: f64,
    pub annualized_return: f64,
    pub volatility: f64,
    pub sharpe: f64,
    pub sortino: f64,
    pub max_drawdown: f64,
    pub win_rate: f64,
    pub calmar: f64,
}

#[derive(Deserialize)]
pub struct StressTestInput {
    pub holdings: Vec<Holding>,
    pub scenario: String,
    pub custom_shocks: Option<Vec<AssetShock>>,
}

#[derive(Deserialize)]
pub struct AssetShock {
    pub asset_class: String,
    pub shock_pct: f64,
}

#[derive(Serialize)]
pub struct StressTestResponse {
    pub scenario: String,
    pub portfolio_impact_pct: f64,
    pub position_impacts: Vec<PositionImpact>,
}

#[derive(Serialize)]
pub struct PositionImpact {
    pub symbol: String,
    pub impact_pct: f64,
    pub impact_value: f64,
}

#[derive(Deserialize)]
pub struct CorrelationInput {
    pub holdings: Vec<Holding>,
    pub window_days: Option<usize>,
}

#[derive(Serialize)]
pub struct CorrelationResponse {
    pub symbols: Vec<String>,
    pub matrix: Vec<Vec<f64>>,
    pub window_days: usize,
}

#[derive(Deserialize)]
pub struct MonteCarloInput {
    pub holdings: Vec<Holding>,
    pub num_simulations: Option<usize>,
    pub time_horizon_months: Option<usize>,
    pub initial_value: f64,
}

#[derive(Serialize)]
pub struct MonteCarloResponse {
    pub percentiles: MonteCarloPercentiles,
    pub num_simulations: usize,
    pub time_horizon_months: usize,
    pub probability_of_loss: f64,
}

#[derive(Serialize)]
pub struct MonteCarloPercentiles {
    pub p5: Vec<f64>,
    pub p25: Vec<f64>,
    pub p50: Vec<f64>,
    pub p75: Vec<f64>,
    pub p95: Vec<f64>,
}

// --- Handlers ---

pub async fn compute_risk(Json(input): Json<PortfolioInput>) -> Json<RiskResponse> {
    let returns = metrics::portfolio_returns(&input.holdings);
    let rf = 0.045; // risk-free rate -- user-configurable in future

    Json(RiskResponse {
        var_95: metrics::value_at_risk(&returns, 0.05),
        var_99: metrics::value_at_risk(&returns, 0.01),
        cvar_95: metrics::conditional_var(&returns, 0.05),
        max_drawdown: metrics::max_drawdown(&returns),
        beta: None, // requires benchmark returns
        sharpe: metrics::sharpe_ratio(&returns, rf),
        sortino: metrics::sortino_ratio(&returns, rf),
        volatility: metrics::volatility(&returns),
    })
}

pub async fn compute_metrics(Json(input): Json<PortfolioInput>) -> Json<MetricsResponse> {
    let returns = metrics::portfolio_returns(&input.holdings);
    let rf = 0.045;

    Json(MetricsResponse {
        total_return: metrics::total_return(&returns),
        annualized_return: metrics::annualized_return(&returns, 12),
        volatility: metrics::volatility(&returns),
        sharpe: metrics::sharpe_ratio(&returns, rf),
        sortino: metrics::sortino_ratio(&returns, rf),
        max_drawdown: metrics::max_drawdown(&returns),
        win_rate: metrics::win_rate(&returns),
        calmar: metrics::calmar_ratio(&returns, 12),
    })
}

pub async fn stress_test(Json(input): Json<StressTestInput>) -> Json<StressTestResponse> {
    stress::run_stress_test(&input)
}

pub async fn correlation_matrix(Json(input): Json<CorrelationInput>) -> Json<CorrelationResponse> {
    correlation::compute_matrix(&input)
}

pub async fn monte_carlo(Json(input): Json<MonteCarloInput>) -> Json<MonteCarloResponse> {
    montecarlo::run_simulation(&input)
}
