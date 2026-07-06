# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import unittest

from execution_controller import build_execution_preview
from final_execution_guard import evaluate_final_execution_guard
from order_execution_request import build_execution_request_preview
from order_hoga_mapper import map_order_hoga_preview
from order_lock_manager import build_order_lock_preview
from order_request_hash import build_order_request_hash_preview
from order_type_mapper import map_order_type_preview


class ExecutionPreviewChainTest(unittest.TestCase):
    def _order(self) -> dict:
        return {
            "id": "ORDER_1",
            "status": "REAL_READY",
            "source_signal_id": "SIG_1",
            "code": "003550",
            "side": "BUY",
            "quantity": 10,
            "price": 85000,
            "execution_enabled": True,
            "order_intent": {
                "side": "BUY",
                "hoga": "\uc2dc\uc7a5\uac00",
            },
        }

    def _guard(self) -> dict:
        return {
            "operator_confirmed": True,
            "real_trade_enabled": True,
            "account_no": "12345678",
        }

    def _run_chain(self, order: dict | None = None, guard: dict | None = None) -> dict:
        target_order = self._order() if order is None else order
        target_guard = self._guard() if guard is None else guard

        hoga_preview = map_order_hoga_preview(target_order)
        order_type_preview = map_order_type_preview(target_order)
        execution_preview = build_execution_preview(target_order, target_guard)
        final_guard_result = evaluate_final_execution_guard(
            target_order,
            target_guard,
            execution_preview,
        )
        lock_preview = build_order_lock_preview(target_order, execution_preview)
        request_hash_preview = build_order_request_hash_preview(
            target_order,
            execution_preview,
            lock_preview,
        )
        execution_request_preview = build_execution_request_preview(
            target_order,
            target_guard,
            execution_preview,
            final_guard_result,
            lock_preview,
            request_hash_preview,
        )

        return {
            "hoga_preview": hoga_preview,
            "order_type_preview": order_type_preview,
            "execution_preview": execution_preview,
            "final_guard_result": final_guard_result,
            "lock_preview": lock_preview,
            "request_hash_preview": request_hash_preview,
            "execution_request_preview": execution_request_preview,
        }

    def test_normal_chain_passes(self) -> None:
        result = self._run_chain()

        self.assertTrue(result["hoga_preview"]["ok"])
        self.assertFalse(result["hoga_preview"]["unresolved"])
        self.assertEqual("MARKET", result["hoga_preview"]["hoga"])
        self.assertTrue(result["order_type_preview"]["ok"])
        self.assertFalse(result["order_type_preview"]["unresolved"])
        self.assertEqual("BUY", result["order_type_preview"]["order_type"])
        self.assertTrue(result["execution_preview"]["ok"])
        self.assertFalse(result["execution_preview"]["unresolved"])
        self.assertTrue(result["final_guard_result"]["ok"])
        self.assertTrue(result["lock_preview"]["ok"])
        self.assertFalse(result["lock_preview"]["unresolved"])
        self.assertTrue(result["request_hash_preview"]["ok"])
        self.assertFalse(result["request_hash_preview"]["unresolved"])
        self.assertTrue(result["execution_request_preview"]["ok"])
        self.assertFalse(result["execution_request_preview"]["unresolved"])
        self.assertIsNotNone(result["execution_request_preview"]["execution_request"])

    def test_execution_enabled_false_blocks_at_final_guard(self) -> None:
        order = self._order()
        order["execution_enabled"] = False

        result = self._run_chain(order=order)

        self.assertFalse(result["final_guard_result"]["ok"])
        self.assertIn(
            "order.execution_enabled is not true",
            result["final_guard_result"]["blocked_reasons"],
        )
        self.assertFalse(result["execution_request_preview"]["ok"])
        self.assertIn(
            "final_guard_result is not ok",
            result["execution_request_preview"]["blocked_reasons"],
        )

    def test_hoga_unresolved_blocks_chain(self) -> None:
        order = self._order()
        order["order_intent"]["hoga"] = "\ubbf8\ud655\uc815"

        result = self._run_chain(order=order)

        self.assertTrue(result["hoga_preview"]["unresolved"])
        self.assertTrue(result["execution_preview"]["unresolved"])
        self.assertFalse(result["final_guard_result"]["ok"])
        self.assertIn("hoga_preview is unresolved", result["final_guard_result"]["blocked_reasons"])
        self.assertFalse(result["request_hash_preview"]["ok"])
        self.assertIn("hoga is required", result["request_hash_preview"]["blocked_reasons"])
        self.assertFalse(result["execution_request_preview"]["ok"])

    def test_missing_request_hash_blocks_execution_request(self) -> None:
        result = self._run_chain()
        request_hash_preview = deepcopy(result["request_hash_preview"])
        request_hash_preview["request_hash"] = ""

        execution_request_preview = build_execution_request_preview(
            self._order(),
            self._guard(),
            result["execution_preview"],
            result["final_guard_result"],
            result["lock_preview"],
            request_hash_preview,
        )

        self.assertFalse(execution_request_preview["ok"])
        self.assertTrue(execution_request_preview["unresolved"])
        self.assertIsNone(execution_request_preview["execution_request"])
        self.assertIn("request_hash is required", execution_request_preview["blocked_reasons"])

    def test_input_dicts_are_not_mutated(self) -> None:
        order = self._order()
        guard = self._guard()
        original_order = deepcopy(order)
        original_guard = deepcopy(guard)

        self._run_chain(order=order, guard=guard)

        self.assertEqual(original_order, order)
        self.assertEqual(original_guard, guard)


if __name__ == "__main__":
    unittest.main()
