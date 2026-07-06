#!/usr/bin/env python3
"""Snapshot GTL state for BTCUSDT — compare with TradingView."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from env_loader import load_env_oi

load_env_oi()

import pandas as pd
from tools.cta.validate_gtl import _load
from orb.gtl.resample import resample_ohlcv
from orb.gtl.engine import compute_gtl_dataframe


def _ts(row: pd.Series) -> pd.Timestamp:
    return pd.to_datetime(int(row["open_time"]), unit="ms", utc=True).tz_convert("America/New_York")


def _dur(gtl: pd.DataFrame, idx: int) -> int:
    births = gtl.index[(gtl.index <= idx) & gtl["is_birth_bar"]]
    return int(idx - births[-1]) if len(births) else 0


def main() -> None:
    sym = "BTCUSDT"
    from_date, to_date = "2026-05-15", "2026-07-04"
    df = resample_ohlcv(_load(sym, from_date, to_date), "30m")
    if df.empty or pd.to_datetime(int(df.iloc[-1]["open_time"]), unit="ms", utc=True) < pd.Timestamp(
        "2026-06-20", tz="UTC"
    ):
        from binance_fapi import fetch_klines_forward, klines_to_df

        lo_ms = int(pd.Timestamp(from_date, tz="UTC").value // 1_000_000)
        hi_ms = int(pd.Timestamp("2026-07-05", tz="UTC").value // 1_000_000)
        df = resample_ohlcv(klines_to_df(fetch_klines_forward(sym, "1m", lo_ms, hi_ms)), "30m")

    gtl = compute_gtl_dataframe(df, lookback=23, vol_window=500)

    last = gtl.iloc[-1]
    row = df.iloc[-1]
    print("=== LAST BAR (TV-style) ===")
    print("time", _ts(row))
    print("close", round(float(row["close"]), 2))
    print("frozen_hh", round(float(last["frozen_hh"]), 2), "frozen_ll", round(float(last["frozen_ll"]), 2))
    print(
        "angles display",
        round(float(last["theta_ceiling_display"]), 1),
        round(float(last["theta_floor_display"]), 1),
        "| raw",
        round(float(last["theta_ceiling"]), 1),
        round(float(last["theta_floor"]), 1),
    )
    print("raw prob_up", f"{float(last['prob_up']) * 100:.1f}%", "display_up", f"{float(last['display_prob_up']) * 100:.1f}%")
    print(
        "forecast",
        "up" if last["forecast_up"] else "down" if last["forecast_down"] else "-",
        "conf",
        last["forecast_confidence"],
        "trade_gate",
        last.get("trade_abstain_reason") or "ok",
    )
    print("signal_up (strict trade)", bool(last["signal_up"]), "n_eff", round(float(last["n_eff"]), 1))
    print("str_duration_proxy", _dur(gtl, gtl.index[-1]))

    mask = (
        gtl["frozen_hh"].between(62700, 63000)
        & gtl["frozen_ll"].between(61300, 61600)
        & gtl["theta_ceiling_display"].between(20, 25)
        & gtl["theta_floor_display"].between(58, 64)
    )
    hits = gtl[mask]
    print("\n=== MATCH TV GEOMETRY (hh~62848 ll~61485 tc~22.6 tf~61.2) ===")
    print("matches", len(hits))
    for idx in list(hits.index[-3:]):
        r = gtl.loc[idx]
        d = df.loc[idx]
        print(
            f"  {_ts(d)} close={float(d['close']):.1f} "
            f"hh={float(r['frozen_hh']):.0f} ll={float(r['frozen_ll']):.0f} "
            f"tc={float(r['theta_ceiling_display']):.1f} tf={float(r['theta_floor_display']):.1f} "
            f"dur~{_dur(gtl, idx)} display={float(r['display_prob_up']) * 100:.1f}% "
            f"forecast={'up' if r['forecast_up'] else 'down' if r['forecast_down'] else '-'}"
        )

    near77 = gtl[(gtl["display_prob_up"].between(0.72, 0.82)) & (gtl["forecast_up"] | gtl["forecast_down"])]
    print("\n=== RECENT display 72-82% + forecast (last 5) ===")
    for idx in list(near77.index[-5:]):
        r = gtl.loc[idx]
        d = df.loc[idx]
        print(
            f"  {_ts(d)} close={float(d['close']):.1f} "
            f"hh={float(r['frozen_hh']):.0f} ll={float(r['frozen_ll']):.0f} "
            f"tc={float(r['theta_ceiling_display']):.1f} tf={float(r['theta_floor_display']):.1f} "
            f"dur~{_dur(gtl, idx)} display={float(r['display_prob_up']) * 100:.1f}%"
        )

    box = gtl[
        gtl["frozen_hh"].between(62348, 63348) & gtl["frozen_ll"].between(60985, 61985)
    ].copy()
    if not box.empty:
        box["dur"] = [_dur(gtl, i) for i in box.index]
        box["close_px"] = [float(df.loc[i, "close"]) for i in box.index]
        box["score"] = (
            (box["theta_ceiling_display"] - 22.6).abs()
            + (box["theta_floor_display"] - 61.2).abs() * 0.5
            + (box["dur"] - 34).abs() * 0.3
            + (box["display_prob_up"] - 0.77).abs() * 50
            + (box["close_px"] - 62540).abs() / 500
        )
        print("\n=== TOP 5 MATCHES TO TV (display prob ~77%) ===")
        for idx, r in box.nsmallest(5, "score").iterrows():
            d = df.loc[idx]
            print(
                f"  {_ts(d)} close={r['close_px']:.1f} "
                f"hh={r['frozen_hh']:.0f} ll={r['frozen_ll']:.0f} "
                f"tc={r['theta_ceiling_display']:.1f} tf={r['theta_floor_display']:.1f} "
                f"dur={int(r['dur'])} display={r['display_prob_up'] * 100:.1f}% "
                f"raw={r['prob_up'] * 100:.1f}%"
            )
            future = gtl.loc[idx:][gtl.loc[idx:, "break_dir"] != 0]
            if not future.empty:
                bi = future.index[0]
                print(
                    f"    -> break {_ts(df.loc[bi])} dir={int(gtl.loc[bi, 'break_dir']):+d} "
                    f"align={bool(gtl.loc[bi, 'break_aligns_birth'])}"
                )

    print("\n=== vol_window SWEEP (match TV params 23/13/83) ===")
    for vw in (13, 83, 500):
        g = compute_gtl_dataframe(df, lookback=23, vol_window=vw)
        r = g.iloc[-1]
        print(
            f"  vw={vw:3d} raw={float(r['prob_up']) * 100:.1f}% display={float(r['display_prob_up']) * 100:.1f}% "
            f"tc={float(r['theta_ceiling_display']):.1f} tf={float(r['theta_floor_display']):.1f} "
            f"n_eff={float(r['n_eff']):.1f} forecast={'up' if r['forecast_up'] else 'down' if r['forecast_down'] else '-'}"
        )


if __name__ == "__main__":
    main()
