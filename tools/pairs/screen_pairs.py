#!/usr/bin/env python3
"""Screen candidate pairs (2x, 5000U, maker 2bps)."""
from __future__ import annotations

import itertools
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from orb.core.backtest import _load_range
from orb.core.kline_cache import load_klines, save_klines, has_kline_cache
from pairs.backtest import PairsBacktestConfig, align_leg_closes, run_pairs_backtest
from pairs.kalman import kalman_hedge_ratio, kalman_zscore

DAYS = 180
CAP = 5000
LEV = 2
COST = 2
end_ms = int(time.time() * 1000)
start_ms = end_ms - int(DAYS * 86_400_000)

# 1h 已有 + 可拉取的 crypto
LOCAL_1H = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "COINUSDT", "MSTRUSDT"]
FETCH_CRYPTO = ["BNBUSDT", "DOGEUSDT", "LINKUSDT", "AVAXUSDT", "XRPUSDT"]


def load_leg(sym: str, fetch: bool = False):
    df = load_klines(sym, "1h", start_ms=start_ms, end_ms=end_ms)
    if (df.empty or len(df) < 100) and fetch:
        fresh = _load_range(sym, "1h", start_ms, end_ms)
        if not fresh.empty:
            save_klines(sym, "1h", fresh)
            df = fresh
    return df


def screen(leg1: str, leg2: str, entry_z: float = 2.5, delta: float = 1e-5) -> dict | None:
    df1, df2 = load_leg(leg1), load_leg(leg2)
    if df1.empty or df2.empty:
        return None
    p = align_leg_closes(df1, df2)
    if len(p) < 100:
        return None
    b, _, _, e, s, _ = kalman_hedge_ratio(p.leg1, p.leg2, delta=delta, r_noise=0)
    z = kalman_zscore(e, s)
    zmax = float(z.abs().max())
    cfg = PairsBacktestConfig(
        leg1, leg2, "1h", delta, 0, entry_z, 0.0, COST, 63, None, None, CAP, 0.5, LEV,
    )
    w = run_pairs_backtest(p, cfg)["wallet"]
    return {
        "pair": f"{leg1}/{leg2}",
        "bars": len(p),
        "zmax": round(zmax, 2),
        "trips": w["round_trips"],
        "pnl": w["total_pnl_usdt"],
        "ret_pct": w["total_return_pct"],
        "dd": w["max_drawdown_usdt"],
        "wr": w["win_rate_trades_pct"],
    }


print("Fetching extra crypto 1h if missing...")
for sym in FETCH_CRYPTO:
    if not has_kline_cache(sym, "1h"):
        load_leg(sym, fetch=True)

all_syms = list(dict.fromkeys(LOCAL_1H + FETCH_CRYPTO))
available = [s for s in all_syms if not load_leg(s).empty]
print(f"1h available: {', '.join(available)}\n")

rows = []
for a, b in itertools.combinations(sorted(available), 2):
    # crypto defaults
    ez, delta = 2.5, 1e-5
    if "COIN" in a or "COIN" in b or "MSTR" in a or "MSTR" in b:
        ez, delta = 1.5, 1e-5
    r = screen(a, b, entry_z=ez, delta=delta)
    if r:
        rows.append(r)

rows.sort(key=lambda x: -x["pnl"])
print(f"{'Pair':<22} {'bars':>5} {'zmax':>5} {'trips':>5} {'PnL':>9} {'ret%':>7} {'DD':>8} {'WR':>5}")
print("-" * 72)
for r in rows:
    flag = " *" if r["trips"] == 0 else ""
    print(
        f"{r['pair']:<22} {r['bars']:5d} {r['zmax']:5.2f} {r['trips']:5d} "
        f"{r['pnl']:+8.0f}U {r['ret_pct']:+6.1f}% {r['dd']:7.0f}U {r['wr']:4.0f}%{flag}"
    )

print("\n* trips=0: Kalman z 不够大，基本不可交易")
print("\nTop tradeable (trips>=5, pnl>0):")
ok = [r for r in rows if r["trips"] >= 5 and r["pnl"] > 0]
for r in ok[:8]:
    print(f"  {r['pair']}: {r['pnl']:+.0f}U ({r['ret_pct']:+.1f}%) trips={r['trips']}")
