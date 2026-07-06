#!/usr/bin/env python3
"""Pooled statistical tests with enough N: GTL signals on pool7, full cache range."""

from __future__ import annotations

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from env_loader import load_env_oi  # noqa: E402

load_env_oi()

import numpy as np
import pandas as pd
from scipy import stats

from orb.core.config import OrbConfig
from orb.core.kline_cache import load_klines, norm_symbol, session_dates_from_cache
from orb.core.session import is_trading_session, session_day_str

POOL7 = ["INTC", "SOXL", "HOOD", "CRCL", "COIN", "SNDK", "MSTR"]


def _load_symbol_df(sym: str, from_date: str, to_date: str, cfg: OrbConfig) -> pd.DataFrame:
    tz = cfg.session_tz
    lo = pd.Timestamp(from_date.strip(), tz=tz)
    hi = pd.Timestamp(to_date.strip(), tz=tz) + pd.Timedelta(days=1) - pd.Timedelta(milliseconds=1)
    lo_ms, hi_ms = int(lo.value // 1_000_000), int(hi.value // 1_000_000)
    df = load_klines(norm_symbol(sym), "1m", start_ms=lo_ms, end_ms=hi_ms)
    return df.sort_values("open_time").reset_index(drop=True) if not df.empty else df


from orb.gtl.engine import compute_gtl_dataframe  # noqa: E402
from orb.gtl.resample import resample_ohlcv  # noqa: E402


def _t_test(x: np.ndarray, null: float = 0.0) -> tuple[float, float, float]:
    x = x[np.isfinite(x)]
    if len(x) < 3:
        return float("nan"), float("nan"), float("nan")
    t, p = stats.ttest_1samp(x, null)
    return float(x.mean()), float(t), float(p)


def _binom_gt(x: np.ndarray, p0: float = 0.5) -> tuple[float, float]:
    x = x[np.isfinite(x)]
    wins = int((x > 0).sum())
    n = len(x)
    if n == 0:
        return float("nan"), float("nan")
    # one-sided: win rate > p0
    p = stats.binomtest(wins, n, p0, alternative="greater").pvalue
    return wins / n, float(p)


def _load_day_bars(sym: str, day: str, cfg: OrbConfig) -> tuple[pd.DataFrame, pd.DataFrame]:
    lo = (pd.Timestamp(day, tz=cfg.session_tz) - pd.Timedelta(days=5)).strftime("%Y-%m-%d")
    hi = (pd.Timestamp(day, tz=cfg.session_tz) + pd.Timedelta(days=2)).strftime("%Y-%m-%d")
    raw = _load_symbol_df(norm_symbol(sym), lo, hi, cfg)
    if raw.empty:
        return raw, raw
    df = resample_ohlcv(raw, cfg.signal_interval)
    gtl = compute_gtl_dataframe(df, lookback=23, vol_window=500)

    def _in_day(ms: int) -> bool:
        return session_day_str(ms, tz=cfg.session_tz, session_open_time=cfg.session_open_time) == day

    m = df["open_time"].astype(int).map(
        lambda ms: _in_day(ms)
        and is_trading_session(
            ms,
            tz=cfg.session_tz,
            session_open_time=cfg.session_open_time,
            session_close_time=cfg.session_close_time,
            market=cfg.market,
        )
    )
    return df[m].reset_index(drop=True), gtl[m].reset_index(drop=True)


def _open_dir(row, open_mode: str = "break_forecast") -> int:
    if open_mode == "break":
        if bool(row.get("break_aligns_birth")):
            d = int(row.get("break_dir") or 0)
            return 1 if d > 0 else (-1 if d < 0 else 0)
        return 0
    if bool(row.get("break_aligns_birth")):
        d = int(row.get("break_dir") or 0)
        return 1 if d > 0 else (-1 if d < 0 else 0)
    if bool(row.get("birth_forecast_up")) or bool(row.get("forecast_up")):
        return 1
    if bool(row.get("birth_forecast_down")) or bool(row.get("forecast_down")):
        return -1
    return 0


def _stop_for(row, side: int) -> float:
    if side > 0:
        return float(row.get("broken_ll") or row.get("ll") or 0)
    return float(row.get("broken_hh") or row.get("hh") or 0)


def collect_open_eod(cfg: OrbConfig) -> pd.DataFrame:
    rows: list[dict] = []
    for sym in POOL7:
        label = sym
        for day in session_dates_from_cache(sym, cfg):
            df, gtl = _load_day_bars(sym, day, cfg)
            if df.empty or gtl.empty:
                continue
            r0 = gtl.iloc[0]
            od = _open_dir(r0)
            if od == 0:
                continue
            entry = float(df.iloc[0]["close"])
            eod = float(df.iloc[-1]["close"])
            signed = (eod - entry) if od > 0 else (entry - eod)
            pct = signed / entry * 100
            sl = _stop_for(r0, od)
            sl_hit = (df["low"].min() <= sl) if od > 0 else (df["high"].max() >= sl)
            rows.append(
                {
                    "day": day,
                    "symbol": label,
                    "open_dir": "up" if od > 0 else "down",
                    "aligned_open": bool(r0.get("break_aligns_birth")),
                    "entry": entry,
                    "eod": eod,
                    "signed_pnl": signed,
                    "signed_pct": pct,
                    "sl_hit": bool(sl_hit),
                }
            )
    return pd.DataFrame(rows)


def collect_aligned_breaks(cfg: OrbConfig) -> pd.DataFrame:
    rows: list[dict] = []
    for sym in POOL7:
        dates = session_dates_from_cache(sym, cfg)
        if not dates:
            continue
        lo, hi = dates[0], dates[-1]
        fetch_lo = (pd.Timestamp(lo, tz=cfg.session_tz) - pd.Timedelta(days=5)).strftime("%Y-%m-%d")
        fetch_hi = (pd.Timestamp(hi, tz=cfg.session_tz) + pd.Timedelta(days=2)).strftime("%Y-%m-%d")
        raw = _load_symbol_df(norm_symbol(sym), fetch_lo, fetch_hi, cfg)
        if raw.empty:
            continue
        df = resample_ohlcv(raw, cfg.signal_interval)
        gtl = compute_gtl_dataframe(df, lookback=23, vol_window=500)
        px = df["close"].astype(float).values
        for i in gtl.index[gtl["break_aligns_birth"]]:
            ms = int(df.iloc[i]["open_time"])
            if not is_trading_session(
                ms,
                tz=cfg.session_tz,
                session_open_time=cfg.session_open_time,
                session_close_time=cfg.session_close_time,
                market=cfg.market,
            ):
                continue
            day = session_day_str(ms, tz=cfg.session_tz, session_open_time=cfg.session_open_time)
            if day not in dates:
                continue
            d = int(gtl.loc[i, "break_dir"])
            ep = px[i]
            for h, label in ((1, "5m"), (4, "20m"), (20, "100m")):
                j = min(i + h, len(px) - 1)
                raw_m = px[j] - ep
                signed = raw_m if d > 0 else -raw_m
                rows.append(
                    {
                        "symbol": sym,
                        "day": day,
                        "hold": label,
                        "signed_pct": signed / ep * 100,
                        "win": signed > 0,
                    }
                )
    return pd.DataFrame(rows)


def collect_daily_cs(cfg: OrbConfig) -> pd.DataFrame:
    """42-symbol cross-section: overnight momentum -> next-day return."""
    root = Path(__file__).resolve().parents[2] / "data" / "orb" / "kline"
    rows: list[dict] = []
    for d in sorted(root.iterdir()):
        if not d.is_dir():
            continue
        p = d / "1d.csv"
        if not p.is_file():
            continue
        df = pd.read_csv(p)
        if len(df) < 3:
            continue
        df = df.sort_values("open_time")
        c = df["close"].astype(float)
        ret = c.pct_change().shift(-1)
        mom = c.pct_change()
        for i in range(len(df) - 2):
            rows.append(
                {
                    "symbol": d.name,
                    "date": pd.Timestamp(int(df.iloc[i]["open_time"]), unit="ms", tz="UTC").strftime("%Y-%m-%d"),
                    "mom": float(mom.iloc[i]),
                    "fwd_ret": float(ret.iloc[i]),
                }
            )
    return pd.DataFrame(rows)


def print_block(title: str, mean: float, t: float, p: float, wr: float, pb: float, n: int) -> None:
    sig_t = "**" if p < 0.05 else ("*" if p < 0.10 else "")
    sig_b = "**" if pb < 0.05 else ("*" if pb < 0.10 else "")
    print(f"\n### {title}")
    print(f"n = {n}")
    print(f"mean return = {mean:+.4f}%   t = {t:+.2f}   p = {p:.4f} {sig_t}")
    print(f"win rate    = {wr*100:.1f}%        binom p(vs 50%) = {pb:.4f} {sig_b}")


def main() -> int:
    cfg = OrbConfig.from_env()
    print("=" * 60)
    print("Statistical tests | full ORB cache | pool7 + 42-symbol CS")
    print("=" * 60)

    # 1) open@0930 -> EOD (full sample)
    oe = collect_open_eod(cfg)
    if not oe.empty:
        x = oe["signed_pct"].values
        m, t, p = _t_test(x, 0)
        wr, pb = _binom_gt(x, 0.5)
        print_block("open@09:30 -> EOD (all pool7 symbol-days)", m, t, p, wr, pb, len(x))

        sub = oe[oe["aligned_open"]]
        if len(sub) >= 10:
            x2 = sub["signed_pct"].values
            m, t, p = _t_test(x2, 0)
            wr, pb = _binom_gt(x2, 0.5)
            print_block("  subset: aligned break @ open", m, t, p, wr, pb, len(x2))

        sub = oe[~oe["sl_hit"]]
        if len(sub) >= 10:
            x3 = sub["signed_pct"].values
            m, t, p = _t_test(x3, 0)
            wr, pb = _binom_gt(x3, 0.5)
            print_block("  subset: no structure SL hit intraday", m, t, p, wr, pb, len(x3))

        print("\n--- per symbol (open->EOD) ---")
        print(f"{'sym':6s} {'n':>4s} {'mean%':>8s} {'win%':>6s} {'p_t':>8s}")
        for sym, g in oe.groupby("symbol"):
            m, t, p = _t_test(g["signed_pct"].values, 0)
            wr = (g["signed_pct"] > 0).mean()
            print(f"{sym:6s} {len(g):4d} {g['signed_pct'].mean():+8.2f} {wr*100:5.0f}% {p:8.4f}")

    # 2) aligned breaks forward
    ab = collect_aligned_breaks(cfg)
    if not ab.empty:
        print("\n" + "=" * 60)
        print("Aligned breaks -> forward return (pooled)")
        for hold in ("5m", "20m", "100m"):
            sub = ab[ab["hold"] == hold]
            x = sub["signed_pct"].values
            m, t, p = _t_test(x, 0)
            wr, pb = _binom_gt(x, 0.5)
            print_block(f"break_aligns_birth -> +{hold}", m, t, p, wr, pb, len(x))

    # 3) 42-symbol cross-sectional IC (daily momentum)
    cs = collect_daily_cs(cfg)
    if not cs.empty:
        cs = cs.dropna()
        daily_ic: list[float] = []
        for _, g in cs.groupby("date"):
            if len(g) < 10:
                continue
            ic = g["mom"].corr(g["fwd_ret"], method="pearson")
            if np.isfinite(ic):
                daily_ic.append(ic)
        ic_arr = np.array(daily_ic)
        m, t, p = _t_test(ic_arr, 0)
        print("\n" + "=" * 60)
        print("42-symbol cross-section | IC(momentum -> next-day ret)")
        print(f"trading days with IC = {len(ic_arr)}")
        print(f"mean IC = {m:+.4f}   t = {t:+.2f}   p = {p:.4f}")
        print(f"IC>0 ratio = {(ic_arr > 0).mean()*100:.1f}%")

    print("\n(* p<0.10  ** p<0.05)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
