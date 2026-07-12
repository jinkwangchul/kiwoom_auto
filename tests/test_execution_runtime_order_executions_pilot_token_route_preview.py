from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from execution_runtime_commit_handoff_preview import (
    build_execution_runtime_commit_handoff_preview,
    build_execution_runtime_commit_service_handoff_preview,
)
from execution_runtime_commit_request_contract import build_execution_runtime_commit_request
from execution_runtime_commit_request_validation_gate import evaluate_execution_runtime_commit_request_validation_gate
from execution_runtime_order_executions_pilot_approval_gate import evaluate_order_executions_pilot_approval
from execution_runtime_order_executions_pilot_approval_token_contract import (
    build_execution_runtime_order_executions_pilot_approval_token,
)
from execution_runtime_order_executions_pilot_boundary import (
    EXECUTION_MODE_APPEND,
    EXECUTION_MODE_INIT,
    build_order_executions_pilot_boundary,
)
from execution_runtime_order_executions_pilot_token_route_preview import (
    ROUTE_PREVIEW_TYPE,
    STATUS_BLOCKED,
    STATUS_INVALID,
    STATUS_READY,
    validate_order_executions_pilot_token_route_preview,
)
from execution_runtime_real_commit_readiness_policy import evaluate_execution_runtime_real_commit_readiness


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


class OrderExecutionsPilotTokenRoutePreviewTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.runtime_root = Path(self.tmp.name)
        self.target = self.runtime_root / "order_executions.json"

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

    def _approval_token(self, target: Path | None = None) -> dict:
        target = self.target if target is None else target
        readiness = evaluate_execution_runtime_real_commit_readiness(
            runtime_api_result=self._runtime_api_result(),
            commit_plan_orchestrator_result=self._commit_plan(),
            order_executions_path=ROOT / "runtime" / "order_executions.json",
            order_locks_path=ROOT / "runtime" / "order_locks.json",
            confirmations={
                "manual_execution_runtime_commit_confirmed": True,
                "manual_runtime_file_write_confirmed": True,
            },
            environment_flags={
                "real_runtime_commit_enabled": True,
                "allow_project_runtime_commit": True,
            },
        )
        request = build_execution_runtime_commit_request(readiness)
        gate = evaluate_execution_runtime_commit_request_validation_gate(request)
        handoff = build_execution_runtime_commit_handoff_preview(gate)
        service_handoff = build_execution_runtime_commit_service_handoff_preview(handoff)
        resolved = str(target.resolve(strict=False))
        service_handoff["handoff_preview"]["logical_target"] = "order_executions"
        service_handoff["handoff_preview"]["runtime_target"] = resolved
        service_handoff["handoff_preview"]["relative_path"] = "order_executions.json"
        service_handoff["handoff_preview"]["allowlist_decision"]["resolved_path"] = resolved
        service_handoff["handoff_preview"]["allowlist_decision"]["normalized_path"] = resolved
        service_handoff["handoff_preview"]["allowlist_decision"]["logical_target"] = "order_executions"
        service_handoff["handoff_preview"]["allowlist_decision"]["relative_path"] = "order_executions.json"
        service_handoff["handoff_preview"]["commit_service_input_preview"]["logical_target"] = "order_executions"
        service_handoff["handoff_preview"]["commit_service_input_preview"]["runtime_target"] = resolved
        service_handoff["handoff_preview"]["commit_service_input_preview"]["relative_path"] = "order_executions.json"
        boundary = build_order_executions_pilot_boundary(
            service_handoff["handoff_preview"],
            service_handoff["commit_service_route_preview"],
            self.runtime_root,
        )
        approval = evaluate_order_executions_pilot_approval(boundary)
        return build_execution_runtime_order_executions_pilot_approval_token(approval)

    def test_valid_token_contract_is_route_ready(self) -> None:
        result = validate_order_executions_pilot_token_route_preview(self._approval_token())

        self.assertEqual(ROUTE_PREVIEW_TYPE, result["route_preview_type"])
        self.assertEqual(STATUS_READY, result["status"])
        self.assertTrue(result["token_valid"])
        self.assertTrue(result["execution_route_ready"])
        self.assertEqual("order_executions", result["logical_target"])
        self.assertEqual(EXECUTION_MODE_INIT, result["execution_mode"])
        self.assertTrue(result["approval_token"])
        self.assertTrue(result["approval_fingerprint"])
        self.assertTrue(result["pilot_snapshot_fingerprint"])
        self.assertFalse(result["actual_execution_allowed"])
        self.assertFalse(result["runtime_write"])
        self.assertEqual([], result["issues"])

    def test_tampered_token_and_fingerprint_are_blocked(self) -> None:
        token = self._approval_token()
        tampered_token = deepcopy(token)
        tampered_token["approval_token"] = "tampered"
        tampered_fingerprint = deepcopy(token)
        tampered_fingerprint["approval_fingerprint"] = "tampered"

        token_result = validate_order_executions_pilot_token_route_preview(tampered_token)
        fingerprint_result = validate_order_executions_pilot_token_route_preview(tampered_fingerprint)

        self.assertEqual(STATUS_INVALID, token_result["status"])
        self.assertIn("APPROVAL_TOKEN_MISMATCH", token_result["issues"])
        self.assertEqual(STATUS_INVALID, fingerprint_result["status"])
        self.assertIn("APPROVAL_FINGERPRINT_MISMATCH", fingerprint_result["issues"])

    def test_target_mode_and_snapshot_mismatch_are_blocked(self) -> None:
        token = self._approval_token()
        target_mismatch = deepcopy(token)
        target_mismatch["logical_target"] = "order_locks"
        mode_mismatch = deepcopy(token)
        mode_mismatch["execution_mode"] = EXECUTION_MODE_APPEND
        snapshot_mismatch = deepcopy(token)
        snapshot_mismatch["pilot_snapshot_fingerprint"] = "tampered"

        target_result = validate_order_executions_pilot_token_route_preview(target_mismatch)
        mode_result = validate_order_executions_pilot_token_route_preview(mode_mismatch)
        snapshot_result = validate_order_executions_pilot_token_route_preview(snapshot_mismatch)

        self.assertEqual(STATUS_INVALID, target_result["status"])
        self.assertIn("LOGICAL_TARGET_MUST_BE_ORDER_EXECUTIONS", target_result["issues"])
        self.assertIn("LOGICAL_TARGET_MISMATCH", target_result["issues"])
        self.assertEqual(STATUS_INVALID, mode_result["status"])
        self.assertIn("EXECUTION_MODE_MISMATCH", mode_result["issues"])
        self.assertEqual(STATUS_INVALID, snapshot_result["status"])
        self.assertIn("PILOT_SNAPSHOT_FINGERPRINT_MISMATCH", snapshot_result["issues"])

    def test_safety_flag_violations_are_fail_closed(self) -> None:
        cases = [
            ("preview_only", False, "PREVIEW_ONLY_REQUIRED"),
            ("dry_run_only", False, "DRY_RUN_ONLY_REQUIRED"),
            ("runtime_write", True, "RUNTIME_WRITE_MUST_BE_FALSE"),
            ("actual_execution_allowed", True, "ACTUAL_EXECUTION_ALLOWED_MUST_BE_FALSE"),
            ("commit_service_called", True, "COMMIT_SERVICE_ALREADY_CALLED"),
        ]

        for field, value, issue in cases:
            with self.subTest(field=field):
                token = self._approval_token()
                token[field] = value
                result = validate_order_executions_pilot_token_route_preview(token)
                self.assertEqual(STATUS_INVALID, result["status"])
                self.assertFalse(result["token_valid"])
                self.assertFalse(result["execution_route_ready"])
                self.assertIn(issue, result["issues"])

    def test_blocked_contract_is_fail_closed(self) -> None:
        token = self._approval_token()
        token["status"] = "BLOCKED"
        token["approval_token"] = ""

        result = validate_order_executions_pilot_token_route_preview(token)

        self.assertEqual(STATUS_INVALID, result["status"])
        self.assertFalse(result["token_valid"])
        self.assertFalse(result["execution_route_ready"])
        self.assertIn("TOKEN_CONTRACT_NOT_APPROVED", result["issues"])
        self.assertIn("MISSING_APPROVAL_TOKEN", result["issues"])

    def test_result_mutation_does_not_modify_input(self) -> None:
        token = self._approval_token()
        before = deepcopy(token)

        result = validate_order_executions_pilot_token_route_preview(token)
        result["pilot_boundary_snapshot"]["backup_plan"]["backup_action"] = "changed"
        result["issues"].append("RESULT_ONLY")

        self.assertEqual(before, token)

    def test_token_store_execution_gate_commit_service_and_writes_are_not_called(self) -> None:
        runtime_paths = [
            ROOT / "runtime" / "order_queue.json",
            ROOT / "runtime" / "order_executions.json",
            ROOT / "runtime" / "order_locks.json",
        ]
        before_runtime = {str(path): _sha256(path) for path in runtime_paths}

        with (
            mock.patch("runtime_commit_approval_token_store.issue_runtime_commit_approval_token") as issue_token,
            mock.patch("runtime_commit_approval_token_store.read_runtime_commit_approval_token") as read_token,
            mock.patch("runtime_commit_approval_token_store.validate_runtime_commit_approval_token") as validate_token,
            mock.patch("runtime_commit_approval_token_store.consume_runtime_commit_approval_token") as consume_token,
            mock.patch("runtime_commit_execution_gate.evaluate_runtime_commit_execution_gate_preview") as execution_gate,
            mock.patch("execution_runtime_commit_service.commit_execution_runtime_plan") as runtime_commit,
            mock.patch("execution_runtime_file_init_commit_service.commit_execution_runtime_file_init_plan") as init_commit,
            mock.patch("execution_runtime_commit_service._write_json_atomic") as runtime_writer,
            mock.patch("execution_runtime_commit_service._make_backup") as backup,
            mock.patch("execution_runtime_commit_service._restore_backups") as restore,
            mock.patch("execution_queue_commit_service.commit_execution_queue_manually") as queue_commit,
            mock.patch("send_order_entrypoint.execute_send_order") as send_order,
        ):
            result = validate_order_executions_pilot_token_route_preview(self._approval_token())

        self.assertEqual(STATUS_READY, result["status"])
        issue_token.assert_not_called()
        read_token.assert_not_called()
        validate_token.assert_not_called()
        consume_token.assert_not_called()
        execution_gate.assert_not_called()
        runtime_commit.assert_not_called()
        init_commit.assert_not_called()
        runtime_writer.assert_not_called()
        backup.assert_not_called()
        restore.assert_not_called()
        queue_commit.assert_not_called()
        send_order.assert_not_called()
        self.assertEqual(before_runtime, {str(path): _sha256(path) for path in runtime_paths})
        self.assertFalse((ROOT / "runtime" / "order_executions.json").exists())
        self.assertFalse((ROOT / "runtime" / "order_locks.json").exists())


if __name__ == "__main__":
    unittest.main()
