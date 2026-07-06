from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import unittest
from unittest import mock

from execution_runtime_file_init_commit_plan_preview import PLAN_TYPE
from execution_runtime_file_init_commit_plan_validator import (
    validate_execution_runtime_file_init_commit_plan_preview,
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


class ExecutionRuntimeFileInitCommitPlanValidatorTest(unittest.TestCase):
    def _ready_plan(self) -> dict:
        return {
            "plan_type": PLAN_TYPE,
            "status": "READY",
            "init_commit_ready": True,
            "preview_only": True,
            "runtime_write": False,
            "planned_targets": {
                "order_executions": "runtime/order_executions.json",
                "order_locks": "runtime/order_locks.json",
            },
            "planned_schemas": {
                "order_executions": default_order_executions_data(),
                "order_locks": default_order_locks_data(),
            },
            "required_confirmations": {
                "manual_runtime_file_init_confirmed": True,
                "manual_project_runtime_path_confirmed": True,
            },
            "issues": [],
            "warnings": ["Preview mode"],
        }

    def _blocked_plan(self) -> dict:
        plan = self._ready_plan()
        plan["status"] = "BLOCKED"
        plan["init_commit_ready"] = False
        plan["issues"] = ["MANUAL_RUNTIME_FILE_INIT_CONFIRMATION_REQUIRED"]
        return plan

    def _invalid_plan(self) -> dict:
        plan = self._ready_plan()
        plan["status"] = "INVALID"
        plan["init_commit_ready"] = False
        plan["issues"] = ["MALFORMED_FILE_INIT_PREVIEW_RESULT"]
        return plan

    def _skipped_plan(self) -> dict:
        plan = self._ready_plan()
        plan["status"] = "SKIPPED"
        plan["init_commit_ready"] = False
        plan["issues"] = []
        plan["warnings"] = ["RUNTIME_FILES_ALREADY_EXIST"]
        return plan

    def test_valid_ready(self) -> None:
        result = validate_execution_runtime_file_init_commit_plan_preview(self._ready_plan())

        self.assertTrue(result["valid"])
        self.assertEqual("READY", result["status"])
        self.assertTrue(result["preview_only"])
        self.assertFalse(result["runtime_write"])
        self.assertEqual([], result["issues"])
        self.assertEqual(["Preview mode"], result["warnings"])

    def test_valid_blocked(self) -> None:
        result = validate_execution_runtime_file_init_commit_plan_preview(self._blocked_plan())

        self.assertTrue(result["valid"])
        self.assertEqual("BLOCKED", result["status"])
        self.assertEqual([], result["issues"])

    def test_valid_invalid(self) -> None:
        result = validate_execution_runtime_file_init_commit_plan_preview(self._invalid_plan())

        self.assertTrue(result["valid"])
        self.assertEqual("INVALID", result["status"])
        self.assertEqual([], result["issues"])

    def test_valid_skipped(self) -> None:
        result = validate_execution_runtime_file_init_commit_plan_preview(self._skipped_plan())

        self.assertTrue(result["valid"])
        self.assertEqual("SKIPPED", result["status"])
        self.assertEqual([], result["issues"])

    def test_invalid_plan_type(self) -> None:
        plan = self._ready_plan()
        plan["plan_type"] = "WRONG"

        result = validate_execution_runtime_file_init_commit_plan_preview(plan)

        self.assertFalse(result["valid"])
        self.assertIn("INVALID_PLAN_TYPE", result["issues"])

    def test_invalid_preview_only_runtime_write(self) -> None:
        plan = self._ready_plan()
        plan["preview_only"] = False
        plan["runtime_write"] = True

        result = validate_execution_runtime_file_init_commit_plan_preview(plan)

        self.assertFalse(result["valid"])
        self.assertIn("PREVIEW_ONLY_REQUIRED", result["issues"])
        self.assertIn("RUNTIME_WRITE_MUST_BE_FALSE", result["issues"])

    def test_invalid_status(self) -> None:
        plan = self._ready_plan()
        plan["status"] = "WAITING"

        result = validate_execution_runtime_file_init_commit_plan_preview(plan)

        self.assertFalse(result["valid"])
        self.assertEqual("INVALID", result["status"])
        self.assertIn("INVALID_STATUS", result["issues"])

    def test_ready_with_init_commit_ready_false(self) -> None:
        plan = self._ready_plan()
        plan["init_commit_ready"] = False

        result = validate_execution_runtime_file_init_commit_plan_preview(plan)

        self.assertFalse(result["valid"])
        self.assertIn("READY_REQUIRES_INIT_COMMIT_READY_TRUE", result["issues"])

    def test_ready_missing_targets(self) -> None:
        plan = self._ready_plan()
        plan["planned_targets"] = {}

        result = validate_execution_runtime_file_init_commit_plan_preview(plan)

        self.assertFalse(result["valid"])
        self.assertIn("MISSING_TARGET_ORDER_EXECUTIONS", result["issues"])
        self.assertIn("MISSING_TARGET_ORDER_LOCKS", result["issues"])

    def test_ready_missing_schemas(self) -> None:
        plan = self._ready_plan()
        plan["planned_schemas"] = {}

        result = validate_execution_runtime_file_init_commit_plan_preview(plan)

        self.assertFalse(result["valid"])
        self.assertIn("MISSING_SCHEMA_ORDER_EXECUTIONS", result["issues"])
        self.assertIn("MISSING_SCHEMA_ORDER_LOCKS", result["issues"])

    def test_ready_with_issues(self) -> None:
        plan = self._ready_plan()
        plan["issues"] = ["SHOULD_NOT_BE_READY"]

        result = validate_execution_runtime_file_init_commit_plan_preview(plan)

        self.assertFalse(result["valid"])
        self.assertIn("READY_WITH_ISSUES", result["issues"])

    def test_invalid_without_issues(self) -> None:
        plan = self._invalid_plan()
        plan["issues"] = []

        result = validate_execution_runtime_file_init_commit_plan_preview(plan)

        self.assertFalse(result["valid"])
        self.assertIn("INVALID_WITHOUT_ISSUES", result["issues"])

    def test_blocked_without_issues_or_warnings(self) -> None:
        plan = self._blocked_plan()
        plan["issues"] = []
        plan["warnings"] = []

        result = validate_execution_runtime_file_init_commit_plan_preview(plan)

        self.assertFalse(result["valid"])
        self.assertIn("BLOCKED_WITHOUT_ISSUES_OR_WARNINGS", result["issues"])

    def test_skipped_with_init_commit_ready_true(self) -> None:
        plan = self._skipped_plan()
        plan["init_commit_ready"] = True

        result = validate_execution_runtime_file_init_commit_plan_preview(plan)

        self.assertFalse(result["valid"])
        self.assertIn("SKIPPED_REQUIRES_INIT_COMMIT_READY_FALSE", result["issues"])

    def test_malformed_required_confirmations(self) -> None:
        plan = self._ready_plan()
        plan["required_confirmations"] = []

        result = validate_execution_runtime_file_init_commit_plan_preview(plan)

        self.assertFalse(result["valid"])
        self.assertIn("REQUIRED_CONFIRMATIONS_MUST_BE_DICT", result["issues"])

    def test_malformed_input(self) -> None:
        result = validate_execution_runtime_file_init_commit_plan_preview("bad")

        self.assertFalse(result["valid"])
        self.assertEqual("INVALID", result["status"])
        self.assertIn("MALFORMED_FILE_INIT_COMMIT_PLAN_PREVIEW", result["issues"])

    def test_input_immutability(self) -> None:
        plan = self._ready_plan()
        before = deepcopy(plan)

        result = validate_execution_runtime_file_init_commit_plan_preview(plan)
        result["issues"].append("MUTATED_RESULT_ONLY")

        self.assertEqual(before, plan)

    def test_no_file_write_or_mkdir(self) -> None:
        with (
            mock.patch("pathlib.Path.write_text") as write_text,
            mock.patch("pathlib.Path.mkdir") as mkdir,
            mock.patch("builtins.open", mock.mock_open()) as open_mock,
        ):
            result = validate_execution_runtime_file_init_commit_plan_preview(self._ready_plan())

        self.assertTrue(result["valid"])
        write_text.assert_not_called()
        mkdir.assert_not_called()
        open_mock.assert_not_called()

    def test_runtime_and_rules_hash_unchanged(self) -> None:
        runtime_paths = [
            ROOT / "runtime" / "order_queue.json",
            ROOT / "runtime" / "order_executions.json",
            ROOT / "runtime" / "order_locks.json",
        ]
        rules_paths = list((ROOT / "routines").glob("**/rules.json"))
        before_runtime = {str(path): _sha256(path) for path in runtime_paths}
        before_rules = {str(path): _sha256(path) for path in rules_paths}

        validate_execution_runtime_file_init_commit_plan_preview(self._ready_plan())
        validate_execution_runtime_file_init_commit_plan_preview(self._blocked_plan())
        validate_execution_runtime_file_init_commit_plan_preview(self._invalid_plan())
        validate_execution_runtime_file_init_commit_plan_preview(self._skipped_plan())

        self.assertEqual(before_runtime, {str(path): _sha256(path) for path in runtime_paths})
        self.assertEqual(before_rules, {str(path): _sha256(path) for path in rules_paths})
        self.assertFalse((ROOT / "runtime" / "order_executions.json").exists())
        self.assertFalse((ROOT / "runtime" / "order_locks.json").exists())


if __name__ == "__main__":
    unittest.main()
