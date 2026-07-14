from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import tempfile
from pathlib import Path
import unittest
from unittest import mock

from sell_runtime_commit_recovery_post_check import check_sell_runtime_commit_recovery_post_commit


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


class SellRuntimeCommitRecoveryPostCheckTests(unittest.TestCase):
    def _files(
        self,
        *,
        queue_orders: list[dict] | None = None,
        backup_orders: list[dict] | None = None,
        safety_orders: list[dict] | None = None,
    ) -> tuple[Path, Path, Path]:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        queue_path = Path(temp.name) / "order_queue.json"
        backup_path = Path(str(queue_path) + ".bak")
        safety_path = Path(str(queue_path) + ".recovery_safety.bak")
        queue_path.write_text(json.dumps({"version": 1, "orders": [] if queue_orders is None else queue_orders}, indent=2), encoding="utf-8")
        backup_path.write_text(json.dumps({"version": 1, "orders": [] if backup_orders is None else backup_orders}, indent=2), encoding="utf-8")
        safety_path.write_text(json.dumps({"version": 1, "orders": [_record()] if safety_orders is None else safety_orders}, indent=2), encoding="utf-8")
        return queue_path, backup_path, safety_path

    def _executor(self, queue_path: Path, backup_path: Path, safety_path: Path, *, status: str = "READY") -> dict:
        result = {
            "status": "READY",
            "queue_path": str(queue_path),
            "backup_path": str(backup_path),
            "safety_backup_path": str(safety_path),
            "temp_restore_path": str(queue_path) + ".tmp",
            "safety_backup_created": True,
            "temp_restore_written": True,
            "target_identity": {
                "order_id": "ORDER_1",
                "request_hash": "r" * 64,
                "lock_id": "LOCK_1",
                "execution_id": "EXEC_1",
            },
            "approval_token": "APPROVED-RECOVERY-1",
            "restore_executed": True,
            "post_restore_verified": status == "READY",
            "reasons": [],
            "warnings": [],
        }
        return {
            "executor_type": "SELL_RUNTIME_COMMIT_RECOVERY_EXECUTOR",
            "ownership": "MASTER_ENGINE",
            "domain": "Execution / Runtime Commit Recovery Executor",
            "routine_dependency": None,
            "status": status,
            "approval_snapshot": {},
            "recovery_results": [result] if status != "BLOCKED" else [],
            "blocked_recovery_results": [],
            "warnings": [],
            "reasons": [],
            "runtime_write": status != "BLOCKED",
            "queue_write": status != "BLOCKED",
            "file_write": status != "BLOCKED",
            "rollback_executed": status != "BLOCKED",
            "backup_restored": status != "BLOCKED",
            "send_order": False,
            "broker_api_called": False,
            "actual_order_sent": False,
            "order_request_created": False,
            "real_ready_state_changed": False,
            "summary": {},
        }

    def test_ready_after_normal_restore(self):
        queue_path, backup_path, safety_path = self._files()

        result = check_sell_runtime_commit_recovery_post_commit(
            self._executor(queue_path, backup_path, safety_path)
        )

        self.assertEqual(result["post_check_type"], "SELL_RUNTIME_COMMIT_RECOVERY_POST_CHECK")
        self.assertEqual(result["status"], "READY")
        self.assertTrue(result["post_recovery_verified"])
        self.assertTrue(result["observed_runtime_write"])
        self.assertTrue(result["observed_queue_write"])
        self.assertTrue(result["observed_file_write"])
        self.assertTrue(result["observed_rollback_executed"])
        self.assertTrue(result["observed_backup_restored"])
        self.assertEqual(len(result["checked_records"]), 1)

    def test_queue_backup_mismatch_is_invalid(self):
        queue_path, backup_path, safety_path = self._files(queue_orders=[{"other": "record"}])

        result = check_sell_runtime_commit_recovery_post_commit(
            self._executor(queue_path, backup_path, safety_path)
        )

        self.assertEqual(result["status"], "INVALID")

    def test_target_identity_remaining_is_invalid(self):
        queue_path, backup_path, safety_path = self._files(queue_orders=[_record()], backup_orders=[_record()])

        result = check_sell_runtime_commit_recovery_post_commit(
            self._executor(queue_path, backup_path, safety_path)
        )

        self.assertEqual(result["status"], "INVALID")

    def test_safety_backup_missing_is_invalid(self):
        queue_path, backup_path, safety_path = self._files()
        safety_path.unlink()

        result = check_sell_runtime_commit_recovery_post_commit(
            self._executor(queue_path, backup_path, safety_path)
        )

        self.assertEqual(result["status"], "INVALID")

    def test_safety_backup_corrupt_is_invalid(self):
        queue_path, backup_path, safety_path = self._files()
        safety_path.write_text("{bad json", encoding="utf-8")

        result = check_sell_runtime_commit_recovery_post_commit(
            self._executor(queue_path, backup_path, safety_path)
        )

        self.assertEqual(result["status"], "INVALID")

    def test_safety_backup_without_target_is_invalid(self):
        queue_path, backup_path, safety_path = self._files(safety_orders=[])

        result = check_sell_runtime_commit_recovery_post_commit(
            self._executor(queue_path, backup_path, safety_path)
        )

        self.assertEqual(result["status"], "INVALID")

    def test_safety_backup_duplicate_target_is_invalid(self):
        queue_path, backup_path, safety_path = self._files(safety_orders=[_record(), _record()])

        result = check_sell_runtime_commit_recovery_post_commit(
            self._executor(queue_path, backup_path, safety_path)
        )

        self.assertEqual(result["status"], "INVALID")

    def test_upstream_blocked_is_blocked(self):
        queue_path, backup_path, safety_path = self._files()

        result = check_sell_runtime_commit_recovery_post_commit(
            self._executor(queue_path, backup_path, safety_path, status="BLOCKED")
        )

        self.assertEqual(result["status"], "BLOCKED")

    def test_upstream_invalid_with_restore_is_invalid(self):
        queue_path, backup_path, safety_path = self._files()

        result = check_sell_runtime_commit_recovery_post_commit(
            self._executor(queue_path, backup_path, safety_path, status="INVALID")
        )

        self.assertEqual(result["status"], "INVALID")
        self.assertEqual(len(result["checked_records"]), 1)
        self.assertTrue(result["observed_runtime_write"])
        self.assertTrue(result["observed_queue_write"])
        self.assertTrue(result["observed_file_write"])
        self.assertTrue(result["observed_rollback_executed"])
        self.assertTrue(result["observed_backup_restored"])

    def test_restore_not_executed_is_blocked(self):
        queue_path, backup_path, safety_path = self._files()
        executor = self._executor(queue_path, backup_path, safety_path)
        executor["runtime_write"] = False
        executor["queue_write"] = False
        executor["file_write"] = False
        executor["backup_restored"] = False
        executor["rollback_executed"] = False
        executor["recovery_results"][0]["restore_executed"] = False

        result = check_sell_runtime_commit_recovery_post_commit(executor)

        self.assertEqual(result["status"], "BLOCKED")
        self.assertFalse(result["observed_runtime_write"])
        self.assertFalse(result["observed_queue_write"])
        self.assertFalse(result["observed_file_write"])
        self.assertFalse(result["observed_rollback_executed"])
        self.assertFalse(result["observed_backup_restored"])

    def test_post_check_itself_remains_read_only_with_observed_effects(self):
        queue_path, backup_path, safety_path = self._files()

        result = check_sell_runtime_commit_recovery_post_commit(
            self._executor(queue_path, backup_path, safety_path)
        )

        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_write"])
        self.assertFalse(result["file_write"])
        self.assertFalse(result["rollback"])
        self.assertFalse(result["backup_restored"])
        self.assertTrue(result["summary"]["observed_runtime_write"])
        self.assertTrue(result["summary"]["observed_queue_write"])
        self.assertTrue(result["summary"]["observed_file_write"])
        self.assertTrue(result["summary"]["observed_rollback_executed"])
        self.assertTrue(result["summary"]["observed_backup_restored"])

    def test_wrong_executor_type_is_invalid(self):
        queue_path, backup_path, safety_path = self._files()
        executor = self._executor(queue_path, backup_path, safety_path)
        executor["executor_type"] = "OTHER"

        result = check_sell_runtime_commit_recovery_post_commit(executor)

        self.assertEqual(result["status"], "INVALID")

    def test_safety_flags_must_remain_false(self):
        queue_path, backup_path, safety_path = self._files()
        executor = self._executor(queue_path, backup_path, safety_path)
        executor["send_order"] = True

        result = check_sell_runtime_commit_recovery_post_commit(executor)

        self.assertEqual(result["status"], "INVALID")

    def test_input_mutation_does_not_occur(self):
        queue_path, backup_path, safety_path = self._files()
        executor = self._executor(queue_path, backup_path, safety_path)
        original = deepcopy(executor)

        result = check_sell_runtime_commit_recovery_post_commit(executor)
        result["executor_snapshot"]["status"] = "MUTATED"

        self.assertEqual(executor, original)

    def test_read_only_no_write_restore_or_send(self):
        queue_path, backup_path, safety_path = self._files()
        with (
            mock.patch("pathlib.Path.write_text") as write_text,
            mock.patch("pathlib.Path.replace") as replace,
            mock.patch("kiwoom_order_adapter.send_order_stub") as send_order_stub,
        ):
            result = check_sell_runtime_commit_recovery_post_commit(
                self._executor(queue_path, backup_path, safety_path)
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
        queue_path, backup_path, safety_path = self._files()

        check_sell_runtime_commit_recovery_post_commit(
            self._executor(queue_path, backup_path, safety_path)
        )

        after = hashlib.sha256(runtime_queue.read_bytes()).hexdigest()
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
