from __future__ import annotations

def smart_round(price: float, min_decimals: int = 4) -> float:
    """Dynamically round price based on magnitude (for small-price coins like PEPE)."""
    if price == 0:
        return 0.0
    abs_price = abs(price)
    if abs_price >= 1:
        return round(price, min_decimals)
    elif abs_price >= 0.01:
        return round(price, 6)
