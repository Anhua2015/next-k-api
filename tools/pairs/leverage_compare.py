#!/usr/bin/env python3
"""2x / 3x / 5x leverage comparison (5000U per pair, total 10k)."""
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

CAP = 5_000
DEPLOY = 0.5
COST = 2
LEVERS = (1, 2, 3, 5)

end_ms = int(time.time() * 1000)
start_ms = end_ms - int(180 * 86_400_000)

prices = {}
for name, (leg1, leg2, *_rest) in CONFIGS.items():
    prices[name] = align_leg_closes(
        load_klines(leg1, "1h", start_ms=start_ms, end_ms=end_ms),
        load_klines(leg2, "1h", start_ms=start_ms, end_ms=end_ms),
    )


def run_pair(name: str, lev: float) -> dict:
    leg1, leg2, delta, ez, xz = CONFIGS[name]
    cfg = PairsBacktestConfig(
        leg1=leg1,
        leg2=leg2,
        interval="1h",
        delta=delta,
        r_noise=0,
        entry_z=ez,
        exit_z=xz,
        cost_bps=COST,
        halt_p_trace_pct=None,
        initial_capital_usdt=CAP,
        deploy_pct=DEPLOY,
        leverage=lev,
    )
    return run_pairs_backtest(prices[name], cfg)["wallet"]


print("180d | 每 pair 5000U | deploy=0.5 | maker 2bps | 总本金 10000U\n")
print(f"{'杠杆':>4} | {'BTC/ETH PnL':>12} {'DD':>8} | {'COIN/MSTR PnL':>12} {'DD':>8} | {'合计PnL':>10} {'合计%':>8} {'安全':>4}")
print("-" * 78)

rows = []
for lev in LEVERS:
    wb = run_pair("BTC/ETH", lev)
    wc = run_pair("COIN/MSTR", lev)
    combined = wb["total_pnl_usdt"] + wc["total_pnl_usdt"]
    pct = combined / 10_000 * 100
    dd_max = max(abs(wb["max_drawdown_usdt"]), abs(wc["max_drawdown_usdt"]))
    safe = "ok" if dd_max < CAP else "LIQ"
    rows.append((lev, wb, wc, combined, pct, safe))
    print(
        f"{lev:>3}x | {wb['total_pnl_usdt']:+11.0f}U {wb['max_drawdown_usdt']:7.0f} | "
        f"{wc['total_pnl_usdt']:+11.0f}U {wc['max_drawdown_usdt']:7.0f} | "
        f"{combined:+9.0f}U {pct:+7.1f}% {safe:>4}"
    )

print("\n--- 明细 (2x / 3x / 5x) ---")
for lev, wb, wc, combined, pct, safe in rows:
    if lev not in (2, 3, 5):
        continue
    print(f"\n[{lev}x] 合计 {combined:+.0f}U ({pct:+.1f}%) | 总本金 10000U -> 期末约 {10000+combined:.0f}U")
    for label, w in [("BTC/ETH", wb), ("COIN/MSTR", wc)]:
        print(
            f"  {label}: {w['total_pnl_usdt']:+.0f}U ({w['total_return_pct']:+.1f}%) "
            f"fees={w['total_fees_usdt']:.0f}U DD={w['max_drawdown_usdt']:.0f}U "
            f"trips={w['round_trips']} WR={w['win_rate_trades_pct']:.0f}%"
        )
