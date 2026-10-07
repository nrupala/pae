# Copyright (C) 2026 Nrupal Akolkar
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for pae.models.optimize (long-only portfolio optimization).

- Two-asset analytical cross-checks: hand-computed max-Sharpe and
  min-variance weights (explicit 2x2 inverse in the test, independent of
  the implementation).
- Property checks: weights sum to 1, are non-negative; frontier endpoints
  match the min-variance and max-return mixes.
- Edge cases: singular covariance (regularization path), single holding,
  zero/negative expected returns, invalid inputs.
- REST (TestClient) and MCP (run_agent_tool) integration.
"""

from __future__ import annotations

import json
import math
import os
from typing import Any

import numpy as np
import pytest
from fastapi.testclient import TestClient

from pae.mcp.tools import TOOL_NAMES, run_agent_tool
from pae.models.optimize import (
    OptimizeError,
    holdings_to_inputs,
    optimize,
)

# --- Analytical cross-checks (hand-computed, no numpy) ---


def _hand_max_sharpe_2asset(mu: list[float], cov: list[list[float]], rf: float) -> list[float]:
    """Tangency weights via the explicit 2x2 inverse formula."""
    e = [mu[0] - rf, mu[1] - rf]
    a, b = cov[0][0], cov[0][1]
    c, d = cov[1][0], cov[1][1]
    det = a * d - b * c
    inv = [[d / det, -b / det], [-c / det, a / det]]
    raw = [inv[0][0] * e[0] + inv[0][1] * e[1], inv[1][0] * e[0] + inv[1][1] * e[1]]
    total = raw[0] + raw[1]
    return [raw[0] / total, raw[1] / total]


def _hand_min_variance_2asset(cov: list[list[float]]) -> list[float]:
    """Global min-variance weights via the textbook 2-asset formula."""
    v1, v12, v2 = cov[0][0], cov[0][1], cov[1][1]
    w1 = (v2 - v12) / (v1 + v2 - 2 * v12)
    return [w1, 1.0 - w1]


MU_2 = [0.10, 0.06]
COV_2 = [[0.04, 0.01], [0.01, 0.0225]]
RF_2 = 0.02


def test_max_sharpe_two_asset_matches_hand_computation() -> None:
    result = optimize(["AAA", "BBB"], MU_2, COV_2, risk_free_rate=RF_2)
    expected = _hand_max_sharpe_2asset(MU_2, COV_2, RF_2)
    assert result.max_sharpe.method == "closed-form"
    assert result.max_sharpe.weights == pytest.approx(expected, abs=1e-9)
    # Sanity: weights are the 1.75/2.75, 1.0/2.75 tangency mix.
    assert expected == pytest.approx([0.6363636364, 0.3636363636], abs=1e-9)


def test_min_variance_two_asset_matches_hand_computation() -> None:
    result = optimize(["AAA", "BBB"], MU_2, COV_2, risk_free_rate=RF_2)
    expected = _hand_min_variance_2asset(COV_2)
    assert result.min_variance.method == "closed-form"
    assert result.min_variance.weights == pytest.approx(expected, abs=1e-9)
    assert expected == pytest.approx([0.2941176471, 0.7058823529], abs=1e-9)


# --- Property checks ---


def _assert_valid_mix(weights: list[float]) -> None:
    assert all(w >= -1e-9 for w in weights), weights
    assert math.isclose(sum(weights), 1.0, abs_tol=1e-9), weights
    assert all(math.isfinite(w) for w in weights), weights


def _random_psd(rng: np.random.Generator, n: int) -> list[list[float]]:
    a = rng.normal(0, 0.2, size=(n, n))
    cov = a @ a.T + np.eye(n) * 0.005
    return cov.tolist()


def test_weights_sum_to_one_and_non_negative() -> None:
    rng = np.random.default_rng(7)
    for trial in range(6):
        n = int(rng.integers(2, 6))
        mu = (rng.normal(0.06, 0.05, size=n)).tolist()
        cov = _random_psd(rng, n)
        result = optimize(
            [f"S{i}" for i in range(n)],
            mu,
            cov,
            risk_free_rate=0.02,
            frontier_points=9,
        )
        for mix in (result.max_sharpe, result.min_variance, result.risk_parity):
            _assert_valid_mix(mix.weights)
            assert math.isfinite(mix.expected_return)
            assert math.isfinite(mix.volatility)
            assert math.isfinite(mix.sharpe_ratio)
        assert len(result.frontier) in (2, 9)  # 2 when min-var == max-return
        for pt in result.frontier:
            _assert_valid_mix(pt.weights)
        # Optimality properties on the computed mixes.
        assert result.max_sharpe.sharpe_ratio >= result.min_variance.sharpe_ratio - 1e-6
        assert result.max_sharpe.sharpe_ratio >= result.risk_parity.sharpe_ratio - 1e-6
        assert result.min_variance.volatility <= result.max_sharpe.volatility + 1e-9
        assert result.min_variance.volatility <= result.risk_parity.volatility + 1e-9


def test_frontier_endpoints_match_min_variance_and_max_return() -> None:
    mu = [0.04, 0.11, 0.07]
    cov = [
        [0.04, 0.008, 0.002],
        [0.008, 0.09, 0.01],
        [0.002, 0.01, 0.03],
    ]
    result = optimize(["A", "B", "C"], mu, cov, risk_free_rate=0.01, frontier_points=11)
    assert len(result.frontier) == 11
    first, last = result.frontier[0], result.frontier[-1]
    # First point is the minimum-variance mix, exactly.
    assert first.expected_return == pytest.approx(result.min_variance.expected_return, abs=1e-12)
    assert first.volatility == pytest.approx(result.min_variance.volatility, abs=1e-12)
    assert first.weights == pytest.approx(result.min_variance.weights)
    # Last point is 100% in the highest expected-return asset ("B").
    assert last.expected_return == pytest.approx(0.11, abs=1e-12)
    assert last.weights == pytest.approx([0.0, 1.0, 0.0], abs=1e-9)
    assert last.volatility == pytest.approx(math.sqrt(0.09), abs=1e-9)
    # Frontier returns are monotone non-decreasing along the grid.
    rets = [p.expected_return for p in result.frontier]
    assert all(b >= a - 1e-9 for a, b in zip(rets, rets[1:]))


def test_risk_parity_equalizes_risk_contributions() -> None:
    mu = [0.08, 0.06, 0.10]
    cov = [
        [0.04, 0.01, 0.005],
        [0.01, 0.0225, 0.004],
        [0.005, 0.004, 0.0625],
    ]
    result = optimize(["A", "B", "C"], mu, cov)
    w = np.array(result.risk_parity.weights)
    c = np.array(cov)
    rc = w * (c @ w)  # risk contributions
    total = float(w @ c @ w)
    # Each asset contributes ~1/3 of total variance.
    assert np.allclose(rc / total, 1 / 3, atol=1e-4)


# --- Edge cases ---


def test_singular_covariance_is_regularized_not_crashed() -> None:
    # Perfectly correlated assets: singular covariance.
    mu = [0.08, 0.06]
    cov = [[0.04, 0.04], [0.04, 0.04]]
    result = optimize(["AAA", "BBB"], mu, cov, risk_free_rate=0.02)
    assert result.covariance_regularized is True
    assert result.regularization_amount > 0
    assert any("regularized" in note for note in result.notes)
    for mix in (result.max_sharpe, result.min_variance, result.risk_parity):
        _assert_valid_mix(mix.weights)
    assert len(result.frontier) >= 2


def test_well_conditioned_covariance_is_not_regularized() -> None:
    result = optimize(["AAA", "BBB"], MU_2, COV_2, risk_free_rate=RF_2)
    assert result.covariance_regularized is False
    assert result.regularization_amount == 0.0


def test_single_holding() -> None:
    result = optimize(["ONLY"], [0.07], [[0.04]], risk_free_rate=0.01)
    for mix in (result.max_sharpe, result.min_variance, result.risk_parity):
        assert mix.weights == [1.0]
        assert mix.expected_return == pytest.approx(0.07)
        assert mix.volatility == pytest.approx(0.2)
        assert mix.method == "single-holding"
    assert len(result.frontier) == 1
    assert result.frontier[0].weights == [1.0]


def test_negative_expected_returns_falls_back_with_note() -> None:
    # No asset beats the risk-free rate: tangency undefined.
    result = optimize(
        ["A", "B"], [-0.02, -0.05], [[0.04, 0.01], [0.01, 0.0225]], risk_free_rate=0.0
    )
    assert result.max_sharpe.method == "fallback"
    assert any("risk-free rate" in note for note in result.notes)
    assert result.max_sharpe.weights == pytest.approx(result.min_variance.weights)
    for mix in (result.max_sharpe, result.min_variance, result.risk_parity):
        _assert_valid_mix(mix.weights)


def test_zero_variance_single_asset_sharpe_is_zero_not_nan() -> None:
    result = optimize(["CASH"], [0.02], [[0.0]], risk_free_rate=0.01)
    assert result.max_sharpe.volatility == pytest.approx(0.0)
    assert result.max_sharpe.sharpe_ratio == 0.0
    assert any("all zeros" in note for note in result.notes)


def test_asymmetric_covariance_is_symmetrized_with_note() -> None:
    cov = [[0.04, 0.011], [0.009, 0.0225]]
    result = optimize(["A", "B"], MU_2, cov, risk_free_rate=RF_2)
    assert any("symmetrized" in note for note in result.notes)
    _assert_valid_mix(result.max_sharpe.weights)


def test_validation_errors() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        optimize([], [], [])
    with pytest.raises(ValueError, match="one per symbol"):
        optimize(["A", "B"], [0.05], [[0.04]])
    with pytest.raises(ValueError, match="square"):
        optimize(["A", "B"], [0.05, 0.06], [[0.04, 0.01]])
    with pytest.raises(ValueError, match="unique"):
        optimize(["A", "A"], [0.05, 0.06], [[0.04, 0.0], [0.0, 0.04]])
    with pytest.raises(ValueError, match="NaN or Infinity"):
        optimize(["A"], [float("nan")], [[0.04]])
    with pytest.raises(ValueError, match="frontier_points"):
        optimize(["A"], [0.05], [[0.04]], frontier_points=1)


def test_holdings_to_inputs() -> None:
    rows = [
        ("AAA", json.dumps([0.01, 0.02, -0.01, 0.03, 0.0, 0.015])),
        (
            "BBB",
            json.dumps([0.02, 0.01, 0.0, 0.02, -0.01, 0.01, 0.03, -0.02]),
        ),  # longer: truncated to common tail
    ]
    symbols, mu, cov = holdings_to_inputs(rows)
    assert symbols == ["AAA", "BBB"]
    assert len(mu) == 2 and len(cov) == 2 and len(cov[0]) == 2
    # Tail means over the last 6 periods of each series.
    tail_a = [0.01, 0.02, -0.01, 0.03, 0.0, 0.015]
    tail_b = [0.0, 0.02, -0.01, 0.01, 0.03, -0.02]  # common tail of BBB
    assert mu[0] == pytest.approx(sum(tail_a) / 6)
    assert mu[1] == pytest.approx(sum(tail_b) / 6)
    # Sample covariance diagonal matches ddof=1 variance.
    mean_b = sum(tail_b) / 6
    var_b = sum((x - mean_b) ** 2 for x in tail_b) / 5
    assert cov[1][1] == pytest.approx(var_b)


def test_holdings_to_inputs_errors() -> None:
    with pytest.raises(OptimizeError, match="usable return series"):
        holdings_to_inputs([("A", "not json"), ("B", json.dumps([]))])
    with pytest.raises(OptimizeError, match="at least 2 common return periods"):
        holdings_to_inputs([("A", json.dumps([0.01]))])


def test_as_dict_shape() -> None:
    result = optimize(["AAA", "BBB"], MU_2, COV_2, risk_free_rate=RF_2, frontier_points=5)
    body = result.as_dict()
    assert body["symbols"] == ["AAA", "BBB"]
    assert body["risk_free_rate"] == RF_2
    for key in ("max_sharpe", "min_variance", "risk_parity"):
        mix = body[key]
        assert set(mix["weights"]) == {"AAA", "BBB"}
        assert math.isclose(sum(mix["weights"].values()), 1.0, abs_tol=1e-6)
        for field in ("expected_return", "volatility", "sharpe_ratio", "label", "method"):
            assert field in mix
    assert len(body["frontier"]) == 5
    assert body["covariance_regularized"] is False
    assert isinstance(body["notes"], list)


# --- REST + MCP integration ---


@pytest.fixture(scope="module")
def client(tmp_path_factory: Any) -> Any:
    db_path = tmp_path_factory.mktemp("pae-opt") / "pae.db"
    os.environ["PAE_DB_PATH"] = str(db_path)
    os.environ["PAE_ENGINE_URL"] = "http://127.0.0.1:9"  # unreachable, by design
    from pae.server import app

    with TestClient(app) as test_client:
        yield test_client


def _seed_portfolio(client: TestClient) -> str:
    # Seed the database the app was bound to at import (pae.server.DB_PATH),
    # not the current env var: module-scoped fixtures in other test modules
    # may have imported pae.server first with their own tmp path.
    from pae.server import DB_PATH
    from pae.storage.db import Holding, PAEDatabase, Portfolio

    db = PAEDatabase(DB_PATH)
    db.initialize()
    portfolio = db.insert_portfolio(Portfolio(name="OptTest"))
    rng = np.random.default_rng(3)
    for i, symbol in enumerate(("AAA", "BBB", "CCC")):
        rets = (rng.normal(0.006 + 0.002 * i, 0.03, 24)).tolist()
        db.insert_holding(
            Holding(
                portfolio_id=portfolio.id,
                symbol=symbol,
                market_value=1000.0 * (i + 1),
                returns_json=json.dumps(rets),
            )
        )
    db.close()
    return portfolio.id


def test_rest_optimize_explicit_inputs(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/analytics/optimize",
        json={
            "symbols": ["AAA", "BBB"],
            "expected_returns": MU_2,
            "covariance": COV_2,
            "risk_free_rate": RF_2,
            "frontier_points": 7,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["symbols"] == ["AAA", "BBB"]
    assert len(body["frontier"]) == 7
    assert body["max_sharpe"]["weights"]["AAA"] == pytest.approx(0.6363636364, abs=1e-6)
    assert "disclosure" in body
    assert "No investment advice" in body["disclosure"]


def test_rest_optimize_portfolio_id(client: TestClient) -> None:
    portfolio_id = _seed_portfolio(client)
    resp = client.post(
        "/api/v1/analytics/optimize",
        json={
            "portfolio_id": portfolio_id,
            "risk_free_rate": 0.0,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["symbols"] == ["AAA", "BBB", "CCC"]
    for key in ("max_sharpe", "min_variance", "risk_parity"):
        total = sum(body[key]["weights"].values())
        assert total == pytest.approx(1.0, abs=1e-6)
    assert len(body["frontier"]) == 25


def test_rest_optimize_bad_input_is_400(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/analytics/optimize",
        json={
            "symbols": ["AAA", "BBB"],
            "expected_returns": [0.05],
            "covariance": COV_2,
        },
    )
    assert resp.status_code == 400


def test_rest_optimize_missing_inputs_is_400(client: TestClient) -> None:
    resp = client.post("/api/v1/analytics/optimize", json={})
    assert resp.status_code == 400


def test_rest_optimize_unknown_portfolio_is_404(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/analytics/optimize",
        json={
            "portfolio_id": "does-not-exist",
        },
    )
    assert resp.status_code == 404


def test_mcp_tool_registered() -> None:
    assert "optimize_portfolio" in TOOL_NAMES


def test_mcp_optimize_portfolio_explicit() -> None:
    import asyncio

    from pae.storage.db import PAEDatabase

    async def run() -> dict[str, Any]:
        db = PAEDatabase(":memory:")
        return await run_agent_tool(
            "optimize_portfolio",
            {
                "symbols": ["AAA", "BBB"],
                "expected_returns": MU_2,
                "covariance": COV_2,
                "risk_free_rate": RF_2,
            },
            db,
        )

    outcome = asyncio.run(run())
    assert outcome["ok"] is True, outcome
    result = outcome["result"]
    assert result["max_sharpe"]["weights"]["AAA"] == pytest.approx(0.6363636364, abs=1e-6)
    assert len(result["frontier"]) == 25


def test_mcp_optimize_portfolio_bad_params() -> None:
    import asyncio

    from pae.storage.db import PAEDatabase

    async def run() -> dict[str, Any]:
        db = PAEDatabase(":memory:")
        return await run_agent_tool("optimize_portfolio", {"symbols": ["AAA"]}, db)

    outcome = asyncio.run(run())
    assert outcome["ok"] is False
    assert "Traceback" not in str(outcome)
