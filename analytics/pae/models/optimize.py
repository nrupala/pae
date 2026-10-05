"""Long-only portfolio optimization engine.

Computes efficient-frontier analytics from expected returns and a
covariance matrix:

- Maximum-Sharpe (tangency) portfolio
- Global minimum-variance portfolio
- Risk-parity portfolio (equal risk contribution)

Closed-form solutions are used when the long-only constraint is not
binding; otherwise a bounded numerical solve (SLSQP) is used. A
near-singular covariance matrix is regularized by eigenvalue clipping,
and the regularization is reported on the result -- never hidden.

All outputs are educational analytics: expected risk/return trade-offs
for a given set of inputs. The tool calculates; the user decides.
No output constitutes investment advice.
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from numpy.typing import NDArray
from scipy.optimize import minimize


class OptimizeError(Exception):
    """Raised when optimization cannot produce a result."""


@dataclass
class PortfolioMix:
    """One optimal mix: weights plus its expected risk/return statistics."""

    label: str
    weights: list[float]  # aligned with OptimizationResult.symbols
    expected_return: float
    volatility: float
    sharpe_ratio: float
    method: str  # "closed-form" | "numerical (SLSQP)" | "fallback" | "single-holding"


@dataclass
class FrontierPoint:
    """One point on the long-only efficient frontier."""

    expected_return: float
    volatility: float
    weights: list[float]  # aligned with OptimizationResult.symbols


@dataclass
class OptimizationResult:
    """Complete optimization output."""

    symbols: list[str]
    risk_free_rate: float
    max_sharpe: PortfolioMix
    min_variance: PortfolioMix
    risk_parity: PortfolioMix
    frontier: list[FrontierPoint]
    covariance_regularized: bool
    regularization_amount: float
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """JSON-safe representation with symbol-keyed weights."""

        def mix_dict(mix: PortfolioMix) -> dict[str, Any]:
            return {
                "label": mix.label,
                "weights": {sym: round(w, 6) for sym, w in zip(self.symbols, mix.weights)},
                "expected_return": round(mix.expected_return, 6),
                "volatility": round(mix.volatility, 6),
                "sharpe_ratio": round(mix.sharpe_ratio, 6),
                "method": mix.method,
            }

        return {
            "symbols": list(self.symbols),
            "risk_free_rate": self.risk_free_rate,
            "max_sharpe": mix_dict(self.max_sharpe),
            "min_variance": mix_dict(self.min_variance),
            "risk_parity": mix_dict(self.risk_parity),
            "frontier": [
                {
                    "expected_return": round(p.expected_return, 6),
                    "volatility": round(p.volatility, 6),
                    "weights": {sym: round(w, 6) for sym, w in zip(self.symbols, p.weights)},
                }
                for p in self.frontier
            ],
            "covariance_regularized": self.covariance_regularized,
            "regularization_amount": self.regularization_amount,
            "notes": list(self.notes),
        }


# --- Input handling ---


def _validate_inputs(
    symbols: Sequence[str],
    expected_returns: Sequence[float],
    covariance: Sequence[Sequence[float]],
    risk_free_rate: float,
    frontier_points: int,
) -> tuple[list[str], NDArray[np.float64], NDArray[np.float64], list[str]]:
    """Validate and normalize optimizer inputs.

    Returns:
        Tuple of (symbols, expected returns, covariance, notes).

    Raises:
        ValueError: If inputs are empty, mismatched, or non-finite.
    """
    syms = [str(s).strip() for s in symbols]
    if not syms:
        msg = "symbols must not be empty"
        raise ValueError(msg)
    if any(not s for s in syms):
        msg = "symbols must not contain blank entries"
        raise ValueError(msg)
    if len(set(syms)) != len(syms):
        msg = "symbols must be unique"
        raise ValueError(msg)

    n = len(syms)

    mu = np.asarray(expected_returns, dtype=np.float64)
    if mu.shape != (n,):
        msg = f"expected_returns has length {mu.size}, expected {n} (one per symbol)"
        raise ValueError(msg)
    if np.any(~np.isfinite(mu)):
        msg = "expected_returns contains NaN or Infinity values"
        raise ValueError(msg)

    cov = np.asarray(covariance, dtype=np.float64)
    if cov.shape != (n, n):
        msg = (
            f"covariance has shape {cov.shape}, expected ({n}, {n}) "
            "(square, one row/column per symbol)"
        )
        raise ValueError(msg)
    if np.any(~np.isfinite(cov)):
        msg = "covariance contains NaN or Infinity values"
        raise ValueError(msg)

    # Symmetrize; covariance estimates should already be symmetric.
    asymmetry = float(np.max(np.abs(cov - cov.T)))
    cov = (cov + cov.T) / 2.0

    if not math.isfinite(risk_free_rate):
        msg = f"risk_free_rate is invalid: {risk_free_rate}"
        raise ValueError(msg)
    if frontier_points < 2:
        msg = f"frontier_points must be >= 2, got {frontier_points}"
        raise ValueError(msg)

    notes: list[str] = []
    if asymmetry > 1e-8:
        notes.append(
            f"Covariance matrix was asymmetric (max asymmetry {asymmetry:.2e}); "
            "symmetrized before optimization."
        )
    return syms, mu, cov, notes


def holdings_to_inputs(
    rows: Sequence[tuple[str, str]],
) -> tuple[list[str], list[float], list[list[float]]]:
    """Build optimizer inputs from ``(symbol, returns_json)`` rows.

    Each JSON return series is cleaned of non-finite values and truncated
    to the longest common tail across holdings. Expected returns are tail
    means; covariance is the sample covariance (ddof=1).

    Args:
        rows: Sequence of (symbol, returns_json) pairs, e.g. from holdings.

    Returns:
        Tuple of (symbols, expected_returns, covariance).

    Raises:
        OptimizeError: If fewer than one usable holding or fewer than two
            common periods remain.
    """
    parsed: list[tuple[str, list[float]]] = []
    for symbol, returns_json in rows:
        try:
            raw = json.loads(returns_json)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(raw, list):
            continue
        clean = [float(r) for r in raw if isinstance(r, (int, float)) and math.isfinite(r)]
        if clean:
            parsed.append((str(symbol), clean))

    if not parsed:
        msg = "No holdings with usable return series"
        raise OptimizeError(msg)

    n_periods = min(len(rets) for _, rets in parsed)
    if n_periods < 2:
        msg = f"Need at least 2 common return periods for a covariance estimate, got {n_periods}"
        raise OptimizeError(msg)

    tails = np.array([rets[-n_periods:] for _, rets in parsed], dtype=np.float64)
    symbols = [symbol for symbol, _ in parsed]
    expected_returns = [float(np.mean(tails[i])) for i in range(len(parsed))]
    covariance = np.cov(tails, ddof=1)
    # np.cov returns a scalar for a single row; normalize to 2-D.
    covariance = np.atleast_2d(covariance)
    return symbols, expected_returns, covariance.tolist()


# --- Regularization ---


def _regularize_covariance(
    cov: NDArray[np.float64],
) -> tuple[NDArray[np.float64], bool, float, str | None]:
    """Eigenvalue-clipping regularization for near-singular covariance.

    Clips eigenvalues below ``1e-8 * max(1, largest)`` up to that floor and
    rebuilds the matrix. A well-conditioned matrix is returned unchanged.

    Returns:
        Tuple of (regularized matrix, was_applied, amount, note).
    """
    vals, vecs = np.linalg.eigh(cov)
    floor = 1e-8 * max(1.0, float(vals[-1]))
    if float(vals[0]) >= floor:
        return cov, False, 0.0, None
    amount = float(floor - vals[0])
    clipped = np.clip(vals, floor, None)
    reg = (vecs * clipped) @ vecs.T
    reg = (reg + reg.T) / 2.0
    note = (
        "Covariance matrix was near-singular; regularized by eigenvalue "
        f"clipping (floor {floor:.2e}, max eigenvalue lift {amount:.2e}). "
        "Weights are computed from the regularized matrix."
    )
    return reg, True, amount, note


# --- Portfolio statistics ---


def _stats(
    w: NDArray[np.float64],
    mu: NDArray[np.float64],
    cov: NDArray[np.float64],
    risk_free_rate: float,
    zero_vol: bool = False,
) -> tuple[float, float, float]:
    """Expected return, volatility, and Sharpe ratio for weights ``w``.

    ``zero_vol`` marks a degenerate (all-zero) input covariance: true
    volatility is zero, so report 0 rather than the regularization floor.
    """
    port_ret = float(np.dot(w, mu))
    if zero_vol:
        return port_ret, 0.0, 0.0
    var = float(w @ cov @ w)
    vol = math.sqrt(max(var, 0.0))
    sharpe = (port_ret - risk_free_rate) / vol if vol > 1e-12 else 0.0
    return port_ret, vol, sharpe


def _clean_weights(w: NDArray[np.float64]) -> NDArray[np.float64]:
    """Clip solver noise below zero and renormalize to sum to 1."""
    w = np.clip(w, 0.0, None)
    total = float(np.sum(w))
    if total <= 0:
        # Degenerate: fall back to equal weights rather than NaNs.
        w = np.full_like(w, 1.0 / len(w))
    else:
        w = w / total
    return w


# --- Closed-form solutions (used when long-only does not bind) ---


def _closed_form_max_sharpe(
    mu: NDArray[np.float64],
    cov: NDArray[np.float64],
    risk_free_rate: float,
) -> NDArray[np.float64] | None:
    """Unconstrained tangency weights, or None if unusable under long-only.

    Returns None when no asset beats the risk-free rate (tangency
    undefined), when the solution has negative weights (long-only binds),
    or when the matrix inverse fails.
    """
    excess = mu - risk_free_rate
    if np.all(excess <= 0):
        return None
    try:
        inv = np.linalg.inv(cov)
    except np.linalg.LinAlgError:
        return None
    raw = inv @ excess
    if not np.all(np.isfinite(raw)):
        return None
    denom = float(np.sum(raw))
    if denom <= 0:
        return None
    w = raw / denom
    if np.any(w < -1e-9):
        return None
    return _clean_weights(w)


def _closed_form_min_variance(
    cov: NDArray[np.float64],
) -> NDArray[np.float64] | None:
    """Unconstrained global minimum-variance weights, or None if long-only binds."""
    n = cov.shape[0]
    try:
        inv = np.linalg.inv(cov)
    except np.linalg.LinAlgError:
        return None
    raw = inv @ np.ones(n)
    denom = float(np.sum(raw))
    if denom == 0 or not np.all(np.isfinite(raw)):
        return None
    w = raw / denom
    if np.any(w < -1e-9):
        return None
    return _clean_weights(w)


# --- Numerical fallbacks (SLSQP, long-only) ---


def _numerical_max_sharpe(
    mu: NDArray[np.float64],
    cov: NDArray[np.float64],
    risk_free_rate: float,
    w0: NDArray[np.float64],
) -> NDArray[np.float64]:
    """Maximize the Sharpe ratio subject to sum(w)=1, w>=0 via SLSQP."""
    excess = mu - risk_free_rate

    def neg_sharpe(w: NDArray[np.float64]) -> float:
        var = float(w @ cov @ w)
        if var <= 0:
            return 0.0
        return -float(np.dot(w, excess)) / math.sqrt(var)

    n = len(mu)
    result = minimize(
        neg_sharpe,
        w0,
        method="SLSQP",
        bounds=[(0.0, 1.0)] * n,
        constraints=[{"type": "eq", "fun": lambda w: float(np.sum(w)) - 1.0}],
        options={"ftol": 1e-12, "maxiter": 1000},
    )
    if not result.success or not np.all(np.isfinite(result.x)):
        msg = f"Maximum-Sharpe numerical solve failed: {result.message}"
        raise OptimizeError(msg)
    return _clean_weights(np.asarray(result.x, dtype=np.float64))


def _numerical_min_variance(
    cov: NDArray[np.float64],
    w0: NDArray[np.float64],
) -> NDArray[np.float64]:
    """Minimize variance subject to sum(w)=1, w>=0 via SLSQP."""
    n = cov.shape[0]
    result = minimize(
        lambda w: float(w @ cov @ w),
        w0,
        method="SLSQP",
        bounds=[(0.0, 1.0)] * n,
        constraints=[{"type": "eq", "fun": lambda w: float(np.sum(w)) - 1.0}],
        options={"ftol": 1e-12, "maxiter": 1000},
    )
    if not result.success or not np.all(np.isfinite(result.x)):
        msg = f"Minimum-variance numerical solve failed: {result.message}"
        raise OptimizeError(msg)
    return _clean_weights(np.asarray(result.x, dtype=np.float64))


def _risk_parity_weights(
    cov: NDArray[np.float64],
    max_iter: int = 5000,
    tol: float = 1e-12,
) -> NDArray[np.float64]:
    """Risk-parity weights via cyclical coordinate descent (Spinu).

    Iterates ``w_i <- portfolio_vol / (n * (Sigma w)_i)`` with
    renormalization until risk contributions equalize. Each asset ends
    with (approximately) 1/n of total portfolio risk.
    """
    n = cov.shape[0]
    w = np.full(n, 1.0 / n)
    for _ in range(max_iter):
        var = float(w @ cov @ w)
        if var <= 0:
            break  # degenerate covariance; keep equal weights
        vol = math.sqrt(var)
        mrc = cov @ w  # marginal risk contributions (unscaled)
        w_new = (vol / n) / np.maximum(mrc, 1e-16)
        w_new = w_new / float(np.sum(w_new))
        if float(np.max(np.abs(w_new - w))) < tol:
            w = w_new
            break
        w = w_new
    return _clean_weights(w)


# --- Efficient frontier ---


def _frontier_point(
    mu: NDArray[np.float64],
    cov: NDArray[np.float64],
    target_return: float,
    w0: NDArray[np.float64],
) -> NDArray[np.float64] | None:
    """Minimum-variance long-only weights for an exact target return.

    Returns None when the SLSQP solve fails (caller carries the previous
    point forward and notes it).
    """
    n = len(mu)
    result = minimize(
        lambda w: float(w @ cov @ w),
        w0,
        method="SLSQP",
        bounds=[(0.0, 1.0)] * n,
        constraints=[
            {"type": "eq", "fun": lambda w: float(np.sum(w)) - 1.0},
            {
                "type": "eq",
                "fun": lambda w, t=target_return: float(np.dot(w, mu)) - t,
            },
        ],
        options={"ftol": 1e-12, "maxiter": 1000},
    )
    if not result.success or not np.all(np.isfinite(result.x)):
        return None
    return _clean_weights(np.asarray(result.x, dtype=np.float64))


def _efficient_frontier(
    mu: NDArray[np.float64],
    cov: NDArray[np.float64],
    risk_free_rate: float,
    w_minvar: NDArray[np.float64],
    n_points: int,
    notes: list[str],
    zero_vol: bool = False,
) -> list[FrontierPoint]:
    """Long-only efficient frontier from the min-variance to the max-return mix.

    Endpoints are exact by construction: the first point is the
    minimum-variance portfolio, the last is 100% in the highest
    expected-return asset.
    """
    ret_minvar = float(np.dot(w_minvar, mu))
    top_idx = int(np.argmax(mu))
    ret_max = float(mu[top_idx])

    w_maxret = np.zeros(len(mu))
    w_maxret[top_idx] = 1.0

    def point(w: NDArray[np.float64]) -> FrontierPoint:
        r, v, _ = _stats(w, mu, cov, risk_free_rate, zero_vol=zero_vol)
        return FrontierPoint(expected_return=r, volatility=v, weights=[float(x) for x in w])

    if ret_max - ret_minvar < 1e-12 or n_points == 2:
        # Degenerate return range (or the minimum grid): endpoints only.
        return [point(w_minvar), point(w_maxret)]

    targets = np.linspace(ret_minvar, ret_max, n_points)
    points: list[FrontierPoint] = [point(w_minvar)]
    w_prev = w_minvar
    for target in targets[1:-1]:
        w = _frontier_point(mu, cov, float(target), w_prev)
        if w is None:
            notes.append(
                f"Frontier solve failed at target return {target:.6f}; "
                "carried the previous point forward."
            )
            w = w_prev
        w_prev = w
        points.append(point(w))
    points.append(point(w_maxret))
    return points


# --- Main entry point ---


def optimize(
    symbols: Sequence[str],
    expected_returns: Sequence[float],
    covariance: Sequence[Sequence[float]],
    risk_free_rate: float = 0.0,
    frontier_points: int = 25,
) -> OptimizationResult:
    """Compute long-only optimal mixes and the efficient frontier.

    Args:
        symbols: Asset symbols (unique, non-blank).
        expected_returns: Expected period return per asset.
        covariance: n x n covariance matrix of period returns.
        risk_free_rate: Risk-free rate per period (Sharpe ratios only).
        frontier_points: Number of efficient-frontier points (>= 2).

    Returns:
        OptimizationResult with max-Sharpe, min-variance, and risk-parity
        mixes plus the efficient frontier.

    Raises:
        ValueError: If inputs are empty, mismatched, or non-finite.
        OptimizeError: If a numerical solve fails.
    """
    syms, mu, cov, notes = _validate_inputs(
        symbols, expected_returns, covariance, risk_free_rate, frontier_points
    )

    cov_reg, regularized, reg_amount, reg_note = _regularize_covariance(cov)
    if reg_note:
        notes.append(reg_note)

    # Degenerate input: an all-zero covariance means true volatility is
    # zero everywhere. Report 0 rather than the regularization floor.
    degenerate = not np.any(cov)
    if degenerate:
        notes.append(
            "Covariance matrix was all zeros; reported volatilities are 0 and Sharpe ratios 0."
        )

    n = len(syms)
    equal_w = np.full(n, 1.0 / n)

    if n == 1:
        # Single holding: every mix is 100% in it; the frontier is one point.
        w_one = np.array([1.0])
        r, v, s = _stats(w_one, mu, cov_reg, risk_free_rate, zero_vol=degenerate)
        notes.append("Single holding: all mixes are 100% in that holding.")

        def single_mix(label: str) -> PortfolioMix:
            return PortfolioMix(
                label=label,
                weights=[1.0],
                expected_return=r,
                volatility=v,
                sharpe_ratio=s,
                method="single-holding",
            )

        return OptimizationResult(
            symbols=syms,
            risk_free_rate=risk_free_rate,
            max_sharpe=single_mix("Maximum Sharpe"),
            min_variance=single_mix("Minimum variance"),
            risk_parity=single_mix("Risk parity"),
            frontier=[FrontierPoint(expected_return=r, volatility=v, weights=[1.0])],
            covariance_regularized=regularized,
            regularization_amount=reg_amount,
            notes=notes,
        )

    # --- Maximum Sharpe ---
    w_sharpe = _closed_form_max_sharpe(mu, cov_reg, risk_free_rate)
    if w_sharpe is not None:
        sharpe_method = "closed-form"
    elif np.all(mu - risk_free_rate <= 0):
        # Tangency undefined: no asset beats the risk-free rate. Report the
        # minimum-variance mix for reference and say so plainly.
        w_mv_fallback = _closed_form_min_variance(cov_reg)
        if w_mv_fallback is None:
            w_mv_fallback = _numerical_min_variance(cov_reg, equal_w)
        w_sharpe = w_mv_fallback
        sharpe_method = "fallback"
        notes.append(
            "No asset has expected return above the risk-free rate, so the "
            "maximum-Sharpe mix is not defined; the minimum-variance mix is "
            "reported for the maximum-Sharpe slot instead."
        )
    else:
        start = _closed_form_start(mu, cov_reg)
        w_sharpe = _numerical_max_sharpe(
            mu, cov_reg, risk_free_rate, start if start is not None else equal_w
        )
        sharpe_method = "numerical (SLSQP)"
        notes.append(
            "Closed-form tangency weights violated the long-only constraint; "
            "used a bounded numerical solve instead."
        )

    # --- Minimum variance ---
    w_minvar = _closed_form_min_variance(cov_reg)
    if w_minvar is not None:
        minvar_method = "closed-form"
    else:
        w_minvar = _numerical_min_variance(cov_reg, equal_w)
        minvar_method = "numerical (SLSQP)"
        notes.append(
            "Closed-form minimum-variance weights violated the long-only "
            "constraint; used a bounded numerical solve instead."
        )

    # --- Risk parity ---
    w_rp = _risk_parity_weights(cov_reg)

    def make_mix(label: str, w: NDArray[np.float64], method: str) -> PortfolioMix:
        r, v, s = _stats(w, mu, cov_reg, risk_free_rate, zero_vol=degenerate)
        return PortfolioMix(
            label=label,
            weights=[float(x) for x in w],
            expected_return=r,
            volatility=v,
            sharpe_ratio=s,
            method=method,
        )

    frontier = _efficient_frontier(
        mu,
        cov_reg,
        risk_free_rate,
        w_minvar,
        frontier_points,
        notes,
        zero_vol=degenerate,
    )

    return OptimizationResult(
        symbols=syms,
        risk_free_rate=risk_free_rate,
        max_sharpe=make_mix("Maximum Sharpe", w_sharpe, sharpe_method),
        min_variance=make_mix("Minimum variance", w_minvar, minvar_method),
        risk_parity=make_mix("Risk parity", w_rp, "cyclical coordinate descent"),
        frontier=frontier,
        covariance_regularized=regularized,
        regularization_amount=reg_amount,
        notes=notes,
    )


def _closed_form_start(
    mu: NDArray[np.float64], cov: NDArray[np.float64]
) -> NDArray[np.float64] | None:
    """Positive-part start vector for the numerical Sharpe solve, if usable."""
    try:
        inv = np.linalg.inv(cov)
    except np.linalg.LinAlgError:
        return None
    raw = np.clip(inv @ mu, 0.0, None)
    total = float(np.sum(raw))
    if total <= 0 or not np.all(np.isfinite(raw)):
        return None
    return raw / total
