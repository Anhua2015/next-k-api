#!/usr/bin/env python3
"""Multi-pair portfolio backtest (Ruuj complete framework)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pairs.backtest import PairsBacktestConfig, run_pairs_backtest  # noqa: E402
from pairs.walk_forward import run_walk_forward  # noqa: E402
from tools.pairs._common import (  # noqa: E402
    load_aligned_prices,
    load_pair_config,
    merge_framework_defaults,
)


def _load_portfolio(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser(description="Pairs portfolio backtest")
    ap.add_argument(
        "--portfolio",
        type=Path,
        default=ROOT / "config/pairs/portfolio.json",
    )
    ap.add_argument("--days", type=float, default=180.0)
    ap.add_argument("--walk-forward", action="store_true", help="Also run walk-forward OOS per pair")
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("-o", type=Path, default=ROOT / "output/pairs/eval/portfolio.json")
    args = ap.parse_args()

    pf = _load_portfolio(args.portfolio)
    framework = pf.get("framework") or {}
    total_cap = float(pf.get("initial_capital_usdt") or 10_000.0)
    alloc = pf.get("allocation") or {}
    pairs_dir = args.portfolio.parent

    results: List[dict] = []
    sum_pnl = 0.0
    sum_start = 0.0

    for name, weight in alloc.items():
        cfg_path = pairs_dir / name / "config.json"
        if not cfg_path.is_file():
            print(f"Missing config: {cfg_path}", file=sys.stderr)
            return 1
        base = load_pair_config(cfg_path)
        cap = total_cap * float(weight)
        cfg = merge_framework_defaults(base, framework)
        cfg_kw = {**{k: getattr(cfg, k) for k in cfg.__dataclass_fields__}, "initial_capital_usdt": cap}
        cfg = PairsBacktestConfig(**cfg_kw)

        prices = load_aligned_prices(cfg, days=args.days, fetch=args.fetch)
        if prices.empty:
            print(f"No data for {name}", file=sys.stderr)
            return 1

        bt = run_pairs_backtest(prices, cfg)
        w = bt["wallet"]
        row: Dict[str, Any] = {
            "name": name,
            "pair": f"{cfg.leg1}/{cfg.leg2}",
            "capital_usdt": cap,
            "wallet": w,
            "bars": bt["bars"],
        }

        if args.walk_forward:
            bars_per_day = 24 if cfg.interval == "1h" else 1
            train_d = float(framework.get("walk_forward_train_days") or 90)
            test_d = float(framework.get("walk_forward_test_days") or 30)
            step_d = float(framework.get("walk_forward_step_days") or test_d)
            row["walk_forward"] = run_walk_forward(
                prices,
                cfg,
                train_bars=int(train_d * bars_per_day),
                test_bars=int(test_d * bars_per_day),
                step_bars=int(step_d * bars_per_day),
            )

        results.append(row)
        sum_pnl += float(w["total_pnl_usdt"])
        sum_start += cap
        print(
            f"{name:<12} {cfg.leg1}/{cfg.leg2} cap={cap:.0f} "
            f"PnL={w['total_pnl_usdt']:+.2f}U ({w['total_return_pct']:+.2f}%) "
            f"trips={w['round_trips']} maxDD={w['max_drawdown_usdt']:.0f}U "
            f"fees={w['total_fees_usdt']:.0f} fund={w.get('total_funding_usdt', 0):.0f}"
        )

    combined_ret = sum_pnl / sum_start * 100 if sum_start > 0 else 0.0
    out = {
        "portfolio": str(args.portfolio),
        "days": args.days,
        "initial_capital_usdt": total_cap,
        "combined_pnl_usdt": round(sum_pnl, 2),
        "combined_return_pct": round(combined_ret, 2),
        "pairs": results,
    }
    args.o.parent.mkdir(parents=True, exist_ok=True)
    args.o.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nCombined: {sum_start:.0f}U -> {sum_start + sum_pnl:.2f}U ({combined_ret:+.2f}%)")
    print(f"wrote {args.o}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
