"""PKE Ingestion Pipeline.

Parses documents (PDF, HTML, Markdown, plain text), chunks into semantic
passages, classifies by theme, generates embeddings, and stores encrypted
in the local vector store.

All processing runs locally. No content ever leaves the user's machine.
"""

import hashlib
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

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


@dataclass
class KnowledgeChunk:
    """A single passage from the user's knowledge base.

    Attributes:
        chunk_id: Deterministic hash-based ID (first 16 chars of SHA-256).
        source: Source document name or path.
        author: Author of the source document.
        date: Publication or creation date string.
        themes: List of theme classifications for this chunk.
        text: The actual passage text.
        embedding: Float vector embedding (populated by embedding model).
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
        source_file: Path to the ingested file.
        chunks_created: Number of knowledge chunks produced.
        themes_detected: Sorted list of unique themes found.
        errors: List of error messages encountered during ingestion.
        chunks: The actual KnowledgeChunk objects created.
    """

    source_file: str
    chunks_created: int
    themes_detected: list[str]
    errors: list[str]
    chunks: list[KnowledgeChunk] = field(default_factory=list)


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Extract YAML frontmatter from a Markdown document.

    Parses the YAML block between ``---`` delimiters at the start of a
    Markdown file. Supports simple key-value pairs and inline lists.

    Args:
        text: Raw Markdown text, possibly with frontmatter.

    Returns:
        A tuple of (metadata_dict, body_text). If no frontmatter is found,
        returns an empty dict and the original text unchanged.

    Note:
        This is a lightweight parser. It handles:
        - Simple key: value pairs
        - Inline lists: key: [val1, val2]
        - Quoted values: key: "value" or key: 'value'
        It does NOT handle nested YAML, multi-line values, or anchors.
    """
    if not text or not text.startswith("---"):
        return {}, text

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
                    if v.strip()
                ]
            metadata[key] = value

    return metadata, body


def chunk_text(text: str, max_tokens: int = 400) -> list[str]:
    """Split text into semantic chunks at paragraph boundaries.

    Attempts to keep paragraphs together up to ``max_tokens`` words.
    Paragraphs exceeding the limit are split at sentence boundaries.

    Args:
        text: Input text to chunk.
        max_tokens: Maximum word count per chunk (default: 400).
            Must be at least 10.

    Returns:
        List of text chunks. Empty list if input text is empty or blank.

    Raises:
        ValueError: If max_tokens is less than 10.
    """
    if max_tokens < 10:
        raise ValueError(f"max_tokens must be at least 10, got {max_tokens}")

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

    Args:
        source: Source document identifier.
        text: Chunk text content (first 200 chars used for hashing).

    Returns:
        16-character hexadecimal hash string.
    """
    content = f"{source}:{text[:200]}"
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def classify_themes(text: str) -> list[str]:
    """Auto-classify a chunk into themes based on keyword matching.

    Stub implementation. Full version uses a local zero-shot classifier
    (e.g., sentence-transformers with BART-MNLI).

    Args:
        text: Chunk text to classify.

    Returns:
        List of detected theme strings. Returns ["general"] if no
        specific themes are detected.
    """
    if not text:
        return ["general"]

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

    Reads the file, extracts frontmatter metadata, splits into semantic
    chunks, classifies themes, and generates deterministic chunk IDs.

    Args:
        file_path: Path to a Markdown file. Must exist and be readable.

    Returns:
        IngestionResult with chunks and metadata. If the file cannot be read,
        returns a result with zero chunks and an error message.

    Note:
        Chunks with fewer than 10 words are discarded as too short
        for meaningful retrieval.
    """
    errors: list[str] = []

    if not file_path.exists():
        return IngestionResult(
            source_file=str(file_path),
            chunks_created=0,
            themes_detected=[],
            errors=[f"File does not exist: {file_path}"],
        )

    try:
        text = file_path.read_text(encoding="utf-8")
    except OSError as e:
        logger.error("Failed to read file %s: %s", file_path, e)
        return IngestionResult(
            source_file=str(file_path),
            chunks_created=0,
            themes_detected=[],
            errors=[f"Failed to read file: {e}"],
        )
    except UnicodeDecodeError as e:
        logger.error("Encoding error reading %s: %s", file_path, e)
        return IngestionResult(
            source_file=str(file_path),
            chunks_created=0,
            themes_detected=[],
            errors=[f"Encoding error: {e}"],
        )

    if not text.strip():
        return IngestionResult(
            source_file=str(file_path),
            chunks_created=0,
            themes_detected=[],
            errors=["File is empty"],
        )

    metadata, body = parse_frontmatter(text)
    source = metadata.get("source", file_path.stem)
    author = metadata.get("author", "Unknown")
    date = metadata.get("date", "")
    themes_meta = metadata.get("themes", [])
    if isinstance(themes_meta, str):
        themes_meta = [themes_meta]

    chunks_text = chunk_text(body)
    chunks: list[KnowledgeChunk] = []

    for ct in chunks_text:
        if len(ct.split()) < 10:
            continue

        chunk_themes = themes_meta if themes_meta else classify_themes(ct)
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

    logger.info(
        "Ingested %s: %d chunks, themes=%s",
        file_path.name, len(chunks), sorted(all_themes),
    )

    return IngestionResult(
        source_file=str(file_path),
        chunks_created=len(chunks),
        themes_detected=sorted(all_themes),
        errors=errors,
        chunks=chunks,
    )


def ingest_directory(dir_path: Path) -> list[IngestionResult]:
    """Ingest all Markdown files in a directory (recursive).

    Args:
        dir_path: Path to directory containing .md files.

    Returns:
        List of IngestionResult, one per file processed.

    Raises:
        ValueError: If dir_path does not exist or is not a directory.
    """
    if not dir_path.exists():
        raise ValueError(f"Directory does not exist: {dir_path}")
    if not dir_path.is_dir():
        raise ValueError(f"Path is not a directory: {dir_path}")

    results: list[IngestionResult] = []
    for md_file in sorted(dir_path.glob("**/*.md")):
        result = ingest_markdown(md_file)
        results.append(result)

    logger.info(
        "Directory ingestion complete: %d files, %d total chunks",
        len(results), sum(r.chunks_created for r in results),
    )

    return results
