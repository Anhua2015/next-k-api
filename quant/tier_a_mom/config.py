"""Tier-A mom-turn lane config — Binance/Bitget SPOT long-only."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import List

from quant.common.exchange_env import resolve_live_exchange_id, resolve_market_data_exchange_id
from quant.common.scanner_potential_pool import merge_with_file_symbols
from quant.common.symbols import parse_symbol_list
from quant.tier_a_mom.paths import resolve_tier_a_mom_symbols_path
from quant.tier_a_mom.switches import TIER_A_MOM_SWITCH


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name, "")
    if not str(raw).strip():
        return float(default)
    try:
        return float(str(raw).strip())
    except ValueError:
        return float(default)


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name, "")
    if not str(raw).strip():
        return int(default)
    try:
        return int(float(str(raw).strip()))
    except ValueError:
        return int(default)


def _bool_env(name: str, default: bool) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw not in ("0", "false", "no", "off")


@dataclass
class TierAMomConfig:
    """
    Production spot strategy aligned with breakoutscanner research:
      mom_turn_pool10_smart_exit
    """

    lane: str = "tier_a_mom"
    engine: str = "vnpy"
    market: str = "spot"
    strategy_id: str = "mom_turn_pool10_smart_exit"
    enabled: bool = False
    shadow: bool = False
    symbols_file: str = ""
    symbols: List[str] | None = None
    use_potential_pool: bool = True
    prefer_entry_alerts: bool = False
    potential_pool_max: int = 30
    require_pool_ok: bool = True
    allow_reclaim_entry: bool = False
    equity_usdt: float = 100_000.0
    position_pct: float = 0.15
    compound: bool = True
    live_enabled: bool = False
    live_exchange: str = "binance"
    market_data_exchange: str = "binance"
    live_leverage: float = 1.0  # spot
    max_notional_usdt: float = 0.0
    max_open_positions: int = 5
    init_bar_days: int = 120
    stop_pct: float = 0.08
    tp1_pct: float = 0.30
    tp2_pct: float = 0.50
    trail_after_pct: float = 0.10
    trail_ema: int = 20
    max_hold_bars: int = 90
    long_only: bool = True
    rth_only: bool = False
    eod_flat: bool = False
    vnpy_idle_outside_rth: bool = False

    @classmethod
    def from_env(cls) -> "TierAMomConfig":
        prefix = "TIER_A_MOM_VNPY_"
        alt = "TIER_A_MOM_"
        sym_file = (os.getenv(f"{prefix}SYMBOLS_FILE") or "").strip() or str(
            resolve_tier_a_mom_symbols_path()
        )
        inline = (os.getenv(f"{prefix}SYMBOLS") or os.getenv(f"{alt}SYMBOLS") or "").strip()
        symbols: List[str] | None = None
        if inline:
            symbols = parse_symbol_list(inline)
        elif Path(sym_file).is_file():
            symbols = parse_symbol_list(Path(sym_file).read_text(encoding="utf-8"))
        return cls(
            enabled=TIER_A_MOM_SWITCH.enabled(),
            shadow=TIER_A_MOM_SWITCH.shadow(),
            symbols_file=sym_file,
            symbols=symbols,
            use_potential_pool=_bool_env(f"{prefix}USE_POOL", _bool_env(f"{alt}USE_POOL", True)),
            prefer_entry_alerts=_bool_env(
                f"{prefix}PREFER_ALERTS", _bool_env(f"{alt}PREFER_ALERTS", False)
            ),
            potential_pool_max=max(0, _int_env(f"{prefix}POOL_MAX", 30)),
            require_pool_ok=_bool_env(f"{prefix}REQUIRE_POOL_OK", True),
            allow_reclaim_entry=_bool_env(f"{prefix}ALLOW_RECLAIM", False),
            equity_usdt=_float_env(f"{prefix}EQUITY_USDT", _float_env(f"{alt}EQUITY_USDT", 100_000.0)),
            position_pct=_float_env(f"{prefix}POSITION_PCT", _float_env(f"{alt}POSITION_PCT", 0.15)),
            compound=_bool_env(f"{prefix}COMPOUND", True),
            live_enabled=TIER_A_MOM_SWITCH.live(),
            live_exchange=resolve_live_exchange_id(),
            market_data_exchange=resolve_market_data_exchange_id(),
            live_leverage=_float_env(f"{prefix}LIVE_LEVERAGE", 1.0),
            max_notional_usdt=_float_env(f"{prefix}MAX_NOTIONAL_USDT", 0.0),
            max_open_positions=max(0, _int_env(f"{prefix}MAX_OPEN_POSITIONS", 5)),
            init_bar_days=max(70, _int_env(f"{prefix}INIT_BAR_DAYS", 120)),
            stop_pct=_float_env(f"{prefix}STOP_PCT", 0.08),
            tp1_pct=_float_env(f"{prefix}TP1_PCT", 0.30),
            tp2_pct=_float_env(f"{prefix}TP2_PCT", 0.50),
            trail_after_pct=_float_env(f"{prefix}TRAIL_AFTER_PCT", 0.10),
            trail_ema=_int_env(f"{prefix}TRAIL_EMA", 20),
            max_hold_bars=_int_env(f"{prefix}MAX_HOLD_BARS", 90),
        )

    def symbol_list(self) -> List[str]:
        base: List[str]
        if self.symbols:
            base = list(self.symbols)
        else:
            p = Path(self.symbols_file)
            base = parse_symbol_list(p.read_text(encoding="utf-8")) if p.is_file() else []
        return merge_with_file_symbols(
            base,
            use_pool=self.use_potential_pool,
            prefer_alerts=self.prefer_entry_alerts,
            max_symbols=self.potential_pool_max,
        )

    def is_vnpy_engine(self) -> bool:
        return str(self.engine).lower() == "vnpy"

    def orb_session_cfg(self):
        from quant.common.config import OrbConfig

        return OrbConfig.from_env()
