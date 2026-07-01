"""KK vnpy 引擎：供 kk_vnpy_runner 与 Railway API 进程内嵌启动。"""

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
from orb.core.protocol_client import protocol_api_url
from orb.kk.live_exec import live_enabled
from orb.kk.vnpy.protocol_gateway import GATEWAY_NAME, ProtocolGateway
from orb.kk.vnpy.sizing import fixed_size_for_symbol
from orb.kk.vnpy.strategies.king_keltner_kk import KingKeltnerKkStrategy

logger = logging.getLogger(__name__)


def _configure_logging() -> None:
    SETTINGS["log.active"] = True
    SETTINGS["log.console"] = True


def _wait_protocol(*, timeout_sec: float) -> bool:
    import requests

    url = (protocol_api_url() or "").strip().rstrip("/")
    if not url:
        return False
    health = f"{url}/api/binance/health"
    deadline = time.time() + max(5.0, float(timeout_sec))
    while time.time() < deadline:
        try:
            if requests.get(health, timeout=5).status_code < 400:
                return True
        except Exception:
            pass
        time.sleep(2.0)
    return False


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
        if live_enabled(kk) and not protocol_api_url().strip():
            out.update({"ok": False, "reason": "protocol_api_url_missing"})
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

        wait_sec = float(
            __import__("os").getenv("KK_PROTOCOL_WAIT_SEC") or 90
        )
        protocol_required = (
            __import__("os").getenv("KK_PROTOCOL_REQUIRED", "1").strip().lower()
            in ("1", "true", "yes", "on")
        )
        if live_enabled(kk) and not _wait_protocol(timeout_sec=wait_sec):
            msg = "[kk-vnpy] protocol 未就绪"
            if protocol_required:
                logger.error("%s，启动中止", msg)
                out.update({"ok": False, "reason": "protocol_not_ready"})
                return out
            logger.warning("%s，仍尝试启动引擎", msg)

        _configure_logging()
        self._event_engine = EventEngine()
        self._main_engine = MainEngine(self._event_engine)
        self._main_engine.add_gateway(ProtocolGateway, GATEWAY_NAME)
        self._cta_engine = self._main_engine.add_app(CtaStrategyApp)
        self._cta_engine.classes["KingKeltnerKkStrategy"] = KingKeltnerKkStrategy
        self._event_engine.register(EVENT_CTA_LOG, lambda e: logger.info("[cta] %s", e.data))

        gateway = self._main_engine.get_gateway(GATEWAY_NAME)
        if gateway:
            gateway._cta_engine = self._cta_engine
            gateway.connect(
                {
                    "协议地址": protocol_api_url(),
                    "轮询间隔秒": kk.vnpy_poll_sec,
                    "行情间隔秒": kk.vnpy_tick_sec,
                }
            )
        time.sleep(1.0)

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
                    vt_symbol=f"{sym}.GLOBAL",
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

        out["strategies"] = list(self._started)
        out["ok"] = True
        logger.info("[kk-vnpy] started %d strategies: %s", len(self._started), self._started)
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
