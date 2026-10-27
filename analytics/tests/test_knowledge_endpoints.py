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
    # Loopback base_url: the F-01 guard refuses non-loopback Hosts.
    with TestClient(app, base_url="http://127.0.0.1:3002") as test_client:
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


# --- Phase 3D (W4): path resolution, empty state, cleartext signal ---


def test_missing_database_is_empty_state_not_500(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fresh PAE_HOME has no pke.db — and before 3D, not even its
    parent directory. The endpoints must answer the empty state."""
    from pae import server

    missing = tmp_path / "fresh-home" / "data" / "pke.db"
    monkeypatch.setattr(server, "PKE_PATH", str(missing))

    resp = client.get("/api/v1/knowledge/chunks")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 0
    assert body["chunks"] == []
    assert body["cleartext_chunks"] == 0
    # A read must not create the file as a side effect.
    assert not missing.exists()

    resp = client.get("/api/v1/knowledge/search", params={"q": "anything"})
    assert resp.status_code == 200
    assert resp.json()["results"] == []


def test_cleartext_chunks_reported_for_cleartext_store(client: TestClient) -> None:
    """The module fixture seeds a cleartext store with 2 chunks; the
    response must say so, so the dashboard can label it."""
    resp = client.get("/api/v1/knowledge/chunks")
    assert resp.status_code == 200
    assert resp.json()["cleartext_chunks"] == 2


def test_cleartext_chunks_zero_for_encrypted_store(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pae import server

    pke_path = tmp_path / "pke-encrypted2.db"
    _seed(pke_path, dek=DEK)
    monkeypatch.setattr(server, "PKE_PATH", str(pke_path))

    resp = client.get("/api/v1/knowledge/chunks")
    assert resp.status_code == 200
    assert resp.json()["cleartext_chunks"] == 0


def test_default_store_path_prefers_pae_home(monkeypatch: pytest.MonkeyPatch) -> None:
    from pae.pke.store import default_store_path

    monkeypatch.setenv("PAE_HOME", "/some/pae-home")
    assert default_store_path() == Path("/some/pae-home/data/pke.db")
    monkeypatch.delenv("PAE_HOME")
    assert default_store_path() == Path.home() / ".local" / "share" / "pae" / "pke.db"


def test_store_creates_missing_parent_directory(tmp_path: Path) -> None:
    deep = tmp_path / "fresh" / "data" / "pke.db"
    store = KnowledgeStore(deep)
    try:
        assert store.count_chunks() == 0
    finally:
        store.close()
    assert deep.exists()
