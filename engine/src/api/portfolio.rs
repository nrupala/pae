use axum::http::StatusCode;
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

// --- Shared API Error ---

#[derive(Serialize)]
pub struct ApiError {
    pub error: String,
    pub code: String,
}

type ApiResult<T> = Result<Json<T>, (StatusCode, Json<ApiError>)>;

fn bad_request(msg: impl Into<String>, code: impl Into<String>) -> (StatusCode, Json<ApiError>) {
    let msg = msg.into();
    let code = code.into();
    tracing::warn!(error = %msg, code = %code, "portfolio API validation error");
    (
        StatusCode::BAD_REQUEST,
        Json(ApiError {
            error: msg,
            code: code,
        }),
    )
}

// --- Validation helpers ---

/// Validate that holdings are non-empty and contain sane values.
fn validate_holdings(holdings: &[Holding]) -> Result<(), (StatusCode, Json<ApiError>)> {
    if holdings.is_empty() {
        return Err(bad_request(
            "Holdings array must not be empty",
            "EMPTY_HOLDINGS",
        ));
    }

    for (i, h) in holdings.iter().enumerate() {
        if h.symbol.is_empty() {
            return Err(bad_request(
                format!("Holding at index {i} has an empty symbol"),
                "EMPTY_SYMBOL",
            ));
        }
        if h.market_value < 0.0 {
            return Err(bad_request(
                format!("Holding '{}' has negative market_value: {}", h.symbol, h.market_value),
                "NEGATIVE_MARKET_VALUE",
            ));
        }
        if !h.market_value.is_finite() {
            return Err(bad_request(
                format!("Holding '{}' has non-finite market_value", h.symbol),
                "INVALID_MARKET_VALUE",
            ));
        }
        if h.returns.is_empty() {
            return Err(bad_request(
                format!("Holding '{}' has no return data", h.symbol),
                "EMPTY_RETURNS",
            ));
        }
        for (j, r) in h.returns.iter().enumerate() {
            if !r.is_finite() {
                return Err(bad_request(
                    format!("Holding '{}' return at index {j} is non-finite", h.symbol),
                    "INVALID_RETURN",
                ));
            }
        }
    }

    let total_value: f64 = holdings.iter().map(|h| h.market_value).sum();
    if total_value <= 0.0 {
        return Err(bad_request(
            "Total portfolio market value must be positive",
            "ZERO_TOTAL_VALUE",
        ));
    }

    Ok(())
}

// --- Handlers ---

pub async fn compute_risk(Json(input): Json<PortfolioInput>) -> ApiResult<RiskResponse> {
    validate_holdings(&input.holdings)?;

    let returns = metrics::portfolio_returns(&input.holdings);
    let rf = 0.045; // risk-free rate -- user-configurable in future

    Ok(Json(RiskResponse {
        var_95: metrics::value_at_risk(&returns, 0.05),
        var_99: metrics::value_at_risk(&returns, 0.01),
        cvar_95: metrics::conditional_var(&returns, 0.05),
        max_drawdown: metrics::max_drawdown(&returns),
        beta: None, // requires benchmark returns
        sharpe: metrics::sharpe_ratio(&returns, rf),
        sortino: metrics::sortino_ratio(&returns, rf),
        volatility: metrics::volatility(&returns),
    }))
}

pub async fn compute_metrics(Json(input): Json<PortfolioInput>) -> ApiResult<MetricsResponse> {
    validate_holdings(&input.holdings)?;

    let returns = metrics::portfolio_returns(&input.holdings);
    let rf = 0.045;

    Ok(Json(MetricsResponse {
        total_return: metrics::total_return(&returns),
        annualized_return: metrics::annualized_return(&returns, 12),
        volatility: metrics::volatility(&returns),
        sharpe: metrics::sharpe_ratio(&returns, rf),
        sortino: metrics::sortino_ratio(&returns, rf),
        max_drawdown: metrics::max_drawdown(&returns),
        win_rate: metrics::win_rate(&returns),
        calmar: metrics::calmar_ratio(&returns, 12),
    }))
}

pub async fn stress_test(Json(input): Json<StressTestInput>) -> ApiResult<StressTestResponse> {
    validate_holdings(&input.holdings)?;

    if input.scenario.is_empty() && input.custom_shocks.is_none() {
        return Err(bad_request(
            "Either scenario name or custom_shocks must be provided",
            "MISSING_SCENARIO",
        ));
    }

    if let Some(ref shocks) = input.custom_shocks {
        for shock in shocks {
            if !shock.shock_pct.is_finite() {
                return Err(bad_request(
                    format!("Shock for '{}' has non-finite value", shock.asset_class),
                    "INVALID_SHOCK",
                ));
            }
        }
    }

    Ok(stress::run_stress_test(&input))
}

pub async fn correlation_matrix(Json(input): Json<CorrelationInput>) -> ApiResult<CorrelationResponse> {
    validate_holdings(&input.holdings)?;

    if input.holdings.len() < 2 {
        return Err(bad_request(
            "Correlation matrix requires at least 2 holdings",
            "INSUFFICIENT_HOLDINGS",
        ));
    }

    if let Some(w) = input.window_days {
        if w == 0 {
            return Err(bad_request(
                "window_days must be at least 1",
                "INVALID_WINDOW",
            ));
        }
    }

    Ok(correlation::compute_matrix(&input))
}

pub async fn monte_carlo(Json(input): Json<MonteCarloInput>) -> ApiResult<MonteCarloResponse> {
    validate_holdings(&input.holdings)?;

    if !input.initial_value.is_finite() || input.initial_value <= 0.0 {
        return Err(bad_request(
            "initial_value must be a positive finite number",
            "INVALID_INITIAL_VALUE",
        ));
    }

    if let Some(n) = input.num_simulations {
        if n == 0 || n > 1_000_000 {
            return Err(bad_request(
                "num_simulations must be between 1 and 1,000,000",
                "INVALID_NUM_SIMULATIONS",
            ));
        }
    }

    if let Some(h) = input.time_horizon_months {
        if h == 0 || h > 600 {
            return Err(bad_request(
                "time_horizon_months must be between 1 and 600 (50 years)",
                "INVALID_HORIZON",
            ));
        }
    }

    Ok(montecarlo::run_simulation(&input))
}
