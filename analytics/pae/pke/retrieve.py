"""PKE Contextual Retrieval.

Retrieves relevant passages from the user's knowledge base
based on the current analytical context.

All retrieval runs locally. No queries leave the user's machine.
"""

from dataclasses import dataclass


@dataclass
class RetrievalResult:
    """A passage retrieved from the knowledge base."""
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
        theme: One of the PKE theme categories.
        top_k: Number of passages to return.

    Returns:
        List of RetrievalResult sorted by relevance.
    """
    # TODO: Implement sqlite-vec vector search
    # 1. Load user's embedded knowledge base
    # 2. Filter by theme
    # 3. Compute cosine similarity with context embedding
    # 4. Return top-k results
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
        context_text: Description of current analytical context.
        themes: Optional theme filter.
        top_k: Number of passages to return.

    Returns:
        List of RetrievalResult sorted by relevance.
    """
    # TODO: Implement semantic search
    # 1. Embed context_text using local model (all-MiniLM-L6-v2)
    # 2. Search sqlite-vec for nearest neighbors
    # 3. Optionally filter by themes
    # 4. Return top-k
    return []


# Context-to-theme mapping for automatic PKE surfacing
ANALYTICAL_CONTEXT_THEMES = {
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
