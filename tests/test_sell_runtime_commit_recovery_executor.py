from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import tempfile
from pathlib import Path
import unittest
from unittest import mock

from sell_runtime_commit_recovery_executor import execute_sell_runtime_commit_recovery
from sell_runtime_commit_recovery_approval_gate import approve_sell_runtime_commit_recovery
from sell_runtime_commit_recovery_plan import build_sell_runtime_commit_recovery_plan
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


def _multi_record(index: int) -> dict:
    return _record(
        order_id=f"ORDER_{index}",
        request_hash=chr(96 + index) * 64,
        lock_id=f"LOCK_{index}",
        execution_id=f"EXEC_{index}",
    )


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
        self.assertEqual(self._read_json(queue_path)["orders"], self._read_json(backup_path)["orders"])
        self.assertEqual(1, self._read_json(queue_path)["revision"])
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
        self.assertEqual(self._read_json(queue_path)["orders"], self._read_json(backup_path)["orders"])

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
        import execution_queue_writer

        original_read = execution_queue_writer._read_queue_file
        call_count = {"count": 0}

        def fake_read(path):
            call_count["count"] += 1
            if call_count["count"] <= 3:
                return original_read(path)
            return {"version": 1, "revision": 1, "orders": [_record()]}, None

        with mock.patch("execution_queue_writer._read_queue_file", side_effect=fake_read):
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

    def test_writer_post_write_failure_preserves_canonical_side_effects(self):
        queue_path, backup_path = self._queue_files()
        writer_result = {
            "committed": True,
            "changed": True,
            "file_write": True,
            "queue_write": True,
            "queue_committed": True,
            "post_write_verified": False,
            "revision_before": 0,
            "revision_after": 1,
            "lock_acquired": True,
            "cas_checked": True,
            "restore_executed": True,
            "blocked_reasons": ["forced post-write failure"],
            "warnings": [],
        }

        with mock.patch(
            "sell_runtime_commit_recovery_executor.restore_order_queue_from_approved_backup",
            return_value=writer_result,
        ):
            result = execute_sell_runtime_commit_recovery(self._approval(queue_path, backup_path))

        self.assertEqual("INVALID", result["status"])
        for field in ("committed", "changed", "file_write", "queue_write", "queue_committed", "lock_acquired", "cas_checked"):
            self.assertTrue(result[field], field)
            self.assertTrue(result["recovery_results"][0][field], field)
        self.assertFalse(result["post_write_verified"])
        self.assertTrue(result["runtime_write"])
        self.assertTrue(result["backup_restored"])

    def test_temp_restore_validation_failure_keeps_file_write_only(self):
        queue_path, backup_path = self._queue_files()
        approval = self._approval(queue_path, backup_path)
        import execution_queue_writer

        original_read = execution_queue_writer._read_queue_file
        call_count = {"count": 0}

        def fake_read(path):
            call_count["count"] += 1
            if call_count["count"] <= 2:
                return original_read(path)
            return {}, {
                "committed": False,
                "write_stage": "read_queue",
                "next_stage": "BLOCKED",
                "changed": False,
                "blocked_reasons": ["forced temp validation failure"],
                "warnings": [],
            }

        with mock.patch("execution_queue_writer._read_queue_file", side_effect=fake_read):
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

        with mock.patch("execution_queue_writer.os.replace", side_effect=RuntimeError("replace failed")):
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

    def _multi_verifier(self, queue_path: Path, backup_path: Path, records: list[dict], *, status: str = "INVALID") -> dict:
        execution_results = []
        commit_result = {
            "committed": True,
            "committed_count": len(records),
            "order_queue_path": str(queue_path),
            "backup_path": str(backup_path),
            "committed_records": deepcopy(records),
            "order_ids": [record["order_id"] for record in records],
            "request_hashes": [record["request_hash"] for record in records],
            "lock_ids": [record["lock_id"] for record in records],
            "execution_ids": [record["execution_id"] for record in records],
        }
        for record in records:
            execution_results.append(
                {
                    "status": "READY",
                    "source_signal_id": record["source_signal_id"],
                    "order_id": record["order_id"],
                    "candidate_id": record["candidate_id"],
                    "queue_pending_id": record["queue_pending_id"],
                    "execution_id": record["execution_id"],
                    "request_hash": record["request_hash"],
                    "lock_id": record["lock_id"],
                    "commit_result": deepcopy(commit_result),
                }
            )
        return {
            "verifier_type": "SELL_RUNTIME_COMMIT_POST_COMMIT_VERIFIER",
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
            "post_commit_verified": False,
            "post_commit_file_verified": False,
            "executor_snapshot": {
                "executor_type": "SELL_RUNTIME_COMMIT_REAL_EXECUTOR",
                "status": status,
                "queue_committed": True,
                "runtime_commit_executed": True,
                "execution_results": execution_results,
            },
            "verified_records": [],
            "blocked_verifications": [
                {
                    "status": "INVALID",
                    "queue_path": str(queue_path),
                    "backup_path": str(backup_path),
                    "source_execution_result": deepcopy(execution_results[0]),
                    "commit_result": deepcopy(commit_result),
                }
            ],
            "warnings": [],
            "reasons": [],
            "summary": {"expected_record_count": len(records)},
        }

    def _approve_multi(self, queue_file: Path, backup_file: Path, records: list[dict], **context_overrides) -> dict:
        verifier = self._multi_verifier(queue_file, backup_file, records)
        plan = build_sell_runtime_commit_recovery_plan(verifier)
        context = {
            "user_approved": True,
            "approval_token": "APPROVED-MULTI",
            "queue_path": str(queue_file),
            "backup_path": str(backup_file),
            "approved_identities": [
                {field: record[field] for field in ("order_id", "candidate_id", "queue_pending_id", "execution_id", "request_hash", "lock_id")}
                for record in records
            ],
        }
        context.update(context_overrides)
        return approve_sell_runtime_commit_recovery(plan, context)

    def test_multi_candidate_recovery_chain_success(self):
        records = [_multi_record(1), _multi_record(2)]
        queue_path, backup_path = self._queue_files(queue_orders=records, backup_orders=[])

        approval = self._approve_multi(queue_path, backup_path, records)
        execution = execute_sell_runtime_commit_recovery(approval)
        post_check = check_sell_runtime_commit_recovery_post_commit(execution)

        self.assertEqual(approval["status"], "READY")
        self.assertEqual(execution["status"], "READY")
        self.assertEqual(post_check["status"], "READY")
        self.assertEqual(self._read_json(queue_path)["orders"], self._read_json(backup_path)["orders"])
        self.assertEqual(self._read_json(queue_path)["orders"], [])
        self.assertEqual(post_check["checked_records"][0]["target_count"], 2)

    def test_multi_candidate_three_preserves_target_order(self):
        records = [_multi_record(1), _multi_record(2), _multi_record(3)]
        queue_path, backup_path = self._queue_files(queue_orders=records, backup_orders=[])

        plan = build_sell_runtime_commit_recovery_plan(self._multi_verifier(queue_path, backup_path, records))

        self.assertEqual(plan["status"], "RECOVERY_READY")
        self.assertEqual(["ORDER_1", "ORDER_2", "ORDER_3"], [item["order_id"] for item in plan["recovery_plans"][0]["target_identities"]])

    def test_multi_candidate_plan_blocks_when_one_queue_target_missing(self):
        records = [_multi_record(1), _multi_record(2)]
        queue_path, backup_path = self._queue_files(queue_orders=[records[0]], backup_orders=[])

        plan = build_sell_runtime_commit_recovery_plan(self._multi_verifier(queue_path, backup_path, records))

        self.assertEqual(plan["status"], "BLOCKED")
        self.assertFalse(plan["recovery_available"])

    def test_multi_candidate_plan_blocks_when_backup_contains_one_target(self):
        records = [_multi_record(1), _multi_record(2)]
        queue_path, backup_path = self._queue_files(queue_orders=records, backup_orders=[records[1]])

        plan = build_sell_runtime_commit_recovery_plan(self._multi_verifier(queue_path, backup_path, records))

        self.assertEqual(plan["status"], "BLOCKED")
        self.assertFalse(plan["recovery_available"])

    def test_multi_candidate_approval_missing_identity_is_invalid(self):
        records = [_multi_record(1), _multi_record(2)]
        queue_path, backup_path = self._queue_files(queue_orders=records, backup_orders=[])

        approval = self._approve_multi(queue_path, backup_path, records, approved_identities=[{k: records[0][k] for k in ("order_id", "candidate_id", "queue_pending_id", "execution_id", "request_hash", "lock_id")}])

        self.assertEqual(approval["status"], "INVALID")

    def test_multi_candidate_approval_reordered_identity_is_invalid(self):
        records = [_multi_record(1), _multi_record(2)]
        queue_path, backup_path = self._queue_files(queue_orders=records, backup_orders=[])
        identities = [
            {field: record[field] for field in ("order_id", "candidate_id", "queue_pending_id", "execution_id", "request_hash", "lock_id")}
            for record in reversed(records)
        ]

        approval = self._approve_multi(queue_path, backup_path, records, approved_identities=identities)

        self.assertEqual(approval["status"], "INVALID")

    def test_multi_candidate_approval_path_or_token_mismatch(self):
        records = [_multi_record(1), _multi_record(2)]
        queue_path, backup_path = self._queue_files(queue_orders=records, backup_orders=[])

        path_result = self._approve_multi(queue_path, backup_path, records, queue_path=str(queue_path) + ".other")
        token_result = self._approve_multi(queue_path, backup_path, records, approval_token="")

        self.assertEqual(path_result["status"], "INVALID")
        self.assertEqual(token_result["status"], "BLOCKED")

    def test_multi_candidate_executor_blocks_after_queue_or_backup_mutation(self):
        records = [_multi_record(1), _multi_record(2)]
        queue_path, backup_path = self._queue_files(queue_orders=records, backup_orders=[])
        approval = self._approve_multi(queue_path, backup_path, records)
        queue_path.write_text(json.dumps({"version": 1, "orders": [records[0]]}), encoding="utf-8")

        queue_result = execute_sell_runtime_commit_recovery(approval)

        queue_path.write_text(json.dumps({"version": 1, "orders": records}, indent=2), encoding="utf-8")
        backup_path.write_text(json.dumps({"version": 1, "orders": [records[1]]}), encoding="utf-8")
        backup_result = execute_sell_runtime_commit_recovery(approval)

        self.assertEqual(queue_result["status"], "BLOCKED")
        self.assertEqual(backup_result["status"], "BLOCKED")

    def test_multi_candidate_post_check_invalid_when_safety_backup_missing_one_target(self):
        records = [_multi_record(1), _multi_record(2)]
        queue_path, backup_path = self._queue_files(queue_orders=records, backup_orders=[])
        execution = execute_sell_runtime_commit_recovery(self._approve_multi(queue_path, backup_path, records))
        safety_path = Path(execution["recovery_results"][0]["safety_backup_path"])
        safety_path.write_text(json.dumps({"version": 1, "orders": [records[0]]}), encoding="utf-8")

        post_check = check_sell_runtime_commit_recovery_post_commit(execution)

        self.assertEqual(post_check["status"], "INVALID")


if __name__ == "__main__":
    unittest.main()
