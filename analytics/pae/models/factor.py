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

import logging

import numpy as np
from numpy.typing import NDArray
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class FactorExposure:
    """Result of a factor regression.

    Attributes:
        factor_name: Name of the factor (e.g., "MKT", "SMB", "HML").
        beta: Regression coefficient (factor loading).
        t_stat: T-statistic for the beta estimate.
        contribution_pct: Percentage of portfolio variance explained by this factor.
    """

    factor_name: str
    beta: float
    t_stat: float
    contribution_pct: float


@dataclass
class FactorDecomposition:
    """Complete factor decomposition of a portfolio.

    Attributes:
        alpha: Regression intercept (excess return not explained by factors).
        alpha_t_stat: T-statistic for the alpha estimate.
        r_squared: Coefficient of determination (0.0 to 1.0).
        exposures: List of per-factor exposure results.
        residual_risk_pct: Percentage of variance not explained by factors.
    """

    alpha: float
    alpha_t_stat: float
    r_squared: float
    exposures: list[FactorExposure]
    residual_risk_pct: float


def decompose(
    portfolio_returns: NDArray[np.float64],
    factor_returns: dict[str, NDArray[np.float64]],
) -> FactorDecomposition:
    """Run OLS regression of portfolio returns on factor returns.

    Args:
        portfolio_returns: Array of portfolio period returns. Must not contain
            NaN or Inf values. Length must be at least (num_factors + 2).
        factor_returns: Dict mapping factor name to array of factor returns.
            Each array must be the same length as portfolio_returns and must
            not contain NaN or Inf values.

    Returns:
        FactorDecomposition with exposures, alpha, and R-squared.

    Raises:
        ValueError: If inputs are empty, contain non-finite values, have
            mismatched lengths, or have insufficient observations.
        numpy.linalg.LinAlgError: If the factor matrix is singular
            (perfectly collinear factors).
    """
    # --- Input validation ---
    portfolio_returns = np.asarray(portfolio_returns, dtype=np.float64)

    if portfolio_returns.ndim != 1:
        raise ValueError(
            f"portfolio_returns must be 1-dimensional, got shape {portfolio_returns.shape}"
        )

    n = len(portfolio_returns)
    if n == 0:
        raise ValueError("portfolio_returns must not be empty")

    if not np.all(np.isfinite(portfolio_returns)):
        raise ValueError("portfolio_returns contains NaN or Inf values")

    if not factor_returns:
        raise ValueError("factor_returns must contain at least one factor")

    factor_names = list(factor_returns.keys())
    k = len(factor_names)

    for name in factor_names:
        arr = np.asarray(factor_returns[name], dtype=np.float64)
        if arr.ndim != 1:
            raise ValueError(
                f"Factor '{name}' must be 1-dimensional, got shape {arr.shape}"
            )
        if len(arr) != n:
            raise ValueError(
                f"Factor '{name}' has length {len(arr)}, expected {n} "
                f"(same as portfolio_returns)"
            )
        if not np.all(np.isfinite(arr)):
            raise ValueError(f"Factor '{name}' contains NaN or Inf values")
        factor_returns[name] = arr

    if n < k + 2:
        raise ValueError(
            f"Need at least {k + 2} observations for {k} factors, got {n}"
        )

    # --- Build factor matrix with intercept ---
    x_matrix = np.column_stack([
        np.ones(n),
        *[factor_returns[name] for name in factor_names],
    ])

    y = portfolio_returns

    # OLS: beta = (X'X)^-1 X'y
    try:
        xtx_inv = np.linalg.inv(x_matrix.T @ x_matrix)
    except np.linalg.LinAlgError:
        logger.error("Singular matrix in factor decomposition -- factors may be collinear")
        raise

    betas = xtx_inv @ x_matrix.T @ y

    # Residuals and R-squared
    y_hat = x_matrix @ betas
    residuals = y - y_hat
    ss_res = float(np.sum(residuals ** 2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    r_squared = 1.0 - (ss_res / ss_tot) if ss_tot > 0 else 0.0
    r_squared = max(0.0, min(1.0, r_squared))  # clamp to [0, 1]

    # Standard errors for t-stats
    dof = n - k - 1
    sigma2 = ss_res / dof if dof > 0 else 0.0
    se_squared = np.diag(xtx_inv) * sigma2
    se = np.sqrt(np.maximum(se_squared, 0.0))  # guard against negative due to float

    # Portfolio variance decomposition
    portfolio_var = float(np.var(y, ddof=1))
    exposures: list[FactorExposure] = []
    total_explained = 0.0

    for i, name in enumerate(factor_names):
        beta_i = betas[i + 1]
        factor_var = float(np.var(factor_returns[name], ddof=1))
        contribution = (beta_i ** 2 * factor_var) / portfolio_var if portfolio_var > 0 else 0.0
        total_explained += contribution
        t_stat_i = float(betas[i + 1] / se[i + 1]) if se[i + 1] > 0 else 0.0

        exposures.append(FactorExposure(
            factor_name=name,
            beta=float(beta_i),
            t_stat=t_stat_i,
            contribution_pct=float(contribution * 100),
        ))

    alpha = float(betas[0])
    alpha_t = float(betas[0] / se[0]) if se[0] > 0 else 0.0
    residual_pct = float((1.0 - total_explained) * 100)

    logger.info(
        "Factor decomposition: R^2=%.4f, alpha=%.6f, %d factors",
        r_squared, alpha, k,
    )

    return FactorDecomposition(
        alpha=alpha,
        alpha_t_stat=alpha_t,
        r_squared=float(r_squared),
        exposures=exposures,
        residual_risk_pct=max(residual_pct, 0.0),
    )
