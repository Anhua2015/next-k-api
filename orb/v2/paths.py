"""ORB 2.0 路径：实盘参数 orb_live/ + 训练产物 data/orb/ml/。"""

from __future__ import annotations

from pathlib import Path

from orb.ml.live_bundle import live_gate_json, resolve_live_gate_path
from orb.ml.paths import CONFIG_V2, PROJECT_ROOT, V2_EVAL, V2_OUTPUT
from orb.ml.model.manifest import archive_snapshot, write_manifest
from orb.ml.model.paths import (
    ARCHIVE_DIR,
    GBM_META,
    GBM_PKL,
    GBM_TRAIN_REPORT,
    MANIFEST_JSON,
    PROFILES_JSON,
    SAMPLES_JSON,
    ensure_model_dirs,
    resolve_gbm_path,
    resolve_profiles_path,
    resolve_symbols_path,
)

OUTPUT_DIR = V2_OUTPUT
GATE_CONFIG = live_gate_json()
SYMBOLS_FILE = resolve_symbols_path()


def ensure_dirs() -> None:
    ensure_model_dirs()
    V2_OUTPUT.mkdir(parents=True, exist_ok=True)
    V2_EVAL.mkdir(parents=True, exist_ok=True)


def resolve_gate_config_path() -> Path:
    return resolve_live_gate_path()


__all__ = [
    "ARCHIVE_DIR",
    "GBM_META",
    "GBM_PKL",
    "GBM_TRAIN_REPORT",
    "MANIFEST_JSON",
    "OUTPUT_DIR",
    "PROFILES_JSON",
    "SAMPLES_JSON",
    "archive_snapshot",
    "ensure_dirs",
    "resolve_gbm_path",
    "resolve_gate_config_path",
    "resolve_profiles_path",
    "resolve_symbols_path",
    "write_manifest",
]
