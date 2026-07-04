"""KK EOD 强平逻辑测试。"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

import pandas as pd

from orb.core.config import OrbConfig
from orb.kk.eod import effective_eod_hm, should_eod_flat_bar


class TestKkEod(unittest.TestCase):
    def _cfg(self) -> OrbConfig:
        return OrbConfig.from_env()

    def test_effective_eod_early_close_day(self):
        # 2026-07-03 提前收市 13:00 ET
        ms = int(pd.Timestamp("2026-07-03 12:00:00", tz="America/New_York").value // 1_000_000)
        cfg = self._cfg()
        h, m = effective_eod_hm(
            bar_ms=ms,
            session_tz=cfg.session_tz,
            session_open_time=cfg.session_open_time,
            session_close_time=cfg.session_close_time,
            market=cfg.market,
            exit_hour=15,
            exit_minute=55,
        )
        self.assertEqual((h, m), (13, 0))

    def test_effective_eod_normal_day(self):
        ms = int(pd.Timestamp("2026-06-02 12:00:00", tz="America/New_York").value // 1_000_000)
        cfg = self._cfg()
        h, m = effective_eod_hm(
            bar_ms=ms,
            session_tz=cfg.session_tz,
            session_open_time=cfg.session_open_time,
            session_close_time=cfg.session_close_time,
            market=cfg.market,
            exit_hour=15,
            exit_minute=55,
        )
        self.assertEqual((h, m), (15, 55))

    def test_last_rth_bar_triggers_on_early_close(self):
        cfg = self._cfg()
        # 12:59 ET bar open on 2026-07-03
        ms = int(datetime(2026, 7, 3, 16, 59, tzinfo=timezone.utc).timestamp() * 1000)
        ts = pd.Timestamp(ms, unit="ms", tz=cfg.session_tz)
        self.assertTrue(
            should_eod_flat_bar(
                bar_ms=ms,
                ts=ts,
                cfg=cfg,
                exit_hour=15,
                exit_minute=55,
            )
        )
        ms2 = int(datetime(2026, 7, 3, 16, 58, tzinfo=timezone.utc).timestamp() * 1000)
        ts2 = pd.Timestamp(ms2, unit="ms", tz=cfg.session_tz)
        self.assertFalse(
            should_eod_flat_bar(
                bar_ms=ms2,
                ts=ts2,
                cfg=cfg,
                exit_hour=15,
                exit_minute=55,
            )
        )


if __name__ == "__main__":
    unittest.main()
