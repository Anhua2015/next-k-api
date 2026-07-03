#!/usr/bin/env python3
"""King Keltner 单 session 回测（拉 Binance 1m K 线，实盘口径）。"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from env_loader import load_env_oi

load_env_oi()

import pandas as pd  # noqa: E402

from binance_fapi import fetch_klines_forward, klines_to_df  # noqa: E402
from orb.core.config import OrbConfig  # noqa: E402
from orb.core.kline_cache import norm_symbol  # noqa: E402
from orb.core.session import session_anchor_ms, session_close_ms  # noqa: E402
from orb.core.symbols import parse_symbol_list  # noqa: E402
from orb.cta.engine import run_cta_backtest  # noqa: E402
from orb.cta.registry import CTA_STRATEGIES, cta_config_for_strategy  # noqa: E402
from orb.kk.paths import resolve_symbols_path  # noqa: E402

KK_CTA_KW = dict(
    compound=True,
    rth_only=True,
    eod_flat=True,
    exit_hour=15,
    exit_minute=55,
    slip_bps_entry=5.0,
    slip_bps_exit=5.0,
    max_notional_usdt=0.0,
)


def _session_slice(df: pd.DataFrame, session_date: str, cfg: OrbConfig) -> pd.DataFrame:
    tz = cfg.session_tz
    ts = pd.Timestamp(f"{session_date} 12:00:00", tz=tz)
    anchor = session_anchor_ms(int(ts.value // 1_000_000), tz=tz, session_open_time=cfg.session_open_time)
    close = session_close_ms(anchor, tz=tz, session_close_time=cfg.session_close_time)
    if close is None:
        close = anchor + 6 * 60 * 60 * 1000
    return df[(df["open_time"] >= anchor) & (df["open_time"] <= close)].copy()


def _fetch_range(sym: str, from_date: str, to_date: str, cfg: OrbConfig) -> pd.DataFrame:
    tz = cfg.session_tz
    lo = pd.Timestamp(from_date.strip(), tz=tz)
    hi = pd.Timestamp(to_date.strip(), tz=tz) + pd.Timedelta(days=1) - pd.Timedelta(milliseconds=1)
    start_ms = int(lo.value // 1_000_000)
    end_ms = int(hi.value // 1_000_000)
    rows = fetch_klines_forward(sym, "1m", start_ms, end_ms)
    df = klines_to_df(rows)
    if df.empty:
        return df
    return df.drop_duplicates(subset=["open_time"], keep="last").sort_values("open_time").reset_index(drop=True)


def _fmt_ms(ms: int) -> str:
    et = pd.Timestamp(int(ms), unit="ms", tz="America/New_York")
    cn = et.tz_convert("Asia/Shanghai")
    return f"{et.strftime('%H:%M:%S')} ET / {cn.strftime('%H:%M:%S')} CN"


def _session_day(ms: int, cfg: OrbConfig) -> str:
    tz = cfg.session_tz
    ts = pd.Timestamp(int(ms), unit="ms", tz=tz)
    anchor = session_anchor_ms(int(ts.value // 1_000_000), tz=tz, session_open_time=cfg.session_open_time)
    return pd.Timestamp(anchor, unit="ms", tz=tz).strftime("%Y-%m-%d")


def main() -> None:
    ap = argparse.ArgumentParser(description="KK single-session backtest")
    ap.add_argument("--session", default="2026-07-02", help="RTH session date (America/New_York)")
    ap.add_argument("--warmup-from", default="2026-06-25", help="fetch start for Keltner warmup")
    ap.add_argument("--equity", type=float, default=14.0, help="starting equity per symbol bot")
    ap.add_argument("--symbols", default="", help="comma list; default KK pool")
    ap.add_argument("--out-csv", default="", help="write all trades to CSV path")
    ap.add_argument(
        "--window-et",
        default="",
        help="optional ET time filter e.g. 12:35-15:57 (America/New_York)",
    )
    args = ap.parse_args()

    cfg = OrbConfig.from_env()
    meta = CTA_STRATEGIES["king_keltner"]
    if args.symbols.strip():
        symbols = [norm_symbol(s) for s in args.symbols.split(",") if s.strip()]
    else:
        symbols = [norm_symbol(s) for s in parse_symbol_list(Path(resolve_symbols_path()).read_text(encoding="utf-8"))]

    session = args.session.strip()
    warmup_from = args.warmup_from.strip()
    equity = float(args.equity)
    win_lo_ms = win_hi_ms = 0
    if args.window_et.strip():
        lo_s, hi_s = [x.strip() for x in args.window_et.split("-", 1)]
        win_lo_ms = int(pd.Timestamp(f"{session} {lo_s}", tz=cfg.session_tz).value // 1_000_000)
        win_hi_ms = int(pd.Timestamp(f"{session} {hi_s}", tz=cfg.session_tz).value // 1_000_000)

    print(f"=== KK session backtest | session={session} | equity={equity}U | live-aligned ===")
    if args.window_et.strip():
        print(f"window ET {args.window_et.strip()}")
    print(f"fetch {warmup_from} .. {session} (warmup + target)\n")

    total_net = 0.0
    total_closes = 0
    total_wins = 0
    csv_rows: list[dict] = []

    for sym in symbols:
        label = sym.replace("USDT", "")
        print(f"--- {label} ---")
        try:
            df_all = _fetch_range(sym, warmup_from, session, cfg)
        except Exception as exc:
            print(f"  FETCH FAIL: {exc}\n")
            continue
        if df_all.empty:
            print("  no klines\n")
            continue

        sl = _session_slice(df_all, session, cfg)
        if sl.empty:
            print(f"  no bars for session {session}\n")
            continue

        out = run_cta_backtest(
            df_all,
            strategy_fn=meta["fn"],
            orb_cfg=cfg,
            cta_cfg=cta_config_for_strategy("king_keltner", equity_usdt=equity, risk_pct=0.01, **KK_CTA_KW),
            warmup=int(meta.get("warmup") or 25),
        )
        trades = out["trades"]
        day_trades = [t for t in trades if _session_day(int(t["ms"]), cfg) == session]
        if win_lo_ms and win_hi_ms:
            day_trades = [t for t in day_trades if win_lo_ms <= int(t["ms"]) <= win_hi_ms]
        opens = [t for t in day_trades if t["event"] == "open"]
        closes = [t for t in day_trades if t["event"] == "close"]
        net = sum(float(t["pnl_usdt"]) for t in closes)
        wins = sum(1 for t in closes if float(t["pnl_usdt"]) > 0)
        total_net += net
        total_closes += len(closes)
        total_wins += wins

        print(f"  bars={len(sl)} opens={len(opens)} closes={len(closes)} net={net:+.4f}U win={wins}/{len(closes)}")
        for t in day_trades:
            ms = int(t["ms"])
            et = pd.Timestamp(ms, unit="ms", tz="America/New_York")
            cn = et.tz_convert("Asia/Shanghai")
            ev = t["event"]
            if args.out_csv.strip():
                row = {
                    "symbol": label,
                    "event": ev,
                    "time_et": et.strftime("%Y-%m-%d %H:%M:%S"),
                    "time_cn": cn.strftime("%Y-%m-%d %H:%M:%S"),
                    "side": t.get("side", ""),
                    "entry": t.get("entry", ""),
                    "exit": t.get("exit", ""),
                    "notional_usdt": t.get("notional_usdt", ""),
                    "outcome": t.get("outcome", ""),
                    "pnl_usdt_gross": t.get("pnl_usdt_gross", ""),
                    "fee_usdt": t.get("fee_usdt", ""),
                    "pnl_usdt": t.get("pnl_usdt", ""),
                    "ms": ms,
                }
                csv_rows.append(row)
            if ev == "open":
                print(
                    f"    OPEN  {_fmt_ms(ms)} {t['side']:5s} "
                    f"entry={t['entry']:.4f} notional={t['notional_usdt']:.2f}U"
                )
            else:
                print(
                    f"    CLOSE {_fmt_ms(ms)} {t['side']:5s} "
                    f"outcome={t.get('outcome','?')} pnl={float(t['pnl_usdt']):+.4f}U "
                    f"(gross={float(t['pnl_usdt_gross']):+.4f} fee={float(t['fee_usdt']):.4f})"
                )
        print()

    if args.out_csv.strip():
        out_path = Path(args.out_csv.strip())
        out_path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(csv_rows).sort_values("ms").drop(columns=["ms"]).to_csv(out_path, index=False)
        print(f"CSV -> {out_path.resolve()}\n")

    wr = 100.0 * total_wins / total_closes if total_closes else 0.0
    print("=== POOL TOTAL ===")
    print(f"closes={total_closes} wins={total_wins} win_rate={wr:.1f}% net_pnl={total_net:+.4f}U")


if __name__ == "__main__":
    main()
