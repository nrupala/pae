#!/usr/bin/env python3
# Copyright (C) 2026 2351485 ALBERTA INC.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Seed a PAE install with demo data (fictional, clearly labelled).

Two independent halves:

1. Engine version store (always): appends demo records through the
   engine's own API (POST /api/v1/version) — a vault manifest, a
   holdings snapshot in two versions, and a decision-journal entry —
   then verifies them through POST /api/v1/version/snapshot.

   Content note: real PAE clients encrypt content client-side
   (AES-256-GCM) before appending, and the server only ever stores
   opaque bytes. This seed stands in for that step with base64-encoded
   JSON and a zero nonce so a first-run user can inspect exactly what
   was seeded. It is demo data in a demo vault, not a pattern for real
   data — never seed real holdings this way.

   The version store is append-only, so re-running this script adds
   new versions of the same demo entities rather than duplicating
   them in snapshots (a snapshot always shows the latest version).

2. Analytics demo portfolio (only when PAE_ANALYTICS_URL is set):
   creates a "Demo Portfolio" with sample holdings through the
   analytics API so the dashboard opens on populated data. Skipped if
   a portfolio with that name already exists.

Environment:
    PAE_ENGINE_URL     default http://localhost:3001
    PAE_ANALYTICS_URL  unset = skip the analytics half

PAE is an analytics tool: it computes and displays. Nothing in this
seed is a recommendation, and every figure in it is fictional.
"""

from __future__ import annotations

import base64
import json
import os
import random
import sys
import time
import urllib.error
import urllib.request

ENGINE_URL = os.environ.get("PAE_ENGINE_URL", "http://localhost:3001").rstrip("/")
ANALYTICS_URL = os.environ.get("PAE_ANALYTICS_URL", "").rstrip("/")

# 12 zero bytes: the AES-256-GCM nonce size. A placeholder only — demo
# content below is base64(JSON), not ciphertext (see module docstring).
DEMO_NONCE_B64 = base64.b64encode(b"\x00" * 12).decode()

# Fictional sample positions. Tickers are invented on purpose.
# yield_pct is set on every position so the Carry view shows real
# income numbers on the demo (before Phase 3D the seed left yields
# unset and Carry showed $0 everywhere).
SAMPLE_POSITIONS_V1 = [
    {"symbol": "CASH-CAD", "name": "Sample cash (CAD)", "asset_class": "cash",
     "quantity": 5000.0, "market_value": 5000.0, "cost_basis": 5000.0,
     "currency": "CAD", "yield_pct": 2.1},
    {"symbol": "DEMO-EQ", "name": "Sample equity fund", "asset_class": "equity",
     "quantity": 120.0, "market_value": 9840.0, "cost_basis": 9000.0,
     "currency": "CAD", "yield_pct": 1.6},
    {"symbol": "DEMO-BD", "name": "Sample bond fund", "asset_class": "fixed_income",
     "quantity": 200.0, "market_value": 10150.0, "cost_basis": 10000.0,
     "currency": "CAD", "yield_pct": 3.4},
]
# Version 2: the sample cash position moved — gives history/diff content.
SAMPLE_POSITIONS_V2 = [
    dict(p, quantity=4500.0, market_value=4500.0) if p["symbol"] == "CASH-CAD"
    else dict(p, quantity=130.0, market_value=10660.0) if p["symbol"] == "DEMO-EQ"
    else dict(p)
    for p in SAMPLE_POSITIONS_V1
]

# Monthly return series so the analytics views have something real
# to compute over: 36 periods per holding (the factor decomposition
# needs >= 12 overlapping periods; the 6-period series seeded before
# Phase 3D made Factors refuse and the risk/optimizer outputs
# degenerate). Deterministic — a fixed-seed generator with plausible
# distinct drift/volatility per holding — so every seeded install
# shows the same fictional dataset and the seed stays reproducible.
# Fictional, illustrative only.
_SAMPLE_SERIES_PARAMS = {
    # symbol: (monthly drift, monthly volatility)
    "CASH-CAD": (0.0015, 0.0015),
    "DEMO-EQ": (0.0075, 0.042),
    "DEMO-BD": (0.0028, 0.009),
}
_SAMPLE_PERIODS = 36


def _synthetic_returns() -> dict[str, list[float]]:
    rng = random.Random(20261026)
    return {
        symbol: [round(rng.gauss(mu, sigma), 6) for _ in range(_SAMPLE_PERIODS)]
        for symbol, (mu, sigma) in _SAMPLE_SERIES_PARAMS.items()
    }


SAMPLE_RETURNS = _synthetic_returns()


def _request(method: str, url: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


def wait_healthy(base_url: str, timeout_s: float = 90.0) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            body = _request("GET", f"{base_url}/health")
            print(f"  healthy: {base_url}/health -> {json.dumps(body)[:120]}")
            return
        except (urllib.error.URLError, OSError, json.JSONDecodeError):
            time.sleep(2)
    raise SystemExit(f"Timed out waiting for {base_url}/health")


def append_record(entity_id: str, entity_type: str, content: dict,
                  change_summary: str, tags: list[str]) -> dict:
    payload = {
        "entity_id": entity_id,
        "entity_type": entity_type,
        "content_encrypted_b64": base64.b64encode(
            json.dumps(content).encode()
        ).decode(),
        "nonce_b64": DEMO_NONCE_B64,
        "change_summary": change_summary,
        "tags": tags,
    }
    return _request("POST", f"{ENGINE_URL}/api/v1/version", payload)


def seed_engine() -> None:
    print(f"Seeding engine version store at {ENGINE_URL} ...")
    wait_healthy(ENGINE_URL)

    r = append_record(
        "demo-vault", "configuration",
        {"kind": "vault_manifest", "name": "Demo Vault", "demo": True,
         "note": "Seeded sample data for first-run exploration. "
                 "All figures are fictional."},
        "Demo seed: vault manifest", ["demo", "seed"],
    )
    print(f"  demo-vault configuration v{r['version']} ({r['version_hash'][:12]}...)")

    holdings_doc = {
        "kind": "holdings_snapshot", "portfolio": "Demo Portfolio",
        "currency": "CAD", "demo": True, "positions": SAMPLE_POSITIONS_V1,
    }
    r = append_record("demo-portfolio", "holdings", holdings_doc,
                      "Demo seed: initial sample positions", ["demo", "seed"])
    print(f"  demo-portfolio holdings v{r['version']} ({r['version_hash'][:12]}...)")

    holdings_doc_v2 = dict(holdings_doc, positions=SAMPLE_POSITIONS_V2)
    r = append_record("demo-portfolio", "holdings", holdings_doc_v2,
                      "Demo seed: sample position quantities updated",
                      ["demo", "seed"])
    print(f"  demo-portfolio holdings v{r['version']} ({r['version_hash'][:12]}...)")

    r = append_record(
        "demo-decision-001", "decision_entry",
        {"kind": "decision_entry", "demo": True,
         "title": "Sample decision journal entry",
         "rationale": "Placeholder entry showing the journal's shape: "
                      "the reasoning is recorded before the action, in "
                      "the author's own words.",
         "confidence": 3, "emotional_state": "calm"},
        "Demo seed: sample decision journal entry", ["demo", "seed"],
    )
    print(f"  demo-decision-001 decision_entry v{r['version']} "
          f"({r['version_hash'][:12]}...)")

    snapshot = _request(
        "POST", f"{ENGINE_URL}/api/v1/version/snapshot",
        {"entity_types": [], "entity_ids": []},
    )
    found = {e["entity_id"]: e for e in snapshot["entities"]}
    expected = {"demo-vault", "demo-portfolio", "demo-decision-001"}
    missing = expected - found.keys()
    if missing:
        raise SystemExit(f"Snapshot verification failed, missing: {missing}")
    print(f"  snapshot verified at {snapshot['as_of']}: "
          + ", ".join(f"{k}=v{found[k]['version']}" for k in sorted(expected)))

    integrity = _request(
        "GET", f"{ENGINE_URL}/api/v1/version/integrity/demo-portfolio")
    print(f"  integrity demo-portfolio: chain_valid={integrity['chain_valid']} "
          f"over {integrity['total_versions']} versions")


def seed_analytics() -> None:
    print(f"Seeding analytics demo portfolio at {ANALYTICS_URL} ...")
    wait_healthy(ANALYTICS_URL)

    listing = _request("GET", f"{ANALYTICS_URL}/api/v1/portfolios")
    if any(p["name"] == "Demo Portfolio" for p in listing["portfolios"]):
        print("  'Demo Portfolio' already exists — skipping (idempotent).")
        return

    created = _request(
        "POST", f"{ANALYTICS_URL}/api/v1/portfolios",
        {"name": "Demo Portfolio",
         "description": "Seeded sample portfolio (fictional data) for "
                        "first-run exploration."},
    )
    portfolio_id = created["portfolio"]["id"]
    print(f"  created portfolio {portfolio_id}")

    for pos in SAMPLE_POSITIONS_V1:
        body = dict(pos)
        body["portfolio_id"] = portfolio_id
        body["returns"] = SAMPLE_RETURNS[pos["symbol"]]
        _request("POST", f"{ANALYTICS_URL}/api/v1/holdings", body)
        print(f"  holding {pos['symbol']} x{pos['quantity']}")


def main() -> int:
    seed_engine()
    if ANALYTICS_URL:
        seed_analytics()
    else:
        print("PAE_ANALYTICS_URL not set — analytics demo portfolio skipped.")
    print("Demo seed complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
