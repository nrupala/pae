"""PAE Python API Server.

Thin FastAPI layer that bridges:
- UI <-> SQLite storage (holdings CRUD, CSV import)
- UI <-> Rust engine (proxies risk/analytics requests)
- UI <-> Python analytics (factor models, carry analysis, PKE)

Runs alongside the Rust engine. UI talks to this server for data management,
and to the Rust engine directly for high-performance risk calculations.

Usage:
    uvicorn pae.server:app --port 3002 --reload
"""

import json
import logging
import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from pae.auth import api_key_or_none
from pae.mcp import TOOL_SPECS, get_tool_manifest, run_agent_tool
from pae.mcp.tools import DISCLOSURE, PAETools
from pae.models.brinson import attribute as brinson_attribute
from pae.models.carry import analyze_carry
from pae.models.optimize import OptimizeError, holdings_to_inputs, optimize
from pae.storage.csv_import import import_csv_string
from pae.storage.db import (
    Account,
    DatabaseError,
    Holding,
    NotFoundError,
    PAEDatabase,
    Portfolio,
    ValidationError,
)

logger = logging.getLogger(__name__)

# --- Configuration ---

DB_PATH = os.environ.get("PAE_DB_PATH", "data/pae.db")
RUST_ENGINE_URL = os.environ.get("PAE_ENGINE_URL", "http://localhost:3001")

# --- App Lifecycle ---

db: PAEDatabase | None = None


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:  # noqa: ARG001
    """Initialize and teardown database on app start/stop."""
    global db  # noqa: PLW0603
    db = PAEDatabase(DB_PATH)
    db.initialize()

    # Create default portfolio if none exists
    portfolios = db.get_portfolios()
    if not portfolios:
        db.insert_portfolio(Portfolio(name="Default"))
        logger.info("Created default portfolio")

    logger.info("PAE Python server started (db: %s)", DB_PATH)
    yield
    if db:
        db.close()
    logger.info("PAE Python server stopped")


app = FastAPI(
    title="PAE - Personal Analytics Engine",
    version="0.1.0",
    description="Non-advisory, zero-knowledge financial analytics platform.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_db() -> PAEDatabase:
    """Get the database instance. Raises if not initialized."""
    if db is None:
        raise HTTPException(status_code=503, detail="Database not initialized")
    return db


# --- Request/Response Models ---


class PortfolioCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""


class AccountCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    account_type: str = "taxable"
    broker: str = ""
    currency: str = "CAD"


class HoldingCreate(BaseModel):
    portfolio_id: str
    account_id: str = ""
    symbol: str = Field(min_length=1, max_length=20)
    name: str = ""
    asset_class: str = "equity"
    quantity: float = 0.0
    market_value: float = 0.0
    cost_basis: float = 0.0
    yield_pct: float = 0.0
    currency: str = "CAD"
    returns: list[float] = []


class HoldingUpdate(BaseModel):
    symbol: str | None = None
    name: str | None = None
    asset_class: str | None = None
    quantity: float | None = None
    market_value: float | None = None
    cost_basis: float | None = None
    yield_pct: float | None = None
    currency: str | None = None
    returns: list[float] | None = None


class CarryRequest(BaseModel):
    portfolio_id: str
    total_margin: float = 0.0
    margin_rate: float = 0.058


class OptimizeRequest(BaseModel):
    """Long-only portfolio optimization request.

    Either ``portfolio_id`` (expected returns and covariance are derived
    from the holdings' stored return series) or explicit ``symbols`` +
    ``expected_returns`` + ``covariance``.
    """

    portfolio_id: str | None = None
    symbols: list[str] | None = None
    expected_returns: list[float] | None = None
    covariance: list[list[float]] | None = None
    risk_free_rate: float = 0.0
    frontier_points: int = Field(default=25, ge=2, le=100)


class AttributionSegment(BaseModel):
    segment: str = Field(min_length=1, max_length=200)
    weight: float
    segment_return: float = Field(
        description="Period return as a decimal fraction (0.05 = 5%)"
    )


class AttributionRequest(BaseModel):
    portfolio_segments: list[AttributionSegment] = Field(min_length=1)
    benchmark_segments: list[AttributionSegment] = Field(min_length=1)

# --- Error Handlers ---


@app.exception_handler(ValidationError)
async def validation_error_handler(request: Any, exc: ValidationError) -> JSONResponse:  # noqa: ARG001
    return JSONResponse(status_code=400, content={"error": str(exc), "code": "VALIDATION_ERROR"})


@app.exception_handler(NotFoundError)
async def not_found_error_handler(request: Any, exc: NotFoundError) -> JSONResponse:  # noqa: ARG001
    return JSONResponse(status_code=404, content={"error": str(exc), "code": "NOT_FOUND"})


@app.exception_handler(DatabaseError)
async def database_error_handler(request: Any, exc: DatabaseError) -> JSONResponse:  # noqa: ARG001
    logger.error("Database error: %s", exc)
    return JSONResponse(
        status_code=500,
        content={"error": "Internal database error", "code": "DB_ERROR"},
    )


# --- Health ---


@app.get("/health")
async def health() -> dict[str, Any]:
    """Health check for the Python server."""
    database = get_db()
    portfolio_count = len(database.get_portfolios())
    return {
        "status": "ok",
        "service": "pae-python",
        "db_path": str(DB_PATH),
        "portfolios": portfolio_count,
    }


# --- Portfolio Endpoints ---


@app.get("/api/v1/portfolios")
async def list_portfolios() -> dict[str, Any]:
    """List all portfolios with summary stats."""
    database = get_db()
    portfolios = database.get_portfolios()
    result = []
    for p in portfolios:
        summary = database.get_portfolio_summary(p.id)
        result.append({**summary, "name": p.name, "description": p.description, "id": p.id})
    return {"portfolios": result}


@app.post("/api/v1/portfolios", status_code=201)
async def create_portfolio(req: PortfolioCreate) -> dict[str, Any]:
    """Create a new portfolio."""
    database = get_db()
    portfolio = database.insert_portfolio(Portfolio(name=req.name, description=req.description))
    return {"portfolio": {"id": portfolio.id, "name": portfolio.name}}


@app.delete("/api/v1/portfolios/{portfolio_id}")
async def delete_portfolio(portfolio_id: str) -> dict[str, Any]:
    """Delete a portfolio and all its holdings."""
    database = get_db()
    database.delete_portfolio(portfolio_id)
    return {"deleted": portfolio_id}


# --- Account Endpoints ---


@app.get("/api/v1/accounts")
async def list_accounts() -> dict[str, Any]:
    """List all accounts."""
    database = get_db()
    accounts = database.get_accounts()
    return {"accounts": [{"id": a.id, "name": a.name, "type": a.account_type,
                          "broker": a.broker, "currency": a.currency} for a in accounts]}


@app.post("/api/v1/accounts", status_code=201)
async def create_account(req: AccountCreate) -> dict[str, Any]:
    """Create a new brokerage/investment account."""
    database = get_db()
    account = database.insert_account(Account(
        name=req.name, account_type=req.account_type,
        broker=req.broker, currency=req.currency,
    ))
    return {"account": {"id": account.id, "name": account.name}}


# --- Holdings Endpoints ---


@app.get("/api/v1/holdings")
async def list_holdings(
    portfolio_id: str | None = Query(None),
    account_id: str | None = Query(None),
) -> dict[str, Any]:
    """List holdings, optionally filtered by portfolio and/or account."""
    database = get_db()
    holdings = database.get_holdings(portfolio_id=portfolio_id, account_id=account_id)

    total_value = sum(h.market_value for h in holdings)
    result = []
    for h in holdings:
        weight = (h.market_value / total_value * 100) if total_value > 0 else 0.0
        try:
            returns = json.loads(h.returns_json)
        except (json.JSONDecodeError, TypeError):
            returns = []
        result.append({
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
        })

    return {
        "holdings": result,
        "total_market_value": round(total_value, 2),
        "count": len(result),
    }


@app.post("/api/v1/holdings", status_code=201)
async def create_holding(req: HoldingCreate) -> dict[str, Any]:
    """Add a new holding to a portfolio."""
    database = get_db()
    holding = database.insert_holding(Holding(
        portfolio_id=req.portfolio_id,
        account_id=req.account_id,
        symbol=req.symbol.upper(),
        name=req.name,
        asset_class=req.asset_class,
        quantity=req.quantity,
        market_value=req.market_value,
        cost_basis=req.cost_basis,
        yield_pct=req.yield_pct,
        currency=req.currency,
        returns_json=json.dumps(req.returns),
    ))
    return {"holding": {"id": holding.id, "symbol": holding.symbol}}


@app.put("/api/v1/holdings/{holding_id}")
async def update_holding(holding_id: str, req: HoldingUpdate) -> dict[str, Any]:
    """Update an existing holding."""
    database = get_db()
    existing = database.get_holding_by_id(holding_id)

    if req.symbol is not None:
        existing.symbol = req.symbol.upper()
    if req.name is not None:
        existing.name = req.name
    if req.asset_class is not None:
        existing.asset_class = req.asset_class
    if req.quantity is not None:
        existing.quantity = req.quantity
    if req.market_value is not None:
        existing.market_value = req.market_value
    if req.cost_basis is not None:
        existing.cost_basis = req.cost_basis
    if req.yield_pct is not None:
        existing.yield_pct = req.yield_pct
    if req.currency is not None:
        existing.currency = req.currency
    if req.returns is not None:
        existing.returns_json = json.dumps(req.returns)

    database.update_holding(existing)
    return {"updated": holding_id}


@app.delete("/api/v1/holdings/{holding_id}")
async def delete_holding(holding_id: str) -> dict[str, Any]:
    """Delete a holding."""
    database = get_db()
    database.delete_holding(holding_id)
    return {"deleted": holding_id}


# --- CSV Import ---


@app.post("/api/v1/import/csv")
async def import_csv(
    file: UploadFile = File(...),
    portfolio_id: str = Query(...),
    account_id: str = Query(""),
) -> dict[str, Any]:
    """Upload and parse a CSV file. Returns parsed holdings for review before saving.

    The user reviews the parsed data, then calls /api/v1/import/confirm to save.
    """
    if not file.filename or not file.filename.lower().endswith((".csv", ".tsv", ".txt")):
        raise HTTPException(status_code=400, detail="File must be .csv, .tsv, or .txt")

    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large (max 10MB)")

    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = content.decode("latin-1")
        except UnicodeDecodeError:
            raise HTTPException(
                status_code=400, detail="Cannot decode file (tried UTF-8 and Latin-1)"
            )

    result = import_csv_string(text, portfolio_id, account_id)

    return {
        "format_detected": result.format_detected,
        "rows_parsed": result.rows_parsed,
        "rows_skipped": result.rows_skipped,
        "holdings_count": len(result.holdings),
        "holdings": [
            {
                "symbol": h.symbol,
                "name": h.name,
                "asset_class": h.asset_class,
                "quantity": h.quantity,
                "market_value": h.market_value,
                "cost_basis": h.cost_basis,
                "yield_pct": h.yield_pct,
                "weight_pct": round(h.weight * 100, 2),
                "currency": h.currency,
            }
            for h in result.holdings
        ],
        "warnings": [
            {"row": w.row, "field": w.field, "message": w.message} for w in result.warnings
        ],
        "errors": [{"row": e.row, "message": e.message} for e in result.errors],
    }


@app.post("/api/v1/import/confirm")
async def confirm_import(
    file: UploadFile = File(...),
    portfolio_id: str = Query(...),
    account_id: str = Query(""),
) -> dict[str, Any]:
    """Parse and save CSV holdings to database in one step.

    Use /api/v1/import/csv first for preview, then this endpoint to save.
    """
    content = await file.read()
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        text = content.decode("latin-1")

    result = import_csv_string(text, portfolio_id, account_id)

    if result.errors:
        raise HTTPException(
            status_code=422,
            detail={
                "message": f"{len(result.errors)} errors found during import",
                "errors": [{"row": e.row, "message": e.message} for e in result.errors],
            },
        )

    database = get_db()
    count = database.bulk_insert_holdings(result.holdings)

    return {
        "imported": count,
        "portfolio_id": portfolio_id,
        "format_detected": result.format_detected,
    }


# --- Analytics Proxy (Rust Engine) ---


@app.post("/api/v1/analytics/risk")
async def compute_risk(portfolio_id: str = Query(...)) -> Any:
    """Compute risk metrics by sending holdings to the Rust engine."""
    database = get_db()
    holdings_data = database.get_holdings_for_engine(portfolio_id)

    if not holdings_data:
        raise HTTPException(status_code=404, detail="No holdings found for this portfolio")

    async with httpx.AsyncClient(timeout=30.0, trust_env=False) as client:
        try:
            resp = await client.post(
                f"{RUST_ENGINE_URL}/api/v1/portfolio/risk",
                json={"holdings": holdings_data},
            )
            resp.raise_for_status()
            return resp.json()
        except httpx.ConnectError:
            raise HTTPException(status_code=502, detail="Rust engine not reachable")
        except httpx.HTTPStatusError as e:
            raise HTTPException(status_code=e.response.status_code, detail=e.response.text)


@app.post("/api/v1/analytics/metrics")
async def compute_metrics(portfolio_id: str = Query(...)) -> Any:
    """Compute performance metrics via the Rust engine."""
    database = get_db()
    holdings_data = database.get_holdings_for_engine(portfolio_id)

    if not holdings_data:
        raise HTTPException(status_code=404, detail="No holdings found for this portfolio")

    async with httpx.AsyncClient(timeout=30.0, trust_env=False) as client:
        try:
            resp = await client.post(
                f"{RUST_ENGINE_URL}/api/v1/portfolio/metrics",
                json={"holdings": holdings_data},
            )
            resp.raise_for_status()
            return resp.json()
        except httpx.ConnectError:
            raise HTTPException(status_code=502, detail="Rust engine not reachable")
        except httpx.HTTPStatusError as e:
            raise HTTPException(status_code=e.response.status_code, detail=e.response.text)


# --- Python-Native Analytics ---


@app.post("/api/v1/analytics/carry")
async def compute_carry(req: CarryRequest) -> dict[str, Any]:
    """Compute margin carry analysis (Python-native, no Rust engine needed)."""
    database = get_db()
    holdings = database.get_holdings(portfolio_id=req.portfolio_id)

    if not holdings:
        raise HTTPException(status_code=404, detail="No holdings found")

    holdings_dicts = [
        {"symbol": h.symbol, "market_value": h.market_value, "yield_pct": h.yield_pct}
        for h in holdings
    ]

    result = analyze_carry(holdings_dicts, req.total_margin, req.margin_rate)

    return {
        "total_nav": result.total_nav,
        "total_long_value": result.total_long_value,
        "total_margin": result.total_margin,
        "leverage_ratio": result.leverage_ratio,
        "total_annual_income": result.total_annual_income,
        "total_annual_margin_cost": result.total_annual_margin_cost,
        "net_carry": result.net_carry,
        "income_coverage_ratio": result.income_coverage_ratio,
        "positions": [
            {
                "symbol": p.symbol,
                "market_value": p.market_value,
                "yield_pct": p.yield_pct,
                "annual_income": p.annual_income,
                "margin_cost": p.annual_margin_cost,
                "net_carry": p.net_carry,
                "carry_spread": p.carry_spread,
            }
            for p in result.positions
        ],
    }


@app.post("/api/v1/analytics/optimize")
async def optimize_portfolio_endpoint(req: OptimizeRequest) -> dict[str, Any]:
    """Long-only portfolio optimization (Python-native, no Rust engine needed).

    Computes maximum-Sharpe, minimum-variance, and risk-parity mixes plus
    the efficient frontier. Either pass ``portfolio_id`` (expected returns
    and covariance are derived from the holdings' stored return series)
    or pass ``symbols`` + ``expected_returns`` + ``covariance`` explicitly.

    Educational analytics only: expected risk/return trade-offs for the
    given inputs. The tool calculates; the user decides. No advice.
    """
    try:
        if req.portfolio_id:
            database = get_db()
            holdings = database.get_holdings(portfolio_id=req.portfolio_id)
            if not holdings:
                raise HTTPException(
                    status_code=404, detail="No holdings found"
                )
            symbols, mu, cov = holdings_to_inputs(
                [(h.symbol, h.returns_json) for h in holdings]
            )
        else:
            if (
                not req.symbols
                or not req.expected_returns
                or not req.covariance
            ):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Provide portfolio_id or symbols + expected_returns "
                        "+ covariance"
                    ),
                )
            symbols, mu, cov = (
                req.symbols,
                req.expected_returns,
                req.covariance,
            )
        result = optimize(
            symbols,
            mu,
            cov,
            risk_free_rate=req.risk_free_rate,
            frontier_points=req.frontier_points,
        )
    except OptimizeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    body = result.as_dict()
    body["disclosure"] = DISCLOSURE
    return body


@app.post("/api/v1/analytics/attribution")
async def compute_attribution(req: AttributionRequest) -> dict[str, Any]:
    """Brinson-Hood-Beebower attribution of portfolio vs. benchmark.

    Python-native. Decomposes the active return into allocation, selection,
    and interaction effects per segment. Educational analytics only —
    explains what drove the difference vs. the benchmark; no investment
    advice and no recommendations.
    """
    try:
        result = brinson_attribute(
            [
                {
                    "segment": s.segment,
                    "weight": s.weight,
                    "return": s.segment_return,
                }
                for s in req.portfolio_segments
            ],
            [
                {
                    "segment": s.segment,
                    "weight": s.weight,
                    "return": s.segment_return,
                }
                for s in req.benchmark_segments
            ],
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return {
        "disclosure": DISCLOSURE,
        "method": "Brinson-Hood-Beebower (arithmetic)",
        "portfolio_return": result.portfolio_return,
        "benchmark_return": result.benchmark_return,
        "active_return": result.active_return,
        "total_allocation": result.total_allocation,
        "total_selection": result.total_selection,
        "total_interaction": result.total_interaction,
        "segments": [
            {
                "segment": s.segment,
                "portfolio_weight": s.portfolio_weight,
                "benchmark_weight": s.benchmark_weight,
                "portfolio_return": s.portfolio_return,
                "benchmark_return": s.benchmark_return,
                "allocation_effect": s.allocation_effect,
                "selection_effect": s.selection_effect,
                "interaction_effect": s.interaction_effect,
                "active_contribution": s.active_contribution,
            }
            for s in result.segments
        ],
    }

# --- Return series, factor decomposition, Monte Carlo, stress (UI views) ---


def _portfolio_period_returns(portfolio_id: str) -> tuple[list[float], int]:
    """Build the weight-weighted portfolio return series from holdings.

    Uses each holding's stored returns series (returns_json), truncated to the
    longest common tail across holdings, weighted by current market-value
    weights. Returns (series, n_periods). Raises HTTPException on empty data.
    """
    database = get_db()
    holdings = database.get_holdings(portfolio_id=portfolio_id)
    if not holdings:
        raise HTTPException(status_code=404, detail="No holdings found")

    total_value = sum(h.market_value for h in holdings)
    if total_value <= 0:
        raise HTTPException(status_code=404, detail="Portfolio has no market value")

    series_list: list[list[float]] = []
    weights: list[float] = []
    for h in holdings:
        try:
            rets = json.loads(h.returns_json)
        except (json.JSONDecodeError, TypeError):
            rets = []
        clean = [float(r) for r in rets if isinstance(r, (int, float)) and r == r]
        if not clean:
            continue
        series_list.append(clean)
        weights.append(h.market_value / total_value)

    if not series_list:
        raise HTTPException(
            status_code=422, detail="Holdings have no usable return series"
        )

    n = min(len(s) for s in series_list)
    tails = [s[-n:] for s in series_list]
    wsum = sum(weights)
    portfolio = [
        sum(tails[i][t] * weights[i] for i in range(len(tails))) / wsum
        for t in range(n)
    ]
    return portfolio, n


def _cumulative_growth(period_returns: list[float]) -> list[float]:
    out: list[float] = []
    value = 1.0
    for r in period_returns:
        value *= 1.0 + r
        out.append(value)
    return out


def _drawdown_series(cumulative: list[float]) -> list[float]:
    out: list[float] = []
    peak = cumulative[0] if cumulative else 1.0
    for v in cumulative:
        if v > peak:
            peak = v
        out.append((v - peak) / peak if peak else 0.0)
    return out


@app.get("/api/v1/analytics/series")
async def portfolio_series(portfolio_id: str = Query(...)) -> dict[str, Any]:
    """Per-period portfolio returns plus cumulative growth and drawdown series.

    Python-native (no Rust engine needed). Powers the risk-view drawdown chart
    and the performance-vs-benchmark chart's portfolio leg.
    """
    portfolio, n = _portfolio_period_returns(portfolio_id)
    cumulative = _cumulative_growth(portfolio)
    return {
        "n_periods": n,
        "period_returns": portfolio,
        "cumulative": cumulative,
        "drawdown": _drawdown_series(cumulative),
        "note": (
            "Weight-weighted series from holdings' stored returns, "
            "truncated to the common tail."
        ),
    }


@app.post("/api/v1/analytics/factor")
async def factor_decomposition(portfolio_id: str = Query(...)) -> dict[str, Any]:
    """Fama-French 5-factor OLS decomposition of the portfolio return series.

    Python-native. Factor data comes from the Ken French data library via
    FactorAdapter (cached); the series are aligned to the overlapping tail.
    Also returns the market-factor (Mkt-RF + RF) cumulative series as the
    performance-chart benchmark leg.
    """
    import numpy as np

    from pae.data.factors import FactorAdapter, FactorDataError
    from pae.models.factor import FactorError, decompose

    portfolio, n = _portfolio_period_returns(portfolio_id)

    try:
        factors = FactorAdapter().get_ff5_factors()
    except FactorDataError as exc:
        raise HTTPException(
            status_code=502, detail=f"Factor data unavailable: {exc}"
        ) from exc

    factor_names = ["Mkt-RF", "SMB", "HML", "RMW", "CMA"]
    m = min(n, min(len(factors[name].returns) for name in factor_names))
    if m < 12:
        raise HTTPException(
            status_code=422,
            detail=f"Insufficient overlapping periods for OLS (have {m}, need >= 12)",
        )
    port = portfolio[-m:]
    factor_rets = {name: factors[name].returns[-m:] for name in factor_names}

    try:
        result = decompose(
            np.asarray(port, dtype=np.float64),
            {
                name: np.asarray(rets, dtype=np.float64)
                for name, rets in factor_rets.items()
            },
        )
    except (ValueError, FactorError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    rf = factors["RF"].returns[-m:]
    market_total = [a + b for a, b in zip(factor_rets["Mkt-RF"], rf)]

    return {
        "n_periods": m,
        "alpha": result.alpha,
        "alpha_t_stat": result.alpha_t_stat,
        "r_squared": result.r_squared,
        "residual_risk_pct": result.residual_risk_pct,
        "exposures": [
            {
                "factor_name": e.factor_name,
                "beta": e.beta,
                "t_stat": e.t_stat,
                "contribution_pct": e.contribution_pct,
            }
            for e in result.exposures
        ],
        "portfolio_cumulative": _cumulative_growth(port),
        "benchmark_cumulative": _cumulative_growth(market_total),
        "benchmark_name": "Fama-French market factor (Mkt-RF + RF)",
        "factor_source": "Ken French data library, Fama-French 5-factor monthly",
        "note": (
            "Series aligned to the overlapping tail of holding returns and "
            "monthly factor data; treat as educational, not precise attribution."
        ),
    }


async def _proxy_engine_post(path: str, payload: dict[str, Any]) -> Any:
    """POST to the Rust engine and return its JSON, mapping failures to HTTP."""
    async with httpx.AsyncClient(timeout=30.0, trust_env=False) as client:
        try:
            resp = await client.post(
                f"{RUST_ENGINE_URL}{path}", json=payload
            )
            resp.raise_for_status()
            return resp.json()
        except httpx.ConnectError as exc:
            raise HTTPException(
                status_code=502, detail="Rust engine not reachable"
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise HTTPException(
                status_code=exc.response.status_code, detail=exc.response.text
            ) from exc


@app.post("/api/v1/analytics/montecarlo")
async def run_montecarlo(
    portfolio_id: str = Query(...),
    num_simulations: int = Query(default=1000, ge=100, le=100000),
    time_horizon_months: int = Query(default=12, ge=1, le=120),
) -> Any:
    """Monte Carlo percentile fan via the Rust engine. Powers the scenario view."""
    database = get_db()
    holdings_data = database.get_holdings_for_engine(portfolio_id)
    if not holdings_data:
        raise HTTPException(status_code=404, detail="No holdings found for this portfolio")
    summary = database.get_portfolio_summary(portfolio_id)
    return await _proxy_engine_post(
        "/api/v1/portfolio/montecarlo",
        {
            "holdings": holdings_data,
            "num_simulations": num_simulations,
            "time_horizon_months": time_horizon_months,
            "initial_value": summary["total_market_value"],
        },
    )


@app.post("/api/v1/analytics/stress")
async def run_stress(
    portfolio_id: str = Query(...),
    scenario: str = Query(default="2008"),
) -> Any:
    """Stress-test scenario via the Rust engine. Powers the scenario view."""
    database = get_db()
    holdings_data = database.get_holdings_for_engine(portfolio_id)
    if not holdings_data:
        raise HTTPException(status_code=404, detail="No holdings found for this portfolio")
    return await _proxy_engine_post(
        "/api/v1/portfolio/stress",
        {"holdings": holdings_data, "scenario": scenario},
    )


# --- Portfolio Dashboard (Aggregated) ---


@app.get("/api/v1/dashboard/{portfolio_id}")
async def get_dashboard(portfolio_id: str) -> dict[str, Any]:
    """Get complete dashboard data for a portfolio.

    Single endpoint that the UI calls on load. Returns everything needed
    to populate the dashboard: summary, holdings, allocation breakdown.

    Delegates to PAETools.dashboard_summary — the same implementation the
    dashboard_summary agent tool uses (single source of truth).
    """
    tools = PAETools(get_db(), RUST_ENGINE_URL)
    result = await tools.dashboard_summary(portfolio_id)
    # REST shape keeps the historical envelope (no "ok" wrapper).
    result.pop("ok", None)
    return result


# --- Agent surfaces: A2A-compatible card + message endpoint ---
# Assumption (stated in the PR body): standalone ACP (i-am-bee/acp) is
# archived upstream ("ACP is now part of A2A under the Linux Foundation"),
# so PAE exposes a minimal A2A-compatible surface instead of ACP.

A2A_MESSAGE_ENDPOINT = "/api/v1/a2a/message/send"

AGENT_CARD: dict[str, Any] = {
    "name": "PAE",
    "description": (
        "PAE (Personal Analytics Engine) — educational investment analytics "
        "for individuals: risk metrics, factor exposure, Monte Carlo, "
        "stress tests, decision journal. Minimal A2A-compatible agent "
        "surface. " + DISCLOSURE
    ),
    "version": "0.1.0",
    "protocol": "A2A",
    "provider": {"organization": "AIMLDS", "url": "https://aimlds.org"},
    "url": A2A_MESSAGE_ENDPOINT,
    "message_endpoint": A2A_MESSAGE_ENDPOINT,
    "skills": [
        {
            "id": tool_name,
            "name": tool_name,
            "description": description,
            "tags": ["pae", "analytics", "education"],
        }
        for tool_name, description in TOOL_SPECS
    ],
}

PAE_DISCOVERY: dict[str, Any] = {
    "name": "PAE",
    "description": (
        "PAE (Personal Analytics Engine) — zero-knowledge, institutional-grade "
        "investment analytics for individuals."
    ),
    "disclosure": DISCLOSURE,
    "version": "0.1.0",
    "surfaces": {
        "rest": "/api/v1",
        "mcp": "stdio via `python -m pae.mcp` (analytics/pae/mcp)",
        "a2a": A2A_MESSAGE_ENDPOINT,
    },
    "links": {
        "llms_txt": "/llms.txt",
        "agent_card": "/.well-known/agent.json",
        "tools_manifest": "/api/v1/tools",
    },
}


@app.get("/.well-known/agent.json")
async def agent_card(_: None = Depends(api_key_or_none)) -> dict[str, Any]:
    """A2A Agent Card: identity, skills, and the message endpoint URL."""
    return AGENT_CARD


def _parse_a2a_tool_call(body: dict[str, Any]) -> tuple[str | None, dict[str, Any], str | None]:
    """Extract (tool_name, params, error) from an A2A-shaped message.

    Expected shape:
        {"message": {"role": "user",
                     "parts": [{"type": "data",
                                "data": {"tool": "<tool_id>", "params": {...}}}]}}.
    Returns (None, {}, error_message) when the shape is unusable.
    """
    message = body.get("message")
    if not isinstance(message, dict):
        return None, {}, "body.message must be an object"
    parts = message.get("parts")
    if not isinstance(parts, list) or not parts:
        return None, {}, "body.message.parts must be a non-empty list"
    for part in parts:
        if not isinstance(part, dict):
            continue
        if part.get("type") != "data":
            continue
        data = part.get("data")
        if not isinstance(data, dict):
            continue
        tool_name = data.get("tool")
        if not isinstance(tool_name, str) or not tool_name:
            continue
        params = data.get("params", {})
        if not isinstance(params, dict):
            return None, {}, "data.params must be an object"
        return tool_name, params, None
    return None, {}, "no data part with {tool, params} found in message.parts"


def _a2a_task_response(
    task_id: str, state: str, tool_name: str, outcome: dict[str, Any]
) -> dict[str, Any]:
    """Build the A2A task response envelope. Never carries a traceback."""
    response: dict[str, Any] = {
        "task": {
            "id": task_id,
            "status": {"state": state},
            "artifacts": [
                {
                    "name": tool_name,
                    "parts": [{"type": "data", "data": outcome}],
                }
            ],
        }
    }
    if state != "completed":
        response["task"]["status"]["message"] = outcome.get("error", "failed")
    return response


@app.post("/api/v1/a2a/message/send")
async def a2a_message_send(
    body: dict[str, Any], _: None = Depends(api_key_or_none)
) -> dict[str, Any]:
    """Minimal A2A-compatible message endpoint.

    Accepts {"message": {"role": "user", "parts": [{"type": "data",
    "data": {"tool": "<tool_id>", "params": {...}}}]}} and runs the named
    tool through the SAME tool layer as the MCP server
    (pae.mcp.run_agent_tool — shared implementation, not duplicated).

    Unknown tools and malformed messages return a failed task with an
    error artifact — never a traceback.
    """
    task_id = uuid.uuid4().hex
    tool_name, params, parse_error = _parse_a2a_tool_call(body)
    if parse_error is not None:
        return _a2a_task_response(
            task_id,
            "failed",
            tool_name or "unknown",
            {"ok": False, "error": parse_error},
        )
    assert tool_name is not None  # narrowed by parse_error being None
    outcome = await run_agent_tool(tool_name, params, get_db(), RUST_ENGINE_URL)
    state = "completed" if outcome.get("ok") else "failed"
    return _a2a_task_response(task_id, state, tool_name, outcome)


# --- Discovery & tool manifest ---


@app.get("/.well-known/pae.json")
async def pae_discovery(_: None = Depends(api_key_or_none)) -> dict[str, Any]:
    """PAE discovery document: surfaces, disclosure, and link relations."""
    return PAE_DISCOVERY


@app.get("/api/v1/tools")
async def tools_manifest(_: None = Depends(api_key_or_none)) -> dict[str, Any]:
    """Machine-readable tool manifest (single source of truth: MCP tools)."""
    return {"tools": await get_tool_manifest(), "count": len(TOOL_SPECS)}
