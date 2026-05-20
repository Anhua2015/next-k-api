#!/usr/bin/env python3
"""DATA_DIR 导出工具单元测试。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_API_ROOT = Path(__file__).resolve().parent.parent
if str(_API_ROOT) not in sys.path:
    sys.path.insert(0, str(_API_ROOT))

from utils.volume_export import (
    create_data_archive,
    export_volume_enabled,
    resolve_data_dir,
    summarize_data_dir,
)


class VolumeExportTests(unittest.TestCase):
    def test_export_disabled_by_default(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("NEXT_K_EXPORT_VOLUME_ENABLED", None)
            self.assertFalse(export_volume_enabled())

    def test_export_enabled_flag(self) -> None:
        with patch.dict(
            os.environ, {"NEXT_K_EXPORT_VOLUME_ENABLED": "1"}, clear=False
        ):
            self.assertTrue(export_volume_enabled())

    def test_resolve_data_dir_from_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"DATA_DIR": tmp}, clear=False):
                self.assertEqual(resolve_data_dir(), Path(tmp).resolve())

    def test_summarize_and_zip_small_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.txt").write_text("hello", encoding="utf-8")
            (root / "sub").mkdir()
            (root / "sub" / "b.db").write_bytes(b"\x00\x01")

            summary = summarize_data_dir(root)
            self.assertTrue(summary["exists"])
            self.assertEqual(summary["file_count"], 2)
            self.assertGreater(summary["total_bytes"], 0)

            archive_path, work_dir = create_data_archive(fmt="zip", root=root)
            self.assertTrue(archive_path.is_file())
            self.assertGreater(archive_path.stat().st_size, 0)
            archive_path.unlink()
            import shutil

            shutil.rmtree(work_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
