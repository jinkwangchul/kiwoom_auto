from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import unittest
from unittest import mock

from execution_runtime_commit_handoff_preview import (
    HANDOFF_TYPE,
    STATUS_BLOCKED,
    STATUS_READY,
    build_execution_runtime_commit_handoff_preview,
)
from execution_runtime_commit_request_contract import build_execution_runtime_commit_request
from execution_runtime_commit_request_validation_gate import evaluate_execution_runtime_commit_request_validation_gate
from execution_runtime_real_commit_readiness_policy import evaluate_execution_runtime_real_commit_readiness


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ExecutionRuntimeCommitHandoffPreviewTest(unittest.TestCase):
    def _runtime_api_result(self) -> dict:
        return {
            "api_type": "EXECUTION_RUNTIME_API",
            "status": "READY",
            "dry_run": True,
            "preview_only": True,
            "runtime_write": False,
            "issues": [],
            "warnings": [],
        }

    def _commit_plan(self) -> dict:
        return {
            "orchestrator_type": "EXECUTION_RUNTIME_COMMIT_PLAN_ORCHESTRATOR",
            "status": "READY",
            "commit_ready": True,
            "preview_only": True,
            "runtime_write": False,
            "issues": [],
            "warnings": [],
        }

    def _readiness(self, **overrides) -> dict:
        kwargs = {
            "runtime_api_result": self._runtime_api_result(),
            "commit_plan_orchestrator_result": self._commit_plan(),
            "order_executions_path": ROOT / "runtime" / "order_executions.json",
            "order_locks_path": ROOT / "runtime" / "order_locks.json",
            "confirmations": {
                "manual_execution_runtime_commit_confirmed": True,
                "manual_runtime_file_write_confirmed": True,
            },
            "environment_flags": {
                "real_runtime_commit_enabled": True,
                "allow_project_runtime_commit": True,
            },
        }
        kwargs.update(overrides)
        return evaluate_execution_runtime_real_commit_readiness(**kwargs)

    def _gate(self, **overrides) -> dict:
        request = build_execution_runtime_commit_request(self._readiness(**overrides))
        return evaluate_execution_runtime_commit_request_validation_gate(request)

    def test_approved_gate_builds_ready_handoff_preview(self) -> None:
        gate = self._gate()

        result = build_execution_runtime_commit_handoff_preview(gate)

        self.assertEqual(HANDOFF_TYPE, result["handoff_type"])
        self.assertEqual(STATUS_READY, result["status"])
        self.assertTrue(result["handoff_ready"])
        self.assertEqual(gate["request_fingerprint"], result["request_fingerprint"])
        self.assertEqual(gate["allowlist_decision"]["logical_target"], result["logical_target"])
        self.assertEqual(gate["allowlist_decision"]["operation"], result["operation"])
        self.assertEqual(gate["allowlist_decision"]["resolved_path"], result["runtime_target"])
        self.assertEqual(gate["allowlist_decision"]["relative_path"], result["relative_path"])
        self.assertEqual(gate["allowlist_decision"], result["allowlist_decision"])
        self.assertEqual(gate["validation_summary"], result["validation_summary"])
        self.assertTrue(result["preview_only"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["commit_service_called"])
        self.assertEqual([], result["issues"])

    def test_commit_service_input_preview_is_read_only_shape(self) -> None:
        gate = self._gate()

        result = build_execution_runtime_commit_handoff_preview(gate)
        preview = result["commit_service_input_preview"]

        self.assertEqual(result["request_fingerprint"], preview["request_fingerprint"])
        self.assertEqual(result["logical_target"], preview["logical_target"])
        self.assertEqual(result["operation"], preview["operation"])
        self.assertEqual(result["runtime_target"], preview["runtime_target"])
        self.assertEqual(result["relative_path"], preview["relative_path"])
        self.assertEqual(result["allowlist_decision"], preview["allowlist_decision"])
        self.assertEqual(result["validation_summary"], preview["validation_summary"])
        self.assertTrue(preview["preview_only"])
        self.assertFalse(preview["runtime_write"])

    def test_blocked_gate_is_fail_closed(self) -> None:
        gate = self._gate(logical_target="order_queue")

        result = build_execution_runtime_commit_handoff_preview(gate)

        self.assertEqual(STATUS_BLOCKED, result["status"])
        self.assertFalse(result["handoff_ready"])
        self.assertIn("VALIDATION_GATE_NOT_APPROVED", result["issues"])
        self.assertIn("ALLOWLIST_NOT_ALLOWED", result["issues"])

    def test_invalid_gate_is_fail_closed_for_tampered_fingerprint(self) -> None:
        request = build_execution_runtime_commit_request(self._readiness())
        request["request_fingerprint"] = "tampered"
        gate = evaluate_execution_runtime_commit_request_validation_gate(request)

        result = build_execution_runtime_commit_handoff_preview(gate)

        self.assertEqual("INVALID", result["status"])
        self.assertFalse(result["handoff_ready"])
        self.assertIn("REQUEST_FINGERPRINT_MISMATCH", result["issues"])
        self.assertIn("VALIDATION_GATE_NOT_APPROVED", result["issues"])

    def test_missing_fingerprint_is_invalid(self) -> None:
        gate = self._gate()
        gate["request_fingerprint"] = ""

        result = build_execution_runtime_commit_handoff_preview(gate)

        self.assertEqual("INVALID", result["status"])
        self.assertFalse(result["handoff_ready"])
        self.assertIn("MISSING_REQUEST_FINGERPRINT", result["issues"])

    def test_preview_only_false_is_invalid(self) -> None:
        gate = self._gate()
        gate["preview_only"] = False

        result = build_execution_runtime_commit_handoff_preview(gate)

        self.assertEqual("INVALID", result["status"])
        self.assertFalse(result["handoff_ready"])
        self.assertIn("PREVIEW_ONLY_REQUIRED", result["issues"])

    def test_runtime_write_true_is_invalid(self) -> None:
        gate = self._gate()
        gate["runtime_write"] = True

        result = build_execution_runtime_commit_handoff_preview(gate)

        self.assertEqual("INVALID", result["status"])
        self.assertFalse(result["handoff_ready"])
        self.assertIn("RUNTIME_WRITE_MUST_BE_FALSE", result["issues"])

    def test_runtime_target_missing_is_invalid(self) -> None:
        gate = self._gate()
        gate["allowlist_decision"]["resolved_path"] = ""
        gate["allowlist_decision"]["normalized_path"] = ""

        result = build_execution_runtime_commit_handoff_preview(gate)

        self.assertEqual("INVALID", result["status"])
        self.assertFalse(result["handoff_ready"])
        self.assertIn("MISSING_RUNTIME_TARGET", result["issues"])

    def test_allowlist_missing_or_blocked_is_fail_closed(self) -> None:
        missing = self._gate()
        missing.pop("allowlist_decision")
        blocked = self._gate()
        blocked["allowlist_decision"]["allowed"] = False
        blocked["allowlist_decision"]["blocked_reason"] = "TEST_BLOCK"

        missing_result = build_execution_runtime_commit_handoff_preview(missing)
        blocked_result = build_execution_runtime_commit_handoff_preview(blocked)

        self.assertEqual("INVALID", missing_result["status"])
        self.assertFalse(missing_result["handoff_ready"])
        self.assertIn("MISSING_ALLOWLIST_DECISION", missing_result["issues"])
        self.assertEqual(STATUS_BLOCKED, blocked_result["status"])
        self.assertFalse(blocked_result["handoff_ready"])
        self.assertTrue(
            any(str(issue).startswith("ALLOWLIST_DECISION_NOT_ALLOWED") for issue in blocked_result["issues"])
        )

    def test_result_mutation_does_not_modify_input(self) -> None:
        gate = self._gate()
        before = deepcopy(gate)

        result = build_execution_runtime_commit_handoff_preview(gate)
        result["allowlist_decision"]["logical_target"] = "changed"
        result["commit_service_input_preview"]["validation_summary"]["policy_allowed"] = False
        result["issues"].append("RESULT_ONLY")

        self.assertEqual(before, gate)

    def test_no_commit_service_runtime_write_queue_or_send_order(self) -> None:
        runtime_paths = [
            ROOT / "runtime" / "order_queue.json",
            ROOT / "runtime" / "order_executions.json",
            ROOT / "runtime" / "order_locks.json",
        ]
        before_runtime = {str(path): _sha256(path) for path in runtime_paths}
        with (
            mock.patch("execution_runtime_commit_service.commit_execution_runtime_plan") as runtime_commit,
            mock.patch("execution_runtime_file_init_commit_service.commit_execution_runtime_file_init_plan") as init_commit,
            mock.patch("execution_queue_commit_service.commit_execution_queue_manually") as queue_commit,
            mock.patch("send_order_entrypoint.execute_send_order") as send_order,
        ):
            result = build_execution_runtime_commit_handoff_preview(self._gate())

        self.assertEqual(STATUS_READY, result["status"])
        runtime_commit.assert_not_called()
        init_commit.assert_not_called()
        queue_commit.assert_not_called()
        send_order.assert_not_called()
        self.assertEqual(before_runtime, {str(path): _sha256(path) for path in runtime_paths})
        self.assertFalse((ROOT / "runtime" / "order_executions.json").exists())
        self.assertFalse((ROOT / "runtime" / "order_locks.json").exists())


if __name__ == "__main__":
    unittest.main()
