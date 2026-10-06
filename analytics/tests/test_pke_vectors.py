# Copyright (C) 2026 Nrupal Akolkar
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for PKE vector search (pae.pke.embeddings / store / retrieve).

Unit tests use a deterministic fake embedding function (384-dim hashed
bag-of-words, L2-normalized) so they never touch the network. One
integration test exercises the real local ONNX model and is skipped with a
plain message when the model is not cached locally — tests must not depend
on network access.
"""

from __future__ import annotations

import hashlib
import math
import re

import pytest

from pae.pke import embeddings, ingest, retrieve
from pae.pke.store import KnowledgeStore

DIM = embeddings.EMBEDDING_DIM


def fake_embed(texts: list[str]) -> list[list[float]]:
    """Deterministic fake embedding: hashed bag-of-words, 384-dim, normalized.

    Shared words land in shared dimensions, so paraphrases with overlapping
    vocabulary score higher — enough to exercise the roundtrip logic without
    a model download.
    """
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
def store(chunks: list[ingest.KnowledgeChunk]) -> KnowledgeStore:
    """In-memory store indexed with the fake embedder."""
    s = KnowledgeStore(":memory:")
    s.add_chunks(chunks, embed_fn=fake_embed)
    yield s
    s.close()


# --- embed_chunks -------------------------------------------------------


def test_embed_chunks_populates_normalized_vectors(chunks):
    ingest.embed_chunks(chunks, embed_fn=fake_embed)
    for chunk in chunks:
        assert len(chunk.embedding) == DIM
        norm = math.sqrt(sum(v * v for v in chunk.embedding))
        assert norm == pytest.approx(1.0)


def test_embed_chunks_empty_list():
    assert ingest.embed_chunks([], embed_fn=fake_embed) == []


# --- chunk -> embed -> retrieve roundtrip --------------------------------


def test_roundtrip_paraphrase_returns_most_similar(store, chunks):
    query = (
        "spreading investments across uncorrelated assets lowers drawdown "
        "risk when markets are stressed"
    )
    results = retrieve.retrieve_by_context(query, store=store, embed_fn=fake_embed)
    assert len(results) > 0
    assert results[0].chunk_id == chunks[0].chunk_id
    assert results[0].themes == ["risk"]


def test_self_similarity_is_one(store, chunks):
    results = retrieve.retrieve_by_context(
        VALUATION_TEXT, top_k=1, store=store, embed_fn=fake_embed
    )
    assert len(results) == 1
    assert results[0].chunk_id == chunks[1].chunk_id
    assert results[0].relevance_score == pytest.approx(1.0)


def test_scores_bounded_and_ordered(store):
    results = retrieve.retrieve_by_context(
        "risk drawdown diversification valuation", top_k=3,
        store=store, embed_fn=fake_embed,
    )
    assert len(results) == 3
    scores = [r.relevance_score for r in results]
    assert all(0.0 <= s <= 1.0 for s in scores)
    assert scores == sorted(scores, reverse=True)


def test_retrieve_by_theme_filters(store, chunks):
    results = retrieve.retrieve_by_theme("risk", store=store, embed_fn=fake_embed)
    assert len(results) > 0
    assert all("risk" in r.themes for r in results)
    assert results[0].chunk_id == chunks[0].chunk_id


def test_retrieve_by_context_theme_filter(store, chunks):
    results = retrieve.retrieve_by_context(
        "investment process", themes=["valuation"], store=store, embed_fn=fake_embed
    )
    assert len(results) > 0
    assert all("valuation" in r.themes for r in results)


# --- fallback chain -------------------------------------------------------


def test_fallback_keyword_when_embeddings_disabled(chunks):
    """embed=False: store has no vectors; retrieval falls back to keywords."""
    s = KnowledgeStore(":memory:")
    try:
        s.add_chunks(chunks, embed=False)
        assert s.count_chunks() == 3
        assert s.count_vectors() == 0
        results = retrieve.retrieve_by_context(
            "how to reduce portfolio drawdown risk", store=s, embed_fn=fake_embed
        )
        assert len(results) > 0
        assert results[0].chunk_id == chunks[0].chunk_id
    finally:
        s.close()


def test_fallback_when_embedder_unavailable(chunks):
    """EmbeddingsUnavailableError at index time: chunks stored, keyword fallback works."""

    def boom(_texts: list[str]) -> list[list[float]]:
        raise embeddings.EmbeddingsUnavailableError("no model here")

    s = KnowledgeStore(":memory:")
    try:
        assert s.add_chunks(chunks, embed_fn=boom) == 3
        assert s.count_vectors() == 0
        results = retrieve.retrieve_by_context(
            "overconfidence bias checklist", store=s, embed_fn=boom
        )
        assert len(results) > 0
        assert results[0].chunk_id == chunks[2].chunk_id
    finally:
        s.close()


def test_empty_store_returns_empty():
    s = KnowledgeStore(":memory:")
    try:
        assert retrieve.retrieve_by_context("risk", store=s, embed_fn=fake_embed) == []
        assert retrieve.retrieve_by_theme("risk", store=s, embed_fn=fake_embed) == []
    finally:
        s.close()


# --- meta -----------------------------------------------------------------


def test_meta_records_model_and_dim(store):
    assert store.get_meta("embedding_model") == "injected:fake_embed"
    assert store.get_meta("embedding_dim") == str(DIM)


def test_reingest_is_idempotent(store, chunks):
    assert store.add_chunks(chunks, embed_fn=fake_embed) == 3
    assert store.count_chunks() == 3
    assert store.count_vectors() == 3


# --- validation ------------------------------------------------------------


def test_retrieve_validation(store):
    with pytest.raises(ValueError):
        retrieve.retrieve_by_context("", store=store)
    with pytest.raises(ValueError):
        retrieve.retrieve_by_context("x", top_k=0, store=store)
    with pytest.raises(ValueError):
        retrieve.retrieve_by_theme("", store=store)
    with pytest.raises(ValueError):
        retrieve.retrieve_by_theme("not_a_theme", store=store)
    with pytest.raises(ValueError):
        retrieve.retrieve_by_context("x", themes=["bogus"], store=store)


# --- integration: real local model -----------------------------------------

needs_model = pytest.mark.skipif(
    not embeddings.embedding_model_available(),
    reason=(
        "PKE integration test skipped: embedding model "
        f"{embeddings.EMBEDDING_MODEL} is not cached locally "
        "(run any PKE ingest once to download it; tests must not depend on network)"
    ),
)


@needs_model
def test_integration_real_model_roundtrip(chunks):
    """End-to-end with the real local ONNX model: a paraphrased query finds
    the right chunk and self-similarity is ~1."""
    s = KnowledgeStore(":memory:")
    try:
        assert s.add_chunks(chunks) == 3
        assert s.count_vectors() == 3
        assert s.get_meta("embedding_model") == embeddings.EMBEDDING_MODEL

        query = (
            "How can spreading investments across assets that do not move "
            "together protect a portfolio from severe drawdowns in a crisis?"
        )
        results = retrieve.retrieve_by_context(query, store=s)
        assert len(results) > 0
        assert results[0].chunk_id == chunks[0].chunk_id

        exact = retrieve.retrieve_by_context(VALUATION_TEXT, top_k=1, store=s)
        assert exact[0].chunk_id == chunks[1].chunk_id
        assert exact[0].relevance_score == pytest.approx(1.0, abs=1e-6)
    finally:
        s.close()


@needs_model
def test_integration_real_model_scores_sane(chunks):
    s = KnowledgeStore(":memory:")
    try:
        s.add_chunks(chunks)
        results = retrieve.retrieve_by_context(
            "portfolio risk management", top_k=3, store=s
        )
        scores = [r.relevance_score for r in results]
        assert all(0.0 <= score <= 1.0 for score in scores)
        assert scores == sorted(scores, reverse=True)
    finally:
        s.close()
