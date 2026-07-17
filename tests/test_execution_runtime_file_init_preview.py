from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from execution_runtime_file_init_preview import (
    PREVIEW_TYPE,
    STATUS_READY,
    build_execution_runtime_file_init_preview,
)
from execution_runtime_file_schema import (
    default_order_executions_data,
    default_order_locks_data,
)


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ExecutionRuntimeFileInitPreviewTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.temp_root = Path(self.tmp.name)
        self.order_executions_path = self.temp_root / "order_executions.json"
        self.order_locks_path = self.temp_root / "order_locks.json"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _preview(self, **overrides) -> dict:
        kwargs = {
            "order_executions_path": self.order_executions_path,
            "order_locks_path": self.order_locks_path,
        }
        kwargs.update(overrides)
        return build_execution_runtime_file_init_preview(**kwargs)

    def test_both_missing_in_temp_dir_ready(self) -> None:
        result = self._preview()

        self.assertEqual(PREVIEW_TYPE, result["preview_type"])
        self.assertEqual(STATUS_READY, result["status"])
        self.assertTrue(result["preview_only"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["mkdir_required"])
        self.assertEqual(["order_executions", "order_locks"], result["would_create"])
        self.assertEqual([], result["existing"])

    def test_both_existing_skipped(self) -> None:
        self.order_executions_path.write_text("{}", encoding="utf-8")
        self.order_locks_path.write_text("{}", encoding="utf-8")

        result = self._preview()

        self.assertEqual("SKIPPED", result["status"])
        self.assertEqual(["order_executions", "order_locks"], result["existing"])
        self.assertEqual([], result["would_create"])

    def test_order_executions_only_existing_ready_to_create_missing_locks(self) -> None:
        self.order_executions_path.write_text("{}", encoding="utf-8")

        result = self._preview()

        self.assertEqual("READY", result["status"])
        self.assertIn("PARTIAL_RUNTIME_FILES_EXIST", result["warnings"])
        self.assertEqual(["order_executions"], result["existing"])
        self.assertEqual(["order_locks"], result["would_create"])

    def test_order_locks_only_existing_ready_to_create_missing_executions(self) -> None:
        self.order_locks_path.write_text("{}", encoding="utf-8")

        result = self._preview()

        self.assertEqual("READY", result["status"])
        self.assertIn("PARTIAL_RUNTIME_FILES_EXIST", result["warnings"])
        self.assertEqual(["order_locks"], result["existing"])
        self.assertEqual(["order_executions"], result["would_create"])

    def test_parent_directory_missing_blocked(self) -> None:
        missing_root = self.temp_root / "missing"

        result = self._preview(
            order_executions_path=missing_root / "order_executions.json",
            order_locks_path=missing_root / "order_locks.json",
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("PARENT_DIRECTORY_MISSING", result["issues"])

    def test_malformed_path_invalid(self) -> None:
        result = self._preview(order_executions_path="")

        self.assertEqual("INVALID", result["status"])
        self.assertIn("MISSING_ORDER_EXECUTIONS_PATH", result["issues"])

    def test_project_runtime_path_without_allow_flag_blocked(self) -> None:
        result = build_execution_runtime_file_init_preview(
            ROOT / "runtime" / "order_executions.json",
            ROOT / "runtime" / "order_locks.json",
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("PROJECT_RUNTIME_PATH_NOT_ALLOWED", result["issues"])

    def test_project_runtime_path_with_allow_flag_ready_or_skipped_preview_only(self) -> None:
        result = build_execution_runtime_file_init_preview(
            ROOT / "runtime" / "order_executions.json",
            ROOT / "runtime" / "order_locks.json",
            allow_project_runtime_path=True,
        )

        self.assertIn(result["status"], {"READY", "SKIPPED"})
        self.assertTrue(result["preview_only"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["mkdir_required"])

    def test_schema_preview_matches_runtime_schema_defaults(self) -> None:
        result = self._preview()

        self.assertEqual(default_order_executions_data(), result["schemas"]["order_executions"])
        self.assertEqual(default_order_locks_data(), result["schemas"]["order_locks"])
        result["schemas"]["order_executions"]["executions"].append({"mutated": True})
        self.assertEqual([], self._preview()["schemas"]["order_executions"]["executions"])

    def test_no_file_write_or_mkdir(self) -> None:
        with mock.patch("pathlib.Path.mkdir") as mkdir:
            result = self._preview()

        self.assertEqual(STATUS_READY, result["status"])
        mkdir.assert_not_called()
        self.assertFalse(self.order_executions_path.exists())
        self.assertFalse(self.order_locks_path.exists())

    def test_no_runtime_files_created(self) -> None:
        build_execution_runtime_file_init_preview(
            ROOT / "runtime" / "order_executions.json",
            ROOT / "runtime" / "order_locks.json",
            allow_project_runtime_path=True,
        )

        self.assertFalse((ROOT / "runtime" / "order_executions.json").exists())
        self.assertFalse((ROOT / "runtime" / "order_locks.json").exists())

    def test_runtime_and_rules_hash_unchanged(self) -> None:
        runtime_paths = [
            ROOT / "runtime" / "order_queue.json",
            ROOT / "runtime" / "order_executions.json",
            ROOT / "runtime" / "order_locks.json",
        ]
        rules_paths = list((ROOT / "routines").glob("**/rules.json"))
        before_runtime = {str(path): _sha256(path) for path in runtime_paths}
        before_rules = {str(path): _sha256(path) for path in rules_paths}

        self._preview()
        build_execution_runtime_file_init_preview(
            ROOT / "runtime" / "order_executions.json",
            ROOT / "runtime" / "order_locks.json",
            allow_project_runtime_path=True,
        )

        self.assertEqual(before_runtime, {str(path): _sha256(path) for path in runtime_paths})
        self.assertEqual(before_rules, {str(path): _sha256(path) for path in rules_paths})
        self.assertFalse((ROOT / "runtime" / "order_executions.json").exists())
        self.assertFalse((ROOT / "runtime" / "order_locks.json").exists())


if __name__ == "__main__":
    unittest.main()
