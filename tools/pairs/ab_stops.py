#!/usr/bin/env python3
"""Test stop-loss / time-stop for pairs."""
from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from orb.core.kline_cache import load_klines
from pairs.backtest import (
    PairsBacktestConfig,
    align_leg_closes,
    wallet_pnl_usdt,
    _leg_notional_usdt,
    _tx_fee_usdt,
)
from pairs.kalman import kalman_hedge_ratio, kalman_zscore


def signals_with_stops(
    zscore: pd.Series,
    *,
    entry_z: float,
    exit_z: float,
    stop_z_extra: Optional[float] = None,
    max_hold_bars: Optional[int] = None,
) -> pd.Series:
    pos = np.zeros(len(zscore), dtype=float)
    current = 0.0
    entry_i = 0
    for i in range(1, len(zscore)):
        z = float(zscore.iloc[i])
        if current == 0.0:
            if z > entry_z:
                current = -1.0
                entry_i = i
            elif z < -entry_z:
                current = 1.0
                entry_i = i
        else:
            held = i - entry_i
            stop = False
            if stop_z_extra is not None:
                if current == -1.0 and z > entry_z + stop_z_extra:
                    stop = True
                if current == 1.0 and z < -(entry_z + stop_z_extra):
                    stop = True
            if max_hold_bars is not None and held >= max_hold_bars:
                stop = True
            if stop:
                current = 0.0
            elif current == -1.0 and z < exit_z:
                current = 0.0
            elif current == 1.0 and z > -exit_z:
                current = 0.0
        pos[i] = current
    return pd.Series(pos, index=zscore.index, name="position")


def run_pair(leg1, leg2, entry_z, exit_z, delta, *, stop_z_extra=None, max_hold=None):
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - int(180 * 86_400_000)
    p = align_leg_closes(
        load_klines(leg1, "1h", start_ms=start_ms, end_ms=end_ms),
        load_klines(leg2, "1h", start_ms=start_ms, end_ms=end_ms),
    )
    beta, _, _, e, s, _ = kalman_hedge_ratio(p.leg1, p.leg2, delta=delta, r_noise=0)
    z = kalman_zscore(e, s)
    pos = signals_with_stops(
        z, entry_z=entry_z, exit_z=exit_z,
        stop_z_extra=stop_z_extra, max_hold_bars=max_hold,
    )
    cfg = PairsBacktestConfig(
        leg1, leg2, "1h", delta, 0, entry_z, exit_z, 2, 63, None, None, 5000, 0.5, 2,
    )
    w = wallet_pnl_usdt(p.leg1, p.leg2, beta, pos, cfg)
    return w


PAIRS = [
    ("BTCUSDT", "ETHUSDT", 3.0, 0.25, 3e-5),
    ("COINUSDT", "MSTRUSDT", 1.5, 0.0, 1e-5),
]

SCENARIOS = [
    ("baseline", None, None),
    ("stop_z+1.0", 1.0, None),
    ("stop_z+1.5", 1.5, None),
    ("max24h", None, 24),
    ("max48h", None, 48),
    ("stop1+max48h", 1.0, 48),
]

print("2x 5000U | 180d | maker 2bps\n")
for name, leg1, leg2, ez, xz, d in [
    (f"{a}/{b}", a, b, ez, xz, d) for a, b, ez, xz, d in PAIRS
]:
    print(f"=== {name} ===")
    for label, sz, mh in SCENARIOS:
        w = run_pair(leg1, leg2, ez, xz, d, stop_z_extra=sz, max_hold=mh)
        print(
            f"  {label:14} trips={w['round_trips']:3d} pnl={w['total_pnl_usdt']:+8.0f}U "
            f"DD={w['max_drawdown_usdt']:7.0f}U WR={w['win_rate_trades_pct']:.0f}%"
        )
    print()

# June losers pattern
print("June losers (baseline): long hold + spread diverge")
print("  BTC -258U: 58h hold, short spread z 3.46->-0.04")
print("  BTC -83U:  50h hold, long spread z -4.06->-0.03")
print("  COIN -324U: 20h, short spread, COIN rallied vs MSTR")
print("  COIN -196U: 12h, short spread, spread widened")
