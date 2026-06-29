"""Pairs trading (Kalman hedge ratio + spread signals)."""

from pairs.backtest import PairsBacktestConfig, run_pairs_backtest
from pairs.kalman import kalman_hedge_ratio, kalman_zscore
from pairs.walk_forward import run_walk_forward

__all__ = [
    "PairsBacktestConfig",
    "kalman_hedge_ratio",
    "kalman_zscore",
    "run_pairs_backtest",
    "run_walk_forward",
]
