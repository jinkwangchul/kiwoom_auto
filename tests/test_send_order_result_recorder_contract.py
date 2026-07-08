# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import unittest
from unittest import mock

from send_order_result_recorder_contract import build_send_order_result_recorder_contract


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _protected_paths() -> list[Path]:
    paths = [
        ROOT / "runtime" / "order_queue.json",
        ROOT / "runtime" / "order_executions.json",
        ROOT / "runtime" / "order_locks.json",
    ]
    paths.extend(sorted((ROOT / "routines").glob("*/rules.json")))
    return paths


class SendOrderResultRecorderContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.protected_hashes = {path: _sha256(path) for path in _protected_paths()}

    def tearDown(self) -> None:
        self.assertEqual(self.protected_hashes, {path: _sha256(path) for path in _protected_paths()})

    def _executor_review(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "status": "SEND_ORDER_REVIEW_OK",
            "review": {
                "review_type": "KIWOOM_SEND_ORDER_EXECUTOR_RESULT_REVIEW",
                "executor_status": "SEND_ORDER_SENT",
                "return_code": 0,
                "adapter_call_count": 1,
                "dispatch_id": "DISPATCH_RECORD_1",
                "order_id": "ORDER_RECORD_1",
                "final_call_token": "FINAL_CALL_TOKEN_RECORD_1",
                "recording_deferred": True,
                "chejan_deferred": True,
            },
            "issues": [],
            "warnings": [],
            "record_ready": True,
            "chejan_wait_required": True,
            "send_order_called": True,
            "runtime_write": False,
            "queue_write": False,
            "recorded": False,
            "chejan_processed": False,
        }
        result.update(overrides)
        return result

    def _record_context(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "source_order_id": "SOURCE_ORDER_RECORD_1",
            "source_signal_id": "SIGNAL_RECORD_1",
            "code": "003550",
            "side": "BUY",
            "quantity": 10,
            "price": 85000,
            "hoga": "03",
        }
        result.update(overrides)
        return result

    def test_record_ready_normal(self) -> None:
        result = build_send_order_result_recorder_contract(
            self._executor_review(),
            self._record_context(),
        )

        self.assertEqual("RECORD_READY", result["status"])
        self.assertTrue(result["record_ready"])
        self.assertFalse(result["record_called"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_write"])
        self.assertFalse(result["chejan_called"])

        contract = result["record_contract"]
        for field in (
            "dispatch_id",
            "order_id",
            "source_order_id",
            "source_signal_id",
            "code",
            "side",
            "quantity",
            "price",
            "hoga",
            "send_order_return_code",
            "send_order_status",
            "review_status",
            "recorded_at",
        ):
            self.assertIn(field, contract)
            self.assertIsNotNone(contract[field])
        self.assertEqual("DISPATCH_RECORD_1", contract["dispatch_id"])
        self.assertEqual("ORDER_RECORD_1", contract["order_id"])
        self.assertEqual("SOURCE_ORDER_RECORD_1", contract["source_order_id"])
        self.assertEqual("SIGNAL_RECORD_1", contract["source_signal_id"])
        self.assertEqual(0, contract["send_order_return_code"])
        self.assertEqual("SEND_ORDER_SENT", contract["send_order_status"])
        self.assertEqual("SEND_ORDER_REVIEW_OK", contract["review_status"])

    def test_blocked_for_executor_blocked(self) -> None:
        result = build_send_order_result_recorder_contract(
            self._executor_review(status="BLOCKED", record_ready=False, issues=["blocked"]),
            self._record_context(),
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertFalse(result["record_ready"])
        self.assertIn("executor_review_result.status is BLOCKED", result["issues"])

    def test_blocked_for_review_failed(self) -> None:
        result = build_send_order_result_recorder_contract(
            self._executor_review(status="SEND_ORDER_REVIEW_FAILED", record_ready=False),
            self._record_context(),
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertFalse(result["record_ready"])

    def test_blocked_for_uncertain_review(self) -> None:
        result = build_send_order_result_recorder_contract(
            self._executor_review(status="SEND_ORDER_REVIEW_UNCERTAIN", record_ready=False),
            self._record_context(),
        )

        self.assertEqual("BLOCKED", result["status"])

    def test_invalid_for_executor_invalid(self) -> None:
        result = build_send_order_result_recorder_contract(
            self._executor_review(status="INVALID", record_ready=False, issues=["bad"]),
            self._record_context(),
        )

        self.assertEqual("INVALID", result["status"])
        self.assertFalse(result["record_ready"])

    def test_malformed_input_is_invalid(self) -> None:
        result = build_send_order_result_recorder_contract(None, self._record_context())
        self.assertEqual("INVALID", result["status"])

        result = build_send_order_result_recorder_contract(self._executor_review(), "bad")
        self.assertEqual("INVALID", result["status"])

    def test_record_context_malformed_is_invalid(self) -> None:
        result = build_send_order_result_recorder_contract(self._executor_review(), {})

        self.assertEqual("INVALID", result["status"])
        self.assertIn("record_context must be a non-empty dict", result["issues"])

    def test_missing_required_field_is_invalid(self) -> None:
        context = self._record_context(source_signal_id="")

        result = build_send_order_result_recorder_contract(self._executor_review(), context)

        self.assertEqual("INVALID", result["status"])
        self.assertIn("source_signal_id", result["issues"][0])

    def test_review_missing_is_invalid(self) -> None:
        result = build_send_order_result_recorder_contract(
            self._executor_review(review={}),
            self._record_context(),
        )

        self.assertEqual("INVALID", result["status"])
        self.assertIn("executor_review_result.review is required", result["issues"])

    def test_deepcopy_defends_external_mutation(self) -> None:
        executor_review = self._executor_review()
        record_context = self._record_context()
        executor_before = deepcopy(executor_review)
        context_before = deepcopy(record_context)

        result = build_send_order_result_recorder_contract(executor_review, record_context)
        result["record_contract"]["order_id"] = "MUTATED_ORDER"
        result["record_contract"]["source_review"]["order_id"] = "MUTATED_REVIEW_ORDER"

        self.assertEqual(executor_before, executor_review)
        self.assertEqual(context_before, record_context)
        fresh = build_send_order_result_recorder_contract(executor_review, record_context)
        self.assertEqual("ORDER_RECORD_1", fresh["record_contract"]["order_id"])
        self.assertEqual("ORDER_RECORD_1", fresh["record_contract"]["source_review"]["order_id"])

    def test_no_runtime_queue_chejan_or_retry_side_effects(self) -> None:
        with mock.patch.dict("sys.modules", {}):
            result = build_send_order_result_recorder_contract(
                self._executor_review(),
                self._record_context(),
            )

        self.assertEqual("RECORD_READY", result["status"])
        self.assertFalse(result["record_called"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_write"])
        self.assertFalse(result["chejan_called"])


if __name__ == "__main__":
    unittest.main()
