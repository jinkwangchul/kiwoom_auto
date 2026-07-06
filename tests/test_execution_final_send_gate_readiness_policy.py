# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import unittest
from unittest import mock

from execution_final_send_gate_readiness_policy import (
    STATUS_READY,
    evaluate_execution_final_send_gate_readiness,
)


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ExecutionFinalSendGateReadinessPolicyTest(unittest.TestCase):
    def _request_preview(self, **overrides: object) -> dict[str, object]:
        preview = {
            "order_id": "ORDER_FINAL_1",
            "source_signal_id": "SIGNAL_FINAL_1",
            "execution_id": "EXEC_FINAL_1",
            "request_hash": "HASH_FINAL_1",
            "lock_id": "LOCK_FINAL_1",
            "account_no": "12345678",
            "side": "BUY",
            "code": "003550",
            "quantity": 10,
            "price": 100,
            "hoga": "LIMIT",
        }
        preview.update(overrides)
        return preview

    def _adapter_preview(self, **overrides: object) -> dict[str, object]:
        preview = {
            "adapter_preview_ok": True,
            "adapter_stage": "kiwoom_send_order_request_preview_created",
            "next_stage": "FINAL_SEND_GATE_REQUIRED",
            "preview_only": True,
            "no_send": True,
            "send_order_called": False,
            "send_order_request_preview": self._request_preview(),
            "blocked_reasons": [],
            "warnings": [],
        }
        preview.update(overrides)
        return preview

    def _record(self, **overrides: object) -> dict[str, object]:
        record = {
            "id": "ORDER_QUEUED_ORDER_FINAL_1",
            "status": "ORDER_QUEUED",
            "order_id": "ORDER_FINAL_1",
            "source_signal_id": "SIGNAL_FINAL_1",
            "execution_id": "EXEC_FINAL_1",
            "request_hash": "HASH_FINAL_1",
            "lock_id": "LOCK_FINAL_1",
            "send_order_called": False,
            "execution_enabled": False,
        }
        record.update(overrides)
        return record

    def _identity(self, **overrides: object) -> dict[str, object]:
        identity = {
            "order_id": "ORDER_FINAL_1",
            "source_signal_id": "SIGNAL_FINAL_1",
            "execution_id": "EXEC_FINAL_1",
            "request_hash": "HASH_FINAL_1",
            "lock_id": "LOCK_FINAL_1",
        }
        identity.update(overrides)
        return identity

    def _adapter_result(self, **overrides: object) -> dict[str, object]:
        result = {
            "adapter_type": "EXECUTION_QUEUE_REVIEW_TO_SEND_ORDER_PREVIEW_ADAPTER",
            "status": "READY_FOR_FINAL_SEND_GATE",
            "preview_only": True,
            "queue_write": False,
            "runtime_write": False,
            "send_order_called": False,
            "final_send_gate_called": False,
            "adapter_preview_result": self._adapter_preview(),
            "order_queued_record": self._record(),
            "identity": self._identity(),
            "issues": [],
            "warnings": [],
        }
        result.update(overrides)
        return result

    def _guard(self, **overrides: object) -> dict[str, object]:
        guard = {
            "real_trade_enabled": True,
            "kiwoom_logged_in": True,
            "account_selected": True,
            "account_no": "12345678",
            "operator_confirmed": True,
        }
        guard.update(overrides)
        return guard

    def _context(self, **overrides: object) -> dict[str, object]:
        context = {"manual_final_send_confirmed": True}
        context.update(overrides)
        return context

    def _evaluate(
        self,
        adapter_result: object | None = None,
        guard: object | None = None,
        context: object | None = None,
    ) -> dict[str, object]:
        return evaluate_execution_final_send_gate_readiness(
            self._adapter_result() if adapter_result is None else adapter_result,
            self._guard() if guard is None else guard,
            self._context() if context is None else context,
        )

    def test_all_valid_ready_for_final_send_gate(self) -> None:
        result = self._evaluate()

        self.assertEqual(STATUS_READY, result["status"])
        self.assertTrue(result["final_send_gate_allowed"])
        self.assertTrue(result["preview_only"])
        self.assertFalse(result["queue_write"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["send_order_called"])
        self.assertFalse(result["final_send_gate_called"])
        self.assertEqual([], result["issues"])

    def test_adapter_result_blocked_invalid_and_malformed(self) -> None:
        blocked = self._evaluate(adapter_result=self._adapter_result(status="BLOCKED", issues=["blocked"]))
        invalid = self._evaluate(adapter_result=self._adapter_result(status="INVALID", issues=["invalid"]))
        malformed = self._evaluate(adapter_result="malformed")

        self.assertEqual("BLOCKED", blocked["status"])
        self.assertFalse(blocked["final_send_gate_allowed"])
        self.assertEqual("INVALID", invalid["status"])
        self.assertEqual("INVALID", malformed["status"])
        self.assertIn("MALFORMED_SEND_ORDER_PREVIEW_ADAPTER_RESULT", malformed["issues"])

    def test_adapter_preview_ok_false_blocks(self) -> None:
        adapter_preview = self._adapter_preview(adapter_preview_ok=False)
        result = self._evaluate(adapter_result=self._adapter_result(adapter_preview_result=adapter_preview))

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("ADAPTER_PREVIEW_OK_NOT_TRUE", result["issues"])

    def test_adapter_preview_next_stage_no_send_and_called_flags_block(self) -> None:
        cases = [
            (self._adapter_preview(next_stage="OTHER"), "ADAPTER_PREVIEW_NEXT_STAGE_NOT_FINAL_SEND_GATE_REQUIRED"),
            (self._adapter_preview(no_send=False), "ADAPTER_PREVIEW_NO_SEND_NOT_TRUE"),
            (self._adapter_preview(send_order_called=True), "ADAPTER_PREVIEW_SEND_ORDER_CALLED_NOT_FALSE"),
        ]
        for adapter_preview, issue in cases:
            with self.subTest(issue=issue):
                result = self._evaluate(adapter_result=self._adapter_result(adapter_preview_result=adapter_preview))

                self.assertEqual("BLOCKED", result["status"])
                self.assertIn(issue, result["issues"])

    def test_no_send_order_request_preview_blocks(self) -> None:
        adapter_preview = self._adapter_preview(send_order_request_preview=None)
        result = self._evaluate(adapter_result=self._adapter_result(adapter_preview_result=adapter_preview))

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("SEND_ORDER_REQUEST_PREVIEW_REQUIRED", result["issues"])

    def test_identity_mismatch_blocks(self) -> None:
        for field, value in (
            ("order_id", "OTHER_ORDER"),
            ("source_signal_id", "OTHER_SIGNAL"),
            ("execution_id", "OTHER_EXEC"),
            ("request_hash", "OTHER_HASH"),
            ("lock_id", "OTHER_LOCK"),
        ):
            with self.subTest(field=field):
                adapter_preview = self._adapter_preview(send_order_request_preview=self._request_preview(**{field: value}))
                result = self._evaluate(adapter_result=self._adapter_result(adapter_preview_result=adapter_preview))

                self.assertEqual("BLOCKED", result["status"])
                self.assertIn(f"IDENTITY_MISMATCH_{field.upper()}", result["issues"])
                self.assertEqual("FAIL", result["identity_checks"][field]["result"])

    def test_guard_real_trade_enabled_false_blocks(self) -> None:
        result = self._evaluate(guard=self._guard(real_trade_enabled=False))

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("GUARD_REAL_TRADE_ENABLED_NOT_TRUE", result["issues"])

    def test_guard_kiwoom_logged_in_false_blocks(self) -> None:
        result = self._evaluate(guard=self._guard(kiwoom_logged_in=False))

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("GUARD_KIWOOM_LOGGED_IN_NOT_TRUE", result["issues"])

    def test_guard_account_selected_false_blocks(self) -> None:
        result = self._evaluate(guard=self._guard(account_selected=False))

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("GUARD_ACCOUNT_SELECTED_NOT_TRUE", result["issues"])

    def test_guard_account_no_missing_blocks(self) -> None:
        result = self._evaluate(guard=self._guard(account_no=""))

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("GUARD_ACCOUNT_NO_REQUIRED", result["issues"])

    def test_guard_operator_confirmed_false_blocks(self) -> None:
        result = self._evaluate(guard=self._guard(operator_confirmed=False))

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("GUARD_OPERATOR_CONFIRMED_NOT_TRUE", result["issues"])

    def test_final_send_confirmation_missing_blocks(self) -> None:
        result = self._evaluate(context={})

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("FINAL_SEND_CONFIRMATION_REQUIRED", result["issues"])

    def test_operator_confirmed_for_final_send_allows_confirmation(self) -> None:
        result = self._evaluate(context={"operator_confirmed_for_final_send": True})

        self.assertEqual(STATUS_READY, result["status"])
        self.assertTrue(result["required_confirmations"]["operator_confirmed_for_final_send"])

    def test_final_send_gate_send_order_and_queue_commit_are_not_called(self) -> None:
        with (
            mock.patch("final_send_gate_service.evaluate_final_send_gate") as final_gate,
            mock.patch("send_order_entrypoint.execute_send_order") as send_order,
            mock.patch("execution_queue_commit_service.commit_execution_queue_manually") as queue_commit,
        ):
            result = self._evaluate()

        self.assertEqual(STATUS_READY, result["status"])
        final_gate.assert_not_called()
        send_order.assert_not_called()
        queue_commit.assert_not_called()

    def test_inputs_are_not_mutated(self) -> None:
        adapter_result = self._adapter_result()
        guard = self._guard()
        context = self._context()
        originals = (deepcopy(adapter_result), deepcopy(guard), deepcopy(context))

        evaluate_execution_final_send_gate_readiness(adapter_result, guard, context)

        self.assertEqual(originals[0], adapter_result)
        self.assertEqual(originals[1], guard)
        self.assertEqual(originals[2], context)

    def test_order_queue_runtime_and_rules_hash_unchanged(self) -> None:
        runtime_paths = [
            ROOT / "runtime" / "order_queue.json",
            ROOT / "runtime" / "order_executions.json",
            ROOT / "runtime" / "order_locks.json",
        ]
        rules_paths = list((ROOT / "routines").glob("**/rules.json"))
        before_runtime = {str(path): _sha256(path) for path in runtime_paths}
        before_rules = {str(path): _sha256(path) for path in rules_paths}

        self._evaluate()
        self._evaluate(adapter_result=self._adapter_result(status="BLOCKED"))

        self.assertEqual(before_runtime, {str(path): _sha256(path) for path in runtime_paths})
        self.assertEqual(before_rules, {str(path): _sha256(path) for path in rules_paths})


if __name__ == "__main__":
    unittest.main()
