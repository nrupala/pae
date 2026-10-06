# Copyright (C) 2026 Nrupal Akolkar
# SPDX-License-Identifier: AGPL-3.0-or-later

"""PKE Contextual Retrieval.

Retrieves relevant passages from the user's knowledge base
based on the current analytical context.

All retrieval runs locally. No queries leave the user's machine.

Retrieval fallback chain (documented, in order):
    1. Vector search — cosine similarity over the sqlite-vec index, using
       locally-generated embeddings (see pae.pke.embeddings). This is the
       primary path: semantic, theme-filterable, scored in [0, 1].
    2. Keyword fallback — token-overlap ranking over stored chunk texts.
       Used automatically when embeddings are unavailable: the local model
       cannot be loaded, the sqlite-vec extension failed to load, or the
       store was built without vectors. Deterministic and dependency-free.

Both paths honor the optional theme filter. An empty store returns [].
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from pae.pke import embeddings
from pae.pke import ingest as ingest_mod
from pae.pke.store import KnowledgeStore, default_store_path

logger = logging.getLogger(__name__)


@dataclass
class RetrievalResult:
    """A single retrieved knowledge passage.

    Attributes:
        chunk_id: Unique identifier of the knowledge chunk.
        source: Source document name or path.
        author: Author of the source document.
        text: The passage text content.
        themes: Theme classifications for the chunk.
        relevance_score: Similarity score (0.0 to 1.0, higher is more relevant).
    """

    chunk_id: str
    source: str
    author: str
    text: str
    themes: list[str]
    relevance_score: float


def _resolve_store(
    store: KnowledgeStore | None, dek: bytes | None = None
) -> tuple[KnowledgeStore, bool]:
    """Return (store, owns_connection). Opens the default store if needed.

    Args:
        store: Caller-supplied store, or None to open the default.
        dek: Optional 32-byte DEK, passed through to the default store so
            encrypted knowledge bases can be read. Ignored when ``store``
            is supplied (the store already carries its own DEK posture).
    """
    if store is not None:
        return store, False
    path = default_store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    logger.debug("PKE: using default knowledge store at %s", path)
    return KnowledgeStore(path, dek=dek), True


def _to_results(store: KnowledgeStore, scored: list[tuple[str, float]]) -> list[RetrievalResult]:
    """Hydrate (chunk_id, score) pairs into ordered RetrievalResults."""
    results: list[RetrievalResult] = []
    for chunk_id, score in scored:
        chunk = store.get_chunk(chunk_id)
        if chunk is None:
            continue
        results.append(
            RetrievalResult(
                chunk_id=chunk["chunk_id"],
                source=chunk["source"],
                author=chunk["author"],
                text=chunk["text"],
                themes=chunk["themes"],
                relevance_score=score,
            )
        )
    return results


def _vector_search(
    store: KnowledgeStore,
    query_text: str,
    top_k: int,
    themes: list[str] | None,
    embed_fn: embeddings.EmbeddingFn | None = None,
) -> list[tuple[str, float]] | None:
    """Try vector search; return None when embeddings are unavailable."""
    if not store.vec_available or store.count_vectors() == 0:
        return None
    try:
        query_vector = embeddings.embed_texts([query_text], embed_fn=embed_fn)[0]
    except embeddings.EmbeddingsUnavailableError as e:
        logger.info("PKE: %s Falling back to keyword search.", e)
        return None
    return store.vector_search(query_vector, top_k=top_k, themes=themes)


def retrieve_by_theme(
    theme: str,
    top_k: int = 5,
    store: KnowledgeStore | None = None,
    embed_fn: embeddings.EmbeddingFn | None = None,
    dek: bytes | None = None,
) -> list[RetrievalResult]:
    """Retrieve top-k passages matching a theme.

    Primary path is sqlite-vec vector similarity search filtered to the
    theme; when embeddings are unavailable it falls back to keyword
    ranking over the theme's chunks (see module docstring).

    Args:
        theme: Theme name to filter by (must be a valid theme from
            the THEMES list in ingest.py).
        top_k: Maximum number of results to return (default: 5).
            Must be at least 1.
        store: KnowledgeStore to search. If None, the default store at
            ``~/.local/share/pae/pke.db`` is opened for this call.
        embed_fn: Optional injectable embedding function for the query
            (tests use a deterministic fake; must match the dimension
            used at index time).
        dek: Optional 32-byte DEK for the default store (ignored when
            ``store`` is supplied). Needed to read an encrypted knowledge
            base; chunk texts are decrypted on hydration.

    Returns:
        List of RetrievalResult ordered by relevance_score descending.
        Empty list if the store has no chunks for the theme.

    Raises:
        ValueError: If theme is empty, not a known theme, or top_k < 1.
        PkeEncryptionError: If the default store is encrypted and the DEK
            is missing or wrong.
    """
    if not theme or not theme.strip():
        raise ValueError("theme must not be empty")
    if theme not in ingest_mod.THEMES:
        raise ValueError(f"unknown theme: {theme!r} (see ingest.THEMES)")
    if top_k < 1:
        raise ValueError(f"top_k must be at least 1, got {top_k}")

    resolved, owns = _resolve_store(store, dek=dek)
    try:
        scored = _vector_search(resolved, theme, top_k, themes=[theme], embed_fn=embed_fn)
        if scored is None:
            logger.info("PKE: vector search unavailable; keyword fallback for theme=%s", theme)
            scored = resolved.keyword_search(theme, top_k=top_k, themes=[theme])
        return _to_results(resolved, scored)
    finally:
        if owns:
            resolved.close()


def retrieve_by_context(
    context_text: str,
    themes: list[str] | None = None,
    top_k: int = 5,
    store: KnowledgeStore | None = None,
    embed_fn: embeddings.EmbeddingFn | None = None,
    dek: bytes | None = None,
) -> list[RetrievalResult]:
    """Retrieve passages relevant to a given analytical context.

    Used by the Decision Intelligence Layer to surface relevant
    knowledge when the user is making decisions.

    Primary path is semantic (vector) search over the local embedding
    index; when embeddings are unavailable it falls back to keyword
    ranking (see module docstring).

    Args:
        context_text: Free-text description of the current analytical
            context (e.g., "evaluating high-yield BDC positions").
        themes: Optional list of themes to filter by. If None, searches
            across all themes. Each must be a valid theme from
            the THEMES list in ingest.py.
        top_k: Maximum number of results to return (default: 5).
            Must be at least 1.
        store: KnowledgeStore to search. If None, the default store at
            ``~/.local/share/pae/pke.db`` is opened for this call.
        embed_fn: Optional injectable embedding function for the query
            (tests use a deterministic fake; must match the dimension
            used at index time).
        dek: Optional 32-byte DEK for the default store (ignored when
            ``store`` is supplied). Needed to read an encrypted knowledge
            base; chunk texts are decrypted on hydration.

    Returns:
        List of RetrievalResult ordered by relevance_score descending.
        Empty list if the store has no matching chunks.

    Raises:
        ValueError: If context_text is empty, a theme is unknown,
            or top_k is less than 1.
        PkeEncryptionError: If the default store is encrypted and the DEK
            is missing or wrong.
    """
    if not context_text or not context_text.strip():
        raise ValueError("context_text must not be empty")
    if top_k < 1:
        raise ValueError(f"top_k must be at least 1, got {top_k}")
    if themes:
        unknown = [t for t in themes if t not in ingest_mod.THEMES]
        if unknown:
            raise ValueError(f"unknown themes: {unknown} (see ingest.THEMES)")

    resolved, owns = _resolve_store(store, dek=dek)
    try:
        scored = _vector_search(resolved, context_text, top_k, themes=themes, embed_fn=embed_fn)
        if scored is None:
            logger.info("PKE: vector search unavailable; keyword fallback in use")
            scored = resolved.keyword_search(context_text, top_k=top_k, themes=themes)
        return _to_results(resolved, scored)
    finally:
        if owns:
            resolved.close()


# Context-to-theme mapping for automatic PKE surfacing.
# Maps analytical context names to relevant knowledge themes.
ANALYTICAL_CONTEXT_THEMES: dict[str, list[str]] = {
    "monte_carlo": ["risk", "quantitative_method"],
    "stress_test": ["risk", "regime_analysis"],
    "factor_decomposition": ["quantitative_method", "valuation"],
    "carry_analysis": ["capital_allocation", "risk"],
    "correlation": ["risk", "regime_analysis"],
    "optimization": ["capital_allocation", "quantitative_method"],
    "decision_journal": ["behavioral_bias", "decision_framework"],
    "premortem": ["decision_framework", "behavioral_bias"],
    "confidence_calibration": ["behavioral_bias", "decision_framework"],
    "margin_review": ["capital_allocation", "risk"],
    "tax_analysis": ["capital_allocation"],
    "macro_overlay": ["macro_economics", "regime_analysis"],
}
