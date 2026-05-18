from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional

import numpy as np
import pandas as pd

from app_state import state

logger = logging.getLogger(__name__)


class AssetType(str, Enum):
    CRYPTO = "crypto"
    STOCK = "stock"
    FOREX = "forex"


# Supported symbols by asset type
SYMBOLS = {
    AssetType.CRYPTO: [
        {"symbol": "BTC/USDT", "name": "Bitcoin", "icon": "₿"},
        {"symbol": "ETH/USDT", "name": "Ethereum", "icon": "Ξ"},
        {"symbol": "BNB/USDT", "name": "BNB", "icon": "B"},
        {"symbol": "SOL/USDT", "name": "Solana", "icon": "◎"},
        {"symbol": "PEPE/USDT", "name": "Pepe", "icon": "🐸"},
    ],
    AssetType.STOCK: [
        {"symbol": "AAPL", "name": "Apple Inc.", "icon": "🍎"},
        {"symbol": "GOOGL", "name": "Alphabet Inc.", "icon": "G"},
        {"symbol": "MSFT", "name": "Microsoft", "icon": "M"},
        {"symbol": "TSLA", "name": "Tesla Inc.", "icon": "T"},
        {"symbol": "NVDA", "name": "NVIDIA", "icon": "N"},
        {"symbol": "AMZN", "name": "Amazon", "icon": "A"},
        {"symbol": "META", "name": "Meta Platforms", "icon": "M"},
    ],
    AssetType.FOREX: [
        {"symbol": "EUR/USD", "name": "Euro/US Dollar", "icon": "€"},
        {"symbol": "GBP/USD", "name": "British Pound/US Dollar", "icon": "£"},
        {"symbol": "USD/JPY", "name": "US Dollar/Japanese Yen", "icon": "¥"},
        {"symbol": "AUD/USD", "name": "Australian Dollar/US Dollar", "icon": "A$"},
        {"symbol": "USD/CHF", "name": "US Dollar/Swiss Franc", "icon": "Fr"},
    ],
}


# ============== Data Fetchers ==============

# CoinGecko coin ID mapping
COINGECKO_IDS = {
    "BTC/USDT": "bitcoin", "ETH/USDT": "ethereum", "SOL/USDT": "solana",
    "BNB/USDT": "binancecoin", "PEPE/USDT": "pepe",
}


async def fetch_crypto_coingecko(symbol: str, limit: int = 100) -> Optional[pd.DataFrame]:
    """Fetch crypto OHLCV data from CoinGecko (fallback)."""
    import aiohttp

    coin_id = COINGECKO_IDS.get(symbol)
    if not coin_id:
        return None

    try:
        days = min(limit // 24 + 1, 90)  # CoinGecko max 90 days for hourly
        url = f"https://api.coingecko.com/api/v3/coins/{coin_id}/ohlc?vs_currency=usd&days={days}"

        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    logger.warning(f"CoinGecko returned {resp.status} for {symbol}")
                    return None
                data = await resp.json()

        if not data:
            return None

        df = pd.DataFrame(data, columns=['timestamp', 'open', 'high', 'low', 'close'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df['volume'] = 0  # CoinGecko OHLC doesn't include volume
        df = df.tail(limit)
        logger.info(f"Fetched {len(df)} bars from CoinGecko for {symbol}")
        return df

    except Exception as e:
        logger.warning(f"CoinGecko failed for {symbol}: {e}")
        return None


async def fetch_crypto_ohlcv(symbol: str, timeframe: str = "1h", limit: int = 100) -> Optional[pd.DataFrame]:
    """Fetch crypto OHLCV data - tries Binance first, then CoinGecko."""
    # Try Binance first
    if state.ccxt_exchange:
        try:
            ohlcv = await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: state.ccxt_exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
            )
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            logger.info(f"Fetched {len(df)} bars from Binance for {symbol}")
            return df
        except Exception as e:
            logger.warning(f"Binance failed for {symbol}: {e}")

    # Fallback to CoinGecko
    return await fetch_crypto_coingecko(symbol, limit)


async def fetch_stock_ohlcv(symbol: str, timeframe: str = "1h", limit: int = 100) -> Optional[pd.DataFrame]:
    """Fetch stock OHLCV data from yfinance."""
    try:
        import yfinance as yf

        # Map timeframe to yfinance parameters
        # 对于1小时数据，需要至少11天才能获取250根（250/24 ≈ 11天）
        # yfinance最多支持730天（2年）的历史数据
        if timeframe == "1h":
            # 计算需要的天数：limit / 24，向上取整，最少11天，最多730天
            days_needed = max(11, min((limit + 23) // 24, 730))
            period = f"{days_needed}d"
            interval = "1h"
        else:
            tf_map = {
                "4h": ("60d", "1h"),  # yfinance doesn't support 4h, fetch 1h and resample
                "1d": (f"{limit}d", "1d"),
                "1w": (f"{limit * 7}d", "1wk"),
            }
            period, interval = tf_map.get(timeframe, ("7d", "1h"))

        def _fetch():
            ticker = yf.Ticker(symbol)
            df = ticker.history(period=period, interval=interval)
            if df.empty and interval == "1h":
                # Fallback to daily data if hourly not available
                df = ticker.history(period=f"{limit}d", interval="1d")
            return df

        df = await asyncio.get_running_loop().run_in_executor(None, _fetch)

        if df is not None and not df.empty:
            df = df.reset_index()
            df.columns = [c.lower() for c in df.columns]
            df = df.rename(columns={'datetime': 'timestamp', 'date': 'timestamp'})
            df = df[['timestamp', 'open', 'high', 'low', 'close', 'volume']]

            # Resample for 4h if needed
            if timeframe == "4h" and interval == "1h":
                df = df.set_index('timestamp')
                df = df.resample('4h').agg({
                    'open': 'first', 'high': 'max', 'low': 'min',
                    'close': 'last', 'volume': 'sum'
                }).dropna().reset_index()

            return df.tail(limit)

        return None
    except Exception as e:
        logger.warning(f"Failed to fetch stock OHLCV for {symbol}: {e}")
        return None


async def fetch_forex_ohlcv(symbol: str, timeframe: str = "1h", limit: int = 100) -> Optional[pd.DataFrame]:
    """Fetch forex OHLCV data from yfinance."""
    try:
        import yfinance as yf

        # Convert forex symbol format: EUR/USD -> EURUSD=X
        yf_symbol = symbol.replace("/", "") + "=X"

        # Map timeframe to yfinance parameters
        # 对于1小时数据，需要至少11天才能获取250根（250/24 ≈ 11天）
        # yfinance最多支持730天（2年）的历史数据
        if timeframe == "1h":
            # 计算需要的天数：limit / 24，向上取整，最少11天，最多730天
            days_needed = max(11, min((limit + 23) // 24, 730))
            period = f"{days_needed}d"
            interval = "1h"
        else:
            tf_map = {
                "4h": ("60d", "1h"),  # Resample to 4h
                "1d": (f"{limit}d", "1d"),
                "1w": (f"{limit * 7}d", "1wk"),
            }
            period, interval = tf_map.get(timeframe, ("7d", "1h"))

        def _fetch():
            ticker = yf.Ticker(yf_symbol)
            df = ticker.history(period=period, interval=interval)
            if df.empty and interval == "1h":
                df = ticker.history(period=f"{limit}d", interval="1d")
            return df

        df = await asyncio.get_running_loop().run_in_executor(None, _fetch)

        if df is not None and not df.empty:
            df = df.reset_index()
            df.columns = [c.lower() for c in df.columns]
            df = df.rename(columns={'datetime': 'timestamp', 'date': 'timestamp'})
            if 'volume' not in df.columns:
                df['volume'] = 0
            df = df[['timestamp', 'open', 'high', 'low', 'close', 'volume']]

            # Resample for 4h if needed
            if timeframe == "4h" and interval == "1h":
                df = df.set_index('timestamp')
                df = df.resample('4h').agg({
                    'open': 'first', 'high': 'max', 'low': 'min',
                    'close': 'last', 'volume': 'sum'
                }).dropna().reset_index()

            return df.tail(limit)

        return None
    except Exception as e:
        logger.warning(f"Failed to fetch forex OHLCV for {symbol}: {e}")
        return None


def generate_demo_ohlcv(symbol: str, asset_type: AssetType, timeframe: str = "1h", limit: int = 100) -> pd.DataFrame:
    """Generate demo OHLCV data when real data is unavailable."""
    # Base prices for different assets
    base_prices = {
        "BTC/USDT": 95000, "ETH/USDT": 3200, "SOL/USDT": 180,
        "BNB/USDT": 650, "PEPE/USDT": 0.00002,
        "AAPL": 230, "MSFT": 420, "GOOGL": 175,
        "TSLA": 380, "NVDA": 140, "AMZN": 220, "META": 580,
        "EUR/USD": 1.08, "GBP/USD": 1.27, "USD/JPY": 155,
        "AUD/USD": 0.64, "USD/CHF": 0.90
    }
    base_price = base_prices.get(symbol, 100)
    volatility = 0.02 if asset_type == AssetType.CRYPTO else 0.01

    # Adjust volatility for different timeframes
    tf_hours = {"1h": 1, "4h": 4, "1d": 24, "1w": 168}
    hours_per_bar = tf_hours.get(timeframe, 1)
    volatility = volatility * np.sqrt(hours_per_bar)  # Scale volatility

    now = datetime.now(timezone.utc)
    data = []
    price = base_price

    for i in range(limit):
        timestamp = now - timedelta(hours=(limit - i) * hours_per_bar)
        change = np.random.randn() * volatility
        price = price * (1 + change)
        high = price * (1 + abs(np.random.randn() * volatility * 0.5))
        low = price * (1 - abs(np.random.randn() * volatility * 0.5))
        open_price = low + np.random.random() * (high - low)
        volume = np.random.randint(1000, 100000) * base_price * hours_per_bar

        data.append({
            'timestamp': timestamp,
            'open': open_price,
            'high': high,
            'low': low,
            'close': price,
            'volume': volume
        })

    return pd.DataFrame(data)


async def fetch_ohlcv(symbol: str, asset_type: AssetType, timeframe: str = "1h", limit: int = 100) -> Optional[pd.DataFrame]:
    """Unified OHLCV fetcher for all asset types. Falls back to demo data if unavailable."""
    df = None

    if asset_type == AssetType.CRYPTO:
        df = await fetch_crypto_ohlcv(symbol, timeframe, limit)
    elif asset_type == AssetType.STOCK:
        df = await fetch_stock_ohlcv(symbol, timeframe, limit)
    elif asset_type == AssetType.FOREX:
        df = await fetch_forex_ohlcv(symbol, timeframe, limit)

    # Fallback to demo data if real data unavailable
    if df is None or len(df) < 30:
        logger.info(f"Using demo data for {symbol} ({asset_type.value}) [{timeframe}]")
        df = generate_demo_ohlcv(symbol, asset_type, timeframe, limit)

    return df


def detect_asset_type(symbol: str) -> AssetType:
    """Auto-detect asset type from symbol."""
    symbol_upper = symbol.upper()

    # Check crypto
    for s in SYMBOLS[AssetType.CRYPTO]:
        if s["symbol"] == symbol_upper or symbol_upper.replace("-", "/") == s["symbol"]:
            return AssetType.CRYPTO

    # Check stocks
    for s in SYMBOLS[AssetType.STOCK]:
        if s["symbol"] == symbol_upper:
            return AssetType.STOCK

    # Check forex
    for s in SYMBOLS[AssetType.FOREX]:
        if s["symbol"] == symbol_upper or symbol_upper.replace("-", "/") == s["symbol"]:
            return AssetType.FOREX

    # Default to crypto if contains USDT/USD pair format
    if "USDT" in symbol_upper or (symbol_upper.count("/") == 1 and len(symbol_upper) < 10):
        return AssetType.CRYPTO

    # Default to stock for single tickers
    return AssetType.STOCK
