# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import unittest

from order_request_hash import build_order_request_hash_preview


class OrderRequestHashPreviewTest(unittest.TestCase):
    def _order(self) -> dict:
        return {
            "id": "ORDER_1",
            "source_signal_id": "SIG_1",
            "code": "003550",
            "side": "BUY",
            "quantity": 10,
            "price": 85000,
        }

    def _execution_preview(self) -> dict:
        return {
            "stage": "EXECUTION_PREVIEW",
            "unresolved": False,
            "hoga_preview": {
                "hoga": "MARKET",
                "unresolved": False,
            },
            "order_type_preview": {
                "order_type": "BUY",
                "unresolved": False,
            },
        }

    def _lock_preview(self) -> dict:
        return {
            "stage": "ORDER_LOCK_PREVIEW",
            "lock_id": "LOCK_PREVIEW_ORDER_1_003550_BUY_SIG_1",
            "unresolved": False,
        }

    def test_builds_normal_hash_preview(self) -> None:
        result = build_order_request_hash_preview(
            self._order(),
            self._execution_preview(),
            self._lock_preview(),
        )

        self.assertTrue(result["ok"])
        self.assertEqual("REQUEST_HASH_PREVIEW", result["stage"])
        self.assertIsInstance(result["request_hash"], str)
        self.assertEqual(64, len(result["request_hash"]))
        self.assertFalse(result["unresolved"])
        self.assertEqual([], result["blocked_reasons"])
        self.assertEqual([], result["warnings"])
        self.assertEqual("ORDER_1", result["hash_source"]["order_id"])
        self.assertEqual("MARKET", result["hash_source"]["hoga"])

    def test_same_input_builds_same_hash(self) -> None:
        first = build_order_request_hash_preview(
            self._order(),
            self._execution_preview(),
            self._lock_preview(),
        )
        second = build_order_request_hash_preview(
            self._order(),
            self._execution_preview(),
            self._lock_preview(),
        )

        self.assertEqual(first["request_hash"], second["request_hash"])

    def test_changed_field_changes_hash(self) -> None:
        order = self._order()
        changed_order = self._order()
        changed_order["quantity"] = 11

        first = build_order_request_hash_preview(order, self._execution_preview(), self._lock_preview())
        second = build_order_request_hash_preview(changed_order, self._execution_preview(), self._lock_preview())

        self.assertNotEqual(first["request_hash"], second["request_hash"])

    def test_missing_required_field_blocks(self) -> None:
        order = self._order()
        order.pop("price")

        result = build_order_request_hash_preview(order, self._execution_preview(), self._lock_preview())

        self.assertFalse(result["ok"])
        self.assertTrue(result["unresolved"])
        self.assertIsNone(result["request_hash"])
        self.assertIn("price is required", result["blocked_reasons"])

    def test_all_required_missing_fields_are_reported(self) -> None:
        result = build_order_request_hash_preview({}, {}, {})

        self.assertFalse(result["ok"])
        self.assertTrue(result["unresolved"])
        self.assertIsNone(result["request_hash"])
        self.assertIn("order_id is required", result["blocked_reasons"])
        self.assertIn("source_signal_id is required", result["blocked_reasons"])
        self.assertIn("code is required", result["blocked_reasons"])
        self.assertIn("side/order_type is required", result["blocked_reasons"])
        self.assertIn("quantity is required", result["blocked_reasons"])
        self.assertIn("price is required", result["blocked_reasons"])
        self.assertIn("hoga is required", result["blocked_reasons"])
        self.assertIn("lock_id is required", result["blocked_reasons"])

    def test_input_dicts_are_not_mutated(self) -> None:
        order = self._order()
        execution_preview = self._execution_preview()
        lock_preview = self._lock_preview()
        original_order = deepcopy(order)
        original_execution_preview = deepcopy(execution_preview)
        original_lock_preview = deepcopy(lock_preview)

        build_order_request_hash_preview(order, execution_preview, lock_preview)

        self.assertEqual(original_order, order)
        self.assertEqual(original_execution_preview, execution_preview)
        self.assertEqual(original_lock_preview, lock_preview)


if __name__ == "__main__":
    unittest.main()
