#!/usr/bin/env python3
"""Tests for pairs.sizing."""

import pandas as pd

from pairs.sizing import p_trace_entry_confident, p_trace_size_scale


def test_p_trace_size_scale_bounds():
    p = pd.Series([0.1, 0.2, 0.5, 1.0, 2.0, 3.0] * 50)
    scale = p_trace_size_scale(p, lookback=60, min_scale=0.25)
    assert float(scale.min()) >= 0.25
    assert float(scale.max()) <= 1.0


def test_p_trace_entry_confident():
    p = pd.Series([0.1, 0.2, 0.15] * 40)
    ok = p_trace_entry_confident(p, lookback=20, halt_pct=80.0)
    assert len(ok) == len(p)
    assert ok.dtype == bool
