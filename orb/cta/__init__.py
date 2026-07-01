"""VeighNa 示例 CTA 策略移植（独立回测研究）。"""

from orb.cta.engine import CtaBacktestConfig, run_cta_backtest
from orb.cta.registry import CTA_STRATEGIES, cta_config_for_strategy, list_strategies

__all__ = [
    "CtaBacktestConfig",
    "run_cta_backtest",
    "CTA_STRATEGIES",
    "cta_config_for_strategy",
    "list_strategies",
]