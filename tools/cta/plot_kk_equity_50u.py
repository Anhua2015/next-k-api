#!/usr/bin/env python3
"""KK 50U 池权益曲线 — vnpy 官方 BacktestingEngine（与 simulate_kk_50u.py 同引擎）。"""
from __future__ import annotations

import sys
from datetime import timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from env_loader import load_env_oi

load_env_oi()

import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from orb.core.config import OrbConfig  # noqa: E402
from orb.core.kline_cache import norm_symbol, session_dates_from_cache  # noqa: E402
from orb.core.symbols import parse_symbol_list  # noqa: E402
from orb.kk.config import KKConfig  # noqa: E402
from orb.kk.paths import resolve_symbols_path  # noqa: E402
from orb.kk.vnpy.backtest import klines_df_to_bars, run_kk_vnpy_backtest  # noqa: E402
from orb.kk.vnpy.binance_gateway import kk_vt_symbol  # noqa: E402
from tools.cta.simulate_kk_vnpy_50u import (  # noqa: E402
    _load_symbol_df,
    _range_engine_dates,
    _range_replay_end,
)

EQUITY = 50.0
LO, HI = "2026-02-01", "2026-06-30"
OUT = ROOT / "output" / "kk" / "kk_equity_50u.png"

COLORS = {
    "INTC": "#1f77b4",
    "SOXL": "#ff7f0e",
    "HOOD": "#2ca02c",
    "CRCL": "#d62728",
    "COIN": "#9467bd",
    "SNDK": "#8c564b",
    "MSTR": "#e377c2",
    "POOL": "#111111",
}


def equity_from_daily(daily_df: pd.DataFrame, *, start_equity: float, tz: str) -> pd.Series:
    if daily_df is None or daily_df.empty:
        t0 = pd.Timestamp(f"{LO} 09:30:00", tz=tz)
        return pd.Series([start_equity], index=[t0], dtype=float)
    if "balance" in daily_df.columns:
        bal = daily_df["balance"].astype(float)
    else:
        bal = daily_df["net_pnl"].astype(float).cumsum() + float(start_equity)
    idx = pd.DatetimeIndex([pd.Timestamp(str(d), tz=tz) for d in daily_df.index])
    return pd.Series(bal.values, index=idx, dtype=float)


def pool_equity_series(curves: dict[str, pd.Series]) -> pd.Series:
    if not curves:
        return pd.Series(dtype=float)
    merged = pd.concat(curves, axis=1, sort=True).ffill().bfill()
    return merged.sum(axis=1)


def run_backtests(cfg: OrbConfig, kk: KKConfig) -> dict[str, pd.Series]:
    symbols = [
        norm_symbol(s)
        for s in parse_symbol_list(Path(resolve_symbols_path()).read_text(encoding="utf-8"))
    ]
    fetch_start, engine_start_s, replay_start = _range_engine_dates(LO, cfg)
    replay_end = _range_replay_end(HI, cfg)
    engine_start = pd.Timestamp(engine_start_s, tz=cfg.session_tz).to_pydatetime().replace(tzinfo=timezone.utc)
    curves: dict[str, pd.Series] = {}

    for sym in symbols:
        label = sym.replace("USDT", "")
        dates = [d for d in session_dates_from_cache(sym, cfg) if LO <= d <= HI]
        if not dates:
            continue
        df = _load_symbol_df(sym, fetch_start, HI, cfg)
        if df.empty:
            continue
        px = float(df.iloc[-1]["close"])
        bars = klines_df_to_bars(df, sym, vt_symbol=kk_vt_symbol(sym))
        out = run_kk_vnpy_backtest(
            sym,
            bars,
            kk=kk,
            equity_usdt=EQUITY,
            start=engine_start,
            end=replay_end,
            replay_start=replay_start,
            replay_end=replay_end,
            price=px,
            quiet=True,
            orb_cfg=cfg,
        )
        if out.get("error"):
            continue
        curves[label] = equity_from_daily(out.get("daily_df"), start_equity=EQUITY, tz=cfg.session_tz)
        end_w = float(out.get("end_wallet") or EQUITY)
        print(f"  {label:5s} end={end_w:8.2f}U (vnpy)", flush=True)
    return curves


def plot_equity(curves: dict[str, pd.Series], *, out: Path, tz: str) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    pool = pool_equity_series(curves)
    pool_start = EQUITY * len(curves)
    pool_end = float(pool.iloc[-1]) if not pool.empty else pool_start

    fig, ax = plt.subplots(figsize=(13, 7), dpi=140)
    for label in sorted(curves.keys()):
        s = curves[label]
        ax.plot(
            s.index,
            s.values,
            label=f"{label} ({float(s.iloc[-1]):.0f}U)",
            color=COLORS.get(label),
            linewidth=1.4,
            alpha=0.85,
        )
    ax.plot(
        pool.index,
        pool.values,
        label=f"POOL ({pool_end:.0f}U)",
        color=COLORS["POOL"],
        linewidth=2.8,
    )
    ax.axhline(pool_start, color="#888888", linestyle="--", linewidth=1.0, alpha=0.7)
    ax.set_title(
        "King Keltner | vnpy BacktestingEngine | 50U/symbol | Feb–Jun 2026\n"
        "RTH+NYSE EOD | no entry after 12:00 ET | compound",
        fontsize=12,
        pad=12,
    )
    ax.set_ylabel("Equity (USDT)")
    ax.grid(True, alpha=0.25)
    ax.legend(loc="upper left", fontsize=9, ncol=2)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d", tz=pd.Timestamp.now(tz=tz).tz))
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)
    print(f"\nSaved: {out}", flush=True)


def main() -> None:
    cfg = OrbConfig.from_env()
    kk = KKConfig.from_env()
    print(f"=== KK equity plot (vnpy) | {LO}..{HI} | {EQUITY}U ===", flush=True)
    curves = run_backtests(cfg, kk)
    if not curves:
        print("No curves.")
        return
    plot_equity(curves, out=OUT, tz=cfg.session_tz)


if __name__ == "__main__":
    main()
