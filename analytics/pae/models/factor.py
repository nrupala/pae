"""Fama-French factor decomposition engine.

Decomposes portfolio returns into factor exposures:
- Market (MKT-RF): broad equity market beta
- Size (SMB): small minus big capitalization
- Value (HML): high minus low book-to-market
- Profitability (RMW): robust minus weak operating profitability
- Investment (CMA): conservative minus aggressive investment

All analysis is backward-looking and user-parameterized.
No output constitutes investment advice.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray


@dataclass
class FactorExposure:
    """Result of a factor regression."""

    factor_name: str
    beta: float
    t_stat: float
    contribution_pct: float


@dataclass
class FactorDecomposition:
    """Complete factor decomposition of a portfolio."""

    alpha: float
    alpha_t_stat: float
    r_squared: float
    exposures: list[FactorExposure]
    residual_risk_pct: float


class FactorError(Exception):
    """Raised when factor decomposition fails."""


def _validate_returns(
    portfolio_returns: NDArray[np.float64],
    factor_returns: dict[str, NDArray[np.float64]],
) -> None:
    """Validate inputs before running the regression.

    Args:
        portfolio_returns: Array of portfolio period returns.
        factor_returns: Dict mapping factor name to array of factor returns.

    Raises:
        ValueError: If inputs are empty, mismatched, or contain invalid values.
    """
    if portfolio_returns.size == 0:
        msg = "portfolio_returns must not be empty"
        raise ValueError(msg)

    if not factor_returns:
        msg = "factor_returns must contain at least one factor"
        raise ValueError(msg)

    n = len(portfolio_returns)
    k = len(factor_returns)

    if n < k + 2:
        msg = f"Need at least {k + 2} observations, got {n}"
        raise ValueError(msg)

    for name, fr in factor_returns.items():
        if len(fr) != n:
            msg = (
                f"Factor '{name}' has {len(fr)} observations, "
                f"expected {n} (same as portfolio_returns)"
            )
            raise ValueError(msg)

    # Check for NaN/Infinity
    if np.any(~np.isfinite(portfolio_returns)):
        msg = "portfolio_returns contains NaN or Infinity values"
        raise ValueError(msg)

    for name, fr in factor_returns.items():
        if np.any(~np.isfinite(fr)):
            msg = f"Factor '{name}' returns contain NaN or Infinity values"
            raise ValueError(msg)


def decompose(
    portfolio_returns: NDArray[np.float64],
    factor_returns: dict[str, NDArray[np.float64]],
) -> FactorDecomposition:
    """Run OLS regression of portfolio returns on factor returns.

    Args:
        portfolio_returns: Array of portfolio period returns.
        factor_returns: Dict mapping factor name to array of factor returns.
            Must be same length as portfolio_returns.

    Returns:
        FactorDecomposition with exposures, alpha, and R-squared.

    Raises:
        ValueError: If inputs fail validation (empty, mismatched lengths,
            NaN/Infinity values, insufficient observations).
        FactorError: If the factor matrix is singular (collinear factors)
            or another numerical error occurs during regression.
    """
    _validate_returns(portfolio_returns, factor_returns)

    n = len(portfolio_returns)
    factor_names = list(factor_returns.keys())
    k = len(factor_names)

    # Build factor matrix with intercept
    x_matrix = np.column_stack([
        np.ones(n),
        *[factor_returns[name] for name in factor_names],
    ])

    y = np.array(portfolio_returns)

    # OLS: beta = (X'X)^-1 X'y
    # Use try/except to handle singular matrices (collinear factors)
    try:
        xtx = x_matrix.T @ x_matrix
        xtx_inv = np.linalg.inv(xtx)
    except np.linalg.LinAlgError as exc:
        msg = (
            "Factor matrix is singular (factors may be perfectly collinear). "
            "Remove redundant factors and retry."
        )
        raise FactorError(msg) from exc

    betas = xtx_inv @ x_matrix.T @ y

    # Check for NaN in betas (numerical instability)
    if np.any(~np.isfinite(betas)):
        msg = (
            "Regression produced NaN/Infinity coefficients. "
            "Factor matrix may be near-singular."
        )
        raise FactorError(msg)

    # Residuals and R-squared
    y_hat = x_matrix @ betas
    residuals = y - y_hat
    ss_res = float(np.sum(residuals ** 2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    r_squared = 1.0 - (ss_res / ss_tot) if ss_tot > 0 else 0.0

    # Standard errors for t-stats
    dof = n - k - 1
    sigma2 = ss_res / dof if dof > 0 else 0.0
    diag_values = np.diag(xtx_inv) * sigma2

    # Guard against negative diagonal values (numerical noise)
    se = np.sqrt(np.maximum(diag_values, 0.0))

    # Portfolio variance decomposition
    portfolio_var = float(np.var(y, ddof=1))
    exposures: list[FactorExposure] = []
    total_explained = 0.0

    for i, name in enumerate(factor_names):
        beta_i = betas[i + 1]
        factor_var = float(np.var(factor_returns[name], ddof=1))
        contribution = (
            (beta_i ** 2 * factor_var) / portfolio_var
            if portfolio_var > 0
            else 0.0
        )
        total_explained += contribution
        t_stat_i = (
            float(betas[i + 1] / se[i + 1]) if se[i + 1] > 0 else 0.0
        )

        exposures.append(FactorExposure(
            factor_name=name,
            beta=float(beta_i),
            t_stat=t_stat_i,
            contribution_pct=float(contribution * 100),
        ))

    alpha = float(betas[0])
    alpha_t = float(betas[0] / se[0]) if se[0] > 0 else 0.0
    residual_pct = float((1.0 - total_explained) * 100)

    return FactorDecomposition(
        alpha=alpha,
        alpha_t_stat=alpha_t,
        r_squared=float(r_squared),
        exposures=exposures,
        residual_risk_pct=max(residual_pct, 0.0),
    )
