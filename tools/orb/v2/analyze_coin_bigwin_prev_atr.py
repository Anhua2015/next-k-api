#!/usr/bin/env python3
"""COIN OR10 大盈日前一日收盘 ATR 分析。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from env_loader import load_env_oi  # noqa: E402
from orb.core.backtest import _daily_df_asof  # noqa: E402
from orb.core.config import OrbConfig  # noqa: E402
from orb.core.indicators import compute_atr_series, daily_atr_asof  # noqa: E402
from orb.core.kline_cache import load_klines  # noqa: E402
from orb.core.rth_daily import aggregate_rth_daily_bars  # noqa: E402
from orb.core.session import session_anchor_ms  # noqa: E402

TRADES_CSV = ROOT / "output" / "orb" / "v2" / "eval" / "coin_or10_3pct_tw0.trades.csv"
OUT_JSON = ROOT / "output" / "orb" / "v2" / "eval" / "coin_or10_bigwin_prev_atr.json"
RISK_PCT = 0.03


def _prev_close(daily: pd.DataFrame, anchor_ms: int, tz: str) -> float | None:
    df = daily.drop_duplicates(subset=["open_time"], keep="last").sort_values("open_time")
    asof_day = pd.Timestamp(int(anchor_ms), unit="ms", tz=tz).normalize()
    day_ts = pd.to_datetime(df["open_time"], unit="ms", utc=True).dt.tz_convert(tz).dt.normalize()
    completed = df[day_ts < asof_day]
    if completed.empty:
        return None
    px = float(completed["close"].iloc[-1])
    return px if px > 0 else None


def _atr_series_pct(daily: pd.DataFrame, period: int, tz: str) -> pd.DataFrame:
    df = daily.drop_duplicates(subset=["open_time"], keep="last").sort_values("open_time").copy()
    df["atr"] = compute_atr_series(df, period=period)
    df["day"] = pd.to_datetime(df["open_time"], unit="ms", utc=True).dt.tz_convert(tz).dt.strftime("%Y-%m-%d")
    df["atr_pct"] = df["atr"] / df["close"].astype(float) * 100.0
    return df[["day", "open_time", "close", "atr", "atr_pct"]]


def main() -> int:
    load_env_oi()
    cfg = OrbConfig.from_env()
    cfg.or_minutes = 10
    cfg.risk_pct = RISK_PCT
    cfg.atr_period = 14
    cfg.sl_mode = "atr_pct"
    cfg.atr_sl_fraction = 0.05

    trades = pd.read_csv(TRADES_CSV)
    trades["pnl_r"] = trades["pnl_usdt"] / (trades["wallet_before"] * RISK_PCT)
    trades["is_win"] = trades["pnl_usdt"] > 0
    trades["is_big5r"] = trades["pnl_r"] >= 5
    trades["is_eod_win"] = (trades["outcome"] == "session_close") & trades["is_win"]

    df5 = load_klines("COINUSDT", "5m")
    daily = aggregate_rth_daily_bars(df5, cfg)
    atr_df = _atr_series_pct(daily, period=cfg.atr_period, tz=cfg.session_tz)
    atr_by_day = {r["day"]: r for _, r in atr_df.iterrows()}

    rows = []
    for _, t in trades.iterrows():
        d = str(t["session_date"])
        ts = pd.Timestamp(f"{d} 12:00:00", tz=cfg.session_tz)
        anchor = session_anchor_ms(int(ts.value // 1_000_000), tz=cfg.session_tz, session_open_time=cfg.session_open_time)
        atr = daily_atr_asof(daily, anchor, period=cfg.atr_period, tz=cfg.session_tz)
        prev_px = _prev_close(daily, anchor, cfg.session_tz)
        atr_pct = (atr / prev_px * 100.0) if atr and prev_px else np.nan

        # 前一日在序列中的 ATR（与 asof 口径一致：trade 日开盘时可见的 ATR）
        prev_row = atr_by_day.get(d)
        # 用 completed 最后一行 = 前一 RTH 收盘后更新的 ATR
        ddf = _daily_df_asof(daily, anchor)
        if not ddf.empty:
            completed_day = pd.to_datetime(ddf["open_time"], unit="ms", utc=True).dt.tz_convert(cfg.session_tz).dt.strftime("%Y-%m-%d").iloc[-1]
        else:
            completed_day = None

        # 相对过去 20 日 ATR% 分位（截至前一日）
        hist = atr_df[atr_df["open_time"] < anchor].tail(20)
        if len(hist) >= 5 and not np.isnan(atr_pct):
            pct_rank = float((hist["atr_pct"] < atr_pct).mean() * 100)
        else:
            pct_rank = np.nan

        rows.append(
            {
                "session_date": d,
                "side": t["side"],
                "pnl_usdt": float(t["pnl_usdt"]),
                "pnl_r": round(float(t["pnl_r"]), 2),
                "outcome": t["outcome"],
                "is_eod_win": bool(t["is_eod_win"]),
                "is_big5r": bool(t["is_big5r"]),
                "prev_rth_day": completed_day,
                "prev_close": prev_px,
                "prev_day_atr14": atr,
                "prev_day_atr_pct": round(atr_pct, 3) if not np.isnan(atr_pct) else None,
                "atr_pct_rank_vs_20d": round(pct_rank, 1) if not np.isnan(pct_rank) else None,
            }
        )

    df = pd.DataFrame(rows)

    def _summ(sub: pd.DataFrame, label: str) -> dict:
        if sub.empty:
            return {"label": label, "n": 0}
        return {
            "label": label,
            "n": int(len(sub)),
            "avg_prev_atr_pct": round(float(sub["prev_day_atr_pct"].mean()), 3),
            "median_prev_atr_pct": round(float(sub["prev_day_atr_pct"].median()), 3),
            "avg_atr_rank_20d": round(float(sub["atr_pct_rank_vs_20d"].mean()), 1),
            "median_atr_rank_20d": round(float(sub["atr_pct_rank_vs_20d"].median()), 1),
            "pct_rank_above_50": round(float((sub["atr_pct_rank_vs_20d"] > 50).mean() * 100), 1),
            "pct_rank_above_70": round(float((sub["atr_pct_rank_vs_20d"] > 70).mean() * 100), 1),
        }

    eod_wins = df[df["is_eod_win"]]
    big5 = df[df["is_big5r"]]
    losses = df[df["pnl_usdt"] <= 0]
    all_median_atr_pct = float(df["prev_day_atr_pct"].median())

    groups = [
        _summ(big5, "big_win_ge_5r"),
        _summ(eod_wins, "eod_win_all"),
        _summ(losses, "loss_or_small"),
        _summ(df, "all_trades"),
    ]

    # 大盈日明细
    big5_detail = big5.sort_values("pnl_usdt", ascending=False)[
        ["session_date", "side", "pnl_usdt", "pnl_r", "prev_day_atr_pct", "atr_pct_rank_vs_20d"]
    ].to_dict(orient="records")

    payload = {
        "symbol": "COINUSDT",
        "strategy": "OR10_3pct_eod",
        "atr_definition": "ATR(14) Wilder, asof prior RTH close before session open (same as ORB live)",
        "all_trades_median_prev_atr_pct": round(all_median_atr_pct, 3),
        "groups": groups,
        "big5r_days": big5_detail,
        "conclusion": {},
    }

    b = groups[0]
    a = groups[3]
    l = groups[2]
    payload["conclusion"] = {
        "big5r_vs_all_atr_pct": round(b["avg_prev_atr_pct"] - a["avg_prev_atr_pct"], 3),
        "big5r_vs_loss_atr_pct": round(b["avg_prev_atr_pct"] - l["avg_prev_atr_pct"], 3),
        "big5r_avg_rank_20d": b["avg_atr_rank_20d"],
        "interpretation": "",
    }

    if b["avg_prev_atr_pct"] > a["avg_prev_atr_pct"] + 0.15:
        interp = "大盈(>=5R)日前一日 ATR% 明显高于全体均值 → 偏高波动日更易出 fat tail"
    elif b["avg_prev_atr_pct"] < a["avg_prev_atr_pct"] - 0.15:
        interp = "大盈(>=5R)日前一日 ATR% 明显低于全体均值 → 压缩后突破更常见"
    else:
        interp = "大盈日前一日 ATR% 与全体/亏损日差异不大，不是单一决定性 filter"

    if b.get("avg_atr_rank_20d", 50) > 60:
        interp += "；相对近20日分位偏高"
    elif b.get("avg_atr_rank_20d", 50) < 40:
        interp += "；相对近20日分位偏低"
    else:
        interp += "；相对近20日分位居中"

    payload["conclusion"]["interpretation"] = interp

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    print(json.dumps(payload, indent=2, ensure_ascii=False))
    print(f"\nWrote {OUT_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
