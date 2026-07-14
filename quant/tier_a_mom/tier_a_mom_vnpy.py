"""Tier-A mom-turn vnpy strategy — spot daily, long-only, smart exit."""

from __future__ import annotations

from typing import List, Optional

import numpy as np

from quant.breakout_donchian.bars import BarRow, drop_incomplete_bars, fetch_bars
from quant.common.exchange_env import resolve_market_data_exchange_id
from quant.common.kline_cache import norm_symbol
from quant.common.scanner_potential_pool import (
    pool_entry_alert,
    pool_ok_for_entry,
    symbol_in_tier_a_pool,
)
from quant.engine.bootstrap import ensure_vnpy_path
from quant.engine.exchanges.registry import symbol_from_vt
from quant.tier_a_mom.config import TierAMomConfig
from quant.tier_a_mom.core import PositionState, detect_mom_turn_signal, on_bar_exit, trade_plan
from quant.tier_a_mom.sizing import size_for_tier_a_mom

ensure_vnpy_path()

from vnpy.trader.constant import Direction, Interval, Offset, OrderType  # noqa: E402
from vnpy.trader.utility import round_to  # noqa: E402
from vnpy_ctastrategy import (  # noqa: E402
    BarData,
    BarGenerator,
    CtaTemplate,
    OrderData,
    StopOrder,
    TickData,
    TradeData,
)


class TierAMomVnpyStrategy(CtaTemplate):
    """Spot mom_turn_pool10_smart_exit — 1D bars."""

    author = "next-k-api"

    stop_pct: float = 0.08
    tp1_pct: float = 0.30
    tp2_pct: float = 0.50
    trail_after_pct: float = 0.10
    trail_ema: int = 20
    max_hold_bars: int = 90
    allow_reclaim_entry: bool = False
    require_pool_ok: bool = True
    fixed_size: float = 1.0

    parameters = [
        "stop_pct",
        "tp1_pct",
        "tp2_pct",
        "trail_after_pct",
        "trail_ema",
        "max_hold_bars",
        "allow_reclaim_entry",
        "require_pool_ok",
        "fixed_size",
    ]
    variables = ["entry_price", "stop_price", "tp1_price", "tp2_price", "bars_held", "qty_frac"]

    def __init__(self, cta_engine, strategy_name, vt_symbol, setting):
        super().__init__(cta_engine, strategy_name, vt_symbol, setting)
        self.entry_price = 0.0
        self.stop_price = 0.0
        self.tp1_price = 0.0
        self.tp2_price = 0.0
        self.bars_held = 0
        self.qty_frac = 0.0
        self._state: Optional[PositionState] = None
        self._signal_bars: list[BarRow] = []
        self._entry_pending = False
        self._exit_pending = False
        self._original_volume = 0.0
        self._cooldown_bar_ms: int = 0
        self._bars_since_refresh = 0
        self.bg: Optional[BarGenerator] = None

    @classmethod
    def from_tier_a_mom_config(cls, cfg: TierAMomConfig) -> dict:
        return {
            "stop_pct": float(cfg.stop_pct),
            "tp1_pct": float(cfg.tp1_pct),
            "tp2_pct": float(cfg.tp2_pct),
            "trail_after_pct": float(cfg.trail_after_pct),
            "trail_ema": int(cfg.trail_ema),
            "max_hold_bars": int(cfg.max_hold_bars),
            "allow_reclaim_entry": bool(cfg.allow_reclaim_entry),
            "require_pool_ok": bool(cfg.require_pool_ok),
        }

    def _cfg(self) -> TierAMomConfig:
        return TierAMomConfig.from_env()

    def _bar_ms(self, bar: BarData) -> int:
        return int(bar.datetime.timestamp() * 1000)

    def _bar_row(self, bar: BarData) -> BarRow:
        return (
            self._bar_ms(bar),
            float(bar.open_price),
            float(bar.high_price),
            float(bar.low_price),
            float(bar.close_price),
            float(bar.volume or 0.0),
        )

    def _append_bar(self, bar: BarData) -> None:
        row = self._bar_row(bar)
        cfg = self._cfg()
        max_bars = max(200, int(cfg.init_bar_days) + 30)
        if self._signal_bars and self._signal_bars[-1][0] == row[0]:
            self._signal_bars[-1] = row
        else:
            self._signal_bars.append(row)
        if len(self._signal_bars) > max_bars:
            self._signal_bars = self._signal_bars[-max_bars:]

    def _refresh_bars(self, cfg: TierAMomConfig, *, force: bool = False) -> None:
        # Avoid hammering REST every bar; refresh periodically.
        self._bars_since_refresh += 1
        if not force and self._signal_bars and self._bars_since_refresh < 5:
            return
        sym = symbol_from_vt(self.vt_symbol)
        md = resolve_market_data_exchange_id(cfg.market_data_exchange)
        days = max(70, int(cfg.init_bar_days))
        try:
            daily = fetch_bars(sym, "1d", days=days, exchange_id=md)
            if daily:
                self._signal_bars = list(daily[-max(200, days + 30) :])
                self._bars_since_refresh = 0
        except Exception as exc:
            self.write_log(f"refresh bars failed: {exc}")

    def _equity_usdt(self, cfg: TierAMomConfig) -> float:
        base = float(cfg.equity_usdt or 100_000.0)
        if not cfg.compound:
            return base
        try:
            from accumulation_radar import init_db
            from quant.common.vnpy_wallet import lane_equity_usdt, migrate_vnpy_lane_tables

            conn = init_db()
            try:
                cur = conn.cursor()
                migrate_vnpy_lane_tables(cur)
                return lane_equity_usdt(cfg, symbol_from_vt(self.vt_symbol), cur=cur)
            finally:
                conn.close()
        except Exception:
            return base

    def _clear_state(self) -> None:
        self._state = None
        self.entry_price = 0.0
        self.stop_price = 0.0
        self.tp1_price = 0.0
        self.tp2_price = 0.0
        self.bars_held = 0
        self.qty_frac = 0.0
        self._original_volume = 0.0

    def _apply_state(self, st: PositionState) -> None:
        self._state = st
        self.entry_price = st.entry
        self.stop_price = st.stop
        self.tp1_price = st.tp1
        self.tp2_price = st.tp2
        self.bars_held = st.bars_held
        self.qty_frac = st.qty_frac

    def _signal_only(self, cfg: TierAMomConfig) -> bool:
        return bool(cfg.shadow or not cfg.live_enabled)

    def on_init(self) -> None:
        self.write_log("Tier-A Mom Turn init (spot 1D)")
        self.bg = BarGenerator(self.on_bar, 1, self.on_signal_bar, Interval.DAILY)
        self._refresh_bars(self._cfg(), force=True)
        self.write_log(f"preloaded daily={len(self._signal_bars)}")

    def on_start(self) -> None:
        self.write_log("Tier-A Mom Turn start")

    def on_stop(self) -> None:
        if self.pos != 0:
            self._flatten_market()
        self.write_log("Tier-A Mom Turn stop")

    def on_tick(self, tick: TickData) -> None:
        extra = getattr(tick, "extra", None) or {}
        bar = extra.get("bar")
        if bar is not None:
            self.on_bar(bar)
            return
        if self.bg is not None:
            self.bg.update_tick(tick)

    def on_bar(self, bar: BarData) -> None:
        if self.bg is not None:
            self.bg.update_bar(bar)

    def on_signal_bar(self, bar: BarData) -> None:
        cfg = self._cfg()
        self._append_bar(bar)
        # Soft refresh so signal history stays warm without wiping every close
        if self._bars_since_refresh >= 5:
            self._refresh_bars(cfg, force=False)
            self._append_bar(bar)
        bars = drop_incomplete_bars(self._signal_bars, "1d")
        if not self.trading:
            self.put_event()
            return

        if abs(self.pos) > 1e-12 and self._state is not None:
            self._handle_exit(bar, bars)
            self.put_event()
            return

        # Flat but leftover state (failed fill / shadow) → drop
        if abs(self.pos) < 1e-12 and self._state is not None and not self._entry_pending:
            self._clear_state()

        if self._entry_pending:
            self.put_event()
            return

        bar_ms = self._bar_ms(bar)
        if bar_ms <= self._cooldown_bar_ms:
            self.put_event()
            return

        if not self._gates_allow_entry(cfg):
            self.put_event()
            return

        signal = detect_mom_turn_signal(
            bars,
            stop_pct=float(self.stop_pct),
            allow_reclaim=bool(self.allow_reclaim_entry),
            pool_ok=True,  # already gated above
        )
        if signal is None:
            self.put_event()
            return

        if cfg.prefer_entry_alerts and pool_entry_alert(symbol_from_vt(self.vt_symbol)) is None:
            self.put_event()
            return

        self._try_enter(cfg, signal, bar)
        self.put_event()

    def _gates_allow_entry(self, cfg: TierAMomConfig) -> bool:
        """Research: pool size <=10 AND this symbol currently in Tier-A pool."""
        del cfg  # env-backed gates; cfg reserved for future overrides
        sym = norm_symbol(symbol_from_vt(self.vt_symbol))
        if not pool_ok_for_entry(max_pool=10):
            return False
        return symbol_in_tier_a_pool(sym)

    def _handle_exit(self, bar: BarData, bars: list) -> None:
        assert self._state is not None
        close_arr = np.array([float(b[4]) for b in bars], dtype=float)
        st, sell_frac, reason = on_bar_exit(
            self._state,
            high=float(bar.high_price),
            low=float(bar.low_price),
            close=float(bar.close_price),
            close_series=close_arr,
            trail_after_pct=float(self.trail_after_pct),
            trail_ema=int(self.trail_ema),
            max_hold_bars=int(self.max_hold_bars),
        )
        self._apply_state(st)
        if sell_frac <= 0 or not reason:
            return

        if st.qty_frac <= 1e-9:
            self.write_log(f"exit {reason} flatten")
            self._cooldown_bar_ms = self._bar_ms(bar)
            self._flatten_market()
            return

        vol = max(0.0, float(self._original_volume) * float(sell_frac))
        vol = min(vol, abs(float(self.pos)))
        self.write_log(f"exit {reason} sell_frac={sell_frac:.3f} vol={vol:.6f}")
        if vol > 0:
            self._sell_market(vol)

    def _try_enter(self, cfg: TierAMomConfig, signal, bar: BarData) -> None:
        sym = symbol_from_vt(self.vt_symbol)
        eq = self._equity_usdt(cfg)
        vol = size_for_tier_a_mom(cfg, signal.entry, equity_usdt=eq)
        if vol <= 0:
            return
        plan = trade_plan(
            signal.entry,
            float(bar.low_price),
            stop_pct=float(self.stop_pct),
            tp1=float(self.tp1_pct),
            tp2=float(self.tp2_pct),
        )
        self.write_log(
            f"TierAMom LONG {self.vt_symbol} signal={signal.signal} "
            f"px={signal.entry:.6f} stop={plan.stop:.6f} tp1={plan.tp1:.6f} "
            f"eq={eq:.2f} vol={vol}"
        )
        try:
            from quant.engine.strategy_signals import LANE_TIER_A_MOM, record_strategy_open_signal

            record_strategy_open_signal(
                lane=LANE_TIER_A_MOM,
                symbol=sym,
                side="LONG",
                entry_price=signal.entry,
                sl_price=plan.stop,
                tp_price=plan.tp2,
                status="shadow" if self._signal_only(cfg) else "emitted",
                bar_ms=self._bar_ms(bar),
                detail={
                    "vol": vol,
                    "signal": signal.signal,
                    "ret_5": signal.ret_5,
                    "tp1": plan.tp1,
                    "tp2": plan.tp2,
                    "market": "spot",
                    "strategy_id": cfg.strategy_id,
                    "position_pct": cfg.position_pct,
                },
            )
        except Exception as exc:
            self.write_log(f"strategy signal persist failed: {exc}")

        if self._signal_only(cfg):
            # Shadow: log only — do not bind exit state without a fill
            self._cooldown_bar_ms = self._bar_ms(bar)
            self.write_log(f"signal-only {self.vt_symbol} (shadow={cfg.shadow} live={cfg.live_enabled})")
            return

        self.fixed_size = vol
        self._original_volume = vol
        self._apply_state(
            PositionState(
                entry=plan.entry,
                stop=plan.stop,
                tp1=plan.tp1,
                tp2=plan.tp2,
                qty_frac=1.0,
            )
        )
        oids = self._open_market(vol)
        if not oids:
            self.write_log(f"open failed, clear state {self.vt_symbol}")
            self._clear_state()
            return
        self._entry_pending = True
        self._cooldown_bar_ms = self._bar_ms(bar)

    def _send_market(self, direction: Direction, offset: Offset, volume: float) -> List[str]:
        if not self.trading or not self.cta_engine:
            return []
        vol = float(volume or 0.0)
        if vol <= 0:
            return []
        contract = self.cta_engine.main_engine.get_contract(self.vt_symbol)
        if contract is None:
            return []
        vol = round_to(vol, float(contract.min_volume or 0.001))
        if vol <= 0:
            return []
        return self.cta_engine.send_server_order(
            self,
            contract,
            direction,
            offset,
            0.0,
            vol,
            OrderType.MARKET,
            False,
            False,
        ) or []

    def _open_market(self, vol: float) -> List[str]:
        return self._send_market(Direction.LONG, Offset.OPEN, vol)

    def _sell_market(self, vol: float) -> None:
        if self._exit_pending:
            return
        oids = self._send_market(Direction.SHORT, Offset.CLOSE, vol)
        if oids:
            self._exit_pending = True

    def _flatten_market(self) -> None:
        if abs(self.pos) < 1e-12:
            self._clear_state()
            return
        self._sell_market(abs(self.pos))

    def on_order(self, order: OrderData) -> None:
        return

    def on_trade(self, trade: TradeData) -> None:
        if trade.offset == Offset.OPEN:
            self._entry_pending = False
            if self._original_volume <= 0:
                self._original_volume = abs(float(trade.volume or 0.0))
        if trade.offset in (Offset.CLOSE, Offset.CLOSETODAY, Offset.CLOSEYESTERDAY):
            self._exit_pending = False
            if abs(self.pos) < 1e-12:
                self._clear_state()
        self.put_event()

    def on_stop_order(self, stop_order: StopOrder) -> None:
        return

    def restore_synced_position(self, *, entry_px: float, pos: float) -> None:
        if abs(float(pos or 0.0)) < 1e-12:
            return
        px = float(entry_px)
        plan = trade_plan(
            px,
            px * (1.0 - float(self.stop_pct)),
            stop_pct=float(self.stop_pct),
            tp1=float(self.tp1_pct),
            tp2=float(self.tp2_pct),
        )
        self._original_volume = abs(float(pos))
        self._apply_state(
            PositionState(entry=plan.entry, stop=plan.stop, tp1=plan.tp1, tp2=plan.tp2, qty_frac=1.0)
        )
        self.write_log(f"restored synced spot position entry={px:.6f} stop={plan.stop:.6f}")
