"""King Keltner vnpy 官方 BacktestingEngine 封装（复用 orb.cta.vnpy）。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from orb.core.config import OrbConfig
from orb.cta.vnpy.backtest import (
    CtaVnpyBacktestConfig,
    bar_symbol_from_vt,
    filter_rth_bars,
    klines_df_to_bars,
    pricetick_for,
    run_vnpy_cta_backtest,
    save_bars,
    session_bounds_for_date,
    trades_to_rows,
)
from orb.kk.config import KKConfig
from orb.kk.vnpy.strategies.king_keltner_kk import KingKeltnerKkStrategy
from orb.kk.vnpy.strategies.king_keltner_kk_backtest import KingKeltnerKkBacktestStrategy
from vnpy.trader.object import BarData  # noqa: E402

_prepare_bars = __import__("orb.cta.vnpy.backtest", fromlist=["_prepare_bars"])._prepare_bars
_bar_ms = __import__("orb.cta.vnpy.backtest", fromlist=["_bar_ms"])._bar_ms


def backtest_engine_params(kk: KKConfig, *, price: float, equity_usdt: float) -> Dict[str, Any]:
    bt = CtaVnpyBacktestConfig(
        equity_usdt=equity_usdt,
        risk_pct=kk.risk_pct,
        compound=kk.compound,
        fee_taker_bps=kk.fee_taker_bps,
        slip_bps_entry=kk.slip_bps_entry,
        max_notional_usdt=kk.max_notional_usdt,
    )
    return __import__("orb.cta.vnpy.backtest", fromlist=["backtest_engine_params"]).backtest_engine_params(
        bt, price=price
    )


def strategy_setting(kk: KKConfig, symbol: str, *, price: float, equity_usdt: float) -> dict:
    bt = CtaVnpyBacktestConfig(
        equity_usdt=equity_usdt,
        risk_pct=kk.risk_pct,
        compound=kk.compound,
        fee_taker_bps=kk.fee_taker_bps,
        slip_bps_entry=kk.slip_bps_entry,
        max_notional_usdt=kk.max_notional_usdt,
    )
    return __import__("orb.cta.vnpy.backtest", fromlist=["build_strategy_setting"]).build_strategy_setting(
        "king_keltner",
        symbol,
        bt_cfg=bt,
        price=price,
        orb_cfg=kk.orb_session_cfg(),
    )


def backtest_strategy_class(kk: KKConfig):
    return KingKeltnerKkBacktestStrategy if kk.compound else KingKeltnerKkStrategy


def run_kk_vnpy_backtest(
    symbol: str,
    bars: List[BarData],
    *,
    kk: KKConfig,
    equity_usdt: float,
    start: datetime,
    end: datetime,
    price: float = 100.0,
    db_path: Optional[Path] = None,
    quiet: bool = False,
    replay_start: Optional[datetime] = None,
    replay_end: Optional[datetime] = None,
    orb_cfg: Optional[OrbConfig] = None,
) -> Dict[str, Any]:
    bt = CtaVnpyBacktestConfig(
        equity_usdt=equity_usdt,
        risk_pct=kk.risk_pct,
        compound=kk.compound,
        fee_taker_bps=kk.fee_taker_bps,
        slip_bps_entry=kk.slip_bps_entry,
        max_notional_usdt=kk.max_notional_usdt,
    )
    out = run_vnpy_cta_backtest(
        symbol,
        bars,
        strategy_key="king_keltner",
        bt_cfg=bt,
        start=start,
        end=end,
        price=price,
        db_path=db_path,
        quiet=quiet,
        replay_start=replay_start,
        replay_end=replay_end,
        orb_cfg=orb_cfg or kk.orb_session_cfg(),
    )
    if "end_wallet" not in out and out.get("statistics"):
        out["end_wallet"] = float(out["statistics"].get("end_balance") or equity_usdt)
    return out


def roundtrip_pnl_usdt(trades, *, equity_usdt: float, risk_pct: float = 0.01) -> float:
    """由成交序列估算净 PnL（USDT 名义，与纸面引擎口径近似）。"""
    if not trades:
        return 0.0
    pos = 0.0
    entry_px = 0.0
    net = 0.0
    trail = 0.008
    risk_frac = float(risk_pct or 0.01)
    for t in sorted(trades, key=lambda x: x.datetime):
        vol = float(t.volume)
        px = float(t.price)
        d = t.direction.value
        off = t.offset.value
        is_open = off.upper() == "OPEN" or (pos == 0)
        if is_open and pos == 0:
            pos = vol if d == "LONG" else -vol
            entry_px = px
            continue
        if pos == 0:
            continue
        side = 1 if pos > 0 else -1
        notion = equity_usdt * risk_frac / trail
        if side == 1:
            net += (px - entry_px) / max(1e-9, entry_px) * notion
        else:
            net += (entry_px - px) / max(1e-9, entry_px) * notion
        pos = 0.0
        entry_px = 0.0
    return net


__all__ = [
    "bar_symbol_from_vt",
    "backtest_engine_params",
    "backtest_strategy_class",
    "filter_rth_bars",
    "klines_df_to_bars",
    "pricetick_for",
    "run_kk_vnpy_backtest",
    "roundtrip_pnl_usdt",
    "save_bars",
    "session_bounds_for_date",
    "strategy_setting",
    "trades_to_rows",
    "_bar_ms",
    "_prepare_bars",
]
