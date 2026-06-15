"""生产环境变量：忽略指向 data/ Volume 的路径覆盖。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from orb.ml.live_bundle import live_bundle_root, resolve_live_gate_path, resolve_live_gbm_path
from orb.ml.model.paths import resolve_symbols_path
from orb.ml.paths import is_risky_production_data_path, production_env_warnings


@pytest.mark.parametrize(
    "raw, risky",
    [
        ("data/orb/ml/symbols/universe.txt", True),
        ("data/orb/live", True),
        ("orb_live", False),
        ("config/orb/v2/symbols.txt", False),
        ("/app/orb_live/breakout_gbm.pkl", False),
    ],
)
def test_is_risky_production_data_path(raw: str, risky: bool):
    assert is_risky_production_data_path(raw) is risky


def test_production_env_warnings_lists_risky_vars(monkeypatch):
    monkeypatch.setenv("ORB_V2_SYMBOLS_FILE", "data/orb/ml/symbols/universe.txt")
    monkeypatch.setenv("ORB_V2_GBM_PATH", "data/orb/live/breakout_gbm.pkl")
    warnings = production_env_warnings()
    assert any("ORB_V2_SYMBOLS_FILE" in w for w in warnings)
    assert any("ORB_V2_GBM_PATH" in w for w in warnings)


def test_resolve_symbols_ignores_data_env(monkeypatch):
    monkeypatch.setenv("ORB_V2_SYMBOLS_FILE", "data/orb/ml/symbols/universe.txt")
    path = resolve_symbols_path()
    assert str(path).replace("\\", "/").endswith("config/orb/v2/symbols.txt")
    assert path.is_file()


def test_live_bundle_root_ignores_data_env(monkeypatch):
    monkeypatch.setenv("ORB_LIVE_BUNDLE_ROOT", "data/orb/live")
    root = live_bundle_root()
    assert str(root).replace("\\", "/").endswith("orb_live")


def test_resolve_live_paths_ignore_data_env(monkeypatch):
    monkeypatch.setenv("ORB_V2_GATE_CONFIG", "data/orb/live/live_gate.json")
    monkeypatch.setenv("ORB_V2_GBM_PATH", "data/orb/live/breakout_gbm.pkl")
    gate = resolve_live_gate_path()
    gbm = resolve_live_gbm_path()
    assert "orb_live" in str(gate).replace("\\", "/")
    assert "orb_live" in str(gbm).replace("\\", "/")
    assert gate.is_file()
    assert gbm.is_file()


def test_load_production_uses_orb_live_with_data_env(monkeypatch):
    from orb.ml.model.bundle import BreakoutModelBundle

    monkeypatch.setenv("ORB_V2_GBM_PATH", "data/orb/live/breakout_gbm.pkl")
    monkeypatch.setenv("ORB_V2_PROFILES_PATH", "data/orb/live/symbol_breakout_profiles.json")
    bundle = BreakoutModelBundle.load_production()
    assert "orb_live" in str(bundle.gbm_path).replace("\\", "/")
    assert bundle.gbm_path.is_file()
