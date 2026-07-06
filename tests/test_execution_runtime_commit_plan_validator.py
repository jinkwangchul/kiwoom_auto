from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import unittest
from unittest import mock

from execution_runtime_commit_plan_preview import PLAN_TYPE
from execution_runtime_commit_plan_validator import validate_execution_runtime_commit_plan_preview


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ExecutionRuntimeCommitPlanValidatorTest(unittest.TestCase):
    def _ready_plan(self) -> dict:
        return {
            "plan_type": PLAN_TYPE,
            "status": "READY",
            "commit_ready": True,
            "preview_only": True,
            "runtime_write": False,
            "planned_targets": {
                "order_executions": "runtime/order_executions.json",
                "order_locks": "runtime/order_locks.json",
            },
            "planned_records": {
                "execution": {
                    "execution_id": "EXEC_1",
                    "order_id": "ORDER_1",
                    "request_hash": "HASH_1",
                    "lock_id": "LOCK_1",
                },
                "lock": {
                    "lock_id": "LOCK_1",
                    "order_id": "ORDER_1",
                    "request_hash": "HASH_1",
                    "execution_id": "EXEC_1",
                },
            },
            "required_confirmations": {
                "manual_execution_runtime_commit_confirmed": True,
                "manual_runtime_file_write_confirmed": True,
            },
            "issues": [],
            "warnings": ["Preview mode"],
        }

    def _blocked_plan(self) -> dict:
        plan = self._ready_plan()
        plan["status"] = "BLOCKED"
        plan["commit_ready"] = False
        plan["planned_records"] = {"execution": None, "lock": None}
        plan["issues"] = ["MANUAL_RUNTIME_FILE_WRITE_CONFIRMATION_REQUIRED"]
        return plan

    def _invalid_plan(self) -> dict:
        plan = self._ready_plan()
        plan["status"] = "INVALID"
        plan["commit_ready"] = False
        plan["planned_records"] = {"execution": None, "lock": None}
        plan["issues"] = ["MALFORMED_WRITE_PREVIEW_ORCHESTRATOR_RESULT"]
        return plan

    def test_valid_ready(self) -> None:
        result = validate_execution_runtime_commit_plan_preview(self._ready_plan())

        self.assertTrue(result["valid"])
        self.assertEqual("READY", result["status"])
        self.assertTrue(result["preview_only"])
        self.assertFalse(result["runtime_write"])
        self.assertEqual([], result["issues"])
        self.assertEqual(["Preview mode"], result["warnings"])

    def test_valid_blocked(self) -> None:
        result = validate_execution_runtime_commit_plan_preview(self._blocked_plan())

        self.assertTrue(result["valid"])
        self.assertEqual("BLOCKED", result["status"])
        self.assertEqual([], result["issues"])

    def test_valid_invalid(self) -> None:
        result = validate_execution_runtime_commit_plan_preview(self._invalid_plan())

        self.assertTrue(result["valid"])
        self.assertEqual("INVALID", result["status"])
        self.assertEqual([], result["issues"])

    def test_invalid_plan_type(self) -> None:
        plan = self._ready_plan()
        plan["plan_type"] = "WRONG"

        result = validate_execution_runtime_commit_plan_preview(plan)

        self.assertFalse(result["valid"])
        self.assertIn("INVALID_PLAN_TYPE", result["issues"])

    def test_invalid_preview_only_runtime_write(self) -> None:
        plan = self._ready_plan()
        plan["preview_only"] = False
        plan["runtime_write"] = True

        result = validate_execution_runtime_commit_plan_preview(plan)

        self.assertFalse(result["valid"])
        self.assertIn("PREVIEW_ONLY_REQUIRED", result["issues"])
        self.assertIn("RUNTIME_WRITE_MUST_BE_FALSE", result["issues"])

    def test_invalid_status(self) -> None:
        plan = self._ready_plan()
        plan["status"] = "WAITING"

        result = validate_execution_runtime_commit_plan_preview(plan)

        self.assertFalse(result["valid"])
        self.assertEqual("INVALID", result["status"])
        self.assertIn("INVALID_STATUS", result["issues"])

    def test_ready_with_commit_ready_false(self) -> None:
        plan = self._ready_plan()
        plan["commit_ready"] = False

        result = validate_execution_runtime_commit_plan_preview(plan)

        self.assertFalse(result["valid"])
        self.assertIn("READY_REQUIRES_COMMIT_READY_TRUE", result["issues"])

    def test_ready_missing_planned_records(self) -> None:
        plan = self._ready_plan()
        plan["planned_records"] = {"execution": None, "lock": None}

        result = validate_execution_runtime_commit_plan_preview(plan)

        self.assertFalse(result["valid"])
        self.assertIn("MISSING_PLANNED_EXECUTION_RECORD", result["issues"])
        self.assertIn("MISSING_PLANNED_LOCK_RECORD", result["issues"])

    def test_ready_with_issues(self) -> None:
        plan = self._ready_plan()
        plan["issues"] = ["SHOULD_NOT_BE_READY"]

        result = validate_execution_runtime_commit_plan_preview(plan)

        self.assertFalse(result["valid"])
        self.assertIn("READY_WITH_ISSUES", result["issues"])

    def test_invalid_without_issues(self) -> None:
        plan = self._invalid_plan()
        plan["issues"] = []

        result = validate_execution_runtime_commit_plan_preview(plan)

        self.assertFalse(result["valid"])
        self.assertIn("INVALID_WITHOUT_ISSUES", result["issues"])

    def test_blocked_without_issues_or_warnings(self) -> None:
        plan = self._blocked_plan()
        plan["issues"] = []
        plan["warnings"] = []

        result = validate_execution_runtime_commit_plan_preview(plan)

        self.assertFalse(result["valid"])
        self.assertIn("BLOCKED_WITHOUT_ISSUES_OR_WARNINGS", result["issues"])

    def test_malformed_planned_targets_and_records(self) -> None:
        plan = self._ready_plan()
        plan["planned_targets"] = []
        plan["planned_records"] = []

        result = validate_execution_runtime_commit_plan_preview(plan)

        self.assertFalse(result["valid"])
        self.assertIn("PLANNED_TARGETS_MUST_BE_DICT", result["issues"])
        self.assertIn("PLANNED_RECORDS_MUST_BE_DICT", result["issues"])

    def test_missing_planned_targets(self) -> None:
        plan = self._ready_plan()
        plan["planned_targets"] = {}

        result = validate_execution_runtime_commit_plan_preview(plan)

        self.assertFalse(result["valid"])
        self.assertIn("MISSING_TARGET_ORDER_EXECUTIONS", result["issues"])
        self.assertIn("MISSING_TARGET_ORDER_LOCKS", result["issues"])

    def test_malformed_required_confirmations_issues_warnings(self) -> None:
        plan = self._ready_plan()
        plan["required_confirmations"] = []
        plan["issues"] = {}
        plan["warnings"] = {}

        result = validate_execution_runtime_commit_plan_preview(plan)

        self.assertFalse(result["valid"])
        self.assertIn("REQUIRED_CONFIRMATIONS_MUST_BE_DICT", result["issues"])
        self.assertIn("ISSUES_MUST_BE_LIST", result["issues"])
        self.assertIn("WARNINGS_MUST_BE_LIST", result["issues"])

    def test_missing_execution_record_required_fields(self) -> None:
        cases = [
            ("execution_id", "MISSING_EXECUTION_RECORD_EXECUTION_ID"),
            ("order_id", "MISSING_EXECUTION_RECORD_ORDER_ID"),
            ("request_hash", "MISSING_EXECUTION_RECORD_REQUEST_HASH"),
        ]
        for field, issue in cases:
            with self.subTest(field=field):
                plan = self._ready_plan()
                plan["planned_records"]["execution"][field] = ""
                result = validate_execution_runtime_commit_plan_preview(plan)
                self.assertFalse(result["valid"])
                self.assertIn(issue, result["issues"])

    def test_missing_lock_record_required_fields(self) -> None:
        cases = [
            ("lock_id", "MISSING_LOCK_RECORD_LOCK_ID"),
            ("order_id", "MISSING_LOCK_RECORD_ORDER_ID"),
            ("request_hash", "MISSING_LOCK_RECORD_REQUEST_HASH"),
        ]
        for field, issue in cases:
            with self.subTest(field=field):
                plan = self._ready_plan()
                plan["planned_records"]["lock"][field] = ""
                result = validate_execution_runtime_commit_plan_preview(plan)
                self.assertFalse(result["valid"])
                self.assertIn(issue, result["issues"])

    def test_malformed_input(self) -> None:
        result = validate_execution_runtime_commit_plan_preview("bad")

        self.assertFalse(result["valid"])
        self.assertEqual("INVALID", result["status"])
        self.assertIn("MALFORMED_COMMIT_PLAN_PREVIEW", result["issues"])

    def test_input_immutability(self) -> None:
        plan = self._ready_plan()
        before = deepcopy(plan)

        result = validate_execution_runtime_commit_plan_preview(plan)
        result["issues"].append("MUTATED_RESULT_ONLY")

        self.assertEqual(before, plan)

    def test_no_file_write_or_mkdir(self) -> None:
        with (
            mock.patch("pathlib.Path.write_text") as write_text,
            mock.patch("pathlib.Path.mkdir") as mkdir,
            mock.patch("builtins.open", mock.mock_open()) as open_mock,
        ):
            result = validate_execution_runtime_commit_plan_preview(self._ready_plan())

        self.assertTrue(result["valid"])
        write_text.assert_not_called()
        mkdir.assert_not_called()
        open_mock.assert_not_called()

    def test_runtime_and_rules_hash_unchanged(self) -> None:
        runtime_queue = ROOT / "runtime" / "order_queue.json"
        rules_path = ROOT / "routines" / "지표추종매매" / "rules.json"
        before_runtime = _sha256(runtime_queue)
        before_rules = _sha256(rules_path)

        validate_execution_runtime_commit_plan_preview(self._ready_plan())

        self.assertEqual(before_runtime, _sha256(runtime_queue))
        self.assertEqual(before_rules, _sha256(rules_path))

    def test_module_has_no_write_commit_execution_send_order_gui_connections(self) -> None:
        import execution_runtime_commit_plan_validator

        module_text = execution_runtime_commit_plan_validator.__loader__.get_source(
            execution_runtime_commit_plan_validator.__name__
        )

        self.assertNotIn("write_text", module_text)
        self.assertNotIn("mkdir", module_text)
        self.assertNotIn("os.replace", module_text)
        self.assertNotIn("commit_execution_queue", module_text)
        self.assertNotIn("send_order", module_text)
        self.assertNotIn("ExecutionController", module_text)
        self.assertNotIn("QWidget", module_text)


if __name__ == "__main__":
    unittest.main()
