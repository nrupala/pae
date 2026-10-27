# Copyright (C) 2026 2351485 ALBERTA INC.
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Known-answer vectors for the Python analytics math (Phase 3C).

Every expected value was computed independently of the implementation
— by hand from the textbook formula or from raw-sum arithmetic — and
the derivation is written next to each assertion. Tolerances are
stated per vector and were not loosened to pass.

Note on scope: PAE has no standalone Python VaR/Sharpe/CVaR
implementation to pin — risk metrics are computed by the Rust engine
(server endpoints proxy there; the engine side is pinned by
engine/src/risk/known_answers.rs). The Python math that IS original
to this codebase — the Fama-French OLS decomposition and the margin
carry arithmetic — is pinned here.
"""

from __future__ import annotations

import numpy as np
import pytest

from pae.models.carry import analyze_carry
from pae.models.factor import decompose

# ---------------------------------------------------------------------
# Factor decomposition, single factor — fully hand-computed OLS.
#
# Design: y = alpha + beta*x + e with alpha = 0.02, beta = 0.5 and a
# residual vector e chosen ORTHOGONAL to both the intercept column
# and x (sum(e) = 0 and sum(x*e) = 0), so OLS must recover alpha and
# beta EXACTLY and the residuals are e themselves.
#
#   x = [0.01, 0.02, 0.03, 0.04, 0.05, 0.06]
#   e = [0.01, -0.01, -0.01, 0.01, 0, 0]
#     sum(e) = 0; sum(x*e) = 0.0001 - 0.0002 - 0.0003 + 0.0004 = 0.
#   y = 0.02 + 0.5x + e = [0.035, 0.02, 0.025, 0.05, 0.045, 0.05]
#
# Hand arithmetic (raw sums):
#   SS_res = sum(e^2) = 4 * 0.0001 = 0.0004.
#   mean(y) = 0.225/6 = 0.0375; SS_tot = sum((y-mean)^2) = 0.00083750
#     (deviations: -0.0025, -0.0175, -0.0125, 0.0125, 0.0075, 0.0125;
#      squares sum to 0.00000625+0.00030625+0.00015625+0.00015625
#      +0.00005625+0.00015625 = 0.00083750).
#   R^2 = 1 - 0.0004/0.0008375 = 0.5223880597014927...
#   sigma^2 = SS_res/(n-2) = 0.0001. X'X = [[6, 0.21], [0.21, 0.0091]],
#   det = 0.0105, so var(alpha) = (0.0091/0.0105)*sigma^2 and
#   var(beta) = (6/0.0105)*sigma^2 = 0.057142857..., giving
#   se(beta) = 0.239045721..., t_beta = 0.5/se = 2.0916500663351885,
#   se(alpha) = 0.009309492..., t_alpha = 0.02/se = 2.148344622118299.
#   Contribution (single factor): beta^2 * var(x) / var(y) with sample
#   variances — equals R^2 here because e is orthogonal to x:
#   contribution_pct = 52.23880597014924, residual = 47.76119402985076.
# ---------------------------------------------------------------------


def test_factor_single_factor_hand_computed() -> None:
    x = np.array([0.01, 0.02, 0.03, 0.04, 0.05, 0.06])
    y = np.array([0.035, 0.02, 0.025, 0.05, 0.045, 0.05])

    out = decompose(y, {"MKT": x})

    assert out.alpha == pytest.approx(0.02, abs=1e-12)
    assert out.alpha_t_stat == pytest.approx(2.148344622118299, rel=1e-9)
    assert out.r_squared == pytest.approx(0.5223880597014927, rel=1e-12)
    assert len(out.exposures) == 1
    exp = out.exposures[0]
    assert exp.factor_name == "MKT"
    assert exp.beta == pytest.approx(0.5, abs=1e-12)
    assert exp.t_stat == pytest.approx(2.0916500663351885, rel=1e-9)
    assert exp.contribution_pct == pytest.approx(52.23880597014924, rel=1e-9)
    assert out.residual_risk_pct == pytest.approx(47.76119402985076, rel=1e-9)


def test_factor_five_factor_exact_recovery() -> None:
    """Synthetic data with KNOWN true coefficients and zero noise.

    y is constructed as alpha + sum(beta_i * f_i) exactly, so OLS
    must return the construction coefficients to float noise and
    R^2 must be 1. (t-statistics are not asserted: with zero residual
    variance the standard errors are 0 and the implementation reports
    t = 0 by its stated guard.)
    """
    rng = np.random.default_rng(7)
    n = 60
    factors = {
        "MKT-RF": rng.normal(0.005, 0.04, n),
        "SMB": rng.normal(0.001, 0.02, n),
        "HML": rng.normal(0.002, 0.025, n),
        "RMW": rng.normal(0.0015, 0.018, n),
        "CMA": rng.normal(0.001, 0.015, n),
    }
    true_alpha = 0.0015
    true_betas = {
        "MKT-RF": 1.1,
        "SMB": 0.3,
        "HML": -0.2,
        "RMW": 0.15,
        "CMA": 0.05,
    }
    y = np.full(n, true_alpha)
    for name, beta in true_betas.items():
        y = y + beta * factors[name]

    out = decompose(y, factors)

    assert out.alpha == pytest.approx(true_alpha, abs=1e-10)
    assert out.r_squared == pytest.approx(1.0, abs=1e-10)
    got = {e.factor_name: e.beta for e in out.exposures}
    for name, beta in true_betas.items():
        assert got[name] == pytest.approx(beta, abs=1e-8), name


# ---------------------------------------------------------------------
# Margin carry — hand-computed arithmetic.
#
# Holdings: AAA 200000 @ 4.0%, BBB 50000 @ 6.5%, CCC 25000 @ 0%
# (CCC omits yield_pct -> the documented 0.0 default).
# total_margin = 100000, margin_rate = 0.06.
#
#   total_long = 275000; NAV = 275000 - 100000 = 175000.
#   leverage = 275000/175000 = 1.571428... -> 1.57.
#   income: AAA 200000*0.04 = 8000; BBB 50000*0.065 = 3250; CCC 0.
#     total income = 11250.
#   margin cost total = 100000 * 0.06 = 6000; net carry = 5250.
#   coverage = 11250/6000 = 1.875 -> 1.88.
#   margin as % of NAV = 100000/175000*100 = 57.142857... -> 57.1.
#   Per position, margin allocated pro-rata by market value:
#     AAA: 200000/275000*100000 = 72727.27; cost *0.06 = 4363.64;
#          net = 8000 - 4363.64 = 3636.36; spread = 0.04-0.06 = -0.02.
#     BBB: 18181.82; cost 1090.91; net 3250 - 1090.91 = 2159.09;
#          spread = 0.065 - 0.06 = 0.005.
#     CCC: 9090.91; cost 545.45; net = 0 - 545.45 = -545.45;
#          spread = 0 - 0.06 = -0.06.
# (Expected values are the implementation's documented 2-dp rounded
# outputs of exactly this arithmetic.)
# ---------------------------------------------------------------------


def test_carry_hand_computed() -> None:
    holdings = [
        {"symbol": "AAA", "market_value": 200_000.0, "yield_pct": 4.0},
        {"symbol": "BBB", "market_value": 50_000.0, "yield_pct": 6.5},
        {"symbol": "CCC", "market_value": 25_000.0},
    ]
    out = analyze_carry(holdings, total_margin=100_000.0, margin_rate=0.06)

    assert out.total_long_value == 275_000.0
    assert out.total_nav == 175_000.0
    assert out.leverage_ratio == 1.57
    assert out.total_annual_income == 11_250.0
    assert out.total_annual_margin_cost == 6_000.0
    assert out.net_carry == 5_250.0
    assert out.income_coverage_ratio == 1.88
    assert out.margin_as_pct_of_nav == 57.1

    pos = {p.symbol: p for p in out.positions}
    aaa = pos["AAA"]
    assert aaa.annual_income == 8_000.0
    assert aaa.margin_allocated == 72_727.27
    assert aaa.annual_margin_cost == 4_363.64
    assert aaa.net_carry == 3_636.36
    assert aaa.carry_spread == -0.02

    bbb = pos["BBB"]
    assert bbb.annual_income == 3_250.0
    assert bbb.margin_allocated == 18_181.82
    assert bbb.annual_margin_cost == 1_090.91
    assert bbb.net_carry == 2_159.09
    assert bbb.carry_spread == 0.005

    ccc = pos["CCC"]
    assert ccc.annual_income == 0.0
    assert ccc.margin_allocated == 9_090.91
    assert ccc.annual_margin_cost == 545.45
    assert ccc.net_carry == -545.45
    assert ccc.carry_spread == -0.06


def test_carry_no_margin_hand_computed() -> None:
    """Zero margin: cost is 0, coverage is infinite when income > 0,
    leverage is exactly 1, every position's net equals its income."""
    holdings = [
        {"symbol": "AAA", "market_value": 40_000.0, "yield_pct": 5.0},
        {"symbol": "BBB", "market_value": 60_000.0, "yield_pct": 2.5},
    ]
    out = analyze_carry(holdings, total_margin=0.0, margin_rate=0.06)

    # income: 40000*0.05 = 2000; 60000*0.025 = 1500; total 3500.
    assert out.total_annual_income == 3_500.0
    assert out.total_annual_margin_cost == 0.0
    assert out.net_carry == 3_500.0
    assert out.total_nav == 100_000.0
    assert out.leverage_ratio == 1.0
    assert out.margin_as_pct_of_nav == 0.0
    assert out.income_coverage_ratio == float("inf")
    for p in out.positions:
        assert p.margin_allocated == 0.0
        assert p.net_carry == p.annual_income
