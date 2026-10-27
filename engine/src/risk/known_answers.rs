// Copyright (C) 2026 2351485 ALBERTA INC.
// SPDX-License-Identifier: AGPL-3.0-or-later

//! Known-answer vectors for the risk math (Phase 3C).
//!
//! Every expected value in this module was computed independently of
//! the implementation — by hand from the textbook formula, from a
//! closed form, or from the analytic distribution — and the derivation
//! is written next to each assertion. Tolerances are stated per
//! vector and were NOT loosened to pass: exact vectors use float-noise
//! tolerances (<= 1e-9); the one stochastic Monte Carlo vector uses
//! tolerances derived from the analytic standard error of the
//! estimator (stated inline).
//!
//! Convention note (VaR/CVaR): the engine's historical-VaR convention
//! is `index = floor(alpha * n)` into the ascending sort (i.e. the
//! (floor(alpha*n)+1)-th order statistic), and CVaR averages the
//! worst `max(1, floor(alpha * n))` observations. The vectors below
//! pin THAT convention with hand-sorted series. Whether this
//! convention should instead be a textbook nearest-rank quantile is
//! a product decision, flagged in the Phase 3C report — these tests
//! pin current behavior so any change is deliberate and visible.

#[cfg(test)]
mod tests {
    use crate::api::bonds::{analyze_bond, BondAnalyticsInput, BondCashFlow};
    use crate::api::portfolio::{
        AssetShock, CorrelationInput, Holding, MonteCarloInput, StressTestInput,
    };
    use crate::risk::correlation::compute_matrix;
    use crate::risk::metrics::*;
    use crate::risk::montecarlo::run_simulation;
    use crate::risk::stress::run_stress_test;

    fn holding(symbol: &str, market_value: f64, returns: Vec<f64>) -> Holding {
        Holding {
            symbol: symbol.to_string(),
            weight: 0.0,
            returns,
            yield_pct: None,
            cost_basis: None,
            market_value,
        }
    }

    // -----------------------------------------------------------------
    // VaR / CVaR — hand-sorted series of 20 monthly returns.
    //
    // Series (as given): 0.03, -0.09, 0.01, -0.05, 0.02, -0.01, 0.04,
    // -0.07, 0.00, 0.05, -0.03, 0.06, -0.02, 0.01, -0.04, 0.02, -0.06,
    // 0.03, -0.08, 0.00.
    // Sorted ascending:  -0.09, -0.08, -0.07, -0.06, -0.05, -0.04,
    // -0.03, -0.02, -0.01, 0.00, 0.00, 0.01, 0.01, 0.02, 0.02, 0.03,
    // 0.03, 0.04, 0.05, 0.06.
    // -----------------------------------------------------------------

    fn series_20() -> Vec<f64> {
        vec![
            0.03, -0.09, 0.01, -0.05, 0.02, -0.01, 0.04, -0.07, 0.00, 0.05, -0.03, 0.06, -0.02,
            0.01, -0.04, 0.02, -0.06, 0.03, -0.08, 0.00,
        ]
    }

    #[test]
    fn var_hand_sorted_series() {
        let r = series_20();
        // alpha = 0.05 -> index floor(0.05*20) = 1 -> sorted[1] = -0.08.
        assert!((value_at_risk(&r, 0.05) - 0.08).abs() < 1e-12);
        // alpha = 0.10 -> index floor(0.10*20) = 2 -> sorted[2] = -0.07.
        assert!((value_at_risk(&r, 0.10) - 0.07).abs() < 1e-12);
        // alpha = 0.25 -> index floor(0.25*20) = 5 -> sorted[5] = -0.04.
        assert!((value_at_risk(&r, 0.25) - 0.04).abs() < 1e-12);
    }

    #[test]
    fn cvar_hand_sorted_series() {
        let r = series_20();
        // alpha = 0.25 -> cutoff 5 -> mean of worst 5:
        // (-0.09 - 0.08 - 0.07 - 0.06 - 0.05) / 5 = -0.35 / 5 = -0.07.
        assert!((conditional_var(&r, 0.25) - 0.07).abs() < 1e-12);
        // alpha = 0.10 -> cutoff 2 -> (-0.09 - 0.08) / 2 = -0.085.
        assert!((conditional_var(&r, 0.10) - 0.085).abs() < 1e-12);
        // alpha = 0.05 -> cutoff max(1, 1) = 1 -> worst single = -0.09.
        assert!((conditional_var(&r, 0.05) - 0.09).abs() < 1e-12);
    }

    // -----------------------------------------------------------------
    // Volatility / Sharpe / Sortino — closed forms.
    // -----------------------------------------------------------------

    #[test]
    fn volatility_closed_form() {
        // [0.02, 0.04, 0.06]: mean 0.04, squared deviations
        // 0.0004 + 0 + 0.0004 = 0.0008, sample variance 0.0008/2 =
        // 0.0004, std = 0.02 exactly.
        assert!((volatility(&[0.02, 0.04, 0.06]) - 0.02).abs() < 1e-15);
        // Arithmetic progression [0.01..0.05]: mean 0.03, squared
        // deviations sum to 0.001, /4 = 0.00025, sqrt = 0.015811388300841896.
        assert!((volatility(&[0.01, 0.02, 0.03, 0.04, 0.05]) - 0.015_811_388_300_841_896).abs() < 1e-12);
    }

    #[test]
    fn sharpe_closed_form() {
        // Series above: mean 0.04, sample std 0.02.
        // rf = 0 -> 0.04 / 0.02 = 2.0.
        assert!((sharpe_ratio(&[0.02, 0.04, 0.06], 0.0) - 2.0).abs() < 1e-12);
        // rf_annual = 0.12 -> per-period rf = 0.01 -> (0.04-0.01)/0.02 = 1.5.
        assert!((sharpe_ratio(&[0.02, 0.04, 0.06], 0.12) - 1.5).abs() < 1e-12);
    }

    #[test]
    fn sortino_closed_form() {
        // [0.03, -0.01, 0.05, -0.03], rf = 0: mean 0.01. Downside
        // observations are -0.01 and -0.03; downside deviation =
        // sqrt((0.0001 + 0.0009) / 2) = sqrt(0.0005) = 0.0223606797749979.
        // Sortino = 0.01 / sqrt(0.0005) = 1/sqrt(5) = 0.4472135954999579.
        assert!(
            (sortino_ratio(&[0.03, -0.01, 0.05, -0.03], 0.0) - 0.447_213_595_499_957_9).abs()
                < 1e-12
        );
    }

    #[test]
    fn drawdown_and_return_family_hand_values() {
        // [0.10, -0.20, -0.10, 0.15]: cumulative 1 -> 1.1 -> 0.88 ->
        // 0.792 -> 0.9108. Peak 1.1, trough 0.792:
        // max drawdown = (1.1 - 0.792) / 1.1 = 0.28 exactly.
        let r = [0.10, -0.20, -0.10, 0.15];
        assert!((max_drawdown(&r) - 0.28).abs() < 1e-12);
        // Total return = 0.9108 - 1 = -0.0892.
        assert!((total_return(&r) - (-0.0892)).abs() < 1e-12);
        // Calmar with 4 periods/year: years = 1, annualized = total,
        // calmar = -0.0892 / 0.28 = -0.31857142857142855.
        assert!((calmar_ratio(&r, 4) - (-0.318_571_428_571_428_55)).abs() < 1e-12);
        // Win rate: strictly positive periods only (0.00 is not a win):
        // [0.03, -0.01, 0.00, 0.05, -0.02] -> 2/5 = 0.4.
        assert!((win_rate(&[0.03, -0.01, 0.00, 0.05, -0.02]) - 0.4).abs() < 1e-15);
        // Annualized: twelve months of exactly +1%, 12 periods/year ->
        // (1.01^12 - 1) with years = 1 -> 0.12682503013196977.
        let monthly = vec![0.01; 12];
        assert!((annualized_return(&monthly, 12) - 0.126_825_030_131_969_77).abs() < 1e-12);
    }

    #[test]
    fn portfolio_returns_weighted_by_hand() {
        // A: value 300 (weight 0.75), B: value 100 (weight 0.25).
        // t0: 0.75*0.02 + 0.25*0.06 = 0.015 + 0.015 = 0.03.
        // t1: 0.75*(-0.01) + 0.25*0.03 = -0.0075 + 0.0075 = 0.0.
        let holdings = vec![
            holding("A", 300.0, vec![0.02, -0.01]),
            holding("B", 100.0, vec![0.06, 0.03]),
        ];
        let out = portfolio_returns(&holdings);
        assert_eq!(out.len(), 2);
        assert!((out[0] - 0.03).abs() < 1e-15);
        assert!((out[1] - 0.0).abs() < 1e-15);
    }

    // -----------------------------------------------------------------
    // Correlation — Pearson by hand.
    // A = [1, 2, 3, 4], B = [1, 3, 2, 4]: means 2.5, 2.5.
    // Sum of cross-deviations: 2.25 - 0.25 - 0.25 + 2.25 = 4.0.
    // Sum of squared deviations: 5.0 for both. rho = 4/sqrt(5*5) = 0.8.
    // -----------------------------------------------------------------

    #[test]
    fn correlation_hand_computed() {
        let input = CorrelationInput {
            holdings: vec![
                holding("A", 100.0, vec![1.0, 2.0, 3.0, 4.0]),
                holding("B", 100.0, vec![1.0, 3.0, 2.0, 4.0]),
                holding("C", 100.0, vec![2.0, 4.0, 6.0, 8.0]), // C = 2A -> rho(A,C) = 1
            ],
            window_days: None,
        };
        let out = compute_matrix(&input);
        assert!((out.matrix[0][1] - 0.8).abs() < 1e-9);
        assert!((out.matrix[1][0] - 0.8).abs() < 1e-9);
        assert!((out.matrix[0][2] - 1.0).abs() < 1e-9);
        assert!((out.matrix[0][0] - 1.0).abs() < 1e-12);
    }

    // -----------------------------------------------------------------
    // Monte Carlo.
    //
    // run_simulation() draws from thread_rng() — there is no seed
    // parameter — so the vectors are of two kinds:
    //  (1) degenerate cases (zero input volatility) whose outputs are
    //      exact closed forms, and
    //  (2) a single-step case whose terminal distribution is EXACTLY
    //      normal by construction (one step of initial*(1 + mu +
    //      sigma*Z)), checked against the analytic normal quantiles
    //      with tolerances = 8x the analytic standard error of each
    //      estimator (6x for the binomial probability). Standard
    //      normal quantiles: Phi^-1(0.95) = 1.6448536269514715,
    //      phi(1.6448...) = 0.10313564037537142 (standard tables).
    // -----------------------------------------------------------------

    #[test]
    fn monte_carlo_zero_volatility_exact() {
        // Constant +1% monthly returns -> sigma = 0 -> every path is
        // identical: final = 10000 * 1.01^12 = 11268.250301319698.
        let input = MonteCarloInput {
            holdings: vec![holding("FLAT", 10_000.0, vec![0.01; 12])],
            num_simulations: Some(500),
            time_horizon_months: Some(12),
            initial_value: 10_000.0,
        };
        let out = run_simulation(&input);
        let expected = 11_268.250_301_319_698;
        assert!((out.percentiles.p50[12] - expected).abs() < 1e-4);
        assert!((out.percentiles.p5[12] - expected).abs() < 1e-4);
        assert!((out.percentiles.p95[12] - expected).abs() < 1e-4);
        assert_eq!(out.percentiles.p50[0], 10_000.0);
        assert_eq!(out.probability_of_loss, 0.0);

        // Constant -2%: final = 10000 * 0.98^12 = 7847.167237348,
        // every path loses -> probability_of_loss exactly 1.
        let input = MonteCarloInput {
            holdings: vec![holding("DOWN", 10_000.0, vec![-0.02; 12])],
            num_simulations: Some(500),
            time_horizon_months: Some(12),
            initial_value: 10_000.0,
        };
        let out = run_simulation(&input);
        assert!((out.percentiles.p50[12] - 7_847.167_237_348).abs() < 1e-4);
        assert_eq!(out.probability_of_loss, 1.0);
    }

    #[test]
    fn monte_carlo_single_step_normal_quantiles() {
        // Return history: 50 x +0.03 and 50 x -0.01 (n = 100).
        // Sample mean mu = (50*0.03 + 50*(-0.01))/100 = 0.01 exactly.
        // Sample variance (Bessel): (100 * 0.02^2)/99 = 0.04/99, so
        // sigma = sqrt(0.04/99) = 0.02010075630518424.
        // One step: final = 10000 * (1 + mu + sigma*Z), Z ~ N(0,1).
        let mut returns = vec![0.03; 50];
        returns.extend(vec![-0.01; 50]);
        let input = MonteCarloInput {
            holdings: vec![holding("STEP", 10_000.0, returns)],
            num_simulations: Some(200_000),
            time_horizon_months: Some(1),
            initial_value: 10_000.0,
        };
        let out = run_simulation(&input);

        // Analytic quantiles of 10000*(1.01 + sigma*Z):
        // p50 = 10100.0; p5 = 9769.3719808695; p95 = 10430.6280191305.
        // SE(median) = 10000*sigma*sqrt(pi/(2N)) = 0.5633 -> tol 4.6.
        assert!(
            (out.percentiles.p50[1] - 10_100.0).abs() < 4.6,
            "p50 {} vs analytic 10100",
            out.percentiles.p50[1]
        );
        // SE(p5) = SE(p95) = 10000*sigma*sqrt(0.05*0.95/N)/phi(z) =
        // 0.9498 -> tol 7.6.
        assert!(
            (out.percentiles.p5[1] - 9_769.371_980_869_5).abs() < 7.6,
            "p5 {} vs analytic 9769.372",
            out.percentiles.p5[1]
        );
        assert!(
            (out.percentiles.p95[1] - 10_430.628_019_130_5).abs() < 7.6,
            "p95 {} vs analytic 10430.628",
            out.percentiles.p95[1]
        );
        // P(loss) = P(Z < -mu/sigma) = Phi(-0.49749371855331) =
        // 0.3094204656987553. Binomial SE = sqrt(p(1-p)/N) = 0.001034
        // -> tol 0.0065 (~6.3 SE).
        assert!(
            (out.probability_of_loss - 0.309_420_465_698_755_3).abs() < 0.0065,
            "probability_of_loss {} vs analytic 0.30942",
            out.probability_of_loss
        );
    }

    // -----------------------------------------------------------------
    // Stress test — arithmetic by hand.
    //
    // NOTE: classify_asset() is currently a stub — every holding is
    // classified "equity" — so these vectors pin scenario/custom
    // shock arithmetic under that stated premise.
    // -----------------------------------------------------------------

    fn stress_holdings() -> Vec<Holding> {
        vec![
            holding("SPY", 60_000.0, vec![0.01, -0.02]),
            holding("AGG", 40_000.0, vec![0.005, 0.003]),
        ]
    }

    #[test]
    fn stress_gfc_hand_computed() {
        // gfc_2008 equity shock = -0.50. Both holdings classify as
        // equity (stub): impacts -30000 and -20000, total -50000 on
        // 100000 -> portfolio impact exactly -0.50.
        let out = run_stress_test(&StressTestInput {
            holdings: stress_holdings(),
            scenario: "gfc_2008".to_string(),
            custom_shocks: None,
        });
        assert!((out.portfolio_impact_pct - (-0.50)).abs() < 1e-12);
        assert!((out.position_impacts[0].impact_value - (-30_000.0)).abs() < 1e-9);
        assert!((out.position_impacts[1].impact_value - (-20_000.0)).abs() < 1e-9);
        assert!((out.position_impacts[0].impact_pct - (-0.50)).abs() < 1e-12);
    }

    #[test]
    fn stress_custom_shock_hand_computed() {
        // Custom equity shock -0.30: impacts -18000 and -12000 ->
        // portfolio impact exactly -0.30.
        let out = run_stress_test(&StressTestInput {
            holdings: stress_holdings(),
            scenario: "custom".to_string(),
            custom_shocks: Some(vec![AssetShock {
                asset_class: "equity".to_string(),
                shock_pct: -0.30,
            }]),
        });
        assert!((out.portfolio_impact_pct - (-0.30)).abs() < 1e-12);
        assert!((out.position_impacts[0].impact_value - (-18_000.0)).abs() < 1e-9);
        assert!((out.position_impacts[1].impact_value - (-12_000.0)).abs() < 1e-9);
    }

    #[test]
    fn stress_missing_class_uses_documented_default() {
        // Custom shocks name only fixed_income; holdings classify as
        // equity -> the documented -0.10 default applies to both.
        let out = run_stress_test(&StressTestInput {
            holdings: stress_holdings(),
            scenario: "custom".to_string(),
            custom_shocks: Some(vec![AssetShock {
                asset_class: "fixed_income".to_string(),
                shock_pct: 0.05,
            }]),
        });
        assert!((out.portfolio_impact_pct - (-0.10)).abs() < 1e-12);
    }

    // -----------------------------------------------------------------
    // Bonds — textbook formulas, computed by direct discounting.
    // Macaulay = sum(t * PV_t) / NPV; modified = Macaulay / (1 + y);
    // convexity = sum(t(t+1) * PV_t) / (NPV * (1+y)^2).
    // -----------------------------------------------------------------

    #[test]
    fn bond_zero_coupon_price_path() {
        // Single cash flow 100 at t = 2, price = 100/1.05^2 =
        // 90.70294784580499 -> YTM must solve to exactly 5%.
        let out = analyze_bond(&BondAnalyticsInput {
            cash_flows: vec![BondCashFlow {
                time_years: 2.0,
                amount: 100.0,
            }],
            price: Some(90.702_947_845_804_99),
            yield_annual: None,
        })
        .unwrap();
        assert!((out.ytm_annual - 0.05).abs() < 1e-6, "ytm {}", out.ytm_annual);
        assert!((out.npv - 90.702_947_845_804_99).abs() < 1e-6);
        // Zero-coupon Macaulay duration = maturity = 2.0 exactly.
        assert!((out.macaulay_duration_years - 2.0).abs() < 1e-9);
        // Modified = 2 / 1.05 = 1.9047619047619047.
        assert!((out.modified_duration - 1.904_761_904_761_904_7).abs() < 1e-9);
        // Convexity = t(t+1) / (1+y)^2 = 6 / 1.1025 = 5.442176870748299.
        assert!((out.convexity - 5.442_176_870_748_299).abs() < 1e-6);
    }

    #[test]
    fn bond_two_cash_flow_duration_by_hand() {
        // Cash flows 5 @ 1y and 105 @ 2y, flat yield 10%.
        // PV1 = 5/1.1 = 4.545454545454546; PV2 = 105/1.21 =
        // 86.77685950413223; NPV = 91.32231404958678.
        // Macaulay = (1*PV1 + 2*PV2)/NPV = 1.9502262443438911.
        // Modified = Macaulay/1.1 = 1.7729329494035373.
        let out = analyze_bond(&BondAnalyticsInput {
            cash_flows: vec![
                BondCashFlow {
                    time_years: 1.0,
                    amount: 5.0,
                },
                BondCashFlow {
                    time_years: 2.0,
                    amount: 105.0,
                },
            ],
            price: None,
            yield_annual: Some(0.10),
        })
        .unwrap();
        assert!((out.npv - 91.322_314_049_586_78).abs() < 1e-9);
        assert!((out.macaulay_duration_years - 1.950_226_244_343_891_1).abs() < 1e-9);
        assert!((out.modified_duration - 1.772_932_949_403_537_3).abs() < 1e-9);
    }
}
