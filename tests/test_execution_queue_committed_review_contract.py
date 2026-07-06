from __future__ import annotations

import hashlib
from pathlib import Path
import unittest
from unittest import mock

from execution_queue_committed_review import (
    STATUS_READY,
    review_execution_queue_committed,
)


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ExecutionQueueCommittedReviewContractTest(unittest.TestCase):
    def _record(self, **overrides) -> dict:
        record = {
            "id": "ORDER_QUEUED_ORDER_CONTRACT_1",
            "status": "ORDER_QUEUED",
            "source_signal_id": "SIGNAL_CONTRACT_1",
            "order_id": "ORDER_CONTRACT_1",
            "request_hash": "HASH_CONTRACT_1",
            "lock_id": "LOCK_CONTRACT_1",
            "execution_id": "EXEC_CONTRACT_1",
            "send_order_called": False,
            "execution_enabled": False,
        }
        record.update(overrides)
        return record

    def _queue_commit_result(self, **overrides) -> dict:
        result = {
            "status": "COMMITTED",
            "manual_commit": True,
            "commit_stage": "committed",
            "next_stage": "QUEUE_COMMITTED_REVIEW_REQUIRED",
            "blocked_reasons": [],
            "commit_result": {
                "committed": True,
                "write_stage": "order_queued_record_committed",
                "next_stage": "QUEUE_COMMITTED_REVIEW_REQUIRED",
                "changed": True,
                "status": "ORDER_QUEUED",
                "send_order_called": False,
                "execution_enabled": False,
                "order_id": "ORDER_CONTRACT_1",
                "order_queued_id": "ORDER_QUEUED_ORDER_CONTRACT_1",
                "source_signal_id": "SIGNAL_CONTRACT_1",
                "request_hash": "HASH_CONTRACT_1",
                "lock_id": "LOCK_CONTRACT_1",
                "execution_id": "EXEC_CONTRACT_1",
                "order_queued_record": self._record(),
                "blocked_reasons": [],
                "warnings": [],
            },
        }
        result.update(overrides)
        return result

    def test_committed_result_ready_for_final_send_gate(self) -> None:
        result = review_execution_queue_committed(self._queue_commit_result())

        self.assertEqual(STATUS_READY, result["status"])
        self.assertEqual("FINAL_SEND_GATE_REQUIRED", result["next_stage"])
        self.assertTrue(result["preview_only"])
        self.assertFalse(result["queue_write"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["send_order_called"])

    def test_identity_and_order_queued_record_are_preserved(self) -> None:
        queue_result = self._queue_commit_result()
        result = review_execution_queue_committed(queue_result)

        self.assertEqual(queue_result["commit_result"]["order_queued_record"], result["order_queued_record"])
        self.assertEqual(
            {
                "order_id": "ORDER_CONTRACT_1",
                "source_signal_id": "SIGNAL_CONTRACT_1",
                "execution_id": "EXEC_CONTRACT_1",
                "request_hash": "HASH_CONTRACT_1",
                "lock_id": "LOCK_CONTRACT_1",
            },
            result["identity"],
        )

    def test_blocked_invalid_and_malformed_inputs(self) -> None:
        blocked = review_execution_queue_committed(
            self._queue_commit_result(status="BLOCKED", manual_commit=False, blocked_reasons=["blocked"])
        )
        invalid = review_execution_queue_committed({"status": "INVALID", "blocked_reasons": ["invalid"]})
        malformed = review_execution_queue_committed(None)

        self.assertEqual("BLOCKED", blocked["status"])
        self.assertEqual("INVALID", invalid["status"])
        self.assertEqual("INVALID", malformed["status"])
        self.assertIn("MALFORMED_QUEUE_COMMIT_RESULT", malformed["issues"])

    def test_next_stage_mismatch_and_missing_commit_result_block(self) -> None:
        next_stage = review_execution_queue_committed(self._queue_commit_result(next_stage="OTHER"))
        missing_commit = review_execution_queue_committed(
            {
                "status": "COMMITTED",
                "manual_commit": True,
                "next_stage": "QUEUE_COMMITTED_REVIEW_REQUIRED",
            }
        )

        self.assertEqual("BLOCKED", next_stage["status"])
        self.assertIn("QUEUE_COMMIT_NEXT_STAGE_NOT_REVIEW_REQUIRED", next_stage["issues"])
        self.assertEqual("BLOCKED", missing_commit["status"])
        self.assertIn("QUEUE_COMMIT_NOT_COMMITTED", missing_commit["issues"])

    def test_internal_status_and_flags_block(self) -> None:
        status_bad = self._queue_commit_result()
        status_bad["commit_result"]["order_queued_record"] = self._record(status="OTHER")
        send_called = self._queue_commit_result()
        send_called["commit_result"]["order_queued_record"] = self._record(send_order_called=True)
        execution_enabled = self._queue_commit_result()
        execution_enabled["commit_result"]["order_queued_record"] = self._record(execution_enabled=True)

        status_result = review_execution_queue_committed(status_bad)
        send_result = review_execution_queue_committed(send_called)
        execution_result = review_execution_queue_committed(execution_enabled)

        self.assertEqual("BLOCKED", status_result["status"])
        self.assertIn("ORDER_QUEUED_RECORD_STATUS_INVALID", status_result["issues"])
        self.assertEqual("BLOCKED", send_result["status"])
        self.assertIn("ORDER_QUEUED_RECORD_SEND_ORDER_CALLED_NOT_FALSE", send_result["issues"])
        self.assertEqual("BLOCKED", execution_result["status"])
        self.assertIn("ORDER_QUEUED_RECORD_EXECUTION_ENABLED_NOT_FALSE", execution_result["issues"])

    def test_missing_identity_fields_block(self) -> None:
        for field in ("order_id", "source_signal_id", "execution_id", "request_hash", "lock_id"):
            with self.subTest(field=field):
                queue_result = self._queue_commit_result()
                record = self._record()
                record.pop(field)
                queue_result["commit_result"]["order_queued_record"] = record

                result = review_execution_queue_committed(queue_result)

                self.assertEqual("BLOCKED", result["status"])
                self.assertIn(f"MISSING_{field.upper()}", result["issues"])

    def test_no_queue_commit_final_send_gate_or_send_order_calls(self) -> None:
        with (
            mock.patch("execution_queue_commit_service.commit_execution_queue_manually") as queue_commit,
            mock.patch("final_send_gate_service.evaluate_final_send_gate") as final_gate,
            mock.patch("send_order_entrypoint.execute_send_order") as send_order,
        ):
            result = review_execution_queue_committed(self._queue_commit_result())

        self.assertEqual(STATUS_READY, result["status"])
        queue_commit.assert_not_called()
        final_gate.assert_not_called()
        send_order.assert_not_called()

    def test_order_queue_runtime_and_rules_hash_unchanged(self) -> None:
        runtime_paths = [
            ROOT / "runtime" / "order_queue.json",
            ROOT / "runtime" / "order_executions.json",
            ROOT / "runtime" / "order_locks.json",
        ]
        rules_paths = list((ROOT / "routines").glob("**/rules.json"))
        before_runtime = {str(path): _sha256(path) for path in runtime_paths}
        before_rules = {str(path): _sha256(path) for path in rules_paths}

        review_execution_queue_committed(self._queue_commit_result())
        review_execution_queue_committed({"status": "INVALID"})

        self.assertEqual(before_runtime, {str(path): _sha256(path) for path in runtime_paths})
        self.assertEqual(before_rules, {str(path): _sha256(path) for path in rules_paths})


if __name__ == "__main__":
    unittest.main()
