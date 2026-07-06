# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import unittest
from unittest import mock

from execution_approval_service import evaluate_execution_approval


class ExecutionApprovalServiceTest(unittest.TestCase):
    def _preview_result(self) -> dict:
        return {
            "ok": True,
            "ready_for_execution_request_preview": True,
            "blocked_stage": None,
            "blocked_reasons": [],
            "warnings": [],
            "order_id": "ORDER_1",
            "execution_id": "EXEC_PREVIEW_ORDER_1",
            "request_hash": "a" * 64,
        }

    def _context(self) -> dict:
        return {
            "execution_enabled": True,
            "operator_confirmed": True,
            "real_trade_guard_ok": True,
        }

    def test_preview_result_not_ok_is_blocked(self) -> None:
        preview_result = self._preview_result()
        preview_result["ok"] = False

        result = evaluate_execution_approval(preview_result, self._context())

        self.assertFalse(result["approved"])
        self.assertEqual("preview_result", result["approval_stage"])
        self.assertEqual("BLOCKED", result["next_stage"])
        self.assertIn("preview result is not ok", result["blocked_reasons"])

    def test_ready_for_execution_request_preview_false_is_blocked(self) -> None:
        preview_result = self._preview_result()
        preview_result["ready_for_execution_request_preview"] = False

        result = evaluate_execution_approval(preview_result, self._context())

        self.assertFalse(result["approved"])
        self.assertEqual("preview_result", result["approval_stage"])
        self.assertIn(
            "preview result is not ready for execution request preview",
            result["blocked_reasons"],
        )

    def test_blocked_stage_is_blocked(self) -> None:
        preview_result = self._preview_result()
        preview_result["blocked_stage"] = "final_guard"

        result = evaluate_execution_approval(preview_result, self._context())

        self.assertFalse(result["approved"])
        self.assertEqual("preview_result", result["approval_stage"])
        self.assertIn("preview result blocked_stage is set: final_guard", result["blocked_reasons"])

    def test_blocked_reasons_are_blocked(self) -> None:
        preview_result = self._preview_result()
        preview_result["blocked_reasons"] = ["hoga unresolved"]

        result = evaluate_execution_approval(preview_result, self._context())

        self.assertFalse(result["approved"])
        self.assertEqual("preview_result", result["approval_stage"])
        self.assertIn(
            "preview result has blocked reasons: hoga unresolved",
            result["blocked_reasons"],
        )

    def test_execution_enabled_false_is_blocked(self) -> None:
        context = self._context()
        context["execution_enabled"] = False

        result = evaluate_execution_approval(self._preview_result(), context)

        self.assertFalse(result["approved"])
        self.assertEqual("execution_enabled", result["approval_stage"])
        self.assertIn("context.execution_enabled is not true", result["blocked_reasons"])

    def test_operator_confirmed_false_is_blocked(self) -> None:
        context = self._context()
        context["operator_confirmed"] = False

        result = evaluate_execution_approval(self._preview_result(), context)

        self.assertFalse(result["approved"])
        self.assertEqual("operator_confirmed", result["approval_stage"])
        self.assertIn("context.operator_confirmed is not true", result["blocked_reasons"])

    def test_real_trade_guard_ok_false_is_blocked(self) -> None:
        context = self._context()
        context["real_trade_guard_ok"] = False

        result = evaluate_execution_approval(self._preview_result(), context)

        self.assertFalse(result["approved"])
        self.assertEqual("real_trade_guard", result["approval_stage"])
        self.assertIn("context.real_trade_guard_ok is not true", result["blocked_reasons"])

    def test_all_conditions_met_is_execution_candidate(self) -> None:
        result = evaluate_execution_approval(self._preview_result(), self._context())

        self.assertTrue(result["approved"])
        self.assertEqual("approved", result["approval_stage"])
        self.assertEqual([], result["blocked_reasons"])
        self.assertEqual("EXECUTION_CANDIDATE", result["next_stage"])

    def test_approved_result_does_not_call_send_order_or_write_runtime(self) -> None:
        with (
            mock.patch("pathlib.Path.write_text") as write_text,
            mock.patch("builtins.open", mock.mock_open()) as open_mock,
            mock.patch("kiwoom_order_adapter.send_order_stub") as send_order_stub,
        ):
            result = evaluate_execution_approval(self._preview_result(), self._context())

        self.assertTrue(result["approved"])
        write_text.assert_not_called()
        open_mock.assert_not_called()
        send_order_stub.assert_not_called()

    def test_input_dicts_are_not_mutated(self) -> None:
        preview_result = self._preview_result()
        context = self._context()
        original_preview = deepcopy(preview_result)
        original_context = deepcopy(context)

        evaluate_execution_approval(preview_result, context)

        self.assertEqual(original_preview, preview_result)
        self.assertEqual(original_context, context)


if __name__ == "__main__":
    unittest.main()
