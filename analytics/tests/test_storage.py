"""Tests for SQLite storage layer."""

import json
import tempfile
from pathlib import Path

import pytest

from pae.storage.db import (
    Account,
    Holding,
    NotFoundError,
    PAEDatabase,
    Portfolio,
    ValidationError,
)


@pytest.fixture
def db():
    """Create a temporary database for each test."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    database = PAEDatabase(db_path)
    database.initialize()
    yield database
    database.close()
    Path(db_path).unlink(missing_ok=True)


@pytest.fixture
def portfolio(db):
    """Create a default portfolio."""
    return db.insert_portfolio(Portfolio(name="Test Portfolio"))


@pytest.fixture
def account(db):
    """Create a default account."""
    return db.insert_account(Account(name="IBKR Margin", account_type="margin", broker="IBKR"))


# --- Portfolio Tests ---


def test_create_portfolio(db):
    p = db.insert_portfolio(Portfolio(name="My Portfolio"))
    assert p.id
    assert p.name == "My Portfolio"

    portfolios = db.get_portfolios()
    assert len(portfolios) == 1
    assert portfolios[0].name == "My Portfolio"


def test_create_portfolio_empty_name(db):
    with pytest.raises(ValidationError):
        db.insert_portfolio(Portfolio(name=""))


def test_delete_portfolio(db):
    p = db.insert_portfolio(Portfolio(name="To Delete"))
    db.delete_portfolio(p.id)
    assert len(db.get_portfolios()) == 0


def test_delete_portfolio_not_found(db):
    with pytest.raises(NotFoundError):
        db.delete_portfolio("nonexistent")


# --- Account Tests ---


def test_create_account(db):
    a = db.insert_account(Account(name="TFSA", account_type="tfsa", broker="Questrade"))
    assert a.id
    assert a.account_type == "tfsa"

    accounts = db.get_accounts()
    assert len(accounts) == 1


def test_create_account_invalid_type(db):
    with pytest.raises(ValidationError):
        db.insert_account(Account(name="Bad", account_type="invalid"))


def test_delete_account(db):
    a = db.insert_account(Account(name="To Delete", account_type="taxable"))
    db.delete_account(a.id)
    assert len(db.get_accounts()) == 0


# --- Holding Tests ---


def test_insert_holding(db, portfolio):
    h = db.insert_holding(Holding(
        portfolio_id=portfolio.id,
        symbol="FDS",
        market_value=18000.0,
        cost_basis=12000.0,
        yield_pct=0.9,
    ))
    assert h.id
    assert h.symbol == "FDS"

    holdings = db.get_holdings(portfolio_id=portfolio.id)
    assert len(holdings) == 1
    assert holdings[0].symbol == "FDS"
    assert holdings[0].market_value == 18000.0


def test_insert_holding_empty_symbol(db, portfolio):
    with pytest.raises(ValidationError, match="Symbol cannot be empty"):
        db.insert_holding(Holding(portfolio_id=portfolio.id, symbol=""))


def test_insert_holding_negative_value(db, portfolio):
    with pytest.raises(ValidationError, match="Market value cannot be negative"):
        db.insert_holding(Holding(portfolio_id=portfolio.id, symbol="BAD", market_value=-100))


def test_insert_holding_invalid_returns(db, portfolio):
    with pytest.raises(ValidationError, match="Invalid returns_json"):
        db.insert_holding(Holding(portfolio_id=portfolio.id, symbol="BAD", returns_json="not json"))


def test_update_holding(db, portfolio):
    h = db.insert_holding(Holding(
        portfolio_id=portfolio.id, symbol="FDS", market_value=18000.0,
    ))
    h.market_value = 20000.0
    updated = db.update_holding(h)
    assert updated.market_value == 20000.0

    fetched = db.get_holding_by_id(h.id)
    assert fetched.market_value == 20000.0


def test_delete_holding(db, portfolio):
    h = db.insert_holding(Holding(portfolio_id=portfolio.id, symbol="FDS", market_value=18000.0))
    db.delete_holding(h.id)
    assert len(db.get_holdings(portfolio_id=portfolio.id)) == 0


def test_delete_holding_not_found(db):
    with pytest.raises(NotFoundError):
        db.delete_holding("nonexistent")


def test_bulk_insert(db, portfolio):
    holdings = [
        Holding(portfolio_id=portfolio.id, symbol=f"SYM{i}", market_value=1000.0 * i)
        for i in range(1, 6)
    ]
    count = db.bulk_insert_holdings(holdings)
    assert count == 5
    assert len(db.get_holdings(portfolio_id=portfolio.id)) == 5


def test_get_holdings_by_account(db, portfolio, account):
    db.insert_holding(Holding(
        portfolio_id=portfolio.id, account_id=account.id,
        symbol="FDS", market_value=18000.0,
    ))
    db.insert_holding(Holding(
        portfolio_id=portfolio.id, account_id="",
        symbol="SPY", market_value=5000.0,
    ))

    ibkr_holdings = db.get_holdings(account_id=account.id)
    assert len(ibkr_holdings) == 1
    assert ibkr_holdings[0].symbol == "FDS"


# --- Aggregate Tests ---


def test_portfolio_summary(db, portfolio):
    db.insert_holding(Holding(
        portfolio_id=portfolio.id, symbol="FDS",
        market_value=18000.0, cost_basis=12000.0,
    ))
    db.insert_holding(Holding(
        portfolio_id=portfolio.id, symbol="GSBD",
        market_value=15000.0, cost_basis=14000.0,
    ))

    summary = db.get_portfolio_summary(portfolio.id)
    assert summary["holding_count"] == 2
    assert summary["total_market_value"] == 33000.0
    assert summary["total_cost_basis"] == 26000.0
    assert summary["unrealized_pnl"] == 7000.0


def test_portfolio_summary_empty(db, portfolio):
    summary = db.get_portfolio_summary(portfolio.id)
    assert summary["holding_count"] == 0
    assert summary["total_market_value"] == 0.0


def test_get_holdings_for_engine(db, portfolio):
    db.insert_holding(Holding(
        portfolio_id=portfolio.id, symbol="FDS",
        market_value=18000.0, returns_json=json.dumps([0.01, 0.02, -0.01]),
    ))
    db.insert_holding(Holding(
        portfolio_id=portfolio.id, symbol="GSBD",
        market_value=15000.0, returns_json=json.dumps([0.03, -0.02, 0.01]),
    ))

    engine_data = db.get_holdings_for_engine(portfolio.id)
    assert len(engine_data) == 2
    assert engine_data[0]["symbol"] in ("FDS", "GSBD")
    assert "weight" in engine_data[0]
    assert "returns" in engine_data[0]
    assert abs(sum(h["weight"] for h in engine_data) - 1.0) < 0.001
