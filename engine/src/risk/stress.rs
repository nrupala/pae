use crate::api::portfolio::{PositionImpact, StressTestInput, StressTestResponse};

/// Historical scenario shock profiles.
/// Each maps asset classes to percentage shocks observed during the event.
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
fn classify_asset(_symbol: &str) -> &'static str {
    // Stub: default to equity. Real implementation uses holdings metadata.
    "equity"
}

pub fn run_stress_test(input: &StressTestInput) -> StressTestResponse {
    let shocks: Vec<(&str, f64)> = if let Some(ref custom) = input.custom_shocks {
        custom
            .iter()
            .map(|s| (s.asset_class.as_str(), s.shock_pct))
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
        total_impact += impact_value;

        position_impacts.push(PositionImpact {
            symbol: holding.symbol.clone(),
            impact_pct: shock,
            impact_value,
        });
    }

    let portfolio_impact_pct = if total_value > 0.0 {
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
