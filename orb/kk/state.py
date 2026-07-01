"""King Keltner CtaContext 序列化。"""

from __future__ import annotations

from typing import Any, Dict, List

from orb.cta.engine import CtaBacktestConfig, CtaContext, PendingStop, Position
from orb.core.config import OrbConfig


def export_ctx(ctx: CtaContext, *, last_day: str, prev_close: float) -> Dict[str, Any]:
    pos = ctx.pos
    pending: List[Dict[str, Any]] = [
        {"side": int(p.side), "px": float(p.px), "is_entry": bool(p.is_entry)} for p in ctx.pending
    ]
    return {
        "last_day": str(last_day or ""),
        "prev_close": float(prev_close or 0.0),
        "pos": {
            "side": int(pos.side),
            "entry": float(pos.entry),
            "sl": float(pos.sl),
            "notional": float(pos.notional),
            "entry_ms": int(pos.entry_ms),
        },
        "pending": pending,
        "intra_high": float(ctx.intra_high),
        "intra_low": float(ctx.intra_low),
        "state": dict(ctx.state),
    }


def import_ctx(
    payload: Dict[str, Any],
    *,
    cta_cfg: CtaBacktestConfig,
    orb_cfg: OrbConfig,
    wallet: float,
) -> tuple[CtaContext, str, float]:
    pos_raw = payload.get("pos") or {}
    pos = Position(
        side=int(pos_raw.get("side") or 0),
        entry=float(pos_raw.get("entry") or 0.0),
        sl=float(pos_raw.get("sl") or 0.0),
        notional=float(pos_raw.get("notional") or 0.0),
        entry_ms=int(pos_raw.get("entry_ms") or 0),
    )
    pending: List[PendingStop] = []
    for p in payload.get("pending") or []:
        if not isinstance(p, dict):
            continue
        pending.append(
            PendingStop(
                side=int(p.get("side") or 0),
                px=float(p.get("px") or 0.0),
                is_entry=bool(p.get("is_entry", True)),
            )
        )
    ctx = CtaContext(
        cfg=cta_cfg,
        orb_cfg=orb_cfg,
        wallet=float(wallet),
        pos=pos,
        pending=pending,
        intra_high=float(payload.get("intra_high") or 0.0),
        intra_low=float(payload.get("intra_low") or 0.0),
        trades=[],
        state=dict(payload.get("state") or {}),
    )
    last_day = str(payload.get("last_day") or "")
    prev_close = float(payload.get("prev_close") or 0.0)
    return ctx, last_day, prev_close
