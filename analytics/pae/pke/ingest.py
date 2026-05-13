"""PKE Ingestion Pipeline.

Parses documents (PDF, HTML, Markdown, plain text), chunks into semantic
passages, classifies by theme, generates embeddings, and stores encrypted
in the local vector store.

All processing runs locally. No content ever leaves the user's machine.
"""

from dataclasses import dataclass, field
from pathlib import Path
import hashlib
import re


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
    """A single passage from the user's knowledge base."""

    chunk_id: str
    source: str
    author: str
    date: str
    themes: list[str]
    text: str
    embedding: list[float] = field(default_factory=list)


@dataclass
class IngestionResult:
    """Result of ingesting a document."""

    source_file: str
    chunks_created: int
    themes_detected: list[str]
    errors: list[str]
    chunks: list[KnowledgeChunk] = field(default_factory=list)


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Extract YAML frontmatter from a Markdown document."""
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
                value = [v.strip().strip('"').strip("'") for v in value[1:-1].split(",")]
            metadata[key] = value

    return metadata, body


def chunk_text(text: str, max_tokens: int = 400) -> list[str]:
    """Split text into semantic chunks at paragraph boundaries."""
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
    """Deterministic chunk ID from source + text content."""
    content = f"{source}:{text[:200]}"
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def classify_themes(text: str) -> list[str]:
    """Auto-classify a chunk into themes based on keyword matching.

    Stub implementation. Full version uses a local zero-shot classifier.
    """
    text_lower = text.lower()
    detected = []
    keyword_map = {
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
    """Ingest a Markdown file into knowledge chunks."""
    errors: list[str] = []

    try:
        text = file_path.read_text(encoding="utf-8")
    except Exception as e:
        return IngestionResult(
            source_file=str(file_path),
            chunks_created=0,
            themes_detected=[],
            errors=[f"Failed to read file: {e}"],
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
        if len(ct.split()) < 10:
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

    all_themes = set()
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
    """Ingest all Markdown files in a directory."""
    results = []
    for md_file in sorted(dir_path.glob("**/*.md")):
        result = ingest_markdown(md_file)
        results.append(result)
    return results
