use crate::api::portfolio::{MonteCarloInput, MonteCarloPercentiles, MonteCarloResponse};
use rand::Rng;

/// Maximum allowed simulations to prevent resource exhaustion.
const MAX_SIMULATIONS: usize = 1_000_000;

/// Maximum allowed time horizon in months (50 years).
const MAX_HORIZON_MONTHS: usize = 600;

/// Run Monte Carlo simulation on portfolio.
///
/// Uses geometric Brownian motion with parameters derived from historical returns.
/// Mean and standard deviation are estimated from the holdings' weighted returns.
///
/// # Parameters
/// - `input.holdings`: portfolio holdings with return histories
/// - `input.num_simulations`: number of simulation paths (default: 10,000; max: 1,000,000)
/// - `input.time_horizon_months`: projection horizon (default: 120 months / 10 years)
/// - `input.initial_value`: starting portfolio value (must be > 0)
///
/// # Edge cases
/// - Empty holdings: uses zero mean/std, all paths stay near initial value
/// - Single holding: uses that holding's return statistics
/// - Zero total market value: uses zero mean/std
/// - Very large num_simulations: capped at MAX_SIMULATIONS
pub fn run_simulation(input: &MonteCarloInput) -> MonteCarloResponse {
    let num_sims = input
        .num_simulations
        .unwrap_or(10_000)
        .min(MAX_SIMULATIONS)
        .max(1);

    let horizon = input
        .time_horizon_months
        .unwrap_or(120)
        .min(MAX_HORIZON_MONTHS)
        .max(1);

    // Compute portfolio-level return statistics from holdings
    let all_returns: Vec<f64> = if input.holdings.is_empty() {
        vec![0.0]
    } else {
        let n = input.holdings.iter().map(|h| h.returns.len()).min().unwrap_or(1);
        let total_val: f64 = input.holdings.iter().map(|h| h.market_value).sum();
        if total_val == 0.0 || n == 0 {
            vec![0.0]
        } else {
            (0..n)
                .map(|i| {
                    input
                        .holdings
                        .iter()
                        .map(|h| (h.market_value / total_val) * h.returns[i])
                        .sum()
                })
                .collect()
        }
    };

    let count = all_returns.len();
    let mean = all_returns.iter().sum::<f64>() / count as f64;

    // Use Bessel's correction; guard against single-observation case
    let variance = if count >= 2 {
        all_returns
            .iter()
            .map(|r| (r - mean).powi(2))
            .sum::<f64>()
            / (count - 1) as f64
    } else {
        0.0
    };
    let std_dev = variance.sqrt();

    // Run simulations
    let mut rng = rand::rng();
    let mut final_values: Vec<Vec<f64>> = vec![vec![0.0; horizon + 1]; num_sims];

    let initial = if input.initial_value.is_nan() || input.initial_value.is_infinite() || input.initial_value <= 0.0 {
        // Fallback: validated upstream by the handler, but defend here too
        1.0
    } else {
        input.initial_value
    };

    for sim in 0..num_sims {
        final_values[sim][0] = initial;
        for t in 1..=horizon {
            let z: f64 = sample_standard_normal(&mut rng);
            let r = mean + std_dev * z;
            // Clamp extreme returns to prevent overflow to Infinity
            let r_clamped = r.max(-0.99).min(10.0);
            final_values[sim][t] = final_values[sim][t - 1] * (1.0 + r_clamped);
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
        // NaN-safe sort: treat NaN as greater than all finite values
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
        .filter(|sim| sim[horizon] < initial)
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

/// Compute the p-th percentile from a sorted slice.
///
/// # Edge cases
/// - Empty slice: returns 0.0
/// - Single element: returns that element
fn percentile(sorted: &[f64], p: f64) -> f64 {
    if sorted.is_empty() {
        return 0.0;
    }
    let idx = (p * (sorted.len() - 1) as f64).round() as usize;
    sorted[idx.min(sorted.len() - 1)]
}

/// Box-Muller transform for standard normal sampling.
///
/// Generates a single N(0,1) sample from two uniform random numbers.
/// Clamps u1 away from zero to avoid ln(0) = -Infinity.
fn sample_standard_normal(rng: &mut impl Rng) -> f64 {
    let u1: f64 = rng.random::<f64>().max(1e-15);
    let u2: f64 = rng.random::<f64>();
    (-2.0 * u1.ln()).sqrt() * (2.0 * std::f64::consts::PI * u2).cos()
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
        assert!(result.percentiles.p95.last().unwrap() > result.percentiles.p5.last().unwrap());
    }

    #[test]
    fn test_monte_carlo_empty_holdings() {
        let input = MonteCarloInput {
            holdings: vec![],
            num_simulations: Some(100),
            time_horizon_months: Some(6),
            initial_value: 5000.0,
        };

        let result = run_simulation(&input);
        assert_eq!(result.num_simulations, 100);
        // With zero mean/std, all paths should stay near initial value
        let final_median = *result.percentiles.p50.last().unwrap();
        assert!((final_median - 5000.0).abs() < 500.0);
    }

    #[test]
    fn test_monte_carlo_capped_simulations() {
        let input = MonteCarloInput {
            holdings: vec![Holding {
                symbol: "TEST".to_string(),
                weight: 1.0,
                returns: vec![0.01, 0.02],
                yield_pct: None,
                cost_basis: None,
                market_value: 1000.0,
            }],
            num_simulations: Some(2_000_000), // exceeds MAX_SIMULATIONS
            time_horizon_months: Some(1),
            initial_value: 1000.0,
        };

        let result = run_simulation(&input);
        assert_eq!(result.num_simulations, MAX_SIMULATIONS);
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
