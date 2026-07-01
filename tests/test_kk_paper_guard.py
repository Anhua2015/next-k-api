"""KK 纸面扫描引擎隔离测试。"""

from __future__ import annotations

import os
import unittest

from orb.kk.paper import run_scan_kk


class TestKKPaperGuard(unittest.TestCase):
    def test_vnpy_engine_skips_paper_scan(self):
        saved = {k: os.environ.get(k) for k in ("KK_ENGINE", "KK_ENABLED")}
        try:
            os.environ["KK_ENGINE"] = "vnpy"
            os.environ["KK_ENABLED"] = "1"
            out = run_scan_kk(now_ms=1_700_000_000_000)
            self.assertTrue(out.get("skipped"))
            self.assertEqual(out.get("reason"), "vnpy_engine")
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


if __name__ == "__main__":
    unittest.main()
