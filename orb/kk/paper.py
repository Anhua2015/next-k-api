"""King Keltner 纸面扫描（RTH + EOD，与 ORB 表/信号隔离）。"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pandas as pd

from accumulation_radar import init_db
from orb.core.macro_calendar import is_macro_skip_day, macro_calendar_status
from orb.core.paper import _load_1m_df, _session_date_now, in_regular_session
from orb.cta.engine import CtaBacktestConfig, process_cta_bar
from orb.cta.strategies import cta_config_for_strategy
from orb.kk.config import KKConfig
from orb.kk.db import (
    count_open_kk_positions,
    incr_session_opens,
    insert_run,
    insert_trade,
    load_state_json,
    load_wallet,
    migrate_kk_tables,
    save_state_json,
    save_wallet,
    session_open_count,
)
from orb.kk.state import export_ctx, import_ctx
from orb.kk.strategy import king_keltner_on_bar

logger = logging.getLogger(__name__)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _cta_cfg(kk: KKConfig) -> CtaBacktestConfig:
    base = cta_config_for_strategy(
        "king_keltner",
        equity_usdt=float(kk.equity_usdt),
        risk_pct=float(kk.risk_pct),
        compound=bool(kk.compound),
        rth_only=bool(kk.rth_only),
        eod_flat=bool(kk.eod_flat),
        exit_hour=int(kk.exit_hour),
        exit_minute=int(kk.exit_minute),
        maker_bps=float(kk.fee_maker_bps),
        taker_bps=float(kk.fee_taker_bps),
        slip_bps_entry=float(kk.slip_bps_entry),
        slip_bps_exit=float(kk.slip_bps_exit),
        max_notional_usdt=float(kk.max_notional_usdt or 0.0),
    )
    return base


def _rollback_open(ctx) -> None:
    from orb.cta.engine import Position

    ctx.pos = Position()
    ctx.pending.clear()


def _rollback_close(ctx, trade: Dict[str, Any]) -> None:
    from orb.cta.engine import Position

    side_s = str(trade.get("side") or "").upper()
    side = 1 if side_s == "LONG" else -1 if side_s == "SHORT" else 0
    if side == 0:
        return
    ctx.pos = Position(
        side=side,
        entry=float(trade.get("entry") or 0.0),
        sl=float(trade.get("pre_sl") or 0.0),
        notional=float(trade.get("notional_usdt") or 0.0),
        entry_ms=int(trade.get("entry_ms") or 0),
    )
    if ctx.cfg.compound:
        pnl = float(trade.get("pnl_usdt") or 0.0)
        ctx.wallet = round(float(ctx.wallet) - pnl, 4)


def _drain_new_trades(
    ctx,
    *,
    sym: str,
    session_date: str,
    cur,
    kk: KKConfig,
    orb_cfg,
    stats: Dict[str, Any],
    trade_buf: List[Dict[str, Any]],
) -> None:
    from orb.kk.live_exec import (
        bootstrap_sl_price,
        live_enabled,
        live_ingest_succeeded,
        notify_close,
        notify_open,
    )

    live_on = live_enabled(kk) and not kk.shadow
    max_pos = int(kk.max_open_positions or 0)

    while trade_buf:
        t = trade_buf.pop(0)
        if t.get("event") == "open":
            if max_pos > 0 and count_open_kk_positions(cur, session_date=session_date) >= max_pos:
                _rollback_open(ctx)
                stats.setdefault("live_rollbacks", []).append(
                    {"symbol": sym, "event": "open", "reason": "max_open_positions"}
                )
                continue
            live_result = None
            if live_on:
                try:
                    live_result = notify_open(t, symbol=sym, session_date=session_date, kk=kk, orb_cfg=orb_cfg)
                except Exception as exc:
                    logger.warning("[kk] live open %s failed: %s", sym, exc)
                    live_result = {"error": str(exc)}
                if not live_ingest_succeeded(live_result):
                    _rollback_open(ctx)
                    stats.setdefault("live_rollbacks", []).append(
                        {"symbol": sym, "event": "open", "reason": "live_ingest_failed", "live": live_result}
                    )
                    continue
            incr_session_opens(cur, session_date, sym)
            stats["opens"] = int(stats.get("opens") or 0) + 1
            stats.setdefault("open_events", []).append({"symbol": sym, **t})
            if live_on:
                sl = bootstrap_sl_price(
                    side=str(t.get("side") or ""),
                    entry=float(t.get("entry") or 0),
                    sl=float(t.get("sl") or 0),
                )
                live_state = ctx.state.setdefault("live", {})
                live_state["last_sl"] = round(sl, 6) if sl > 0 else 0.0
            insert_trade(
                cur,
                {
                    "session_date": session_date,
                    "symbol": sym,
                    "event": "open",
                    "side": t.get("side"),
                    "entry": t.get("entry"),
                    "notional_usdt": t.get("notional_usdt"),
                    "detail": {**t, "live": live_result},
                    "bar_ms": t.get("ms"),
                    "created_at_utc": _utc_now(),
                },
            )
        elif t.get("event") == "close":
            live_result = None
            if live_on:
                try:
                    live_result = notify_close(t, symbol=sym, session_date=session_date, kk=kk)
                except Exception as exc:
                    logger.warning("[kk] live close %s failed: %s", sym, exc)
                    live_result = {"error": str(exc)}
                if not live_ingest_succeeded(live_result):
                    _rollback_close(ctx, t)
                    stats.setdefault("live_rollbacks", []).append(
                        {"symbol": sym, "event": "close", "reason": "live_ingest_failed", "live": live_result}
                    )
                    continue
            stats["closes"] = int(stats.get("closes") or 0) + 1
            if str(t.get("outcome") or "") == "eod":
                stats["eod_closes"] = int(stats.get("eod_closes") or 0) + 1
            stats.setdefault("close_events", []).append({"symbol": sym, **t})
            ctx.state.setdefault("live", {}).pop("last_sl", None)
            insert_trade(
                cur,
                {
                    "session_date": session_date,
                    "symbol": sym,
                    "event": "close",
                    "side": t.get("side"),
                    "entry": t.get("entry"),
                    "exit_px": t.get("exit"),
                    "notional_usdt": t.get("notional_usdt"),
                    "pnl_usdt_gross": t.get("pnl_usdt_gross"),
                    "fee_usdt": t.get("fee_usdt"),
                    "pnl_usdt": t.get("pnl_usdt"),
                    "outcome": t.get("outcome"),
                    "detail": {**t, "live": live_result},
                    "bar_ms": t.get("ms"),
                    "created_at_utc": _utc_now(),
                },
            )


def _sync_trailing_sl(
    ctx,
    *,
    sym: str,
    kk: KKConfig,
    stats: Dict[str, Any],
) -> None:
    from orb.kk.live_exec import live_enabled, live_ingest_succeeded, notify_trailing_sl

    if not live_enabled(kk) or kk.shadow or ctx.pos.side == 0:
        return
    sl = float(ctx.pos.sl or 0.0)
    if sl <= 0:
        return
    live_state = ctx.state.setdefault("live", {})
    last_sl = float(live_state.get("last_sl") or 0.0)
    if last_sl > 0 and abs(sl - last_sl) < 1e-6:
        return
    side = "LONG" if ctx.pos.side == 1 else "SHORT"
    try:
        result = notify_trailing_sl(symbol=sym, side=side, sl_price=sl)
    except Exception as exc:
        logger.warning("[kk] trailing sl %s failed: %s", sym, exc)
        stats.setdefault("trailing_sl_errors", []).append({"symbol": sym, "error": str(exc)})
        return
    if live_ingest_succeeded(result):
        live_state["last_sl"] = round(sl, 6)
        stats.setdefault("trailing_sl_updates", []).append({"symbol": sym, "sl": sl, "result": result})


def _idle_skip_reason(kk: KKConfig, cur, orb_cfg, *, now_ms: int, session_date: str) -> Optional[str]:
    if not kk.rth_only:
        return None
    if in_regular_session(orb_cfg, now_ms=now_ms):
        return None
    if count_open_kk_positions(cur, session_date=session_date) > 0:
        return None
    return "outside_regular_session_no_open_positions"


def _scan_symbol(
    sym: str,
    *,
    kk: KKConfig,
    orb_cfg,
    cta_cfg: CtaBacktestConfig,
    session_date: str,
    cur,
    now_ms: int,
    stats: Dict[str, Any],
) -> None:
    sym = str(sym).strip().upper()
    stored = load_state_json(cur, sym, session_date)
    last_bar_ms = int(stored.get("last_bar_ms") or 0)
    ctx_payload = stored.get("ctx") if isinstance(stored.get("ctx"), dict) else stored
    wallet = load_wallet(cur, sym, default=float(kk.equity_usdt))
    ctx, last_day, prev_close = import_ctx(ctx_payload or {}, cta_cfg=cta_cfg, orb_cfg=orb_cfg, wallet=wallet)
    trade_buf: List[Dict[str, Any]] = []
    block_new_entries = kk.one_trade_per_session and session_open_count(cur, session_date, sym) >= 1

    def _on_bar(ctx_obj, row, ms):
        if block_new_entries and ctx_obj.pos.side == 0:
            return
        king_keltner_on_bar(ctx_obj, row, ms)

    df = _load_1m_df(sym, orb_cfg, now_ms=now_ms)
    if df.empty:
        stats.setdefault("symbol_skips", []).append({"symbol": sym, "reason": "empty_klines"})
        return
    if last_bar_ms > 0:
        df = df[df["open_time"] > last_bar_ms].reset_index(drop=True)
    if df.empty:
        return

    for _, row in df.iterrows():
        before = len(ctx.trades)
        last_day, prev_close = process_cta_bar(
            ctx,
            row,
            strategy_fn=_on_bar,
            orb_cfg=orb_cfg,
            cta_cfg=cta_cfg,
            last_day=last_day,
            prev_close=prev_close,
        )
        new_trades = ctx.trades[before:]
        ctx.trades = ctx.trades[:before]
        trade_buf.extend(new_trades)
        _drain_new_trades(ctx, sym=sym, session_date=session_date, cur=cur, kk=kk, orb_cfg=orb_cfg, stats=stats, trade_buf=trade_buf)
        if kk.one_trade_per_session and session_open_count(cur, session_date, sym) >= 1:
            block_new_entries = True
        last_bar_ms = int(row["open_time"])

    _sync_trailing_sl(ctx, sym=sym, kk=kk, stats=stats)
    save_wallet(cur, sym, ctx.wallet, now_utc=_utc_now())
    save_state_json(
        cur,
        sym,
        session_date,
        state={"ctx": export_ctx(ctx, last_day=last_day, prev_close=prev_close)},
        last_bar_ms=last_bar_ms,
    )


def run_scan_kk(*, now_ms: Optional[int] = None) -> Dict[str, Any]:
    kk = KKConfig.from_env()
    orb_cfg = kk.orb_session_cfg()
    cta_cfg = _cta_cfg(kk)
    t_ms = int(now_ms if now_ms is not None else time.time() * 1000)
    session_date = _session_date_now(orb_cfg)

    out: Dict[str, Any] = {
        "ok": True,
        "lane": kk.lane,
        "shadow": bool(kk.shadow),
        "session_date": session_date,
        "symbols": [],
        "skipped": False,
        "reason": None,
        "opens": [],
        "closes": [],
    }

    if not kk.enabled:
        out.update({"ok": True, "skipped": True, "reason": "kk_disabled"})
        return out

    if kk.is_vnpy_engine():
        out.update({"ok": True, "skipped": True, "reason": "vnpy_engine"})
        return out

    symbols = kk.symbol_list()
    out["symbols"] = symbols
    if not symbols:
        out.update({"ok": False, "skipped": True, "reason": "no_symbols"})
        return out

    if kk.macro_filter and is_macro_skip_day(session_date):
        macro = macro_calendar_status(session_date)
        out.update({"ok": True, "skipped": True, "reason": "macro_skip", "macro": macro})
        return out

    conn = init_db()
    try:
        cur = conn.cursor()
        migrate_kk_tables(cur)
        from orb.kk.live_exec import live_enabled, sync_live_pending

        if live_enabled(kk) and not kk.shadow:
            sync_live_pending()
        idle = _idle_skip_reason(kk, cur, orb_cfg, now_ms=t_ms, session_date=session_date)
        if idle:
            out.update({"ok": True, "skipped": True, "reason": idle})
            conn.commit()
            return out

        stats: Dict[str, Any] = {
            "opens": 0,
            "closes": 0,
            "eod_closes": 0,
            "open_events": [],
            "close_events": [],
            "symbol_skips": [],
        }
        for sym in symbols:
            try:
                _scan_symbol(
                    sym,
                    kk=kk,
                    orb_cfg=orb_cfg,
                    cta_cfg=cta_cfg,
                    session_date=session_date,
                    cur=cur,
                    now_ms=t_ms,
                    stats=stats,
                )
            except Exception as exc:
                logger.exception("[kk] scan %s failed: %s", sym, exc)
                stats.setdefault("errors", []).append({"symbol": sym, "error": str(exc)})

        insert_run(
            cur,
            {
                "ran_at_utc": _utc_now(),
                "session_date": session_date,
                "symbols_scanned": len(symbols),
                "opens": stats.get("opens"),
                "closes": stats.get("closes"),
                "eod_closes": stats.get("eod_closes"),
                "detail": stats,
            },
        )
        conn.commit()
        out["opens"] = stats.get("open_events") or []
        out["closes"] = stats.get("close_events") or []
        out["summary"] = {
            "opens": int(stats.get("opens") or 0),
            "closes": int(stats.get("closes") or 0),
            "eod_closes": int(stats.get("eod_closes") or 0),
        }
        if stats.get("errors"):
            out["errors"] = stats["errors"]
        return out
    finally:
        conn.close()
