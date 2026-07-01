#!/usr/bin/env python3
"""king_keltner 全池逐标的排名。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from env_loader import load_env_oi
from orb.core.config import OrbConfig
from orb.core.kline_cache import load_klines, norm_symbol
from orb.cta.engine import run_cta_backtest
from orb.cta.registry import CTA_STRATEGIES, cta_config_for_strategy
from orb.core.symbols import parse_symbol_list
from orb.kk.paths import resolve_symbols_path
from tools.cta.research_vnpy_cta import _session_slice
from orb.core.kline_cache import session_dates_from_cache
import pandas as pd

load_env_oi()
cfg = OrbConfig.from_env()
symbols = parse_symbol_list(Path(resolve_symbols_path()).read_text(encoding="utf-8"))
meta = CTA_STRATEGIES["king_keltner"]
lo, hi = "2026-02-01", "2026-06-30"

# 与实盘 KK 对齐：RTH + 15:55 EOD、复利、5bps 滑点、无名义封顶
KK_CTA_KW = dict(
    compound=True,
    rth_only=True,
    eod_flat=True,
    exit_hour=15,
    exit_minute=55,
    slip_bps_entry=5.0,
    slip_bps_exit=5.0,
    max_notional_usdt=0.0,
)

rows = []
for sym in symbols:
    sym = norm_symbol(sym)
    dates = [d for d in session_dates_from_cache(sym, cfg) if lo <= d <= hi]
    df1 = load_klines(sym, "1m")
    if df1.empty or not dates:
        continue
    chunks = [_session_slice(df1, d, cfg) for d in dates]
    df = pd.concat([c for c in chunks if not c.empty], ignore_index=True)
    if df.empty:
        continue
    out = run_cta_backtest(
        df,
        strategy_fn=meta["fn"],
        orb_cfg=cfg,
        cta_cfg=cta_config_for_strategy("king_keltner", equity_usdt=1000, risk_pct=0.01, **KK_CTA_KW),
        warmup=25,
    )
    s = out["summary"]
    closes = [t for t in out["trades"] if t["event"] == "close"]
    wins = sum(1 for t in closes if float(t["pnl_usdt"]) > 0)
    n = len(closes)
    gross = sum(float(t["pnl_usdt_gross"]) for t in closes)
    fees = sum(float(t["fee_usdt"]) for t in closes)
    notions = [float(t["notional_usdt"]) for t in out["trades"] if t["event"] == "open"]
    net = float(s["net_pnl_usdt"])
    rows.append(
        {
            "symbol": sym.replace("USDT", ""),
            "sessions": len(dates),
            "opens": s["opens"],
            "win_rate": round(100 * wins / n, 1) if n else 0,
            "gross": round(gross, 2),
            "fees": round(fees, 2),
            "net": round(net, 2),
            "ret_pct": round(net / 10.0, 1),
            "avg_notional": round(sum(notions) / len(notions), 0) if notions else 0,
        }
    )
    print(f"  {sym.replace('USDT',''):6s} net={rows[-1]['net']:+.0f} opens={rows[-1]['opens']}", flush=True)

print("\n=== king_keltner ranking (实盘口径: RTH+EOD, 复利, 5bps, 无封顶) ===")
for r in sorted(rows, key=lambda x: -x["net"]):
    tag = "OK" if r["net"] > 100 else ("WEAK" if r["net"] > 0 else "AVOID")
    print(
        f"{r['symbol']:6s} net={r['net']:+8.0f}U ret={r['ret_pct']:+.0f}% "
        f"opens={r['opens']:4d} win={r['win_rate']:4.1f}% fees={r['fees']:6.0f}  [{tag}]"
    )
