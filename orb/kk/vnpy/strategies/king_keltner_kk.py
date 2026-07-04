"""King Keltner + RTH/EOD（vnpy 原版逻辑上叠加 next-k-api 会话规则）。"""

from __future__ import annotations

from typing import Optional

import pandas as pd

from orb.kk.config import KKConfig
from orb.kk.eod import should_eod_flat_bar
from orb.kk.vnpy.bootstrap import ensure_vnpy_path

ensure_vnpy_path()

from vnpy_ctastrategy import BarData
from vnpy_ctastrategy.strategies.king_keltner_strategy import KingKeltnerStrategy


class KingKeltnerKkStrategy(KingKeltnerStrategy):
    """在 vnpy KingKeltnerStrategy 上增加 RTH 过滤与 EOD 强平。"""

    kk_rth_only: bool = True
    kk_eod_flat: bool = True
    kk_exit_hour: int = 15
    kk_exit_minute: int = 55
    kk_no_entry_after_hour: int = 12
    kk_no_entry_after_minute: int = 0

    parameters = KingKeltnerStrategy.parameters + [
        "kk_rth_only",
        "kk_eod_flat",
        "kk_exit_hour",
        "kk_exit_minute",
        "kk_no_entry_after_hour",
        "kk_no_entry_after_minute",
    ]

    def __init__(self, cta_engine, strategy_name, vt_symbol, setting):
        super().__init__(cta_engine, strategy_name, vt_symbol, setting)

    @classmethod
    def from_kk_config(cls, kk: KKConfig) -> dict:
        return {
            "kk_rth_only": bool(kk.rth_only),
            "kk_eod_flat": bool(kk.eod_flat),
            "kk_exit_hour": int(kk.exit_hour),
            "kk_exit_minute": int(kk.exit_minute),
            "kk_no_entry_after_hour": int(kk.no_entry_after_hour),
            "kk_no_entry_after_minute": int(kk.no_entry_after_minute),
        }

    def _session_cfg(self):
        return KKConfig.from_env().orb_session_cfg()

    def _bar_session_ts(self, bar: BarData) -> pd.Timestamp:
        cfg = self._session_cfg()
        ms = int(bar.datetime.timestamp() * 1000)
        return pd.Timestamp(ms, unit="ms", tz=cfg.session_tz)

    def _in_rth(self, bar: BarData) -> bool:
        if not self.kk_rth_only:
            return True
        from orb.core.paper import in_regular_session

        ms = int(bar.datetime.timestamp() * 1000)
        return bool(in_regular_session(self._session_cfg(), now_ms=ms))

    def _is_eod_bar(self, bar: BarData) -> bool:
        if not self.kk_eod_flat:
            return False
        cfg = self._session_cfg()
        ms = int(bar.datetime.timestamp() * 1000)
        ts = self._bar_session_ts(bar)
        return should_eod_flat_bar(
            bar_ms=ms,
            ts=ts,
            cfg=cfg,
            exit_hour=int(self.kk_exit_hour),
            exit_minute=int(self.kk_exit_minute),
        )

    def _should_flatten_eod(self, bar: BarData) -> bool:
        if not self.kk_eod_flat or self.pos == 0:
            return False
        return self._is_eod_bar(bar) or not self._in_rth(bar)

    def _flatten_at_bar(self, bar: BarData) -> None:
        if self.pos == 0:
            return
        pending = list(getattr(self, "vt_orderids", None) or [])
        if pending:
            return
        self.cancel_all()
        vol = abs(self.pos)
        side = "LONG" if self.pos > 0 else "SHORT"
        msg = f"EOD flatten {self.vt_symbol} {side} vol={vol} px={bar.close_price}"
        write_log = getattr(self, "write_log", None)
        if callable(write_log):
            write_log(msg)
        if self.pos > 0:
            self.sell(bar.close_price, vol)
        elif self.pos < 0:
            self.cover(bar.close_price, vol)

    def _past_entry_cutoff(self, bar: BarData) -> bool:
        """>= kk_no_entry_after_* 后禁止新开仓（含该时刻）。"""
        h_limit = int(self.kk_no_entry_after_hour)
        if h_limit < 0:
            return False
        ts = self._bar_session_ts(bar)
        m_limit = int(self.kk_no_entry_after_minute or 0)
        if ts.hour > h_limit:
            return True
        if ts.hour == h_limit and ts.minute >= m_limit:
            return True
        return False

    def _trailing_sl_price(self) -> Optional[float]:
        if self.pos > 0:
            return float(self.intra_trade_high) * (1 - float(self.trailing_percent) / 100.0)
        if self.pos < 0:
            return float(self.intra_trade_low) * (1 + float(self.trailing_percent) / 100.0)
        return None

    def on_bar(self, bar: BarData) -> None:
        if self._should_flatten_eod(bar):
            self._flatten_at_bar(bar)
            return
        if not self._in_rth(bar):
            kk = KKConfig.from_env()
            if kk.vnpy_idle_outside_rth:
                self.cancel_all()
            return
        super().on_bar(bar)

    def on_5min_bar(self, bar: BarData) -> None:
        if self._should_flatten_eod(bar):
            self._flatten_at_bar(bar)
            return
        if not self._in_rth(bar):
            kk = KKConfig.from_env()
            if kk.vnpy_idle_outside_rth:
                self.cancel_all()
            return
        if self._past_entry_cutoff(bar) and self.pos == 0:
            self.cancel_all()
            return
        super().on_5min_bar(bar)

    def _refresh_compound_size(self) -> None:
        kk = KKConfig.from_env()
        if not kk.compound:
            return
        from binance_fapi import fetch_mark_price
        from orb.kk.vnpy.sizing import fixed_size_for_symbol
        from orb.kk.vnpy.binance_gateway import kk_symbol_from_vt

        sym = kk_symbol_from_vt(self.vt_symbol)
        px = fetch_mark_price(sym) or 100.0
        eq = float(kk.equity_usdt or 14.0)
        if kk.compound:
            try:
                from accumulation_radar import init_db
                from orb.kk.db import migrate_kk_tables
                from orb.kk.equity import symbol_equity_usdt

                conn = init_db()
                try:
                    cur = conn.cursor()
                    migrate_kk_tables(cur)
                    eq = symbol_equity_usdt(kk, sym, cur=cur)
                finally:
                    conn.close()
            except Exception:
                pass
        vol = fixed_size_for_symbol(kk, sym, px, equity_usdt=eq, orb_cfg=kk.orb_session_cfg())
        if vol <= 0 or abs(float(self.fixed_size) - vol) < 1e-6:
            return
        self.fixed_size = vol
        if self.cta_engine:
            setting = {**KingKeltnerKkStrategy.from_kk_config(kk), "fixed_size": vol}
            self.cta_engine.update_strategy_setting(self.strategy_name, setting)

    def on_trade(self, trade) -> None:
        super().on_trade(trade)
        if self.pos == 0:
            self.cancel_all()
            self._refresh_compound_size()
