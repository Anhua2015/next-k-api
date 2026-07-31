# -*- coding: utf-8 -*-
"""Tiny first-clip in an Open burst must not leave a small seat flat."""

from __future__ import annotations

import unittest

from utils import hl_paper_copy as pc


class FlatEntryCoalesceTests(unittest.TestCase):
    def test_coalesce_merges_same_sign_while_flat(self):
        bot = {"id": "bot_o", "positions": {}}
        items = [
            {
                "coin": "BTC",
                "target_delta": -0.01595,
                "px": 64390.0,
                "tid": "t1",
                "dir": "Open Short",
            },
            {
                "coin": "BTC",
                "target_delta": -2.1764,
                "px": 64395.0,
                "tid": "t2",
                "dir": "Open Short",
            },
            {
                "coin": "BTC",
                "target_delta": -1.0,
                "px": 64400.0,
                "tid": "t3",
                "dir": "Open Short",
            },
        ]
        out = pc._coalesce_flat_entry_fills(bot, items)
        self.assertEqual(len(out), 1)
        self.assertAlmostEqual(out[0]["target_delta"], -3.19235)
        self.assertEqual(out[0]["coalesced_n"], 3)
        self.assertEqual(out[0]["extra_tids"], ["t1", "t2", "t3"])

    def test_small_seat_opens_after_coalesce_not_dust(self):
        """Reproduce K/O: first clip alone is dust on 100U; coalesced is not."""
        cfg = pc.paper_config()
        cfg = dict(cfg)
        cfg["min_notional"] = 10.0
        bot = {
            "id": "bot_o",
            "balance": 100.0,
            "equity": 100.0,
            "paper_balance": 100.0,
            "target_av": 23000.0,
            "positions": {},
            "fills": [],
            "realized_pnl": 0.0,
        }
        ratio = 100.0 / 23000.0
        mids = {"BTC": 64390.0}
        items = [
            {
                "coin": "BTC",
                "target_delta": -0.01595,
                "px": 64390.0,
                "tid": "t1",
                "dir": "Open Short",
                "start_position": 0.0,
            },
            {
                "coin": "BTC",
                "target_delta": -2.1764,
                "px": 64390.0,
                "tid": "t2",
                "dir": "Open Short",
            },
            {
                "coin": "BTC",
                "target_delta": -9.08279,
                "px": 64390.0,
                "tid": "t3",
                "dir": "Open Short",
            },
        ]
        # Alone: first clip is dust
        alone = pc._apply_market_fill(
            bot,
            coin="BTC",
            target_delta=-0.01595,
            px=64390.0,
            cfg=cfg,
            mids=mids,
            ratio=ratio,
            lev=20,
            trigger_tid="t1",
            fill_dir="Open Short",
            start_position=0.0,
        )
        self.assertEqual(alone, [])
        self.assertEqual(bot.get("positions") or {}, {})
        self.assertEqual(bot["fills"][0].get("reason"), "dust_open")

        bot["fills"] = []
        merged = pc._coalesce_flat_entry_fills(bot, items)[0]
        rows = pc._apply_market_fill(
            bot,
            coin="BTC",
            target_delta=float(merged["target_delta"]),
            px=float(merged["px"]),
            cfg=cfg,
            mids=mids,
            ratio=ratio,
            lev=20,
            trigger_tid=merged.get("tid"),
            fill_dir=merged.get("dir"),
            start_position=0.0,
            extra_tids=merged.get("extra_tids"),
        )
        self.assertTrue(rows)
        self.assertEqual(rows[0]["action"], "open")
        pos = bot["positions"]["bot_o:BTC"]
        self.assertLess(float(pos["sz"]), 0)
        self.assertGreater(abs(float(pos["sz"])) * 64390.0, 10.0)

    def test_coalesce_after_close_in_same_batch(self):
        """Bugbot: Close then reopen must merge reopen clips, not use pre-batch pos."""
        bot = {
            "id": "bot_o",
            "positions": {
                "bot_o:BTC": {
                    "key": "bot_o:BTC",
                    "coin": "BTC",
                    "sz": -1.5,
                }
            },
        }
        items = [
            {
                "coin": "BTC",
                "target_delta": 1.5,
                "px": 64000.0,
                "tid": "c1",
                "dir": "Close Short",
            },
            {
                "coin": "BTC",
                "target_delta": -0.01,
                "px": 64000.0,
                "tid": "o1",
                "dir": "Open Short",
            },
            {
                "coin": "BTC",
                "target_delta": -2.0,
                "px": 64010.0,
                "tid": "o2",
                "dir": "Open Short",
            },
            {
                "coin": "BTC",
                "target_delta": -1.0,
                "px": 64020.0,
                "tid": "o3",
                "dir": "Open Short",
            },
        ]
        out = pc._coalesce_flat_entry_fills(bot, items)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["tid"], "c1")
        self.assertEqual(out[1]["coalesced_n"], 3)
        self.assertAlmostEqual(out[1]["target_delta"], -3.01)
        self.assertEqual(out[1]["extra_tids"], ["o1", "o2", "o3"])

    def test_open_dir_with_prior_is_orphan(self):
        """HL Open Short + startPos≠0 must NOT stub-open (K加空 / O开空 bug)."""
        cfg = dict(pc.paper_config())
        cfg["min_notional"] = 10.0
        bot = {
            "id": "bot_o",
            "balance": 1000.0,
            "equity": 1000.0,
            "paper_balance": 1000.0,
            "target_av": 20000.0,
            "positions": {},
            "fills": [],
            "realized_pnl": 0.0,
        }
        ratio = 1000.0 / 20000.0
        rows = pc._apply_market_fill(
            bot,
            coin="BTC",
            target_delta=-0.5,
            px=64000.0,
            cfg=cfg,
            mids={"BTC": 64000.0},
            ratio=ratio,
            lev=20,
            trigger_tid="x1",
            fill_dir="Open Short",
            start_position=-2.0,  # leader already short
        )
        self.assertEqual(rows, [])
        self.assertEqual(bot.get("positions") or {}, {})
        self.assertEqual(bot["fills"][0].get("reason"), "orphan_add")

    def test_coalesce_skips_when_leader_had_prior(self):
        bot = {"id": "bot_o", "positions": {}}
        items = [
            {
                "coin": "BTC",
                "target_delta": -0.05,
                "px": 64000.0,
                "tid": "a1",
                "dir": "Open Short",
                "start_position": -11.0,
            },
            {
                "coin": "BTC",
                "target_delta": -1.0,
                "px": 64000.0,
                "tid": "a2",
                "dir": "Open Short",
                "start_position": -11.05,
            },
        ]
        out = pc._coalesce_flat_entry_fills(bot, items)
        self.assertEqual(len(out), 2)
        self.assertNotIn("coalesced_n", out[0])

    def test_twin_copy_current_scales_to_sibling(self):
        cfg = dict(pc.paper_config())
        cfg["min_notional"] = 10.0
        book = {
            "bots": {
                "bot_k": {
                    "id": "bot_k",
                    "balance": 1000.0,
                    "equity": 4000.0,
                    "positions": {
                        "bot_k:BTC": {
                            "key": "bot_k:BTC",
                            "coin": "BTC",
                            "sz": -2.0,
                            "entry_px": 64000.0,
                            "leverage": 20,
                            "mark_px": 64000.0,
                        }
                    },
                    "fills": [],
                    "realized_pnl": 0.0,
                },
                "bot_o": {
                    "id": "bot_o",
                    "balance": 100.0,
                    "equity": 100.0,
                    "paper_balance": 100.0,
                    "live": True,
                    "mirror_of": "bot_k",
                    "positions": {},
                    "fills": [],
                    "realized_pnl": 0.0,
                },
            }
        }
        rows = pc._sync_paper_to_mirror_sibling(
            book, book["bots"]["bot_o"], {"BTC": 64000.0}, cfg
        )
        self.assertTrue(rows)
        self.assertEqual(rows[0]["action"], "open")
        self.assertEqual(rows[0]["reason"], "twin_copy_current")
        pos = book["bots"]["bot_o"]["positions"]["bot_o:BTC"]
        # Raw ratio 100/4000 * -2.0 = -0.05, but equity×lev notional cap clips.
        self.assertLess(float(pos["sz"]), 0)
        self.assertGreater(abs(float(pos["sz"])), 0.01)
        self.assertLessEqual(abs(float(pos["sz"])), 0.05 + 1e-9)


if __name__ == "__main__":
    unittest.main()
