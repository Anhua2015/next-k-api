"""Unit tests for Tier-A mom-turn spot core + pool helpers."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from quant.tier_a_mom.core import (
    PositionState,
    detect_mom_turn_signal,
    mom_turn_positive,
    on_bar_exit,
    stop_price,
    trade_plan,
)


def test_mom_turn_positive():
    close = np.array([100.0] * 6 + [99.0, 98.0, 97.0, 96.0, 95.0, 101.0], dtype=float)
    assert mom_turn_positive(close, 5) is True


def test_stop_uses_day_low_when_tighter():
    entry = 100.0
    assert abs(stop_price(entry, 97.0) - 97.0) < 1e-9
    assert abs(stop_price(entry, 90.0) - 92.0) < 1e-9


def test_trade_plan_levels():
    plan = trade_plan(100.0, 97.0)
    assert plan.stop == 97.0
    assert abs(plan.tp1 - 130.0) < 1e-9
    assert abs(plan.tp2 - 150.0) < 1e-9


def test_detect_requires_pool_ok():
    bars = []
    for i in range(30):
        bars.append((i, 100.0, 101.0, 99.0, 100.0, 1.0))
    for j, c in enumerate([100.0, 100.0, 100.0, 100.0, 99.0, 105.0]):
        bars.append((30 + j, c, c + 1, c - 1, c, 1.0))
    assert detect_mom_turn_signal(bars, pool_ok=False) is None
    sig = detect_mom_turn_signal(bars, pool_ok=True)
    assert sig is not None
    assert sig.side == 1
    assert sig.signal == "mom_turn_5d"


def test_on_bar_stop():
    st = PositionState(entry=100.0, stop=97.0, tp1=130.0, tp2=150.0)
    close = np.linspace(100, 98, 30)
    st2, sell, reason = on_bar_exit(st, high=99, low=96.5, close=97, close_series=close)
    assert reason == "stop"
    assert sell == 1.0
    assert st2.qty_frac == 0.0
    assert st.qty_frac == 1.0  # immutable


def test_on_bar_tp1_partial():
    st = PositionState(entry=100.0, stop=90.0, tp1=130.0, tp2=150.0)
    close = np.linspace(100, 131, 40)
    st2, sell, reason = on_bar_exit(st, high=131, low=129, close=130.5, close_series=close)
    assert reason == "tp1"
    assert abs(sell - 1.0 / 3.0) < 1e-9
    assert abs(st2.qty_frac - 2.0 / 3.0) < 1e-9


def test_stop_priority_over_tp_same_bar():
    st = PositionState(entry=100.0, stop=97.0, tp1=130.0, tp2=150.0)
    close = np.linspace(100, 100, 30)
    st2, sell, reason = on_bar_exit(st, high=140, low=96, close=120, close_series=close)
    assert reason == "stop"
    assert sell == 1.0
    assert st2.qty_frac == 0.0


def test_pool_helpers(tmp_path: Path, monkeypatch):
    from quant.common import scanner_potential_pool as spp

    payload = {
        "pool_size": 2,
        "pool_ok_for_entry": True,
        "potential_pool": ["eth", "PENGU"],
        "tier_a_pool": ["eth", "PENGU"],
        "memory_30d": ["ETH", "PENGU", "WLD"],
        "entry_alerts": [{"symbol": "PENGU", "signal": "mom_turn_5d"}],
    }
    path = tmp_path / "potential_pool.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("SCANNER_POTENTIAL_POOL_PATH", str(path))

    assert spp.current_tier_a_pool() == ["ETHUSDT", "PENGUUSDT"]
    assert spp.symbol_in_tier_a_pool("eth") is True
    assert spp.symbol_in_tier_a_pool("WLD") is False
    assert spp.pool_ok_for_entry() is True
    univ = spp.load_tier_a_symbols()
    assert univ[0] == "ETHUSDT"
    assert "WLDUSDT" in univ
    assert spp.pool_entry_alert("pengu") is not None
