use crate::api::portfolio::{MonteCarloInput, MonteCarloPercentiles, MonteCarloResponse};
use rand::Rng;

/// Run Monte Carlo simulation on portfolio.
/// Uses geometric Brownian motion with parameters derived from historical returns.
///
/// # Parameters
/// - `input.holdings`: Portfolio holdings with historical returns
/// - `input.num_simulations`: Number of paths (default: 10,000; max: 1,000,000)
/// - `input.time_horizon_months`: Simulation horizon (default: 120 months / 10 years)
/// - `input.initial_value`: Starting portfolio value (must be positive)
///
/// # Edge Cases
/// - Empty holdings: uses zero mean/stddev, producing flat paths
/// - Zero total value: uses zero mean/stddev
/// - Extreme returns: capped at +/-50% per period to prevent overflow
pub fn run_simulation(input: &MonteCarloInput) -> MonteCarloResponse {
    let num_sims = input.num_simulations.unwrap_or(10_000).min(1_000_000).max(1);
    let horizon = input.time_horizon_months.unwrap_or(120).min(600).max(1);

    // Compute portfolio-level return statistics from holdings
    let all_returns: Vec<f64> = if input.holdings.is_empty() {
        vec![0.0]
    } else {
        let n = input.holdings.iter().map(|h| h.returns.len()).min().unwrap_or(1);
        let total_val: f64 = input.holdings.iter().map(|h| h.market_value).sum();
        if total_val <= 0.0 || n == 0 {
            vec![0.0]
        } else {
            (0..n)
                .map(|i| {
                    input
                        .holdings
                        .iter()
                        .map(|h| {
                            let r = h.returns.get(i).copied().unwrap_or(0.0);
                            (h.market_value / total_val) * r
                        })
                        .sum()
                })
                .collect()
        }
    };

    let count = all_returns.len().max(1);
    let mean = all_returns.iter().sum::<f64>() / count as f64;
    let variance = if count >= 2 {
        all_returns
            .iter()
            .map(|r| (r - mean).powi(2))
            .sum::<f64>()
            / (count - 1) as f64
    } else {
        0.0
    };
    let std_dev = if variance.is_finite() { variance.sqrt() } else { 0.0 };

    // Run simulations
    let mut rng = rand::rng();
    let mut final_values: Vec<Vec<f64>> = vec![vec![0.0; horizon + 1]; num_sims];

    let initial = if input.initial_value.is_finite() && input.initial_value > 0.0 {
        input.initial_value
    } else {
        1.0
    };

    for sim in 0..num_sims {
        final_values[sim][0] = initial;
        for t in 1..=horizon {
            let z: f64 = sample_standard_normal(&mut rng);
            // Cap per-period return to prevent overflow
            let r = (mean + std_dev * z).clamp(-0.50, 0.50);
            let next = final_values[sim][t - 1] * (1.0 + r);
            final_values[sim][t] = if next.is_finite() && next >= 0.0 { next } else { 0.0 };
        }
    }

    // Compute percentiles at each time step
    let mut p5 = Vec::with_capacity(horizon + 1);
    let mut p25 = Vec::with_capacity(horizon + 1);
    let mut p50 = Vec::with_capacity(horizon + 1);
    let mut p75 = Vec::with_capacity(horizon + 1);
    let mut p95 = Vec::with_capacity(horizon + 1);

    for t in 0..=horizon {
        let mut values_at_t: Vec<f64> = final_values.iter().map(|sim| sim[t]).collect();
        values_at_t.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));

        p5.push(percentile(&values_at_t, 0.05));
        p25.push(percentile(&values_at_t, 0.25));
        p50.push(percentile(&values_at_t, 0.50));
        p75.push(percentile(&values_at_t, 0.75));
        p95.push(percentile(&values_at_t, 0.95));
    }

    // Probability of loss
    let losses = final_values
        .iter()
        .filter(|sim| {
            sim.last().copied().unwrap_or(0.0) < initial
        })
        .count();

    MonteCarloResponse {
        percentiles: MonteCarloPercentiles {
            p5,
            p25,
            p50,
            p75,
            p95,
        },
        num_simulations: num_sims,
        time_horizon_months: horizon,
        probability_of_loss: losses as f64 / num_sims as f64,
    }
}

/// Compute a percentile value from a sorted slice.
///
/// # Edge Cases
/// - Empty slice: returns 0.0
/// - p outside [0, 1]: clamped
fn percentile(sorted: &[f64], p: f64) -> f64 {
    if sorted.is_empty() {
        return 0.0;
    }
    let p = p.clamp(0.0, 1.0);
    let idx = (p * (sorted.len() - 1) as f64).round() as usize;
    sorted[idx.min(sorted.len() - 1)]
}

/// Box-Muller transform for standard normal sampling.
fn sample_standard_normal(rng: &mut impl Rng) -> f64 {
    let u1: f64 = rng.random::<f64>().max(1e-15);
    let u2: f64 = rng.random::<f64>();
    let z = (-2.0 * u1.ln()).sqrt() * (2.0 * std::f64::consts::PI * u2).cos();
    if z.is_finite() { z } else { 0.0 }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::api::portfolio::Holding;

    #[test]
    fn test_monte_carlo_basic() {
        let input = MonteCarloInput {
            holdings: vec![Holding {
                symbol: "SPY".to_string(),
                weight: 1.0,
                returns: vec![0.01, 0.02, -0.01, 0.015, 0.005, -0.005, 0.02, 0.01, -0.02, 0.03,
                              0.01, 0.005],
                yield_pct: None,
                cost_basis: None,
                market_value: 10000.0,
            }],
            num_simulations: Some(1000),
            time_horizon_months: Some(12),
            initial_value: 10000.0,
        };

        let result = run_simulation(&input);

        assert_eq!(result.num_simulations, 1000);
        assert_eq!(result.time_horizon_months, 12);
        assert!(result.probability_of_loss >= 0.0 && result.probability_of_loss <= 1.0);
        assert_eq!(result.percentiles.p50.len(), 13); // 0..=12
        assert!(result.percentiles.p95.last().unwrap() >= result.percentiles.p5.last().unwrap());
    }

    #[test]
    fn test_monte_carlo_empty_holdings() {
        let input = MonteCarloInput {
            holdings: vec![],
            num_simulations: Some(100),
            time_horizon_months: Some(6),
            initial_value: 1000.0,
        };

        let result = run_simulation(&input);
        assert_eq!(result.num_simulations, 100);
        // With zero mean/stddev, all paths should equal initial value
        for &v in result.percentiles.p50.iter() {
            assert!((v - 1000.0).abs() < 0.01);
        }
    }

    #[test]
    fn test_monte_carlo_clamps_simulations() {
        let input = MonteCarloInput {
            holdings: vec![Holding {
                symbol: "SPY".to_string(),
                weight: 1.0,
                returns: vec![0.01, 0.02],
                yield_pct: None,
                cost_basis: None,
                market_value: 10000.0,
            }],
            num_simulations: Some(0), // should be clamped to 1
            time_horizon_months: Some(1),
            initial_value: 1000.0,
        };

        let result = run_simulation(&input);
        assert_eq!(result.num_simulations, 1);
    }

    #[test]
    fn test_percentile_empty() {
        assert_eq!(percentile(&[], 0.5), 0.0);
    }

    #[test]
    fn test_percentile_single() {
        assert_eq!(percentile(&[42.0], 0.5), 42.0);
    }
}
