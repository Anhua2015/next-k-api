#!/usr/bin/env python3
"""KK 池 vnpy 官方回测：每标的复利钱包，NYSE RTH + 12:00 禁开 + EOD。"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from env_loader import load_env_oi

load_env_oi()

import pandas as pd  # noqa: E402

from binance_fapi import fetch_klines_forward, klines_to_df  # noqa: E402
from orb.core.config import OrbConfig  # noqa: E402
from orb.core.kline_cache import load_klines, norm_symbol, session_dates_from_cache  # noqa: E402
from orb.core.session import session_anchor_ms  # noqa: E402
from orb.core.symbols import parse_symbol_list  # noqa: E402
from orb.kk.config import KKConfig  # noqa: E402
from orb.kk.paths import resolve_symbols_path  # noqa: E402
from orb.kk.vnpy.backtest import (  # noqa: E402
    klines_df_to_bars,
    run_kk_vnpy_backtest,
    session_bounds_for_date,
    trades_to_rows,
)
from orb.kk.vnpy.binance_gateway import kk_vt_symbol  # noqa: E402


def _fetch_range(sym: str, from_date: str, to_date: str, cfg: OrbConfig) -> pd.DataFrame:
    tz = cfg.session_tz
    lo = pd.Timestamp(from_date.strip(), tz=tz)
    hi = pd.Timestamp(to_date.strip(), tz=tz) + pd.Timedelta(days=1) - pd.Timedelta(milliseconds=1)
    rows = fetch_klines_forward(sym, "1m", int(lo.value // 1_000_000), int(hi.value // 1_000_000))
    df = klines_to_df(rows)
    if df.empty:
        return df
    return df.drop_duplicates(subset=["open_time"], keep="last").sort_values("open_time").reset_index(drop=True)


def _load_symbol_df(sym: str, fetch_from: str, hi: str, cfg: OrbConfig) -> pd.DataFrame:
    tz = cfg.session_tz
    lo_ts = pd.Timestamp(fetch_from.strip(), tz=tz)
    hi_ts = pd.Timestamp(hi.strip(), tz=tz) + pd.Timedelta(days=1) - pd.Timedelta(milliseconds=1)
    lo_ms, hi_ms = int(lo_ts.value // 1_000_000), int(hi_ts.value // 1_000_000)
    df = load_klines(sym, "1m")
    if df is not None and not df.empty:
        sl = df[(df["open_time"] >= lo_ms) & (df["open_time"] <= hi_ms)].copy()
        if not sl.empty:
            return sl.sort_values("open_time").reset_index(drop=True)
    return _fetch_range(sym, fetch_from, hi, cfg)


def _range_engine_dates(lo: str, cfg: OrbConfig) -> tuple[str, str, datetime, datetime]:
    """engine.start 与全区间 RTH replay 起止（UTC）。"""
    tz = cfg.session_tz
    engine_start = (pd.Timestamp(lo.strip(), tz=tz) - pd.Timedelta(days=12)).strftime("%Y-%m-%d")
    fetch_start = (pd.Timestamp(engine_start, tz=tz) - pd.Timedelta(days=12)).strftime("%Y-%m-%d")
    replay_start, _, _, _, _ = session_bounds_for_date(lo, cfg)
    return fetch_start, engine_start, replay_start


def _range_replay_end(hi: str, cfg: OrbConfig) -> datetime:
    _, replay_end, _, _, _ = session_bounds_for_date(hi, cfg)
    return replay_end


def main() -> None:
    ap = argparse.ArgumentParser(description="KK vnpy pool backtest (compound per symbol)")
    ap.add_argument("--from", dest="date_from", default="2026-02-01")
    ap.add_argument("--to", dest="date_to", default="2026-06-30")
    ap.add_argument("--equity", type=float, default=50.0)
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

    print(f"=== KK vnpy pool | {lo}..{hi} | {equity}U x {len(symbols)} | BacktestingEngine ===")
    print(
        f"Rules: RTH+NYSE, EOD flat, no entry after {kk.no_entry_after_hour:02d}:"
        f"{kk.no_entry_after_minute:02d} ET, compound, slip {kk.slip_bps_entry}bps"
    )
    print(f"Data fetch {fetch_start} .. {hi} | engine.start={engine_start_s}\n")

    rows: list[dict] = []
    for sym in symbols:
        label = sym.replace("USDT", "")
        dates = [d for d in session_dates_from_cache(sym, cfg) if lo <= d <= hi]
        print(f"--- {label} --- (sessions in cache: {len(dates)})", flush=True)
        try:
            df = _load_symbol_df(sym, fetch_start, hi, cfg)
        except Exception as exc:
            print(f"  LOAD FAIL: {exc}\n", flush=True)
            continue
        if df.empty:
            print("  no klines\n", flush=True)
            continue

        px = float(df.iloc[-1]["close"])
        bars = klines_df_to_bars(df, sym, vt_symbol=kk_vt_symbol(sym))
        try:
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
        except Exception as exc:
            print(f"  BACKTEST FAIL: {exc}\n", flush=True)
            continue

        if out.get("error") == "no_history_data":
            print("  no replay bars\n", flush=True)
            continue
        if out.get("error") == "backtest_aborted":
            print("  backtest aborted (see verbose)\n", flush=True)
            continue

        stats = out.get("statistics") or {}
        fills = trades_to_rows(out.get("trades") or [])
        tz = cfg.session_tz
        lo_ms = int(pd.Timestamp(f"{lo} 00:00:00", tz=tz).value // 1_000_000)
        hi_ms = int(pd.Timestamp(f"{hi} 23:59:59", tz=tz).value // 1_000_000)
        in_range = [f for f in fills if lo_ms <= int(f["ms"]) <= hi_ms]
        end_w = float(out.get("end_wallet") or stats.get("end_balance") or equity)
        net = end_w - equity
        opens = sum(1 for f in in_range if str(f.get("offset", "")).upper() == "OPEN")
        closes = sum(1 for f in in_range if str(f.get("offset", "")).upper() == "CLOSE")

        rows.append(
            {
                "symbol": label,
                "fills": len(in_range),
                "opens": opens,
                "closes": closes,
                "net": round(net, 2),
                "equity_end": round(end_w, 2),
                "ret_pct": round(100.0 * (end_w - equity) / equity, 1),
            }
        )
        print(
            f"  start={equity:.0f}U end={end_w:7.2f}U net={net:+8.2f}U "
            f"fills={len(in_range)} (open={opens} close={closes})\n",
            flush=True,
        )

    if not rows:
        print("No results.")
        return

    pool_start = equity * len(rows)
    pool_end = sum(r["equity_end"] for r in rows)
    pool_net = sum(r["net"] for r in rows)
    fills = sum(r["fills"] for r in rows)
    pool_ret = 100.0 * (pool_end - pool_start) / pool_start

    print("=== POOL TOTAL (vnpy) ===")
    print(f"start={pool_start:.0f}U  end={pool_end:.2f}U  net={pool_net:+.2f}U  ret={pool_ret:+.1f}%")
    print(f"fills={fills}")
    print()
    print(f"{'sym':6s} {'start':>7s} {'end':>9s} {'net':>9s} {'ret%':>7s} {'fills':>6s}")
    for r in rows:
        print(
            f"{r['symbol']:6s} {equity:7.0f} {r['equity_end']:9.2f} {r['net']:+9.2f} "
            f"{r['ret_pct']:+6.1f}% {r['fills']:6d}"
        )


if __name__ == "__main__":
    main()
