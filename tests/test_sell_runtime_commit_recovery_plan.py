from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import tempfile
from pathlib import Path
import unittest
from unittest import mock

from sell_runtime_commit_recovery_plan import build_sell_runtime_commit_recovery_plan


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
        "execution_request": {
            "execution_id": execution_id,
            "request_hash": request_hash,
            "lock_id": lock_id,
        },
        "queue_contract_version": "preview-1",
        "send_order_called": False,
        "execution_enabled": False,
    }


class SellRuntimeCommitRecoveryPlanTests(unittest.TestCase):
    def _queue_files(
        self,
        *,
        queue_orders: list[dict] | None = None,
        backup_orders: list[dict] | None = None,
    ) -> tuple[Path, Path]:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        queue_path = Path(temp.name) / "order_queue.json"
        backup_path = Path(str(queue_path) + ".bak")
        queue_path.write_text(
            json.dumps({"version": 1, "updated_at": "after", "orders": [_record()] if queue_orders is None else queue_orders}, indent=2),
            encoding="utf-8",
        )
        backup_path.write_text(
            json.dumps({"version": 1, "updated_at": "before", "orders": [] if backup_orders is None else backup_orders}, indent=2),
            encoding="utf-8",
        )
        return queue_path, backup_path

    def _verifier(
        self,
        queue_path: Path,
        backup_path: Path | None,
        *,
        status: str = "INVALID",
        post_commit_file_verified: bool = False,
        include_blocked: bool = True,
        committed: bool = True,
    ) -> dict:
        commit_result = {
            "committed": committed,
            "order_queue_path": str(queue_path),
            "backup_path": str(backup_path) if backup_path else None,
            "order_id": "ORDER_1",
            "request_hash": "r" * 64,
            "lock_id": "LOCK_1",
            "status": "ORDER_QUEUED" if committed else None,
            "send_order_called": False,
            "execution_enabled": False,
        }
        execution_result = {
            "status": status,
            "source_signal_id": "SIG_1",
            "order_id": "ORDER_1",
            "candidate_id": "CANDIDATE_ORDER_1",
            "queue_pending_id": "QUEUE_PENDING_ORDER_1",
            "execution_id": "EXEC_1",
            "request_hash": "r" * 64,
            "lock_id": "LOCK_1",
            "commit_result": commit_result,
            "runtime_write": committed,
            "queue_write": committed,
            "file_write": committed,
            "queue_committed": committed,
            "send_order": False,
            "broker_api_called": False,
            "actual_order_sent": False,
            "order_request_created": False,
            "real_ready_state_changed": False,
            "runtime_commit_executed": committed,
            "reasons": [],
            "warnings": [],
        }
        blocked = {
            "status": "INVALID",
            "queue_path": str(queue_path),
            "backup_path": str(backup_path) if backup_path else None,
            "source_execution_result": deepcopy(execution_result),
            "commit_result": deepcopy(commit_result),
            "reasons": ["ORDER_QUEUED record mismatch"],
        }
        verified = {
            "status": "READY",
            "order_queue_path": str(queue_path),
            "backup_path": str(backup_path) if backup_path else None,
            "record": _record(),
            "commit_result": deepcopy(commit_result),
            "source_execution_result": deepcopy(execution_result),
        }
        return {
            "verifier_type": "SELL_RUNTIME_COMMIT_POST_COMMIT_VERIFIER",
            "ownership": "MASTER_ENGINE",
            "domain": "Execution / Runtime Commit Post-Commit Verifier",
            "routine_dependency": None,
            "read_only": True,
            "runtime_write": False,
            "queue_write": False,
            "file_write": False,
            "rollback": False,
            "send_order": False,
            "broker_api_called": False,
            "actual_order_sent": False,
            "order_request_created": False,
            "real_ready_state_changed": False,
            "status": status,
            "post_commit_verified": status == "READY",
            "post_commit_file_verified": post_commit_file_verified,
            "executor_snapshot": {
                "executor_type": "SELL_RUNTIME_COMMIT_REAL_EXECUTOR",
                "status": status,
                "queue_committed": committed,
                "runtime_commit_executed": committed,
                "execution_results": [deepcopy(execution_result)],
            },
            "verified_records": [verified] if post_commit_file_verified else [],
            "blocked_verifications": [blocked] if include_blocked else [],
            "warnings": [],
            "reasons": [],
            "summary": {
                "verified_record_count": 1 if post_commit_file_verified else 0,
                "blocked_verification_count": 0,
                "invalid_verification_count": 1 if include_blocked else 0,
            },
        }

    def test_ready_verifier_does_not_require_recovery(self):
        queue_path, backup_path = self._queue_files()

        result = build_sell_runtime_commit_recovery_plan(
            self._verifier(queue_path, backup_path, status="READY", post_commit_file_verified=True, include_blocked=False)
        )

        self.assertEqual(result["plan_type"], "SELL_RUNTIME_COMMIT_RECOVERY_PLAN")
        self.assertEqual(result["status"], "READY")
        self.assertFalse(result["recovery_required"])
        self.assertFalse(result["recovery_available"])
        self.assertEqual(result["recovery_plans"], [])

    def test_invalid_with_valid_backup_is_recovery_ready(self):
        queue_path, backup_path = self._queue_files()

        result = build_sell_runtime_commit_recovery_plan(self._verifier(queue_path, backup_path))

        self.assertEqual(result["status"], "RECOVERY_READY")
        self.assertTrue(result["recovery_required"])
        self.assertTrue(result["recovery_available"])
        self.assertEqual(len(result["recovery_plans"]), 1)
        plan = result["recovery_plans"][0]
        self.assertEqual(plan["target_identity"]["order_id"], "ORDER_1")
        self.assertEqual(plan["target_identity"]["request_hash"], "r" * 64)
        self.assertEqual(plan["target_identity"]["lock_id"], "LOCK_1")
        self.assertEqual(plan["target_identity"]["execution_id"], "EXEC_1")
        self.assertTrue(plan["queue_backup_diff"]["queue_backup_changed"])
        self.assertTrue(plan["manual_only"])
        self.assertFalse(plan["automatic_restore_performed"])

    def test_invalid_with_missing_backup_is_blocked(self):
        queue_path, backup_path = self._queue_files()
        backup_path.unlink()

        result = build_sell_runtime_commit_recovery_plan(self._verifier(queue_path, backup_path))

        self.assertEqual(result["status"], "BLOCKED")
        self.assertFalse(result["recovery_available"])
        self.assertEqual(len(result["blocked_recovery_plans"]), 1)

    def test_invalid_without_backup_path_is_blocked(self):
        queue_path, _ = self._queue_files()

        result = build_sell_runtime_commit_recovery_plan(self._verifier(queue_path, None))

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("backup_path is required for manual recovery", result["reasons"])

    def test_invalid_with_corrupt_backup_is_blocked(self):
        queue_path, backup_path = self._queue_files()
        backup_path.write_text("{bad json", encoding="utf-8")

        result = build_sell_runtime_commit_recovery_plan(self._verifier(queue_path, backup_path))

        self.assertEqual(result["status"], "BLOCKED")

    def test_invalid_with_corrupt_queue_is_invalid(self):
        queue_path, backup_path = self._queue_files()
        queue_path.write_text("{bad json", encoding="utf-8")

        result = build_sell_runtime_commit_recovery_plan(self._verifier(queue_path, backup_path))

        self.assertEqual(result["status"], "INVALID")

    def test_invalid_with_non_object_queue_json_is_invalid(self):
        queue_path, backup_path = self._queue_files()
        queue_path.write_text("[]", encoding="utf-8")

        result = build_sell_runtime_commit_recovery_plan(self._verifier(queue_path, backup_path))

        self.assertEqual(result["status"], "INVALID")

    def test_invalid_with_non_list_queue_orders_is_invalid(self):
        queue_path, backup_path = self._queue_files()
        queue_path.write_text(json.dumps({"orders": {}}), encoding="utf-8")

        result = build_sell_runtime_commit_recovery_plan(self._verifier(queue_path, backup_path))

        self.assertEqual(result["status"], "INVALID")

    def test_missing_identity_is_invalid(self):
        queue_path, backup_path = self._queue_files()
        verifier = self._verifier(queue_path, backup_path)
        verifier["blocked_verifications"][0]["source_execution_result"]["request_hash"] = ""
        verifier["blocked_verifications"][0]["source_execution_result"]["commit_result"]["request_hash"] = ""
        verifier["blocked_verifications"][0]["commit_result"]["request_hash"] = ""

        result = build_sell_runtime_commit_recovery_plan(verifier)

        self.assertEqual(result["status"], "INVALID")

    def test_wrong_verifier_type_is_invalid(self):
        queue_path, backup_path = self._queue_files()
        verifier = self._verifier(queue_path, backup_path)
        verifier["verifier_type"] = "OTHER"

        result = build_sell_runtime_commit_recovery_plan(verifier)

        self.assertEqual(result["status"], "INVALID")

    def test_unknown_status_is_invalid(self):
        queue_path, backup_path = self._queue_files()
        verifier = self._verifier(queue_path, backup_path)
        verifier["status"] = "OTHER"

        result = build_sell_runtime_commit_recovery_plan(verifier)

        self.assertEqual(result["status"], "INVALID")

    def test_blocked_verifier_stays_blocked(self):
        queue_path, backup_path = self._queue_files()

        result = build_sell_runtime_commit_recovery_plan(
            self._verifier(queue_path, backup_path, status="BLOCKED", committed=False)
        )

        self.assertEqual(result["status"], "BLOCKED")
        self.assertFalse(result["recovery_required"])

    def test_invalid_without_actual_commit_is_blocked(self):
        queue_path, backup_path = self._queue_files()

        result = build_sell_runtime_commit_recovery_plan(
            self._verifier(queue_path, backup_path, status="INVALID", committed=False)
        )

        self.assertEqual(result["status"], "BLOCKED")
        self.assertFalse(result["recovery_required"])

    def test_missing_verification_record_is_invalid(self):
        queue_path, backup_path = self._queue_files()
        verifier = self._verifier(queue_path, backup_path)
        verifier["blocked_verifications"] = []
        verifier["verified_records"] = []

        result = build_sell_runtime_commit_recovery_plan(verifier)

        self.assertEqual(result["status"], "INVALID")

    def test_target_record_in_backup_is_blocked(self):
        backup_record = _record()
        queue_path, backup_path = self._queue_files(backup_orders=[backup_record])

        result = build_sell_runtime_commit_recovery_plan(self._verifier(queue_path, backup_path))

        self.assertEqual(result["status"], "BLOCKED")
        self.assertFalse(result["recovery_available"])
        diff = result["blocked_recovery_plans"][0]["queue_backup_diff"]
        self.assertEqual(diff["queue_matching_record_count"], 1)
        self.assertEqual(diff["backup_matching_record_count"], 1)
        self.assertFalse(diff["target_record_changed"])

    def test_identical_queue_and_backup_is_blocked(self):
        record = _record()
        queue_path, backup_path = self._queue_files(queue_orders=[record], backup_orders=[record])

        result = build_sell_runtime_commit_recovery_plan(self._verifier(queue_path, backup_path))

        self.assertEqual(result["status"], "BLOCKED")
        self.assertFalse(result["recovery_available"])
        diff = result["blocked_recovery_plans"][0]["queue_backup_diff"]
        self.assertFalse(diff["queue_backup_changed"])
        self.assertFalse(diff["target_record_changed"])

    def test_missing_target_record_in_queue_is_blocked(self):
        queue_path, backup_path = self._queue_files(queue_orders=[])

        result = build_sell_runtime_commit_recovery_plan(self._verifier(queue_path, backup_path))

        self.assertEqual(result["status"], "BLOCKED")
        self.assertFalse(result["recovery_available"])
        diff = result["blocked_recovery_plans"][0]["queue_backup_diff"]
        self.assertEqual(diff["queue_matching_record_count"], 0)
        self.assertEqual(diff["backup_matching_record_count"], 0)

    def test_single_queue_record_absent_from_changed_backup_is_recovery_ready(self):
        queue_path, backup_path = self._queue_files(queue_orders=[_record()], backup_orders=[])

        result = build_sell_runtime_commit_recovery_plan(self._verifier(queue_path, backup_path))

        self.assertEqual(result["status"], "RECOVERY_READY")
        self.assertTrue(result["recovery_available"])
        diff = result["recovery_plans"][0]["queue_backup_diff"]
        self.assertTrue(diff["queue_backup_changed"])
        self.assertTrue(diff["target_record_changed"])
        self.assertEqual(diff["queue_matching_record_count"], 1)
        self.assertEqual(diff["backup_matching_record_count"], 0)

    def test_input_mutation_does_not_occur(self):
        queue_path, backup_path = self._queue_files()
        verifier = self._verifier(queue_path, backup_path)
        original = deepcopy(verifier)

        result = build_sell_runtime_commit_recovery_plan(verifier)
        result["verifier_snapshot"]["status"] = "MUTATED"

        self.assertEqual(verifier, original)

    def test_read_only_no_write_or_restore_or_send(self):
        queue_path, backup_path = self._queue_files()
        with (
            mock.patch("pathlib.Path.write_text") as write_text,
            mock.patch("pathlib.Path.replace") as replace,
            mock.patch("kiwoom_order_adapter.send_order_stub") as send_order_stub,
        ):
            result = build_sell_runtime_commit_recovery_plan(self._verifier(queue_path, backup_path))

        self.assertEqual(result["status"], "RECOVERY_READY")
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

        build_sell_runtime_commit_recovery_plan(self._verifier(queue_path, backup_path))

        after = hashlib.sha256(runtime_queue.read_bytes()).hexdigest()
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
