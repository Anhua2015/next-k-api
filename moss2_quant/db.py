"""Moss2 lane SQLite schema and helpers."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def migrate_moss2_tables(c: sqlite3.Cursor) -> None:
    c.execute(
        """CREATE TABLE IF NOT EXISTS moss2_robots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        template TEXT NOT NULL,
        layer_code TEXT NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1,
        candidate_symbols_json TEXT NOT NULL DEFAULT '[]',
        tactical_params_json TEXT NOT NULL DEFAULT '{}',
        current_symbol TEXT,
        cooldown_until_utc TEXT,
        created_at_utc TEXT NOT NULL,
        updated_at_utc TEXT NOT NULL
    )"""
    )
    c.execute(
        """CREATE TABLE IF NOT EXISTS moss2_symbol_layers (
        symbol TEXT PRIMARY KEY,
        layer_code TEXT NOT NULL,
        score REAL,
        note TEXT,
        updated_at_utc TEXT NOT NULL
    )"""
    )
    c.execute(
        """CREATE TABLE IF NOT EXISTS moss2_positions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        robot_id INTEGER NOT NULL,
        symbol TEXT NOT NULL,
        side TEXT NOT NULL,
        status TEXT NOT NULL,
        opened_at_utc TEXT NOT NULL,
        closed_at_utc TEXT,
        open_reason TEXT,
        close_reason TEXT,
        FOREIGN KEY (robot_id) REFERENCES moss2_robots(id)
    )"""
    )
    c.execute(
        """CREATE TABLE IF NOT EXISTS moss2_scan_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ran_at_utc TEXT NOT NULL,
        robots_scanned INTEGER NOT NULL,
        opens INTEGER NOT NULL,
        closes INTEGER NOT NULL,
        skips INTEGER NOT NULL,
        details_json TEXT NOT NULL DEFAULT '{}'
    )"""
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS ix_moss2_robots_enabled ON moss2_robots(enabled)"
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS ix_moss2_layers_layer ON moss2_symbol_layers(layer_code)"
    )
    c.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_moss2_positions_open_symbol ON moss2_positions(symbol) WHERE status='OPEN'"
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS ix_moss2_positions_robot_status ON moss2_positions(robot_id, status)"
    )


def list_robots(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM moss2_robots ORDER BY enabled DESC, id ASC"
    ).fetchall()
    out: List[Dict[str, Any]] = []
    for row in rows:
        d = dict(row)
        d["enabled"] = bool(d.get("enabled"))
        d["candidate_symbols"] = json.loads(d.get("candidate_symbols_json") or "[]")
        d["tactical_params"] = json.loads(d.get("tactical_params_json") or "{}")
        out.append(d)
    return out


def upsert_robot(
    conn: sqlite3.Connection,
    *,
    name: str,
    template: str,
    layer_code: str,
    candidate_symbols: List[str],
    tactical_params: Dict[str, Any],
    enabled: bool = True,
) -> Dict[str, Any]:
    now = _utc_now()
    conn.execute(
        """INSERT INTO moss2_robots(
               name, template, layer_code, enabled, candidate_symbols_json,
               tactical_params_json, created_at_utc, updated_at_utc
           ) VALUES (?,?,?,?,?,?,?,?)
           ON CONFLICT(name) DO UPDATE SET
               template=excluded.template,
               layer_code=excluded.layer_code,
               enabled=excluded.enabled,
               candidate_symbols_json=excluded.candidate_symbols_json,
               tactical_params_json=excluded.tactical_params_json,
               updated_at_utc=excluded.updated_at_utc""",
        (
            name.strip(),
            template.strip().lower(),
            layer_code.strip().upper(),
            1 if enabled else 0,
            json.dumps(candidate_symbols, ensure_ascii=False),
            json.dumps(tactical_params, ensure_ascii=False),
            now,
            now,
        ),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM moss2_robots WHERE name = ?", (name.strip(),)).fetchone()
    d = dict(row)
    d["enabled"] = bool(d.get("enabled"))
    d["candidate_symbols"] = json.loads(d.get("candidate_symbols_json") or "[]")
    d["tactical_params"] = json.loads(d.get("tactical_params_json") or "{}")
    return d


def list_symbol_layers(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM moss2_symbol_layers ORDER BY layer_code ASC, score DESC, symbol ASC"
    ).fetchall()
    return [dict(row) for row in rows]


def list_open_positions(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM moss2_positions WHERE status='OPEN' ORDER BY opened_at_utc ASC, id ASC"
    ).fetchall()
    return [dict(row) for row in rows]


def get_open_position_by_robot(conn: sqlite3.Connection, robot_id: int) -> Dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM moss2_positions WHERE robot_id=? AND status='OPEN' ORDER BY id DESC LIMIT 1",
        (int(robot_id),),
    ).fetchone()
    return dict(row) if row else None


def has_open_position_for_symbol(conn: sqlite3.Connection, symbol: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM moss2_positions WHERE symbol=? AND status='OPEN' LIMIT 1",
        (symbol.strip().upper(),),
    ).fetchone()
    return bool(row)


def open_position(
    conn: sqlite3.Connection,
    *,
    robot_id: int,
    symbol: str,
    side: str,
    reason: str,
) -> Dict[str, Any]:
    now = _utc_now()
    sym = symbol.strip().upper()
    conn.execute(
        """INSERT INTO moss2_positions(robot_id, symbol, side, status, opened_at_utc, open_reason)
           VALUES (?,?,?,?,?,?)""",
        (int(robot_id), sym, side.strip().upper(), "OPEN", now, reason),
    )
    conn.execute(
        "UPDATE moss2_robots SET current_symbol=?, updated_at_utc=? WHERE id=?",
        (sym, now, int(robot_id)),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM moss2_positions WHERE id=last_insert_rowid()").fetchone()
    return dict(row)


def close_position(
    conn: sqlite3.Connection,
    *,
    position_id: int,
    reason: str,
) -> Dict[str, Any]:
    now = _utc_now()
    pos = conn.execute(
        "SELECT * FROM moss2_positions WHERE id=?",
        (int(position_id),),
    ).fetchone()
    if not pos:
        raise ValueError(f"position not found: {position_id}")
    row = dict(pos)
    conn.execute(
        """UPDATE moss2_positions
           SET status='CLOSED', closed_at_utc=?, close_reason=?
           WHERE id=?""",
        (now, reason, int(position_id)),
    )
    conn.execute(
        "UPDATE moss2_robots SET current_symbol=NULL, updated_at_utc=? WHERE id=?",
        (now, int(row["robot_id"])),
    )
    conn.commit()
    out = conn.execute("SELECT * FROM moss2_positions WHERE id=?", (int(position_id),)).fetchone()
    return dict(out)


def set_robot_cooldown(conn: sqlite3.Connection, *, robot_id: int, cooldown_until_utc: str | None) -> None:
    conn.execute(
        "UPDATE moss2_robots SET cooldown_until_utc=?, updated_at_utc=? WHERE id=?",
        (cooldown_until_utc, _utc_now(), int(robot_id)),
    )
    conn.commit()


def insert_scan_run(
    conn: sqlite3.Connection,
    *,
    robots_scanned: int,
    opens: int,
    closes: int,
    skips: int,
    details: Dict[str, Any],
) -> int:
    cur = conn.execute(
        """INSERT INTO moss2_scan_runs(
               ran_at_utc, robots_scanned, opens, closes, skips, details_json
           ) VALUES (?,?,?,?,?,?)""",
        (
            _utc_now(),
            int(robots_scanned),
            int(opens),
            int(closes),
            int(skips),
            json.dumps(details or {}, ensure_ascii=False),
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def list_scan_runs(conn: sqlite3.Connection, *, limit: int = 20) -> List[Dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM moss2_scan_runs ORDER BY id DESC LIMIT ?",
        (max(1, int(limit)),),
    ).fetchall()
    out: List[Dict[str, Any]] = []
    for row in rows:
        d = dict(row)
        d["details"] = json.loads(d.get("details_json") or "{}")
        out.append(d)
    return out


def upsert_symbol_layer(
    conn: sqlite3.Connection,
    *,
    symbol: str,
    layer_code: str,
    score: float | None = None,
    note: str | None = None,
) -> Dict[str, Any]:
    now = _utc_now()
    sym = symbol.strip().upper()
    layer = layer_code.strip().upper()
    conn.execute(
        """INSERT INTO moss2_symbol_layers(symbol, layer_code, score, note, updated_at_utc)
           VALUES (?,?,?,?,?)
           ON CONFLICT(symbol) DO UPDATE SET
               layer_code=excluded.layer_code,
               score=excluded.score,
               note=excluded.note,
               updated_at_utc=excluded.updated_at_utc""",
        (sym, layer, score, note, now),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM moss2_symbol_layers WHERE symbol = ?", (sym,)).fetchone()
    return dict(row)

