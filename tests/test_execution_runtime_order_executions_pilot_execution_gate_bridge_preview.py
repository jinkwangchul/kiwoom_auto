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
from execution_runtime_order_executions_pilot_execution_gate_bridge_preview import (
    BRIDGE_TYPE,
    STATUS_BLOCKED,
    STATUS_INVALID,
    STATUS_READY,
    build_order_executions_pilot_execution_gate_bridge_preview,
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


class OrderExecutionsPilotExecutionGateBridgePreviewTest(unittest.TestCase):
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

    def _route_preview(self, target: Path | None = None) -> dict:
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
        token = build_execution_runtime_order_executions_pilot_approval_token(approval)
        return validate_order_executions_pilot_token_route_preview(token)

    def test_ready_route_preview_builds_bridge_ready(self) -> None:
        result = build_order_executions_pilot_execution_gate_bridge_preview(self._route_preview())

        self.assertEqual(BRIDGE_TYPE, result["bridge_type"])
        self.assertEqual(STATUS_READY, result["status"])
        self.assertTrue(result["execution_gate_input_ready"])
        self.assertTrue(result["commit_id_preview"].startswith("order-executions-pilot-"))
        self.assertTrue(result["plan_hash_preview"])
        self.assertTrue(result["execution_token_preview"])
        self.assertEqual(result["source_approval_token"], result["execution_token_preview"]["token_id"])
        self.assertEqual("order_executions", result["logical_target"])
        self.assertEqual(EXECUTION_MODE_INIT, result["execution_mode"])
        self.assertFalse(result["actual_execution_allowed"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["execution_gate_called"])
        self.assertEqual([], result["issues"])

    def test_blocked_or_invalid_route_preview_is_fail_closed(self) -> None:
        blocked = self._route_preview()
        blocked["status"] = STATUS_BLOCKED
        blocked["token_valid"] = False
        invalid = self._route_preview()
        invalid["status"] = STATUS_INVALID
        invalid["execution_route_ready"] = False

        blocked_result = build_order_executions_pilot_execution_gate_bridge_preview(blocked)
        invalid_result = build_order_executions_pilot_execution_gate_bridge_preview(invalid)

        self.assertEqual(STATUS_BLOCKED, blocked_result["status"])
        self.assertFalse(blocked_result["execution_gate_input_ready"])
        self.assertIn("ROUTE_PREVIEW_NOT_READY", blocked_result["issues"])
        self.assertIn("ROUTE_TOKEN_NOT_VALID", blocked_result["issues"])
        self.assertEqual(STATUS_INVALID, invalid_result["status"])
        self.assertFalse(invalid_result["execution_gate_input_ready"])
        self.assertIn("ROUTE_EXECUTION_NOT_READY", invalid_result["issues"])
        self.assertIn("ROUTE_PREVIEW_INVALID", invalid_result["issues"])

    def test_token_fingerprint_target_mode_and_snapshot_tamper_are_blocked(self) -> None:
        cases = [
            ("approval_token", "tampered", "APPROVAL_TOKEN_MISMATCH"),
            ("approval_fingerprint", "tampered", "APPROVAL_FINGERPRINT_MISMATCH"),
            ("logical_target", "order_locks", "LOGICAL_TARGET_MUST_BE_ORDER_EXECUTIONS"),
            ("execution_mode", EXECUTION_MODE_APPEND, "EXECUTION_MODE_MISMATCH"),
            ("pilot_snapshot_fingerprint", "tampered", "PILOT_SNAPSHOT_FINGERPRINT_MISMATCH"),
        ]

        for field, value, expected_issue in cases:
            with self.subTest(field=field):
                route = self._route_preview()
                route[field] = value
                result = build_order_executions_pilot_execution_gate_bridge_preview(route)
                self.assertEqual(STATUS_INVALID, result["status"])
                self.assertFalse(result["execution_gate_input_ready"])
                self.assertIn(expected_issue, result["issues"])

    def test_preview_gate_inputs_are_deterministic_and_bound(self) -> None:
        first = build_order_executions_pilot_execution_gate_bridge_preview(self._route_preview())
        second = build_order_executions_pilot_execution_gate_bridge_preview(self._route_preview())
        changed_route = self._route_preview()
        changed_route["runtime_target"] = str((self.runtime_root / "changed_order_executions.json").resolve(strict=False))
        changed = build_order_executions_pilot_execution_gate_bridge_preview(changed_route)

        self.assertEqual(first["commit_id_preview"], second["commit_id_preview"])
        self.assertEqual(first["plan_hash_preview"], second["plan_hash_preview"])
        self.assertEqual(first["execution_token_preview"], second["execution_token_preview"])
        self.assertEqual(
            first["plan_hash_preview"],
            build_execution_plan_hash(first["execution_plan_preview"]),
        )
        self.assertNotEqual(first["plan_hash_preview"], changed["plan_hash_preview"])
        self.assertNotEqual(first["execution_token_preview"]["plan_hash"], changed["execution_token_preview"]["plan_hash"])

    def test_execution_gate_input_shape_matches_existing_contract_preview(self) -> None:
        result = build_order_executions_pilot_execution_gate_bridge_preview(self._route_preview())
        gate_input = result["execution_gate_input_preview"]

        self.assertEqual(result["commit_id_preview"], gate_input["commit_id"])
        self.assertEqual(result["plan_hash_preview"], gate_input["expected_plan_hash"])
        self.assertEqual(result["execution_plan_preview"], gate_input["execution_plan"])
        self.assertEqual(result["approval_context_preview"], gate_input["approval_context"])
        self.assertEqual(result["execution_token_preview"], gate_input["execution_token"])
        self.assertEqual("RUNTIME_COMMIT_EXECUTION", gate_input["execution_token"]["scope"])
        self.assertFalse(gate_input["execution_token"]["consumed"])

    def test_result_mutation_does_not_modify_input(self) -> None:
        route = self._route_preview()
        before = deepcopy(route)

        result = build_order_executions_pilot_execution_gate_bridge_preview(route)
        result["route_preview_snapshot"]["pilot_boundary_snapshot"]["backup_plan"]["backup_action"] = "changed"
        result["issues"].append("RESULT_ONLY")

        self.assertEqual(before, route)

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
            mock.patch("runtime_commit_execution_gate.evaluate_runtime_commit_execution_gate") as execution_gate,
            mock.patch("runtime_commit_execution_gate.evaluate_runtime_commit_execution_gate_preview") as execution_gate_preview,
            mock.patch("execution_runtime_commit_service.commit_execution_runtime_plan") as runtime_commit,
            mock.patch("execution_runtime_file_init_commit_service.commit_execution_runtime_file_init_plan") as init_commit,
            mock.patch("execution_runtime_commit_service._write_json_atomic") as runtime_writer,
            mock.patch("execution_runtime_commit_service._make_backup") as backup,
            mock.patch("execution_runtime_commit_service._restore_backups") as restore,
            mock.patch("execution_queue_commit_service.commit_execution_queue_manually") as queue_commit,
            mock.patch("send_order_entrypoint.execute_send_order") as send_order,
        ):
            result = build_order_executions_pilot_execution_gate_bridge_preview(self._route_preview())

        self.assertEqual(STATUS_READY, result["status"])
        issue_token.assert_not_called()
        read_token.assert_not_called()
        validate_token.assert_not_called()
        consume_token.assert_not_called()
        execution_gate.assert_not_called()
        execution_gate_preview.assert_not_called()
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
