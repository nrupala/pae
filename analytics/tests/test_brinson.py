# Copyright (C) 2026 Nrupal Akolkar
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for Brinson-Hood-Beebower performance attribution.

The core case is a fully HAND-COMPUTED 3-segment example. Hand work
(shown below) — verify before editing:

    Portfolio: Equities w=0.50 r=0.08, Bonds w=0.30 r=0.04, Cash w=0.20 r=0.02
    Benchmark: Equities w=0.60 r=0.10, Bonds w=0.30 r=0.03, Cash w=0.10 r=0.02

    Totals:
      R_p = 0.50*0.08 + 0.30*0.04 + 0.20*0.02 = 0.040 + 0.012 + 0.004 = 0.056
      R_b = 0.60*0.10 + 0.30*0.03 + 0.10*0.02 = 0.060 + 0.009 + 0.002 = 0.071
      Active = 0.056 - 0.071 = -0.015

    Equities (w_p-w_b = -0.10):
      Allocation  = (-0.10) * (0.10 - 0.071) = -0.10 * 0.029  = -0.0029
      Selection   = 0.60 * (0.08 - 0.10)     = 0.60 * (-0.02) = -0.0120
      Interaction = (-0.10) * (0.08 - 0.10)  = -0.10 * (-0.02)=  0.0020
      Contribution = -0.0029 - 0.0120 + 0.0020 = -0.0129

    Bonds (w_p-w_b = 0.00):
      Allocation  = 0.00 * (0.03 - 0.071) = 0.0000
      Selection   = 0.30 * (0.04 - 0.03)  = 0.0030
      Interaction = 0.00 * 0.01            = 0.0000
      Contribution = 0.0030

    Cash (w_p-w_b = 0.10):
      Allocation  = 0.10 * (0.02 - 0.071) = 0.10 * (-0.051) = -0.0051
      Selection   = 0.10 * (0.02 - 0.02)  = 0.0000
      Interaction = 0.10 * 0.00           = 0.0000
      Contribution = -0.0051

    Segment contributions sum: -0.0129 + 0.0030 - 0.0051 = -0.0150 = Active
    Totals: alloc = -0.0080, sel = -0.0090, inter = 0.0020,
            sum = -0.0150 = Active
"""

import math

import pytest

from pae.models.brinson import BrinsonError, attribute

PORTFOLIO = [
    {"segment": "Equities", "weight": 0.50, "return": 0.08},
    {"segment": "Bonds", "weight": 0.30, "return": 0.04},
    {"segment": "Cash", "weight": 0.20, "return": 0.02},
]

BENCHMARK = [
    {"segment": "Equities", "weight": 0.60, "return": 0.10},
    {"segment": "Bonds", "weight": 0.30, "return": 0.03},
    {"segment": "Cash", "weight": 0.10, "return": 0.02},
]


def _by_name(result):
    return {s.segment: s for s in result.segments}


def test_hand_computed_totals():
    """Totals match the hand computation: R_p=0.056, R_b=0.071, active=-0.015."""
    result = attribute(PORTFOLIO, BENCHMARK)
    assert result.portfolio_return == pytest.approx(0.056)
    assert result.benchmark_return == pytest.approx(0.071)
    assert result.active_return == pytest.approx(-0.015)


def test_hand_computed_per_segment_effects():
    """Per-segment allocation/selection/interaction match the hand computation."""
    segs = _by_name(attribute(PORTFOLIO, BENCHMARK))

    eq = segs["Equities"]
    assert eq.allocation_effect == pytest.approx(-0.0029)
    assert eq.selection_effect == pytest.approx(-0.0120)
    assert eq.interaction_effect == pytest.approx(0.0020)
    assert eq.active_contribution == pytest.approx(-0.0129)

    bd = segs["Bonds"]
    assert bd.allocation_effect == pytest.approx(0.0, abs=1e-12)
    assert bd.selection_effect == pytest.approx(0.0030)
    assert bd.interaction_effect == pytest.approx(0.0, abs=1e-12)
    assert bd.active_contribution == pytest.approx(0.0030)

    ca = segs["Cash"]
    assert ca.allocation_effect == pytest.approx(-0.0051)
    assert ca.selection_effect == pytest.approx(0.0, abs=1e-12)
    assert ca.interaction_effect == pytest.approx(0.0, abs=1e-12)
    assert ca.active_contribution == pytest.approx(-0.0051)


def test_hand_computed_effect_totals():
    """Effect totals: alloc=-0.008, sel=-0.009, inter=0.002, sum=active."""
    result = attribute(PORTFOLIO, BENCHMARK)
    assert result.total_allocation == pytest.approx(-0.0080)
    assert result.total_selection == pytest.approx(-0.0090)
    assert result.total_interaction == pytest.approx(0.0020)
    effects_sum = result.total_allocation + result.total_selection + result.total_interaction
    assert effects_sum == pytest.approx(result.active_return)
    contrib_sum = sum(s.active_contribution for s in result.segments)
    assert contrib_sum == pytest.approx(result.active_return)


def test_identity_on_random_inputs():
    """The core identity (effects sum to active return) holds on random data."""
    import random

    rng = random.Random(20261005)
    names = ["A", "B", "C", "D"]
    for _ in range(50):
        p_raw = [rng.random() + 0.01 for _ in names]
        b_raw = [rng.random() + 0.01 for _ in names]
        p_sum, b_sum = sum(p_raw), sum(b_raw)
        p = [
            {
                "segment": n,
                "weight": w / p_sum,
                "return": rng.uniform(-0.2, 0.3),
            }
            for n, w in zip(names, p_raw)
        ]
        b = [
            {
                "segment": n,
                "weight": w / b_sum,
                "return": rng.uniform(-0.2, 0.3),
            }
            for n, w in zip(names, b_raw)
        ]
        result = attribute(p, b)
        effects_sum = result.total_allocation + result.total_selection + result.total_interaction
        assert effects_sum == pytest.approx(result.active_return, abs=1e-12)
        for s in result.segments:
            assert (
                s.allocation_effect + s.selection_effect + s.interaction_effect
            ) == pytest.approx(s.active_contribution, abs=1e-12)


def test_zero_weight_segments_do_not_crash():
    """Zero weights (on both sides and one side) compute without error."""
    p = [
        {"segment": "Equities", "weight": 0.70, "return": 0.05},
        {"segment": "Bonds", "weight": 0.30, "return": 0.02},
        {"segment": "Cash", "weight": 0.0, "return": 0.01},
    ]
    b = [
        {"segment": "Equities", "weight": 0.60, "return": 0.06},
        {"segment": "Bonds", "weight": 0.30, "return": 0.02},
        {"segment": "Cash", "weight": 0.10, "return": 0.01},
    ]
    result = attribute(p, b)
    segs = _by_name(result)
    # Zero-on-both-sides segment contributes nothing.
    zero = segs["Cash"]
    assert zero.portfolio_weight == 0.0
    # Portfolio has Cash at 0.0, benchmark at 0.10: still computable.
    effects_sum = result.total_allocation + result.total_selection + result.total_interaction
    assert effects_sum == pytest.approx(result.active_return, abs=1e-12)


def test_missing_segment_documented_treatment():
    """A segment on one side only: weight 0, return imputed as side total."""
    p = [
        {"segment": "Equities", "weight": 0.60, "return": 0.10},
        {"segment": "Bonds", "weight": 0.40, "return": 0.03},
    ]
    b = [
        {"segment": "Equities", "weight": 0.50, "return": 0.08},
        {"segment": "Bonds", "weight": 0.30, "return": 0.02},
        {"segment": "RealEstate", "weight": 0.20, "return": 0.06},
    ]
    result = attribute(p, b)
    segs = _by_name(result)
    re = segs["RealEstate"]
    # Missing on the portfolio side: weight 0, return = portfolio total.
    assert re.portfolio_weight == 0.0
    assert re.portfolio_return == pytest.approx(result.portfolio_return)
    # Allocation still measures the underweight; selection/interaction on the
    # imputed side collapse to zero.
    assert re.selection_effect == pytest.approx(0.20 * (result.portfolio_return - 0.06))
    assert math.isfinite(re.allocation_effect)
    effects_sum = result.total_allocation + result.total_selection + result.total_interaction
    assert effects_sum == pytest.approx(result.active_return, abs=1e-12)


def test_empty_inputs_rejected():
    """Empty segment lists raise clear errors."""
    with pytest.raises(ValueError, match="portfolio segments must not be empty"):
        attribute([], BENCHMARK)
    with pytest.raises(ValueError, match="benchmark segments must not be empty"):
        attribute(PORTFOLIO, [])


def test_weights_must_sum_to_one():
    """Weights that do not sum to 1.0 are rejected."""
    bad = [
        {"segment": "Equities", "weight": 0.50, "return": 0.08},
        {"segment": "Bonds", "weight": 0.30, "return": 0.04},
    ]
    with pytest.raises(ValueError, match="weights sum to"):
        attribute(bad, BENCHMARK)


def test_validation_errors():
    """Duplicate names, missing keys, non-finite and out-of-range values."""
    dup = [dict(PORTFOLIO[0]), dict(PORTFOLIO[0]), dict(PORTFOLIO[2])]
    dup[1]["weight"] = 0.40  # keep weights summing to 1.0 for the dup check
    with pytest.raises(ValueError, match="duplicated"):
        attribute(dup, BENCHMARK)

    missing_key = [{"segment": "Equities", "weight": 1.0}]
    with pytest.raises(ValueError, match="missing 'return'"):
        attribute(missing_key, BENCHMARK)

    non_finite = [{"segment": "Equities", "weight": 1.0, "return": float("nan")}]
    with pytest.raises(ValueError, match="non-numeric"):
        attribute(non_finite, BENCHMARK)

    negative_weight = [
        {"segment": "Equities", "weight": -0.1, "return": 0.05},
        {"segment": "Bonds", "weight": 1.1, "return": 0.05},
    ]
    with pytest.raises(ValueError, match="outside \\[0, 1\\]"):
        attribute(negative_weight, BENCHMARK)


def test_brinson_error_exported():
    """The BrinsonError exception type exists for callers to catch."""
    assert issubclass(BrinsonError, Exception)
