"""ORB 纸面信号 → Next-k-protocol 实盘执行（开/平/止损）。"""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any, Dict, List, Optional

from orb.core.config import OrbConfig
from orb.core.kline_cache import norm_symbol
from orb.core.protocol_client import (
    LIVE_PENDING_NOTE,
    LIVE_PENDING_OCO_NOTE,
    SOURCE_ORB,
    ingest_signals,
    lookup_signal,
    protocol_configured,
    reconcile_pending_entries,
)
from orb.core.signals import (
    OrbSignal,
    PreplaceArmBundle,
    limit_price_for_side,
    preplace_sl_risk_dist,
    refresh_preplace_leg_after_fill,
)

logger = logging.getLogger(__name__)

_PENDING_STATUSES = frozenset({"submitted", "received", "pending", ""})
_TERMINAL_STATUSES = frozenset({"cancelled", "error", "skipped", "skipped_duplicate"})


def live_enabled(cfg: OrbConfig) -> bool:
    return bool(getattr(cfg, "live_enabled", False)) and protocol_configured()


def _leverage(cfg: OrbConfig) -> float:
    lev = float(getattr(cfg, "live_leverage", 0.0) or 0.0)
    if lev > 0:
        return lev
    return max(1.0, float(cfg.leverage or 1.0))


def _margin_from_notional(notional_usdt: float, cfg: OrbConfig) -> float:
    lev = _leverage(cfg)
    n = max(0.0, float(notional_usdt or 0.0))
    if n > 0 and lev > 0:
        return n / lev
    return max(0.0, float(cfg.margin_usdt or 0.0))


def _open_signal_id(sig: OrbSignal) -> str:
    bar = int(sig.entry_bar_open_ms or 0)
    sess = (sig.session_date or "").strip()
    return f"orb:open:{sig.symbol}:{sess}:{bar}"


def _preplace_signal_id(sig: OrbSignal) -> str:
    bar = int(sig.entry_bar_open_ms or 0)
    sess = (sig.session_date or "").strip()
    side = str(sig.side).upper()
    return f"orb:preplace:{sig.symbol}:{sess}:{bar}:{side}"


def _close_signal_id(symbol: str, *, signal_id: int, tag: str) -> str:
    sym = norm_symbol(symbol)
    return f"orb:close:{sym}:{int(signal_id)}:{str(tag or 'resolve').strip().lower()}"


def _live_entry_type(cfg: OrbConfig) -> str:
    if bool(getattr(cfg, "arm_at_or_close", False)):
        return "STOP_LIMIT"
    raw = str(getattr(cfg, "live_entry_type", "") or "stoplimit_gap").strip().lower()
    if raw in ("stoplimit_gap", "stoplimit", "stop_limit", "stop-limit"):
        return "STOP_LIMIT"
    if raw in ("market", ""):
        return "MARKET"
    return raw.upper()


def ingest_detail_action(result: Optional[Dict[str, Any]]) -> str:
    if not isinstance(result, dict):
        return ""
    for detail in result.get("details") or []:
        action = str(detail.get("action") or "").lower()
        if action:
            return action
    return ""


def live_open_is_pending(result: Optional[Dict[str, Any]]) -> bool:
    return ingest_detail_action(result) == "submitted"


def live_open_any_pending(result: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(result, dict):
        return False
    if live_open_is_pending(result):
        return True
    for detail in result.get("details") or []:
        if str(detail.get("action") or "").lower() == "submitted":
            return True
    return False


def live_preplace_oco_succeeded(result: Optional[Dict[str, Any]]) -> bool:
    """OCO 双腿均 submitted/traded 且无 error。"""
    if not isinstance(result, dict) or result.get("skipped") is True:
        return True
    if result.get("error") or int(result.get("errors") or 0) > 0:
        return False
    details = result.get("details") or []
    if len(details) < 2:
        return False
    ok = frozenset({"submitted", "traded", "duplicate"})
    for detail in details:
        act = str(detail.get("action") or "").lower()
        if act == "error":
            return False
        if act not in ok:
            return False
    return True


def _parse_proto_result(proto: Dict[str, Any]) -> Dict[str, Any]:
    raw = proto.get("result")
    if raw is None:
        raw = proto.get("result_json") or {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}
    return {}


def _preplace_api_id(*, sym: str, session_date: str, or_end_ms: int, leg_side: str) -> str:
    return f"orb:preplace:{str(sym).strip().upper()}:{session_date or ''}:{int(or_end_ms)}:{leg_side.upper()}"


def _lookup_oco_legs(*, sym: str, session_date: str, or_end_ms: int) -> Dict[str, Dict[str, Any]]:
    legs: Dict[str, Dict[str, Any]] = {}
    for leg_side in ("LONG", "SHORT"):
        api_id = _preplace_api_id(sym=sym, session_date=session_date, or_end_ms=or_end_ms, leg_side=leg_side)
        try:
            proto = lookup_signal(source=SOURCE_ORB, api_signal_id=api_id)
        except Exception as exc:
            logger.warning("[orb] protocol lookup %s failed: %s", api_id, exc)
            continue
        if proto:
            legs[leg_side] = proto
    return legs


def build_open_payload(sig: OrbSignal, cfg: OrbConfig, *, oco_peer_api_id: Optional[str] = None) -> Dict[str, Any]:
    notional = float(sig.paper_notional_usdt or cfg.default_paper_notional())
    lev = _leverage(cfg)
    margin = _margin_from_notional(notional, cfg)
    if notional > 0 and lev > 0:
        implied = round(margin * lev, 4)
        if abs(implied - notional) > max(1.0, notional * 0.001):
            logger.warning(
                "[orb] live open margin×lev drift: %s notional=%.4f implied=%.4f lev=%s",
                sig.symbol,
                notional,
                implied,
                lev,
            )
    entry = float(sig.price) if sig.price else None
    side_u = str(sig.side).upper()
    limit_px = limit_price_for_side(entry=entry, side=side_u, cfg=cfg) if entry else None
    api_id = _preplace_signal_id(sig) if cfg.arm_at_or_close else _open_signal_id(sig)
    payload: Dict[str, Any] = {
        "source": SOURCE_ORB,
        "api_signal_id": api_id,
        "symbol": str(sig.symbol).strip().upper(),
        "side": side_u,
        "margin_usdt": round(margin, 4),
        "leverage": lev,
        "entry_price": entry,
        "limit_price": limit_px,
        "sl_price": float(sig.sl_price) if sig.sl_price is not None else None,
        "tp_price": float(sig.tp_price) if sig.tp_price is not None else None,
        "play": sig.play or "ORB",
        "confidence": sig.confidence or "high",
        "action": "open",
        "entry_type": _live_entry_type(cfg),
        "allow_gap_market": not bool(getattr(cfg, "arm_at_or_close", False)),
        "client_ref": api_id,
    }
    if float(sig.or_high or 0) > 0 and float(sig.or_low or 0) > 0:
        payload["or_high"] = round(float(sig.or_high), 8)
        payload["or_low"] = round(float(sig.or_low), 8)
    if entry and sig.sl_price is not None:
        payload["sl_risk_dist"] = round(
            preplace_sl_risk_dist(stop_entry=float(entry), sl=float(sig.sl_price)), 8
        )
    if oco_peer_api_id:
        payload["oco_peer_api_id"] = oco_peer_api_id
    return payload


def build_close_payload(
    symbol: str,
    side: str,
    *,
    close_price: Optional[float] = None,
    play: Optional[str] = None,
    tag: str = "resolve",
    signal_id: Optional[int] = None,
) -> Dict[str, Any]:
    sym = norm_symbol(symbol)
    side_u = str(side).upper()
    sid = int(signal_id or 0)
    tag_s = str(tag or "resolve").strip().lower()
    api_id = _close_signal_id(sym, signal_id=sid, tag=tag_s) if sid > 0 else f"orb:close:{sym}:{tag_s}"
    payload: Dict[str, Any] = {
        "source": SOURCE_ORB,
        "api_signal_id": api_id,
        "symbol": sym,
        "side": side_u,
        "action": "close",
        "play": play or "ORB",
    }
    if close_price is not None and close_price > 0 and tag_s != "session_close":
        payload["close_price"] = float(close_price)
    return payload


def live_ingest_succeeded(result: Optional[Dict[str, Any]]) -> bool:
    """Return True when protocol ingest traded/submitted the signal (or live was not attempted)."""
    if result is None:
        return True
    action = ingest_detail_action(result)
    if action == "duplicate":
        return False
    if result.get("skipped") is True:
        return True
    if result.get("error"):
        return False
    if int(result.get("errors") or 0) > 0:
        return False
    if int(result.get("traded") or 0) >= 1:
        return True
    if int(result.get("submitted") or 0) >= 1:
        return True
    if action in ("traded", "submitted"):
        return True
    for detail in result.get("details") or []:
        act = str(detail.get("action") or "").lower()
        if act in ("traded", "submitted"):
            return True
        if act == "error":
            return False
    return False


def notify_open(sig: OrbSignal, cfg: OrbConfig) -> Dict[str, Any]:
    if not live_enabled(cfg):
        return {"skipped": True, "reason": "live_disabled"}
    if str(sig.side).upper() not in ("LONG", "SHORT"):
        return {"skipped": True, "reason": "not_actionable"}
    payload = build_open_payload(sig, cfg)
    return ingest_signals([payload])


def notify_preplace_arm(bundle: PreplaceArmBundle, cfg: OrbConfig) -> Dict[str, Any]:
    if not live_enabled(cfg):
        return {"skipped": True, "reason": "live_disabled"}
    long_sig = bundle.long_sig
    short_sig = bundle.short_sig
    long_id = _preplace_signal_id(long_sig)
    short_id = _preplace_signal_id(short_sig)
    payloads: List[Dict[str, Any]] = []
    if cfg.preplace_oco:
        payloads.append(build_open_payload(long_sig, cfg, oco_peer_api_id=short_id))
        payloads.append(build_open_payload(short_sig, cfg, oco_peer_api_id=long_id))
    else:
        payloads.append(build_open_payload(long_sig, cfg))
    logger.info(
        "[orb] preplace ingest %s oco=%s legs=%d long_id=%s short_id=%s "
        "L_stop=%.6f S_stop=%.6f notion_L=%.2f notion_S=%.2f",
        long_sig.symbol,
        bool(cfg.preplace_oco),
        len(payloads),
        long_id,
        short_id if cfg.preplace_oco else "-",
        float(long_sig.price),
        float(short_sig.price),
        float(long_sig.paper_notional_usdt or 0),
        float(short_sig.paper_notional_usdt or 0),
    )
    result = ingest_signals(payloads)
    ingest_ok = (
        live_preplace_oco_succeeded(result)
        if cfg.preplace_oco and len(payloads) >= 2
        else live_ingest_succeeded(result)
    )
    if not ingest_ok:
        logger.warning(
            "[orb] preplace ingest failed %s result=%s",
            long_sig.symbol,
            {k: result.get(k) for k in ("traded", "submitted", "errors", "skipped")} if isinstance(result, dict) else result,
        )
    return result


def notify_close(
    symbol: str,
    side: str,
    cfg: OrbConfig,
    *,
    close_price: Optional[float] = None,
    play: Optional[str] = None,
    tag: str = "resolve",
    signal_id: Optional[int] = None,
) -> Dict[str, Any]:
    if not live_enabled(cfg):
        return {"skipped": True, "reason": "live_disabled"}
    payload = build_close_payload(
        symbol,
        side,
        close_price=close_price,
        play=play,
        tag=tag,
        signal_id=signal_id,
    )
    return ingest_signals([payload])


def _promote_oco_fill(
    cur,
    *,
    sid: int,
    sym: str,
    leg_side: str,
    proto: Dict[str, Any],
    cfg: OrbConfig,
) -> None:
    """成交后：用成交腿的 protocol 回报刷新 side/entry/SL/仓位。"""
    result = _parse_proto_result(proto)
    side = str(proto.get("side") or result.get("side") or leg_side).upper()
    fill_px = float(result.get("entry_price") or proto.get("entry_price") or 0)
    sl_px = proto.get("sl_price")
    if sl_px is None:
        sl_px = result.get("sl_price")
    tp_px = proto.get("tp_price")
    if tp_px is None:
        tp_px = result.get("tp_price")
    notion = result.get("notional_usdt")
    if notion is None:
        notion = proto.get("notional_usdt")
    sl_f = float(sl_px) if sl_px is not None else None
    tp_f = float(tp_px) if tp_px is not None else None
    notion_f = float(notion) if notion is not None else None

    cur.execute(
        "SELECT entry_price, or_high, or_low FROM orb_signals WHERE id=?",
        (int(sid),),
    )
    row = cur.fetchone()
    if row and fill_px > 0:
        stop_px = float(row[0] or fill_px)
        or_h = float(row[1] or 0)
        or_l = float(row[2] or 0)
        if or_h > 0 and or_l > or_h:
            leg = OrbSignal(
                symbol=str(sym),
                price=stop_px,
                side=side,
                play=f"ORB_PREPLACE_{side}",
                confidence="high",
                or_high=or_h,
                or_low=or_l,
                sl_price=sl_f,
                tp_price=tp_f,
            )
            refreshed = refresh_preplace_leg_after_fill(leg, fill_px=fill_px, cfg=cfg)
            sl_f = refreshed.sl_price
            tp_f = refreshed.tp_price
            if notion_f is None and refreshed.paper_notional_usdt:
                notion_f = float(refreshed.paper_notional_usdt)

    cur.execute(
        """
        UPDATE orb_signals SET
            side=?, play=?, entry_price=?, sl_price=?, tp_price=?,
            virtual_notional_usdt=?, notes=NULL, confidence='high'
        WHERE id=? AND outcome IS NULL
        """,
        (
            side,
            f"ORB_PREPLACE_{side}",
            fill_px if fill_px > 0 else None,
            sl_f,
            tp_f,
            round(notion_f, 4) if notion_f is not None and notion_f > 0 else None,
            int(sid),
        ),
    )
    logger.info(
        "[orb] preplace fill promoted %s id=%s side=%s fill=%.6f sl=%s notion=%s",
        sym,
        sid,
        side,
        fill_px,
        sl_f,
        notion_f,
    )


def _sync_oco_pending_row(
    cur,
    *,
    sid: int,
    sym: str,
    session_date: str,
    or_end_ms: int,
    cfg: OrbConfig,
) -> bool:
    """对账单条 pending OCO：成交 promote；双腿均终态且无成交则回滚。"""
    legs = _lookup_oco_legs(sym=sym, session_date=session_date, or_end_ms=or_end_ms)
    if not legs:
        return False

    for leg_side in ("LONG", "SHORT"):
        proto = legs.get(leg_side)
        if not proto:
            continue
        if str(proto.get("status") or "").lower() == "traded":
            _promote_oco_fill(cur, sid=int(sid), sym=str(sym), leg_side=leg_side, proto=proto, cfg=cfg)
            return True

    if len(legs) < 2:
        logger.debug("[orb] preplace OCO pending %s id=%s legs_found=%d", sym, sid, len(legs))
        return False

    statuses = {str(legs[s].get("status") or "").lower() for s in legs}
    if statuses & _PENDING_STATUSES:
        return False

    if all(st in _TERMINAL_STATUSES for st in statuses):
        cur.execute(
            """
            DELETE FROM orb_signals
            WHERE id=? AND outcome IS NULL AND COALESCE(notes, '') = ?
            """,
            (int(sid), LIVE_PENDING_OCO_NOTE),
        )
        logger.info("[orb] rolled back pending OCO id=%s symbol=%s statuses=%s", sid, sym, sorted(statuses))
        return True
    return False


def sync_live_pending_entries(conn: sqlite3.Connection, cfg: OrbConfig) -> int:
    """对账 pending STOP 入场：成交清标记，取消则回滚纸面持仓。"""
    if not live_enabled(cfg):
        return 0
    try:
        reconcile_pending_entries()
    except Exception as exc:
        logger.warning("[orb] protocol reconcile failed: %s", exc)

    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, symbol, session_date, entry_bar_open_ms, notes
        FROM orb_signals
        WHERE outcome IS NULL AND COALESCE(notes, '') IN (?, ?)
          AND sl_price IS NOT NULL
        """,
        (LIVE_PENDING_NOTE, LIVE_PENDING_OCO_NOTE),
    )
    rows = cur.fetchall()
    changed = 0
    for sid, sym, session_date, entry_bar, notes in rows:
        note_s = str(notes or "")
        if note_s == LIVE_PENDING_OCO_NOTE:
            if _sync_oco_pending_row(
                cur,
                sid=int(sid),
                sym=str(sym),
                session_date=str(session_date or ""),
                or_end_ms=int(entry_bar or 0),
                cfg=cfg,
            ):
                changed += 1
            continue

        api_id = f"orb:open:{str(sym).strip().upper()}:{session_date or ''}:{int(entry_bar or 0)}"
        try:
            proto = lookup_signal(source=SOURCE_ORB, api_signal_id=api_id)
        except Exception as exc:
            logger.warning("[orb] protocol lookup %s failed: %s", api_id, exc)
            continue
        if not proto:
            continue
        status = str(proto.get("status") or "").lower()
        if status == "traded":
            cur.execute("UPDATE orb_signals SET notes=NULL WHERE id=?", (int(sid),))
            changed += 1
        elif status in ("cancelled", "error"):
            cur.execute(
                """
                DELETE FROM orb_signals
                WHERE id=? AND outcome IS NULL AND COALESCE(notes, '') = ?
                """,
                (int(sid), LIVE_PENDING_NOTE),
            )
            changed += 1
            logger.info("[orb] rolled back pending paper open id=%s symbol=%s status=%s", sid, sym, status)
    if changed:
        conn.commit()
    return changed
