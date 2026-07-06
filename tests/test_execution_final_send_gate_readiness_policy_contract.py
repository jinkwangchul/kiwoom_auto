# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
from pathlib import Path
import unittest
from unittest import mock

from execution_final_send_gate_readiness_policy import evaluate_execution_final_send_gate_readiness


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ExecutionFinalSendGateReadinessPolicyContractTest(unittest.TestCase):
    def _request_preview(self, **overrides: object) -> dict[str, object]:
        preview = {
            "order_id": "ORDER_CONTRACT_FINAL",
            "source_signal_id": "SIGNAL_CONTRACT_FINAL",
            "execution_id": "EXEC_CONTRACT_FINAL",
            "request_hash": "HASH_CONTRACT_FINAL",
            "lock_id": "LOCK_CONTRACT_FINAL",
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
            "id": "ORDER_QUEUED_CONTRACT_FINAL",
            "status": "ORDER_QUEUED",
            "order_id": "ORDER_CONTRACT_FINAL",
            "source_signal_id": "SIGNAL_CONTRACT_FINAL",
            "execution_id": "EXEC_CONTRACT_FINAL",
            "request_hash": "HASH_CONTRACT_FINAL",
            "lock_id": "LOCK_CONTRACT_FINAL",
            "send_order_called": False,
            "execution_enabled": False,
        }
        record.update(overrides)
        return record

    def _identity(self, **overrides: object) -> dict[str, object]:
        identity = {
            "order_id": "ORDER_CONTRACT_FINAL",
            "source_signal_id": "SIGNAL_CONTRACT_FINAL",
            "execution_id": "EXEC_CONTRACT_FINAL",
            "request_hash": "HASH_CONTRACT_FINAL",
            "lock_id": "LOCK_CONTRACT_FINAL",
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

    def assert_not_allowed(self, result: dict[str, object]) -> None:
        self.assertNotEqual("READY_FOR_FINAL_SEND_GATE", result["status"])
        self.assertFalse(result["final_send_gate_allowed"])
        self.assertTrue(result["preview_only"])
        self.assertFalse(result["queue_write"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["send_order_called"])
        self.assertFalse(result["final_send_gate_called"])

    def test_all_valid_ready_and_allowed(self) -> None:
        result = self._evaluate()

        self.assertEqual("READY_FOR_FINAL_SEND_GATE", result["status"])
        self.assertTrue(result["final_send_gate_allowed"])
        self.assertTrue(result["preview_only"])
        self.assertFalse(result["queue_write"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["send_order_called"])
        self.assertFalse(result["final_send_gate_called"])

    def test_final_send_gate_allowed_only_when_ready(self) -> None:
        cases = [
            self._adapter_result(status="BLOCKED", issues=["blocked"]),
            self._adapter_result(status="INVALID", issues=["invalid"]),
            self._adapter_result(adapter_preview_result=self._adapter_preview(adapter_preview_ok=False)),
            self._adapter_result(adapter_preview_result=self._adapter_preview(send_order_request_preview=None)),
        ]
        for adapter_result in cases:
            with self.subTest(adapter_result=adapter_result):
                self.assert_not_allowed(self._evaluate(adapter_result=adapter_result))

    def test_adapter_result_blocked_invalid_and_malformed(self) -> None:
        blocked = self._evaluate(adapter_result=self._adapter_result(status="BLOCKED", issues=["blocked"]))
        invalid = self._evaluate(adapter_result=self._adapter_result(status="INVALID", issues=["invalid"]))
        malformed = self._evaluate(adapter_result="malformed")

        self.assertEqual("BLOCKED", blocked["status"])
        self.assertEqual("INVALID", invalid["status"])
        self.assertEqual("INVALID", malformed["status"])
        self.assert_not_allowed(blocked)
        self.assert_not_allowed(invalid)
        self.assert_not_allowed(malformed)

    def test_adapter_preview_ok_false_and_missing_request_preview_block(self) -> None:
        adapter_bad = self._adapter_result(adapter_preview_result=self._adapter_preview(adapter_preview_ok=False))
        request_missing = self._adapter_result(adapter_preview_result=self._adapter_preview(send_order_request_preview=None))

        adapter_result = self._evaluate(adapter_result=adapter_bad)
        request_result = self._evaluate(adapter_result=request_missing)

        self.assertEqual("BLOCKED", adapter_result["status"])
        self.assertIn("ADAPTER_PREVIEW_OK_NOT_TRUE", adapter_result["issues"])
        self.assertEqual("BLOCKED", request_result["status"])
        self.assertIn("SEND_ORDER_REQUEST_PREVIEW_REQUIRED", request_result["issues"])

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
                self.assertFalse(result["final_send_gate_allowed"])

    def test_guard_missing_or_false_blocks(self) -> None:
        cases = [
            ({}, "CURRENT_GUARD_REQUIRED"),
            (self._guard(real_trade_enabled=False), "GUARD_REAL_TRADE_ENABLED_NOT_TRUE"),
            (self._guard(kiwoom_logged_in=False), "GUARD_KIWOOM_LOGGED_IN_NOT_TRUE"),
            (self._guard(account_selected=False), "GUARD_ACCOUNT_SELECTED_NOT_TRUE"),
            (self._guard(account_no=""), "GUARD_ACCOUNT_NO_REQUIRED"),
            (self._guard(operator_confirmed=False), "GUARD_OPERATOR_CONFIRMED_NOT_TRUE"),
        ]
        for guard, issue in cases:
            with self.subTest(issue=issue):
                result = self._evaluate(guard=guard)

                self.assertEqual("BLOCKED", result["status"])
                self.assertIn(issue, result["issues"])
                self.assertFalse(result["final_send_gate_allowed"])

    def test_final_send_confirmation_missing_blocks(self) -> None:
        result = self._evaluate(context={})

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("FINAL_SEND_CONFIRMATION_REQUIRED", result["issues"])
        self.assertFalse(result["final_send_gate_allowed"])

    def test_no_final_send_gate_send_order_or_queue_commit_calls(self) -> None:
        with (
            mock.patch("final_send_gate_service.evaluate_final_send_gate") as final_gate,
            mock.patch("send_order_entrypoint.execute_send_order") as send_order,
            mock.patch("execution_queue_commit_service.commit_execution_queue_manually") as queue_commit,
        ):
            result = self._evaluate()
            blocked = self._evaluate(context={})

        self.assertEqual("READY_FOR_FINAL_SEND_GATE", result["status"])
        self.assertEqual("BLOCKED", blocked["status"])
        final_gate.assert_not_called()
        send_order.assert_not_called()
        queue_commit.assert_not_called()

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
        self._evaluate(context={})

        self.assertEqual(before_runtime, {str(path): _sha256(path) for path in runtime_paths})
        self.assertEqual(before_rules, {str(path): _sha256(path) for path in rules_paths})


if __name__ == "__main__":
    unittest.main()
