#!/usr/bin/env python3
"""Compare pairs backtests across leg combinations."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from orb.core.backtest import _load_range  # noqa: E402
from orb.core.kline_cache import load_klines, save_klines  # noqa: E402
from pairs.backtest import PairsBacktestConfig, align_leg_closes, run_pairs_backtest  # noqa: E402

PRESETS = {
    "baseline": {"entry_z": 2.0, "exit_z": 0.0, "delta": 1e-4, "cost_bps": 4},
    "optimized": {"entry_z": 2.5, "exit_z": 0.0, "delta": 1e-5, "cost_bps": 2},
}


def _load_leg(symbol: str, interval: str, *, days: float, fetch: bool):
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - int(days * 86_400_000)
    df = load_klines(symbol, interval, start_ms=start_ms, end_ms=end_ms)
    if (df.empty or len(df) < 100) and fetch:
        fresh = _load_range(symbol, interval, start_ms, end_ms)
        if not fresh.empty:
            save_klines(symbol, interval, fresh)
            df = fresh
    return df


def _run_pair(
    leg1: str,
    leg2: str,
    *,
    interval: str,
    days: float,
    fetch: bool,
    preset: str,
    initial_capital: float,
    deploy_pct: float,
) -> dict:
    kw = dict(PRESETS[preset])
    cfg = PairsBacktestConfig(
        leg1=leg1,
        leg2=leg2,
        interval=interval,
        r_noise=0,
        halt_p_trace_pct=None,
        initial_capital_usdt=initial_capital,
        deploy_pct=deploy_pct,
        **kw,
    )
    df1 = _load_leg(leg1, interval, days=days, fetch=fetch)
    df2 = _load_leg(leg2, interval, days=days, fetch=fetch)
    if df1.empty or df2.empty:
        return {"error": "missing_klines", "leg1": leg1, "leg2": leg2, "bars1": len(df1), "bars2": len(df2)}
    prices = align_leg_closes(df1, df2)
    if len(prices) < 30:
        return {
            "error": "insufficient_overlap",
            "leg1": leg1,
            "leg2": leg2,
            "bars1": len(df1),
            "bars2": len(df2),
            "overlap": len(prices),
        }
    result = run_pairs_backtest(prices, cfg)
    w = result["wallet"]
    ts = result["series"]["open_time"]
    return {
        "pair": f"{leg1}/{leg2}",
        "preset": preset,
        "bars": result["bars"],
        "overlap_start_ms": ts[0],
        "overlap_end_ms": ts[-1],
        "round_trips": w["round_trips"],
        "total_pnl_usdt": w["total_pnl_usdt"],
        "total_return_pct": w["total_return_pct"],
        "max_drawdown_usdt": w["max_drawdown_usdt"],
        "total_fees_usdt": w["total_fees_usdt"],
        "win_rate_trades_pct": w["win_rate_trades_pct"],
        "sharpe": w["sharpe"],
        "config": result["config"],
        "full": result,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Compare pairs Kalman backtests")
    ap.add_argument("--days", type=float, default=180.0)
    ap.add_argument("--interval", default="1h")
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--preset", choices=list(PRESETS), default="optimized")
    ap.add_argument("--capital", type=float, default=10_000.0)
    ap.add_argument("--deploy-pct", type=float, default=0.5)
    ap.add_argument(
        "--pairs",
        nargs="+",
        default=["BTCUSDT/ETHUSDT", "COINUSDT/MSTRUSDT", "SOLUSDT/ETHUSDT"],
    )
    ap.add_argument("-o", type=Path, default=ROOT / "output/pairs/eval/pair_compare.json")
    args = ap.parse_args()

    rows = []
    for spec in args.pairs:
        leg1, leg2 = spec.split("/", 1)
        print(f"Running {leg1}/{leg2} ...", flush=True)
        row = _run_pair(
            leg1,
            leg2,
            interval=args.interval,
            days=args.days,
            fetch=args.fetch,
            preset=args.preset,
            initial_capital=args.capital,
            deploy_pct=args.deploy_pct,
        )
        rows.append(row)
        if row.get("error"):
            print(f"  ERROR: {row['error']} bars1={row.get('bars1')} bars2={row.get('bars2')} overlap={row.get('overlap')}")
        else:
            print(
                f"  bars={row['bars']} trips={row['round_trips']} "
                f"PnL={row['total_pnl_usdt']:+.1f}U ({row['total_return_pct']:+.1f}%) "
                f"fees={row['total_fees_usdt']:.0f}U WR={row['win_rate_trades_pct']}%"
            )

    out = {
        "days": args.days,
        "interval": args.interval,
        "preset": args.preset,
        "capital": args.capital,
        "deploy_pct": args.deploy_pct,
        "results": [{k: v for k, v in r.items() if k != "full"} for r in rows],
    }
    args.o.parent.mkdir(parents=True, exist_ok=True)
    args.o.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")

    # per-pair full JSON
    detail_dir = args.o.parent / "by_pair"
    detail_dir.mkdir(parents=True, exist_ok=True)
    for r in rows:
        if r.get("full"):
            name = r["pair"].replace("/", "_").lower()
            (detail_dir / f"{name}_{args.preset}.json").write_text(
                json.dumps(r["full"], indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

    print(f"\nwrote {args.o}")
    return 0 if all(not r.get("error") for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
