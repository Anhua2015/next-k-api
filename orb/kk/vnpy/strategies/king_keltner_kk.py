"""King Keltner + RTH/EOD（vnpy 原版逻辑上叠加 next-k-api 会话规则）。"""

from __future__ import annotations

from typing import Optional

import pandas as pd

from orb.kk.config import KKConfig
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

    parameters = KingKeltnerStrategy.parameters + [
        "kk_rth_only",
        "kk_eod_flat",
        "kk_exit_hour",
        "kk_exit_minute",
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
        ts = self._bar_session_ts(bar)
        return ts.hour > int(self.kk_exit_hour) or (
            ts.hour == int(self.kk_exit_hour) and ts.minute >= int(self.kk_exit_minute)
        )

    def _trailing_sl_price(self) -> Optional[float]:
        if self.pos > 0:
            return float(self.intra_trade_high) * (1 - float(self.trailing_percent) / 100.0)
        if self.pos < 0:
            return float(self.intra_trade_low) * (1 + float(self.trailing_percent) / 100.0)
        return None

    def on_bar(self, bar: BarData) -> None:
        if self._is_eod_bar(bar) and self.pos != 0:
            self.cancel_all()
            if self.pos > 0:
                self.sell(bar.close_price, abs(self.pos))
            elif self.pos < 0:
                self.cover(bar.close_price, abs(self.pos))
            return
        if not self._in_rth(bar):
            kk = KKConfig.from_env()
            if kk.vnpy_idle_outside_rth and self.pos == 0:
                self.cancel_all()
            return
        super().on_bar(bar)

    def on_5min_bar(self, bar: BarData) -> None:
        if not self._in_rth(bar):
            kk = KKConfig.from_env()
            if kk.vnpy_idle_outside_rth and self.pos == 0:
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
            self._refresh_compound_size()
