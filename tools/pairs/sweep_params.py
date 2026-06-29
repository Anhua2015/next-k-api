#!/usr/bin/env python3
"""Grid search pairs params with train/holdout split."""

from __future__ import annotations

import argparse
import itertools
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from orb.core.kline_cache import load_klines
from pairs.backtest import PairsBacktestConfig, align_leg_closes, run_pairs_backtest

PAIRS = [
    ("BTCUSDT", "ETHUSDT"),
    ("COINUSDT", "MSTRUSDT"),
]

ENTRY_Z = [1.5, 2.0, 2.5, 3.0]
EXIT_Z = [0.0, 0.25, 0.5]
DELTA = [1e-5, 3e-5, 1e-4, 3e-4]


def _score(w: dict, *, min_trips: int = 5) -> float:
    """Higher is better; penalize too few trades and deep drawdown."""
    trips = int(w["round_trips"])
    if trips < min_trips:
        return -1e9
    pnl = float(w["total_pnl_usdt"])
    dd = abs(float(w["max_drawdown_usdt"]))
    fees = float(w["total_fees_usdt"])
    sharpe = float(w["sharpe"])
    # net edge after fees relative to capital; mild DD penalty
    cap = max(float(w.get("initial_capital_usdt") or 10_000), 1.0)
    ret = pnl / cap
    dd_pen = dd / cap
    fee_pen = fees / cap * 0.25
    return ret * 100 + sharpe * 0.5 - dd_pen * 30 - fee_pen * 10


def _run(prices, leg1: str, leg2: str, entry_z: float, exit_z: float, delta: float, *, cost_bps: float) -> dict:
    cfg = PairsBacktestConfig(
        leg1=leg1,
        leg2=leg2,
        interval="1h",
        delta=delta,
        r_noise=0,
        entry_z=entry_z,
        exit_z=exit_z,
        cost_bps=cost_bps,
        halt_p_trace_pct=None,
        initial_capital_usdt=10_000.0,
        deploy_pct=0.5,
    )
    return run_pairs_backtest(prices, cfg)["wallet"]


def _split_prices(prices, holdout_frac: float):
    n = len(prices)
    cut = int(n * (1.0 - holdout_frac))
    cut = max(cut, 30)
    return prices.iloc[:cut].reset_index(drop=True), prices.iloc[cut:].reset_index(drop=True)


def sweep_pair(
    leg1: str,
    leg2: str,
    prices,
    *,
    holdout_frac: float,
    cost_bps: float,
    min_trips_train: int,
    min_trips_hold: int,
) -> Dict[str, Any]:
    train, hold = _split_prices(prices, holdout_frac)
    rows: List[dict] = []

    for entry_z, exit_z, delta in itertools.product(ENTRY_Z, EXIT_Z, DELTA):
        wt = _run(train, leg1, leg2, entry_z, exit_z, delta, cost_bps=cost_bps)
        wh = _run(hold, leg1, leg2, entry_z, exit_z, delta, cost_bps=cost_bps)
        st = _score(wt, min_trips=min_trips_train)
        sh = _score(wh, min_trips=max(min_trips_hold, 2))
        combined = st * 0.55 + sh * 0.45 if sh > -1e8 else st - 50
        rows.append(
            {
                "entry_z": entry_z,
                "exit_z": exit_z,
                "delta": delta,
                "score": combined,
                "train": {
                    "bars": len(train),
                    "round_trips": wt["round_trips"],
                    "pnl_usdt": wt["total_pnl_usdt"],
                    "return_pct": wt["total_return_pct"],
                    "max_dd_usdt": wt["max_drawdown_usdt"],
                    "fees_usdt": wt["total_fees_usdt"],
                    "wr_pct": wt["win_rate_trades_pct"],
                    "sharpe": wt["sharpe"],
                },
                "holdout": {
                    "bars": len(hold),
                    "round_trips": wh["round_trips"],
                    "pnl_usdt": wh["total_pnl_usdt"],
                    "return_pct": wh["total_return_pct"],
                    "max_dd_usdt": wh["max_drawdown_usdt"],
                    "fees_usdt": wh["total_fees_usdt"],
                    "wr_pct": wh["win_rate_trades_pct"],
                    "sharpe": wh["sharpe"],
                },
            }
        )

    rows.sort(key=lambda r: -r["score"])
    best = rows[0]
    full_w = _run(prices, leg1, leg2, best["entry_z"], best["exit_z"], best["delta"], cost_bps=cost_bps)
    return {
        "pair": f"{leg1}/{leg2}",
        "bars_full": len(prices),
        "holdout_frac": holdout_frac,
        "cost_bps": cost_bps,
        "best": {
            "entry_z": best["entry_z"],
            "exit_z": best["exit_z"],
            "delta": best["delta"],
            "score": round(best["score"], 3),
            "train": best["train"],
            "holdout": best["holdout"],
            "full_sample": {
                "round_trips": full_w["round_trips"],
                "pnl_usdt": full_w["total_pnl_usdt"],
                "return_pct": full_w["total_return_pct"],
                "max_dd_usdt": full_w["max_drawdown_usdt"],
                "fees_usdt": full_w["total_fees_usdt"],
                "wr_pct": full_w["win_rate_trades_pct"],
                "sharpe": full_w["sharpe"],
            },
        },
        "top10": rows[:10],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Pairs parameter grid search")
    ap.add_argument("--days", type=float, default=180.0)
    ap.add_argument("--holdout-frac", type=float, default=0.33)
    ap.add_argument("--cost-bps", type=float, default=2.0)
    ap.add_argument("-o", type=Path, default=ROOT / "output/pairs/eval/param_sweep.json")
    ap.add_argument("--write-config", action="store_true", help="Update config/pairs/*/config.json")
    args = ap.parse_args()

    end_ms = int(time.time() * 1000)
    start_ms = end_ms - int(args.days * 86_400_000)

    results = []
    for leg1, leg2 in PAIRS:
        p = align_leg_closes(
            load_klines(leg1, "1h", start_ms=start_ms, end_ms=end_ms),
            load_klines(leg2, "1h", start_ms=start_ms, end_ms=end_ms),
        )
        if len(p) < 60:
            print(f"SKIP {leg1}/{leg2}: only {len(p)} bars")
            continue
        min_hold = 3 if leg2 == "MSTRUSDT" else 5
        r = sweep_pair(
            leg1,
            leg2,
            p,
            holdout_frac=args.holdout_frac,
            cost_bps=args.cost_bps,
            min_trips_train=8,
            min_trips_hold=min_hold,
        )
        results.append(r)
        b = r["best"]
        f = b["full_sample"]
        print(f"\n=== {r['pair']} ===")
        print(
            f"BEST entry={b['entry_z']} exit={b['exit_z']} delta={b['delta']} "
            f"(score={b['score']})"
        )
        print(
            f"  train:  trips={b['train']['round_trips']} pnl={b['train']['pnl_usdt']:+.0f}U "
            f"({b['train']['return_pct']:+.1f}%)"
        )
        print(
            f"  hold:   trips={b['holdout']['round_trips']} pnl={b['holdout']['pnl_usdt']:+.0f}U "
            f"({b['holdout']['return_pct']:+.1f}%)"
        )
        print(
            f"  full:   trips={f['round_trips']} pnl={f['pnl_usdt']:+.0f}U ({f['return_pct']:+.1f}%) "
            f"maxDD={f['max_dd_usdt']:.0f}U fees={f['fees_usdt']:.0f}U WR={f['wr_pct']:.0f}%"
        )

    out = {
        "days": args.days,
        "holdout_frac": args.holdout_frac,
        "cost_bps": args.cost_bps,
        "grid": {"entry_z": ENTRY_Z, "exit_z": EXIT_Z, "delta": DELTA},
        "results": results,
    }
    args.o.parent.mkdir(parents=True, exist_ok=True)
    args.o.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nwrote {args.o}")

    if args.write_config:
        mapping = {
            "BTCUSDT/ETHUSDT": ROOT / "config/pairs/btc_eth/config.json",
            "COINUSDT/MSTRUSDT": ROOT / "config/pairs/coin_mstr/config.json",
        }
        for r in results:
            path = mapping.get(r["pair"])
            if not path:
                continue
            b = r["best"]
            cfg = json.loads(path.read_text(encoding="utf-8"))
            cfg["entry_z"] = b["entry_z"]
            cfg["exit_z"] = b["exit_z"]
            cfg["delta"] = b["delta"]
            cfg["cost_bps"] = args.cost_bps
            path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            print(f"updated {path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
