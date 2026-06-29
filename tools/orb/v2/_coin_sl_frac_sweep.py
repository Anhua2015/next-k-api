#!/usr/bin/env python3
import os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from env_loader import load_env_oi
load_env_oi()
os.environ["ORB_V2_ROBOT_RESET_CAP"] = "0"
from orb.ml.gate import LiveGateConfig, gate_with_ml_bypass
from orb.v2.paths import resolve_gate_config_path
from tools.orb.ml.eval_live_gate import _ml_cfg
from tools.orb.v2.backtest_universe import filter_backtest_sessions_with_atr, universe_session_dates
from tools.orb.v2.batch_symbol_sim import _run_one

SYM = "COINUSDT"
LO, HI = "2026-02-09", "2026-06-24"
gate = gate_with_ml_bypass(LiveGateConfig.from_json(Path(resolve_gate_config_path())))
probe = _ml_cfg(compound_per_symbol=True, respect_env_filters=False)
dates = filter_backtest_sessions_with_atr(
    [d for d in universe_session_dates([SYM], probe) if LO <= d <= HI], [SYM], probe
)
print("COIN OR10 tw0 risk=1% | stop = ATR x fraction")
print("frac | net_U | opens | win%")
for frac in [0.03, 0.04, 0.05, 0.06, 0.07, 0.08, 0.10, 0.12, 0.15]:
    cfg = _ml_cfg(compound_per_symbol=True, respect_env_filters=False)
    cfg.or_minutes = 10
    cfg.risk_pct = 0.01
    cfg.trade_window_minutes = 0
    cfg.macro_filter = False
    cfg.atr_sl_fraction = frac
    row = _run_one(
        SYM, dates, gate=gate, ranker=None, cfg=cfg,
        robot_equity=1000.0, fee_bps=4.0, entry_fill="preplace_stop",
    )
    print(f"{frac:.2f} | {row['net_pnl_usdt']:+.0f} | {row['opens']} | {row['win_rate']:.0f}")
