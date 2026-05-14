use crate::api::portfolio::Holding;

/// Compute weighted portfolio returns from holdings.
///
/// Weights each holding's returns by its share of total market value.
/// Returns an empty vec if holdings are empty or total market value is zero.
///
/// # Edge cases
/// - Empty holdings: returns `vec![]`
/// - All zero market values: returns `vec![]`
/// - Single holding: returns that holding's returns directly
/// - Mismatched return lengths: truncates to the shortest series
pub fn portfolio_returns(holdings: &[Holding]) -> Vec<f64> {
    if holdings.is_empty() {
        return vec![];
    }

    let n = holdings.iter().map(|h| h.returns.len()).min().unwrap_or(0);
    let total_value: f64 = holdings.iter().map(|h| h.market_value).sum();

    if total_value == 0.0 || n == 0 {
        return vec![];
    }

    (0..n)
        .map(|i| {
            holdings
                .iter()
                .map(|h| (h.market_value / total_value) * h.returns[i])
                .sum()
        })
        .collect()
}

/// Annualized volatility (standard deviation of returns).
///
/// Uses Bessel's correction (N-1 denominator) for sample standard deviation.
///
/// # Edge cases
/// - Fewer than 2 returns: returns 0.0 (insufficient data)
/// - All identical returns: returns 0.0
pub fn volatility(returns: &[f64]) -> f64 {
    if returns.len() < 2 {
        return 0.0;
    }
    let mean = returns.iter().sum::<f64>() / returns.len() as f64;
    let variance = returns.iter().map(|r| (r - mean).powi(2)).sum::<f64>()
        / (returns.len() - 1) as f64;
    variance.sqrt()
}

/// Annualized Sharpe ratio.
///
/// `(mean_return - risk_free_period) / volatility`
/// where `risk_free_period = risk_free_annual / 12` (assumes monthly returns).
///
/// # Edge cases
/// - Fewer than 2 returns: returns 0.0
/// - Zero volatility (constant returns): returns 0.0 to avoid division by zero
pub fn sharpe_ratio(returns: &[f64], risk_free_annual: f64) -> f64 {
    if returns.len() < 2 {
        return 0.0;
    }
    let mean = returns.iter().sum::<f64>() / returns.len() as f64;
    let vol = volatility(returns);
    if vol == 0.0 {
        return 0.0;
    }
    let rf_period = risk_free_annual / 12.0; // assuming monthly returns
    (mean - rf_period) / vol
}

/// Sortino ratio (penalizes only downside volatility).
///
/// Measures return per unit of downside risk, where downside is defined
/// as returns below the risk-free rate.
///
/// # Edge cases
/// - Fewer than 2 returns: returns 0.0
/// - No downside returns (all above risk-free): returns 0.0 (no downside risk to measure)
/// - Zero downside deviation with negative excess return: returns 0.0
pub fn sortino_ratio(returns: &[f64], risk_free_annual: f64) -> f64 {
    if returns.len() < 2 {
        return 0.0;
    }
    let mean = returns.iter().sum::<f64>() / returns.len() as f64;
    let rf_period = risk_free_annual / 12.0;

    let downside_returns: Vec<f64> = returns
        .iter()
        .filter(|&&r| r < rf_period)
        .map(|&r| (r - rf_period).powi(2))
        .collect();

    if downside_returns.is_empty() {
        // No downside observations: cannot compute a meaningful Sortino.
        // Return 0.0 rather than Infinity to avoid serialization issues.
        return 0.0;
    }

    let downside_dev = (downside_returns.iter().sum::<f64>() / downside_returns.len() as f64).sqrt();
    if downside_dev == 0.0 {
        return 0.0;
    }

    (mean - rf_period) / downside_dev
}

/// Historical Value at Risk at a given confidence level.
///
/// `alpha = 0.05` for 95% VaR, `alpha = 0.01` for 99% VaR.
/// VaR is reported as a positive number (magnitude of loss).
///
/// # Edge cases
/// - Empty returns: returns 0.0
/// - NaN values in sort: handled by treating NaN as equal to avoid panic
pub fn value_at_risk(returns: &[f64], alpha: f64) -> f64 {
    if returns.is_empty() {
        return 0.0;
    }
    let mut sorted = returns.to_vec();
    sorted.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));

    let index = (alpha * sorted.len() as f64).floor() as usize;
    let index = index.min(sorted.len() - 1);
    -sorted[index] // VaR is reported as positive number
}

/// Conditional VaR (Expected Shortfall) -- average loss beyond VaR threshold.
///
/// More conservative than VaR because it averages all losses in the tail.
///
/// # Edge cases
/// - Empty returns: returns 0.0
/// - Cutoff rounds to zero: uses at least 1 observation
pub fn conditional_var(returns: &[f64], alpha: f64) -> f64 {
    if returns.is_empty() {
        return 0.0;
    }
    let mut sorted = returns.to_vec();
    sorted.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));

    let cutoff = (alpha * sorted.len() as f64).floor() as usize;
    let cutoff = cutoff.max(1).min(sorted.len());

    let tail: Vec<f64> = sorted[..cutoff].to_vec();
    if tail.is_empty() {
        return 0.0;
    }
    -(tail.iter().sum::<f64>() / tail.len() as f64)
}

/// Maximum drawdown from peak.
///
/// Computes the largest peak-to-trough decline in cumulative returns.
///
/// # Edge cases
/// - Empty returns: returns 0.0
/// - Monotonically increasing: returns 0.0 (no drawdown)
/// - Single return: returns the drawdown from that single period
pub fn max_drawdown(returns: &[f64]) -> f64 {
    if returns.is_empty() {
        return 0.0;
    }

    let mut cumulative = Vec::with_capacity(returns.len() + 1);
    cumulative.push(1.0);
    for r in returns {
        let prev = *cumulative.last().unwrap_or(&1.0);
        cumulative.push(prev * (1.0 + r));
    }

    let mut peak = cumulative[0];
    let mut max_dd = 0.0_f64;

    for &val in &cumulative {
        if val > peak {
            peak = val;
        }
        if peak > 0.0 {
            let dd = (peak - val) / peak;
            if dd > max_dd {
                max_dd = dd;
            }
        }
    }

    max_dd
}

/// Total cumulative return.
///
/// Compounds all period returns: `product(1 + r_i) - 1`.
pub fn total_return(returns: &[f64]) -> f64 {
    returns.iter().fold(1.0, |acc, r| acc * (1.0 + r)) - 1.0
}

/// Annualized return given periods per year.
///
/// `(1 + total_return) ^ (1 / years) - 1`
///
/// # Edge cases
/// - Empty returns: returns 0.0
/// - Zero years (no periods): returns 0.0
/// - `periods_per_year` of 0: returns 0.0 to avoid division by zero
pub fn annualized_return(returns: &[f64], periods_per_year: usize) -> f64 {
    if returns.is_empty() || periods_per_year == 0 {
        return 0.0;
    }
    let total = total_return(returns);
    let years = returns.len() as f64 / periods_per_year as f64;
    if years == 0.0 {
        return 0.0;
    }
    (1.0 + total).powf(1.0 / years) - 1.0
}

/// Win rate (percentage of positive return periods).
///
/// # Edge cases
/// - Empty returns: returns 0.0
pub fn win_rate(returns: &[f64]) -> f64 {
    if returns.is_empty() {
        return 0.0;
    }
    let wins = returns.iter().filter(|&&r| r > 0.0).count();
    wins as f64 / returns.len() as f64
}

/// Calmar ratio (annualized return / max drawdown).
///
/// # Edge cases
/// - Zero max drawdown: returns 0.0 to avoid division by zero
pub fn calmar_ratio(returns: &[f64], periods_per_year: usize) -> f64 {
    let dd = max_drawdown(returns);
    if dd == 0.0 {
        return 0.0;
    }
    annualized_return(returns, periods_per_year) / dd
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_volatility() {
        let returns = vec![0.01, -0.02, 0.015, -0.005, 0.02];
        let vol = volatility(&returns);
        assert!(vol > 0.0);
        assert!(vol < 1.0);
    }

    #[test]
    fn test_volatility_empty() {
        assert_eq!(volatility(&[]), 0.0);
    }

    #[test]
    fn test_volatility_single() {
        assert_eq!(volatility(&[0.05]), 0.0);
    }

    #[test]
    fn test_max_drawdown() {
        let returns = vec![0.10, -0.20, -0.10, 0.15, 0.05];
        let dd = max_drawdown(&returns);
        assert!(dd > 0.0);
        assert!(dd < 1.0);
    }

    #[test]
    fn test_max_drawdown_empty() {
        assert_eq!(max_drawdown(&[]), 0.0);
    }

    #[test]
    fn test_var_95() {
        let returns = vec![-0.05, -0.03, -0.01, 0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.07,
                          -0.08, -0.02, 0.01, 0.03, 0.05, 0.02, -0.04, 0.06, -0.01, 0.03];
        let var = value_at_risk(&returns, 0.05);
        assert!(var > 0.0, "VaR should be positive (loss)");
    }

    #[test]
    fn test_var_empty() {
        assert_eq!(value_at_risk(&[], 0.05), 0.0);
    }

    #[test]
    fn test_sharpe_positive() {
        let returns = vec![0.02, 0.03, 0.01, 0.04, 0.02, 0.03, 0.01, 0.02, 0.03, 0.02, 0.01, 0.02];
        let sharpe = sharpe_ratio(&returns, 0.045);
        assert!(sharpe > 0.0);
    }

    #[test]
    fn test_sharpe_zero_vol() {
        let returns = vec![0.01, 0.01, 0.01, 0.01];
        assert_eq!(sharpe_ratio(&returns, 0.045), 0.0);
    }

    #[test]
    fn test_sortino_no_downside() {
        // All returns above risk-free rate -> no downside -> returns 0.0
        let returns = vec![0.10, 0.20, 0.15, 0.12];
        assert_eq!(sortino_ratio(&returns, 0.045), 0.0);
    }

    #[test]
    fn test_total_return() {
        let returns = vec![0.10, 0.05, -0.03];
        let total = total_return(&returns);
        let expected = (1.10 * 1.05 * 0.97) - 1.0;
        assert!((total - expected).abs() < 1e-10);
    }

    #[test]
    fn test_annualized_return_zero_periods() {
        assert_eq!(annualized_return(&[0.01, 0.02], 0), 0.0);
    }

    #[test]
    fn test_win_rate_empty() {
        assert_eq!(win_rate(&[]), 0.0);
    }

    #[test]
    fn test_calmar_no_drawdown() {
        // Monotonically positive returns -> no drawdown -> 0.0
        let returns = vec![0.01, 0.02, 0.03];
        assert_eq!(calmar_ratio(&returns, 12), 0.0);
    }

    #[test]
    fn test_portfolio_returns_empty_holdings() {
        let returns = portfolio_returns(&[]);
        assert!(returns.is_empty());
    }

    #[test]
    fn test_value_at_risk_with_nan() {
        // NaN values should not panic the sort
        let returns = vec![0.01, f64::NAN, -0.02, 0.03];
        // Should not panic
        let _var = value_at_risk(&returns, 0.05);
    }
}
