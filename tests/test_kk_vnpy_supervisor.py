"""KK vnpy supervisor 启动条件测试。"""

from __future__ import annotations

import os
import unittest
from unittest import mock

from orb.kk.vnpy.supervisor import KkVnpySupervisor


class TestKkVnpySupervisor(unittest.TestCase):
    def test_should_start_vnpy_enabled(self):
        saved = {k: os.environ.get(k) for k in ("KK_ENGINE", "KK_ENABLED", "KK_VNPY_AUTO_START", "KK_VNPY_STANDALONE")}
        try:
            os.environ["KK_ENGINE"] = "vnpy"
            os.environ["KK_ENABLED"] = "1"
            os.environ["KK_VNPY_AUTO_START"] = "1"
            os.environ.pop("KK_VNPY_STANDALONE", None)
            sup = KkVnpySupervisor()
            self.assertTrue(sup.should_start())
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_should_not_start_paper_engine(self):
        saved = os.environ.get("KK_ENGINE")
        try:
            os.environ["KK_ENGINE"] = "paper"
            sup = KkVnpySupervisor()
            self.assertFalse(sup.should_start())
        finally:
            if saved is None:
                os.environ.pop("KK_ENGINE", None)
            else:
                os.environ["KK_ENGINE"] = saved

    def test_should_not_start_when_standalone(self):
        saved = {k: os.environ.get(k) for k in ("KK_ENGINE", "KK_VNPY_STANDALONE")}
        try:
            os.environ["KK_ENGINE"] = "vnpy"
            os.environ["KK_VNPY_STANDALONE"] = "1"
            sup = KkVnpySupervisor()
            self.assertFalse(sup.should_start())
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_should_not_start_when_auto_start_off(self):
        saved = os.environ.get("KK_VNPY_AUTO_START")
        try:
            os.environ["KK_VNPY_AUTO_START"] = "0"
            sup = KkVnpySupervisor()
            self.assertFalse(sup.should_start())
        finally:
            if saved is None:
                os.environ.pop("KK_VNPY_AUTO_START", None)
            else:
                os.environ["KK_VNPY_AUTO_START"] = saved

    def test_bootstrap_fatal_does_not_restart(self):
        saved = os.environ.get("KK_VNPY_RESTART_SEC")
        try:
            os.environ["KK_VNPY_RESTART_SEC"] = "0.01"
            sup = KkVnpySupervisor()
            with mock.patch(
                "orb.kk.vnpy.runner.KkVnpyEngine.bootstrap",
                return_value={"ok": False, "reason": "binance_credentials_missing"},
            ):
                with mock.patch("orb.kk.vnpy.runner.KkVnpyEngine.shutdown"):
                    sup._stop.clear()
                    sup._run()
            self.assertFalse(sup.is_running)
            self.assertEqual(sup.last_status.get("reason"), "binance_credentials_missing")
        finally:
            if saved is None:
                os.environ.pop("KK_VNPY_RESTART_SEC", None)
            else:
                os.environ["KK_VNPY_RESTART_SEC"] = saved


if __name__ == "__main__":
    unittest.main()
