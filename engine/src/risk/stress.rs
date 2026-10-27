// Copyright (C) 2026 2351485 ALBERTA INC.
// SPDX-License-Identifier: AGPL-3.0-or-later

use crate::api::portfolio::{PositionImpact, StressTestInput, StressTestResponse};

/// Canonical scenario name for a caller-supplied scenario string.
///
/// The canonical names are the table keys below. Three legacy short
/// names are accepted as aliases because shipped callers use them:
/// the dashboard's scenario picker sends `2008` / `2020` /
/// `rate_shock`, and the MCP `stress_test` tool historically
/// documented the same short names. Before Phase 3D those strings
/// matched NO table key and silently fell through to the flat
/// default profile — the alias map is the fix, kept in exactly one
/// place so every caller resolves identically.
fn canonical_scenario(name: &str) -> &str {
    match name {
        "2008" => "gfc_2008",
        "2020" => "covid_2020",
        "rate_shock" => "rate_shock_2022",
        other => other,
    }
}

/// Historical scenario shock profiles.
/// Each maps asset classes to fractional shocks observed during the event
/// (-0.50 = the class loses half its value). Cash is an explicit 0.0 in
/// every profile: before Phase 3D the tables had no "cash" entry at all,
/// so a cash holding fell through to the -0.10 unknown-class default and
/// was reported as losing 10% in a crisis it does not participate in.
///
/// Supported scenarios:
/// - `gfc_2008`: Global Financial Crisis (alias: `2008`)
/// - `covid_2020`: COVID-19 pandemic crash (alias: `2020`)
/// - `rate_shock_2022`: 2022 interest rate hiking cycle (alias: `rate_shock`)
/// - `dotcom_2000`: Dot-com bubble burst
/// - `stagflation_1970s`: 1970s stagflation era
/// - `black_monday_1987`: October 1987 crash
/// - `oil_shock_2020`: Oil price collapse
/// - Any other string: uses a moderate default shock profile
fn get_scenario_shocks(name: &str) -> Vec<(&'static str, f64)> {
    match canonical_scenario(name) {
        "gfc_2008" => vec![
            ("equity", -0.50),
            ("fixed_income", 0.05),
            ("commodity", -0.35),
            ("real_estate", -0.30),
            ("preferred", -0.25),
            ("cash", 0.0),
        ],
        "covid_2020" => vec![
            ("equity", -0.34),
            ("fixed_income", 0.02),
            ("commodity", -0.25),
            ("real_estate", -0.15),
            ("preferred", -0.20),
            ("cash", 0.0),
        ],
        "rate_shock_2022" => vec![
            ("equity", -0.25),
            ("fixed_income", -0.15),
            ("commodity", 0.10),
            ("real_estate", -0.10),
            ("preferred", -0.15),
            ("cash", 0.0),
        ],
        "dotcom_2000" => vec![
            ("equity", -0.45),
            ("fixed_income", 0.10),
            ("commodity", -0.05),
            ("real_estate", 0.05),
            ("preferred", -0.10),
            ("cash", 0.0),
        ],
        "stagflation_1970s" => vec![
            ("equity", -0.40),
            ("fixed_income", -0.10),
            ("commodity", 0.30),
            ("real_estate", -0.15),
            ("preferred", -0.20),
            ("cash", 0.0),
        ],
        "black_monday_1987" => vec![
            ("equity", -0.22),
            ("fixed_income", 0.03),
            ("commodity", -0.05),
            ("real_estate", -0.05),
            ("preferred", -0.10),
            ("cash", 0.0),
        ],
        "oil_shock_2020" => vec![
            ("equity", -0.15),
            ("fixed_income", 0.02),
            ("commodity", -0.60),
            ("real_estate", -0.05),
            ("preferred", -0.10),
            ("cash", 0.0),
        ],
        _ => vec![
            ("equity", -0.20),
            ("fixed_income", -0.05),
            ("commodity", -0.10),
            ("real_estate", -0.10),
            ("preferred", -0.10),
            ("cash", 0.0),
        ],
    }
}

/// Classify a holding into a broad asset class for shock lookup.
///
/// Uses the holding's own `asset_class` when the caller supplied one
/// (the analytics layer stores one per holding and passes it through),
/// normalized to lowercase. When absent or blank, falls back to the
/// historic default "equity" — callers that do not classify holdings
/// keep the pre-3D behavior, now as an explicit documented default
/// rather than a stub. (Phase 3D buyer-walk fix: previously this
/// ignored holdings metadata entirely, so every position — cash and
/// bonds included — took the equity shock.)
fn classify_asset(holding: &crate::api::portfolio::Holding) -> String {
    match holding.asset_class.as_deref() {
        Some(class) if !class.trim().is_empty() => class.trim().to_lowercase(),
        _ => "equity".to_string(),
    }
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
/// Both `impact_pct` fields are TRUE PERCENTS (impact_value / base_value
/// * 100): a -0.50 equity shock reports `impact_pct: -50.0`, not -0.50.
///   (Before Phase 3D they were raw fractions and the dashboard printed
///   them with a "%" sign, understating every impact a hundredfold.)
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
        let asset_class = classify_asset(holding);
        let shock = shock_map.get(asset_class.as_str()).copied().unwrap_or(-0.10);
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
            // True percent: the shock IS impact/base for a single
            // position (base = its market value), so pct = shock * 100.
            impact_pct: shock * 100.0,
            impact_value: safe_impact,
        });
    }

    let portfolio_impact_pct = if total_value > 0.0 && !total_value.is_nan() && !total_value.is_infinite() {
        total_impact / total_value * 100.0
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
                asset_class: None,
            },
            Holding {
                symbol: "AGG".to_string(),
                weight: 0.4,
                returns: vec![0.005, 0.003],
                yield_pct: None,
                cost_basis: None,
                market_value: 40000.0,
                asset_class: None,
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
                asset_class: None,
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

    fn classified(symbol: &str, class: &str, market_value: f64) -> Holding {
        Holding {
            symbol: symbol.to_string(),
            weight: 0.0,
            returns: vec![],
            yield_pct: None,
            cost_basis: None,
            market_value,
            asset_class: Some(class.to_string()),
        }
    }

    #[test]
    fn test_stress_per_class_shocks_and_true_percent() {
        // GFC: cash is explicitly unshocked, fixed_income gains 5%,
        // equity halves. impact_pct is a true percent (Phase 3D).
        let input = StressTestInput {
            holdings: vec![
                classified("CASH", "cash", 5000.0),
                classified("EQ", "equity", 10000.0),
                classified("BD", "fixed_income", 10000.0),
            ],
            scenario: "gfc_2008".to_string(),
            custom_shocks: None,
        };
        let result = run_stress_test(&input);
        let by_symbol: std::collections::HashMap<&str, &PositionImpact> = result
            .position_impacts
            .iter()
            .map(|p| (p.symbol.as_str(), p))
            .collect();
        assert_eq!(by_symbol["CASH"].impact_pct, 0.0);
        assert_eq!(by_symbol["CASH"].impact_value, 0.0);
        assert_eq!(by_symbol["EQ"].impact_pct, -50.0);
        assert_eq!(by_symbol["EQ"].impact_value, -5000.0);
        assert_eq!(by_symbol["BD"].impact_pct, 5.0);
        assert_eq!(by_symbol["BD"].impact_value, 500.0);
        // (-5000 + 500 + 0) / 25000 * 100 = -18.0
        assert_eq!(result.portfolio_impact_pct, -18.0);
    }

    #[test]
    fn test_stress_legacy_aliases_resolve_to_canonical_tables() {
        // The dashboard sends "2008"; it must produce the gfc_2008
        // table, not the flat default profile (Phase 3D root cause).
        let holdings = vec![classified("EQ", "equity", 10000.0)];
        for (alias, canonical) in [
            ("2008", "gfc_2008"),
            ("2020", "covid_2020"),
            ("rate_shock", "rate_shock_2022"),
        ] {
            let via_alias = run_stress_test(&StressTestInput {
                holdings: holdings.clone(),
                scenario: alias.to_string(),
                custom_shocks: None,
            });
            let via_canonical = run_stress_test(&StressTestInput {
                holdings: holdings.clone(),
                scenario: canonical.to_string(),
                custom_shocks: None,
            });
            assert_eq!(
                via_alias.portfolio_impact_pct, via_canonical.portfolio_impact_pct,
                "alias {alias} must resolve to {canonical}"
            );
        }
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
