#!/usr/bin/env python3
"""king_keltner 全池逐标收益（仅该策略，较快）。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from env_loader import load_env_oi
from orb.core.config import OrbConfig
from orb.core.kline_cache import load_klines, norm_symbol
from orb.cta.engine import run_cta_backtest
from orb.cta.registry import CTA_STRATEGIES, cta_config_for_strategy
from orb.core.symbols import parse_symbol_list
from orb.kk.paths import resolve_symbols_path
from tools.cta.research_vnpy_cta import _session_slice
from orb.core.kline_cache import session_dates_from_cache
import pandas as pd

load_env_oi()
cfg = OrbConfig.from_env()
symbols = parse_symbol_list(Path(resolve_symbols_path()).read_text(encoding="utf-8"))
meta = CTA_STRATEGIES["king_keltner"]
dates_all = None
rows = []
for sym in symbols:
    sym = norm_symbol(sym)
    df1 = load_klines(sym, "1m")
    if df1.empty:
        rows.append((sym.replace("USDT", ""), 0, 0, 0, 0, "no_data"))
        continue
    dates = [d for d in session_dates_from_cache(sym, cfg) if "2026-02-01" <= d <= "2026-06-30"]
    chunks = [_session_slice(df1, d, cfg) for d in dates]
    df = pd.concat([c for c in chunks if not c.empty], ignore_index=True)
    if df.empty:
        continue
    out = run_cta_backtest(
        df.sort_values("open_time"),
        strategy_fn=meta["fn"],
        orb_cfg=cfg,
        cta_cfg=cta_config_for_strategy("king_keltner", equity_usdt=1000, risk_pct=0.01, compound=True),
        warmup=25,
    )
    s = out["summary"]
    closes = [t for t in out["trades"] if t["event"] == "close"]
    fees = sum(float(t["fee_usdt"]) for t in closes)
    gross = sum(float(t["pnl_usdt_gross"]) for t in closes)
    tag = sym.replace("USDT", "")
    ret = (float(s["equity_end"]) / 1000 - 1) * 100
    rows.append((tag, int(s["opens"]), gross, fees, float(s["net_pnl_usdt"]), f"{ret:+.1f}%"))

print("king_keltner | 1000U 1% compound | Feb-Jun 2026 | per symbol")
print(f"{'sym':6s} {'opens':>5s} {'gross':>9s} {'fees':>8s} {'net':>9s} {'ret':>7s}")
for r in sorted(rows, key=lambda x: -x[4]):
    print(f"{r[0]:6s} {r[1]:5d} {r[2]:+9.2f} {r[3]:8.2f} {r[4]:+9.2f} {r[5]:>7s}")
