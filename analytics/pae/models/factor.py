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

import numpy as np
from numpy.typing import NDArray
from dataclasses import dataclass


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
    """
    n = len(portfolio_returns)
    factor_names = list(factor_returns.keys())
    k = len(factor_names)

    if n < k + 2:
        raise ValueError(f"Need at least {k + 2} observations, got {n}")

    # Build factor matrix with intercept
    X = np.column_stack([
        np.ones(n),
        *[factor_returns[name] for name in factor_names]
    ])

    y = np.array(portfolio_returns)

    # OLS: beta = (X'X)^-1 X'y
    XtX_inv = np.linalg.inv(X.T @ X)
    betas = XtX_inv @ X.T @ y

    # Residuals and R-squared
    y_hat = X @ betas
    residuals = y - y_hat
    ss_res = np.sum(residuals ** 2)
    ss_tot = np.sum((y - np.mean(y)) ** 2)
    r_squared = 1.0 - (ss_res / ss_tot) if ss_tot > 0 else 0.0

    # Standard errors for t-stats
    sigma2 = ss_res / (n - k - 1) if n > k + 1 else 0.0
    se = np.sqrt(np.diag(XtX_inv) * sigma2)

    # Portfolio variance decomposition
    portfolio_var = np.var(y, ddof=1)
    exposures = []
    total_explained = 0.0

    for i, name in enumerate(factor_names):
        beta_i = betas[i + 1]
        factor_var = np.var(factor_returns[name], ddof=1)
        contribution = (beta_i ** 2 * factor_var) / portfolio_var if portfolio_var > 0 else 0.0
        total_explained += contribution
        t_stat_i = betas[i + 1] / se[i + 1] if se[i + 1] > 0 else 0.0

        exposures.append(FactorExposure(
            factor_name=name,
            beta=float(beta_i),
            t_stat=float(t_stat_i),
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
