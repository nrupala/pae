# Copyright (C) 2026 2351485 ALBERTA INC.
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for the F-01 loopback guard (pae.server.LoopbackGuardMiddleware).

- DNS-rebinding shape: a non-loopback Host is refused 403 on any method.
- Cross-site shape: a mutating request whose Origin is present and not
  loopback is refused 403 before any route runs.
- Loopback Host spellings, loopback Origins, and absent Origins (curl,
  the MCP server, the engine's proxy) pass through unchanged.

The client uses a loopback base_url — TestClient's default
"testserver" host is, correctly, refused by the guard.
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from pae.server import (
    _is_loopback_authority,
    _origin_is_loopback,
)


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    db_path = tmp_path_factory.mktemp("pae-guard") / "pae.db"
    os.environ["PAE_DB_PATH"] = str(db_path)
    from pae.server import app

    with TestClient(app, base_url="http://127.0.0.1:3002") as test_client:
        yield test_client


# --- Parsing (unit level) ---


@pytest.mark.parametrize(
    "authority",
    [
        "127.0.0.1",
        "127.0.0.1:3002",
        "127.0.1.9:3002",
        "localhost",
        "localhost:3002",
        "LOCALHOST:3002",
        "[::1]",
        "[::1]:3002",
    ],
)
def test_loopback_authorities(authority: str) -> None:
    assert _is_loopback_authority(authority)


@pytest.mark.parametrize(
    "authority",
    [
        "evil.example",
        "evil.example:3002",
        "localhost.evil.example",
        "0.0.0.0:3002",
        "192.168.1.10:3002",
        "[2001:db8::1]:3002",
        "testserver",
        "",
    ],
)
def test_non_loopback_authorities(authority: str) -> None:
    assert not _is_loopback_authority(authority)


def test_origin_parsing() -> None:
    assert _origin_is_loopback("http://localhost:3002")
    assert _origin_is_loopback("http://127.0.0.1:3002")
    assert _origin_is_loopback("https://[::1]:8443")
    assert not _origin_is_loopback("https://evil.example")
    assert not _origin_is_loopback("http://localhost.evil.example")
    assert not _origin_is_loopback("null")


# --- Host guard (integration) ---


def test_non_loopback_host_rejected(client: TestClient) -> None:
    resp = client.get("/health", headers={"host": "evil.example"})
    assert resp.status_code == 403
    assert resp.json()["code"] == "NON_LOOPBACK_HOST"


@pytest.mark.parametrize("host", ["localhost:3002", "127.0.0.1:3002", "[::1]:3002"])
def test_loopback_host_spellings_pass(client: TestClient, host: str) -> None:
    resp = client.get("/health", headers={"host": host})
    assert resp.status_code == 200


def test_normal_request_passes(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200


# --- Origin guard (integration) ---


def test_cross_site_origin_rejected_on_mutation(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/portfolios",
        json={"name": "guard-cross-site"},
        headers={"origin": "https://evil.example"},
    )
    assert resp.status_code == 403
    assert resp.json()["code"] == "CROSS_SITE_ORIGIN"
    # The rejection happens before routing: nothing was created.
    listing = client.get("/api/v1/portfolios").json()
    names = [p["name"] for p in listing["portfolios"]]
    assert "guard-cross-site" not in names


def test_loopback_origin_passes_on_mutation(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/portfolios",
        json={"name": "guard-loopback-origin"},
        headers={"origin": "http://127.0.0.1:3002"},
    )
    assert resp.status_code == 201


def test_no_origin_passes_on_mutation(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/portfolios", json={"name": "guard-no-origin"}
    )
    assert resp.status_code == 201


def test_cross_site_origin_not_refused_on_safe_method(client: TestClient) -> None:
    # The Origin check applies to mutations only. A GET carrying a
    # foreign Origin is governed by CORS (which withholds the
    # Access-Control-Allow-Origin header), not refused outright.
    resp = client.get("/health", headers={"origin": "https://evil.example"})
    assert resp.status_code == 200
    assert "access-control-allow-origin" not in resp.headers
