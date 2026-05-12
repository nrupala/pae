"""PKE Document Ingestion Pipeline.

Processes Markdown files with YAML frontmatter into the local knowledge base.
Chunks text, generates embeddings locally, and stores in an encrypted vector store.

All processing runs locally. No data leaves the user's machine.

Usage:
    python -m pae.pke.ingest knowledge/content/
"""

import hashlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class DocumentChunk:
    """A chunk of a knowledge document, ready for embedding."""
    chunk_id: str
    source: str
    author: str
    date: str
    themes: list[str]
    text: str
    char_offset: int
    char_length: int


@dataclass
class FrontmatterData:
    """Parsed YAML frontmatter from a knowledge document."""
    source: str = ""
    author: str = ""
    date: str = ""
    themes: list[str] = field(default_factory=list)
    notes: str = ""


@dataclass
class IngestResult:
    """Summary of an ingestion run."""
    files_processed: int = 0
    chunks_created: int = 0
    files_skipped: int = 0
    errors: list[str] = field(default_factory=list)


def parse_frontmatter(text: str) -> tuple[FrontmatterData, str]:
    """Extract YAML frontmatter and body from a Markdown file.

    Expects frontmatter delimited by --- lines at the top of the file.
    Returns (frontmatter_data, body_text).
    """
    frontmatter = FrontmatterData()
    body = text

    match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", text, re.DOTALL)
    if not match:
        return frontmatter, body

    yaml_block = match.group(1)
    body = match.group(2)

    # Simple YAML parsing (avoids PyYAML dependency for basic use)
    for line in yaml_block.split("\n"):
        line = line.strip()
        if line.startswith("source:"):
            frontmatter.source = _extract_yaml_string(line, "source:")
        elif line.startswith("author:"):
            frontmatter.author = _extract_yaml_string(line, "author:")
        elif line.startswith("date:"):
            frontmatter.date = _extract_yaml_string(line, "date:")
        elif line.startswith("notes:"):
            frontmatter.notes = _extract_yaml_string(line, "notes:")
        elif line.startswith("themes:"):
            themes_str = line.replace("themes:", "").strip()
            if themes_str.startswith("[") and themes_str.endswith("]"):
                frontmatter.themes = [
                    t.strip().strip("'").strip('"')
                    for t in themes_str[1:-1].split(",")
                    if t.strip()
                ]

    return frontmatter, body


def _extract_yaml_string(line: str, prefix: str) -> str:
    """Extract a string value from a simple YAML key: value line."""
    value = line.replace(prefix, "", 1).strip()
    return value.strip("'").strip('"')


def chunk_text(
    text: str,
    chunk_size: int = 512,
    overlap: int = 64,
) -> list[tuple[str, int]]:
    """Split text into overlapping chunks for embedding.

    Uses paragraph boundaries when possible, falling back to
    sentence boundaries, then hard character splits.

    Returns list of (chunk_text, char_offset) tuples.
    """
    if len(text) <= chunk_size:
        return [(text, 0)]

    chunks: list[tuple[str, int]] = []
    paragraphs = text.split("\n\n")
    current_chunk = ""
    current_offset = 0
    running_offset = 0

    for para in paragraphs:
        para = para.strip()
        if not para:
            running_offset += 2  # account for \n\n
            continue

        if len(current_chunk) + len(para) + 2 <= chunk_size:
            if current_chunk:
                current_chunk += "\n\n" + para
            else:
                current_chunk = para
                current_offset = running_offset
        else:
            if current_chunk:
                chunks.append((current_chunk, current_offset))
            # Start new chunk with overlap from previous
            if chunks and overlap > 0:
                prev_text = chunks[-1][0]
                overlap_text = prev_text[-overlap:] if len(prev_text) > overlap else prev_text
                current_chunk = overlap_text + "\n\n" + para
            else:
                current_chunk = para
            current_offset = running_offset

        running_offset += len(para) + 2

    if current_chunk:
        chunks.append((current_chunk, current_offset))

    return chunks


def generate_chunk_id(source: str, offset: int) -> str:
    """Generate a deterministic chunk ID from source and offset."""
    raw = f"{source}:{offset}"
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


def ingest_file(filepath: Path) -> list[DocumentChunk]:
    """Ingest a single Markdown file into document chunks.

    Parses frontmatter, chunks the body text, and returns
    DocumentChunk objects ready for embedding.
    """
    text = filepath.read_text(encoding="utf-8")
    frontmatter, body = parse_frontmatter(text)

    source = frontmatter.source or filepath.stem
    raw_chunks = chunk_text(body)

    chunks = []
    for chunk_text_str, offset in raw_chunks:
        chunk = DocumentChunk(
            chunk_id=generate_chunk_id(source, offset),
            source=source,
            author=frontmatter.author,
            date=frontmatter.date,
            themes=frontmatter.themes,
            text=chunk_text_str,
            char_offset=offset,
            char_length=len(chunk_text_str),
        )
        chunks.append(chunk)

    return chunks


def ingest_directory(content_dir: str | Path) -> IngestResult:
    """Ingest all Markdown files in a directory.

    Scans for .md files, parses each, and returns an IngestResult
    summarizing what was processed. Does not yet write to the vector
    store (that integration is planned for Phase 2).
    """
    content_path = Path(content_dir)
    result = IngestResult()

    if not content_path.is_dir():
        result.errors.append(f"Directory not found: {content_dir}")
        return result

    md_files = sorted(content_path.glob("*.md"))

    for filepath in md_files:
        try:
            chunks = ingest_file(filepath)
            result.files_processed += 1
            result.chunks_created += len(chunks)
        except Exception as e:
            result.files_skipped += 1
            result.errors.append(f"{filepath.name}: {e}")

    return result


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python -m pae.pke.ingest <content_directory>")
        print("Example: python -m pae.pke.ingest knowledge/content/")
        sys.exit(1)

    directory = sys.argv[1]
    print(f"Ingesting documents from: {directory}")

    result = ingest_directory(directory)

    print(f"Files processed: {result.files_processed}")
    print(f"Chunks created:  {result.chunks_created}")
    print(f"Files skipped:   {result.files_skipped}")
    if result.errors:
        print("Errors:")
        for err in result.errors:
            print(f"  - {err}")
