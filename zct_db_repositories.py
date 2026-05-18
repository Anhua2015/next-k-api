#!/usr/bin/env python3
"""ZCT / 雷达 SQLite 仓储层：集中 SQL、WAL 连接、冷却批量查询。"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
from dataclasses import dataclass
from typing import Any, Callable, List, Optional, Sequence, Set, Tuple

from accumulation_radar import init_db

logger = logging.getLogger(__name__)


@dataclass
class PersistScanLimits:
    max_open_positions: int = 0
    max_open_play01: int = 0
    max_open_play02: int = 0
    db_skip_flat: bool = False
    default_notional_usdt: float = 1000.0


@dataclass
class PersistScanStats:
    written: int = 0
    skipped_open: int = 0
    skipped_open_cap: int = 0
    skipped_play01_cap: int = 0
    skipped_play02_cap: int = 0


@dataclass(frozen=True)
class PersistScanCallbacks:
    """scanner 传入的门控与 supersede 结算（避免 repositories 依赖 SignalResult）。"""

    is_open_hold_row: Callable[[Any], bool]
    scan_supersedes_open_hold: Callable[[str, Any], bool]
    play_is_play01: Callable[[Optional[str]], bool]
    play_is_play02: Callable[[Optional[str]], bool]
    settle_supersede: Callable[[sqlite3.Cursor, Tuple[Any, ...], Any, str], None]


def signals_table_ident() -> str:
    import os

    raw = (os.getenv("ZCT_DB_SIGNALS_TABLE") or "zct_vwap_signals").strip()
    if raw and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", raw):
        return raw
    return "zct_vwap_signals"


def settlements_table_ident() -> str:
    import os

    raw = (os.getenv("ZCT_DB_SETTLEMENTS_TABLE") or "zct_vwap_settlements").strip()
    if raw and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", raw):
        return raw
    return "zct_vwap_settlements"


class CooldownRepository:
    """`zct_symbol_cooldown` 读写。"""

    TABLE = "zct_symbol_cooldown"

    def __init__(self, conn: Optional[sqlite3.Connection] = None) -> None:
        self._conn = conn
        self._own = conn is None

    def _connection(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        return init_db()

    def close_if_owned(self) -> None:
        if self._own and self._conn is not None:
            self._conn.close()
            self._conn = None

    def is_symbol_in_cooldown(self, symbol: str) -> bool:
        sym = symbol.strip().upper()
        now_ms = int(time.time() * 1000)
        conn = self._connection()
        try:
            cur = conn.cursor()
            cur.execute(
                f"SELECT cooldown_until_ms FROM {self.TABLE} WHERE symbol = ?",
                (sym,),
            )
            row = cur.fetchone()
            if not row:
                return False
            return int(row[0]) > now_ms
        finally:
            if self._own:
                conn.close()

    def symbols_in_cooldown(self, symbols: List[str]) -> Set[str]:
        syms = sorted({s.strip().upper() for s in symbols if s and str(s).strip()})
        if not syms:
            return set()
        now_ms = int(time.time() * 1000)
        conn = self._connection()
        try:
            cur = conn.cursor()
            placeholders = ",".join("?" * len(syms))
            cur.execute(
                f"""
                SELECT symbol FROM {self.TABLE}
                WHERE symbol IN ({placeholders}) AND cooldown_until_ms > ?
                """,
                (*syms, now_ms),
            )
            return {str(r[0]).strip().upper() for r in cur.fetchall() if r and r[0]}
        finally:
            if self._own:
                conn.close()

    def merge_cooldown(self, cur: sqlite3.Cursor, symbol: str, until_ms: int) -> None:
        sym = str(symbol).strip().upper()
        cur.execute(
            f"SELECT cooldown_until_ms FROM {self.TABLE} WHERE symbol = ?",
            (sym,),
        )
        row = cur.fetchone()
        prev = int(row[0]) if row else 0
        final = max(prev, int(until_ms))
        cur.execute(
            f"""
            INSERT OR REPLACE INTO {self.TABLE} (symbol, cooldown_until_ms)
            VALUES (?, ?)
            """,
            (sym, final),
        )


class SignalRepository:
    """`zct_vwap_signals` 持仓计数与 UPSERT（逐步从 scanner 迁入）。"""

    def __init__(
        self,
        conn: Optional[sqlite3.Connection] = None,
        *,
        signals_table: Optional[str] = None,
        settlements_table: Optional[str] = None,
    ) -> None:
        self._conn = conn
        self._own = conn is None
        self.signals_table = signals_table or signals_table_ident()
        self.settlements_table = settlements_table or settlements_table_ident()

    def connection(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        return init_db()

    def close_if_owned(self) -> None:
        if self._own and self._conn is not None:
            self._conn.close()
            self._conn = None

    def count_open_positions(self, cur: sqlite3.Cursor) -> int:
        cur.execute(
            f"""
            SELECT COUNT(*) FROM {self.signals_table}
            WHERE outcome IS NULL AND side IN ('LONG','SHORT')
              AND sl_price IS NOT NULL
            """
        )
        return int(cur.fetchone()[0] or 0)

    def daily_pnl_sum(self, cur: sqlite3.Cursor, start_iso: str) -> float:
        cur.execute(
            f"""
            SELECT COALESCE(SUM(pnl_usdt), 0)
            FROM {self.settlements_table}
            WHERE settled_at_utc >= ?
            """,
            (start_iso,),
        )
        return float(cur.fetchone()[0] or 0)

    def fetch_symbols_with_open_positions(self, cur: sqlite3.Cursor) -> Set[str]:
        cur.execute(
            f"""
            SELECT DISTINCT symbol FROM {self.signals_table}
            WHERE outcome IS NULL
              AND sl_price IS NOT NULL
              AND side IN ('LONG', 'SHORT')
            """
        )
        return {str(row[0]) for row in cur.fetchall() if row and row[0]}

    def symbol_has_open_position(self, cur: sqlite3.Cursor, symbol: str) -> bool:
        sym = str(symbol).strip().upper()
        if not sym:
            return False
        cur.execute(
            f"""
            SELECT 1 FROM {self.signals_table}
            WHERE symbol = ? AND outcome IS NULL
              AND sl_price IS NOT NULL
              AND side IN ('LONG', 'SHORT')
            LIMIT 1
            """,
            (sym,),
        )
        return cur.fetchone() is not None

    def count_open_play_family(self, cur: sqlite3.Cursor, family: str) -> int:
        prefix = str(family).strip().upper()
        if not prefix.startswith("PLAY") or len(prefix) < 6:
            return 0
        cur.execute(
            f"""
            SELECT COUNT(*) FROM {self.signals_table}
            WHERE outcome IS NULL
              AND sl_price IS NOT NULL
              AND side IN ('LONG', 'SHORT')
              AND play LIKE ?
            """,
            (f"{prefix}%",),
        )
        return int(cur.fetchone()[0] or 0)

    def fetch_open_hold_row(
        self, cur: sqlite3.Cursor, symbol: str, *, default_notional: float
    ) -> Optional[Tuple[Any, ...]]:
        """未平仓方向单行：id, symbol, side, play, entry, sl, tp, notional。"""
        cur.execute(
            f"""
            SELECT id, symbol, side, play, entry_price, sl_price, tp_price,
                   COALESCE(virtual_notional_usdt, ?)
            FROM {self.signals_table}
            WHERE symbol = ? AND outcome IS NULL
              AND sl_price IS NOT NULL AND side IN ('LONG','SHORT')
            """,
            (default_notional, str(symbol).strip().upper()),
        )
        return cur.fetchone()

    def delete_symbol_snapshot(self, cur: sqlite3.Cursor, symbol: str) -> None:
        cur.execute(
            f"DELETE FROM {self.signals_table} WHERE symbol = ?",
            (str(symbol).strip().upper(),),
        )

    def insert_settlement(
        self,
        cur: sqlite3.Cursor,
        *,
        settled_at_utc: str,
        signal_id: int,
        symbol: str,
        side: str,
        play: Optional[str],
        outcome: str,
        entry_price: float,
        exit_price: float,
        pnl_r: float,
        pnl_usdt: float,
        virtual_notional_usdt: float,
    ) -> None:
        cur.execute(
            f"""
            INSERT INTO {self.settlements_table} (
                settled_at_utc, signal_id, symbol, side, play, outcome,
                entry_price, exit_price, pnl_r, pnl_usdt, virtual_notional_usdt
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
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
                virtual_notional_usdt,
            ),
        )

    def fetch_open_signals_for_resolve(
        self, cur: sqlite3.Cursor, *, default_notional_usdt: float
    ) -> List[Tuple[Any, ...]]:
        """待结算方向单：id, symbol, side, play, entry, sl, tp, bar_open_ms, notion。"""
        cur.execute(
            f"""
            SELECT id, symbol, side, play, entry_price, sl_price, tp_price, entry_bar_open_ms,
                   COALESCE(virtual_notional_usdt, ?) AS notion
            FROM {self.signals_table}
            WHERE outcome IS NULL
              AND sl_price IS NOT NULL AND tp_price IS NOT NULL
              AND side IN ('LONG','SHORT')
            ORDER BY id ASC
            """,
            (default_notional_usdt,),
        )
        return list(cur.fetchall())

    def update_resolved_signal(
        self,
        cur: sqlite3.Cursor,
        signal_id: int,
        *,
        outcome: str,
        outcome_at_utc: str,
        exit_price: float,
        pnl_r: float,
        pnl_usdt: float,
        note: str,
    ) -> int:
        cur.execute(
            f"""
            UPDATE {self.signals_table}
            SET outcome = ?, outcome_at_utc = ?, exit_price = ?, pnl_r = ?, pnl_usdt = ?,
                notes = CASE WHEN notes IS NULL OR notes = '' THEN ?
                             ELSE notes || '; ' || ? END
            WHERE id = ? AND outcome IS NULL
            """,
            (
                outcome,
                outcome_at_utc,
                exit_price,
                round(pnl_r, 6),
                round(pnl_usdt, 4),
                note,
                note,
                signal_id,
            ),
        )
        return int(cur.rowcount or 0)

    def _upsert_signal_sql(self) -> str:
        t = self.signals_table
        return f"""
            INSERT INTO {t} (
                recorded_at_utc, symbol, play, side, confidence, regime,
                entry_price, entry_bar_open_ms, sl_price, tp_price, r_unit,
                virtual_notional_usdt,
                vwap, vwap_upper, vwap_lower,
                slope_bps, band_width_pct, vwap_crosses, ma_crosses, chop_score,
                bands_wide, bands_tight, slope_steep, slope_flat,
                ref_levels_json, nearest_levels_json, reasons_json, scan_params_json,
                setup_level, vwap_cross_bucket, position_vs_vwap,
                outcome, outcome_at_utc, exit_price, pnl_r, pnl_usdt
            ) VALUES (
                ?,?,?,?,?,?,
                ?,?,?,?,?,
                ?,
                ?,?,?,
                ?,?,?,?,?,
                ?,?,?,?,
                ?,?,?,?,
                ?,?,?,
                NULL, NULL, NULL, NULL, NULL
            )
            ON CONFLICT(symbol) DO UPDATE SET
                recorded_at_utc = excluded.recorded_at_utc,
                play = excluded.play,
                side = excluded.side,
                confidence = excluded.confidence,
                regime = excluded.regime,
                entry_price = excluded.entry_price,
                entry_bar_open_ms = excluded.entry_bar_open_ms,
                sl_price = excluded.sl_price,
                tp_price = excluded.tp_price,
                r_unit = excluded.r_unit,
                virtual_notional_usdt = excluded.virtual_notional_usdt,
                vwap = excluded.vwap,
                vwap_upper = excluded.vwap_upper,
                vwap_lower = excluded.vwap_lower,
                slope_bps = excluded.slope_bps,
                band_width_pct = excluded.band_width_pct,
                vwap_crosses = excluded.vwap_crosses,
                ma_crosses = excluded.ma_crosses,
                chop_score = excluded.chop_score,
                bands_wide = excluded.bands_wide,
                bands_tight = excluded.bands_tight,
                slope_steep = excluded.slope_steep,
                slope_flat = excluded.slope_flat,
                ref_levels_json = excluded.ref_levels_json,
                nearest_levels_json = excluded.nearest_levels_json,
                reasons_json = excluded.reasons_json,
                scan_params_json = excluded.scan_params_json,
                setup_level = excluded.setup_level,
                vwap_cross_bucket = excluded.vwap_cross_bucket,
                position_vs_vwap = excluded.position_vs_vwap,
                outcome = excluded.outcome,
                outcome_at_utc = excluded.outcome_at_utc,
                exit_price = excluded.exit_price,
                pnl_r = excluded.pnl_r,
                pnl_usdt = excluded.pnl_usdt,
                manual_entry_price = {t}.manual_entry_price,
                manual_exit_price = {t}.manual_exit_price,
                manual_notes = {t}.manual_notes,
                notes = {t}.notes
        """

    def upsert_signal_snapshot(
        self,
        cur: sqlite3.Cursor,
        row: Any,
        *,
        recorded_at_utc: str,
        scan_params_json: str,
        default_notional_usdt: float,
    ) -> None:
        notion = (
            float(row.paper_notional_usdt)
            if getattr(row, "paper_notional_usdt", None) is not None
            else default_notional_usdt
        )
        cur.execute(
            self._upsert_signal_sql(),
            (
                recorded_at_utc,
                row.symbol,
                row.play,
                row.side,
                row.confidence,
                row.regime,
                row.price,
                row.entry_bar_open_ms,
                row.sl_price,
                row.tp_price,
                row.r_unit,
                notion,
                row.vwap,
                row.vwap_upper,
                row.vwap_lower,
                row.slope_bps,
                row.band_width_pct,
                row.vwap_crosses,
                row.ma_crosses,
                row.chop_score,
                int(bool(row.bands_wide)),
                int(bool(row.bands_tight)),
                int(bool(row.slope_steep)),
                int(bool(row.slope_flat)),
                json.dumps(row.ref_levels, ensure_ascii=False),
                json.dumps(row.nearest_levels, ensure_ascii=False),
                json.dumps(row.reasons, ensure_ascii=False),
                scan_params_json,
                row.setup_level,
                row.vwap_cross_bucket,
                row.position_vs_vwap,
            ),
        )

    def persist_scan_results(
        self,
        cur: sqlite3.Cursor,
        *,
        recorded_at_utc: str,
        rows: Sequence[Any],
        scan_params_json: str,
        limits: PersistScanLimits,
        callbacks: PersistScanCallbacks,
    ) -> PersistScanStats:
        """扫描结果落库：持仓保护、supersede、仓位上限、FLAT 跳过。"""
        stats = PersistScanStats()
        open_syms = self.fetch_symbols_with_open_positions(cur)
        open_position_count = self.count_open_positions(cur)
        open_play01_count = self.count_open_play_family(cur, "PLAY01")
        open_play02_count = self.count_open_play_family(cur, "PLAY02")

        for r in rows:
            had_hold = False
            superseded = False
            if r.symbol in open_syms:
                hold = self.fetch_open_hold_row(
                    cur, r.symbol, default_notional=limits.default_notional_usdt
                )
                if not hold:
                    open_syms.discard(r.symbol)
                else:
                    had_hold = True
                    db_side = str(hold[2])
                    if callbacks.scan_supersedes_open_hold(db_side, r):
                        superseded = True
                        callbacks.settle_supersede(cur, hold, r, recorded_at_utc)
                        open_syms.discard(r.symbol)
                    else:
                        stats.skipped_open += 1
                        logger.info(
                            "[db] skip %s: 已有未平仓记录（持仓中），保留该行（不覆盖、不删除）",
                            r.symbol,
                        )
                        continue
            if (
                callbacks.is_open_hold_row(r)
                and not superseded
                and not had_hold
                and limits.max_open_positions > 0
                and open_position_count >= limits.max_open_positions
            ):
                stats.skipped_open_cap += 1
                logger.info(
                    "[db] skip %s: 未平仓已达 %s>=%s，不再新开仓（同标的反向 supersede 不受限）",
                    r.symbol,
                    open_position_count,
                    limits.max_open_positions,
                )
                continue
            if (
                callbacks.is_open_hold_row(r)
                and not superseded
                and not had_hold
                and limits.max_open_play01 > 0
                and callbacks.play_is_play01(r.play)
                and open_play01_count >= limits.max_open_play01
            ):
                stats.skipped_play01_cap += 1
                logger.info(
                    "[db] skip %s: PLAY01 未平仓已达 %s>=%s，不再新开 PLAY01",
                    r.symbol,
                    open_play01_count,
                    limits.max_open_play01,
                )
                continue
            if (
                callbacks.is_open_hold_row(r)
                and not superseded
                and not had_hold
                and limits.max_open_play02 > 0
                and callbacks.play_is_play02(r.play)
                and open_play02_count >= limits.max_open_play02
            ):
                stats.skipped_play02_cap += 1
                logger.info(
                    "[db] skip %s: PLAY02 未平仓已达 %s>=%s，不再新开 PLAY02",
                    r.symbol,
                    open_play02_count,
                    limits.max_open_play02,
                )
                continue
            if limits.db_skip_flat and r.side == "FLAT":
                self.delete_symbol_snapshot(cur, r.symbol)
                continue
            self.upsert_signal_snapshot(
                cur,
                r,
                recorded_at_utc=recorded_at_utc,
                scan_params_json=scan_params_json,
                default_notional_usdt=limits.default_notional_usdt,
            )
            stats.written += 1
            if callbacks.is_open_hold_row(r):
                open_syms.add(r.symbol)
                if not had_hold:
                    open_position_count += 1
                    if callbacks.play_is_play01(r.play):
                        open_play01_count += 1
                    if callbacks.play_is_play02(r.play):
                        open_play02_count += 1

        if stats.skipped_open:
            logger.info("[db] skipped_open_hold=%s", stats.skipped_open)
        if stats.skipped_open_cap:
            logger.info("[db] skipped_open_position_cap=%s", stats.skipped_open_cap)
        if stats.skipped_play01_cap:
            logger.info("[db] skipped_open_play01_cap=%s", stats.skipped_play01_cap)
        if stats.skipped_play02_cap:
            logger.info("[db] skipped_open_play02_cap=%s", stats.skipped_play02_cap)
        return stats
