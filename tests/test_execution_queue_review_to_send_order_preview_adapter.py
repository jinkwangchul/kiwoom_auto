# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import unittest
from unittest import mock

from execution_queue_review_to_send_order_preview_adapter import (
    STATUS_READY,
    adapt_queue_review_to_send_order_preview,
)


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ExecutionQueueReviewToSendOrderPreviewAdapterTest(unittest.TestCase):
    def _request_preview(self, **overrides: object) -> dict[str, object]:
        preview = {
            "account_no": "12345678",
            "side": "BUY",
            "code": "003550",
            "quantity": 10,
            "price": 100,
            "hoga": "LIMIT",
        }
        preview.update(overrides)
        return preview

    def _execution_request(self, **overrides: object) -> dict[str, object]:
        request = {
            "execution_id": "EXEC_ADAPTER_1",
            "order_id": "ORDER_ADAPTER_1",
            "source_signal_id": "SIGNAL_ADAPTER_1",
            "lock_id": "LOCK_ADAPTER_1",
            "request_hash": "HASH_ADAPTER_1",
            "guard_snapshot": {"account_no": "12345678", "operator_confirmed": True},
            "request_preview": self._request_preview(),
        }
        request.update(overrides)
        return request

    def _record(self, **overrides: object) -> dict[str, object]:
        record = {
            "id": "ORDER_QUEUED_ORDER_ADAPTER_1",
            "status": "ORDER_QUEUED",
            "source_signal_id": "SIGNAL_ADAPTER_1",
            "order_id": "ORDER_ADAPTER_1",
            "request_hash": "HASH_ADAPTER_1",
            "lock_id": "LOCK_ADAPTER_1",
            "execution_id": "EXEC_ADAPTER_1",
            "execution_request": self._execution_request(),
            "send_order_called": False,
            "execution_enabled": False,
            "blocked_reasons": [],
        }
        record.update(overrides)
        return record

    def _identity(self, **overrides: object) -> dict[str, object]:
        identity = {
            "order_id": "ORDER_ADAPTER_1",
            "source_signal_id": "SIGNAL_ADAPTER_1",
            "execution_id": "EXEC_ADAPTER_1",
            "request_hash": "HASH_ADAPTER_1",
            "lock_id": "LOCK_ADAPTER_1",
        }
        identity.update(overrides)
        return identity

    def _queue_review(self, **overrides: object) -> dict[str, object]:
        result = {
            "review_type": "EXECUTION_QUEUE_COMMITTED_REVIEW",
            "status": "READY_FOR_FINAL_SEND_GATE",
            "preview_only": True,
            "queue_write": False,
            "runtime_write": False,
            "send_order_called": False,
            "next_stage": "FINAL_SEND_GATE_REQUIRED",
            "order_queued_record": self._record(),
            "identity": self._identity(),
            "issues": [],
            "warnings": [],
        }
        result.update(overrides)
        return result

    def test_queue_review_ready_builds_send_order_preview(self) -> None:
        result = adapt_queue_review_to_send_order_preview(self._queue_review())

        self.assertEqual(STATUS_READY, result["status"])
        self.assertTrue(result["preview_only"])
        self.assertFalse(result["queue_write"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["send_order_called"])
        self.assertFalse(result["final_send_gate_called"])
        self.assertTrue(result["adapter_preview_result"]["adapter_preview_ok"])
        self.assertEqual("FINAL_SEND_GATE_REQUIRED", result["adapter_preview_result"]["next_stage"])

    def test_queue_review_blocked_invalid_and_malformed(self) -> None:
        blocked = adapt_queue_review_to_send_order_preview(
            self._queue_review(status="BLOCKED", issues=["blocked"])
        )
        invalid = adapt_queue_review_to_send_order_preview(
            self._queue_review(status="INVALID", issues=["invalid"])
        )
        malformed = adapt_queue_review_to_send_order_preview(None)

        self.assertEqual("BLOCKED", blocked["status"])
        self.assertEqual("INVALID", invalid["status"])
        self.assertEqual("INVALID", malformed["status"])
        self.assertIn("MALFORMED_QUEUE_COMMITTED_REVIEW_RESULT", malformed["issues"])

    def test_missing_order_queued_record_blocks(self) -> None:
        result = adapt_queue_review_to_send_order_preview(self._queue_review(order_queued_record=None))

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("ORDER_QUEUED_RECORD_REQUIRED", result["issues"])

    def test_next_stage_mismatch_blocks(self) -> None:
        result = adapt_queue_review_to_send_order_preview(self._queue_review(next_stage="OTHER"))

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("QUEUE_REVIEW_NEXT_STAGE_NOT_FINAL_SEND_GATE_REQUIRED", result["issues"])

    def test_missing_identity_field_blocks(self) -> None:
        for field in ("order_id", "source_signal_id", "execution_id", "request_hash", "lock_id"):
            with self.subTest(field=field):
                identity = self._identity()
                identity.pop(field)
                result = adapt_queue_review_to_send_order_preview(self._queue_review(identity=identity))

                self.assertEqual("BLOCKED", result["status"])
                self.assertIn(f"MISSING_{field.upper()}", result["issues"])

    def test_adapter_preview_result_not_ok_blocks(self) -> None:
        record = self._record(execution_request=None)
        result = adapt_queue_review_to_send_order_preview(self._queue_review(order_queued_record=record))

        self.assertEqual("BLOCKED", result["status"])
        self.assertIsNotNone(result["adapter_preview_result"])
        self.assertFalse(result["adapter_preview_result"]["adapter_preview_ok"])
        self.assertIn("order_queued_record.execution_request is required", result["issues"])

    def test_identity_mismatch_blocks(self) -> None:
        for field, value in (
            ("order_id", "OTHER_ORDER"),
            ("source_signal_id", "OTHER_SIGNAL"),
            ("execution_id", "OTHER_EXEC"),
            ("request_hash", "OTHER_HASH"),
            ("lock_id", "OTHER_LOCK"),
        ):
            with self.subTest(field=field):
                request = self._execution_request(**{field: value})
                record = self._record(execution_request=request)
                result = adapt_queue_review_to_send_order_preview(self._queue_review(order_queued_record=record))

                self.assertEqual("BLOCKED", result["status"])
                self.assertIn(f"record.{field} does not match execution_request.{field}", result["issues"])

    def test_adapter_preview_identity_mismatch_blocks(self) -> None:
        with mock.patch(
            "execution_queue_review_to_send_order_preview_adapter.preview_kiwoom_send_order_request"
        ) as preview:
            preview.return_value = {
                "adapter_preview_ok": True,
                "adapter_stage": "kiwoom_send_order_request_preview_created",
                "next_stage": "FINAL_SEND_GATE_REQUIRED",
                "preview_only": True,
                "no_send": True,
                "send_order_called": False,
                "send_order_request_preview": {
                    "order_id": "OTHER_ORDER",
                    "source_signal_id": "SIGNAL_ADAPTER_1",
                    "execution_id": "EXEC_ADAPTER_1",
                    "request_hash": "HASH_ADAPTER_1",
                    "lock_id": "LOCK_ADAPTER_1",
                },
                "blocked_reasons": [],
                "warnings": [],
            }

            result = adapt_queue_review_to_send_order_preview(self._queue_review())

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("IDENTITY_MISMATCH_ORDER_ID", result["issues"])

    def test_final_send_gate_send_order_and_queue_commit_are_not_called(self) -> None:
        with (
            mock.patch("final_send_gate_service.evaluate_final_send_gate") as final_gate,
            mock.patch("send_order_entrypoint.execute_send_order") as send_order,
            mock.patch("execution_queue_commit_service.commit_execution_queue_manually") as queue_commit,
        ):
            result = adapt_queue_review_to_send_order_preview(self._queue_review())

        self.assertEqual(STATUS_READY, result["status"])
        final_gate.assert_not_called()
        send_order.assert_not_called()
        queue_commit.assert_not_called()

    def test_inputs_are_not_mutated(self) -> None:
        queue_review = self._queue_review()
        original = deepcopy(queue_review)

        adapt_queue_review_to_send_order_preview(queue_review)

        self.assertEqual(original, queue_review)

    def test_order_queue_runtime_and_rules_hash_unchanged(self) -> None:
        runtime_paths = [
            ROOT / "runtime" / "order_queue.json",
            ROOT / "runtime" / "order_executions.json",
            ROOT / "runtime" / "order_locks.json",
        ]
        rules_paths = list((ROOT / "routines").glob("**/rules.json"))
        before_runtime = {str(path): _sha256(path) for path in runtime_paths}
        before_rules = {str(path): _sha256(path) for path in rules_paths}

        adapt_queue_review_to_send_order_preview(self._queue_review())
        adapt_queue_review_to_send_order_preview(self._queue_review(status="BLOCKED"))

        self.assertEqual(before_runtime, {str(path): _sha256(path) for path in runtime_paths})
        self.assertEqual(before_rules, {str(path): _sha256(path) for path in rules_paths})


if __name__ == "__main__":
    unittest.main()
