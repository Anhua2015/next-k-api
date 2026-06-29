#!/usr/bin/env python3
"""COIN：9/20 EMA 独立策略 + ORB 方向过滤探索。

用法:
  python tools/orb/v2/explore_coin_ema.py
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

logging.getLogger("orb").setLevel(logging.ERROR)

from env_loader import load_env_oi  # noqa: E402

load_env_oi()
os.environ["ORB_V2_ROBOT_RESET_CAP"] = "0"
os.environ["ORB_V2_GATE_ML"] = "0"

from orb.core.config import OrbConfig  # noqa: E402
from orb.core.ema import (  # noqa: E402
    aggregate_ohlcv,
    bar_touches_ema_zone,
    ema_at_bar_index,
    ema_trend_allows,
    ema_values_asof,
)
from orb.core.kline_cache import load_klines  # noqa: E402
from orb.core.session import session_anchor_ms, session_close_ms  # noqa: E402
from orb.core.signals import compute_position_notional  # noqa: E402
from orb.ml.gate import LiveGateConfig, gate_with_ml_bypass  # noqa: E402
from orb.v2.paths import resolve_gate_config_path  # noqa: E402
from orb.v2.robots import init_robot_wallets  # noqa: E402
from tools.orb.ml.eval_live_gate import _ml_cfg  # noqa: E402
from tools.orb.v2.backtest_universe import filter_backtest_sessions_with_atr, universe_session_dates  # noqa: E402
from tools.orb.v2.sim_live_session import simulate_live_sessions, trade_fee_usdt  # noqa: E402

SYM = "COINUSDT"
LO, HI = "2026-02-09", "2026-06-24"
EQ, FEE = 1000.0, 4.0
BAR_15M = 900_000
BAR_5M = 300_000


def _dates(cfg: OrbConfig) -> List[str]:
    raw = [d for d in universe_session_dates([SYM], cfg) if LO <= d <= HI]
    return filter_backtest_sessions_with_atr(raw, [SYM], cfg)


def _run_orb(
    dates: List[str],
    cfg: OrbConfig,
    *,
    risk_pct: float,
    ema_filter: bool = False,
    ema_bar_ms: int = BAR_15M,
    return_trades: bool = False,
) -> Dict[str, Any]:
    gate = gate_with_ml_bypass(LiveGateConfig.from_json(Path(resolve_gate_config_path())))
    c = _ml_cfg(compound_per_symbol=True, respect_env_filters=False)
    c.or_minutes = cfg.or_minutes
    c.risk_pct = float(risk_pct)
    c.trade_window_minutes = cfg.trade_window_minutes
    c.macro_filter = False
    c.exit_mode = "eod"
    c.sl_mode = "atr_pct"
    wallets = init_robot_wallets(count=1, equity_usdt=EQ)
    days = simulate_live_sessions(
        dates,
        [SYM],
        gate=gate,
        ranker=None,
        cfg=c,
        robot_wallets=wallets,
        respect_env_filters=False,
        fee_bps_per_side=FEE,
        entry_fill="preplace_stop",
        ml_enabled=False,
        ema_trend_filter=ema_filter,
        ema_bar_ms=ema_bar_ms,
    )
    trades = [t for d in days for t in (d.get("trades") or [])]
    wins = sum(1 for t in trades if float(t.get("pnl_usdt") or 0) > 0)
    out: Dict[str, Any] = {
        "opens": len(trades),
        "wins": wins,
        "win_rate_pct": round(wins / len(trades) * 100, 1) if trades else 0.0,
        "wallet_net_usdt": round(float(wallets[0]) - EQ, 2),
        "ema_filter": ema_filter,
        "ema_bar_ms": ema_bar_ms,
    }
    if return_trades:
        out["trades"] = trades
    return out


def _posthoc_orb_ema(trades: List[Dict[str, Any]], df_ema: pd.DataFrame, *, bar_ms: int) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    for t in trades:
        fill_ms = int(t.get("fill_bar_open_ms") or t.get("scan_open_ms") or 0)
        side = str(t.get("side") or "")
        emas = ema_values_asof(df_ema, fill_ms - bar_ms)
        if emas is None:
            continue
        aligned = ema_trend_allows(side, emas[0], emas[1])
        rows.append(
            {
                "aligned": aligned,
                "win": float(t.get("pnl_usdt") or 0) > 0,
                "pnl_usdt": float(t.get("pnl_usdt") or 0),
            }
        )
    if not rows:
        return {"n": 0}
    aligned_rows = [r for r in rows if r["aligned"]]
    counter = [r for r in rows if not r["aligned"]]
    return {
        "n": len(rows),
        "all_win_pct": round(sum(1 for r in rows if r["win"]) / len(rows) * 100, 1),
        "aligned_n": len(aligned_rows),
        "aligned_win_pct": round(sum(1 for r in aligned_rows if r["win"]) / len(aligned_rows) * 100, 1)
        if aligned_rows
        else 0.0,
        "aligned_pnl_sum": round(sum(r["pnl_usdt"] for r in aligned_rows), 2),
        "counter_n": len(counter),
        "counter_win_pct": round(sum(1 for r in counter if r["win"]) / len(counter) * 100, 1) if counter else 0.0,
        "counter_pnl_sum": round(sum(r["pnl_usdt"] for r in counter), 2),
    }


def _simulate_ema_pullback_session(
    session_date: str,
    df15: pd.DataFrame,
    cfg: OrbConfig,
    *,
    wallet: float,
    risk_pct: float,
) -> Optional[Dict[str, Any]]:
    tz = cfg.session_tz
    ts = pd.Timestamp(f"{session_date} 12:00:00", tz=tz)
    anchor = session_anchor_ms(int(ts.value // 1_000_000), tz=tz, session_open_time=cfg.session_open_time)
    close_ms = session_close_ms(anchor, tz=tz, session_close_time=cfg.session_close_time)
    if close_ms is None:
        return None
    sess = df15[(df15["open_time"] >= anchor) & (df15["open_time"] < close_ms)].reset_index(drop=True)
    if len(sess) < 8:
        return None
    hist = df15[df15["open_time"] < anchor].reset_index(drop=True)
    combined = pd.concat([hist, sess], ignore_index=True)
    h0 = len(hist)

    pos: Optional[Dict[str, Any]] = None
    session_high = -1e18
    session_low = 1e18

    for j in range(len(sess)):
        gi = h0 + j
        bar = sess.iloc[j]
        h, l, c = float(bar["high"]), float(bar["low"]), float(bar["close"])
        ot = int(bar["open_time"])
        session_high = max(session_high, h)
        session_low = min(session_low, l)

        emas = ema_at_bar_index(combined, gi)
        if emas is None:
            continue
        e9, e20 = emas

        if pos is None:
            if e9 > e20 and bar_touches_ema_zone(high=h, low=l, ema9=e9, ema20=e20):
                entry, sl = c, e20
                if entry - sl <= 0:
                    continue
                notional = compute_position_notional(
                    entry=entry, sl=sl, cfg=cfg, bot_equity_usdt=wallet, risk_pct_override=risk_pct
                )
                if notional <= 0:
                    continue
                pos = {
                    "side": "LONG",
                    "entry": entry,
                    "notional": notional,
                    "hod_at_entry": session_high,
                    "lod_at_entry": session_low,
                }
            elif e9 < e20 and bar_touches_ema_zone(high=h, low=l, ema9=e9, ema20=e20):
                entry, sl = c, e20
                if sl - entry <= 0:
                    continue
                notional = compute_position_notional(
                    entry=entry, sl=sl, cfg=cfg, bot_equity_usdt=wallet, risk_pct_override=risk_pct
                )
                if notional <= 0:
                    continue
                pos = {
                    "side": "SHORT",
                    "entry": entry,
                    "notional": notional,
                    "hod_at_entry": session_high,
                    "lod_at_entry": session_low,
                }
            continue

        side = pos["side"]
        entry = float(pos["entry"])
        notional = float(pos["notional"])
        exit_px: Optional[float] = None
        outcome = ""

        if side == "LONG":
            if c < e20:
                exit_px, outcome = c, "ema20_stop"
            elif h > float(pos["hod_at_entry"]) and h >= session_high:
                exit_px, outcome = c, "pivot_high"
        else:
            if c > e20:
                exit_px, outcome = c, "ema20_stop"
            elif l < float(pos["lod_at_entry"]) and l <= session_low:
                exit_px, outcome = c, "pivot_low"

        if j == len(sess) - 1 and exit_px is None:
            exit_px, outcome = c, "session_close"

        if exit_px is None:
            continue

        if side == "LONG":
            gross = notional * (exit_px - entry) / entry
        else:
            gross = notional * (entry - exit_px) / entry
        fee = trade_fee_usdt(notional, fee_bps_per_side=FEE)
        net = round(gross - fee, 2)
        return {
            "session_date": session_date,
            "side": side,
            "entry": entry,
            "exit": exit_px,
            "outcome": outcome,
            "pnl_usdt": net,
            "notional_usdt": notional,
        }
    return None


def _run_ema_standalone(dates: List[str], cfg: OrbConfig, *, risk_pct: float) -> Dict[str, Any]:
    fetch_start = int(pd.Timestamp(dates[0] + " 09:30:00", tz=cfg.session_tz).value // 1_000_000) - BAR_15M * 200
    fetch_end = int(pd.Timestamp(dates[-1] + " 16:00:00", tz=cfg.session_tz).value // 1_000_000)
    df5 = load_klines(SYM, "5m", start_ms=fetch_start, end_ms=fetch_end)
    df15 = aggregate_ohlcv(df5, BAR_15M)

    wallet = EQ
    trades: List[Dict[str, Any]] = []
    for d in dates:
        row = _simulate_ema_pullback_session(d, df15, cfg, wallet=wallet, risk_pct=risk_pct)
        if row:
            wallet = round(wallet + float(row["pnl_usdt"]), 2)
            trades.append(row)
    wins = sum(1 for t in trades if float(t["pnl_usdt"]) > 0)
    return {
        "opens": len(trades),
        "wins": wins,
        "win_rate_pct": round(wins / len(trades) * 100, 1) if trades else 0.0,
        "wallet_net_usdt": round(wallet - EQ, 2),
        "trades": trades,
    }


def main() -> int:
    t0 = time.time()
    cfg = _ml_cfg(compound_per_symbol=True, respect_env_filters=False)
    cfg.or_minutes = 10
    cfg.trade_window_minutes = 0
    cfg.macro_filter = False
    dates = _dates(cfg)

    report: Dict[str, Any] = {
        "symbol": SYM,
        "date_range": {"from": LO, "to": HI, "sessions": len(dates)},
        "orb_or10": {},
        "posthoc_ema_split": {},
        "ema_standalone_15m": {},
    }

    print(f"COIN EMA explore | {len(dates)} sessions\n", flush=True)

    for risk in (0.01, 0.03):
        label = f"risk_{int(risk * 100)}pct"
        base = _run_orb(dates, cfg, risk_pct=risk, ema_filter=False)
        f15 = _run_orb(dates, cfg, risk_pct=risk, ema_filter=True, ema_bar_ms=BAR_15M)
        f5 = _run_orb(dates, cfg, risk_pct=risk, ema_filter=True, ema_bar_ms=BAR_5M)
        report["orb_or10"][label] = {"baseline": base, "ema_filter_15m": f15, "ema_filter_5m": f5}
        print(
            f"ORB OR10 {label}: base opens={base['opens']} WR={base['win_rate_pct']}% net={base['wallet_net_usdt']:+.0f}U | "
            f"EMA15 opens={f15['opens']} WR={f15['win_rate_pct']}% net={f15['wallet_net_usdt']:+.0f}U | "
            f"EMA5 opens={f5['opens']} WR={f5['win_rate_pct']}% net={f5['wallet_net_usdt']:+.0f}U",
            flush=True,
        )

    base1 = _run_orb(dates, cfg, risk_pct=0.01, ema_filter=False, return_trades=True)
    trades_list = base1.pop("trades", [])
    fetch_start = int(pd.Timestamp(dates[0] + " 09:30:00", tz=cfg.session_tz).value // 1_000_000) - BAR_15M * 200
    fetch_end = int(pd.Timestamp(dates[-1] + " 16:00:00", tz=cfg.session_tz).value // 1_000_000)
    df5 = load_klines(SYM, "5m", start_ms=fetch_start, end_ms=fetch_end)
    report["posthoc_ema_split"]["15m"] = _posthoc_orb_ema(trades_list, aggregate_ohlcv(df5, BAR_15M), bar_ms=BAR_15M)
    report["posthoc_ema_split"]["5m"] = _posthoc_orb_ema(trades_list, aggregate_ohlcv(df5, BAR_5M), bar_ms=BAR_5M)
    ph = report["posthoc_ema_split"]["15m"]
    print(
        f"\nPost-hoc ORB@1%: all WR={ph.get('all_win_pct')}% | "
        f"EMA-aligned {ph.get('aligned_n')} WR={ph.get('aligned_win_pct')}% | "
        f"counter {ph.get('counter_n')} WR={ph.get('counter_win_pct')}%",
        flush=True,
    )

    ema1 = _run_ema_standalone(dates, cfg, risk_pct=0.01)
    ema3 = _run_ema_standalone(dates, cfg, risk_pct=0.03)
    report["ema_standalone_15m"]["risk_1pct"] = {k: ema1[k] for k in ("opens", "wins", "win_rate_pct", "wallet_net_usdt")}
    report["ema_standalone_15m"]["risk_3pct"] = {k: ema3[k] for k in ("opens", "wins", "win_rate_pct", "wallet_net_usdt")}
    print(
        f"\nEMA pullback 15m @1%: opens={ema1['opens']} WR={ema1['win_rate_pct']}% net={ema1['wallet_net_usdt']:+.0f}U",
        flush=True,
    )
    print(
        f"EMA pullback 15m @3%: opens={ema3['opens']} WR={ema3['win_rate_pct']}% net={ema3['wallet_net_usdt']:+.0f}U",
        flush=True,
    )

    out = ROOT / "output" / "orb/v2/eval/coin_ema_explore.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    pd.DataFrame(ema1.get("trades") or []).to_csv(
        ROOT / "output/orb/v2/eval/coin_ema_pullback_trades.csv", index=False, encoding="utf-8-sig"
    )
    report["elapsed_sec"] = round(time.time() - t0, 1)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nwrote {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
