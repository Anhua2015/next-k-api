#!/usr/bin/env python3
"""COIN OR10：开盘第一根 5m 阴阳定方向 filter 回测。"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from env_loader import load_env_oi  # noqa: E402
from orb.core.config import OrbConfig  # noqa: E402
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
RISK_PCT = 0.03


def _first_bar_5m_direction(df5, anchor_ms: int, *, bar_ms: int = 300_000):
    sub = df5[df5["open_time"] == int(anchor_ms)]
    if sub.empty:
        sub = df5[(df5["open_time"] >= int(anchor_ms)) & (df5["open_time"] < int(anchor_ms) + int(bar_ms))]
    if sub.empty:
        return None
    o, c = float(sub.iloc[0]["open"]), float(sub.iloc[0]["close"])
    if c > o:
        return "LONG"
    if c < o:
        return "SHORT"
    return None


def _coin_cfg() -> OrbConfig:
    os.environ.setdefault("ORB_MARKET", "us_equity")
    os.environ["ORB_OR_MINUTES"] = "10"
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
    return OrbConfig.from_env()


def _summarize(trades: List[Dict[str, Any]], end_wallet: float, elapsed: float) -> Dict[str, Any]:
    wins = sum(1 for t in trades if float(t.get("pnl_usdt") or 0) > 0)
    big5 = sum(
        1
        for t in trades
        if float(t.get("pnl_usdt") or 0) > 0
        and float(t.get("pnl_usdt") or 0) / (float(t.get("wallet_before") or 1) * RISK_PCT) >= 5
    )
    gross_wins = sorted([float(t["pnl_usdt"]) for t in trades if float(t.get("pnl_usdt") or 0) > 0], reverse=True)
    return {
        "opens": len(trades),
        "win_trades": wins,
        "win_rate": round(wins / len(trades) * 100, 1) if trades else 0.0,
        "big_wins_5r": big5,
        "net_pnl_usdt": round(end_wallet - ROBOT_EQUITY, 2),
        "return_pct": round((end_wallet / ROBOT_EQUITY - 1) * 100, 1),
        "end_wallet_usdt": round(end_wallet, 2),
        "max_win_usdt": round(gross_wins[0], 2) if gross_wins else 0.0,
        "elapsed_sec": round(elapsed, 1),
    }


def _run(dates: List[str], cfg: OrbConfig, *, first_bar: bool) -> Dict[str, Any]:
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
        entry_fill="preplace_stop",
        ml_enabled=False,
        atr_daily_source="binance_1d",
        first_bar_direction_filter=first_bar,
    )
    trades = [t for d in days for t in (d.get("trades") or [])]
    return {"trades": trades, **_summarize(trades, float(wallets[0]), time.time() - t0)}


def _posthoc(trades_csv: Path, df5: pd.DataFrame, cfg: OrbConfig) -> Dict[str, Any]:
    trades = pd.read_csv(trades_csv)
    trades["pnl_r"] = trades["pnl_usdt"] / (trades["wallet_before"] * RISK_PCT)
    rows = []
    for _, t in trades.iterrows():
        d = str(t["session_date"])
        ts = pd.Timestamp(d + " 12:00:00", tz=cfg.session_tz)
        anchor = session_anchor_ms(int(ts.value // 1_000_000), tz=cfg.session_tz, session_open_time=cfg.session_open_time)
        bias = _first_bar_5m_direction(df5, anchor)
        side = str(t["side"]).upper()
        aligned = bias is not None and side == bias
        rows.append(
            {
                "session_date": d,
                "side": side,
                "bias": bias,
                "aligned": aligned,
                "win": float(t["pnl_usdt"]) > 0,
                "big5r": float(t["pnl_r"]) >= 5,
                "pnl_usdt": float(t["pnl_usdt"]),
            }
        )
    df = pd.DataFrame(rows)
    big_dates = set(df.loc[df["big5r"], "session_date"])

    def stat(sub: pd.DataFrame) -> Dict[str, Any]:
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
    doji = df[df["bias"].isna()]
    missed_big = sorted(big_dates - set(aligned["session_date"]))
    return {
        "all": stat(df),
        "aligned_with_first_bar": stat(aligned),
        "counter_to_first_bar": stat(counter),
        "doji_days_traded": stat(doji),
        "big5r_kept_posthoc": f"{len(big_dates & set(aligned['session_date']))}/{len(big_dates)}",
        "big5r_missed_if_filter": missed_big,
        "rows": df.to_dict(orient="records"),
    }


def main() -> int:
    load_env_oi()
    cfg = _coin_cfg()
    raw = [d for d in universe_session_dates([SYMBOL], cfg) if FROM_DATE <= d <= TO_DATE]
    dates = filter_backtest_sessions_with_atr(raw, [SYMBOL], cfg, atr_daily_source="binance_1d")

    print("COIN OR10 first 5m bar direction filter\n", flush=True)
    base = _run(dates, cfg, first_bar=False)
    filt = _run(dates, cfg, first_bar=True)
    base["tag"] = "baseline"
    filt["tag"] = "first_bar_direction"
    base_trades = base.pop("trades")
    filt_trades = filt.pop("trades")

    for row in (base, filt):
        print(
            f"  [{row['tag']}] opens={row['opens']} WR={row['win_rate']}% "
            f"big5R={row['big_wins_5r']} net={row['net_pnl_usdt']:+.1f}U maxWin={row['max_win_usdt']}U",
            flush=True,
        )

    trades_csv = ROOT / "output/orb/v2/eval/coin_or10_3pct_tw0.trades.csv"
    fetch_start = int(pd.Timestamp(FROM_DATE + " 09:30:00", tz=cfg.session_tz).value // 1_000_000) - 86400_000
    fetch_end = int(pd.Timestamp(TO_DATE + " 16:00:00", tz=cfg.session_tz).value // 1_000_000)
    df5 = load_klines(SYMBOL, "5m", start_ms=fetch_start, end_ms=fetch_end)
    posthoc = _posthoc(trades_csv, df5, cfg)

    big5_base = {
        str(t.get("session_date"))
        for t in base_trades
        if float(t.get("pnl_usdt") or 0) > 0
        and float(t.get("pnl_usdt") or 0) / (float(t.get("wallet_before") or 1) * RISK_PCT) >= 5
    }
    big5_filt = {
        str(t.get("session_date"))
        for t in filt_trades
        if float(t.get("pnl_usdt") or 0) > 0
        and float(t.get("pnl_usdt") or 0) / (float(t.get("wallet_before") or 1) * RISK_PCT) >= 5
    }
    missed_sim = sorted(big5_base - big5_filt)

    out = ROOT / "output/orb/v2/eval/coin_or10_first_bar_filter.json"
    payload = {
        "symbol": SYMBOL,
        "from_date": FROM_DATE,
        "to_date": TO_DATE,
        "rule": "第一根5m阳线只做多、阴线只做空；十字星当日不交易",
        "full_sim": {
            "baseline": {k: v for k, v in base.items()},
            "first_bar_filter": {k: v for k, v in filt.items()},
            "big5r_dates_baseline": sorted(big5_base),
            "big5r_dates_filtered": sorted(big5_filt),
            "big5r_missed_in_full_sim": missed_sim,
        },
        "posthoc_on_baseline_trades": posthoc,
    }
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\npost-hoc: aligned WR={posthoc['aligned_with_first_bar'].get('win_rate')}% "
          f"big5R={posthoc['aligned_with_first_bar'].get('big5r')} "
          f"net={posthoc['aligned_with_first_bar'].get('net_pnl'):+.0f}U")
    print(f"          counter WR={posthoc['counter_to_first_bar'].get('win_rate')}% "
          f"big5R={posthoc['counter_to_first_bar'].get('big5r')} "
          f"net={posthoc['counter_to_first_bar'].get('net_pnl'):+.0f}U")
    if missed_sim:
        print("full sim missed big5R days:", ", ".join(missed_sim))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
