"""King Keltner SQLite 表（kk_*，与 orb_* 隔离）。"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Dict, Optional


def migrate_kk_tables(c: sqlite3.Cursor) -> None:
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS kk_symbol_bots (
            symbol TEXT PRIMARY KEY,
            wallet_usdt REAL NOT NULL,
            updated_at_utc TEXT NOT NULL
        )
        """
    )
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS kk_symbol_state (
            symbol TEXT NOT NULL,
            session_date TEXT NOT NULL,
            last_bar_ms INTEGER NOT NULL DEFAULT 0,
            state_json TEXT NOT NULL DEFAULT '{}',
            PRIMARY KEY (symbol, session_date)
        )
        """
    )
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS kk_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_date TEXT NOT NULL,
            symbol TEXT NOT NULL,
            event TEXT NOT NULL,
            side TEXT,
            entry REAL,
            exit_px REAL,
            notional_usdt REAL,
            pnl_usdt_gross REAL,
            fee_usdt REAL,
            pnl_usdt REAL,
            outcome TEXT,
            detail_json TEXT,
            bar_ms INTEGER,
            created_at_utc TEXT NOT NULL
        )
        """
    )
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS kk_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ran_at_utc TEXT NOT NULL,
            session_date TEXT,
            symbols_scanned INTEGER DEFAULT 0,
            opens INTEGER DEFAULT 0,
            closes INTEGER DEFAULT 0,
            eod_closes INTEGER DEFAULT 0,
            detail_json TEXT
        )
        """
    )
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS kk_session_opens (
            session_date TEXT NOT NULL,
            symbol TEXT NOT NULL,
            opens INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (session_date, symbol)
        )
        """
    )


def load_wallet(cur: sqlite3.Cursor, symbol: str, *, default: float) -> float:
    cur.execute("SELECT wallet_usdt FROM kk_symbol_bots WHERE symbol = ?", (symbol,))
    row = cur.fetchone()
    if row is None:
        return float(default)
    return float(row[0] or default)


def save_wallet(cur: sqlite3.Cursor, symbol: str, wallet: float, *, now_utc: str) -> None:
    cur.execute(
        """
        INSERT INTO kk_symbol_bots (symbol, wallet_usdt, updated_at_utc)
        VALUES (?, ?, ?)
        ON CONFLICT(symbol) DO UPDATE SET
            wallet_usdt = excluded.wallet_usdt,
            updated_at_utc = excluded.updated_at_utc
        """,
        (symbol, round(float(wallet), 4), now_utc),
    )


def load_state_json(cur: sqlite3.Cursor, symbol: str, session_date: str) -> Dict[str, Any]:
    cur.execute(
        "SELECT state_json, last_bar_ms FROM kk_symbol_state WHERE symbol = ? AND session_date = ?",
        (symbol, session_date),
    )
    row = cur.fetchone()
    if not row:
        return {"last_bar_ms": 0}
    try:
        payload = json.loads(row[0] or "{}")
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    payload["last_bar_ms"] = int(row[1] or 0)
    return payload


def count_open_kk_positions(cur: sqlite3.Cursor, *, session_date: Optional[str] = None) -> int:
    if session_date:
        cur.execute(
            "SELECT state_json FROM kk_symbol_state WHERE session_date = ?",
            (session_date,),
        )
    else:
        cur.execute("SELECT state_json FROM kk_symbol_state")
    n = 0
    for row in cur.fetchall():
        try:
            payload = json.loads(row[0] or "{}")
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        ctx = payload.get("ctx") if isinstance(payload.get("ctx"), dict) else payload
        pos = ctx.get("pos") if isinstance(ctx.get("pos"), dict) else {}
        if int(pos.get("side") or 0) != 0:
            n += 1
    return n


def save_state_json(
    cur: sqlite3.Cursor,
    symbol: str,
    session_date: str,
    *,
    state: Dict[str, Any],
    last_bar_ms: int,
) -> None:
    cur.execute(
        """
        INSERT INTO kk_symbol_state (symbol, session_date, last_bar_ms, state_json)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(symbol, session_date) DO UPDATE SET
            last_bar_ms = excluded.last_bar_ms,
            state_json = excluded.state_json
        """,
        (symbol, session_date, int(last_bar_ms), json.dumps(state, ensure_ascii=False)),
    )


def session_open_count(cur: sqlite3.Cursor, session_date: str, symbol: str) -> int:
    cur.execute(
        "SELECT opens FROM kk_session_opens WHERE session_date = ? AND symbol = ?",
        (session_date, symbol),
    )
    row = cur.fetchone()
    return int(row[0]) if row else 0


def incr_session_opens(cur: sqlite3.Cursor, session_date: str, symbol: str) -> None:
    cur.execute(
        """
        INSERT INTO kk_session_opens (session_date, symbol, opens)
        VALUES (?, ?, 1)
        ON CONFLICT(session_date, symbol) DO UPDATE SET opens = opens + 1
        """,
        (session_date, symbol),
    )


def decr_session_opens(cur: sqlite3.Cursor, session_date: str, symbol: str) -> None:
    cur.execute(
        """
        UPDATE kk_session_opens SET opens = MAX(0, opens - 1)
        WHERE session_date = ? AND symbol = ?
        """,
        (session_date, symbol),
    )


def insert_trade(cur: sqlite3.Cursor, row: Dict[str, Any]) -> None:
    cur.execute(
        """
        INSERT INTO kk_trades
            (session_date, symbol, event, side, entry, exit_px, notional_usdt,
             pnl_usdt_gross, fee_usdt, pnl_usdt, outcome, detail_json, bar_ms, created_at_utc)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            row.get("session_date"),
            row.get("symbol"),
            row.get("event"),
            row.get("side"),
            row.get("entry"),
            row.get("exit_px"),
            row.get("notional_usdt"),
            row.get("pnl_usdt_gross"),
            row.get("fee_usdt"),
            row.get("pnl_usdt"),
            row.get("outcome"),
            json.dumps(row.get("detail") or {}, ensure_ascii=False),
            row.get("bar_ms"),
            row.get("created_at_utc"),
        ),
    )


def insert_run(cur: sqlite3.Cursor, row: Dict[str, Any]) -> None:
    cur.execute(
        """
        INSERT INTO kk_runs (ran_at_utc, session_date, symbols_scanned, opens, closes, eod_closes, detail_json)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            row.get("ran_at_utc"),
            row.get("session_date"),
            int(row.get("symbols_scanned") or 0),
            int(row.get("opens") or 0),
            int(row.get("closes") or 0),
            int(row.get("eod_closes") or 0),
            json.dumps(row.get("detail") or {}, ensure_ascii=False),
        ),
    )
