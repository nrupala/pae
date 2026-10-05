"""Tests for PKE chunk-text encryption (pae.pke.store DEK support).

Covers the "encrypted" half of the README's "private, encrypted,
locally-indexed knowledge base" claim:

    * encrypt/decrypt roundtrip through add_chunks -> vector search and
      add_chunks -> keyword search;
    * vector-search ranking/scoring unchanged by encryption;
    * wrong DEK fails loudly (PkeEncryptionError);
    * tampered ciphertext fails loudly;
    * no-DEK path keeps cleartext behavior and logs a loud warning;
    * at-rest, chunk text is ciphertext + a per-chunk nonce (not plaintext).

Unit tests use the same deterministic fake embedder as test_pke_vectors.py
so they never touch the network.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import math
import re
import sqlite3
from pathlib import Path

import pytest

from pae.pke import embeddings, ingest, retrieve
from pae.pke.store import DEK_BYTES, GCM_NONCE_BYTES, KnowledgeStore, PkeEncryptionError

DIM = embeddings.EMBEDDING_DIM

DEK_A = bytes(range(DEK_BYTES))  # 32-byte test DEK
DEK_B = bytes(reversed(range(DEK_BYTES)))  # a different 32-byte DEK


def fake_embed(texts: list[str]) -> list[list[float]]:
    """Deterministic fake embedding (same construction as test_pke_vectors)."""
    vectors: list[list[float]] = []
    for text in texts:
        vec = [0.0] * DIM
        for word in re.findall(r"[a-z0-9]+", text.lower()):
            idx = int(hashlib.sha256(word.encode()).hexdigest(), 16) % DIM
            vec[idx] += 1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        vectors.append([v / norm for v in vec])
    return vectors


def _chunk(text: str, themes: list[str], source: str = "test-doc") -> ingest.KnowledgeChunk:
    return ingest.KnowledgeChunk(
        chunk_id=ingest.generate_chunk_id(source, text),
        source=source,
        author="Test Author",
        date="2026-10-05",
        themes=themes,
        text=text,
    )


RISK_TEXT = (
    "Diversification across uncorrelated assets reduces portfolio drawdown "
    "risk and smooths returns during market stress periods."
)
VALUATION_TEXT = (
    "A disciplined valuation process compares market price to intrinsic "
    "earnings power using conservative multiples and a margin of safety."
)
BIAS_TEXT = (
    "Overconfidence bias leads investors to trade too often; a written "
    "decision checklist counters anchoring, fear, and greed."
)


@pytest.fixture
def chunks() -> list[ingest.KnowledgeChunk]:
    return [
        _chunk(RISK_TEXT, ["risk"], source="risk-notes"),
        _chunk(VALUATION_TEXT, ["valuation"], source="valuation-notes"),
        _chunk(BIAS_TEXT, ["behavioral_bias"], source="bias-notes"),
    ]


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "pke-enc.db"


# --- roundtrips -------------------------------------------------------------


def test_encrypted_vector_search_roundtrip(chunks, db_path):
    """add_chunks (DEK) -> retrieve_by_context (vector path) returns plaintext."""
    with KnowledgeStore(db_path, dek=DEK_A) as store:
        assert store.encrypted
        assert store.add_chunks(chunks, embed_fn=fake_embed) == 3
        assert store.count_vectors() == 3

        results = retrieve.retrieve_by_context(
            "spreading investments across uncorrelated assets lowers drawdown "
            "risk when markets are stressed",
            store=store,
            embed_fn=fake_embed,
        )
        assert len(results) > 0
        assert results[0].chunk_id == chunks[0].chunk_id
        assert results[0].text == RISK_TEXT
        assert results[0].themes == ["risk"]


def test_encrypted_keyword_search_roundtrip(chunks, db_path):
    """add_chunks (DEK, no vectors) -> keyword fallback returns plaintext."""
    with KnowledgeStore(db_path, dek=DEK_A) as store:
        store.add_chunks(chunks, embed=False)
        assert store.count_vectors() == 0

        results = retrieve.retrieve_by_context(
            "how to reduce portfolio drawdown risk", store=store, embed_fn=fake_embed
        )
        assert len(results) > 0
        assert results[0].chunk_id == chunks[0].chunk_id
        assert results[0].text == RISK_TEXT


def test_encrypted_get_chunk_roundtrip(chunks, db_path):
    with KnowledgeStore(db_path, dek=DEK_A) as store:
        store.add_chunks(chunks, embed=False)
        chunk = store.get_chunk(chunks[1].chunk_id)
        assert chunk is not None
        assert chunk["text"] == VALUATION_TEXT
        assert chunk["source"] == "valuation-notes"


def test_encryption_does_not_change_ranking_or_scoring(chunks, db_path, tmp_path):
    """Same corpus, same embedder: encrypted vs cleartext stores must rank
    and score identically. Vector-search behavior is encryption-agnostic."""
    query = "risk drawdown diversification valuation"

    plain_path = tmp_path / "pke-plain.db"
    with KnowledgeStore(plain_path) as plain:
        plain.add_chunks(chunks, embed_fn=fake_embed)
        plain_results = plain.vector_search(fake_embed([query])[0], top_k=3)

    with KnowledgeStore(db_path, dek=DEK_A) as enc:
        enc.add_chunks(chunks, embed_fn=fake_embed)
        enc_results = enc.vector_search(fake_embed([query])[0], top_k=3)

    assert [cid for cid, _ in enc_results] == [cid for cid, _ in plain_results]
    for (cid_e, score_e), (cid_p, score_p) in zip(enc_results, plain_results):
        assert cid_e == cid_p
        assert score_e == pytest.approx(score_p)


def test_retrieve_by_theme_encrypted(chunks, db_path):
    with KnowledgeStore(db_path, dek=DEK_A) as store:
        store.add_chunks(chunks, embed_fn=fake_embed)
        results = retrieve.retrieve_by_theme("risk", store=store, embed_fn=fake_embed)
        assert len(results) > 0
        assert results[0].chunk_id == chunks[0].chunk_id
        assert results[0].text == RISK_TEXT


# --- failure modes ------------------------------------------------------------


def test_wrong_dek_fails_loudly(chunks, db_path):
    """Opening an encrypted store with a different DEK raises on read."""
    with KnowledgeStore(db_path, dek=DEK_A) as store:
        store.add_chunks(chunks, embed_fn=fake_embed)

    with KnowledgeStore(db_path, dek=DEK_B) as store:
        # vector_search itself only returns ids/scores (no text touched)
        scored = store.vector_search(fake_embed(["risk drawdown"])[0], top_k=1)
        assert scored
        # hydration must fail loudly, not return garbage
        with pytest.raises(PkeEncryptionError, match="wrong DEK or tampered"):
            store.get_chunk(scored[0][0])
        with pytest.raises(PkeEncryptionError):
            retrieve.retrieve_by_context("risk", store=store, embed_fn=fake_embed)
        with pytest.raises(PkeEncryptionError):
            store.keyword_search("risk drawdown")


def test_tampered_ciphertext_detected(chunks, db_path):
    """Flipping a byte of stored ciphertext breaks GCM auth -> loud failure."""
    with KnowledgeStore(db_path, dek=DEK_A) as store:
        store.add_chunks(chunks, embed=False)
        chunk_id = chunks[0].chunk_id

    raw = sqlite3.connect(db_path)
    try:
        (stored,) = raw.execute(
            "SELECT text FROM pke_chunks WHERE chunk_id = ?", (chunk_id,)
        ).fetchone()
        tampered = ("A" if stored[0] != "A" else "B") + stored[1:]
        assert tampered != stored
        raw.execute("UPDATE pke_chunks SET text = ? WHERE chunk_id = ?", (tampered, chunk_id))
        raw.commit()
    finally:
        raw.close()

    with KnowledgeStore(db_path, dek=DEK_A) as store:
        with pytest.raises(PkeEncryptionError, match="wrong DEK or tampered"):
            store.get_chunk(chunk_id)


def test_tampered_nonce_detected(chunks, db_path):
    """Flipping a nonce byte also breaks auth -> loud failure."""
    with KnowledgeStore(db_path, dek=DEK_A) as store:
        store.add_chunks(chunks, embed=False)
        chunk_id = chunks[0].chunk_id

    raw = sqlite3.connect(db_path)
    try:
        (nonce,) = raw.execute(
            "SELECT nonce FROM pke_chunks WHERE chunk_id = ?", (chunk_id,)
        ).fetchone()
        bad = bytes([nonce[0] ^ 0xFF]) + nonce[1:]
        raw.execute("UPDATE pke_chunks SET nonce = ? WHERE chunk_id = ?", (bad, chunk_id))
        raw.commit()
    finally:
        raw.close()

    with KnowledgeStore(db_path, dek=DEK_A) as store:
        with pytest.raises(PkeEncryptionError, match="wrong DEK or tampered"):
            store.get_chunk(chunk_id)


def test_encrypted_row_without_dek_fails_loudly(chunks, db_path):
    """Opening an encrypted store with no DEK at all raises on read."""
    with KnowledgeStore(db_path, dek=DEK_A) as store:
        store.add_chunks(chunks, embed=False)

    with KnowledgeStore(db_path) as store:  # no DEK
        assert not store.encrypted
        with pytest.raises(PkeEncryptionError, match="no DEK"):
            store.get_chunk(chunks[0].chunk_id)


def test_invalid_dek_length_rejected(db_path):
    with pytest.raises(ValueError, match="exactly 32 bytes"):
        KnowledgeStore(db_path, dek=b"too-short")
    with pytest.raises(ValueError, match="exactly 32 bytes"):
        KnowledgeStore(db_path, dek=b"x" * 33)


# --- at-rest guarantees -------------------------------------------------------


def test_at_rest_text_is_ciphertext_not_plaintext(chunks, db_path):
    """Raw SQL on the file must not reveal plaintext; nonces are 12 bytes,
    unique per chunk (fresh random nonce each)."""
    with KnowledgeStore(db_path, dek=DEK_A) as store:
        store.add_chunks(chunks, embed=False)

    raw = sqlite3.connect(db_path)
    try:
        rows = raw.execute("SELECT chunk_id, text, nonce FROM pke_chunks").fetchall()
    finally:
        raw.close()

    assert len(rows) == 3
    nonces: set[bytes] = set()
    for chunk_id, stored_text, nonce in rows:
        original = next(c.text for c in chunks if c.chunk_id == chunk_id)
        assert original not in stored_text
        assert stored_text not in (RISK_TEXT, VALUATION_TEXT, BIAS_TEXT)
        # text column holds base64 ciphertext including the 16-byte GCM tag
        decoded = base64.b64decode(stored_text, validate=True)
        assert len(decoded) == len(original.encode("utf-8")) + 16
        assert isinstance(nonce, bytes) and len(nonce) == GCM_NONCE_BYTES
        nonces.add(nonce)
    assert len(nonces) == 3, "each chunk must get a fresh random nonce"


def test_reingest_regenerates_nonce(chunks, db_path):
    """Re-ingesting the same chunk re-encrypts with a fresh nonce."""
    with KnowledgeStore(db_path, dek=DEK_A) as store:
        store.add_chunks(chunks, embed=False)
        (nonce1,) = store._conn.execute(
            "SELECT nonce FROM pke_chunks WHERE chunk_id = ?", (chunks[0].chunk_id,)
        ).fetchone()
        store.add_chunks([chunks[0]], embed=False)
        (nonce2,) = store._conn.execute(
            "SELECT nonce FROM pke_chunks WHERE chunk_id = ?", (chunks[0].chunk_id,)
        ).fetchone()
        assert bytes(nonce1) != bytes(nonce2)
        # ...and still decrypts to the same plaintext
        assert store.get_chunk(chunks[0].chunk_id)["text"] == RISK_TEXT


def test_metadata_stays_cleartext(chunks, db_path):
    """Themes (needed for SQL filtering) and other metadata stay cleartext;
    only the passage text is encrypted. Documented in the store docstring."""
    with KnowledgeStore(db_path, dek=DEK_A) as store:
        store.add_chunks(chunks, embed=False)

    raw = sqlite3.connect(db_path)
    try:
        row = raw.execute(
            "SELECT source, author, themes, nonce FROM pke_chunks WHERE chunk_id = ?",
            (chunks[0].chunk_id,),
        ).fetchone()
    finally:
        raw.close()
    assert row[0] == "risk-notes"
    assert row[1] == "Test Author"
    assert row[2] == '["risk"]'
    assert row[3] is not None  # text is encrypted


# --- no-DEK (cleartext) path ----------------------------------------------------


def test_no_dek_stores_cleartext_and_warns(chunks, db_path, caplog):
    """Without a DEK the historic behavior is preserved, with a loud warning."""
    with caplog.at_level(logging.WARNING, logger="pae.pke.store"):
        with KnowledgeStore(db_path) as store:
            assert not store.encrypted
            store.add_chunks(chunks, embed_fn=fake_embed)

    assert any("CLEARTEXT" in rec.message for rec in caplog.records), (
        "store without DEK must log a cleartext warning"
    )

    raw = sqlite3.connect(db_path)
    try:
        (stored_text, nonce) = raw.execute(
            "SELECT text, nonce FROM pke_chunks WHERE chunk_id = ?",
            (chunks[0].chunk_id,),
        ).fetchone()
    finally:
        raw.close()
    assert stored_text == RISK_TEXT
    assert nonce is None

    with KnowledgeStore(db_path) as store:
        results = retrieve.retrieve_by_context(
            "spreading investments across uncorrelated assets lowers drawdown "
            "risk when markets are stressed",
            store=store,
            embed_fn=fake_embed,
        )
        assert results[0].text == RISK_TEXT


def test_no_dek_keyword_fallback_unchanged(chunks, db_path):
    with KnowledgeStore(db_path) as store:
        store.add_chunks(chunks, embed=False)
        scored = store.keyword_search("overconfidence bias checklist", top_k=3)
        assert scored[0][0] == chunks[2].chunk_id
        assert store.get_chunk(scored[0][0])["text"] == BIAS_TEXT


# --- migration: pre-encryption stores ------------------------------------------


def test_legacy_store_without_nonce_column_still_opens(chunks, db_path):
    """Stores created before encryption existed (no nonce column) migrate on
    open and read their cleartext rows as cleartext."""
    # Build a legacy-schema store: pke_chunks without the nonce column.
    raw = sqlite3.connect(db_path)
    try:
        raw.execute(
            "CREATE TABLE pke_chunks (chunk_id TEXT PRIMARY KEY, source TEXT NOT NULL,"
            " author TEXT NOT NULL DEFAULT 'Unknown', date TEXT NOT NULL DEFAULT '',"
            " themes TEXT NOT NULL DEFAULT '[\"general\"]', text TEXT NOT NULL,"
            " created_at TEXT NOT NULL)"
        )
        raw.execute(
            "INSERT INTO pke_chunks (chunk_id, source, author, date, themes, text, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                chunks[0].chunk_id,
                "risk-notes",
                "Test Author",
                "2026-10-05",
                '["risk"]',
                RISK_TEXT,
                "2026-10-05T00:00:00+00:00",
            ),
        )
        raw.commit()
    finally:
        raw.close()

    # Opens with a DEK (migration runs), legacy row reads as cleartext.
    with KnowledgeStore(db_path, dek=DEK_A) as store:
        columns = {r["name"] for r in store._conn.execute("PRAGMA table_info(pke_chunks)")}
        assert "nonce" in columns
        chunk = store.get_chunk(chunks[0].chunk_id)
        assert chunk is not None
        assert chunk["text"] == RISK_TEXT

        # ...and new writes to the migrated store are encrypted.
        store.add_chunks([chunks[1]], embed=False)
        (nonce,) = store._conn.execute(
            "SELECT nonce FROM pke_chunks WHERE chunk_id = ?", (chunks[1].chunk_id,)
        ).fetchone()
        assert nonce is not None
