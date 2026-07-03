#!/usr/bin/env python3
"""对比 baseline vs 12:00 ET 后只平不开 的复利回测。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from env_loader import load_env_oi

load_env_oi()

import pandas as pd  # noqa: E402

from orb.core.config import OrbConfig  # noqa: E402
from orb.core.kline_cache import load_klines, norm_symbol, session_dates_from_cache  # noqa: E402
from orb.core.symbols import parse_symbol_list  # noqa: E402
from orb.cta.engine import run_cta_backtest  # noqa: E402
from orb.cta.registry import CTA_STRATEGIES, cta_config_for_strategy  # noqa: E402
from orb.kk.paths import resolve_symbols_path  # noqa: E402
from tools.cta.research_vnpy_cta import _session_slice  # noqa: E402

KK = dict(
    compound=True,
    rth_only=True,
    eod_flat=True,
    exit_hour=15,
    exit_minute=55,
    slip_bps_entry=5.0,
    slip_bps_exit=5.0,
    max_notional_usdt=0.0,
)
LO, HI = "2026-02-01", "2026-06-30"
EQUITY = 14.0


def run_pool(*, no_entry_after_hour: int = -1) -> list[dict]:
    cfg = OrbConfig.from_env()
    meta = CTA_STRATEGIES["king_keltner"]
    symbols = [norm_symbol(s) for s in parse_symbol_list(Path(resolve_symbols_path()).read_text(encoding="utf-8"))]
    rows = []
    for sym in symbols:
        label = sym.replace("USDT", "")
        dates = [d for d in session_dates_from_cache(sym, cfg) if LO <= d <= HI]
        df1 = load_klines(sym, "1m")
        if df1.empty or not dates:
            continue
        chunks = [_session_slice(df1, d, cfg) for d in dates]
        df = pd.concat([c for c in chunks if not c.empty], ignore_index=True)
        if df.empty:
            continue
        kw = {**KK}
        if no_entry_after_hour >= 0:
            kw["no_entry_after_hour"] = int(no_entry_after_hour)
            kw["no_entry_after_minute"] = 0
        out = run_cta_backtest(
            df,
            strategy_fn=meta["fn"],
            orb_cfg=cfg,
            cta_cfg=cta_config_for_strategy("king_keltner", equity_usdt=EQUITY, risk_pct=0.01, **kw),
            warmup=25,
        )
        s = out["summary"]
        closes = [t for t in out["trades"] if t["event"] == "close"]
        wins = sum(1 for t in closes if float(t["pnl_usdt"]) > 0)
        n = len(closes)
        net = float(s["net_pnl_usdt"])
        end = float(s["equity_end"])
        rows.append(
            {
                "symbol": label,
                "opens": int(s["opens"]),
                "closes": n,
                "win_rate": round(100.0 * wins / n, 1) if n else 0.0,
                "net": round(net, 2),
                "equity_end": round(end, 2),
                "ret_pct": round(100.0 * (end - EQUITY) / EQUITY, 1),
            }
        )
        tag = "no12" if no_entry_after_hour >= 0 else "base"
        print(f"  [{tag}] {label:5s} end={end:7.2f}U net={net:+8.2f}U opens={s['opens']:4d} win={wins}/{n}", flush=True)
    return rows


def main() -> None:
    pool_start = EQUITY * 7
    print(f"=== KK compound compare | {LO}..{HI} | equity={EQUITY}U x 7 | RTH+EOD ===\n")
    print("--- baseline (full RTH entries) ---")
    base = run_pool(no_entry_after_hour=-1)
    print("\n--- no entry after 12:00 ET (close only) ---")
    cut = run_pool(no_entry_after_hour=12)

    def tot(rows: list[dict]) -> dict:
        net = sum(r["net"] for r in rows)
        end = sum(r["equity_end"] for r in rows)
        opens = sum(r["opens"] for r in rows)
        closes = sum(r["closes"] for r in rows)
        return {
            "net": round(net, 2),
            "equity_end": round(end, 2),
            "ret_pct": round(100.0 * (end - pool_start) / pool_start, 1),
            "opens": opens,
            "closes": closes,
        }

    b = tot(base)
    c = tot(cut)
    print("\n=== POOL TOTAL (7 bots x 14U = 98U start) ===")
    print(f"{'mode':28s} {'end U':>10s} {'net U':>10s} {'ret%':>8s} {'opens':>7s} {'closes':>7s}")
    print(f"{'baseline full RTH':28s} {b['equity_end']:10.2f} {b['net']:+10.2f} {b['ret_pct']:+7.1f}% {b['opens']:7d} {b['closes']:7d}")
    print(f"{'no entry after 12:00 ET':28s} {c['equity_end']:10.2f} {c['net']:+10.2f} {c['ret_pct']:+7.1f}% {c['opens']:7d} {c['closes']:7d}")
    print(f"{'delta (cut - base)':28s} {c['equity_end']-b['equity_end']:+10.2f} {c['net']-b['net']:+10.2f} {c['ret_pct']-b['ret_pct']:+7.1f}pp")

    print("\n=== PER SYMBOL ===")
    print(f"{'sym':6s} {'base end':>10s} {'base ret%':>10s} {'cut end':>10s} {'cut ret%':>10s} {'delta U':>9s}")
    cut_map = {r["symbol"]: r for r in cut}
    for r in base:
        x = cut_map.get(r["symbol"], {})
        d = float(x.get("net", 0)) - r["net"]
        print(
            f"{r['symbol']:6s} {r['equity_end']:10.2f} {r['ret_pct']:+9.1f}% "
            f"{x.get('equity_end', 0):10.2f} {x.get('ret_pct', 0):+9.1f}% {d:+9.2f}"
        )


if __name__ == "__main__":
    main()
