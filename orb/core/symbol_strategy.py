"""Per-symbol ORB strategy from config/orb/<TICKER>/strategy.env."""

from __future__ import annotations

from dataclasses import replace
from functools import lru_cache
from pathlib import Path
from typing import Dict, Optional

from orb.core.config import OrbConfig
from orb.ml.paths import PROJECT_ROOT

_ENV_TO_FIELD: Dict[str, str] = {
    "ORB_OR_MINUTES": "or_minutes",
    "ORB_RISK_PCT": "risk_pct",
    "ORB_TRADE_WINDOW_MINUTES": "trade_window_minutes",
    "ORB_MIN_RANGE_WIDTH_PCT": "min_or_width_pct",
    "ORB_MIN_OR_WIDTH_PCT": "min_or_width_pct",
    "ORB_MAX_RANGE_WIDTH_PCT": "max_or_width_pct",
    "ORB_EXIT_MODE": "exit_mode",
    "ORB_SL_MODE": "sl_mode",
    "ORB_ATR_PERIOD": "atr_period",
    "ORB_ATR_SL_FRACTION": "atr_sl_fraction",
    "ORB_ATR_BREAKOUT_MULT": "atr_breakout_mult",
    "ORB_ENTRY_MODE": "entry_mode",
    "ORB_ARM_AT_OR_CLOSE": "arm_at_or_close",
    "ORB_PREPLACE_OCO": "preplace_oco",
    "ORB_MACRO_FILTER": "macro_filter",
    "ORB_EARLY_EXIT_MINUTES": "early_exit_minutes",
    "ORB_VWAP_FILTER": "vwap_filter",
    "ORB_VOL_MULT": "vol_mult",
    "ORB_SIGNAL_INTERVAL": "signal_interval",
}


def ticker_from_symbol(symbol: str) -> str:
    s = str(symbol or "").strip().upper()
    if s.endswith("USDT"):
        return s[:-4]
    return s


def strategy_env_path(symbol: str) -> Optional[Path]:
    path = PROJECT_ROOT / "config" / "orb" / ticker_from_symbol(symbol) / "strategy.env"
    return path if path.is_file() else None


def parse_strategy_env(path: Path) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        out[key.strip()] = val.strip()
    return out


def _coerce_field(name: str, raw: str):
    if name in (
        "or_minutes",
        "trade_window_minutes",
        "atr_period",
        "early_exit_minutes",
        "vol_ma_period",
    ):
        return max(0 if name != "or_minutes" else 1, int(float(raw)))
    if name in (
        "risk_pct",
        "min_or_width_pct",
        "max_or_width_pct",
        "atr_sl_fraction",
        "atr_breakout_mult",
        "vol_mult",
    ):
        return max(0.0, float(raw))
    if name in (
        "one_trade_per_session",
        "arm_at_or_close",
        "preplace_oco",
        "macro_filter",
        "vwap_filter",
    ):
        return str(raw).strip().lower() in ("1", "true", "yes", "on")
    return raw.strip()


def config_for_symbol(symbol: str, base: Optional[OrbConfig] = None) -> OrbConfig:
    """Merge config/orb/<TICKER>/strategy.env onto base (global .env.oi)."""
    base_cfg = base or OrbConfig.from_env()
    path = strategy_env_path(symbol)
    if path is None:
        return base_cfg
    env = parse_strategy_env(path)
    kwargs: Dict[str, object] = {}
    for env_key, field in _ENV_TO_FIELD.items():
        if env_key not in env:
            continue
        kwargs[field] = _coerce_field(field, env[env_key])
    if "ORB_ONE_TRADE_PER_SESSION" in env:
        kwargs["one_trade_per_session"] = _coerce_field(
            "one_trade_per_session", env["ORB_ONE_TRADE_PER_SESSION"]
        )
    if "ORB_SYMBOL_BOT_EQUITY_USDT" in env:
        kwargs["symbol_bot_equity_usdt"] = max(0.0, float(env["ORB_SYMBOL_BOT_EQUITY_USDT"]))
    elif "ORB_SYMBOL_BOT_EQUITY" in env:
        kwargs["symbol_bot_equity_usdt"] = max(0.0, float(env["ORB_SYMBOL_BOT_EQUITY"]))
    if not kwargs:
        return base_cfg
    return replace(base_cfg, **kwargs)


@lru_cache(maxsize=64)
def _cached_config_for_symbol(symbol: str) -> OrbConfig:
    return config_for_symbol(symbol)


def config_for_symbol_cached(symbol: str, base: Optional[OrbConfig] = None) -> OrbConfig:
    if base is None:
        return _cached_config_for_symbol(str(symbol).strip().upper())
    return config_for_symbol(symbol, base=base)
