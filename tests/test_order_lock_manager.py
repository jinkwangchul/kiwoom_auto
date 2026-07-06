# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import unittest

from order_lock_manager import build_order_lock_preview


class OrderLockManagerPreviewTest(unittest.TestCase):
    def _order(self) -> dict:
        return {
            "id": "ORDER_1",
            "source_signal_id": "SIG_1",
            "code": "003550",
            "side": "BUY",
        }

    def _execution_preview(self) -> dict:
        return {
            "stage": "EXECUTION_PREVIEW",
            "unresolved": False,
            "order_type_preview": {
                "order_type": "BUY",
                "unresolved": False,
            },
        }

    def test_builds_normal_lock_preview(self) -> None:
        result = build_order_lock_preview(self._order(), self._execution_preview())

        self.assertTrue(result["ok"])
        self.assertEqual("ORDER_LOCK_PREVIEW", result["stage"])
        self.assertEqual("003550:BUY:SIG_1", result["lock_key"])
        self.assertEqual("LOCK_PREVIEW_ORDER_1_003550_BUY_SIG_1", result["lock_id"])
        self.assertFalse(result["unresolved"])
        self.assertEqual([], result["blocked_reasons"])
        self.assertEqual([], result["warnings"])

    def test_missing_order_id_blocks(self) -> None:
        order = self._order()
        order.pop("id")

        result = build_order_lock_preview(order, self._execution_preview())

        self.assertFalse(result["ok"])
        self.assertTrue(result["unresolved"])
        self.assertIsNone(result["lock_key"])
        self.assertIsNone(result["lock_id"])
        self.assertIn("order_id is required", result["blocked_reasons"])

    def test_missing_source_signal_id_blocks(self) -> None:
        order = self._order()
        order.pop("source_signal_id")

        result = build_order_lock_preview(order, self._execution_preview())

        self.assertFalse(result["ok"])
        self.assertIn("source_signal_id is required", result["blocked_reasons"])

    def test_missing_code_blocks(self) -> None:
        order = self._order()
        order.pop("code")

        result = build_order_lock_preview(order, self._execution_preview())

        self.assertFalse(result["ok"])
        self.assertIn("code is required", result["blocked_reasons"])

    def test_missing_side_or_order_type_blocks(self) -> None:
        order = self._order()
        order.pop("side")
        execution_preview = self._execution_preview()
        execution_preview["order_type_preview"]["order_type"] = None

        result = build_order_lock_preview(order, execution_preview)

        self.assertFalse(result["ok"])
        self.assertIn("side/order_type is required", result["blocked_reasons"])

    def test_uses_order_type_preview_when_order_side_is_missing(self) -> None:
        order = self._order()
        order.pop("side")

        result = build_order_lock_preview(order, self._execution_preview())

        self.assertTrue(result["ok"])
        self.assertEqual("BUY", result["side_or_order_type"])
        self.assertEqual("003550:BUY:SIG_1", result["lock_key"])

    def test_input_dicts_are_not_mutated(self) -> None:
        order = self._order()
        execution_preview = self._execution_preview()
        original_order = deepcopy(order)
        original_preview = deepcopy(execution_preview)

        build_order_lock_preview(order, execution_preview)

        self.assertEqual(original_order, order)
        self.assertEqual(original_preview, execution_preview)


if __name__ == "__main__":
    unittest.main()
