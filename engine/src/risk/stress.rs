use crate::api::portfolio::{PositionImpact, StressTestInput, StressTestResponse};

/// Historical scenario shock profiles.
/// Each maps asset classes to percentage shocks observed during the event.
///
/// # Supported Scenarios
/// - `gfc_2008`: Global Financial Crisis
/// - `covid_2020`: COVID-19 crash
/// - `rate_shock_2022`: 2022 rate hiking cycle
/// - `dotcom_2000`: Dot-com bubble burst
/// - `stagflation_1970s`: 1970s stagflation
/// - `black_monday_1987`: Black Monday crash
/// - `oil_shock_2020`: Oil price war
///
/// Unknown scenarios fall through to a moderate default shock profile.
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
/// In production, this would use GICS codes or user-defined tags.
///
/// # Returns
/// Currently always returns "equity" as a stub. Real implementation
/// would inspect holdings metadata, GICS sector codes, or user tags.
fn classify_asset(_symbol: &str) -> &'static str {
    // Stub: default to equity. Real implementation uses holdings metadata.
    "equity"
}

/// Run a stress test on the portfolio using a historical scenario or custom shocks.
///
/// # Parameters
/// - `input.holdings`: Portfolio positions
/// - `input.scenario`: Named scenario (e.g., "gfc_2008") or empty if using custom_shocks
/// - `input.custom_shocks`: Optional user-defined shock profile
///
/// # Edge Cases
/// - Zero total portfolio value: returns 0.0 for portfolio_impact_pct
/// - Unknown scenario: falls through to moderate default shocks
/// - Non-finite shock values: treated as 0.0
pub fn run_stress_test(input: &StressTestInput) -> StressTestResponse {
    let shocks: Vec<(&str, f64)> = if let Some(ref custom) = input.custom_shocks {
        custom
            .iter()
            .map(|s| {
                let shock = if s.shock_pct.is_finite() { s.shock_pct } else { 0.0 };
                (s.asset_class.as_str(), shock)
            })
            .collect()
    } else {
        get_scenario_shocks(&input.scenario)
    };

    let shock_map: std::collections::HashMap<&str, f64> =
        shocks.into_iter().collect();

    let total_value: f64 = input.holdings.iter().map(|h| h.market_value).sum();
    let mut total_impact = 0.0;
    let mut position_impacts = Vec::with_capacity(input.holdings.len());

    for holding in &input.holdings {
        let asset_class = classify_asset(&holding.symbol);
        let shock = shock_map.get(asset_class).copied().unwrap_or(-0.10);
        let mv = if holding.market_value.is_finite() { holding.market_value } else { 0.0 };
        let impact_value = mv * shock;
        total_impact += impact_value;

        position_impacts.push(PositionImpact {
            symbol: holding.symbol.clone(),
            impact_pct: shock,
            impact_value: if impact_value.is_finite() { impact_value } else { 0.0 },
        });
    }

    let portfolio_impact_pct = if total_value > 0.0 && total_impact.is_finite() {
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

    fn test_holding(symbol: &str, mv: f64) -> Holding {
        Holding {
            symbol: symbol.to_string(),
            weight: 1.0,
            returns: vec![0.01, -0.01],
            yield_pct: None,
            cost_basis: None,
            market_value: mv,
        }
    }

    #[test]
    fn test_gfc_scenario() {
        let input = StressTestInput {
            holdings: vec![test_holding("SPY", 10000.0)],
            scenario: "gfc_2008".to_string(),
            custom_shocks: None,
        };
        let result = run_stress_test(&input);
        assert_eq!(result.scenario, "gfc_2008");
        assert!(result.portfolio_impact_pct < 0.0);
    }

    #[test]
    fn test_custom_shocks() {
        let input = StressTestInput {
            holdings: vec![test_holding("BTC", 5000.0)],
            scenario: "custom".to_string(),
            custom_shocks: Some(vec![AssetShock {
                asset_class: "equity".to_string(),
                shock_pct: -0.30,
            }]),
        };
        let result = run_stress_test(&input);
        assert!((result.portfolio_impact_pct - (-0.30)).abs() < 1e-10);
    }

    #[test]
    fn test_zero_value_portfolio() {
        let input = StressTestInput {
            holdings: vec![test_holding("SPY", 0.0)],
            scenario: "gfc_2008".to_string(),
            custom_shocks: None,
        };
        let result = run_stress_test(&input);
        assert_eq!(result.portfolio_impact_pct, 0.0);
    }

    #[test]
    fn test_unknown_scenario_uses_default() {
        let input = StressTestInput {
            holdings: vec![test_holding("SPY", 10000.0)],
            scenario: "alien_invasion".to_string(),
            custom_shocks: None,
        };
        let result = run_stress_test(&input);
        // Default shock for equity is -0.20
        assert!((result.portfolio_impact_pct - (-0.20)).abs() < 1e-10);
    }
}
