from __future__ import annotations

import pandas as pd

from orb.core.config import OrbConfig
from orb.core.signals import (
    classify_or_preplace_arm,
    compute_position_notional,
    compute_sl_tp,
    effective_risk_pct,
    limit_price_for_side,
    should_arm_preplace,
    sl_on_loss_side,
    worst_fill_for_preplace,
)


def _utc_day0(date_str: str = "2024-03-15") -> int:
    return int(pd.Timestamp(date_str, tz="UTC").value // 1_000_000)


def _make_df(n: int, *, step_ms: int = 300_000, start_ms: int | None = None) -> pd.DataFrame:
    start_ms = _utc_day0() if start_ms is None else start_ms
    rows = []
    for i in range(n):
        rows.append(
            {
                "open_time": start_ms + i * step_ms,
                "open": 100.0,
                "high": 100.2,
                "low": 99.8,
                "close": 100.0,
                "volume": 1000.0,
            }
        )
    return pd.DataFrame(rows)


def test_should_arm_preplace():
    assert should_arm_preplace(now_ms=1001, or_end_ms=1000) is True
    assert should_arm_preplace(now_ms=1000, or_end_ms=1000) is False


def test_limit_price_for_side():
    cfg = OrbConfig(tick_size=0.01, max_chase_ticks=30)
    assert limit_price_for_side(entry=100.0, side="LONG", cfg=cfg) == 100.3
    assert limit_price_for_side(entry=100.0, side="SHORT", cfg=cfg) == 99.7


def test_preplace_arm_right_after_or_end():
    day0 = _utc_day0()
    step = 300_000
    df = _make_df(10, step_ms=step, start_ms=day0)
    cfg = OrbConfig(
        or_minutes=15,
        session_tz="UTC",
        session_open_time="",
        session_close_time="",
        regular_session_only=False,
        sl_mode="or_range",
        exit_mode="eod",
        arm_at_or_close=True,
        vol_mult=0.0,
    )
    or_end_ms = day0 + 15 * 60_000 - 1
    sig = classify_or_preplace_arm(
        "BTCUSDT",
        df,
        asof_open_ms=or_end_ms,
        cfg=cfg,
        now_ms=or_end_ms + 5000,
    )
    assert sig.preplace_arm is not None
    bundle = sig.preplace_arm
    assert bundle.long_sig.side == "LONG"
    assert bundle.short_sig.side == "SHORT"
    assert sl_on_loss_side(
        side="LONG", entry=bundle.long_sig.price, sl=float(bundle.long_sig.sl_price)
    )
    assert sl_on_loss_side(
        side="SHORT", entry=bundle.short_sig.price, sl=float(bundle.short_sig.sl_price)
    )


def test_preplace_waits_until_or_end():
    day0 = _utc_day0()
    step = 300_000
    df = _make_df(10, step_ms=step, start_ms=day0)
    cfg = OrbConfig(
        or_minutes=15,
        session_tz="UTC",
        session_open_time="",
        session_close_time="",
        regular_session_only=False,
        sl_mode="or_range",
        exit_mode="eod",
        arm_at_or_close=True,
    )
    or_end_ms = day0 + 15 * 60_000 - 1
    sig = classify_or_preplace_arm(
        "BTCUSDT",
        df,
        asof_open_ms=or_end_ms,
        cfg=cfg,
        now_ms=or_end_ms,
    )
    assert sig.preplace_arm is None
    assert "or_window_in_progress" in sig.reasons

def test_preplace_conservative_sizing_uses_limit_cap():
    cfg = OrbConfig(
        tick_size=0.01,
        max_chase_ticks=30,
        risk_pct=0.01,
        preplace_risk_scale=1.0,
        sl_mode="or_range",
        exit_mode="eod",
        symbol_bot_equity_usdt=1000.0,
    )
    stop = 100.0
    worst = worst_fill_for_preplace(stop_entry=stop, side="LONG", cfg=cfg)
    assert worst == 100.3
    sl_stop, _, _ = compute_sl_tp(
        side="LONG", entry=stop, or_high=99.8, or_low=99.0, cfg=cfg
    )
    notion_ideal = compute_position_notional(
        entry=stop, sl=float(sl_stop), cfg=cfg, bot_equity_usdt=1000.0, for_preplace=False
    )
    notion_worst = compute_position_notional(
        entry=worst, sl=float(sl_stop), cfg=cfg, bot_equity_usdt=1000.0, for_preplace=True
    )
    assert notion_worst < notion_ideal


def test_preplace_sl_on_loss_side_at_stop():
    cfg = OrbConfig(
        tick_size=0.01,
        max_chase_ticks=30,
        sl_mode="atr_pct",
        exit_mode="eod",
        atr_sl_fraction=0.05,
        symbol_bot_equity_usdt=1000.0,
    )
    stop = 19.73
    daily_atr = 2.0
    sl, _, _ = compute_sl_tp(
        side="SHORT",
        entry=stop,
        or_high=20.5,
        or_low=19.5,
        cfg=cfg,
        daily_atr=daily_atr,
    )
    assert sl is not None
    assert sl_on_loss_side(side="SHORT", entry=stop, sl=float(sl))


def test_effective_risk_pct_preplace_scale():
    cfg = OrbConfig(risk_pct=0.01, preplace_risk_pct=0.0, preplace_risk_scale=1.0)
    assert effective_risk_pct(cfg, for_preplace=False) == 0.01
    assert effective_risk_pct(cfg, for_preplace=True) == 0.01
    cfg_scaled = OrbConfig(risk_pct=0.01, preplace_risk_pct=0.0, preplace_risk_scale=0.85)
    assert effective_risk_pct(cfg_scaled, for_preplace=True) == 0.0085
    cfg2 = OrbConfig(risk_pct=0.01, preplace_risk_pct=0.007)
    assert effective_risk_pct(cfg2, for_preplace=True) == 0.007


def test_preplace_can_arm_on_later_scan_same_session():
    day0 = _utc_day0()
    step = 300_000
    df = _make_df(10, step_ms=step, start_ms=day0)
    cfg = OrbConfig(
        or_minutes=15,
        session_tz="UTC",
        session_open_time="",
        session_close_time="",
        regular_session_only=False,
        sl_mode="or_range",
        exit_mode="eod",
        arm_at_or_close=True,
        vol_mult=0.0,
    )
    or_end_ms = day0 + 15 * 60_000 - 1
    sig = classify_or_preplace_arm(
        "BTCUSDT",
        df,
        asof_open_ms=or_end_ms,
        cfg=cfg,
        now_ms=or_end_ms + step,
    )
    assert sig.preplace_arm is not None


def test_preplace_or5_allows_single_bar_session():
    day0 = _utc_day0()
    step = 300_000
    df = _make_df(10, step_ms=step, start_ms=day0)
    cfg = OrbConfig(
        or_minutes=5,
        session_tz="UTC",
        session_open_time="",
        session_close_time="",
        regular_session_only=False,
        sl_mode="or_range",
        exit_mode="eod",
        arm_at_or_close=True,
        vol_mult=0.0,
    )
    or_end_ms = day0 + 5 * 60_000 - 1
    sig = classify_or_preplace_arm(
        "BTCUSDT",
        df.iloc[:1],
        asof_open_ms=or_end_ms,
        cfg=cfg,
        now_ms=or_end_ms + 5000,
    )
    assert sig.preplace_arm is not None
