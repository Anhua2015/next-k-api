"""GTL core: Yang-Zhang vol, ICS angles, frozen structures, KDE estimate."""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# Documented design constants from TradingView GTL v2
CAL_LAMBDA = 0.999
CAL_JUMPTH = 2.0
CAL_MINW = 30
CAL_ROLL = 20
JUMP_WIN = 5


@dataclass
class ArchivedBreak:
    theta_c: float
    theta_f: float
    break_dir: int
    duration: int


@dataclass
class GtlBarReading:
    bar_index: int
    frozen_hh: float = 0.0
    frozen_ll: float = 0.0
    anchor_index: int = 0
    theta_ceiling: float = 0.0
    theta_floor: float = 0.0
    prob_up: float = 0.5
    prob_down: float = 0.5
    n_eff: float = 0.0
    verified_up: float = 0.5
    verified_down: float = 0.5
    display_prob_up: float = 0.5
    display_prob_down: float = 0.5
    forecast_up: bool = False
    forecast_down: bool = False
    forecast_confidence: str = "low"
    theta_ceiling_display: float = 0.0
    theta_floor_display: float = 0.0
    jumpiness: float = 0.0
    signal_up: bool = False
    signal_down: bool = False
    abstain_reason: str = ""
    trade_abstain_reason: str = ""
    structure_active: bool = False
    pending: bool = False
    break_dir: int = 0
    event: int = 0  # 0 none, 1 birth, 2 break, 3 both
    str_duration: int = 0
    birth_prob_up: float = 0.5
    birth_hit: int = -1  # 1/0 on break bar, else -1
    broken_hh: float = 0.0
    broken_ll: float = 0.0
    # Frozen at structure birth (Log P Birth semantics)
    birth_signal_up: bool = False
    birth_signal_down: bool = False
    birth_gates_ok: bool = False
    birth_verified_up: float = 0.5
    birth_display_prob_up: float = 0.5
    birth_forecast_up: bool = False
    birth_forecast_down: bool = False
    birth_forecast_confidence: str = "low"
    birth_n_eff: float = 0.0
    birth_abstain_reason: str = ""
    birth_trade_abstain_reason: str = ""
    is_birth_bar: bool = False
    break_aligns_birth: bool = False


@dataclass
class _ActiveStructure:
    anchor_index: int
    frozen_hh: float
    frozen_ll: float
    birth_index: int
    birth_theta_c: float = 0.0
    birth_theta_f: float = 0.0
    birth_prob_up: float = 0.5
    birth_steady: bool = True
    birth_signal_up: bool = False
    birth_signal_down: bool = False
    birth_gates_ok: bool = False
    birth_verified_up: float = 0.5
    birth_display_prob_up: float = 0.5
    birth_forecast_up: bool = False
    birth_forecast_down: bool = False
    birth_forecast_confidence: str = "low"
    birth_n_eff: float = 0.0
    birth_abstain_reason: str = ""
    birth_trade_abstain_reason: str = ""


def _theta_display(theta: float) -> float:
    """TradingView-style positive angle magnitude for UI."""
    return abs(float(theta))


def _display_prob(prob_up: float, n_eff: float, verified_up: float, cal_total: float) -> float:
    """TV-aligned display probability: calibrated rate or n_eff-shrinkage toward 50%."""
    if cal_total >= CAL_MINW:
        return float(verified_up)
    weight = min(max(float(n_eff) / CAL_MINW, 0.0), 1.0)
    return 0.5 + (float(prob_up) - 0.5) * weight


def _forecast_confidence(n_eff: float, steady: bool, cal_total: float) -> str:
    if not steady:
        return "low"
    if cal_total >= CAL_MINW or n_eff >= CAL_MINW:
        return "high"
    if n_eff >= CAL_MINW * 0.5:
        return "medium"
    return "low"


def _forecast_direction(
    display_up: float, display_down: float, *, steady: bool, min_edge: float = 0.05
) -> Tuple[bool, bool]:
    if not steady:
        return False, False
    if display_up >= 0.5 + min_edge and display_up > display_down:
        return True, False
    if display_down >= 0.5 + min_edge and display_down > display_up:
        return False, True
    return False, False


def yang_zhang_series(df: pd.DataFrame, window: int = 500) -> pd.Series:
    """Yang-Zhang volatility (annualization omitted — used as ICS scale)."""
    log_o = np.log(df["open"].astype(float))
    log_h = np.log(df["high"].astype(float))
    log_l = np.log(df["low"].astype(float))
    log_c = np.log(df["close"].astype(float))

    ro = log_o - log_c.shift(1)
    rc = log_c - log_o
    ho = log_h - log_o
    lo = log_l - log_o
    co = log_c - log_o
    rs = ho * (ho - co) + lo * (lo - co)

    n = int(window)
    k = 0.34 / (1.34 + (n + 1) / max(n - 1, 1))
    sigma_o2 = ro.rolling(n, min_periods=max(10, n // 5)).var()
    sigma_c2 = rc.rolling(n, min_periods=max(10, n // 5)).var()
    sigma_rs2 = rs.rolling(n, min_periods=max(10, n // 5)).mean()
    sigma2 = sigma_o2 + k * sigma_c2 + (1.0 - k) * sigma_rs2
    return np.sqrt(sigma2.clip(lower=1e-12))


def _ics_y(price: float, sigma: float) -> float:
    sig = max(float(sigma), 1e-12)
    return math.log(max(float(price), 1e-12)) / sig


def _theta(from_y: float, to_y: float, delta_x: float) -> float:
    if delta_x <= 0:
        return 0.0
    return math.degrees(math.atan((to_y - from_y) / delta_x))


def _kish_n_eff(weights: np.ndarray) -> float:
    w = weights.astype(float)
    s = w.sum()
    if s <= 0:
        return 0.0
    return float(s * s / max(np.dot(w, w), 1e-12))


def _silverman_h(values: np.ndarray, n: int) -> float:
    if n <= 1:
        return 1.0
    std = float(np.std(values, ddof=1)) if n > 1 else 1.0
    std = max(std, 1e-6)
    return std * (n ** (-1.0 / 6.0))


def _kde_prob_up(theta_c: float, theta_f: float, archive: List[ArchivedBreak]) -> Tuple[float, float]:
    if not archive:
        return 0.5, 0.0
    tc = np.array([a.theta_c for a in archive], dtype=float)
    tf = np.array([a.theta_f for a in archive], dtype=float)
    dirs = np.array([a.break_dir for a in archive], dtype=float)
    n = len(archive)
    h_c = max(_silverman_h(tc, n), 1.0)
    h_f = max(_silverman_h(tf, n), 1.0)
    w = np.exp(-0.5 * ((tc - theta_c) / h_c) ** 2 - 0.5 * ((theta_f - tf) / h_f) ** 2)
    total = w.sum()
    if total <= 0:
        return 0.5, 0.0
    up_w = w[dirs > 0].sum()
    prob_up = float(up_w / total)
    return prob_up, _kish_n_eff(w)


def _significant(prob: float, n_eff: float, *, alpha: float = 0.05) -> bool:
    if n_eff <= 0:
        return False
    # Normal approx to Beta(1,1) posterior with effective sample size
    se = math.sqrt(max(prob * (1.0 - prob) / n_eff, 1e-12))
    z = (prob - 0.5) / se
    z_crit = 1.645  # one-sided 95%
    return z > z_crit


def _confidence_bin(prob: float) -> int:
    p = min(max(prob, 0.0), 1.0)
    return min(9, int(p * 10))


@dataclass
class _CalCell:
    hits: float = 0.0
    total: float = 0.0

    def rate(self) -> float:
        if self.total <= 0:
            return 0.5
        return self.hits / self.total

    def update(self, hit: bool) -> None:
        self.hits = self.hits * CAL_LAMBDA + (1.0 if hit else 0.0)
        self.total = self.total * CAL_LAMBDA + 1.0


class GtlEngine:
    """Streaming GTL engine for vnpy on_bar loops."""

    def __init__(
        self,
        *,
        lookback: int = 23,
        vol_window: int = 500,
        max_archive: int = 2000,
    ) -> None:
        self.lookback = int(lookback)
        self.vol_window = int(vol_window)
        self.max_archive = int(max_archive)

        self._bar_index = -1
        self._opens: Deque[float] = deque(maxlen=vol_window + 2)
        self._highs: Deque[float] = deque(maxlen=vol_window + 2)
        self._lows: Deque[float] = deque(maxlen=vol_window + 2)
        self._closes: Deque[float] = deque(maxlen=vol_window + 2)

        self._sigma = 0.01
        self._structure: Optional[_ActiveStructure] = None
        self._archive: List[ArchivedBreak] = []
        self._cal: Dict[Tuple[int, int], _CalCell] = {}
        self._prob_hist: Deque[float] = deque(maxlen=JUMP_WIN)
        self._hit_roll_birth: Deque[int] = deque(maxlen=CAL_ROLL)

    def _refresh_sigma(self) -> None:
        n = len(self._closes)
        if n < max(20, self.vol_window // 5):
            return
        df = pd.DataFrame(
            {
                "open": list(self._opens),
                "high": list(self._highs),
                "low": list(self._lows),
                "close": list(self._closes),
            }
        )
        sig = yang_zhang_series(df, window=min(self.vol_window, n))
        val = float(sig.iloc[-1])
        if math.isfinite(val) and val > 0:
            self._sigma = val

    def _window_hh_ll(self) -> Tuple[float, float, int]:
        lb = self.lookback
        highs = list(self._highs)[-lb:]
        lows = list(self._lows)[-lb:]
        hh = max(highs)
        ll = min(lows)
        anchor = self._bar_index - lb + 1
        return hh, ll, anchor

    def _angles(self, hh: float, ll: float, anchor_index: int, high: float, low: float) -> Tuple[float, float]:
        delta_x = (self._bar_index - anchor_index) / float(self.lookback)
        if delta_x <= 0:
            return 0.0, 0.0
        y_hh = _ics_y(hh, self._sigma)
        y_ll = _ics_y(ll, self._sigma)
        y_hi = _ics_y(high, self._sigma)
        y_lo = _ics_y(low, self._sigma)
        theta_c = _theta(y_hh, y_hi, delta_x)
        theta_f = _theta(y_ll, y_lo, delta_x)
        return theta_c, theta_f

    def _cell_key(self, prob_up: float, steady: bool) -> Tuple[int, int]:
        return _confidence_bin(prob_up), 1 if steady else 0

    def _cal_cell(self, prob_up: float, steady: bool) -> _CalCell:
        return self._cal.setdefault(self._cell_key(prob_up, steady), _CalCell())

    def _verified(self, prob_up: float, steady: bool) -> Tuple[float, float]:
        cell = self._cal_cell(prob_up, steady)
        if cell.total < CAL_MINW:
            return prob_up, 1.0 - prob_up
        rate_up = cell.rate()
        return rate_up, 1.0 - rate_up

    def _apply_forecast_layer(
        self,
        out: GtlBarReading,
        *,
        prob_up: float,
        n_eff: float,
        steady: bool,
        sig_up: bool,
        sig_down: bool,
        trade_reason: str,
        verified_up: float,
        verified_down: float,
    ) -> None:
        cell = self._cal_cell(prob_up, steady)
        disp_up = _display_prob(prob_up, n_eff, verified_up, cell.total)
        disp_down = 1.0 - disp_up
        conf = _forecast_confidence(n_eff, steady, cell.total)
        fc_up, fc_down = _forecast_direction(disp_up, disp_down, steady=steady)
        out.display_prob_up = disp_up
        out.display_prob_down = disp_down
        out.forecast_confidence = conf
        out.forecast_up = fc_up
        out.forecast_down = fc_down
        out.trade_abstain_reason = trade_reason
        out.abstain_reason = "" if (fc_up or fc_down) else (trade_reason or "uncertain")

    def _grade_birth(self, hit: bool, prob_up: float, steady: bool) -> None:
        key = self._cell_key(prob_up, steady)
        cell = self._cal.setdefault(key, _CalCell())
        cell.update(hit)
        self._hit_roll_birth.append(1 if hit else 0)

    def _evaluate_gates(
        self, prob_up: float, n_eff: float, steady: bool
    ) -> Tuple[bool, bool, str, float, float]:
        verified_up, verified_down = self._verified(prob_up, steady)
        prob_down = 1.0 - prob_up
        sig_up = sig_down = False
        reason = ""
        if not steady:
            reason = "choppy"
        elif n_eff < CAL_MINW:
            reason = "low_n_eff"
        elif not _significant(prob_up, n_eff) and not _significant(prob_down, n_eff):
            reason = "not_significant"
        else:
            if prob_up > prob_down and _significant(prob_up, n_eff):
                sig_up = True
            elif prob_down > prob_up and _significant(prob_down, n_eff):
                sig_down = True
            else:
                reason = "not_significant"
        return sig_up, sig_down, reason, verified_up, verified_down

    def _birth_structure(self) -> _ActiveStructure:
        hh, ll, anchor = self._window_hh_ll()
        tc, tf = self._angles(hh, ll, anchor, float(self._highs[-1]), float(self._lows[-1]))
        prob_up, n_eff = _kde_prob_up(tc, tf, self._archive)
        jump = float(np.std(list(self._prob_hist))) if len(self._prob_hist) >= 2 else 0.0
        steady = jump < CAL_JUMPTH
        sig_up, sig_down, trade_reason, verified_up, _ = self._evaluate_gates(prob_up, n_eff, steady)
        gates_ok = sig_up or sig_down
        cell = self._cal_cell(prob_up, steady)
        disp_up = _display_prob(prob_up, n_eff, verified_up, cell.total)
        conf = _forecast_confidence(n_eff, steady, cell.total)
        fc_up, fc_down = _forecast_direction(disp_up, 1.0 - disp_up, steady=steady)
        return _ActiveStructure(
            anchor_index=anchor,
            frozen_hh=hh,
            frozen_ll=ll,
            birth_index=self._bar_index,
            birth_theta_c=tc,
            birth_theta_f=tf,
            birth_prob_up=prob_up,
            birth_steady=steady,
            birth_signal_up=sig_up,
            birth_signal_down=sig_down,
            birth_gates_ok=gates_ok,
            birth_verified_up=verified_up,
            birth_display_prob_up=disp_up,
            birth_forecast_up=fc_up,
            birth_forecast_down=fc_down,
            birth_forecast_confidence=conf,
            birth_n_eff=n_eff,
            birth_abstain_reason="" if (fc_up or fc_down) else (trade_reason or "uncertain"),
            birth_trade_abstain_reason="" if gates_ok else trade_reason,
        )

    def _archive_break(self, st: _ActiveStructure, theta_c: float, theta_f: float, break_dir: int) -> None:
        rec = ArchivedBreak(
            theta_c=theta_c,
            theta_f=theta_f,
            break_dir=break_dir,
            duration=self._bar_index - st.birth_index,
        )
        self._archive.append(rec)
        if len(self._archive) > self.max_archive:
            self._archive = self._archive[-self.max_archive :]

        hit = (break_dir > 0 and st.birth_signal_up) or (break_dir < 0 and st.birth_signal_down)
        if st.birth_gates_ok:
            self._grade_birth(hit, st.birth_prob_up, st.birth_steady)

    def update(self, open_: float, high: float, low: float, close: float) -> GtlBarReading:
        self._bar_index += 1
        self._opens.append(float(open_))
        self._highs.append(float(high))
        self._lows.append(float(low))
        self._closes.append(float(close))
        self._refresh_sigma()

        out = GtlBarReading(bar_index=self._bar_index)
        if len(self._closes) < self.lookback:
            out.abstain_reason = "warmup"
            return out

        event = 0
        if self._structure is None:
            self._structure = self._birth_structure()
            event = 1

        st = self._structure
        assert st is not None
        theta_c, theta_f = self._angles(st.frozen_hh, st.frozen_ll, st.anchor_index, high, low)
        prob_up, n_eff = _kde_prob_up(theta_c, theta_f, self._archive)
        prob_down = 1.0 - prob_up
        jump = float(np.std(list(self._prob_hist))) if len(self._prob_hist) >= 2 else 0.0
        steady = jump < CAL_JUMPTH
        sig_up, sig_down, reason, verified_up, verified_down = self._evaluate_gates(prob_up, n_eff, steady)
        self._prob_hist.append(prob_up)

        out.frozen_hh = st.frozen_hh
        out.frozen_ll = st.frozen_ll
        out.anchor_index = st.anchor_index
        out.theta_ceiling = theta_c
        out.theta_floor = theta_f
        out.theta_ceiling_display = _theta_display(theta_c)
        out.theta_floor_display = _theta_display(theta_f)
        out.prob_up = prob_up
        out.prob_down = prob_down
        out.n_eff = n_eff
        out.verified_up = verified_up
        out.verified_down = verified_down
        out.jumpiness = jump
        out.signal_up = sig_up
        out.signal_down = sig_down
        self._apply_forecast_layer(
            out,
            prob_up=prob_up,
            n_eff=n_eff,
            steady=steady,
            sig_up=sig_up,
            sig_down=sig_down,
            trade_reason=reason,
            verified_up=verified_up,
            verified_down=verified_down,
        )
        out.structure_active = True
        out.event = event
        out.is_birth_bar = event in (1, 3)
        out.birth_prob_up = st.birth_prob_up
        out.birth_signal_up = st.birth_signal_up
        out.birth_signal_down = st.birth_signal_down
        out.birth_gates_ok = st.birth_gates_ok
        out.birth_verified_up = st.birth_verified_up
        out.birth_display_prob_up = st.birth_display_prob_up
        out.birth_forecast_up = st.birth_forecast_up
        out.birth_forecast_down = st.birth_forecast_down
        out.birth_forecast_confidence = st.birth_forecast_confidence
        out.birth_n_eff = st.birth_n_eff
        out.birth_abstain_reason = st.birth_abstain_reason
        out.birth_trade_abstain_reason = st.birth_trade_abstain_reason

        break_dir = 0
        if close > st.frozen_hh:
            break_dir = 1
        elif close < st.frozen_ll:
            break_dir = -1

        if break_dir != 0:
            out.break_dir = break_dir
            out.str_duration = self._bar_index - st.birth_index
            out.broken_hh = st.frozen_hh
            out.broken_ll = st.frozen_ll
            out.break_aligns_birth = st.birth_gates_ok and (
                (break_dir > 0 and st.birth_signal_up) or (break_dir < 0 and st.birth_signal_down)
            )
            out.birth_hit = 1 if out.break_aligns_birth else (0 if st.birth_gates_ok else -1)
            self._archive_break(st, theta_c, theta_f, break_dir)
            self._structure = self._birth_structure()
            out.event = 3  # break + birth of next structure on same bar
            out.is_birth_bar = True
            st2 = self._structure
            theta_c2, theta_f2 = self._angles(
                st2.frozen_hh, st2.frozen_ll, st2.anchor_index, high, low
            )
            prob_up2, n_eff2 = _kde_prob_up(theta_c2, theta_f2, self._archive)
            out.frozen_hh = st2.frozen_hh
            out.frozen_ll = st2.frozen_ll
            out.anchor_index = st2.anchor_index
            out.birth_prob_up = st2.birth_prob_up
            out.birth_signal_up = st2.birth_signal_up
            out.birth_signal_down = st2.birth_signal_down
            out.birth_gates_ok = st2.birth_gates_ok
            out.birth_verified_up = st2.birth_verified_up
            out.birth_display_prob_up = st2.birth_display_prob_up
            out.birth_forecast_up = st2.birth_forecast_up
            out.birth_forecast_down = st2.birth_forecast_down
            out.birth_forecast_confidence = st2.birth_forecast_confidence
            out.birth_n_eff = st2.birth_n_eff
            out.birth_abstain_reason = st2.birth_abstain_reason
            out.birth_trade_abstain_reason = st2.birth_trade_abstain_reason
            out.theta_ceiling = theta_c2
            out.theta_floor = theta_f2
            out.theta_ceiling_display = _theta_display(theta_c2)
            out.theta_floor_display = _theta_display(theta_f2)
            out.prob_up = prob_up2
            out.prob_down = 1.0 - prob_up2
            out.n_eff = n_eff2
            sig2_up, sig2_down, reason2, v2_up, v2_down = self._evaluate_gates(prob_up2, n_eff2, st2.birth_steady)
            out.signal_up = sig2_up
            out.signal_down = sig2_down
            out.verified_up = v2_up
            out.verified_down = v2_down
            self._apply_forecast_layer(
                out,
                prob_up=prob_up2,
                n_eff=n_eff2,
                steady=st2.birth_steady,
                sig_up=sig2_up,
                sig_down=sig2_down,
                trade_reason=reason2,
                verified_up=v2_up,
                verified_down=v2_down,
            )

        return out


def compute_gtl_dataframe(
    df: pd.DataFrame,
    *,
    lookback: int = 23,
    vol_window: int = 500,
) -> pd.DataFrame:
    """Batch compute GTL columns for validation / research."""
    engine = GtlEngine(lookback=lookback, vol_window=vol_window)
    rows: List[Dict[str, object]] = []
    for _, row in df.iterrows():
        r = engine.update(float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"]))
        rows.append(
            {
                "open_time": int(row.get("open_time", 0)),
                "frozen_hh": r.frozen_hh,
                "frozen_ll": r.frozen_ll,
                "theta_ceiling": r.theta_ceiling,
                "theta_floor": r.theta_floor,
                "theta_ceiling_display": r.theta_ceiling_display,
                "theta_floor_display": r.theta_floor_display,
                "prob_up": r.prob_up,
                "prob_down": r.prob_down,
                "n_eff": r.n_eff,
                "verified_up": r.verified_up,
                "verified_down": r.verified_down,
                "display_prob_up": r.display_prob_up,
                "display_prob_down": r.display_prob_down,
                "forecast_up": r.forecast_up,
                "forecast_down": r.forecast_down,
                "forecast_confidence": r.forecast_confidence,
                "jumpiness": r.jumpiness,
                "signal_up": r.signal_up,
                "signal_down": r.signal_down,
                "trade_abstain_reason": r.trade_abstain_reason,
                "break_dir": r.break_dir,
                "str_duration": r.str_duration,
                "log_event": r.event,
                "abstain_reason": r.abstain_reason,
                "birth_prob_up": r.birth_prob_up,
                "birth_hit": r.birth_hit,
                "broken_hh": r.broken_hh,
                "broken_ll": r.broken_ll,
                "birth_signal_up": r.birth_signal_up,
                "birth_signal_down": r.birth_signal_down,
                "birth_gates_ok": r.birth_gates_ok,
                "birth_verified_up": r.birth_verified_up,
                "birth_display_prob_up": r.birth_display_prob_up,
                "birth_forecast_up": r.birth_forecast_up,
                "birth_forecast_down": r.birth_forecast_down,
                "birth_forecast_confidence": r.birth_forecast_confidence,
                "birth_n_eff": r.birth_n_eff,
                "birth_trade_abstain_reason": r.birth_trade_abstain_reason,
                "is_birth_bar": r.is_birth_bar,
                "break_aligns_birth": r.break_aligns_birth,
            }
        )
    return pd.DataFrame(rows)
