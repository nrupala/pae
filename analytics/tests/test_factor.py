"""Tests for factor decomposition engine."""

import numpy as np
from pae.models.factor import decompose


def test_decompose_basic():
    """Test basic factor decomposition with synthetic data."""
    np.random.seed(42)
    n = 60

    mkt = np.random.normal(0.008, 0.04, n)
    smb = np.random.normal(0.002, 0.03, n)
    hml = np.random.normal(0.003, 0.025, n)

    alpha = 0.001
    portfolio = alpha + 1.2 * mkt + 0.3 * smb + 0.1 * hml + np.random.normal(0, 0.01, n)

    result = decompose(
        portfolio_returns=portfolio,
        factor_returns={"MKT": mkt, "SMB": smb, "HML": hml},
    )

    assert result.r_squared > 0.5, f"R-squared too low: {result.r_squared}"
    assert len(result.exposures) == 3

    mkt_exposure = next(e for e in result.exposures if e.factor_name == "MKT")
    assert abs(mkt_exposure.beta - 1.2) < 0.3, f"MKT beta off: {mkt_exposure.beta}"


def test_decompose_single_factor():
    """Test decomposition with a single factor."""
    np.random.seed(123)
    n = 100
    mkt = np.random.normal(0.01, 0.05, n)
    portfolio = 0.8 * mkt + np.random.normal(0, 0.005, n)

    result = decompose(
        portfolio_returns=portfolio,
        factor_returns={"MKT": mkt},
    )

    assert result.r_squared > 0.8
    assert len(result.exposures) == 1
    assert abs(result.exposures[0].beta - 0.8) < 0.15
