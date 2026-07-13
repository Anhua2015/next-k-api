"""1D execute + 1W confirm + 1H bonus (aligned with breakoutscanner)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional, Sequence

from quant.breakout_donchian.bars import BarRow, drop_incomplete_bars, resample_weekly_from_daily
from quant.breakout_donchian.core import BreakoutDirection, DonchianSignal, detect_donchian_signal

if TYPE_CHECKING:
    from quant.breakout_donchian.config import BreakoutDonchianConfig


@dataclass(frozen=True)
class ResonanceResult:
    weekly_ok: bool
    hourly_bonus: bool
    tier: str
    risk_mult: float


def _direction_filter(cfg: "BreakoutDonchianConfig") -> Optional[BreakoutDirection]:
    return "bullish" if cfg.long_only else None


def _mode(cfg: "BreakoutDonchianConfig") -> str:
    return "strict" if str(cfg.breakout_mode).lower() == "strict" else "standard"


def _detect_kwargs(cfg: "BreakoutDonchianConfig") -> dict:
    return {
        "mode": _mode(cfg),
        "strict_vol_mult": float(cfg.strict_vol_mult),
        "atr_period": int(cfg.atr_period),
        "direction_filter": _direction_filter(cfg),
        "tp1_rr": float(cfg.tp1_rr),
        "tp2_rr": float(cfg.tp2_rr),
        "tp3_rr": float(cfg.tp3_rr),
    }


def weekly_bars_from_daily(daily_bars: Sequence[BarRow]) -> list[BarRow]:
    return resample_weekly_from_daily(daily_bars)


def detect_weekly_confirm(
    bars: Sequence[BarRow],
    cfg: "BreakoutDonchianConfig",
) -> Optional[DonchianSignal]:
    clean = drop_incomplete_bars(list(bars), "1w")
    return detect_donchian_signal(
        clean,
        lookback=int(cfg.weekly_lookback),
        vol_lookback=int(cfg.weekly_vol_lookback),
        vol_mult=float(cfg.weekly_vol_mult),
        strong_close_pct=float(cfg.weekly_strong_close_pct),
        strict_atr_mult=float(cfg.strict_atr_mult),
        **_detect_kwargs(cfg),
    )


def detect_hourly_bonus(
    bars: Sequence[BarRow],
    cfg: "BreakoutDonchianConfig",
) -> bool:
    clean = drop_incomplete_bars(list(bars), "1h")
    sig = detect_donchian_signal(
        clean,
        lookback=int(cfg.hourly_lookback),
        vol_lookback=int(cfg.hourly_vol_lookback),
        vol_mult=float(cfg.hourly_vol_mult),
        strong_close_pct=float(cfg.hourly_strong_close_pct),
        strict_atr_mult=float(cfg.hourly_strict_atr_mult),
        **_detect_kwargs(cfg),
    )
    return sig is not None


def evaluate_resonance(
    cfg: "BreakoutDonchianConfig",
    *,
    weekly_bars: Sequence[BarRow],
    hourly_bars: Sequence[BarRow] | None = None,
) -> ResonanceResult:
    weekly_sig = detect_weekly_confirm(weekly_bars, cfg)
    weekly_ok = weekly_sig is not None
    hourly_bonus = bool(cfg.check_1h_bonus and hourly_bars and detect_hourly_bonus(hourly_bars, cfg))

    if weekly_ok and hourly_bonus:
        return ResonanceResult(True, True, "triple", float(cfg.risk_mult_triple))
    if weekly_ok:
        return ResonanceResult(True, False, "dual", float(cfg.risk_mult_base))
    return ResonanceResult(False, False, "none", 0.0)


def preload_days_for_interval(interval: str, *, min_bars: int) -> int:
    key = interval.strip().lower()
    if key == "1h":
        return max(14, (min_bars // 24) + 7)
    return max(120, min_bars + 30)
