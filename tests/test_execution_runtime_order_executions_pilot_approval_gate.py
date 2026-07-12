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
from execution_runtime_order_executions_pilot_approval_gate import (
    APPROVAL_TYPE,
    STATUS_APPROVED,
    evaluate_order_executions_pilot_approval,
)
from execution_runtime_order_executions_pilot_boundary import (
    EXECUTION_MODE_APPEND,
    EXECUTION_MODE_INIT,
    STATUS_BLOCKED,
    STATUS_INVALID,
    STATUS_READY,
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


class OrderExecutionsPilotApprovalGateTest(unittest.TestCase):
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

    def _pilot_boundary(self, target: Path | None = None) -> dict:
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
        return build_order_executions_pilot_boundary(
            service_handoff["handoff_preview"],
            service_handoff["commit_service_route_preview"],
            self.runtime_root,
        )

    def test_approved_for_init_boundary(self) -> None:
        result = evaluate_order_executions_pilot_approval(self._pilot_boundary())

        self.assertEqual(APPROVAL_TYPE, result["approval_type"])
        self.assertEqual(STATUS_APPROVED, result["status"])
        self.assertTrue(result["production_pilot_approved"])
        self.assertEqual("order_executions", result["logical_target"])
        self.assertEqual(EXECUTION_MODE_INIT, result["execution_mode"])
        self.assertFalse(result["actual_execution_allowed"])
        self.assertFalse(result["commit_service_called"])
        self.assertEqual([], result["issues"])

    def test_approved_for_append_boundary(self) -> None:
        _write_json(self.target, default_order_executions_data())
        result = evaluate_order_executions_pilot_approval(self._pilot_boundary())

        self.assertEqual(STATUS_APPROVED, result["status"])
        self.assertTrue(result["production_pilot_approved"])
        self.assertEqual(EXECUTION_MODE_APPEND, result["execution_mode"])
        self.assertTrue(result["file_exists"])
        self.assertTrue(result["backup_required"])

    def test_blocked_or_invalid_boundary_is_fail_closed(self) -> None:
        blocked = self._pilot_boundary()
        blocked["status"] = STATUS_BLOCKED
        blocked["pilot_ready"] = False
        invalid = self._pilot_boundary()
        invalid["status"] = STATUS_INVALID
        invalid["pilot_ready"] = False

        blocked_result = evaluate_order_executions_pilot_approval(blocked)
        invalid_result = evaluate_order_executions_pilot_approval(invalid)

        self.assertEqual(STATUS_BLOCKED, blocked_result["status"])
        self.assertFalse(blocked_result["production_pilot_approved"])
        self.assertIn("PILOT_BOUNDARY_NOT_READY", blocked_result["issues"])
        self.assertEqual(STATUS_INVALID, invalid_result["status"])
        self.assertFalse(invalid_result["production_pilot_approved"])
        self.assertIn("PILOT_BOUNDARY_NOT_READY", invalid_result["issues"])

    def test_plan_tampering_is_blocked(self) -> None:
        result = self._pilot_boundary()
        result["atomic_write_plan"]["runtime_write"] = True

        approved = evaluate_order_executions_pilot_approval(result)

        self.assertEqual(STATUS_INVALID, approved["status"])
        self.assertFalse(approved["production_pilot_approved"])
        self.assertIn("ATOMIC_WRITE_RUNTIME_WRITE_MUST_BE_FALSE", approved["issues"])

    def test_missing_plan_is_blocked(self) -> None:
        result = self._pilot_boundary()
        result.pop("rollback_plan")

        approved = evaluate_order_executions_pilot_approval(result)

        self.assertEqual(STATUS_INVALID, approved["status"])
        self.assertFalse(approved["production_pilot_approved"])
        self.assertIn("ROLLBACK_PLAN_MISSING", approved["issues"])

    def test_fingerprint_is_deterministic_and_changes_on_plan_change(self) -> None:
        first = evaluate_order_executions_pilot_approval(self._pilot_boundary())
        second = evaluate_order_executions_pilot_approval(self._pilot_boundary())
        changed = self._pilot_boundary()
        changed["atomic_write_plan"]["temp_path_preview"] = "changed"
        changed_result = evaluate_order_executions_pilot_approval(changed)

        self.assertEqual(first["approval_fingerprint"], second["approval_fingerprint"])
        self.assertNotEqual(first["approval_fingerprint"], changed_result["approval_fingerprint"])

    def test_snapshot_mutation_does_not_leak_back(self) -> None:
        result = self._pilot_boundary()
        before = deepcopy(result)

        approval = evaluate_order_executions_pilot_approval(result)
        approval["pilot_boundary_snapshot"]["backup_plan"]["backup_action"] = "changed"

        self.assertEqual(before, result)

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
            result = evaluate_order_executions_pilot_approval(self._pilot_boundary())

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
