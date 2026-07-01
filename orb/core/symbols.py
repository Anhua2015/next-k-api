"""标的列表解析（原 orb.ml.samples，供 KK 等共用）。"""

from __future__ import annotations

from typing import List

from orb.core.kline_cache import norm_symbol


def parse_symbol_list(text: str) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "#" in line:
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
        for part in line.split(","):
            raw = part.strip()
            if not raw:
                continue
            sym = norm_symbol(raw)
            if sym and sym not in seen:
                seen.add(sym)
                out.append(sym)
    return out
