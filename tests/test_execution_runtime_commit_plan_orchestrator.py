from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import unittest
from unittest import mock

from execution_runtime_commit_plan_orchestrator import (
    ORCHESTRATOR_TYPE,
    run_execution_runtime_commit_plan_orchestrator,
)


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ExecutionRuntimeCommitPlanOrchestratorTest(unittest.TestCase):
    def _write_preview(self, *, status: str = "READY") -> dict:
        return {
            "status": status,
            "preview_only": True,
            "runtime_write": False,
            "write_preview_type": "EXECUTION_RUNTIME_WRITE_PREVIEW",
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
            "duplicate_checks": {},
            "would_write_targets": {
                "order_executions": "runtime/order_executions.json",
                "order_locks": "runtime/order_locks.json",
            },
            "issues": [] if status == "READY" else [f"WRITE_PREVIEW_{status}"],
            "warnings": ["Preview mode"],
        }

    def _write_orchestrator(self, *, status: str = "READY") -> dict:
        write_preview = self._write_preview(status=status)
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

    def test_ready_flow(self) -> None:
        result = run_execution_runtime_commit_plan_orchestrator(
            self._write_orchestrator(),
            self._gate(),
        )

        self.assertEqual(ORCHESTRATOR_TYPE, result["orchestrator_type"])
        self.assertEqual("READY", result["status"])
        self.assertTrue(result["commit_ready"])
        self.assertTrue(result["preview_only"])
        self.assertFalse(result["runtime_write"])
        self.assertEqual("READY", result["commit_plan"]["status"])
        self.assertTrue(result["validation"]["valid"])
        self.assertEqual([], result["issues"])

    def test_blocked_flow(self) -> None:
        result = run_execution_runtime_commit_plan_orchestrator(
            self._write_orchestrator(),
            self._gate(status="BLOCKED", commit_ready=False),
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertFalse(result["commit_ready"])
        self.assertEqual("BLOCKED", result["commit_plan"]["status"])
        self.assertTrue(result["validation"]["valid"])
        self.assertIn("GATE_BLOCKED", result["issues"])

    def test_invalid_flow(self) -> None:
        result = run_execution_runtime_commit_plan_orchestrator(
            "bad",
            self._gate(),
        )

        self.assertEqual("INVALID", result["status"])
        self.assertFalse(result["commit_ready"])
        self.assertEqual("INVALID", result["commit_plan"]["status"])
        self.assertFalse(result["validation"]["valid"])
        self.assertIn("MALFORMED_WRITE_PREVIEW_ORCHESTRATOR_RESULT", result["issues"])

    def test_validator_invalid_flow(self) -> None:
        invalid_validation = {
            "valid": False,
            "status": "INVALID",
            "preview_only": True,
            "runtime_write": False,
            "issues": ["VALIDATOR_FORCED_INVALID"],
            "warnings": [],
        }

        with mock.patch(
            "execution_runtime_commit_plan_orchestrator.validate_execution_runtime_commit_plan_preview",
            return_value=invalid_validation,
        ):
            result = run_execution_runtime_commit_plan_orchestrator(
                self._write_orchestrator(),
                self._gate(),
            )

        self.assertEqual("INVALID", result["status"])
        self.assertFalse(result["commit_ready"])
        self.assertEqual("READY", result["commit_plan"]["status"])
        self.assertEqual(invalid_validation, result["validation"])
        self.assertIn("VALIDATOR_FORCED_INVALID", result["issues"])

    def test_commit_ready_is_preserved_for_ready_only(self) -> None:
        ready = run_execution_runtime_commit_plan_orchestrator(
            self._write_orchestrator(),
            self._gate(),
        )
        blocked = run_execution_runtime_commit_plan_orchestrator(
            self._write_orchestrator(),
            self._gate(status="BLOCKED", commit_ready=False),
        )

        self.assertTrue(ready["commit_ready"])
        self.assertFalse(blocked["commit_ready"])

    def test_inputs_are_not_mutated(self) -> None:
        write_orchestrator = self._write_orchestrator()
        gate = self._gate()
        before = (deepcopy(write_orchestrator), deepcopy(gate))

        result = run_execution_runtime_commit_plan_orchestrator(write_orchestrator, gate)
        result["commit_plan"]["planned_records"]["execution"]["execution_id"] = "MUTATED_RESULT_ONLY"
        result["validation"]["issues"].append("MUTATED_RESULT_ONLY")

        self.assertEqual(before[0], write_orchestrator)
        self.assertEqual(before[1], gate)

    def test_no_file_write_or_mkdir(self) -> None:
        with (
            mock.patch("pathlib.Path.write_text") as write_text,
            mock.patch("pathlib.Path.mkdir") as mkdir,
            mock.patch("builtins.open", mock.mock_open()) as open_mock,
        ):
            result = run_execution_runtime_commit_plan_orchestrator(
                self._write_orchestrator(),
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

        run_execution_runtime_commit_plan_orchestrator(self._write_orchestrator(), self._gate())

        self.assertEqual(before_runtime, _sha256(runtime_queue))
        self.assertEqual(before_rules, _sha256(rules_path))

    def test_module_has_no_write_commit_execution_send_order_gui_connections(self) -> None:
        import execution_runtime_commit_plan_orchestrator

        module_text = execution_runtime_commit_plan_orchestrator.__loader__.get_source(
            execution_runtime_commit_plan_orchestrator.__name__
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
