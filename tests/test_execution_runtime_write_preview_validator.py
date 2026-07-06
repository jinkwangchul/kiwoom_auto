from __future__ import annotations

import copy
import hashlib
from pathlib import Path
import unittest
from unittest import mock

from execution_runtime_write_preview import WRITE_PREVIEW_TYPE
from execution_runtime_write_preview_validator import validate_execution_runtime_write_preview


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ExecutionRuntimeWritePreviewValidatorTest(unittest.TestCase):
    def _ready_preview(self) -> dict:
        return {
            "status": "READY",
            "preview_only": True,
            "runtime_write": False,
            "write_preview_type": WRITE_PREVIEW_TYPE,
            "execution_record_preview": {
                "execution_id": "EXEC_1",
                "order_id": "ORDER_1",
                "request_hash": "HASH_1",
                "lock_id": "LOCK_1",
            },
            "lock_record_preview": {
                "lock_id": "LOCK_1",
                "order_id": "ORDER_1",
                "request_hash": "HASH_1",
                "execution_id": "EXEC_1",
            },
            "duplicate_checks": {
                "execution_id": "EXEC_1",
                "request_hash": "HASH_1",
                "order_id": "ORDER_1",
                "lock_id": "LOCK_1",
            },
            "would_write_targets": {
                "order_executions": "runtime/order_executions.json",
                "order_locks": "runtime/order_locks.json",
            },
            "issues": [],
            "warnings": ["Preview mode"],
        }

    def _blocked_preview(self) -> dict:
        data = self._ready_preview()
        data["status"] = "BLOCKED"
        data["execution_record_preview"] = None
        data["lock_record_preview"] = None
        data["issues"] = ["DUPLICATE_EXECUTION_ID"]
        return data

    def _invalid_preview(self) -> dict:
        data = self._ready_preview()
        data["status"] = "INVALID"
        data["execution_record_preview"] = None
        data["lock_record_preview"] = None
        data["issues"] = ["MISSING_EXECUTION_ID"]
        return data

    def test_valid_ready(self) -> None:
        result = validate_execution_runtime_write_preview(self._ready_preview())

        self.assertTrue(result["valid"])
        self.assertEqual("READY", result["status"])
        self.assertTrue(result["preview_only"])
        self.assertFalse(result["runtime_write"])
        self.assertEqual([], result["issues"])
        self.assertEqual(["Preview mode"], result["warnings"])

    def test_valid_blocked(self) -> None:
        result = validate_execution_runtime_write_preview(self._blocked_preview())

        self.assertTrue(result["valid"])
        self.assertEqual("BLOCKED", result["status"])
        self.assertEqual([], result["issues"])

    def test_valid_invalid(self) -> None:
        result = validate_execution_runtime_write_preview(self._invalid_preview())

        self.assertTrue(result["valid"])
        self.assertEqual("INVALID", result["status"])
        self.assertEqual([], result["issues"])

    def test_invalid_write_preview_type(self) -> None:
        preview = self._ready_preview()
        preview["write_preview_type"] = "WRONG"

        result = validate_execution_runtime_write_preview(preview)

        self.assertFalse(result["valid"])
        self.assertIn("INVALID_WRITE_PREVIEW_TYPE", result["issues"])

    def test_invalid_preview_only_and_runtime_write(self) -> None:
        preview = self._ready_preview()
        preview["preview_only"] = False
        preview["runtime_write"] = True

        result = validate_execution_runtime_write_preview(preview)

        self.assertFalse(result["valid"])
        self.assertIn("PREVIEW_ONLY_REQUIRED", result["issues"])
        self.assertIn("RUNTIME_WRITE_MUST_BE_FALSE", result["issues"])

    def test_invalid_status(self) -> None:
        preview = self._ready_preview()
        preview["status"] = "WAITING"

        result = validate_execution_runtime_write_preview(preview)

        self.assertFalse(result["valid"])
        self.assertEqual("INVALID", result["status"])
        self.assertIn("INVALID_STATUS", result["issues"])

    def test_ready_missing_execution_record_preview(self) -> None:
        preview = self._ready_preview()
        preview["execution_record_preview"] = None

        result = validate_execution_runtime_write_preview(preview)

        self.assertFalse(result["valid"])
        self.assertIn("MISSING_EXECUTION_RECORD_PREVIEW", result["issues"])

    def test_ready_missing_lock_record_preview(self) -> None:
        preview = self._ready_preview()
        preview["lock_record_preview"] = None

        result = validate_execution_runtime_write_preview(preview)

        self.assertFalse(result["valid"])
        self.assertIn("MISSING_LOCK_RECORD_PREVIEW", result["issues"])

    def test_ready_with_issues(self) -> None:
        preview = self._ready_preview()
        preview["issues"] = ["SHOULD_NOT_BE_READY"]

        result = validate_execution_runtime_write_preview(preview)

        self.assertFalse(result["valid"])
        self.assertIn("READY_WITH_ISSUES", result["issues"])

    def test_invalid_without_issues(self) -> None:
        preview = self._invalid_preview()
        preview["issues"] = []

        result = validate_execution_runtime_write_preview(preview)

        self.assertFalse(result["valid"])
        self.assertIn("INVALID_WITHOUT_ISSUES", result["issues"])

    def test_blocked_without_issues_or_warnings(self) -> None:
        preview = self._blocked_preview()
        preview["issues"] = []
        preview["warnings"] = []

        result = validate_execution_runtime_write_preview(preview)

        self.assertFalse(result["valid"])
        self.assertIn("BLOCKED_WITHOUT_ISSUES_OR_WARNINGS", result["issues"])

    def test_malformed_duplicate_checks_issues_warnings(self) -> None:
        preview = self._ready_preview()
        preview["duplicate_checks"] = []
        preview["issues"] = {}
        preview["warnings"] = {}

        result = validate_execution_runtime_write_preview(preview)

        self.assertFalse(result["valid"])
        self.assertIn("DUPLICATE_CHECKS_MUST_BE_DICT", result["issues"])
        self.assertIn("ISSUES_MUST_BE_LIST", result["issues"])
        self.assertIn("WARNINGS_MUST_BE_LIST", result["issues"])

    def test_missing_would_write_targets(self) -> None:
        preview = self._ready_preview()
        preview["would_write_targets"] = {}

        result = validate_execution_runtime_write_preview(preview)

        self.assertFalse(result["valid"])
        self.assertIn("MISSING_TARGET_ORDER_EXECUTIONS", result["issues"])
        self.assertIn("MISSING_TARGET_ORDER_LOCKS", result["issues"])

    def test_missing_execution_record_required_fields(self) -> None:
        cases = [
            ("execution_id", "MISSING_EXECUTION_RECORD_EXECUTION_ID"),
            ("order_id", "MISSING_EXECUTION_RECORD_ORDER_ID"),
            ("request_hash", "MISSING_EXECUTION_RECORD_REQUEST_HASH"),
        ]
        for field, issue in cases:
            with self.subTest(field=field):
                preview = self._ready_preview()
                preview["execution_record_preview"][field] = ""
                result = validate_execution_runtime_write_preview(preview)
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
                preview = self._ready_preview()
                preview["lock_record_preview"][field] = ""
                result = validate_execution_runtime_write_preview(preview)
                self.assertFalse(result["valid"])
                self.assertIn(issue, result["issues"])

    def test_malformed_input(self) -> None:
        result = validate_execution_runtime_write_preview("bad")

        self.assertFalse(result["valid"])
        self.assertEqual("INVALID", result["status"])
        self.assertIn("MALFORMED_WRITE_PREVIEW", result["issues"])

    def test_input_immutability(self) -> None:
        preview = self._ready_preview()
        before = copy.deepcopy(preview)

        result = validate_execution_runtime_write_preview(preview)
        result["issues"].append("MUTATED_RESULT_ONLY")

        self.assertEqual(before, preview)

    def test_no_file_write_or_mkdir(self) -> None:
        with (
            mock.patch("pathlib.Path.write_text") as write_text,
            mock.patch("pathlib.Path.mkdir") as mkdir,
            mock.patch("builtins.open", mock.mock_open()) as open_mock,
        ):
            result = validate_execution_runtime_write_preview(self._ready_preview())

        self.assertTrue(result["valid"])
        write_text.assert_not_called()
        mkdir.assert_not_called()
        open_mock.assert_not_called()

    def test_runtime_and_rules_hash_unchanged(self) -> None:
        runtime_queue = ROOT / "runtime" / "order_queue.json"
        rules_path = ROOT / "routines" / "지표추종매매" / "rules.json"
        before_runtime = _sha256(runtime_queue)
        before_rules = _sha256(rules_path)

        validate_execution_runtime_write_preview(self._ready_preview())

        self.assertEqual(before_runtime, _sha256(runtime_queue))
        self.assertEqual(before_rules, _sha256(rules_path))

    def test_module_has_no_write_commit_execution_send_order_gui_connections(self) -> None:
        import execution_runtime_write_preview_validator

        module_text = execution_runtime_write_preview_validator.__loader__.get_source(
            execution_runtime_write_preview_validator.__name__
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
