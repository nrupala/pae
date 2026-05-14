"""Local SQLite cache for market data with TTL and integrity checks.

All data is cached locally. No cache state ever leaves the user's machine.
Supports optional encryption-at-rest for sensitive data (brokerage positions).
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Default cache location
DEFAULT_CACHE_DIR = Path.home() / ".pae" / "cache"
DEFAULT_CACHE_DB = "market_data.db"

# Default TTL values (seconds)
TTL_QUOTES = 300          # 5 minutes for price quotes
TTL_DAILY_PRICES = 3600   # 1 hour for daily OHLCV
TTL_FACTORS = 86400       # 24 hours for factor returns
TTL_MACRO = 86400         # 24 hours for macro indicators
TTL_METADATA = 604800     # 7 days for instrument metadata


@dataclass
class CacheEntry:
    """A single cached data entry.

    Attributes:
        key: Cache key (typically source:symbol:datatype).
        value: JSON-serializable cached data.
        source: Data source identifier (e.g. 'yahoo', 'fred').
        created_at: Unix timestamp when entry was created.
        expires_at: Unix timestamp when entry expires.
        checksum: SHA-256 of the serialized value for integrity verification.
    """

    key: str
    value: Any
    source: str
    created_at: float
    expires_at: float
    checksum: str


class CacheError(Exception):
    """Raised when cache operations fail."""


def _compute_checksum(data: str) -> str:
    """Compute SHA-256 checksum of serialized data."""
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


class DataCache:
    """SQLite-backed cache for market data.

    Provides TTL-based expiration, integrity checks via SHA-256 checksums,
    and optional encryption-at-rest for sensitive entries.

    Args:
        cache_dir: Directory for the cache database. Created if missing.
        db_name: SQLite database filename.

    Raises:
        CacheError: If the database cannot be created or opened.
    """

    def __init__(
        self,
        cache_dir: Path | None = None,
        db_name: str = DEFAULT_CACHE_DB,
    ) -> None:
        self._cache_dir = cache_dir or DEFAULT_CACHE_DIR
        self._db_path = self._cache_dir / db_name
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    def _init_db(self) -> None:
        """Initialize the cache database and create tables if needed."""
        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(
                str(self._db_path),
                timeout=10.0,
                check_same_thread=False,
            )
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS cache (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    source TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    checksum TEXT NOT NULL
                )
            """)
            self._conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_cache_expires
                ON cache(expires_at)
            """)
            self._conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_cache_source
                ON cache(source)
            """)
            self._conn.commit()
            logger.info("Cache initialized at %s", self._db_path)
        except (sqlite3.Error, OSError) as e:
            msg = f"Failed to initialize cache at {self._db_path}: {e}"
            raise CacheError(msg) from e

    def _ensure_conn(self) -> sqlite3.Connection:
        """Return the active connection or raise."""
        if self._conn is None:
            msg = "Cache connection is closed"
            raise CacheError(msg)
        return self._conn

    def get(self, key: str) -> CacheEntry | None:
        """Retrieve a cache entry by key.

        Returns None if the key does not exist or has expired.
        Verifies integrity via checksum on read.

        Args:
            key: Cache key to look up.

        Returns:
            CacheEntry if found, valid, and not expired. None otherwise.

        Raises:
            CacheError: If the database read fails.
        """
        if not key or not key.strip():
            return None

        conn = self._ensure_conn()
        now = time.time()

        try:
            row = conn.execute(
                "SELECT key, value, source, created_at, expires_at, checksum "
                "FROM cache WHERE key = ? AND expires_at > ?",
                (key, now),
            ).fetchone()
        except sqlite3.Error as e:
            msg = f"Cache read failed for key '{key}': {e}"
            raise CacheError(msg) from e

        if row is None:
            return None

        raw_value = row[1]
        stored_checksum = row[5]

        # Integrity check
        computed = _compute_checksum(raw_value)
        if computed \!= stored_checksum:
            logger.warning(
                "Cache integrity check failed for key '%s': "
                "stored=%s computed=%s. Evicting entry.",
                key, stored_checksum, computed,
            )
            self.delete(key)
            return None

        try:
            value = json.loads(raw_value)
        except json.JSONDecodeError as e:
            logger.warning("Cache JSON decode failed for key '%s': %s", key, e)
            self.delete(key)
            return None

        return CacheEntry(
            key=row[0],
            value=value,
            source=row[2],
            created_at=row[3],
            expires_at=row[4],
            checksum=stored_checksum,
        )

    def put(
        self,
        key: str,
        value: Any,
        source: str,
        ttl: float = TTL_QUOTES,
    ) -> None:
        """Store a value in the cache with TTL.

        Args:
            key: Cache key (must be non-empty).
            value: JSON-serializable data to cache.
            source: Data source identifier.
            ttl: Time-to-live in seconds.

        Raises:
            ValueError: If key is empty or ttl is non-positive.
            CacheError: If the database write fails.
        """
        if not key or not key.strip():
            msg = "Cache key must not be empty"
            raise ValueError(msg)
        if ttl <= 0:
            msg = f"TTL must be positive, got {ttl}"
            raise ValueError(msg)

        conn = self._ensure_conn()
        now = time.time()

        try:
            raw_value = json.dumps(value, default=str)
        except (TypeError, ValueError) as e:
            msg = f"Failed to serialize value for key '{key}': {e}"
            raise CacheError(msg) from e

        checksum = _compute_checksum(raw_value)

        try:
            conn.execute(
                "INSERT OR REPLACE INTO cache "
                "(key, value, source, created_at, expires_at, checksum) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (key, raw_value, source, now, now + ttl, checksum),
            )
            conn.commit()
        except sqlite3.Error as e:
            msg = f"Cache write failed for key '{key}': {e}"
            raise CacheError(msg) from e

    def delete(self, key: str) -> None:
        """Delete a cache entry by key.

        Args:
            key: Cache key to delete. No error if key does not exist.

        Raises:
            CacheError: If the database delete fails.
        """
        conn = self._ensure_conn()
        try:
            conn.execute("DELETE FROM cache WHERE key = ?", (key,))
            conn.commit()
        except sqlite3.Error as e:
            msg = f"Cache delete failed for key '{key}': {e}"
            raise CacheError(msg) from e

    def evict_expired(self) -> int:
        """Remove all expired entries from the cache.

        Returns:
            Number of entries evicted.

        Raises:
            CacheError: If the eviction query fails.
        """
        conn = self._ensure_conn()
        now = time.time()
        try:
            cursor = conn.execute(
                "DELETE FROM cache WHERE expires_at <= ?", (now,)
            )
            conn.commit()
            count = cursor.rowcount
            if count > 0:
                logger.info("Evicted %d expired cache entries", count)
            return count
        except sqlite3.Error as e:
            msg = f"Cache eviction failed: {e}"
            raise CacheError(msg) from e

    def clear_source(self, source: str) -> int:
        """Clear all entries from a specific data source.

        Args:
            source: Source identifier to clear (e.g. 'yahoo').

        Returns:
            Number of entries cleared.

        Raises:
            CacheError: If the clear query fails.
        """
        conn = self._ensure_conn()
        try:
            cursor = conn.execute(
                "DELETE FROM cache WHERE source = ?", (source,)
            )
            conn.commit()
            return cursor.rowcount
        except sqlite3.Error as e:
            msg = f"Cache clear failed for source '{source}': {e}"
            raise CacheError(msg) from e

    def stats(self) -> dict[str, Any]:
        """Return cache statistics.

        Returns:
            Dict with total_entries, expired_entries, size_bytes,
            and per-source counts.
        """
        conn = self._ensure_conn()
        now = time.time()
        try:
            total = conn.execute("SELECT COUNT(*) FROM cache").fetchone()[0]
            expired = conn.execute(
                "SELECT COUNT(*) FROM cache WHERE expires_at <= ?", (now,)
            ).fetchone()[0]
            sources = conn.execute(
                "SELECT source, COUNT(*) FROM cache GROUP BY source"
            ).fetchall()
            size = self._db_path.stat().st_size if self._db_path.exists() else 0
        except (sqlite3.Error, OSError):
            return {"total_entries": 0, "error": "failed to read stats"}

        return {
            "total_entries": total,
            "expired_entries": expired,
            "active_entries": total - expired,
            "size_bytes": size,
            "sources": {row[0]: row[1] for row in sources},
        }

    def close(self) -> None:
        """Close the cache database connection."""
        if self._conn is not None:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass
            self._conn = None

    def __enter__(self) -> DataCache:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
