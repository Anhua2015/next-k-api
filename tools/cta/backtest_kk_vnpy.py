#!/usr/bin/env python3
"""King Keltner 单 session 回测 — vnpy 官方 BacktestingEngine + KingKeltnerKkStrategy。"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from env_loader import load_env_oi

load_env_oi()

import pandas as pd  # noqa: E402

from binance_fapi import fetch_klines_forward, klines_to_df  # noqa: E402
from orb.core.config import OrbConfig  # noqa: E402
from orb.core.kline_cache import norm_symbol  # noqa: E402
from orb.core.session import session_anchor_ms  # noqa: E402
from orb.core.symbols import parse_symbol_list  # noqa: E402
from orb.kk.config import KKConfig  # noqa: E402
from orb.kk.paths import resolve_symbols_path  # noqa: E402
from orb.kk.vnpy.backtest import (  # noqa: E402
    klines_df_to_bars,
    run_kk_vnpy_backtest,
    session_bounds_for_date,
    trades_to_rows,
)
from orb.kk.vnpy.binance_gateway import kk_vt_symbol  # noqa: E402


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


def _session_bounds(session_date: str, cfg: OrbConfig) -> tuple[datetime, datetime, int, int, str]:
    return session_bounds_for_date(session_date, cfg)


def _session_day(ms: int, cfg: OrbConfig) -> str:
    tz = cfg.session_tz
    ts = pd.Timestamp(int(ms), unit="ms", tz=tz)
    anchor = session_anchor_ms(int(ts.value // 1_000_000), tz=tz, session_open_time=cfg.session_open_time)
    return pd.Timestamp(anchor, unit="ms", tz=tz).strftime("%Y-%m-%d")


def _fmt_ms(ms: int) -> str:
    et = pd.Timestamp(int(ms), unit="ms", tz="America/New_York")
    cn = et.tz_convert("Asia/Shanghai")
    return f"{et.strftime('%H:%M:%S')} ET / {cn.strftime('%H:%M:%S')} CN"


def _fetch_start_for_load_bar(session_date: str, warmup_from: str, cfg: OrbConfig) -> tuple[str, str]:
    """返回 (engine.start 日期, 数据抓取起始日)；DB 需比 engine.start 早 ≥10 日供 load_bar(10)。"""
    tz = cfg.session_tz
    sess = pd.Timestamp(session_date.strip(), tz=tz)
    warm = pd.Timestamp(warmup_from.strip(), tz=tz)
    engine_start = min(warm, sess - pd.Timedelta(days=12))
    fetch_start = engine_start - pd.Timedelta(days=12)
    return engine_start.strftime("%Y-%m-%d"), fetch_start.strftime("%Y-%m-%d")


def main() -> None:
    ap = argparse.ArgumentParser(description="KK vnpy official backtest (BacktestingEngine)")
    ap.add_argument("--session", default="2026-07-03", help="RTH session date (America/New_York)")
    ap.add_argument("--warmup-from", default="2026-06-25", help="fetch start for Keltner warmup + load_bar(10)")
    ap.add_argument("--equity", type=float, default=14.0, help="starting equity per symbol bot")
    ap.add_argument("--symbols", default="", help="comma list; default KK pool")
    ap.add_argument("--out-csv", default="", help="write fills to CSV")
    ap.add_argument(
        "--window-et",
        default="",
        help="optional ET filter e.g. 09:30-16:00",
    )
    ap.add_argument("--verbose", action="store_true", help="print vnpy engine logs")
    args = ap.parse_args()

    cfg = OrbConfig.from_env()
    kk = KKConfig.from_env()
    if args.symbols.strip():
        symbols = [norm_symbol(s) for s in args.symbols.split(",") if s.strip()]
    else:
        symbols = [norm_symbol(s) for s in parse_symbol_list(Path(resolve_symbols_path()).read_text(encoding="utf-8"))]

    session = args.session.strip()
    warmup_from = args.warmup_from.strip()
    equity = float(args.equity)

    bt_start, bt_end, anchor_ms, close_ms, close_et = _session_bounds(session, cfg)
    engine_start_s, fetch_from = _fetch_start_for_load_bar(session, warmup_from, cfg)
    engine_start = pd.Timestamp(engine_start_s, tz=cfg.session_tz).to_pydatetime().replace(tzinfo=timezone.utc)
    win_lo_ms = win_hi_ms = 0
    if args.window_et.strip():
        lo_s, hi_s = [x.strip() for x in args.window_et.split("-", 1)]
        win_lo_ms = int(pd.Timestamp(f"{session} {lo_s}", tz=cfg.session_tz).value // 1_000_000)
        win_hi_ms = int(pd.Timestamp(f"{session} {hi_s}", tz=cfg.session_tz).value // 1_000_000)

    print(f"=== KK vnpy backtest | session={session} | equity={equity}U | engine=BacktestingEngine ===")
    print(
        f"strategy=KingKeltnerKkStrategy | RTH={kk.rth_only} EOD={kk.eod_flat} "
        f"no_entry_after={kk.no_entry_after_hour}:{kk.no_entry_after_minute:02d} ET"
    )
    if args.window_et.strip():
        print(f"window ET {args.window_et.strip()}")
    print(f"fetch {fetch_from} .. {session} | replay {session} RTH close={close_et} ET | engine.start={engine_start_s}\n")

    total_net = 0.0
    total_fills = 0
    csv_rows: list[dict] = []

    for sym in symbols:
        label = sym.replace("USDT", "")
        print(f"--- {label} ---")
        try:
            df_all = _fetch_range(sym, fetch_from, session, cfg)
        except Exception as exc:
            print(f"  FETCH FAIL: {exc}\n")
            continue
        if df_all.empty:
            print("  no klines\n")
            continue

        px = float(df_all.iloc[-1]["close"])
        bars = klines_df_to_bars(df_all, sym, vt_symbol=kk_vt_symbol(sym))
        try:
            out = run_kk_vnpy_backtest(
                sym,
                bars,
                kk=kk,
                equity_usdt=equity,
                start=engine_start,
                end=bt_end,
                replay_start=bt_start,
                replay_end=bt_end,
                price=px,
                quiet=not args.verbose,
                orb_cfg=cfg,
            )
        except Exception as exc:
            print(f"  BACKTEST FAIL: {exc}\n")
            continue

        if out.get("error"):
            print(f"  {out['error']}\n")
            continue

        stats = out.get("statistics") or {}
        net = float(stats.get("total_net_pnl") or 0.0)
        fills = trades_to_rows(out.get("trades") or [])
        day_fills = [f for f in fills if _session_day(int(f["ms"]), cfg) == session]
        if win_lo_ms and win_hi_ms:
            day_fills = [f for f in day_fills if win_lo_ms <= int(f["ms"]) <= win_hi_ms]

        total_net += net
        total_fills += len(day_fills)
        end_w = out.get("end_wallet")
        end_s = f"{end_w:.4f}U" if end_w is not None else stats.get("end_balance", "?")
        print(f"  fills(session)={len(day_fills)} net={net:+.4f}U end_wallet={end_s}")

        for f in day_fills:
            ms = int(f["ms"])
            et = pd.Timestamp(ms, unit="ms", tz="America/New_York")
            cn = et.tz_convert("Asia/Shanghai")
            if args.out_csv.strip():
                csv_rows.append(
                    {
                        "symbol": label,
                        "time_et": et.strftime("%Y-%m-%d %H:%M:%S"),
                        "time_cn": cn.strftime("%Y-%m-%d %H:%M:%S"),
                        "direction": f["direction"],
                        "offset": f["offset"],
                        "price": f["price"],
                        "volume": f["volume"],
                    }
                )
            print(
                f"    FILL  {_fmt_ms(ms)} {f['direction']:5s} {f['offset']:5s} "
                f"px={float(f['price']):.4f} vol={float(f['volume']):.4f}"
            )
        print()

    if args.out_csv.strip() and csv_rows:
        out_path = Path(args.out_csv.strip())
        out_path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(csv_rows).to_csv(out_path, index=False)
        print(f"CSV -> {out_path.resolve()}\n")

    print("=== POOL TOTAL (vnpy) ===")
    print(f"fills={total_fills} net_pnl={total_net:+.4f}U")


if __name__ == "__main__":
    main()
