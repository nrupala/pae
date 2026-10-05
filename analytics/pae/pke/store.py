"""PKE Local Vector Store.

A single SQLite file holding the knowledge chunks plus a sqlite-vec virtual
table with their embeddings. No server, no background process, no network:
the "locally-indexed knowledge base" the README promises.

Encryption (2026-10-05): the README calls the PKE a "private, encrypted,
locally-indexed knowledge base" — the store now encrypts chunk text at rest.
Pass an optional 32-byte DEK (data encryption key) to :class:`KnowledgeStore`;
chunk text is then encrypted with AES-256-GCM before insert and decrypted on
retrieval (both the vector-search and keyword-search paths hydrate plaintext).
Each chunk gets a fresh random 12-byte nonce, stored in the ``nonce`` column
next to the base64 ciphertext (stored in ``text``).

DESIGN (trust model, consistent with the existing local-first oracle pattern):
    * The Python analytics process runs on the user's machine, like the local
      ONNX embedding model and the sqlite-vec index.
    * The DEK is supplied per-operation from the client, which holds it after
      a vault unlock (see ``ui/src/crypto/vault-client.ts`` for the DEK/KEK
      hierarchy and envelope format). The Python side NEVER persists the DEK
      — not in this file, not anywhere on disk — the same posture as the
      engine's caller-supplied-key ``/encrypt`` / ``/decrypt`` oracles
      (``engine/src/api/crypto_api.rs``).
    * The DEK lives in memory for the store's lifetime only. It is not
      zeroed on close (CPython offers no reliable guarantee there); the
      security property is at-rest, not in-memory.
    * Cipher convention matches the engine and the vault-client interop spec:
      AES-256-GCM, random 12-byte nonce, empty AAD, base64 ciphertext that
      INCLUDES the 16-byte GCM tag (encrypt-then-MAC as emitted by
      WebCrypto / Rust aes-gcm / Python cryptography). Authentication failure
      (wrong DEK or tampered bytes) is fatal, never silent.
    * Granularity matches the vault's per-vault DEK decision: ONE DEK for the
      whole knowledge base, NOT per-record keys. Justification (same shape as
      ``vault-client.ts``): one vault = one trust boundary; per-record keys
      would multiply rotation work with no threat-model win — the adversary
      sees ciphertext either way, and memory holds the working key either way.

AT-REST GUARANTEE (what is and is not encrypted):
    * Encrypted: chunk TEXT (the passage content) — the ``text`` column holds
      base64 AES-GCM ciphertext when a DEK is in use.
    * NOT encrypted: chunk metadata (source, author, date, themes). Themes
      must stay cleartext because theme filtering is a SQL ``json_each``
      predicate; the other fields are display metadata.
    * NOT encrypted: embeddings in ``pke_chunks_vec``. They are derived from
      plaintext at ingest — but that happens IN MEMORY ONLY (see
      ``pae.pke.ingest.embed_chunks`` / ``pae.pke.embeddings``), never written
      to disk as text. A 384-dim L2-normalized float vector reveals
      similarity structure but not the passage content.

When no DEK is supplied, the store keeps the historic cleartext behavior but
logs an explicit warning — loudly, at construction — so "encrypted" is never
assumed silently.

Schema:
    pke_chunks      chunk_id TEXT PK, source, author, date, themes (JSON array),
                    text (cleartext OR base64 AES-GCM ciphertext),
                    nonce (BLOB, 12-byte GCM nonce; NULL = cleartext row),
                    created_at
    pke_chunks_vec  vec0 virtual table: chunk_id TEXT PK, embedding FLOAT[dim]
    pke_meta        key/value registry (embedding model name + dimensions, so
                    a stale index built with a different model is detectable)

Zero-knowledge posture: everything stays in this file on the user's machine.
The file itself inherits the host's disk protections.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import sqlite3
import struct
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import sqlite_vec
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from pae.pke import embeddings
from pae.pke import ingest as ingest_mod

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "1"

_META_MODEL_KEY = "embedding_model"
_META_DIM_KEY = "embedding_dim"
_META_SCHEMA_KEY = "schema_version"

# AES-256-GCM parameters. Key/nonce lengths match the vault-client interop
# spec (ui/src/crypto/vault-client.ts): 32-byte keys, 12-byte nonces.
DEK_BYTES = 32
GCM_NONCE_BYTES = 12

_TOKEN_RE = re.compile(r"[a-z0-9]+")


class PkeEncryptionError(Exception):
    """Raised when PKE chunk encryption or decryption fails.

    This is the loud failure mode for the encrypted store: a wrong DEK,
    tampered ciphertext, or a missing DEK for an encrypted row all surface
    as this exception — never as silently corrupted text.
    """


def default_store_path() -> Path:
    """Default on-disk location for the PKE knowledge base (XDG data dir)."""
    return Path.home() / ".local" / "share" / "pae" / "pke.db"


class KnowledgeStore:
    """SQLite + sqlite-vec store for PKE chunks and their embeddings.

    Args:
        db_path: Filesystem path for the SQLite database. Use ``:memory:``
            for an ephemeral store (tests).
        dek: Optional 32-byte data encryption key. When supplied, chunk text
            is encrypted with AES-256-GCM before insert and decrypted on
            retrieval. The DEK is held in memory for the store's lifetime and
            NEVER persisted. When omitted, chunk text is stored in cleartext
            and a warning is logged.
    """

    def __init__(self, db_path: str | Path, dek: bytes | None = None) -> None:
        """Open (creating if needed) the store at ``db_path``.

        Args:
            db_path: Filesystem path for the SQLite database. Use ``:memory:``
                for an ephemeral store (tests).
            dek: Optional 32-byte data encryption key for chunk-text
                encryption at rest (see class docstring).

        Raises:
            ValueError: If ``dek`` is not None and is not exactly
                :data:`DEK_BYTES` bytes.
            sqlite3.Error: If the database cannot be opened or the schema
                cannot be created.
        """
        if dek is not None:
            if not isinstance(dek, (bytes, bytearray)) or len(dek) != DEK_BYTES:
                raise ValueError(
                    f"PKE DEK must be exactly {DEK_BYTES} bytes, "
                    f"got {len(dek) if isinstance(dek, (bytes, bytearray)) else type(dek).__name__}"
                )
            logger.info("PKE: chunk-text encryption ENABLED (AES-256-GCM)")
        else:
            # Loud by design: the README promises "encrypted", so a
            # cleartext store must never be mistaken for an encrypted one.
            logger.warning(
                "PKE: NO DEK supplied — chunk text will be stored in CLEARTEXT "
                "in %s. Pass a 32-byte dek= to encrypt the knowledge base at rest.",
                db_path,
            )
        self.db_path = str(db_path)
        self._dek = bytes(dek) if dek is not None else None
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
                nonce      BLOB,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS pke_meta (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        # Migration for stores created before chunk-text encryption existed:
        # rows written then have no nonce column at all.
        columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(pke_chunks)")}
        if "nonce" not in columns:
            self._conn.execute("ALTER TABLE pke_chunks ADD COLUMN nonce BLOB")
            logger.info("PKE: migrated schema — added pke_chunks.nonce column")
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

    # -- encryption ------------------------------------------------------

    @property
    def encrypted(self) -> bool:
        """Whether this store encrypts chunk text at rest."""
        return self._dek is not None

    def _encrypt_text(self, plaintext: str) -> tuple[str, bytes]:
        """Encrypt chunk text: fresh random nonce per chunk.

        Returns (base64 ciphertext INCLUDING the 16-byte GCM tag, nonce
        bytes), matching the engine/vault-client cipher convention.

        Raises:
            PkeEncryptionError: If encryption fails.
        """
        assert self._dek is not None  # caller checks ``encrypted``
        nonce = os.urandom(GCM_NONCE_BYTES)
        try:
            ciphertext = AESGCM(self._dek).encrypt(nonce, plaintext.encode("utf-8"), None)
        except Exception as e:
            raise PkeEncryptionError(f"PKE: chunk encryption failed: {e}") from e
        return base64.b64encode(ciphertext).decode("ascii"), nonce

    def _decrypt_text(self, ciphertext_b64: str, nonce: bytes) -> str:
        """Decrypt chunk text; auth failure is fatal, never silent.

        Raises:
            PkeEncryptionError: On wrong DEK, tampered ciphertext/nonce, or
                undecodable payload.
        """
        if self._dek is None:
            raise PkeEncryptionError(
                "PKE: chunk is encrypted but no DEK was supplied to this store; "
                "reopen with the DEK to read it"
            )
        try:
            ciphertext = base64.b64decode(ciphertext_b64, validate=True)
        except Exception as e:
            raise PkeEncryptionError(
                f"PKE: stored chunk ciphertext is not valid base64: {e}"
            ) from e
        try:
            plaintext = AESGCM(self._dek).decrypt(nonce, ciphertext, None)
        except InvalidTag as e:
            # GCM authentication failed: wrong DEK or tampered bytes.
            # cryptography raises InvalidTag for both; the caller cannot and
            # need not distinguish them — both are fatal.
            raise PkeEncryptionError(
                "PKE: chunk decryption failed (wrong DEK or tampered ciphertext)"
            ) from e
        except Exception as e:
            raise PkeEncryptionError(f"PKE: chunk decryption failed: {e}") from e
        return plaintext.decode("utf-8")

    def _maybe_decrypt(self, stored_text: str, nonce: bytes | None) -> str:
        """Return plaintext for a row: decrypt when a nonce is present.

        A NULL nonce means the row was written before encryption existed (or
        by a store opened without a DEK); such rows are read as cleartext —
        honest mixed-store behavior, documented in the module docstring.
        """
        if nonce is None:
            return stored_text
        return self._decrypt_text(stored_text, bytes(nonce))

    # -- writes ----------------------------------------------------------

    def add_chunks(
        self,
        chunks: Sequence[ingest_mod.KnowledgeChunk],
        embed: bool = True,
        embed_fn: embeddings.EmbeddingFn | None = None,
    ) -> int:
        """Store chunks and (by default) index their embeddings.

        Chunk IDs are deterministic, so re-ingesting the same document is
        idempotent (INSERT OR REPLACE). When this store has a DEK, chunk text
        is encrypted (fresh nonce per chunk) before insert.

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
                stored_text: str
                stored_nonce: bytes | None
                if self._dek is not None:
                    stored_text, stored_nonce = self._encrypt_text(chunk.text)
                else:
                    stored_text, stored_nonce = chunk.text, None
                self._conn.execute(
                    "INSERT OR REPLACE INTO pke_chunks "
                    "(chunk_id, source, author, date, themes, text, nonce, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        chunk.chunk_id,
                        chunk.source,
                        chunk.author,
                        chunk.date,
                        json.dumps(chunk.themes),
                        stored_text,
                        stored_nonce,
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
                            blob = struct.pack(f"{len(chunk.embedding)}f", *chunk.embedding)
                            # vec0 virtual tables do not support INSERT OR
                            # REPLACE, so delete-then-insert for idempotent
                            # re-ingestion.
                            self._conn.execute(
                                "DELETE FROM pke_chunks_vec WHERE chunk_id = ?",
                                (chunk.chunk_id,),
                            )
                            self._conn.execute(
                                "INSERT INTO pke_chunks_vec (chunk_id, embedding) VALUES (?, ?)",
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
        row = self._conn.execute("SELECT value FROM pke_meta WHERE key = ?", (key,)).fetchone()
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
        """Fetch one chunk as a dict (themes decoded, text decrypted), or None.

        Raises:
            PkeEncryptionError: If the row is encrypted and decryption fails
                (wrong DEK, tampered data) or no DEK was supplied.
        """
        row = self._conn.execute(
            "SELECT chunk_id, source, author, date, themes, text, nonce "
            "FROM pke_chunks WHERE chunk_id = ?",
            (chunk_id,),
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        nonce = result.pop("nonce")
        result["text"] = self._maybe_decrypt(result["text"], nonce)
        result["themes"] = json.loads(result["themes"])
        return result

    def vector_search(
        self,
        query_vector: Sequence[float],
        top_k: int = 5,
        themes: Sequence[str] | None = None,
    ) -> list[tuple[str, float]]:
        """Cosine-similarity search over the embedding index.

        Ranking and scoring are computed over the embedding index only, so
        encryption of chunk text does not change vector-search behavior at
        all; text hydration (with decryption) happens in ``get_chunk``.

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
        return [(row["chunk_id"], max(0.0, min(1.0, 1.0 - float(row["distance"])))) for row in rows]

    def keyword_search(
        self,
        query_text: str,
        top_k: int = 5,
        themes: Sequence[str] | None = None,
    ) -> list[tuple[str, float]]:
        """Fallback retrieval: token-overlap ranking over chunk texts.

        Used when embeddings are unavailable (no model, no sqlite-vec, or an
        index built without vectors). Deterministic and dependency-free.
        Encrypted rows are decrypted in Python before tokenizing, so scoring
        is identical whether the store is encrypted or not.

        Scoring is the overlap coefficient: |query tokens ∩ chunk tokens| /
        |query tokens|, in [0, 1].

        Args:
            query_text: Free-text query.
            top_k: Maximum results.
            themes: Optional theme filter (chunk must carry at least one).

        Returns:
            List of (chunk_id, score) ordered by score descending. Chunks
            with zero overlap are omitted.

        Raises:
            PkeEncryptionError: If an encrypted row cannot be decrypted
                (wrong DEK, tampered data, or no DEK supplied).
        """
        query_tokens = set(_TOKEN_RE.findall(query_text.lower()))
        if not query_tokens or top_k < 1:
            return []

        sql = "SELECT chunk_id, themes, text, nonce FROM pke_chunks"
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
            text = self._maybe_decrypt(row["text"], row["nonce"])
            chunk_tokens = set(_TOKEN_RE.findall(text.lower()))
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
