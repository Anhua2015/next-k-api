#!/usr/bin/env python3
"""COIN 样本外验证：前半段选 OR，后半段固定参数；对比 Binance 1d vs RTH 5m ATR。

用法:
  python tools/orb/v2/explore_coin_oos.py
  python tools/orb/v2/explore_coin_oos.py --oos-from 2026-04-17
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

logging.getLogger("orb").setLevel(logging.ERROR)

from env_loader import load_env_oi  # noqa: E402

load_env_oi()
os.environ["ORB_V2_ROBOT_RESET_CAP"] = "0"

from orb.core.config import OrbConfig  # noqa: E402
from orb.ml.gate import LiveGateConfig, gate_with_ml_bypass  # noqa: E402
from orb.v2.paths import resolve_gate_config_path  # noqa: E402
from orb.v2.robots import init_robot_wallets  # noqa: E402
from tools.orb.ml.eval_live_gate import _ml_cfg  # noqa: E402
from tools.orb.v2.backtest_universe import filter_backtest_sessions_with_atr, universe_session_dates  # noqa: E402
from tools.orb.v2.sim_live_session import simulate_live_sessions  # noqa: E402

SYM = "COINUSDT"
LO, HI = "2026-02-09", "2026-06-24"
EQ, FEE = 1000.0, 4.0
README_CFG = {"or_minutes": 10, "risk_pct": 0.025, "trade_window_minutes": 90}


def _dates(cfg: OrbConfig, atr_src: str) -> List[str]:
    raw = [d for d in universe_session_dates([SYM], cfg) if LO <= d <= HI]
    return filter_backtest_sessions_with_atr(raw, [SYM], cfg, atr_daily_source=atr_src)


def _split(dates: List[str], oos_from: str) -> Tuple[List[str], List[str]]:
    is_dates = [d for d in dates if d < oos_from]
    oos_dates = [d for d in dates if d >= oos_from]
    return is_dates, oos_dates


def _run(
    dates: List[str],
    *,
    gate: LiveGateConfig,
    cfg: OrbConfig,
    atr_src: str,
) -> Dict[str, Any]:
    wallets = init_robot_wallets(count=1, equity_usdt=EQ)
    days = simulate_live_sessions(
        dates,
        [SYM],
        gate=gate,
        ranker=None,
        cfg=cfg,
        robot_wallets=wallets,
        respect_env_filters=False,
        fee_bps_per_side=FEE,
        entry_fill="preplace_stop",
        ml_enabled=False,
        atr_daily_source=atr_src,
    )
    trades = [t for day in days for t in (day.get("trades") or [])]
    wallet_net = round(float(wallets[0]) - EQ, 2)
    return {
        "sessions": len(dates),
        "opens": len(trades),
        "wallet_net_usdt": wallet_net,
        "end_wallet_usdt": round(float(wallets[0]), 2),
        "win_rate": round(sum(1 for t in trades if float(t.get("pnl_usdt") or 0) > 0) / len(trades) * 100, 1)
        if trades
        else 0.0,
    }


def _pick_or_is(is_dates: List[str], gate: LiveGateConfig, atr_src: str) -> Dict[str, Any]:
    best: Dict[str, Any] = {}
    for or_m in (5, 10, 15):
        cfg = _ml_cfg(compound_per_symbol=True, respect_env_filters=False)
        cfg.or_minutes = or_m
        cfg.risk_pct = 0.01
        cfg.trade_window_minutes = 0
        cfg.macro_filter = False
        cfg.exit_mode = "eod"
        cfg.sl_mode = "atr_pct"
        row = _run(is_dates, gate=gate, cfg=cfg, atr_src=atr_src)
        row["or_minutes"] = or_m
        if not best or row["wallet_net_usdt"] > best.get("wallet_net_usdt", -1e9):
            best = row
    return best


def main() -> int:
    ap = argparse.ArgumentParser(description="COIN OOS with optional RTH 5m ATR")
    ap.add_argument("--oos-from", default="2026-04-17", help="OOS 起始 session 日（含）")
    ap.add_argument("--out", default="output/orb/v2/eval/coin_oos.json")
    args = ap.parse_args()

    gate = gate_with_ml_bypass(LiveGateConfig.from_json(Path(resolve_gate_config_path())))
    probe = _ml_cfg(compound_per_symbol=True, respect_env_filters=False)
    t0 = time.time()

    report: Dict[str, Any] = {
        "symbol": SYM,
        "date_range": {"from": LO, "to": HI},
        "oos_from": args.oos_from,
        "readme_oos_config": README_CFG,
        "method": "preplace_stop, wallet_net, ROBOT_RESET_CAP=0, macro_filter=0",
    }

    for atr_src in ("binance_1d", "rth_5m"):
        dates = _dates(probe, atr_src)
        is_dates, oos_dates = _split(dates, args.oos_from)
        is_pick = _pick_or_is(is_dates, gate, atr_src)

        cfg_oos = _ml_cfg(compound_per_symbol=True, respect_env_filters=False)
        cfg_oos.or_minutes = README_CFG["or_minutes"]
        cfg_oos.risk_pct = README_CFG["risk_pct"]
        cfg_oos.trade_window_minutes = README_CFG["trade_window_minutes"]
        cfg_oos.macro_filter = False
        cfg_oos.exit_mode = "eod"
        cfg_oos.sl_mode = "atr_pct"

        is_full = _run(is_dates, gate=gate, cfg=cfg_oos, atr_src=atr_src)
        oos = _run(oos_dates, gate=gate, cfg=cfg_oos, atr_src=atr_src)
        full = _run(dates, gate=gate, cfg=cfg_oos, atr_src=atr_src)

        report[atr_src] = {
            "atr_sessions_total": len(dates),
            "is_sessions": len(is_dates),
            "oos_sessions": len(oos_dates),
            "is_or_pick_1pct": {
                "or_minutes": is_pick.get("or_minutes"),
                "wallet_net_usdt": is_pick.get("wallet_net_usdt"),
                "opens": is_pick.get("opens"),
            },
            "readme_config_full": full,
            "readme_config_is": is_full,
            "readme_config_oos": oos,
        }
        print(
            f"[{atr_src}] sessions={len(dates)} IS={len(is_dates)} OOS={len(oos_dates)} | "
            f"IS pick OR{is_pick.get('or_minutes')} @1%={is_pick.get('wallet_net_usdt')}U | "
            f"OOS OR10 2.5% tw90={oos['wallet_net_usdt']}U | full={full['wallet_net_usdt']}U",
            flush=True,
        )

    report["elapsed_sec"] = round(time.time() - t0, 1)
    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
