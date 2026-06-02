"""Moss2 runtime service: selection, dedupe, switching, backtest."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import pandas as pd

from moss2_quant import config as m2cfg
from moss2_quant import db as m2db
from moss_quant import config as mq_cfg
from moss_quant.backtest_service import run_full_backtest
from moss_quant.core.decision import DecisionParams, compute_last_composite
from moss_quant.core.regime import classify_regime
from moss_quant.kline_cache import load_cached
from moss_quant.params import build_initial_params, resolve_params_dict
from moss_quant.universe import MOSS_DAILY_CORE_BASES, base_to_binance_symbol


TEMPLATE_TO_LAYER = {
    "trend": "A",
    "momentum": "B",
    "mean_revert": "C",
    "balanced": "D",
}

ROBOT_TACTICAL_DEFAULTS: Dict[str, Dict[str, Any]] = {
    # trend 双机器人：1 偏进攻，2 偏稳健
    "trend-1": {
        "entry_threshold": 0.44,
        "sl_atr_mult": 2.1,
        "tp_rr_ratio": 2.7,
        "trend_strength_min": 22,
        "regime_sensitivity": 0.58,
    },
    "trend-2": {
        "entry_threshold": 0.50,
        "sl_atr_mult": 2.7,
        "tp_rr_ratio": 2.3,
        "trend_strength_min": 28,
        "regime_sensitivity": 0.52,
    },
    # momentum 双机器人：1 偏进攻，2 偏稳健
    "momentum-1": {
        "entry_threshold": 0.45,
        "sl_atr_mult": 2.0,
        "tp_rr_ratio": 2.6,
        "trailing_activation_pct": 0.018,
        "trailing_distance_atr": 1.35,
    },
    "momentum-2": {
        "entry_threshold": 0.52,
        "sl_atr_mult": 2.4,
        "tp_rr_ratio": 2.2,
        "trailing_activation_pct": 0.026,
        "trailing_distance_atr": 1.7,
    },
    "mean-revert-1": {
        "entry_threshold": 0.40,
        "sl_atr_mult": 1.9,
        "tp_rr_ratio": 1.7,
        "exit_threshold": 0.10,
        "trend_strength_min": 18,
    },
    "balanced-1": {
        "entry_threshold": 0.46,
        "sl_atr_mult": 2.2,
        "tp_rr_ratio": 2.1,
        "regime_sensitivity": 0.54,
        "trend_strength_min": 21,
    },
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_utc(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _to_utc_str(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _score_symbol(symbol: str, params: dict, *, refresh_klines: bool = False) -> Dict[str, Any]:
    df = load_cached(symbol, refresh=refresh_klines)
    regime = classify_regime(df, version=mq_cfg.MOSS_QUANT_REGIME_VERSION)
    p = DecisionParams.from_dict(resolve_params_dict(params))
    composite = float(compute_last_composite(df, p, regime))
    close_s = pd.to_numeric(df["close"], errors="coerce").dropna()
    high_s = pd.to_numeric(df["high"], errors="coerce").dropna()
    low_s = pd.to_numeric(df["low"], errors="coerce").dropna()
    atr_pct = 0.0
    if len(close_s) > 20 and len(high_s) == len(close_s) and len(low_s) == len(close_s):
        prev_close = close_s.shift(1)
        tr = pd.concat(
            [
                (high_s - low_s).abs(),
                (high_s - prev_close).abs(),
                (low_s - prev_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        atr_v = float(tr.tail(14).mean() or 0.0)
        last_close = float(close_s.iloc[-1] or 0.0)
        atr_pct = (atr_v / last_close) if last_close > 0 else 0.0

    vol_pct = 0.0
    if len(close_s) > 40:
        ret = close_s.pct_change().dropna()
        if len(ret):
            vol_pct = float(ret.tail(96).std() or 0.0) * (96.0 ** 0.5)

    risk_penalty = min(0.35, atr_pct * 4.0 + vol_pct * 1.6)
    score_adj = abs(composite) - risk_penalty
    side = "LONG" if composite >= 0 else "SHORT"
    return {
        "symbol": symbol,
        "composite": composite,
        "abs_composite": abs(composite),
        "risk_penalty": risk_penalty,
        "score_adj": score_adj,
        "side": side,
        "close": float(df["close"].iloc[-1]) if len(df) else None,
    }


def _robot_effective_params(robot: Dict[str, Any]) -> dict:
    template = str(robot.get("template") or "balanced").strip().lower()
    base = build_initial_params(template=template)
    tactical = robot.get("tactical_params") or {}
    base.update({k: v for k, v in tactical.items() if v is not None})
    return resolve_params_dict(base)


def _eligible_candidates(conn, robot: Dict[str, Any]) -> List[str]:
    candidates = [str(s).strip().upper() for s in (robot.get("candidate_symbols") or []) if str(s).strip()]
    if not candidates:
        layer = str(robot.get("layer_code") or "").strip().upper()
        rows = m2db.list_symbol_layers(conn)
        candidates = [str(r["symbol"]).upper() for r in rows if str(r.get("layer_code") or "").upper() == layer]
    return candidates


@dataclass
class RobotDecision:
    robot_id: int
    robot_name: str
    action: str
    reason: str
    symbol: Optional[str] = None
    side: Optional[str] = None
    composite: Optional[float] = None


def run_scan_once(conn, *, refresh_klines: bool = False) -> Dict[str, Any]:
    robots = [r for r in m2db.list_robots(conn) if bool(r.get("enabled"))]
    robots_scanned = len(robots)
    decisions: List[RobotDecision] = []
    opens = 0
    closes = 0
    skips = 0

    for robot in robots:
        rid = int(robot["id"])
        name = str(robot.get("name") or f"robot-{rid}")
        now = _utc_now()
        cooldown_until = _parse_utc(robot.get("cooldown_until_utc"))
        if cooldown_until and now < cooldown_until:
            skips += 1
            decisions.append(RobotDecision(rid, name, "SKIP", "cooldown"))
            continue

        open_pos = m2db.get_open_position_by_robot(conn, rid)
        params = _robot_effective_params(robot)
        entry_threshold = float(params.get("entry_threshold", 0.4))
        candidates = _eligible_candidates(conn, robot)
        if not candidates:
            skips += 1
            decisions.append(RobotDecision(rid, name, "SKIP", "empty_candidate_pool"))
            continue

        scored: List[Dict[str, Any]] = []
        for symbol in candidates:
            try:
                row = _score_symbol(symbol, params, refresh_klines=refresh_klines)
                scored.append(row)
            except Exception:
                continue
        if not scored:
            skips += 1
            decisions.append(RobotDecision(rid, name, "SKIP", "no_market_data"))
            continue
        scored.sort(key=lambda x: x.get("score_adj", x["abs_composite"]), reverse=True)
        best = scored[0]

        if open_pos:
            # 方案约束：仅空仓允许切换标的。持仓中保持，不做同轮换仓。
            current_symbol = str(open_pos.get("symbol") or "").upper()
            skips += 1
            decisions.append(RobotDecision(rid, name, "HOLD", "position_open_no_switch", current_symbol))
            continue

        if float(best.get("score_adj", best["abs_composite"])) < entry_threshold:
            skips += 1
            decisions.append(RobotDecision(rid, name, "SKIP", "below_entry_threshold", best["symbol"], best["side"], float(best["composite"])))
            continue
        if m2db.has_open_position_for_symbol(conn, best["symbol"]):
            skips += 1
            decisions.append(RobotDecision(rid, name, "SKIP", "symbol_already_held", best["symbol"], best["side"], float(best["composite"])))
            continue

        m2db.open_position(
            conn,
            robot_id=rid,
            symbol=best["symbol"],
            side=best["side"],
            reason="new_open_from_scan",
        )
        opens += 1
        decisions.append(RobotDecision(rid, name, "OPEN", "best_candidate", best["symbol"], best["side"], float(best["composite"])))

    details = {
        "decisions": [d.__dict__ for d in decisions],
    }
    run_id = m2db.insert_scan_run(
        conn,
        robots_scanned=robots_scanned,
        opens=opens,
        closes=closes,
        skips=skips,
        details=details,
    )
    return {
        "ok": True,
        "run_id": run_id,
        "robots_scanned": robots_scanned,
        "opens": opens,
        "closes": closes,
        "skips": skips,
        "details": details,
    }


def seed_recommended_setup(conn) -> Dict[str, Any]:
    robots = [
        ("trend-1", "trend", "A"),
        ("trend-2", "trend", "A"),
        ("momentum-1", "momentum", "B"),
        ("momentum-2", "momentum", "B"),
        ("mean-revert-1", "mean_revert", "C"),
        ("balanced-1", "balanced", "D"),
    ]
    existing = {str(r.get("name") or ""): r for r in m2db.list_robots(conn)}
    created = []
    for name, template, layer in robots:
        tactical = dict(ROBOT_TACTICAL_DEFAULTS.get(name) or {})
        prev = existing.get(name) or {}
        prev_tactical = prev.get("tactical_params") or {}
        # 已有机器人优先保留历史调参，避免同步/seed 重置。
        merged_tactical = dict(tactical)
        merged_tactical.update({k: v for k, v in prev_tactical.items() if v is not None})
        created.append(
            m2db.upsert_robot(
                conn,
                name=name,
                template=template,
                layer_code=layer,
                candidate_symbols=prev.get("candidate_symbols") or [],
                tactical_params=merged_tactical,
                enabled=bool(prev.get("enabled", True)),
            )
        )
    return {"ok": True, "robots": created}


def run_portfolio_backtest(
    conn,
    *,
    top_n_per_robot: int = 3,
    refresh_klines: bool = False,
) -> Dict[str, Any]:
    robots = [r for r in m2db.list_robots(conn) if bool(r.get("enabled"))]
    reports = []
    total_return = 0.0
    total_trades = 0
    total_weight = 0.0
    for robot in robots:
        rid = int(robot["id"])
        name = str(robot.get("name") or f"robot-{rid}")
        params = _robot_effective_params(robot)
        candidates = _eligible_candidates(conn, robot)
        if not candidates:
            reports.append({"robot_id": rid, "name": name, "status": "skipped", "reason": "empty_candidate_pool"})
            continue
        scores = []
        for symbol in candidates:
            try:
                s = _score_symbol(symbol, params, refresh_klines=refresh_klines)
                scores.append(s)
            except Exception:
                continue
        if not scores:
            reports.append({"robot_id": rid, "name": name, "status": "skipped", "reason": "no_market_data"})
            continue
        scores.sort(key=lambda x: x["abs_composite"], reverse=True)
        picks = scores[: max(1, int(top_n_per_robot))]
        best = None
        for item in picks:
            bt = run_full_backtest(
                symbol=item["symbol"],
                params=params,
                refresh_klines=refresh_klines,
            )
            if best is None or float(bt["summary"]["sharpe"]) > float(best["summary"]["sharpe"]):
                best = bt
        if best is None:
            reports.append({"robot_id": rid, "name": name, "status": "skipped", "reason": "backtest_failed"})
            continue
        ret = float(best["summary"]["total_return"])
        trades = int(best["summary"]["total_trades"])
        total_return += ret
        total_trades += trades
        total_weight += 1.0
        reports.append(
            {
                "robot_id": rid,
                "name": name,
                "template": robot.get("template"),
                "selected_symbol": best["symbol"],
                "summary": best["summary"],
                "status": "ok",
            }
        )
    avg_return = (total_return / total_weight) if total_weight > 0 else 0.0
    return {
        "ok": True,
        "robots_total": len(robots),
        "robots_effective": int(total_weight),
        "portfolio_avg_return": round(avg_return, 6),
        "portfolio_total_trades": total_trades,
        "reports": reports,
    }


def sync_from_daily_optimize(
    conn,
    *,
    batch_id: Optional[int] = None,
    max_symbols: int = 30,
    auto_assign_robots: bool = True,
) -> Dict[str, Any]:
    from moss_quant.daily_optimize_service import get_latest_daily_batch

    if batch_id is not None:
        row = conn.execute(
            "SELECT id FROM moss_daily_optimize_batches WHERE id=?",
            (int(batch_id),),
        ).fetchone()
        if not row:
            raise ValueError(f"daily batch not found: {batch_id}")
        # 复用 latest 结构：把指定 batch 提升为 latest 读取
        daily = {"id": int(batch_id), "items": []}
        rows = conn.execute(
            """SELECT * FROM moss_daily_optimize_items
               WHERE batch_id=? ORDER BY score DESC, symbol ASC""",
            (int(batch_id),),
        ).fetchall()
        import json as _json

        out_items = []
        for r in rows:
            d = dict(r)
            d["summary"] = _json.loads(d.get("summary_json") or "{}")
            out_items.append(d)
        daily["items"] = out_items
    else:
        daily = get_latest_daily_batch(conn)
    if not daily:
        return {"ok": False, "reason": "no_daily_optimize_batch"}

    items = list(daily.get("items") or [])
    filtered = []
    for it in items:
        symbol = str(it.get("symbol") or "").strip()
        template = str(it.get("template") or "").strip().lower()
        summary = it.get("summary") or {}
        score = float(it.get("score") or -999.0)
        if not symbol:
            continue
        if not template:
            continue
        if summary.get("error"):
            continue
        if score <= -900:
            continue
        filtered.append(it)
    items = filtered
    items.sort(key=lambda x: float(x.get("score") or -999.0), reverse=True)
    selected = items[: max(1, int(max_symbols))]

    layer_symbols: Dict[str, List[str]] = {"A": [], "B": [], "C": [], "D": []}
    synced = []
    for it in selected:
        symbol = str(it.get("symbol") or "").strip().upper()
        template = str(it.get("template") or "").strip().lower()
        summary = it.get("summary") or {}
        tier = str(summary.get("pool_tier") or "C").strip().upper()
        if tier == "A":
            layer = "A"
        elif tier == "B":
            layer = "B"
        else:
            # 每日寻优只有 A/B/C，Moss2 额外拆出 D 给 balanced
            layer = "D" if template == "balanced" else "C"
        row = m2db.upsert_symbol_layer(
            conn,
            symbol=symbol,
            layer_code=layer,
            score=float(it.get("score") or 0.0),
            note=f"daily_batch={int(daily.get('id') or 0)};pool_tier={tier};template={template or 'na'}",
        )
        layer_symbols[layer].append(symbol)
        synced.append(row)

    robot_updates = []
    if auto_assign_robots:
        seed_recommended_setup(conn)
        robots = m2db.list_robots(conn)
        for rb in robots:
            name = str(rb.get("name") or "")
            template = str(rb.get("template") or "").lower()
            if template == "trend":
                pool = layer_symbols["A"]
                # trend-1 / trend-2 分半，避免抢同一批币
                mid = max(1, len(pool) // 2)
                candidates = pool[:mid] if name.endswith("-1") else pool[mid:]
                if not candidates:
                    candidates = pool
            elif template == "momentum":
                pool = layer_symbols["B"]
                mid = max(1, len(pool) // 2)
                candidates = pool[:mid] if name.endswith("-1") else pool[mid:]
                if not candidates:
                    candidates = pool
            elif template == "mean_revert":
                candidates = layer_symbols["C"]
            else:
                candidates = layer_symbols["D"]
            updated = m2db.upsert_robot(
                conn,
                name=name,
                template=template,
                layer_code=TEMPLATE_TO_LAYER.get(template, "D"),
                candidate_symbols=candidates,
                tactical_params=rb.get("tactical_params") or {},
                enabled=bool(rb.get("enabled")),
            )
            robot_updates.append(updated)

    return {
        "ok": True,
        "source_batch_id": int(daily.get("id") or 0),
        "symbols_selected": len(selected),
        "layer_counts": {k: len(v) for k, v in layer_symbols.items()},
        "synced_layers": synced,
        "robots_updated": robot_updates,
    }


def sync_from_builtin_core_list(
    conn,
    *,
    auto_assign_robots: bool = True,
) -> Dict[str, Any]:
    symbols = [base_to_binance_symbol(base) for base in MOSS_DAILY_CORE_BASES]
    symbols = [s for s in symbols if s]
    # 按目标结构分层：A(8) / B(10) / C(6) / D(余下)
    a = symbols[:8]
    b = symbols[8:18]
    c = symbols[18:24]
    d = symbols[24:]
    layer_symbols: Dict[str, List[str]] = {"A": a, "B": b, "C": c, "D": d}

    synced = []
    for layer, pool in layer_symbols.items():
        for i, symbol in enumerate(pool):
            row = m2db.upsert_symbol_layer(
                conn,
                symbol=symbol,
                layer_code=layer,
                score=float(len(pool) - i),
                note="source=builtin_core_list",
            )
            synced.append(row)

    robot_updates = []
    if auto_assign_robots:
        seed_recommended_setup(conn)
        robots = m2db.list_robots(conn)
        for rb in robots:
            name = str(rb.get("name") or "")
            template = str(rb.get("template") or "").lower()
            if template == "trend":
                pool = layer_symbols["A"]
                mid = max(1, len(pool) // 2)
                candidates = pool[:mid] if name.endswith("-1") else pool[mid:]
                if not candidates:
                    candidates = pool
            elif template == "momentum":
                pool = layer_symbols["B"]
                mid = max(1, len(pool) // 2)
                candidates = pool[:mid] if name.endswith("-1") else pool[mid:]
                if not candidates:
                    candidates = pool
            elif template == "mean_revert":
                candidates = layer_symbols["C"]
            else:
                candidates = layer_symbols["D"]
            updated = m2db.upsert_robot(
                conn,
                name=name,
                template=template,
                layer_code=TEMPLATE_TO_LAYER.get(template, "D"),
                candidate_symbols=candidates,
                tactical_params=rb.get("tactical_params") or {},
                enabled=bool(rb.get("enabled")),
            )
            robot_updates.append(updated)

    return {
        "ok": True,
        "source": "builtin_core_list",
        "symbols_selected": len(symbols),
        "layer_counts": {k: len(v) for k, v in layer_symbols.items()},
        "synced_layers": synced,
        "robots_updated": robot_updates,
    }
