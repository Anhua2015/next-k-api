#!/usr/bin/env python3
"""维护鉴权单元测试。"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_API_ROOT = Path(__file__).resolve().parent.parent
if str(_API_ROOT) not in sys.path:
    sys.path.insert(0, str(_API_ROOT))

from utils.maintenance_token import (
    MaintenanceAuthError,
    maintenance_token_configured,
    verify_maintenance_token,
)  # noqa: E402


class MaintenanceAuthTests(unittest.TestCase):
    def test_open_mode_when_token_is_not_configured(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(maintenance_token_configured())
            verify_maintenance_token(None, None)

    def test_header_token_is_accepted(self) -> None:
        with patch.dict(
            os.environ,
            {"NEXT_K_MAINTENANCE_TOKEN": "test-token"},
            clear=True,
        ):
            self.assertTrue(maintenance_token_configured())
            verify_maintenance_token("test-token", None)

    def test_bearer_token_is_accepted(self) -> None:
        with patch.dict(
            os.environ,
            {"PROTOCOL_MAINTENANCE_TOKEN": "shared-token"},
            clear=True,
        ):
            verify_maintenance_token(None, "Bearer shared-token")

    def test_missing_or_wrong_token_is_rejected(self) -> None:
        with patch.dict(
            os.environ,
            {"NEXT_K_MAINTENANCE_TOKEN": "test-token"},
            clear=True,
        ):
            with self.assertRaises(MaintenanceAuthError):
                verify_maintenance_token(None, None)
            with self.assertRaises(MaintenanceAuthError):
                verify_maintenance_token("wrong", None)


if __name__ == "__main__":
    unittest.main()
