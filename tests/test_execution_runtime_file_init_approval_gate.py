from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from execution_runtime_file_init_approval_gate import (
    GATE_TYPE,
    approve_execution_runtime_file_init,
)
from execution_runtime_file_init_preview import build_execution_runtime_file_init_preview


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ExecutionRuntimeFileInitApprovalGateTest(unittest.TestCase):
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

    def _approve(self, preview: dict, **overrides) -> dict:
        kwargs = {"manual_runtime_file_init_confirmed": True}
        kwargs.update(overrides)
        return approve_execution_runtime_file_init(preview, **kwargs)

    def test_ready_with_manual_confirmed_approved(self) -> None:
        result = self._approve(self._preview())

        self.assertEqual(GATE_TYPE, result["gate_type"])
        self.assertEqual("APPROVED", result["status"])
        self.assertTrue(result["init_commit_allowed"])
        self.assertTrue(result["preview_only"])
        self.assertFalse(result["runtime_write"])

    def test_ready_with_manual_missing_blocked(self) -> None:
        result = approve_execution_runtime_file_init(self._preview())

        self.assertEqual("BLOCKED", result["status"])
        self.assertFalse(result["init_commit_allowed"])
        self.assertIn("MANUAL_RUNTIME_FILE_INIT_CONFIRMATION_REQUIRED", result["issues"])

    def test_project_runtime_path_missing_project_confirmation_blocked(self) -> None:
        preview = build_execution_runtime_file_init_preview(
            ROOT / "runtime" / "order_executions.json",
            ROOT / "runtime" / "order_locks.json",
            allow_project_runtime_path=True,
        )

        result = approve_execution_runtime_file_init(
            preview,
            manual_runtime_file_init_confirmed=True,
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertFalse(result["init_commit_allowed"])
        self.assertIn("MANUAL_PROJECT_RUNTIME_PATH_CONFIRMATION_REQUIRED", result["issues"])

    def test_project_runtime_path_with_both_confirmations_approved(self) -> None:
        preview = build_execution_runtime_file_init_preview(
            ROOT / "runtime" / "order_executions.json",
            ROOT / "runtime" / "order_locks.json",
            allow_project_runtime_path=True,
        )

        result = approve_execution_runtime_file_init(
            preview,
            manual_runtime_file_init_confirmed=True,
            manual_project_runtime_path_confirmed=True,
        )

        self.assertEqual("APPROVED", result["status"])
        self.assertTrue(result["init_commit_allowed"])

    def test_skipped_preview_skipped(self) -> None:
        self.order_executions_path.write_text("{}", encoding="utf-8")
        self.order_locks_path.write_text("{}", encoding="utf-8")

        result = self._approve(self._preview())

        self.assertEqual("SKIPPED", result["status"])
        self.assertFalse(result["init_commit_allowed"])

    def test_blocked_preview_blocked(self) -> None:
        self.order_executions_path.write_text("{}", encoding="utf-8")

        result = self._approve(self._preview())

        self.assertEqual("BLOCKED", result["status"])
        self.assertFalse(result["init_commit_allowed"])
        self.assertIn("PARTIAL_RUNTIME_FILES_EXIST", result["issues"])

    def test_invalid_preview_invalid(self) -> None:
        result = self._approve(self._preview(order_executions_path=""))

        self.assertEqual("INVALID", result["status"])
        self.assertFalse(result["init_commit_allowed"])
        self.assertIn("MISSING_ORDER_EXECUTIONS_PATH", result["issues"])

    def test_malformed_preview_invalid(self) -> None:
        result = approve_execution_runtime_file_init(None)

        self.assertEqual("INVALID", result["status"])
        self.assertFalse(result["init_commit_allowed"])
        self.assertIn("MALFORMED_FILE_INIT_PREVIEW_RESULT", result["issues"])

    def test_invalid_preview_type_invalid(self) -> None:
        preview = self._preview()
        preview["preview_type"] = "OTHER"

        result = self._approve(preview)

        self.assertEqual("INVALID", result["status"])
        self.assertFalse(result["init_commit_allowed"])
        self.assertIn("INVALID_FILE_INIT_PREVIEW_TYPE", result["issues"])

    def test_preview_only_runtime_write_contract(self) -> None:
        result = self._approve(self._preview())

        self.assertTrue(result["preview_only"])
        self.assertFalse(result["runtime_write"])
        self.assertTrue(result["required_confirmations"]["manual_runtime_file_init_confirmed"])

    def test_no_file_write_or_mkdir(self) -> None:
        with mock.patch("pathlib.Path.mkdir") as mkdir:
            result = self._approve(self._preview())

        self.assertEqual("APPROVED", result["status"])
        mkdir.assert_not_called()
        self.assertFalse(self.order_executions_path.exists())
        self.assertFalse(self.order_locks_path.exists())

    def test_runtime_and_rules_hash_unchanged(self) -> None:
        runtime_paths = [
            ROOT / "runtime" / "order_queue.json",
            ROOT / "runtime" / "order_executions.json",
            ROOT / "runtime" / "order_locks.json",
        ]
        rules_paths = list((ROOT / "routines").glob("**/rules.json"))
        before_runtime = {str(path): _sha256(path) for path in runtime_paths}
        before_rules = {str(path): _sha256(path) for path in rules_paths}

        self._approve(self._preview())
        runtime_preview = build_execution_runtime_file_init_preview(
            ROOT / "runtime" / "order_executions.json",
            ROOT / "runtime" / "order_locks.json",
            allow_project_runtime_path=True,
        )
        approve_execution_runtime_file_init(
            runtime_preview,
            manual_runtime_file_init_confirmed=True,
            manual_project_runtime_path_confirmed=True,
        )

        self.assertEqual(before_runtime, {str(path): _sha256(path) for path in runtime_paths})
        self.assertEqual(before_rules, {str(path): _sha256(path) for path in rules_paths})
        self.assertFalse((ROOT / "runtime" / "order_executions.json").exists())
        self.assertFalse((ROOT / "runtime" / "order_locks.json").exists())


if __name__ == "__main__":
    unittest.main()
