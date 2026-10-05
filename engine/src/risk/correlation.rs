use crate::api::portfolio::{CorrelationInput, CorrelationResponse};
use crate::num_ffi;

/// Compute pairwise correlation matrix for holdings.
///
/// Builds an NxN Pearson correlation matrix where N = number of holdings.
/// Diagonal is always 1.0 (self-correlation). Matrix is symmetric.
///
/// The O(N^2 * window) core runs through the C numerical core
/// ([`num_ffi::correlation_matrix`], BLAS `dgemm_` covariance) by default;
/// the pure-Rust pairwise path below remains as the fallback and as the
/// reference implementation for the cross-check tests.
///
/// # Parameters
/// - `input.holdings`: portfolio holdings with return histories
/// - `input.window_days`: number of trailing observations to use (default: 90)
///
/// # Edge cases
/// - Empty holdings: returns empty matrix and symbols
/// - Single holding: returns 1x1 matrix with `[[1.0]]`
/// - Constant returns (zero variance): correlation is 0.0
/// - NaN/Infinity in returns: produces 0.0 correlation for affected pairs
///
/// # Alignment note
/// The BLAS kernel needs one common observation window, so all series are
/// aligned to the trailing `m = min(len, window)` observations. For
/// equal-length series (the normal case) this reproduces the old per-pair
/// computation exactly; for mixed-length histories the longer series are
/// truncated to the common window instead of using per-pair minima.
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

    // Fast path: BLAS covariance kernel via the C numerical core.
    if let Some(matrix) = correlation_via_native(input, window) {
        return CorrelationResponse {
            symbols,
            matrix,
            window_days: window,
        };
    }

    // Fallback: pure-Rust pairwise computation (also the test reference).
    CorrelationResponse {
        symbols,
        matrix: pairwise_matrix(input, window),
        window_days: window,
    }
}

/// Correlation matrix via the C/BLAS numerical core.
///
/// Returns `None` when the native call cannot run (degenerate input), in
/// which case the caller falls back to the pure-Rust path.
fn correlation_via_native(input: &CorrelationInput, window: usize) -> Option<Vec<Vec<f64>>> {
    let n = input.holdings.len();
    if n < 2 {
        // 0/1 holdings: the diagonal-only matrix below is already exact.
        let mut matrix = vec![vec![0.0_f64; n]; n];
        for (i, row) in matrix.iter_mut().enumerate() {
            row[i] = 1.0;
        }
        return Some(matrix);
    }

    let m = input
        .holdings
        .iter()
        .map(|h| h.returns.len().min(window))
        .min()
        .unwrap_or(0);
    if m < 2 {
        // Fewer than 2 common observations: every pair is undefined (0.0),
        // diagonal stays 1.0 -- matches the pairwise contract exactly.
        let mut matrix = vec![vec![0.0_f64; n]; n];
        for (i, row) in matrix.iter_mut().enumerate() {
            row[i] = 1.0;
        }
        return Some(matrix);
    }

    // Align every series to the trailing m observations.
    let aligned: Vec<&[f64]> = input
        .holdings
        .iter()
        .map(|h| {
            let len = h.returns.len();
            let take = m.min(len);
            &h.returns[len - take..]
        })
        .collect();

    let mut matrix = num_ffi::correlation_matrix(&aligned).ok()?;
    // Restore the unconditional 1.0 self-correlation (the C kernel reports
    // 0.0 on the diagonal for degenerate series; the engine contract keeps
    // the diagonal at 1.0, as before).
    for (i, row) in matrix.iter_mut().enumerate() {
        row[i] = 1.0;
    }
    Some(matrix)
}

/// Pure-Rust pairwise correlation matrix.
///
/// Kept as the fallback for `compute_matrix` and as the reference
/// implementation for the FFI cross-check tests.
fn pairwise_matrix(input: &CorrelationInput, window: usize) -> Vec<Vec<f64>> {
    let n = input.holdings.len();
    let mut matrix = vec![vec![0.0_f64; n]; n];

    #[allow(clippy::needless_range_loop)]
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
    matrix
}

/// Clamp a correlation value to [-1.0, 1.0].
///
/// Handles NaN and Infinity by returning 0.0 (undefined correlation).
fn clamp_correlation(corr: f64) -> f64 {
    if corr.is_nan() || corr.is_infinite() {
        return 0.0;
    }
    corr.clamp(-1.0, 1.0)
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

    // ---- FFI cross-checks: the default path runs through the C/BLAS core.
    //
    // These tests prove (a) the native kernel path is actually exercised by
    // `compute_matrix` -- a stubbed C layer returning zeros/identity cannot
    // produce these exact non-trivial values -- and (b) it agrees with the
    // pure-Rust reference to 1e-12 on representative inputs.

    fn sample_holdings(n: usize, m: usize) -> Vec<crate::api::portfolio::Holding> {
        use crate::api::portfolio::Holding;
        (0..n)
            .map(|j| Holding {
                symbol: format!("S{}", j),
                weight: 1.0 / n as f64,
                returns: (0..m)
                    .map(|i| {
                        (i as f64 * 0.37 + j as f64 * 1.7).sin()
                            + 0.5 * ((i * (j + 3)) as f64 * 0.11).cos()
                    })
                    .collect(),
                yield_pct: None,
                cost_basis: None,
                market_value: 1000.0,
            })
            .collect()
    }

    #[test]
    fn test_compute_matrix_uses_native_path() {
        let holdings = sample_holdings(5, 60);
        let input = CorrelationInput {
            holdings,
            window_days: Some(60),
        };
        // The native path must be taken (not the fallback): Some, not None.
        let native = correlation_via_native(&input, 60);
        assert!(native.is_some(), "native BLAS path was not exercised");
        let matrix = native.unwrap();
        // Non-trivial values: a stubbed kernel cannot produce these.
        assert!(matrix.iter().flatten().any(|&v| v.abs() > 0.01 && v.abs() < 1.0));
        for (i, row) in matrix.iter().enumerate() {
            assert_eq!(row[i], 1.0);
            for (j, v) in row.iter().enumerate() {
                assert!((v - matrix[j][i]).abs() < 1e-15, "symmetric");
                assert!(*v >= -1.0 && *v <= 1.0, "clamped");
            }
        }
    }

    #[test]
    fn test_compute_matrix_matches_pure_rust_reference() {
        // Equal-length series: the aligned BLAS computation must reproduce
        // the old pairwise computation to 1e-12.
        let holdings = sample_holdings(6, 90);
        let input = CorrelationInput {
            holdings,
            window_days: Some(90),
        };
        let result = compute_matrix(&input);
        let reference = pairwise_matrix(&input, 90);
        assert_eq!(result.matrix.len(), reference.len());
        let max_diff = result
            .matrix
            .iter()
            .zip(reference.iter())
            .flat_map(|(a, b)| a.iter().zip(b.iter()).map(|(x, y)| (x - y).abs()))
            .fold(0.0, f64::max);
        assert!(
            max_diff < 1e-12,
            "FFI path diverged from pure-Rust reference: {}",
            max_diff
        );
    }

    #[test]
    fn test_compute_matrix_degenerate_inputs() {
        use crate::api::portfolio::Holding;
        // Constant + NaN series: affected pairs are 0.0, diagonal stays 1.0.
        let holdings = vec![
            Holding {
                symbol: "C".to_string(),
                weight: 0.5,
                returns: vec![0.01; 20],
                yield_pct: None,
                cost_basis: None,
                market_value: 1000.0,
            },
            Holding {
                symbol: "N".to_string(),
                weight: 0.5,
                returns: vec![0.01, f64::NAN, 0.03, 0.02, 0.01, 0.02, 0.03, 0.01,
                              0.02, 0.03, 0.01, 0.02, 0.03, 0.01, 0.02, 0.03,
                              0.01, 0.02, 0.03, 0.02],
                yield_pct: None,
                cost_basis: None,
                market_value: 1000.0,
            },
        ];
        let result = compute_matrix(&CorrelationInput {
            holdings,
            window_days: Some(20),
        });
        assert_eq!(result.matrix[0][0], 1.0);
        assert_eq!(result.matrix[1][1], 1.0);
        assert_eq!(result.matrix[0][1], 0.0);
        assert_eq!(result.matrix[1][0], 0.0);
        // And the pure-Rust fallback agrees exactly on this input too.
        let reference = pairwise_matrix(
            &CorrelationInput {
                holdings: vec![
                    Holding {
                        symbol: "C".to_string(),
                        weight: 0.5,
                        returns: vec![0.01; 20],
                        yield_pct: None,
                        cost_basis: None,
                        market_value: 1000.0,
                    },
                    Holding {
                        symbol: "N".to_string(),
                        weight: 0.5,
                        returns: vec![0.01, f64::NAN, 0.03, 0.02, 0.01, 0.02, 0.03, 0.01,
                                      0.02, 0.03, 0.01, 0.02, 0.03, 0.01, 0.02, 0.03,
                                      0.01, 0.02, 0.03, 0.02],
                        yield_pct: None,
                        cost_basis: None,
                        market_value: 1000.0,
                    },
                ],
                window_days: Some(20),
            },
            20,
        );
        assert_eq!(result.matrix, reference);
    }
}
