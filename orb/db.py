"""ORB 纸面 SQLite 表。"""

from __future__ import annotations

import sqlite3
from typing import Any, Optional


def migrate_orb_tables(c: sqlite3.Cursor) -> None:
    c.execute(
        """CREATE TABLE IF NOT EXISTS orb_signals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        recorded_at_utc TEXT NOT NULL,
        updated_at_utc TEXT,
        symbol TEXT NOT NULL UNIQUE,
        play TEXT NOT NULL,
        side TEXT NOT NULL,
        confidence TEXT,
        entry_price REAL,
        entry_bar_open_ms INTEGER,
        sl_price REAL,
        tp_price REAL,
        r_unit REAL,
        virtual_notional_usdt REAL DEFAULT 1000,
        or_high REAL,
        or_low REAL,
        or_width_pct REAL,
        session_date TEXT,
        volume REAL,
        vol_ma REAL,
        mark_price REAL,
        unrealized_pnl_usdt REAL,
        outcome TEXT,
        outcome_at_utc TEXT,
        exit_price REAL,
        pnl_r REAL,
        pnl_usdt REAL,
        exit_rule TEXT,
        reasons_json TEXT,
        scan_params_json TEXT,
        notes TEXT
    )"""
    )
    c.execute(
        """CREATE TABLE IF NOT EXISTS orb_settlements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        settled_at_utc TEXT NOT NULL,
        signal_id INTEGER NOT NULL,
        symbol TEXT NOT NULL,
        side TEXT NOT NULL,
        play TEXT,
        outcome TEXT NOT NULL,
        entry_price REAL,
        exit_price REAL,
        pnl_r REAL,
        pnl_usdt REAL,
        virtual_notional_usdt REAL,
        exit_rule TEXT,
        session_date TEXT
    )"""
    )
    c.execute(
        """CREATE TABLE IF NOT EXISTS orb_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ran_at_utc TEXT NOT NULL,
        symbols_scanned INTEGER DEFAULT 0,
        opens INTEGER DEFAULT 0,
        resolves INTEGER DEFAULT 0,
        detail_json TEXT
    )"""
    )
    for sql in (
        "CREATE INDEX IF NOT EXISTS ix_orb_recorded ON orb_signals(recorded_at_utc)",
        "CREATE INDEX IF NOT EXISTS ix_orb_session ON orb_signals(session_date)",
        "CREATE INDEX IF NOT EXISTS ix_orb_settle_time ON orb_settlements(settled_at_utc)",
    ):
        try:
            c.execute(sql)
        except sqlite3.OperationalError:
            pass


def symbol_session_traded(cur: sqlite3.Cursor, symbol: str, session_date: str) -> bool:
    if not session_date:
        return False
    sym = str(symbol).strip().upper()
    day = str(session_date)
    cur.execute(
        """
        SELECT 1 FROM orb_settlements
        WHERE symbol = ? AND session_date = ?
        LIMIT 1
        """,
        (sym, day),
    )
    if cur.fetchone() is not None:
        return True
    cur.execute(
        """
        SELECT 1 FROM orb_signals
        WHERE symbol = ? AND session_date = ?
          AND side IN ('LONG','SHORT') AND entry_bar_open_ms IS NOT NULL
        LIMIT 1
        """,
        (sym, day),
    )
    return cur.fetchone() is not None


def fetch_open_hold(cur: sqlite3.Cursor, symbol: str, *, default_notional: float) -> Optional[sqlite3.Row]:
    cur.execute(
        """
        SELECT id, symbol, side, play, entry_price, sl_price, tp_price,
               COALESCE(virtual_notional_usdt, ?) AS notion, session_date
        FROM orb_signals
        WHERE symbol = ? AND outcome IS NULL
          AND sl_price IS NOT NULL AND side IN ('LONG','SHORT')
        """,
        (default_notional, str(symbol).strip().upper()),
    )
    return cur.fetchone()


def count_open_positions(cur: sqlite3.Cursor) -> int:
    cur.execute(
        """
        SELECT COUNT(*) FROM orb_signals
        WHERE outcome IS NULL AND side IN ('LONG','SHORT') AND sl_price IS NOT NULL
        """
    )
    return int(cur.fetchone()[0] or 0)


def fetch_open_for_resolve(cur: sqlite3.Cursor, *, default_notional: float) -> list[tuple[Any, ...]]:
    cur.execute(
        """
        SELECT id, symbol, side, play, entry_price, sl_price, tp_price,
               entry_bar_open_ms, COALESCE(virtual_notional_usdt, ?) AS notion
        FROM orb_signals
        WHERE outcome IS NULL AND sl_price IS NOT NULL AND entry_bar_open_ms IS NOT NULL
          AND side IN ('LONG','SHORT')
        ORDER BY id ASC
        """,
        (default_notional,),
    )
    return list(cur.fetchall())


def archive_settlement(
    cur: sqlite3.Cursor,
    *,
    signal_id: int,
    symbol: str,
    side: str,
    play: Optional[str],
    outcome: str,
    entry_price: float,
    exit_price: float,
    pnl_r: float,
    pnl_usdt: float,
    notional: float,
    exit_rule: str,
    settled_at_utc: str,
    session_date: Optional[str] = None,
) -> None:
    cur.execute(
        """
        INSERT INTO orb_settlements (
            settled_at_utc, signal_id, symbol, side, play, outcome,
            entry_price, exit_price, pnl_r, pnl_usdt, virtual_notional_usdt,
            exit_rule, session_date
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            settled_at_utc,
            signal_id,
            symbol,
            side,
            play,
            outcome,
            entry_price,
            exit_price,
            pnl_r,
            pnl_usdt,
            notional,
            exit_rule,
            session_date,
        ),
    )


def clear_orb_tables(conn: sqlite3.Connection) -> dict[str, int]:
    cur = conn.cursor()
    out: dict[str, int] = {}
    for table, key in (
        ("orb_settlements", "deleted_settlements"),
        ("orb_signals", "deleted_signals"),
        ("orb_runs", "deleted_runs"),
    ):
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
        if not cur.fetchone():
            out[key] = 0
            continue
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        n = int(cur.fetchone()[0] or 0)
        cur.execute(f"DELETE FROM {table}")
        out[key] = n
    conn.commit()
    return out
