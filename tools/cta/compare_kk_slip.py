#!/usr/bin/env python3
"""King Keltner RTH+EOD：无滑点 vs 5bps 滑点对比。"""
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
from orb.kk.paths import resolve_kk_symbols_path
from orb.core.symbols import parse_symbol_list
from tools.cta.research_vnpy_cta import _session_slice
from orb.core.kline_cache import session_dates_from_cache
import pandas as pd

load_env_oi()
cfg = OrbConfig.from_env()
cfg.risk_pct = 0.01
meta = CTA_STRATEGIES["king_keltner"]
lo, hi = "2026-02-01", "2026-06-30"

SLIP_MODES = [
    ("ideal", "无滑点", 0.0, 0.0),
    ("live", "5bps 入/出", 5.0, 5.0),
]


def run_one(sym: str, dates: list[str], *, slip_in: float, slip_out: float) -> dict:
    sym = norm_symbol(sym)
    df1 = load_klines(sym, "1m")
    if df1.empty or not dates:
        return {}
    chunks = [_session_slice(df1, d, cfg) for d in dates]
    df = pd.concat([c for c in chunks if not c.empty], ignore_index=True).sort_values("open_time")
    if df.empty:
        return {}
    out = run_cta_backtest(
        df,
        strategy_fn=meta["fn"],
        orb_cfg=cfg,
        cta_cfg=cta_config_for_strategy(
            "king_keltner",
            equity_usdt=1000,
            risk_pct=0.01,
            compound=False,
            rth_only=True,
            eod_flat=True,
            exit_hour=15,
            exit_minute=55,
            slip_bps_entry=slip_in,
            slip_bps_exit=slip_out,
        ),
        warmup=25,
    )
    closes = [t for t in out["trades"] if t["event"] == "close"]
    gross = sum(float(t["pnl_usdt_gross"]) for t in closes)
    fees = sum(float(t["fee_usdt"]) for t in closes)
    s = out["summary"]
    wins = sum(1 for t in closes if float(t["pnl_usdt"]) > 0)
    return {
        "opens": int(s["opens"]),
        "closes": len(closes),
        "eod": sum(1 for t in closes if t.get("outcome") == "eod"),
        "gross": round(gross, 2),
        "fees": round(fees, 2),
        "net": round(float(s["net_pnl_usdt"]), 2),
        "equity_end": round(float(s["equity_end"]), 2),
        "win_rate": round(100.0 * wins / len(closes), 1) if closes else 0.0,
    }


def main() -> None:
    syms = parse_symbol_list(resolve_kk_symbols_path().read_text(encoding="utf-8"))
    print("king_keltner | RTH + EOD 15:55 | 1000U | 1% risk | compound | taker 8bps RT")
    print(f"Feb-Jun 2026 | pool={len(syms)} symbols\n")

    totals = {k: 0.0 for k, _, _, _ in SLIP_MODES}
    for slip_key, slip_label, slip_in, slip_out in SLIP_MODES:
        print(f"=== {slip_label} ({slip_key}) ===")
        print(f"{'sym':6s} {'opens':>5s} {'eod':>4s} {'gross':>9s} {'fees':>7s} {'net':>9s} {'ret':>7s} {'win%':>5s}")
        for tag in syms:
            sym = norm_symbol(tag)
            dates = [d for d in session_dates_from_cache(sym, cfg) if lo <= d <= hi]
            r = run_one(sym, dates, slip_in=slip_in, slip_out=slip_out)
            if not r:
                print(f"{tag.replace('USDT',''):6s}  no data")
                continue
            ret = (r["equity_end"] / 1000 - 1) * 100
            totals[slip_key] += r["net"]
            print(
                f"{tag.replace('USDT',''):6s} {r['opens']:5d} {r['eod']:4d} {r['gross']:+9.2f} "
                f"{r['fees']:7.2f} {r['net']:+9.2f} {ret:+6.1f}% {r['win_rate']:5.1f}"
            )
        print(f"{'TOTAL':6s} {'':5s} {'':4s} {'':9s} {'':7s} {totals[slip_key]:+9.2f}\n")

    ideal, live = totals["ideal"], totals["live"]
    drag = live - ideal
    print("--- 滑点影响（全池合计）---")
    print(f"  无滑点 net: {ideal:+.2f} U")
    print(f"  5bps net:   {live:+.2f} U")
    print(f"  滑点拖累:   {drag:+.2f} U ({100*drag/abs(ideal) if ideal else 0:.1f}% of ideal gross edge)")


if __name__ == "__main__":
    main()
