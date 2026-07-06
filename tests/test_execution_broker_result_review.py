# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
from pathlib import Path
import unittest
from unittest import mock

from execution_broker_result_review import review_broker_dispatch_result


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ExecutionBrokerResultReviewTest(unittest.TestCase):
    def _broker_result(self, **overrides: object) -> dict[str, object]:
        result = {
            "broker_status": "SUBMITTED",
            "broker_order_no": "BRK_REVIEW_1",
            "order_id": "ORDER_REVIEW_1",
            "request_hash": "HASH_REVIEW_1",
        }
        result.update(overrides)
        return result

    def _dispatch(self, **overrides: object) -> dict[str, object]:
        result = {
            "orchestrator_type": "EXECUTION_BROKER_DISPATCH_ORCHESTRATOR",
            "status": "BROKER_DISPATCH_SUBMITTED",
            "broker_dispatch_called": True,
            "kiwoom_called": False,
            "runtime_write": False,
            "queue_write": False,
            "send_order_called": True,
            "broker_result": self._broker_result(),
            "next_stage": "BROKER_RESULT_REVIEW_REQUIRED",
            "issues": [],
            "warnings": [],
        }
        result.update(overrides)
        return result

    def test_broker_success_ready_for_result_record(self) -> None:
        result = review_broker_dispatch_result(self._dispatch())

        self.assertEqual("READY_FOR_RESULT_RECORD", result["status"])
        self.assertEqual("BROKER_RESULT_RECORD_REQUIRED", result["next_stage"])
        self.assertTrue(result["preview_only"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_write"])
        self.assertTrue(result["broker_called"])
        self.assertFalse(result["kiwoom_called"])
        self.assertEqual("SUBMITTED", result["broker_result"]["broker_status"])

    def test_broker_blocked_dispatch_blocks(self) -> None:
        result = review_broker_dispatch_result(self._dispatch(status="BLOCKED", issues=["BROKER_DISPATCH_EXCEPTION: x"]))

        self.assertEqual("BLOCKED", result["status"])
        self.assertEqual("BLOCKED", result["next_stage"])
        self.assertIn("BROKER_DISPATCH_EXCEPTION: x", result["issues"])

    def test_malformed_dispatch_and_malformed_broker_result_invalid(self) -> None:
        malformed_dispatch = review_broker_dispatch_result("malformed")
        malformed_broker_result = review_broker_dispatch_result(self._dispatch(broker_result="malformed"))
        empty_broker_result = review_broker_dispatch_result(self._dispatch(broker_result={}))

        self.assertEqual("INVALID", malformed_dispatch["status"])
        self.assertEqual("INVALID", malformed_broker_result["status"])
        self.assertEqual("INVALID", empty_broker_result["status"])

    def test_broker_dispatch_called_false_blocks(self) -> None:
        result = review_broker_dispatch_result(self._dispatch(broker_dispatch_called=False))

        self.assertEqual("BLOCKED", result["status"])
        self.assertFalse(result["broker_called"])
        self.assertIn("BROKER_DISPATCH_CALLED_NOT_TRUE", result["issues"])

    def test_missing_broker_result_blocks(self) -> None:
        result = review_broker_dispatch_result(self._dispatch(broker_result=None))

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("BROKER_RESULT_REQUIRED", result["issues"])

    def test_next_stage_mismatch_blocks(self) -> None:
        result = review_broker_dispatch_result(self._dispatch(next_stage="BLOCKED"))

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("BROKER_RESULT_REVIEW_NEXT_STAGE_REQUIRED", result["issues"])

    def test_broker_exception_like_result_blocks(self) -> None:
        cases = [
            self._broker_result(exception="broker exception"),
            self._broker_result(error="broker error"),
            self._broker_result(broker_status="FAILED"),
            self._broker_result(status="REJECTED"),
        ]
        for broker_result in cases:
            with self.subTest(broker_result=broker_result):
                result = review_broker_dispatch_result(self._dispatch(broker_result=broker_result))

                self.assertEqual("BLOCKED", result["status"])

    def test_result_recorder_lock_release_runtime_and_queue_commit_are_not_called(self) -> None:
        with (
            mock.patch("execution_queue_commit_service.commit_execution_queue_manually") as queue_commit,
            mock.patch("execution_runtime_commit_service.commit_execution_runtime_plan") as runtime_commit,
        ):
            result = review_broker_dispatch_result(self._dispatch())

        self.assertEqual("READY_FOR_RESULT_RECORD", result["status"])
        queue_commit.assert_not_called()
        runtime_commit.assert_not_called()

    def test_runtime_order_queue_and_rules_hash_unchanged(self) -> None:
        protected_paths = [
            ROOT / "runtime" / "order_queue.json",
            ROOT / "runtime" / "order_executions.json",
            ROOT / "runtime" / "order_locks.json",
        ]
        protected_paths.extend((ROOT / "routines").glob("**/rules.json"))
        before = {str(path): _sha256(path) for path in protected_paths}

        review_broker_dispatch_result(self._dispatch())
        review_broker_dispatch_result(self._dispatch(status="BLOCKED"))

        self.assertEqual(before, {str(path): _sha256(path) for path in protected_paths})


if __name__ == "__main__":
    unittest.main()
