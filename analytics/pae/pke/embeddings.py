"""PKE Local Embedding Provider.

Generates vector embeddings for knowledge chunks using a small ONNX model
that runs entirely on the user's machine. No text is ever sent to a hosted
API — the model weights are downloaded once (public weights from HuggingFace)
and every embedding after that is computed locally.

"As him" decision (2026-10-05, Nrupal's local-first doctrine):
    We evaluated fastembed first, per the Phase 4 brief, and it is suitable:
    ONNX runtime only, no torch, ~67 MB model, 384 dimensions, quality on par
    with the classic MiniLM baselines for retrieval. Alternatives rejected:
    sentence-transformers (already in pyproject but unused; drags in torch,
    ~2 GB — violates the dependency-light rule), hosted APIs (OpenAI/Cohere
    embeddings — violate the PKE zero-knowledge posture: document text would
    leave the machine). If fastembed ever becomes unsuitable, the next pick is
    a direct onnxruntime + tokenizers pipeline against the same
    Qdrant/bge-small-en-v1.5-onnx-Q weights — same model, zero extra deps.

The vector index itself lives in sqlite-vec (see pae.pke.store): a single
extension-loaded SQLite file, no server, no background process.
"""

from __future__ import annotations

import logging
import math
import os
import tempfile
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Default embedding model. fastembed's canonical default: small, fast, and
# good enough for personal-knowledge retrieval. ONNX weights, no torch.
EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
EMBEDDING_DIM = 384
EMBEDDING_BATCH_SIZE = 32

# HuggingFace repo holding the ONNX weights fastembed downloads. Used only to
# detect a cached model without touching the network (see
# embedding_model_available).
_HF_ONNX_REPO = "Qdrant/bge-small-en-v1.5-onnx-Q"

# Approximate one-time download size, stated in the user-facing log line.
_MODEL_DOWNLOAD_SIZE = "~67 MB"


class EmbeddingsUnavailableError(Exception):
    """Raised when local embeddings cannot be produced.

    Callers treat this as "vector search unavailable" and fall back to
    keyword retrieval (see pae.pke.retrieve). It is never raised for
    per-text failures — those are logged and the text is skipped.
    """


# Signature for injectable embedding functions (tests, future providers):
# a batch of texts in, a batch of L2-normalized vectors out.
EmbeddingFn = Callable[[list[str]], list[list[float]]]

_model: Any = None  # Lazy singleton fastembed TextEmbedding.


def _default_cache_dir() -> Path:
    """Resolve fastembed's effective cache directory without importing it."""
    override = os.environ.get("FASTEMBED_CACHE_PATH")
    if override:
        return Path(override)
    return Path(tempfile.gettempdir()) / "fastembed_cache"


def embedding_model_available() -> bool:
    """Check whether the embedding model is cached locally (no network).

    Returns:
        True if the ONNX weights are already on disk and can be loaded
        offline. Tests use this for skip-if-unavailable gating.
    """
    snapshot = _default_cache_dir() / f"models--{_HF_ONNX_REPO.replace('/', '--')}"
    return snapshot.is_dir() and any(snapshot.iterdir())


def _get_model() -> Any:
    """Load (downloading once on first use) the local embedding model."""
    global _model
    if _model is not None:
        return _model
    try:
        from fastembed import TextEmbedding
    except ImportError as e:
        raise EmbeddingsUnavailableError(
            "fastembed is not installed; install it to enable PKE vector "
            "search (pip install fastembed). Keyword fallback remains available."
        ) from e

    if not embedding_model_available():
        logger.info(
            "PKE: downloading embedding model %s (%s, one-time) into %s. "
            "These are public model weights; no document content leaves this machine.",
            EMBEDDING_MODEL,
            _MODEL_DOWNLOAD_SIZE,
            _default_cache_dir(),
        )
    else:
        logger.debug("PKE: loading cached embedding model %s", EMBEDDING_MODEL)
    _model = TextEmbedding(EMBEDDING_MODEL)
    return _model


def _l2_normalize(vector: Iterable[float]) -> list[float]:
    """Return the L2-normalized vector (zero vector stays zero)."""
    values = list(vector)
    norm = math.sqrt(sum(v * v for v in values))
    if norm == 0.0:
        return values
    return [v / norm for v in values]


def embed_texts(
    texts: list[str],
    batch_size: int = EMBEDDING_BATCH_SIZE,
    embed_fn: EmbeddingFn | None = None,
) -> list[list[float]]:
    """Embed a batch of texts into L2-normalized vectors.

    Vectors are unit length so cosine similarity is a plain dot product and
    sqlite-vec's cosine distance maps to a [0, 1] relevance score as
    ``1 - distance``.

    Args:
        texts: Texts to embed. Empty/whitespace texts yield zero vectors.
        batch_size: fastembed batch size for the ONNX session.
        embed_fn: Optional injectable embedding function (used by tests to
            avoid the model download). Must return one vector per text.

    Returns:
        List of float vectors, one per input text, each of length
        EMBEDDING_DIM (or the injected function's dimension).

    Raises:
        EmbeddingsUnavailableError: If the local model cannot be loaded and no
            embed_fn was provided.
    """
    if not texts:
        return []
    if embed_fn is not None:
        vectors = embed_fn(texts)
    else:
        model = _get_model()
        vectors = [list(map(float, v)) for v in model.embed(texts, batch_size=batch_size)]
    if len(vectors) != len(texts):
        raise EmbeddingsUnavailableError(
            f"Embedding provider returned {len(vectors)} vectors for {len(texts)} texts"
        )
    return [_l2_normalize(v) for v in vectors]
