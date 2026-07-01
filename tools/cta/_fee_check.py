import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from env_loader import load_env_oi

load_env_oi()
from orb.core.config import OrbConfig
from orb.core.kline_cache import load_klines, norm_symbol
from orb.cta.engine import CtaBacktestConfig, run_cta_backtest
from orb.cta.registry import CTA_STRATEGIES, cta_config_for_strategy
from tools.cta.research_vnpy_cta import _session_slice
from orb.core.kline_cache import session_dates_from_cache
import pandas as pd

cfg = OrbConfig.from_env()
sym = norm_symbol("COIN")
dates = [d for d in session_dates_from_cache(sym, cfg) if "2026-02-09" <= d <= "2026-06-30"]
df1 = load_klines(sym, "1m")
chunks = [_session_slice(df1, d, cfg) for d in dates]
df = pd.concat([c for c in chunks if not c.empty], ignore_index=True).sort_values("open_time")

print("COIN fee breakdown (maker=2 taker=4 config, entry_mode=signal -> open taker 4 + close taker 4 = 8bps RT)")
print("-" * 72)
for key in ["king_keltner", "boll_channel", "double_ma", "turtle"]:
    meta = CTA_STRATEGIES[key]
    cta_cfg = cta_config_for_strategy(
        key,
        equity_usdt=1000,
        risk_pct=0.01,
        eod_flat=bool(meta.get("eod_flat", False)),
        maker_bps=2.0,
        taker_bps=4.0,
    )
    out = run_cta_backtest(
        df,
        strategy_fn=meta["fn"],
        orb_cfg=cfg,
        cta_cfg=cta_cfg,
        warmup=int(meta.get("warmup") or 30),
    )
    s = out["summary"]
    closes = [t for t in out["trades"] if t["event"] == "close"]
    gross = sum(float(t["pnl_usdt_gross"]) for t in closes)
    fees = sum(float(t["fee_usdt"]) for t in closes)
    net = sum(float(t["pnl_usdt"]) for t in closes)
    print(f"{key:14s} opens={s['opens']:4d}  gross={gross:+9.2f}U  fees={fees:7.2f}U  net={net:+9.2f}U")
    if closes:
        print(f"               fee/trade={fees/len(closes):.4f}U  fee% of gross={100*fees/abs(gross) if gross else 0:.1f}%")

print()
print("Compound ON vs OFF (king_keltner COIN):")
for compound in (True, False):
    out = run_cta_backtest(
        df,
        strategy_fn=CTA_STRATEGIES["king_keltner"]["fn"],
        orb_cfg=cfg,
        cta_cfg=cta_config_for_strategy(
            "king_keltner", equity_usdt=1000, risk_pct=0.01, compound=compound
        ),
        warmup=25,
    )
    closes = [t for t in out["trades"] if t["event"] == "close"]
    notions = [float(t["notional_usdt"]) for t in out["trades"] if t["event"] == "open"]
    s = out["summary"]
    print(
        f"  compound={compound}: net={s['net_pnl_usdt']:+.2f}U end={s['equity_end']} "
        f"avg_notional={sum(notions)/len(notions):.0f} max_notional={max(notions):.0f}"
    )
