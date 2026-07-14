"""Tier-A mom-turn — vnpy strategy registration (spot)."""

from __future__ import annotations

from typing import List

from quant.common.exchange_env import resolve_live_exchange_id, resolve_market_data_exchange_id
from quant.common.kline_cache import norm_symbol
from quant.common.vnpy_wallet import lane_equity_usdt, migrate_vnpy_lane_tables
from quant.engine.exchanges.registry import vnpy_vt_symbol
from quant.engine.registry import VnpyLanePlugin
from quant.market import fetch_mark_price
from quant.tier_a_mom.config import TierAMomConfig
from quant.tier_a_mom.sizing import size_for_tier_a_mom
from quant.tier_a_mom.switches import TIER_A_MOM_SWITCH
from quant.tier_a_mom.tier_a_mom_vnpy import TierAMomVnpyStrategy


def register_vnpy_strategies(cta_engine, cfg: TierAMomConfig, wallet_cur) -> List[str]:
    if wallet_cur is not None:
        migrate_vnpy_lane_tables(wallet_cur)
    live_ex = resolve_live_exchange_id(cfg.live_exchange)
    md_exchange = resolve_market_data_exchange_id(cfg.market_data_exchange)
    names: List[str] = []
    settings = TierAMomVnpyStrategy.from_tier_a_mom_config(cfg)
    for sym in cfg.symbol_list():
        sym = norm_symbol(sym)
        px = fetch_mark_price(sym, exchange_id=md_exchange) or 1.0
        eq = lane_equity_usdt(cfg, sym, cur=wallet_cur) if wallet_cur is not None else float(cfg.equity_usdt)
        vol = size_for_tier_a_mom(cfg, px, equity_usdt=eq)
        name = f"tam_{sym.lower()}"
        cta_engine.add_strategy(
            class_name="TierAMomVnpyStrategy",
            strategy_name=name,
            vt_symbol=vnpy_vt_symbol(sym, exchange_id=live_ex),
            setting={**settings, "fixed_size": max(vol, 0.001)},
        )
        names.append(name)
    return names


TIER_A_MOM_VNPY_PLUGIN = VnpyLanePlugin(
    name="tier_a_mom",
    load_config=TierAMomConfig.from_env,
    strategy_class=TierAMomVnpyStrategy,
    class_name="TierAMomVnpyStrategy",
    sync_prefix="tam",
    register=register_vnpy_strategies,
    switch=TIER_A_MOM_SWITCH,
    uses_kline_stream=True,
)
