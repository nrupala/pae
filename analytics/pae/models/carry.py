"""Margin carry analysis engine.

Calculates income vs. margin cost for leveraged portfolios.
Surfaces factual carry metrics -- does not recommend actions.
"""

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class PositionCarry:
    """Per-position carry breakdown.

    Attributes:
        symbol: Ticker symbol.
        market_value: Current market value.
        yield_pct: Annual yield as percentage (e.g., 13.0 for 13%).
        annual_income: Estimated annual income from yield.
        margin_allocated: Margin allocated proportionally by market value.
        margin_rate: Annual margin interest rate.
        annual_margin_cost: Annual cost of allocated margin.
        net_carry: annual_income - annual_margin_cost.
        carry_spread: yield_pct/100 - margin_rate (net spread).
    """

    symbol: str
    market_value: float
    yield_pct: float
    annual_income: float
    margin_allocated: float
    margin_rate: float
    annual_margin_cost: float
    net_carry: float
    carry_spread: float


@dataclass
class PortfolioCarry:
    """Portfolio-level carry analysis.

    Attributes:
        total_nav: Net asset value (total_long_value - total_margin).
        total_long_value: Sum of all holdings' market values.
        total_margin: Total margin debt.
        leverage_ratio: total_long_value / total_nav.
        total_annual_income: Sum of all positions' annual income.
        total_annual_margin_cost: total_margin * margin_rate.
        net_carry: total_annual_income - total_annual_margin_cost.
        income_coverage_ratio: total_annual_income / total_annual_margin_cost.
        margin_as_pct_of_nav: (total_margin / total_nav) * 100.
        positions: Per-position carry details.
    """

    total_nav: float
    total_long_value: float
    total_margin: float
    leverage_ratio: float
    total_annual_income: float
    total_annual_margin_cost: float
    net_carry: float
    income_coverage_ratio: float
    margin_as_pct_of_nav: float
    positions: list[PositionCarry]


def analyze_carry(
    holdings: list[dict],
    total_margin: float,
    margin_rate: float = 0.058,
) -> PortfolioCarry:
    """Compute carry analysis for a leveraged portfolio.

    Args:
        holdings: List of holding dicts, each requiring:
            - "symbol" (str): Ticker symbol.
            - "market_value" (float): Current market value (must be >= 0).
            Optional:
            - "yield_pct" (float): Annual yield percentage (default: 0.0).
        total_margin: Total margin debt in the account (must be >= 0).
        margin_rate: Annual margin interest rate as decimal (default: 0.058 = 5.8%).
            Must be >= 0.

    Returns:
        PortfolioCarry with position-level and portfolio-level carry metrics.

    Raises:
        ValueError: If holdings is empty, margin is negative, rate is negative,
            or any holding is missing required fields.
    """
    # --- Input validation ---
    if not holdings:
        raise ValueError("holdings must not be empty")

    if total_margin < 0:
        raise ValueError(f"total_margin must be non-negative, got {total_margin}")

    if margin_rate < 0:
        raise ValueError(f"margin_rate must be non-negative, got {margin_rate}")

    if not isinstance(total_margin, (int, float)) or not _is_finite(total_margin):
        raise ValueError(f"total_margin must be a finite number, got {total_margin}")

    if not isinstance(margin_rate, (int, float)) or not _is_finite(margin_rate):
        raise ValueError(f"margin_rate must be a finite number, got {margin_rate}")

    for i, h in enumerate(holdings):
        if "symbol" not in h:
            raise ValueError(f"Holding at index {i} is missing 'symbol'")
        if "market_value" not in h:
            raise ValueError(f"Holding '{h.get('symbol', i)}' is missing 'market_value'")
        mv = h["market_value"]
        if not isinstance(mv, (int, float)) or not _is_finite(mv):
            raise ValueError(
                f"Holding '{h['symbol']}' has non-finite market_value: {mv}"
            )
        if mv < 0:
            raise ValueError(
                f"Holding '{h['symbol']}' has negative market_value: {mv}"
            )

    # --- Compute ---
    total_long = sum(h["market_value"] for h in holdings)
    total_nav = total_long - total_margin

    positions: list[PositionCarry] = []
    total_income = 0.0

    for h in holdings:
        yield_pct = h.get("yield_pct", 0.0)
        if not isinstance(yield_pct, (int, float)) or not _is_finite(yield_pct):
            yield_pct = 0.0

        mv = h["market_value"]
        income = mv * (yield_pct / 100.0)
        total_income += income

        margin_share = (mv / total_long * total_margin) if total_long > 0 else 0.0
        margin_cost = margin_share * margin_rate
        net = income - margin_cost

        positions.append(PositionCarry(
            symbol=h["symbol"],
            market_value=mv,
            yield_pct=yield_pct,
            annual_income=round(income, 2),
            margin_allocated=round(margin_share, 2),
            margin_rate=margin_rate,
            annual_margin_cost=round(margin_cost, 2),
            net_carry=round(net, 2),
            carry_spread=round(yield_pct / 100.0 - margin_rate, 4),
        ))

    total_margin_cost = total_margin * margin_rate
    net_carry = total_income - total_margin_cost
    coverage = total_income / total_margin_cost if total_margin_cost > 0 else float("inf")
    if total_income == 0 and total_margin_cost == 0:
        coverage = 0.0

    leverage = total_long / total_nav if total_nav > 0 else 0.0
    margin_pct = (total_margin / total_nav * 100) if total_nav > 0 else 0.0

    logger.info(
        "Carry analysis: %d positions, NAV=%.2f, net_carry=%.2f, coverage=%.2f",
        len(positions), total_nav, net_carry, coverage,
    )

    return PortfolioCarry(
        total_nav=round(total_nav, 2),
        total_long_value=round(total_long, 2),
        total_margin=round(total_margin, 2),
        leverage_ratio=round(leverage, 2),
        total_annual_income=round(total_income, 2),
        total_annual_margin_cost=round(total_margin_cost, 2),
        net_carry=round(net_carry, 2),
        income_coverage_ratio=round(coverage, 2),
        margin_as_pct_of_nav=round(margin_pct, 1),
        positions=positions,
    )


def _is_finite(value: float) -> bool:
    """Check if a numeric value is finite (not NaN, not Inf)."""
    import math
    return math.isfinite(value)
