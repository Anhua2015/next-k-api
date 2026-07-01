"""VeighNa 官方示例 CTA 策略逻辑（移植版）。"""

from __future__ import annotations

from typing import Any, Callable, Dict

import pandas as pd

from orb.cta import indicators as ind


def _buf(ctx) -> Dict[str, Any]:
    return ctx.state.setdefault("buf", {"rows": []})


def _df_from_buf(ctx, min_rows: int) -> pd.DataFrame | None:
    rows = _buf(ctx)["rows"]
    if len(rows) < min_rows:
        return None
    return pd.DataFrame(rows)


def _push_bar(ctx, row: pd.Series, max_rows: int = 500) -> pd.DataFrame | None:
    b = _buf(ctx)
    b["rows"].append(
        {
            "open_time": int(row["open_time"]),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": float(row.get("volume") or 0),
        }
    )
    if len(b["rows"]) > max_rows:
        b["rows"] = b["rows"][-max_rows:]
    return pd.DataFrame(b["rows"])


# --- Double MA (1m) ---
def double_ma_on_bar(ctx, row: pd.Series, ms: int) -> None:
  df = _push_bar(ctx, row)
  if df is None or len(df) < 22:
    return
  fast = ind.sma(df["close"], 10)
  slow = ind.sma(df["close"], 20)
  f0, f1, s0, s1 = fast.iloc[-1], fast.iloc[-2], slow.iloc[-1], slow.iloc[-2]
  px = float(row["close"])
  cross_over = f0 > s0 and f1 <= s1
  cross_below = f0 < s0 and f1 >= s1
  if cross_over:
    if ctx.pos.side < 0:
      ctx.close(px, ms=ms, outcome="reverse")
    if ctx.pos.side == 0:
      ctx.open_long(px, sl=px * 0.98, ms=ms, tag="dma_cross")
  elif cross_below:
    if ctx.pos.side > 0:
      ctx.close(px, ms=ms, outcome="reverse")
    if ctx.pos.side == 0:
      ctx.open_short(px, sl=px * 1.02, ms=ms, tag="dma_cross")


# --- ATR + RSI (1m) ---
def atr_rsi_on_bar(ctx, row: pd.Series, ms: int) -> None:
  df = _push_bar(ctx, row)
  if df is None or len(df) < 30:
    return
  atr_v = ind.atr(df, 22)
  rsi_v = ind.rsi(df["close"], 5)
  atr_ma = atr_v.iloc[-10:].mean()
  a, r = float(atr_v.iloc[-1]), float(rsi_v.iloc[-1])
  px = float(row["close"])
  if ctx.pos.side == 0:
    if a > atr_ma:
      if r > 66:
        ctx.open_long(px + 0.01, sl=px * 0.992, ms=ms, tag="atr_rsi")
      elif r < 34:
        ctx.open_short(px - 0.01, sl=px * 1.008, ms=ms, tag="atr_rsi")
  elif ctx.pos.side == 1:
    stop = ctx.intra_high * (1 - 0.008)
    ctx.set_exit_stop(stop)
  elif ctx.pos.side == -1:
    stop = ctx.intra_low * (1 + 0.008)
    ctx.set_exit_stop(stop)


# --- Boll Channel (15m) ---
def boll_channel_on_bar(ctx, row: pd.Series, ms: int) -> None:
  st = ctx.state.setdefault("boll", {"last_bucket": None, "bar": None})
  bucket = int(ms) // (15 * 60_000)
  if st["last_bucket"] is None:
    st["last_bucket"] = bucket
    st["bar"] = dict(open=row["open"], high=row["high"], low=row["low"], close=row["close"], open_time=ms)
    return
  if bucket == st["last_bucket"]:
    b = st["bar"]
    b["high"] = max(float(b["high"]), float(row["high"]))
    b["low"] = min(float(b["low"]), float(row["low"]))
    b["close"] = float(row["close"])
    return
  # flush 15m
  completed = pd.Series(st["bar"])
  st["last_bucket"] = bucket
  st["bar"] = dict(open=row["open"], high=row["high"], low=row["low"], close=row["close"], open_time=ms)
  df = _push_bar(ctx, completed)
  if df is None or len(df) < 35:
    return
  up, down = ind.boll(df["close"], 18, 3.4)
  cci_v = ind.cci(df, 10)
  atr_v = ind.atr(df, 30)
  u, d, c, a = float(up.iloc[-1]), float(down.iloc[-1]), float(cci_v.iloc[-1]), float(atr_v.iloc[-1])
  if ctx.pos.side == 0:
    ctx.intra_high = float(completed["high"])
    ctx.intra_low = float(completed["low"])
    if c > 0:
      ctx.set_entry_stops(u, 0)
    elif c < 0:
      ctx.set_entry_stops(0, d)
  elif ctx.pos.side == 1:
    ctx.set_exit_stop(ctx.intra_high - a * 5.2)
  elif ctx.pos.side == -1:
    ctx.set_exit_stop(ctx.intra_low + a * 5.2)


# --- King Keltner (5m, vnpy KingKeltnerStrategy) ---
KK_LENGTH = 11
KK_DEV = 1.6
KK_TRAILING_PCT = 0.8  # trailing_percent / 100


def king_keltner_on_bar(ctx, row: pd.Series, ms: int) -> None:
    """对齐 vnpy_ctastrategy KingKeltnerStrategy.on_5min_bar。"""
    st = ctx.state.setdefault("kk", {"last_bucket": None, "bar": None})
    bucket = int(ms) // (5 * 60_000)
    if st["last_bucket"] is None:
        st["last_bucket"] = bucket
        st["bar"] = dict(
            open=row["open"], high=row["high"], low=row["low"], close=row["close"], open_time=ms
        )
        return
    if bucket == st["last_bucket"]:
        b = st["bar"]
        b["high"] = max(float(b["high"]), float(row["high"]))
        b["low"] = min(float(b["low"]), float(row["low"]))
        b["close"] = float(row["close"])
        return
    completed = pd.Series(st["bar"])
    st["last_bucket"] = bucket
    st["bar"] = dict(
        open=row["open"], high=row["high"], low=row["low"], close=row["close"], open_time=ms
    )
    df = _push_bar(ctx, completed)
    if df is None or len(df) < 20:
        return
    up, down = ind.keltner(df, KK_LENGTH, KK_DEV)
    ku, kd = float(up.iloc[-1]), float(down.iloc[-1])
    h5 = float(completed["high"])
    l5 = float(completed["low"])
    trail = KK_TRAILING_PCT / 100.0
    if ctx.pos.side == 0:
        ctx.intra_high = h5
        ctx.intra_low = l5
        ctx.set_entry_stops(ku, kd)
    elif ctx.pos.side == 1:
        ctx.intra_high = max(float(ctx.intra_high), h5)
        ctx.intra_low = l5
        ctx.set_exit_stop(ctx.intra_high * (1.0 - trail))
    elif ctx.pos.side == -1:
        ctx.intra_high = h5
        ctx.intra_low = min(float(ctx.intra_low), l5)
        ctx.set_exit_stop(ctx.intra_low * (1.0 + trail))


# --- Dual Thrust (1m, 日内) ---
def dual_thrust_on_bar(ctx, row: pd.Series, ms: int) -> None:
  import pandas as pd

  st = ctx.state.setdefault(
      "dt",
      {
          "day": "",
          "day_high": 0.0,
          "day_low": 0.0,
          "day_range": 0.0,
          "long_entry": 0.0,
          "short_entry": 0.0,
      },
  )
  ts = pd.Timestamp(ms, unit="ms", tz=ctx.orb_cfg.session_tz)
  day = ts.strftime("%Y-%m-%d")
  if st["day"] != day:
    if st["day"] and st["day_high"] > st["day_low"]:
      st["day_range"] = st["day_high"] - st["day_low"]
      op = float(row["open"])
      st["long_entry"] = op + 0.4 * st["day_range"]
      st["short_entry"] = op - 0.6 * st["day_range"]
    st["day"] = day
    st["day_high"] = float(row["high"])
    st["day_low"] = float(row["low"])
  else:
    st["day_high"] = max(st["day_high"], float(row["high"]))
    st["day_low"] = min(st["day_low"], float(row["low"]))

  if not st.get("day_range"):
    return
  if ts.hour > 15 or (ts.hour == 15 and ts.minute >= 55):
    if ctx.pos.side != 0:
      ctx.close(float(row["close"]), ms=ms, outcome="eod")
    ctx.pending.clear()
    return
  if ctx.pos.side == 0:
    if float(row["close"]) > float(row["open"]):
      ctx.set_entry_stops(st["long_entry"], 0)
    else:
      ctx.set_entry_stops(0, st["short_entry"])
  elif ctx.pos.side > 0:
    ctx.set_exit_stop(st["short_entry"])
  elif ctx.pos.side < 0:
    ctx.set_exit_stop(st["long_entry"])


# --- Turtle (1m/持仓跨日) ---
def turtle_on_bar(ctx, row: pd.Series, ms: int) -> None:
  df = _push_bar(ctx, row)
  if df is None or len(df) < 25:
    return
  entry_up = ind.donchian_high(df["high"], 20)
  entry_dn = ind.donchian_low(df["low"], 20)
  exit_up = ind.donchian_high(df["high"], 10)
  exit_dn = ind.donchian_low(df["low"], 10)
  atr_v = ind.atr(df, 20)
  eu, ed, xu, xd, a = float(entry_up.iloc[-2]), float(entry_dn.iloc[-2]), float(exit_up.iloc[-1]), float(exit_dn.iloc[-1]), float(atr_v.iloc[-1])
  if ctx.pos.side == 0:
    ctx.set_entry_stops(eu, ed)
    ctx.state["turtle_atr"] = a
  elif ctx.pos.side > 0:
    entry = float(ctx.pos.entry)
    stop = max(entry - 2 * a, xd)
    ctx.set_exit_stop(stop)
    ctx.set_entry_stops(eu, 0)
  elif ctx.pos.side < 0:
    entry = float(ctx.pos.entry)
    stop = min(entry + 2 * a, xu)
    ctx.set_exit_stop(stop)
    ctx.set_entry_stops(0, ed)


CTA_STRATEGIES: Dict[str, Dict[str, Any]] = {
    "double_ma": {
        "title": "双均线金叉死叉",
        "fn": double_ma_on_bar,
        "interval": "1m",
        "warmup": 25,
        "eod_flat": False,
    },
    "atr_rsi": {
        "title": "ATR放大 + RSI极端",
        "fn": atr_rsi_on_bar,
        "interval": "1m",
        "warmup": 35,
        "eod_flat": False,
    },
    "boll_channel": {
        "title": "布林通道 + CCI + ATR止损",
        "fn": boll_channel_on_bar,
        "interval": "1m",
        "warmup": 40,
        "eod_flat": False,
    },
    "king_keltner": {
        "title": "肯特纳通道突破",
        "fn": king_keltner_on_bar,
        "interval": "1m",
        "warmup": 25,
        "eod_flat": False,
        "cta_overrides": {
            "entry_stop_sl_pct": 0.0,
            "entry_risk_sl_pct": 0.008,
            "bar_intra_update": False,
            "entry_fee_mode": "stop",
            "slip_bps_entry": 5.0,
            "slip_bps_exit": 5.0,
        },
    },
    "dual_thrust": {
        "title": "Dual Thrust 日内突破",
        "fn": dual_thrust_on_bar,
        "interval": "1m",
        "warmup": 5,
        "eod_flat": True,
    },
    "turtle": {
        "title": "海龟唐奇安通道",
        "fn": turtle_on_bar,
        "interval": "1m",
        "warmup": 25,
        "eod_flat": False,
    },
}


def list_strategies() -> list[str]:
    return list(CTA_STRATEGIES.keys())


def cta_config_for_strategy(strategy_key: str, **overrides) -> "CtaBacktestConfig":
    """按策略元数据构建 CtaBacktestConfig（含 vnpy 对齐 overrides）。"""
    from orb.cta.engine import CtaBacktestConfig

    meta = CTA_STRATEGIES[strategy_key]
    base = {
        "equity_usdt": 1000.0,
        "risk_pct": 0.01,
        "compound": True,
        "eod_flat": bool(meta.get("eod_flat")),
    }
    base.update(meta.get("cta_overrides") or {})
    base.update(overrides)
    return CtaBacktestConfig(**base)
