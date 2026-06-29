#!/usr/bin/env python3
"""Shared helpers for pairs CLI tools."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict

import pandas as pd

from orb.core.backtest import _load_range
from orb.core.kline_cache import load_klines, save_klines
from pairs.backtest import PairsBacktestConfig, align_leg_closes


def load_pair_config(path: Path) -> PairsBacktestConfig:
    raw = json.loads(path.read_text(encoding="utf-8"))
    fields = PairsBacktestConfig.__dataclass_fields__
    return PairsBacktestConfig(**{k: v for k, v in raw.items() if k in fields})


def load_leg(symbol: str, interval: str, *, days: float, fetch: bool) -> pd.DataFrame:
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - int(days * 86_400_000)
    df = load_klines(symbol, interval, start_ms=start_ms, end_ms=end_ms)
    if df.empty and fetch:
        df = _load_range(symbol, interval, start_ms, end_ms)
        if not df.empty:
            save_klines(symbol, interval, df)
    return df


def load_aligned_prices(cfg: PairsBacktestConfig, *, days: float, fetch: bool) -> pd.DataFrame:
    df1 = load_leg(cfg.leg1, cfg.interval, days=days, fetch=fetch)
    df2 = load_leg(cfg.leg2, cfg.interval, days=days, fetch=fetch)
    if df1.empty or df2.empty:
        return pd.DataFrame()
    return align_leg_closes(df1, df2)


def merge_framework_defaults(cfg: PairsBacktestConfig, framework: Dict[str, Any]) -> PairsBacktestConfig:
    """Overlay portfolio-level framework keys onto a pair config."""
    if not framework:
        return cfg
    fields = PairsBacktestConfig.__dataclass_fields__
    kw = {k: getattr(cfg, k) for k in fields}
    for k, v in framework.items():
        if k in fields and k not in ("leg1", "leg2"):
            kw[k] = v
    return PairsBacktestConfig(**kw)
