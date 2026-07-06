"""King Keltner 回测策略：复利用模拟钱包，不依赖 DB / 实时行情。"""

from __future__ import annotations

from orb.kk.config import KKConfig
from orb.kk.vnpy.bootstrap import ensure_vnpy_path

ensure_vnpy_path()

from vnpy_ctastrategy.strategies.king_keltner_strategy import KingKeltnerStrategy

from orb.kk.vnpy.binance_gateway import kk_symbol_from_vt
from orb.kk.vnpy.sizing import fixed_size_for_symbol
from orb.kk.vnpy.strategies.king_keltner_kk import KingKeltnerKkStrategy


class KingKeltnerKkBacktestStrategy(KingKeltnerKkStrategy):
    """vnpy BacktestingEngine 专用：平仓后按 kk_bt_wallet 刷新 fixed_size。"""

    kk_bt_wallet: float = 14.0

    parameters = KingKeltnerKkStrategy.parameters + ["kk_bt_wallet"]

    def __init__(self, cta_engine, strategy_name, vt_symbol, setting):
        super().__init__(cta_engine, strategy_name, vt_symbol, setting)
        self._bt_entry_px: float | None = None
        self._bt_entry_side: int = 0
        self._bt_last_px: float = 0.0

    def _refresh_compound_size(self) -> None:
        kk = KKConfig.from_env()
        if not kk.compound:
            return
        sym = kk_symbol_from_vt(self.vt_symbol)
        px = float(self._bt_last_px or 100.0)
        eq = max(0.01, float(getattr(self, "kk_bt_wallet", kk.equity_usdt or 14.0)))
        vol = fixed_size_for_symbol(kk, sym, px, equity_usdt=eq, orb_cfg=kk.orb_session_cfg())
        if vol <= 0 or abs(float(self.fixed_size) - vol) < 1e-6:
            return
        self.fixed_size = vol

    def _apply_close_pnl(self, trade) -> None:
        entry = self._bt_entry_px
        if entry is None:
            return
        px = float(trade.price)
        vol = float(trade.volume)
        side = int(self._bt_entry_side)
        gross = (px - entry) * vol if side > 0 else (entry - px) * vol
        kk = KKConfig.from_env()
        rate = float(kk.fee_taker_bps or 4.0) / 10_000.0
        fee = (entry * vol + px * vol) * rate
        self.kk_bt_wallet = max(0.01, float(self.kk_bt_wallet) + gross - fee)
        self._bt_entry_px = None
        self._bt_entry_side = 0

    def _flatten_at_bar(self, bar) -> None:
        """回测最后一根 RTH bar：即时按 close 成交（先撤单，避免 trailing stop 阻塞 EOD）。"""
        if self.pos == 0:
            return
        self.cancel_all()
        vol = abs(self.pos)
        px = float(bar.close_price)
        side = "LONG" if self.pos > 0 else "SHORT"
        write_log = getattr(self, "write_log", None)
        if callable(write_log):
            write_log(f"EOD flatten (bt) {self.vt_symbol} {side} vol={vol} px={px}")

        from vnpy.trader.constant import Direction, Offset
        from vnpy.trader.object import TradeData

        engine = self.cta_engine
        direction = Direction.SHORT if self.pos > 0 else Direction.LONG
        engine.trade_count += 1
        trade = TradeData(
            symbol=engine.symbol,
            exchange=engine.exchange,
            orderid="eod",
            tradeid=str(engine.trade_count),
            direction=direction,
            offset=Offset.CLOSE,
            price=px,
            volume=vol,
            datetime=bar.datetime,
            gateway_name=engine.gateway_name,
        )
        engine.trades[trade.vt_tradeid] = trade
        self.pos = 0
        self.on_trade(trade)

    def on_trade(self, trade) -> None:
        KingKeltnerStrategy.on_trade(self, trade)
        self._bt_last_px = float(trade.price)
        if self.pos != 0:
            if self._bt_entry_px is None:
                self._bt_entry_px = float(trade.price)
                self._bt_entry_side = 1 if self.pos > 0 else -1
        else:
            self._apply_close_pnl(trade)
            self.cancel_all()
            self._refresh_compound_size()
