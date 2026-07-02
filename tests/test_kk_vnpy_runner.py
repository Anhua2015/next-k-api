"""KkVnpyEngine bootstrap 测试。"""

from __future__ import annotations

import os
import unittest
from unittest import mock

from orb.kk.vnpy.runner import KkVnpyEngine


class TestKkVnpyRunner(unittest.TestCase):
    def test_bootstrap_fails_when_live_without_binance_keys(self):
        saved = {
            k: os.environ.get(k)
            for k in (
                "BINANCE_API_KEY",
                "BINANCE_API_SECRET",
                "KK_ENABLED",
                "KK_ENGINE",
                "KK_LIVE_ENABLED",
                "KK_SYMBOLS",
            )
        }
        try:
            os.environ.pop("BINANCE_API_KEY", None)
            os.environ.pop("BINANCE_API_SECRET", None)
            os.environ["KK_ENABLED"] = "1"
            os.environ["KK_ENGINE"] = "vnpy"
            os.environ["KK_LIVE_ENABLED"] = "1"
            os.environ["KK_SYMBOLS"] = "COINUSDT"
            engine = KkVnpyEngine()
            out = engine.bootstrap(init_wait_sec=0.1)
            self.assertFalse(out.get("ok"))
            self.assertEqual(out.get("reason"), "binance_credentials_missing")
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_bootstrap_allows_missing_binance_when_not_live(self):
        saved = {
            k: os.environ.get(k)
            for k in ("BINANCE_API_KEY", "BINANCE_API_SECRET", "KK_ENABLED", "KK_LIVE_ENABLED")
        }
        try:
            os.environ.pop("BINANCE_API_KEY", None)
            os.environ.pop("BINANCE_API_SECRET", None)
            os.environ["KK_ENABLED"] = "1"
            os.environ["KK_LIVE_ENABLED"] = "0"
            engine = KkVnpyEngine()
            with mock.patch("orb.kk.vnpy.runner.EventEngine", side_effect=RuntimeError("stop")):
                try:
                    out = engine.bootstrap(init_wait_sec=0.1)
                except RuntimeError:
                    out = {"reason": None}
            self.assertNotEqual(out.get("reason"), "binance_credentials_missing")
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_bootstrap_skips_when_disabled(self):
        saved = os.environ.get("KK_ENABLED")
        try:
            os.environ["KK_ENABLED"] = "0"
            engine = KkVnpyEngine()
            out = engine.bootstrap(init_wait_sec=0.1)
            self.assertTrue(out.get("skipped"))
            self.assertEqual(out.get("reason"), "kk_disabled")
        finally:
            if saved is None:
                os.environ.pop("KK_ENABLED", None)
            else:
                os.environ["KK_ENABLED"] = saved


if __name__ == "__main__":
    unittest.main()
