use crate::api::portfolio::Holding;

/// Compute weighted portfolio returns from holdings.
/// Weights are derived from market_value relative to total portfolio value.
///
/// # Edge Cases
/// - Empty holdings: returns empty vec
/// - Zero total value: returns empty vec
/// - Mismatched return lengths: uses minimum length across all holdings
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
                .map(|h| {
                    let w = h.market_value / total_value;
                    let r = h.returns.get(i).copied().unwrap_or(0.0);
                    w * r
                })
                .sum()
        })
        .collect()
}

/// Annualized volatility (standard deviation of returns * sqrt(periods_per_year)).
///
/// # Edge Cases
/// - Fewer than 2 returns: returns 0.0 (undefined volatility)
/// - All identical returns: returns 0.0
pub fn volatility(returns: &[f64]) -> f64 {
    if returns.len() < 2 {
        return 0.0;
    }
    let mean = returns.iter().sum::<f64>() / returns.len() as f64;
    let variance = returns.iter().map(|r| (r - mean).powi(2)).sum::<f64>()
        / (returns.len() - 1) as f64;
    let vol = variance.sqrt();
    if vol.is_finite() { vol } else { 0.0 }
}

/// Annualized Sharpe ratio.
/// Sharpe = (mean_return - risk_free_per_period) / volatility.
///
/// # Edge Cases
/// - Fewer than 2 returns: returns 0.0
/// - Zero volatility: returns 0.0 (avoid division by zero)
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
    let ratio = (mean - rf_period) / vol;
    if ratio.is_finite() { ratio } else { 0.0 }
}

/// Sortino ratio (penalizes only downside volatility).
/// Sortino = (mean_return - risk_free_per_period) / downside_deviation.
///
/// # Edge Cases
/// - Fewer than 2 returns: returns 0.0
/// - No downside returns: returns f64::INFINITY (no downside risk)
/// - Zero downside deviation: returns f64::INFINITY
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
        return f64::INFINITY;
    }

    let downside_dev = (downside_returns.iter().sum::<f64>() / downside_returns.len() as f64).sqrt();
    if downside_dev == 0.0 || !downside_dev.is_finite() {
        return f64::INFINITY;
    }

    let ratio = (mean - rf_period) / downside_dev;
    if ratio.is_finite() { ratio } else { 0.0 }
}

/// Historical Value at Risk at a given confidence level.
/// alpha = 0.05 for 95% VaR, 0.01 for 99% VaR.
///
/// # Edge Cases
/// - Empty returns: returns 0.0
/// - alpha <= 0 or alpha >= 1: clamped to valid range
/// - NaN values in returns: sorted to end, effectively ignored
pub fn value_at_risk(returns: &[f64], alpha: f64) -> f64 {
    if returns.is_empty() {
        return 0.0;
    }

    let alpha = alpha.clamp(0.001, 0.999);

    let mut sorted: Vec<f64> = returns.iter().copied().filter(|v| v.is_finite()).collect();
    if sorted.is_empty() {
        return 0.0;
    }
    sorted.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));

    let index = ((alpha * sorted.len() as f64).floor() as usize).min(sorted.len() - 1);
    -sorted[index] // VaR is reported as positive number
}

/// Conditional VaR (Expected Shortfall) -- average loss beyond VaR threshold.
///
/// # Edge Cases
/// - Empty returns: returns 0.0
/// - alpha produces empty tail: returns 0.0
pub fn conditional_var(returns: &[f64], alpha: f64) -> f64 {
    if returns.is_empty() {
        return 0.0;
    }

    let alpha = alpha.clamp(0.001, 0.999);

    let mut sorted: Vec<f64> = returns.iter().copied().filter(|v| v.is_finite()).collect();
    if sorted.is_empty() {
        return 0.0;
    }
    sorted.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));

    let cutoff = (alpha * sorted.len() as f64).floor() as usize;
    let cutoff = cutoff.max(1).min(sorted.len());

    let tail: Vec<f64> = sorted[..cutoff].to_vec();
    if tail.is_empty() {
        return 0.0;
    }
    let cvar = -(tail.iter().sum::<f64>() / tail.len() as f64);
    if cvar.is_finite() { cvar } else { 0.0 }
}

/// Maximum drawdown from peak.
///
/// # Edge Cases
/// - Empty returns: returns 0.0
/// - All positive returns: returns 0.0 (no drawdown)
/// - Single return: peak-to-trough from that one period
pub fn max_drawdown(returns: &[f64]) -> f64 {
    if returns.is_empty() {
        return 0.0;
    }

    let mut cumulative = Vec::with_capacity(returns.len() + 1);
    cumulative.push(1.0);
    for r in returns {
        let prev = cumulative.last().copied().unwrap_or(1.0);
        let next = prev * (1.0 + r);
        cumulative.push(if next.is_finite() { next } else { prev });
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

    if max_dd.is_finite() { max_dd } else { 0.0 }
}

/// Total cumulative return.
///
/// # Edge Cases
/// - Empty returns: returns 0.0 ((1.0 fold) - 1.0 = 0.0)
/// - Contains extreme values: result may be very large
pub fn total_return(returns: &[f64]) -> f64 {
    let result = returns.iter().fold(1.0, |acc, r| acc * (1.0 + r)) - 1.0;
    if result.is_finite() { result } else { 0.0 }
}

/// Annualized return given periods per year.
///
/// # Edge Cases
/// - Empty returns: returns 0.0
/// - periods_per_year == 0: returns 0.0 (avoid division by zero)
/// - Negative total return exceeding -100%: clamped to avoid NaN from powf
pub fn annualized_return(returns: &[f64], periods_per_year: usize) -> f64 {
    if returns.is_empty() || periods_per_year == 0 {
        return 0.0;
    }
    let total = total_return(returns);
    let years = returns.len() as f64 / periods_per_year as f64;
    if years == 0.0 {
        return 0.0;
    }
    let base = 1.0 + total;
    if base <= 0.0 {
        // Total loss exceeds 100% -- annualization is undefined; return -1.0
        return -1.0;
    }
    let result = base.powf(1.0 / years) - 1.0;
    if result.is_finite() { result } else { 0.0 }
}

/// Win rate (percentage of positive return periods).
///
/// # Edge Cases
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
/// # Edge Cases
/// - Zero drawdown: returns 0.0 (no drawdown to divide by)
/// - periods_per_year == 0: returns 0.0
pub fn calmar_ratio(returns: &[f64], periods_per_year: usize) -> f64 {
    let dd = max_drawdown(returns);
    if dd == 0.0 {
        return 0.0;
    }
    let ann = annualized_return(returns, periods_per_year);
    let ratio = ann / dd;
    if ratio.is_finite() { ratio } else { 0.0 }
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
    fn test_sharpe_empty() {
        assert_eq!(sharpe_ratio(&[], 0.045), 0.0);
    }

    #[test]
    fn test_total_return() {
        let returns = vec![0.10, 0.05, -0.03];
        let total = total_return(&returns);
        let expected = (1.10 * 1.05 * 0.97) - 1.0;
        assert!((total - expected).abs() < 1e-10);
    }

    #[test]
    fn test_total_return_empty() {
        assert_eq!(total_return(&[]), 0.0);
    }

    #[test]
    fn test_annualized_return_zero_periods() {
        assert_eq!(annualized_return(&[0.05, 0.03], 0), 0.0);
    }

    #[test]
    fn test_calmar_no_drawdown() {
        let returns = vec![0.01, 0.02, 0.03]; // all positive -- no drawdown
        assert_eq!(calmar_ratio(&returns, 12), 0.0);
    }

    #[test]
    fn test_portfolio_returns_empty_holdings() {
        assert!(portfolio_returns(&[]).is_empty());
    }

    #[test]
    fn test_sortino_no_downside() {
        // Returns all above risk-free rate
        let returns = vec![0.10, 0.12, 0.15, 0.08, 0.09];
        let s = sortino_ratio(&returns, 0.045);
        assert_eq!(s, f64::INFINITY);
    }

    #[test]
    fn test_win_rate_empty() {
        assert_eq!(win_rate(&[]), 0.0);
    }

    #[test]
    fn test_win_rate_all_positive() {
        assert_eq!(win_rate(&[0.01, 0.02, 0.03]), 1.0);
    }
}
