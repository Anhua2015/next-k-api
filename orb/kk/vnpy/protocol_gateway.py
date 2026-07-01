"""VeighNa Gateway → Next-k-protocol（KK vnpy 实盘链路）。"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Set

from orb.kk.config import KKConfig
from orb.kk.live_exec import (
    SOURCE_KK,
    bootstrap_sl_price,
    build_close_payload,
    build_open_payload,
    live_enabled,
    live_ingest_succeeded,
)
from orb.kk.vnpy.bootstrap import ensure_vnpy_path

ensure_vnpy_path()

from vnpy.event import EventEngine
from vnpy.trader.constant import Direction, Exchange, Interval, Offset, Product, Status
from vnpy.trader.gateway import BaseGateway
from vnpy.trader.object import (
    AccountData,
    BarData,
    CancelRequest,
    ContractData,
    HistoryRequest,
    OrderData,
    OrderRequest,
    PositionData,
    SubscribeRequest,
    TickData,
    TradeData,
)

from binance_fapi import fetch_klines_forward, fetch_mark_price, klines_to_df
from orb.core.kline_cache import norm_symbol
from orb.core.protocol_client import ingest_signals, lookup_signal, protocol_api_url
from orb.kk.vnpy.sizing import fixed_size_for_symbol, order_volume_to_notional, round_order_volume
from orb.kk.wallet_sync import estimate_close_pnl, record_vnpy_fill, sum_symbol_wallets

logger = logging.getLogger(__name__)

GATEWAY_NAME = "PROTOCOL"


class ProtocolGateway(BaseGateway):
    """CTA 本地 Stop 触发后的真实委托，经 Next-k-protocol 下到币安。"""

    default_name = GATEWAY_NAME
    default_setting: dict[str, str | int | float | bool] = {
        "协议地址": "",
        "轮询间隔秒": 1.0,
        "行情间隔秒": 1.0,
    }
    exchanges = [Exchange.GLOBAL]

    def __init__(self, event_engine: EventEngine, gateway_name: str = GATEWAY_NAME) -> None:
        super().__init__(event_engine, gateway_name)
        self.kk: KKConfig = KKConfig.from_env()
        self.orb_cfg = self.kk.orb_session_cfg()
        self._connected = False
        self._order_seq = 0
        self._orders: Dict[str, OrderData] = {}
        self._api_ids: Dict[str, str] = {}
        self._subs: Set[str] = set()
        self._tick_interval = 1.0
        self._poll_interval = 1.0
        self._tick_thread: Optional[threading.Thread] = None
        self._tick_stop = threading.Event()
        self._open_lots: Dict[str, Dict[str, Any]] = {}
        self._cta_engine = None

    def connect(self, setting: dict) -> None:
        url = str(setting.get("协议地址") or protocol_api_url() or "").strip()
        if not url:
            self.write_log("PROTOCOL_API_URL 未配置，Gateway 无法连接")
            return
        self._tick_interval = float(setting.get("行情间隔秒") or 1.0)
        self._poll_interval = float(setting.get("轮询间隔秒") or 1.0)
        self._connected = True
        self.write_log(f"Protocol Gateway 已连接 {url}")

        for sym in self.kk.symbol_list():
            sym = norm_symbol(sym)
            self.on_contract(self._make_contract(sym))

        self.on_account(
            AccountData(
                accountid="USDT",
                balance=self._account_balance(),
                frozen=0.0,
                gateway_name=self.gateway_name,
            )
        )
        self.query_position()
        self._start_tick_loop()

    def close(self) -> None:
        self._tick_stop.set()
        self._connected = False
        self.write_log("Protocol Gateway 已关闭")

    def subscribe(self, req: SubscribeRequest) -> None:
        sym = norm_symbol(req.symbol)
        self._subs.add(sym)
        self.write_log(f"订阅行情 {sym}")

    def send_order(self, req: OrderRequest) -> str:
        self._order_seq += 1
        orderid = f"{int(time.time() * 1000)}_{self._order_seq}"
        order = req.create_order_data(orderid, self.gateway_name)
        order.status = Status.SUBMITTING
        order.datetime = datetime.now(timezone.utc)
        self._orders[order.vt_orderid] = order
        self.on_order(order)

        worker = threading.Thread(
            target=self._execute_order,
            args=(order.vt_orderid, req),
            daemon=True,
            name=f"protocol-order-{orderid}",
        )
        worker.start()
        return order.vt_orderid

    def cancel_order(self, req: CancelRequest) -> None:
        vt_id = f"{self.gateway_name}.{req.orderid}"
        order = self._orders.get(vt_id)
        if not order:
            return
        order.status = Status.CANCELLED
        self.on_order(order)
        self.write_log(f"本地撤单 {req.symbol} {req.orderid}")

    def query_account(self) -> None:
        if not self._connected:
            return
        self.on_account(
            AccountData(
                accountid="USDT",
                balance=self._account_balance(),
                frozen=0.0,
                gateway_name=self.gateway_name,
            )
        )

    def query_position(self) -> None:
        if not self._connected:
            return
        try:
            import requests

            url = f"{protocol_api_url().rstrip('/')}/api/binance/positions"
            resp = requests.get(url, params={"status": "open", "limit": 200}, timeout=15)
            if resp.status_code >= 400:
                self.write_log(f"查询持仓失败 HTTP {resp.status_code}")
                return
            rows = resp.json() or []
        except Exception as exc:
            self.write_log(f"查询持仓异常: {exc}")
            return

        seen: set[str] = set()
        for row in rows:
            sym = norm_symbol(str(row.get("symbol") or ""))
            if not sym:
                continue
            side = str(row.get("side") or "").upper()
            qty = float(row.get("quantity") or 0.0)
            if qty <= 0:
                continue
            seen.add(sym)
            direction = Direction.LONG if side == "LONG" else Direction.SHORT
            self.on_position(
                PositionData(
                    symbol=sym,
                    exchange=Exchange.GLOBAL,
                    direction=direction,
                    volume=qty,
                    frozen=0.0,
                    price=float(row.get("entry_price") or 0.0),
                    pnl=float(row.get("unrealized_pnl_usdt") or 0.0),
                    gateway_name=self.gateway_name,
                )
            )
        for sym in self.kk.symbol_list():
            sym = norm_symbol(sym)
            if sym not in seen:
                self.on_position(
                    PositionData(
                        symbol=sym,
                        exchange=Exchange.GLOBAL,
                        direction=Direction.NET,
                        volume=0.0,
                        frozen=0.0,
                        price=0.0,
                        pnl=0.0,
                        gateway_name=self.gateway_name,
                    )
                )

    def query_history(self, req: HistoryRequest) -> list[BarData]:
        sym = norm_symbol(req.symbol)
        start = req.start
        end = req.end or datetime.now(timezone.utc)
        start_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)
        interval = "1m"
        if req.interval == Interval.MINUTE:
            interval = "1m"
        elif req.interval == Interval.HOUR:
            interval = "1h"
        elif req.interval == Interval.DAILY:
            interval = "1d"
        rows = fetch_klines_forward(sym, interval, start_ms, end_ms)
        df = klines_to_df(rows)
        if df.empty:
            return []
        out: list[BarData] = []
        for _, r in df.iterrows():
            dt = datetime.fromtimestamp(int(r["open_time"]) / 1000, tz=timezone.utc)
            out.append(
                BarData(
                    symbol=sym,
                    exchange=Exchange.GLOBAL,
                    datetime=dt,
                    interval=req.interval,
                    open_price=float(r["open"]),
                    high_price=float(r["high"]),
                    low_price=float(r["low"]),
                    close_price=float(r["close"]),
                    volume=float(r.get("volume") or 0.0),
                    gateway_name=self.gateway_name,
                )
            )
        return out

    def _make_contract(self, symbol: str) -> ContractData:
        sym = norm_symbol(symbol)
        mark = fetch_mark_price(sym) or 100.0
        eq = self._symbol_equity(sym)
        vol = max(0.001, fixed_size_for_symbol(self.kk, sym, mark, equity_usdt=eq, orb_cfg=self.orb_cfg))
        return ContractData(
            symbol=sym,
            exchange=Exchange.GLOBAL,
            name=sym,
            product=Product.SWAP,
            size=1.0,
            pricetick=0.01,
            min_volume=round_order_volume(vol, mark),
            stop_supported=False,
            net_position=True,
            history_data=True,
            gateway_name=self.gateway_name,
        )

    def _session_date(self) -> str:
        from orb.core.paper import _session_date_now

        return _session_date_now(self.orb_cfg)

    def _kk_pool(self) -> set[str]:
        return {norm_symbol(s) for s in self.kk.symbol_list()}

    def _symbol_equity(self, symbol: str) -> float:
        sym = norm_symbol(symbol)
        base = float(self.kk.equity_usdt or 14.0)
        if not self.kk.compound:
            return base
        try:
            from accumulation_radar import init_db
            from orb.kk.db import migrate_kk_tables
            from orb.kk.equity import symbol_equity_usdt

            conn = init_db()
            try:
                cur = conn.cursor()
                migrate_kk_tables(cur)
                return symbol_equity_usdt(self.kk, sym, cur=cur)
            finally:
                conn.close()
        except Exception as exc:
            logger.warning("[kk-vnpy] wallet read %s: %s", sym, exc)
            return base

    def _account_balance(self) -> float:
        syms = self.kk.symbol_list()
        if not syms:
            return float(self.kk.equity_usdt or 14.0)
        if not self.kk.compound:
            return round(float(self.kk.equity_usdt or 14.0) * len(syms), 4)
        try:
            from accumulation_radar import init_db
            from orb.kk.db import migrate_kk_tables

            conn = init_db()
            try:
                cur = conn.cursor()
                migrate_kk_tables(cur)
                return sum_symbol_wallets(cur, syms, default=float(self.kk.equity_usdt or 14.0))
            finally:
                conn.close()
        except Exception as exc:
            logger.warning("[kk-vnpy] account balance read: %s", exc)
            return round(float(self.kk.equity_usdt or 14.0) * len(syms), 4)

    def _is_eod_close(self) -> bool:
        if not self.kk.eod_flat:
            return False
        import pandas as pd

        now_ms = int(time.time() * 1000)
        ts = pd.Timestamp(now_ms, unit="ms", tz=self.orb_cfg.session_tz)
        return ts.hour > int(self.kk.exit_hour) or (
            ts.hour == int(self.kk.exit_hour) and ts.minute >= int(self.kk.exit_minute)
        )

    def _refresh_strategy_size(self, symbol: str) -> None:
        if not self.kk.compound or self._cta_engine is None:
            return
        sym = norm_symbol(symbol)
        try:
            from orb.kk.vnpy.strategies.king_keltner_kk import KingKeltnerKkStrategy

            eq = self._symbol_equity(sym)
            px = fetch_mark_price(sym) or 100.0
            vol = fixed_size_for_symbol(self.kk, sym, px, equity_usdt=eq, orb_cfg=self.orb_cfg)
            name = f"kk_{sym.lower()}"
            strategy = self._cta_engine.strategies.get(name)
            if strategy is None:
                return
            if vol <= 0 or abs(float(strategy.fixed_size) - vol) < 1e-6:
                return
            strategy.fixed_size = vol
            setting = {**KingKeltnerKkStrategy.from_kk_config(self.kk), "fixed_size": vol}
            self._cta_engine.update_strategy_setting(name, setting)
            self.write_log(f"compound fixed_size {sym} -> {vol}")
        except Exception as exc:
            self.write_log(f"compound size refresh failed {symbol}: {exc}")

    def _open_position_count(self) -> int:
        pool = self._kk_pool()
        try:
            import requests

            url = f"{protocol_api_url().rstrip('/')}/api/binance/positions"
            resp = requests.get(url, params={"status": "open", "limit": 200}, timeout=15)
            if resp.status_code >= 400:
                return 0
            rows = resp.json() or []
        except Exception:
            return 0
        return sum(
            1
            for row in rows
            if norm_symbol(str(row.get("symbol") or "")) in pool
            and float(row.get("quantity") or 0.0) > 0
        )

    def _execute_order(self, vt_orderid: str, req: OrderRequest) -> None:
        order = self._orders.get(vt_orderid)
        if not order:
            return
        sym = norm_symbol(req.symbol)
        bar_ms = int(time.time() * 1000)
        session_date = self._session_date()
        if self.kk.shadow:
            order.status = Status.REJECTED
            self.on_order(order)
            self.write_log(f"KK_SHADOW=1 跳过实盘下单 {sym}")
            return
        if not live_enabled(self.kk):
            order.status = Status.REJECTED
            self.on_order(order)
            self.write_log(f"KK_LIVE_ENABLED=0 或 PROTOCOL 未配置，拒单 {sym}")
            return
        try:
            if req.offset == Offset.OPEN:
                max_pos = int(self.kk.max_open_positions or 0)
                if max_pos > 0 and self._open_position_count() >= max_pos:
                    order.status = Status.REJECTED
                    self.on_order(order)
                    self.write_log(f"已达最大持仓数 {max_pos}，拒单 {sym}")
                    return
                side = "LONG" if req.direction == Direction.LONG else "SHORT"
                entry_px = float(req.price or 0.0)
                eq = self._symbol_equity(sym)
                notional = float(req.volume) * max(1e-9, entry_px)
                if notional <= 0:
                    notional = order_volume_to_notional(
                        self.kk, entry_px, equity_usdt=eq, orb_cfg=self.orb_cfg
                    )
                sl_px = bootstrap_sl_price(side=side, entry=entry_px)
                payload = build_open_payload(
                    symbol=sym,
                    side=side,
                    entry_price=entry_px,
                    notional_usdt=notional,
                    session_date=session_date,
                    bar_ms=bar_ms,
                    sl_price=sl_px,
                    kk=self.kk,
                    orb_cfg=self.orb_cfg,
                )
                api_id = str(payload["api_signal_id"])
                result = ingest_signals([payload])
            else:
                pos_side = "LONG" if req.direction == Direction.SHORT else "SHORT"
                outcome = "eod" if self._is_eod_close() else "close"
                # 实盘平仓一律市价（不传 close_price），与 protocol 行为一致
                payload = build_close_payload(
                    symbol=sym,
                    side=pos_side,
                    session_date=session_date,
                    bar_ms=bar_ms,
                    outcome=outcome,
                    close_price=None,
                )
                api_id = str(payload["api_signal_id"])
                result = ingest_signals([payload])

            self._api_ids[vt_orderid] = api_id
            if not live_ingest_succeeded(result):
                order.status = Status.REJECTED
                self.on_order(order)
                self.write_log(f"protocol 拒单 {sym} {api_id} {result}")
                return

            fill = self._wait_fill(api_id, timeout_sec=45.0)
            if not fill:
                order.status = Status.REJECTED
                self.on_order(order)
                self.write_log(f"protocol 成交超时 {sym} {api_id}")
                return

            px = float(fill.get("entry_price") or fill.get("close_price") or req.price or 0.0)
            if px <= 0:
                px = float(req.price or 0.0)
            order.status = Status.ALLTRADED
            order.traded = float(req.volume)
            self.on_order(order)
            trade = TradeData(
                symbol=sym,
                exchange=Exchange.GLOBAL,
                orderid=order.orderid,
                tradeid=str(uuid.uuid4()),
                direction=req.direction,
                offset=req.offset,
                price=px,
                volume=float(req.volume),
                datetime=datetime.now(timezone.utc),
                gateway_name=self.gateway_name,
            )
            self.on_trade(trade)
            if req.offset == Offset.OPEN:
                self._open_lots[sym] = {
                    "side": "LONG" if req.direction == Direction.LONG else "SHORT",
                    "entry": px,
                    "notional_usdt": notional,
                    "volume": float(req.volume),
                }
                try:
                    record_vnpy_fill(
                        symbol=sym,
                        event="open",
                        side="LONG" if req.direction == Direction.LONG else "SHORT",
                        price=px,
                        volume=float(req.volume),
                        notional_usdt=notional,
                        session_date=session_date,
                        bar_ms=bar_ms,
                        kk=self.kk,
                    )
                except Exception as exc:
                    self.write_log(f"trade persist open failed {sym}: {exc}")
            else:
                lot = self._open_lots.get(sym, {})
                pos_side = str(lot.get("side") or ("LONG" if req.direction == Direction.SHORT else "SHORT"))
                entry_px = float(lot.get("entry") or px)
                notion = float(lot.get("notional_usdt") or 0.0)
                if notion <= 0:
                    notion = float(req.volume) * max(1e-9, px)
                outcome = "eod" if self._is_eod_close() else "close"
                gross, fee, net = estimate_close_pnl(
                    side=pos_side,
                    entry=entry_px,
                    exit_px=px,
                    notional_usdt=notion,
                    kk=self.kk,
                )
                try:
                    record_vnpy_fill(
                        symbol=sym,
                        event="close",
                        side=pos_side,
                        price=px,
                        volume=float(req.volume),
                        notional_usdt=notion,
                        session_date=session_date,
                        bar_ms=bar_ms,
                        kk=self.kk,
                        outcome=outcome,
                        pnl_usdt=net,
                        pnl_gross=gross,
                        fee_usdt=fee,
                    )
                except Exception as exc:
                    self.write_log(f"trade persist close failed {sym}: {exc}")
                self._open_lots.pop(sym, None)
                self._refresh_strategy_size(sym)
                self.query_account()
            self.query_position()
        except Exception as exc:
            logger.exception("[kk-vnpy] execute order %s failed", sym)
            order.status = Status.REJECTED
            self.on_order(order)
            self.write_log(f"下单异常 {sym}: {exc}")

    def _wait_fill(self, api_id: str, *, timeout_sec: float) -> Optional[Dict[str, Any]]:
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            try:
                row = lookup_signal(source=SOURCE_KK, api_signal_id=api_id)
            except Exception as exc:
                logger.warning("[kk-vnpy] lookup %s: %s", api_id, exc)
                row = None
            if row:
                status = str(row.get("status") or "").lower()
                if status == "traded":
                    return row
                if status == "submitted":
                    # 市价单可能短暂 submitted，继续轮询直到 traded
                    continue
            time.sleep(self._poll_interval)
        return None

    def _start_tick_loop(self) -> None:
        if self._tick_thread and self._tick_thread.is_alive():
            return
        self._tick_stop.clear()

        def _loop() -> None:
            while not self._tick_stop.is_set():
                for sym in list(self._subs):
                    px = fetch_mark_price(sym)
                    if not px or px <= 0:
                        continue
                    tick = TickData(
                        symbol=sym,
                        exchange=Exchange.GLOBAL,
                        datetime=datetime.now(timezone.utc),
                        last_price=float(px),
                        ask_price_1=float(px),
                        bid_price_1=float(px),
                        gateway_name=self.gateway_name,
                    )
                    self.on_tick(tick)
                self._tick_stop.wait(self._tick_interval)

        self._tick_thread = threading.Thread(target=_loop, daemon=True, name="protocol-tick")
        self._tick_thread.start()
