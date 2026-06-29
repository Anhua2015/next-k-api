#!/usr/bin/env python3
"""导出 CRCL 推荐方案（OR5 3% tw0）交易明细 CSV。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from env_loader import load_env_oi  # noqa: E402

load_env_oi()
os.environ["ORB_V2_ROBOT_RESET_CAP"] = "0"
os.environ["ORB_V2_GATE_ML"] = "0"
os.environ["ORB_MACRO_FILTER"] = "0"

import pandas as pd  # noqa: E402

from orb.core.kline_cache import load_klines  # noqa: E402
from orb.ml.gate import LiveGateConfig, gate_with_ml_bypass  # noqa: E402
from orb.v2.paths import resolve_gate_config_path  # noqa: E402
from tools.orb.ml.eval_live_gate import _ml_cfg  # noqa: E402
from tools.orb.v2.backtest_universe import filter_backtest_sessions_with_atr, universe_session_dates  # noqa: E402
from tools.orb.v2.explore_symbol_profile import _enrich_trades, _run_bt  # noqa: E402

SYM = "CRCLUSDT"
LO, HI = "2026-02-09", "2026-06-24"
OUT_DIR = ROOT / "output" / "orb" / "v2" / "eval"


def main() -> int:
    gate = gate_with_ml_bypass(LiveGateConfig.from_json(Path(resolve_gate_config_path())))
    cfg = _ml_cfg(compound_per_symbol=True, respect_env_filters=False)
    cfg.macro_filter = False

    raw_dates = [d for d in universe_session_dates([SYM], cfg) if LO <= d <= HI]
    dates = filter_backtest_sessions_with_atr(raw_dates, [SYM], cfg)

    bt = _run_bt(
        SYM,
        dates,
        or_minutes=5,
        risk_pct=0.03,
        trade_window_minutes=0,
    )
    trades = bt["trades"]

    fetch_start = int(
        pd.Timestamp(dates[0] + " 09:30:00", tz=cfg.session_tz).value // 1_000_000
    ) - cfg.daily_atr_warmup_ms()
    fetch_end = int(pd.Timestamp(dates[-1] + " 16:00:00", tz=cfg.session_tz).value // 1_000_000)
    df5 = load_klines(SYM, "5m", start_ms=fetch_start, end_ms=fetch_end)
    detail = _enrich_trades(trades, df5, cfg, or_minutes=5)

    raw_path = OUT_DIR / "crcl_or5_3pct_tw0.trades.csv"
    detail_path = OUT_DIR / "crcl_or5_3pct_tw0_detail.csv"
    pd.DataFrame(trades).to_csv(raw_path, index=False, encoding="utf-8-sig")
    detail.to_csv(detail_path, index=False, encoding="utf-8-sig")

    wins = int(detail["win"].sum()) if "win" in detail.columns else 0
    print(
        f"CRCL OR5 3% tw0 | {len(dates)} sessions | {len(trades)} trades | "
        f"win={wins} loss={len(trades)-wins} | net={bt['wallet_net_usdt']:+.2f}U | "
        f"end={bt['end_wallet_usdt']:.2f}U"
    )
    print(f"raw  -> {raw_path}")
    print(f"detail -> {detail_path}")

    if len(detail):
        d = detail.copy()
        d["month"] = d["session_date"].astype(str).str[:7]
        m = d.groupby("month").agg(trades=("pnl_usdt", "count"), net=("pnl_usdt", "sum"), wins=("win", "sum"))
        print("\n--- monthly ---")
        print(m.to_string())
        cols = [c for c in ("session_date", "side", "entry", "pnl_usdt", "outcome", "r_multiple") if c in detail.columns]
        big = detail[detail["pnl_usdt"] > 0].nlargest(8, "pnl_usdt")[cols]
        print("\n--- top 8 wins ---")
        print(big.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
