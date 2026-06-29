#!/usr/bin/env python3
"""Export pairs round-trip trade details."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from orb.core.kline_cache import load_klines
from pairs.backtest import (
    PairsBacktestConfig,
    align_leg_closes,
    signals_from_zscore,
    _leg_notional_usdt,
    _tx_fee_usdt,
)
from pairs.kalman import kalman_hedge_ratio, kalman_zscore


def extract_round_trips(
    prices: pd.DataFrame,
    position: pd.Series,
    zscore: pd.Series,
    beta: pd.Series,
    cfg: PairsBacktestConfig,
) -> pd.DataFrame:
    p1 = prices["leg1"].astype(float)
    p2 = prices["leg2"].astype(float)
    times = prices["open_time"].astype("int64")
    n = len(prices)

    equity = float(cfg.initial_capital_usdt)
    q1 = q2 = 0.0
    trade_entry_equity: Optional[float] = None
    entry_idx: Optional[int] = None
    entry_side: Optional[str] = None
    entry_z: Optional[float] = None
    entry_fee = 0.0
    rows: List[Dict[str, Any]] = []

    for t in range(1, n):
        pos_held = float(position.iloc[t - 1])
        pos_end = float(position.iloc[t])
        gross = 0.0

        if pos_held != 0.0 and q1 != 0.0:
            gross = pos_held * (
                q1 * (float(p1.iloc[t]) - float(p1.iloc[t - 1]))
                - q2 * (float(p2.iloc[t]) - float(p2.iloc[t - 1]))
            )
        equity += gross

        if pos_end != pos_held:
            if pos_held != 0.0 and entry_idx is not None:
                n1 = abs(q1) * float(p1.iloc[t])
                n2 = abs(q2) * float(p2.iloc[t])
                fee_close = _tx_fee_usdt(n1, n2, cfg)
                equity -= fee_close
                pnl = equity - float(trade_entry_equity)
                rows.append(
                    {
                        "pair": f"{cfg.leg1}/{cfg.leg2}",
                        "side": entry_side,
                        "entry_time_ms": int(times.iloc[entry_idx]),
                        "exit_time_ms": int(times.iloc[t]),
                        "entry_z": round(float(entry_z or 0), 4),
                        "exit_z": round(float(zscore.iloc[t]), 4),
                        "entry_leg1": round(float(p1.iloc[entry_idx]), 4),
                        "entry_leg2": round(float(p2.iloc[entry_idx]), 4),
                        "exit_leg1": round(float(p1.iloc[t]), 4),
                        "exit_leg2": round(float(p2.iloc[t]), 4),
                        "bars_held": t - entry_idx,
                        "pnl_usdt": round(pnl, 2),
                        "fees_usdt": round(entry_fee + fee_close, 2),
                        "equity_after": round(equity, 2),
                    }
                )
                trade_entry_equity = None
                entry_idx = None
                q1 = q2 = 0.0

            if pos_end != 0.0:
                notional = _leg_notional_usdt(equity, cfg)
                p1t = float(p1.iloc[t])
                p2t = float(p2.iloc[t])
                b = max(float(beta.iloc[t]), 1e-8)
                q1 = notional / p1t
                q2 = b * notional / p1t
                fee_open = _tx_fee_usdt(notional, b * p2t / p1t * notional, cfg)
                equity -= fee_open
                trade_entry_equity = equity
                entry_idx = t
                entry_side = "long_spread" if pos_end > 0 else "short_spread"
                entry_z = float(zscore.iloc[t])
                entry_fee = fee_open
            elif pos_held != 0.0:
                n1 = abs(q1) * float(p1.iloc[t])
                n2 = abs(q2) * float(p2.iloc[t])
                equity -= _tx_fee_usdt(n1, n2, cfg)

    if trade_entry_equity is not None and entry_idx is not None:
        t = n - 1
        rows.append(
            {
                "pair": f"{cfg.leg1}/{cfg.leg2}",
                "side": entry_side,
                "entry_time_ms": int(times.iloc[entry_idx]),
                "exit_time_ms": int(times.iloc[t]),
                "entry_z": round(float(entry_z or 0), 4),
                "exit_z": round(float(zscore.iloc[t]), 4),
                "entry_leg1": round(float(p1.iloc[entry_idx]), 4),
                "entry_leg2": round(float(p2.iloc[entry_idx]), 4),
                "exit_leg1": round(float(p1.iloc[t]), 4),
                "exit_leg2": round(float(p2.iloc[t]), 4),
                "bars_held": t - entry_idx,
                "pnl_usdt": round(equity - float(trade_entry_equity), 2),
                "fees_usdt": round(entry_fee, 2),
                "equity_after": round(equity, 2),
                "note": "open_at_end",
            }
        )

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    for col in ("entry_time_ms", "exit_time_ms"):
        df[col.replace("_ms", "_utc")] = pd.to_datetime(df[col], unit="ms", utc=True)
    return df


def run_from_config(config_path: Path, *, days: float) -> pd.DataFrame:
    cfg_kw = json.loads(config_path.read_text(encoding="utf-8"))
    cfg = PairsBacktestConfig(**{k: v for k, v in cfg_kw.items() if k in PairsBacktestConfig.__dataclass_fields__})
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - int(days * 86_400_000)
    df1 = load_klines(cfg.leg1, cfg.interval, start_ms=start_ms, end_ms=end_ms)
    df2 = load_klines(cfg.leg2, cfg.interval, start_ms=start_ms, end_ms=end_ms)
    prices = align_leg_closes(df1, df2)
    beta, _, _, e, s, p_trace = kalman_hedge_ratio(p1=prices.leg1, p2=prices.leg2, delta=cfg.delta, r_noise=cfg.r_noise)
    zscore = kalman_zscore(e, s)
    position = signals_from_zscore(zscore, entry_z=cfg.entry_z, exit_z=cfg.exit_z)
    return extract_round_trips(prices, position, zscore, beta, cfg)


def main() -> int:
    ap = argparse.ArgumentParser(description="Export pairs trade details")
    ap.add_argument("--config", type=Path, action="append", required=True)
    ap.add_argument("--days", type=float, default=180.0)
    ap.add_argument("--month", default="2026-06", help="YYYY-MM filter on exit_time")
    ap.add_argument("-o", type=Path, default=ROOT / "output/pairs/eval/trades_june.csv")
    args = ap.parse_args()

    parts = []
    for cp in args.config:
        parts.append(run_from_config(cp, days=args.days))
    all_trades = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()

    if all_trades.empty:
        print("No trades")
        return 1

    y, m = args.month.split("-")
    mask = (
        pd.to_datetime(all_trades["exit_time_ms"], unit="ms", utc=True).dt.year == int(y)
    ) & (pd.to_datetime(all_trades["exit_time_ms"], unit="ms", utc=True).dt.month == int(m))
    june = all_trades[mask].copy()
    june = june.sort_values("exit_time_ms")

    args.o.parent.mkdir(parents=True, exist_ok=True)
    june.to_csv(args.o, index=False)

    print(f"{args.month} closed round-trips: {len(june)}")
    if len(june):
        print(f"PnL sum: {june['pnl_usdt'].sum():+.2f}U | fees: {june['fees_usdt'].sum():.2f}U")
        print(f"WR: {(june['pnl_usdt'] > 0).mean() * 100:.0f}%")
        print(f"wrote {args.o}")
        print()
        for _, r in june.iterrows():
            side = "多spread" if r["side"] == "long_spread" else "空spread"
            print(
                f"{r['exit_time_utc']} | {r['pair']:<20} {side} "
                f"z {r['entry_z']:+.2f}->{r['exit_z']:+.2f} | "
                f"{int(r['bars_held'])}h | {r['pnl_usdt']:+.2f}U"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
