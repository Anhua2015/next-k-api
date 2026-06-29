#!/usr/bin/env python3
"""Leverage scenario calculator for pairs."""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from orb.core.kline_cache import load_klines
from pairs.backtest import PairsBacktestConfig, align_leg_closes, run_pairs_backtest

CONFIGS = {
    "BTC/ETH": ("BTCUSDT", "ETHUSDT", 3e-5, 3.0, 0.25),
    "COIN/MSTR": ("COINUSDT", "MSTRUSDT", 1e-5, 1.5, 0.0),
}

end_ms = int(time.time() * 1000)
start_ms = end_ms - int(180 * 86_400_000)

SCENARIOS = [
    ("参考: 1万U/pair 1x deploy0.5", 10_000, 0.5, 1),
    ("1万U总计: 每pair 5000U 1x deploy0.5", 5_000, 0.5, 1),
    ("1万U总计: 每pair 5000U 10x deploy0.5", 5_000, 0.5, 10),
    ("1万U总计: 每pair 5000U 10x deploy1.0(满杠杆)", 5_000, 1.0, 10),
]

print("180d | maker 2bps | tuned params | 两 pair 独立回测后相加\n")
print(f"{'场景':<42} {'BTC/ETH':>12} {'COIN/MSTR':>12} {'合计':>12} {'合计%':>8}")
print("-" * 90)

for label, cap, deploy, lev in SCENARIOS:
    pnls = []
    for name, (leg1, leg2, delta, ez, xz) in CONFIGS.items():
        p = align_leg_closes(
            load_klines(leg1, "1h", start_ms=start_ms, end_ms=end_ms),
            load_klines(leg2, "1h", start_ms=start_ms, end_ms=end_ms),
        )
        cfg = PairsBacktestConfig(
            leg1=leg1,
            leg2=leg2,
            interval="1h",
            delta=delta,
            r_noise=0,
            entry_z=ez,
            exit_z=xz,
            cost_bps=2,
            halt_p_trace_pct=None,
            initial_capital_usdt=cap,
            deploy_pct=deploy,
            leverage=lev,
        )
        w = run_pairs_backtest(p, cfg)["wallet"]
        pnls.append(w)
    total_cap = cap * 2 if "总计" in label else cap
    if "参考" in label:
        total_cap = 20_000
    combined = pnls[0]["total_pnl_usdt"] + pnls[1]["total_pnl_usdt"]
    base_cap = 10_000 if "总计" in label else 20_000
    pct = combined / base_cap * 100
    print(
        f"{label:<42} {pnls[0]['total_pnl_usdt']:+11.0f}U {pnls[1]['total_pnl_usdt']:+11.0f}U "
        f"{combined:+11.0f}U {pct:+7.1f}%"
    )

print("\n--- 用户场景详情: 5000U/pair, 10x, deploy=0.5 ---")
for name, (leg1, leg2, delta, ez, xz) in CONFIGS.items():
    p = align_leg_closes(
        load_klines(leg1, "1h", start_ms=start_ms, end_ms=end_ms),
        load_klines(leg2, "1h", start_ms=start_ms, end_ms=end_ms),
    )
    cfg = PairsBacktestConfig(
        leg1=leg1, leg2=leg2, interval="1h", delta=delta, r_noise=0,
        entry_z=ez, exit_z=xz, cost_bps=2, halt_p_trace_pct=None,
        initial_capital_usdt=5_000, deploy_pct=0.5, leverage=10,
    )
    w = run_pairs_backtest(p, cfg)["wallet"]
print("\n--- 5000U/pair 不同杠杆 maxDD ---")
for lev in (1, 2, 3, 4, 5, 10):
    p = align_leg_closes(
        load_klines("BTCUSDT", "1h", start_ms=start_ms, end_ms=end_ms),
        load_klines("ETHUSDT", "1h", start_ms=start_ms, end_ms=end_ms),
    )
    w = run_pairs_backtest(
        p,
        PairsBacktestConfig(
            "BTCUSDT", "ETHUSDT", "1h", 3e-5, 0, 3.0, 0.25, 2, 63, None, None, 5000, 0.5, lev,
        ),
    )["wallet"]
    dd_pct = abs(w["max_drawdown_usdt"]) / 5000 * 100
    liq = "LIQ" if abs(w["max_drawdown_usdt"]) >= 5000 else "ok"
    print(
        f"lev={lev:2d} pnl={w['total_pnl_usdt']:+7.0f}U ({w['total_return_pct']:+5.1f}%) "
        f"maxDD={w['max_drawdown_usdt']:.0f}U ({dd_pct:.0f}%) {liq}"
    )
