#!/usr/bin/env python3
"""单独回测 vnpy_ctastrategy 官方示例策略（移植版）。"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from env_loader import load_env_oi  # noqa: E402
from orb.core.config import OrbConfig  # noqa: E402
from orb.core.kline_cache import load_klines, norm_symbol  # noqa: E402
from orb.cta.engine import run_cta_backtest  # noqa: E402
from orb.cta.registry import CTA_STRATEGIES, cta_config_for_strategy, list_strategies  # noqa: E402
from orb.core.symbols import parse_symbol_list  # noqa: E402
from orb.kk.paths import resolve_symbols_path  # noqa: E402
from orb.core.kline_cache import session_dates_from_cache  # noqa: E402

import pandas as pd  # noqa: E402


def _session_slice(df: pd.DataFrame, session_date: str, cfg: OrbConfig) -> pd.DataFrame:
    from orb.core.session import session_anchor_ms, session_close_ms

    tz = cfg.session_tz
    ts = pd.Timestamp(f"{session_date} 12:00:00", tz=tz)
    anchor = session_anchor_ms(int(ts.value // 1_000_000), tz=tz, session_open_time=cfg.session_open_time)
    close = session_close_ms(anchor, tz=tz, session_close_time=cfg.session_close_time)
    if close is None:
        close = anchor + 6 * 60 * 60 * 1000
    return df[(df["open_time"] >= anchor) & (df["open_time"] <= close)].copy()


def run_one(
    strategy_key: str,
    symbol: str,
    dates: List[str],
    *,
    cfg: OrbConfig,
    equity: float,
    risk_pct: float,
) -> Dict[str, Any]:
    meta = CTA_STRATEGIES[strategy_key]
    sym = norm_symbol(symbol)
    df1 = load_klines(sym, "1m")
    if df1.empty:
        return {"symbol": sym, "strategy": strategy_key, "summary": {"net_pnl_usdt": 0, "opens": 0}}
    chunks = []
    for d in dates:
        sl = _session_slice(df1, d, cfg)
        if not sl.empty:
            chunks.append(sl)
    if not chunks:
        return {"symbol": sym, "strategy": strategy_key, "summary": {"net_pnl_usdt": 0, "opens": 0}}
    df = pd.concat(chunks, ignore_index=True).sort_values("open_time")
    cta_cfg = cta_config_for_strategy(
        strategy_key,
        equity_usdt=float(equity),
        risk_pct=float(risk_pct),
        compound=True,
        eod_flat=bool(meta.get("eod_flat")),
    )
    cfg_bt = OrbConfig.from_env()
    cfg_bt.risk_pct = float(risk_pct)
    cfg_bt.fixed_notional_usdt = 0.0
    out = run_cta_backtest(
        df,
        strategy_fn=meta["fn"],
        orb_cfg=cfg,
        cta_cfg=cta_cfg,
        warmup=int(meta.get("warmup") or 30),
    )
    out["symbol"] = sym.replace("USDT", "")
    out["strategy"] = strategy_key
    out["title"] = meta["title"]
    return out


def main() -> int:
    load_env_oi()
    ap = argparse.ArgumentParser(description="Research vnpy CTA example strategies")
    ap.add_argument("--symbol", default="", help="单标，如 TSLA")
    ap.add_argument("--symbols", default="", help="逗号分隔多标")
    ap.add_argument("--symbols-file", default=str(resolve_symbols_path()))
    ap.add_argument("--strategy", default="all", help="策略 key 或 all")
    ap.add_argument("--from-date", default="2026-02-01")
    ap.add_argument("--to-date", default="2026-06-30")
    ap.add_argument("--equity", type=float, default=1000.0)
    ap.add_argument("--risk-pct", type=float, default=0.01)
    ap.add_argument("--json-out", default="")
    args = ap.parse_args()

    cfg = OrbConfig.from_env()
    if (args.symbols or "").strip():
        symbols = [norm_symbol(s.strip()) for s in args.symbols.split(",") if s.strip()]
    elif (args.symbol or "").strip():
        symbols = [norm_symbol(args.symbol.strip())]
    else:
        symbols = parse_symbol_list(Path(args.symbols_file).read_text(encoding="utf-8"))

    ref = symbols[0]
    dates = [
        d
        for d in session_dates_from_cache(ref, cfg)
        if (not args.from_date or d >= args.from_date) and (not args.to_date or d <= args.to_date)
    ]
    keys = list_strategies() if args.strategy == "all" else [args.strategy.strip()]
    for k in keys:
        if k not in CTA_STRATEGIES:
            print(f"Unknown strategy: {k}")
            return 1

    print(f"[cta research] {dates[0]}..{dates[-1]} | {len(symbols)} sym | eq={args.equity} risk={args.risk_pct}", flush=True)
    t0 = time.time()
    results: List[Dict[str, Any]] = []
    for k in keys:
        total_net = 0.0
        total_opens = 0
        per_sym = []
        for sym in symbols:
            r = run_one(k, sym, dates, cfg=cfg, equity=float(args.equity), risk_pct=float(args.risk_pct))
            s = r["summary"]
            total_net += float(s.get("net_pnl_usdt") or 0)
            total_opens += int(s.get("opens") or 0)
            per_sym.append(
                {
                    "symbol": r["symbol"],
                    "net_pnl_usdt": s.get("net_pnl_usdt"),
                    "opens": s.get("opens"),
                    "equity_end": s.get("equity_end"),
                }
            )
        row = {
            "strategy": k,
            "title": CTA_STRATEGIES[k]["title"],
            "net_pnl_usdt": round(total_net, 2),
            "opens": total_opens,
            "per_symbol": per_sym,
        }
        results.append(row)
        print(f"  {k:14s} {CTA_STRATEGIES[k]['title']:22s} net={total_net:+.2f}U opens={total_opens}", flush=True)

    out_dir = ROOT / "output" / "orb" / "cta"
    out_dir.mkdir(parents=True, exist_ok=True)
    tag = f"{dates[0]}_{dates[-1]}"
    sym_tag = symbols[0].replace("USDT", "") if len(symbols) == 1 else f"pool{len(symbols)}"
    out_path = Path(args.json_out) if args.json_out else out_dir / f"vnpy_cta_{sym_tag}_eq{int(args.equity)}_{tag}.json"
    payload = {
        "date_range": {"from": dates[0], "to": dates[-1], "sessions": len(dates)},
        "equity_usdt": float(args.equity),
        "risk_pct": float(args.risk_pct),
        "symbols": [norm_symbol(s) for s in symbols],
        "results": results,
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\njson -> {out_path} ({time.time()-t0:.1f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
