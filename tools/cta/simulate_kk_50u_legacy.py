#!/usr/bin/env python3
"""[已废弃] KK 1m 触价纸面回测 — 显著乐观，仅作对照。请用 simulate_kk_50u.py（vnpy）。"""
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
from tools.cta.research_vnpy_cta import _session_slice  # noqa: E402

EQUITY = 50.0
LO, HI = "2026-02-01", "2026-06-30"
KK = dict(
    compound=True,
    rth_only=True,
    eod_flat=True,
    exit_hour=15,
    exit_minute=55,
    no_entry_after_hour=12,
    no_entry_after_minute=0,
    slip_bps_entry=5.0,
    slip_bps_exit=5.0,
    max_notional_usdt=0.0,
)


def main() -> None:
    warnings.warn(
        "simulate_kk_50u_legacy 使用 1m 触价引擎，结果显著乐观；请用 simulate_kk_50u.py（vnpy 官方）",
        DeprecationWarning,
        stacklevel=1,
    )
    cfg = OrbConfig.from_env()
    meta = CTA_STRATEGIES["king_keltner"]
    symbols = [
        norm_symbol(s)
        for s in parse_symbol_list(Path(resolve_symbols_path()).read_text(encoding="utf-8"))
    ]
    rows: list[dict] = []

    print(
        f"=== KK LEGACY 1m touch | {LO}..{HI} | {EQUITY}U | DEPRECATED ===",
        flush=True,
    )
    print(flush=True)

    for sym in symbols:
        label = sym.replace("USDT", "")
        dates = [d for d in session_dates_from_cache(sym, cfg) if LO <= d <= HI]
        df1 = load_klines(sym, "1m")
        if df1.empty or not dates:
            print(f"  SKIP {label}: no data", flush=True)
            continue
        chunks = [_session_slice(df1, d, cfg) for d in dates]
        df = pd.concat([c for c in chunks if not c.empty], ignore_index=True)
        if df.empty:
            continue
        out = run_cta_backtest(
            df,
            strategy_fn=meta["fn"],
            orb_cfg=cfg,
            cta_cfg=cta_config_for_strategy(
                "king_keltner", equity_usdt=EQUITY, risk_pct=0.01, **KK
            ),
            warmup=25,
        )
        s = out["summary"]
        closes = [t for t in out["trades"] if t["event"] == "close"]
        wins = sum(1 for t in closes if float(t["pnl_usdt"]) > 0)
        n = len(closes)
        net = float(s["net_pnl_usdt"])
        end = float(s["equity_end"])
        rows.append(
            {
                "symbol": label,
                "opens": int(s["opens"]),
                "closes": n,
                "win_rate": round(100.0 * wins / n, 1) if n else 0.0,
                "net": round(net, 2),
                "equity_end": round(end, 2),
                "ret_pct": round(100.0 * (end - EQUITY) / EQUITY, 1),
            }
        )
        print(
            f"  {label:5s} start={EQUITY:5.0f}U end={end:7.2f}U net={net:+8.2f}U "
            f"opens={s['opens']:4d} win={wins}/{n}",
            flush=True,
        )

    if not rows:
        print("No results.")
        return

    pool_start = EQUITY * len(rows)
    pool_end = sum(r["equity_end"] for r in rows)
    pool_net = sum(r["net"] for r in rows)
    pool_ret = 100.0 * (pool_end - pool_start) / pool_start
    print()
    print(f"Pool start: {pool_start:.0f}U  end: {pool_end:.2f}U  net: {pool_net:+.2f}U  ret: {pool_ret:+.1f}%")


if __name__ == "__main__":
    main()
