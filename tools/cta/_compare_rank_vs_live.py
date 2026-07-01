#!/usr/bin/env python3
"""对比 rank 脚本口径 vs 实盘 KK 口径。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import pandas as pd
from env_loader import load_env_oi

load_env_oi()

from orb.core.config import OrbConfig
from orb.core.kline_cache import load_klines, norm_symbol, session_dates_from_cache
from orb.core.symbols import parse_symbol_list
from orb.kk.paths import resolve_kk_symbols_path
from orb.cta.engine import run_cta_backtest
from orb.cta.registry import CTA_STRATEGIES, cta_config_for_strategy
from tools.cta.research_vnpy_cta import _session_slice

cfg = OrbConfig.from_env()
meta = CTA_STRATEGIES["king_keltner"]
lo, hi = "2026-02-01", "2026-06-30"
syms = parse_symbol_list(resolve_kk_symbols_path().read_text(encoding="utf-8"))

MODES = [
    (
        "wrong_rank",
        "上次排名脚本(compound+EOD关)",
        dict(
            compound=True,
            rth_only=True,
            eod_flat=False,
            slip_bps_entry=5.0,
            slip_bps_exit=5.0,
            max_notional_usdt=0,
        ),
    ),
    (
        "live_kk",
        "实盘KK(RTH+EOD+复利+无封顶)",
        dict(
            compound=True,
            rth_only=True,
            eod_flat=True,
            exit_hour=15,
            exit_minute=55,
            slip_bps_entry=5.0,
            slip_bps_exit=5.0,
            max_notional_usdt=0,
        ),
    ),
]


def run_mode(kw: dict) -> list[tuple[str, float, float, int]]:
    rows: list[tuple[str, float, float, int]] = []
    for sym in syms:
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
            cta_cfg=cta_config_for_strategy("king_keltner", equity_usdt=1000, risk_pct=0.01, **kw),
            warmup=25,
        )
        s = out["summary"]
        net = float(s["net_pnl_usdt"])
        if kw["compound"]:
            ret = (float(s["equity_end"]) / 1000 - 1) * 100
        else:
            ret = net / 10.0
        rows.append((sym.replace("USDT", ""), net, ret, int(s["opens"])))
    rows.sort(key=lambda x: -x[1])
    return rows


for mode_key, mode_label, kw in MODES:
    rows = run_mode(kw)
    print(f"\n=== {mode_label} ===")
    print(f"{'sym':6s} {'net':>10s} {'ret%':>8s} {'opens':>6s}")
    for tag, net, ret, opens in rows[:10]:
        print(f"{tag:6s} {net:+10.0f}U {ret:+8.1f}% {opens:6d}")
    if rows:
        print(f"全池合计 net {sum(x[1] for x in rows):+.0f}U")
