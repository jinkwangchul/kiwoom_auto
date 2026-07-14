from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import tempfile
from pathlib import Path
import unittest
from unittest import mock

from sell_runtime_commit_recovery_approval_gate import approve_sell_runtime_commit_recovery


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


class SellRuntimeCommitRecoveryApprovalGateTests(unittest.TestCase):
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

    def _plan(self, queue_path: Path, backup_path: Path, *, status: str = "RECOVERY_READY") -> dict:
        identity = {
            "order_id": "ORDER_1",
            "request_hash": "r" * 64,
            "lock_id": "LOCK_1",
            "execution_id": "EXEC_1",
        }
        recovery_item = {
            "status": "RECOVERY_READY",
            "manual_only": True,
            "automatic_restore_performed": False,
            "queue_path": str(queue_path),
            "backup_path": str(backup_path),
            "target_identity": deepcopy(identity),
            "queue_matching_record_count": 1,
            "backup_matching_record_count": 0,
            "queue_backup_diff": {
                "queue_order_count": 1,
                "backup_order_count": 0,
                "queue_matching_record_count": 1,
                "backup_matching_record_count": 0,
                "queue_backup_changed": True,
                "target_record_changed": True,
            },
            "manual_steps": [],
            "expected_result": {},
            "source_verification": {},
        }
        return {
            "plan_type": "SELL_RUNTIME_COMMIT_RECOVERY_PLAN",
            "ownership": "MASTER_ENGINE",
            "domain": "Execution / Runtime Commit Recovery Plan",
            "routine_dependency": None,
            "read_only": True,
            "manual_recovery_only": True,
            "recovery_required": status == "RECOVERY_READY",
            "recovery_available": status == "RECOVERY_READY",
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
            "verifier_snapshot": {},
            "recovery_plans": [recovery_item] if status == "RECOVERY_READY" else [],
            "blocked_recovery_plans": [],
            "warnings": [],
            "reasons": [],
            "summary": {
                "recovery_plan_count": 1 if status == "RECOVERY_READY" else 0,
                "blocked_recovery_plan_count": 0,
                "queue_backup_changed": status == "RECOVERY_READY",
            },
        }

    def _approval_context(self, queue_path: Path, backup_path: Path, **overrides) -> dict:
        context = {
            "user_approved": True,
            "approval_token": "APPROVED-RECOVERY-1",
            "queue_path": str(queue_path),
            "backup_path": str(backup_path),
            "approved_order_id": "ORDER_1",
            "approved_request_hash": "r" * 64,
            "approved_lock_id": "LOCK_1",
            "approved_execution_id": "EXEC_1",
        }
        context.update(overrides)
        return context

    def test_normal_approval_ready(self):
        queue_path, backup_path = self._queue_files()

        result = approve_sell_runtime_commit_recovery(
            self._plan(queue_path, backup_path),
            self._approval_context(queue_path, backup_path),
        )

        self.assertEqual(result["approval_type"], "SELL_RUNTIME_COMMIT_RECOVERY_APPROVAL_GATE")
        self.assertEqual(result["status"], "READY")
        self.assertTrue(result["approval_granted"])
        self.assertTrue(result["recovery_execution_allowed"])
        self.assertEqual(len(result["approved_recovery_actions"]), 1)
        self.assertEqual(result["approved_recovery_actions"][0]["target_identity"]["order_id"], "ORDER_1")

    def test_missing_approval_is_blocked(self):
        queue_path, backup_path = self._queue_files()

        result = approve_sell_runtime_commit_recovery(self._plan(queue_path, backup_path), {})

        self.assertEqual(result["status"], "BLOCKED")
        self.assertFalse(result["approval_granted"])

    def test_user_rejected_is_blocked(self):
        queue_path, backup_path = self._queue_files()

        result = approve_sell_runtime_commit_recovery(
            self._plan(queue_path, backup_path),
            self._approval_context(queue_path, backup_path, user_approved=False),
        )

        self.assertEqual(result["status"], "BLOCKED")

    def test_empty_token_is_blocked(self):
        queue_path, backup_path = self._queue_files()

        result = approve_sell_runtime_commit_recovery(
            self._plan(queue_path, backup_path),
            self._approval_context(queue_path, backup_path, approval_token=""),
        )

        self.assertEqual(result["status"], "BLOCKED")

    def test_queue_path_mismatch_is_invalid(self):
        queue_path, backup_path = self._queue_files()
        context = self._approval_context(queue_path, backup_path)
        context["queue_path"] = str(queue_path) + ".other"

        result = approve_sell_runtime_commit_recovery(
            self._plan(queue_path, backup_path),
            context,
        )

        self.assertEqual(result["status"], "INVALID")

    def test_backup_path_mismatch_is_invalid(self):
        queue_path, backup_path = self._queue_files()
        context = self._approval_context(queue_path, backup_path)
        context["backup_path"] = str(backup_path) + ".other"

        result = approve_sell_runtime_commit_recovery(
            self._plan(queue_path, backup_path),
            context,
        )

        self.assertEqual(result["status"], "INVALID")

    def test_identity_mismatch_is_invalid(self):
        queue_path, backup_path = self._queue_files()

        result = approve_sell_runtime_commit_recovery(
            self._plan(queue_path, backup_path),
            self._approval_context(queue_path, backup_path, approved_request_hash="wrong"),
        )

        self.assertEqual(result["status"], "INVALID")

    def test_queue_changed_after_plan_is_invalid(self):
        queue_path, backup_path = self._queue_files()
        plan = self._plan(queue_path, backup_path)
        queue_path.write_text(json.dumps({"version": 1, "orders": []}), encoding="utf-8")

        result = approve_sell_runtime_commit_recovery(plan, self._approval_context(queue_path, backup_path))

        self.assertEqual(result["status"], "INVALID")

    def test_backup_changed_after_plan_is_invalid(self):
        queue_path, backup_path = self._queue_files()
        plan = self._plan(queue_path, backup_path)
        backup_path.write_text(json.dumps({"version": 1, "orders": [_record()]}), encoding="utf-8")

        result = approve_sell_runtime_commit_recovery(plan, self._approval_context(queue_path, backup_path))

        self.assertEqual(result["status"], "INVALID")

    def test_queue_file_missing_is_invalid(self):
        queue_path, backup_path = self._queue_files()
        queue_path.unlink()

        result = approve_sell_runtime_commit_recovery(
            self._plan(queue_path, backup_path),
            self._approval_context(queue_path, backup_path),
        )

        self.assertEqual(result["status"], "INVALID")

    def test_backup_file_missing_is_invalid(self):
        queue_path, backup_path = self._queue_files()
        backup_path.unlink()

        result = approve_sell_runtime_commit_recovery(
            self._plan(queue_path, backup_path),
            self._approval_context(queue_path, backup_path),
        )

        self.assertEqual(result["status"], "INVALID")

    def test_plan_ready_without_recovery_is_blocked(self):
        queue_path, backup_path = self._queue_files()

        result = approve_sell_runtime_commit_recovery(
            self._plan(queue_path, backup_path, status="READY"),
            self._approval_context(queue_path, backup_path),
        )

        self.assertEqual(result["status"], "BLOCKED")

    def test_plan_blocked_is_blocked(self):
        queue_path, backup_path = self._queue_files()

        result = approve_sell_runtime_commit_recovery(
            self._plan(queue_path, backup_path, status="BLOCKED"),
            self._approval_context(queue_path, backup_path),
        )

        self.assertEqual(result["status"], "BLOCKED")

    def test_plan_invalid_is_invalid(self):
        queue_path, backup_path = self._queue_files()

        result = approve_sell_runtime_commit_recovery(
            self._plan(queue_path, backup_path, status="INVALID"),
            self._approval_context(queue_path, backup_path),
        )

        self.assertEqual(result["status"], "INVALID")

    def test_plan_must_contain_exactly_one_recovery_plan(self):
        queue_path, backup_path = self._queue_files()
        plan = self._plan(queue_path, backup_path)
        plan["recovery_plans"].append(deepcopy(plan["recovery_plans"][0]))

        result = approve_sell_runtime_commit_recovery(plan, self._approval_context(queue_path, backup_path))

        self.assertEqual(result["status"], "INVALID")

    def test_plan_diff_contract_must_be_recovery_ready(self):
        queue_path, backup_path = self._queue_files()
        plan = self._plan(queue_path, backup_path)
        plan["recovery_plans"][0]["queue_backup_diff"]["backup_matching_record_count"] = 1

        result = approve_sell_runtime_commit_recovery(plan, self._approval_context(queue_path, backup_path))

        self.assertEqual(result["status"], "INVALID")

    def test_input_mutation_does_not_occur(self):
        queue_path, backup_path = self._queue_files()
        plan = self._plan(queue_path, backup_path)
        context = self._approval_context(queue_path, backup_path)
        original_plan = deepcopy(plan)
        original_context = deepcopy(context)

        result = approve_sell_runtime_commit_recovery(plan, context)
        result["recovery_plan_snapshot"]["status"] = "MUTATED"
        result["approval_context_snapshot"]["approval_token"] = "MUTATED"

        self.assertEqual(plan, original_plan)
        self.assertEqual(context, original_context)

    def test_read_only_no_restore_or_send(self):
        queue_path, backup_path = self._queue_files()
        with (
            mock.patch("pathlib.Path.write_text") as write_text,
            mock.patch("pathlib.Path.replace") as replace,
            mock.patch("kiwoom_order_adapter.send_order_stub") as send_order_stub,
        ):
            result = approve_sell_runtime_commit_recovery(
                self._plan(queue_path, backup_path),
                self._approval_context(queue_path, backup_path),
            )

        self.assertEqual(result["status"], "READY")
        write_text.assert_not_called()
        replace.assert_not_called()
        send_order_stub.assert_not_called()
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_write"])
        self.assertFalse(result["file_write"])
        self.assertFalse(result["rollback"])
        self.assertFalse(result["backup_restored"])
        self.assertFalse(result["send_order"])
        self.assertFalse(result["broker_api_called"])
        self.assertFalse(result["order_request_created"])
        self.assertFalse(result["real_ready_state_changed"])

    def test_project_runtime_order_queue_not_accessed(self):
        runtime_queue = Path(__file__).resolve().parents[1] / "runtime" / "order_queue.json"
        before = hashlib.sha256(runtime_queue.read_bytes()).hexdigest()
        queue_path, backup_path = self._queue_files()

        approve_sell_runtime_commit_recovery(
            self._plan(queue_path, backup_path),
            self._approval_context(queue_path, backup_path),
        )

        after = hashlib.sha256(runtime_queue.read_bytes()).hexdigest()
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
