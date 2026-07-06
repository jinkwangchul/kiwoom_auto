from __future__ import annotations

import hashlib
from pathlib import Path
import unittest
from unittest import mock

from execution_queue_commit_readiness_policy import (
    STATUS_READY,
    evaluate_execution_queue_commit_readiness,
)


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ExecutionQueueCommitReadinessPolicyContractTest(unittest.TestCase):
    def _runtime_commit_result(self, **overrides) -> dict:
        result = {
            "service_type": "EXECUTION_RUNTIME_COMMIT_SERVICE",
            "status": "COMMITTED",
            "runtime_write": True,
            "committed": True,
            "read_back_verified": True,
            "execution_id": "EXEC_QUEUE_CONTRACT_1",
            "order_id": "ORDER_QUEUE_CONTRACT_1",
            "request_hash": "HASH_QUEUE_CONTRACT_1",
            "lock_id": "LOCK_QUEUE_CONTRACT_1",
            "issues": [],
            "warnings": [],
        }
        result.update(overrides)
        return result

    def _queue_preview(self, **record_overrides) -> dict:
        record = {
            "id": "ORDER_QUEUED_ORDER_QUEUE_CONTRACT_1",
            "status": "ORDER_QUEUED",
            "source": "execution_queue_pending",
            "source_signal_id": "SIGNAL_QUEUE_CONTRACT_1",
            "order_id": "ORDER_QUEUE_CONTRACT_1",
            "candidate_id": "CANDIDATE_QUEUE_CONTRACT_1",
            "queue_pending_id": "QUEUE_PENDING_QUEUE_CONTRACT_1",
            "request_hash": "HASH_QUEUE_CONTRACT_1",
            "lock_id": "LOCK_QUEUE_CONTRACT_1",
            "execution_id": "EXEC_QUEUE_CONTRACT_1",
            "execution_request": {
                "execution_id": "EXEC_QUEUE_CONTRACT_1",
                "order_id": "ORDER_QUEUE_CONTRACT_1",
                "request_hash": "HASH_QUEUE_CONTRACT_1",
                "lock_id": "LOCK_QUEUE_CONTRACT_1",
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

        self.assertEqual(STATUS_READY, result["status"])
        self.assertTrue(result["queue_commit_allowed"])
        self.assertTrue(result["preview_only"])
        self.assertFalse(result["queue_write"])
        self.assertFalse(result["runtime_write"])

    def test_queue_commit_allowed_only_when_ready(self) -> None:
        results = [
            self._evaluate(),
            self._evaluate(runtime_commit_result=self._runtime_commit_result(status="ERROR")),
            self._evaluate(runtime_commit_result=None),
            self._evaluate(confirmations=self._confirmations(queue=False)),
            self._evaluate(queue_write_preview_result=self._queue_preview(execution_id="OTHER_EXEC")),
        ]

        self.assertTrue(results[0]["queue_commit_allowed"])
        for result in results[1:]:
            self.assertFalse(result["queue_commit_allowed"])

    def test_runtime_commit_missing_invalid(self) -> None:
        result = self._evaluate(runtime_commit_result=None)

        self.assertEqual("INVALID", result["status"])
        self.assertIn("MALFORMED_RUNTIME_COMMIT_RESULT", result["issues"])

    def test_runtime_status_and_flags_block(self) -> None:
        cases = [
            (self._runtime_commit_result(status="BLOCKED"), "RUNTIME_COMMIT_NOT_COMMITTED"),
            (self._runtime_commit_result(committed=False), "RUNTIME_COMMITTED_FLAG_NOT_TRUE"),
            (self._runtime_commit_result(runtime_write=False), "RUNTIME_WRITE_FLAG_NOT_TRUE"),
            (self._runtime_commit_result(read_back_verified=False), "RUNTIME_READ_BACK_NOT_VERIFIED"),
        ]

        for runtime_result, issue in cases:
            with self.subTest(issue=issue):
                result = self._evaluate(runtime_commit_result=runtime_result)

                self.assertEqual("BLOCKED", result["status"])
                self.assertIn(issue, result["issues"])

    def test_queue_preview_invalid_blocks(self) -> None:
        preview = self._queue_preview()
        preview["next_stage"] = "BLOCKED"

        result = self._evaluate(queue_write_preview_result=preview)

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("QUEUE_NEXT_STAGE_NOT_QUEUE_WRITE_REQUIRED", result["issues"])

    def test_identity_mismatches_block(self) -> None:
        cases = [
            ("execution_id", "OTHER_EXEC", "IDENTITY_MISMATCH_EXECUTION_ID"),
            ("order_id", "OTHER_ORDER", "IDENTITY_MISMATCH_ORDER_ID"),
            ("request_hash", "OTHER_HASH", "IDENTITY_MISMATCH_REQUEST_HASH"),
            ("lock_id", "OTHER_LOCK", "IDENTITY_MISMATCH_LOCK_ID"),
        ]

        for field, value, issue in cases:
            with self.subTest(field=field):
                result = self._evaluate(queue_write_preview_result=self._queue_preview(**{field: value}))

                self.assertEqual("BLOCKED", result["status"])
                self.assertIn(issue, result["issues"])

    def test_confirmation_missing_blocks(self) -> None:
        queue_missing = self._evaluate(confirmations=self._confirmations(queue=False))
        runtime_missing = self._evaluate(confirmations=self._confirmations(runtime_queue=False))

        self.assertEqual("BLOCKED", queue_missing["status"])
        self.assertIn("MANUAL_QUEUE_WRITE_CONFIRMATION_REQUIRED", queue_missing["issues"])
        self.assertEqual("BLOCKED", runtime_missing["status"])
        self.assertIn("MANUAL_RUNTIME_QUEUE_WRITE_CONFIRMATION_REQUIRED", runtime_missing["issues"])

    def test_preview_only_no_write_contract(self) -> None:
        for result in (
            self._evaluate(),
            self._evaluate(confirmations=self._confirmations(queue=False)),
            self._evaluate(runtime_commit_result=self._runtime_commit_result(status="ERROR")),
        ):
            self.assertTrue(result["preview_only"])
            self.assertFalse(result["queue_write"])
            self.assertFalse(result["runtime_write"])

    def test_no_queue_commit_sendorder_execution_controller_or_gui_calls(self) -> None:
        with (
            mock.patch("execution_queue_commit_service.commit_execution_queue_manually") as queue_commit,
            mock.patch("send_order_entrypoint.execute_send_order") as send_order,
        ):
            result = self._evaluate()

        self.assertEqual(STATUS_READY, result["status"])
        queue_commit.assert_not_called()
        send_order.assert_not_called()
        self.assertNotIn("execution_controller_called", result)
        self.assertNotIn("gui_connected", result)

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
        self._evaluate(confirmations=self._confirmations(queue=False))
        self._evaluate(runtime_commit_result=self._runtime_commit_result(status="ERROR"))

        self.assertEqual(before_runtime, {str(path): _sha256(path) for path in runtime_paths})
        self.assertEqual(before_rules, {str(path): _sha256(path) for path in rules_paths})


if __name__ == "__main__":
    unittest.main()
