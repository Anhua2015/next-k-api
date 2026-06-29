#!/usr/bin/env python3
"""Explore pairs universe: fetch 1h klines, screen, holdout + mini tune."""

from __future__ import annotations

import argparse
import itertools
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from orb.core.backtest import _load_range
from orb.core.kline_cache import load_klines, save_klines
from pairs.backtest import PairsBacktestConfig, align_leg_closes, run_pairs_backtest
from pairs.cointegration import cointegration_stats
from pairs.kalman import kalman_hedge_ratio, kalman_zscore

# Binance USDT-M / tokenized symbols worth trying
CRYPTO = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT",
    "LINKUSDT", "AVAXUSDT", "ADAUSDT", "LTCUSDT", "DOTUSDT", "UNIUSDT",
    "ATOMUSDT", "NEARUSDT", "FILUSDT", "APTUSDT", "ARBUSDT", "OPUSDT",
]
STOCK = [
    "COINUSDT", "MSTRUSDT", "TSLAUSDT", "NVDAUSDT", "HOODUSDT", "AMDUSDT",
    "METAUSDT", "AAPLUSDT", "AMZNUSDT", "GOOGLUSDT", "MSFTUSDT", "PLTRUSDT",
]

ENTRY_GRID = [1.5, 2.0, 2.5, 3.0]
EXIT_GRID = [0.0, 0.25]
DELTA_GRID = [1e-5, 3e-5, 1e-4]
CAP = 5000.0
LEV = 2.0
COST = 2.0


def fetch_leg(sym: str, days: float, *, force: bool = False) -> pd.DataFrame:
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - int(days * 86_400_000)
    df = load_klines(sym, "1h", start_ms=start_ms, end_ms=end_ms)
    need = force or df.empty or len(df) < int(days * 20)
    if need:
        fresh = _load_range(sym, "1h", start_ms, end_ms)
        if not fresh.empty:
            save_klines(sym, "1h", fresh)
            df = fresh
    return df


def split_prices(p: pd.DataFrame, holdout_frac: float) -> Tuple[pd.DataFrame, pd.DataFrame]:
    cut = max(int(len(p) * (1 - holdout_frac)), 30)
    return p.iloc[:cut].reset_index(drop=True), p.iloc[cut:].reset_index(drop=True)


def wallet_run(prices: pd.DataFrame, leg1: str, leg2: str, entry_z: float, exit_z: float, delta: float) -> dict:
    cfg = PairsBacktestConfig(
        leg1, leg2, "1h", delta, 0, entry_z, exit_z, COST, 63, None, None, CAP, 0.5, LEV,
    )
    return run_pairs_backtest(prices, cfg)["wallet"]


def quick_stats(p: pd.DataFrame, leg1: str, leg2: str, delta: float = 1e-5) -> dict:
    b, _, _, e, s, _ = kalman_hedge_ratio(p.leg1, p.leg2, delta=delta, r_noise=0)
    z = kalman_zscore(e, s)
    return {
        "bars": len(p),
        "start_ms": int(p.open_time.iloc[0]),
        "end_ms": int(p.open_time.iloc[-1]),
        "zmax": float(z.abs().max()),
        "z_gt2_pct": float((z.abs() > 2).mean() * 100),
    }


def tune_pair(leg1: str, leg2: str, train: pd.DataFrame, hold: pd.DataFrame) -> Optional[dict]:
    best = None
    for ez, xz, d in itertools.product(ENTRY_GRID, EXIT_GRID, DELTA_GRID):
        if quick_stats(train, leg1, leg2, d)["zmax"] < ez:
            continue
        wt = wallet_run(train, leg1, leg2, ez, xz, d)
        wh = wallet_run(hold, leg1, leg2, ez, xz, d)
        if wt["round_trips"] < 8 or wh["round_trips"] < 3:
            continue
        if wh["total_pnl_usdt"] <= 0:
            continue
        score = (
            wt["total_return_pct"] * 0.4
            + wh["total_return_pct"] * 0.6
            - abs(wt["max_drawdown_usdt"]) / CAP * 15
            - abs(wh["max_drawdown_usdt"]) / CAP * 20
        )
        row = {
            "entry_z": ez,
            "exit_z": xz,
            "delta": d,
            "score": score,
            "train": {k: wt[k] for k in ("round_trips", "total_pnl_usdt", "total_return_pct", "max_drawdown_usdt", "win_rate_trades_pct")},
            "holdout": {k: wh[k] for k in ("round_trips", "total_pnl_usdt", "total_return_pct", "max_drawdown_usdt", "win_rate_trades_pct")},
        }
        if best is None or score > best["score"]:
            best = row
    return best


def pair_class(leg1: str, leg2: str) -> str:
    stock_set = {s.replace("USDT", "") for s in STOCK}
    a, b = leg1.replace("USDT", ""), leg2.replace("USDT", "")
    if a in stock_set and b in stock_set:
        return "stock-stock"
    if a not in stock_set and b not in stock_set:
        return "crypto-crypto"
    return "mixed"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=180.0)
    ap.add_argument("--holdout-frac", type=float, default=0.33)
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--top-screen", type=int, default=25, help="pairs to mini-tune")
    ap.add_argument("-o", type=Path, default=ROOT / "output/pairs/eval/pair_explore.json")
    args = ap.parse_args()

    universe = list(dict.fromkeys(CRYPTO + STOCK))
    loaded: Dict[str, pd.DataFrame] = {}

    print(f"Loading {len(universe)} symbols, {args.days:.0f}d 1h ...")
    for sym in universe:
        df = fetch_leg(sym, args.days, force=args.fetch)
        if not df.empty:
            loaded[sym] = df
            print(f"  {sym}: {len(df)} bars", flush=True)
        else:
            print(f"  {sym}: SKIP (no data)", flush=True)

    # Phase 1: cointegration rank (train window)
    coint_rows: List[dict] = []
    syms = sorted(loaded.keys())
    for a, b in itertools.combinations(syms, 2):
        p = align_leg_closes(loaded[a], loaded[b])
        if len(p) < 200:
            continue
        train, _ = split_prices(p, args.holdout_frac)
        cg = cointegration_stats(train.leg1, train.leg2)
        cls = pair_class(a, b)
        if cls == "mixed":
            continue  # paper-style: skip crypto-stock mixes
        coint_rows.append({"leg1": a, "leg2": b, "class": cls, "bars": len(p), **cg})

    coint_rows.sort(key=lambda r: (r["adf_pvalue"], -(r["half_life_bars"] or 9999)))
    print(f"\nCointegration candidates (no mixed): {len(coint_rows)}")
    passed = [r for r in coint_rows if r["adf_pvalue"] < 0.10 or r.get("half_life_bars", 999) < 72]
    print(f"  adf_p<0.10 or HL<72h: {len(passed)}")

    # Phase 2: coarse PnL screen on coint pool first, then rest
    pool = {f"{r['leg1']}/{r['leg2']}": r for r in passed}
    pool.update({f"{r['leg1']}/{r['leg2']}": r for r in coint_rows[:40]})

    screen_rows: List[dict] = []
    seen = set()
    for key, meta in pool.items():
        a, b = meta["leg1"], meta["leg2"]
        if key in seen:
            continue
        seen.add(key)
        p = align_leg_closes(loaded[a], loaded[b])
        st = quick_stats(p, a, b)
        if st["zmax"] < 1.5:
            continue
        ez = 2.5 if meta["class"] == "crypto-crypto" else 1.5
        w = wallet_run(p, a, b, ez, 0.0, 1e-5)
        if w["round_trips"] < 5:
            continue
        if w["round_trips"] <= 2 and w["total_pnl_usdt"] > CAP:
            continue
        screen_rows.append(
            {
                "leg1": a,
                "leg2": b,
                "class": meta["class"],
                "bars": st["bars"],
                "adf_pvalue": meta["adf_pvalue"],
                "half_life_bars": meta.get("half_life_bars"),
                "log_corr": meta.get("log_corr"),
                "zmax": round(st["zmax"], 2),
                "trips": w["round_trips"],
                "pnl": w["total_pnl_usdt"],
                "ret_pct": w["total_return_pct"],
                "dd": w["max_drawdown_usdt"],
                "wr": w["win_rate_trades_pct"],
            }
        )

    screen_rows.sort(key=lambda r: (r["adf_pvalue"], -r["pnl"]))
    print(f"\nCoarse screen: {len(screen_rows)} tradeable pairs")

    # Phase 2: tune top N with holdout
    tuned: List[dict] = []
    candidates = screen_rows[: args.top_screen]
    for i, row in enumerate(candidates):
        leg1, leg2 = row["leg1"], row["leg2"]
        p = align_leg_closes(loaded[leg1], loaded[leg2])
        train, hold = split_prices(p, args.holdout_frac)
        print(f"tune [{i+1}/{len(candidates)}] {leg1}/{leg2} ...", flush=True)
        best = tune_pair(leg1, leg2, train, hold)
        if best:
            full = wallet_run(p, leg1, leg2, best["entry_z"], best["exit_z"], best["delta"])
            tuned.append(
                {
                    "pair": f"{leg1}/{leg2}",
                    "class": row["class"],
                    "bars": row["bars"],
                    "best_params": {
                        "entry_z": best["entry_z"],
                        "exit_z": best["exit_z"],
                        "delta": best["delta"],
                    },
                    "train": best["train"],
                    "holdout": best["holdout"],
                    "full_sample": {
                        "round_trips": full["round_trips"],
                        "total_pnl_usdt": full["total_pnl_usdt"],
                        "total_return_pct": full["total_return_pct"],
                        "max_drawdown_usdt": full["max_drawdown_usdt"],
                        "win_rate_trades_pct": full["win_rate_trades_pct"],
                    },
                    "score": round(best["score"], 3),
                }
            )

    tuned.sort(key=lambda r: -r["score"])

    # baselines
    baselines = {}
    for leg1, leg2 in [("BTCUSDT", "ETHUSDT"), ("COINUSDT", "MSTRUSDT")]:
        p = align_leg_closes(loaded.get(leg1, pd.DataFrame()), loaded.get(leg2, pd.DataFrame()))
        if p.empty:
            continue
        train, hold = split_prices(p, args.holdout_frac)
        kw = (3.0, 0.25, 3e-5) if leg1 == "BTCUSDT" else (1.5, 0.0, 1e-5)
        baselines[f"{leg1}/{leg2}"] = {
            "params": {"entry_z": kw[0], "exit_z": kw[1], "delta": kw[2]},
            "train": wallet_run(train, leg1, leg2, *kw),
            "holdout": wallet_run(hold, leg1, leg2, *kw),
            "full": wallet_run(p, leg1, leg2, *kw),
        }

    out = {
        "days": args.days,
        "holdout_frac": args.holdout_frac,
        "symbols_loaded": syms,
        "screen_count": len(screen_rows),
        "coint_top20": coint_rows[:20],
        "screen_top20": screen_rows[:20],
        "tuned_top15": tuned[:15],
        "baselines": baselines,
    }
    args.o.parent.mkdir(parents=True, exist_ok=True)
    args.o.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nwrote {args.o}")

    print("\n=== TOP tuned (holdout>0) ===")
    for r in tuned[:10]:
        h = r["holdout"]
        f = r["full_sample"]
        p = r["best_params"]
        print(
            f"{r['pair']:<24} [{r['class']}] score={r['score']:.1f} "
            f"hold={h['total_pnl_usdt']:+.0f}U full={f['total_pnl_usdt']:+.0f}U "
            f"ez={p['entry_z']} xz={p['exit_z']} d={p['delta']}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
