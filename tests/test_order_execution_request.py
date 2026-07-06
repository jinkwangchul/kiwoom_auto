# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import unittest

from order_execution_request import build_execution_request_preview


class OrderExecutionRequestPreviewTest(unittest.TestCase):
    def _order(self) -> dict:
        return {
            "id": "ORDER_1",
            "source_signal_id": "SIG_1",
            "code": "003550",
            "side": "BUY",
        }

    def _guard(self) -> dict:
        return {
            "operator_confirmed": True,
            "real_trade_enabled": True,
            "account_no": "12345678",
        }

    def _execution_preview(self) -> dict:
        return {
            "stage": "EXECUTION_PREVIEW",
            "unresolved": False,
            "adapter_request_preview": {
                "request_preview_built": True,
                "request_preview": {
                    "code": "003550",
                    "quantity": 10,
                    "send_order_enabled": False,
                },
                "send_order_called": False,
            },
        }

    def _final_guard_result(self) -> dict:
        return {
            "stage": "FINAL_EXECUTION_GUARD",
            "ok": True,
            "blocked_reasons": [],
            "warnings": [],
        }

    def _lock_preview(self) -> dict:
        return {
            "stage": "ORDER_LOCK_PREVIEW",
            "lock_id": "LOCK_PREVIEW_ORDER_1_003550_BUY_SIG_1",
            "unresolved": False,
        }

    def _request_hash_preview(self) -> dict:
        return {
            "stage": "REQUEST_HASH_PREVIEW",
            "request_hash": "a" * 64,
            "hash_source": {
                "order_id": "ORDER_1",
                "source_signal_id": "SIG_1",
            },
            "unresolved": False,
        }

    def _build(self, **overrides: dict) -> dict:
        return build_execution_request_preview(
            overrides.get("order", self._order()),
            overrides.get("guard", self._guard()),
            overrides.get("execution_preview", self._execution_preview()),
            overrides.get("final_guard_result", self._final_guard_result()),
            overrides.get("lock_preview", self._lock_preview()),
            overrides.get("request_hash_preview", self._request_hash_preview()),
        )

    def test_builds_normal_execution_request_preview(self) -> None:
        result = self._build()

        self.assertTrue(result["ok"])
        self.assertEqual("EXECUTION_REQUEST_PREVIEW", result["stage"])
        self.assertFalse(result["unresolved"])
        self.assertEqual([], result["blocked_reasons"])
        request = result["execution_request"]
        self.assertEqual("ORDER_1", request["order_id"])
        self.assertEqual("SIG_1", request["source_signal_id"])
        self.assertEqual("LOCK_PREVIEW_ORDER_1_003550_BUY_SIG_1", request["lock_id"])
        self.assertEqual("a" * 64, request["request_hash"])
        self.assertEqual(self._guard(), request["guard_snapshot"])
        self.assertEqual("003550", request["request_preview"]["code"])
        self.assertTrue(request["execution_id"].startswith("EXEC_PREVIEW_ORDER_1_"))

    def test_final_guard_failure_blocks(self) -> None:
        final_guard_result = self._final_guard_result()
        final_guard_result["ok"] = False
        final_guard_result["blocked_reasons"] = ["blocked"]

        result = self._build(final_guard_result=final_guard_result)

        self.assertFalse(result["ok"])
        self.assertTrue(result["unresolved"])
        self.assertIsNone(result["execution_request"])
        self.assertIn("final_guard_result is not ok", result["blocked_reasons"])

    def test_lock_unresolved_blocks(self) -> None:
        lock_preview = self._lock_preview()
        lock_preview["unresolved"] = True

        result = self._build(lock_preview=lock_preview)

        self.assertFalse(result["ok"])
        self.assertIn("lock_preview is unresolved", result["blocked_reasons"])

    def test_request_hash_unresolved_blocks(self) -> None:
        request_hash_preview = self._request_hash_preview()
        request_hash_preview["unresolved"] = True

        result = self._build(request_hash_preview=request_hash_preview)

        self.assertFalse(result["ok"])
        self.assertIn("request_hash_preview is unresolved", result["blocked_reasons"])

    def test_missing_request_hash_blocks(self) -> None:
        request_hash_preview = self._request_hash_preview()
        request_hash_preview["request_hash"] = ""

        result = self._build(request_hash_preview=request_hash_preview)

        self.assertFalse(result["ok"])
        self.assertIn("request_hash is required", result["blocked_reasons"])

    def test_execution_preview_unresolved_blocks(self) -> None:
        execution_preview = self._execution_preview()
        execution_preview["unresolved"] = True

        result = self._build(execution_preview=execution_preview)

        self.assertFalse(result["ok"])
        self.assertIn("execution_preview is unresolved", result["blocked_reasons"])

    def test_input_dicts_are_not_mutated(self) -> None:
        order = self._order()
        guard = self._guard()
        execution_preview = self._execution_preview()
        final_guard_result = self._final_guard_result()
        lock_preview = self._lock_preview()
        request_hash_preview = self._request_hash_preview()
        originals = [
            deepcopy(order),
            deepcopy(guard),
            deepcopy(execution_preview),
            deepcopy(final_guard_result),
            deepcopy(lock_preview),
            deepcopy(request_hash_preview),
        ]

        build_execution_request_preview(
            order,
            guard,
            execution_preview,
            final_guard_result,
            lock_preview,
            request_hash_preview,
        )

        self.assertEqual(originals[0], order)
        self.assertEqual(originals[1], guard)
        self.assertEqual(originals[2], execution_preview)
        self.assertEqual(originals[3], final_guard_result)
        self.assertEqual(originals[4], lock_preview)
        self.assertEqual(originals[5], request_hash_preview)


if __name__ == "__main__":
    unittest.main()
