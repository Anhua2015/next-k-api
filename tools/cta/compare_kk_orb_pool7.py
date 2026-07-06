#!/usr/bin/env python3
"""Side-by-side KK vs ORB on pool7 (same window, honest ORB fills)."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from env_loader import load_env_oi

load_env_oi()

import numpy as np
import pandas as pd
from binance_fapi import fetch_klines_forward, klines_to_df
from orb.core.config import OrbConfig
from orb.core.fees import trade_fee_usdt
from orb.core.kline_cache import load_klines, norm_symbol, session_dates_from_cache
from orb.core.indicators import daily_atr_asof
from orb.core.resolve import pnl_r, pnl_usdt, resolve_forward
from orb.core.signals import classify_signal, compute_position_notional
from orb.core.symbols import parse_symbol_list
from orb.cta.engine import run_cta_backtest
from orb.cta.execution import entry_fill_px, market_exit_fill_px, stop_exit_fill_px
from orb.cta.registry import CTA_STRATEGIES, cta_config_for_strategy
from orb.gtl.engine import compute_gtl_dataframe
from orb.gtl.resample import resample_ohlcv
from orb.kk.paths import resolve_symbols_path
from tools.cta.research_gtl_downstream import (
    _daily_bars,
    _gtl_bias,
    _session_anchors,
    backtest_orb,
)
from tools.cta.research_gtl_vnpy import _load_symbol_df
from tools.cta.research_vnpy_cta import _session_slice
from tools.cta.validate_gtl import _load

POOL7 = ["INTC", "SOXL", "HOOD", "CRCL", "COIN", "SNDK", "MSTR"]
KK_LIVE = dict(
    compound=True,
    rth_only=True,
    eod_flat=True,
    exit_hour=15,
    exit_minute=55,
    slip_bps_entry=5.0,
    slip_bps_exit=5.0,
    no_entry_after_hour=12,
    no_entry_after_minute=0,
    max_notional_usdt=0.0,
)


def _load_range(sym: str, fetch_lo: str, fetch_hi: str, cfg: OrbConfig) -> pd.DataFrame:
    lo_ms = int(pd.Timestamp(fetch_lo, tz="UTC").value // 1_000_000)
    hi_ms = int(pd.Timestamp(fetch_hi, tz="UTC").value // 1_000_000)
    raw = _load_symbol_df(sym, fetch_lo, fetch_hi, cfg)
    last_ms = int(raw.iloc[-1]["open_time"]) if not raw.empty else 0
    if last_ms < hi_ms - 86400_000:
        fetched = klines_to_df(fetch_klines_forward(sym, "1m", lo_ms, hi_ms))
        if not fetched.empty:
            raw = (
                pd.concat([raw, fetched], ignore_index=True)
                .drop_duplicates(subset=["open_time"], keep="last")
                .sort_values("open_time")
                .reset_index(drop=True)
            )
    if raw.empty:
        raw = klines_to_df(fetch_klines_forward(sym, "1m", lo_ms, hi_ms))
    return raw


def _wait_1m_fill(
    df_1m: pd.DataFrame,
    signal_ms: int,
    side: str,
    trigger: float,
    session_close_ms: int,
    slip_bps: float,
    bar_step_ms: int,
) -> Tuple[Optional[int], Optional[float]]:
    """Fill after 5m signal bar closes; first 1m touch of stop entry."""
    start_ms = int(signal_ms) + int(bar_step_ms)
    sub = df_1m[(df_1m["open_time"] >= start_ms) & (df_1m["open_time"] < session_close_ms)]
    side_u = str(side).upper()
    sgn = 1 if side_u == "LONG" else -1
    for _, row in sub.iterrows():
        h, l = float(row["high"]), float(row["low"])
        if side_u == "LONG" and h >= trigger:
            return int(row["open_time"]), entry_fill_px(1, trigger, slip_bps)
        if side_u == "SHORT" and l <= trigger:
            return int(row["open_time"]), entry_fill_px(-1, trigger, slip_bps)
    return None, None


def _exit_px_with_slip(side: str, outcome: str, raw_exit: float, bar_open: float, slip_bps: float) -> float:
    side_i = 1 if str(side).upper() == "LONG" else -1
    if outcome in ("loss", "win") and str(outcome) != "eod":
        return stop_exit_fill_px(side_i, raw_exit, bar_open=bar_open, slip_bps=slip_bps)
    return market_exit_fill_px(side_i, raw_exit, slip_bps)


def _bar_minutes_et(ms: int, tz: str) -> int:
    ts = pd.Timestamp(int(ms), unit="ms", tz=tz)
    return int(ts.hour) * 60 + int(ts.minute)


def _in_entry_window(
    ms: int,
    tz: str,
    *,
    start_hm: Optional[Tuple[int, int]] = None,
    end_hm: Optional[Tuple[int, int]] = None,
) -> bool:
    if start_hm is None and end_hm is None:
        return True
    cur = _bar_minutes_et(ms, tz)
    if start_hm is not None:
        sh, sm = start_hm
        if cur < sh * 60 + sm:
            return False
    if end_hm is not None:
        eh, em = end_hm
        if cur >= eh * 60 + em:
            return False
    return True


def backtest_orb_honest(
    df_1m: pd.DataFrame,
    df_5m: pd.DataFrame,
    gtl: pd.DataFrame,
    df_30m: pd.DataFrame,
    cfg: OrbConfig,
    *,
    equity: float,
    slip_bps: float,
    gtl_mode: str,
    entry_start_hm: Optional[Tuple[int, int]] = None,
    entry_end_hm: Optional[Tuple[int, int]] = None,
) -> Dict[str, Any]:
    """gtl_mode: none | same (require same dir) | block (skip opposite only)."""
    from orb.core.session import is_trading_session, session_close_ms

    daily = _daily_bars(df_1m, cfg)
    bar_step = cfg.bar_step_ms()
    trades: List[Dict[str, Any]] = []
    filtered = 0
    no_fill = 0
    time_skipped = 0

    for anchor in _session_anchors(df_1m, cfg):
        close_ms = session_close_ms(anchor, tz=cfg.session_tz, session_close_time=cfg.session_close_time)
        if close_ms is None:
            continue
        bias = _gtl_bias(gtl, df_30m, anchor) if gtl_mode != "none" else "any"
        session_traded = False
        sess_bars = df_5m[(df_5m["open_time"] >= anchor) & (df_5m["open_time"] < close_ms)]
        for _, bar in sess_bars.iterrows():
            ms = int(bar["open_time"])
            if not is_trading_session(
                ms,
                tz=cfg.session_tz,
                session_open_time=cfg.session_open_time,
                session_close_time=cfg.session_close_time,
                market=cfg.market,
            ):
                continue
            atr = daily_atr_asof(daily, ms, period=cfg.atr_period, tz=cfg.session_tz)
            sig = classify_signal(
                "SYM",
                df_1m,
                asof_open_ms=ms,
                cfg=cfg,
                session_traded=session_traded,
                daily_atr=atr,
                daily_df=daily,
                bot_equity_usdt=equity,
            )
            if sig.side not in ("LONG", "SHORT"):
                continue
            if not _in_entry_window(
                ms,
                cfg.session_tz,
                start_hm=entry_start_hm,
                end_hm=entry_end_hm,
            ):
                time_skipped += 1
                continue
            if gtl_mode == "same":
                if sig.side == "LONG" and bias not in ("long", "any"):
                    filtered += 1
                    continue
                if sig.side == "SHORT" and bias not in ("short", "any"):
                    filtered += 1
                    continue
            elif gtl_mode == "block":
                if sig.side == "LONG" and bias == "short":
                    filtered += 1
                    continue
                if sig.side == "SHORT" and bias == "long":
                    filtered += 1
                    continue
            if sig.sl_price is None:
                continue
            trigger = float(sig.price)
            fill_ms, fill_px = _wait_1m_fill(df_1m, ms, sig.side, trigger, close_ms, slip_bps, bar_step)
            if fill_ms is None or fill_px is None:
                no_fill += 1
                continue
            outcome, exit_px, note, bars_seen, exit_bo = resolve_forward(
                df_1m,
                entry=float(fill_px),
                entry_bar_open_ms=fill_ms,
                side=sig.side,
                sl=float(sig.sl_price),
                tp=sig.tp_price,
                hist_end_ms=int(df_1m["open_time"].max()),
                bar_step_ms=60_000,
                cfg=cfg,
            )
            if outcome is None:
                continue
            bar_open = float(fill_px)
            if exit_bo is not None:
                m = df_1m["open_time"] == int(exit_bo)
                if m.any():
                    bar_open = float(df_1m.loc[m, "open"].iloc[0])
            exit_adj = _exit_px_with_slip(sig.side, str(outcome), float(exit_px), bar_open, slip_bps)
            sl = float(sig.sl_price)
            notion = compute_position_notional(
                entry=float(fill_px), sl=sl, cfg=cfg, bot_equity_usdt=equity
            )
            if notion <= 0:
                continue
            gross = pnl_usdt(sig.side, float(fill_px), exit_adj, notion)
            fee = trade_fee_usdt(
                notional_usdt=notion,
                entry_mode="breakout",
                maker_bps=cfg.fee_maker_bps,
                taker_bps=cfg.fee_taker_bps,
            )
            net = gross - fee
            r_pnl = pnl_r(sig.side, float(fill_px), exit_adj, sl)
            session_traded = True
            trades.append({"net": net, "r": r_pnl, "outcome": outcome})
            break

    nets = [t["net"] for t in trades]
    rs = [t["r"] for t in trades]
    wins = sum(1 for x in nets if x > 0)
    return {
        "trades": len(trades),
        "filtered": filtered,
        "time_skipped": time_skipped,
        "no_fill": no_fill,
        "win_rate": round(wins / len(nets), 3) if nets else 0.0,
        "sum_usd": round(float(sum(nets)), 2) if nets else 0.0,
        "avg_usd": round(float(np.mean(nets)), 2) if nets else 0.0,
        "sum_r": round(float(sum(rs)), 3) if rs else 0.0,
    }


def run_kk(sym: str, dates: List[str], cfg: OrbConfig, equity: float) -> Dict[str, Any]:
    sym = norm_symbol(sym)
    df1 = load_klines(sym, "1m")
    if df1.empty or not dates:
        return {"error": "no_data"}
    chunks = [_session_slice(df1, d, cfg) for d in dates]
    df = pd.concat([c for c in chunks if not c.empty], ignore_index=True).sort_values("open_time")
    if df.empty:
        return {"error": "no_bars"}
    meta = CTA_STRATEGIES["king_keltner"]
    out = run_cta_backtest(
        df,
        strategy_fn=meta["fn"],
        orb_cfg=cfg,
        cta_cfg=cta_config_for_strategy(
            "king_keltner", equity_usdt=equity, risk_pct=0.01, **KK_LIVE
        ),
        warmup=25,
    )
    s = out["summary"]
    closes = [t for t in out["trades"] if t["event"] == "close"]
    wins = sum(1 for t in closes if float(t["pnl_usdt"]) > 0)
    fees = sum(float(t.get("fee_usdt") or 0) for t in closes)
    return {
        "trades": int(s["opens"]),
        "win_rate": round(wins / len(closes), 3) if closes else 0.0,
        "sum_usd": round(float(s["net_pnl_usdt"]), 2),
        "fees": round(fees, 2),
        "equity_end": round(float(s["equity_end"]), 2),
        "ret_pct": round(100.0 * (float(s["equity_end"]) - equity) / equity, 1),
    }


def analyze(sym: str, lo: str, hi: str, cfg: OrbConfig, equity: float, slip_bps: float) -> Dict[str, Any]:
    label = sym.replace("USDT", "")
    fetch_lo = (pd.Timestamp(lo) - pd.Timedelta(days=45)).strftime("%Y-%m-%d")
    df_1m = _load_range(sym, fetch_lo, hi, cfg)
    if df_1m.empty:
        df_1m = _load(sym, lo, hi)
    if df_1m.empty:
        return {"symbol": label, "error": "no_data"}

    lo_ms = int(pd.Timestamp(lo, tz=cfg.session_tz).value // 1_000_000)
    hi_ms = int(
        (pd.Timestamp(hi, tz=cfg.session_tz) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)).value // 1_000_000
    )
    df_1m = df_1m[(df_1m["open_time"] >= lo_ms - 30 * 86400 * 1000) & (df_1m["open_time"] <= hi_ms)].copy()
    df_5m = resample_ohlcv(df_1m, "5m")
    df_30m = resample_ohlcv(df_1m, "30m")
    gtl = compute_gtl_dataframe(df_30m, lookback=23, vol_window=500)

    orb_cfg = OrbConfig.from_env()
    orb_cfg.macro_filter = True
    orb_cfg.resolve_at_session_close = True

    dates = [d for d in session_dates_from_cache(sym, cfg) if lo <= d <= hi]

    kk = run_kk(sym, dates, cfg, equity)
    orb_ideal = backtest_orb(df_1m, df_5m, gtl, df_30m, orb_cfg, gtl_filter=False)
    orb_ideal_gtl = backtest_orb(df_1m, df_5m, gtl, df_30m, orb_cfg, gtl_filter=True)
    orb_honest = backtest_orb_honest(
        df_1m, df_5m, gtl, df_30m, orb_cfg, equity=equity, slip_bps=slip_bps, gtl_mode="none"
    )
    orb_honest_block = backtest_orb_honest(
        df_1m, df_5m, gtl, df_30m, orb_cfg, equity=equity, slip_bps=slip_bps, gtl_mode="block"
    )
    # ideal R → approx USD (1R = risk_pct * equity)
    r_usd = equity * 0.01
    return {
        "symbol": label,
        "kk": kk,
        "orb_ideal_r": orb_ideal.get("sum_r", 0),
        "orb_ideal_usd_approx": round(float(orb_ideal.get("sum_r", 0)) * r_usd, 2),
        "orb_ideal_gtl_r": orb_ideal_gtl.get("sum_r", 0),
        "orb_honest": orb_honest,
        "orb_honest_block": orb_honest_block,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="KK vs ORB pool7 comparison")
    ap.add_argument("--from-date", default="2026-02-01")
    ap.add_argument("--to-date", default="2026-06-30")
    ap.add_argument("--equity", type=float, default=1000.0)
    ap.add_argument("--slip-bps", type=float, default=5.0)
    args = ap.parse_args()

    cfg = OrbConfig.from_env()
    syms = [norm_symbol(s) for s in parse_symbol_list(Path(resolve_symbols_path()).read_text(encoding="utf-8"))]
    if not syms:
        syms = [norm_symbol(s) for s in POOL7]

    lo, hi = args.from_date, args.to_date
    eq, slip = float(args.equity), float(args.slip_bps)

    print(f"=== KK vs ORB | pool7 | {lo}..{hi} ===")
    print(f"KK: RTH+EOD 15:55, no entry after 12:00 ET, {slip}bps slip, compound, {eq}U/symbol")
    print(f"ORB honest: 1m touch fill after 5m signal, {slip}bps slip, fees, macro_filter=on")
    print(f"ORB ideal: research upstream (instant OR fill, no slip) — reference only\n")

    t0 = time.time()
    rows = []
    for sym in syms:
        print(f"  {sym.replace('USDT',''):5s} ...", flush=True, end=" ")
        rows.append(analyze(sym, lo, hi, cfg, eq, slip))
        r = rows[-1]
        if r.get("error"):
            print(r["error"], flush=True)
        else:
            print(
                f"KK {r['kk'].get('sum_usd', 0):+.0f}U | "
                f"ORB honest {r['orb_honest'].get('sum_usd', 0):+.0f}U | "
                f"ideal {r['orb_ideal_r']:+.0f}R",
                flush=True,
            )

    print(f"\n{'sym':6s} {'KK net':>9s} {'KK ret%':>7s} {'ORB$ hon':>10s} {'ORB$ blk':>10s} {'ORB idealR':>10s} {'~ideal$':>9s}")
    print("-" * 72)
    tot_kk = tot_hon = tot_blk = tot_ir = tot_iu = 0.0
    for r in rows:
        if r.get("error"):
            print(f"{r['symbol']:6s} ERROR")
            continue
        kk = r["kk"]
        hon = r["orb_honest"]
        blk = r["orb_honest_block"]
        print(
            f"{r['symbol']:6s} {kk.get('sum_usd', 0):+9.2f} {kk.get('ret_pct', 0):+6.1f}% "
            f"{hon.get('sum_usd', 0):+10.2f} {blk.get('sum_usd', 0):+10.2f} "
            f"{r['orb_ideal_r']:+10.1f} {r['orb_ideal_usd_approx']:+9.0f}"
        )
        tot_kk += float(kk.get("sum_usd") or 0)
        tot_hon += float(hon.get("sum_usd") or 0)
        tot_blk += float(blk.get("sum_usd") or 0)
        tot_ir += float(r["orb_ideal_r"] or 0)
        tot_iu += float(r["orb_ideal_usd_approx"] or 0)

    print("-" * 72)
    print(
        f"{'TOTAL':6s} {tot_kk:+9.2f} {'':>7s} {tot_hon:+10.2f} {tot_blk:+10.2f} "
        f"{tot_ir:+10.1f} {tot_iu:+9.0f}"
    )
    print(f"\n({time.time()-t0:.0f}s)")
    print("\nNotes:")
    print("  ~ideal$ = ideal R × 1% × equity (approx; instant fill, no slip)")
    print("  ORB$ blk = honest ORB + GTL block-opposite only (skip trades vs 30m GTL bias)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
