use crate::api::portfolio::{CorrelationInput, CorrelationResponse};

/// Compute pairwise correlation matrix for holdings.
pub fn compute_matrix(input: &CorrelationInput) -> CorrelationResponse {
    let n = input.holdings.len();
    let window = input.window_days.unwrap_or(90);
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
fn pearson_correlation(x: &[f64], y: &[f64], window: usize) -> f64 {
    let n = x.len().min(y.len()).min(window);
    if n < 2 {
        return 0.0;
    }

    let x_slice = &x[x.len().saturating_sub(n)..];
    let y_slice = &y[y.len().saturating_sub(n)..];

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
}
