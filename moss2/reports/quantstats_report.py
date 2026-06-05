"""QuantStats tearsheet：回测 equity / 纸面结算 → HTML 报告。"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from moss2 import config as cfg

logger = logging.getLogger(__name__)

_BAR_MINUTES = 15


def quantstats_available() -> bool:
    try:
        import quantstats  # noqa: F401

        return True
    except ImportError:
        return False


def reports_dir() -> Path:
    data_dir = __import__("os").getenv("DATA_DIR", "").strip()
    if data_dir:
        root = Path(data_dir) / "moss2_reports"
    else:
        root = Path(__file__).resolve().parent.parent.parent / "data" / "moss2_reports"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _safe_slug(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "_", str(text or "report"))[:80]


def equity_list_to_daily_returns(
    equity: List[float],
    *,
    bar_minutes: int = _BAR_MINUTES,
) -> pd.Series:
    if not equity or len(equity) < 2:
        return pd.Series(dtype=float)
    eq = pd.Series([float(x) for x in equity], dtype=float)
    end = pd.Timestamp.now("UTC")
    freq = f"{int(bar_minutes)}min"
    idx = pd.date_range(end=end, periods=len(eq), freq=freq)
    eq.index = idx
    daily = eq.resample("1D").last().dropna()
    if len(daily) < 2:
        ret = eq.pct_change().dropna()
    else:
        ret = daily.pct_change().dropna()
    ret = ret.replace([np.inf, -np.inf], np.nan).dropna()
    # 短窗回测常不足 5 个交易日：退回 bar 级收益，避免 QuantStats insufficient_returns
    if len(ret) < 5:
        ret = eq.pct_change().dropna()
        ret = ret.replace([np.inf, -np.inf], np.nan).dropna()
    return ret


def settlements_to_daily_returns(
    conn: sqlite3.Connection,
    profile_id: int,
    *,
    capital: float,
) -> Tuple[pd.Series, Dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT settled_at_utc, pnl_usdt, virtual_notional_usdt, outcome
           FROM moss2_settlements
           WHERE profile_id=?
           ORDER BY settled_at_utc ASC""",
        (int(profile_id),),
    ).fetchall()
    if not rows:
        return pd.Series(dtype=float), {"trades": 0, "capital": capital}

    daily_pnl: Dict[str, float] = {}
    daily_cap: Dict[str, float] = {}
    wins = 0
    for r in rows:
        day = str(r["settled_at_utc"] or "")[:10]
        if not day:
            continue
        pnl = float(r["pnl_usdt"] or 0)
        notional = float(r["virtual_notional_usdt"] or 0) or float(capital)
        daily_pnl[day] = daily_pnl.get(day, 0.0) + pnl
        daily_cap[day] = daily_cap.get(day, 0.0) + notional
        if str(r["outcome"] or "").lower() == "win":
            wins += 1

    idx = pd.to_datetime(sorted(daily_pnl.keys()))
    rets = []
    for d in idx.strftime("%Y-%m-%d"):
        cap = daily_cap.get(d, capital) or capital
        rets.append(daily_pnl[d] / cap if cap > 0 else 0.0)
    series = pd.Series(rets, index=idx, dtype=float)
    meta = {
        "trades": len(rows),
        "capital": capital,
        "win_rate": round(wins / len(rows), 4) if rows else 0.0,
        "total_pnl_usdt": round(sum(daily_pnl.values()), 4),
    }
    return series.replace([np.inf, -np.inf], np.nan).dropna(), meta


def benchmark_daily_returns(
    symbol: str,
    *,
    variant: str = "en",
    limit_bars: int = 4500,
) -> Optional[pd.Series]:
    try:
        from moss2.dataset import load_ohlcv, normalize_symbol

        sym = normalize_symbol(symbol, variant=variant)
        df = load_ohlcv(sym, variant, limit=limit_bars)
        if df is None or df.empty or "close" not in df.columns:
            return None
        ts_col = "timestamp" if "timestamp" in df.columns else None
        close = df["close"].astype(float)
        if ts_col:
            idx = pd.to_datetime(df[ts_col])
            if getattr(idx.dt, "tz", None) is not None:
                idx = idx.dt.tz_localize(None)
            close.index = idx
        else:
            end = pd.Timestamp.now("UTC")
            close.index = pd.date_range(
                end=end, periods=len(close), freq=f"{_BAR_MINUTES}min"
            )
        daily = close.resample("1D").last().dropna()
        if len(daily) < 2:
            return None
        return daily.pct_change().dropna().replace([np.inf, -np.inf], np.nan).dropna()
    except Exception as exc:
        logger.warning("[moss2] benchmark returns failed %s: %s", symbol, exc)
        return None


def latest_backtest_run(
    conn: sqlite3.Connection, profile_id: int
) -> Optional[Dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        """SELECT id, profile_id, variant, symbol, result_json, summary_json, created_at_utc
           FROM moss2_backtest_runs
           WHERE profile_id=?
           ORDER BY id DESC LIMIT 1""",
        (int(profile_id),),
    ).fetchone()
    if not row:
        return None
    out = dict(row)
    try:
        out["result"] = json.loads(row["result_json"] or "{}")
    except json.JSONDecodeError:
        out["result"] = {}
    try:
        out["summary"] = json.loads(row["summary_json"] or "{}")
    except json.JSONDecodeError:
        out["summary"] = {}
    return out


def _qs_float(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, pd.Series):
        value = value.iloc[-1] if len(value) else 0.0
    elif hasattr(value, "item"):
        try:
            value = value.item()
        except (ValueError, IndexError):
            value = float(value) if value is not None else 0.0
    try:
        v = float(value)
        if v != v:  # NaN
            return 0.0
        return v
    except (TypeError, ValueError):
        return 0.0


def _stats_snapshot(returns: pd.Series) -> Dict[str, Any]:
    if returns is None or len(returns) < 2:
        return {}
    import quantstats as qs

    try:
        return {
            "cagr": round(_qs_float(qs.stats.cagr(returns)), 4),
            "sharpe": round(_qs_float(qs.stats.sharpe(returns)), 4),
            "max_drawdown": round(_qs_float(qs.stats.max_drawdown(returns)), 4),
            "volatility": round(_qs_float(qs.stats.volatility(returns)), 4),
            "win_rate": round(_qs_float(qs.stats.win_rate(returns)), 4),
            "best_day": round(_qs_float(returns.max()), 6),
            "worst_day": round(_qs_float(returns.min()), 6),
            "observations": int(len(returns)),
        }
    except Exception as exc:
        logger.warning("[moss2] quantstats stats snapshot failed: %s", exc)
        return {"observations": int(len(returns))}


def generate_moss2_tearsheet(
    *,
    returns: pd.Series,
    benchmark: Optional[pd.Series] = None,
    title: str = "Moss2 Strategy",
    filename_stem: str = "moss2_report",
) -> Dict[str, Any]:
    if not quantstats_available():
        return {
            "ok": False,
            "error": "quantstats_not_installed",
            "hint": "pip install quantstats>=0.0.62",
        }
    if returns is None or len(returns) < 5:
        return {
            "ok": False,
            "error": "insufficient_returns",
            "hint": "需要至少 5 个收益样本（先跑回测或积累纸面结算）",
            "observations": int(len(returns)) if returns is not None else 0,
        }

    import quantstats as qs

    qs.extend_pandas()
    out_dir = reports_dir()
    stem = _safe_slug(filename_stem)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    fname = f"{stem}_{ts}.html"
    path = out_dir / fname

    bench = None
    if benchmark is not None and len(benchmark) >= 5:
        bench = benchmark.reindex(returns.index).fillna(0.0)
        if bench.std() == 0 or bench.isna().all():
            bench = None

    try:
        qs.reports.html(
            returns,
            benchmark=bench,
            title=title,
            output=str(path),
            download_filename=fname,
        )
    except Exception as exc:
        logger.exception("[moss2] quantstats tearsheet failed")
        return {"ok": False, "error": str(exc)}

    rel_url = f"/api/moss2/reports/files/{fname}"
    return {
        "ok": True,
        "path": str(path),
        "filename": fname,
        "url": rel_url,
        "title": title,
        "stats": _stats_snapshot(returns),
        "observations": int(len(returns)),
        "has_benchmark": bench is not None,
    }


def build_tearsheet_for_profile(
    conn: sqlite3.Connection,
    profile: Dict[str, Any],
    *,
    mode: str = "backtest",
    benchmark_symbol: Optional[str] = None,
    limit_bars: Optional[int] = None,
    run_fresh_backtest: bool = False,
) -> Dict[str, Any]:
    pid = int(profile["id"])
    sym = str(profile.get("symbol") or "").upper()
    variant = str(profile.get("variant") or cfg.MOSS2_OPS_VARIANT)
    capital = float(profile.get("virtual_equity_usdt") or cfg.MOSS2_PROFILE_CAPITAL)
    bench_sym = (benchmark_symbol or "BTCUSDT").upper()
    mode_l = str(mode or "backtest").lower()

    if mode_l == "paper":
        returns, paper_meta = settlements_to_daily_returns(conn, pid, capital=capital)
        meta_src = "paper_settlements"
        summary = paper_meta
    else:
        meta_src = "backtest_equity"
        summary = {}
        if run_fresh_backtest:
            from moss2.backtest_service import run_profile_backtest

            bt = run_profile_backtest(
                profile, capital=capital, limit_bars=limit_bars
            )
            equity = bt.get("equity_curve") or []
            summary = bt.get("summary") or {}
        else:
            row = latest_backtest_run(conn, pid)
            if not row:
                return {
                    "ok": False,
                    "error": "no_backtest_run",
                    "hint": "尚无回测记录，请先点「工厂回测」或传 run_backtest=true",
                }
            equity = (row.get("result") or {}).get("equity_curve") or []
            summary = row.get("summary") or {}
        returns = equity_list_to_daily_returns(equity)

    bench = benchmark_daily_returns(
        bench_sym, variant=variant, limit_bars=limit_bars or 4500
    )
    title = f"Moss2 p{pid} {sym} · {mode_l} · {profile.get('template', '')}"
    stem = f"m2_p{pid}_{sym}_{mode_l}"
    out = generate_moss2_tearsheet(
        returns=returns,
        benchmark=bench,
        title=title,
        filename_stem=stem,
    )
    out["profile_id"] = pid
    out["symbol"] = sym
    out["mode"] = mode_l
    out["source"] = meta_src
    out["benchmark_symbol"] = bench_sym
    out["factory_summary"] = summary
    return out
