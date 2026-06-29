#!/usr/bin/env python3
"""COIN target 4000U sweep: risk%, OR, trade window, multi-trade, robot_reuse."""

from __future__ import annotations

import itertools
import json
import logging
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

logging.getLogger("orb").setLevel(logging.ERROR)

from env_loader import load_env_oi  # noqa: E402

load_env_oi()
os.environ["ORB_V2_ROBOT_RESET_CAP"] = "0"  # 回测勿在 2500U 提现，否则净收益失真

from orb.core.config import OrbConfig  # noqa: E402
from orb.ml.gate import LiveGateConfig, gate_with_ml_bypass  # noqa: E402
from orb.v2.paths import resolve_gate_config_path  # noqa: E402
from tools.orb.ml.eval_live_gate import _ml_cfg  # noqa: E402
from tools.orb.v2.backtest_universe import filter_backtest_sessions_with_atr, universe_session_dates  # noqa: E402
from tools.orb.v2.batch_symbol_sim import _run_one  # noqa: E402

SYM = "COINUSDT"
LO, HI = "2026-02-09", "2026-06-24"
EQ = 1000.0
FEE = 4.0
TARGET = 4000.0


def _dates(cfg: OrbConfig) -> List[str]:
    raw = [d for d in universe_session_dates([SYM], cfg) if LO <= d <= HI]
    return filter_backtest_sessions_with_atr(raw, [SYM], cfg)


def _run(
    dates: List[str],
    *,
    gate: LiveGateConfig,
    cfg: OrbConfig,
) -> Dict[str, Any]:
    row = _run_one(
        SYM,
        dates,
        gate=gate,
        ranker=None,
        cfg=cfg,
        robot_equity=EQ,
        fee_bps=FEE,
        entry_fill="preplace_stop",
    )
    return {
        "sessions": len(dates),
        "opens": int(row.get("opens") or 0),
        "fill_skips": int(row.get("fill_skips") or 0),
        "net_pnl_usdt": float(row.get("net_pnl_usdt") or 0),
        "return_pct": float(row.get("return_pct") or 0),
        "win_rate": float(row.get("win_rate") or 0),
        "avg_pnl_per_trade": float(row.get("avg_pnl_per_trade") or 0),
        "end_wallet_usdt": float(row.get("end_wallet_usdt") or 0),
        "wallet_net_usdt": round(float(row.get("end_wallet_usdt") or 0) - EQ, 2),
    }


def main() -> int:
    base_gate = gate_with_ml_bypass(LiveGateConfig.from_json(Path(resolve_gate_config_path())))
    probe = _ml_cfg(compound_per_symbol=True, respect_env_filters=False)
    dates = _dates(probe)
    print(f"COIN 4000U sweep | ATR-filtered {len(dates)} sessions | target net>={TARGET:.0f}U", flush=True)

    grid: List[Dict[str, Any]] = []
    for or_m, risk, tw, multi, reuse, sig_iv in itertools.product(
        (5, 10, 15),
        (0.01, 0.015, 0.02, 0.025, 0.03, 0.04, 0.05),
        (0, 90, 120),
        (False, True),  # one_trade_per_session inverted -> multi
        (False, True),  # robot_reuse_after_exit
        ("5m",),  # keep 5m for now; 1m sweep separate if needed
    ):
        grid.append(
            {
                "or_minutes": or_m,
                "risk_pct": risk,
                "trade_window_minutes": tw,
                "one_trade_per_session": not multi,
                "robot_reuse": reuse,
                "signal_interval": sig_iv,
            }
        )

    t0 = time.time()
    results: List[Dict[str, Any]] = []
    for i, g in enumerate(grid, 1):
        if i % 20 == 1:
            print(f"  [{i}/{len(grid)}] ...", flush=True)
        gate = replace(base_gate, robot_reuse_after_exit=bool(g["robot_reuse"]), max_opens_per_day=8)
        cfg = _ml_cfg(compound_per_symbol=True, respect_env_filters=False)
        cfg.or_minutes = int(g["or_minutes"])
        cfg.risk_pct = float(g["risk_pct"])
        cfg.trade_window_minutes = int(g["trade_window_minutes"])
        cfg.one_trade_per_session = bool(g["one_trade_per_session"])
        cfg.signal_interval = str(g["signal_interval"])
        cfg.exit_mode = "eod"
        cfg.sl_mode = "atr_pct"
        cfg.macro_filter = False
        r = _run(dates, gate=gate, cfg=cfg)
        results.append({**g, **r})

    hit = [r for r in results if r["wallet_net_usdt"] >= TARGET]
    hit.sort(key=lambda x: (-x["wallet_net_usdt"], -x["win_rate"]))
    profitable = [r for r in results if r["wallet_net_usdt"] > 0]
    profitable.sort(key=lambda x: x["wallet_net_usdt"], reverse=True)

    out = {
        "symbol": "COIN",
        "date_range": {"from": LO, "to": HI, "sessions_atr": len(dates)},
        "target_net_usdt": TARGET,
        "grid_size": len(grid),
        "hit_target": len(hit),
        "elapsed_sec": round(time.time() - t0, 1),
        "top10": profitable[:10],
        "target_hits": hit[:20],
        "all_results": results,
    }
    out_path = ROOT / "output/orb/v2/eval/coin_target4000_sweep.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")

    print()
    print(f"{'OR':>3} {'risk%':>5} {'tw':>4} {'1/d':>4} {'reuse':>5} {'opens':>5} {'net':>9} {'ret%':>7} {'WR':>5}")
    for r in profitable[:12]:
        one = "Y" if r["one_trade_per_session"] else "N"
        reuse = "Y" if r["robot_reuse"] else "N"
        print(
            f"{r['or_minutes']:>3} {r['risk_pct']*100:>5.1f} {r['trade_window_minutes']:>4} {one:>4} {reuse:>5} "
            f"{r['opens']:>5} {r['wallet_net_usdt']:>+9.1f} {r['return_pct']:>+7.1f} {r['win_rate']:>5.1f}"
        )

    print()
    if hit:
        print(f"TARGET HITS (>={TARGET:.0f}U): {len(hit)}")
        for r in hit[:5]:
            print(json.dumps(r, indent=2))
    else:
        best = profitable[0] if profitable else None
        print(f"No config hit {TARGET:.0f}U. Best wallet net: {best['wallet_net_usdt']:+.0f}U" if best else "No profitable configs")
    print(f"\njson -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
