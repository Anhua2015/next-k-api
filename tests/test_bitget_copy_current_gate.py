# -*- coding: utf-8 -*-
"""live_only + copy_current=off must not orphan-catch mid-book inventory."""

from __future__ import annotations

import unittest
from unittest import mock

from utils import hl_bitget_executor as ex


class CopyCurrentGateTests(unittest.TestCase):
    def setUp(self) -> None:
        with ex._pending_fresh_lock:
            ex._pending_fresh_opens.clear()
        ex._pending_fresh_loaded = True  # skip disk load in unit tests
        self._persist_patch = mock.patch.object(ex, "_persist_pending_fresh_opens")
        self._persist_patch.start()
        self.addCleanup(self._persist_patch.stop)

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

    def test_missing_start_position_does_not_count_as_fresh(self):
        """Open* without startPosition must not catch up mid-book inventory."""
        bot = {
            "id": "bot_c",
            "live_only": True,
            "copy_current": False,
            "target_positions": {"ETH": {"sz": -10.0}},
        }
        desired = {"ETHUSDT": 1.0}
        open_pos: dict[str, float] = {}
        rows = [{"coin": "ETH", "dir": "Open Short"}]  # no start_position
        with mock.patch.object(ex, "hl_coin_to_bitget", return_value="ETHUSDT"):
            out = ex._gate_desired_no_copy_current(
                bot, desired, open_pos, rows, account_id="C"
            )
        self.assertEqual(out, {})

    def test_pending_fresh_survives_later_midbook_add(self):
        """J miss mode: open place failed, later add must still be allowed."""
        bot = {
            "id": "bot_c",
            "live_only": True,
            "copy_current": False,
            "target_positions": {"ZEC": {"sz": 200.0}},
        }
        with mock.patch.object(ex, "hl_coin_to_bitget", return_value="ZECUSDT"):
            # First batch: true flat→open (marks pending even if place later fails)
            ex._gate_desired_no_copy_current(
                bot,
                {"ZECUSDT": 4.0},
                {},
                [{"coin": "ZEC", "start_position": 0.0, "dir": "Open Long"}],
                account_id="C",
            )
            # Later batch: only mid-book add — must NOT orphan-skip
            out = ex._gate_desired_no_copy_current(
                bot,
                {"ZECUSDT": 5.0},
                {},
                [{"coin": "ZEC", "start_position": 100.0, "dir": "Open Long"}],
                account_id="C",
            )
        self.assertEqual(out.get("ZECUSDT"), 5.0)

    def test_want_zero_glitch_does_not_clear_pending(self):
        bot = {
            "id": "bot_c",
            "live_only": True,
            "copy_current": False,
            "target_positions": {"ZEC": {"sz": 100.0}},
        }
        with mock.patch.object(ex, "hl_coin_to_bitget", return_value="ZECUSDT"):
            ex._gate_desired_no_copy_current(
                bot,
                {"ZECUSDT": 4.0},
                {},
                [{"coin": "ZEC", "start_position": 0.0, "dir": "Open Long"}],
                account_id="C",
            )
            # Sizing blip: desired empty but leader still holds
            ex._gate_desired_no_copy_current(
                bot, {}, {}, rows=[], account_id="C"
            )
            out = ex._gate_desired_no_copy_current(
                bot,
                {"ZECUSDT": 5.0},
                {},
                [{"coin": "ZEC", "start_position": 50.0, "dir": "Open Long"}],
                account_id="C",
            )
        self.assertEqual(out.get("ZECUSDT"), 5.0)

    def test_holds_existing_leg_without_fill_signal(self):
        """Mature: no leader fill in batch → do not top-up on ratio chase."""
        bot = {"id": "bot_j", "live_only": True, "copy_current": False}
        desired = {"ZECUSDT": 5.0}
        open_pos = {"ZECUSDT": 2.0}
        out = ex._gate_desired_no_copy_current(
            bot, desired, open_pos, rows=[], account_id="J"
        )
        self.assertEqual(out.get("ZECUSDT"), 2.0)

    def test_size_up_when_leader_fill_delta_present(self):
        bot = {"id": "bot_j", "live_only": True, "copy_current": False}
        rows = [
            {
                "action": "live_sync",
                "coin": "ZEC",
                "target_delta": 50.0,
                "dir": "Open Long",
                "start_position": 100.0,
            }
        ]
        with mock.patch.object(ex, "hl_coin_to_bitget", return_value="ZECUSDT"):
            out = ex._gate_desired_no_copy_current(
                bot,
                {"ZECUSDT": 5.0},
                {"ZECUSDT": 2.0},
                rows,
                account_id="J",
            )
        self.assertEqual(out.get("ZECUSDT"), 5.0)

    def test_av_drift_shrink_holds_without_leader_reduce(self):
        """Leader coin unchanged → must not cut Bitget on AV/ratio shrink."""
        bot = {
            "id": "bot_j",
            "live_only": True,
            "copy_current": False,
            "target_positions": {"ZEC": {"sz": 200.0}},
        }
        out = ex._gate_desired_no_copy_current(
            bot,
            {"ZECUSDT": 1.0},
            {"ZECUSDT": 2.0},
            rows=[],
            account_id="J",
        )
        self.assertEqual(out.get("ZECUSDT"), 2.0)

    def test_reduce_when_leader_flat(self):
        bot = {
            "id": "bot_j",
            "live_only": True,
            "copy_current": False,
            "target_positions": {},
        }
        out = ex._gate_desired_no_copy_current(
            bot,
            {"ZECUSDT": 0.0},
            {"ZECUSDT": 2.0},
            rows=[],
            account_id="J",
        )
        self.assertEqual(out.get("ZECUSDT"), 0.0)

    def test_reduce_when_leader_reduce_fill(self):
        bot = {
            "id": "bot_j",
            "live_only": True,
            "copy_current": False,
            "target_positions": {"ZEC": {"sz": 50.0}},
        }
        rows = [
            {
                "action": "live_sync",
                "coin": "ZEC",
                "target_delta": -50.0,
                "dir": "Close Long",
                "start_position": 100.0,
            }
        ]
        with mock.patch.object(ex, "hl_coin_to_bitget", return_value="ZECUSDT"):
            out = ex._gate_desired_no_copy_current(
                bot,
                {"ZECUSDT": 1.0},
                {"ZECUSDT": 2.0},
                rows,
                account_id="J",
            )
        self.assertEqual(out.get("ZECUSDT"), 1.0)

    def test_reduce_fill_does_not_unlock_ratio_top_up(self):
        """A leader reduce in-batch must not allow chasing a larger desired."""
        bot = {"id": "bot_j", "live_only": True, "copy_current": False}
        rows = [
            {
                "action": "live_sync",
                "coin": "ZEC",
                "target_delta": -10.0,  # reduce long
                "dir": "Close Long",
                "start_position": 100.0,
            }
        ]
        with mock.patch.object(ex, "hl_coin_to_bitget", return_value="ZECUSDT"):
            out = ex._gate_desired_no_copy_current(
                bot,
                {"ZECUSDT": 5.0},  # ratio wants larger
                {"ZECUSDT": 2.0},
                rows,
                account_id="J",
            )
        self.assertEqual(out.get("ZECUSDT"), 2.0)

    def test_catch_up_opens_orphan_without_cutting_held(self):
        """Synthetic catch_up row opens GOOGL; BTC hold despite smaller want."""
        bot = {
            "id": "bot_c",
            "live_only": True,
            "copy_current": False,
            "target_positions": {
                "BTC": {"sz": -15.0},
                "xyz:GOOGL": {"sz": 2800.0},
            },
        }
        desired = {"BTCUSDT": -0.04, "GOOGLUSDT": 8.0}
        open_pos = {"BTCUSDT": -0.05}
        rows = [
            {
                "action": "catch_up",
                "coin": "xyz:GOOGL",
                "start_position": 0.0,
                "dir": "Open Long",
            }
        ]

        def _map(coin, route_coins=None):
            c = str(coin or "").upper()
            if "GOOGL" in c or c.endswith("GOOGL"):
                return "GOOGLUSDT"
            if "BTC" in c:
                return "BTCUSDT"
            return None

        with mock.patch.object(ex, "hl_coin_to_bitget", side_effect=_map):
            out = ex._gate_desired_no_copy_current(
                bot, desired, open_pos, rows, account_id="C"
            )
        self.assertEqual(out.get("GOOGLUSDT"), 8.0)
        self.assertEqual(out.get("BTCUSDT"), -0.05)


class StampStartPositionTests(unittest.TestCase):
    def test_infers_pre_from_snap_batch(self):
        from utils.hl_paper_copy import _stamp_leader_start_positions

        snap = {
            "positions": [{"coin": "ETH", "szi": 5.0}],
        }
        # One open of +5 → post=5 ⇒ pre=0
        fresh = [
            {
                "coin": "ETH",
                "target_delta": 5.0,
                "fill_time": 1,
                "tid": "a",
                "dir": "Open Long",
                "start_position": None,
            }
        ]
        with mock.patch(
            "utils.hl_paper_copy._target_coin_szi", return_value=5.0
        ):
            out = _stamp_leader_start_positions(fresh, snap)
        self.assertEqual(out[0].get("start_position"), 0.0)


if __name__ == "__main__":
    unittest.main()
