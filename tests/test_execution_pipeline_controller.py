# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import unittest
from unittest import mock

import execution_pipeline_controller
from execution_pipeline_controller import run_execution_preview_pipeline


class ExecutionPipelineControllerTest(unittest.TestCase):
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

    def _assert_diagnostic_shape(self, diagnostics: list[dict]) -> None:
        self.assertTrue(diagnostics)
        for item in diagnostics:
            with self.subTest(stage=item.get("stage")):
                self.assertIn("stage", item)
                self.assertIn("ok", item)
                self.assertIn("reason", item)
                self.assertTrue("preview_keys" in item or "output_keys" in item)

    def test_normal_pipeline_passes(self) -> None:
        result = run_execution_preview_pipeline(self._order(), self._guard())

        self.assertTrue(result["ok"])
        self.assertEqual("EXECUTION_PREVIEW_PIPELINE", result["stage"])
        self.assertIsNone(result["blocked_stage"])
        self.assertFalse(result["pipeline"]["execution_preview"]["unresolved"])
        self.assertTrue(result["pipeline"]["final_guard"]["ok"])
        self.assertFalse(result["pipeline"]["lock_preview"]["unresolved"])
        self.assertFalse(result["pipeline"]["request_hash_preview"]["unresolved"])
        self.assertFalse(result["pipeline"]["execution_request_preview"]["unresolved"])
        self.assertIsNotNone(result["pipeline"]["execution_request_preview"]["execution_request"])

    def test_normal_pipeline_includes_all_stage_diagnostics(self) -> None:
        result = run_execution_preview_pipeline(self._order(), self._guard())

        diagnostics = result["stage_diagnostics"]
        self.assertEqual(
            [
                "hoga_mapper",
                "order_type_mapper",
                "guard",
                "lock_preview",
                "request_hash_preview",
                "execution_request_preview",
            ],
            [item["stage"] for item in diagnostics],
        )
        self._assert_diagnostic_shape(diagnostics)
        self.assertTrue(all(item["ok"] for item in diagnostics))

    def test_execution_preview_blocked(self) -> None:
        order = self._order()
        order["order_intent"]["hoga"] = "\ubbf8\ud655\uc815"

        result = run_execution_preview_pipeline(order, self._guard())

        self.assertFalse(result["ok"])
        self.assertEqual("execution_preview", result["blocked_stage"])
        self.assertTrue(result["pipeline"]["execution_preview"]["unresolved"])

    def test_blocked_pipeline_records_diagnostics_to_blocked_stage(self) -> None:
        order = self._order()
        order["order_intent"]["hoga"] = "\ubbf8\ud655\uc815"

        result = run_execution_preview_pipeline(order, self._guard())

        diagnostics = result["stage_diagnostics"]
        self.assertEqual("execution_preview", result["blocked_stage"])
        self.assertEqual("hoga_mapper", diagnostics[-1]["stage"])
        self.assertFalse(diagnostics[-1]["ok"])
        self.assertEqual(result["blocked_reason"], diagnostics[-1]["reason"])
        self.assertNotIn("guard", [item["stage"] for item in diagnostics])
        self._assert_diagnostic_shape(diagnostics)

    def test_missing_code_is_blocked_at_lock_preview(self) -> None:
        order = self._order()
        order.pop("code")

        result = run_execution_preview_pipeline(order, self._guard())

        self.assertFalse(result["ok"])
        self.assertEqual("lock_preview", result["blocked_stage"])
        self.assertIn("code is required", result["pipeline"]["lock_preview"]["blocked_reasons"])
        self.assertEqual("lock_preview", result["stage_diagnostics"][-1]["stage"])
        self.assertEqual("code is required", result["stage_diagnostics"][-1]["reason"])

    def test_missing_order_id_is_blocked_at_lock_preview(self) -> None:
        order = self._order()
        order.pop("id")

        result = run_execution_preview_pipeline(order, self._guard())

        self.assertFalse(result["ok"])
        self.assertEqual("lock_preview", result["blocked_stage"])
        self.assertIn("order_id is required", result["pipeline"]["lock_preview"]["blocked_reasons"])

    def test_missing_source_signal_id_is_blocked_at_lock_preview(self) -> None:
        order = self._order()
        order.pop("source_signal_id")

        result = run_execution_preview_pipeline(order, self._guard())

        self.assertFalse(result["ok"])
        self.assertEqual("lock_preview", result["blocked_stage"])
        self.assertIn("source_signal_id is required", result["pipeline"]["lock_preview"]["blocked_reasons"])

    def test_missing_order_type_intent_is_unresolved_at_execution_preview(self) -> None:
        order = self._order()
        order.pop("side")
        order["order_intent"].pop("side")

        result = run_execution_preview_pipeline(order, self._guard())

        self.assertFalse(result["ok"])
        self.assertEqual("execution_preview", result["blocked_stage"])
        self.assertTrue(result["pipeline"]["execution_preview"]["order_type_preview"]["unresolved"])
        self.assertEqual("order_type_mapper", result["stage_diagnostics"][-1]["stage"])
        self.assertFalse(result["stage_diagnostics"][-1]["ok"])

    def test_missing_hoga_intent_is_unresolved_at_execution_preview(self) -> None:
        order = self._order()
        order["order_intent"].pop("hoga")

        result = run_execution_preview_pipeline(order, self._guard())

        self.assertFalse(result["ok"])
        self.assertEqual("execution_preview", result["blocked_stage"])
        self.assertTrue(result["pipeline"]["execution_preview"]["hoga_preview"]["unresolved"])
        self.assertEqual("hoga_mapper", result["stage_diagnostics"][-1]["stage"])
        self.assertFalse(result["stage_diagnostics"][-1]["ok"])

    def test_real_trade_guard_failure_keeps_guard_blocked_stage_and_reason(self) -> None:
        guard = self._guard()
        guard["real_trade_enabled"] = False

        result = run_execution_preview_pipeline(self._order(), guard)

        self.assertFalse(result["ok"])
        self.assertEqual("final_guard", result["blocked_stage"])
        self.assertEqual("guard.real_trade_enabled is not true", result["blocked_reason"])
        self.assertEqual("guard", result["stage_diagnostics"][-1]["stage"])
        self.assertFalse(result["stage_diagnostics"][-1]["ok"])
        self.assertEqual("guard.real_trade_enabled is not true", result["stage_diagnostics"][-1]["reason"])

    def test_final_guard_blocked(self) -> None:
        order = self._order()
        order["execution_enabled"] = False

        result = run_execution_preview_pipeline(order, self._guard())

        self.assertFalse(result["ok"])
        self.assertEqual("final_guard", result["blocked_stage"])
        self.assertFalse(result["pipeline"]["final_guard"]["ok"])

    def test_lock_preview_blocked(self) -> None:
        order = self._order()
        order.pop("source_signal_id")

        result = run_execution_preview_pipeline(order, self._guard())

        self.assertFalse(result["ok"])
        self.assertEqual("lock_preview", result["blocked_stage"])
        self.assertTrue(result["pipeline"]["lock_preview"]["unresolved"])

    def test_request_hash_preview_blocked(self) -> None:
        order = self._order()
        order["price"] = ""

        result = run_execution_preview_pipeline(order, self._guard())

        self.assertFalse(result["ok"])
        self.assertEqual("request_hash_preview", result["blocked_stage"])
        self.assertTrue(result["pipeline"]["request_hash_preview"]["unresolved"])

    def test_execution_request_preview_blocked(self) -> None:
        request_hash_preview = {
            "ok": True,
            "stage": "REQUEST_HASH_PREVIEW",
            "request_hash": "",
            "hash_source": {},
            "unresolved": False,
            "blocked_reasons": [],
            "warnings": [],
        }

        with mock.patch.object(
            execution_pipeline_controller,
            "build_order_request_hash_preview",
            return_value=request_hash_preview,
        ):
            result = run_execution_preview_pipeline(self._order(), self._guard())

        self.assertFalse(result["ok"])
        self.assertEqual("execution_request_preview", result["blocked_stage"])
        self.assertTrue(result["pipeline"]["execution_request_preview"]["unresolved"])

    def test_input_dicts_are_not_mutated(self) -> None:
        order = self._order()
        guard = self._guard()
        original_order = deepcopy(order)
        original_guard = deepcopy(guard)

        run_execution_preview_pipeline(order, guard)

        self.assertEqual(original_order, order)
        self.assertEqual(original_guard, guard)


if __name__ == "__main__":
    unittest.main()
