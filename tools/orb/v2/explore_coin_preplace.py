#!/usr/bin/env python3
"""COIN-only preplace sweep (SL-fixed). Rank configs; verify top on OOS half."""

from __future__ import annotations

import itertools
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

logging.getLogger("orb").setLevel(logging.ERROR)

from env_loader import load_env_oi  # noqa: E402
from orb.core.kline_cache import norm_symbol  # noqa: E402
from orb.ml.gate import LiveGateConfig, gate_with_ml_bypass  # noqa: E402
from orb.v2.paths import resolve_gate_config_path  # noqa: E402
from tools.orb.ml.eval_live_gate import _ml_cfg  # noqa: E402
from tools.orb.v2.backtest_universe import universe_session_dates  # noqa: E402
from tools.orb.v2.batch_symbol_sim import _run_one  # noqa: E402

load_env_oi()

SYM = "COINUSDT"
LO, HI = "2026-02-09", "2026-06-24"
OOS_LO = "2026-04-17"  # 后半段样本外
EQ = 1000.0
FEE = 4.0


def _run(
    dates: List[str],
    *,
    or_minutes: int,
    risk_pct: float,
    exit_mode: str,
    tp_r: float,
    sl_mode: str,
    min_or_width_pct: float,
) -> Dict[str, Any]:
    gate = gate_with_ml_bypass(LiveGateConfig.from_json(Path(resolve_gate_config_path())))
    cfg = _ml_cfg(compound_per_symbol=True, respect_env_filters=False)
    cfg.or_minutes = int(or_minutes)
    cfg.risk_pct = float(risk_pct)
    cfg.exit_mode = str(exit_mode)
    cfg.tp_r_multiple = float(tp_r) if exit_mode == "fixed_r" else 0.0
    cfg.sl_mode = str(sl_mode)
    cfg.min_or_width_pct = float(min_or_width_pct)
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
        "or_minutes": or_minutes,
        "risk_pct": risk_pct,
        "exit_mode": exit_mode,
        "tp_r": tp_r if exit_mode == "fixed_r" else None,
        "sl_mode": sl_mode,
        "min_or_width_pct": min_or_width_pct,
        "sessions": len(dates),
        "opens": int(row.get("opens") or 0),
        "net_pnl_usdt": float(row.get("net_pnl_usdt") or 0),
        "return_pct": float(row.get("return_pct") or 0),
        "win_rate": float(row.get("win_rate") or 0),
        "fill_skips": int(row.get("fill_skips") or 0),
        "avg_pnl_per_trade": float(row.get("avg_pnl_per_trade") or 0),
        "end_wallet_usdt": float(row.get("end_wallet_usdt") or 0),
    }


def main() -> int:
    cfg_probe = _ml_cfg(compound_per_symbol=True, respect_env_filters=False)
    all_dates = [d for d in universe_session_dates([SYM], cfg_probe) if LO <= d <= HI]
    oos_dates = [d for d in all_dates if d >= OOS_LO]
    is_dates = [d for d in all_dates if d < OOS_LO]

    grid: List[Dict[str, Any]] = []
    for or_m, risk, exit_m, tp_r, sl_m, min_w in itertools.product(
        (5, 10, 15),
        (0.01, 0.015, 0.02),
        ("eod", "fixed_r"),
        (1.5,),  # fixed_r only; eod ignores
        ("atr_pct", "or_range"),
        (0.0, 0.35),
    ):
        if exit_m == "eod":
            tp_use = 0.0
        else:
            tp_use = float(tp_r)
        grid.append(
            {
                "or_minutes": or_m,
                "risk_pct": risk,
                "exit_mode": exit_m,
                "tp_r": tp_use,
                "sl_mode": sl_m,
                "min_or_width_pct": min_w,
            }
        )

    print(f"COIN sweep | {len(all_dates)} sessions | {len(grid)} configs", flush=True)
    t0 = time.time()
    results: List[Dict[str, Any]] = []
    for i, g in enumerate(grid, 1):
        if i % 10 == 1:
            print(f"  [{i}/{len(grid)}] ...", flush=True)
        r = _run(all_dates, **g)
        results.append(r)

    profitable = [r for r in results if r["net_pnl_usdt"] > 0 and r["opens"] >= 10]
    profitable.sort(key=lambda x: x["net_pnl_usdt"], reverse=True)

    top_verify: List[Dict[str, Any]] = []
    for r in profitable[:5]:
        g = {k: r[k] for k in ("or_minutes", "risk_pct", "exit_mode", "tp_r", "sl_mode", "min_or_width_pct")}
        if r["exit_mode"] == "eod":
            g["tp_r"] = 0.0
        oos = _run(oos_dates, **g)
        is_ = _run(is_dates, **g)
        top_verify.append(
            {
                **g,
                "full_net": r["net_pnl_usdt"],
                "full_ret_pct": r["return_pct"],
                "full_opens": r["opens"],
                "full_win_rate": r["win_rate"],
                "is_net": is_["net_pnl_usdt"],
                "oos_net": oos["net_pnl_usdt"],
                "oos_opens": oos["opens"],
                "oos_win_rate": oos["win_rate"],
            }
        )

    out = {
        "symbol": "COIN",
        "date_range": {"from": LO, "to": HI, "sessions": len(all_dates)},
        "oos_from": OOS_LO,
        "grid_size": len(grid),
        "profitable_configs": len(profitable),
        "elapsed_sec": round(time.time() - t0, 1),
        "top5_full_period": profitable[:5],
        "top5_oos_verify": top_verify,
        "all_results": results,
    }
    out_path = ROOT / "output/orb/v2/eval/coin_preplace_sweep_slfix.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")

    print()
    print("TOP (full period, net>0, opens>=10):")
    print(f"{'OR':>3} {'risk':>5} {'exit':>8} {'tp':>4} {'sl':>8} {'minW':>5} {'net':>8} {'ret%':>7} {'win%':>5} {'opens':>5}")
    for r in profitable[:8]:
        tp = r["tp_r"] if r["exit_mode"] == "fixed_r" else "-"
        print(
            f"{r['or_minutes']:>3} {r['risk_pct']:>5.2f} {r['exit_mode']:>8} {str(tp):>4} "
            f"{r['sl_mode']:>8} {r['min_or_width_pct']:>5.2f} {r['net_pnl_usdt']:>+8.1f} "
            f"{r['return_pct']:>+7.1f} {r['win_rate']:>5.1f} {r['opens']:>5}"
        )
    if top_verify:
        best = top_verify[0]
        print()
        print("BEST (also OOS check):")
        print(json.dumps(best, indent=2))
    print(f"\njson -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
