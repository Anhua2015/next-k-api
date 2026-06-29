#!/usr/bin/env python3
"""Preplace SL-fix exploration: OR window / symbols / risk / exit."""

from __future__ import annotations

import json
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from env_loader import load_env_oi  # noqa: E402
from orb.core.kline_cache import norm_symbol  # noqa: E402
from orb.ml.gate import LiveGateConfig, gate_with_ml_bypass  # noqa: E402
from orb.ml.samples import parse_symbol_list  # noqa: E402
from orb.v2.paths import resolve_gate_config_path, resolve_symbols_path  # noqa: E402
from tools.orb.ml.eval_live_gate import _ml_cfg  # noqa: E402
from tools.orb.v2.backtest_universe import universe_session_dates  # noqa: E402
from tools.orb.v2.batch_symbol_sim import _run_one  # noqa: E402

load_env_oi()

LO, HI = "2026-02-09", "2026-06-24"
EQ = 1000.0
FEE = 4.0
TOP8 = ["COIN", "HOOD", "PLTR", "INTC", "CRCL", "PAYP", "AMZN", "NVDA"]
TOP6 = ["COIN", "HOOD", "INTC", "CRCL", "PAYP", "NVDA"]  # drop AMZN, PLTR


def _summarize(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    net = round(sum(float(r.get("net_pnl_usdt") or 0) for r in rows), 2)
    opens = sum(int(r.get("opens") or 0) for r in rows)
    prof = sum(1 for r in rows if float(r.get("net_pnl_usdt") or 0) > 0)
    wins = sum(int(r.get("win_trades") or 0) for r in rows)
    losses = sum(int(r.get("loss_trades") or 0) for r in rows)
    wr = round(wins / opens * 100, 1) if opens else 0.0
    cap = len(rows) * EQ
    return {
        "net_usdt": net,
        "opens": opens,
        "profitable_syms": f"{prof}/{len(rows)}",
        "win_rate_pct": wr,
        "ret_on_cap_pct": round(net / cap * 100, 1) if cap else 0.0,
    }


def _run_scenario(
    name: str,
    syms: List[str],
    *,
    or_minutes: int,
    risk_pct: float = 0.01,
    exit_mode: str = "eod",
    tp_r: float = 0.0,
) -> Dict[str, Any]:
    gate = gate_with_ml_bypass(LiveGateConfig.from_json(Path(resolve_gate_config_path())))
    cfg = _ml_cfg(compound_per_symbol=True, respect_env_filters=False)
    cfg.or_minutes = int(or_minutes)
    cfg.risk_pct = float(risk_pct)
    cfg.exit_mode = str(exit_mode)
    cfg.tp_r_multiple = float(tp_r) if exit_mode == "fixed_r" else 0.0

    rows: List[Dict[str, Any]] = []
    t0 = time.time()
    for sym in syms:
        s = norm_symbol(sym)
        dates = [d for d in universe_session_dates([s], cfg) if LO <= d <= HI]
        if not dates:
            rows.append({"symbol": sym.replace("USDT", ""), "sessions": 0, "opens": 0, "net_pnl_usdt": 0})
            continue
        row = _run_one(
            s,
            dates,
            gate=gate,
            ranker=None,
            cfg=cfg,
            robot_equity=EQ,
            fee_bps=FEE,
            entry_fill="preplace_stop",
        )
        rows.append({k: row[k] for k in row if k != "days"})
    summary = _summarize(rows)
    summary["name"] = name
    summary["or_minutes"] = or_minutes
    summary["risk_pct"] = risk_pct
    summary["exit_mode"] = exit_mode
    summary["symbols"] = len(syms)
    summary["elapsed_sec"] = round(time.time() - t0, 1)
    summary["rows"] = sorted(rows, key=lambda r: float(r.get("net_pnl_usdt") or 0), reverse=True)
    return summary


def main() -> int:
    scenarios = [
        ("OR5 top8", TOP8, {"or_minutes": 5}),
        ("OR10 top8", TOP8, {"or_minutes": 10}),
        ("OR15 top8", TOP8, {"or_minutes": 15}),
        ("OR5 top6 no AMZN/PLTR", TOP6, {"or_minutes": 5}),
        ("OR5 risk1.5%", TOP8, {"or_minutes": 5, "risk_pct": 0.015}),
        ("OR5 risk2%", TOP8, {"or_minutes": 5, "risk_pct": 0.02}),
        ("OR5 fixed_r 1.5R", TOP8, {"or_minutes": 5, "exit_mode": "fixed_r", "tp_r": 1.5}),
    ]
    out: List[Dict[str, Any]] = []
    for name, syms, kw in scenarios:
        print(f"=== {name} ===", flush=True)
        out.append(_run_scenario(name, syms, **kw))

    out_path = ROOT / "output/orb/v2/eval/explore_preplace_slfix.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")

    print()
    print(f"{'scenario':<28} {'net_U':>8} {'opens':>6} {'prof':>5} {'win%':>6} {'ret/cap%':>9}")
    for s in out:
        print(
            f"{s['name']:<28} {s['net_usdt']:>+8.1f} {s['opens']:>6} "
            f"{s['profitable_syms']:>5} {s['win_rate_pct']:>6.1f} {s['ret_on_cap_pct']:>+9.1f}"
        )
    print(f"\njson -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
