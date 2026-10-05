"""Tests for PAE's agent-facing REST surfaces.

- GET /.well-known/agent.json (A2A agent card)
- POST /api/v1/a2a/message/send (A2A-compatible message endpoint)
- GET /.well-known/pae.json (discovery) + static repo-root copy
- GET /api/v1/tools (tool manifest, sourced from MCP definitions)
- pae.auth.api_key_or_none deferred seam
- repo-root llms.txt

All TestClient-based; no live engine needed.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[2]

EXPECTED_TOOLS = [
    "compute_risk",
    "monte_carlo",
    "stress_test",
    "factor_decompose",
    "brinson_attribution",
    "journal_log",
    "holdings_create",
    "holdings_list",
    "holdings_update",
    "holdings_delete",
    "dashboard_summary",
]

DISCLOSURE = (
    "Educational analytics only. PAE calculates; the user decides. "
    "No investment advice."
)


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    db_path = tmp_path_factory.mktemp("pae") / "pae.db"
    os.environ["PAE_DB_PATH"] = str(db_path)
    os.environ["PAE_ENGINE_URL"] = "http://127.0.0.1:9"  # unreachable, by design
    from pae.server import app

    with TestClient(app) as test_client:
        yield test_client


def _a2a_envelope(tool: str, params: dict[str, Any]) -> dict[str, Any]:
    return {
        "message": {
            "role": "user",
            "parts": [{"type": "data", "data": {"tool": tool, "params": params}}],
        }
    }


# --- Agent card ---


def test_agent_card_shape(client: TestClient) -> None:
    resp = client.get("/.well-known/agent.json")
    assert resp.status_code == 200
    card = resp.json()

    assert card["name"] == "PAE"
    assert card["version"] == "0.1.0"
    assert card["provider"]["organization"] == "AIMLDS"
    assert card["message_endpoint"] == "/api/v1/a2a/message/send"
    assert DISCLOSURE in card["description"]

    skills = card["skills"]
    assert [s["id"] for s in skills] == EXPECTED_TOOLS
    for skill in skills:
        assert skill["name"] == skill["id"]
        assert DISCLOSURE in skill["description"]


# --- A2A message/send ---

_BRINSON_REST_PAYLOAD = {
    "portfolio_segments": [
        {"segment": "Equities", "weight": 0.50, "segment_return": 0.08},
        {"segment": "Bonds", "weight": 0.30, "segment_return": 0.04},
        {"segment": "Cash", "weight": 0.20, "segment_return": 0.02},
    ],
    "benchmark_segments": [
        {"segment": "Equities", "weight": 0.60, "segment_return": 0.10},
        {"segment": "Bonds", "weight": 0.30, "segment_return": 0.03},
        {"segment": "Cash", "weight": 0.10, "segment_return": 0.02},
    ],
}


def test_rest_attribution_endpoint(client: TestClient) -> None:
    """POST /api/v1/analytics/attribution: hand-computed values, disclosure."""
    resp = client.post("/api/v1/analytics/attribution", json=_BRINSON_REST_PAYLOAD)
    assert resp.status_code == 200
    body = resp.json()

    assert DISCLOSURE in body["disclosure"]
    assert body["method"].startswith("Brinson-Hood-Beebower")
    assert body["active_return"] == pytest.approx(-0.015)
    effects_sum = (
        body["total_allocation"] + body["total_selection"] + body["total_interaction"]
    )
    assert effects_sum == pytest.approx(body["active_return"])

    segs = {s["segment"]: s for s in body["segments"]}
    eq = segs["Equities"]
    assert eq["allocation_effect"] == pytest.approx(-0.0029)
    assert eq["selection_effect"] == pytest.approx(-0.0120)
    assert eq["interaction_effect"] == pytest.approx(0.0020)
    # Pure performance explanation: no recommendations in the response.
    assert "recommendation" not in " ".join(body.keys()).lower()


def test_rest_attribution_rejects_bad_input(client: TestClient) -> None:
    """Weights not summing to 1.0 -> 422, not a traceback."""
    bad = {
        "portfolio_segments": [
            {"segment": "Equities", "weight": 0.50, "segment_return": 0.08}
        ],
        "benchmark_segments": [
            {"segment": "Equities", "weight": 0.60, "segment_return": 0.10},
            {"segment": "Cash", "weight": 0.40, "segment_return": 0.02},
        ],
    }
    resp = client.post("/api/v1/analytics/attribution", json=bad)
    assert resp.status_code == 422
    assert "weights sum to" in resp.json()["detail"]


def test_a2a_roundtrip_factor_decompose(client: TestClient) -> None:
    rng = np.random.default_rng(11)
    params = {
        "portfolio_returns": [float(x) for x in rng.normal(0.008, 0.04, 60)],
        "factor_returns": {
            name: [float(x) for x in rng.normal(0.0, 0.03, 60)]
            for name in ("MKT-RF", "SMB", "HML", "RMW", "CMA")
        },
    }
    resp = client.post(
        "/api/v1/a2a/message/send", json=_a2a_envelope("factor_decompose", params)
    )
    assert resp.status_code == 200
    body = resp.json()

    task = body["task"]
    assert task["id"]
    assert task["status"]["state"] == "completed"

    artifact = task["artifacts"][0]
    assert artifact["name"] == "factor_decompose"
    data = artifact["parts"][0]
    assert data["type"] == "data"
    outcome = data["data"]
    assert outcome["ok"] is True
    assert "alpha" in outcome["result"]
    assert len(outcome["result"]["exposures"]) == 5


def test_a2a_roundtrip_brinson_attribution(client: TestClient) -> None:
    params = {
        "portfolio_segments": [
            {"segment": "Equities", "weight": 0.50, "return": 0.08},
            {"segment": "Bonds", "weight": 0.30, "return": 0.04},
            {"segment": "Cash", "weight": 0.20, "return": 0.02},
        ],
        "benchmark_segments": [
            {"segment": "Equities", "weight": 0.60, "return": 0.10},
            {"segment": "Bonds", "weight": 0.30, "return": 0.03},
            {"segment": "Cash", "weight": 0.10, "return": 0.02},
        ],
    }
    resp = client.post(
        "/api/v1/a2a/message/send", json=_a2a_envelope("brinson_attribution", params)
    )
    assert resp.status_code == 200
    body = resp.json()

    task = body["task"]
    assert task["status"]["state"] == "completed"
    outcome = task["artifacts"][0]["parts"][0]["data"]
    assert outcome["ok"] is True
    result = outcome["result"]
    assert result["active_return"] == pytest.approx(-0.015)
    assert result["disclosure"] == DISCLOSURE
    segs = {s["segment"]: s for s in result["segments"]}
    assert segs["Cash"]["allocation_effect"] == pytest.approx(-0.0051)


def test_a2a_roundtrip_journal_log(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/a2a/message/send",
        json=_a2a_envelope(
            "journal_log",
            {"action": "A2A probe", "confidence": 7, "emotional_state": "calm"},
        ),
    )
    body = resp.json()
    assert body["task"]["status"]["state"] == "completed"
    outcome = body["task"]["artifacts"][0]["parts"][0]["data"]
    assert outcome["ok"] is True
    assert outcome["result"]["entry_id"]


def test_a2a_unknown_tool_is_failed_not_traceback(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/a2a/message/send", json=_a2a_envelope("delete_everything", {})
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["task"]["status"]["state"] == "failed"
    outcome = body["task"]["artifacts"][0]["parts"][0]["data"]
    assert outcome["ok"] is False
    assert "Unknown tool" in outcome["error"]
    assert "Traceback" not in resp.text


def test_a2a_malformed_message_is_failed_not_traceback(client: TestClient) -> None:
    for bad in ({}, {"message": {}}, {"message": {"parts": []}},
                {"message": {"parts": [{"type": "text", "text": "hi"}]}}):
        resp = client.post("/api/v1/a2a/message/send", json=bad)
        assert resp.status_code == 200, bad
        body = resp.json()
        assert body["task"]["status"]["state"] == "failed", bad
        assert "Traceback" not in resp.text


def test_a2a_tool_error_is_failed_not_traceback(client: TestClient) -> None:
    """compute_risk with no live engine -> failed task, clean error."""
    resp = client.post(
        "/api/v1/a2a/message/send",
        json=_a2a_envelope("compute_risk", {"portfolio_id": "nope"}),
    )
    body = resp.json()
    assert body["task"]["status"]["state"] == "failed"
    outcome = body["task"]["artifacts"][0]["parts"][0]["data"]
    assert outcome["ok"] is False
    assert "Traceback" not in resp.text


# --- Discovery & manifest ---


def test_pae_discovery_document(client: TestClient) -> None:
    resp = client.get("/.well-known/pae.json")
    assert resp.status_code == 200
    doc = resp.json()
    assert doc["name"] == "PAE"
    assert doc["version"] == "0.1.0"
    assert doc["disclosure"] == DISCLOSURE
    assert set(doc["surfaces"]) == {"rest", "mcp", "a2a"}
    assert doc["links"]["agent_card"] == "/.well-known/agent.json"
    assert doc["links"]["tools_manifest"] == "/api/v1/tools"
    assert doc["links"]["llms_txt"] == "/llms.txt"


def test_static_pae_json_matches_served(client: TestClient) -> None:
    static_path = REPO_ROOT / ".well-known" / "pae.json"
    assert static_path.exists(), "repo-root .well-known/pae.json missing"
    static_doc = json.loads(static_path.read_text())
    served_doc = client.get("/.well-known/pae.json").json()
    assert static_doc == served_doc


def test_tools_manifest_from_mcp_definitions(client: TestClient) -> None:
    resp = client.get("/api/v1/tools")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == len(EXPECTED_TOOLS)
    tools = body["tools"]
    assert [t["name"] for t in tools] == EXPECTED_TOOLS
    for tool in tools:
        assert DISCLOSURE in tool["description"]
        schema = tool["input_schema"]
        assert schema["type"] == "object"
        assert isinstance(schema["properties"], dict)


def test_manifest_schema_spot_check(client: TestClient) -> None:
    tools = {t["name"]: t for t in client.get("/api/v1/tools").json()["tools"]}
    fd = tools["factor_decompose"]["input_schema"]["properties"]
    assert "portfolio_returns" in fd
    assert "factor_returns" in fd
    mc = tools["monte_carlo"]["input_schema"]["properties"]
    assert mc["num_simulations"].get("default", 10000) == 10000


# --- Auth seam ---


def test_auth_seam_is_noop_passthrough() -> None:
    from pae.auth import api_key_or_none

    assert asyncio.run(api_key_or_none()) is None


def test_agent_routes_require_no_credentials(client: TestClient) -> None:
    """Deferred auth = no behavior change: routes work with no credentials."""
    assert client.get("/.well-known/agent.json").status_code == 200
    assert client.get("/.well-known/pae.json").status_code == 200
    assert client.get("/api/v1/tools").status_code == 200


# --- llms.txt ---


def test_llms_txt_at_repo_root() -> None:
    llms = REPO_ROOT / "llms.txt"
    assert llms.exists(), "repo-root llms.txt missing"
    text = llms.read_text()
    flat = " ".join(line.lstrip("> ").strip() for line in text.splitlines())
    flat = " ".join(flat.split())
    assert "not a financial advisor" in flat
    assert "The tool calculates; the user decides" in flat
    assert "/.well-known/agent.json" in text
    assert "/api/v1/a2a/message/send" in text
    assert "python -m pae.mcp" in text
    for tool in EXPECTED_TOOLS:
        assert f"`{tool}`" in text, f"llms.txt missing tool {tool}"
    assert "deferred" in text.lower()
