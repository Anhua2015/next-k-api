#!/usr/bin/env python3
"""[已废弃] king_keltner 1m 触价排名 — 显著乐观，勿用于选池/上线决策。"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from env_loader import load_env_oi

load_env_oi()

import pandas as pd  # noqa: E402

from orb.core.config import OrbConfig  # noqa: E402
from orb.core.kline_cache import load_klines, norm_symbol, session_dates_from_cache  # noqa: E402
from orb.core.symbols import parse_symbol_list  # noqa: E402
from orb.cta.engine import run_cta_backtest  # noqa: E402
from orb.cta.registry import CTA_STRATEGIES, cta_config_for_strategy  # noqa: E402
from orb.kk.paths import resolve_symbols_path  # noqa: E402
from tools.cta.research_vnpy_cta_legacy import _session_slice  # noqa: E402


def main() -> None:
    warnings.warn(
        "rank_king_keltner_pool_legacy 使用 1m 触价引擎，结果显著乐观；请用 rank_king_keltner_pool.py（vnpy）",
        stacklevel=2,
    )
    cfg = OrbConfig.from_env()
    symbols = parse_symbol_list(Path(resolve_symbols_path()).read_text(encoding="utf-8"))
    meta = CTA_STRATEGIES["king_keltner"]
    lo, hi = "2026-02-01", "2026-06-30"

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

    print("\n=== king_keltner ranking [LEGACY 1m touch — DO NOT USE] ===")
    for r in sorted(rows, key=lambda x: -x["net"]):
        tag = "OK" if r["net"] > 100 else ("WEAK" if r["net"] > 0 else "AVOID")
        print(
            f"{r['symbol']:6s} net={r['net']:+8.0f}U ret={r['ret_pct']:+.0f}% "
            f"opens={r['opens']:4d} win={r['win_rate']:4.1f}% fees={r['fees']:6.0f}  [{tag}]"
        )


if __name__ == "__main__":
    main()
