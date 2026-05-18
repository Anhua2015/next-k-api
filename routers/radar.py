from __future__ import annotations

import logging
from typing import Optional

import numpy as np
from fastapi import APIRouter

from market_data import AssetType, SYMBOLS, detect_asset_type, fetch_ohlcv
from models.api_models import RadarItem, SignalType
from utils.prices import smart_round

logger = logging.getLogger(__name__)

router = APIRouter(tags=["radar"])

@router.get("/api/radar")
async def get_radar(asset_type: Optional[str] = None):
    """Anomaly Radar - Scan for unusual patterns across all asset types."""
    items = []

    # Determine which asset types to scan
    if asset_type:
        asset_types = [AssetType(asset_type)]
    else:
        asset_types = list(AssetType)

    for at in asset_types:
        for sym_info in SYMBOLS[at]:
            symbol = sym_info["symbol"]
            name = sym_info["name"]

            try:
                df = await fetch_ohlcv(symbol, at, "1h", 50)
                if df is None or len(df) < 24:
                    continue

                closes = df['close'].values.astype(float)
                current = float(closes[-1])

                # Calculate anomaly score
                if len(closes) > 48:
                    recent_vol = np.std(np.log(closes[-24:] / closes[-25:-1]))
                    hist_vol = np.std(np.log(closes[-48:-24] / closes[-49:-25]))
                    vol_ratio = recent_vol / hist_vol if hist_vol > 0 else 1.0
                else:
                    vol_ratio = 1.0

                price_24h_ago = closes[-24] if len(closes) >= 24 else closes[0]
                price_change = (current - price_24h_ago) / price_24h_ago * 100

                anomaly = min(100, max(0, (vol_ratio - 1) * 50 + abs(price_change) * 2))

                signals = []
                if vol_ratio > 1.5:
                    signals.append("波動率飆升")
                if abs(price_change) > 5:
                    signals.append(f"強勢動能 ({price_change:+.1f}%)")

                if price_change > 3 and anomaly > 50:
                    signal = SignalType.BULLISH
                elif price_change < -3 and anomaly > 50:
                    signal = SignalType.BEARISH
                else:
                    signal = SignalType.NEUTRAL

                volatility_hint = "高度不確定 - 可能大波動" if anomaly > 70 else "正常波動" if anomaly < 30 else "值得關注"

                items.append(RadarItem(
                    symbol=symbol, name=name, asset_type=at.value,
                    anomaly_score=round(anomaly, 1), signal=signal,
                    signals=signals if signals else ["正常"],
                    price=smart_round(current), price_change=round(price_change, 2),
                    volatility_hint=volatility_hint,
                ))

            except Exception as e:
                logger.warning(f"Radar scan failed for {symbol}: {e}")

    items.sort(key=lambda x: x.anomaly_score, reverse=True)
    return items

