from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from execution_runtime_file_init_approval_gate import approve_execution_runtime_file_init
from execution_runtime_file_init_commit_plan_preview import (
    PLAN_TYPE,
    build_execution_runtime_file_init_commit_plan_preview,
)
from execution_runtime_file_init_preview import build_execution_runtime_file_init_preview
from execution_runtime_file_schema import (
    default_order_executions_data,
    default_order_locks_data,
)


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ExecutionRuntimeFileInitCommitPlanPreviewTest(unittest.TestCase):
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

    def _approval(self, preview: dict, **overrides) -> dict:
        kwargs = {"manual_runtime_file_init_confirmed": True}
        kwargs.update(overrides)
        return approve_execution_runtime_file_init(preview, **kwargs)

    def _plan(self, preview: dict, approval: dict) -> dict:
        return build_execution_runtime_file_init_commit_plan_preview(preview, approval)

    def test_approved_ready_plan(self) -> None:
        preview = self._preview()
        approval = self._approval(preview)

        result = self._plan(preview, approval)

        self.assertEqual(PLAN_TYPE, result["plan_type"])
        self.assertEqual("READY", result["status"])
        self.assertTrue(result["init_commit_ready"])
        self.assertTrue(result["preview_only"])
        self.assertFalse(result["runtime_write"])
        self.assertEqual(str(self.order_executions_path), result["planned_targets"]["order_executions"])
        self.assertEqual(default_order_executions_data(), result["planned_schemas"]["order_executions"])
        self.assertEqual(default_order_locks_data(), result["planned_schemas"]["order_locks"])

    def test_skipped_plan(self) -> None:
        self.order_executions_path.write_text("{}", encoding="utf-8")
        self.order_locks_path.write_text("{}", encoding="utf-8")
        preview = self._preview()
        approval = self._approval(preview)

        result = self._plan(preview, approval)

        self.assertEqual("SKIPPED", result["status"])
        self.assertFalse(result["init_commit_ready"])

    def test_partial_plan_ready_with_warning(self) -> None:
        self.order_executions_path.write_text("{}", encoding="utf-8")
        preview = self._preview()
        approval = self._approval(preview)

        result = self._plan(preview, approval)

        self.assertEqual("READY", result["status"])
        self.assertTrue(result["init_commit_ready"])
        self.assertIn("PARTIAL_RUNTIME_FILES_EXIST", result["warnings"])

    def test_invalid_plan(self) -> None:
        preview = self._preview(order_executions_path="")
        approval = self._approval(preview)

        result = self._plan(preview, approval)

        self.assertEqual("INVALID", result["status"])
        self.assertFalse(result["init_commit_ready"])
        self.assertIn("MISSING_ORDER_EXECUTIONS_PATH", result["issues"])

    def test_preview_approval_conflict_invalid(self) -> None:
        preview = self._preview()
        approval = self._approval(preview)
        approval["status"] = "BLOCKED"

        result = self._plan(preview, approval)

        self.assertEqual("INVALID", result["status"])
        self.assertFalse(result["init_commit_ready"])
        self.assertIn("FILE_INIT_PREVIEW_APPROVAL_STATUS_CONFLICT", result["issues"])

    def test_ready_missing_planned_targets_invalid(self) -> None:
        preview = self._preview()
        approval = self._approval(preview)
        preview["targets"] = {}

        result = self._plan(preview, approval)

        self.assertEqual("INVALID", result["status"])
        self.assertFalse(result["init_commit_ready"])
        self.assertIn("MISSING_PLANNED_TARGETS", result["issues"])

    def test_ready_missing_schema_invalid(self) -> None:
        preview = self._preview()
        approval = self._approval(preview)
        preview["schemas"] = {"order_executions": default_order_executions_data()}

        result = self._plan(preview, approval)

        self.assertEqual("INVALID", result["status"])
        self.assertFalse(result["init_commit_ready"])
        self.assertIn("MISSING_PLANNED_SCHEMAS", result["issues"])

    def test_malformed_input_invalid(self) -> None:
        result = build_execution_runtime_file_init_commit_plan_preview(None, None)

        self.assertEqual("INVALID", result["status"])
        self.assertFalse(result["init_commit_ready"])
        self.assertIn("MALFORMED_FILE_INIT_PREVIEW_RESULT", result["issues"])

    def test_preview_only_runtime_write_contract(self) -> None:
        preview = self._preview()
        approval = self._approval(preview)

        result = self._plan(preview, approval)

        self.assertTrue(result["preview_only"])
        self.assertFalse(result["runtime_write"])

    def test_no_file_write_or_mkdir(self) -> None:
        preview = self._preview()
        approval = self._approval(preview)

        with mock.patch("pathlib.Path.mkdir") as mkdir:
            result = self._plan(preview, approval)

        self.assertEqual("READY", result["status"])
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

        preview = build_execution_runtime_file_init_preview(
            ROOT / "runtime" / "order_executions.json",
            ROOT / "runtime" / "order_locks.json",
            allow_project_runtime_path=True,
        )
        approval = approve_execution_runtime_file_init(
            preview,
            manual_runtime_file_init_confirmed=True,
            manual_project_runtime_path_confirmed=True,
        )
        self._plan(preview, approval)

        self.assertEqual(before_runtime, {str(path): _sha256(path) for path in runtime_paths})
        self.assertEqual(before_rules, {str(path): _sha256(path) for path in rules_paths})
        self.assertFalse((ROOT / "runtime" / "order_executions.json").exists())
        self.assertFalse((ROOT / "runtime" / "order_locks.json").exists())


if __name__ == "__main__":
    unittest.main()
