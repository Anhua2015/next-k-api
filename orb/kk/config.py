"""King Keltner lane 配置（KK_* env，与 ORB_V2_* 隔离）。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import List

from orb.core.config import OrbConfig
from orb.kk.paths import resolve_kk_symbols_path
from orb.core.symbols import parse_symbol_list


def _truthy(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "")
    if not str(raw).strip():
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name, "")
    if not str(raw).strip():
        return float(default)
    try:
        return float(str(raw).strip())
    except ValueError:
        return float(default)


def _str_env(name: str, default: str) -> str:
    raw = (os.getenv(name) or "").strip().lower()
    return raw or default


@dataclass
class KKConfig:
    lane: str = "king_keltner"
    engine: str = "vnpy"  # vnpy | paper
    enabled: bool = True
    scheduler_enabled: bool = True
    shadow: bool = False
    symbols_file: str = ""
    symbols: List[str] | None = None
    equity_usdt: float = 14.0
    risk_pct: float = 0.01
    compound: bool = True
    rth_only: bool = True
    eod_flat: bool = True
    exit_hour: int = 15
    exit_minute: int = 55
    fee_maker_bps: float = 2.0
    fee_taker_bps: float = 4.0
    slip_bps_entry: float = 5.0
    slip_bps_exit: float = 5.0
    macro_filter: bool = True
    one_trade_per_session: bool = False
    scan_interval_minutes: int = 1
    live_enabled: bool = False
    live_leverage: float = 0.0
    max_notional_usdt: float = 0.0
    max_open_positions: int = 0
    vnpy_enabled: bool = False
    vnpy_idle_outside_rth: bool = True
    vnpy_poll_sec: float = 1.0
    vnpy_tick_sec: float = 1.0

    @classmethod
    def from_env(cls) -> KKConfig:
        sym_file = (os.getenv("KK_SYMBOLS_FILE") or "").strip() or str(resolve_kk_symbols_path())
        inline = (os.getenv("KK_SYMBOLS") or "").strip()
        symbols: List[str] | None = None
        if inline:
            symbols = parse_symbol_list(inline)
        elif Path(sym_file).is_file():
            symbols = parse_symbol_list(Path(sym_file).read_text(encoding="utf-8"))
        live_on = _truthy("KK_LIVE_ENABLED", default=False)
        engine = _str_env("KK_ENGINE", "vnpy")
        if engine not in ("vnpy", "paper"):
            engine = "vnpy"
        # 兼容旧开关
        if _truthy("KK_VNPY_ENABLED", default=False):
            engine = "vnpy"
        vnpy_on = engine == "vnpy"
        return cls(
            engine=engine,
            enabled=_truthy("KK_ENABLED", default=True),
            scheduler_enabled=_truthy("KK_SCHEDULER_ENABLED", default=True),
            shadow=_truthy("KK_SHADOW", default=False),
            symbols_file=sym_file,
            symbols=symbols,
            equity_usdt=_float_env("KK_EQUITY_USDT", 14.0),
            risk_pct=_float_env("KK_RISK_PCT", 0.01),
            compound=_truthy("KK_COMPOUND", default=True),
            rth_only=_truthy("KK_RTH_ONLY", default=True),
            eod_flat=_truthy("KK_EOD_FLAT", default=True),
            exit_hour=int(_float_env("KK_EXIT_HOUR", 15)),
            exit_minute=int(_float_env("KK_EXIT_MINUTE", 55)),
            fee_maker_bps=_float_env("KK_FEE_MAKER_BPS", 2.0),
            fee_taker_bps=_float_env("KK_FEE_TAKER_BPS", 4.0),
            slip_bps_entry=_float_env("KK_SLIP_BPS_ENTRY", 5.0),
            slip_bps_exit=_float_env("KK_SLIP_BPS_EXIT", 5.0),
            macro_filter=_truthy("KK_MACRO_FILTER", default=True),
            one_trade_per_session=_truthy("KK_ONE_TRADE_PER_SESSION", default=False),
            scan_interval_minutes=max(1, int(_float_env("KK_SCAN_INTERVAL_MINUTES", 1))),
            live_enabled=live_on,
            live_leverage=_float_env("KK_LIVE_LEVERAGE", 5.0 if live_on else 0.0),
            max_notional_usdt=_float_env("KK_MAX_NOTIONAL_USDT", 0.0),
            max_open_positions=max(0, int(_float_env("KK_MAX_OPEN_POSITIONS", 7 if live_on else 0))),
            vnpy_enabled=vnpy_on,
            vnpy_idle_outside_rth=_truthy("KK_VNPY_IDLE_OUTSIDE_RTH", default=True),
            vnpy_poll_sec=_float_env("KK_VNPY_POLL_SEC", 1.0),
            vnpy_tick_sec=_float_env("KK_VNPY_TICK_SEC", 1.0),
        )

    def symbol_list(self) -> List[str]:
        if self.symbols:
            return list(self.symbols)
        p = Path(self.symbols_file)
        if p.is_file():
            return parse_symbol_list(p.read_text(encoding="utf-8"))
        return []

    def is_paper_engine(self) -> bool:
        return str(self.engine).lower() == "paper"

    def is_vnpy_engine(self) -> bool:
        return str(self.engine).lower() == "vnpy"

    def orb_session_cfg(self) -> OrbConfig:
        """共用 session / RTH / 宏观日历（只读 ORB_SESSION_*，不写 orb_signals）。"""
        cfg = OrbConfig.from_env()
        cfg.risk_pct = float(self.risk_pct)
        cfg.fixed_notional_usdt = 0.0
        return cfg
