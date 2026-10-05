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
import logging
import sqlite3
import uuid
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pae.decision.journal import DecisionEntry

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
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


@dataclass
class Portfolio:
    """A named collection of holdings across accounts."""
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    name: str = "Default"
    description: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


@dataclass
class Holding:
    """A single position in a portfolio."""
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    portfolio_id: str = ""
    account_id: str = ""
    symbol: str = ""
    name: str = ""
    # equity, fixed_income, commodity, real_estate, cash, crypto, preferred
    asset_class: str = "equity"
    quantity: float = 0.0
    market_value: float = 0.0
    cost_basis: float = 0.0
    weight: float = 0.0
    yield_pct: float = 0.0
    currency: str = "CAD"
    returns_json: str = "[]"  # JSON array of periodic returns
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


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

    SCHEMA_VERSION = 2

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
                account_id TEXT,
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
                FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE SET NULL
            );

            CREATE INDEX IF NOT EXISTS idx_holdings_portfolio ON holdings(portfolio_id);
            CREATE INDEX IF NOT EXISTS idx_holdings_symbol ON holdings(symbol);
            CREATE INDEX IF NOT EXISTS idx_holdings_account ON holdings(account_id);

            CREATE TABLE IF NOT EXISTS journal_entries (
                entry_id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL,
                action TEXT NOT NULL DEFAULT '',
                symbols_affected TEXT NOT NULL DEFAULT '[]',
                rationale TEXT NOT NULL DEFAULT '',
                alternatives_considered TEXT NOT NULL DEFAULT '[]',
                thesis TEXT NOT NULL DEFAULT '',
                confidence INTEGER NOT NULL DEFAULT 5,
                time_horizon TEXT NOT NULL DEFAULT '',
                what_could_go_wrong TEXT NOT NULL DEFAULT '',
                max_acceptable_loss_pct REAL NOT NULL DEFAULT 0.0,
                emotional_state TEXT NOT NULL DEFAULT 'neutral',
                market_context TEXT NOT NULL DEFAULT '',
                trigger TEXT NOT NULL DEFAULT '',
                outcome_30d REAL,
                outcome_90d REAL,
                outcome_180d REAL,
                outcome_notes TEXT NOT NULL DEFAULT '',
                was_thesis_correct INTEGER
            );

            CREATE INDEX IF NOT EXISTS idx_journal_timestamp ON journal_entries(timestamp);

        """)
        self._apply_migrations(conn)
        logger.info("Database initialized (schema v%d)", self.SCHEMA_VERSION)

    def _apply_migrations(self, conn: sqlite3.Connection) -> None:
        """Stamp fresh DBs at the latest schema version; migrate older ones in place."""
        row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
        current = int(row[0]) if row and row[0] is not None else 0
        if current >= self.SCHEMA_VERSION:
            return
        if 0 < current < 2:
            self._migrate_v2_account_nullable(conn)
        conn.execute(
            "INSERT OR IGNORE INTO schema_version (version) VALUES (?)",
            (self.SCHEMA_VERSION,),
        )
        conn.commit()

    @staticmethod
    def _migrate_v2_account_nullable(conn: sqlite3.Connection) -> None:
        """v1 -> v2: holdings.account_id becomes nullable (FK ON DELETE SET NULL); '' -> NULL."""
        conn.execute("PRAGMA foreign_keys=OFF")
        try:
            conn.executescript(
                """
                BEGIN;
                CREATE TABLE holdings_v2 (
                    id TEXT PRIMARY KEY,
                    portfolio_id TEXT NOT NULL,
                    account_id TEXT,
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
                    FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE SET NULL
                );
                INSERT INTO holdings_v2
                    SELECT id, portfolio_id, NULLIF(account_id, ''), symbol, name, asset_class,
                           quantity, market_value, cost_basis, weight, yield_pct, currency,
                           returns_json, created_at, updated_at
                    FROM holdings;
                DROP TABLE holdings;
                ALTER TABLE holdings_v2 RENAME TO holdings;
                CREATE INDEX IF NOT EXISTS idx_holdings_portfolio ON holdings(portfolio_id);
                CREATE INDEX IF NOT EXISTS idx_holdings_symbol ON holdings(symbol);
                CREATE INDEX IF NOT EXISTS idx_holdings_account ON holdings(account_id);
                COMMIT;
                """
            )
        finally:
            conn.execute("PRAGMA foreign_keys=ON")
        logger.info("Migrated holdings schema to v2 (nullable account_id)")

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
                (holding.id, holding.portfolio_id, holding.account_id or None,
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
        holding.updated_at = datetime.now(UTC).isoformat()

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
        return [Holding(**{**dict(row), "account_id": row["account_id"] or ""}) for row in rows]

    def get_holding_by_id(self, holding_id: str) -> Holding:
        """Get a single holding by ID."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM holdings WHERE id = ?", (holding_id,)
        ).fetchone()
        if row is None:
            raise NotFoundError(f"Holding not found: {holding_id}")
        return Holding(**{**dict(row), "account_id": row["account_id"] or ""})

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
                    (h.id, h.portfolio_id, h.account_id or None, h.symbol, h.name,
                     h.asset_class, h.quantity, h.market_value, h.cost_basis,
                     h.weight, h.yield_pct, h.currency, h.returns_json,
                     h.created_at, h.updated_at),
                )
        logger.info("Bulk inserted %d holdings", len(holdings))
        return len(holdings)

    # --- Aggregate Queries ---

    def get_portfolio_summary(self, portfolio_id: str) -> dict[str, Any]:
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

    def get_holdings_for_engine(self, portfolio_id: str) -> list[dict[str, Any]]:
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

    # --- Decision Journal ---

    def insert_journal_entry(self, entry: DecisionEntry) -> DecisionEntry:
        """Persist a decision journal entry. Returns the entry."""
        with self._transaction() as cur:
            cur.execute(
                "INSERT INTO journal_entries "
                "(entry_id, timestamp, action, symbols_affected, rationale, "
                "alternatives_considered, thesis, confidence, time_horizon, "
                "what_could_go_wrong, max_acceptable_loss_pct, emotional_state, "
                "market_context, trigger, outcome_30d, outcome_90d, outcome_180d, "
                "outcome_notes, was_thesis_correct) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    entry.entry_id,
                    entry.timestamp,
                    entry.action,
                    json.dumps(entry.symbols_affected),
                    entry.rationale,
                    json.dumps(entry.alternatives_considered),
                    entry.thesis,
                    entry.confidence,
                    entry.time_horizon,
                    entry.what_could_go_wrong,
                    entry.max_acceptable_loss_pct,
                    entry.emotional_state,
                    entry.market_context,
                    entry.trigger,
                    entry.outcome_30d,
                    entry.outcome_90d,
                    entry.outcome_180d,
                    entry.outcome_notes,
                    (
                        None
                        if entry.was_thesis_correct is None
                        else int(entry.was_thesis_correct)
                    ),
                ),
            )
        logger.info("Inserted journal entry: %s", entry.entry_id)
        return entry

    def get_journal_entry(self, entry_id: str) -> DecisionEntry:
        """Get a single journal entry by ID."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM journal_entries WHERE entry_id = ?", (entry_id,)
        ).fetchone()
        if row is None:
            raise NotFoundError(f"Journal entry not found: {entry_id}")
        return self._row_to_journal_entry(row)

    def get_journal_entries(self, limit: int = 100) -> list[DecisionEntry]:
        """Get journal entries, newest first."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM journal_entries ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._row_to_journal_entry(row) for row in rows]

    @staticmethod
    def _row_to_journal_entry(row: sqlite3.Row) -> DecisionEntry:
        """Convert a DB row to a DecisionEntry."""
        data = dict(row)
        try:
            symbols = json.loads(data.get("symbols_affected") or "[]")
        except json.JSONDecodeError:
            symbols = []
        try:
            alternatives = json.loads(data.get("alternatives_considered") or "[]")
        except json.JSONDecodeError:
            alternatives = []
        thesis_flag = data.get("was_thesis_correct")
        return DecisionEntry(
            entry_id=data["entry_id"],
            timestamp=data["timestamp"],
            action=data.get("action") or "",
            symbols_affected=symbols if isinstance(symbols, list) else [],
            rationale=data.get("rationale") or "",
            alternatives_considered=alternatives if isinstance(alternatives, list) else [],
            thesis=data.get("thesis") or "",
            confidence=int(data["confidence"]),
            time_horizon=data.get("time_horizon") or "",
            what_could_go_wrong=data.get("what_could_go_wrong") or "",
            max_acceptable_loss_pct=float(data["max_acceptable_loss_pct"]),
            emotional_state=data.get("emotional_state") or "neutral",
            market_context=data.get("market_context") or "",
            trigger=data.get("trigger") or "",
            outcome_30d=data.get("outcome_30d"),
            outcome_90d=data.get("outcome_90d"),
            outcome_180d=data.get("outcome_180d"),
            outcome_notes=data.get("outcome_notes") or "",
            was_thesis_correct=None if thesis_flag is None else bool(thesis_flag),
        )

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
