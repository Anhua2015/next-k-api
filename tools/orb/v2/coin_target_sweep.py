#!/usr/bin/env python3
"""COIN 参数扫描：目标 net >= 4000U（preplace_stop + ATR filter）。"""

from __future__ import annotations

import itertools
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from env_loader import load_env_oi  # noqa: E402

load_env_oi()

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


def main() -> int:
    gate = gate_with_ml_bypass(LiveGateConfig.from_json(Path(resolve_gate_config_path())))
    base = _ml_cfg(compound_per_symbol=True, respect_env_filters=False)
    base.macro_filter = False
    all_dates = [d for d in universe_session_dates([SYM], base) if LO <= d <= HI]
    dates = filter_backtest_sessions_with_atr(all_dates, [SYM], base)
    print(f"COIN sweep | {len(dates)} sessions (ATR ok) | target net>={TARGET:.0f}U\n")

    grid: List[Dict[str, Any]] = []
    for or_m, risk, tw, sl_m in itertools.product(
        (5, 10, 15),
        (0.01, 0.015, 0.02, 0.025, 0.03, 0.04),
        (0, 60, 90, 120),
        ("atr_pct", "or_range"),
    ):
        grid.append({"or_minutes": or_m, "risk_pct": risk, "trade_window_minutes": tw, "sl_mode": sl_m})

    t0 = time.time()
    results: List[Dict[str, Any]] = []
    for i, g in enumerate(grid, 1):
        cfg = _ml_cfg(compound_per_symbol=True, respect_env_filters=False)
        cfg.macro_filter = False
        cfg.or_minutes = int(g["or_minutes"])
        cfg.risk_pct = float(g["risk_pct"])
        cfg.trade_window_minutes = int(g["trade_window_minutes"])
        cfg.sl_mode = str(g["sl_mode"])
        cfg.exit_mode = "eod"
        row = _run_one(SYM, dates, gate=gate, ranker=None, cfg=cfg, robot_equity=EQ, fee_bps=FEE, entry_fill="preplace_stop")
        rec = {**g, **{k: row[k] for k in ("opens", "fill_skips", "net_pnl_usdt", "return_pct", "win_rate", "end_wallet_usdt", "avg_pnl_per_trade")}}
        results.append(rec)
        if i % 20 == 0:
            print(f"  [{i}/{len(grid)}] best so far {max(r['net_pnl_usdt'] for r in results):+.0f}U", flush=True)

    results.sort(key=lambda x: x["net_pnl_usdt"], reverse=True)
    hit = [r for r in results if r["net_pnl_usdt"] >= TARGET]

    print(f"\n{'OR':>3} {'risk%':>6} {'win':>4} {'SL':>8} {'opens':>5} {'skip':>5} {'net':>8} {'ret%':>7} {'WR':>5}")
    for r in results[:12]:
        print(
            f"{r['or_minutes']:>3} {r['risk_pct']:>6.3f} {r['trade_window_minutes']:>4} {r['sl_mode']:>8} "
            f"{r['opens']:>5} {r['fill_skips']:>5} {r['net_pnl_usdt']:>+8.1f} {r['return_pct']:>+7.1f} {r['win_rate']:>5.1f}"
        )

    print(f"\n>= {TARGET:.0f}U configs: {len(hit)}/{len(results)}")
    if hit:
        b = hit[0]
        print("BEST HIT:", json.dumps(b, indent=2))

    out = ROOT / "output/orb/v2/eval/coin_target_4000_sweep.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "target_net_usdt": TARGET,
                "sessions": len(dates),
                "date_range": {"from": LO, "to": HI},
                "elapsed_sec": round(time.time() - t0, 1),
                "hit_count": len(hit),
                "top10": results[:10],
                "hit_configs": hit[:20],
                "all": results,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\njson -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
