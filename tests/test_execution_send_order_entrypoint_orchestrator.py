# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
from pathlib import Path
import unittest
from unittest import mock

from execution_send_order_entrypoint_orchestrator import orchestrate_send_order_entrypoint


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ExecutionSendOrderEntrypointOrchestratorTest(unittest.TestCase):
    def _request(self, **overrides: object) -> dict[str, object]:
        result = {
            "order_id": "ORDER_ENTRY_1",
            "source_signal_id": "SIGNAL_ENTRY_1",
            "execution_id": "EXEC_ENTRY_1",
            "request_hash": "HASH_ENTRY_1",
            "lock_id": "LOCK_ENTRY_1",
            "account_no": "12345678",
            "side": "BUY",
            "code": "003550",
            "quantity": 10,
            "price": 100,
            "hoga": "LIMIT",
        }
        result.update(overrides)
        return result

    def _adapter_preview(self, **overrides: object) -> dict[str, object]:
        result = {
            "adapter_preview_ok": True,
            "next_stage": "FINAL_SEND_GATE_REQUIRED",
            "preview_only": True,
            "no_send": True,
            "send_order_called": False,
            "send_order_request_preview": self._request(),
            "blocked_reasons": [],
            "warnings": [],
        }
        result.update(overrides)
        return result

    def _record(self, **overrides: object) -> dict[str, object]:
        result = {
            "id": "ORDER_QUEUED_ENTRY_1",
            "status": "ORDER_QUEUED",
            "order_id": "ORDER_ENTRY_1",
            "source_signal_id": "SIGNAL_ENTRY_1",
            "execution_id": "EXEC_ENTRY_1",
            "request_hash": "HASH_ENTRY_1",
            "lock_id": "LOCK_ENTRY_1",
            "send_order_called": False,
            "execution_enabled": False,
        }
        result.update(overrides)
        return result

    def _guard(self, **overrides: object) -> dict[str, object]:
        result = {
            "real_trade_enabled": True,
            "kiwoom_logged_in": True,
            "account_selected": True,
            "account_no": "12345678",
            "operator_confirmed": True,
        }
        result.update(overrides)
        return result

    def _context(self, **overrides: object) -> dict[str, object]:
        result = {"manual_final_send_confirmed": True}
        result.update(overrides)
        return result

    def _final_input(self, **overrides: object) -> dict[str, object]:
        result = {
            "adapter_preview_result": self._adapter_preview(),
            "send_order_request_preview": self._request(),
            "order_queued_record": self._record(),
            "current_guard": self._guard(),
            "context": self._context(),
        }
        result.update(overrides)
        return result

    def _final_gate_result(self, **overrides: object) -> dict[str, object]:
        result = {
            "final_send_gate_ok": True,
            "send_gate_stage": "final_send_gate_approved",
            "next_stage": "SEND_ORDER_ENTRYPOINT_REQUIRED",
            "preview_only": True,
            "no_send": True,
            "send_order_called": False,
            "order_id": "ORDER_ENTRY_1",
            "order_queued_id": "ORDER_QUEUED_ENTRY_1",
            "request_hash": "HASH_ENTRY_1",
            "lock_id": "LOCK_ENTRY_1",
            "execution_id": "EXEC_ENTRY_1",
            "blocked_reasons": [],
            "warnings": [],
        }
        result.update(overrides)
        return result

    def _call_result(self, **overrides: object) -> dict[str, object]:
        result = {
            "orchestrator_type": "EXECUTION_FINAL_SEND_GATE_CALL_ORCHESTRATOR",
            "status": "FINAL_SEND_GATE_PASSED",
            "preview_only": True,
            "runtime_write": False,
            "queue_write": False,
            "entrypoint_called": False,
            "send_order_called": False,
            "final_send_gate_called": True,
            "next_stage": "SEND_ORDER_ENTRYPOINT_REQUIRED",
            "final_send_gate_result": self._final_gate_result(),
            "final_send_gate_input": self._final_input(),
            "issues": [],
            "warnings": [],
        }
        result.update(overrides)
        return result

    def _open_policy(self, **overrides: object) -> dict[str, object]:
        result = {
            "policy_type": "EXECUTION_SEND_ORDER_ENTRYPOINT_OPEN_POLICY",
            "status": "READY_TO_OPEN_SEND_ORDER_ENTRYPOINT",
            "send_order_entrypoint_allowed": True,
            "preview_only": True,
            "runtime_write": False,
            "queue_write": False,
            "send_order_called": False,
            "entrypoint_called": False,
            "required_confirmations": {"manual_send_order_entrypoint_confirmed": True},
            "environment_checks": {
                "send_order_entrypoint_enabled": True,
                "real_send_order_enabled": True,
            },
            "issues": [],
            "warnings": [],
        }
        result.update(overrides)
        return result

    def test_open_policy_ready_and_final_gate_pass_entrypoint_pass(self) -> None:
        result = orchestrate_send_order_entrypoint(self._open_policy(), self._call_result())

        self.assertEqual("SEND_ORDER_ENTRYPOINT_PASSED", result["status"])
        self.assertTrue(result["preview_only"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_write"])
        self.assertTrue(result["entrypoint_called"])
        self.assertTrue(result["send_order_called"])
        self.assertEqual("BROKER_SEND_REQUIRED", result["next_stage"])
        self.assertEqual(True, result["send_order_entrypoint_result"]["send_order_executed"])
        self.assertEqual("SEND_ORDER_ENTRYPOINT_PREVIEW_BROKER", result["send_order_entrypoint_result"]["broker"])

    def test_open_policy_blocked_invalid_and_disallowed_do_not_call_entrypoint(self) -> None:
        cases = [
            (self._open_policy(status="BLOCKED", issues=["blocked"]), "BLOCKED"),
            (self._open_policy(status="INVALID", issues=["invalid"]), "INVALID"),
            (self._open_policy(send_order_entrypoint_allowed=False), "BLOCKED"),
        ]
        for policy, expected in cases:
            with self.subTest(expected=expected):
                with mock.patch("execution_send_order_entrypoint_orchestrator.execute_send_order") as entrypoint:
                    result = orchestrate_send_order_entrypoint(policy, self._call_result())

                self.assertEqual(expected, result["status"])
                self.assertFalse(result["entrypoint_called"])
                entrypoint.assert_not_called()

    def test_final_gate_call_blocked_invalid_and_malformed_do_not_call_entrypoint(self) -> None:
        cases = [
            (self._call_result(status="BLOCKED", issues=["blocked"]), "BLOCKED"),
            (self._call_result(status="INVALID", issues=["invalid"]), "INVALID"),
            ("malformed", "INVALID"),
        ]
        for call_result, expected in cases:
            with self.subTest(expected=expected):
                with mock.patch("execution_send_order_entrypoint_orchestrator.execute_send_order") as entrypoint:
                    result = orchestrate_send_order_entrypoint(self._open_policy(), call_result)

                self.assertEqual(expected, result["status"])
                self.assertFalse(result["entrypoint_called"])
                entrypoint.assert_not_called()

    def test_missing_final_send_gate_result_blocks(self) -> None:
        with mock.patch("execution_send_order_entrypoint_orchestrator.execute_send_order") as entrypoint:
            result = orchestrate_send_order_entrypoint(
                self._open_policy(),
                self._call_result(final_send_gate_result=None),
            )

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("FINAL_SEND_GATE_RESULT_REQUIRED", result["issues"])
        self.assertFalse(result["entrypoint_called"])
        entrypoint.assert_not_called()

    def test_missing_final_send_gate_input_blocks(self) -> None:
        with mock.patch("execution_send_order_entrypoint_orchestrator.execute_send_order") as entrypoint:
            result = orchestrate_send_order_entrypoint(
                self._open_policy(),
                self._call_result(final_send_gate_input=None),
            )

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("FINAL_SEND_GATE_INPUT_REQUIRED", result["issues"])
        self.assertFalse(result["entrypoint_called"])
        entrypoint.assert_not_called()

    def test_entrypoint_blocked_result_blocks(self) -> None:
        final_input = self._final_input(current_guard=self._guard(operator_confirmed=False))

        result = orchestrate_send_order_entrypoint(
            self._open_policy(),
            self._call_result(final_send_gate_input=final_input),
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertTrue(result["entrypoint_called"])
        self.assertFalse(result["send_order_called"])
        self.assertIn("current_guard.operator_confirmed is not true", result["issues"])

    def test_send_order_called_value_is_preserved_from_entrypoint_result(self) -> None:
        with mock.patch("execution_send_order_entrypoint_orchestrator.execute_send_order") as entrypoint:
            entrypoint.return_value = {
                "send_order_executed": False,
                "entrypoint_stage": "broker_adapter",
                "next_stage": "BLOCKED",
                "send_order_called": False,
                "blocked_reasons": ["blocked-before-send"],
                "warnings": [],
            }
            result = orchestrate_send_order_entrypoint(self._open_policy(), self._call_result())

        self.assertEqual("BLOCKED", result["status"])
        self.assertTrue(result["entrypoint_called"])
        self.assertFalse(result["send_order_called"])

    def test_queue_runtime_commit_and_external_broker_are_not_called(self) -> None:
        external_broker = mock.Mock()
        with (
            mock.patch("execution_queue_commit_service.commit_execution_queue_manually") as queue_commit,
            mock.patch("execution_runtime_commit_service.commit_execution_runtime_plan") as runtime_commit,
        ):
            result = orchestrate_send_order_entrypoint(self._open_policy(), self._call_result())

        self.assertEqual("SEND_ORDER_ENTRYPOINT_PASSED", result["status"])
        external_broker.send_order.assert_not_called()
        queue_commit.assert_not_called()
        runtime_commit.assert_not_called()

    def test_runtime_order_queue_and_rules_hash_unchanged(self) -> None:
        protected_paths = [
            ROOT / "runtime" / "order_queue.json",
            ROOT / "runtime" / "order_executions.json",
            ROOT / "runtime" / "order_locks.json",
        ]
        protected_paths.extend((ROOT / "routines").glob("**/rules.json"))
        before = {str(path): _sha256(path) for path in protected_paths}

        orchestrate_send_order_entrypoint(self._open_policy(), self._call_result())
        orchestrate_send_order_entrypoint(self._open_policy(status="BLOCKED"), self._call_result())

        self.assertEqual(before, {str(path): _sha256(path) for path in protected_paths})


if __name__ == "__main__":
    unittest.main()
