"""Main + HIP-3 (xyz) clearinghouse merge for copy snapshots."""

from __future__ import annotations

import unittest
from unittest import mock

from utils import hl_short_term as hs


class SnapshotXyzTests(unittest.TestCase):
    def test_merges_main_and_xyz_positions_and_avs(self):
        main = {
            "marginSummary": {"accountValue": "1000"},
            "assetPositions": [
                {
                    "position": {
                        "coin": "BTC",
                        "szi": "-1.5",
                        "entryPx": "64000",
                        "unrealizedPnl": "10",
                        "leverage": {"value": 20},
                    }
                }
            ],
        }
        xyz = {
            "marginSummary": {"accountValue": "400"},
            "assetPositions": [
                {
                    "position": {
                        "coin": "xyz:GOOGL",
                        "szi": "100",
                        "entryPx": "360",
                        "unrealizedPnl": "5",
                        "leverage": {"value": 10},
                    }
                }
            ],
        }

        def _fake_http(body):
            if body.get("dex") == "xyz":
                return xyz
            return main

        with mock.patch.object(hs, "http_json", side_effect=_fake_http):
            snap = hs.snapshot_positions("0xabc")

        self.assertAlmostEqual(float(snap["account_value"]), 1400.0)
        coins = {p["coin"]: p for p in snap["positions"]}
        self.assertIn("BTC", coins)
        self.assertIn("xyz:GOOGL", coins)
        self.assertAlmostEqual(float(coins["BTC"]["szi"]), -1.5)
        self.assertAlmostEqual(float(coins["xyz:GOOGL"]["szi"]), 100.0)

    def test_xyz_failure_still_returns_main(self):
        main = {
            "marginSummary": {"accountValue": "500"},
            "assetPositions": [
                {
                    "position": {
                        "coin": "ETH",
                        "szi": "-2",
                        "entryPx": "1900",
                        "unrealizedPnl": "0",
                        "leverage": {"value": 10},
                    }
                }
            ],
        }

        def _fake_http(body):
            if body.get("dex") == "xyz":
                raise RuntimeError("xyz down")
            return main

        with mock.patch.object(hs, "http_json", side_effect=_fake_http):
            snap = hs.snapshot_positions("0xabc")

        self.assertAlmostEqual(float(snap["account_value"]), 500.0)
        self.assertEqual(len(snap["positions"]), 1)
        self.assertEqual(snap["positions"][0]["coin"], "ETH")


class AugmentFreshFillTests(unittest.TestCase):
    def test_seeds_googl_when_desired_missing(self):
        from utils import hl_bitget_executor as ex

        bot = {"id": "bot_c", "live_only": True, "target_av": 250000.0}
        rows = [
            {
                "action": "live_sync",
                "coin": "xyz:GOOGL",
                "target_delta": 2808.988,
                "start_position": 0.0,
                "dir": "Open Long",
            }
        ]
        with (
            mock.patch.object(ex, "_fetch_bitget_equity", return_value=850.0),
            mock.patch.object(ex, "hl_coin_to_bitget", return_value="GOOGLUSDT"),
        ):
            out = ex._augment_desired_from_fresh_fills(
                bot, {}, rows, route_scale=1.0, env_prefix=""
            )
        self.assertIn("GOOGLUSDT", out)
        # 2808.988 * (850/250000) ≈ 9.55
        self.assertAlmostEqual(out["GOOGLUSDT"], 2808.988 * (850 / 250000), places=4)


if __name__ == "__main__":
    unittest.main()
