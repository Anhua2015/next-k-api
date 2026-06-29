#!/usr/bin/env python3
"""COIN OR10@3%：5m 9/20 EMA 与真/假突破、大盈日关系。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from env_loader import load_env_oi  # noqa: E402
from orb.core.config import OrbConfig  # noqa: E402
from orb.core.ema import aggregate_ohlcv, ema_trend_allows, ema_values_asof  # noqa: E402
from orb.core.kline_cache import load_klines  # noqa: E402

BAR_5M = 300_000
TRADES = ROOT / "output/orb/v2/eval/coin_or10_3pct_tw0.trades.csv"
OUT = ROOT / "output/orb/v2/eval/coin_or10_ema5_analysis.json"


def main() -> int:
    load_env_oi()
    cfg = OrbConfig.from_env()
    trades = pd.read_csv(TRADES)
    trades["pnl_r"] = trades["pnl_usdt"] / (trades["wallet_before"] * 0.03)

    lo, hi = str(trades["session_date"].min()), str(trades["session_date"].max())
    fetch_start = int(pd.Timestamp(lo + " 09:30:00", tz=cfg.session_tz).value // 1_000_000) - BAR_5M * 200
    fetch_end = int(pd.Timestamp(hi + " 16:00:00", tz=cfg.session_tz).value // 1_000_000)
    df5 = aggregate_ohlcv(load_klines("COINUSDT", "5m", start_ms=fetch_start, end_ms=fetch_end), BAR_5M)

    rows = []
    for _, t in trades.iterrows():
        fill_ms = int(t.get("fill_bar_open_ms") or t.get("scan_open_ms") or 0)
        side = str(t["side"])
        emas = ema_values_asof(df5, fill_ms - BAR_5M)
        if emas is None:
            continue
        e9, e20 = emas
        aligned = ema_trend_allows(side, e9, e20)
        rows.append(
            {
                "session_date": str(t["session_date"]),
                "side": side,
                "pnl_usdt": float(t["pnl_usdt"]),
                "pnl_r": float(t["pnl_r"]),
                "outcome": str(t["outcome"]),
                "win": float(t["pnl_usdt"]) > 0,
                "big5r": float(t["pnl_r"]) >= 5,
                "ema9": round(e9, 4),
                "ema20": round(e20, 4),
                "ema9_gt_20": e9 > e20,
                "aligned": aligned,
            }
        )

    df = pd.DataFrame(rows)
    big_dates = set(df.loc[df["big5r"], "session_date"])

    def _stat(sub: pd.DataFrame) -> dict:
        if sub.empty:
            return {"n": 0}
        return {
            "n": int(len(sub)),
            "win_rate": round(sub["win"].mean() * 100, 1),
            "big5r": int(sub["big5r"].sum()),
            "net_pnl": round(float(sub["pnl_usdt"].sum()), 1),
        }

    aligned = df[df["aligned"]]
    counter = df[~df["aligned"]]

    missed_big = sorted(big_dates - set(aligned["session_date"]))
    kept_big = sorted(big_dates & set(aligned["session_date"]))

    payload = {
        "ema_bar": "5m",
        "rule": "LONG requires EMA9>EMA20; SHORT requires EMA9<EMA20 at fill",
        "all": _stat(df),
        "ema_aligned": _stat(aligned),
        "ema_counter": _stat(counter),
        "big5r_days_kept": kept_big,
        "big5r_days_missed_if_filter": missed_big,
        "big5r_kept": f"{len(kept_big)}/{len(big_dates)}",
        "trades": df.to_dict(orient="records"),
    }
    OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    print("5m EMA filter post-hoc on OR10@3% trades")
    print("  all:     n=%d WR=%.1f%% big5R=%d net=%+.0fU" % (payload["all"]["n"], payload["all"]["win_rate"], payload["all"]["big5r"], payload["all"]["net_pnl"]))
    print("  aligned: n=%d WR=%.1f%% big5R=%d net=%+.0fU" % (payload["ema_aligned"]["n"], payload["ema_aligned"]["win_rate"], payload["ema_aligned"]["big5r"], payload["ema_aligned"]["net_pnl"]))
    print("  counter: n=%d WR=%.1f%% big5R=%d net=%+.0fU" % (payload["ema_counter"]["n"], payload["ema_counter"]["win_rate"], payload["ema_counter"]["big5r"], payload["ema_counter"]["net_pnl"]))
    print("  big5R kept if filter: %s" % payload["big5r_kept"])
    if missed_big:
        print("  missed big days:", ", ".join(missed_big))
    print("wrote", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
