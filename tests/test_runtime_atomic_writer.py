# -*- coding: utf-8 -*-
"""Tests for runtime_atomic_writer (M6-1).

All tests exercise temp/test paths only. No protected runtime files
(runtime/*.json, routines/*/rules.json) are touched.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path

from runtime_atomic_writer import (
    STATUS_ERROR,
    STATUS_OK,
    WRITER_TYPE,
    write_json_atomic,
)


class TestRuntimeAtomicWriter(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="runtime_atomic_writer_test_"))

    def tearDown(self):
        for child in self.tmp_dir.glob("**/*"):
            if child.is_file():
                try:
                    child.unlink()
                except OSError:
                    pass
        try:
            self.tmp_dir.rmdir()
        except OSError:
            pass

    def _target(self, name: str) -> Path:
        return self.tmp_dir / name

    def test_write_creates_file_with_content(self):
        target = self._target("order_executions.json")
        data = {"version": 1, "updated_at": None, "executions": [{"id": "E1"}]}

        result = write_json_atomic(target, data)

        self.assertEqual(result["status"], STATUS_OK)
        self.assertEqual(result["writer_type"], WRITER_TYPE)
        self.assertTrue(result["written"])
        self.assertGreater(result["bytes_written"], 0)
        self.assertTrue(target.exists())
        loaded = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(loaded, data)

    def test_write_preserves_unicode(self):
        target = self._target("names.json")
        data = {"name": "삼성전자", "side": "매도"}

        result = write_json_atomic(target, data)

        self.assertEqual(result["status"], STATUS_OK)
        loaded = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(loaded["name"], "삼성전자")
        self.assertEqual(loaded["side"], "매도")

    def test_write_replaces_existing_file_atomically(self):
        target = self._target("order_locks.json")
        target.write_text(json.dumps({"version": 1, "locks": []}), encoding="utf-8")

        new_data = {"version": 1, "updated_at": "2026-07-09", "locks": [{"id": "L1"}]}
        result = write_json_atomic(target, new_data)

        self.assertEqual(result["status"], STATUS_OK)
        loaded = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(loaded, new_data)
        self.assertEqual(loaded["locks"], [{"id": "L1"}])

    def test_temp_file_is_cleaned_up(self):
        target = self._target("cleanup.json")
        write_json_atomic(target, {"a": 1})

        leftovers = list(self.tmp_dir.glob(".cleanup.json.*.tmp"))
        self.assertEqual(leftovers, [])

    def test_write_returns_error_when_parent_missing(self):
        target = self._target("nested/deep/missing.json")
        # Parent directory intentionally not created.
        result = write_json_atomic(target, {"a": 1})

        self.assertEqual(result["status"], STATUS_ERROR)
        self.assertFalse(result["written"])
        self.assertEqual(result["bytes_written"], 0)
        self.assertIn("ATOMIC_WRITE_FAILED", result["error"])
        self.assertFalse(target.exists())

    def test_write_returns_error_on_non_serializable_data(self):
        target = self._target("bad.json")
        # set is not JSON serializable
        result = write_json_atomic(target, {"bad": {1, 2, 3}})

        self.assertEqual(result["status"], STATUS_ERROR)
        self.assertFalse(result["written"])
        self.assertIn("ATOMIC_WRITE_FAILED", result["error"])
        self.assertFalse(target.exists())

    def test_write_accepts_str_path(self):
        target = str(self._target("str_path.json"))
        result = write_json_atomic(target, {"ok": True})

        self.assertEqual(result["status"], STATUS_OK)
        self.assertTrue(Path(target).exists())


if __name__ == "__main__":
    unittest.main()
