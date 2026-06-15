"""Live 人工替换包路径测试。"""

from __future__ import annotations

from orb.ml.live_bundle import live_bundle_root, resolve_live_gate_path
from orb.ml.model import layout_status, resolve_gbm_path


def test_live_bundle_root():
    assert str(live_bundle_root()).replace("\\", "/").endswith("orb_live")


def test_resolve_prefers_live_bundle():
    assert "orb_live" in str(resolve_gbm_path()).replace("\\", "/")
    assert str(resolve_live_gate_path()).endswith(".json")


def test_layout_includes_live_bundle():
    st = layout_status()
    assert "live_bundle_root" in st
    assert "orb_live" in st["live_bundle_root"].replace("\\", "/")


def test_live_bundle_hint_ready():
    from orb.ml.live_bundle import live_bundle_hint

    hint = live_bundle_hint()
    assert hint["ok"] is True
    assert "message" in hint
    assert hint["severity"] in ("ok", "warn", "block")
    assert "orb_live" in hint.get("root", "").replace("\\", "/")
    assert "active_gbm" in hint
    assert isinstance(hint.get("artifacts"), list)
    assert len(hint["artifacts"]) >= 3


def test_sync_live_bundle_from_ml_models(tmp_path, monkeypatch):
    from orb.ml.live_bundle import live_bundle_root, sync_live_bundle_from_ml_models
    import orb.ml.model.paths as mp

    live_dir = tmp_path / "orb_live"
    models_dir = tmp_path / "models"
    live_dir.mkdir()
    models_dir.mkdir()
    monkeypatch.setattr("orb.ml.live_bundle.live_bundle_root", lambda: live_dir)
    monkeypatch.setattr(mp, "GBM_PKL", models_dir / "breakout_gbm.pkl")
    monkeypatch.setattr(mp, "GBM_META", models_dir / "breakout_gbm.json")
    monkeypatch.setattr(mp, "PROFILES_JSON", models_dir / "symbol_breakout_profiles.json")
    monkeypatch.setattr(mp, "GBM_TRAIN_REPORT", models_dir / "breakout_gbm_train_report.json")
    (models_dir / "breakout_gbm.pkl").write_bytes(b"pkl")
    (models_dir / "breakout_gbm.json").write_text("{}", encoding="utf-8")
    (models_dir / "symbol_breakout_profiles.json").write_text("{}", encoding="utf-8")

    copied = sync_live_bundle_from_ml_models(overwrite=True)
    assert copied
    assert (live_dir / "breakout_gbm.pkl").is_file()
    assert (live_dir / "symbol_breakout_profiles.json").is_file()
