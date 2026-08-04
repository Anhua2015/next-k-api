# -*- coding: utf-8 -*-
"""live_only + copy_current=off must not orphan-catch mid-book inventory."""

from __future__ import annotations

import unittest
from unittest import mock

from utils import hl_bitget_executor as ex


class CopyCurrentGateTests(unittest.TestCase):
    def test_skips_orphan_open_when_flat(self):
        bot = {"id": "bot_j", "live_only": True, "copy_current": False}
        desired = {"ZECUSDT": 4.0}
        open_pos: dict[str, float] = {}
        rows = [
            {
                "coin": "ZEC",
                "start_position": 100.0,  # mid-book add
                "dir": "Open Long",
            }
        ]
        with mock.patch.object(ex, "hl_coin_to_bitget", return_value="ZECUSDT"):
            out = ex._gate_desired_no_copy_current(
                bot, desired, open_pos, rows, account_id="J"
            )
        self.assertEqual(out, {})

    def test_allows_fresh_open(self):
        bot = {"id": "bot_j", "live_only": True, "copy_current": False}
        desired = {"ZECUSDT": 4.0}
        open_pos: dict[str, float] = {}
        rows = [{"coin": "ZEC", "start_position": 0.0, "dir": "Open Long"}]
        with mock.patch.object(ex, "hl_coin_to_bitget", return_value="ZECUSDT"):
            out = ex._gate_desired_no_copy_current(
                bot, desired, open_pos, rows, account_id="J"
            )
        self.assertEqual(out.get("ZECUSDT"), 4.0)

    def test_syncs_existing_leg(self):
        bot = {"id": "bot_j", "live_only": True, "copy_current": False}
        desired = {"ZECUSDT": 5.0}
        open_pos = {"ZECUSDT": 2.0}
        out = ex._gate_desired_no_copy_current(
            bot, desired, open_pos, rows=[], account_id="J"
        )
        self.assertEqual(out.get("ZECUSDT"), 5.0)


if __name__ == "__main__":
    unittest.main()
