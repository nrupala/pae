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
    chunk_id: str
    source: str
    author: str
    date: str
    themes: list[str]
    text: str
    embedding: list[float] = field(default_factory=list)


@dataclass
class IngestionResult:
    source_file: str
    chunks_created: int
    themes_detected: list[str]
    errors: list[str]


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


def chunk_text(text: str, max_tokens: int = 400, overlap: int = 50) -> list[str]:
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
    themes = metadata.get("themes", ["general"])
    if isinstance(themes, str):
        themes = [themes]

    chunks_text = chunk_text(body)
    chunks: list[KnowledgeChunk] = []

    for ct in chunks_text:
        if len(ct.split()) < 10:
            continue

        chunk = KnowledgeChunk(
            chunk_id=generate_chunk_id(source, ct),
            source=source,
            author=author,
            date=date,
            themes=themes,
            text=ct,
        )
        chunks.append(chunk)

    return IngestionResult(
        source_file=str(file_path),
        chunks_created=len(chunks),
        themes_detected=list(set(themes)),
        errors=errors,
    )


def ingest_directory(dir_path: Path) -> list[IngestionResult]:
    """Ingest all Markdown files in a directory."""
    results = []
    for md_file in sorted(dir_path.glob("**/*.md")):
        result = ingest_markdown(md_file)
        results.append(result)
    return results
