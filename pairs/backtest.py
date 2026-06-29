"""Pairs backtest: Kalman spread signals on aligned leg prices."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

from pairs.kalman import kalman_hedge_ratio, kalman_zscore
from pairs.sizing import p_trace_entry_confident, p_trace_size_scale


@dataclass
class PairsBacktestConfig:
    leg1: str
    leg2: str
    interval: str = "1h"
    delta: float = 1e-4
    r_noise: float = 1.0
    entry_z: float = 1.0
    exit_z: float = 0.0
    cost_bps: float = 10.0
    vol_lookback: int = 63
    max_p_trace: Optional[float] = None
    halt_p_trace_pct: Optional[float] = 95.0
    p_trace_sizing: bool = False
    p_trace_lookback: int = 252
    p_trace_min_scale: float = 0.25
    slippage_bps: float = 0.0
    funding_bps_per_8h: float = 0.0
    stop_z_extra: Optional[float] = None
    max_hold_bars: Optional[int] = None
    initial_capital_usdt: float = 10_000.0
    deploy_pct: float = 0.5
    leverage: float = 1.0


def align_leg_closes(
    df1: pd.DataFrame,
    df2: pd.DataFrame,
) -> pd.DataFrame:
    """Inner-join two OHLCV frames on open_time; return leg1/leg2 close columns."""
    a = df1.drop_duplicates("open_time").sort_values("open_time")
    b = df2.drop_duplicates("open_time").sort_values("open_time")
    m = pd.merge(
        a[["open_time", "close"]].rename(columns={"close": "leg1"}),
        b[["open_time", "close"]].rename(columns={"close": "leg2"}),
        on="open_time",
        how="inner",
    )
    return m.reset_index(drop=True)


def signals_from_zscore(
    zscore: pd.Series,
    *,
    entry_z: float,
    exit_z: float,
    confident: Optional[pd.Series] = None,
    stop_z_extra: Optional[float] = None,
    max_hold_bars: Optional[int] = None,
) -> pd.Series:
    """+1 long spread, -1 short spread, 0 flat. Long spread = long leg1 / short leg2."""
    pos = np.zeros(len(zscore), dtype=float)
    current = 0.0
    hold_bars = 0
    stop_level = float(stop_z_extra or 0.0)

    for i in range(1, len(zscore)):
        z = float(zscore.iloc[i])
        ok = bool(confident.iloc[i]) if confident is not None else True

        if current != 0.0:
            hold_bars += 1
            if stop_level > 0.0:
                if current == 1.0 and z < -(entry_z + stop_level):
                    current = 0.0
                    hold_bars = 0
                elif current == -1.0 and z > entry_z + stop_level:
                    current = 0.0
                    hold_bars = 0
            if max_hold_bars and hold_bars >= int(max_hold_bars):
                current = 0.0
                hold_bars = 0

        if current == 0.0:
            hold_bars = 0
            if ok:
                if z > entry_z:
                    current = -1.0
                elif z < -entry_z:
                    current = 1.0
        elif current == -1.0 and z < exit_z:
            current = 0.0
            hold_bars = 0
        elif current == 1.0 and z > -exit_z:
            current = 0.0
            hold_bars = 0

        pos[i] = current
    return pd.Series(pos, index=zscore.index, name="position")


def _leg_notional_usdt(equity: float, cfg: PairsBacktestConfig, *, scale: float = 1.0) -> float:
    """USDT notional on leg1 at entry (leg2 sized by beta)."""
    lev = max(float(cfg.leverage), 0.0)
    sc = max(0.0, min(1.0, float(scale)))
    return max(0.0, float(equity) * float(cfg.deploy_pct) * lev * sc)


def _tx_fee_usdt(
    leg1_notional: float,
    leg2_notional: float,
    cfg: PairsBacktestConfig,
    *,
    include_slippage: bool = False,
) -> float:
    """Fee + optional slippage on both legs (bps per side)."""
    bps = float(cfg.cost_bps)
    if include_slippage:
        bps += float(cfg.slippage_bps)
    return (leg1_notional + leg2_notional) * (bps / 10_000.0)


def _funding_per_bar(both_notional: float, cfg: PairsBacktestConfig) -> float:
    """Funding drag per bar (funding_bps_per_8h quoted per 8h window)."""
    if cfg.funding_bps_per_8h <= 0:
        return 0.0
    hours_per_bar = 1.0 if cfg.interval == "1h" else 24.0
    return both_notional * (float(cfg.funding_bps_per_8h) / 10_000.0) * (hours_per_bar / 8.0)


def wallet_pnl_usdt(
    p1: pd.Series,
    p2: pd.Series,
    beta: pd.Series,
    position: pd.Series,
    cfg: PairsBacktestConfig,
    *,
    size_scale: Optional[pd.Series] = None,
) -> Dict[str, Any]:
    """Bar-by-bar USDT PnL: beta-sized leg2, fees, funding, optional P_trace scale at entry."""
    n = len(p1)
    equity = float(cfg.initial_capital_usdt)
    q1 = q2 = 0.0
    fees_total = 0.0
    funding_total = 0.0
    equity_curve = [equity]
    bar_net: list[float] = [0.0]
    trade_entry_equity: Optional[float] = None
    round_trip_pnls: list[float] = []
    entry_scale = 1.0

    for t in range(1, n):
        pos_held = float(position.iloc[t - 1])
        pos_end = float(position.iloc[t])
        gross = 0.0
        fee = 0.0
        funding = 0.0

        if pos_held != 0.0 and q1 != 0.0:
            p1t = float(p1.iloc[t])
            p2t = float(p2.iloc[t])
            gross = pos_held * (
                q1 * (p1t - float(p1.iloc[t - 1]))
                - q2 * (p2t - float(p2.iloc[t - 1]))
            )
            both_notional = abs(q1) * p1t + abs(q2) * p2t
            funding = _funding_per_bar(both_notional, cfg)

        equity += gross - funding
        funding_total += funding

        if pos_end != pos_held:
            if pos_held != 0.0:
                p1t = float(p1.iloc[t])
                p2t = float(p2.iloc[t])
                n1 = abs(q1) * p1t
                n2 = abs(q2) * p2t
                fee_close = _tx_fee_usdt(n1, n2, cfg, include_slippage=True)
                fee += fee_close
                equity -= fee_close
                if trade_entry_equity is not None:
                    round_trip_pnls.append(equity - trade_entry_equity)
                trade_entry_equity = None
                q1 = q2 = 0.0

            if pos_end != 0.0:
                sc = float(size_scale.iloc[t]) if size_scale is not None else 1.0
                entry_scale = max(0.0, min(1.0, sc))
                notional = _leg_notional_usdt(equity, cfg, scale=entry_scale)
                p1t = float(p1.iloc[t])
                p2t = float(p2.iloc[t])
                b = max(float(beta.iloc[t]), 1e-8)
                q1 = notional / p1t
                q2 = b * notional / p1t
                fee_open = _tx_fee_usdt(notional, b * p2t / p1t * notional, cfg, include_slippage=True)
                fee += fee_open
                equity -= fee_open
                trade_entry_equity = equity

        fees_total += fee
        bar_net.append(gross - fee - funding)
        equity_curve.append(equity)

    if trade_entry_equity is not None:
        round_trip_pnls.append(equity - trade_entry_equity)

    eq = pd.Series(equity_curve, dtype=float)
    ret_pct = eq.pct_change().fillna(0.0)
    peak = eq.cummax()
    dd_usdt = eq - peak
    max_dd_usdt = float(dd_usdt.min()) if len(dd_usdt) else 0.0
    max_dd_pct = float((dd_usdt / peak.replace(0, np.nan)).min() * 100) if len(dd_usdt) and peak.max() > 0 else 0.0
    std = float(ret_pct.std())
    bars_per_year = 365 * 24 if cfg.interval == "1h" else 252
    sharpe = float(ret_pct.mean() / std * np.sqrt(bars_per_year)) if std > 0 else 0.0
    total_pnl = float(equity - cfg.initial_capital_usdt)
    total_ret_pct = float(total_pnl / cfg.initial_capital_usdt * 100) if cfg.initial_capital_usdt > 0 else 0.0
    held = position.shift(1).fillna(0).abs() > 0
    active = pd.Series(bar_net)[held.values]
    bar_wr = float((active > 0).mean() * 100) if len(active) else 0.0
    rt = len(round_trip_pnls)
    trade_wr = float(sum(1 for x in round_trip_pnls if x > 0) / rt * 100) if rt else 0.0
    avg_notional = float(
        np.mean([_leg_notional_usdt(e, cfg) for e in eq[held.values]])
    ) if held.any() else 0.0

    return {
        "initial_capital_usdt": cfg.initial_capital_usdt,
        "final_equity_usdt": round(equity, 2),
        "total_pnl_usdt": round(total_pnl, 2),
        "total_return_pct": round(total_ret_pct, 2),
        "max_drawdown_usdt": round(max_dd_usdt, 2),
        "max_drawdown_pct": round(max_dd_pct, 2),
        "sharpe": round(sharpe, 3),
        "total_fees_usdt": round(fees_total, 2),
        "total_funding_usdt": round(funding_total, 2),
        "round_trips": rt,
        "win_rate_trades_pct": round(trade_wr, 1),
        "win_rate_bars_pct": round(bar_wr, 1),
        "avg_leg1_notional_usdt": round(avg_notional, 2),
        "deploy_pct": cfg.deploy_pct,
        "equity_usdt": [round(x, 2) for x in equity_curve],
    }


def run_pairs_backtest(
    prices: pd.DataFrame,
    cfg: PairsBacktestConfig,
) -> Dict[str, Any]:
    """Run Kalman pairs backtest on aligned leg1/leg2 close series."""
    if prices.empty or len(prices) < 30:
        return {"error": "insufficient_data", "bars": len(prices)}

    p1 = prices["leg1"].astype(float)
    p2 = prices["leg2"].astype(float)
    beta, intercept, spread, e, s, p_trace = kalman_hedge_ratio(
        p1, p2, delta=cfg.delta, r_noise=cfg.r_noise
    )
    zscore = kalman_zscore(e, s)

    confident = None
    if cfg.max_p_trace is not None:
        confident = p_trace < float(cfg.max_p_trace)
    elif cfg.halt_p_trace_pct is not None and len(p_trace) >= 20:
        confident = p_trace_entry_confident(
            p_trace,
            lookback=cfg.p_trace_lookback,
            halt_pct=float(cfg.halt_p_trace_pct),
        )

    size_scale = None
    if cfg.p_trace_sizing:
        size_scale = p_trace_size_scale(
            p_trace,
            lookback=cfg.p_trace_lookback,
            min_scale=cfg.p_trace_min_scale,
        )

    position = signals_from_zscore(
        zscore,
        entry_z=cfg.entry_z,
        exit_z=cfg.exit_z,
        confident=confident,
        stop_z_extra=cfg.stop_z_extra,
        max_hold_bars=cfg.max_hold_bars,
    )

    spread_vol = spread.rolling(cfg.vol_lookback, min_periods=max(10, cfg.vol_lookback // 3)).std().bfill()
    spread_vol = spread_vol.replace(0, np.nan).bfill().fillna(1.0)
    spread_chg = spread.diff().fillna(0)
    gross = position.shift(1).fillna(0) * (spread_chg / spread_vol)
    turnover = position.diff().abs().fillna(0)
    cost = turnover * (cfg.cost_bps / 10_000.0)
    net_poc = gross - cost

    trades = int((turnover > 0).sum())
    wallet = wallet_pnl_usdt(p1, p2, beta, position, cfg, size_scale=size_scale)

    active_mask = position.shift(1).fillna(0).abs() > 0
    active_poc = net_poc[active_mask]
    poc_wr = float((active_poc > 0).mean() * 100) if len(active_poc) else 0.0

    std_poc = float(net_poc.std())
    bars_per_year = 365 * 24 if cfg.interval == "1h" else 252
    poc_sharpe = float(net_poc.mean() / std_poc * np.sqrt(bars_per_year)) if std_poc > 0 else 0.0
    poc_equity = net_poc.cumsum()
    poc_dd = float((poc_equity - poc_equity.cummax()).min()) if len(poc_equity) else 0.0

    return {
        "leg1": cfg.leg1,
        "leg2": cfg.leg2,
        "interval": cfg.interval,
        "bars": int(len(prices)),
        "trades": trades,
        "wallet": wallet,
        "sharpe": wallet["sharpe"],
        "max_drawdown": wallet["max_drawdown_usdt"],
        "total_return": wallet["total_pnl_usdt"],
        "win_rate_pct": wallet["win_rate_trades_pct"],
        "config": {
            "delta": cfg.delta,
            "entry_z": cfg.entry_z,
            "exit_z": cfg.exit_z,
            "cost_bps": cfg.cost_bps,
            "slippage_bps": cfg.slippage_bps,
            "funding_bps_per_8h": cfg.funding_bps_per_8h,
            "p_trace_sizing": cfg.p_trace_sizing,
            "halt_p_trace_pct": cfg.halt_p_trace_pct,
            "initial_capital_usdt": cfg.initial_capital_usdt,
            "deploy_pct": cfg.deploy_pct,
            "leverage": cfg.leverage,
        },
        "poc": {
            "total_return_units": round(float(net_poc.sum()), 4),
            "sharpe": round(poc_sharpe, 3),
            "max_drawdown_units": round(poc_dd, 4),
            "win_rate_bars_pct": round(poc_wr, 1),
            "note": "spread-vol normalized units (legacy diagnostic)",
        },
        "series": {
            "open_time": prices["open_time"].tolist(),
            "zscore": zscore.round(4).tolist(),
            "position": position.tolist(),
            "beta": beta.round(6).tolist(),
            "P_trace": p_trace.round(6).tolist(),
            "size_scale": size_scale.round(4).tolist() if size_scale is not None else None,
            "equity_usdt": wallet["equity_usdt"],
        },
    }
