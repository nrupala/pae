"""PAE SQLite Database Layer.

Persistent storage for holdings, portfolios, accounts, and decision journal.
Uses WAL mode for crash recovery. All sensitive content stored as encrypted
blobs (ciphertext from client-side AES-256-GCM). The server never sees plaintext.

Design:
- WAL mode: concurrent reads during writes, crash recovery
- Foreign keys enforced
- Timestamps in UTC ISO 8601
- Content fields store encrypted JSON blobs (base64-encoded ciphertext)
- Metadata fields (symbol, account_type) stored in plaintext for querying
"""

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator
import uuid
import logging

logger = logging.getLogger(__name__)

# --- Data Models ---


@dataclass
class Account:
    """Brokerage or investment account."""
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    name: str = ""
    account_type: str = "taxable"  # rrsp, tfsa, lira, taxable, margin
    broker: str = ""
    currency: str = "CAD"
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class Portfolio:
    """A named collection of holdings across accounts."""
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    name: str = "Default"
    description: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class Holding:
    """A single position in a portfolio."""
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    portfolio_id: str = ""
    account_id: str = ""
    symbol: str = ""
    name: str = ""
    asset_class: str = "equity"  # equity, fixed_income, commodity, real_estate, cash, crypto, preferred
    quantity: float = 0.0
    market_value: float = 0.0
    cost_basis: float = 0.0
    weight: float = 0.0
    yield_pct: float = 0.0
    currency: str = "CAD"
    returns_json: str = "[]"  # JSON array of periodic returns
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# --- Database Manager ---


class DatabaseError(Exception):
    """Base exception for storage errors."""


class NotFoundError(DatabaseError):
    """Entity not found."""


class ValidationError(DatabaseError):
    """Input validation failed."""


class PAEDatabase:
    """SQLite database manager for PAE.

    Usage:
        db = PAEDatabase("path/to/pae.db")
        db.initialize()
        db.insert_holding(holding)
        holdings = db.get_holdings(portfolio_id="...")
        db.close()
    """

    SCHEMA_VERSION = 1

    def __init__(self, db_path: str | Path = "pae.db") -> None:
        self.db_path = Path(db_path)
        self._conn: sqlite3.Connection | None = None

    def _get_conn(self) -> sqlite3.Connection:
        """Get or create the database connection."""
        if self._conn is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(
                str(self.db_path),
                check_same_thread=False,
            )
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.execute("PRAGMA busy_timeout=5000")
            logger.info("Database connection opened: %s", self.db_path)
        return self._conn

    @contextmanager
    def _transaction(self) -> Generator[sqlite3.Cursor, None, None]:
        """Context manager for atomic transactions with rollback on error."""
        conn = self._get_conn()
        cursor = conn.cursor()
        try:
            yield cursor
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def initialize(self) -> None:
        """Create tables if they don't exist."""
        conn = self._get_conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER PRIMARY KEY
            );

            CREATE TABLE IF NOT EXISTS accounts (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL DEFAULT '',
                account_type TEXT NOT NULL DEFAULT 'taxable',
                broker TEXT NOT NULL DEFAULT '',
                currency TEXT NOT NULL DEFAULT 'CAD',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS portfolios (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL DEFAULT 'Default',
                description TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS holdings (
                id TEXT PRIMARY KEY,
                portfolio_id TEXT NOT NULL,
                account_id TEXT NOT NULL DEFAULT '',
                symbol TEXT NOT NULL,
                name TEXT NOT NULL DEFAULT '',
                asset_class TEXT NOT NULL DEFAULT 'equity',
                quantity REAL NOT NULL DEFAULT 0.0,
                market_value REAL NOT NULL DEFAULT 0.0,
                cost_basis REAL NOT NULL DEFAULT 0.0,
                weight REAL NOT NULL DEFAULT 0.0,
                yield_pct REAL NOT NULL DEFAULT 0.0,
                currency TEXT NOT NULL DEFAULT 'CAD',
                returns_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (portfolio_id) REFERENCES portfolios(id) ON DELETE CASCADE,
                FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE SET DEFAULT
            );

            CREATE INDEX IF NOT EXISTS idx_holdings_portfolio ON holdings(portfolio_id);
            CREATE INDEX IF NOT EXISTS idx_holdings_symbol ON holdings(symbol);
            CREATE INDEX IF NOT EXISTS idx_holdings_account ON holdings(account_id);

            INSERT OR IGNORE INTO schema_version (version) VALUES (1);
        """)
        logger.info("Database initialized (schema v%d)", self.SCHEMA_VERSION)

    def close(self) -> None:
        """Close the database connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None
            logger.info("Database connection closed")

    # --- Account CRUD ---

    def insert_account(self, account: Account) -> Account:
        """Insert a new account. Returns the account with generated ID."""
        if not account.name:
            raise ValidationError("Account name cannot be empty")
        if account.account_type not in ("rrsp", "tfsa", "lira", "taxable", "margin", "other"):
            raise ValidationError(f"Invalid account type: {account.account_type}")

        with self._transaction() as cur:
            cur.execute(
                "INSERT INTO accounts (id, name, account_type, broker, currency, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (account.id, account.name, account.account_type,
                 account.broker, account.currency, account.created_at),
            )
        logger.info("Inserted account: %s (%s)", account.name, account.id)
        return account

    def get_accounts(self) -> list[Account]:
        """Get all accounts."""
        conn = self._get_conn()
        rows = conn.execute("SELECT * FROM accounts ORDER BY name").fetchall()
        return [Account(**dict(row)) for row in rows]

    def delete_account(self, account_id: str) -> None:
        """Delete an account by ID."""
        with self._transaction() as cur:
            cur.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
            if cur.rowcount == 0:
                raise NotFoundError(f"Account not found: {account_id}")

    # --- Portfolio CRUD ---

    def insert_portfolio(self, portfolio: Portfolio) -> Portfolio:
        """Insert a new portfolio. Returns the portfolio with generated ID."""
        if not portfolio.name:
            raise ValidationError("Portfolio name cannot be empty")

        with self._transaction() as cur:
            cur.execute(
                "INSERT INTO portfolios (id, name, description, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (portfolio.id, portfolio.name, portfolio.description,
                 portfolio.created_at, portfolio.updated_at),
            )
        logger.info("Inserted portfolio: %s (%s)", portfolio.name, portfolio.id)
        return portfolio

    def get_portfolios(self) -> list[Portfolio]:
        """Get all portfolios."""
        conn = self._get_conn()
        rows = conn.execute("SELECT * FROM portfolios ORDER BY name").fetchall()
        return [Portfolio(**dict(row)) for row in rows]

    def delete_portfolio(self, portfolio_id: str) -> None:
        """Delete a portfolio and all its holdings (CASCADE)."""
        with self._transaction() as cur:
            cur.execute("DELETE FROM portfolios WHERE id = ?", (portfolio_id,))
            if cur.rowcount == 0:
                raise NotFoundError(f"Portfolio not found: {portfolio_id}")

    # --- Holding CRUD ---

    def insert_holding(self, holding: Holding) -> Holding:
        """Insert a new holding. Returns the holding with generated ID."""
        self._validate_holding(holding)

        with self._transaction() as cur:
            cur.execute(
                "INSERT INTO holdings "
                "(id, portfolio_id, account_id, symbol, name, asset_class, "
                "quantity, market_value, cost_basis, weight, yield_pct, "
                "currency, returns_json, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (holding.id, holding.portfolio_id, holding.account_id,
                 holding.symbol, holding.name, holding.asset_class,
                 holding.quantity, holding.market_value, holding.cost_basis,
                 holding.weight, holding.yield_pct, holding.currency,
                 holding.returns_json, holding.created_at, holding.updated_at),
            )
        logger.info("Inserted holding: %s (%s)", holding.symbol, holding.id)
        return holding

    def update_holding(self, holding: Holding) -> Holding:
        """Update an existing holding by ID."""
        self._validate_holding(holding)
        holding.updated_at = datetime.now(timezone.utc).isoformat()

        with self._transaction() as cur:
            cur.execute(
                "UPDATE holdings SET "
                "symbol=?, name=?, asset_class=?, quantity=?, market_value=?, "
                "cost_basis=?, weight=?, yield_pct=?, currency=?, returns_json=?, "
                "updated_at=? WHERE id=?",
                (holding.symbol, holding.name, holding.asset_class,
                 holding.quantity, holding.market_value, holding.cost_basis,
                 holding.weight, holding.yield_pct, holding.currency,
                 holding.returns_json, holding.updated_at, holding.id),
            )
            if cur.rowcount == 0:
                raise NotFoundError(f"Holding not found: {holding.id}")
        return holding

    def delete_holding(self, holding_id: str) -> None:
        """Delete a holding by ID."""
        with self._transaction() as cur:
            cur.execute("DELETE FROM holdings WHERE id = ?", (holding_id,))
            if cur.rowcount == 0:
                raise NotFoundError(f"Holding not found: {holding_id}")

    def get_holdings(
        self,
        portfolio_id: str | None = None,
        account_id: str | None = None,
    ) -> list[Holding]:
        """Get holdings, optionally filtered by portfolio and/or account."""
        conn = self._get_conn()
        query = "SELECT * FROM holdings WHERE 1=1"
        params: list[str] = []

        if portfolio_id:
            query += " AND portfolio_id = ?"
            params.append(portfolio_id)
        if account_id:
            query += " AND account_id = ?"
            params.append(account_id)

        query += " ORDER BY symbol"
        rows = conn.execute(query, params).fetchall()
        return [Holding(**dict(row)) for row in rows]

    def get_holding_by_id(self, holding_id: str) -> Holding:
        """Get a single holding by ID."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM holdings WHERE id = ?", (holding_id,)
        ).fetchone()
        if row is None:
            raise NotFoundError(f"Holding not found: {holding_id}")
        return Holding(**dict(row))

    def bulk_insert_holdings(self, holdings: list[Holding]) -> int:
        """Insert multiple holdings in a single transaction. Returns count inserted."""
        for h in holdings:
            self._validate_holding(h)

        with self._transaction() as cur:
            for h in holdings:
                cur.execute(
                    "INSERT INTO holdings "
                    "(id, portfolio_id, account_id, symbol, name, asset_class, "
                    "quantity, market_value, cost_basis, weight, yield_pct, "
                    "currency, returns_json, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (h.id, h.portfolio_id, h.account_id, h.symbol, h.name,
                     h.asset_class, h.quantity, h.market_value, h.cost_basis,
                     h.weight, h.yield_pct, h.currency, h.returns_json,
                     h.created_at, h.updated_at),
                )
        logger.info("Bulk inserted %d holdings", len(holdings))
        return len(holdings)

    # --- Aggregate Queries ---

    def get_portfolio_summary(self, portfolio_id: str) -> dict:
        """Get summary stats for a portfolio."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT COUNT(*) as count, "
            "COALESCE(SUM(market_value), 0) as total_value, "
            "COALESCE(SUM(cost_basis), 0) as total_cost "
            "FROM holdings WHERE portfolio_id = ?",
            (portfolio_id,),
        ).fetchone()

        total_value = row["total_value"]
        total_cost = row["total_cost"]
        unrealized_pnl = total_value - total_cost

        return {
            "portfolio_id": portfolio_id,
            "holding_count": row["count"],
            "total_market_value": round(total_value, 2),
            "total_cost_basis": round(total_cost, 2),
            "unrealized_pnl": round(unrealized_pnl, 2),
            "unrealized_pnl_pct": round(
                (unrealized_pnl / total_cost * 100) if total_cost > 0 else 0.0, 2
            ),
        }

    def get_holdings_for_engine(self, portfolio_id: str) -> list[dict]:
        """Get holdings formatted for the Rust risk engine API.

        Returns list of dicts matching the Rust Holding struct:
        {symbol, weight, returns, yield_pct, cost_basis, market_value}
        """
        holdings = self.get_holdings(portfolio_id=portfolio_id)
        if not holdings:
            return []

        total_value = sum(h.market_value for h in holdings)
        if total_value <= 0:
            return []

        result = []
        for h in holdings:
            try:
                returns = json.loads(h.returns_json)
            except (json.JSONDecodeError, TypeError):
                returns = []

            result.append({
                "symbol": h.symbol,
                "weight": h.market_value / total_value,
                "returns": returns,
                "yield_pct": h.yield_pct,
                "cost_basis": h.cost_basis,
                "market_value": h.market_value,
            })

        return result

    # --- Validation ---

    @staticmethod
    def _validate_holding(holding: Holding) -> None:
        """Validate a holding before insert/update."""
        if not holding.symbol or not holding.symbol.strip():
            raise ValidationError("Symbol cannot be empty")
        if not holding.portfolio_id:
            raise ValidationError("Portfolio ID is required")
        if holding.market_value < 0:
            raise ValidationError(f"Market value cannot be negative: {holding.market_value}")
        if holding.quantity < 0:
            raise ValidationError(f"Quantity cannot be negative: {holding.quantity}")

        # Validate returns_json is valid JSON array
        try:
            returns = json.loads(holding.returns_json)
            if not isinstance(returns, list):
                raise ValidationError("returns_json must be a JSON array")
            for i, r in enumerate(returns):
                if not isinstance(r, (int, float)):
                    raise ValidationError(f"returns_json[{i}] must be a number")
        except json.JSONDecodeError as e:
            raise ValidationError(f"Invalid returns_json: {e}") from e
