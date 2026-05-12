"""Margin carry analysis engine.

Calculates income vs. margin cost for leveraged portfolios.
Surfaces factual carry metrics -- does not recommend actions.
"""

from dataclasses import dataclass


@dataclass
class PositionCarry:
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
    """Compute carry analysis for a leveraged portfolio."""
    total_nav = sum(h["market_value"] for h in holdings) - total_margin
    total_long = sum(h["market_value"] for h in holdings)

    positions = []
    total_income = 0.0

    for h in holdings:
        yield_pct = h.get("yield_pct", 0.0)
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
    leverage = total_long / total_nav if total_nav > 0 else 0.0

    return PortfolioCarry(
        total_nav=round(total_nav, 2),
        total_long_value=round(total_long, 2),
        total_margin=round(total_margin, 2),
        leverage_ratio=round(leverage, 2),
        total_annual_income=round(total_income, 2),
        total_annual_margin_cost=round(total_margin_cost, 2),
        net_carry=round(net_carry, 2),
        income_coverage_ratio=round(coverage, 2),
        margin_as_pct_of_nav=round(total_margin / total_nav * 100, 1) if total_nav > 0 else 0.0,
        positions=positions,
    )
