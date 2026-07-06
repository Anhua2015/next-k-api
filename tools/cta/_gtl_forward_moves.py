#!/usr/bin/env python3
"""Forward price move after GTL aligned breaks (direction-signed)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from env_loader import load_env_oi

load_env_oi()

import pandas as pd
from orb.core.config import OrbConfig
from orb.core.kline_cache import norm_symbol
from orb.core.session import is_trading_session, session_day_str
from orb.gtl.engine import compute_gtl_dataframe
from orb.gtl.resample import resample_ohlcv
from tools.cta.research_gtl_vnpy import _load_symbol_df

from binance_fapi import fetch_klines_forward, klines_to_df  # noqa: E402

POOL7 = ["INTC", "SOXL", "HOOD", "CRCL", "COIN", "SNDK", "MSTR"]
HOLDS = (1, 4, 20)
HOLD_LABELS = {1: "5min", 4: "20min", 20: "100min"}


def _load_range(sym: str, fetch_lo: str, fetch_hi: str, cfg: OrbConfig) -> pd.DataFrame:
    lo_ms = int(pd.Timestamp(fetch_lo, tz="UTC").value // 1_000_000)
    hi_ms = int(pd.Timestamp(fetch_hi, tz="UTC").value // 1_000_000)
    raw = _load_symbol_df(sym, fetch_lo, fetch_hi, cfg)
    last_ms = int(raw.iloc[-1]["open_time"]) if not raw.empty else 0
    if last_ms < hi_ms - 86400_000:
        fetched = klines_to_df(fetch_klines_forward(sym, "1m", lo_ms, hi_ms))
        if not fetched.empty:
            raw = (
                pd.concat([raw, fetched], ignore_index=True)
                .drop_duplicates(subset=["open_time"], keep="last")
                .sort_values("open_time")
                .reset_index(drop=True)
            )
    if raw.empty:
        raw = klines_to_df(fetch_klines_forward(sym, "1m", lo_ms, hi_ms))
    return raw


def _rth_mask(df: pd.DataFrame, cfg: OrbConfig) -> pd.Series:
    ms = df["open_time"].astype(int)

    def _ok(m: int) -> bool:
        return bool(
            is_trading_session(
                m,
                tz=cfg.session_tz,
                session_open_time=cfg.session_open_time,
                session_close_time=cfg.session_close_time,
                market=cfg.market,
            )
        )

    return ms.map(_ok)


def _fwd_rows(df: pd.DataFrame, gtl: pd.DataFrame, holds: tuple[int, ...], cfg: OrbConfig) -> list[dict]:
    px = df["close"].astype(float).values
    hh = gtl["frozen_hh"].astype(float).values
    ll = gtl["frozen_ll"].astype(float).values
    ots = df["open_time"].astype(int).values
    n = len(px)
    rows: list[dict] = []
    for i in gtl.index[gtl["break_aligns_birth"]]:
        d = int(gtl.loc[i, "break_dir"])
        ep = px[i]
        span = max(hh[i] - ll[i], 1e-9)
        ts = pd.Timestamp(int(ots[i]), unit="ms", tz="UTC").tz_convert(cfg.session_tz)
        rec: dict = {
            "dir": "up" if d > 0 else "down",
            "entry": ep,
            "box": span,
            "time_et": ts.strftime("%H:%M"),
            "open_time_ms": int(ots[i]),
        }
        for h in holds:
            j = min(i + h, n - 1)
            raw = px[j] - ep
            signed = raw if d > 0 else -raw
            rec[f"move_{h}"] = signed
            rec[f"pct_{h}"] = signed / ep * 100
            rec[f"boxR_{h}"] = signed / span
        rows.append(rec)
    return rows


def _collect(
    syms: list[str],
    lo: str,
    hi: str,
    rs: str,
    cfg: OrbConfig,
    *,
    day: str | None,
) -> pd.DataFrame:
    rows: list[dict] = []
    for s in syms:
        sym = norm_symbol(s)
        fetch_lo = (pd.Timestamp(lo if not day else day) - pd.Timedelta(days=30)).strftime("%Y-%m-%d")
        fetch_hi = (pd.Timestamp(hi if not day else day) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        raw = _load_range(sym, fetch_lo, fetch_hi, cfg)
        if raw.empty:
            continue
        df = resample_ohlcv(raw, rs) if rs != "1m" else raw
        gtl = compute_gtl_dataframe(df, lookback=23, vol_window=500)
        m = _rth_mask(df, cfg)
        if day:
            m = m & df["open_time"].astype(int).map(
                lambda ms: session_day_str(ms, tz=cfg.session_tz, session_open_time=cfg.session_open_time) == day
            )
        else:
            lo_ms = int(pd.Timestamp(lo, tz=cfg.session_tz).value // 1_000_000)
            hi_ms = int(
                (pd.Timestamp(hi, tz=cfg.session_tz) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)).value
                // 1_000_000
            )
            m = m & (df["open_time"] >= lo_ms) & (df["open_time"] <= hi_ms)
        sub_df = df[m].reset_index(drop=True)
        sub_gtl = gtl[m].reset_index(drop=True)
        for r in _fwd_rows(sub_df, sub_gtl, HOLDS, cfg):
            r["symbol"] = sym.replace("USDT", "")
            rows.append(r)
    return pd.DataFrame(rows)


def _print_stats(dfm: pd.DataFrame, title: str) -> None:
    print(title)
    print(f"n={len(dfm)} aligned breaks")
    if dfm.empty:
        print("(no data)\n")
        return
    for h in HOLDS:
        col, pcol, rcol = f"move_{h}", f"pct_{h}", f"boxR_{h}"
        x = dfm[col]
        print(f"--- hold {HOLD_LABELS[h]} ({h} bars) ---")
        print(f"  avg in break dir:  {x.mean():+.3f} USD  ({dfm[pcol].mean():+.3f}%)")
        print(f"  median:            {x.median():+.3f} USD  ({dfm[pcol].median():+.3f}%)")
        print(f"  p25 / p75:         {x.quantile(0.25):+.3f} / {x.quantile(0.75):+.3f} USD")
        print(f"  avg vs box (R):    {dfm[rcol].mean():+.3f}  (move / HH-LL at entry)")
        print(f"  win rate:          {(x > 0).mean() * 100:.1f}%  (price moved with break)")
        print()
    for side in ("up", "down"):
        sub = dfm[dfm["dir"] == side]
        if sub.empty:
            continue
        print(f"--- break {side} n={len(sub)} ---")
        for h in HOLDS:
            x = sub[f"move_{h}"]
            print(
                f"  {HOLD_LABELS[h]}: med {x.median():+.2f} USD ({sub[f'pct_{h}'].median():+.2f}%) "
                f"avg {x.mean():+.2f} win {(x > 0).mean() * 100:.0f}%"
            )
        print()


def _print_by_symbol(dfm: pd.DataFrame) -> None:
    print("--- per symbol (median move in break direction) ---")
    print(f"{'sym':6s} {'n':>3s}  {'5m $':>8s} {'5m %':>6s}  {'20m $':>8s} {'20m %':>6s}  {'100m $':>8s} {'100m %':>6s}")
    for sym, g in dfm.groupby("symbol"):
        print(
            f"{sym:6s} {len(g):3d}  "
            f"{g['move_1'].median():+8.2f} {g['pct_1'].median():+5.2f}%  "
            f"{g['move_4'].median():+8.2f} {g['pct_4'].median():+5.2f}%  "
            f"{g['move_20'].median():+8.2f} {g['pct_20'].median():+5.2f}%"
        )
    print()


def _print_events(dfm: pd.DataFrame, tz: str) -> None:
    print(f"--- each aligned break (ET {tz}) ---")
    hdr = (
        f"{'#':>2s} {'sym':6s} {'time':>5s} {'dir':>4s} {'entry':>8s} "
        f"{'5m%':>6s} {'20m%':>6s} {'100m%':>7s}"
    )
    print(hdr)
    print("-" * len(hdr))
    dfm = dfm.sort_values(["symbol", "open_time_ms"]).reset_index(drop=True)
    for n, r in dfm.iterrows():
        ok5 = "+" if r["move_1"] > 0 else "-"
        ok20 = "+" if r["move_4"] > 0 else "-"
        ok100 = "+" if r["move_20"] > 0 else "-"
        print(
            f"{n+1:2d} {r['symbol']:6s} {r['time_et']:>5s} {r['dir']:>4s} {r['entry']:8.2f} "
            f"{r['pct_1']:+5.2f}{ok5} {r['pct_4']:+5.2f}{ok20} {r['pct_20']:+6.2f}{ok100}"
        )
    print("  (+/- = moved with break direction at 5m / 20m / 100m)")
    print()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-date", default="2026-02-01")
    ap.add_argument("--to-date", default="2026-06-30")
    ap.add_argument("--day", default="", help="single session day e.g. 2026-07-02")
    ap.add_argument("--resample", default="5m")
    args = ap.parse_args()

    cfg = OrbConfig.from_env()
    syms = [norm_symbol(s) for s in POOL7]
    rs = args.resample.strip() or "5m"

    if args.day.strip():
        dfm = _collect(syms, args.day, args.day, rs, cfg, day=args.day.strip())
        _print_stats(dfm, f"=== {args.day} RTH | {rs} | pool7 ===")
        if not dfm.empty:
            _print_by_symbol(dfm)
            _print_events(dfm, cfg.session_tz)
    else:
        dfm = _collect(syms, args.from_date, args.to_date, rs, cfg, day=None)
        _print_stats(
            dfm,
            f"=== pool7 {args.from_date}..{args.to_date} | {rs} RTH | aligned breaks ===",
        )
        d2 = _collect(syms, "2026-07-02", "2026-07-02", rs, cfg, day="2026-07-02")
        _print_stats(d2, "=== 2026-07-02 RTH (reference day) ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
