#!/usr/bin/env python3
"""Quick parameter sweep for pairs wallet PnL."""
from __future__ import annotations

import itertools
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from orb.core.kline_cache import load_klines
from pairs.backtest import PairsBacktestConfig, align_leg_closes, run_pairs_backtest

end_ms = int(time.time() * 1000)
start_ms = end_ms - int(180 * 86_400_000)
prices = align_leg_closes(
    load_klines("BTCUSDT", "1h", start_ms=start_ms, end_ms=end_ms),
    load_klines("ETHUSDT", "1h", start_ms=start_ms, end_ms=end_ms),
)

BASE = dict(
    leg1="BTCUSDT",
    leg2="ETHUSDT",
    interval="1h",
    delta=1e-4,
    r_noise=0,
    cost_bps=2,
    halt_p_trace_pct=None,
    initial_capital_usdt=10_000.0,
    deploy_pct=0.5,
)


def run(entry_z: float, exit_z: float, **kw) -> dict:
    params = {**BASE, "entry_z": entry_z, "exit_z": exit_z, **kw}
    cfg = PairsBacktestConfig(**params)
    w = run_pairs_backtest(prices, cfg)["wallet"]
    return w


print("=== entry_z × exit_z (maker 2bps) ===")
rows = []
for ez, xz in itertools.product([1.5, 2.0, 2.5, 3.0], [0.0, 0.5, 1.0]):
    w = run(ez, xz)
    rows.append((ez, xz, w["round_trips"], w["total_pnl_usdt"], w["total_fees_usdt"], w["win_rate_trades_pct"]))
rows.sort(key=lambda r: -r[3])
for r in rows[:8]:
    print(f"entry={r[0]} exit={r[1]} trips={r[2]:3d} pnl={r[3]:+8.1f}U fees={r[4]:6.0f}U wr={r[5]:.0f}%")

print("\n=== deploy_pct (entry=2.5 exit=0.5 maker 2bps) ===")
for dp in [0.3, 0.5, 0.7, 1.0]:
    w = run(2.5, 0.5, deploy_pct=dp)
    print(f"deploy={dp} trips={w['round_trips']} pnl={w['total_pnl_usdt']:+.1f}U maxDD={w['max_drawdown_usdt']:.0f}U")

print("\n=== halt_p_trace_pct filter (entry=2.5 exit=0.5 maker 2bps) ===")
for halt in [None, 90, 95, 99]:
    w = run(2.5, 0.5, halt_p_trace_pct=halt)
    print(f"halt={halt} trips={w['round_trips']} pnl={w['total_pnl_usdt']:+.1f}U wr={w['win_rate_trades_pct']:.0f}%")

print("\n=== delta (entry=2.5 exit=0.5 maker 2bps) ===")
for d in [1e-5, 1e-4, 5e-4, 1e-3]:
    w = run(2.5, 0.5, delta=d)
    print(f"delta={d} trips={w['round_trips']} pnl={w['total_pnl_usdt']:+.1f}U")

print("\n=== baseline current config (taker 4bps entry=2 exit=0) ===")
w = run(2.0, 0.0, cost_bps=4)
print(f"trips={w['round_trips']} pnl={w['total_pnl_usdt']:+.1f}U fees={w['total_fees_usdt']:.0f}U")
