"""Tests for margin carry analysis engine."""

from pae.models.carry import analyze_carry


def test_positive_carry():
    """Test portfolio with positive carry (income > margin cost)."""
    holdings = [
        {"symbol": "GSBD", "market_value": 15000.0, "yield_pct": 13.0},
        {"symbol": "BCSF", "market_value": 5000.0, "yield_pct": 12.5},
        {"symbol": "FDS", "market_value": 18000.0, "yield_pct": 0.9},
    ]

    result = analyze_carry(holdings, total_margin=15000.0, margin_rate=0.058)

    assert result.net_carry > 0, f"Expected positive carry, got {result.net_carry}"
    assert result.income_coverage_ratio > 1.0
    assert result.leverage_ratio > 1.0


def test_negative_carry():
    """Test portfolio with negative carry (margin cost > income)."""
    holdings = [
        {"symbol": "MSTR", "market_value": 20000.0, "yield_pct": 0.0},
        {"symbol": "TTD", "market_value": 10000.0, "yield_pct": 0.0},
    ]

    result = analyze_carry(holdings, total_margin=20000.0, margin_rate=0.058)

    assert result.net_carry < 0, f"Expected negative carry, got {result.net_carry}"
    assert result.income_coverage_ratio == 0.0


def test_no_margin():
    """Test fully funded portfolio (no margin)."""
    holdings = [
        {"symbol": "FDS", "market_value": 10000.0, "yield_pct": 0.9},
    ]

    result = analyze_carry(holdings, total_margin=0.0)

    assert result.leverage_ratio == 1.0
    assert result.total_annual_margin_cost == 0.0
    assert result.net_carry > 0
