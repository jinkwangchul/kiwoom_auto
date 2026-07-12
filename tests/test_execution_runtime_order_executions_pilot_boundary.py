from __future__ import annotations

from copy import deepcopy
import hashlib
import json
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
from execution_runtime_order_executions_pilot_boundary import (
    EXECUTION_MODE_APPEND,
    EXECUTION_MODE_INIT,
    PILOT_TYPE,
    STATUS_BLOCKED,
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
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


class OrderExecutionsPilotBoundaryTest(unittest.TestCase):
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

    def _handoff_and_route(self, target: Path | None = None) -> tuple[dict, dict]:
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
        return self._retarget(service_handoff["handoff_preview"], target), service_handoff["commit_service_route_preview"]

    def _retarget(self, handoff: dict, target: Path) -> dict:
        target_text = str(target.resolve(strict=False))
        handoff["logical_target"] = "order_executions"
        handoff["runtime_target"] = target_text
        handoff["relative_path"] = "order_executions.json"
        handoff["commit_service_input_preview"]["logical_target"] = "order_executions"
        handoff["commit_service_input_preview"]["runtime_target"] = target_text
        handoff["commit_service_input_preview"]["relative_path"] = "order_executions.json"
        allowlist = handoff["allowlist_decision"]
        allowlist["logical_target"] = "order_executions"
        allowlist["relative_path"] = "order_executions.json"
        allowlist["resolved_path"] = target_text
        allowlist["normalized_path"] = target_text
        return handoff

    def _boundary(self, *, target: Path | None = None) -> dict:
        handoff, route = self._handoff_and_route(target)
        return build_order_executions_pilot_boundary(handoff, route, self.runtime_root)

    def test_missing_file_is_init_ready(self) -> None:
        result = self._boundary()

        self.assertEqual(PILOT_TYPE, result["pilot_type"])
        self.assertEqual(STATUS_READY, result["status"])
        self.assertTrue(result["pilot_ready"])
        self.assertEqual("order_executions", result["logical_target"])
        self.assertEqual(str(self.target.resolve(strict=False)), result["runtime_target"])
        self.assertFalse(result["file_exists"])
        self.assertEqual(EXECUTION_MODE_INIT, result["execution_mode"])
        self.assertFalse(result["backup_required"])
        self.assertEqual("none", result["backup_plan"]["backup_action"])
        self.assertTrue(result["atomic_write_plan"]["can_execute_preview"])
        self.assertEqual("remove_created_file_after_init_failure", result["rollback_plan"]["rollback_action"])
        self.assertTrue(result["rollback_plan"]["cleanup_required_on_init_failure"])
        self.assertEqual([], result["issues"])

    def test_existing_file_is_append_ready_and_requires_backup(self) -> None:
        _write_json(self.target, default_order_executions_data())

        result = self._boundary()

        self.assertEqual(STATUS_READY, result["status"])
        self.assertTrue(result["pilot_ready"])
        self.assertTrue(result["file_exists"])
        self.assertEqual(EXECUTION_MODE_APPEND, result["execution_mode"])
        self.assertTrue(result["backup_required"])
        self.assertEqual("copy_existing_file", result["backup_plan"]["backup_action"])
        self.assertEqual(str(self.target.resolve(strict=False)) + ".bak", result["backup_plan"]["backup_path_preview"])
        self.assertTrue(result["atomic_write_plan"]["can_execute_preview"])
        self.assertEqual("restore_backup_after_write_failure", result["rollback_plan"]["rollback_action"])
        self.assertTrue(result["rollback_plan"]["rollback_required_on_write_failure"])

    def test_wrong_target_or_path_is_blocked(self) -> None:
        handoff, route = self._handoff_and_route()
        handoff["logical_target"] = "order_locks"
        path_handoff, path_route = self._handoff_and_route()
        path_handoff["relative_path"] = "order_locks.json"

        target_result = build_order_executions_pilot_boundary(handoff, route, self.runtime_root)
        path_result = build_order_executions_pilot_boundary(path_handoff, path_route, self.runtime_root)

        self.assertEqual("INVALID", target_result["status"])
        self.assertIn("PILOT_LOGICAL_TARGET_MUST_BE_ORDER_EXECUTIONS", target_result["issues"])
        self.assertEqual("INVALID", path_result["status"])
        self.assertIn("PILOT_RELATIVE_PATH_MUST_BE_ORDER_EXECUTIONS_JSON", path_result["issues"])

    def test_handoff_blocked_or_invalid_is_fail_closed(self) -> None:
        blocked, route = self._handoff_and_route()
        blocked["status"] = "BLOCKED"
        blocked["handoff_ready"] = False
        invalid, invalid_route = self._handoff_and_route()
        invalid["gate_decision"]["status"] = "INVALID"

        blocked_result = build_order_executions_pilot_boundary(blocked, route, self.runtime_root)
        invalid_result = build_order_executions_pilot_boundary(invalid, invalid_route, self.runtime_root)

        self.assertEqual(STATUS_BLOCKED, blocked_result["status"])
        self.assertFalse(blocked_result["pilot_ready"])
        self.assertIn("HANDOFF_NOT_READY", blocked_result["issues"])
        self.assertEqual("INVALID", invalid_result["status"])
        self.assertFalse(invalid_result["pilot_ready"])
        self.assertIn("VALIDATION_GATE_INVALID", invalid_result["issues"])

    def test_safety_flag_violations_are_blocked(self) -> None:
        handoff, route = self._handoff_and_route()
        route["call_allowed"] = True
        route["dry_run_only"] = False
        handoff["runtime_write"] = True

        result = build_order_executions_pilot_boundary(handoff, route, self.runtime_root)

        self.assertEqual("INVALID", result["status"])
        self.assertFalse(result["pilot_ready"])
        self.assertIn("COMMIT_SERVICE_CALL_MUST_NOT_BE_ALLOWED", result["issues"])
        self.assertIn("COMMIT_SERVICE_ROUTE_DRY_RUN_ONLY_REQUIRED", result["issues"])
        self.assertIn("RUNTIME_WRITE_MUST_BE_FALSE", result["issues"])

    def test_runtime_target_must_stay_under_runtime_root(self) -> None:
        outside = self.runtime_root.parent / "outside_order_executions.json"
        handoff, route = self._handoff_and_route(outside)

        result = build_order_executions_pilot_boundary(handoff, route, self.runtime_root)

        self.assertEqual("INVALID", result["status"])
        self.assertFalse(result["pilot_ready"])
        self.assertIn("RUNTIME_TARGET_OUTSIDE_RUNTIME_ROOT", result["issues"])

    def test_backup_atomic_and_rollback_plans_are_preview_only(self) -> None:
        _write_json(self.target, default_order_executions_data())

        result = self._boundary()

        self.assertFalse(result["backup_plan"]["backup_created"])
        self.assertEqual("_write_json_atomic", result["atomic_write_plan"]["method"])
        self.assertTrue(result["atomic_write_plan"]["temp_path_preview"].endswith(".tmp"))
        self.assertFalse(result["atomic_write_plan"]["writer_called"])
        self.assertFalse(result["rollback_plan"]["rollback_executed"])
        self.assertFalse(result["backup_plan"]["runtime_write"])
        self.assertFalse(result["atomic_write_plan"]["runtime_write"])
        self.assertFalse(result["rollback_plan"]["runtime_write"])
        self.assertTrue(result["preview_only"])
        self.assertTrue(result["dry_run_only"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["commit_service_called"])

    def test_mutating_result_does_not_modify_input(self) -> None:
        handoff, route = self._handoff_and_route()
        before = (deepcopy(handoff), deepcopy(route))

        result = build_order_executions_pilot_boundary(handoff, route, self.runtime_root)
        result["backup_plan"]["backup_path_preview"] = "changed"
        result["preconditions"][0]["ok"] = False
        result["issues"].append("RESULT_ONLY")

        self.assertEqual(before, (handoff, route))

    def test_no_write_backup_rollback_commit_service_order_queue_or_send_order(self) -> None:
        runtime_paths = [
            ROOT / "runtime" / "order_queue.json",
            ROOT / "runtime" / "order_executions.json",
            ROOT / "runtime" / "order_locks.json",
        ]
        before_runtime = {str(path): _sha256(path) for path in runtime_paths}
        handoff, route = self._handoff_and_route()

        with (
            mock.patch("execution_runtime_commit_service.commit_execution_runtime_plan") as runtime_commit,
            mock.patch("execution_runtime_file_init_commit_service.commit_execution_runtime_file_init_plan") as init_commit,
            mock.patch("execution_runtime_commit_service._write_json_atomic") as runtime_writer,
            mock.patch("execution_runtime_commit_service._make_backup") as backup,
            mock.patch("execution_runtime_commit_service._restore_backups") as restore,
            mock.patch("execution_queue_commit_service.commit_execution_queue_manually") as queue_commit,
            mock.patch("send_order_entrypoint.execute_send_order") as send_order,
        ):
            result = build_order_executions_pilot_boundary(handoff, route, self.runtime_root)

        self.assertEqual(STATUS_READY, result["status"])
        runtime_commit.assert_not_called()
        init_commit.assert_not_called()
        runtime_writer.assert_not_called()
        backup.assert_not_called()
        restore.assert_not_called()
        queue_commit.assert_not_called()
        send_order.assert_not_called()
        self.assertFalse(self.target.exists())
        self.assertEqual(before_runtime, {str(path): _sha256(path) for path in runtime_paths})
        self.assertFalse((ROOT / "runtime" / "order_executions.json").exists())
        self.assertFalse((ROOT / "runtime" / "order_locks.json").exists())


if __name__ == "__main__":
    unittest.main()
