#!/usr/bin/env python3
"""king_keltner: 原版 vs 仅RTH+强制EOD 对比。"""
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
meta = CTA_STRATEGIES["king_keltner"]
lo, hi = "2026-02-01", "2026-06-30"

MODES = [
    ("baseline_overnight", "原版(可隔夜)", dict(rth_only=True, eod_flat=False)),
    ("rth_eod", "仅RTH+强制EOD", dict(rth_only=True, eod_flat=True)),
]


def run_sym(sym: str, dates: list[str], *, rth_only: bool, eod_flat: bool) -> dict:
    sym = norm_symbol(sym)
    df1 = load_klines(sym, "1m")
    if df1.empty:
        return {}
    chunks = [_session_slice(df1, d, cfg) for d in dates]
    df = pd.concat([c for c in chunks if not c.empty], ignore_index=True).sort_values("open_time")
    out = run_cta_backtest(
        df,
        strategy_fn=meta["fn"],
        orb_cfg=cfg,
        cta_cfg=cta_config_for_strategy(
            "king_keltner",
            equity_usdt=1000,
            risk_pct=0.01,
            compound=False,
            rth_only=rth_only,
            eod_flat=eod_flat,
            exit_hour=15,
            exit_minute=55,
        ),
        warmup=25,
    )
    closes = [t for t in out["trades"] if t["event"] == "close"]
    eod_n = sum(1 for t in closes if t.get("outcome") == "eod")
    gross = sum(float(t["pnl_usdt_gross"]) for t in closes)
    fees = sum(float(t["fee_usdt"]) for t in closes)
    s = out["summary"]
    holds = []
    opens = [t for t in out["trades"] if t["event"] == "open"]
    oi = 0
    for c in closes:
        while oi < len(opens) and opens[oi]["ms"] > c["ms"]:
            oi += 1
        if oi < len(opens):
            holds.append((int(c["ms"]) - int(opens[oi]["ms"])) / 60_000)
            oi += 1
    return {
        "opens": int(s["opens"]),
        "closes": len(closes),
        "eod_closes": eod_n,
        "gross": round(gross, 2),
        "fees": round(fees, 2),
        "net": float(s["net_pnl_usdt"]),
        "equity_end": float(s["equity_end"]),
        "avg_hold_min": round(sum(holds) / len(holds), 1) if holds else 0,
        "max_hold_min": round(max(holds), 1) if holds else 0,
    }


def main() -> None:
    symbols = parse_symbol_list(Path(resolve_symbols_path()).read_text(encoding="utf-8"))
    focus = ["COIN", "CRCL", "INTC", "TSLA", "QQQ"]
    ref = norm_symbol(symbols[0])
    dates = [d for d in session_dates_from_cache(ref, cfg) if lo <= d <= hi]

    print("king_keltner | 1000U | 1% risk | compound | 8bps | Feb-Jun 2026")
    print("EOD = 15:55 ET 强平 | RTH = 09:30-16:00 ET 内才跑 bar\n")

    for mode_key, mode_label, kw in MODES:
        print(f"=== {mode_label} ({mode_key}) ===")
        print(f"{'sym':6s} {'opens':>5s} {'eod':>4s} {'gross':>9s} {'fees':>7s} {'net':>9s} {'ret':>7s} {'avg_hold':>8s}")
        total_net = 0.0
        for tag in focus:
            sym = norm_symbol(tag)
            sym_dates = [d for d in session_dates_from_cache(sym, cfg) if lo <= d <= hi]
            r = run_sym(sym, sym_dates, **kw)
            if not r:
                continue
            ret = (r["equity_end"] / 1000 - 1) * 100
            total_net += r["net"]
            print(
                f"{tag:6s} {r['opens']:5d} {r['eod_closes']:4d} {r['gross']:+9.2f} {r['fees']:7.2f} "
                f"{r['net']:+9.2f} {ret:+6.1f}% {r['avg_hold_min']:7.0f}m"
            )
        print(f"{'TOTAL':6s} {'':5s} {'':4s} {'':9s} {'':7s} {total_net:+9.2f}\n")


if __name__ == "__main__":
    main()
