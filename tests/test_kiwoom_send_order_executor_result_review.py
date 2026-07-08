# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import unittest
from unittest import mock

from kiwoom_send_order_executor_result_review import review_kiwoom_send_order_executor_result


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


class KiwoomSendOrderExecutorResultReviewTest(unittest.TestCase):
    def _call_preview(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "status": "SEND_ORDER_CALL_READY",
            "send_order_call_preview": {
                "preview_type": "KIWOOM_SEND_ORDER_CALL_PREVIEW",
                "final_call_token": "FINAL_CALL_TOKEN_REVIEW_1",
                "dispatch_id": "DISPATCH_REVIEW_1",
                "order_id": "ORDER_REVIEW_1",
                "account_no": "12345678",
                "screen_no": "0101",
                "send_order_args_ready": True,
            },
            "send_order_args": ["0101", "BUY", "12345678", 1, "003550", 10, 85000, "03", ""],
            "issues": [],
            "warnings": [],
            "send_order_called": False,
            "broker_called": False,
            "runtime_write": False,
            "queue_write": False,
        }
        result.update(overrides)
        return result

    def _executor_result(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "status": "SEND_ORDER_SENT",
            "send_order_result": {
                "executor_stage": "send_order_adapter_called",
                "final_call_token": "FINAL_CALL_TOKEN_REVIEW_1",
                "dispatch_id": "DISPATCH_REVIEW_1",
                "order_id": "ORDER_REVIEW_1",
                "send_order_args": ["0101", "BUY", "12345678", 1, "003550", 10, 85000, "03", ""],
                "raw_result": 0,
                "return_code": 0,
                "adapter_call_count": 1,
            },
            "issues": [],
            "warnings": [],
            "adapter_call_count": 1,
            "send_order_called": True,
            "broker_called": True,
            "runtime_write": False,
            "queue_write": False,
            "recorded": False,
        }
        result.update(overrides)
        return result

    def _context(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {"review_enabled": True}
        result.update(overrides)
        return result

    def test_send_order_review_ok_normal(self) -> None:
        result = review_kiwoom_send_order_executor_result(
            self._executor_result(),
            self._call_preview(),
            self._context(),
        )

        self.assertEqual("SEND_ORDER_REVIEW_OK", result["status"])
        self.assertTrue(result["record_ready"])
        self.assertTrue(result["chejan_wait_required"])
        self.assertTrue(result["send_order_called"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_write"])
        self.assertFalse(result["recorded"])
        self.assertFalse(result["chejan_processed"])
        review = result["review"]
        self.assertEqual(0, review["return_code"])
        self.assertEqual(1, review["adapter_call_count"])
        self.assertEqual("DISPATCH_REVIEW_1", review["dispatch_id"])
        self.assertEqual("ORDER_REVIEW_1", review["order_id"])

    def test_send_order_review_failed_normal(self) -> None:
        executor = self._executor_result(
            status="SEND_ORDER_FAILED",
            issues=["send_order_adapter returned non-zero or unknown return code"],
        )
        executor["send_order_result"]["raw_result"] = -308
        executor["send_order_result"]["return_code"] = -308

        result = review_kiwoom_send_order_executor_result(
            executor,
            self._call_preview(),
            self._context(),
        )

        self.assertEqual("SEND_ORDER_REVIEW_FAILED", result["status"])
        self.assertFalse(result["record_ready"])
        self.assertFalse(result["chejan_wait_required"])
        self.assertTrue(result["send_order_called"])
        self.assertEqual(-308, result["review"]["return_code"])

    def test_send_order_review_uncertain_for_error(self) -> None:
        executor = self._executor_result(
            status="ERROR",
            issues=["send_order_adapter raised exception: boom"],
        )
        executor["send_order_result"]["return_code"] = None
        executor["send_order_result"]["exception"] = "boom"

        result = review_kiwoom_send_order_executor_result(
            executor,
            self._call_preview(),
            self._context(),
        )

        self.assertEqual("SEND_ORDER_REVIEW_UNCERTAIN", result["status"])
        self.assertFalse(result["record_ready"])
        self.assertFalse(result["chejan_wait_required"])
        self.assertTrue(result["send_order_called"])

    def test_blocked_for_executor_blocked(self) -> None:
        result = review_kiwoom_send_order_executor_result(
            self._executor_result(status="BLOCKED", send_order_called=False, broker_called=False, issues=["blocked"]),
            self._call_preview(),
            self._context(),
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertFalse(result["record_ready"])
        self.assertFalse(result["send_order_called"])

    def test_invalid_for_executor_invalid(self) -> None:
        result = review_kiwoom_send_order_executor_result(
            self._executor_result(status="INVALID", send_order_called=False, broker_called=False, issues=["bad"]),
            self._call_preview(),
            self._context(),
        )

        self.assertEqual("INVALID", result["status"])
        self.assertFalse(result["record_ready"])

    def test_order_id_mismatch_is_invalid(self) -> None:
        executor = self._executor_result()
        executor["send_order_result"]["order_id"] = "OTHER_ORDER"

        result = review_kiwoom_send_order_executor_result(
            executor,
            self._call_preview(),
            self._context(),
        )

        self.assertEqual("INVALID", result["status"])
        self.assertIn("executor order_id does not match call preview", result["issues"])

    def test_dispatch_id_mismatch_is_invalid(self) -> None:
        executor = self._executor_result()
        executor["send_order_result"]["dispatch_id"] = "OTHER_DISPATCH"

        result = review_kiwoom_send_order_executor_result(
            executor,
            self._call_preview(),
            self._context(),
        )

        self.assertEqual("INVALID", result["status"])
        self.assertIn("executor dispatch_id does not match call preview", result["issues"])

    def test_adapter_call_count_not_one_is_invalid(self) -> None:
        executor = self._executor_result(adapter_call_count=2)
        executor["send_order_result"]["adapter_call_count"] = 2

        result = review_kiwoom_send_order_executor_result(
            executor,
            self._call_preview(),
            self._context(),
        )

        self.assertEqual("INVALID", result["status"])
        self.assertIn("adapter_call_count must be 1", result["issues"])

    def test_missing_return_code_is_uncertain(self) -> None:
        executor = self._executor_result()
        executor["send_order_result"]["return_code"] = None

        result = review_kiwoom_send_order_executor_result(
            executor,
            self._call_preview(),
            self._context(),
        )

        self.assertEqual("SEND_ORDER_REVIEW_UNCERTAIN", result["status"])
        self.assertFalse(result["record_ready"])

    def test_malformed_input_is_invalid(self) -> None:
        result = review_kiwoom_send_order_executor_result(
            None,
            self._call_preview(),
            self._context(),
        )

        self.assertEqual("INVALID", result["status"])
        self.assertIn("send_order_executor_result must be a dict", result["issues"])

    def test_inputs_are_not_mutated(self) -> None:
        executor = self._executor_result()
        call_preview = self._call_preview()
        context = self._context()
        originals = (deepcopy(executor), deepcopy(call_preview), deepcopy(context))

        result = review_kiwoom_send_order_executor_result(executor, call_preview, context)
        result["review"]["order_id"] = "MUTATED"

        self.assertEqual(originals[0], executor)
        self.assertEqual(originals[1], call_preview)
        self.assertEqual(originals[2], context)

    def test_runtime_order_queue_rules_hash_unchanged_and_no_recorders_or_retry(self) -> None:
        before = {path: _sha256(path) for path in _protected_paths()}

        with mock.patch("send_order_result_recorder.record_send_order_result") as result_recorder, \
            mock.patch("chejan_event_recorder.record_chejan_event") as chejan_recorder, \
            mock.patch("kiwoom_send_order_executor.execute_kiwoom_send_order") as executor:
            result = review_kiwoom_send_order_executor_result(
                self._executor_result(),
                self._call_preview(),
                self._context(),
            )

        self.assertEqual("SEND_ORDER_REVIEW_OK", result["status"])
        result_recorder.assert_not_called()
        chejan_recorder.assert_not_called()
        executor.assert_not_called()
        self.assertEqual(before, {path: _sha256(path) for path in _protected_paths()})
        self.assertFalse((ROOT / "runtime" / "order_executions.json").exists())
        self.assertFalse((ROOT / "runtime" / "order_locks.json").exists())


if __name__ == "__main__":
    unittest.main()
