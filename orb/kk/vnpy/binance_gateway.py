"""官方 vnpy_binance BinanceLinearGateway（KK 实盘）。"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, Optional, Set

from orb.core.kline_cache import norm_symbol
from orb.core.paper import _session_date_now
from orb.kk.config import KKConfig
from orb.kk.live_exec import live_enabled
from orb.kk.vnpy.bootstrap import ensure_vnpy_path
from orb.kk.wallet_sync import estimate_close_pnl, record_vnpy_fill

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
    kline = (os.getenv("BINANCE_KLINE_STREAM") or "False").strip()
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
    """官方 Gateway + KK 实盘守卫与复利记账。"""

    def __init__(self, event_engine, gateway_name: str = GATEWAY_NAME) -> None:
        super().__init__(event_engine, gateway_name)
        self._open_lots: Dict[str, Dict[str, Any]] = {}
        self._active_symbols: Set[str] = set()

    def send_order(self, req: OrderRequest) -> str:
        kk = KKConfig.from_env()
        sym = kk_symbol_from_vt(req.symbol)
        if kk.shadow:
            self.write_log(f"KK_SHADOW=1 跳过实盘下单 {sym}")
            return ""
        if not live_enabled(kk):
            self.write_log(f"KK_LIVE_ENABLED=0 或未配置币安 Key，拒单 {sym}")
            return ""
        vol = float(req.volume or 0.0)
        if vol <= 0:
            self.write_log(
                f"拒单 volume<=0 {sym} {req.direction.value} {req.offset.value} price={req.price}"
            )
            return ""
        if req.offset == Offset.OPEN:
            max_pos = int(kk.max_open_positions or 0)
            if max_pos > 0 and sym not in self._active_symbols:
                if self._open_position_count() >= max_pos:
                    self.write_log(f"已达最大持仓数 {max_pos}，拒单 {sym}")
                    return ""
        return super().send_order(req)

    def on_position(self, position: PositionData) -> None:
        sym = kk_symbol_from_vt(position.symbol)
        if float(position.volume or 0) > 0:
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

    def _kk_pool(self) -> Set[str]:
        return {norm_symbol(s) for s in KKConfig.from_env().symbol_list()}

    def _open_position_count(self) -> int:
        pool = self._kk_pool()
        return sum(1 for sym in self._active_symbols if sym in pool)

    def _session_date(self) -> str:
        kk = KKConfig.from_env()
        return _session_date_now(kk.orb_session_cfg())

    def _is_eod_close(self) -> bool:
        kk = KKConfig.from_env()
        if not kk.eod_flat:
            return False
        import pandas as pd

        cfg = kk.orb_session_cfg()
        now_ms = int(time.time() * 1000)
        ts = pd.Timestamp(now_ms, unit="ms", tz=cfg.session_tz)
        return ts.hour > int(kk.exit_hour) or (
            ts.hour == int(kk.exit_hour) and ts.minute >= int(kk.exit_minute)
        )

    def _persist_trade(self, trade: TradeData) -> None:
        kk = KKConfig.from_env()
        if not live_enabled(kk) or kk.shadow:
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
            record_vnpy_fill(
                symbol=sym,
                event="open",
                side=side,
                price=px,
                volume=vol,
                notional_usdt=notional,
                session_date=session_date,
                bar_ms=bar_ms,
                kk=kk,
            )
            return

        lot = self._open_lots.get(sym, {})
        pos_side = str(lot.get("side") or ("LONG" if trade.direction == Direction.SHORT else "SHORT"))
        entry_px = float(lot.get("entry") or px)
        notion = float(lot.get("notional_usdt") or 0.0)
        if notion <= 0:
            notion = px * vol
        outcome = "eod" if self._is_eod_close() else "close"
        gross, fee, net = estimate_close_pnl(
            side=pos_side,
            entry=entry_px,
            exit_px=px,
            notional_usdt=notion,
            kk=kk,
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
            kk=kk,
            outcome=outcome,
            pnl_usdt=net,
            pnl_gross=gross,
            fee_usdt=fee,
        )
        self._open_lots.pop(sym, None)


__all__ = [
    "GATEWAY_NAME",
    "KkBinanceLinearGateway",
    "binance_connect_setting",
    "binance_credentials_configured",
    "kk_symbol_from_vt",
    "kk_vt_symbol",
]
