use crate::api::portfolio::{MonteCarloInput, MonteCarloPercentiles, MonteCarloResponse};
use rand::Rng;

/// Run Monte Carlo simulation on portfolio.
/// Uses geometric Brownian motion with parameters derived from historical returns.
pub fn run_simulation(input: &MonteCarloInput) -> MonteCarloResponse {
    let num_sims = input.num_simulations.unwrap_or(10_000);
    let horizon = input.time_horizon_months.unwrap_or(120); // 10 years default

    // Compute portfolio-level return statistics from holdings
    let all_returns: Vec<f64> = if input.holdings.is_empty() {
        vec![0.0]
    } else {
        let n = input.holdings.iter().map(|h| h.returns.len()).min().unwrap_or(1);
        let total_val: f64 = input.holdings.iter().map(|h| h.market_value).sum();
        if total_val == 0.0 {
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

    let mean = all_returns.iter().sum::<f64>() / all_returns.len() as f64;
    let variance = all_returns
        .iter()
        .map(|r| (r - mean).powi(2))
        .sum::<f64>()
        / (all_returns.len().max(2) - 1) as f64;
    let std_dev = variance.sqrt();

    // Run simulations
    let mut rng = rand::rng();
    let mut final_values: Vec<Vec<f64>> = vec![vec![0.0; horizon + 1]; num_sims];

    for sim in 0..num_sims {
        final_values[sim][0] = input.initial_value;
        for t in 1..=horizon {
            let z: f64 = sample_standard_normal(&mut rng);
            let r = mean + std_dev * z;
            final_values[sim][t] = final_values[sim][t - 1] * (1.0 + r);
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
        values_at_t.sort_by(|a, b| a.partial_cmp(b).unwrap());

        p5.push(percentile(&values_at_t, 0.05));
        p25.push(percentile(&values_at_t, 0.25));
        p50.push(percentile(&values_at_t, 0.50));
        p75.push(percentile(&values_at_t, 0.75));
        p95.push(percentile(&values_at_t, 0.95));
    }

    // Probability of loss
    let losses = final_values
        .iter()
        .filter(|sim| sim[horizon] < input.initial_value)
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

fn percentile(sorted: &[f64], p: f64) -> f64 {
    if sorted.is_empty() {
        return 0.0;
    }
    let idx = (p * (sorted.len() - 1) as f64).round() as usize;
    sorted[idx.min(sorted.len() - 1)]
}

/// Box-Muller transform for standard normal sampling.
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
        assert!(result.percentiles.p50.len() == 13); // 0..=12
        assert!(result.percentiles.p95.last().unwrap() > result.percentiles.p5.last().unwrap());
    }
}
