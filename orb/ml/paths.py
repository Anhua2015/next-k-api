"""ORB ML 路径：运行时配置/模型在 data/ 与 config/；output/ 仅本地回测报告写入。"""

from __future__ import annotations

from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = PKG_ROOT.parent

CONFIG_V2 = PROJECT_ROOT / "config" / "orb" / "v2"

# 回测 / 调参报告（只写不读作生产配置）
V2_OUTPUT = PROJECT_ROOT / "output" / "orb" / "v2"
V2_EVAL = V2_OUTPUT / "eval"
V2_LIVE_GATE_EVAL = V2_EVAL / "live_gate_eval.json"
V2_LIVE_GATE_LAST30D = V2_EVAL / "live_gate_last30d.json"
V2_LIVE_GATE_SWEEP = V2_EVAL / "live_gate_sweep.json"


def _first_existing(*paths: Path) -> Path:
    for p in paths:
        if p.is_file():
            return p
    return paths[0]


def ensure_v2_eval_dirs() -> None:
    V2_EVAL.mkdir(parents=True, exist_ok=True)


def ensure_v2_dirs() -> None:
    """兼容旧工具名。"""
    ensure_v2_eval_dirs()


def default_shared_samples_path() -> Path:
    from orb.ml.model.paths import resolve_samples_path

    return resolve_samples_path()


def default_shared_true_model_path() -> Path:
    from orb.ml.model.paths import resolve_logistic_true_path

    return resolve_logistic_true_path()


def default_shared_fake_model_path() -> Path:
    from orb.ml.model.paths import LOGISTIC_FAKE_JSON

    return LOGISTIC_FAKE_JSON


def default_gbm_path() -> Path:
    from orb.ml.model.paths import resolve_gbm_path

    return resolve_gbm_path()


def default_profiles_path() -> Path:
    from orb.ml.model.paths import resolve_profiles_path

    return resolve_profiles_path()


def default_gbm_train_report_path() -> Path:
    from orb.ml.model.paths import resolve_train_report_path

    return resolve_train_report_path()


def default_live_gate_eval_path() -> Path:
    return V2_LIVE_GATE_EVAL


def default_live_gate_last30d_path() -> Path:
    return V2_LIVE_GATE_LAST30D


# 训练脚本默认写入 data/orb/ml（非 output）
def default_gbm_write_paths() -> tuple[Path, Path, Path]:
    from orb.ml.model.paths import GBM_META, GBM_PKL, GBM_TRAIN_REPORT

    return GBM_PKL, GBM_META, GBM_TRAIN_REPORT
