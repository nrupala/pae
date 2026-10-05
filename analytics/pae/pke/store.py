"""PKE Local Vector Store.

A single SQLite file holding the knowledge chunks plus a sqlite-vec virtual
table with their embeddings. No server, no background process, no network:
the "locally-indexed knowledge base" the README promises.

Schema:
    pke_chunks      chunk_id TEXT PK, source, author, date, themes (JSON array),
                    text, created_at
    pke_chunks_vec  vec0 virtual table: chunk_id TEXT PK, embedding FLOAT[dim]
    pke_meta        key/value registry (embedding model name + dimensions, so
                    a stale index built with a different model is detectable)

Zero-knowledge posture: everything stays in this file on the user's machine.
Note: chunk text is stored in cleartext in this local file. Field-level
encryption of the PKE store is deferred to the Crypto Vault integration
(key management lives in the Rust engine; the Python side never holds raw
keys) — see docs/THREAT_MODEL.md. The file itself inherits the host's disk
protections.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import struct
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import sqlite_vec

from pae.pke import embeddings
from pae.pke import ingest as ingest_mod

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "1"

_META_MODEL_KEY = "embedding_model"
_META_DIM_KEY = "embedding_dim"
_META_SCHEMA_KEY = "schema_version"

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def default_store_path() -> Path:
    """Default on-disk location for the PKE knowledge base (XDG data dir)."""
    return Path.home() / ".local" / "share" / "pae" / "pke.db"


class KnowledgeStore:
    """SQLite + sqlite-vec store for PKE chunks and their embeddings."""

    def __init__(self, db_path: str | Path) -> None:
        """Open (creating if needed) the store at ``db_path``.

        Args:
            db_path: Filesystem path for the SQLite database. Use ``:memory:``
                for an ephemeral store (tests).

        Raises:
            sqlite3.Error: If the database cannot be opened or the schema
                cannot be created.
        """
        self.db_path = str(db_path)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._vec_available = self._load_vec_extension()
        self._init_schema()

    # -- setup -----------------------------------------------------------

    def _load_vec_extension(self) -> bool:
        """Load the sqlite-vec extension; False degrades to keyword-only."""
        try:
            self._conn.enable_load_extension(True)
            sqlite_vec.load(self._conn)
            version = self._conn.execute("SELECT vec_version()").fetchone()[0]
            logger.debug("PKE: sqlite-vec %s loaded", version)
            return True
        except Exception as e:  # noqa: BLE001 - extension load is best-effort
            logger.warning(
                "PKE: sqlite-vec extension unavailable (%s); "
                "vector search disabled, keyword fallback will be used.",
                e,
            )
            return False

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS pke_chunks (
                chunk_id   TEXT PRIMARY KEY,
                source     TEXT NOT NULL,
                author     TEXT NOT NULL DEFAULT 'Unknown',
                date       TEXT NOT NULL DEFAULT '',
                themes     TEXT NOT NULL DEFAULT '["general"]',
                text       TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS pke_meta (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        if self._vec_available:
            self._conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS pke_chunks_vec USING "
                f"vec0(chunk_id TEXT PRIMARY KEY, embedding FLOAT[{embeddings.EMBEDDING_DIM}])"
            )
        self._conn.execute(
            "INSERT OR IGNORE INTO pke_meta (key, value) VALUES (?, ?)",
            (_META_SCHEMA_KEY, SCHEMA_VERSION),
        )
        self._conn.commit()

    # -- writes ----------------------------------------------------------

    def add_chunks(
        self,
        chunks: Sequence[ingest_mod.KnowledgeChunk],
        embed: bool = True,
        embed_fn: embeddings.EmbeddingFn | None = None,
    ) -> int:
        """Store chunks and (by default) index their embeddings.

        Chunk IDs are deterministic, so re-ingesting the same document is
        idempotent (INSERT OR REPLACE).

        Args:
            chunks: Knowledge chunks to store.
            embed: If True, generate embeddings (batched) and fill the
                sqlite-vec index. If embedding generation is unavailable,
                chunks are still stored and a warning is logged — retrieval
                then uses the keyword fallback.
            embed_fn: Optional injectable embedding function (tests).

        Returns:
            Number of chunks stored.
        """
        if not chunks:
            return 0
        now = datetime.now(UTC).isoformat()
        with self._conn:
            for chunk in chunks:
                self._conn.execute(
                    "INSERT OR REPLACE INTO pke_chunks "
                    "(chunk_id, source, author, date, themes, text, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        chunk.chunk_id,
                        chunk.source,
                        chunk.author,
                        chunk.date,
                        json.dumps(chunk.themes),
                        chunk.text,
                        now,
                    ),
                )

        vectors_stored = 0
        if embed and self._vec_available:
            try:
                ingest_mod.embed_chunks(list(chunks), embed_fn=embed_fn)
            except embeddings.EmbeddingsUnavailableError as e:
                logger.warning("PKE: %s Storing chunks without vectors.", e)
            else:
                with self._conn:
                    for chunk in chunks:
                        if chunk.embedding:
                            blob = struct.pack(
                                f"{len(chunk.embedding)}f", *chunk.embedding
                            )
                            # vec0 virtual tables do not support INSERT OR
                            # REPLACE, so delete-then-insert for idempotent
                            # re-ingestion.
                            self._conn.execute(
                                "DELETE FROM pke_chunks_vec WHERE chunk_id = ?",
                                (chunk.chunk_id,),
                            )
                            self._conn.execute(
                                "INSERT INTO pke_chunks_vec "
                                "(chunk_id, embedding) VALUES (?, ?)",
                                (chunk.chunk_id, blob),
                            )
                            vectors_stored += 1
                # Record which model produced the vectors so a stale index
                # built with a different model is detectable. Injected test
                # functions are labeled honestly, never as the real model.
                model_label = (
                    embeddings.EMBEDDING_MODEL
                    if embed_fn is None
                    else f"injected:{getattr(embed_fn, '__name__', 'custom')}"
                )
                self._set_meta(_META_MODEL_KEY, model_label)
                self._set_meta(_META_DIM_KEY, str(embeddings.EMBEDDING_DIM))
                logger.info(
                    "PKE: indexed %d/%d chunk vectors with %s",
                    vectors_stored,
                    len(chunks),
                    model_label,
                )
        elif embed and not self._vec_available:
            logger.warning(
                "PKE: sqlite-vec unavailable; stored %d chunks without vectors.",
                len(chunks),
            )
        return len(chunks)

    def _set_meta(self, key: str, value: str) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO pke_meta (key, value) VALUES (?, ?)",
                (key, value),
            )

    # -- reads -----------------------------------------------------------

    def get_meta(self, key: str) -> str | None:
        """Return a meta value, or None if unset."""
        row = self._conn.execute(
            "SELECT value FROM pke_meta WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else None

    @property
    def vec_available(self) -> bool:
        """Whether the sqlite-vec index is usable in this store."""
        return self._vec_available

    def count_chunks(self) -> int:
        """Number of chunks stored."""
        row = self._conn.execute("SELECT COUNT(*) AS n FROM pke_chunks").fetchone()
        return int(row["n"])

    def count_vectors(self) -> int:
        """Number of chunks with an indexed embedding (0 if vec unavailable)."""
        if not self._vec_available:
            return 0
        row = self._conn.execute("SELECT COUNT(*) AS n FROM pke_chunks_vec").fetchone()
        return int(row["n"])

    def get_chunk(self, chunk_id: str) -> dict[str, Any] | None:
        """Fetch one chunk as a dict (themes decoded), or None."""
        row = self._conn.execute(
            "SELECT chunk_id, source, author, date, themes, text "
            "FROM pke_chunks WHERE chunk_id = ?",
            (chunk_id,),
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["themes"] = json.loads(result["themes"])
        return result

    def vector_search(
        self,
        query_vector: Sequence[float],
        top_k: int = 5,
        themes: Sequence[str] | None = None,
    ) -> list[tuple[str, float]]:
        """Cosine-similarity search over the embedding index.

        Args:
            query_vector: L2-normalized query embedding.
            top_k: Maximum results.
            themes: Optional theme filter (chunk must carry at least one).

        Returns:
            List of (chunk_id, score) ordered by score descending. Score is
            cosine similarity clamped to [0, 1].

        Raises:
            RuntimeError: If the sqlite-vec index is unavailable.
        """
        if not self._vec_available:
            raise RuntimeError("sqlite-vec index unavailable")
        if top_k < 1:
            raise ValueError(f"top_k must be at least 1, got {top_k}")
        blob = struct.pack(f"{len(query_vector)}f", *query_vector)

        theme_filter = ""
        params: list[Any] = [blob, blob]
        if themes:
            placeholders = ", ".join("?" for _ in themes)
            theme_filter = (
                " AND v.chunk_id IN ("
                "SELECT chunk_id FROM pke_chunks, json_each(pke_chunks.themes) AS j "
                f"WHERE j.value IN ({placeholders}))"
            )
            params.extend(themes)
        params.append(top_k)

        rows = self._conn.execute(
            "SELECT v.chunk_id AS chunk_id, "
            "vec_distance_cosine(v.embedding, ?) AS distance "
            "FROM pke_chunks_vec v "
            "WHERE v.embedding MATCH ?"
            f"{theme_filter} AND k = ? "
            "ORDER BY distance",
            params,
        ).fetchall()
        return [
            (row["chunk_id"], max(0.0, min(1.0, 1.0 - float(row["distance"]))))
            for row in rows
        ]

    def keyword_search(
        self,
        query_text: str,
        top_k: int = 5,
        themes: Sequence[str] | None = None,
    ) -> list[tuple[str, float]]:
        """Fallback retrieval: token-overlap ranking over chunk texts.

        Used when embeddings are unavailable (no model, no sqlite-vec, or an
        index built without vectors). Deterministic and dependency-free.

        Scoring is the overlap coefficient: |query tokens ∩ chunk tokens| /
        |query tokens|, in [0, 1].

        Args:
            query_text: Free-text query.
            top_k: Maximum results.
            themes: Optional theme filter (chunk must carry at least one).

        Returns:
            List of (chunk_id, score) ordered by score descending. Chunks
            with zero overlap are omitted.
        """
        query_tokens = set(_TOKEN_RE.findall(query_text.lower()))
        if not query_tokens or top_k < 1:
            return []

        sql = "SELECT chunk_id, themes, text FROM pke_chunks"
        params: list[Any] = []
        if themes:
            placeholders = ", ".join("?" for _ in themes)
            sql += (
                " WHERE chunk_id IN ("
                "SELECT chunk_id FROM pke_chunks, json_each(pke_chunks.themes) AS j "
                f"WHERE j.value IN ({placeholders}))"
            )
            params.extend(themes)

        scored: list[tuple[str, float]] = []
        for row in self._conn.execute(sql, params).fetchall():
            chunk_tokens = set(_TOKEN_RE.findall(row["text"].lower()))
            overlap = query_tokens & chunk_tokens
            if overlap:
                scored.append((row["chunk_id"], len(overlap) / len(query_tokens)))
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:top_k]

    # -- lifecycle -------------------------------------------------------

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()

    def __enter__(self) -> KnowledgeStore:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
