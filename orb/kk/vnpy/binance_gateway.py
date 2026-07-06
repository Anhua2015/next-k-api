"""官方 vnpy_binance BinanceLinearGateway（KK 实盘）。"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, Optional, Set

from orb.core.kline_cache import norm_symbol
from orb.core.session_paper import _session_date_now
from orb.kk.config import KKConfig
from orb.kk.live_exec import live_enabled as kk_live_enabled
from orb.kk.vnpy.bootstrap import ensure_vnpy_path
from orb.kk.wallet_sync import estimate_close_pnl as kk_estimate_close_pnl
from orb.kk.wallet_sync import record_vnpy_fill as kk_record_vnpy_fill
from orb.trading_orb.live_exec import live_enabled as orb_live_enabled
from orb.trading_orb.wallet_sync import estimate_close_pnl as orb_estimate_close_pnl
from orb.trading_orb.wallet_sync import record_vnpy_fill as orb_record_vnpy_fill
from orb.vnpy.lane import get_active_vnpy_config

ensure_vnpy_path()

from vnpy.trader.constant import Direction, Exchange, Offset  # noqa: E402
from vnpy.trader.object import OrderRequest, PositionData, TradeData  # noqa: E402
from vnpy_binance.linear_gateway import BinanceLinearGateway  # noqa: E402

logger = logging.getLogger(__name__)

GATEWAY_NAME = BinanceLinearGateway.default_name


def binance_credentials_configured() -> bool:
    return bool(
        (os.getenv("BINANCE_API_KEY") or "").strip()
        and (os.getenv("BINANCE_API_SECRET") or "").strip()
    )


def binance_connect_setting() -> dict:
    server = (os.getenv("BINANCE_SERVER") or "REAL").strip().upper()
    if server not in ("REAL", "TESTNET"):
        server = "REAL"
    kline = (os.getenv("BINANCE_KLINE_STREAM") or "").strip()
    if not kline:
        lane, _ = get_active_vnpy_config()
        kline = "True" if lane == "trading_orb" else "False"
    if kline.lower() in ("1", "true", "yes", "on"):
        kline = "True"
    else:
        kline = "False"
    proxy_port = int((os.getenv("BINANCE_PROXY_PORT") or "0").strip() or 0)
    return {
        "API Key": (os.getenv("BINANCE_API_KEY") or "").strip(),
        "API Secret": (os.getenv("BINANCE_API_SECRET") or "").strip(),
        "Server": server,
        "Kline Stream": kline,
        "Proxy Host": (os.getenv("BINANCE_PROXY_HOST") or "").strip(),
        "Proxy Port": proxy_port,
    }


def kk_vt_symbol(symbol: str) -> str:
    """官方合约 vt_symbol：INTCUSDT_SWAP_BINANCE.GLOBAL"""
    sym = norm_symbol(symbol)
    return f"{sym}_SWAP_BINANCE.GLOBAL"


def kk_symbol_from_vt(vt_symbol: str) -> str:
    raw = str(vt_symbol or "").split(".", 1)[0]
    if raw.endswith("_SWAP_BINANCE"):
        raw = raw[: -len("_SWAP_BINANCE")]
    return norm_symbol(raw)


class KkBinanceLinearGateway(BinanceLinearGateway):
    """官方 Gateway + vnpy 实盘守卫与复利记账（KK / Trading ORB）。"""

    def __init__(self, event_engine, gateway_name: str = GATEWAY_NAME) -> None:
        super().__init__(event_engine, gateway_name)
        self._open_lots: Dict[str, Dict[str, Any]] = {}
        self._active_symbols: Set[str] = set()

    def _lane_cfg(self):
        _, cfg = get_active_vnpy_config()
        if cfg is not None:
            return cfg
        return KKConfig.from_env()

    def _lane_live_enabled(self, cfg) -> bool:
        if isinstance(cfg, KKConfig):
            return live_enabled(cfg)
        return orb_live_enabled(cfg)

    def send_order(self, req: OrderRequest) -> str:
        cfg = self._lane_cfg()
        sym = kk_symbol_from_vt(req.symbol)
        if getattr(cfg, "shadow", False):
            self.write_log(f"SHADOW=1 跳过实盘下单 {sym}")
            return ""
        if not self._lane_live_enabled(cfg):
            self.write_log(f"LIVE_ENABLED=0 或未配置币安 Key，拒单 {sym}")
            return ""
        vol = float(req.volume or 0.0)
        if vol <= 0:
            self.write_log(
                f"拒单 volume<=0 {sym} {req.direction.value} {req.offset.value} price={req.price}"
            )
            return ""
        if req.offset == Offset.OPEN:
            max_pos = int(getattr(cfg, "max_open_positions", 0) or 0)
            if max_pos > 0 and sym not in self._active_symbols:
                if self._open_position_count() >= max_pos:
                    self.write_log(f"已达最大持仓数 {max_pos}，拒单 {sym}")
                    return ""
        return super().send_order(req)

    def on_position(self, position: PositionData) -> None:
        sym = kk_symbol_from_vt(position.symbol)
        if abs(float(position.volume or 0.0)) > 0:
            self._active_symbols.add(sym)
        else:
            self._active_symbols.discard(sym)
        super().on_position(position)

    def on_trade(self, trade: TradeData) -> None:
        super().on_trade(trade)
        try:
            self._persist_trade(trade)
        except Exception as exc:
            self.write_log(f"trade persist failed {trade.symbol}: {exc}")

    def _lane_pool(self) -> Set[str]:
        cfg = self._lane_cfg()
        return {norm_symbol(s) for s in cfg.symbol_list()}

    def _open_position_count(self) -> int:
        pool = self._lane_pool()
        return sum(1 for sym in self._active_symbols if sym in pool)

    def _session_date(self) -> str:
        cfg = self._lane_cfg()
        return _session_date_now(cfg.orb_session_cfg())

    def _is_eod_close(self) -> bool:
        cfg = self._lane_cfg()
        if not getattr(cfg, "eod_flat", False):
            return False
        import pandas as pd

        sess = cfg.orb_session_cfg()
        now_ms = int(time.time() * 1000)
        ts = pd.Timestamp(now_ms, unit="ms", tz=sess.session_tz)
        exit_hour = int(getattr(cfg, "exit_hour", 15))
        exit_minute = int(getattr(cfg, "exit_minute", 55))
        return ts.hour > exit_hour or (ts.hour == exit_hour and ts.minute >= exit_minute)

    def _persist_trade(self, trade: TradeData) -> None:
        cfg = self._lane_cfg()
        if not self._lane_live_enabled(cfg) or getattr(cfg, "shadow", False):
            return
        sym = kk_symbol_from_vt(trade.symbol)
        bar_ms = int(time.time() * 1000)
        session_date = self._session_date()
        px = float(trade.price or 0.0)
        vol = float(trade.volume or 0.0)
        if px <= 0 or vol <= 0:
            return

        if trade.offset == Offset.OPEN:
            side = "LONG" if trade.direction == Direction.LONG else "SHORT"
            notional = px * vol
            self._open_lots[sym] = {
                "side": side,
                "entry": px,
                "notional_usdt": notional,
                "volume": vol,
            }
            if isinstance(cfg, KKConfig):
                record_vnpy_fill(
                    symbol=sym,
                    event="open",
                    side=side,
                    price=px,
                    volume=vol,
                    notional_usdt=notional,
                    session_date=session_date,
                    bar_ms=bar_ms,
                    kk=cfg,
                )
            else:
                orb_record_vnpy_fill(
                    symbol=sym,
                    event="open",
                    side=side,
                    price=px,
                    volume=vol,
                    notional_usdt=notional,
                    session_date=session_date,
                    bar_ms=bar_ms,
                    cfg=cfg,
                )
            return

        lot = self._open_lots.get(sym, {})
        pos_side = str(lot.get("side") or ("LONG" if trade.direction == Direction.SHORT else "SHORT"))
        entry_px = float(lot.get("entry") or px)
        notion = float(lot.get("notional_usdt") or 0.0)
        if notion <= 0:
            notion = px * vol
        outcome = "eod" if self._is_eod_close() else "close"
        if isinstance(cfg, KKConfig):
            gross, fee, net = estimate_close_pnl(
                side=pos_side,
                entry=entry_px,
                exit_px=px,
                notional_usdt=notion,
                kk=cfg,
            )
            record_vnpy_fill(
                symbol=sym,
                event="close",
                side=pos_side,
                price=px,
                volume=vol,
                notional_usdt=notion,
                session_date=session_date,
                bar_ms=bar_ms,
                kk=cfg,
                outcome=outcome,
                pnl_usdt=net,
                pnl_gross=gross,
                fee_usdt=fee,
            )
        else:
            gross, fee, net = orb_estimate_close_pnl(
                side=pos_side,
                entry=entry_px,
                exit_px=px,
                notional_usdt=notion,
                cfg=cfg,
            )
            orb_record_vnpy_fill(
                symbol=sym,
                event="close",
                side=pos_side,
                price=px,
                volume=vol,
                notional_usdt=notion,
                session_date=session_date,
                bar_ms=bar_ms,
                cfg=cfg,
                outcome=outcome,
                pnl_usdt=net,
                pnl_gross=gross,
                fee_usdt=fee,
            )
        self._open_lots.pop(sym, None)


# 兼容旧测试 / import
live_enabled = kk_live_enabled
estimate_close_pnl = kk_estimate_close_pnl
record_vnpy_fill = kk_record_vnpy_fill


__all__ = [
    "GATEWAY_NAME",
    "KkBinanceLinearGateway",
    "binance_connect_setting",
    "binance_credentials_configured",
    "kk_symbol_from_vt",
    "kk_vt_symbol",
]
