"""SQLite database layer for the Binance live-trading bridge.

Database file: next-k-api/binance_bridge/binance.db (separate from accumulation.db).
Uses WAL journal mode and a process-level RLock to serialise writes.

close_reason values: 'tp' | 'sl' | 'expired' | 'manual' | 'unknown'
"""

from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional

# Honour DATA_DIR for Railway / Docker volume mounts.
_DATA_DIR = Path(os.getenv("DATA_DIR", Path(__file__).parent))
DB_PATH = _DATA_DIR / "binance.db"

# Serialises all writes; also used by signal_bridge for atomic check-then-insert.
_db_write_lock = threading.RLock()

# Default configuration values written on first init.
DEFAULT_CONFIG: Dict[str, str] = {
    "binance_api_key": "",
    "binance_api_secret": "",
    "testnet": "false",
    "enabled": "false",
    "margin_usdt": "100",
    "max_positions": "3",
    "leverage": "10",
    "enabled_sources": "zct_vwap",
    "position_expire_hours": "4",
}

# Map environment variables to config keys (applied on first init, env wins over defaults).
_ENV_TO_CONFIG: Dict[str, str] = {
    "BINANCE_API_KEY": "binance_api_key",
    "BINANCE_API_SECRET": "binance_api_secret",
    "BINANCE_TESTNET": "testnet",
    "BINANCE_MARGIN_USDT": "margin_usdt",
    "BINANCE_LEVERAGE": "leverage",
    "BINANCE_MAX_POSITIONS": "max_positions",
    "BINANCE_EXPIRE_HOURS": "position_expire_hours",
}

DDL = """
CREATE TABLE IF NOT EXISTS config (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS signals_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source          TEXT    NOT NULL,
    api_signal_id   TEXT    NOT NULL,
    symbol          TEXT    NOT NULL,
    side            TEXT    NOT NULL,
    entry_price     REAL,
    sl_price        REAL,
    tp_price        REAL,
    confidence      REAL,
    regime          TEXT,
    notional_usdt   REAL,
    received_at     TEXT    NOT NULL,
    status          TEXT    NOT NULL DEFAULT 'received',
    skip_reason     TEXT
);

-- Prevent double-processing the same signal from the same source.
CREATE UNIQUE INDEX IF NOT EXISTS ux_signal_source_id
    ON signals_log (source, api_signal_id);

CREATE TABLE IF NOT EXISTS positions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_log_id   INTEGER REFERENCES signals_log(id),
    symbol          TEXT    NOT NULL,
    side            TEXT    NOT NULL,
    entry_order_id  TEXT,
    sl_order_id     TEXT,
    tp_order_id     TEXT,
    entry_price     REAL,
    sl_price        REAL,
    tp_price        REAL,
    quantity        REAL,
    notional_usdt   REAL,
    leverage        INTEGER,
    opened_at       TEXT    NOT NULL,
    expire_at       TEXT,
    status          TEXT    NOT NULL DEFAULT 'open',
    close_reason    TEXT,
    close_price     REAL,
    closed_at       TEXT,
    pnl_usdt        REAL,
    pnl_pct         REAL
);
"""


@contextmanager
def get_db(write: bool = False) -> Generator[sqlite3.Connection, None, None]:
    """Open a per-call connection; optionally acquire the write lock.

    Always uses WAL mode for concurrent read safety.
    Pass write=True for any mutating operation.
    """
    if write:
        _db_write_lock.acquire()
    conn = sqlite3.connect(str(DB_PATH), timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
        if write:
            _db_write_lock.release()


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r["name"] == column for r in rows)


def init_db() -> None:
    """Create tables and seed default config on first run."""
    with get_db(write=True) as conn:
        conn.executescript(DDL)
        # Online migration: add expire_at if upgrading from a schema without it.
        if not _column_exists(conn, "positions", "expire_at"):
            conn.execute("ALTER TABLE positions ADD COLUMN expire_at TEXT")
        # Apply env vars first (win over defaults) — INSERT OR IGNORE so
        # user-set values already in DB are never overwritten on restart.
        for env_key, config_key in _ENV_TO_CONFIG.items():
            env_val = os.getenv(env_key, "").strip()
            if env_val:
                conn.execute(
                    "INSERT OR IGNORE INTO config (key, value) VALUES (?, ?)",
                    (config_key, env_val),
                )
        # Seed remaining defaults (INSERT OR IGNORE skips already-set keys).
        for k, v in DEFAULT_CONFIG.items():
            conn.execute(
                "INSERT OR IGNORE INTO config (key, value) VALUES (?, ?)", (k, v)
            )


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def get_config(key: str, default: str = "") -> str:
    with get_db() as conn:
        row = conn.execute(
            "SELECT value FROM config WHERE key = ?", (key,)
        ).fetchone()
    val = row["value"] if row else ""
    if val:
        return val
    if key == "margin_usdt":
        with get_db() as conn:
            row = conn.execute(
                "SELECT value FROM config WHERE key = ?", ("position_size_usdt",)
            ).fetchone()
        if row and row["value"]:
            return row["value"]
    return default


def get_all_config() -> Dict[str, str]:
    with get_db() as conn:
        rows = conn.execute("SELECT key, value FROM config").fetchall()
    return {r["key"]: r["value"] for r in rows}


def set_config(key: str, value: str) -> None:
    with get_db(write=True) as conn:
        conn.execute(
            "INSERT INTO config (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


def set_config_batch(pairs: Dict[str, str]) -> None:
    with get_db(write=True) as conn:
        for k, v in pairs.items():
            conn.execute(
                "INSERT INTO config (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (k, v),
            )


# ---------------------------------------------------------------------------
# signals_log helpers
# ---------------------------------------------------------------------------

def insert_signal(
    source: str,
    api_signal_id: str,
    symbol: str,
    side: str,
    entry_price: Optional[float],
    sl_price: Optional[float],
    tp_price: Optional[float],
    confidence: Optional[float],
    regime: Optional[str],
    notional_usdt: Optional[float],
    received_at: str,
    status: str = "received",
    skip_reason: Optional[str] = None,
) -> Optional[int]:
    """Insert signal; return rowid, or None if duplicate (UNIQUE constraint)."""
    try:
        with get_db(write=True) as conn:
            cur = conn.execute(
                """INSERT INTO signals_log
                   (source, api_signal_id, symbol, side, entry_price, sl_price,
                    tp_price, confidence, regime, notional_usdt, received_at,
                    status, skip_reason)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    source, api_signal_id, symbol, side, entry_price, sl_price,
                    tp_price, confidence, regime, notional_usdt, received_at,
                    status, skip_reason,
                ),
            )
            return cur.lastrowid
    except sqlite3.IntegrityError:
        return None  # duplicate


def update_signal_status(
    signal_id: int, status: str, skip_reason: Optional[str] = None
) -> None:
    with get_db(write=True) as conn:
        conn.execute(
            "UPDATE signals_log SET status=?, skip_reason=? WHERE id=?",
            (status, skip_reason, signal_id),
        )


def list_signals(limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM signals_log ORDER BY id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# positions helpers
# ---------------------------------------------------------------------------

def _compute_expire_at(expire_hours: float) -> str:
    """Return ISO8601 UTC timestamp expire_hours from now."""
    return (
        datetime.now(timezone.utc) + timedelta(hours=expire_hours)
    ).isoformat()


def insert_position(
    signal_log_id: int,
    symbol: str,
    side: str,
    entry_order_id: Optional[str],
    sl_order_id: Optional[str],
    tp_order_id: Optional[str],
    entry_price: Optional[float],
    sl_price: Optional[float],
    tp_price: Optional[float],
    quantity: Optional[float],
    notional_usdt: Optional[float],
    leverage: Optional[int],
    opened_at: str,
) -> int:
    expire_hours = float(get_config("position_expire_hours", "4"))
    expire_at = _compute_expire_at(expire_hours)
    with get_db(write=True) as conn:
        cur = conn.execute(
            """INSERT INTO positions
               (signal_log_id, symbol, side, entry_order_id, sl_order_id,
                tp_order_id, entry_price, sl_price, tp_price, quantity,
                notional_usdt, leverage, opened_at, expire_at, status)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,'open')""",
            (
                signal_log_id, symbol, side, entry_order_id, sl_order_id,
                tp_order_id, entry_price, sl_price, tp_price, quantity,
                notional_usdt, leverage, opened_at, expire_at,
            ),
        )
        return cur.lastrowid


def update_position_closed(
    position_id: int,
    close_reason: str,
    close_price: float,
    closed_at: str,
    pnl_usdt: float,
    pnl_pct: float,
) -> None:
    with get_db(write=True) as conn:
        conn.execute(
            """UPDATE positions
               SET status='closed', close_reason=?, close_price=?,
                   closed_at=?, pnl_usdt=?, pnl_pct=?
               WHERE id=? AND status='open'""",
            (close_reason, close_price, closed_at, pnl_usdt, pnl_pct, position_id),
        )


def list_positions(
    status: Optional[str] = None, limit: int = 100, offset: int = 0
) -> List[Dict[str, Any]]:
    with get_db() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM positions WHERE status=? ORDER BY id DESC LIMIT ? OFFSET ?",
                (status, limit, offset),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM positions ORDER BY id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
    return [dict(r) for r in rows]


def get_open_positions() -> List[Dict[str, Any]]:
    return list_positions(status="open", limit=500)


def get_open_expired_positions() -> List[Dict[str, Any]]:
    """Return open positions whose expire_at has passed."""
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM positions "
            "WHERE status='open' AND expire_at IS NOT NULL AND expire_at <= ?",
            (now,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_open_position_for_symbol(symbol: str) -> Optional[Dict[str, Any]]:
    """Return the first open position for a symbol, or None."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM positions WHERE status='open' AND symbol=? LIMIT 1",
            (symbol,),
        ).fetchone()
    return dict(row) if row else None


def get_position_by_id(position_id: int) -> Optional[Dict[str, Any]]:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM positions WHERE id=?", (position_id,)
        ).fetchone()
    return dict(row) if row else None


def pnl_summary() -> Dict[str, Any]:
    with get_db() as conn:
        row = conn.execute(
            """SELECT
               COALESCE(COUNT(*), 0)                                        AS total,
               COALESCE(SUM(CASE WHEN pnl_usdt > 0 THEN 1 ELSE 0 END), 0)  AS wins,
               COALESCE(SUM(CASE WHEN pnl_usdt <= 0 THEN 1 ELSE 0 END), 0) AS losses,
               COALESCE(SUM(pnl_usdt), 0.0)                                AS total_pnl,
               COALESCE(AVG(pnl_usdt), 0.0)                                AS avg_pnl
               FROM positions WHERE status='closed'"""
        ).fetchone()
        daily = conn.execute(
            """SELECT DATE(closed_at) AS day, COALESCE(SUM(pnl_usdt), 0.0) AS pnl
               FROM positions WHERE status='closed' AND closed_at IS NOT NULL
               GROUP BY day ORDER BY day DESC LIMIT 30"""
        ).fetchall()
    return {
        "total": int(row["total"]),
        "wins": int(row["wins"]),
        "losses": int(row["losses"]),
        "total_pnl": round(float(row["total_pnl"]), 4),
        "avg_pnl": round(float(row["avg_pnl"]), 4),
        "daily": [dict(r) for r in daily],
    }
