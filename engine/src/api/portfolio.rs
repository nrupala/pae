use axum::http::StatusCode;
use axum::Json;
use serde::{Deserialize, Serialize};

use crate::risk::{correlation, metrics, montecarlo, stress};

// --- Error Response ---

/// Standard error response body for portfolio endpoints.
#[derive(Serialize)]
pub struct PortfolioErrorResponse {
    pub error: String,
    pub code: String,
}

/// Portfolio-level validation and processing errors.
#[derive(Debug)]
pub enum PortfolioError {
    EmptyHoldings,
    NegativeWeight { symbol: String },
    NegativeMarketValue { symbol: String },
    EmptySymbol,
    NoReturnsData { symbol: String },
    NanOrInfReturn { symbol: String, index: usize },
    NanOrInfWeight { symbol: String },
    InvalidAlpha,
    InvalidSimulationCount,
    InvalidTimeHorizon,
    NegativeInitialValue,
    ZeroInitialValue,
    InvalidWindowDays,
    ProcessingError(String),
}

impl PortfolioError {
    fn status_code(&self) -> StatusCode {
        match self {
            PortfolioError::ProcessingError(_) => StatusCode::UNPROCESSABLE_ENTITY,
            _ => StatusCode::BAD_REQUEST,
        }
    }

    fn error_code(&self) -> &'static str {
        match self {
            PortfolioError::EmptyHoldings => "EMPTY_HOLDINGS",
            PortfolioError::NegativeWeight { .. } => "NEGATIVE_WEIGHT",
            PortfolioError::NegativeMarketValue { .. } => "NEGATIVE_MARKET_VALUE",
            PortfolioError::EmptySymbol => "EMPTY_SYMBOL",
            PortfolioError::NoReturnsData { .. } => "NO_RETURNS_DATA",
            PortfolioError::NanOrInfReturn { .. } => "NAN_INF_RETURN",
            PortfolioError::NanOrInfWeight { .. } => "NAN_INF_WEIGHT",
            PortfolioError::InvalidAlpha => "INVALID_ALPHA",
            PortfolioError::InvalidSimulationCount => "INVALID_SIMULATION_COUNT",
            PortfolioError::InvalidTimeHorizon => "INVALID_TIME_HORIZON",
            PortfolioError::NegativeInitialValue => "NEGATIVE_INITIAL_VALUE",
            PortfolioError::ZeroInitialValue => "ZERO_INITIAL_VALUE",
            PortfolioError::InvalidWindowDays => "INVALID_WINDOW_DAYS",
            PortfolioError::ProcessingError(_) => "PROCESSING_ERROR",
        }
    }

    fn message(&self) -> String {
        match self {
            PortfolioError::EmptyHoldings => "Holdings array must not be empty".to_string(),
            PortfolioError::NegativeWeight { symbol } => format!("Negative weight for symbol '{symbol}'"),
            PortfolioError::NegativeMarketValue { symbol } => format!("Negative market_value for symbol '{symbol}'"),
            PortfolioError::EmptySymbol => "Symbol must not be empty".to_string(),
            PortfolioError::NoReturnsData { symbol } => format!("No returns data for symbol '{symbol}'"),
            PortfolioError::NanOrInfReturn { symbol, index } => format!("NaN or Infinity in returns[{index}] for symbol '{symbol}'"),
            PortfolioError::NanOrInfWeight { symbol } => format!("NaN or Infinity weight for symbol '{symbol}'"),
            PortfolioError::InvalidAlpha => "Alpha must be between 0 and 1 exclusive".to_string(),
            PortfolioError::InvalidSimulationCount => "num_simulations must be between 1 and 1_000_000".to_string(),
            PortfolioError::InvalidTimeHorizon => "time_horizon_months must be between 1 and 600".to_string(),
            PortfolioError::NegativeInitialValue => "initial_value must not be negative".to_string(),
            PortfolioError::ZeroInitialValue => "initial_value must be greater than zero".to_string(),
            PortfolioError::InvalidWindowDays => "window_days must be between 2 and 10_000".to_string(),
            PortfolioError::ProcessingError(msg) => format!("Processing error: {msg}"),
        }
    }
}

fn into_error_response(err: PortfolioError) -> (StatusCode, Json<PortfolioErrorResponse>) {
    let status = err.status_code();
    (status, Json(PortfolioErrorResponse {
        error: err.message(),
        code: err.error_code().to_string(),
    }))
}

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

// --- Validation ---

/// Validate holdings common to all portfolio endpoints.
/// Checks for: empty array, empty symbols, negative weights/values,
/// NaN/Infinity in numeric fields, and at least one return per holding.
fn validate_holdings(holdings: &[Holding]) -> Result<(), PortfolioError> {
    if holdings.is_empty() {
        return Err(PortfolioError::EmptyHoldings);
    }

    for h in holdings {
        if h.symbol.trim().is_empty() {
            return Err(PortfolioError::EmptySymbol);
        }
        if h.weight.is_nan() || h.weight.is_infinite() {
            return Err(PortfolioError::NanOrInfWeight { symbol: h.symbol.clone() });
        }
        if h.weight < 0.0 {
            return Err(PortfolioError::NegativeWeight { symbol: h.symbol.clone() });
        }
        if h.market_value.is_nan() || h.market_value.is_infinite() {
            return Err(PortfolioError::NegativeMarketValue { symbol: h.symbol.clone() });
        }
        if h.market_value < 0.0 {
            return Err(PortfolioError::NegativeMarketValue { symbol: h.symbol.clone() });
        }
        if h.returns.is_empty() {
            return Err(PortfolioError::NoReturnsData { symbol: h.symbol.clone() });
        }
        for (i, r) in h.returns.iter().enumerate() {
            if r.is_nan() || r.is_infinite() {
                return Err(PortfolioError::NanOrInfReturn { symbol: h.symbol.clone(), index: i });
            }
        }
    }

    Ok(())
}

/// Sanitize computed f64 values: replace NaN/Infinity with 0.0.
/// This prevents JSON serialization issues and ensures clients always
/// receive valid numeric responses.
fn sanitize_f64(val: f64) -> f64 {
    if val.is_nan() || val.is_infinite() {
        0.0
    } else {
        val
    }
}

// --- Handlers ---

/// POST /api/v1/portfolio/risk
///
/// Computes VaR, CVaR, Sharpe, Sortino, volatility, and max drawdown.
/// Returns 400 if holdings are invalid.
pub async fn compute_risk(
    Json(input): Json<PortfolioInput>,
) -> Result<Json<RiskResponse>, (StatusCode, Json<PortfolioErrorResponse>)> {
    validate_holdings(&input.holdings).map_err(into_error_response)?;

    let returns = metrics::portfolio_returns(&input.holdings);
    if returns.is_empty() {
        return Err(into_error_response(PortfolioError::ProcessingError(
            "All holdings have zero market value, producing no returns".to_string(),
        )));
    }

    let rf = 0.045; // risk-free rate -- user-configurable in future

    Ok(Json(RiskResponse {
        var_95: sanitize_f64(metrics::value_at_risk(&returns, 0.05)),
        var_99: sanitize_f64(metrics::value_at_risk(&returns, 0.01)),
        cvar_95: sanitize_f64(metrics::conditional_var(&returns, 0.05)),
        max_drawdown: sanitize_f64(metrics::max_drawdown(&returns)),
        beta: None, // requires benchmark returns
        sharpe: sanitize_f64(metrics::sharpe_ratio(&returns, rf)),
        sortino: sanitize_f64(metrics::sortino_ratio(&returns, rf)),
        volatility: sanitize_f64(metrics::volatility(&returns)),
    }))
}

/// POST /api/v1/portfolio/metrics
///
/// Computes total return, annualized return, volatility, Sharpe, Sortino,
/// max drawdown, win rate, and Calmar ratio.
/// Returns 400 if holdings are invalid.
pub async fn compute_metrics(
    Json(input): Json<PortfolioInput>,
) -> Result<Json<MetricsResponse>, (StatusCode, Json<PortfolioErrorResponse>)> {
    validate_holdings(&input.holdings).map_err(into_error_response)?;

    let returns = metrics::portfolio_returns(&input.holdings);
    if returns.is_empty() {
        return Err(into_error_response(PortfolioError::ProcessingError(
            "All holdings have zero market value, producing no returns".to_string(),
        )));
    }

    let rf = 0.045;

    Ok(Json(MetricsResponse {
        total_return: sanitize_f64(metrics::total_return(&returns)),
        annualized_return: sanitize_f64(metrics::annualized_return(&returns, 12)),
        volatility: sanitize_f64(metrics::volatility(&returns)),
        sharpe: sanitize_f64(metrics::sharpe_ratio(&returns, rf)),
        sortino: sanitize_f64(metrics::sortino_ratio(&returns, rf)),
        max_drawdown: sanitize_f64(metrics::max_drawdown(&returns)),
        win_rate: sanitize_f64(metrics::win_rate(&returns)),
        calmar: sanitize_f64(metrics::calmar_ratio(&returns, 12)),
    }))
}

/// POST /api/v1/portfolio/stress
///
/// Runs a scenario-based stress test on the portfolio.
/// Returns 400 if holdings are invalid.
pub async fn stress_test(
    Json(input): Json<StressTestInput>,
) -> Result<Json<StressTestResponse>, (StatusCode, Json<PortfolioErrorResponse>)> {
    validate_holdings(&input.holdings).map_err(into_error_response)?;

    if input.scenario.trim().is_empty() {
        return Err(into_error_response(PortfolioError::ProcessingError(
            "Scenario name must not be empty".to_string(),
        )));
    }

    // Validate custom shocks if provided
    if let Some(ref shocks) = input.custom_shocks {
        for shock in shocks {
            if shock.asset_class.trim().is_empty() {
                return Err(into_error_response(PortfolioError::ProcessingError(
                    "Custom shock asset_class must not be empty".to_string(),
                )));
            }
            if shock.shock_pct.is_nan() || shock.shock_pct.is_infinite() {
                return Err(into_error_response(PortfolioError::ProcessingError(
                    format!("Custom shock for '{}' has NaN or Infinity shock_pct", shock.asset_class),
                )));
            }
        }
    }

    Ok(Json(stress::run_stress_test(&input)))
}

/// POST /api/v1/portfolio/correlation
///
/// Computes the pairwise Pearson correlation matrix.
/// Returns 400 if holdings are invalid or window_days is out of range.
pub async fn correlation_matrix(
    Json(input): Json<CorrelationInput>,
) -> Result<Json<CorrelationResponse>, (StatusCode, Json<PortfolioErrorResponse>)> {
    validate_holdings(&input.holdings).map_err(into_error_response)?;

    if let Some(w) = input.window_days {
        if w < 2 || w > 10_000 {
            return Err(into_error_response(PortfolioError::InvalidWindowDays));
        }
    }

    Ok(Json(correlation::compute_matrix(&input)))
}

/// POST /api/v1/portfolio/montecarlo
///
/// Runs a Monte Carlo simulation on the portfolio.
/// Returns 400 if holdings are invalid, initial_value <= 0,
/// num_simulations is out of range, or time_horizon is out of range.
pub async fn monte_carlo(
    Json(input): Json<MonteCarloInput>,
) -> Result<Json<MonteCarloResponse>, (StatusCode, Json<PortfolioErrorResponse>)> {
    validate_holdings(&input.holdings).map_err(into_error_response)?;

    if input.initial_value < 0.0 {
        return Err(into_error_response(PortfolioError::NegativeInitialValue));
    }
    if input.initial_value == 0.0 {
        return Err(into_error_response(PortfolioError::ZeroInitialValue));
    }
    if input.initial_value.is_nan() || input.initial_value.is_infinite() {
        return Err(into_error_response(PortfolioError::NegativeInitialValue));
    }

    if let Some(n) = input.num_simulations {
        if n == 0 || n > 1_000_000 {
            return Err(into_error_response(PortfolioError::InvalidSimulationCount));
        }
    }

    if let Some(t) = input.time_horizon_months {
        if t == 0 || t > 600 {
            return Err(into_error_response(PortfolioError::InvalidTimeHorizon));
        }
    }

    Ok(Json(montecarlo::run_simulation(&input)))
}
