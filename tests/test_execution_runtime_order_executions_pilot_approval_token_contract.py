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
from execution_runtime_file_schema import default_order_executions_data
from execution_runtime_order_executions_pilot_approval_gate import evaluate_order_executions_pilot_approval
from execution_runtime_order_executions_pilot_approval_token_contract import (
    APPROVAL_TYPE,
    STATUS_APPROVED,
    STATUS_BLOCKED,
    STATUS_INVALID,
    TOKEN_TYPE,
    ExecutionRuntimeOrderExecutionsPilotApprovalToken,
    build_execution_runtime_order_executions_pilot_approval_token,
)
from execution_runtime_order_executions_pilot_boundary import (
    EXECUTION_MODE_APPEND,
    EXECUTION_MODE_INIT,
    build_order_executions_pilot_boundary,
)
from execution_runtime_real_commit_readiness_policy import evaluate_execution_runtime_real_commit_readiness


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, data: dict) -> None:
    import json

    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


class OrderExecutionsPilotApprovalTokenContractTest(unittest.TestCase):
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

    def _approval_gate(self, target: Path | None = None) -> dict:
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
        service_handoff["handoff_preview"]["logical_target"] = "order_executions"
        service_handoff["handoff_preview"]["runtime_target"] = str(target.resolve(strict=False))
        service_handoff["handoff_preview"]["relative_path"] = "order_executions.json"
        service_handoff["handoff_preview"]["allowlist_decision"]["resolved_path"] = str(target.resolve(strict=False))
        service_handoff["handoff_preview"]["allowlist_decision"]["normalized_path"] = str(target.resolve(strict=False))
        service_handoff["handoff_preview"]["allowlist_decision"]["logical_target"] = "order_executions"
        service_handoff["handoff_preview"]["allowlist_decision"]["relative_path"] = "order_executions.json"
        service_handoff["handoff_preview"]["commit_service_input_preview"]["logical_target"] = "order_executions"
        service_handoff["handoff_preview"]["commit_service_input_preview"]["runtime_target"] = str(target.resolve(strict=False))
        service_handoff["handoff_preview"]["commit_service_input_preview"]["relative_path"] = "order_executions.json"
        boundary = build_order_executions_pilot_boundary(
            service_handoff["handoff_preview"],
            service_handoff["commit_service_route_preview"],
            self.runtime_root,
        )
        return evaluate_order_executions_pilot_approval(boundary)

    def test_normal_token_is_generated_from_approved_gate(self) -> None:
        result = build_execution_runtime_order_executions_pilot_approval_token(self._approval_gate())

        self.assertEqual(TOKEN_TYPE, result["token_type"])
        self.assertEqual("EXECUTION_RUNTIME_ORDER_EXECUTIONS_PILOT_APPROVAL_TOKEN_V1", result["token_version"])
        self.assertEqual(STATUS_APPROVED, result["status"])
        self.assertTrue(result["preview_only"])
        self.assertTrue(result["dry_run_only"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["actual_execution_allowed"])
        self.assertFalse(result["commit_service_called"])
        self.assertEqual("order_executions", result["logical_target"])
        self.assertEqual(EXECUTION_MODE_INIT, result["execution_mode"])
        self.assertTrue(result["approval_token"].startswith("EXECUTION_RUNTIME_ORDER_EXECUTIONS_PILOT_APPROVAL_TOKEN_"))
        self.assertTrue(result["approval_fingerprint"])
        self.assertTrue(result["pilot_snapshot_fingerprint"])
        self.assertEqual([], result["issues"])

    def test_append_mode_is_reflected_in_token(self) -> None:
        _write_json(self.target, default_order_executions_data())

        result = build_execution_runtime_order_executions_pilot_approval_token(self._approval_gate())

        self.assertEqual(STATUS_APPROVED, result["status"])
        self.assertEqual(EXECUTION_MODE_APPEND, result["execution_mode"])
        self.assertTrue(result["file_exists"])
        self.assertTrue(result["backup_required"])

    def test_blocked_or_invalid_gate_is_fail_closed(self) -> None:
        blocked = self._approval_gate()
        blocked["status"] = STATUS_BLOCKED
        blocked["production_pilot_approved"] = False
        invalid = self._approval_gate()
        invalid["status"] = STATUS_INVALID
        invalid["production_pilot_approved"] = False

        blocked_result = build_execution_runtime_order_executions_pilot_approval_token(blocked)
        invalid_result = build_execution_runtime_order_executions_pilot_approval_token(invalid)

        self.assertEqual(STATUS_BLOCKED, blocked_result["status"])
        self.assertFalse(blocked_result["approval_token"])
        self.assertIn("PILOT_APPROVAL_NOT_APPROVED", blocked_result["issues"])
        self.assertEqual(STATUS_INVALID, invalid_result["status"])
        self.assertFalse(invalid_result["approval_token"])
        self.assertIn("PILOT_APPROVAL_NOT_APPROVED", invalid_result["issues"])

    def test_fingerprint_tampering_is_blocked(self) -> None:
        gate = self._approval_gate()
        gate["approval_fingerprint"] = "tampered"

        result = build_execution_runtime_order_executions_pilot_approval_token(gate)

        self.assertEqual("INVALID", result["status"])
        self.assertIn("APPROVAL_FINGERPRINT_MISMATCH", result["issues"])

    def test_snapshot_target_and_mode_mismatch_are_blocked(self) -> None:
        gate = self._approval_gate()
        gate["pilot_boundary_snapshot"]["logical_target"] = "order_locks"
        gate["pilot_boundary_snapshot"]["execution_mode"] = EXECUTION_MODE_APPEND

        result = build_execution_runtime_order_executions_pilot_approval_token(gate)

        self.assertEqual("INVALID", result["status"])
        self.assertIn("PILOT_BOUNDARY_LOGICAL_TARGET_MISMATCH", result["issues"])
        self.assertIn("PILOT_BOUNDARY_EXECUTION_MODE_MISMATCH", result["issues"])

    def test_token_is_deterministic_and_changes_on_core_input_change(self) -> None:
        first = build_execution_runtime_order_executions_pilot_approval_token(self._approval_gate())
        second = build_execution_runtime_order_executions_pilot_approval_token(self._approval_gate())
        changed = self._approval_gate()
        changed["pilot_boundary_snapshot"]["backup_plan"]["backup_action"] = "changed"
        changed_result = build_execution_runtime_order_executions_pilot_approval_token(changed)

        self.assertEqual(first["approval_token"], second["approval_token"])
        self.assertEqual(first["approval_fingerprint"], second["approval_fingerprint"])
        self.assertNotEqual(first["approval_token"], changed_result["approval_token"])
        self.assertNotEqual(first["pilot_snapshot_fingerprint"], changed_result["pilot_snapshot_fingerprint"])

    def test_result_mutation_does_not_modify_input(self) -> None:
        gate = self._approval_gate()
        before = deepcopy(gate)

        result = build_execution_runtime_order_executions_pilot_approval_token(gate)
        result["pilot_boundary_snapshot"]["backup_plan"]["backup_action"] = "changed"
        result["issues"].append("RESULT_ONLY")

        self.assertEqual(before, gate)

    def test_dataclass_is_frozen(self) -> None:
        token = ExecutionRuntimeOrderExecutionsPilotApprovalToken(
            token_type=TOKEN_TYPE,
            token_version="v1",
            status=STATUS_APPROVED,
            approval_token="token",
            approval_fingerprint="fingerprint",
            logical_target="order_executions",
            runtime_target=str(self.target),
            execution_mode=EXECUTION_MODE_INIT,
            file_exists=False,
            backup_required=False,
            pilot_snapshot_fingerprint="snapshot",
            preview_only=True,
            dry_run_only=True,
            runtime_write=False,
            actual_execution_allowed=False,
            commit_service_called=False,
            approval_gate_snapshot={},
            pilot_boundary_snapshot={},
            issues=(),
            warnings=(),
        )

        with self.assertRaises(Exception):
            token.status = STATUS_BLOCKED  # type: ignore[misc]

    def test_no_write_backup_rollback_commit_service_order_queue_or_send_order(self) -> None:
        runtime_paths = [
            ROOT / "runtime" / "order_queue.json",
            ROOT / "runtime" / "order_executions.json",
            ROOT / "runtime" / "order_locks.json",
        ]
        before_runtime = {str(path): _sha256(path) for path in runtime_paths}

        with (
            mock.patch("execution_runtime_commit_service.commit_execution_runtime_plan") as runtime_commit,
            mock.patch("execution_runtime_file_init_commit_service.commit_execution_runtime_file_init_plan") as init_commit,
            mock.patch("execution_runtime_commit_service._write_json_atomic") as runtime_writer,
            mock.patch("execution_runtime_commit_service._make_backup") as backup,
            mock.patch("execution_runtime_commit_service._restore_backups") as restore,
            mock.patch("execution_queue_commit_service.commit_execution_queue_manually") as queue_commit,
            mock.patch("send_order_entrypoint.execute_send_order") as send_order,
        ):
            result = build_execution_runtime_order_executions_pilot_approval_token(self._approval_gate())

        self.assertEqual(STATUS_APPROVED, result["status"])
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
