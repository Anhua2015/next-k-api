#!/usr/bin/env python3
"""COIN rule exploration: backtest config knobs + entry-time filter simulation."""

from __future__ import annotations

import json
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from env_loader import load_env_oi  # noqa: E402

load_env_oi()

from orb.core.config import OrbConfig  # noqa: E402
from orb.core.kline_cache import load_klines  # noqa: E402
from orb.core.session import session_anchor_ms, session_close_ms, session_slice  # noqa: E402
from orb.ml.gate import LiveGateConfig, gate_with_ml_bypass  # noqa: E402
from orb.v2.paths import resolve_gate_config_path  # noqa: E402
from tools.orb.ml.eval_live_gate import _ml_cfg  # noqa: E402
from tools.orb.v2.backtest_universe import universe_session_dates  # noqa: E402
from tools.orb.v2.batch_symbol_sim import _run_one  # noqa: E402

SYM = "COINUSDT"
LO, HI = "2026-02-09", "2026-06-24"
EQ = 1000.0
FEE = 4.0


def _base_cfg() -> OrbConfig:
    cfg = _ml_cfg(compound_per_symbol=True, respect_env_filters=False)
    cfg.or_minutes = 15
    cfg.sl_mode = "atr_pct"
    cfg.exit_mode = "eod"
    cfg.macro_filter = False
    return cfg


def run_backtest(label: str, **overrides: Any) -> Dict[str, Any]:
    gate = gate_with_ml_bypass(LiveGateConfig.from_json(Path(resolve_gate_config_path())))
    cfg = _base_cfg()
    for k, v in overrides.items():
        setattr(cfg, k, v)
    dates = [d for d in universe_session_dates([SYM], cfg) if LO <= d <= HI]
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
        "label": label,
        "opens": int(row.get("opens") or 0),
        "fill_skips": int(row.get("fill_skips") or 0),
        "net_pnl_usdt": float(row.get("net_pnl_usdt") or 0),
        "return_pct": float(row.get("return_pct") or 0),
        "win_rate": float(row.get("win_rate") or 0),
        "avg_pnl_per_trade": float(row.get("avg_pnl_per_trade") or 0),
        "end_wallet_usdt": float(row.get("end_wallet_usdt") or 0),
        **overrides,
    }


def entry_time_trend_aligned(
    trades_csv: Path,
    df5: pd.DataFrame,
    cfg: OrbConfig,
) -> pd.DataFrame:
    """At entry: session open -> entry move aligns with trade side."""
    trades = pd.read_csv(trades_csv)
    rows = []
    for _, t in trades.iterrows():
        d = str(t["session_date"])
        ts = pd.Timestamp(d + " 12:00:00", tz=cfg.session_tz)
        anchor = session_anchor_ms(
            int(ts.value // 1_000_000),
            tz=cfg.session_tz,
            session_open_time=cfg.session_open_time,
        )
        fill_ms = int(t.get("fill_bar_open_ms") or t.get("scan_open_ms") or 0)
        if fill_ms <= 0:
            continue
        sess = session_slice(df5, fill_ms, tz=cfg.session_tz, session_open_time=cfg.session_open_time)
        if sess.empty:
            continue
        open_p = float(sess.iloc[0]["open"])
        entry_p = float(t["entry"])
        day_move = (entry_p - open_p) / open_p * 100
        side = str(t["side"])
        aligned = (side == "LONG" and day_move > 0) or (side == "SHORT" and day_move < 0)
        rows.append(
            {
                "session_date": d,
                "side": side,
                "pnl_usdt": float(t["pnl_usdt"]),
                "outcome": t.get("outcome"),
                "day_move_pct": day_move,
                "trend_aligned": aligned,
            }
        )
    return pd.DataFrame(rows)


def simulate_posthoc_filters(trades_csv: Path, df5: pd.DataFrame, cfg: OrbConfig) -> None:
    """Simulate entry-time filters on baseline preplace trades."""
    if not trades_csv.is_file():
        print(f"  (skip posthoc: missing {trades_csv})")
        return

    trades = pd.read_csv(trades_csv)
    enriched = entry_time_trend_aligned(trades_csv, df5, cfg)
    if enriched.empty:
        return

    base_pnl = trades["pnl_usdt"].sum()
    print(f"\n  baseline trades={len(trades)} net={base_pnl:+.0f}U")

    rules: List[Tuple[str, pd.Series]] = [
        ("trend_aligned (open->entry)", enriched["trend_aligned"]),
        ("counter_trend skip", ~enriched["trend_aligned"]),
    ]
    merged = trades.merge(enriched[["session_date", "trend_aligned", "day_move_pct"]], on="session_date", how="inner")
    rules.extend(
        [
            ("|day_move|>=0.3% at entry", merged["day_move_pct"].abs() >= 0.3),
            ("LONG & day_move>0", (merged["side"] == "LONG") & (merged["day_move_pct"] > 0)),
            ("SHORT & day_move<0", (merged["side"] == "SHORT") & (merged["day_move_pct"] < 0)),
            ("winners only hindsight", merged["pnl_usdt"] > 0),
        ]
    )

    print(f"  {'filter':<32} {'keep':>4} {'WR':>5} {'net':>8} {'avg/tr':>8}")
    for name, mask in rules:
        if len(mask) != len(merged):
            sub = merged[mask.values] if hasattr(mask, "values") else merged[mask]
        else:
            sub = merged[mask]
        if len(sub) == 0:
            continue
        wr = (sub["pnl_usdt"] > 0).mean() * 100
        pnl = sub["pnl_usdt"].sum()
        print(f"  {name:<32} {len(sub):>4} {wr:>4.0f}% {pnl:>+8.0f}U {pnl/len(sub):>+8.1f}U")


def main() -> int:
    configs = [
        ("baseline OR15 EOD", {}),
        ("min_or_width=2.0", {"min_or_width_pct": 2.0}),
        ("min_or_width=2.5", {"min_or_width_pct": 2.5}),
        ("max_or_width=4.0", {"max_or_width_pct": 4.0}),
        ("trade_window=60m", {"trade_window_minutes": 60}),
        ("trade_window=90m", {"trade_window_minutes": 90}),
        ("trade_window=120m", {"trade_window_minutes": 120}),
        ("vwap_filter", {"vwap_filter": True}),
        ("early_exit=60m", {"early_exit_minutes": 60}),
        ("min_or=2.0 + vwap", {"min_or_width_pct": 2.0, "vwap_filter": True}),
        ("min_or=2.5 + vwap", {"min_or_width_pct": 2.5, "vwap_filter": True}),
        ("min_or=2.0 + early60", {"min_or_width_pct": 2.0, "early_exit_minutes": 60}),
        ("min_or=2.5 + early60", {"min_or_width_pct": 2.5, "early_exit_minutes": 60}),
        ("min_or=2.0 + win60m", {"min_or_width_pct": 2.0, "trade_window_minutes": 60}),
        ("min_or=2.5 + win90m", {"min_or_width_pct": 2.5, "trade_window_minutes": 90}),
        ("vwap + early60", {"vwap_filter": True, "early_exit_minutes": 60}),
        ("min_or=2.0 + vwap + early60", {"min_or_width_pct": 2.0, "vwap_filter": True, "early_exit_minutes": 60}),
    ]

    print(f"COIN rule backtest | preplace_stop | {LO}..{HI}")
    print(f"{'label':<28} {'opens':>5} {'skip':>5} {'net':>8} {'ret%':>7} {'WR':>5} {'avg/tr':>7}")
    t0 = time.time()
    results: List[Dict[str, Any]] = []
    for label, overrides in configs:
        r = run_backtest(label, **overrides)
        results.append(r)
        print(
            f"{label:<28} {r['opens']:>5} {r['fill_skips']:>5} {r['net_pnl_usdt']:>+8.1f} "
            f"{r['return_pct']:>+7.1f} {r['win_rate']:>5.1f} {r['avg_pnl_per_trade']:>+7.1f}"
        )

    results.sort(key=lambda x: x["net_pnl_usdt"], reverse=True)
    print(f"\nTOP 5 by net PnL ({time.time()-t0:.0f}s):")
    for r in results[:5]:
        print(f"  {r['label']}: {r['net_pnl_usdt']:+.0f}U ({r['return_pct']:+.0f}%) WR={r['win_rate']:.0f}% opens={r['opens']}")

    out = ROOT / "output/orb/v2/eval/coin_rule_explore.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"results": results, "elapsed_sec": round(time.time() - t0, 1)}, indent=2), encoding="utf-8")
    print(f"\njson -> {out}")

    # Post-hoc on baseline run - re-run once to get trades csv
    print("\n[post-hoc entry-time filters on baseline trades]")
    cfg = _base_cfg()
    gate = gate_with_ml_bypass(LiveGateConfig.from_json(Path(resolve_gate_config_path())))
    dates = [d for d in universe_session_dates([SYM], cfg) if LO <= d <= HI]
    from tools.orb.v2.sim_live_session import simulate_live_sessions  # noqa: E402
    from orb.v2.robots import init_robot_wallets  # noqa: E402

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
    )
    trades = [t for d in days for t in (d.get("trades") or [])]
    tmp_csv = out.parent / "_coin_baseline_trades_tmp.csv"
    if trades:
        pd.DataFrame(trades).to_csv(tmp_csv, index=False)
        df5 = load_klines(SYM, "5m")
        simulate_posthoc_filters(tmp_csv, df5, cfg)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
