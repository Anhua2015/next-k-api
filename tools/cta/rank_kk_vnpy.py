#!/usr/bin/env python3
"""king_keltner 全池排名 — 默认 vnpy BacktestingEngine（与实盘同引擎）。"""
from __future__ import annotations

import argparse
import sys
from datetime import timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from env_loader import load_env_oi

load_env_oi()

import pandas as pd  # noqa: E402

from orb.core.config import OrbConfig  # noqa: E402
from orb.core.kline_cache import norm_symbol, session_dates_from_cache  # noqa: E402
from orb.core.symbols import parse_symbol_list  # noqa: E402
from orb.kk.config import KKConfig  # noqa: E402
from orb.kk.paths import resolve_symbols_path  # noqa: E402
from orb.kk.vnpy.backtest import klines_df_to_bars, run_kk_vnpy_backtest, trades_to_rows  # noqa: E402
from orb.kk.vnpy.binance_gateway import kk_vt_symbol  # noqa: E402
from tools.cta.simulate_kk_vnpy_50u import (  # noqa: E402
    _load_symbol_df,
    _range_engine_dates,
    _range_replay_end,
)


def main() -> None:
    ap = argparse.ArgumentParser(description="KK pool rank (vnpy default)")
    ap.add_argument("--from", dest="date_from", default="2026-02-01")
    ap.add_argument("--to", dest="date_to", default="2026-06-30")
    ap.add_argument("--equity", type=float, default=1000.0)
    ap.add_argument("--symbols", default="")
    args = ap.parse_args()

    lo, hi = args.date_from.strip(), args.date_to.strip()
    equity = float(args.equity)
    cfg = OrbConfig.from_env()
    kk = KKConfig.from_env()

    if args.symbols.strip():
        symbols = [norm_symbol(s) for s in args.symbols.split(",") if s.strip()]
    else:
        symbols = [
            norm_symbol(s)
            for s in parse_symbol_list(Path(resolve_symbols_path()).read_text(encoding="utf-8"))
        ]

    fetch_start, engine_start_s, replay_start = _range_engine_dates(lo, cfg)
    replay_end = _range_replay_end(hi, cfg)
    engine_start = pd.Timestamp(engine_start_s, tz=cfg.session_tz).to_pydatetime().replace(tzinfo=timezone.utc)

    print(f"=== king_keltner ranking | vnpy | {lo}..{hi} | {equity}U ===")
    print(
        f"Rules: RTH+NYSE EOD, no entry after {kk.no_entry_after_hour:02d}:"
        f"{kk.no_entry_after_minute:02d} ET, compound\n"
    )

    rows: list[dict] = []
    for sym in symbols:
        label = sym.replace("USDT", "")
        dates = [d for d in session_dates_from_cache(sym, cfg) if lo <= d <= hi]
        if not dates:
            continue
        df = _load_symbol_df(sym, fetch_start, hi, cfg)
        if df.empty:
            continue
        px = float(df.iloc[-1]["close"])
        bars = klines_df_to_bars(df, sym, vt_symbol=kk_vt_symbol(sym))
        out = run_kk_vnpy_backtest(
            sym,
            bars,
            kk=kk,
            equity_usdt=equity,
            start=engine_start,
            end=replay_end,
            replay_start=replay_start,
            replay_end=replay_end,
            price=px,
            quiet=True,
            orb_cfg=cfg,
        )
        if out.get("error"):
            continue
        fills = trades_to_rows(out.get("trades") or [])
        tz = cfg.session_tz
        lo_ms = int(pd.Timestamp(f"{lo} 00:00:00", tz=tz).value // 1_000_000)
        hi_ms = int(pd.Timestamp(f"{hi} 23:59:59", tz=tz).value // 1_000_000)
        in_range = [f for f in fills if lo_ms <= int(f["ms"]) <= hi_ms]
        closes = [f for f in in_range if str(f.get("offset", "")).upper() == "CLOSE"]
        wins = sum(1 for f in closes if float(f.get("pnl", 0) or 0) > 0)
        n = len(closes)
        end_w = float(out.get("end_wallet") or equity)
        net = end_w - equity
        opens = sum(1 for f in in_range if str(f.get("offset", "")).upper() == "OPEN")
        rows.append(
            {
                "symbol": label,
                "sessions": len(dates),
                "opens": opens,
                "closes": n,
                "win_rate": round(100 * wins / n, 1) if n else 0,
                "net": round(net, 2),
                "ret_pct": round(100.0 * (end_w - equity) / equity, 1),
                "equity_end": round(end_w, 2),
                "fills": len(in_range),
            }
        )
        print(f"  {label:6s} net={net:+.0f}U opens={opens} closes={n}", flush=True)

    print("\n=== ranking (vnpy, same engine as live) ===")
    for r in sorted(rows, key=lambda x: -x["net"]):
        tag = "OK" if r["net"] > equity * 0.1 else ("WEAK" if r["net"] > 0 else "AVOID")
        print(
            f"{r['symbol']:6s} net={r['net']:+8.0f}U ret={r['ret_pct']:+.1f}% "
            f"opens={r['opens']:4d} win={r['win_rate']:4.1f}% fills={r['fills']:4d}  [{tag}]"
        )
    if rows:
        pool_net = sum(r["net"] for r in rows)
        print(f"\n全池 net {pool_net:+.0f}U ({len(rows)} symbols)")


if __name__ == "__main__":
    main()
