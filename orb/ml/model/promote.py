"""staging → production 原子发布。"""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict

from orb.ml.model.paths import (
    ARCHIVE_DIR,
    GBM_META,
    GBM_PKL,
    GBM_TRAIN_REPORT,
    PROFILES_JSON,
    SAMPLES_JSON,
    STAGING_MODELS_DIR,
    STAGING_SAMPLES_DIR,
    ensure_model_dirs,
    staging_gbm_meta_path,
    staging_gbm_pkl_path,
    staging_profiles_path,
    staging_samples_path,
    staging_train_report_path,
)
from orb.ml.live_bundle import live_bundle_root, sync_live_bundle_from_ml_models


def _atomic_copy(src: Path, dst: Path) -> None:
    if not src.is_file():
        raise FileNotFoundError(str(src))
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(dst.suffix + ".tmp")
    shutil.copy2(src, tmp)
    tmp.replace(dst)


def archive_production(tag: str) -> Path:
    ensure_model_dirs()
    stamp = tag or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = ARCHIVE_DIR / f"pre_promote_{stamp}"
    dest.mkdir(parents=True, exist_ok=True)
    for src in (GBM_PKL, GBM_META, GBM_TRAIN_REPORT, PROFILES_JSON, SAMPLES_JSON):
        if src.is_file():
            shutil.copy2(src, dest / src.name)
    live_root = live_bundle_root()
    if live_root.is_dir():
        live_dest = dest / "orb_live"
        live_dest.mkdir(parents=True, exist_ok=True)
        for name in ("live_gate.json", "breakout_gbm.pkl", "breakout_gbm.json", "symbol_breakout_profiles.json"):
            src = live_root / name
            if src.is_file():
                shutil.copy2(src, live_dest / name)
    return dest


def promote_staging_to_production(*, tag: str = "") -> Dict[str, str]:
    """验收通过后，将 staging 覆盖到 production。"""
    ensure_model_dirs()
    pairs = [
        (staging_gbm_pkl_path(), GBM_PKL),
        (staging_gbm_meta_path(), GBM_META),
        (staging_train_report_path(), GBM_TRAIN_REPORT),
        (staging_profiles_path(), PROFILES_JSON),
        (staging_samples_path(), SAMPLES_JSON),
    ]
    for src, _dst in pairs:
        if not src.is_file():
            raise FileNotFoundError(f"staging artifact missing: {src}")

    archive_production(tag)
    promoted: Dict[str, str] = {}
    for src, dst in pairs:
        _atomic_copy(src, dst)
        promoted[src.name] = str(dst)
    live_synced = sync_live_bundle_from_ml_models(overwrite=True)
    if live_synced:
        promoted["orb_live_sync"] = ",".join(live_synced)
    return promoted


def clear_staging() -> None:
    for d in (STAGING_MODELS_DIR, STAGING_SAMPLES_DIR):
        if d.is_dir():
            shutil.rmtree(d, ignore_errors=True)


def ensure_staging_dirs() -> None:
    STAGING_MODELS_DIR.mkdir(parents=True, exist_ok=True)
    STAGING_SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
