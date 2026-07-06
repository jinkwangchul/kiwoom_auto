# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import unittest

from execution_pipeline_controller import run_execution_preview_pipeline
from execution_pipeline_summary import summarize_execution_preview_pipeline


class ExecutionPipelineSummaryTest(unittest.TestCase):
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

    def test_normal_summary(self) -> None:
        pipeline_result = run_execution_preview_pipeline(self._order(), self._guard())

        summary = summarize_execution_preview_pipeline(pipeline_result)

        self.assertTrue(summary["ok"])
        self.assertIsNone(summary["blocked_stage"])
        self.assertTrue(summary["ready_for_execution_request"])
        self.assertEqual("ORDER_1", summary["order_id"])
        self.assertEqual([], summary["blocked_reasons"])

    def test_blocked_stage_is_reflected(self) -> None:
        order = self._order()
        order["order_intent"]["hoga"] = "\ubbf8\ud655\uc815"
        pipeline_result = run_execution_preview_pipeline(order, self._guard())

        summary = summarize_execution_preview_pipeline(pipeline_result)

        self.assertFalse(summary["ok"])
        self.assertEqual("execution_preview", summary["blocked_stage"])
        self.assertFalse(summary["ready_for_execution_request"])

    def test_stage_diagnostics_are_reflected(self) -> None:
        pipeline_result = run_execution_preview_pipeline(self._order(), self._guard())

        summary = summarize_execution_preview_pipeline(pipeline_result)

        self.assertEqual(pipeline_result["stage_diagnostics"], summary["stage_diagnostics"])
        self.assertIsNone(summary["blocked_reason"])

    def test_blocked_reason_is_reflected(self) -> None:
        order = self._order()
        order["order_intent"]["hoga"] = "\ubbf8\ud655\uc815"
        pipeline_result = run_execution_preview_pipeline(order, self._guard())

        summary = summarize_execution_preview_pipeline(pipeline_result)

        self.assertEqual(pipeline_result["blocked_reason"], summary["blocked_reason"])

    def test_final_guard_blocked_reasons_are_collected(self) -> None:
        order = self._order()
        order["execution_enabled"] = False
        pipeline_result = run_execution_preview_pipeline(order, self._guard())

        summary = summarize_execution_preview_pipeline(pipeline_result)

        self.assertIn("order.execution_enabled is not true", summary["blocked_reasons"])

    def test_guard_failure_summary_keeps_diagnostic_reason(self) -> None:
        guard = self._guard()
        guard["real_trade_enabled"] = False
        pipeline_result = run_execution_preview_pipeline(self._order(), guard)

        summary = summarize_execution_preview_pipeline(pipeline_result)

        self.assertEqual("final_guard", summary["blocked_stage"])
        self.assertEqual("guard.real_trade_enabled is not true", summary["blocked_reason"])
        self.assertEqual("guard", summary["stage_diagnostics"][-1]["stage"])
        self.assertFalse(summary["stage_diagnostics"][-1]["ok"])
        self.assertEqual(
            "guard.real_trade_enabled is not true",
            summary["stage_diagnostics"][-1]["reason"],
        )

    def test_mapper_unresolved_summary_keeps_diagnostics_without_order_available_wording(self) -> None:
        order = self._order()
        order["order_intent"]["hoga"] = "\ubbf8\ud655\uc815"
        pipeline_result = run_execution_preview_pipeline(order, self._guard())

        summary = summarize_execution_preview_pipeline(pipeline_result)

        self.assertFalse(summary["ok"])
        self.assertEqual("execution_preview", summary["blocked_stage"])
        self.assertEqual("hoga_mapper", summary["stage_diagnostics"][-1]["stage"])
        self.assertFalse(summary["stage_diagnostics"][-1]["ok"])
        self.assertNotIn("\uc8fc\ubb38 \uac00\ub2a5", str(summary))
        self.assertNotIn("\uc2e4\uc8fc\ubb38 \uac00\ub2a5", str(summary))
        self.assertNotIn("\uc804\uc1a1 \uac00\ub2a5", str(summary))

    def test_request_hash_is_extracted(self) -> None:
        pipeline_result = run_execution_preview_pipeline(self._order(), self._guard())
        expected_hash = pipeline_result["pipeline"]["request_hash_preview"]["request_hash"]

        summary = summarize_execution_preview_pipeline(pipeline_result)

        self.assertEqual(expected_hash, summary["request_hash"])

    def test_execution_id_is_extracted(self) -> None:
        pipeline_result = run_execution_preview_pipeline(self._order(), self._guard())
        expected_id = pipeline_result["pipeline"]["execution_request_preview"]["execution_request"]["execution_id"]

        summary = summarize_execution_preview_pipeline(pipeline_result)

        self.assertEqual(expected_id, summary["execution_id"])

    def test_input_dict_is_not_mutated(self) -> None:
        pipeline_result = run_execution_preview_pipeline(self._order(), self._guard())
        original = deepcopy(pipeline_result)

        summarize_execution_preview_pipeline(pipeline_result)

        self.assertEqual(original, pipeline_result)


if __name__ == "__main__":
    unittest.main()
