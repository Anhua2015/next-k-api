"""Signal bridge: pushes ZCT signals to Binance execution on scan completion.

Called by worker_tasks.run_zct_vwap_signal_task() immediately after the ZCT
scanner subprocess finishes — no polling interval needed.

Flow:
  1. Open accumulation.db read-only.
  2. Query zct_vwap_signals WHERE outcome IS NULL AND sl/tp set AND side in LONG/SHORT.
  3. For each signal not yet in binance.db signals_log (UNIQUE guard):
     a. Skip if trading disabled or source disabled.
     b. Skip if a position for the same symbol is already open.
     c. Skip if max_positions reached.
     d. Call trader.execute_trade() — synchronous, runs in scheduler thread.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from binance_bridge import db as _db
from binance_bridge.trader import execute_trade

logger = logging.getLogger("binance_bridge.signal_bridge")

# accumulation.db lives at DATA_DIR / accumulation.db (mirrors accumulation_radar.py).
_DATA_DIR = Path(os.getenv("DATA_DIR", Path(__file__).parent.parent))
_ACCUM_DB_PATH = _DATA_DIR / "accumulation.db"

SOURCE_NAME = "zct_vwap"


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_open_zct_signals() -> List[Dict[str, Any]]:
    """Read open ZCT signals from accumulation.db (read-only URI)."""
    uri = f"file:{_ACCUM_DB_PATH}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=5.0)
        conn.row_factory = sqlite3.Row
    except sqlite3.OperationalError as exc:
        logger.warning("Cannot open accumulation.db read-only: %s", exc)
        return []
    try:
        cur = conn.execute(
            """SELECT id, symbol, side, entry_price, sl_price, tp_price,
                      virtual_notional_usdt, recorded_at_utc, confidence, regime, play
               FROM zct_vwap_signals
               WHERE outcome IS NULL
                 AND sl_price IS NOT NULL
                 AND tp_price IS NOT NULL
                 AND side IN ('LONG','SHORT')
               ORDER BY id ASC"""
        )
        return [dict(r) for r in cur.fetchall()]
    except sqlite3.OperationalError as exc:
        # Table may not exist yet on first run before any scan.
        logger.debug("zct_vwap_signals read failed: %s", exc)
        return []
    finally:
        conn.close()


def on_scan_complete() -> Dict[str, Any]:
    """Process any new ZCT signals after a scan completes.

    Returns a summary dict: {scanned, traded, skipped, errors, details}.
    """
    result: Dict[str, Any] = {"scanned": 0, "traded": 0, "skipped": 0, "errors": 0, "details": []}

    if _db.get_config("enabled", "false").lower() != "true":
        logger.debug("signal_bridge: trading disabled, skipping")
        return result

    enabled_sources = [
        s.strip()
        for s in _db.get_config("enabled_sources", "zct_vwap,zct_hot_oi").split(",")
        if s.strip()
    ]
    if SOURCE_NAME not in enabled_sources:
        logger.debug("signal_bridge: source %s not in enabled_sources", SOURCE_NAME)
        return result

    try:
        max_pos = int(_db.get_config("max_positions", "8"))
    except ValueError:
        max_pos = 8

    # Per-play max configs
    _play_max = {}
    for _pn in ("play01", "play02", "play03"):
        try:
            _play_max[_pn] = int(_db.get_config(f"max_positions_{_pn}", "5"))
        except ValueError:
            _play_max[_pn] = 5

    def _play_max_for(p: str) -> int:
        if not p:
            return max_pos
        pu = p.strip().upper()
        for _k in ("PLAY01", "PLAY02", "PLAY03"):
            if pu.startswith(_k):
                return _play_max.get(_k.lower(), 5)
        return max_pos

    signals = _read_open_zct_signals()
    result["scanned"] = len(signals)

    for sig in signals:
        api_id = str(sig.get("id", ""))
        symbol = sig.get("symbol", "")
        side = sig.get("side", "")
        sl_price = sig.get("sl_price")
        tp_price = sig.get("tp_price")
        play = sig.get("play", "") or ""

        if not api_id or not symbol or not sl_price or not tp_price:
            continue

        detail: Dict[str, Any] = {"api_signal_id": api_id, "symbol": symbol, "side": side, "play": play}

        # Atomic: insert into signals_log + position-count check must be serialised.
        with _db._db_write_lock:
            signal_log_id = _db.insert_signal(
                source=SOURCE_NAME,
                api_signal_id=api_id,
                symbol=symbol,
                side=side,
                entry_price=sig.get("entry_price"),
                sl_price=float(sl_price),
                tp_price=float(tp_price),
                confidence=sig.get("confidence"),
                regime=sig.get("regime"),
                notional_usdt=sig.get("virtual_notional_usdt"),
                received_at=_now_utc(),
                play=play,
            )
            if signal_log_id is None:
                # Duplicate — already processed in a previous scan.
                detail["action"] = "duplicate"
                result["skipped"] += 1
                result["details"].append(detail)
                continue

            # Gate: existing open position for this symbol → no new open
            if _db.get_open_position_for_symbol(symbol) is not None:
                _db.update_signal_status(signal_log_id, "skipped_position_exists", "open position for symbol")
                logger.info("bridge skip %s %s: position already open", side, symbol)
                detail["action"] = "skipped_position_exists"
                result["skipped"] += 1
                result["details"].append(detail)
                continue

            # Gate: per-play max positions reached
            play_max = _play_max_for(play)
            play_open = _db.count_open_by_play(play)
            if play_open >= play_max:
                _db.update_signal_status(signal_log_id, "skipped_max_positions", f"play={play} max={play_max} open={play_open}")
                logger.info("bridge skip %s %s: play=%s max=%d reached", side, symbol, play, play_max)
                detail["action"] = "skipped_max_positions"
                result["skipped"] += 1
                result["details"].append(detail)
                continue

            # Gate: global max_positions reached
            open_count = _db.count_open_total()
            if open_count >= max_pos:
                _db.update_signal_status(signal_log_id, "skipped_max_positions", f"global max={max_pos} open={open_count}")
                logger.info("bridge skip %s %s: global max_positions=%d reached", side, symbol, max_pos)
                detail["action"] = "skipped_max_positions"
                result["skipped"] += 1
                result["details"].append(detail)
                continue

        # Execute outside the lock — Binance REST calls are slow.
        try:
            ok = execute_trade({
                "signal_log_id": signal_log_id,
                "symbol": symbol,
                "side": side,
                "sl_price": float(sl_price),
                "tp_price": float(tp_price),
                "notional_usdt": sig.get("virtual_notional_usdt"),
                "play": play,
            })
            detail["action"] = "traded" if ok else "error"
            if ok:
                result["traded"] += 1
            else:
                result["errors"] += 1
        except Exception as exc:
            logger.error("bridge execute_trade %s %s: %s", side, symbol, exc)
            _db.update_signal_status(signal_log_id, "error", str(exc))
            detail["action"] = "error"
            detail["error"] = str(exc)
            result["errors"] += 1

        result["details"].append(detail)

    if result["scanned"]:
        logger.info(
            "signal_bridge complete: scanned=%d traded=%d skipped=%d errors=%d",
            result["scanned"], result["traded"], result["skipped"], result["errors"],
        )
    return result
