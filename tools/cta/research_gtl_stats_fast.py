#!/usr/bin/env python3
"""Fast pooled stats: load 5m once per symbol, test GTL hypotheses."""

from __future__ import annotations

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
from orb.gtl.engine import compute_gtl_dataframe
from orb.gtl.resample import resample_ohlcv

POOL7 = ["INTC", "SOXL", "HOOD", "CRCL", "COIN", "SNDK", "MSTR"]


def _t_p(x: np.ndarray) -> tuple[float, float, float]:
    x = x[np.isfinite(x)]
    if len(x) < 3:
        return float("nan"), float("nan"), float("nan")
    t, p = stats.ttest_1samp(x, 0.0)
    return float(x.mean()), float(t), float(p)


def _open_dir(row) -> int:
    if bool(row.get("break_aligns_birth")):
        d = int(row.get("break_dir") or 0)
        return 1 if d > 0 else (-1 if d < 0 else 0)
    if bool(row.get("birth_forecast_up")) or bool(row.get("forecast_up")):
        return 1
    if bool(row.get("birth_forecast_down")) or bool(row.get("forecast_down")):
        return -1
    return 0


def _load_5m(sym: str, cfg: OrbConfig) -> pd.DataFrame:
    dates = session_dates_from_cache(sym, cfg)
    if not dates:
        return pd.DataFrame()
    lo = (pd.Timestamp(dates[0], tz=cfg.session_tz) - pd.Timedelta(days=3)).strftime("%Y-%m-%d")
    hi = (pd.Timestamp(dates[-1], tz=cfg.session_tz) + pd.Timedelta(days=2)).strftime("%Y-%m-%d")
    tz = cfg.session_tz
    lo_ms = int(pd.Timestamp(lo, tz=tz).value // 1_000_000)
    hi_ms = int((pd.Timestamp(hi, tz=tz) + pd.Timedelta(days=1)).value // 1_000_000)
    raw = load_klines(norm_symbol(sym), "1m", start_ms=lo_ms, end_ms=hi_ms)
    if raw.empty:
        return raw
    df = resample_ohlcv(raw, cfg.signal_interval)
    df["_day"] = df["open_time"].astype(int).map(
        lambda ms: session_day_str(ms, tz=cfg.session_tz, session_open_time=cfg.session_open_time)
    )
    m = df["open_time"].astype(int).map(
        lambda ms: is_trading_session(
            ms,
            tz=cfg.session_tz,
            session_open_time=cfg.session_open_time,
            session_close_time=cfg.session_close_time,
            market=cfg.market,
        )
    )
    return df[m].reset_index(drop=True)


def analyze_pool7(cfg: OrbConfig) -> tuple[pd.DataFrame, pd.DataFrame]:
    open_rows: list[dict] = []
    brk_rows: list[dict] = []
    px_all: dict[str, pd.DataFrame] = {}

    for sym in POOL7:
        df = _load_5m(sym, cfg)
        if df.empty:
            continue
        gtl = compute_gtl_dataframe(df, lookback=23, vol_window=500)
        px = df["close"].astype(float).values
        px_all[sym] = df

        for day, idx in df.groupby("_day").groups.items():
            ii = list(idx)
            if not ii:
                continue
            i0, i1 = ii[0], ii[-1]
            r0 = gtl.iloc[i0]
            od = _open_dir(r0)
            if od == 0:
                continue
            entry, eod = px[i0], px[i1]
            signed_pct = ((eod - entry) if od > 0 else (entry - eod)) / entry * 100
            open_rows.append(
                {
                    "symbol": sym,
                    "day": day,
                    "aligned_open": bool(r0.get("break_aligns_birth")),
                    "signed_pct": signed_pct,
                    "win": signed_pct > 0,
                }
            )

        for i in gtl.index[gtl["break_aligns_birth"]]:
            d = int(gtl.loc[i, "break_dir"])
            ep = px[i]
            day = df.iloc[i]["_day"]
            for h, label in ((1, "5m"), (4, "20m"), (20, "100m")):
                j = min(i + h, len(px) - 1)
                raw_m = px[j] - ep
                signed_pct = (raw_m if d > 0 else -raw_m) / ep * 100
                brk_rows.append(
                    {"symbol": sym, "day": day, "hold": label, "signed_pct": signed_pct, "win": signed_pct > 0}
                )

    return pd.DataFrame(open_rows), pd.DataFrame(brk_rows)


def analyze_cs42() -> tuple[float, float, float, int]:
    root = ROOT / "data" / "orb" / "kline"
    ics: list[float] = []
    for d in sorted(root.iterdir()):
        if not d.is_dir():
            continue
        p = d / "1d.csv"
        if not p.is_file():
            continue
        df = pd.read_csv(p).sort_values("open_time")
        if len(df) < 5:
            continue
        c = df["close"].astype(float)
        mom = c.pct_change()
        fwd = c.pct_change().shift(-1)
        tmp = pd.DataFrame({"mom": mom, "fwd": fwd}).dropna()
        if len(tmp) < 5:
            continue
        ics.append(float(tmp["mom"].corr(tmp["fwd"])))
    arr = np.array(ics)
    return _t_p(arr) + (len(arr),)


def block(title: str, n: int, mean: float, t: float, p: float, wr: float) -> None:
    pb = stats.binomtest(int(round(wr * n)), n, 0.5, alternative="two-sided").pvalue if n else 1.0
    star = "**" if p < 0.05 else ("*" if p < 0.10 else "")
    print(f"\n{title}")
    print(f"  n={n}  mean={mean:+.3f}%  t={t:+.2f}  p={p:.4f} {star}")
    print(f"  win={wr*100:.1f}%  binom2s={pb:.4f}")


def main() -> int:
    cfg = OrbConfig.from_env()
    print("=" * 62)
    print("Fast pooled stats | pool7 5m full cache | 42-symbol daily IC")
    print("=" * 62)

    oe, ab = analyze_pool7(cfg)
    if not oe.empty:
        x = oe["signed_pct"].values
        m, t, p = _t_p(x)
        block("open@09:30 -> EOD (pool7, all symbol-days)", len(x), m, t, p, (x > 0).mean())

        sub = oe[oe["aligned_open"]]
        if len(sub) >= 20:
            x2 = sub["signed_pct"].values
            m, t, p = _t_p(x2)
            block("  only aligned break @ open", len(x2), m, t, p, (x2 > 0).mean())

        print("\n  per symbol:")
        for sym, g in oe.groupby("symbol"):
            m, t, pv = _t_p(g["signed_pct"].values)
            print(f"    {sym:5s} n={len(g):3d} mean={m:+.2f}% win={(g['win'].mean()*100):.0f}% p={pv:.3f}")

    if not ab.empty:
        print("\n" + "-" * 62)
        for hold in ("5m", "20m", "100m"):
            sub = ab[ab["hold"] == hold]
            x = sub["signed_pct"].values
            m, t, p = _t_p(x)
            block(f"aligned break -> +{hold} forward", len(x), m, t, p, (x > 0).mean())

    m, t, p, nd = analyze_cs42()
    print("\n" + "-" * 62)
    print(f"42 symbols | daily IC(momentum->next-day) over {nd} symbol-series")
    print(f"  mean IC={m:+.4f}  t={t:+.2f}  p={p:.4f}")

    print("\n(* p<0.10  ** p<0.05)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
