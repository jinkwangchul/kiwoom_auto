from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import tempfile
from pathlib import Path
import unittest
from unittest import mock

from sell_runtime_commit_post_commit_verifier import verify_sell_runtime_commit_post_commit


def _record(
    *,
    source_signal_id: str = "SIG_1",
    order_id: str = "ORDER_1",
    candidate_id: str = "CANDIDATE_ORDER_1",
    queue_pending_id: str = "QUEUE_PENDING_ORDER_1",
    request_hash: str = "r" * 64,
    lock_id: str = "LOCK_1",
    execution_id: str = "EXEC_1",
) -> dict:
    return {
        "id": f"ORDER_QUEUED_{order_id}",
        "status": "ORDER_QUEUED",
        "source": "execution_queue_pending",
        "source_signal_id": source_signal_id,
        "order_id": order_id,
        "candidate_id": candidate_id,
        "queue_pending_id": queue_pending_id,
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


class SellRuntimeCommitPostCommitVerifierTests(unittest.TestCase):
    def _queue_files(self, orders: list[dict] | None = None, *, backup_orders: list[dict] | None = None) -> tuple[Path, Path]:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        queue_path = Path(temp.name) / "order_queue.json"
        backup_path = Path(str(queue_path) + ".bak")
        queue_path.write_text(
            json.dumps({"version": 1, "updated_at": "after", "orders": [_record()] if orders is None else orders}, indent=2),
            encoding="utf-8",
        )
        backup_path.write_text(
            json.dumps({"version": 1, "updated_at": "before", "orders": [] if backup_orders is None else backup_orders}, indent=2),
            encoding="utf-8",
        )
        return queue_path, backup_path

    def _executor_result(self, queue_path: Path, backup_path: Path | None = None, *, status: str = "READY", committed: bool = True) -> dict:
        commit_result = {
            "committed": committed,
            "write_stage": "order_queued_record_committed" if committed else "duplicate",
            "next_stage": "QUEUE_COMMITTED_REVIEW_REQUIRED" if committed else "BLOCKED",
            "changed": committed,
            "order_queue_path": str(queue_path),
            "backup_path": str(backup_path) if backup_path else None,
            "order_id": "ORDER_1",
            "order_queued_id": "ORDER_QUEUED_ORDER_1",
            "request_hash": "r" * 64,
            "lock_id": "LOCK_1",
            "status": "ORDER_QUEUED" if committed else None,
            "send_order_called": False,
            "execution_enabled": False,
            "blocked_reasons": [] if committed else ["duplicate order_id"],
            "warnings": [],
        }
        execution_result = {
            "status": status,
            "commit_boundary_function": "execution_queue_writer.commit_execution_queue_write",
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
        return {
            "executor_type": "SELL_RUNTIME_COMMIT_REAL_EXECUTOR",
            "ownership": "MASTER_ENGINE",
            "domain": "Execution / Runtime Commit Real Executor",
            "routine_dependency": None,
            "preview_only": False,
            "execution_connected": False,
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
            "status": status,
            "commit_allowed": status == "READY",
            "approval_snapshot": {},
            "execution_results": [execution_result],
            "blocked_execution_results": [],
            "source_summary": {},
            "warnings": [],
            "reasons": [],
            "summary": {
                "execution_ready_count": 1 if status == "READY" else 0,
                "execution_blocked_count": 1 if status == "BLOCKED" else 0,
                "execution_invalid_count": 1 if status == "INVALID" else 0,
                "execution_result_count": 1,
                "blocked_execution_result_count": 0,
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
            },
        }

    def _multi_executor_result(self, queue_path: Path, backup_path: Path, records: list[dict], *, status: str = "READY") -> dict:
        commit_result = {
            "committed": True,
            "committed_count": len(records),
            "write_stage": "order_queued_records_committed",
            "next_stage": "QUEUE_COMMITTED_REVIEW_REQUIRED",
            "changed": True,
            "order_queue_path": str(queue_path),
            "backup_path": str(backup_path),
            "committed_records": deepcopy(records),
            "order_ids": [record["order_id"] for record in records],
            "order_queued_ids": [record["id"] for record in records],
            "request_hashes": [record["request_hash"] for record in records],
            "lock_ids": [record["lock_id"] for record in records],
            "execution_ids": [record["execution_id"] for record in records],
            "send_order_called": False,
            "execution_enabled": False,
            "blocked_reasons": [],
            "warnings": [],
        }
        execution_results = []
        for record in records:
            execution_results.append(
                {
                    "status": "READY",
                    "commit_boundary_function": "execution_queue_writer.commit_execution_queue_write_batch",
                    "source_signal_id": record["source_signal_id"],
                    "order_id": record["order_id"],
                    "candidate_id": record["candidate_id"],
                    "queue_pending_id": record["queue_pending_id"],
                    "execution_id": record["execution_id"],
                    "request_hash": record["request_hash"],
                    "lock_id": record["lock_id"],
                    "commit_result": deepcopy(commit_result),
                    "runtime_write": True,
                    "queue_write": True,
                    "file_write": True,
                    "queue_committed": True,
                    "send_order": False,
                    "broker_api_called": False,
                    "actual_order_sent": False,
                    "order_request_created": False,
                    "real_ready_state_changed": False,
                    "runtime_commit_executed": True,
                    "reasons": [],
                    "warnings": [],
                }
            )
        return {
            "executor_type": "SELL_RUNTIME_COMMIT_REAL_EXECUTOR",
            "ownership": "MASTER_ENGINE",
            "domain": "Execution / Runtime Commit Real Executor",
            "routine_dependency": None,
            "preview_only": False,
            "execution_connected": False,
            "runtime_write": True,
            "queue_write": True,
            "file_write": True,
            "queue_committed": True,
            "send_order": False,
            "broker_api_called": False,
            "actual_order_sent": False,
            "order_request_created": False,
            "real_ready_state_changed": False,
            "runtime_commit_executed": True,
            "status": status,
            "commit_allowed": status == "READY",
            "approval_snapshot": {},
            "execution_results": execution_results,
            "blocked_execution_results": [],
            "source_summary": {},
            "warnings": [],
            "reasons": [],
            "summary": {
                "execution_ready_count": len(records) if status == "READY" else 0,
                "execution_blocked_count": 0,
                "execution_invalid_count": len(records) if status == "INVALID" else 0,
                "execution_result_count": len(records),
                "blocked_execution_result_count": 0,
                "runtime_write": True,
                "queue_write": True,
                "file_write": True,
                "queue_committed": True,
                "send_order": False,
                "broker_api_called": False,
                "actual_order_sent": False,
                "order_request_created": False,
                "real_ready_state_changed": False,
                "runtime_commit_executed": True,
            },
        }

    def _multi_records(self, count: int = 2) -> list[dict]:
        records = []
        for index in range(1, count + 1):
            records.append(
                _record(
                    order_id=f"ORDER_{index}",
                    candidate_id=f"CANDIDATE_ORDER_{index}",
                    queue_pending_id=f"QUEUE_PENDING_ORDER_{index}",
                    request_hash=chr(96 + index) * 64,
                    lock_id=f"LOCK_{index}",
                    execution_id=f"EXEC_{index}",
                )
            )
        return records

    def test_ready_after_normal_commit(self):
        queue_path, backup_path = self._queue_files()

        result = verify_sell_runtime_commit_post_commit(self._executor_result(queue_path, backup_path))

        self.assertEqual(result["verifier_type"], "SELL_RUNTIME_COMMIT_POST_COMMIT_VERIFIER")
        self.assertEqual(result["status"], "READY")
        self.assertTrue(result["post_commit_verified"])
        self.assertTrue(result["post_commit_file_verified"])
        self.assertEqual(len(result["verified_records"]), 1)

    def test_missing_record_invalid(self):
        queue_path, backup_path = self._queue_files(orders=[])

        result = verify_sell_runtime_commit_post_commit(self._executor_result(queue_path, backup_path))

        self.assertEqual(result["status"], "INVALID")
        self.assertFalse(result["post_commit_file_verified"])

    def test_duplicate_record_invalid(self):
        queue_path, backup_path = self._queue_files(orders=[_record(), _record()])

        result = verify_sell_runtime_commit_post_commit(self._executor_result(queue_path, backup_path))

        self.assertEqual(result["status"], "INVALID")

    def test_identity_mismatch_invalid(self):
        queue_path, backup_path = self._queue_files(orders=[_record(request_hash="mismatch")])

        result = verify_sell_runtime_commit_post_commit(self._executor_result(queue_path, backup_path))

        self.assertEqual(result["status"], "INVALID")

    def test_execution_id_mismatch_invalid(self):
        queue_path, backup_path = self._queue_files(orders=[_record(execution_id="OTHER")])

        result = verify_sell_runtime_commit_post_commit(self._executor_result(queue_path, backup_path))

        self.assertEqual(result["status"], "INVALID")

    def test_record_status_mismatch_invalid(self):
        record = _record()
        record["status"] = "OTHER"
        queue_path, backup_path = self._queue_files(orders=[record])

        result = verify_sell_runtime_commit_post_commit(self._executor_result(queue_path, backup_path))

        self.assertEqual(result["status"], "INVALID")

    def test_send_order_called_true_invalid(self):
        record = _record()
        record["send_order_called"] = True
        queue_path, backup_path = self._queue_files(orders=[record])

        result = verify_sell_runtime_commit_post_commit(self._executor_result(queue_path, backup_path))

        self.assertEqual(result["status"], "INVALID")

    def test_execution_enabled_true_invalid(self):
        record = _record()
        record["execution_enabled"] = True
        queue_path, backup_path = self._queue_files(orders=[record])

        result = verify_sell_runtime_commit_post_commit(self._executor_result(queue_path, backup_path))

        self.assertEqual(result["status"], "INVALID")

    def test_corrupt_queue_json_invalid(self):
        queue_path, backup_path = self._queue_files()
        queue_path.write_text("{bad json", encoding="utf-8")

        result = verify_sell_runtime_commit_post_commit(self._executor_result(queue_path, backup_path))

        self.assertEqual(result["status"], "INVALID")

    def test_queue_json_root_non_object_invalid(self):
        queue_path, backup_path = self._queue_files()
        queue_path.write_text("[]", encoding="utf-8")

        result = verify_sell_runtime_commit_post_commit(self._executor_result(queue_path, backup_path))

        self.assertEqual(result["status"], "INVALID")

    def test_queue_orders_non_list_invalid(self):
        queue_path, backup_path = self._queue_files()
        queue_path.write_text(json.dumps({"version": 1, "orders": {}}), encoding="utf-8")

        result = verify_sell_runtime_commit_post_commit(self._executor_result(queue_path, backup_path))

        self.assertEqual(result["status"], "INVALID")

    def test_backup_missing_invalid(self):
        queue_path, backup_path = self._queue_files()
        backup_path.unlink()

        result = verify_sell_runtime_commit_post_commit(self._executor_result(queue_path, backup_path))

        self.assertEqual(result["status"], "INVALID")

    def test_backup_corrupt_invalid(self):
        queue_path, backup_path = self._queue_files()
        backup_path.write_text("{bad json", encoding="utf-8")

        result = verify_sell_runtime_commit_post_commit(self._executor_result(queue_path, backup_path))

        self.assertEqual(result["status"], "INVALID")

    def test_backup_orders_non_list_invalid(self):
        queue_path, backup_path = self._queue_files()
        backup_path.write_text(json.dumps({"version": 1, "orders": {}}), encoding="utf-8")

        result = verify_sell_runtime_commit_post_commit(self._executor_result(queue_path, backup_path))

        self.assertEqual(result["status"], "INVALID")

    def test_no_backup_path_allowed(self):
        queue_path, _ = self._queue_files()

        result = verify_sell_runtime_commit_post_commit(self._executor_result(queue_path, None))

        self.assertEqual(result["status"], "READY")

    def test_upstream_blocked_is_blocked(self):
        queue_path, backup_path = self._queue_files()

        result = verify_sell_runtime_commit_post_commit(
            self._executor_result(queue_path, backup_path, status="BLOCKED", committed=False)
        )

        self.assertEqual(result["status"], "BLOCKED")

    def test_upstream_invalid_is_invalid(self):
        queue_path, backup_path = self._queue_files()

        result = verify_sell_runtime_commit_post_commit(
            self._executor_result(queue_path, backup_path, status="INVALID", committed=True)
        )

        self.assertEqual(result["status"], "INVALID")
        self.assertFalse(result["post_commit_verified"])
        self.assertTrue(result["post_commit_file_verified"])

    def test_upstream_invalid_with_file_mismatch_keeps_file_unverified(self):
        queue_path, backup_path = self._queue_files(orders=[_record(request_hash="mismatch")])

        result = verify_sell_runtime_commit_post_commit(
            self._executor_result(queue_path, backup_path, status="INVALID", committed=True)
        )

        self.assertEqual(result["status"], "INVALID")
        self.assertFalse(result["post_commit_verified"])
        self.assertFalse(result["post_commit_file_verified"])

    def test_queue_committed_false_blocked(self):
        queue_path, backup_path = self._queue_files()
        upstream = self._executor_result(queue_path, backup_path)
        upstream["queue_committed"] = False

        result = verify_sell_runtime_commit_post_commit(upstream)

        self.assertEqual(result["status"], "BLOCKED")

    def test_runtime_commit_executed_false_blocked(self):
        queue_path, backup_path = self._queue_files()
        upstream = self._executor_result(queue_path, backup_path)
        upstream["runtime_commit_executed"] = False

        result = verify_sell_runtime_commit_post_commit(upstream)

        self.assertEqual(result["status"], "BLOCKED")

    def test_commit_result_committed_false_blocked(self):
        queue_path, backup_path = self._queue_files()
        upstream = self._executor_result(queue_path, backup_path)
        upstream["execution_results"][0]["commit_result"]["committed"] = False

        result = verify_sell_runtime_commit_post_commit(upstream)

        self.assertEqual(result["status"], "BLOCKED")

    def test_multi_two_records_ready(self):
        records = self._multi_records(2)
        queue_path, backup_path = self._queue_files(orders=records)

        result = verify_sell_runtime_commit_post_commit(self._multi_executor_result(queue_path, backup_path, records))

        self.assertEqual(result["status"], "READY")
        self.assertTrue(result["post_commit_verified"])
        self.assertTrue(result["post_commit_file_verified"])
        self.assertEqual(2, result["summary"]["verified_record_count"])
        self.assertEqual(["ORDER_1", "ORDER_2"], [item["record"]["order_id"] for item in result["verified_records"]])

    def test_multi_three_records_preserves_order(self):
        records = self._multi_records(3)
        queue_path, backup_path = self._queue_files(orders=records)

        result = verify_sell_runtime_commit_post_commit(self._multi_executor_result(queue_path, backup_path, records))

        self.assertEqual(result["status"], "READY")
        self.assertEqual(["ORDER_1", "ORDER_2", "ORDER_3"], [item["record"]["order_id"] for item in result["verified_records"]])

    def test_multi_missing_one_queue_record_invalid(self):
        records = self._multi_records(2)
        queue_path, backup_path = self._queue_files(orders=[records[0]])

        result = verify_sell_runtime_commit_post_commit(self._multi_executor_result(queue_path, backup_path, records))

        self.assertEqual(result["status"], "INVALID")
        self.assertFalse(result["post_commit_file_verified"])

    def test_multi_duplicate_one_queue_record_invalid(self):
        records = self._multi_records(2)
        queue_path, backup_path = self._queue_files(orders=[records[0], records[1], deepcopy(records[1])])

        result = verify_sell_runtime_commit_post_commit(self._multi_executor_result(queue_path, backup_path, records))

        self.assertEqual(result["status"], "INVALID")

    def test_multi_candidate_id_mismatch_invalid(self):
        records = self._multi_records(2)
        queue_records = deepcopy(records)
        queue_records[1]["candidate_id"] = "OTHER"
        queue_path, backup_path = self._queue_files(orders=queue_records)

        result = verify_sell_runtime_commit_post_commit(self._multi_executor_result(queue_path, backup_path, records))

        self.assertEqual(result["status"], "INVALID")

    def test_multi_queue_pending_id_mismatch_invalid(self):
        records = self._multi_records(2)
        queue_records = deepcopy(records)
        queue_records[1]["queue_pending_id"] = "OTHER"
        queue_path, backup_path = self._queue_files(orders=queue_records)

        result = verify_sell_runtime_commit_post_commit(self._multi_executor_result(queue_path, backup_path, records))

        self.assertEqual(result["status"], "INVALID")

    def test_multi_execution_id_mismatch_invalid(self):
        records = self._multi_records(2)
        upstream = self._multi_executor_result(*self._queue_files(orders=records), records)
        upstream["execution_results"][1]["commit_result"]["execution_ids"][1] = "OTHER"

        result = verify_sell_runtime_commit_post_commit(upstream)

        self.assertEqual(result["status"], "INVALID")

    def test_multi_committed_count_mismatch_invalid(self):
        records = self._multi_records(2)
        upstream = self._multi_executor_result(*self._queue_files(orders=records), records)
        upstream["execution_results"][0]["commit_result"]["committed_count"] = 1
        upstream["execution_results"][1]["commit_result"]["committed_count"] = 1

        result = verify_sell_runtime_commit_post_commit(upstream)

        self.assertEqual(result["status"], "INVALID")

    def test_multi_committed_records_count_mismatch_invalid(self):
        records = self._multi_records(2)
        upstream = self._multi_executor_result(*self._queue_files(orders=records), records)
        upstream["execution_results"][0]["commit_result"]["committed_records"] = [records[0]]
        upstream["execution_results"][1]["commit_result"]["committed_records"] = [records[0]]

        result = verify_sell_runtime_commit_post_commit(upstream)

        self.assertEqual(result["status"], "INVALID")

    def test_multi_result_queue_path_mismatch_invalid(self):
        records = self._multi_records(2)
        queue_path, backup_path = self._queue_files(orders=records)
        upstream = self._multi_executor_result(queue_path, backup_path, records)
        upstream["execution_results"][1]["commit_result"]["order_queue_path"] = str(queue_path) + ".other"

        result = verify_sell_runtime_commit_post_commit(upstream)

        self.assertEqual(result["status"], "INVALID")

    def test_multi_batch_identity_order_mismatch_invalid(self):
        records = self._multi_records(2)
        queue_path, backup_path = self._queue_files(orders=records)
        upstream = self._multi_executor_result(queue_path, backup_path, records)
        reversed_order_ids = ["ORDER_2", "ORDER_1"]
        for execution_result in upstream["execution_results"]:
            execution_result["commit_result"]["order_ids"] = reversed_order_ids

        result = verify_sell_runtime_commit_post_commit(upstream)

        self.assertEqual(result["status"], "INVALID")

    def test_multi_backup_contains_new_target_invalid(self):
        records = self._multi_records(2)
        queue_path, backup_path = self._queue_files(orders=records, backup_orders=[records[0]])

        result = verify_sell_runtime_commit_post_commit(self._multi_executor_result(queue_path, backup_path, records))

        self.assertEqual(result["status"], "INVALID")

    def test_upstream_invalid_multi_file_ok_keeps_file_verified(self):
        records = self._multi_records(2)
        queue_path, backup_path = self._queue_files(orders=records)

        result = verify_sell_runtime_commit_post_commit(
            self._multi_executor_result(queue_path, backup_path, records, status="INVALID")
        )

        self.assertEqual(result["status"], "INVALID")
        self.assertFalse(result["post_commit_verified"])
        self.assertTrue(result["post_commit_file_verified"])

    def test_upstream_invalid_multi_file_mismatch_unverified(self):
        records = self._multi_records(2)
        queue_path, backup_path = self._queue_files(orders=[records[0]])

        result = verify_sell_runtime_commit_post_commit(
            self._multi_executor_result(queue_path, backup_path, records, status="INVALID")
        )

        self.assertEqual(result["status"], "INVALID")
        self.assertFalse(result["post_commit_file_verified"])

    def test_queue_file_read_once_for_multi_result(self):
        records = self._multi_records(2)
        queue_path, backup_path = self._queue_files(orders=records)
        original_read_text = Path.read_text
        read_counts = {"queue": 0}

        def counting_read_text(path_self, *args, **kwargs):
            if path_self == queue_path:
                read_counts["queue"] += 1
            return original_read_text(path_self, *args, **kwargs)

        with mock.patch("pathlib.Path.read_text", new=counting_read_text):
            result = verify_sell_runtime_commit_post_commit(
                self._multi_executor_result(queue_path, backup_path, records)
            )

        self.assertEqual(result["status"], "READY")
        self.assertEqual(1, read_counts["queue"])

    def test_executor_type_required(self):
        queue_path, backup_path = self._queue_files()
        upstream = self._executor_result(queue_path, backup_path)
        upstream["executor_type"] = "OTHER"

        result = verify_sell_runtime_commit_post_commit(upstream)

        self.assertEqual(result["status"], "INVALID")

    def test_input_mutation_does_not_occur(self):
        queue_path, backup_path = self._queue_files()
        upstream = self._executor_result(queue_path, backup_path)
        original = deepcopy(upstream)

        result = verify_sell_runtime_commit_post_commit(upstream)
        result["executor_snapshot"]["status"] = "MUTATED"

        self.assertEqual(upstream, original)

    def test_read_only_no_write_or_send(self):
        queue_path, backup_path = self._queue_files()
        with (
            mock.patch("pathlib.Path.write_text") as write_text,
            mock.patch("kiwoom_order_adapter.send_order_stub") as send_order_stub,
        ):
            result = verify_sell_runtime_commit_post_commit(self._executor_result(queue_path, backup_path))

        self.assertEqual(result["status"], "READY")
        write_text.assert_not_called()
        send_order_stub.assert_not_called()
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_write"])
        self.assertFalse(result["file_write"])
        self.assertFalse(result["rollback"])
        self.assertFalse(result["send_order"])
        self.assertFalse(result["broker_api_called"])
        self.assertFalse(result["order_request_created"])
        self.assertFalse(result["real_ready_state_changed"])

    def test_project_runtime_order_queue_not_accessed(self):
        runtime_queue = Path(__file__).resolve().parents[1] / "runtime" / "order_queue.json"
        before = hashlib.sha256(runtime_queue.read_bytes()).hexdigest()
        queue_path, backup_path = self._queue_files()

        verify_sell_runtime_commit_post_commit(self._executor_result(queue_path, backup_path))

        after = hashlib.sha256(runtime_queue.read_bytes()).hexdigest()
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
