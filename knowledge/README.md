# PAE Personal Knowledge Engine - Content Directory

This directory contains templates and scripts for the Personal Knowledge Engine (PKE).

## How to Add Knowledge

1. Export your source material to Markdown (from OneNote, PDF, HTML, or plain text).
2. Add YAML frontmatter using the template in `templates/frontmatter.yaml`.
3. Place the file in `knowledge/content/` (this directory is gitignored -- your knowledge stays private).
4. Run the ingestion pipeline: `python -m pae.pke.ingest knowledge/content/`

## Directory Structure

```
knowledge/
|-- templates/
|   +-- frontmatter.yaml    # YAML template for document metadata
|-- scripts/
|   +-- (coming: onenote_export.py, pdf_to_md.py, bulk_ingest.py)
|-- content/                 # YOUR knowledge files (gitignored, never committed)
+-- README.md
```

## Content is Private

The `content/` directory is in `.gitignore`. Your Buffett letters, coursework notes, book highlights -- none of it is ever committed to the repository or transmitted to any server.

All PKE processing runs locally. Embeddings are generated on your machine. The vector store is encrypted with your KEK.
