from __future__ import annotations

import hashlib
from pathlib import Path
import unittest
from unittest import mock

from execution_queue_commit_readiness_policy import (
    POLICY_TYPE,
    STATUS_READY,
    evaluate_execution_queue_commit_readiness,
)


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ExecutionQueueCommitReadinessPolicyTest(unittest.TestCase):
    def _runtime_commit_result(self, **overrides) -> dict:
        result = {
            "service_type": "EXECUTION_RUNTIME_COMMIT_SERVICE",
            "status": "COMMITTED",
            "runtime_write": True,
            "committed": True,
            "read_back_verified": True,
            "execution_id": "EXEC_QUEUE_POLICY_1",
            "order_id": "ORDER_QUEUE_POLICY_1",
            "request_hash": "HASH_QUEUE_POLICY_1",
            "lock_id": "LOCK_QUEUE_POLICY_1",
            "issues": [],
            "warnings": [],
        }
        result.update(overrides)
        return result

    def _queue_preview(self, **record_overrides) -> dict:
        record = {
            "id": "ORDER_QUEUED_ORDER_QUEUE_POLICY_1",
            "status": "ORDER_QUEUED",
            "source": "execution_queue_pending",
            "source_signal_id": "SIGNAL_QUEUE_POLICY_1",
            "order_id": "ORDER_QUEUE_POLICY_1",
            "candidate_id": "CANDIDATE_QUEUE_POLICY_1",
            "queue_pending_id": "QUEUE_PENDING_QUEUE_POLICY_1",
            "request_hash": "HASH_QUEUE_POLICY_1",
            "lock_id": "LOCK_QUEUE_POLICY_1",
            "execution_id": "EXEC_QUEUE_POLICY_1",
            "execution_request": {
                "execution_id": "EXEC_QUEUE_POLICY_1",
                "order_id": "ORDER_QUEUE_POLICY_1",
                "request_hash": "HASH_QUEUE_POLICY_1",
                "lock_id": "LOCK_QUEUE_POLICY_1",
            },
            "queue_contract_version": "preview-1",
            "send_order_called": False,
            "execution_enabled": False,
            "blocked_reasons": [],
        }
        record.update(record_overrides)
        return {
            "write_preview": True,
            "write_stage": "order_queued_record_preview_created",
            "next_stage": "QUEUE_WRITE_REQUIRED",
            "preview_only": True,
            "no_write": True,
            "blocked_reasons": [],
            "warnings": [],
            "order_queued_record_preview": record,
        }

    def _confirmations(self, *, queue: bool = True, runtime_queue: bool = True) -> dict:
        return {
            "manual_queue_write_confirmed": queue,
            "manual_runtime_queue_write_confirmed": runtime_queue,
        }

    def _evaluate(self, **overrides) -> dict:
        kwargs = {
            "runtime_commit_result": self._runtime_commit_result(),
            "queue_write_preview_result": self._queue_preview(),
            "queue_path": ROOT / "runtime" / "order_queue.json",
            "confirmations": self._confirmations(),
        }
        kwargs.update(overrides)
        return evaluate_execution_queue_commit_readiness(**kwargs)

    def test_ready_to_commit_queue(self) -> None:
        result = self._evaluate()

        self.assertEqual(POLICY_TYPE, result["policy_type"])
        self.assertEqual(STATUS_READY, result["status"])
        self.assertTrue(result["queue_commit_allowed"])
        self.assertTrue(result["preview_only"])
        self.assertFalse(result["queue_write"])
        self.assertFalse(result["runtime_write"])
        self.assertTrue(result["required_confirmations"]["manual_queue_write_confirmed"])
        self.assertTrue(result["required_confirmations"]["manual_runtime_queue_write_confirmed"])

    def test_runtime_commit_missing_invalid(self) -> None:
        result = self._evaluate(runtime_commit_result=None)

        self.assertEqual("INVALID", result["status"])
        self.assertFalse(result["queue_commit_allowed"])
        self.assertIn("MALFORMED_RUNTIME_COMMIT_RESULT", result["issues"])

    def test_runtime_status_not_committed_blocked(self) -> None:
        result = self._evaluate(runtime_commit_result=self._runtime_commit_result(status="ERROR"))

        self.assertEqual("BLOCKED", result["status"])
        self.assertFalse(result["queue_commit_allowed"])
        self.assertIn("RUNTIME_COMMIT_NOT_COMMITTED", result["issues"])

    def test_runtime_read_back_false_blocked(self) -> None:
        result = self._evaluate(runtime_commit_result=self._runtime_commit_result(read_back_verified=False))

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("RUNTIME_READ_BACK_NOT_VERIFIED", result["issues"])

    def test_queue_preview_invalid_blocked(self) -> None:
        preview = self._queue_preview()
        preview["write_preview"] = False

        result = self._evaluate(queue_write_preview_result=preview)

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("QUEUE_WRITE_PREVIEW_NOT_TRUE", result["issues"])

    def test_identity_mismatches_block(self) -> None:
        mismatch_cases = [
            ("execution_id", "OTHER_EXEC", "IDENTITY_MISMATCH_EXECUTION_ID"),
            ("order_id", "OTHER_ORDER", "IDENTITY_MISMATCH_ORDER_ID"),
            ("request_hash", "OTHER_HASH", "IDENTITY_MISMATCH_REQUEST_HASH"),
            ("lock_id", "OTHER_LOCK", "IDENTITY_MISMATCH_LOCK_ID"),
        ]

        for field, value, issue in mismatch_cases:
            with self.subTest(field=field):
                result = self._evaluate(queue_write_preview_result=self._queue_preview(**{field: value}))

                self.assertEqual("BLOCKED", result["status"])
                self.assertIn(issue, result["issues"])
                self.assertFalse(result["identity_checks"][field]["match"])

    def test_manual_queue_confirmation_missing_blocked(self) -> None:
        result = self._evaluate(confirmations=self._confirmations(queue=False))

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("MANUAL_QUEUE_WRITE_CONFIRMATION_REQUIRED", result["issues"])

    def test_runtime_queue_confirmation_missing_blocked(self) -> None:
        result = self._evaluate(confirmations=self._confirmations(runtime_queue=False))

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("MANUAL_RUNTIME_QUEUE_WRITE_CONFIRMATION_REQUIRED", result["issues"])

    def test_non_runtime_queue_path_does_not_require_runtime_confirmation(self) -> None:
        result = self._evaluate(
            queue_path=ROOT / "tmp_order_queue.json",
            confirmations=self._confirmations(runtime_queue=False),
        )

        self.assertEqual(STATUS_READY, result["status"])
        self.assertTrue(result["queue_commit_allowed"])
        self.assertFalse(result["required_confirmations"]["runtime_queue_path"])

    def test_no_queue_commit_sendorder_or_runtime_write(self) -> None:
        with (
            mock.patch("execution_queue_commit_service.commit_execution_queue_manually") as queue_commit,
            mock.patch("send_order_entrypoint.execute_send_order") as send_order,
        ):
            result = self._evaluate()

        self.assertEqual(STATUS_READY, result["status"])
        queue_commit.assert_not_called()
        send_order.assert_not_called()
        self.assertFalse(result["queue_write"])
        self.assertFalse(result["runtime_write"])

    def test_order_queue_runtime_and_rules_hash_unchanged(self) -> None:
        runtime_paths = [
            ROOT / "runtime" / "order_queue.json",
            ROOT / "runtime" / "order_executions.json",
            ROOT / "runtime" / "order_locks.json",
        ]
        rules_paths = list((ROOT / "routines").glob("**/rules.json"))
        before_runtime = {str(path): _sha256(path) for path in runtime_paths}
        before_rules = {str(path): _sha256(path) for path in rules_paths}

        self._evaluate()
        self._evaluate(runtime_commit_result=self._runtime_commit_result(status="ERROR"))
        self._evaluate(confirmations=self._confirmations(queue=False))

        self.assertEqual(before_runtime, {str(path): _sha256(path) for path in runtime_paths})
        self.assertEqual(before_rules, {str(path): _sha256(path) for path in rules_paths})


if __name__ == "__main__":
    unittest.main()
