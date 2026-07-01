"""ORB universe K 线拉取（供月度训练 / 定时刷新）。"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from binance_fapi import check_fapi_connectivity
from orb.core.backtest import _load_range
from orb.core.config import OrbConfig
from orb.core.kline_cache import (
    COLUMNS,
    has_kline_cache,
    kline_path,
    load_klines,
    norm_symbol,
    symbol_cache_dir,
    symbol_label,
    write_meta,
)
from orb.core.session import extended_fetch_anchor_ms
from orb.core.symbols import parse_symbol_list


class KlineFetchError(RuntimeError):
    pass


def _fetch_window(*, days: float, cfg: OrbConfig) -> tuple[int, int, int, str, str]:
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - int(float(days) * 86_400_000)
    bar_step = cfg.bar_step_ms()
    fetch_start = extended_fetch_anchor_ms(start_ms, cfg) - bar_step * 96
    tz = cfg.session_tz
    lo = pd.Timestamp(start_ms, unit="ms", tz=tz).strftime("%Y-%m-%d")
    hi = pd.Timestamp(end_ms, unit="ms", tz=tz).strftime("%Y-%m-%d")
    return end_ms, fetch_start, start_ms, lo, hi


def _fetch_window_dates(
    *,
    from_date: str,
    to_date: str,
    cfg: OrbConfig,
) -> tuple[int, int, int, str, str]:
    tz = cfg.session_tz
    lo = pd.Timestamp(from_date.strip(), tz=tz)
    hi = pd.Timestamp(to_date.strip(), tz=tz) + pd.Timedelta(days=1) - pd.Timedelta(milliseconds=1)
    start_ms = int(lo.value // 1_000_000)
    end_ms = int(hi.value // 1_000_000)
    if end_ms <= start_ms:
        raise ValueError(f"invalid date range {from_date} .. {to_date}")
    bar_step = cfg.bar_step_ms()
    fetch_start = extended_fetch_anchor_ms(start_ms, cfg) - bar_step * 96
    return end_ms, fetch_start, start_ms, from_date.strip(), to_date.strip()


def _min_rows(interval: str, start_ms: int, end_ms: int) -> int:
    days = max(1.0, (int(end_ms) - int(start_ms)) / 86_400_000)
    iv = interval.strip().lower()
    if iv == "5m":
        return max(50, int(days * 6.5 * 12 * 0.12))
    if iv == "1m":
        return max(200, int(days * 6.5 * 60 * 0.12))
    if iv == "1d":
        return max(15, int(days * 0.45))
    return 30


def _save_klines_atomic(symbol: str, interval: str, df: pd.DataFrame) -> Path:
    if df is None or df.empty:
        raise KlineFetchError(f"refuse empty save {symbol_label(symbol)} {interval}")
    out = kline_path(symbol, interval)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".csv.tmp")
    cols = [c for c in COLUMNS if c in df.columns]
    df[cols].to_csv(tmp, index=False)
    tmp.replace(out)
    return out


def _require_min_rows(symbol: str, interval: str, df: pd.DataFrame, *, start_ms: int, end_ms: int) -> None:
    label = symbol_label(symbol)
    need = _min_rows(interval, start_ms, end_ms)
    got = len(df)
    if got < need:
        raise KlineFetchError(f"{label} {interval}: {got} rows < min {need}")


def _merge_frames(old: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    if old is None or old.empty:
        return new.reset_index(drop=True)
    if new is None or new.empty:
        return old.reset_index(drop=True)
    merged = pd.concat([old, new], ignore_index=True)
    return (
        merged.drop_duplicates(subset=["open_time"], keep="last")
        .sort_values("open_time")
        .reset_index(drop=True)
    )


def fetch_symbol(
    sym: str,
    *,
    days: float,
    intervals: List[str],
    cfg: OrbConfig,
    end_ms: int,
    fetch_start: int,
    range_start_ms: int,
    merge_existing: bool = True,
    progress_prefix: str = "",
) -> dict:
    label = symbol_label(sym)
    out_dir = symbol_cache_dir(sym)
    out_dir.mkdir(parents=True, exist_ok=True)
    sym_summary: dict = {"symbol": label, "dir": str(out_dir), "intervals": {}}

    for iv in intervals:
        iv_start = fetch_start
        if iv == "1d" and (cfg.sl_mode or "").strip().lower() == "atr_pct":
            iv_start = fetch_start - cfg.daily_atr_warmup_ms()
        tag = f"{progress_prefix}{label} {iv}" if progress_prefix else f"{label} {iv}"
        print(f"  {tag} ...", flush=True)
        t1 = time.time()
        new_df = _load_range(sym, iv, iv_start, end_ms)
        if new_df.empty:
            raise KlineFetchError(f"{label} {iv}: API returned 0 rows")
        df = _merge_frames(load_klines(sym, iv) if merge_existing else pd.DataFrame(), new_df)
        _require_min_rows(sym, iv, df, start_ms=range_start_ms, end_ms=end_ms)
        path = _save_klines_atomic(sym, iv, df)
        elapsed = round(time.time() - t1, 1)
        sym_summary["intervals"][iv] = {
            "rows": len(df),
            "fetched_rows": len(new_df),
            "path": path.name,
            "elapsed_sec": elapsed,
        }
        print(f"  {tag} ok total={len(df)} fetched={len(new_df)} ({elapsed:.0f}s)", flush=True)

    write_meta(sym, days=days, intervals=intervals)
    return sym_summary


def cached_symbol_dirs(cache_root: Optional[Path] = None) -> List[str]:
    """data/orb/kline 下已有缓存目录 → USDT 符号列表。"""
    from orb.data.paths import KLINE_ROOT

    root = cache_root or KLINE_ROOT
    if not root.is_dir():
        return []
    out: List[str] = []
    for d in sorted(root.iterdir()):
        if not d.is_dir():
            continue
        if any((d / f"{iv}.csv").is_file() for iv in ("5m", "1m", "1d")):
            out.append(norm_symbol(d.name))
    return out


def fetch_universe_klines(
    *,
    symbols_file: Path,
    days: float = 180.0,
    from_date: str = "",
    to_date: str = "",
    intervals: Optional[List[str]] = None,
    skip_existing: bool = False,
    merge_existing: bool = True,
    cfg: Optional[OrbConfig] = None,
    symbols: Optional[List[str]] = None,
    preflight: bool = True,
) -> Dict[str, Any]:
    """拉取 universe K 线到 data/orb/kline/。"""
    if symbols is not None:
        syms = [norm_symbol(s) for s in symbols if str(s).strip()]
    else:
        syms = parse_symbol_list(symbols_file.read_text(encoding="utf-8"))
    if not syms:
        raise ValueError(f"empty symbols file: {symbols_file}")

    ivs = intervals or ["5m", "1m", "1d"]
    c = cfg or OrbConfig.from_env()
    if from_date.strip():
        hi = to_date.strip() or pd.Timestamp.now(tz=c.session_tz).strftime("%Y-%m-%d")
        end_ms, fetch_start, range_start_ms, lo, hi_out = _fetch_window_dates(
            from_date=from_date.strip(),
            to_date=hi,
            cfg=c,
        )
        days = round((end_ms - range_start_ms) / 86_400_000, 1)
    else:
        end_ms, fetch_start, range_start_ms, lo, hi_out = _fetch_window(days=days, cfg=c)

    if preflight:
        ok, msg = check_fapi_connectivity()
        if not ok:
            raise KlineFetchError(
                f"Binance fapi unreachable: {msg}. "
                "Enable VPN or set HTTPS_PROXY in .env.oi / shell, then retry."
            )
        print(f"[preflight] {msg}", flush=True)
    print(f"[range] {lo} .. {hi_out} | fetch_from_ms={fetch_start} | intervals={','.join(ivs)}", flush=True)

    t0 = time.time()
    summary: Dict[str, Any] = {
        "symbols_file": str(symbols_file),
        "days": float(days),
        "date_range": {"from": lo, "to": hi_out},
        "intervals": ivs,
        "symbols": {},
        "skipped": [],
        "errors": [],
    }
    total = len(syms)
    for i, sym in enumerate(syms, 1):
        sym = norm_symbol(sym)
        label = symbol_label(sym)
        if skip_existing and all(has_kline_cache(sym, iv) for iv in ivs):
            summary["skipped"].append(label)
            print(f"[{i}/{total}] {label} skip (cache ok)", flush=True)
            continue
        print(f"[{i}/{total}] {label} fetching {','.join(ivs)}", flush=True)
        try:
            sym_summary = fetch_symbol(
                sym,
                days=float(days),
                intervals=ivs,
                cfg=c,
                end_ms=end_ms,
                fetch_start=fetch_start,
                range_start_ms=range_start_ms,
                merge_existing=merge_existing,
                progress_prefix=f"[{i}/{total}] ",
            )
            summary["symbols"][label] = sym_summary
            rows = {iv: sym_summary["intervals"][iv]["rows"] for iv in ivs}
            elapsed = sum(sym_summary["intervals"][iv]["elapsed_sec"] for iv in ivs)
            print(
                f"[{i}/{total}] {label} DONE "
                + " ".join(f"{iv}={rows[iv]}" for iv in ivs)
                + f"  ({elapsed:.0f}s)",
                flush=True,
            )
        except Exception as exc:
            summary["errors"].append({"symbol": label, "error": str(exc)})
            print(f"[{i}/{total}] {label} FAILED: {exc}", flush=True)

    summary["elapsed_sec"] = round(time.time() - t0, 1)
    summary["fetched"] = len(summary["symbols"])
    summary["ok"] = not summary["errors"]
    return summary
