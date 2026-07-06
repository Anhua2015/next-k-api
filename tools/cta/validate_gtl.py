#!/usr/bin/env python3
"""Validate GTL geometry + forecast layer (hit rate, baseline, Brier)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from env_loader import load_env_oi  # noqa: E402

load_env_oi()

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from binance_fapi import fetch_klines_forward, klines_to_df  # noqa: E402
from orb.core.kline_cache import load_klines, norm_symbol  # noqa: E402
from orb.gtl.engine import compute_gtl_dataframe  # noqa: E402
from orb.gtl.resample import resample_ohlcv  # noqa: E402


def _load(sym: str, from_date: str, to_date: str) -> pd.DataFrame:
    lo = pd.Timestamp(from_date.strip(), tz="America/New_York")
    hi = pd.Timestamp(to_date.strip(), tz="America/New_York") + pd.Timedelta(days=1) - pd.Timedelta(milliseconds=1)
    lo_ms, hi_ms = int(lo.value // 1_000_000), int(hi.value // 1_000_000)
    df = load_klines(sym, "1m")
    if df is not None and not df.empty:
        sl = df[(df["open_time"] >= lo_ms) & (df["open_time"] <= hi_ms)].copy()
        if not sl.empty:
            return sl.sort_values("open_time").reset_index(drop=True)
    rows = fetch_klines_forward(sym, "1m", lo_ms, hi_ms)
    return klines_to_df(rows)


def _brier(prob: np.ndarray, actual_up: np.ndarray) -> float:
    p = np.clip(prob, 0.0, 1.0)
    y = (actual_up > 0).astype(float)
    return float(np.mean((p - y) ** 2))


def _honest_trading_sim(df: pd.DataFrame, gtl: pd.DataFrame) -> dict[str, float | int]:
    """Fixed-hold and buy-and-hold baselines — GTL is a forecast layer, not a full system."""
    px = df["close"].astype(float).values
    n = len(px)
    aligned_idx = list(gtl.index[gtl["break_aligns_birth"]])
    out: dict[str, float | int] = {"aligned_setups": len(aligned_idx)}

    for hold in (1, 4, 20):
        pnls: list[float] = []
        for i in aligned_idx:
            d = int(gtl.loc[i, "break_dir"])
            j = min(i + hold, n - 1)
            ep, xp = px[i], px[j]
            pnls.append((xp - ep) if d > 0 else (ep - xp))
        if pnls:
            out[f"hold_{hold}_sum"] = round(float(sum(pnls)), 2)
            out[f"hold_{hold}_win"] = round(float(np.mean(np.array(pnls) > 0)), 3)

    opp_pnls: list[float] = []
    for k, i in enumerate(aligned_idx):
        d = int(gtl.loc[i, "break_dir"])
        ep = px[i]
        exit_i = n - 1
        for j in aligned_idx[k + 1 :]:
            if int(gtl.loc[j, "break_dir"]) != d:
                exit_i = j
                break
        xp = px[exit_i]
        opp_pnls.append((xp - ep) if d > 0 else (ep - xp))
    if opp_pnls:
        out["exit_opposite_sum"] = round(float(sum(opp_pnls)), 2)
        out["exit_opposite_win"] = round(float(np.mean(np.array(opp_pnls) > 0)), 3)

    p0, p1 = float(px[0]), float(px[-1])
    out["buy_hold_move"] = round(p1 - p0, 2)
    return out


def _position_baseline(df: pd.DataFrame, gtl: pd.DataFrame) -> np.ndarray:
    """Use prior bar close inside the frozen box (pre-break, no look-ahead)."""
    hh = gtl["broken_hh"].where(gtl["broken_hh"] > 0, gtl["frozen_hh"]).astype(float).values
    ll = gtl["broken_ll"].where(gtl["broken_ll"] > 0, gtl["frozen_ll"]).astype(float).values
    close = df["close"].astype(float).shift(1).reindex(gtl.index).ffill().values
    span = np.maximum(hh - ll, 1e-12)
    pos = (close - ll) / span
    return (pos >= 0.5).astype(float)


def main() -> int:
    ap = argparse.ArgumentParser(description="GTL validation metrics")
    ap.add_argument("--symbol", default="COINUSDT")
    ap.add_argument("--from-date", default="2026-02-01")
    ap.add_argument("--to-date", default="2026-06-30")
    ap.add_argument("--lookback", type=int, default=23)
    ap.add_argument("--resample", default="30m", help="e.g. 3m, 30m; empty=1m")
    args = ap.parse_args()

    sym = norm_symbol(args.symbol)
    df = _load(sym, args.from_date, args.to_date)
    if df.empty:
        print("no data")
        return 1

    if args.resample.strip():
        df = resample_ohlcv(df, args.resample.strip())

    gtl = compute_gtl_dataframe(df, lookback=args.lookback)
    breaks = gtl[gtl["break_dir"] != 0].copy()
    if breaks.empty:
        print("no completed structures")
        return 0

    actual_up = (breaks["break_dir"] > 0).astype(int)
    birth_prob = breaks["birth_prob_up"].astype(float).values
    birth_hit = breaks["birth_hit"].astype(int)
    birth_mask = birth_hit >= 0
    prob = breaks["prob_up"].astype(float).values
    base = _position_baseline(df.loc[breaks.index], breaks)
    base_acc = float(np.mean(base == actual_up.values))

    brier_gtl = _brier(prob, actual_up.values)
    disp = breaks["display_prob_up"].astype(float).values if "display_prob_up" in breaks.columns else prob
    brier_display = _brier(disp, actual_up.values)
    brier_birth = _brier(birth_prob[birth_mask], actual_up.values[birth_mask]) if birth_mask.any() else float("nan")
    brier_base = _brier(base, actual_up.values)
    hit_birth = float(np.mean(birth_hit[birth_mask])) if birth_mask.any() else float("nan")
    hit_gtl = float(np.mean((prob >= 0.5).astype(int) == actual_up))
    hit_display = float(np.mean((disp >= 0.5).astype(int) == actual_up))
    fc = gtl[gtl["forecast_up"] | gtl["forecast_down"]]
    sig = breaks[breaks["signal_up"] | breaks["signal_down"]]
    aligned = breaks[breaks["break_aligns_birth"]]
    birth_gated = int(gtl["birth_gates_ok"].sum())
    if len(sig):
        correct = ((sig["signal_up"] & (sig["break_dir"] > 0)) | (sig["signal_down"] & (sig["break_dir"] < 0)))
        sig_hit = float(correct.mean())
    else:
        sig_hit = float("nan")

    print(f"symbol={sym} bars={len(df)} structures={len(breaks)} resample={args.resample or '1m'}")
    print(f"  break_up_rate     = {actual_up.mean():.3f}")
    print(f"  birth_hit_rate    = {hit_birth:.3f}")
    print(f"  kde_hit_at_break  = {hit_gtl:.3f}  (in-sample, break bar)")
    print(f"  display_hit_break = {hit_display:.3f}  (TV-aligned display prob)")
    print(f"  forecast_bars     = {len(fc)}  (research display arrows)")
    print(f"  position_baseline = {base_acc:.3f}")
    print(f"  brier_birth       = {brier_birth:.4f}")
    print(f"  brier_at_break    = {brier_gtl:.4f}")
    print(f"  brier_display     = {brier_display:.4f}")
    print(f"  brier_baseline    = {brier_base:.4f}")
    print(f"  gated_signals     = {len(sig)}")
    print(f"  birth_gated_bars  = {birth_gated}")
    print(f"  birth_break_align = {len(aligned)}  (article-style trade setups)")
    print(f"  gated_hit_rate    = {sig_hit:.3f}" if len(sig) else "  gated_hit_rate    = n/a")
    print(f"  abstain_top       = {gtl['abstain_reason'].value_counts().head(3).to_dict()}")

    sim = _honest_trading_sim(df, gtl)
    print("  --- honest execution sim (close@aligned break, no stops) ---")
    print(f"  buy_hold_move     = {sim['buy_hold_move']:+.2f}  (same window)")
    print(
        f"  hold_1bar         = sum {sim.get('hold_1_sum', 0):+.2f}  win {sim.get('hold_1_win', 0):.3f}"
    )
    print(
        f"  hold_4bar         = sum {sim.get('hold_4_sum', 0):+.2f}  win {sim.get('hold_4_win', 0):.3f}"
    )
    print(
        f"  hold_20bar        = sum {sim.get('hold_20_sum', 0):+.2f}  win {sim.get('hold_20_win', 0):.3f}"
    )
    print(
        f"  exit_opposite     = sum {sim.get('exit_opposite_sum', 0):+.2f}  win {sim.get('exit_opposite_win', 0):.3f}"
    )
    print("  NOTE: birth_hit on aligned breaks is tautological (100%). Edge must come from timing/exit, not forecast alone.")

    last = gtl.iloc[-1]
    last_px = float(df.iloc[-1]["close"])
    print("  --- latest bar (TV-style display) ---")
    print(
        f"  close={last_px:.2f} hh={float(last['frozen_hh']):.0f} ll={float(last['frozen_ll']):.0f} "
        f"tc={float(last.get('theta_ceiling_display', abs(last['theta_ceiling']))):.1f} "
        f"tf={float(last.get('theta_floor_display', abs(last['theta_floor']))):.1f}"
    )
    print(
        f"  raw_prob_up={float(last['prob_up']) * 100:.1f}% "
        f"display_up={float(last.get('display_prob_up', last['prob_up'])) * 100:.1f}% "
        f"forecast={'up' if last.get('forecast_up') else 'down' if last.get('forecast_down') else '-'} "
        f"conf={last.get('forecast_confidence', '?')} trade_gate={last.get('trade_abstain_reason') or 'ok'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
