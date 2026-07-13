"""Donchian breakout — vnpy strategy registration."""

from __future__ import annotations

from typing import List

from quant.breakout_donchian.config import BreakoutDonchianConfig
from quant.breakout_donchian.donchian_vnpy import BreakoutDonchianVnpyStrategy
from quant.breakout_donchian.sizing import size_for_donchian
from quant.breakout_donchian.switches import BREAKOUT_DONCHIAN_SWITCH
from quant.common.exchange_env import resolve_live_exchange_id, resolve_market_data_exchange_id
from quant.common.kline_cache import norm_symbol
from quant.common.register_sizing import recent_atr
from quant.common.vnpy_wallet import lane_equity_usdt, migrate_vnpy_lane_tables
from quant.engine.exchanges.registry import vnpy_vt_symbol
from quant.engine.registry import VnpyLanePlugin
from quant.market import fetch_mark_price


def register_vnpy_strategies(cta_engine, cfg: BreakoutDonchianConfig, wallet_cur) -> List[str]:
    if wallet_cur is not None:
        migrate_vnpy_lane_tables(wallet_cur)
    live_ex = resolve_live_exchange_id(cfg.live_exchange)
    md_exchange = resolve_market_data_exchange_id(cfg.market_data_exchange)
    names: List[str] = []
    settings = BreakoutDonchianVnpyStrategy.from_donchian_config(cfg)
    interval = "1d" if cfg.signal_minutes >= 1440 else f"{max(1, int(cfg.signal_minutes))}m"
    for sym in cfg.symbol_list():
        sym = norm_symbol(sym)
        px = fetch_mark_price(sym, exchange_id=md_exchange) or 100.0
        eq = lane_equity_usdt(cfg, sym, cur=wallet_cur) if wallet_cur is not None else float(cfg.equity_usdt)
        atr = recent_atr(sym, interval, exchange_id=md_exchange, atr_period=max(2, int(cfg.atr_period)))
        stop_dist = max(1.5 * (atr if atr and atr > 0 else px * 0.015), px * 0.01)
        vol = size_for_donchian(cfg, px, stop_distance=stop_dist, equity_usdt=eq)
        name = f"dcn_{sym.lower()}"
        cta_engine.add_strategy(
            class_name="BreakoutDonchianVnpyStrategy",
            strategy_name=name,
            vt_symbol=vnpy_vt_symbol(sym, exchange_id=live_ex),
            setting={**settings, "fixed_size": max(vol, 0.001)},
        )
        names.append(name)
    return names


BREAKOUT_DONCHIAN_VNPY_PLUGIN = VnpyLanePlugin(
    name="breakout_donchian",
    load_config=BreakoutDonchianConfig.from_env,
    strategy_class=BreakoutDonchianVnpyStrategy,
    class_name="BreakoutDonchianVnpyStrategy",
    sync_prefix="dcn",
    register=register_vnpy_strategies,
    switch=BREAKOUT_DONCHIAN_SWITCH,
    uses_kline_stream=True,
)
