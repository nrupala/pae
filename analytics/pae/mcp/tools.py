"""PAE agent tool implementations.

One async function per tool, each a thin wrapper over existing logic:

- ``compute_risk`` / ``monte_carlo`` / ``stress_test`` proxy the Rust engine
  over HTTP (same pattern as ``pae.server``'s analytics proxy).
- ``factor_decompose`` wraps :func:`pae.models.factor.decompose`.
- ``journal_log`` validates via :func:`pae.decision.journal.validate_entry`
  and persists with :class:`pae.storage.db.PAEDatabase`.
- ``holdings_*`` wrap the same :class:`pae.storage.db.PAEDatabase` CRUD the
  REST layer uses.
- ``dashboard_summary`` is the single implementation behind both the REST
  ``GET /api/v1/dashboard/{id}`` route and the agent tool.

Every tool raises :class:`PAEToolError` (or a subclass) on failure; the
message is always safe to relay to an agent caller — never a traceback.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx
import numpy as np

from pae.decision.journal import DecisionEntry, validate_entry
from pae.models.factor import FactorError, decompose
from pae.models.optimize import OptimizeError, holdings_to_inputs, optimize
from pae.storage.db import (
    DatabaseError,
    Holding,
    NotFoundError,
    PAEDatabase,
)

logger = logging.getLogger(__name__)

DISCLOSURE = (
    "Educational analytics only. PAE calculates; the user decides. "
    "No investment advice."
)

DEFAULT_ENGINE_URL = "http://localhost:3001"


def resolve_engine_url(explicit: str | None = None) -> str:
    """Resolve the Rust engine URL: explicit arg, then env, then default."""
    return (
        explicit
        or os.environ.get("PAE_ENGINE_URL")
        or os.environ.get("RUST_ENGINE_URL")
        or DEFAULT_ENGINE_URL
    )


class PAEToolError(Exception):
    """A tool failed in a way that is safe to report to an agent caller."""


class EngineUnreachableError(PAEToolError):
    """The Rust engine could not be reached (connection refused / timeout)."""


class PAETools:
    """The PAE toolset bound to one database and one engine URL."""

    def __init__(self, db: PAEDatabase, engine_url: str | None = None) -> None:
        self._db = db
        self._engine_url = resolve_engine_url(engine_url)

    # --- Rust engine proxy ---

    async def _post_engine(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        """POST to the Rust engine; map transport failures to PAEToolError.

        ``trust_env=False``: the engine is a local sidecar, so proxy env
        vars must never reroute (or break) engine traffic.
        """
        url = self._engine_url.rstrip("/") + path
        try:
            async with httpx.AsyncClient(timeout=30.0, trust_env=False) as client:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                data = resp.json()
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise EngineUnreachableError(
                f"Rust engine not reachable at {self._engine_url}"
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise PAEToolError(
                f"Engine returned HTTP {exc.response.status_code}"
            ) from exc
        if not isinstance(data, dict):
            raise PAEToolError("Engine returned a non-object response")
        return data

    def _engine_holdings(self, portfolio_id: str) -> list[dict[str, Any]]:
        """Holdings formatted for the engine; error when the portfolio is empty."""
        holdings = self._db.get_holdings_for_engine(portfolio_id)
        if not holdings:
            raise PAEToolError(
                f"No holdings found for portfolio '{portfolio_id}'"
            )
        return holdings

    async def compute_risk(self, portfolio_id: str) -> dict[str, Any]:
        """Compute risk metrics (VaR, CVaR, Sharpe, Sortino, drawdown) via the engine."""
        holdings = self._engine_holdings(portfolio_id)
        return await self._post_engine(
            "/api/v1/portfolio/risk", {"holdings": holdings}
        )

    async def monte_carlo(
        self,
        portfolio_id: str,
        num_simulations: int = 10000,
        time_horizon_months: int = 12,
    ) -> dict[str, Any]:
        """Run a Monte Carlo simulation of portfolio value via the engine."""
        holdings = self._engine_holdings(portfolio_id)
        summary = self._db.get_portfolio_summary(portfolio_id)
        return await self._post_engine(
            "/api/v1/portfolio/montecarlo",
            {
                "holdings": holdings,
                "num_simulations": num_simulations,
                "time_horizon_months": time_horizon_months,
                "initial_value": summary["total_market_value"],
            },
        )

    async def stress_test(
        self,
        portfolio_id: str,
        scenario: str,
        custom_shocks: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Run a stress-test scenario (e.g. '2008', '2020', 'rate_shock') via the engine."""
        holdings = self._engine_holdings(portfolio_id)
        return await self._post_engine(
            "/api/v1/portfolio/stress",
            {
                "holdings": holdings,
                "scenario": scenario,
                "custom_shocks": custom_shocks,
            },
        )

    # --- Python-native analytics ---

    async def factor_decompose(
        self,
        portfolio_returns: list[float],
        factor_returns: dict[str, list[float]],
    ) -> dict[str, Any]:
        """Decompose portfolio returns into Fama-French factor exposures (OLS)."""
        try:
            result = decompose(
                np.asarray(portfolio_returns, dtype=np.float64),
                {
                    name: np.asarray(rets, dtype=np.float64)
                    for name, rets in factor_returns.items()
                },
            )
        except (ValueError, FactorError) as exc:
            raise PAEToolError(str(exc)) from exc
        return {
            "alpha": result.alpha,
            "alpha_t_stat": result.alpha_t_stat,
            "r_squared": result.r_squared,
            "exposures": [
                {
                    "factor_name": e.factor_name,
                    "beta": e.beta,
                    "t_stat": e.t_stat,
                    "contribution_pct": e.contribution_pct,
                }
                for e in result.exposures
            ],
            "residual_risk_pct": result.residual_risk_pct,
        }

    # --- Portfolio optimization ---

    async def optimize_portfolio(
        self,
        symbols: list[str] | None = None,
        expected_returns: list[float] | None = None,
        covariance: list[list[float]] | None = None,
        portfolio_id: str | None = None,
        risk_free_rate: float = 0.0,
        frontier_points: int = 25,
    ) -> dict[str, Any]:
        """Compute long-only optimal mixes and the efficient frontier.

        Either pass ``portfolio_id`` (expected returns and covariance are
        derived from the holdings' stored return series) or pass
        ``symbols`` + ``expected_returns`` + ``covariance`` explicitly.
        Returns maximum-Sharpe, minimum-variance, and risk-parity mixes
        plus efficient-frontier points. Analytics only -- no advice.
        """
        try:
            if portfolio_id:
                holdings = self._db.get_holdings(portfolio_id=portfolio_id)
                if not holdings:
                    raise PAEToolError(
                        f"No holdings found for portfolio '{portfolio_id}'"
                    )
                syms, mu, cov = holdings_to_inputs(
                    [(h.symbol, h.returns_json) for h in holdings]
                )
            else:
                if not symbols or not expected_returns or not covariance:
                    raise PAEToolError(
                        "Provide portfolio_id or symbols + expected_returns "
                        "+ covariance"
                    )
                syms, mu, cov = symbols, expected_returns, covariance
            result = optimize(
                syms,
                mu,
                cov,
                risk_free_rate=risk_free_rate,
                frontier_points=frontier_points,
            )
        except OptimizeError as exc:
            raise PAEToolError(str(exc)) from exc
        except ValueError as exc:
            raise PAEToolError(str(exc)) from exc
        return result.as_dict()

    # --- Decision journal ---

    async def journal_log(
        self,
        action: str,
        symbols_affected: list[str] | None = None,
        rationale: str = "",
        alternatives_considered: list[str] | None = None,
        thesis: str = "",
        confidence: int = 5,
        time_horizon: str = "",
        what_could_go_wrong: str = "",
        max_acceptable_loss_pct: float = 0.0,
        emotional_state: str = "neutral",
        market_context: str = "",
        trigger: str = "",
        outcome_30d: float | None = None,
        outcome_90d: float | None = None,
        outcome_180d: float | None = None,
        outcome_notes: str = "",
        was_thesis_correct: bool | None = None,
    ) -> dict[str, Any]:
        """Log a decision-journal entry (rationale, confidence, pre-mortem)."""
        entry = DecisionEntry(
            action=action,
            symbols_affected=symbols_affected or [],
            rationale=rationale,
            alternatives_considered=alternatives_considered or [],
            thesis=thesis,
            confidence=confidence,
            time_horizon=time_horizon,
            what_could_go_wrong=what_could_go_wrong,
            max_acceptable_loss_pct=max_acceptable_loss_pct,
            emotional_state=emotional_state,
            market_context=market_context,
            trigger=trigger,
            outcome_30d=outcome_30d,
            outcome_90d=outcome_90d,
            outcome_180d=outcome_180d,
            outcome_notes=outcome_notes,
            was_thesis_correct=was_thesis_correct,
        )
        errors = validate_entry(entry)
        if errors:
            raise PAEToolError("Invalid journal entry: " + "; ".join(errors))
        try:
            self._db.insert_journal_entry(entry)
        except DatabaseError as exc:
            raise PAEToolError(str(exc)) from exc
        return {"ok": True, "entry_id": entry.entry_id, "timestamp": entry.timestamp}

    # --- Holdings CRUD ---

    async def holdings_create(
        self,
        portfolio_id: str,
        symbol: str,
        name: str = "",
        asset_class: str = "equity",
        quantity: float = 0.0,
        market_value: float = 0.0,
        cost_basis: float = 0.0,
        yield_pct: float = 0.0,
        currency: str = "CAD",
        account_id: str = "",
        returns: list[float] | None = None,
    ) -> dict[str, Any]:
        """Add a holding to a portfolio."""
        try:
            holding = self._db.insert_holding(
                Holding(
                    portfolio_id=portfolio_id,
                    account_id=account_id,
                    symbol=symbol.upper(),
                    name=name,
                    asset_class=asset_class,
                    quantity=quantity,
                    market_value=market_value,
                    cost_basis=cost_basis,
                    yield_pct=yield_pct,
                    currency=currency,
                    returns_json=json.dumps(returns or []),
                )
            )
        except DatabaseError as exc:
            raise PAEToolError(str(exc)) from exc
        return {"ok": True, "holding": {"id": holding.id, "symbol": holding.symbol}}

    async def holdings_list(
        self,
        portfolio_id: str | None = None,
        account_id: str | None = None,
    ) -> dict[str, Any]:
        """List holdings, optionally filtered by portfolio and/or account."""
        holdings = self._db.get_holdings(portfolio_id=portfolio_id, account_id=account_id)
        total_value = sum(h.market_value for h in holdings)
        result = []
        for h in holdings:
            weight = (h.market_value / total_value * 100) if total_value > 0 else 0.0
            try:
                returns = json.loads(h.returns_json)
            except (json.JSONDecodeError, TypeError):
                returns = []
            result.append(
                {
                    "id": h.id,
                    "symbol": h.symbol,
                    "name": h.name,
                    "asset_class": h.asset_class,
                    "quantity": h.quantity,
                    "market_value": round(h.market_value, 2),
                    "cost_basis": round(h.cost_basis, 2),
                    "weight_pct": round(weight, 2),
                    "yield_pct": h.yield_pct,
                    "currency": h.currency,
                    "unrealized_pnl": round(h.market_value - h.cost_basis, 2),
                    "returns_count": len(returns),
                }
            )
        return {
            "ok": True,
            "holdings": result,
            "total_market_value": round(total_value, 2),
            "count": len(result),
        }

    async def holdings_update(
        self,
        holding_id: str,
        symbol: str | None = None,
        name: str | None = None,
        asset_class: str | None = None,
        quantity: float | None = None,
        market_value: float | None = None,
        cost_basis: float | None = None,
        yield_pct: float | None = None,
        currency: str | None = None,
        returns: list[float] | None = None,
    ) -> dict[str, Any]:
        """Update fields of an existing holding."""
        try:
            existing = self._db.get_holding_by_id(holding_id)
        except NotFoundError as exc:
            raise PAEToolError(str(exc)) from exc

        if symbol is not None:
            existing.symbol = symbol.upper()
        if name is not None:
            existing.name = name
        if asset_class is not None:
            existing.asset_class = asset_class
        if quantity is not None:
            existing.quantity = quantity
        if market_value is not None:
            existing.market_value = market_value
        if cost_basis is not None:
            existing.cost_basis = cost_basis
        if yield_pct is not None:
            existing.yield_pct = yield_pct
        if currency is not None:
            existing.currency = currency
        if returns is not None:
            existing.returns_json = json.dumps(returns)

        try:
            self._db.update_holding(existing)
        except DatabaseError as exc:
            raise PAEToolError(str(exc)) from exc
        return {"ok": True, "updated": holding_id}

    async def holdings_delete(self, holding_id: str) -> dict[str, Any]:
        """Delete a holding."""
        try:
            self._db.delete_holding(holding_id)
        except NotFoundError as exc:
            raise PAEToolError(str(exc)) from exc
        return {"ok": True, "deleted": holding_id}

    # --- Dashboard ---

    async def dashboard_summary(self, portfolio_id: str) -> dict[str, Any]:
        """Aggregate dashboard view: summary, allocation, top holdings.

        Single implementation shared by the REST route and the agent tool.
        """
        database = self._db
        summary = database.get_portfolio_summary(portfolio_id)
        holdings = database.get_holdings(portfolio_id=portfolio_id)

        total_value = summary["total_market_value"]

        allocation: dict[str, float] = {}
        for h in holdings:
            allocation[h.asset_class] = allocation.get(h.asset_class, 0.0) + h.market_value

        allocation_pct = {
            k: round(v / total_value * 100, 2) if total_value > 0 else 0.0
            for k, v in allocation.items()
        }

        top_holdings = sorted(holdings, key=lambda h: h.market_value, reverse=True)[:10]

        return {
            "ok": True,
            "summary": summary,
            "allocation": allocation_pct,
            "top_holdings": [
                {
                    "symbol": h.symbol,
                    "name": h.name,
                    "market_value": round(h.market_value, 2),
                    "weight_pct": (
                        round(h.market_value / total_value * 100, 2)
                        if total_value > 0
                        else 0.0
                    ),
                    "yield_pct": h.yield_pct,
                    "unrealized_pnl": round(h.market_value - h.cost_basis, 2),
                }
                for h in top_holdings
            ],
            "holding_count": len(holdings),
        }


TOOL_SPECS: list[tuple[str, str]] = [
    (
        "compute_risk",
        "Compute portfolio risk metrics — VaR, CVaR, Sharpe, Sortino, "
        "max drawdown, volatility — via the Rust risk engine. " + DISCLOSURE,
    ),
    (
        "monte_carlo",
        "Run a Monte Carlo simulation of portfolio value over a horizon "
        "via the Rust risk engine. " + DISCLOSURE,
    ),
    (
        "stress_test",
        "Run a stress-test scenario against the portfolio via the Rust "
        "risk engine. " + DISCLOSURE,
    ),
    (
        "factor_decompose",
        "Decompose portfolio returns into Fama-French factor exposures "
        "(market, size, value, profitability, investment) via OLS. "
        + DISCLOSURE,
    ),
    (
        "optimize_portfolio",
        "Compute long-only optimal portfolio mixes -- maximum-Sharpe, "
        "minimum-variance, and risk-parity -- plus the efficient frontier, "
        "from expected returns and a covariance matrix (or derive them "
        "from a portfolio's stored holding returns). " + DISCLOSURE,
    ),
    (
        "journal_log",
        "Log a decision-journal entry: action, rationale, alternatives, "
        "thesis, confidence, pre-mortem. " + DISCLOSURE,
    ),
    (
        "holdings_create",
        "Add a holding to a portfolio. " + DISCLOSURE,
    ),
    (
        "holdings_list",
        "List holdings, optionally filtered by portfolio and/or account. "
        + DISCLOSURE,
    ),
    (
        "holdings_update",
        "Update fields of an existing holding. " + DISCLOSURE,
    ),
    (
        "holdings_delete",
        "Delete a holding. " + DISCLOSURE,
    ),
    (
        "dashboard_summary",
        "Aggregate portfolio dashboard: summary, allocation by asset "
        "class, top holdings. " + DISCLOSURE,
    ),
]

TOOL_NAMES: list[str] = [name for name, _ in TOOL_SPECS]


async def run_agent_tool(
    tool_name: str,
    params: dict[str, Any],
    db: PAEDatabase,
    engine_url: str | None = None,
) -> dict[str, Any]:
    """Dispatch a tool call by name against one database.

    Shared by the A2A message endpoint and any other agent caller. Never
    raises and never leaks a traceback: failures become
    ``{"ok": False, "error": <message>}``.
    """
    if tool_name not in TOOL_NAMES:
        return {
            "ok": False,
            "error": f"Unknown tool: {tool_name}",
            "known_tools": TOOL_NAMES,
        }
    tools = PAETools(db, engine_url=engine_url)
    fn = getattr(tools, tool_name)
    try:
        result = await fn(**params)
    except TypeError as exc:
        return {"ok": False, "error": f"Invalid parameters for {tool_name}: {exc}"}
    except PAEToolError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception:  # noqa: BLE001 — never leak tracebacks to agent callers
        logger.exception("Unhandled error in tool %s", tool_name)
        return {"ok": False, "error": f"Tool {tool_name} failed unexpectedly"}
    if not isinstance(result, dict):
        return {"ok": False, "error": f"Tool {tool_name} returned a bad result"}
    return {"ok": True, "result": result}
