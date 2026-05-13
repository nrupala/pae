use crate::api::portfolio::{CorrelationInput, CorrelationResponse};

/// Compute pairwise correlation matrix for holdings.
///
/// Builds an NxN Pearson correlation matrix where N = number of holdings.
/// Diagonal is always 1.0 (self-correlation). Matrix is symmetric.
///
/// # Parameters
/// - `input.holdings`: portfolio holdings with return histories
/// - `input.window_days`: number of trailing observations to use (default: 90)
///
/// # Edge cases
/// - Empty holdings: returns empty matrix and symbols
/// - Single holding: returns 1x1 matrix with `[[1.0]]`
/// - Mismatched return lengths: uses the shorter of the two series
/// - Constant returns (zero variance): correlation is 0.0
/// - NaN/Infinity in returns: produces 0.0 correlation for affected pairs
pub fn compute_matrix(input: &CorrelationInput) -> CorrelationResponse {
    let n = input.holdings.len();
    let window = input.window_days.unwrap_or(90).max(2);
    let symbols: Vec<String> = input.holdings.iter().map(|h| h.symbol.clone()).collect();

    if n == 0 {
        return CorrelationResponse {
            symbols: vec![],
            matrix: vec![],
            window_days: window,
        };
    }

    let mut matrix = vec![vec![0.0_f64; n]; n];

    for i in 0..n {
        for j in 0..n {
            if i == j {
                matrix[i][j] = 1.0;
            } else if j > i {
                let corr = pearson_correlation(
                    &input.holdings[i].returns,
                    &input.holdings[j].returns,
                    window,
                );
                // Clamp to [-1, 1] to handle floating-point drift
                let clamped = clamp_correlation(corr);
                matrix[i][j] = clamped;
                matrix[j][i] = clamped;
            }
        }
    }

    CorrelationResponse {
        symbols,
        matrix,
        window_days: window,
    }
}

/// Clamp a correlation value to [-1.0, 1.0].
///
/// Handles NaN and Infinity by returning 0.0 (undefined correlation).
fn clamp_correlation(corr: f64) -> f64 {
    if corr.is_nan() || corr.is_infinite() {
        return 0.0;
    }
    corr.max(-1.0).min(1.0)
}

/// Pearson correlation coefficient over the last `window` observations.
///
/// # Parameters
/// - `x`, `y`: return series for two holdings
/// - `window`: number of trailing observations to consider
///
/// # Edge cases
/// - Fewer than 2 overlapping observations: returns 0.0
/// - Zero variance in either series: returns 0.0 (undefined correlation)
/// - NaN/Infinity in either series: returns 0.0
fn pearson_correlation(x: &[f64], y: &[f64], window: usize) -> f64 {
    let n = x.len().min(y.len()).min(window);
    if n < 2 {
        return 0.0;
    }

    let x_slice = &x[x.len().saturating_sub(n)..];
    let y_slice = &y[y.len().saturating_sub(n)..];

    // Check for NaN/Infinity in the slices
    if x_slice.iter().any(|v| v.is_nan() || v.is_infinite())
        || y_slice.iter().any(|v| v.is_nan() || v.is_infinite())
    {
        return 0.0;
    }

    let mean_x = x_slice.iter().sum::<f64>() / n as f64;
    let mean_y = y_slice.iter().sum::<f64>() / n as f64;

    let mut cov = 0.0;
    let mut var_x = 0.0;
    let mut var_y = 0.0;

    for i in 0..n {
        let dx = x_slice[i] - mean_x;
        let dy = y_slice[i] - mean_y;
        cov += dx * dy;
        var_x += dx * dx;
        var_y += dy * dy;
    }

    let denom = (var_x * var_y).sqrt();
    if denom == 0.0 {
        return 0.0;
    }
    cov / denom
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_perfect_correlation() {
        let x = vec![1.0, 2.0, 3.0, 4.0, 5.0];
        let y = vec![2.0, 4.0, 6.0, 8.0, 10.0];
        let corr = pearson_correlation(&x, &y, 5);
        assert!((corr - 1.0).abs() < 1e-10);
    }

    #[test]
    fn test_negative_correlation() {
        let x = vec![1.0, 2.0, 3.0, 4.0, 5.0];
        let y = vec![5.0, 4.0, 3.0, 2.0, 1.0];
        let corr = pearson_correlation(&x, &y, 5);
        assert!((corr - (-1.0)).abs() < 1e-10);
    }

    #[test]
    fn test_zero_variance_returns_zero() {
        let x = vec![1.0, 1.0, 1.0, 1.0];
        let y = vec![1.0, 2.0, 3.0, 4.0];
        let corr = pearson_correlation(&x, &y, 4);
        assert_eq!(corr, 0.0);
    }

    #[test]
    fn test_nan_returns_zero() {
        let x = vec![1.0, f64::NAN, 3.0, 4.0];
        let y = vec![2.0, 4.0, 6.0, 8.0];
        let corr = pearson_correlation(&x, &y, 4);
        assert_eq!(corr, 0.0);
    }

    #[test]
    fn test_infinity_returns_zero() {
        let x = vec![1.0, f64::INFINITY, 3.0, 4.0];
        let y = vec![2.0, 4.0, 6.0, 8.0];
        let corr = pearson_correlation(&x, &y, 4);
        assert_eq!(corr, 0.0);
    }

    #[test]
    fn test_single_observation_returns_zero() {
        let x = vec![1.0];
        let y = vec![2.0];
        let corr = pearson_correlation(&x, &y, 10);
        assert_eq!(corr, 0.0);
    }

    #[test]
    fn test_empty_holdings_returns_empty_matrix() {
        let input = crate::api::portfolio::CorrelationInput {
            holdings: vec![],
            window_days: None,
        };
        let result = compute_matrix(&input);
        assert!(result.symbols.is_empty());
        assert!(result.matrix.is_empty());
    }

    #[test]
    fn test_clamp_correlation_nan() {
        assert_eq!(clamp_correlation(f64::NAN), 0.0);
    }

    #[test]
    fn test_clamp_correlation_inf() {
        assert_eq!(clamp_correlation(f64::INFINITY), 0.0);
    }

    #[test]
    fn test_clamp_correlation_normal() {
        assert_eq!(clamp_correlation(0.85), 0.85);
        assert_eq!(clamp_correlation(-0.5), -0.5);
    }
}
