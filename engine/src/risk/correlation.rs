use crate::api::portfolio::{CorrelationInput, CorrelationResponse};

/// Compute pairwise correlation matrix for holdings.
///
/// # Parameters
/// - `input.holdings`: At least 2 holdings with return series
/// - `input.window_days`: Rolling window size (default: 90)
///
/// # Edge Cases
/// - Single holding: produces a 1x1 identity matrix
/// - Mismatched return lengths: uses minimum overlapping length
/// - Constant return series: correlation is 0.0 (zero variance)
pub fn compute_matrix(input: &CorrelationInput) -> CorrelationResponse {
    let n = input.holdings.len();
    let window = input.window_days.unwrap_or(90).max(2);
    let symbols: Vec<String> = input.holdings.iter().map(|h| h.symbol.clone()).collect();

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
                matrix[i][j] = corr;
                matrix[j][i] = corr;
            }
        }
    }

    CorrelationResponse {
        symbols,
        matrix,
        window_days: window,
    }
}

/// Pearson correlation coefficient over the last `window` observations.
///
/// # Parameters
/// - `x`, `y`: Return series
/// - `window`: Number of trailing observations to use
///
/// # Returns
/// Correlation in [-1.0, 1.0], or 0.0 if:
/// - Fewer than 2 overlapping observations
/// - Either series has zero variance (constant values)
///
/// # Edge Cases
/// - NaN/Infinity values: filtered out before computation
/// - Mismatched lengths: uses the shorter series
fn pearson_correlation(x: &[f64], y: &[f64], window: usize) -> f64 {
    let n = x.len().min(y.len()).min(window);
    if n < 2 {
        return 0.0;
    }

    let x_slice = &x[x.len().saturating_sub(n)..];
    let y_slice = &y[y.len().saturating_sub(n)..];

    // Filter out non-finite pairs
    let pairs: Vec<(f64, f64)> = x_slice
        .iter()
        .zip(y_slice.iter())
        .filter(|(&xi, &yi)| xi.is_finite() && yi.is_finite())
        .map(|(&xi, &yi)| (xi, yi))
        .collect();

    let count = pairs.len();
    if count < 2 {
        return 0.0;
    }

    let mean_x: f64 = pairs.iter().map(|(xi, _)| xi).sum::<f64>() / count as f64;
    let mean_y: f64 = pairs.iter().map(|(_, yi)| yi).sum::<f64>() / count as f64;

    let mut cov = 0.0;
    let mut var_x = 0.0;
    let mut var_y = 0.0;

    for &(xi, yi) in &pairs {
        let dx = xi - mean_x;
        let dy = yi - mean_y;
        cov += dx * dy;
        var_x += dx * dx;
        var_y += dy * dy;
    }

    let denom = (var_x * var_y).sqrt();
    if denom == 0.0 || !denom.is_finite() {
        return 0.0;
    }
    let corr = cov / denom;
    // Clamp to [-1, 1] to handle floating-point drift
    corr.clamp(-1.0, 1.0)
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
    fn test_zero_variance() {
        let x = vec![1.0, 1.0, 1.0, 1.0];
        let y = vec![2.0, 4.0, 6.0, 8.0];
        let corr = pearson_correlation(&x, &y, 4);
        assert_eq!(corr, 0.0);
    }

    #[test]
    fn test_single_observation() {
        let x = vec![1.0];
        let y = vec![2.0];
        let corr = pearson_correlation(&x, &y, 5);
        assert_eq!(corr, 0.0);
    }

    #[test]
    fn test_empty_series() {
        let corr = pearson_correlation(&[], &[], 5);
        assert_eq!(corr, 0.0);
    }

    #[test]
    fn test_with_nan_values() {
        let x = vec![1.0, f64::NAN, 3.0, 4.0, 5.0];
        let y = vec![2.0, 4.0, 6.0, f64::NAN, 10.0];
        // Only 3 valid pairs: (1,2), (3,6), (5,10)
        let corr = pearson_correlation(&x, &y, 5);
        assert!(corr.is_finite());
        assert!(corr > 0.0); // positive relationship
    }

    #[test]
    fn test_correlation_clamped() {
        // Even with floating-point drift, result stays in [-1, 1]
        let x = vec![1.0, 2.0, 3.0, 4.0, 5.0];
        let y = vec![2.0, 4.0, 6.0, 8.0, 10.0];
        let corr = pearson_correlation(&x, &y, 5);
        assert!(corr >= -1.0 && corr <= 1.0);
    }
}
