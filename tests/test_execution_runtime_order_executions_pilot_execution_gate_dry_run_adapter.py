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
from execution_runtime_order_executions_pilot_boundary import build_order_executions_pilot_boundary
from execution_runtime_order_executions_pilot_execution_gate_bridge_preview import (
    build_order_executions_pilot_execution_gate_bridge_preview,
)
from execution_runtime_order_executions_pilot_execution_gate_dry_run_adapter import (
    ADAPTER_TYPE,
    STATUS_BLOCKED,
    STATUS_INVALID,
    STATUS_READY,
    evaluate_order_executions_pilot_execution_gate_dry_run,
)
from execution_runtime_order_executions_pilot_token_route_preview import (
    validate_order_executions_pilot_token_route_preview,
)
from execution_runtime_real_commit_readiness_policy import evaluate_execution_runtime_real_commit_readiness
from runtime_commit_execution_gate import build_execution_plan_hash


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


class OrderExecutionsPilotExecutionGateDryRunAdapterTest(unittest.TestCase):
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

    def _bridge_preview(self) -> dict:
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
        resolved = str(self.target.resolve(strict=False))
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
        token = build_execution_runtime_order_executions_pilot_approval_token(approval)
        route = validate_order_executions_pilot_token_route_preview(token)
        return build_order_executions_pilot_execution_gate_bridge_preview(route)

    def test_ready_bridge_preview_builds_ready_dry_run(self) -> None:
        result = evaluate_order_executions_pilot_execution_gate_dry_run(self._bridge_preview())

        self.assertEqual(ADAPTER_TYPE, result["adapter_type"])
        self.assertEqual(STATUS_READY, result["status"])
        self.assertTrue(result["execution_gate_dry_run_ready"])
        self.assertTrue(result["commit_id"])
        self.assertTrue(result["plan_hash"])
        self.assertEqual(result["plan_hash"], build_execution_plan_hash(result["execution_plan"]))
        self.assertEqual(result["commit_id"], result["execution_token"]["commit_id"])
        self.assertEqual(result["plan_hash"], result["execution_token"]["plan_hash"])
        self.assertEqual(result["commit_id"], result["approval_context"]["approved_commit_id"])
        self.assertEqual(result["plan_hash"], result["approval_context"]["approved_plan_hash"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["actual_execution_allowed"])
        self.assertFalse(result["execution_gate_called"])
        self.assertEqual([], result["issues"])

    def test_blocked_or_invalid_bridge_preview_is_fail_closed(self) -> None:
        blocked = self._bridge_preview()
        blocked["status"] = STATUS_BLOCKED
        blocked["execution_gate_input_ready"] = False
        invalid = self._bridge_preview()
        invalid["status"] = STATUS_INVALID

        blocked_result = evaluate_order_executions_pilot_execution_gate_dry_run(blocked)
        invalid_result = evaluate_order_executions_pilot_execution_gate_dry_run(invalid)

        self.assertEqual(STATUS_BLOCKED, blocked_result["status"])
        self.assertFalse(blocked_result["execution_gate_dry_run_ready"])
        self.assertIn("BRIDGE_PREVIEW_NOT_READY", blocked_result["issues"])
        self.assertIn("BRIDGE_EXECUTION_GATE_INPUT_NOT_READY", blocked_result["issues"])
        self.assertEqual(STATUS_INVALID, invalid_result["status"])
        self.assertFalse(invalid_result["execution_gate_dry_run_ready"])

    def test_commit_id_plan_hash_and_execution_token_tamper_are_blocked(self) -> None:
        cases = [
            ("commit_id", "tampered", "execution_plan commit_id mismatch"),
            ("expected_plan_hash", "tampered", "expected_plan_hash mismatch"),
            ("token_commit_id", "tampered", "execution_token commit_id mismatch"),
            ("token_plan_hash", "tampered", "execution_token plan_hash mismatch"),
        ]

        for field, value, issue in cases:
            with self.subTest(field=field):
                bridge = self._bridge_preview()
                gate_input = bridge["execution_gate_input_preview"]
                if field == "commit_id":
                    gate_input["commit_id"] = value
                elif field == "expected_plan_hash":
                    gate_input["expected_plan_hash"] = value
                elif field == "token_commit_id":
                    gate_input["execution_token"]["commit_id"] = value
                elif field == "token_plan_hash":
                    gate_input["execution_token"]["plan_hash"] = value
                result = evaluate_order_executions_pilot_execution_gate_dry_run(bridge)
                self.assertEqual(STATUS_INVALID, result["status"])
                self.assertFalse(result["execution_gate_dry_run_ready"])
                self.assertIn(issue, result["issues"])

    def test_approval_context_and_execution_plan_mismatch_are_blocked(self) -> None:
        cases = [
            ("approved", False, "approval_context approved must be true"),
            ("approved_commit_id", "tampered", "approval_context approved_commit_id mismatch"),
            ("approved_plan_hash", "tampered", "approval_context approved_plan_hash mismatch"),
            ("approval_scope", "OTHER", "approval_context approval_scope is invalid"),
            ("final_state", "BLOCKED", "state_machine final state is not READY_TO_EXECUTE"),
            ("rollback_required", True, "execution_plan rollback_required is true"),
        ]

        for field, value, issue in cases:
            with self.subTest(field=field):
                bridge = self._bridge_preview()
                gate_input = bridge["execution_gate_input_preview"]
                if field in {"approved", "approved_commit_id", "approved_plan_hash", "approval_scope"}:
                    gate_input["approval_context"][field] = value
                elif field == "final_state":
                    gate_input["execution_plan"]["state_machine"]["terminal_state"] = value
                elif field == "rollback_required":
                    gate_input["execution_plan"]["execution_plan"]["rollback_required"] = value
                result = evaluate_order_executions_pilot_execution_gate_dry_run(bridge)
                self.assertFalse(result["execution_gate_dry_run_ready"])
                self.assertIn(issue, result["issues"])

    def test_safety_flag_violation_is_invalid(self) -> None:
        bridge = self._bridge_preview()
        bridge["execution_gate_input_preview"]["execution_plan"]["safety_flags"] = {"runtime_write": True}

        result = evaluate_order_executions_pilot_execution_gate_dry_run(bridge)

        self.assertEqual(STATUS_INVALID, result["status"])
        self.assertFalse(result["execution_gate_dry_run_ready"])
        self.assertIn("execution_plan: safety flag runtime_write must be false", result["issues"])

    def test_result_mutation_does_not_modify_input(self) -> None:
        bridge = self._bridge_preview()
        before = deepcopy(bridge)

        result = evaluate_order_executions_pilot_execution_gate_dry_run(bridge)
        result["execution_plan"]["source_statuses"]["logical_target"] = "changed"
        result["issues"].append("RESULT_ONLY")

        self.assertEqual(before, bridge)

    def test_execution_gate_token_store_commit_service_and_writes_are_not_called(self) -> None:
        runtime_paths = [
            ROOT / "runtime" / "order_queue.json",
            ROOT / "runtime" / "order_executions.json",
            ROOT / "runtime" / "order_locks.json",
        ]
        before_runtime = {str(path): _sha256(path) for path in runtime_paths}

        with (
            mock.patch("runtime_commit_execution_gate.evaluate_runtime_commit_execution_gate") as execution_gate,
            mock.patch("runtime_commit_execution_gate.evaluate_runtime_commit_execution_gate_preview") as execution_gate_preview,
            mock.patch("runtime_commit_approval_token_store.issue_runtime_commit_approval_token") as issue_token,
            mock.patch("runtime_commit_approval_token_store.read_runtime_commit_approval_token") as read_token,
            mock.patch("runtime_commit_approval_token_store.validate_runtime_commit_approval_token") as validate_token,
            mock.patch("runtime_commit_approval_token_store.consume_runtime_commit_approval_token") as consume_token,
            mock.patch("execution_runtime_commit_service.commit_execution_runtime_plan") as runtime_commit,
            mock.patch("execution_runtime_file_init_commit_service.commit_execution_runtime_file_init_plan") as init_commit,
            mock.patch("execution_runtime_commit_service._write_json_atomic") as runtime_writer,
            mock.patch("execution_runtime_commit_service._make_backup") as backup,
            mock.patch("execution_runtime_commit_service._restore_backups") as restore,
            mock.patch("execution_queue_commit_service.commit_execution_queue_manually") as queue_commit,
            mock.patch("send_order_entrypoint.execute_send_order") as send_order,
        ):
            result = evaluate_order_executions_pilot_execution_gate_dry_run(self._bridge_preview())

        self.assertEqual(STATUS_READY, result["status"])
        execution_gate.assert_not_called()
        execution_gate_preview.assert_not_called()
        issue_token.assert_not_called()
        read_token.assert_not_called()
        validate_token.assert_not_called()
        consume_token.assert_not_called()
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
