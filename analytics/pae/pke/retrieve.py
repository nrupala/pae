"""PKE Contextual Retrieval.

Retrieves relevant passages from the user's knowledge base
based on the current analytical context.

All retrieval runs locally. No queries leave the user's machine.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

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


def retrieve_by_theme(
    theme: str,
    top_k: int = 5,
) -> list[RetrievalResult]:
    """Retrieve top-k passages matching a theme.

    Stub implementation. Full version uses sqlite-vec for
    vector similarity search with theme filtering.

    Args:
        theme: Theme name to filter by (must be a valid theme from
            the THEMES list in ingest.py).
        top_k: Maximum number of results to return (default: 5).
            Must be at least 1.

    Returns:
        List of RetrievalResult ordered by relevance_score descending.
        Currently returns an empty list (stub).

    Raises:
        ValueError: If theme is empty or top_k is less than 1.
    """
    if not theme or not theme.strip():
        raise ValueError("theme must not be empty")
    if top_k < 1:
        raise ValueError(f"top_k must be at least 1, got {top_k}")

    logger.debug("retrieve_by_theme: theme=%s, top_k=%d (stub)", theme, top_k)
    # TODO: Implement sqlite-vec vector search
    return []


def retrieve_by_context(
    context_text: str,
    themes: list[str] | None = None,
    top_k: int = 5,
) -> list[RetrievalResult]:
    """Retrieve passages relevant to a given analytical context.

    Used by the Decision Intelligence Layer to surface relevant
    knowledge when the user is making decisions.

    Args:
        context_text: Free-text description of the current analytical
            context (e.g., "evaluating high-yield BDC positions").
        themes: Optional list of themes to filter by. If None, searches
            across all themes.
        top_k: Maximum number of results to return (default: 5).
            Must be at least 1.

    Returns:
        List of RetrievalResult ordered by relevance_score descending.
        Currently returns an empty list (stub).

    Raises:
        ValueError: If context_text is empty or top_k is less than 1.
    """
    if not context_text or not context_text.strip():
        raise ValueError("context_text must not be empty")
    if top_k < 1:
        raise ValueError(f"top_k must be at least 1, got {top_k}")

    logger.debug(
        "retrieve_by_context: text_len=%d, themes=%s, top_k=%d (stub)",
        len(context_text), themes, top_k,
    )
    # TODO: Implement semantic search
    return []


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
