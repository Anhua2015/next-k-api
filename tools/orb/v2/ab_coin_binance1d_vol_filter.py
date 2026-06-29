#!/usr/bin/env python3
"""COIN OR10：Binance 1d 波动收敛前置 filter 回测（live 对齐 ATR 口径）。"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from env_loader import load_env_oi  # noqa: E402
from orb.core.backtest import _daily_df_asof  # noqa: E402
from orb.core.config import OrbConfig  # noqa: E402
from orb.core.indicators import compute_atr_series, daily_atr_asof  # noqa: E402
from orb.core.kline_cache import load_klines  # noqa: E402
from orb.core.session import session_anchor_ms  # noqa: E402
from orb.ml.gate import LiveGateConfig, gate_with_ml_bypass  # noqa: E402
from orb.v2.paths import resolve_gate_config_path  # noqa: E402
from orb.v2.robots import init_robot_wallets  # noqa: E402
from tools.orb.v2.backtest_universe import filter_backtest_sessions_with_atr, universe_session_dates  # noqa: E402
from tools.orb.v2.sim_live_session import DEFAULT_FEE_BPS_PER_SIDE, simulate_live_sessions  # noqa: E402

SYMBOL = "COINUSDT"
FROM_DATE = "2026-02-09"
TO_DATE = "2026-06-24"
ROBOT_EQUITY = 1000.0
ENTRY_FILL = "preplace_stop"
ATR_PERIOD = 14
RANK_LOOKBACK = 20


def _coin_cfg() -> OrbConfig:
    os.environ.setdefault("ORB_MARKET", "us_equity")
    os.environ["ORB_OR_MINUTES"] = "10"
    os.environ["ORB_RISK_PCT"] = "0.03"
    os.environ["ORB_TRADE_WINDOW_MINUTES"] = "0"
    os.environ["ORB_ONE_TRADE_PER_SESSION"] = "1"
    os.environ["ORB_EXIT_MODE"] = "eod"
    os.environ["ORB_SL_MODE"] = "atr_pct"
    os.environ["ORB_ATR_PERIOD"] = str(ATR_PERIOD)
    os.environ["ORB_ATR_SL_FRACTION"] = "0.05"
    os.environ["ORB_SIGNAL_INTERVAL"] = "5m"
    os.environ["ORB_MACRO_FILTER"] = "0"
    os.environ["ORB_V2_ROBOT_RESET_CAP"] = "0"
    os.environ["ORB_V2_GATE_ML"] = "0"
    return OrbConfig.from_env()


def _build_session_features(daily: pd.DataFrame, dates: List[str], cfg: OrbConfig) -> pd.DataFrame:
    d = daily.drop_duplicates(subset=["open_time"], keep="last").sort_values("open_time").copy()
    d["atr"] = compute_atr_series(d, period=ATR_PERIOD)
    d["atr_pct"] = d["atr"] / d["close"].astype(float) * 100.0
    d["range_pct"] = (d["high"].astype(float) - d["low"].astype(float)) / d["close"].astype(float) * 100.0
    rows: List[Dict[str, Any]] = []
    for day in dates:
        ts = pd.Timestamp(f"{day} 12:00:00", tz=cfg.session_tz)
        anchor = session_anchor_ms(int(ts.value // 1_000_000), tz=cfg.session_tz, session_open_time=cfg.session_open_time)
        atr = daily_atr_asof(d, anchor, period=ATR_PERIOD, tz=cfg.session_tz)
        ddf = _daily_df_asof(d, anchor)
        if atr is None or ddf.empty:
            continue
        prev_close = float(ddf["close"].iloc[-1])
        atr_pct = float(atr) / prev_close * 100.0 if prev_close > 0 else np.nan
        hist = d[d["open_time"] < anchor].tail(RANK_LOOKBACK)
        rank = float((hist["atr_pct"] < atr_pct).mean() * 100.0) if len(hist) >= 5 and not np.isnan(atr_pct) else np.nan
        prev = hist.iloc[-1] if len(hist) else None
        prev2 = hist.iloc[-2] if len(hist) >= 2 else None
        range_ma = float(hist["range_pct"].mean()) if len(hist) >= 5 else np.nan
        prev_range = float(prev["range_pct"]) if prev is not None else np.nan
        contracting = bool(prev is not None and prev2 is not None and float(prev["atr_pct"]) < float(prev2["atr_pct"]))
        narrow_range = bool(not np.isnan(range_ma) and not np.isnan(prev_range) and prev_range < range_ma)
        rows.append(
            {
                "session_date": day,
                "prev_day_atr_pct": round(atr_pct, 4) if not np.isnan(atr_pct) else None,
                "atr_rank20": round(rank, 1) if not np.isnan(rank) else None,
                "contracting": contracting,
                "narrow_range": narrow_range,
                "prev_range_pct": round(prev_range, 4) if not np.isnan(prev_range) else None,
            }
        )
    return pd.DataFrame(rows)


def _summarize(days: List[Dict[str, Any]], trades: List[Dict[str, Any]], end_wallet: float, elapsed: float) -> Dict[str, Any]:
    wins = sum(1 for t in trades if float(t.get("pnl_usdt") or 0) > 0)
    big5 = sum(
        1
        for t in trades
        if float(t.get("pnl_usdt") or 0) > 0
        and float(t.get("pnl_usdt") or 0) / (float(t.get("wallet_before") or 1) * 0.03) >= 5
    )
    gross_wins = sorted([float(t["pnl_usdt"]) for t in trades if float(t.get("pnl_usdt") or 0) > 0], reverse=True)
    return {
        "sessions": len(days),
        "opens": len(trades),
        "win_trades": wins,
        "loss_trades": len(trades) - wins,
        "win_rate": round(wins / len(trades) * 100, 1) if trades else 0.0,
        "big_wins_5r": big5,
        "net_pnl_usdt": round(sum(float(d.get("net_pnl_usdt") or 0) for d in days), 2),
        "return_pct": round((end_wallet / ROBOT_EQUITY - 1) * 100, 1),
        "end_wallet_usdt": round(end_wallet, 2),
        "avg_pnl_per_trade": round(sum(float(d.get("net_pnl_usdt") or 0) for d in days) / len(trades), 2) if trades else 0.0,
        "max_win_usdt": round(gross_wins[0], 2) if gross_wins else 0.0,
        "top3_wins_usdt": [round(x, 2) for x in gross_wins[:3]],
        "elapsed_sec": round(elapsed, 1),
    }


def _run_dates(
    dates: List[str],
    cfg: OrbConfig,
    *,
    tag: str,
    label: str,
) -> Dict[str, Any]:
    gate = gate_with_ml_bypass(LiveGateConfig.from_json(Path(resolve_gate_config_path())))
    t0 = time.time()
    wallets = init_robot_wallets(count=1, equity_usdt=ROBOT_EQUITY)
    days = simulate_live_sessions(
        dates,
        [SYMBOL],
        gate=gate,
        ranker=None,
        cfg=cfg,
        robot_wallets=wallets,
        respect_env_filters=False,
        fee_bps_per_side=DEFAULT_FEE_BPS_PER_SIDE,
        entry_fill=ENTRY_FILL,
        ml_enabled=False,
        atr_daily_source="binance_1d",
    )
    trades = [t for d in days for t in (d.get("trades") or [])]
    row = {"tag": tag, "label": label, "trade_dates": [str(t.get("session_date")) for t in trades], **_summarize(days, trades, float(wallets[0]), time.time() - t0)}
    return row


def main() -> int:
    load_env_oi()
    cfg = _coin_cfg()

    raw = [d for d in universe_session_dates([SYMBOL], cfg) if FROM_DATE <= d <= TO_DATE]
    all_dates = filter_backtest_sessions_with_atr(raw, [SYMBOL], cfg, atr_daily_source="binance_1d")

    warmup_start = pd.Timestamp(FROM_DATE, tz=cfg.session_tz).value // 1_000_000 - cfg.daily_atr_warmup_ms()
    end_ms = pd.Timestamp(TO_DATE + " 23:59:59", tz=cfg.session_tz).value // 1_000_000
    daily = load_klines(SYMBOL, "1d", start_ms=int(warmup_start), end_ms=int(end_ms))
    feat = _build_session_features(daily, all_dates, cfg)
    median_atr = float(feat["prev_day_atr_pct"].median()) if not feat.empty else 0.0

    filters: List[tuple[str, str, Callable[[pd.Series], bool]]] = [
        ("baseline", "无 filter（Binance 1d ATR 全样本）", lambda r: True),
        ("rank50", "前日 ATR% 近20日分位 ≤ 50", lambda r: float(r["atr_rank20"]) <= 50),
        ("rank40", "前日 ATR% 近20日分位 ≤ 40", lambda r: float(r["atr_rank20"]) <= 40),
        ("rank30", "前日 ATR% 近20日分位 ≤ 30", lambda r: float(r["atr_rank20"]) <= 30),
        ("below_median", f"前日 ATR% < 样本中位 ({median_atr:.2f}%)", lambda r: float(r["prev_day_atr_pct"]) < median_atr),
        ("contracting", "前日 ATR% 低于前两日（收敛）", lambda r: bool(r["contracting"])),
        ("narrow_range", "前日振幅 < 近20日均振幅", lambda r: bool(r["narrow_range"])),
        ("rank50_narrow", "分位≤50 且 narrow range", lambda r: float(r["atr_rank20"]) <= 50 and bool(r["narrow_range"])),
    ]

    feat_by_day = {str(r["session_date"]): r for _, r in feat.iterrows()}
    results: List[Dict[str, Any]] = []

    print(f"COIN OR10 + Binance 1d vol filter | {FROM_DATE}..{TO_DATE} | eq={ROBOT_EQUITY}U\n", flush=True)
    for tag, label, pred in filters:
        dates = [d for d in all_dates if d in feat_by_day and pred(feat_by_day[d])]
        print(f"[{tag}] {label} | sessions={len(dates)} ...", flush=True)
        row = _run_dates(dates, cfg, tag=tag, label=label)
        row["filtered_sessions"] = len(dates)
        row["skipped_sessions"] = len(all_dates) - len(dates)
        results.append(row)
        print(
            f"  opens={row['opens']} net={row['net_pnl_usdt']:+.1f}U ret={row['return_pct']:+.1f}% "
            f"win={row['win_rate']:.0f}% big5R={row['big_wins_5r']} maxWin={row['max_win_usdt']}U ({row['elapsed_sec']}s)\n",
            flush=True,
        )

    out = ROOT / "output" / "orb" / "v2" / "eval" / "coin_or10_binance1d_vol_filter.json"
    payload = {
        "symbol": SYMBOL,
        "from_date": FROM_DATE,
        "to_date": TO_DATE,
        "atr_source": "binance_1d",
        "atr_period": ATR_PERIOD,
        "strategy": "OR10_3pct_eod_preplace_stop",
        "all_atr_sessions": len(all_dates),
        "median_prev_atr_pct_binance1d": round(median_atr, 4),
        "session_features": feat.to_dict(orient="records"),
        "variants": results,
    }
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
