from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import unittest
from unittest import mock

from execution_runtime_commit_plan_orchestrator import run_execution_runtime_commit_plan_orchestrator
from execution_runtime_commit_plan_preview import build_execution_runtime_commit_plan_preview
from execution_runtime_commit_plan_validator import validate_execution_runtime_commit_plan_preview
from execution_runtime_commit_readiness_gate import evaluate_execution_runtime_commit_readiness


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ExecutionRuntimeCommitPlanContractTest(unittest.TestCase):
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
        return {
            "status": status,
            "preview_only": True,
            "runtime_write": False,
            "orchestrator_type": "EXECUTION_RUNTIME_WRITE_PREVIEW_ORCHESTRATOR",
            "write_preview": self._write_preview(status=status),
            "validation": {"valid": status != "INVALID"},
            "issues": [] if status == "READY" else [f"ORCHESTRATOR_{status}"],
            "warnings": ["Preview mode"],
        }

    def _ready_gate(self, write_orchestrator: dict | None = None) -> dict:
        return evaluate_execution_runtime_commit_readiness(
            self._write_orchestrator() if write_orchestrator is None else write_orchestrator,
            manual_execution_runtime_commit_confirmed=True,
            manual_runtime_file_write_confirmed=True,
        )

    def test_ready_gate_and_ready_write_preview_make_ready_orchestrator(self) -> None:
        write_orchestrator = self._write_orchestrator()
        gate = self._ready_gate(write_orchestrator)
        plan = build_execution_runtime_commit_plan_preview(write_orchestrator, gate)
        validation = validate_execution_runtime_commit_plan_preview(plan)
        orchestrated = run_execution_runtime_commit_plan_orchestrator(write_orchestrator, gate)

        self.assertEqual("READY", gate["status"])
        self.assertEqual("READY", plan["status"])
        self.assertTrue(validation["valid"])
        self.assertEqual("READY", orchestrated["status"])
        self.assertTrue(orchestrated["commit_ready"])

    def test_missing_confirmation_preserves_blocked(self) -> None:
        write_orchestrator = self._write_orchestrator()
        gate = evaluate_execution_runtime_commit_readiness(
            write_orchestrator,
            manual_execution_runtime_commit_confirmed=True,
            manual_runtime_file_write_confirmed=False,
        )
        orchestrated = run_execution_runtime_commit_plan_orchestrator(write_orchestrator, gate)

        self.assertEqual("BLOCKED", gate["status"])
        self.assertEqual("BLOCKED", orchestrated["status"])
        self.assertFalse(orchestrated["commit_ready"])
        self.assertIn("MANUAL_RUNTIME_FILE_WRITE_CONFIRMATION_REQUIRED", orchestrated["issues"])

    def test_write_preview_blocked_preserves_blocked(self) -> None:
        write_orchestrator = self._write_orchestrator(status="BLOCKED")
        gate = self._ready_gate(write_orchestrator)
        orchestrated = run_execution_runtime_commit_plan_orchestrator(write_orchestrator, gate)

        self.assertEqual("BLOCKED", gate["status"])
        self.assertEqual("BLOCKED", orchestrated["status"])
        self.assertFalse(orchestrated["commit_ready"])
        self.assertIn("ORCHESTRATOR_BLOCKED", orchestrated["issues"])

    def test_malformed_input_preserves_invalid(self) -> None:
        gate = self._ready_gate()
        orchestrated = run_execution_runtime_commit_plan_orchestrator("malformed", gate)

        self.assertEqual("INVALID", orchestrated["status"])
        self.assertFalse(orchestrated["commit_ready"])
        self.assertIn("MALFORMED_WRITE_PREVIEW_ORCHESTRATOR_RESULT", orchestrated["issues"])

    def test_commit_ready_is_true_only_when_ready(self) -> None:
        ready = run_execution_runtime_commit_plan_orchestrator(
            self._write_orchestrator(),
            self._ready_gate(),
        )
        blocked = run_execution_runtime_commit_plan_orchestrator(
            self._write_orchestrator(),
            evaluate_execution_runtime_commit_readiness(
                self._write_orchestrator(),
                manual_execution_runtime_commit_confirmed=False,
                manual_runtime_file_write_confirmed=True,
            ),
        )

        self.assertTrue(ready["commit_ready"])
        self.assertEqual("READY", ready["status"])
        self.assertFalse(blocked["commit_ready"])
        self.assertEqual("BLOCKED", blocked["status"])

    def test_preview_only_runtime_write_boundaries_are_preserved(self) -> None:
        orchestrated = run_execution_runtime_commit_plan_orchestrator(
            self._write_orchestrator(),
            self._ready_gate(),
        )

        self.assertTrue(orchestrated["preview_only"])
        self.assertFalse(orchestrated["runtime_write"])
        self.assertTrue(orchestrated["commit_plan"]["preview_only"])
        self.assertFalse(orchestrated["commit_plan"]["runtime_write"])
        self.assertTrue(orchestrated["validation"]["preview_only"])
        self.assertFalse(orchestrated["validation"]["runtime_write"])

    def test_planned_targets_are_preserved(self) -> None:
        write_orchestrator = self._write_orchestrator()
        orchestrated = run_execution_runtime_commit_plan_orchestrator(
            write_orchestrator,
            self._ready_gate(write_orchestrator),
        )

        self.assertEqual(
            write_orchestrator["write_preview"]["would_write_targets"],
            orchestrated["commit_plan"]["planned_targets"],
        )

    def test_planned_execution_and_lock_records_are_preserved(self) -> None:
        write_orchestrator = self._write_orchestrator()
        orchestrated = run_execution_runtime_commit_plan_orchestrator(
            write_orchestrator,
            self._ready_gate(write_orchestrator),
        )

        self.assertEqual(
            write_orchestrator["write_preview"]["execution_record_preview"],
            orchestrated["commit_plan"]["planned_records"]["execution"],
        )
        self.assertEqual(
            write_orchestrator["write_preview"]["lock_record_preview"],
            orchestrated["commit_plan"]["planned_records"]["lock"],
        )

    def test_required_confirmations_are_preserved(self) -> None:
        write_orchestrator = self._write_orchestrator()
        gate = self._ready_gate(write_orchestrator)
        orchestrated = run_execution_runtime_commit_plan_orchestrator(write_orchestrator, gate)

        self.assertEqual(
            gate["required_confirmations"],
            orchestrated["commit_plan"]["required_confirmations"],
        )

    def test_issues_and_warnings_are_preserved(self) -> None:
        write_orchestrator = self._write_orchestrator()
        gate = evaluate_execution_runtime_commit_readiness(
            write_orchestrator,
            manual_execution_runtime_commit_confirmed=True,
            manual_runtime_file_write_confirmed=False,
        )
        orchestrated = run_execution_runtime_commit_plan_orchestrator(write_orchestrator, gate)

        self.assertIn("MANUAL_RUNTIME_FILE_WRITE_CONFIRMATION_REQUIRED", orchestrated["issues"])
        self.assertIn("Preview mode", orchestrated["warnings"])

    def test_inputs_remain_unchanged_across_contract_flow(self) -> None:
        write_orchestrator = self._write_orchestrator()
        gate = self._ready_gate(write_orchestrator)
        before = (deepcopy(write_orchestrator), deepcopy(gate))

        orchestrated = run_execution_runtime_commit_plan_orchestrator(write_orchestrator, gate)
        orchestrated["commit_plan"]["planned_records"]["execution"]["execution_id"] = "MUTATED"
        orchestrated["commit_plan"]["planned_targets"]["order_locks"] = "changed"

        self.assertEqual(before[0], write_orchestrator)
        self.assertEqual(before[1], gate)

    def test_no_file_write_or_mkdir(self) -> None:
        with (
            mock.patch("pathlib.Path.write_text") as write_text,
            mock.patch("pathlib.Path.mkdir") as mkdir,
            mock.patch("builtins.open", mock.mock_open()) as open_mock,
        ):
            orchestrated = run_execution_runtime_commit_plan_orchestrator(
                self._write_orchestrator(),
                self._ready_gate(),
            )

        self.assertEqual("READY", orchestrated["status"])
        write_text.assert_not_called()
        mkdir.assert_not_called()
        open_mock.assert_not_called()

    def test_runtime_and_rules_hash_unchanged(self) -> None:
        runtime_queue = ROOT / "runtime" / "order_queue.json"
        rules_path = ROOT / "routines" / "지표추종매매" / "rules.json"
        before_runtime = _sha256(runtime_queue)
        before_rules = _sha256(rules_path)

        run_execution_runtime_commit_plan_orchestrator(self._write_orchestrator(), self._ready_gate())

        self.assertEqual(before_runtime, _sha256(runtime_queue))
        self.assertEqual(before_rules, _sha256(rules_path))


if __name__ == "__main__":
    unittest.main()
