//! Encrypted SQLite persistence layer for PAE.
//!
//! Holdings, portfolios, and accounts are persisted to a local SQLite
//! database so portfolio state survives between sessions. Consistent with
//! PAE's zero-knowledge model, every sensitive field (returns series,
//! cost basis, market value, names) is stored as a **client-side
//! AES-256-GCM ciphertext blob** plus its nonce. The engine never sees
//! plaintext and never holds the user's key.
//!
//! Durability is provided by SQLite's WAL (write-ahead log) journal mode,
//! which gives crash recovery and lets readers proceed concurrently with a
//! single writer. A small fixed-size connection pool serves concurrent
//! Axum handlers.

use std::path::{Path, PathBuf};
use std::sync::Mutex;

use chrono::Utc;
use rusqlite::{Connection, OpenFlags};
use thiserror::Error;
use uuid::Uuid;

/// Default number of pooled connections.
const DEFAULT_POOL_SIZE: usize = 4;

/// Busy-wait timeout (ms) before SQLite returns SQLITE_BUSY.
const BUSY_TIMEOUT_MS: u32 = 5_000;

/// Errors that can occur in the storage layer.
///
/// Mirrors the `thiserror`-based pattern used by `CryptoError`
/// (`crypto/vault.rs`) and `VersionStoreError` (`versioning/store.rs`).
#[derive(Debug, Error)]
pub enum StorageError {
    /// The underlying SQLite driver returned an error.
    #[error("SQLite error: {0}")]
    Sqlite(String),

    /// A connection could not be opened or the on-disk path is invalid.
    #[error("Failed to open database at '{path}': {source_msg}")]
    OpenFailed { path: String, source_msg: String },

    /// The connection pool lock was poisoned (a thread panicked while holding it).
    #[error("Connection pool lock poisoned")]
    PoolPoisoned,

    /// The connection pool was momentarily exhausted.
    #[error("No available connection in pool")]
    PoolExhausted,

    /// A requested entity (holding/portfolio/account) does not exist.
    #[error("{entity} not found: {id}")]
    NotFound { entity: &'static str, id: String },

    /// A field failed validation before reaching the database.
    #[error("Validation failed: {0}")]
    Validation(String),

    /// An account type string did not match a known variant.
    #[error("Invalid account type: '{0}'")]
    InvalidAccountType(String),
}

impl From<rusqlite::Error> for StorageError {
    fn from(e: rusqlite::Error) -> Self {
        StorageError::Sqlite(e.to_string())
    }
}

/// Registered account types. Modeled as an enum so illegal account
/// types are unrepresentable once parsed.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AccountType {
    /// Registered Retirement Savings Plan (Canada).
    Rrsp,
    /// Tax-Free Savings Account (Canada).
    Tfsa,
    /// Locked-In Retirement Account (Canada).
    Lira,
    /// Ordinary taxable / non-registered cash account.
    Taxable,
    /// Margin (leveraged) account.
    Margin,
}

impl AccountType {
    /// Canonical lowercase string for persistence.
    pub fn as_str(&self) -> &'static str {
        match self {
            AccountType::Rrsp => "rrsp",
            AccountType::Tfsa => "tfsa",
            AccountType::Lira => "lira",
            AccountType::Taxable => "taxable",
            AccountType::Margin => "margin",
        }
    }

    /// Parse a stored/string account type into the enum.
    ///
    /// # Errors
    /// Returns [`StorageError::InvalidAccountType`] for unknown values.
    pub fn parse(s: &str) -> Result<Self, StorageError> {
        match s.trim().to_ascii_lowercase().as_str() {
            "rrsp" => Ok(AccountType::Rrsp),
            "tfsa" => Ok(AccountType::Tfsa),
            "lira" => Ok(AccountType::Lira),
            "taxable" => Ok(AccountType::Taxable),
            "margin" => Ok(AccountType::Margin),
            other => Err(StorageError::InvalidAccountType(other.to_string())),
        }
    }
}

/// An account row. `name` and `broker` are encrypted client-side; only
/// the non-sensitive `id`, `account_type`, and timestamps are plaintext.
#[derive(Debug, Clone)]
pub struct Account {
    pub id: String,
    /// Encrypted display name (ciphertext + nonce, base64 in transit, bytes at rest).
    pub name_encrypted: Vec<u8>,
    pub name_nonce: Vec<u8>,
    pub account_type: AccountType,
    /// Encrypted broker name.
    pub broker_encrypted: Vec<u8>,
    pub broker_nonce: Vec<u8>,
    pub created_at: String,
}

/// A portfolio row. `name` is encrypted client-side.
#[derive(Debug, Clone)]
pub struct Portfolio {
    pub id: String,
    pub name_encrypted: Vec<u8>,
    pub name_nonce: Vec<u8>,
    pub created_at: String,
}

/// A holding row. Every financially-sensitive field is an encrypted blob.
///
/// `symbol` is also stored encrypted to avoid leaking position composition;
/// the engine treats holdings as opaque ciphertext containers.
#[derive(Debug, Clone)]
pub struct Holding {
    pub id: String,
    pub portfolio_id: String,
    pub account_id: Option<String>,
    /// Encrypted ticker symbol.
    pub symbol_encrypted: Vec<u8>,
    pub symbol_nonce: Vec<u8>,
    /// Encrypted JSON payload holding weight, returns[], yield_pct,
    /// cost_basis, market_value. Stored as one blob so the schema does
    /// not need to change as the analytics payload evolves.
    pub payload_encrypted: Vec<u8>,
    pub payload_nonce: Vec<u8>,
    pub created_at: String,
    pub updated_at: String,
}

/// Parameters for inserting a new holding. Using a params struct keeps
/// the `insert_holding` signature readable (avoids a 10-argument fn).
#[derive(Debug, Clone)]
pub struct NewHolding {
    pub portfolio_id: String,
    pub account_id: Option<String>,
    pub symbol_encrypted: Vec<u8>,
    pub symbol_nonce: Vec<u8>,
    pub payload_encrypted: Vec<u8>,
    pub payload_nonce: Vec<u8>,
}

/// Encrypted SQLite store with a small connection pool.
///
/// Clone-free by design: share it behind an `Arc` (as Axum shared state).
pub struct Store {
    pool: Mutex<Vec<Connection>>,
    path: PathBuf,
}

impl Store {
    /// Open (or create) a database at `path` and initialize the schema.
    ///
    /// Applies WAL journal mode and a busy timeout to every pooled
    /// connection, then runs the idempotent schema migration once.
    ///
    /// # Errors
    /// Returns [`StorageError::OpenFailed`] if the path cannot be opened,
    /// or [`StorageError::Sqlite`] if schema setup fails.
    pub fn open(path: impl AsRef<Path>) -> Result<Self, StorageError> {
        Self::open_with_pool_size(path, DEFAULT_POOL_SIZE)
    }

    /// Open with an explicit pool size (>= 1).
    ///
    /// # Errors
    /// As [`Store::open`]. Also validates `pool_size >= 1`.
    pub fn open_with_pool_size(
        path: impl AsRef<Path>,
        pool_size: usize,
    ) -> Result<Self, StorageError> {
        if pool_size == 0 {
            return Err(StorageError::Validation(
                "pool_size must be at least 1".to_string(),
            ));
        }

        let path = path.as_ref().to_path_buf();
        let path_str = path.display().to_string();

        let mut connections = Vec::with_capacity(pool_size);
        for _ in 0..pool_size {
            let conn = Connection::open_with_flags(
                &path,
                OpenFlags::SQLITE_OPEN_READ_WRITE | OpenFlags::SQLITE_OPEN_CREATE,
            )
            .map_err(|e| StorageError::OpenFailed {
                path: path_str.clone(),
                source_msg: e.to_string(),
            })?;

            conn.busy_timeout(std::time::Duration::from_millis(BUSY_TIMEOUT_MS as u64))?;
            // WAL: durable, crash-recoverable, concurrent readers + one writer.
            conn.pragma_update(None, "journal_mode", "WAL")?;
            conn.pragma_update(None, "synchronous", "NORMAL")?;
            conn.pragma_update(None, "foreign_keys", "ON")?;
            connections.push(conn);
        }

        let store = Store {
            pool: Mutex::new(connections),
            path,
        };
        store.init_schema()?;
        Ok(store)
    }

    /// Open an in-memory database (tests only). Pool size of 1 because
    /// each `:memory:` connection has its own private database.
    #[cfg(test)]
    pub fn open_in_memory() -> Result<Self, StorageError> {
        let conn = Connection::open_in_memory()?;
        conn.pragma_update(None, "foreign_keys", "ON")?;
        let store = Store {
            pool: Mutex::new(vec![conn]),
            path: PathBuf::from(":memory:"),
        };
        store.init_schema()?;
        Ok(store)
    }

    /// Path the store was opened at (for diagnostics).
    pub fn path(&self) -> &Path {
        &self.path
    }

    /// Borrow a connection from the pool, run `f`, and return the
    /// connection. If the pool is momentarily empty, returns
    /// [`StorageError::PoolExhausted`] rather than blocking forever.
    fn with_conn<T>(
        &self,
        f: impl FnOnce(&Connection) -> Result<T, StorageError>,
    ) -> Result<T, StorageError> {
        let conn = {
            let mut pool = self.pool.lock().map_err(|_| StorageError::PoolPoisoned)?;
            pool.pop().ok_or(StorageError::PoolExhausted)?
        };

        let result = f(&conn);

        // Always return the connection, even on error.
        if let Ok(mut pool) = self.pool.lock() {
            pool.push(conn);
        }
        result
    }

    /// Create all tables and indices if they do not already exist.
    fn init_schema(&self) -> Result<(), StorageError> {
        self.with_conn(|conn| {
            conn.execute_batch(
                r#"
                CREATE TABLE IF NOT EXISTS accounts (
                    id              TEXT PRIMARY KEY,
                    name_encrypted  BLOB NOT NULL,
                    name_nonce      BLOB NOT NULL,
                    account_type    TEXT NOT NULL
                        CHECK (account_type IN ('rrsp','tfsa','lira','taxable','margin')),
                    broker_encrypted BLOB NOT NULL,
                    broker_nonce     BLOB NOT NULL,
                    created_at      TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS portfolios (
                    id              TEXT PRIMARY KEY,
                    name_encrypted  BLOB NOT NULL,
                    name_nonce      BLOB NOT NULL,
                    created_at      TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS holdings (
                    id                TEXT PRIMARY KEY,
                    portfolio_id      TEXT NOT NULL,
                    account_id        TEXT,
                    symbol_encrypted  BLOB NOT NULL,
                    symbol_nonce      BLOB NOT NULL,
                    payload_encrypted BLOB NOT NULL,
                    payload_nonce     BLOB NOT NULL,
                    created_at        TEXT NOT NULL,
                    updated_at        TEXT NOT NULL,
                    FOREIGN KEY (portfolio_id) REFERENCES portfolios(id) ON DELETE CASCADE,
                    FOREIGN KEY (account_id)   REFERENCES accounts(id)   ON DELETE SET NULL
                );

                CREATE INDEX IF NOT EXISTS idx_holdings_portfolio
                    ON holdings(portfolio_id);
                CREATE INDEX IF NOT EXISTS idx_holdings_account
                    ON holdings(account_id);
                CREATE INDEX IF NOT EXISTS idx_holdings_updated
                    ON holdings(updated_at);
                "#,
            )?;
            Ok(())
        })
    }

    // --- Accounts ---

    /// Insert a new account. Returns the generated account id (UUID v4).
    ///
    /// # Errors
    /// [`StorageError::Validation`] if encrypted blobs are empty;
    /// [`StorageError::Sqlite`] on a database write failure.
    pub fn insert_account(
        &self,
        name_encrypted: &[u8],
        name_nonce: &[u8],
        account_type: AccountType,
        broker_encrypted: &[u8],
        broker_nonce: &[u8],
    ) -> Result<String, StorageError> {
        if name_encrypted.is_empty() || name_nonce.is_empty() {
            return Err(StorageError::Validation(
                "account name ciphertext and nonce must not be empty".to_string(),
            ));
        }

        let id = Uuid::new_v4().to_string();
        let now = Utc::now().to_rfc3339();

        self.with_conn(|conn| {
            conn.execute(
                "INSERT INTO accounts \
                 (id, name_encrypted, name_nonce, account_type, broker_encrypted, broker_nonce, created_at) \
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
                rusqlite::params![
                    id,
                    name_encrypted,
                    name_nonce,
                    account_type.as_str(),
                    broker_encrypted,
                    broker_nonce,
                    now,
                ],
            )?;
            Ok(())
        })?;

        Ok(id)
    }

    // --- Portfolios ---

    /// Insert a new portfolio. Returns the generated portfolio id (UUID v4).
    ///
    /// # Errors
    /// [`StorageError::Validation`] if the name ciphertext/nonce is empty.
    pub fn insert_portfolio(
        &self,
        name_encrypted: &[u8],
        name_nonce: &[u8],
    ) -> Result<String, StorageError> {
        if name_encrypted.is_empty() || name_nonce.is_empty() {
            return Err(StorageError::Validation(
                "portfolio name ciphertext and nonce must not be empty".to_string(),
            ));
        }

        let id = Uuid::new_v4().to_string();
        let now = Utc::now().to_rfc3339();

        self.with_conn(|conn| {
            conn.execute(
                "INSERT INTO portfolios (id, name_encrypted, name_nonce, created_at) \
                 VALUES (?1, ?2, ?3, ?4)",
                rusqlite::params![id, name_encrypted, name_nonce, now],
            )?;
            Ok(())
        })?;

        Ok(id)
    }

    /// List all portfolios, newest first.
    pub fn list_portfolios(&self) -> Result<Vec<Portfolio>, StorageError> {
        self.with_conn(|conn| {
            let mut stmt = conn.prepare(
                "SELECT id, name_encrypted, name_nonce, created_at \
                 FROM portfolios ORDER BY created_at DESC",
            )?;
            let rows = stmt.query_map([], |row| {
                Ok(Portfolio {
                    id: row.get(0)?,
                    name_encrypted: row.get(1)?,
                    name_nonce: row.get(2)?,
                    created_at: row.get(3)?,
                })
            })?;

            let mut out = Vec::new();
            for r in rows {
                out.push(r?);
            }
            Ok(out)
        })
    }

    /// Return true if a portfolio with `portfolio_id` exists.
    pub fn portfolio_exists(&self, portfolio_id: &str) -> Result<bool, StorageError> {
        self.with_conn(|conn| {
            let count: i64 = conn.query_row(
                "SELECT COUNT(1) FROM portfolios WHERE id = ?1",
                rusqlite::params![portfolio_id],
                |row| row.get(0),
            )?;
            Ok(count > 0)
        })
    }

    // --- Holdings CRUD ---

    /// Insert a holding. Returns the generated holding id (UUID v4).
    ///
    /// # Errors
    /// [`StorageError::Validation`] if required ciphertext blobs are empty
    /// or `portfolio_id` is empty;
    /// [`StorageError::NotFound`] if the referenced portfolio does not exist.
    pub fn insert_holding(&self, h: &NewHolding) -> Result<String, StorageError> {
        if h.portfolio_id.trim().is_empty() {
            return Err(StorageError::Validation(
                "portfolio_id must not be empty".to_string(),
            ));
        }
        if h.symbol_encrypted.is_empty() || h.payload_encrypted.is_empty() {
            return Err(StorageError::Validation(
                "symbol and payload ciphertext must not be empty".to_string(),
            ));
        }
        if !self.portfolio_exists(&h.portfolio_id)? {
            return Err(StorageError::NotFound {
                entity: "portfolio",
                id: h.portfolio_id.clone(),
            });
        }

        let id = Uuid::new_v4().to_string();
        let now = Utc::now().to_rfc3339();

        let h = h.clone();
        let id_clone = id.clone();
        self.with_conn(move |conn| {
            conn.execute(
                "INSERT INTO holdings \
                 (id, portfolio_id, account_id, symbol_encrypted, symbol_nonce, \
                  payload_encrypted, payload_nonce, created_at, updated_at) \
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)",
                rusqlite::params![
                    id_clone,
                    h.portfolio_id,
                    h.account_id,
                    h.symbol_encrypted,
                    h.symbol_nonce,
                    h.payload_encrypted,
                    h.payload_nonce,
                    now,
                    now,
                ],
            )?;
            Ok(())
        })?;

        Ok(id)
    }

    /// Update a holding's encrypted symbol/payload. Bumps `updated_at`.
    ///
    /// # Errors
    /// [`StorageError::NotFound`] if no holding has the given `id`.
    pub fn update_holding(
        &self,
        id: &str,
        symbol_encrypted: &[u8],
        symbol_nonce: &[u8],
        payload_encrypted: &[u8],
        payload_nonce: &[u8],
    ) -> Result<(), StorageError> {
        if symbol_encrypted.is_empty() || payload_encrypted.is_empty() {
            return Err(StorageError::Validation(
                "symbol and payload ciphertext must not be empty".to_string(),
            ));
        }

        let now = Utc::now().to_rfc3339();
        let affected = self.with_conn(|conn| {
            let n = conn.execute(
                "UPDATE holdings SET \
                 symbol_encrypted = ?2, symbol_nonce = ?3, \
                 payload_encrypted = ?4, payload_nonce = ?5, updated_at = ?6 \
                 WHERE id = ?1",
                rusqlite::params![
                    id,
                    symbol_encrypted,
                    symbol_nonce,
                    payload_encrypted,
                    payload_nonce,
                    now,
                ],
            )?;
            Ok(n)
        })?;

        if affected == 0 {
            return Err(StorageError::NotFound {
                entity: "holding",
                id: id.to_string(),
            });
        }
        Ok(())
    }

    /// Delete a holding by id.
    ///
    /// # Errors
    /// [`StorageError::NotFound`] if no holding has the given `id`.
    pub fn delete_holding(&self, id: &str) -> Result<(), StorageError> {
        let affected = self.with_conn(|conn| {
            let n = conn.execute(
                "DELETE FROM holdings WHERE id = ?1",
                rusqlite::params![id],
            )?;
            Ok(n)
        })?;

        if affected == 0 {
            return Err(StorageError::NotFound {
                entity: "holding",
                id: id.to_string(),
            });
        }
        Ok(())
    }

    /// Fetch all holdings for a portfolio, optionally filtered by account.
    ///
    /// Pass `account_id = None` to return every holding in the portfolio.
    pub fn get_holdings_by_portfolio(
        &self,
        portfolio_id: &str,
        account_id: Option<&str>,
    ) -> Result<Vec<Holding>, StorageError> {
        self.with_conn(|conn| {
            let mut out = Vec::new();
            match account_id {
                Some(acct) => {
                    let mut stmt = conn.prepare(
                        "SELECT id, portfolio_id, account_id, symbol_encrypted, symbol_nonce, \
                         payload_encrypted, payload_nonce, created_at, updated_at \
                         FROM holdings WHERE portfolio_id = ?1 AND account_id = ?2 \
                         ORDER BY created_at ASC",
                    )?;
                    let rows = stmt.query_map(
                        rusqlite::params![portfolio_id, acct],
                        Self::map_holding,
                    )?;
                    for r in rows {
                        out.push(r?);
                    }
                }
                None => {
                    let mut stmt = conn.prepare(
                        "SELECT id, portfolio_id, account_id, symbol_encrypted, symbol_nonce, \
                         payload_encrypted, payload_nonce, created_at, updated_at \
                         FROM holdings WHERE portfolio_id = ?1 \
                         ORDER BY created_at ASC",
                    )?;
                    let rows = stmt.query_map(
                        rusqlite::params![portfolio_id],
                        Self::map_holding,
                    )?;
                    for r in rows {
                        out.push(r?);
                    }
                }
            }
            Ok(out)
        })
    }

    /// Fetch a single holding by id.
    ///
    /// # Errors
    /// [`StorageError::NotFound`] if no holding has the given `id`.
    pub fn get_holding(&self, id: &str) -> Result<Holding, StorageError> {
        self.with_conn(|conn| {
            conn.query_row(
                "SELECT id, portfolio_id, account_id, symbol_encrypted, symbol_nonce, \
                 payload_encrypted, payload_nonce, created_at, updated_at \
                 FROM holdings WHERE id = ?1",
                rusqlite::params![id],
                Self::map_holding,
            )
            .map_err(|e| match e {
                rusqlite::Error::QueryReturnedNoRows => StorageError::NotFound {
                    entity: "holding",
                    id: id.to_string(),
                },
                other => StorageError::Sqlite(other.to_string()),
            })
        })
    }

    /// Point-in-time snapshot: every holding in `portfolio_id` that already
    /// existed at `as_of` (RFC3339) and was last updated at or before that
    /// instant. Used by the versioning layer to reconstruct historical
    /// portfolio state.
    ///
    /// Because `created_at`/`updated_at` are stored as RFC3339 strings,
    /// lexical comparison is also chronological, so a string `<=` is correct.
    pub fn get_portfolio_snapshot_at(
        &self,
        portfolio_id: &str,
        as_of: &str,
    ) -> Result<Vec<Holding>, StorageError> {
        if as_of.trim().is_empty() {
            return Err(StorageError::Validation(
                "as_of timestamp must not be empty".to_string(),
            ));
        }

        self.with_conn(|conn| {
            let mut stmt = conn.prepare(
                "SELECT id, portfolio_id, account_id, symbol_encrypted, symbol_nonce, \
                 payload_encrypted, payload_nonce, created_at, updated_at \
                 FROM holdings \
                 WHERE portfolio_id = ?1 AND created_at <= ?2 AND updated_at <= ?2 \
                 ORDER BY created_at ASC",
            )?;
            let rows = stmt.query_map(
                rusqlite::params![portfolio_id, as_of],
                Self::map_holding,
            )?;
            let mut out = Vec::new();
            for r in rows {
                out.push(r?);
            }
            Ok(out)
        })
    }

    /// Row -> `Holding` mapper shared by the read queries.
    fn map_holding(row: &rusqlite::Row<'_>) -> rusqlite::Result<Holding> {
        Ok(Holding {
            id: row.get(0)?,
            portfolio_id: row.get(1)?,
            account_id: row.get(2)?,
            symbol_encrypted: row.get(3)?,
            symbol_nonce: row.get(4)?,
            payload_encrypted: row.get(5)?,
            payload_nonce: row.get(6)?,
            created_at: row.get(7)?,
            updated_at: row.get(8)?,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn seed_portfolio(store: &Store) -> String {
        store
            .insert_portfolio(b"enc-name", b"nonce")
            .expect("insert portfolio")
    }

    fn new_holding(portfolio_id: &str) -> NewHolding {
        NewHolding {
            portfolio_id: portfolio_id.to_string(),
            account_id: None,
            symbol_encrypted: b"enc-AAPL".to_vec(),
            symbol_nonce: b"sym-nonce-12".to_vec(),
            payload_encrypted: b"enc-payload".to_vec(),
            payload_nonce: b"pay-nonce-12".to_vec(),
        }
    }

    #[test]
    fn test_account_type_roundtrip() {
        for t in [
            AccountType::Rrsp,
            AccountType::Tfsa,
            AccountType::Lira,
            AccountType::Taxable,
            AccountType::Margin,
        ] {
            assert_eq!(AccountType::parse(t.as_str()).unwrap(), t);
        }
        assert!(matches!(
            AccountType::parse("crypto").unwrap_err(),
            StorageError::InvalidAccountType(_)
        ));
        // case-insensitive
        assert_eq!(AccountType::parse("TFSA").unwrap(), AccountType::Tfsa);
    }

    #[test]
    fn test_insert_and_get_holding() {
        let store = Store::open_in_memory().unwrap();
        let pid = seed_portfolio(&store);
        let hid = store.insert_holding(&new_holding(&pid)).unwrap();

        let got = store.get_holding(&hid).unwrap();
        assert_eq!(got.portfolio_id, pid);
        assert_eq!(got.symbol_encrypted, b"enc-AAPL".to_vec());
    }

    #[test]
    fn test_insert_holding_unknown_portfolio_rejected() {
        let store = Store::open_in_memory().unwrap();
        let mut h = new_holding("does-not-exist");
        h.portfolio_id = "does-not-exist".to_string();
        let err = store.insert_holding(&h).unwrap_err();
        assert!(matches!(err, StorageError::NotFound { entity: "portfolio", .. }));
    }

    #[test]
    fn test_insert_holding_empty_ciphertext_rejected() {
        let store = Store::open_in_memory().unwrap();
        let pid = seed_portfolio(&store);
        let mut h = new_holding(&pid);
        h.payload_encrypted = vec![];
        assert!(matches!(
            store.insert_holding(&h).unwrap_err(),
            StorageError::Validation(_)
        ));
    }

    #[test]
    fn test_update_holding() {
        let store = Store::open_in_memory().unwrap();
        let pid = seed_portfolio(&store);
        let hid = store.insert_holding(&new_holding(&pid)).unwrap();

        store
            .update_holding(&hid, b"enc-MSFT", b"sym-nonce-99", b"enc-new", b"pay-nonce-99")
            .unwrap();

        let got = store.get_holding(&hid).unwrap();
        assert_eq!(got.symbol_encrypted, b"enc-MSFT".to_vec());
        assert_eq!(got.payload_encrypted, b"enc-new".to_vec());
    }

    #[test]
    fn test_update_missing_holding_not_found() {
        let store = Store::open_in_memory().unwrap();
        let err = store
            .update_holding("nope", b"a", b"b", b"c", b"d")
            .unwrap_err();
        assert!(matches!(err, StorageError::NotFound { entity: "holding", .. }));
    }

    #[test]
    fn test_delete_holding() {
        let store = Store::open_in_memory().unwrap();
        let pid = seed_portfolio(&store);
        let hid = store.insert_holding(&new_holding(&pid)).unwrap();

        store.delete_holding(&hid).unwrap();
        assert!(matches!(
            store.get_holding(&hid).unwrap_err(),
            StorageError::NotFound { .. }
        ));
        // second delete -> NotFound
        assert!(matches!(
            store.delete_holding(&hid).unwrap_err(),
            StorageError::NotFound { .. }
        ));
    }

    #[test]
    fn test_get_holdings_by_portfolio_and_account_filter() {
        let store = Store::open_in_memory().unwrap();
        let pid = seed_portfolio(&store);
        let acct = store
            .insert_account(b"enc-acct", b"nonce", AccountType::Tfsa, b"enc-broker", b"nonce")
            .unwrap();

        // one holding with no account, one tied to the account
        store.insert_holding(&new_holding(&pid)).unwrap();
        let mut h2 = new_holding(&pid);
        h2.account_id = Some(acct.clone());
        store.insert_holding(&h2).unwrap();

        assert_eq!(store.get_holdings_by_portfolio(&pid, None).unwrap().len(), 2);
        assert_eq!(
            store.get_holdings_by_portfolio(&pid, Some(&acct)).unwrap().len(),
            1
        );
    }

    #[test]
    fn test_snapshot_at_excludes_future_holdings() {
        let store = Store::open_in_memory().unwrap();
        let pid = seed_portfolio(&store);
        store.insert_holding(&new_holding(&pid)).unwrap();

        // A timestamp well in the past should yield no holdings.
        let past = "2000-01-01T00:00:00+00:00";
        assert_eq!(
            store.get_portfolio_snapshot_at(&pid, past).unwrap().len(),
            0
        );

        // A far-future timestamp should include the holding.
        let future = "2999-01-01T00:00:00+00:00";
        assert_eq!(
            store.get_portfolio_snapshot_at(&pid, future).unwrap().len(),
            1
        );
    }

    #[test]
    fn test_list_portfolios() {
        let store = Store::open_in_memory().unwrap();
        seed_portfolio(&store);
        seed_portfolio(&store);
        assert_eq!(store.list_portfolios().unwrap().len(), 2);
    }
}
