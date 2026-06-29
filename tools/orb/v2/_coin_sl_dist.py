import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from env_loader import load_env_oi

load_env_oi()

from orb.core.config import OrbConfig
from orb.core.kline_cache import load_klines
from orb.core.paper import analyze_at_ms
from orb.core.session import session_anchor_ms, session_day_str
from tools.orb.v2.backtest_universe import universe_session_dates
import pandas as pd

sym = "COINUSDT"
base = OrbConfig.from_env()
df5 = load_klines(sym, "5m")
df1d = load_klines(sym, "1d")
dates = [d for d in universe_session_dates([sym], base) if "2026-02-09" <= d <= "2026-06-24"]

for or_m in (5, 10, 15):
    for sl_mode in ("atr_pct", "or_range"):
        cfg = replace(base, or_minutes=or_m, sl_mode=sl_mode, exit_mode="eod")
        dists = []
        or_widths = []
        for d in dates:
            ts = pd.Timestamp(d + " 12:00:00", tz=cfg.session_tz)
            anchor = session_anchor_ms(int(ts.value // 1_000_000), tz=cfg.session_tz, session_open_time=cfg.session_open_time)
            scan = anchor + or_m * 60_000 + 5000
            sig = analyze_at_ms(sym, cfg=cfg, now_ms=scan, bot_equity_usdt=1000, df5=df5)
            b = sig.preplace_arm
            if not b:
                continue
            w = float(b.long_sig.or_width_pct or 0)
            or_widths.append(w)
            for leg in (b.long_sig, b.short_sig):
                e, sl = float(leg.price), float(leg.sl_price)
                dists.append(abs(e - sl) / e * 100)
        if not dists:
            print(f"OR{or_m} {sl_mode}: no arms")
            continue
        print(
            f"OR{or_m} {sl_mode:8} n={len(dists)//2:3d} "
            f"SL dist avg={sum(dists)/len(dists):.3f}% min={min(dists):.3f}% max={max(dists):.3f}% "
            f"OR width avg={sum(or_widths)/len(or_widths):.2f}%"
        )
