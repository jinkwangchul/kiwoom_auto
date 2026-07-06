# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
from pathlib import Path
import unittest
from unittest import mock

from execution_final_send_gate_call_orchestrator import call_final_send_gate_after_open_policy


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ExecutionFinalSendGateCallOrchestratorTest(unittest.TestCase):
    def _request_preview(self, **overrides: object) -> dict[str, object]:
        preview = {
            "order_id": "ORDER_CALL_1",
            "source_signal_id": "SIGNAL_CALL_1",
            "execution_id": "EXEC_CALL_1",
            "request_hash": "HASH_CALL_1",
            "lock_id": "LOCK_CALL_1",
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
            "id": "ORDER_QUEUED_CALL_1",
            "status": "ORDER_QUEUED",
            "order_id": "ORDER_CALL_1",
            "source_signal_id": "SIGNAL_CALL_1",
            "execution_id": "EXEC_CALL_1",
            "request_hash": "HASH_CALL_1",
            "lock_id": "LOCK_CALL_1",
            "send_order_called": False,
            "execution_enabled": False,
        }
        record.update(overrides)
        return record

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

    def _final_input(self, **overrides: object) -> dict[str, object]:
        final_input = {
            "adapter_preview_result": self._adapter_preview(),
            "send_order_request_preview": self._request_preview(),
            "order_queued_record": self._record(),
            "identity": {
                "order_id": "ORDER_CALL_1",
                "source_signal_id": "SIGNAL_CALL_1",
                "execution_id": "EXEC_CALL_1",
                "request_hash": "HASH_CALL_1",
                "lock_id": "LOCK_CALL_1",
            },
            "current_guard": self._guard(),
            "context": self._context(),
        }
        final_input.update(overrides)
        return final_input

    def _open_policy(self, **overrides: object) -> dict[str, object]:
        result = {
            "policy_type": "EXECUTION_FINAL_SEND_GATE_OPEN_POLICY",
            "status": "READY_TO_OPEN_FINAL_SEND_GATE",
            "final_send_gate_call_allowed": True,
            "preview_only": True,
            "runtime_write": False,
            "queue_write": False,
            "send_order_called": False,
            "final_send_gate_called": False,
            "required_confirmations": {"manual_final_send_gate_call_confirmed": True},
            "environment_checks": {"final_send_gate_call_enabled": True},
            "issues": [],
            "warnings": [],
        }
        result.update(overrides)
        return result

    def _orchestrator(self, **overrides: object) -> dict[str, object]:
        result = {
            "orchestrator_type": "EXECUTION_FINAL_SEND_GATE_ORCHESTRATOR",
            "status": "READY_FOR_FINAL_SEND_GATE",
            "preview_only": True,
            "runtime_write": False,
            "queue_write": False,
            "send_order_called": False,
            "final_send_gate_called": False,
            "final_send_gate_ready": True,
            "next_stage": "FINAL_SEND_GATE_SERVICE_REQUIRED",
            "final_send_gate_input": self._final_input(),
            "identity": {"order_id": "ORDER_CALL_1"},
            "issues": [],
            "warnings": [],
        }
        result.update(overrides)
        return result

    def test_ready_policy_and_orchestrator_gate_pass(self) -> None:
        result = call_final_send_gate_after_open_policy(self._open_policy(), self._orchestrator())

        self.assertEqual("FINAL_SEND_GATE_PASSED", result["status"])
        self.assertTrue(result["preview_only"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_write"])
        self.assertFalse(result["send_order_called"])
        self.assertTrue(result["final_send_gate_called"])
        self.assertEqual("SEND_ORDER_ENTRYPOINT_REQUIRED", result["next_stage"])
        self.assertTrue(result["final_send_gate_result"]["final_send_gate_ok"])

    def test_open_policy_blocked_invalid_and_malformed_gate_not_called(self) -> None:
        cases = [
            (self._open_policy(status="BLOCKED", issues=["blocked"]), "BLOCKED"),
            (self._open_policy(status="INVALID", issues=["invalid"]), "INVALID"),
            ("malformed", "INVALID"),
        ]
        for policy, expected in cases:
            with self.subTest(expected=expected):
                with mock.patch("execution_final_send_gate_call_orchestrator.evaluate_final_send_gate") as gate:
                    result = call_final_send_gate_after_open_policy(policy, self._orchestrator())

                self.assertEqual(expected, result["status"])
                self.assertFalse(result["final_send_gate_called"])
                gate.assert_not_called()

    def test_open_policy_ready_but_allowed_false_blocks(self) -> None:
        with mock.patch("execution_final_send_gate_call_orchestrator.evaluate_final_send_gate") as gate:
            result = call_final_send_gate_after_open_policy(
                self._open_policy(final_send_gate_call_allowed=False),
                self._orchestrator(),
            )

        self.assertEqual("BLOCKED", result["status"])
        self.assertFalse(result["final_send_gate_called"])
        self.assertIn("FINAL_SEND_GATE_CALL_NOT_ALLOWED", result["issues"])
        gate.assert_not_called()

    def test_orchestrator_blocked_invalid_and_malformed_gate_not_called(self) -> None:
        cases = [
            (self._orchestrator(status="BLOCKED", issues=["blocked"]), "BLOCKED"),
            (self._orchestrator(status="INVALID", issues=["invalid"]), "INVALID"),
            ("malformed", "INVALID"),
        ]
        for orchestrator, expected in cases:
            with self.subTest(expected=expected):
                with mock.patch("execution_final_send_gate_call_orchestrator.evaluate_final_send_gate") as gate:
                    result = call_final_send_gate_after_open_policy(self._open_policy(), orchestrator)

                self.assertEqual(expected, result["status"])
                self.assertFalse(result["final_send_gate_called"])
                gate.assert_not_called()

    def test_orchestrator_ready_flags_and_stage_required(self) -> None:
        cases = [
            (self._orchestrator(final_send_gate_ready=False), "FINAL_SEND_GATE_READY_NOT_TRUE"),
            (self._orchestrator(next_stage="OTHER"), "FINAL_SEND_GATE_NEXT_STAGE_NOT_SERVICE_REQUIRED"),
            (self._orchestrator(final_send_gate_input=None), "FINAL_SEND_GATE_INPUT_REQUIRED"),
        ]
        for orchestrator, issue in cases:
            with self.subTest(issue=issue):
                with mock.patch("execution_final_send_gate_call_orchestrator.evaluate_final_send_gate") as gate:
                    result = call_final_send_gate_after_open_policy(self._open_policy(), orchestrator)

                self.assertEqual("BLOCKED", result["status"])
                self.assertFalse(result["final_send_gate_called"])
                self.assertIn(issue, result["issues"])
                gate.assert_not_called()

    def test_missing_required_final_input_payload_blocks(self) -> None:
        cases = [
            ("adapter_preview_result", "ADAPTER_PREVIEW_RESULT_REQUIRED"),
            ("order_queued_record", "ORDER_QUEUED_RECORD_REQUIRED"),
            ("current_guard", "CURRENT_GUARD_REQUIRED"),
            ("context", "CONTEXT_REQUIRED"),
        ]
        for field, issue in cases:
            with self.subTest(field=field):
                final_input = self._final_input()
                final_input[field] = None
                with mock.patch("execution_final_send_gate_call_orchestrator.evaluate_final_send_gate") as gate:
                    result = call_final_send_gate_after_open_policy(
                        self._open_policy(),
                        self._orchestrator(final_send_gate_input=final_input),
                    )

                self.assertEqual("BLOCKED", result["status"])
                self.assertFalse(result["final_send_gate_called"])
                self.assertIn(issue, result["issues"])
                gate.assert_not_called()

    def test_gate_blocked_result_blocks(self) -> None:
        final_input = self._final_input(current_guard=self._guard(operator_confirmed=False))

        result = call_final_send_gate_after_open_policy(
            self._open_policy(),
            self._orchestrator(final_send_gate_input=final_input),
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertTrue(result["final_send_gate_called"])
        self.assertIn("current_guard.operator_confirmed is not true", result["issues"])

    def test_gate_called_only_when_all_conditions_valid(self) -> None:
        with mock.patch("execution_final_send_gate_call_orchestrator.evaluate_final_send_gate") as gate:
            gate.return_value = {
                "final_send_gate_ok": True,
                "next_stage": "SEND_ORDER_ENTRYPOINT_REQUIRED",
                "send_order_called": False,
                "blocked_reasons": [],
                "warnings": [],
            }
            ready = call_final_send_gate_after_open_policy(self._open_policy(), self._orchestrator())
            blocked = call_final_send_gate_after_open_policy(
                self._open_policy(status="BLOCKED"),
                self._orchestrator(),
            )

        self.assertEqual("FINAL_SEND_GATE_PASSED", ready["status"])
        self.assertEqual("BLOCKED", blocked["status"])
        self.assertEqual(1, gate.call_count)

    def test_send_order_queue_and_runtime_commit_are_not_called(self) -> None:
        with (
            mock.patch("send_order_entrypoint.execute_send_order") as send_order,
            mock.patch("execution_queue_commit_service.commit_execution_queue_manually") as queue_commit,
            mock.patch("execution_runtime_commit_service.commit_execution_runtime_plan") as runtime_commit,
        ):
            result = call_final_send_gate_after_open_policy(self._open_policy(), self._orchestrator())

        self.assertEqual("FINAL_SEND_GATE_PASSED", result["status"])
        self.assertFalse(result["send_order_called"])
        send_order.assert_not_called()
        queue_commit.assert_not_called()
        runtime_commit.assert_not_called()

    def test_order_queue_runtime_and_rules_hash_unchanged(self) -> None:
        runtime_paths = [
            ROOT / "runtime" / "order_queue.json",
            ROOT / "runtime" / "order_executions.json",
            ROOT / "runtime" / "order_locks.json",
        ]
        rules_paths = list((ROOT / "routines").glob("**/rules.json"))
        before_runtime = {str(path): _sha256(path) for path in runtime_paths}
        before_rules = {str(path): _sha256(path) for path in rules_paths}

        call_final_send_gate_after_open_policy(self._open_policy(), self._orchestrator())
        call_final_send_gate_after_open_policy(self._open_policy(status="BLOCKED"), self._orchestrator())

        self.assertEqual(before_runtime, {str(path): _sha256(path) for path in runtime_paths})
        self.assertEqual(before_rules, {str(path): _sha256(path) for path in rules_paths})


if __name__ == "__main__":
    unittest.main()
