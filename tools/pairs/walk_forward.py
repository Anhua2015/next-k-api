#!/usr/bin/env python3
"""Walk-forward OOS backtest for a pairs config (Ruuj framework validation)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pairs.walk_forward import run_walk_forward  # noqa: E402
from tools.pairs._common import load_aligned_prices, load_pair_config, merge_framework_defaults  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Pairs walk-forward OOS")
    ap.add_argument("--config", type=Path, required=True)
    ap.add_argument("--portfolio", type=Path, help="Optional portfolio.json framework overlay")
    ap.add_argument("--days", type=float, default=180.0)
    ap.add_argument("--train-days", type=float, default=90.0)
    ap.add_argument("--test-days", type=float, default=30.0)
    ap.add_argument("--step-days", type=float, default=30.0)
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("-o", type=Path, default=ROOT / "output/pairs/eval/walk_forward.json")
    args = ap.parse_args()

    cfg = load_pair_config(args.config)
    framework = {}
    if args.portfolio and args.portfolio.is_file():
        pf = json.loads(args.portfolio.read_text(encoding="utf-8"))
        framework = pf.get("framework") or {}
    cfg = merge_framework_defaults(cfg, framework)

    prices = load_aligned_prices(cfg, days=args.days, fetch=args.fetch)
    if prices.empty:
        print("Missing klines", file=sys.stderr)
        return 1

    bars_per_day = 24 if cfg.interval == "1h" else 1
    result = run_walk_forward(
        prices,
        cfg,
        train_bars=int(args.train_days * bars_per_day),
        test_bars=int(args.test_days * bars_per_day),
        step_bars=int(args.step_days * bars_per_day),
    )
    if result.get("error"):
        print(result, file=sys.stderr)
        return 1

    args.o.parent.mkdir(parents=True, exist_ok=True)
    args.o.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(
        f"{cfg.leg1}/{cfg.leg2} | folds={result['folds']} traded={result['folds_traded']} "
        f"OOS PnL={result['oos_total_pnl_usdt']:+.2f}U ({result['oos_return_pct']:+.2f}%) "
        f"trips={result['oos_round_trips']}"
    )
    for row in result["fold_details"]:
        if row.get("skipped"):
            print(f"  fold {row['fold']}: SKIP adf={row['adf_pvalue']}")
        else:
            print(
                f"  fold {row['fold']}: pnl={row['pnl_usdt']:+.0f}U "
                f"ret={row['return_pct']:+.1f}% trips={row['round_trips']}"
            )
    print(f"wrote {args.o}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
