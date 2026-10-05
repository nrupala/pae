"""Tests for the PAE MCP server (analytics/pae/mcp).

Uses a REAL MCP client session (in-memory ``mcp.Client`` against the
``MCPServer``) for every tool. Engine-backed tools (compute_risk,
monte_carlo, stress_test) need the Rust engine: ``test_engine_tools_live``
fails LOUDLY when no engine is reachable (never silently skips); proxy
plumbing is behavior-verified separately against a fake local engine.
"""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import httpx
import numpy as np
import pytest
from mcp import Client
from mcp.server.mcpserver import MCPServer

from pae.decision.journal import DecisionEntry
from pae.mcp import TOOL_NAMES, TOOL_SPECS, create_mcp_server, run_agent_tool
from pae.mcp.tools import DISCLOSURE, EngineUnreachableError, PAETools
from pae.models.factor import decompose
from pae.storage.db import Holding, NotFoundError, PAEDatabase, Portfolio

ENGINE_URL = (
    os.environ.get("PAE_ENGINE_URL")
    or os.environ.get("RUST_ENGINE_URL")
    or "http://localhost:3001"
)

EXPECTED_TOOLS = [
    "compute_risk",
    "monte_carlo",
    "stress_test",
    "factor_decompose",
    "journal_log",
    "holdings_create",
    "holdings_list",
    "holdings_update",
    "holdings_delete",
    "dashboard_summary",
]

UNREACHABLE_ENGINE = "http://127.0.0.1:9"  # discard port: refused instantly


# --- Fixtures ---


@pytest.fixture()
def db(tmp_path):
    """Fresh database with one portfolio and two holdings (with returns)."""
    database = PAEDatabase(str(tmp_path / "pae.db"))
    database.initialize()
    portfolio = database.insert_portfolio(Portfolio(name="Fixture"))
    rng = np.random.default_rng(42)
    database.insert_holding(
        Holding(
            portfolio_id=portfolio.id,
            symbol="AAA",
            name="AAA Corp",
            asset_class="equity",
            quantity=100.0,
            market_value=10000.0,
            cost_basis=9000.0,
            yield_pct=2.0,
            returns_json=json.dumps([float(x) for x in rng.normal(0.01, 0.05, 60)]),
        )
    )
    database.insert_holding(
        Holding(
            portfolio_id=portfolio.id,
            symbol="BBB",
            name="BBB Inc",
            asset_class="fixed_income",
            quantity=50.0,
            market_value=5000.0,
            cost_basis=5200.0,
            yield_pct=4.0,
            returns_json=json.dumps([float(x) for x in rng.normal(0.005, 0.02, 60)]),
        )
    )
    yield database
    database.close()


@pytest.fixture()
def portfolio_id(db: PAEDatabase) -> str:
    return db.get_portfolios()[0].id


@pytest.fixture()
def mcp_server(db: PAEDatabase) -> MCPServer:
    """MCP server bound to the fixture DB and an unreachable engine."""
    return create_mcp_server(db=db, engine_url=UNREACHABLE_ENGINE)


async def _call(server: MCPServer, tool: str, args: dict[str, Any]) -> Any:
    """Call a tool through a real in-memory MCP client session."""
    async with Client(server) as client:
        return await client.call_tool(tool, args)


def _structured(result: Any) -> dict[str, Any]:
    assert result.structured_content is not None, "expected structured content"
    assert isinstance(result.structured_content, dict)
    return result.structured_content


# --- Tool listing & disclosures ---


@pytest.mark.asyncio()
async def test_tool_listing_has_all_tools(mcp_server: MCPServer) -> None:
    async with Client(mcp_server) as client:
        listing = await client.list_tools()
    names = [t.name for t in listing.tools]
    assert names == EXPECTED_TOOLS
    assert [n for n, _ in TOOL_SPECS] == EXPECTED_TOOLS
    assert TOOL_NAMES == EXPECTED_TOOLS


@pytest.mark.asyncio()
async def test_every_tool_description_carries_disclosure(mcp_server: MCPServer) -> None:
    async with Client(mcp_server) as client:
        listing = await client.list_tools()
    for tool in listing.tools:
        assert DISCLOSURE in tool.description, f"{tool.name} missing disclosure"


# --- Python-native tools ---


@pytest.mark.asyncio()
async def test_factor_decompose_matches_direct_call(mcp_server: MCPServer) -> None:
    rng = np.random.default_rng(7)
    portfolio_returns = [float(x) for x in rng.normal(0.008, 0.04, 60)]
    factor_returns = {
        name: [float(x) for x in rng.normal(0.0, 0.03, 60)]
        for name in ("MKT-RF", "SMB", "HML", "RMW", "CMA")
    }

    result = await _call(
        mcp_server,
        "factor_decompose",
        {"portfolio_returns": portfolio_returns, "factor_returns": factor_returns},
    )
    assert not result.is_error
    payload = _structured(result)

    expected = decompose(
        np.asarray(portfolio_returns),
        {k: np.asarray(v) for k, v in factor_returns.items()},
    )
    assert payload["alpha"] == pytest.approx(expected.alpha)
    assert payload["r_squared"] == pytest.approx(expected.r_squared)
    assert payload["residual_risk_pct"] == pytest.approx(expected.residual_risk_pct)
    assert [e["factor_name"] for e in payload["exposures"]] == list(factor_returns)
    for got, want in zip(payload["exposures"], expected.exposures, strict=True):
        assert got["beta"] == pytest.approx(want.beta)
        assert got["t_stat"] == pytest.approx(want.t_stat)


@pytest.mark.asyncio()
async def test_factor_decompose_rejects_bad_input(mcp_server: MCPServer) -> None:
    result = await _call(
        mcp_server,
        "factor_decompose",
        {"portfolio_returns": [0.01, 0.02], "factor_returns": {"MKT-RF": [0.01]}},
    )
    assert result.is_error
    assert "Traceback" not in "".join(c.text for c in result.content)


@pytest.mark.asyncio()
async def test_journal_log_persists_and_is_retrievable(
    mcp_server: MCPServer, db: PAEDatabase
) -> None:
    result = await _call(
        mcp_server,
        "journal_log",
        {
            "action": "Trim AAA on valuation",
            "symbols_affected": ["AAA"],
            "rationale": "Concentration above target",
            "thesis": "Rebalance to target weights",
            "confidence": 8,
            "emotional_state": "calm",
            "max_acceptable_loss_pct": 5.0,
        },
    )
    assert not result.is_error
    payload = _structured(result)
    assert payload["ok"] is True

    entry_id = payload["entry_id"]
    stored = db.get_journal_entry(entry_id)
    assert stored.action == "Trim AAA on valuation"
    assert stored.symbols_affected == ["AAA"]
    assert stored.confidence == 8
    assert stored.emotional_state == "calm"

    entries = db.get_journal_entries()
    assert any(e.entry_id == entry_id for e in entries)


@pytest.mark.asyncio()
async def test_journal_log_rejects_invalid_entry(mcp_server: MCPServer) -> None:
    result = await _call(
        mcp_server,
        "journal_log",
        {"action": "Bad entry", "confidence": 99, "emotional_state": "spicy"},
    )
    assert result.is_error
    assert "Traceback" not in "".join(c.text for c in result.content)


def test_journal_db_roundtrip(db: PAEDatabase) -> None:
    entry = DecisionEntry(
        action="Roundtrip",
        symbols_affected=["AAA", "BBB"],
        alternatives_considered=["Hold"],
        confidence=6,
        outcome_90d=3.5,
        was_thesis_correct=True,
    )
    db.insert_journal_entry(entry)
    back = db.get_journal_entry(entry.entry_id)
    assert back.action == "Roundtrip"
    assert back.symbols_affected == ["AAA", "BBB"]
    assert back.alternatives_considered == ["Hold"]
    assert back.outcome_90d == 3.5
    assert back.was_thesis_correct is True
    with pytest.raises(NotFoundError):
        db.get_journal_entry("nope-nope-nope")


# --- Holdings CRUD via MCP ---


@pytest.mark.asyncio()
async def test_holdings_crud_roundtrip(mcp_server: MCPServer, portfolio_id: str) -> None:
    created = _structured(
        await _call(
            mcp_server,
            "holdings_create",
            {
                "portfolio_id": portfolio_id,
                "symbol": "ccc",
                "name": "CCC Ltd",
                "quantity": 10.0,
                "market_value": 2500.0,
                "cost_basis": 2000.0,
            },
        )
    )
    assert created["ok"] is True
    assert created["holding"]["symbol"] == "CCC"  # normalized to upper case
    holding_id = created["holding"]["id"]

    listed = _structured(
        await _call(mcp_server, "holdings_list", {"portfolio_id": portfolio_id})
    )
    assert listed["count"] == 3
    ccc = next(h for h in listed["holdings"] if h["id"] == holding_id)
    assert ccc["weight_pct"] == pytest.approx(round(2500 / 17500 * 100, 2))
    assert ccc["unrealized_pnl"] == pytest.approx(500.0)

    updated = _structured(
        await _call(
            mcp_server, "holdings_update", {"holding_id": holding_id, "market_value": 3000.0}
        )
    )
    assert updated == {"ok": True, "updated": holding_id}

    deleted = _structured(
        await _call(mcp_server, "holdings_delete", {"holding_id": holding_id})
    )
    assert deleted == {"ok": True, "deleted": holding_id}

    listed2 = _structured(
        await _call(mcp_server, "holdings_list", {"portfolio_id": portfolio_id})
    )
    assert listed2["count"] == 2


@pytest.mark.asyncio()
async def test_holdings_update_missing_is_error(mcp_server: MCPServer) -> None:
    result = await _call(
        mcp_server, "holdings_update", {"holding_id": "does-not-exist", "quantity": 1.0}
    )
    assert result.is_error


# --- Dashboard ---


@pytest.mark.asyncio()
async def test_dashboard_summary(mcp_server: MCPServer, portfolio_id: str) -> None:
    payload = _structured(
        await _call(mcp_server, "dashboard_summary", {"portfolio_id": portfolio_id})
    )
    assert payload["ok"] is True
    assert payload["summary"]["total_market_value"] == pytest.approx(15000.0)
    assert payload["summary"]["holding_count"] == 2
    assert payload["allocation"]["equity"] == pytest.approx(round(10000 / 15000 * 100, 2))
    assert payload["allocation"]["fixed_income"] == pytest.approx(round(5000 / 15000 * 100, 2))
    assert payload["holding_count"] == 2
    assert payload["top_holdings"][0]["symbol"] == "AAA"


# --- Engine proxy: plumbing against a fake engine ---


class _FakeEngineHandler(BaseHTTPRequestHandler):
    """Minimal stand-in for the Rust engine: echoes canned analytics."""

    def log_message(self, *args: Any) -> None:  # silence test output
        pass

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        payload = json.loads(body) if body else {}
        holdings = payload.get("holdings", [])

        if self.path == "/api/v1/portfolio/risk":
            response: dict[str, Any] = {
                "var_95": 1.5,
                "var_99": 2.5,
                "cvar_95": 2.0,
                "max_drawdown": 8.0,
                "beta": 1.1,
                "sharpe": 0.9,
                "sortino": 1.2,
                "volatility": 12.0,
                "holdings_seen": len(holdings),
            }
        elif self.path == "/api/v1/portfolio/montecarlo":
            response = {
                "num_simulations": payload.get("num_simulations"),
                "time_horizon_months": payload.get("time_horizon_months"),
                "initial_value": payload.get("initial_value"),
                "probability_of_loss": 0.3,
            }
        elif self.path == "/api/v1/portfolio/stress":
            response = {
                "scenario": payload.get("scenario"),
                "portfolio_impact_pct": -12.5,
                "position_impacts": [],
            }
        else:
            self.send_response(404)
            self.end_headers()
            return

        data = json.dumps(response).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture()
def fake_engine():
    server = HTTPServer(("127.0.0.1", 0), _FakeEngineHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    thread.join()


@pytest.mark.asyncio()
async def test_engine_proxy_plumbing(
    db: PAEDatabase, portfolio_id: str, fake_engine: str
) -> None:
    """The httpx proxy path works end-to-end (payload + pass-through)."""
    server = create_mcp_server(db=db, engine_url=fake_engine)

    risk = _structured(await _call(server, "compute_risk", {"portfolio_id": portfolio_id}))
    assert risk["var_95"] == 1.5
    assert risk["holdings_seen"] == 2  # holdings payload reached the engine

    mc = _structured(
        await _call(
            server,
            "monte_carlo",
            {
                "portfolio_id": portfolio_id,
                "num_simulations": 5000,
                "time_horizon_months": 6,
            },
        )
    )
    assert mc["num_simulations"] == 5000
    assert mc["time_horizon_months"] == 6
    assert mc["initial_value"] == pytest.approx(15000.0)

    stress = _structured(
        await _call(
            server, "stress_test", {"portfolio_id": portfolio_id, "scenario": "2008"}
        )
    )
    assert stress["scenario"] == "2008"
    assert stress["portfolio_impact_pct"] == -12.5


@pytest.mark.asyncio()
async def test_engine_unreachable_is_clean_error(mcp_server: MCPServer, portfolio_id: str) -> None:
    """Unreachable engine -> is_error result, no traceback leak."""
    result = await _call(mcp_server, "compute_risk", {"portfolio_id": portfolio_id})
    assert result.is_error
    text = "".join(c.text for c in result.content)
    assert "Traceback" not in text


@pytest.mark.asyncio()
async def test_engine_tools_raise_loudly_when_unreachable(
    db: PAEDatabase, portfolio_id: str
) -> None:
    """Direct calls raise EngineUnreachableError (fail loudly, never hang)."""
    tools = PAETools(db, engine_url=UNREACHABLE_ENGINE)
    with pytest.raises(EngineUnreachableError, match="not reachable"):
        await tools.compute_risk(portfolio_id)
    with pytest.raises(EngineUnreachableError, match="not reachable"):
        await tools.monte_carlo(portfolio_id)
    with pytest.raises(EngineUnreachableError, match="not reachable"):
        await tools.stress_test(portfolio_id, "2008")


@pytest.mark.asyncio()
async def test_run_agent_tool_unknown_tool(db: PAEDatabase) -> None:
    outcome = await run_agent_tool("nope", {}, db)
    assert outcome["ok"] is False
    assert "Unknown tool" in outcome["error"]
    assert outcome["known_tools"] == EXPECTED_TOOLS


@pytest.mark.asyncio()
async def test_run_agent_tool_bad_params(db: PAEDatabase) -> None:
    outcome = await run_agent_tool("factor_decompose", {"bogus": 1}, db)
    assert outcome["ok"] is False
    assert "Invalid parameters" in outcome["error"]


# --- Live engine: FAIL LOUDLY when absent (never silently skip) ---


async def _engine_reachable(url: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=2.0, trust_env=False) as client:
            resp = await client.get(url.rstrip("/") + "/health")
            return resp.status_code < 500
    except Exception:
        return False


@pytest.mark.asyncio()
async def test_engine_tools_against_live_engine(db: PAEDatabase, portfolio_id: str) -> None:
    """Requires a live Rust engine. Fails LOUDLY when unreachable."""
    if not await _engine_reachable(ENGINE_URL):
        pytest.fail(
            f"Rust engine not reachable at {ENGINE_URL} — compute_risk / "
            "monte_carlo / stress_test are UNVERIFIED against the real engine. "
            "Start the engine (engine/ on :3001) or set PAE_ENGINE_URL / "
            "RUST_ENGINE_URL, then re-run."
        )
    server = create_mcp_server(db=db, engine_url=ENGINE_URL)
    risk = _structured(await _call(server, "compute_risk", {"portfolio_id": portfolio_id}))
    for key in (
        "var_95",
        "var_99",
        "cvar_95",
        "max_drawdown",
        "sharpe",
        "sortino",
        "volatility",
    ):
        assert key in risk, f"live engine risk response missing {key}"

    mc = _structured(await _call(server, "monte_carlo", {"portfolio_id": portfolio_id}))
    assert "probability_of_loss" in mc

    stress = _structured(
        await _call(server, "stress_test", {"portfolio_id": portfolio_id, "scenario": "2008"})
    )
    assert "portfolio_impact_pct" in stress
