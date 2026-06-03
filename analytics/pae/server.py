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
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from pae.models.carry import analyze_carry
from pae.models.factor import decompose
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
async def lifespan(application: FastAPI):  # noqa: ARG001
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
    return JSONResponse(status_code=500, content={"error": "Internal database error", "code": "DB_ERROR"})


# --- Health ---


@app.get("/health")
async def health():
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
async def list_portfolios():
    """List all portfolios with summary stats."""
    database = get_db()
    portfolios = database.get_portfolios()
    result = []
    for p in portfolios:
        summary = database.get_portfolio_summary(p.id)
        result.append({**summary, "name": p.name, "description": p.description, "id": p.id})
    return {"portfolios": result}


@app.post("/api/v1/portfolios", status_code=201)
async def create_portfolio(req: PortfolioCreate):
    """Create a new portfolio."""
    database = get_db()
    portfolio = database.insert_portfolio(Portfolio(name=req.name, description=req.description))
    return {"portfolio": {"id": portfolio.id, "name": portfolio.name}}


@app.delete("/api/v1/portfolios/{portfolio_id}")
async def delete_portfolio(portfolio_id: str):
    """Delete a portfolio and all its holdings."""
    database = get_db()
    database.delete_portfolio(portfolio_id)
    return {"deleted": portfolio_id}


# --- Account Endpoints ---


@app.get("/api/v1/accounts")
async def list_accounts():
    """List all accounts."""
    database = get_db()
    accounts = database.get_accounts()
    return {"accounts": [{"id": a.id, "name": a.name, "type": a.account_type,
                          "broker": a.broker, "currency": a.currency} for a in accounts]}


@app.post("/api/v1/accounts", status_code=201)
async def create_account(req: AccountCreate):
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
):
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
async def create_holding(req: HoldingCreate):
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
async def update_holding(holding_id: str, req: HoldingUpdate):
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
async def delete_holding(holding_id: str):
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
):
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
            raise HTTPException(status_code=400, detail="Cannot decode file (tried UTF-8 and Latin-1)")

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
        "warnings": [{"row": w.row, "field": w.field, "message": w.message} for w in result.warnings],
        "errors": [{"row": e.row, "message": e.message} for e in result.errors],
    }


@app.post("/api/v1/import/confirm")
async def confirm_import(
    file: UploadFile = File(...),
    portfolio_id: str = Query(...),
    account_id: str = Query(""),
):
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
async def compute_risk(portfolio_id: str = Query(...)):
    """Compute risk metrics by sending holdings to the Rust engine."""
    database = get_db()
    holdings_data = database.get_holdings_for_engine(portfolio_id)

    if not holdings_data:
        raise HTTPException(status_code=404, detail="No holdings found for this portfolio")

    async with httpx.AsyncClient(timeout=30.0) as client:
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
async def compute_metrics(portfolio_id: str = Query(...)):
    """Compute performance metrics via the Rust engine."""
    database = get_db()
    holdings_data = database.get_holdings_for_engine(portfolio_id)

    if not holdings_data:
        raise HTTPException(status_code=404, detail="No holdings found for this portfolio")

    async with httpx.AsyncClient(timeout=30.0) as client:
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
async def compute_carry(req: CarryRequest):
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


# --- Portfolio Dashboard (Aggregated) ---


@app.get("/api/v1/dashboard/{portfolio_id}")
async def get_dashboard(portfolio_id: str):
    """Get complete dashboard data for a portfolio.

    Single endpoint that the UI calls on load. Returns everything needed
    to populate the dashboard: summary, holdings, allocation breakdown.
    """
    database = get_db()
    summary = database.get_portfolio_summary(portfolio_id)
    holdings = database.get_holdings(portfolio_id=portfolio_id)

    total_value = summary["total_market_value"]

    # Allocation by asset class
    allocation: dict[str, float] = {}
    for h in holdings:
        allocation[h.asset_class] = allocation.get(h.asset_class, 0.0) + h.market_value

    allocation_pct = {
        k: round(v / total_value * 100, 2) if total_value > 0 else 0.0
        for k, v in allocation.items()
    }

    # Top holdings
    top_holdings = sorted(holdings, key=lambda h: h.market_value, reverse=True)[:10]

    return {
        "summary": summary,
        "allocation": allocation_pct,
        "top_holdings": [
            {
                "symbol": h.symbol,
                "name": h.name,
                "market_value": round(h.market_value, 2),
                "weight_pct": round(h.market_value / total_value * 100, 2) if total_value > 0 else 0.0,
                "yield_pct": h.yield_pct,
                "unrealized_pnl": round(h.market_value - h.cost_basis, 2),
            }
            for h in top_holdings
        ],
        "holding_count": len(holdings),
    }
