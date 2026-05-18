#!/usr/bin/env python3
"""维护鉴权单元测试（stdlib unittest）。"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_API_ROOT = Path(__file__).resolve().parent.parent
if str(_API_ROOT) not in sys.path:
    sys.path.insert(0, str(_API_ROOT))

from utils.maintenance_token import MaintenanceAuthError, verify_maintenance_token


class MaintenanceAuthTests(unittest.TestCase):
    def test_open_when_token_unset(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("NEXT_K_MAINTENANCE_TOKEN", None)
            verify_maintenance_token(None, None)

    def test_reject_when_token_set_and_missing(self) -> None:
        with patch.dict(os.environ, {"NEXT_K_MAINTENANCE_TOKEN": "secret"}, clear=False):
            with self.assertRaises(MaintenanceAuthError):
                verify_maintenance_token(None, None)

    def test_accept_x_header(self) -> None:
        with patch.dict(os.environ, {"NEXT_K_MAINTENANCE_TOKEN": "secret"}, clear=False):
            verify_maintenance_token("secret", None)

    def test_accept_bearer(self) -> None:
        with patch.dict(os.environ, {"NEXT_K_MAINTENANCE_TOKEN": "secret"}, clear=False):
            verify_maintenance_token(None, "Bearer secret")


if __name__ == "__main__":
    unittest.main()
