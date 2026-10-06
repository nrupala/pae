// Copyright (C) 2026 Nrupal Akolkar
// SPDX-License-Identifier: AGPL-3.0-or-later

//! Fixed-income analytics endpoint.
//!
//! `POST /api/v1/analytics/bond` prices a bond from its cash-flow schedule
//! using the C numerical core (`crate::num_ffi::bonds`, BLAS-free standard
//! bond mathematics following QuantLib's bond methodology — see
//! `engine/c/pae_bonds.h`). Supply either a market `price` (the yield to
//! maturity is solved) or a `yield_annual` (the NPV is computed from a flat
//! discount curve); the response always carries NPV, YTM, Macaulay and
//! modified duration, and convexity.

use axum::http::StatusCode;
use axum::Json;
use serde::{Deserialize, Serialize};

use crate::api::portfolio::PortfolioErrorResponse;
use crate::num_ffi::bonds;

/// One scheduled cash flow: `amount` payable `time_years` years out.
#[derive(Deserialize)]
pub struct BondCashFlow {
    pub time_years: f64,
    pub amount: f64,
}

/// Bond analytics request.
///
/// Exactly one of `price` / `yield_annual` must be present:
/// - `price`: market price per 100 face; the yield to maturity is solved
///   from the NPV equation.
/// - `yield_annual`: annual yield; the NPV is discounted on a flat curve.
#[derive(Deserialize)]
pub struct BondAnalyticsInput {
    pub cash_flows: Vec<BondCashFlow>,
    pub price: Option<f64>,
    pub yield_annual: Option<f64>,
}

/// Bond analytics response. All values per 100 face.
#[derive(Serialize)]
pub struct BondAnalyticsResponse {
    pub npv: f64,
    pub ytm_annual: f64,
    pub macaulay_duration_years: f64,
    pub modified_duration: f64,
    pub convexity: f64,
    pub num_cash_flows: usize,
}

/// Bond endpoint validation and computation errors.
#[derive(Debug)]
pub enum BondError {
    EmptyCashFlows,
    TooManyCashFlows,
    InvalidCashFlow { index: usize },
    MissingPriceAndYield,
    BothPriceAndYield,
    InvalidPrice,
    InvalidYield,
    ComputationFailed(&'static str),
}

impl BondError {
    fn status_code(&self) -> StatusCode {
        match self {
            BondError::ComputationFailed(_) => StatusCode::UNPROCESSABLE_ENTITY,
            _ => StatusCode::BAD_REQUEST,
        }
    }

    fn error_code(&self) -> &'static str {
        match self {
            BondError::EmptyCashFlows => "EMPTY_CASH_FLOWS",
            BondError::TooManyCashFlows => "TOO_MANY_CASH_FLOWS",
            BondError::InvalidCashFlow { .. } => "INVALID_CASH_FLOW",
            BondError::MissingPriceAndYield => "MISSING_PRICE_AND_YIELD",
            BondError::BothPriceAndYield => "BOTH_PRICE_AND_YIELD",
            BondError::InvalidPrice => "INVALID_PRICE",
            BondError::InvalidYield => "INVALID_YIELD",
            BondError::ComputationFailed(_) => "BOND_COMPUTATION_FAILED",
        }
    }

    fn message(&self) -> String {
        match self {
            BondError::EmptyCashFlows => "cash_flows must not be empty".to_string(),
            BondError::TooManyCashFlows => "cash_flows is capped at 1200 legs".to_string(),
            BondError::InvalidCashFlow { index } => {
                format!("cash_flows[{index}]: time_years must be > 0 and amount finite")
            }
            BondError::MissingPriceAndYield => {
                "exactly one of price or yield_annual must be provided".to_string()
            }
            BondError::BothPriceAndYield => {
                "provide either price or yield_annual, not both".to_string()
            }
            BondError::InvalidPrice => "price must be a positive finite number".to_string(),
            BondError::InvalidYield => "yield_annual must be finite and greater than -1".to_string(),
            BondError::ComputationFailed(what) => {
                format!("bond computation failed: {what}")
            }
        }
    }
}

fn into_error_response(err: BondError) -> (StatusCode, Json<PortfolioErrorResponse>) {
    let status = err.status_code();
    (
        status,
        Json(PortfolioErrorResponse {
            error: err.message(),
            code: err.error_code().to_string(),
        }),
    )
}

/// Maximum cash-flow legs (100 years of monthly coupons).
const MAX_CASH_FLOWS: usize = 1200;

/// Pure analytics: validate + compute. Factored out of the handler so unit
/// tests exercise the logic without HTTP.
pub fn analyze_bond(input: &BondAnalyticsInput) -> Result<BondAnalyticsResponse, BondError> {
    let n = input.cash_flows.len();
    if n == 0 {
        return Err(BondError::EmptyCashFlows);
    }
    if n > MAX_CASH_FLOWS {
        return Err(BondError::TooManyCashFlows);
    }
    for (i, cf) in input.cash_flows.iter().enumerate() {
        if cf.time_years.is_nan() || cf.time_years <= 0.0 || !cf.amount.is_finite() {
            return Err(BondError::InvalidCashFlow { index: i });
        }
    }

    let times: Vec<f64> = input.cash_flows.iter().map(|cf| cf.time_years).collect();
    let amounts: Vec<f64> = input.cash_flows.iter().map(|cf| cf.amount).collect();

    let (npv, ytm) = match (input.price, input.yield_annual) {
        (Some(_), Some(_)) => return Err(BondError::BothPriceAndYield),
        (None, None) => return Err(BondError::MissingPriceAndYield),
        (Some(p), None) => {
            if p.is_nan() || p <= 0.0 || !p.is_finite() {
                return Err(BondError::InvalidPrice);
            }
            let y = bonds::ytm(&times, &amounts, p)
                .ok_or(BondError::ComputationFailed("yield did not converge"))?;
            // Reprice at the solved yield; must recover the input price.
            let df = bonds::flat_discount_factors(y, &times);
            let npv = bonds::npv(&amounts, &df)
                .ok_or(BondError::ComputationFailed("npv undefined"))?;
            (npv, y)
        }
        (None, Some(y)) => {
            if !y.is_finite() || y <= -1.0 {
                return Err(BondError::InvalidYield);
            }
            let df = bonds::flat_discount_factors(y, &times);
            let npv = bonds::npv(&amounts, &df)
                .ok_or(BondError::ComputationFailed("npv undefined"))?;
            (npv, y)
        }
    };

    let macaulay = bonds::macaulay_duration(&times, &amounts, ytm)
        .ok_or(BondError::ComputationFailed("duration undefined"))?;
    let modified = bonds::modified_duration(&times, &amounts, ytm)
        .ok_or(BondError::ComputationFailed("duration undefined"))?;
    let convexity = bonds::convexity(&times, &amounts, ytm)
        .ok_or(BondError::ComputationFailed("convexity undefined"))?;

    Ok(BondAnalyticsResponse {
        npv,
        ytm_annual: ytm,
        macaulay_duration_years: macaulay,
        modified_duration: modified,
        convexity,
        num_cash_flows: n,
    })
}

/// POST /api/v1/analytics/bond
///
/// Fixed-income analytics for a cash-flow schedule. See
/// [`BondAnalyticsInput`] for the price-vs-yield contract.
pub async fn bond_analytics(
    Json(input): Json<BondAnalyticsInput>,
) -> Result<Json<BondAnalyticsResponse>, (StatusCode, Json<PortfolioErrorResponse>)> {
    analyze_bond(&input).map(Json).map_err(into_error_response)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn par_bond_input_price() -> BondAnalyticsInput {
        BondAnalyticsInput {
            cash_flows: (1..=10)
                .map(|t| BondCashFlow {
                    time_years: t as f64,
                    amount: if t == 10 { 105.0 } else { 5.0 },
                })
                .collect(),
            price: Some(100.0),
            yield_annual: None,
        }
    }

    #[test]
    fn test_par_bond_price_path_textbook_values() {
        let r = analyze_bond(&par_bond_input_price()).unwrap();
        assert!((r.ytm_annual - 0.05).abs() < 1e-9);
        assert!((r.npv - 100.0).abs() < 1e-6);
        assert!((r.macaulay_duration_years - 8.107_822).abs() < 1e-4);
        assert!((r.modified_duration - 7.721_735).abs() < 1e-4);
        assert!((r.convexity - 74.997_7).abs() < 1e-3);
        assert_eq!(r.num_cash_flows, 10);
    }

    #[test]
    fn test_yield_path_npv() {
        let input = BondAnalyticsInput {
            cash_flows: (1..=10)
                .map(|t| BondCashFlow {
                    time_years: t as f64,
                    amount: if t == 10 { 105.0 } else { 5.0 },
                })
                .collect(),
            price: None,
            yield_annual: Some(0.04),
        };
        let r = analyze_bond(&input).unwrap();
        // 5% bond at a 4% yield trades above par.
        assert!(r.npv > 100.0);
        assert!((r.ytm_annual - 0.04).abs() < 1e-12);
        // Independent check: sum of discounted cash flows.
        let expected: f64 = (1..=10)
            .map(|t| {
                let cf = if t == 10 { 105.0 } else { 5.0 };
                cf / 1.04_f64.powi(t)
            })
            .sum();
        assert!((r.npv - expected).abs() < 1e-9);
    }

    #[test]
    fn test_zero_coupon() {
        let input = BondAnalyticsInput {
            cash_flows: vec![BondCashFlow {
                time_years: 5.0,
                amount: 100.0,
            }],
            price: None,
            yield_annual: Some(0.06),
        };
        let r = analyze_bond(&input).unwrap();
        assert!((r.macaulay_duration_years - 5.0).abs() < 1e-12);
        assert!((r.npv - 100.0 / 1.06_f64.powi(5)).abs() < 1e-9);
    }

    #[test]
    fn test_validation_errors() {
        // Empty schedule.
        let e = analyze_bond(&BondAnalyticsInput {
            cash_flows: vec![],
            price: Some(100.0),
            yield_annual: None,
        });
        assert!(matches!(e, Err(BondError::EmptyCashFlows)));

        // Both price and yield.
        let e = analyze_bond(&BondAnalyticsInput {
            cash_flows: vec![BondCashFlow { time_years: 1.0, amount: 105.0 }],
            price: Some(100.0),
            yield_annual: Some(0.05),
        });
        assert!(matches!(e, Err(BondError::BothPriceAndYield)));

        // Neither.
        let e = analyze_bond(&BondAnalyticsInput {
            cash_flows: vec![BondCashFlow { time_years: 1.0, amount: 105.0 }],
            price: None,
            yield_annual: None,
        });
        assert!(matches!(e, Err(BondError::MissingPriceAndYield)));

        // Bad leg.
        let e = analyze_bond(&BondAnalyticsInput {
            cash_flows: vec![BondCashFlow { time_years: 0.0, amount: 105.0 }],
            price: Some(100.0),
            yield_annual: None,
        });
        assert!(matches!(e, Err(BondError::InvalidCashFlow { index: 0 })));

        // Non-positive price.
        let e = analyze_bond(&BondAnalyticsInput {
            cash_flows: vec![BondCashFlow { time_years: 1.0, amount: 105.0 }],
            price: Some(-3.0),
            yield_annual: None,
        });
        assert!(matches!(e, Err(BondError::InvalidPrice)));
    }
}
