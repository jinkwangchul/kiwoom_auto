from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import unittest
from unittest import mock

from execution_runtime_allowlist import OPERATION_WRITE
from execution_runtime_commit_request_contract import build_execution_runtime_commit_request
from execution_runtime_commit_request_validation_gate import (
    GATE_TYPE,
    STATUS_APPROVED,
    evaluate_execution_runtime_commit_request_validation_gate,
)
from execution_runtime_real_commit_readiness_policy import evaluate_execution_runtime_real_commit_readiness


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ExecutionRuntimeCommitRequestValidationGateTest(unittest.TestCase):
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

    def _request(self, **overrides) -> dict:
        return build_execution_runtime_commit_request(self._readiness(**overrides))

    def test_ready_contract_is_approved(self) -> None:
        request = self._request()
        result = evaluate_execution_runtime_commit_request_validation_gate(request)

        self.assertEqual(GATE_TYPE, result["gate_type"])
        self.assertEqual(STATUS_APPROVED, result["status"])
        self.assertTrue(result["commit_request_approved"])
        self.assertTrue(result["preview_only"])
        self.assertFalse(result["runtime_write"])
        self.assertEqual(request["request_fingerprint"], result["request_fingerprint"])
        self.assertEqual([], result["issues"])

    def test_tampered_fingerprint_is_blocked(self) -> None:
        request = self._request()
        request["request_fingerprint"] = "tampered"

        result = evaluate_execution_runtime_commit_request_validation_gate(request)

        self.assertEqual("INVALID", result["status"])
        self.assertFalse(result["commit_request_approved"])
        self.assertIn("REQUEST_FINGERPRINT_MISMATCH", result["issues"])

    def test_allowlist_blocked_contract_is_blocked(self) -> None:
        request = self._request(logical_target="order_queue")

        result = evaluate_execution_runtime_commit_request_validation_gate(request)

        self.assertEqual("BLOCKED", result["status"])
        self.assertFalse(result["commit_request_approved"])
        self.assertIn("COMMIT_REQUEST_STATUS_NOT_READY", result["issues"])
        self.assertIn("ALLOWLIST_NOT_ALLOWED", result["issues"])

    def test_write_operation_contract_is_blocked(self) -> None:
        request = self._request(allowlist_operation=OPERATION_WRITE)

        result = evaluate_execution_runtime_commit_request_validation_gate(request)

        self.assertEqual("BLOCKED", result["status"])
        self.assertFalse(result["commit_request_approved"])
        self.assertTrue(any(str(issue).startswith("ALLOWLIST_DECISION_NOT_ALLOWED") for issue in result["issues"]))

    def test_runtime_write_true_is_invalid(self) -> None:
        request = self._request()
        request["runtime_write"] = True

        result = evaluate_execution_runtime_commit_request_validation_gate(request)

        self.assertEqual("INVALID", result["status"])
        self.assertFalse(result["commit_request_approved"])
        self.assertIn("RUNTIME_WRITE_MUST_BE_FALSE", result["issues"])

    def test_preview_only_false_is_invalid(self) -> None:
        request = self._request()
        request["preview_only"] = False

        result = evaluate_execution_runtime_commit_request_validation_gate(request)

        self.assertEqual("INVALID", result["status"])
        self.assertFalse(result["commit_request_approved"])
        self.assertIn("PREVIEW_ONLY_REQUIRED", result["issues"])

    def test_missing_or_wrong_type_is_invalid(self) -> None:
        malformed = deepcopy(self._request())
        malformed.pop("allowlist_decision")
        malformed["issues"] = "bad"

        result = evaluate_execution_runtime_commit_request_validation_gate(malformed)

        self.assertEqual("INVALID", result["status"])
        self.assertFalse(result["commit_request_approved"])
        self.assertIn("ALLOWLIST_DECISION_MUST_BE_DICT", result["issues"])
        self.assertIn("ISSUES_MUST_BE_LIST", result["issues"])

    def test_invalid_non_dict_input_is_fail_closed(self) -> None:
        result = evaluate_execution_runtime_commit_request_validation_gate("bad")

        self.assertEqual("INVALID", result["status"])
        self.assertFalse(result["commit_request_approved"])
        self.assertIn("MALFORMED_COMMIT_REQUEST", result["issues"])

    def test_input_is_not_mutated(self) -> None:
        request = self._request()
        before = deepcopy(request)

        result = evaluate_execution_runtime_commit_request_validation_gate(request)
        result["issues"].append("MUTATED_RESULT_ONLY")

        self.assertEqual(before, request)

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
            result = evaluate_execution_runtime_commit_request_validation_gate(self._request())

        self.assertEqual(STATUS_APPROVED, result["status"])
        runtime_commit.assert_not_called()
        init_commit.assert_not_called()
        queue_commit.assert_not_called()
        send_order.assert_not_called()
        self.assertEqual(before_runtime, {str(path): _sha256(path) for path in runtime_paths})
        self.assertFalse((ROOT / "runtime" / "order_executions.json").exists())
        self.assertFalse((ROOT / "runtime" / "order_locks.json").exists())


if __name__ == "__main__":
    unittest.main()
