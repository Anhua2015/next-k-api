"""KK vnpy 引擎：官方 BinanceLinearGateway + KingKeltnerStrategy。"""

from __future__ import annotations

import logging
import time
from threading import Event
from typing import Any, Dict, List, Optional

from orb.kk.config import KKConfig
from orb.kk.vnpy.bootstrap import ensure_vnpy_path

ensure_vnpy_path()

from vnpy.event import EventEngine
from vnpy.trader.engine import MainEngine
from vnpy.trader.setting import SETTINGS
from vnpy_ctastrategy import CtaStrategyApp
from vnpy_ctastrategy.base import EVENT_CTA_LOG

from binance_fapi import fetch_mark_price
from orb.core.kline_cache import norm_symbol
from orb.core.macro_calendar import is_macro_skip_day
from orb.core.paper import _session_date_now
from orb.kk.vnpy.binance_gateway import (
    GATEWAY_NAME,
    KkBinanceLinearGateway,
    binance_connect_setting,
    binance_credentials_configured,
    kk_vt_symbol,
)
from orb.kk.vnpy.sizing import fixed_size_for_symbol
from orb.kk.vnpy.strategies.king_keltner_kk import KingKeltnerKkStrategy
from orb.kk.vnpy.cta_rth_patch import apply_cta_engine_patches
from orb.kk.vnpy.position_sync import sync_cta_positions

logger = logging.getLogger(__name__)


def _configure_logging() -> None:
    SETTINGS["log.active"] = True
    SETTINGS["log.console"] = True


def _wait_contracts(gateway: KkBinanceLinearGateway, symbols: List[str], *, timeout_sec: float) -> bool:
    names = {norm_symbol(s) for s in symbols}
    deadline = time.time() + max(10.0, float(timeout_sec))
    while time.time() < deadline:
        if names.issubset(set(gateway.name_contract_map.keys())):
            return True
        time.sleep(0.5)
    missing = sorted(names - set(gateway.name_contract_map.keys()))
    if missing:
        logger.error("[kk-vnpy] 合约未就绪: %s", missing)
    return not missing


class KkVnpyEngine:
    """单次 vnpy 会话；stop_event 置位后 shutdown。"""

    def __init__(self) -> None:
        self._event_engine: Optional[EventEngine] = None
        self._main_engine: Optional[MainEngine] = None
        self._cta_engine = None
        self._started: List[str] = []

    def bootstrap(self, *, init_wait_sec: float = 30.0) -> Dict[str, Any]:
        kk = KKConfig.from_env()
        out: Dict[str, Any] = {
            "ok": False,
            "engine": "vnpy",
            "gateway": GATEWAY_NAME,
            "lane": kk.lane,
            "symbols": [],
            "strategies": [],
            "reason": None,
        }
        if not kk.enabled:
            out.update({"ok": True, "skipped": True, "reason": "kk_disabled"})
            return out
        if not kk.vnpy_enabled:
            out.update({"ok": True, "skipped": True, "reason": "kk_vnpy_disabled"})
            return out
        if kk.live_enabled and not binance_credentials_configured():
            out.update({"ok": False, "reason": "binance_credentials_missing"})
            return out

        symbols = kk.symbol_list()
        out["symbols"] = symbols
        if not symbols:
            out.update({"ok": False, "reason": "no_symbols"})
            return out

        session_date = _session_date_now(kk.orb_session_cfg())
        if kk.macro_filter and is_macro_skip_day(session_date):
            out.update({"ok": True, "skipped": True, "reason": "macro_skip"})
            return out

        _configure_logging()
        self._event_engine = EventEngine()
        self._main_engine = MainEngine(self._event_engine)
        self._main_engine.add_gateway(KkBinanceLinearGateway, GATEWAY_NAME)
        self._cta_engine = self._main_engine.add_app(CtaStrategyApp)
        apply_cta_engine_patches()
        self._cta_engine.classes["KingKeltnerKkStrategy"] = KingKeltnerKkStrategy
        self._event_engine.register(EVENT_CTA_LOG, lambda e: logger.info("[cta] %s", e.data))

        gateway = self._main_engine.get_gateway(GATEWAY_NAME)
        if gateway:
            gateway.connect(binance_connect_setting())

        contract_wait = max(60.0, float(init_wait_sec) * max(1, len(symbols)))
        if gateway and not _wait_contracts(gateway, symbols, timeout_sec=contract_wait):
            out.update({"ok": False, "reason": "binance_contracts_not_ready"})
            return out

        if kk.live_enabled and binance_credentials_configured():
            try:
                from orb.kk.vnpy.binance_account import ensure_pool_leverage

                ensure_pool_leverage(symbols, kk)
            except Exception as exc:
                logger.warning("[kk-vnpy] leverage setup failed: %s", exc)

        self._cta_engine.init_engine()
        kk_settings = KingKeltnerKkStrategy.from_kk_config(kk)
        self._started = []

        wallet_cur = None
        wallet_conn = None
        if kk.compound:
            try:
                from accumulation_radar import init_db
                from orb.kk.db import migrate_kk_tables
                from orb.kk.equity import symbol_equity_usdt

                wallet_conn = init_db()
                wallet_cur = wallet_conn.cursor()
                migrate_kk_tables(wallet_cur)
            except Exception as exc:
                logger.warning("[kk-vnpy] per-symbol wallet load skipped: %s", exc)
                wallet_cur = None

        try:
            for sym in symbols:
                sym = norm_symbol(sym)
                px = fetch_mark_price(sym) or 100.0
                if wallet_cur is not None:
                    eq = symbol_equity_usdt(kk, sym, cur=wallet_cur)
                else:
                    eq = float(kk.equity_usdt or 14.0)
                vol = fixed_size_for_symbol(kk, sym, px, equity_usdt=eq)
                name = f"kk_{sym.lower()}"
                self._cta_engine.add_strategy(
                    class_name="KingKeltnerKkStrategy",
                    strategy_name=name,
                    vt_symbol=kk_vt_symbol(sym),
                    setting={**kk_settings, "fixed_size": vol},
                )
                self._started.append(name)
        finally:
            if wallet_conn is not None:
                wallet_conn.close()

        futures = self._cta_engine.init_all_strategies()
        init_timeout = max(120.0, float(init_wait_sec) * max(1, len(symbols)))
        deadline = time.time() + init_timeout
        for name, fut in futures.items():
            remaining = max(1.0, deadline - time.time())
            try:
                fut.result(timeout=remaining)
            except Exception as exc:
                logger.warning("[kk-vnpy] strategy init %s failed: %s", name, exc)
        not_ready = [
            n for n, s in self._cta_engine.strategies.items() if not getattr(s, "inited", False)
        ]
        if not_ready:
            logger.error("[kk-vnpy] strategies not inited before start: %s", not_ready)
        self._cta_engine.start_all_strategies()

        if kk.live_enabled and binance_credentials_configured():
            try:
                synced = sync_cta_positions(self._cta_engine, symbols)
                if synced:
                    logger.info("[kk-vnpy] position sync: %s", synced)
            except Exception as exc:
                logger.warning("[kk-vnpy] position sync failed: %s", exc)

        out["strategies"] = list(self._started)
        out["ok"] = True
        logger.info("[kk-vnpy] started %d strategies via %s: %s", len(self._started), GATEWAY_NAME, self._started)
        return out

    def run_until(self, stop_event: Event) -> None:
        while not stop_event.is_set():
            stop_event.wait(1.0)

    def shutdown(self) -> None:
        try:
            if self._cta_engine is not None:
                self._cta_engine.stop_all_strategies()
        except Exception as exc:
            logger.warning("[kk-vnpy] stop strategies: %s", exc)
        try:
            if self._main_engine is not None:
                self._main_engine.close()
        except Exception as exc:
            logger.warning("[kk-vnpy] main_engine close: %s", exc)
        self._cta_engine = None
        self._main_engine = None
        self._event_engine = None
        self._started = []


def run_vnpy_kk(
    *,
    run_seconds: Optional[float] = None,
    init_wait_sec: float = 30.0,
    stop_event: Optional[Event] = None,
) -> Dict[str, Any]:
    engine = KkVnpyEngine()
    out = engine.bootstrap(init_wait_sec=init_wait_sec)
    if not out.get("ok") or out.get("skipped"):
        return out
    evt = stop_event or Event()
    t0 = time.time()
    try:
        while not evt.is_set():
            if run_seconds is not None and (time.time() - t0) >= float(run_seconds):
                break
            evt.wait(1.0)
    except KeyboardInterrupt:
        logger.info("[kk-vnpy] interrupted")
    finally:
        engine.shutdown()
    return out
