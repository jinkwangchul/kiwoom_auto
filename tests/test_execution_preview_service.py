# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import unittest

from execution_preview_service import preview_execution_for_order


class ExecutionPreviewServiceTest(unittest.TestCase):
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

    def test_normal_service_result(self) -> None:
        result = preview_execution_for_order(self._order(), self._guard())

        self.assertTrue(result["ok"])
        self.assertEqual("EXECUTION_PREVIEW_SERVICE", result["stage"])
        self.assertTrue(result["summary"]["ok"])
        self.assertTrue(result["summary"]["ready_for_execution_request"])
        self.assertTrue(result["approval_result"]["approved"])
        self.assertEqual("EXECUTION_CANDIDATE", result["approval_result"]["next_stage"])
        self.assertTrue(result["candidate_result"]["candidate"])
        self.assertEqual("QUEUE_PENDING", result["candidate_result"]["next_stage"])
        self.assertTrue(result["queue_pending_result"]["queue_pending"])
        self.assertEqual("QUEUE_WRITER_REQUIRED", result["queue_pending_result"]["next_stage"])
        self.assertTrue(result["queue_write_preview_result"]["write_preview"])
        self.assertEqual("QUEUE_WRITE_REQUIRED", result["queue_write_preview_result"]["next_stage"])

    def test_blocked_service_result(self) -> None:
        order = self._order()
        order["execution_enabled"] = False

        result = preview_execution_for_order(order, self._guard())

        self.assertFalse(result["ok"])
        self.assertFalse(result["summary"]["ok"])
        self.assertEqual("final_guard", result["summary"]["blocked_stage"])
        self.assertFalse(result["approval_result"]["approved"])
        self.assertEqual("preview_result", result["approval_result"]["approval_stage"])
        self.assertFalse(result["candidate_result"]["candidate"])
        self.assertEqual("approval", result["candidate_result"]["candidate_stage"])
        self.assertFalse(result["queue_pending_result"]["queue_pending"])
        self.assertEqual("candidate", result["queue_pending_result"]["queue_pending_stage"])
        self.assertFalse(result["queue_write_preview_result"]["write_preview"])
        self.assertEqual("queue_pending", result["queue_write_preview_result"]["write_stage"])

    def test_summary_is_included(self) -> None:
        result = preview_execution_for_order(self._order(), self._guard())

        self.assertIn("summary", result)
        self.assertEqual("ORDER_1", result["summary"]["order_id"])

    def test_pipeline_result_is_included(self) -> None:
        result = preview_execution_for_order(self._order(), self._guard())

        self.assertIn("pipeline_result", result)
        self.assertEqual("EXECUTION_PREVIEW_PIPELINE", result["pipeline_result"]["stage"])

    def test_approval_result_is_included(self) -> None:
        result = preview_execution_for_order(self._order(), self._guard())

        self.assertIn("approval_result", result)
        self.assertEqual("approved", result["approval_result"]["approval_stage"])

    def test_candidate_result_is_included(self) -> None:
        result = preview_execution_for_order(self._order(), self._guard())

        self.assertIn("candidate_result", result)
        self.assertEqual("candidate_created", result["candidate_result"]["candidate_stage"])

    def test_queue_pending_result_is_included(self) -> None:
        result = preview_execution_for_order(self._order(), self._guard())

        self.assertIn("queue_pending_result", result)
        self.assertEqual(
            "queue_pending_created",
            result["queue_pending_result"]["queue_pending_stage"],
        )

    def test_queue_write_preview_result_is_included(self) -> None:
        result = preview_execution_for_order(self._order(), self._guard())

        self.assertIn("queue_write_preview_result", result)
        self.assertEqual(
            "order_queued_record_preview_created",
            result["queue_write_preview_result"]["write_stage"],
        )

    def test_approval_reflects_preview_block_before_context_gate(self) -> None:
        guard = self._guard()
        guard["operator_confirmed"] = False

        result = preview_execution_for_order(self._order(), guard)

        self.assertFalse(result["approval_result"]["approved"])
        self.assertEqual("preview_result", result["approval_result"]["approval_stage"])

    def test_approval_reflects_real_trade_guard_context(self) -> None:
        guard = self._guard()
        guard["real_trade_guard_ok"] = False

        result = preview_execution_for_order(self._order(), guard)

        self.assertTrue(result["summary"]["ok"])
        self.assertFalse(result["approval_result"]["approved"])
        self.assertEqual("real_trade_guard", result["approval_result"]["approval_stage"])
        self.assertFalse(result["candidate_result"]["candidate"])
        self.assertIn(
            "approval_result.approved is not true",
            result["candidate_result"]["blocked_reasons"],
        )
        self.assertFalse(result["queue_pending_result"]["queue_pending"])
        self.assertIn(
            "candidate_result.candidate is not true",
            result["queue_pending_result"]["blocked_reasons"],
        )
        self.assertFalse(result["queue_write_preview_result"]["write_preview"])
        self.assertIn(
            "queue_pending_result.queue_pending is not true",
            result["queue_write_preview_result"]["blocked_reasons"],
        )

    def test_input_dicts_are_not_mutated(self) -> None:
        order = self._order()
        guard = self._guard()
        original_order = deepcopy(order)
        original_guard = deepcopy(guard)

        preview_execution_for_order(order, guard)

        self.assertEqual(original_order, order)
        self.assertEqual(original_guard, guard)


if __name__ == "__main__":
    unittest.main()
