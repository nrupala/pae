use crate::api::portfolio::{PositionImpact, StressTestInput, StressTestResponse};

/// Historical scenario shock profiles.
/// Each maps asset classes to percentage shocks observed during the event.
///
/// Supported scenarios:
/// - `gfc_2008`: Global Financial Crisis
/// - `covid_2020`: COVID-19 pandemic crash
/// - `rate_shock_2022`: 2022 interest rate hiking cycle
/// - `dotcom_2000`: Dot-com bubble burst
/// - `stagflation_1970s`: 1970s stagflation era
/// - `black_monday_1987`: October 1987 crash
/// - `oil_shock_2020`: Oil price collapse
/// - Any other string: uses a moderate default shock profile
fn get_scenario_shocks(name: &str) -> Vec<(&'static str, f64)> {
    match name {
        "gfc_2008" => vec![
            ("equity", -0.50),
            ("fixed_income", 0.05),
            ("commodity", -0.35),
            ("real_estate", -0.30),
            ("preferred", -0.25),
        ],
        "covid_2020" => vec![
            ("equity", -0.34),
            ("fixed_income", 0.02),
            ("commodity", -0.25),
            ("real_estate", -0.15),
            ("preferred", -0.20),
        ],
        "rate_shock_2022" => vec![
            ("equity", -0.25),
            ("fixed_income", -0.15),
            ("commodity", 0.10),
            ("real_estate", -0.10),
            ("preferred", -0.15),
        ],
        "dotcom_2000" => vec![
            ("equity", -0.45),
            ("fixed_income", 0.10),
            ("commodity", -0.05),
            ("real_estate", 0.05),
            ("preferred", -0.10),
        ],
        "stagflation_1970s" => vec![
            ("equity", -0.40),
            ("fixed_income", -0.10),
            ("commodity", 0.30),
            ("real_estate", -0.15),
            ("preferred", -0.20),
        ],
        "black_monday_1987" => vec![
            ("equity", -0.22),
            ("fixed_income", 0.03),
            ("commodity", -0.05),
            ("real_estate", -0.05),
            ("preferred", -0.10),
        ],
        "oil_shock_2020" => vec![
            ("equity", -0.15),
            ("fixed_income", 0.02),
            ("commodity", -0.60),
            ("real_estate", -0.05),
            ("preferred", -0.10),
        ],
        _ => vec![
            ("equity", -0.20),
            ("fixed_income", -0.05),
            ("commodity", -0.10),
            ("real_estate", -0.10),
            ("preferred", -0.10),
        ],
    }
}

/// Classify a holding into a broad asset class.
///
/// Stub implementation: defaults all holdings to "equity".
/// Production version uses GICS codes or user-defined tags from holdings metadata.
fn classify_asset(_symbol: &str) -> &'static str {
    // Stub: default to equity. Real implementation uses holdings metadata.
    "equity"
}

/// Run a scenario-based stress test on the portfolio.
///
/// Applies either a historical scenario's shock profile or custom shocks
/// to each holding based on its asset class classification.
///
/// # Parameters
/// - `input.holdings`: portfolio holdings (must be non-empty; validated by handler)
/// - `input.scenario`: scenario name (matches a historical profile or falls back to defaults)
/// - `input.custom_shocks`: optional custom shock percentages per asset class
///
/// # Edge cases
/// - Empty holdings: returns zero impact (though handler validates before calling)
/// - Zero total market value: portfolio_impact_pct is 0.0
/// - Unknown asset class in custom shocks: uses -0.10 default
/// - NaN/Infinity in shock_pct: sanitized to 0.0
///
/// # Returns
/// `StressTestResponse` with per-position and aggregate portfolio impact.
pub fn run_stress_test(input: &StressTestInput) -> StressTestResponse {
    let shocks: Vec<(&str, f64)> = if let Some(ref custom) = input.custom_shocks {
        custom
            .iter()
            .map(|s| {
                // Sanitize: if shock_pct is NaN/Infinity, treat as zero shock
                let pct = if s.shock_pct.is_nan() || s.shock_pct.is_infinite() {
                    0.0
                } else {
                    s.shock_pct
                };
                (s.asset_class.as_str(), pct)
            })
            .collect()
    } else {
        get_scenario_shocks(&input.scenario)
    };

    let shock_map: std::collections::HashMap<&str, f64> =
        shocks.into_iter().collect();

    let total_value: f64 = input.holdings.iter().map(|h| h.market_value).sum();
    let mut total_impact = 0.0;
    let mut position_impacts = Vec::new();

    for holding in &input.holdings {
        let asset_class = classify_asset(&holding.symbol);
        let shock = shock_map.get(asset_class).copied().unwrap_or(-0.10);
        let impact_value = holding.market_value * shock;

        // Guard against NaN/Infinity propagation from market_value
        let safe_impact = if impact_value.is_nan() || impact_value.is_infinite() {
            0.0
        } else {
            impact_value
        };

        total_impact += safe_impact;

        position_impacts.push(PositionImpact {
            symbol: holding.symbol.clone(),
            impact_pct: shock,
            impact_value: safe_impact,
        });
    }

    let portfolio_impact_pct = if total_value > 0.0 && !total_value.is_nan() && !total_value.is_infinite() {
        total_impact / total_value
    } else {
        0.0
    };

    StressTestResponse {
        scenario: input.scenario.clone(),
        portfolio_impact_pct,
        position_impacts,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::api::portfolio::{AssetShock, Holding, StressTestInput};

    fn sample_holdings() -> Vec<Holding> {
        vec![
            Holding {
                symbol: "SPY".to_string(),
                weight: 0.6,
                returns: vec![0.01, -0.02],
                yield_pct: None,
                cost_basis: None,
                market_value: 60000.0,
            },
            Holding {
                symbol: "AGG".to_string(),
                weight: 0.4,
                returns: vec![0.005, 0.003],
                yield_pct: None,
                cost_basis: None,
                market_value: 40000.0,
            },
        ]
    }

    #[test]
    fn test_stress_gfc_2008() {
        let input = StressTestInput {
            holdings: sample_holdings(),
            scenario: "gfc_2008".to_string(),
            custom_shocks: None,
        };
        let result = run_stress_test(&input);
        assert_eq!(result.scenario, "gfc_2008");
        assert!(result.portfolio_impact_pct < 0.0, "GFC should produce negative impact");
        assert_eq!(result.position_impacts.len(), 2);
    }

    #[test]
    fn test_stress_custom_shocks() {
        let input = StressTestInput {
            holdings: sample_holdings(),
            scenario: "custom".to_string(),
            custom_shocks: Some(vec![
                AssetShock { asset_class: "equity".to_string(), shock_pct: -0.30 },
            ]),
        };
        let result = run_stress_test(&input);
        assert!(result.portfolio_impact_pct < 0.0);
    }

    #[test]
    fn test_stress_empty_holdings() {
        let input = StressTestInput {
            holdings: vec![],
            scenario: "gfc_2008".to_string(),
            custom_shocks: None,
        };
        let result = run_stress_test(&input);
        assert_eq!(result.portfolio_impact_pct, 0.0);
        assert!(result.position_impacts.is_empty());
    }

    #[test]
    fn test_stress_zero_market_value() {
        let input = StressTestInput {
            holdings: vec![Holding {
                symbol: "ZERO".to_string(),
                weight: 1.0,
                returns: vec![0.0],
                yield_pct: None,
                cost_basis: None,
                market_value: 0.0,
            }],
            scenario: "gfc_2008".to_string(),
            custom_shocks: None,
        };
        let result = run_stress_test(&input);
        assert_eq!(result.portfolio_impact_pct, 0.0);
    }

    #[test]
    fn test_stress_nan_shock_sanitized() {
        let input = StressTestInput {
            holdings: sample_holdings(),
            scenario: "custom".to_string(),
            custom_shocks: Some(vec![
                AssetShock { asset_class: "equity".to_string(), shock_pct: f64::NAN },
            ]),
        };
        let result = run_stress_test(&input);
        // NaN shock should be sanitized to 0.0, so zero impact
        assert_eq!(result.portfolio_impact_pct, 0.0);
    }

    #[test]
    fn test_stress_unknown_scenario_uses_defaults() {
        let input = StressTestInput {
            holdings: sample_holdings(),
            scenario: "unknown_scenario_xyz".to_string(),
            custom_shocks: None,
        };
        let result = run_stress_test(&input);
        // Should use the default moderate shock profile
        assert!(result.portfolio_impact_pct < 0.0);
    }
}
