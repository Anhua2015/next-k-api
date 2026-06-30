"""entry_resolve_step_ms tests."""

from __future__ import annotations

import unittest

from orb.core.config import OrbConfig
from orb.core.resolve import ENTRY_1M_STEP_MS, entry_resolve_step_ms


class TestResolveStep(unittest.TestCase):
    def test_1m_fill_uses_1m_step(self):
        cfg = OrbConfig(signal_interval="5m")
        # 10:03 ET bar (not on 5m grid)
        self.assertEqual(entry_resolve_step_ms(cfg, 1_000_000 + 180_000), ENTRY_1M_STEP_MS)

    def test_5m_fill_uses_5m_step(self):
        cfg = OrbConfig(signal_interval="5m")
        bar5 = 300_000
        self.assertEqual(entry_resolve_step_ms(cfg, bar5 * 10), bar5)


if __name__ == "__main__":
    unittest.main()
