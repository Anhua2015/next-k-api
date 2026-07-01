#!/usr/bin/env python3
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from env_loader import load_env_oi
load_env_oi()
import pandas as pd
from orb.core.config import OrbConfig
from orb.core.kline_cache import load_klines, session_dates_from_cache
from orb.cta.engine import run_cta_backtest
from orb.cta.registry import CTA_STRATEGIES, cta_config_for_strategy
from tools.cta.research_vnpy_cta import _session_slice

cfg = OrbConfig.from_env()
sym = "INTCUSDT"
dates = [d for d in session_dates_from_cache(sym, cfg) if "2026-02-01" <= d <= "2026-06-30"]
df1 = load_klines(sym, "1m")
df = pd.concat(
    [c for d in dates if not (c := _session_slice(df1, d, cfg)).empty],
    ignore_index=True,
)
meta = CTA_STRATEGIES["king_keltner"]
cases = [
    ("compound+noEOD+5bps (rank脚本)", dict(compound=True, eod_flat=False, slip_bps_entry=5, slip_bps_exit=5)),
    ("compound+noEOD+0bps", dict(compound=True, eod_flat=False, slip_bps_entry=0, slip_bps_exit=0)),
    ("compound+RTH+EOD+5bps", dict(compound=True, eod_flat=True, rth_only=True, exit_hour=15, exit_minute=55, slip_bps_entry=5, slip_bps_exit=5)),
    ("RTH+EOD+复利+5bps+无封顶 (实盘)", dict(compound=True, eod_flat=True, rth_only=True, exit_hour=15, exit_minute=55, slip_bps_entry=5, slip_bps_exit=5, max_notional_usdt=0)),
]
for label, kw in cases:
    out = run_cta_backtest(
        df,
        strategy_fn=meta["fn"],
        orb_cfg=cfg,
        cta_cfg=cta_config_for_strategy("king_keltner", equity_usdt=1000, risk_pct=0.01, **kw),
        warmup=25,
    )
    s = out["summary"]
    net = float(s["net_pnl_usdt"])
    ret = (float(s["equity_end"]) / 1000 - 1) * 100 if kw.get("compound") else net / 10.0
    print(f"{label:35s} net={net:+.0f}U  ret={ret:+.1f}%  opens={s['opens']}")
