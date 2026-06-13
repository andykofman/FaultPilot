"""Tests for plugins/wind_matrix/analysis_helpers.py.

Pure-filesystem behavior tests for collect_bin_log: strict new-name
selection, mtime fallback, and the empty/missing-directory edge cases.
"""
from __future__ import annotations

# pyright: reportMissingImports=false

import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from faultpilot.plugins.wind_matrix import analysis_helpers  # noqa: E402


class TestCollectBinLogBehavior(unittest.TestCase):
    """Pure-filesystem behavior tests for collect_bin_log."""

    def _write_bin(self, directory: Path, name: str) -> Path:
        path = directory / name
        path.write_bytes(b"\x00")
        return path

    def test_strict_single_new_bin_returns_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            before = set()
            path = self._write_bin(log_dir, "00000001.BIN")

            result = analysis_helpers.collect_bin_log(
                before, time.time(), log_dir=log_dir, strict_new_names=True
            )
            self.assertEqual(result, path)

    def test_strict_multiple_new_bins_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            before: set[str] = set()
            self._write_bin(log_dir, "00000001.BIN")
            self._write_bin(log_dir, "00000002.BIN")

            with self.assertRaisesRegex(RuntimeError, "[Mm]ultiple new"):
                analysis_helpers.collect_bin_log(
                    before, time.time(), log_dir=log_dir, strict_new_names=True
                )

    def test_strict_no_new_bins_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            path = self._write_bin(log_dir, "00000001.BIN")
            before = {path.name}

            result = analysis_helpers.collect_bin_log(
                before, time.time(), log_dir=log_dir, strict_new_names=True
            )
            self.assertIsNone(result)

    def test_non_strict_mtime_fallback_within_window_returns_newest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            path = self._write_bin(log_dir, "00000001.BIN")
            before = {path.name}
            started_wall = time.time()

            result = analysis_helpers.collect_bin_log(
                before, started_wall, log_dir=log_dir, strict_new_names=False
            )
            self.assertEqual(result, path)

    def test_empty_dir_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            before: set[str] = set()

            result = analysis_helpers.collect_bin_log(
                before, time.time(), log_dir=log_dir
            )
            self.assertIsNone(result)

    def test_missing_dir_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp) / "nonexistent"
            before: set[str] = set()

            result = analysis_helpers.collect_bin_log(
                before, time.time(), log_dir=log_dir
            )
            self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
