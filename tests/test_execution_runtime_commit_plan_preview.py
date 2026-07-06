from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import unittest
from unittest import mock

from execution_runtime_commit_plan_preview import (
    PLAN_TYPE,
    build_execution_runtime_commit_plan_preview,
)


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ExecutionRuntimeCommitPlanPreviewTest(unittest.TestCase):
    def _write_preview(self, *, status: str = "READY", missing_execution: bool = False, missing_lock: bool = False) -> dict:
        execution_record = None if missing_execution else {
            "execution_id": "EXEC_1",
            "order_id": "ORDER_1",
            "request_hash": "HASH_1",
            "lock_id": "LOCK_1",
        }
        lock_record = None if missing_lock else {
            "lock_id": "LOCK_1",
            "order_id": "ORDER_1",
            "request_hash": "HASH_1",
            "execution_id": "EXEC_1",
        }
        return {
            "status": status,
            "preview_only": True,
            "runtime_write": False,
            "write_preview_type": "EXECUTION_RUNTIME_WRITE_PREVIEW",
            "execution_record_preview": execution_record,
            "lock_record_preview": lock_record,
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
            "issues": [] if status == "READY" else [f"WRITE_PREVIEW_{status}"],
            "warnings": ["Preview mode"],
        }

    def _orchestrator(self, *, status: str = "READY", missing_execution: bool = False, missing_lock: bool = False) -> dict:
        write_preview = self._write_preview(
            status=status,
            missing_execution=missing_execution,
            missing_lock=missing_lock,
        )
        return {
            "status": status,
            "preview_only": True,
            "runtime_write": False,
            "orchestrator_type": "EXECUTION_RUNTIME_WRITE_PREVIEW_ORCHESTRATOR",
            "write_preview": write_preview,
            "validation": {"valid": status != "INVALID"},
            "issues": [] if status == "READY" else [f"ORCHESTRATOR_{status}"],
            "warnings": ["Preview mode"],
        }

    def _gate(self, *, status: str = "READY", commit_ready: bool = True) -> dict:
        return {
            "gate_type": "EXECUTION_RUNTIME_COMMIT_READINESS_GATE",
            "status": status,
            "commit_ready": commit_ready,
            "preview_only": True,
            "runtime_write": False,
            "required_confirmations": {
                "manual_execution_runtime_commit_confirmed": commit_ready,
                "manual_runtime_file_write_confirmed": commit_ready,
            },
            "issues": [] if status == "READY" and commit_ready else [f"GATE_{status}"],
            "warnings": ["Preview mode"],
        }

    def test_ready_commit_plan(self) -> None:
        result = build_execution_runtime_commit_plan_preview(
            self._orchestrator(),
            self._gate(),
        )

        self.assertEqual(PLAN_TYPE, result["plan_type"])
        self.assertEqual("READY", result["status"])
        self.assertTrue(result["commit_ready"])
        self.assertTrue(result["preview_only"])
        self.assertFalse(result["runtime_write"])
        self.assertEqual(
            "runtime/order_executions.json",
            result["planned_targets"]["order_executions"],
        )
        self.assertEqual("EXEC_1", result["planned_records"]["execution"]["execution_id"])
        self.assertEqual("LOCK_1", result["planned_records"]["lock"]["lock_id"])
        self.assertEqual([], result["issues"])

    def test_blocked_gate_not_confirmed(self) -> None:
        result = build_execution_runtime_commit_plan_preview(
            self._orchestrator(),
            self._gate(status="BLOCKED", commit_ready=False),
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertFalse(result["commit_ready"])
        self.assertIn("GATE_BLOCKED", result["issues"])

    def test_blocked_write_preview_blocked(self) -> None:
        result = build_execution_runtime_commit_plan_preview(
            self._orchestrator(status="BLOCKED"),
            self._gate(),
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertFalse(result["commit_ready"])
        self.assertIn("ORCHESTRATOR_BLOCKED", result["issues"])

    def test_invalid_malformed_write_preview(self) -> None:
        result = build_execution_runtime_commit_plan_preview("bad", self._gate())

        self.assertEqual("INVALID", result["status"])
        self.assertFalse(result["commit_ready"])
        self.assertIn("MALFORMED_WRITE_PREVIEW_ORCHESTRATOR_RESULT", result["issues"])

    def test_invalid_malformed_gate_result(self) -> None:
        result = build_execution_runtime_commit_plan_preview(self._orchestrator(), "bad")

        self.assertEqual("INVALID", result["status"])
        self.assertFalse(result["commit_ready"])
        self.assertIn("MALFORMED_COMMIT_READINESS_GATE_RESULT", result["issues"])

    def test_invalid_gate_invalid(self) -> None:
        result = build_execution_runtime_commit_plan_preview(
            self._orchestrator(),
            self._gate(status="INVALID", commit_ready=False),
        )

        self.assertEqual("INVALID", result["status"])
        self.assertFalse(result["commit_ready"])
        self.assertIn("GATE_INVALID", result["issues"])

    def test_missing_planned_execution_record(self) -> None:
        result = build_execution_runtime_commit_plan_preview(
            self._orchestrator(missing_execution=True),
            self._gate(),
        )

        self.assertEqual("INVALID", result["status"])
        self.assertIn("MISSING_PLANNED_EXECUTION_RECORD", result["issues"])

    def test_missing_planned_lock_record(self) -> None:
        result = build_execution_runtime_commit_plan_preview(
            self._orchestrator(missing_lock=True),
            self._gate(),
        )

        self.assertEqual("INVALID", result["status"])
        self.assertIn("MISSING_PLANNED_LOCK_RECORD", result["issues"])

    def test_preview_only_runtime_write_boundaries(self) -> None:
        result = build_execution_runtime_commit_plan_preview(
            self._orchestrator(),
            self._gate(),
        )

        self.assertTrue(result["preview_only"])
        self.assertFalse(result["runtime_write"])

    def test_input_immutability(self) -> None:
        orchestrator = self._orchestrator()
        gate = self._gate()
        before = (deepcopy(orchestrator), deepcopy(gate))

        result = build_execution_runtime_commit_plan_preview(orchestrator, gate)
        result["planned_records"]["execution"]["execution_id"] = "MUTATED_RESULT_ONLY"
        result["planned_targets"]["order_locks"] = "changed"

        self.assertEqual(before[0], orchestrator)
        self.assertEqual(before[1], gate)

    def test_no_file_write_or_mkdir(self) -> None:
        with (
            mock.patch("pathlib.Path.write_text") as write_text,
            mock.patch("pathlib.Path.mkdir") as mkdir,
            mock.patch("builtins.open", mock.mock_open()) as open_mock,
        ):
            result = build_execution_runtime_commit_plan_preview(
                self._orchestrator(),
                self._gate(),
            )

        self.assertEqual("READY", result["status"])
        write_text.assert_not_called()
        mkdir.assert_not_called()
        open_mock.assert_not_called()

    def test_runtime_and_rules_hash_unchanged(self) -> None:
        runtime_queue = ROOT / "runtime" / "order_queue.json"
        rules_path = ROOT / "routines" / "지표추종매매" / "rules.json"
        before_runtime = _sha256(runtime_queue)
        before_rules = _sha256(rules_path)

        build_execution_runtime_commit_plan_preview(self._orchestrator(), self._gate())

        self.assertEqual(before_runtime, _sha256(runtime_queue))
        self.assertEqual(before_rules, _sha256(rules_path))

    def test_module_has_no_write_commit_execution_send_order_gui_connections(self) -> None:
        import execution_runtime_commit_plan_preview

        module_text = execution_runtime_commit_plan_preview.__loader__.get_source(
            execution_runtime_commit_plan_preview.__name__
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
