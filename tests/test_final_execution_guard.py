# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import unittest

from final_execution_guard import evaluate_final_execution_guard


class FinalExecutionGuardTest(unittest.TestCase):
    def _order(self) -> dict:
        return {
            "id": "ORDER_1",
            "status": "REAL_READY",
            "execution_enabled": True,
        }

    def _guard(self) -> dict:
        return {
            "operator_confirmed": True,
            "real_trade_enabled": True,
        }

    def _preview(self) -> dict:
        return {
            "stage": "EXECUTION_PREVIEW",
            "unresolved": False,
            "hoga_preview": {
                "unresolved": False,
                "hoga": "MARKET",
            },
            "order_type_preview": {
                "unresolved": False,
                "order_type": "BUY",
            },
        }

    def test_pass_case(self) -> None:
        result = evaluate_final_execution_guard(self._order(), self._guard(), self._preview())

        self.assertTrue(result["ok"])
        self.assertEqual([], result["blocked_reasons"])
        self.assertEqual([], result["warnings"])
        self.assertEqual("FINAL_EXECUTION_GUARD", result["stage"])

    def test_status_mismatch_blocks(self) -> None:
        order = self._order()
        order["status"] = "APPROVED"

        result = evaluate_final_execution_guard(order, self._guard(), self._preview())

        self.assertFalse(result["ok"])
        self.assertIn("order.status is not REAL_READY", result["blocked_reasons"])

    def test_execution_enabled_false_blocks(self) -> None:
        order = self._order()
        order["execution_enabled"] = False

        result = evaluate_final_execution_guard(order, self._guard(), self._preview())

        self.assertFalse(result["ok"])
        self.assertIn("order.execution_enabled is not true", result["blocked_reasons"])

    def test_operator_confirmed_false_blocks(self) -> None:
        guard = self._guard()
        guard["operator_confirmed"] = False

        result = evaluate_final_execution_guard(self._order(), guard, self._preview())

        self.assertFalse(result["ok"])
        self.assertIn("guard.operator_confirmed is not true", result["blocked_reasons"])

    def test_real_trade_enabled_false_blocks(self) -> None:
        guard = self._guard()
        guard["real_trade_enabled"] = False

        result = evaluate_final_execution_guard(self._order(), guard, self._preview())

        self.assertFalse(result["ok"])
        self.assertIn("guard.real_trade_enabled is not true", result["blocked_reasons"])

    def test_hoga_unresolved_blocks(self) -> None:
        preview = self._preview()
        preview["hoga_preview"]["unresolved"] = True

        result = evaluate_final_execution_guard(self._order(), self._guard(), preview)

        self.assertFalse(result["ok"])
        self.assertIn("hoga_preview is unresolved", result["blocked_reasons"])

    def test_order_type_unresolved_blocks(self) -> None:
        preview = self._preview()
        preview["order_type_preview"]["unresolved"] = True

        result = evaluate_final_execution_guard(self._order(), self._guard(), preview)

        self.assertFalse(result["ok"])
        self.assertIn("order_type_preview is unresolved", result["blocked_reasons"])

    def test_execution_preview_unresolved_blocks(self) -> None:
        preview = self._preview()
        preview["unresolved"] = True

        result = evaluate_final_execution_guard(self._order(), self._guard(), preview)

        self.assertFalse(result["ok"])
        self.assertIn("execution_preview is unresolved", result["blocked_reasons"])

    def test_input_values_are_not_mutated(self) -> None:
        order = self._order()
        guard = self._guard()
        preview = self._preview()
        original_order = deepcopy(order)
        original_guard = deepcopy(guard)
        original_preview = deepcopy(preview)

        evaluate_final_execution_guard(order, guard, preview)

        self.assertEqual(original_order, order)
        self.assertEqual(original_guard, guard)
        self.assertEqual(original_preview, preview)


if __name__ == "__main__":
    unittest.main()
