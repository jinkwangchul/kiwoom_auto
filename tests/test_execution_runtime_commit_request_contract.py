from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from execution_runtime_allowlist import OPERATION_WRITE, validate_runtime_target
from execution_runtime_commit_request_contract import (
    CONTRACT_TYPE,
    STATUS_READY,
    build_execution_runtime_commit_request,
    validate_execution_runtime_commit_request,
)
from execution_runtime_real_commit_readiness_policy import evaluate_execution_runtime_real_commit_readiness


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ExecutionRuntimeCommitRequestContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.tmp.cleanup()

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

    def test_commit_request_created_from_ready_readiness_policy(self) -> None:
        readiness = self._readiness()
        request = build_execution_runtime_commit_request(readiness)

        self.assertEqual(CONTRACT_TYPE, request["contract_type"])
        self.assertEqual(STATUS_READY, request["status"])
        self.assertEqual("EXECUTION_RUNTIME_REAL_COMMIT_READINESS_POLICY", request["source_policy_type"])
        self.assertEqual("order_executions", request["logical_target"])
        self.assertEqual("preview", request["operation"])
        self.assertEqual("order_executions.json", request["relative_path"])
        self.assertTrue(request["preview_only"])
        self.assertTrue(request["dry_run"])
        self.assertFalse(request["runtime_write"])
        self.assertTrue(request["validation_summary"]["policy_allowed"])
        self.assertTrue(request["validation_summary"]["allowlist_allowed"])
        self.assertEqual([], request["issues"])

        validation = validate_execution_runtime_commit_request(request)
        self.assertTrue(validation["valid"])

    def test_allowlist_decision_is_carried_into_request(self) -> None:
        readiness = self._readiness()
        request = build_execution_runtime_commit_request(readiness)

        self.assertEqual(readiness["allowlist_decisions"]["runtime_target"], request["allowlist_decision"])
        self.assertEqual(
            readiness["allowlist_decisions"]["runtime_target"]["resolved_path"],
            request["runtime_target"],
        )

    def test_dry_run_argument_is_preserved(self) -> None:
        readiness = self._readiness()
        request = build_execution_runtime_commit_request(readiness, dry_run=False)

        self.assertFalse(request["dry_run"])
        self.assertTrue(validate_execution_runtime_commit_request(request)["valid"])

    def test_request_fingerprint_is_stable_and_changes_with_payload(self) -> None:
        readiness = self._readiness()
        first = build_execution_runtime_commit_request(readiness)
        second = build_execution_runtime_commit_request(deepcopy(readiness))
        changed = deepcopy(readiness)
        changed["allowlist_decisions"]["runtime_target"]["operation"] = "read"

        self.assertEqual(first["request_fingerprint"], second["request_fingerprint"])
        self.assertNotEqual(
            first["request_fingerprint"],
            build_execution_runtime_commit_request(changed)["request_fingerprint"],
        )

    def test_blocked_allowlist_result_stays_fail_closed(self) -> None:
        readiness = self._readiness(logical_target="order_queue")
        request = build_execution_runtime_commit_request(readiness)

        self.assertEqual("BLOCKED", request["status"])
        self.assertFalse(request["validation_summary"]["policy_allowed"])
        self.assertFalse(request["validation_summary"]["allowlist_allowed"])
        self.assertIn("READINESS_POLICY_NOT_ALLOWED", request["issues"])
        self.assertTrue(any(issue.startswith("ALLOWLIST_DECISION_NOT_ALLOWED") for issue in request["issues"]))

    def test_write_operation_stays_fail_closed(self) -> None:
        readiness = self._readiness(allowlist_operation=OPERATION_WRITE)
        request = build_execution_runtime_commit_request(readiness)

        self.assertEqual("BLOCKED", request["status"])
        self.assertIn("RUNTIME_WRITE_DISABLED", request["allowlist_decision"]["blocked_reason"])
        self.assertFalse(request["runtime_write"])

    def test_file_init_policy_shape_is_supported(self) -> None:
        decision = validate_runtime_target("order_executions", runtime_root=ROOT / "runtime").to_dict()
        policy_result = {
            "policy_type": "EXECUTION_RUNTIME_FILE_INIT_OPEN_POLICY",
            "status": "READY_TO_OPEN_FILE_INIT",
            "file_init_allowed": True,
            "preview_only": True,
            "runtime_write": False,
            "allowlist_decisions": {"runtime_target": decision},
            "issues": [],
            "warnings": [],
        }

        request = build_execution_runtime_commit_request(policy_result)

        self.assertEqual(STATUS_READY, request["status"])
        self.assertEqual("EXECUTION_RUNTIME_FILE_INIT_OPEN_POLICY", request["source_policy_type"])
        self.assertTrue(validate_execution_runtime_commit_request(request)["valid"])

    def test_builder_and_validator_do_not_mutate_inputs(self) -> None:
        readiness = self._readiness()
        original = deepcopy(readiness)

        request = build_execution_runtime_commit_request(readiness)
        request_before_validation = deepcopy(request)
        validate_execution_runtime_commit_request(request)

        self.assertEqual(original, readiness)
        self.assertEqual(request_before_validation, request)

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
            request = build_execution_runtime_commit_request(self._readiness())

        self.assertEqual(STATUS_READY, request["status"])
        runtime_commit.assert_not_called()
        init_commit.assert_not_called()
        queue_commit.assert_not_called()
        send_order.assert_not_called()
        self.assertEqual(before_runtime, {str(path): _sha256(path) for path in runtime_paths})
        self.assertFalse((ROOT / "runtime" / "order_executions.json").exists())
        self.assertFalse((ROOT / "runtime" / "order_locks.json").exists())


if __name__ == "__main__":
    unittest.main()
