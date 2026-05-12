use crate::api::portfolio::{Holding, RiskMetrics};

/// Calculate core risk metrics for a portfolio.
/// All calculations are deterministic and reproducible.
/// Latency target: < 1ms for typical portfolio sizes (< 500 holdings).
pub fn calculate_risk_metrics(
    holdings: &[Holding],
    returns: &[f64],
    risk_free_rate: f64,
) -> RiskMetrics {
    let total_value: f64 = holdings.iter().map(|h| h.market_value).sum();
    let volatility = annualized_volatility(returns);
    let sharpe = sharpe_ratio(returns, risk_free_rate);
    let sortino = sortino_ratio(returns, risk_free_rate);
    let max_dd = max_drawdown(returns);
    let var_95 = value_at_risk(returns, 0.95);
    let cvar_95 = conditional_var(returns, 0.95);

    RiskMetrics {
        total_value,
        volatility,
        sharpe_ratio: sharpe,
        sortino_ratio: sortino,
        max_drawdown: max_dd,
        var_95,
        cvar_95,
    }
}

/// Annualized volatility from periodic returns.
/// Assumes monthly returns; multiplies by sqrt(12) for annualization.
pub fn annualized_volatility(returns: &[f64]) -> f64 {
    if returns.len() < 2 {
        return 0.0;
    }
    let mean = returns.iter().sum::<f64>() / returns.len() as f64;
    let variance = returns.iter().map(|r| (r - mean).powi(2)).sum::<f64>()
        / (returns.len() - 1) as f64;
    variance.sqrt() * (12.0_f64).sqrt()
}

/// Sharpe ratio: (mean return - risk-free rate) / volatility.
/// Uses annualized figures. Returns 0.0 if volatility is zero.
pub fn sharpe_ratio(returns: &[f64], risk_free_rate: f64) -> f64 {
    let vol = annualized_volatility(returns);
    if vol == 0.0 {
        return 0.0;
    }
    let mean_annual = returns.iter().sum::<f64>() / returns.len() as f64 * 12.0;
    (mean_annual - risk_free_rate) / vol
}

/// Sortino ratio: (mean return - risk-free rate) / downside deviation.
/// Only penalizes returns below the target (risk-free rate).
pub fn sortino_ratio(returns: &[f64], risk_free_rate: f64) -> f64 {
    let monthly_target = risk_free_rate / 12.0;
    let downside: Vec<f64> = returns
        .iter()
        .filter(|&&r| r < monthly_target)
        .map(|&r| (r - monthly_target).powi(2))
        .collect();

    if downside.is_empty() {
        return 0.0;
    }

    let downside_dev = (downside.iter().sum::<f64>() / downside.len() as f64).sqrt()
        * (12.0_f64).sqrt();

    if downside_dev == 0.0 {
        return 0.0;
    }

    let mean_annual = returns.iter().sum::<f64>() / returns.len() as f64 * 12.0;
    (mean_annual - risk_free_rate) / downside_dev
}

/// Maximum drawdown: largest peak-to-trough decline.
/// Returns a positive number (e.g., 0.25 means 25% drawdown).
pub fn max_drawdown(returns: &[f64]) -> f64 {
    if returns.is_empty() {
        return 0.0;
    }

    let mut cumulative = Vec::with_capacity(returns.len() + 1);
    cumulative.push(1.0);
    for &r in returns {
        let prev = *cumulative.last().unwrap();
        cumulative.push(prev * (1.0 + r));
    }

    let mut peak = cumulative[0];
    let mut max_dd = 0.0_f64;

    for &value in &cumulative[1..] {
        if value > peak {
            peak = value;
        }
        let dd = (peak - value) / peak;
        if dd > max_dd {
            max_dd = dd;
        }
    }

    max_dd
}

/// Value at Risk (historical simulation method).
/// Returns the loss threshold at the given confidence level.
/// E.g., VaR(0.95) = the 5th percentile of returns (as a positive loss number).
pub fn value_at_risk(returns: &[f64], confidence: f64) -> f64 {
    if returns.is_empty() {
        return 0.0;
    }

    let mut sorted = returns.to_vec();
    sorted.sort_by(|a, b| a.partial_cmp(b).unwrap());

    let index = ((1.0 - confidence) * sorted.len() as f64).floor() as usize;
    let index = index.min(sorted.len() - 1);

    -sorted[index] // Return as positive number
}

/// Conditional VaR (Expected Shortfall).
/// Average of all returns below the VaR threshold.
/// More informative than VaR for tail risk.
pub fn conditional_var(returns: &[f64], confidence: f64) -> f64 {
    if returns.is_empty() {
        return 0.0;
    }

    let mut sorted = returns.to_vec();
    sorted.sort_by(|a, b| a.partial_cmp(b).unwrap());

    let cutoff_index = ((1.0 - confidence) * sorted.len() as f64).floor() as usize;
    let cutoff_index = cutoff_index.max(1).min(sorted.len());

    let tail: Vec<f64> = sorted[..cutoff_index].to_vec();
    if tail.is_empty() {
        return value_at_risk(returns, confidence);
    }

    -(tail.iter().sum::<f64>() / tail.len() as f64)
}

/// Portfolio beta relative to a benchmark.
/// Beta = Cov(portfolio, benchmark) / Var(benchmark).
pub fn portfolio_beta(portfolio_returns: &[f64], benchmark_returns: &[f64]) -> f64 {
    let n = portfolio_returns.len().min(benchmark_returns.len());
    if n < 2 {
        return 1.0;
    }

    let p_mean = portfolio_returns[..n].iter().sum::<f64>() / n as f64;
    let b_mean = benchmark_returns[..n].iter().sum::<f64>() / n as f64;

    let mut covariance = 0.0;
    let mut b_variance = 0.0;

    for i in 0..n {
        let p_diff = portfolio_returns[i] - p_mean;
        let b_diff = benchmark_returns[i] - b_mean;
        covariance += p_diff * b_diff;
        b_variance += b_diff * b_diff;
    }

    if b_variance == 0.0 {
        return 1.0;
    }

    covariance / b_variance
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_volatility_constant_returns() {
        let returns = vec![0.01, 0.01, 0.01, 0.01];
        assert_eq!(annualized_volatility(&returns), 0.0);
    }

    #[test]
    fn test_max_drawdown_no_loss() {
        let returns = vec![0.05, 0.03, 0.02, 0.04];
        assert_eq!(max_drawdown(&returns), 0.0);
    }

    #[test]
    fn test_max_drawdown_simple() {
        let returns = vec![0.10, -0.20, 0.05, -0.05];
        let dd = max_drawdown(&returns);
        assert!(dd > 0.15, "Drawdown should be > 15%, got {}", dd);
    }

    #[test]
    fn test_var_basic() {
        let returns = vec![-0.10, -0.05, 0.0, 0.02, 0.05, 0.08, 0.10, 0.12, 0.15, 0.20];
        let var = value_at_risk(&returns, 0.95);
        assert!(var > 0.0, "VaR should be positive");
    }

    #[test]
    fn test_sharpe_zero_vol() {
        let returns = vec![0.01, 0.01, 0.01];
        assert_eq!(sharpe_ratio(&returns, 0.02), 0.0);
    }

    #[test]
    fn test_beta_identity() {
        let returns = vec![0.01, -0.02, 0.03, -0.01, 0.02];
        let beta = portfolio_beta(&returns, &returns);
        assert!((beta - 1.0).abs() < 1e-10, "Self-beta should be 1.0, got {}", beta);
    }
}
