"""PKE Ingestion Pipeline.

Parses documents (PDF, HTML, Markdown, plain text), chunks into semantic
passages, classifies by theme, generates embeddings, and stores encrypted
in the local vector store.

All processing runs locally. No content ever leaves the user's machine.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

THEMES = [
    "risk",
    "valuation",
    "behavioral_bias",
    "capital_allocation",
    "regime_analysis",
    "decision_framework",
    "quantitative_method",
    "macro_economics",
    "leadership",
    "general",
]

# Maximum file size to ingest (10 MB). Prevents accidental memory exhaustion.
MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024

# Minimum chunk word count to keep. Shorter chunks lack semantic value.
MIN_CHUNK_WORDS = 10


@dataclass
class KnowledgeChunk:
    """A single passage from the user's knowledge base.

    Attributes:
        chunk_id: Deterministic SHA-256 hash (first 16 hex chars) of source + text.
        source: Source document identifier (from frontmatter or filename).
        author: Author name (from frontmatter, defaults to 'Unknown').
        date: Date string (from frontmatter, defaults to empty).
        themes: List of classified themes for this chunk.
        text: The passage text content.
        embedding: Vector embedding (populated by embedding pipeline).
    """

    chunk_id: str
    source: str
    author: str
    date: str
    themes: list[str]
    text: str
    embedding: list[float] = field(default_factory=list)


@dataclass
class IngestionResult:
    """Result of ingesting a document.

    Attributes:
        source_file: Path of the ingested file.
        chunks_created: Number of chunks produced.
        themes_detected: Sorted list of unique themes across all chunks.
        errors: List of error messages encountered during ingestion.
        chunks: The actual KnowledgeChunk objects produced.
    """

    source_file: str
    chunks_created: int
    themes_detected: list[str]
    errors: list[str]
    chunks: list[KnowledgeChunk] = field(default_factory=list)


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Extract YAML frontmatter from a Markdown document.

    Parses the leading ``---`` delimited block into a dict of key-value pairs.
    List values in ``[a, b, c]`` format are parsed into Python lists.

    Args:
        text: Raw Markdown text, potentially with frontmatter.

    Returns:
        Tuple of (metadata dict, body text without frontmatter).
        Returns ({}, original text) if no frontmatter is found.
    """
    pattern = r"^---\s*\n(.*?)\n---\s*\n(.*)$"
    match = re.match(pattern, text, re.DOTALL)
    if not match:
        return {}, text

    frontmatter_raw = match.group(1)
    body = match.group(2)

    metadata: dict = {}
    for line in frontmatter_raw.strip().split("\n"):
        if ":" in line:
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if value.startswith("[") and value.endswith("]"):
                value = [
                    v.strip().strip('"').strip("'")
                    for v in value[1:-1].split(",")
                ]
            metadata[key] = value

    return metadata, body


def chunk_text(text: str, max_tokens: int = 400) -> list[str]:
    """Split text into semantic chunks at paragraph boundaries.

    Attempts to keep chunks under ``max_tokens`` words while respecting
    paragraph boundaries. Oversized paragraphs are split at sentence
    boundaries as a fallback.

    Args:
        text: Plain text content to chunk.
        max_tokens: Maximum word count per chunk (default: 400).

    Returns:
        List of chunk strings. Empty input produces an empty list.

    Raises:
        ValueError: If max_tokens is less than 1.
    """
    if max_tokens < 1:
        msg = f"max_tokens must be >= 1, got {max_tokens}"
        raise ValueError(msg)

    if not text or not text.strip():
        return []

    paragraphs = re.split(r"\n\s*\n", text.strip())
    chunks: list[str] = []
    current = ""

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        word_count = len(para.split())

        if len(current.split()) + word_count <= max_tokens:
            current = f"{current}\n\n{para}" if current else para
        else:
            if current:
                chunks.append(current.strip())
            if word_count > max_tokens:
                # Split oversized paragraph at sentence boundaries
                sentences = re.split(r"(?<=[.!?])\s+", para)
                current = ""
                for sent in sentences:
                    if len(current.split()) + len(sent.split()) <= max_tokens:
                        current = f"{current} {sent}" if current else sent
                    else:
                        if current:
                            chunks.append(current.strip())
                        current = sent
            else:
                current = para

    if current.strip():
        chunks.append(current.strip())

    return chunks


def generate_chunk_id(source: str, text: str) -> str:
    """Deterministic chunk ID from source + text content.

    Uses SHA-256 of ``source:text[:200]`` truncated to 16 hex characters.
    This ensures the same chunk always gets the same ID across re-ingestion.

    Args:
        source: Source document identifier.
        text: Chunk text content.

    Returns:
        16-character hex string.
    """
    content = f"{source}:{text[:200]}"
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def classify_themes(text: str) -> list[str]:
    """Auto-classify a chunk into themes based on keyword matching.

    Stub implementation using simple keyword presence detection.
    Full version uses a local zero-shot classifier model.

    Args:
        text: Chunk text to classify.

    Returns:
        List of matching theme names. Returns ["general"] if no
        specific themes are detected.
    """
    text_lower = text.lower()
    detected: list[str] = []
    keyword_map: dict[str, list[str]] = {
        "risk": ["risk", "volatility", "drawdown", "var", "loss"],
        "valuation": ["valuation", "price", "earnings", "p/e", "multiple"],
        "behavioral_bias": ["bias", "anchoring", "overconfidence", "fear", "greed"],
        "capital_allocation": ["allocation", "capital", "deploy", "dividend", "buyback"],
        "regime_analysis": ["regime", "cycle", "recession", "expansion", "crisis"],
        "decision_framework": ["decision", "framework", "process", "checklist"],
        "quantitative_method": ["regression", "factor", "correlation", "monte carlo"],
        "macro_economics": ["inflation", "interest rate", "gdp", "unemployment"],
    }
    for theme, keywords in keyword_map.items():
        if any(kw in text_lower for kw in keywords):
            detected.append(theme)
    return detected if detected else ["general"]


def ingest_markdown(file_path: Path) -> IngestionResult:
    """Ingest a Markdown file into knowledge chunks.

    Reads the file, extracts frontmatter metadata, chunks the body text,
    classifies each chunk by theme, and returns structured results.

    Args:
        file_path: Path to the Markdown file.

    Returns:
        IngestionResult with chunks, themes, and any errors encountered.
        File read errors are captured in the errors list rather than raised.

    Error handling:
        - FileNotFoundError: captured with descriptive message.
        - PermissionError: captured with descriptive message.
        - UnicodeDecodeError: captured with descriptive message.
        - Files exceeding MAX_FILE_SIZE_BYTES: rejected with size error.
        - Other I/O exceptions: captured with generic message.
    """
    errors: list[str] = []

    # Validate path exists and is a file
    try:
        if not file_path.exists():
            return IngestionResult(
                source_file=str(file_path),
                chunks_created=0,
                themes_detected=[],
                errors=[f"File not found: {file_path}"],
            )

        if not file_path.is_file():
            return IngestionResult(
                source_file=str(file_path),
                chunks_created=0,
                themes_detected=[],
                errors=[f"Path is not a file: {file_path}"],
            )

        # Check file size before reading
        file_size = file_path.stat().st_size
        if file_size > MAX_FILE_SIZE_BYTES:
            return IngestionResult(
                source_file=str(file_path),
                chunks_created=0,
                themes_detected=[],
                errors=[
                    f"File too large: {file_size} bytes "
                    f"(max {MAX_FILE_SIZE_BYTES} bytes)"
                ],
            )
    except OSError as e:
        return IngestionResult(
            source_file=str(file_path),
            chunks_created=0,
            themes_detected=[],
            errors=[f"Cannot access file: {e}"],
        )

    # Read file content with explicit error handling per exception type
    try:
        text = file_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return IngestionResult(
            source_file=str(file_path),
            chunks_created=0,
            themes_detected=[],
            errors=[f"File not found: {file_path}"],
        )
    except PermissionError:
        return IngestionResult(
            source_file=str(file_path),
            chunks_created=0,
            themes_detected=[],
            errors=[f"Permission denied reading file: {file_path}"],
        )
    except UnicodeDecodeError as e:
        return IngestionResult(
            source_file=str(file_path),
            chunks_created=0,
            themes_detected=[],
            errors=[f"File is not valid UTF-8: {e}"],
        )
    except OSError as e:
        return IngestionResult(
            source_file=str(file_path),
            chunks_created=0,
            themes_detected=[],
            errors=[f"Failed to read file: {e}"],
        )

    if not text.strip():
        return IngestionResult(
            source_file=str(file_path),
            chunks_created=0,
            themes_detected=[],
            errors=["File is empty or contains only whitespace"],
        )

    metadata, body = parse_frontmatter(text)
    source = metadata.get("source", file_path.stem)
    author = metadata.get("author", "Unknown")
    date = metadata.get("date", "")
    themes = metadata.get("themes", [])
    if isinstance(themes, str):
        themes = [themes]

    chunks_text = chunk_text(body)
    chunks: list[KnowledgeChunk] = []

    for ct in chunks_text:
        if len(ct.split()) < MIN_CHUNK_WORDS:
            continue

        chunk_themes = themes if themes else classify_themes(ct)
        chunk = KnowledgeChunk(
            chunk_id=generate_chunk_id(source, ct),
            source=source,
            author=author,
            date=date,
            themes=chunk_themes,
            text=ct,
        )
        chunks.append(chunk)

    all_themes: set[str] = set()
    for c in chunks:
        all_themes.update(c.themes)

    return IngestionResult(
        source_file=str(file_path),
        chunks_created=len(chunks),
        themes_detected=sorted(all_themes),
        errors=errors,
        chunks=chunks,
    )


def ingest_directory(dir_path: Path) -> list[IngestionResult]:
    """Ingest all Markdown files in a directory recursively.

    Processes each ``.md`` file found via glob, collecting results.
    Individual file failures are captured in each result's errors list
    and do not abort processing of remaining files.

    Args:
        dir_path: Root directory to scan for Markdown files.

    Returns:
        List of IngestionResult, one per file found.

    Raises:
        ValueError: If dir_path does not exist or is not a directory.
    """
    if not dir_path.exists():
        msg = f"Directory does not exist: {dir_path}"
        raise ValueError(msg)

    if not dir_path.is_dir():
        msg = f"Path is not a directory: {dir_path}"
        raise ValueError(msg)

    results: list[IngestionResult] = []
    try:
        md_files = sorted(dir_path.glob("**/*.md"))
    except OSError as e:
        logger.error("Failed to list directory %s: %s", dir_path, e)
        return results

    for md_file in md_files:
        result = ingest_markdown(md_file)
        results.append(result)

    return results
