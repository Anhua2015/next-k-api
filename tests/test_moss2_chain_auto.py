"""Moss2 链式全自动配置与运维记录。"""

import json
import os
import tempfile
import unittest
from unittest.mock import patch


class Moss2ChainAutoTests(unittest.TestCase):
    def test_chain_defaults_disable_duplicate_provision_jobs(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MOSS2_CHAIN_PROVISION_AFTER_BOOTSTRAP", None)
            os.environ.pop("MOSS2_AUTO_PROVISION_ON_START", None)
            os.environ.pop("MOSS2_AUTO_PROVISION_WEEKLY", None)
            import importlib
            from moss2 import config as cfg

            importlib.reload(cfg)
            self.assertTrue(cfg.MOSS2_CHAIN_PROVISION_AFTER_BOOTSTRAP)
            self.assertFalse(cfg.MOSS2_AUTO_PROVISION_ON_START)
            self.assertFalse(cfg.MOSS2_AUTO_PROVISION_WEEKLY)

    def test_manual_allowed_when_scheduler_off(self):
        with patch.dict(
            os.environ,
            {"MOSS2_SCHEDULER_ENABLED": "0", "MOSS2_CHAIN_PROVISION_AFTER_BOOTSTRAP": "1"},
            clear=False,
        ):
            import importlib
            from moss2 import config as cfg

            importlib.reload(cfg)
            self.assertFalse(cfg.auto_provision_scheduler_enabled())
            self.assertTrue(cfg.auto_provision_allowed(manual=True))
            self.assertTrue(cfg.data_bootstrap_allowed(manual=True))
            self.assertFalse(cfg.auto_provision_allowed(manual=False))

    def test_save_and_load_last_provision(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"DATA_DIR": tmp}):
                from moss2.provision_history import (
                    load_last_provision_run,
                    save_last_provision_run,
                )

                stats = {
                    "ok": True,
                    "created": 2,
                    "updated": 0,
                    "maintained": 1,
                    "skipped": 0,
                    "enabled_profiles": 2,
                    "sync_enabled_approved": 0,
                    "results": [],
                    "summary_text": "demo",
                }
                save_last_provision_run(
                    stats, trigger="test", bootstrap_context="startup"
                )
                row = load_last_provision_run()
                self.assertIsNotNone(row)
                self.assertEqual(row["trigger"], "test")
                self.assertEqual(row["stats"]["created"], 2)
                self.assertIn("summary_text", row)


if __name__ == "__main__":
    unittest.main()
