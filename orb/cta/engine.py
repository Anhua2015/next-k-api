"""CTA bar 回测引擎（简化 vnpy 撮合）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

import pandas as pd

from orb.core.config import OrbConfig
from orb.core.fees import trade_fee_usdt
from orb.core.session import session_anchor_ms, session_close_ms, session_day_str
from orb.core.signals import compute_position_notional
from orb.cta.execution import entry_fill_px, market_exit_fill_px, stop_exit_fill_px


@dataclass
class CtaBacktestConfig:
    equity_usdt: float = 1000.0
    risk_pct: float = 0.01
    compound: bool = True
    rth_only: bool = True
    eod_flat: bool = False
    exit_hour: int = 15
    exit_minute: int = 55
    maker_bps: float = 2.0
    taker_bps: float = 4.0
    entry_fee_mode: str = "signal"
    slip_bps_entry: float = 0.0
    slip_bps_exit: float = 0.0
    max_notional_usdt: float = 0.0
    # stop 入场初始止损比例；0 = 无（King Keltner 原版仅靠 5m 移动止损）
    entry_stop_sl_pct: float = 0.02
    # 无初始止损时，用该比例估算风险定仓（对应 trailing_percent/100）
    entry_risk_sl_pct: float = 0.01
    # False 时由策略在 5m 回调内维护 intra_high/low（King Keltner）
    bar_intra_update: bool = True


@dataclass
class Position:
    side: int = 0  # 1 long -1 short
    entry: float = 0.0
    sl: float = 0.0
    notional: float = 0.0
    entry_ms: int = 0


@dataclass
class PendingStop:
    side: int
    px: float
    is_entry: bool = True


@dataclass
class CtaContext:
    cfg: CtaBacktestConfig
    orb_cfg: OrbConfig
    wallet: float
    pos: Position = field(default_factory=Position)
    pending: List[PendingStop] = field(default_factory=list)
    intra_high: float = 0.0
    intra_low: float = 0.0
    trades: List[Dict[str, Any]] = field(default_factory=list)
    state: Dict[str, Any] = field(default_factory=dict)

    def open_long(self, entry: float, sl: float, *, ms: int, tag: str) -> bool:
        return self._open(1, entry, sl, ms=ms, tag=tag)

    def open_short(self, entry: float, sl: float, *, ms: int, tag: str) -> bool:
        return self._open(-1, entry, sl, ms=ms, tag=tag)

    def _open(self, side: int, entry: float, sl: float, *, ms: int, tag: str, trigger_px: Optional[float] = None) -> bool:
        if self.pos.side != 0 or entry <= 0:
            return False
        slip_bps = float(self.cfg.slip_bps_entry or 0.0)
        trigger = float(trigger_px if trigger_px is not None else entry)
        fill_entry = entry_fill_px(side, trigger, slip_bps) if slip_bps > 0 else float(entry)
        sl = float(sl)
        sizing_sl = sl
        if sl <= 0:
            risk_frac = float(self.cfg.entry_risk_sl_pct or 0.01)
            sizing_sl = fill_entry * (1.0 - risk_frac) if side == 1 else fill_entry * (1.0 + risk_frac)
        elif side == 1 and sl >= fill_entry:
            sl = fill_entry * 0.99
            sizing_sl = sl
        elif side == -1 and sl <= fill_entry:
            sl = fill_entry * 1.01
            sizing_sl = sl
        notion = compute_position_notional(
            entry=float(fill_entry),
            sl=float(sizing_sl),
            cfg=self.orb_cfg,
            bot_equity_usdt=self.wallet,
        )
        if notion <= 0:
            return False
        cap = float(self.cfg.max_notional_usdt or 0.0)
        if cap > 0:
            notion = min(notion, cap)
        active_sl = float(sl) if sl > 0 else 0.0
        self.pos = Position(
            side=side,
            entry=float(fill_entry),
            sl=active_sl,
            notional=float(notion),
            entry_ms=int(ms),
        )
        if self.cfg.bar_intra_update:
            self.intra_high = float(fill_entry)
            self.intra_low = float(fill_entry)
        self.trades.append(
            {
                "event": "open",
                "tag": tag,
                "side": "LONG" if side == 1 else "SHORT",
                "entry": round(fill_entry, 4),
                "trigger_px": round(trigger, 4),
                "sl": round(active_sl, 4),
                "notional_usdt": round(notion, 4),
                "ms": int(ms),
                "slip_bps_entry": slip_bps,
            }
        )
        return True

    def close(
        self,
        exit_px: float,
        *,
        ms: int,
        outcome: str,
        slip_bps: Optional[float] = None,
        bar_open: Optional[float] = None,
    ) -> None:
        if self.pos.side == 0:
            return
        side = self.pos.side
        slip = float(self.cfg.slip_bps_exit if slip_bps is None else slip_bps)
        if str(outcome or "") in ("eod", "session_close"):
            fill_px = market_exit_fill_px(side, exit_px, slip)
        else:
            fill_px = stop_exit_fill_px(
                side,
                exit_px,
                bar_open=float(bar_open if bar_open is not None else exit_px),
                slip_bps=slip,
            )
        entry = float(self.pos.entry)
        notion = float(self.pos.notional)
        pre_sl = float(self.pos.sl)
        pre_entry_ms = int(self.pos.entry_ms)
        gross = (fill_px - entry) / entry * notion if side == 1 else (entry - fill_px) / entry * notion
        fee = trade_fee_usdt(
            notion,
            entry_mode=self.cfg.entry_fee_mode,
            maker_bps=self.cfg.maker_bps,
            taker_bps=self.cfg.taker_bps,
        )
        net = round(float(gross) - float(fee), 4)
        self.trades.append(
            {
                "event": "close",
                "outcome": outcome,
                "side": "LONG" if side == 1 else "SHORT",
                "entry": round(entry, 4),
                "exit": round(float(fill_px), 4),
                "notional_usdt": round(notion, 4),
                "pnl_usdt_gross": round(float(gross), 4),
                "fee_usdt": round(float(fee), 4),
                "pnl_usdt": net,
                "ms": int(ms),
                "slip_bps_exit": slip,
                "pre_sl": round(pre_sl, 4),
                "entry_ms": pre_entry_ms,
            }
        )
        if self.cfg.compound:
            self.wallet = round(self.wallet + net, 4)
        self.pos = Position()
        self.pending.clear()

    def set_exit_stop(self, px: float) -> None:
        self.pending = [p for p in self.pending if p.is_entry]
        if self.pos.side == 1:
            self.pending.append(PendingStop(side=-1, px=float(px), is_entry=False))
            self.pos.sl = float(px)
        elif self.pos.side == -1:
            self.pending.append(PendingStop(side=1, px=float(px), is_entry=False))
            self.pos.sl = float(px)

    def set_entry_stops(self, long_px: float, short_px: float) -> None:
        self.pending = []
        if long_px > 0:
            self.pending.append(PendingStop(side=1, px=float(long_px), is_entry=True))
        if short_px > 0:
            self.pending.append(PendingStop(side=-1, px=float(short_px), is_entry=True))


StrategyFn = Callable[[CtaContext, pd.Series, int], None]


def in_rth(ms: int, orb_cfg: OrbConfig) -> bool:
    return _in_rth(ms, orb_cfg)


def try_bar_fills(ctx: CtaContext, bar: pd.Series, *, cta_cfg: Optional[CtaBacktestConfig] = None) -> None:
    _try_fills(ctx, bar, cta_cfg)


def process_cta_bar(
    ctx: CtaContext,
    row: pd.Series,
    *,
    strategy_fn: StrategyFn,
    orb_cfg: OrbConfig,
    cta_cfg: CtaBacktestConfig,
    last_day: str,
    prev_close: float,
) -> tuple[str, float]:
    """处理一根已收盘 1m bar，返回 (session_day, close_px)。"""
    ms = int(row["open_time"])
    close_px = float(row["close"])
    if cta_cfg.rth_only and not _in_rth(ms, orb_cfg):
        return last_day, close_px
    day = session_day_str(ms, tz=orb_cfg.session_tz, session_open_time=orb_cfg.session_open_time)
    ts = pd.Timestamp(ms, unit="ms", tz=orb_cfg.session_tz)
    if cta_cfg.eod_flat and ctx.pos.side != 0 and day != last_day and last_day:
        ctx.close(float(prev_close), ms=ms, outcome="eod")
    if cta_cfg.eod_flat and ctx.pos.side != 0:
        if ts.hour > cta_cfg.exit_hour or (ts.hour == cta_cfg.exit_hour and ts.minute >= cta_cfg.exit_minute):
            ctx.close(close_px, ms=ms, outcome="eod")
            strategy_fn(ctx, row, ms)
            return day, close_px
    _try_fills(ctx, row, cta_cfg)
    if cta_cfg.bar_intra_update and ctx.pos.side != 0:
        ctx.intra_high = max(ctx.intra_high, float(row["high"]))
        ctx.intra_low = min(ctx.intra_low, float(row["low"]))
    strategy_fn(ctx, row, ms)
    return day, close_px


def _in_rth(ms: int, orb_cfg: OrbConfig) -> bool:
    tz = orb_cfg.session_tz
    day = session_day_str(ms, tz=tz, session_open_time=orb_cfg.session_open_time)
    anchor = session_anchor_ms(
        int(pd.Timestamp(f"{day} 12:00:00", tz=tz).value // 1_000_000),
        tz=tz,
        session_open_time=orb_cfg.session_open_time,
    )
    close = session_close_ms(anchor, tz=tz, session_close_time=orb_cfg.session_close_time)
    if close is None:
        close = anchor + 6 * 60 * 60 * 1000
    return int(anchor) <= int(ms) <= int(close)


def _try_fills(ctx: CtaContext, bar: pd.Series, cta_cfg: Optional[CtaBacktestConfig] = None) -> None:
    cfg = cta_cfg or ctx.cfg
    h, l, o = float(bar["high"]), float(bar["low"]), float(bar["open"])
    ms = int(bar["open_time"])
    if ctx.pos.side != 0:
        sl = float(ctx.pos.sl)
        if sl > 0:
            if ctx.pos.side == 1 and l <= sl:
                ctx.close(sl, ms=ms, outcome="loss" if sl < ctx.pos.entry else "win", slip_bps=cfg.slip_bps_exit, bar_open=o)
                return
            if ctx.pos.side == -1 and h >= sl:
                ctx.close(sl, ms=ms, outcome="loss" if sl > ctx.pos.entry else "win", slip_bps=cfg.slip_bps_exit, bar_open=o)
                return
    entry_sl_pct = float(cfg.entry_stop_sl_pct or 0.0)
    for p in list(ctx.pending):
        if ctx.pos.side != 0 and p.is_entry:
            continue
        if p.is_entry and ctx.pos.side == 0:
            if p.side == 1 and h >= p.px:
                ctx.pending = []
                sl_px = p.px * (1.0 - entry_sl_pct) if entry_sl_pct > 0 else 0.0
                fill_px = entry_fill_px(1, p.px, cfg.slip_bps_entry)
                ctx._open(1, fill_px, sl_px, ms=ms, tag="stop_entry", trigger_px=p.px)
                return
            if p.side == -1 and l <= p.px:
                ctx.pending = []
                sl_px = p.px * (1.0 + entry_sl_pct) if entry_sl_pct > 0 else 0.0
                fill_px = entry_fill_px(-1, p.px, cfg.slip_bps_entry)
                ctx._open(-1, fill_px, sl_px, ms=ms, tag="stop_entry", trigger_px=p.px)
                return


def run_cta_backtest(
    df: pd.DataFrame,
    *,
    strategy_fn: StrategyFn,
    orb_cfg: OrbConfig,
    cta_cfg: CtaBacktestConfig,
    warmup: int = 30,
) -> Dict[str, Any]:
    if df is None or df.empty:
        return {"trades": [], "summary": {"net_pnl_usdt": 0.0, "opens": 0}}
    bars = df.sort_values("open_time").reset_index(drop=True)
    ctx = CtaContext(cfg=cta_cfg, orb_cfg=orb_cfg, wallet=float(cta_cfg.equity_usdt))
    last_day = ""
    for i, row in bars.iterrows():
        ms = int(row["open_time"])
        if cta_cfg.rth_only and not _in_rth(ms, orb_cfg):
            continue
        if i < warmup:
            continue
        day = session_day_str(ms, tz=orb_cfg.session_tz, session_open_time=orb_cfg.session_open_time)
        ts = pd.Timestamp(ms, unit="ms", tz=orb_cfg.session_tz)
        if cta_cfg.eod_flat and ctx.pos.side != 0 and day != last_day and last_day:
            ctx.close(float(bars.iloc[i - 1]["close"]), ms=ms, outcome="eod")
        if cta_cfg.eod_flat and ctx.pos.side != 0:
            if ts.hour > cta_cfg.exit_hour or (ts.hour == cta_cfg.exit_hour and ts.minute >= cta_cfg.exit_minute):
                ctx.close(float(row["close"]), ms=ms, outcome="eod")
                strategy_fn(ctx, row, ms)
                last_day = day
                continue
        _try_fills(ctx, row, cta_cfg)
        if cta_cfg.bar_intra_update and ctx.pos.side != 0:
            ctx.intra_high = max(ctx.intra_high, float(row["high"]))
            ctx.intra_low = min(ctx.intra_low, float(row["low"]))
        strategy_fn(ctx, row, ms)
        last_day = day

    if ctx.pos.side != 0:
        ctx.close(float(bars.iloc[-1]["close"]), ms=int(bars.iloc[-1]["open_time"]), outcome="session_close")

    closes = [t for t in ctx.trades if t.get("event") == "close"]
    net = round(sum(float(t.get("pnl_usdt") or 0) for t in closes), 2)
    fees = round(sum(float(t.get("fee_usdt") or 0) for t in closes), 2)
    return {
        "trades": ctx.trades,
        "summary": {
            "opens": sum(1 for t in ctx.trades if t.get("event") == "open"),
            "closes": len(closes),
            "net_pnl_usdt": net,
            "fees_usdt": fees,
            "equity_end": round(ctx.wallet, 2),
        },
    }
