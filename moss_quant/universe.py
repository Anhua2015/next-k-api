"""Moss 内置 crypto 标的 ∩ 币安 U 本位永续。"""

from __future__ import annotations

import os
import re
import sqlite3
from typing import Any, Dict, List, Optional

from watchlist_symbols import drop_blacklisted_symbols, filter_symbols_to_binance_usdt_perps

# 每日寻优必扫：HyperCore 主板 23 + ICP、TON（见 moss_daily_core_symbols 表）
MOSS_DAILY_CORE_BASES = (
    "BTC",
    "ETH",
    "SOL",
    "BNB",
    "DOGE",
    "APT",
    "ATOM",
    "AVAX",
    "BCH",
    "DOT",
    "FIL",
    "HBAR",
    "ICP",
    "LINK",
    "LTC",
    "NEAR",
    "OP",
    "SUI",
    "TON",
    "TRX",
    "UNI",
    "XRP",
    "ADA",
    "ARB",
    "HYPE",
)

# 其余扩展币（暂不参与每日寻优；MOSS_QUANT_EXTENDED_UNIVERSE=1 时并入 universe）
MOSS_EXTENDED_BASES = (
    "AAVE",
    "ALGO",
    "BONK",
    "ENA",
    "ETC",
    "IMX",
    "INJ",
    "PENDLE",
    "PEPE",
    "POL",
    "RENDER",
    "SEI",
    "SHIB",
    "STRK",
    "TIA",
    "WIF",
    "WLD",
    "XLM",
)

# 全量目录（核心 + 扩展）；研究/回测 relax 时仍可用
_MOSS_CRYPTO_BASES = MOSS_DAILY_CORE_BASES + MOSS_EXTENDED_BASES


# 币安合约报价单位与 base 名不一致（千枚计价）
_BINANCE_CONTRACT_PREFIX: Dict[str, str] = {
    "PEPE": "1000PEPE",
    "SHIB": "1000SHIB",
    "BONK": "1000BONK",
}


def _extended_universe_enabled() -> bool:
    from moss_quant import config as cfg

    return bool(getattr(cfg, "MOSS_QUANT_EXTENDED_UNIVERSE", False))


def moss_catalog_bases() -> List[str]:
    """当前对外宇宙：默认每日核心 25；MOSS_QUANT_EXTENDED_UNIVERSE=1 时含其余扩展币。"""
    bases = list(MOSS_DAILY_CORE_BASES)
    if _extended_universe_enabled():
        for b in MOSS_EXTENDED_BASES:
            if b not in bases:
                bases.append(b)
    extra = os.getenv("MOSS_QUANT_EXTRA_BASES", "").strip()
    if extra:
        for x in extra.split(","):
            b = x.strip().upper()
            if b and b not in bases:
                bases.append(b)
    return sorted(bases)


def base_to_binance_symbol(base: str) -> str:
    b = str(base or "").strip().upper()
    if not b:
        return ""
    if b.endswith("USDT"):
        return b
    contract = _BINANCE_CONTRACT_PREFIX.get(b, b)
    return f"{contract}USDT"


def symbol_to_base(symbol: str) -> str:
    s = str(symbol or "").strip().upper()
    for q in ("USDT", "USDC", "BUSD"):
        if s.endswith(q) and len(s) > len(q):
            core = s[: -len(q)]
            if core.startswith("1000") and len(core) > 4:
                return core[4:]
            return core
    return s.replace("/", "").replace("-", "")


def list_daily_core_universe(
    conn: Optional[sqlite3.Connection] = None,
) -> List[Dict[str, Any]]:
    """每日寻优必扫标的：优先读 moss_daily_core_symbols 表，空表则回退内置 25。"""
    bases: List[str]
    if conn is not None:
        from moss_quant.db import list_daily_core_bases

        bases = list_daily_core_bases(conn)
    else:
        bases = list(MOSS_DAILY_CORE_BASES)
    raw = [base_to_binance_symbol(b) for b in bases]
    filtered = drop_blacklisted_symbols(filter_symbols_to_binance_usdt_perps(raw))
    out: List[Dict[str, Any]] = []
    for sym in filtered:
        base = symbol_to_base(sym)
        out.append(
            {
                "symbol": sym,
                "base": base,
                "display": f"{base}/USDT",
                "timeframe": "15m",
                "daily_core": True,
            }
        )
    return out


def list_universe() -> List[Dict[str, Any]]:
    """返回可在 Next-K 使用的 Moss 标的（币安永续）；默认与每日核心 25 一致。"""
    raw = [base_to_binance_symbol(b) for b in moss_catalog_bases()]
    filtered = drop_blacklisted_symbols(filter_symbols_to_binance_usdt_perps(raw))
    out: List[Dict[str, Any]] = []
    for sym in filtered:
        base = symbol_to_base(sym)
        out.append(
            {
                "symbol": sym,
                "base": base,
                "display": f"{base}/USDT",
                "timeframe": "15m",
            }
        )
    return out


def normalize_usdt_perp_symbol(symbol: str) -> str:
    """规范为 XXXUSDT（去空格/斜杠，无后缀则补 USDT）。"""
    s = str(symbol or "").strip().upper().replace("/", "").replace("-", "")
    if not s:
        return ""
    if not s.endswith("USDT"):
        s += "USDT"
    return s


def is_symbol_allowed(symbol: str) -> bool:
    """纸面 Profile / 每日寻优宇宙：仅 Moss 内置 ∩ 币安永续。"""
    sym = normalize_usdt_perp_symbol(symbol)
    allowed = {u["symbol"] for u in list_universe()}
    return sym in allowed


def is_research_symbol_allowed(symbol: str) -> bool:
    """回测 / 寻优 / 进化：默认任意 XXXUSDT；可关闭 relax 后仅允许币安永续 TRADING。"""
    from moss_quant import config as cfg

    sym = normalize_usdt_perp_symbol(symbol)
    if not sym or len(sym) < 6:
        return False
    if not re.fullmatch(r"[A-Z0-9]+USDT", sym):
        return False
    if cfg.MOSS_QUANT_RESEARCH_RELAX_SYMBOL_CHECK:
        return True
    kept = filter_symbols_to_binance_usdt_perps([sym])
    return bool(kept)


def active_symbols_taken(conn, *, exclude_profile_id: Optional[int] = None) -> set[str]:
    q = "SELECT symbol FROM moss_profiles WHERE enabled = 1"
    params: list[Any] = []
    if exclude_profile_id is not None:
        q += " AND id != ?"
        params.append(int(exclude_profile_id))
    rows = conn.execute(q, params).fetchall()
    return {str(r[0]).upper() for r in rows if r[0]}
