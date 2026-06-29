#!/usr/bin/env python3
"""Pairs + Kalman 回测 CLI。"""

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


def _load_leg(symbol: str, interval: str, *, days: float, fetch: bool) -> "pd.DataFrame":
    import pandas as pd

    end_ms = int(time.time() * 1000)
    start_ms = end_ms - int(days * 86_400_000)
    df = load_klines(symbol, interval, start_ms=start_ms, end_ms=end_ms)
    if df.empty and fetch:
        df = _load_range(symbol, interval, start_ms, end_ms)
        if not df.empty:
            save_klines(symbol, interval, df)
    return df


def main() -> int:
    ap = argparse.ArgumentParser(description="Pairs Kalman backtest")
    ap.add_argument("--leg1", default="BTCUSDT")
    ap.add_argument("--leg2", default="ETHUSDT")
    ap.add_argument("--interval", default="1h")
    ap.add_argument("--days", type=float, default=180.0)
    ap.add_argument("--delta", type=float, default=1e-4)
    ap.add_argument("--entry-z", type=float, default=1.0)
    ap.add_argument("--exit-z", type=float, default=0.0)
    ap.add_argument("--cost-bps", type=float, default=10.0)
    ap.add_argument("--capital", type=float, default=10_000.0, help="初始 USDT")
    ap.add_argument("--deploy-pct", type=float, default=0.5, help="每次开仓 leg1 名义 = 权益 × deploy_pct × leverage")
    ap.add_argument("--leverage", type=float, default=1.0, help="杠杆倍数（放大 leg 名义）")
    ap.add_argument("--fetch", action="store_true", help="Binance 拉取缺失 K 线")
    ap.add_argument("--config", type=Path, help="JSON config（覆盖 CLI 参数）")
    ap.add_argument("-o", "--output", type=Path, default=ROOT / "output/pairs/eval/last_backtest.json")
    args = ap.parse_args()

    cfg_kw = {
        "leg1": args.leg1,
        "leg2": args.leg2,
        "interval": args.interval,
        "delta": args.delta,
        "entry_z": args.entry_z,
        "exit_z": args.exit_z,
        "cost_bps": args.cost_bps,
        "initial_capital_usdt": args.capital,
        "deploy_pct": args.deploy_pct,
        "leverage": args.leverage,
    }
    if args.config and args.config.is_file():
        cfg_kw.update(json.loads(args.config.read_text(encoding="utf-8")))

    cfg = PairsBacktestConfig(**{k: v for k, v in cfg_kw.items() if k in PairsBacktestConfig.__dataclass_fields__})
    days = float(args.days)

    print(f"Pairs Kalman | {cfg.leg1} / {cfg.leg2} | {cfg.interval} | {days:.0f}d | lev={cfg.leverage}x", flush=True)
    df1 = _load_leg(cfg.leg1, cfg.interval, days=days, fetch=args.fetch)
    df2 = _load_leg(cfg.leg2, cfg.interval, days=days, fetch=args.fetch)
    if df1.empty or df2.empty:
        print("Missing klines — run with --fetch or refresh_klines.py", file=sys.stderr)
        return 1

    prices = align_leg_closes(df1, df2)
    result = run_pairs_backtest(prices, cfg)
    if result.get("error"):
        print(result, file=sys.stderr)
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    w = result["wallet"]
    print(
        f"bars={result['bars']} round_trips={w['round_trips']} "
        f"PnL={w['total_pnl_usdt']:+.2f}U ({w['total_return_pct']:+.2f}%) "
        f"final={w['final_equity_usdt']:.2f}U maxDD={w['max_drawdown_usdt']:.2f}U "
        f"fees={w['total_fees_usdt']:.2f}U WR={w['win_rate_trades_pct']}% Sharpe={w['sharpe']}"
    )
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
