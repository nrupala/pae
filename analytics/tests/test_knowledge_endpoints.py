# Copyright (C) 2026 2351485 ALBERTA INC.
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for the Phase 3B knowledge-base read endpoints.

- GET /api/v1/knowledge/chunks (list, newest first)
- GET /api/v1/knowledge/search (keyword search)

The PKE store shipped as a library with no HTTP surface; these
endpoints are the thin read layer the dashboard's Knowledge view
calls. Seeding goes through the library directly (ingest stays a
library operation — there is deliberately no write endpoint).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pae.pke.ingest import KnowledgeChunk, generate_chunk_id
from pae.pke.store import KnowledgeStore

DEK = b"\x07" * 32


def _chunk(source: str, text: str, themes: list[str]) -> KnowledgeChunk:
    return KnowledgeChunk(
        chunk_id=generate_chunk_id(source, text),
        source=source,
        author="Test Author",
        date="2026-10-01",
        themes=themes,
        text=text,
    )


def _seed(path: Path, dek: bytes | None = None) -> None:
    store = KnowledgeStore(path, dek=dek)
    try:
        store.add_chunks(
            [
                _chunk(
                    "notes/risk.md",
                    "Position sizing keeps any single loss small enough "
                    "to survive being wrong several times in a row.",
                    ["risk"],
                ),
                _chunk(
                    "notes/tax.md",
                    "Harvest capital losses before year end to offset "
                    "realized gains in the taxable account.",
                    ["tax"],
                ),
            ],
            embed=False,
        )
    finally:
        store.close()


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    db_path = tmp_path_factory.mktemp("pae-kb") / "pae.db"
    os.environ["PAE_DB_PATH"] = str(db_path)
    from pae import server
    from pae.server import app

    pke_path = tmp_path_factory.mktemp("pke") / "pke.db"
    _seed(pke_path)
    old = server.PKE_PATH
    server.PKE_PATH = str(pke_path)
    with TestClient(app) as test_client:
        yield test_client
    server.PKE_PATH = old


def test_list_chunks_returns_seeded_notes(client: TestClient) -> None:
    resp = client.get("/api/v1/knowledge/chunks")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert len(body["chunks"]) == 2
    for chunk in body["chunks"]:
        assert chunk["text"]  # cleartext store: text is readable
        assert chunk["encrypted"] is False
        assert chunk["source"].startswith("notes/")
        assert chunk["themes"]


def test_search_finds_matching_note(client: TestClient) -> None:
    resp = client.get("/api/v1/knowledge/search", params={"q": "capital losses"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["query"] == "capital losses"
    assert len(body["results"]) == 1
    hit = body["results"][0]
    assert hit["source"] == "notes/tax.md"
    assert "Harvest capital losses" in hit["text"]
    assert hit["score"] > 0


def test_search_no_match_is_empty_not_error(client: TestClient) -> None:
    resp = client.get("/api/v1/knowledge/search", params={"q": "zanzibar quilting"})
    assert resp.status_code == 200
    assert resp.json()["results"] == []


def test_encrypted_base_lists_metadata_but_locks_text(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pae import server

    pke_path = tmp_path / "pke-encrypted.db"
    _seed(pke_path, dek=DEK)
    monkeypatch.setattr(server, "PKE_PATH", str(pke_path))

    resp = client.get("/api/v1/knowledge/chunks")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    for chunk in body["chunks"]:
        assert chunk["encrypted"] is True
        assert chunk["text"] is None
        assert chunk["source"].startswith("notes/")  # metadata stays listable

    # Keyword search needs chunk text to rank: locked, not a 500.
    resp = client.get("/api/v1/knowledge/search", params={"q": "losses"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["results"] == []
    assert body["locked"] is True
