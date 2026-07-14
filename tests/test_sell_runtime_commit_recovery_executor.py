from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import tempfile
from pathlib import Path
import unittest
from unittest import mock

from sell_runtime_commit_recovery_executor import execute_sell_runtime_commit_recovery


def _record(
    *,
    order_id: str = "ORDER_1",
    request_hash: str = "r" * 64,
    lock_id: str = "LOCK_1",
    execution_id: str = "EXEC_1",
) -> dict:
    return {
        "id": f"ORDER_QUEUED_{order_id}",
        "status": "ORDER_QUEUED",
        "source": "execution_queue_pending",
        "source_signal_id": "SIG_1",
        "order_id": order_id,
        "candidate_id": f"CANDIDATE_{order_id}",
        "queue_pending_id": f"QUEUE_PENDING_{order_id}",
        "request_hash": request_hash,
        "lock_id": lock_id,
        "execution_id": execution_id,
        "send_order_called": False,
        "execution_enabled": False,
    }


class SellRuntimeCommitRecoveryExecutorTests(unittest.TestCase):
    def _queue_files(self, *, queue_orders: list[dict] | None = None, backup_orders: list[dict] | None = None) -> tuple[Path, Path]:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        queue_path = Path(temp.name) / "order_queue.json"
        backup_path = Path(str(queue_path) + ".bak")
        queue_path.write_text(
            json.dumps({"version": 1, "orders": [_record()] if queue_orders is None else queue_orders}, indent=2),
            encoding="utf-8",
        )
        backup_path.write_text(
            json.dumps({"version": 1, "orders": [] if backup_orders is None else backup_orders}, indent=2),
            encoding="utf-8",
        )
        return queue_path, backup_path

    def _approval(self, queue_path: Path, backup_path: Path, *, status: str = "READY") -> dict:
        identity = {
            "order_id": "ORDER_1",
            "request_hash": "r" * 64,
            "lock_id": "LOCK_1",
            "execution_id": "EXEC_1",
        }
        action = {
            "status": "READY",
            "approval_action": "MANUAL_RECOVERY_APPROVED",
            "manual_only": True,
            "approval_token": "APPROVED-RECOVERY-1",
            "queue_path": str(queue_path),
            "backup_path": str(backup_path),
            "target_identity": deepcopy(identity),
            "queue_backup_diff": {
                "queue_order_count": 1,
                "backup_order_count": 0,
                "queue_matching_record_count": 1,
                "backup_matching_record_count": 0,
                "queue_backup_changed": True,
                "target_record_changed": True,
            },
            "source_recovery_plan": {},
            "restore_executed": False,
            "rollback_executed": False,
            "send_order_called": False,
            "broker_api_called": False,
        }
        return {
            "approval_type": "SELL_RUNTIME_COMMIT_RECOVERY_APPROVAL_GATE",
            "ownership": "MASTER_ENGINE",
            "domain": "Execution / Runtime Commit Recovery Approval Gate",
            "routine_dependency": None,
            "read_only": True,
            "manual_recovery_only": True,
            "approval_granted": status == "READY",
            "recovery_execution_allowed": status == "READY",
            "runtime_write": False,
            "queue_write": False,
            "file_write": False,
            "rollback": False,
            "backup_restored": False,
            "send_order": False,
            "broker_api_called": False,
            "actual_order_sent": False,
            "order_request_created": False,
            "real_ready_state_changed": False,
            "status": status,
            "recovery_plan_snapshot": {},
            "approval_context_snapshot": {},
            "approved_recovery_actions": [action] if status == "READY" else [],
            "blocked_approval_actions": [],
            "warnings": [],
            "reasons": [],
            "summary": {},
        }

    def _read_json(self, path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8"))

    def test_ready_approval_restores_queue(self):
        queue_path, backup_path = self._queue_files()

        result = execute_sell_runtime_commit_recovery(self._approval(queue_path, backup_path))

        self.assertEqual(result["executor_type"], "SELL_RUNTIME_COMMIT_RECOVERY_EXECUTOR")
        self.assertEqual(result["status"], "READY")
        self.assertTrue(result["runtime_write"])
        self.assertTrue(result["queue_write"])
        self.assertTrue(result["file_write"])
        self.assertTrue(result["rollback_executed"])
        self.assertTrue(result["backup_restored"])
        self.assertTrue(result["recovery_results"][0]["safety_backup_created"])
        self.assertTrue(result["recovery_results"][0]["temp_restore_written"])
        self.assertEqual(self._read_json(queue_path), self._read_json(backup_path))
        self.assertEqual(self._read_json(queue_path)["orders"], [])

    def test_safety_backup_created(self):
        queue_path, backup_path = self._queue_files()

        result = execute_sell_runtime_commit_recovery(self._approval(queue_path, backup_path))

        safety_backup = Path(result["recovery_results"][0]["safety_backup_path"])
        self.assertTrue(safety_backup.exists())
        self.assertEqual(self._read_json(safety_backup)["orders"], [_record()])

    def test_atomic_temp_file_replaced(self):
        queue_path, backup_path = self._queue_files()

        result = execute_sell_runtime_commit_recovery(self._approval(queue_path, backup_path))

        temp_restore = Path(result["recovery_results"][0]["temp_restore_path"])
        self.assertFalse(temp_restore.exists())
        self.assertEqual(self._read_json(queue_path), self._read_json(backup_path))

    def test_target_identity_removed_after_restore(self):
        queue_path, backup_path = self._queue_files()

        execute_sell_runtime_commit_recovery(self._approval(queue_path, backup_path))

        orders = self._read_json(queue_path)["orders"]
        self.assertEqual([item for item in orders if item.get("order_id") == "ORDER_1"], [])

    def test_blocked_approval_input_is_blocked(self):
        queue_path, backup_path = self._queue_files()

        result = execute_sell_runtime_commit_recovery(self._approval(queue_path, backup_path, status="BLOCKED"))

        self.assertEqual(result["status"], "BLOCKED")
        self.assertFalse(result["backup_restored"])

    def test_invalid_approval_input_is_invalid(self):
        queue_path, backup_path = self._queue_files()

        result = execute_sell_runtime_commit_recovery(self._approval(queue_path, backup_path, status="INVALID"))

        self.assertEqual(result["status"], "INVALID")
        self.assertFalse(result["backup_restored"])

    def test_missing_approval_granted_is_blocked(self):
        queue_path, backup_path = self._queue_files()
        approval = self._approval(queue_path, backup_path)
        approval["approval_granted"] = False

        result = execute_sell_runtime_commit_recovery(approval)

        self.assertEqual(result["status"], "BLOCKED")

    def test_missing_recovery_execution_allowed_is_blocked(self):
        queue_path, backup_path = self._queue_files()
        approval = self._approval(queue_path, backup_path)
        approval["recovery_execution_allowed"] = False

        result = execute_sell_runtime_commit_recovery(approval)

        self.assertEqual(result["status"], "BLOCKED")

    def test_wrong_approval_type_is_invalid(self):
        queue_path, backup_path = self._queue_files()
        approval = self._approval(queue_path, backup_path)
        approval["approval_type"] = "OTHER"

        result = execute_sell_runtime_commit_recovery(approval)

        self.assertEqual(result["status"], "INVALID")

    def test_actions_must_contain_exactly_one(self):
        queue_path, backup_path = self._queue_files()
        approval = self._approval(queue_path, backup_path)
        approval["approved_recovery_actions"].append(deepcopy(approval["approved_recovery_actions"][0]))

        result = execute_sell_runtime_commit_recovery(approval)

        self.assertEqual(result["status"], "INVALID")

    def test_empty_token_is_invalid(self):
        queue_path, backup_path = self._queue_files()
        approval = self._approval(queue_path, backup_path)
        approval["approved_recovery_actions"][0]["approval_token"] = ""

        result = execute_sell_runtime_commit_recovery(approval)

        self.assertEqual(result["status"], "INVALID")

    def test_missing_identity_is_invalid(self):
        queue_path, backup_path = self._queue_files()
        approval = self._approval(queue_path, backup_path)
        approval["approved_recovery_actions"][0]["target_identity"]["lock_id"] = ""

        result = execute_sell_runtime_commit_recovery(approval)

        self.assertEqual(result["status"], "INVALID")

    def test_queue_mutated_before_execution_is_blocked(self):
        queue_path, backup_path = self._queue_files()
        approval = self._approval(queue_path, backup_path)
        queue_path.write_text(json.dumps({"version": 1, "orders": []}), encoding="utf-8")

        result = execute_sell_runtime_commit_recovery(approval)

        self.assertEqual(result["status"], "BLOCKED")
        self.assertFalse(result["backup_restored"])

    def test_backup_mutated_before_execution_is_blocked(self):
        queue_path, backup_path = self._queue_files()
        approval = self._approval(queue_path, backup_path)
        backup_path.write_text(json.dumps({"version": 1, "orders": [_record()]}), encoding="utf-8")

        result = execute_sell_runtime_commit_recovery(approval)

        self.assertEqual(result["status"], "BLOCKED")
        self.assertFalse(result["backup_restored"])

    def test_missing_queue_file_is_blocked(self):
        queue_path, backup_path = self._queue_files()
        queue_path.unlink()

        result = execute_sell_runtime_commit_recovery(self._approval(queue_path, backup_path))

        self.assertEqual(result["status"], "BLOCKED")

    def test_missing_backup_file_is_blocked(self):
        queue_path, backup_path = self._queue_files()
        backup_path.unlink()

        result = execute_sell_runtime_commit_recovery(self._approval(queue_path, backup_path))

        self.assertEqual(result["status"], "BLOCKED")

    def test_actual_restore_then_post_validation_failure_keeps_side_effect_flags(self):
        queue_path, backup_path = self._queue_files()
        approval = self._approval(queue_path, backup_path)
        queue_data = self._read_json(queue_path)
        backup_data = self._read_json(backup_path)
        call_count = {"count": 0}

        def fake_read(path):
            call_count["count"] += 1
            if call_count["count"] == 1:
                return deepcopy(queue_data), None
            if call_count["count"] == 2:
                return deepcopy(backup_data), None
            if call_count["count"] == 3:
                return deepcopy(backup_data), None
            return {"version": 1, "orders": [_record()]}, None

        with mock.patch("sell_runtime_commit_recovery_executor._read_json_object", side_effect=fake_read):
            result = execute_sell_runtime_commit_recovery(approval)

        self.assertEqual(result["status"], "INVALID")
        self.assertTrue(result["runtime_write"])
        self.assertTrue(result["queue_write"])
        self.assertTrue(result["file_write"])
        self.assertTrue(result["rollback_executed"])
        self.assertTrue(result["backup_restored"])
        self.assertFalse(result["send_order"])
        self.assertFalse(result["broker_api_called"])
        self.assertFalse(result["actual_order_sent"])
        self.assertFalse(result["order_request_created"])
        self.assertFalse(result["real_ready_state_changed"])

    def test_temp_restore_validation_failure_keeps_file_write_only(self):
        queue_path, backup_path = self._queue_files()
        approval = self._approval(queue_path, backup_path)
        queue_data = self._read_json(queue_path)
        backup_data = self._read_json(backup_path)
        call_count = {"count": 0}

        def fake_read(path):
            call_count["count"] += 1
            if call_count["count"] == 1:
                return deepcopy(queue_data), None
            if call_count["count"] == 2:
                return deepcopy(backup_data), None
            return {}, "forced temp validation failure"

        with mock.patch("sell_runtime_commit_recovery_executor._read_json_object", side_effect=fake_read):
            result = execute_sell_runtime_commit_recovery(approval)

        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(result["file_write"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_write"])
        self.assertFalse(result["rollback_executed"])
        self.assertFalse(result["backup_restored"])
        blocked = result["blocked_recovery_results"][0]
        self.assertTrue(blocked["safety_backup_created"])
        self.assertTrue(blocked["temp_restore_written"])
        self.assertTrue(Path(blocked["temp_restore_path"]).exists())

    def test_replace_before_restore_failure_keeps_file_write_only(self):
        queue_path, backup_path = self._queue_files()

        with mock.patch("pathlib.Path.replace", side_effect=RuntimeError("replace failed")):
            result = execute_sell_runtime_commit_recovery(self._approval(queue_path, backup_path))

        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(result["file_write"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_write"])
        self.assertFalse(result["rollback_executed"])
        self.assertFalse(result["backup_restored"])
        blocked = result["blocked_recovery_results"][0]
        self.assertTrue(blocked["safety_backup_created"])
        self.assertTrue(blocked["temp_restore_written"])
        self.assertTrue(Path(blocked["temp_restore_path"]).exists())
        self.assertEqual(self._read_json(queue_path)["orders"], [_record()])

    def test_input_mutation_does_not_occur(self):
        queue_path, backup_path = self._queue_files()
        approval = self._approval(queue_path, backup_path)
        original = deepcopy(approval)

        result = execute_sell_runtime_commit_recovery(approval)
        result["approval_snapshot"]["status"] = "MUTATED"

        self.assertEqual(approval, original)

    def test_sendorder_and_broker_not_called(self):
        queue_path, backup_path = self._queue_files()
        with mock.patch("kiwoom_order_adapter.send_order_stub") as send_order_stub:
            result = execute_sell_runtime_commit_recovery(self._approval(queue_path, backup_path))

        send_order_stub.assert_not_called()
        self.assertFalse(result["send_order"])
        self.assertFalse(result["broker_api_called"])
        self.assertFalse(result["actual_order_sent"])
        self.assertFalse(result["order_request_created"])
        self.assertFalse(result["real_ready_state_changed"])

    def test_project_runtime_order_queue_not_accessed(self):
        runtime_queue = Path(__file__).resolve().parents[1] / "runtime" / "order_queue.json"
        before = hashlib.sha256(runtime_queue.read_bytes()).hexdigest()
        queue_path, backup_path = self._queue_files()

        execute_sell_runtime_commit_recovery(self._approval(queue_path, backup_path))

        after = hashlib.sha256(runtime_queue.read_bytes()).hexdigest()
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
