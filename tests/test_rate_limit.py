#!/usr/bin/env python3
"""MinIntervalGuard 单元测试。"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_API_ROOT = Path(__file__).resolve().parent.parent
if str(_API_ROOT) not in sys.path:
    sys.path.insert(0, str(_API_ROOT))

from utils.rate_limit import MinIntervalGuard


class RateLimitTests(unittest.TestCase):
    def test_zero_disables_limit(self) -> None:
        with patch.dict(os.environ, {"TEST_COOLDOWN_SEC": "0"}, clear=False):
            g = MinIntervalGuard("TEST_COOLDOWN_SEC", 60.0)
            self.assertTrue(g.check_allow()[0])
            g.mark_used()
            self.assertTrue(g.check_allow()[0])

    def test_blocks_within_interval(self) -> None:
        with patch.dict(os.environ, {"TEST_COOLDOWN_SEC": "60"}, clear=False):
            g = MinIntervalGuard("TEST_COOLDOWN_SEC", 60.0)
            self.assertTrue(g.check_allow()[0])
            g.mark_used()
            allowed, retry = g.check_allow()
            self.assertFalse(allowed)
            self.assertGreater(retry, 0)


if __name__ == "__main__":
    unittest.main()
