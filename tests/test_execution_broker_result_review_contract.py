# -*- coding: utf-8 -*-
from __future__ import annotations

import copy
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


def _broker_result(**overrides: object) -> dict[str, object]:
    result = {
        "broker_status": "SUBMITTED",
        "broker_order_no": "BRK_CONTRACT_1",
        "order_id": "ORDER_REVIEW_CONTRACT_1",
        "request_hash": "HASH_REVIEW_CONTRACT_1",
    }
    result.update(overrides)
    return result


def _dispatch(**overrides: object) -> dict[str, object]:
    result = {
        "orchestrator_type": "EXECUTION_BROKER_DISPATCH_ORCHESTRATOR",
        "status": "BROKER_DISPATCH_SUBMITTED",
        "broker_dispatch_called": True,
        "kiwoom_called": False,
        "runtime_write": False,
        "queue_write": False,
        "send_order_called": True,
        "broker_result": _broker_result(),
        "next_stage": "BROKER_RESULT_REVIEW_REQUIRED",
        "issues": [],
        "warnings": [],
    }
    result.update(overrides)
    return result


class ExecutionBrokerResultReviewContractTest(unittest.TestCase):
    def test_broker_success_ready_for_result_record_and_preserves_result(self) -> None:
        broker_result = _broker_result()

        result = review_broker_dispatch_result(_dispatch(broker_result=copy.deepcopy(broker_result)))

        self.assertEqual("READY_FOR_RESULT_RECORD", result["status"])
        self.assertEqual("BROKER_RESULT_RECORD_REQUIRED", result["next_stage"])
        self.assertEqual(broker_result, result["broker_result"])
        self.assertTrue(result["preview_only"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_write"])

    def test_broker_blocked_error_rejected_failed_block(self) -> None:
        cases = [
            _broker_result(exception="broker exception"),
            _broker_result(error="broker error"),
            _broker_result(broker_status="BLOCKED"),
            _broker_result(broker_status="FAILED"),
            _broker_result(status="REJECTED"),
            _broker_result(result="INVALID"),
        ]

        for broker_result in cases:
            with self.subTest(broker_result=broker_result):
                result = review_broker_dispatch_result(_dispatch(broker_result=broker_result))

                self.assertEqual("BLOCKED", result["status"])
                self.assertEqual("BLOCKED", result["next_stage"])
                self.assertFalse(result["runtime_write"])
                self.assertFalse(result["queue_write"])

    def test_malformed_dispatch_and_result_are_invalid(self) -> None:
        malformed_dispatch = review_broker_dispatch_result("malformed")
        malformed_result = review_broker_dispatch_result(_dispatch(broker_result="malformed"))
        empty_result = review_broker_dispatch_result(_dispatch(broker_result={}))

        self.assertEqual("INVALID", malformed_dispatch["status"])
        self.assertEqual("INVALID", malformed_result["status"])
        self.assertEqual("INVALID", empty_result["status"])

    def test_dispatch_not_called_missing_result_and_next_stage_mismatch_block(self) -> None:
        cases = [
            (_dispatch(broker_dispatch_called=False), "BROKER_DISPATCH_CALLED_NOT_TRUE"),
            (_dispatch(broker_result=None), "BROKER_RESULT_REQUIRED"),
            (_dispatch(next_stage="BLOCKED"), "BROKER_RESULT_REVIEW_NEXT_STAGE_REQUIRED"),
        ]

        for dispatch, issue in cases:
            with self.subTest(issue=issue):
                result = review_broker_dispatch_result(dispatch)

                self.assertEqual("BLOCKED", result["status"])
                self.assertIn(issue, result["issues"])

    def test_result_recorder_lock_release_runtime_and_queue_commit_are_not_called(self) -> None:
        with (
            mock.patch("execution_queue_commit_service.commit_execution_queue_manually") as queue_commit,
            mock.patch("execution_runtime_commit_service.commit_execution_runtime_plan") as runtime_commit,
        ):
            result = review_broker_dispatch_result(_dispatch())

        self.assertEqual("READY_FOR_RESULT_RECORD", result["status"])
        queue_commit.assert_not_called()
        runtime_commit.assert_not_called()

    def test_input_deepcopy_boundary(self) -> None:
        dispatch = _dispatch()
        before = copy.deepcopy(dispatch)

        result = review_broker_dispatch_result(dispatch)
        result["broker_result"]["broker_status"] = "MUTATED"

        self.assertEqual(before, dispatch)

    def test_runtime_order_queue_and_rules_are_unchanged(self) -> None:
        protected_paths = [
            ROOT / "runtime" / "order_queue.json",
            ROOT / "runtime" / "order_executions.json",
            ROOT / "runtime" / "order_locks.json",
        ]
        protected_paths.extend((ROOT / "routines").glob("**/rules.json"))
        before = {str(path): _sha256(path) for path in protected_paths}

        review_broker_dispatch_result(_dispatch())
        review_broker_dispatch_result(_dispatch(status="BLOCKED"))

        self.assertEqual(before, {str(path): _sha256(path) for path in protected_paths})


if __name__ == "__main__":
    unittest.main()
