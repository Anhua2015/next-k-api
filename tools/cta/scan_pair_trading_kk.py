#!/usr/bin/env python3
"""KK 池 Pair Trading 协整 + 简易收益扫描（日频，无手续费）。"""
from __future__ import annotations

import sys
import warnings
from itertools import combinations
from pathlib import Path

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from env_loader import load_env_oi  # noqa: E402

load_env_oi()

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import statsmodels.api as sm  # noqa: E402

from orb.core.kline_cache import load_klines, norm_symbol  # noqa: E402
from orb.core.symbols import parse_symbol_list  # noqa: E402
from orb.kk.paths import resolve_symbols_path  # noqa: E402


def daily_close(sym: str) -> pd.Series:
    df = load_klines(sym, "1m")
    if df is None or df.empty:
        return pd.Series(dtype=float)
    df = df.copy()
    ts = pd.to_datetime(df["open_time"], unit="ms", utc=True).dt.tz_convert("America/New_York")
    df["day"] = ts.dt.normalize()
    return df.groupby("day")["close"].last().sort_index()


def eg_method(x: pd.Series, y: pd.Series) -> tuple[bool, object | None]:
    model1 = sm.OLS(y, sm.add_constant(x)).fit()
    eps = model1.resid
    if sm.tsa.stattools.adfuller(eps)[1] > 0.05:
        return False, model1
    x_dif = sm.add_constant(pd.concat([x.diff(), eps.shift(1)], axis=1).dropna())
    y_dif = y.diff().dropna()
    model2 = sm.OLS(y_dif, x_dif).fit()
    if list(model2.params)[-1] > 0:
        return False, model1
    return True, model1


def pair_backtest(s: pd.DataFrame, *, bandwidth: int) -> dict | None:
    if len(s) < bandwidth + 10:
        return None
    capital = 20_000.0
    pos1 = pos2 = 0
    cash1 = cash2 = capital
    shares1 = shares2 = 0
    trades = 0
    equity: list[float] = []

    for i in range(bandwidth, len(s)):
        window = s.iloc[i - bandwidth : i]
        coint, model = eg_method(window.iloc[:, 0], window.iloc[:, 1])
        px1, px2 = float(s.iloc[i, 0]), float(s.iloc[i, 1])
        if not coint:
            if pos1 or pos2:
                cash1 += pos1 * px1 * shares1
                cash2 += pos2 * px2 * shares2
                pos1 = pos2 = 0
                trades += 1
            equity.append(cash1 + cash2)
            continue
        fitted = float(model.params.iloc[0] + model.params.iloc[1] * px1)
        z = (px2 - fitted - np.mean(model.resid)) / np.std(model.resid)
        if pos1 == 0 and pos2 == 0 and abs(z) > 1:
            shares1 = max(1, int(capital // px1))
            shares2 = max(1, int(capital // px2))
            if z > 1:
                pos1, pos2 = 1, -1
                cash1 -= shares1 * px1
                cash2 += shares2 * px2
            else:
                pos1, pos2 = -1, 1
                cash1 += shares1 * px1
                cash2 -= shares2 * px2
            trades += 1
        eq = cash1 + cash2 + pos1 * shares1 * px1 + pos2 * shares2 * px2
        equity.append(eq)

    if not equity:
        return None
    start_eq = 40_000.0
    end_eq = equity[-1]
    return {
        "ret_pct": round((end_eq / start_eq - 1) * 100, 2),
        "trades": trades,
        "days": len(equity),
        "end_eq": round(end_eq, 2),
    }


def main() -> None:
    lo = pd.Timestamp("2026-02-01", tz="America/New_York")
    hi = pd.Timestamp("2026-06-30", tz="America/New_York")
    symbols = [
        norm_symbol(s)
        for s in parse_symbol_list(Path(resolve_symbols_path()).read_text(encoding="utf-8"))
    ]
    closes = {s: daily_close(s) for s in symbols}

    print("=== Pair Trading | KK pool | daily | 2026-02..06 | EG z=1 | no fees ===")
    rows: list[tuple] = []
    for a, b in combinations(symbols, 2):
        s = pd.concat([closes[a], closes[b]], axis=1, join="inner").dropna()
        s = s[(s.index >= lo) & (s.index <= hi)]
        s.columns = ["x", "y"]
        la, lb = a.replace("USDT", ""), b.replace("USDT", "")
        if len(s) < 40:
            print(f"{la}/{lb}: insufficient overlap days={len(s)}")
            continue
        bw = min(60, max(20, len(s) // 2))
        coint_full, model = eg_method(s["x"], s["y"])
        bt = pair_backtest(s, bandwidth=bw)
        adf_p = round(float(sm.tsa.stattools.adfuller(model.resid)[1]), 4)
        ret = bt["ret_pct"] if bt else None
        tr = bt["trades"] if bt else 0
        rows.append((la, lb, coint_full, ret, tr, adf_p, len(s), bw))
        ret_s = f"{ret:+.2f}%" if ret is not None else "n/a"
        print(
            f"{la:4s}/{lb:4s}  days={len(s):3d}  bw={bw:2d}  coint={coint_full!s:5s}  "
            f"adf_p={adf_p:.4f}  ret={ret_s:>8s}  trades={tr}"
        )

    valid = [r for r in rows if r[3] is not None]
    if valid:
        avg = sum(r[3] for r in valid) / len(valid)
        pos = sum(1 for r in valid if r[3] > 0)
        print(f"\nSummary: {len(valid)} pairs | avg ret {avg:+.2f}% on 40k | positive {pos}/{len(valid)}")


if __name__ == "__main__":
    main()
