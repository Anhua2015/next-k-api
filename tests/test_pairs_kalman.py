#!/usr/bin/env python3
"""Unit tests for pairs.kalman."""

import numpy as np
import pytest

from pairs.kalman import kalman_hedge_ratio, kalman_zscore


def test_kalman_hedge_ratio_length():
    n = 100
    p2 = np.linspace(100, 110, n)
    p1 = 1.5 * p2 + 3.0 + np.random.default_rng(0).normal(0, 0.5, n)
    beta, intercept, spread, e, s, p_trace = kalman_hedge_ratio(p1, p2, delta=1e-4)
    assert len(beta) == n
    assert len(spread) == n
    assert float(beta.iloc[-1]) == pytest.approx(1.5, abs=0.15)


def test_kalman_zscore():
    import pandas as pd

    e = pd.Series([1.0, -2.0, 0.0])
    s = pd.Series([1.0, 4.0, 1.0])
    z = kalman_zscore(e, s)
    assert float(z.iloc[0]) == pytest.approx(1.0)
    assert float(z.iloc[1]) == pytest.approx(-1.0)


def test_wallet_pnl_long_spread():
    """Long spread profits when leg1 outperforms leg2."""
    import pandas as pd

    from pairs.backtest import PairsBacktestConfig, wallet_pnl_usdt

    p2 = pd.Series([100.0, 100.0, 100.0, 100.0])
    p1 = pd.Series([200.0, 201.0, 202.0, 202.0])
    beta = pd.Series([2.0, 2.0, 2.0, 2.0])  # p1 = 2*p2 → dollar-neutral q2*p2 = notional
    position = pd.Series([0.0, 1.0, 1.0, 0.0])  # long spread bar 2-3
    cfg = PairsBacktestConfig(
        leg1="A",
        leg2="B",
        cost_bps=0.0,
        initial_capital_usdt=10_000.0,
        deploy_pct=0.5,
    )
    w = wallet_pnl_usdt(p1, p2, beta, position, cfg)
    assert w["total_pnl_usdt"] > 0
