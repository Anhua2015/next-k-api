#!/usr/bin/env python3
"""COIN OR10：开盘30min振幅 filter + early exit 全样本回测。"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

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
from orb.core.session import (  # noqa: E402
    compute_opening_range,
    session_anchor_ms,
    session_slice,
)
from orb.ml.gate import LiveGateConfig, gate_with_ml_bypass  # noqa: E402
from orb.v2.paths import resolve_gate_config_path  # noqa: E402
from orb.v2.robots import init_robot_wallets  # noqa: E402
from tools.orb.v2.backtest_universe import filter_backtest_sessions_with_atr, universe_session_dates  # noqa: E402
from tools.orb.v2.sim_live_session import DEFAULT_FEE_BPS_PER_SIDE, simulate_live_sessions, trade_fee_usdt  # noqa: E402

SYMBOL = "COINUSDT"
FROM_DATE = "2026-02-09"
TO_DATE = "2026-06-24"
ROBOT_EQUITY = 1000.0
ENTRY_FILL = "preplace_stop"
OR_MIN = 10
RISK_PCT = 0.03
FEE_BPS = DEFAULT_FEE_BPS_PER_SIDE


def _coin_cfg(**overrides: Any) -> OrbConfig:
    os.environ.setdefault("ORB_MARKET", "us_equity")
    os.environ["ORB_OR_MINUTES"] = str(OR_MIN)
    os.environ["ORB_RISK_PCT"] = str(RISK_PCT)
    os.environ["ORB_TRADE_WINDOW_MINUTES"] = "0"
    os.environ["ORB_ONE_TRADE_PER_SESSION"] = "1"
    os.environ["ORB_EXIT_MODE"] = "eod"
    os.environ["ORB_SL_MODE"] = "atr_pct"
    os.environ["ORB_ATR_PERIOD"] = "14"
    os.environ["ORB_ATR_SL_FRACTION"] = "0.05"
    os.environ["ORB_SIGNAL_INTERVAL"] = "5m"
    os.environ["ORB_MACRO_FILTER"] = "0"
    os.environ["ORB_V2_ROBOT_RESET_CAP"] = "0"
    os.environ["ORB_V2_GATE_ML"] = "0"
    cfg = OrbConfig.from_env()
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def _summarize(days: List[Dict[str, Any]], trades: List[Dict[str, Any]], end_wallet: float, elapsed: float) -> Dict[str, Any]:
    wins = sum(1 for t in trades if float(t.get("pnl_usdt") or 0) > 0)
    big5 = sum(
        1
        for t in trades
        if float(t.get("pnl_usdt") or 0) > 0
        and float(t.get("pnl_usdt") or 0) / (float(t.get("wallet_before") or 1) * RISK_PCT) >= 5
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
        "max_win_usdt": round(gross_wins[0], 2) if gross_wins else 0.0,
        "top3_wins_usdt": [round(x, 2) for x in gross_wins[:3]],
        "elapsed_sec": round(elapsed, 1),
    }


def _run_dates(dates: List[str], cfg: OrbConfig, *, tag: str, label: str) -> Dict[str, Any]:
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
        fee_bps_per_side=FEE_BPS,
        entry_fill=ENTRY_FILL,
        ml_enabled=False,
        atr_daily_source="binance_1d",
    )
    trades = [t for d in days for t in (d.get("trades") or [])]
    return {
        "tag": tag,
        "label": label,
        "trade_dates": [str(t.get("session_date")) for t in trades],
        "trades": trades,
        **_summarize(days, trades, float(wallets[0]), time.time() - t0),
    }


def _session_range_features(df5: pd.DataFrame, day: str, cfg: OrbConfig) -> Dict[str, Any]:
    ts = pd.Timestamp(f"{day} 12:00:00", tz=cfg.session_tz)
    anchor = session_anchor_ms(int(ts.value // 1_000_000), tz=cfg.session_tz, session_open_time=cfg.session_open_time)
    or_end = anchor + OR_MIN * 60_000
    first30_end = anchor + 30 * 60_000

    sess = session_slice(df5, first30_end, tz=cfg.session_tz, session_open_time=cfg.session_open_time)
    if sess.empty:
        return {}
    open_p = float(sess.iloc[0]["open"])

    or_bars = df5[(df5["open_time"] >= anchor) & (df5["open_time"] < or_end)]
    first30 = df5[(df5["open_time"] >= anchor) & (df5["open_time"] <= first30_end)]

    or_range = (or_bars["high"].max() - or_bars["low"].min()) / open_p * 100 if not or_bars.empty else np.nan
    first30_range = (first30["high"].max() - first30["low"].min()) / open_p * 100 if not first30.empty else np.nan

    pack = compute_opening_range(
        sess,
        or_minutes=OR_MIN,
        bar_step_ms=300_000,
        asof_open_ms=or_end,
        tz=cfg.session_tz,
        session_open_time=cfg.session_open_time,
    )
    or_w = float(pack["or_width_pct"]) if pack else np.nan

    return {
        "session_date": day,
        "or10_range_pct": round(float(or_range), 4) if not np.isnan(or_range) else None,
        "first30m_range_pct": round(float(first30_range), 4) if not np.isnan(first30_range) else None,
        "or_width_pct": round(or_w, 4) if not np.isnan(or_w) else None,
    }


def _prev_atr_rank(daily_rth: pd.DataFrame, day: str, cfg: OrbConfig) -> Optional[float]:
    ts = pd.Timestamp(f"{day} 12:00:00", tz=cfg.session_tz)
    anchor = session_anchor_ms(int(ts.value // 1_000_000), tz=cfg.session_tz, session_open_time=cfg.session_open_time)
    d = daily_rth.drop_duplicates("open_time").sort_values("open_time").copy()
    d["atr"] = compute_atr_series(d, period=14)
    d["atr_pct"] = d["atr"] / d["close"] * 100
    ddf = _daily_df_asof(d, anchor)
    if ddf.empty:
        return None
    prev_close = float(ddf["close"].iloc[-1])
    atr = daily_atr_asof(d, anchor, period=14, tz=cfg.session_tz)
    if not atr or not prev_close:
        return None
    atr_pct = float(atr) / prev_close * 100
    hist = d[d["open_time"] < anchor].tail(20)
    if len(hist) < 5:
        return None
    return float((hist["atr_pct"] < atr_pct).mean() * 100)


def _trade_pnl_at_px(side: str, entry: float, exit_px: float, notional: float) -> Tuple[float, float]:
    side_u = side.upper()
    gross = (exit_px - entry) / entry * notional if side_u == "LONG" else (entry - exit_px) / entry * notional
    fee = trade_fee_usdt(notional, fee_bps_per_side=FEE_BPS)
    return round(gross, 4), round(gross - fee, 4)


def _resolve_original_pnl(
    t: Dict[str, Any], df5: pd.DataFrame, side: str, entry: float, notional: float
) -> Tuple[str, float]:
    orig_outcome = str(t.get("outcome") or "")
    exit_ms = int(t.get("exit_bar_open_ms") or t.get("exit_ms") or 0)
    sl = float(t.get("sl") or t.get("sl_price") or 0)
    if orig_outcome in ("loss", "sl", "stop") and sl > 0:
        exit_px = sl
    else:
        exit_path = df5[df5["open_time"] == exit_ms]
        exit_px = float(exit_path.iloc[0]["close"]) if not exit_path.empty else entry
    _, pnl = _trade_pnl_at_px(side, entry, exit_px, notional)
    return orig_outcome, pnl


def _underwater_exit_sim(trades: List[Dict[str, Any]], df5: pd.DataFrame, *, minutes: int, mode: str) -> Dict[str, Any]:
    """Post-hoc: baseline trades 上模拟 T+N 分钟退出规则（顺序复利 wallet）。"""
    sorted_trades = sorted(trades, key=lambda t: str(t.get("session_date") or ""))
    wallet = ROBOT_EQUITY
    out_trades: List[Dict[str, Any]] = []
    early_exits = 0
    saved_from_sl = 0
    cut_winners = 0

    for t in sorted_trades:
        d = str(t["session_date"])
        side = str(t["side"])
        entry = float(t["entry"])
        fill_ms = int(t.get("fill_bar_open_ms") or t.get("scan_open_ms") or 0)
        orig_pnl = float(t.get("pnl_usdt") or 0)
        orig_outcome = str(t.get("outcome") or "")

        # 按当前 wallet 重算 notional（与 live 一致：risk_pct * wallet / sl_dist）
        sl = float(t.get("sl") or t.get("sl_price") or 0)
        if sl <= 0 or entry <= 0:
            notional = float(t.get("notional_usdt") or 0)
        else:
            risk_usd = wallet * RISK_PCT
            sl_dist = abs(entry - sl) / entry
            notional = risk_usd / sl_dist if sl_dist > 0 else float(t.get("notional_usdt") or 0)

        deadline = fill_ms + minutes * 60_000
        path = df5[(df5["open_time"] >= fill_ms) & (df5["open_time"] <= deadline)].sort_values("open_time")
        if path.empty:
            pnl = orig_pnl
            outcome = orig_outcome
            note = "no_bars"
        else:
            row = path.iloc[-1]
            close_px = float(row["close"])
            hi = path["high"].astype(float)
            lo = path["low"].astype(float)
            side_u = side.upper()

            if mode == "never_favor":
                continued = (hi.max() > entry) if side_u == "LONG" else (lo.min() < entry)
                if not continued:
                    _, pnl = _trade_pnl_at_px(side, entry, close_px, notional)
                    outcome = "early_exit"
                    early_exits += 1
                    note = "never_favor"
                    if orig_pnl < 0:
                        saved_from_sl += 1
                else:
                    outcome, pnl = _resolve_original_pnl(t, df5, side, entry, notional)
                    note = "hold_original"
            else:  # underwater
                favor_ret = (close_px - entry) / entry * 100 if side_u == "LONG" else (entry - close_px) / entry * 100
                if favor_ret < 0:
                    _, pnl = _trade_pnl_at_px(side, entry, close_px, notional)
                    outcome = "early_exit_underwater"
                    early_exits += 1
                    note = "underwater"
                    if orig_pnl < 0:
                        saved_from_sl += 1
                    elif orig_pnl > 0:
                        cut_winners += 1
                else:
                    outcome, pnl = _resolve_original_pnl(t, df5, side, entry, notional)
                    note = "hold_original"

        wallet_before = wallet
        wallet += pnl
        out_trades.append(
            {
                "session_date": d,
                "side": side,
                "outcome": outcome,
                "pnl_usdt": round(pnl, 2),
                "wallet_before": round(wallet_before, 2),
                "note": note,
                "orig_pnl_usdt": orig_pnl,
                "orig_outcome": orig_outcome,
            }
        )

    wins = sum(1 for t in out_trades if float(t["pnl_usdt"]) > 0)
    big5 = sum(
        1
        for t in out_trades
        if float(t["pnl_usdt"]) > 0 and float(t["pnl_usdt"]) / (float(t["wallet_before"]) * RISK_PCT) >= 5
    )
    return {
        "tag": f"posthoc_{mode}_{minutes}m",
        "label": f"post-hoc {mode} @ {minutes}m（baseline trades 顺序复利）",
        "opens": len(out_trades),
        "win_trades": wins,
        "win_rate": round(wins / len(out_trades) * 100, 1) if out_trades else 0.0,
        "big_wins_5r": big5,
        "net_pnl_usdt": round(wallet - ROBOT_EQUITY, 2),
        "return_pct": round((wallet / ROBOT_EQUITY - 1) * 100, 1),
        "end_wallet_usdt": round(wallet, 2),
        "early_exits": early_exits,
        "saved_from_sl": saved_from_sl,
        "cut_winners": cut_winners,
        "trade_dates": [t["session_date"] for t in out_trades],
    }


def main() -> int:
    load_env_oi()
    cfg = _coin_cfg()

    raw = [d for d in universe_session_dates([SYMBOL], cfg) if FROM_DATE <= d <= TO_DATE]
    all_dates = filter_backtest_sessions_with_atr(raw, [SYMBOL], cfg, atr_daily_source="binance_1d")

    df5 = load_klines(SYMBOL, "5m")
    daily_rth = aggregate_rth_daily_bars(df5, cfg)

    feat_rows = [_session_range_features(df5, d, cfg) for d in all_dates]
    feat = pd.DataFrame([r for r in feat_rows if r])
    med30 = float(feat["first30m_range_pct"].median()) if not feat.empty else 3.5
    med_or10 = float(feat["or10_range_pct"].median()) if not feat.empty else 2.4
    fake_avg30 = 3.44

    for d in all_dates:
        rank = _prev_atr_rank(daily_rth, d, cfg)
        idx = feat.index[feat["session_date"] == d]
        if len(idx):
            feat.loc[idx[0], "prev_atr_rank20"] = rank

    feat_by_day = {str(r["session_date"]): r for _, r in feat.iterrows()}

    date_filters: List[tuple[str, str, Callable[[pd.Series], bool]]] = [
        ("baseline", "无 filter", lambda r: True),
        (
            "first30_ge_median",
            f"开盘30min振幅 ≥ 中位 ({med30:.2f}%)",
            lambda r: float(r["first30m_range_pct"]) >= med30,
        ),
        (
            "first30_ge_3.44",
            "开盘30min振幅 ≥ 3.44%（假突破均值）",
            lambda r: float(r["first30m_range_pct"]) >= fake_avg30,
        ),
        (
            "or10_ge_median",
            f"OR10 区间振幅 ≥ 中位 ({med_or10:.2f}%)",
            lambda r: float(r["or10_range_pct"]) >= med_or10,
        ),
        (
            "prev_rank50_first30_med",
            "前日ATR分位≤50 且 30min振幅≥中位",
            lambda r: float(r.get("prev_atr_rank20") or 999) <= 50 and float(r["first30m_range_pct"]) >= med30,
        ),
    ]

    results: List[Dict[str, Any]] = []
    baseline_trades: List[Dict[str, Any]] = []

    print(f"COIN OR10 breakout filters | {FROM_DATE}..{TO_DATE}\n", flush=True)
    for tag, label, pred in date_filters:
        dates = [d for d in all_dates if d in feat_by_day and pred(feat_by_day[d])]
        print(f"[{tag}] {label} | sessions={len(dates)} ...", flush=True)
        row = _run_dates(dates, cfg, tag=tag, label=label)
        row["filtered_sessions"] = len(dates)
        if tag == "baseline":
            baseline_trades = list(row.get("trades") or [])
        row.pop("trades", None)
        results.append(row)
        print(
            f"  opens={row['opens']} net={row['net_pnl_usdt']:+.1f}U big5R={row['big_wins_5r']} "
            f"maxWin={row['max_win_usdt']}U ({row['elapsed_sec']}s)\n",
            flush=True,
        )

    # Full sim early_exit (engine native: never moved in favor)
    for mins in (30, 60):
        tag = f"early_exit_{mins}m"
        label = f"early_exit={mins}m（N分钟内未朝有利方向则平仓）"
        print(f"[{tag}] {label} | sessions={len(all_dates)} ...", flush=True)
        row = _run_dates(all_dates, _coin_cfg(early_exit_minutes=mins), tag=tag, label=label)
        row.pop("trades", None)
        results.append(row)
        print(
            f"  opens={row['opens']} net={row['net_pnl_usdt']:+.1f}U big5R={row['big_wins_5r']} "
            f"maxWin={row['max_win_usdt']}U\n",
            flush=True,
        )

    # Post-hoc underwater / never_favor on baseline trades
    if baseline_trades:
        for mode, mins in (("underwater", 30), ("underwater", 60), ("never_favor", 30)):
            row = _underwater_exit_sim(baseline_trades, df5, minutes=mins, mode=mode)
            results.append(row)
            print(
                f"[{row['tag']}] net={row['net_pnl_usdt']:+.1f}U big5R={row['big_wins_5r']} "
                f"early_exits={row.get('early_exits')} cut_winners={row.get('cut_winners')} "
                f"saved_from_sl={row.get('saved_from_sl')}\n",
                flush=True,
            )

    out = ROOT / "output" / "orb" / "v2" / "eval" / "coin_or10_breakout_filters.json"
    payload = {
        "symbol": SYMBOL,
        "from_date": FROM_DATE,
        "to_date": TO_DATE,
        "strategy": "OR10_3pct_eod_preplace_stop",
        "thresholds": {
            "median_first30m_range_pct": round(med30, 4),
            "median_or10_range_pct": round(med_or10, 4),
            "fake_avg_first30m_pct": fake_avg30,
        },
        "session_features": feat.to_dict(orient="records"),
        "variants": [{k: v for k, v in r.items() if k != "trades"} for r in results],
        "notes": [
            "first30m_range 用 9:30-10:00 全段；9:40 入场时对 10:00 前 20min 有轻微 lookahead",
            "post-hoc underwater = 入场后 N 分钟仍亏损则市价平，否则走原 exit",
            "engine early_exit = N 分钟内 high/low 从未越过 entry 则平仓",
        ],
    }
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {out}")

    best = max(results, key=lambda x: float(x.get("net_pnl_usdt") or 0))
    print(f"\nBest net PnL: [{best['tag']}] {best['net_pnl_usdt']:+.1f}U (baseline ref={results[0]['net_pnl_usdt']:+.1f}U)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
