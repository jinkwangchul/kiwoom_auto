# -*- coding: utf-8 -*-
from __future__ import annotations

import copy
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


def _send_order_request_preview(**overrides: object) -> dict[str, object]:
    result = {
        "order_id": "ORDER_CONTRACT_1",
        "source_signal_id": "SIGNAL_CONTRACT_1",
        "execution_id": "EXEC_CONTRACT_1",
        "request_hash": "HASH_CONTRACT_1",
        "lock_id": "LOCK_CONTRACT_1",
        "account_no": "12345678",
        "side": "BUY",
        "code": "005930",
        "quantity": 3,
        "price": 71000,
        "hoga": "LIMIT",
    }
    result.update(overrides)
    return result


def _adapter_preview(**overrides: object) -> dict[str, object]:
    result = {
        "adapter_preview_ok": True,
        "next_stage": "FINAL_SEND_GATE_REQUIRED",
        "preview_only": True,
        "no_send": True,
        "send_order_called": False,
        "send_order_request_preview": _send_order_request_preview(),
        "blocked_reasons": [],
        "warnings": [],
    }
    result.update(overrides)
    return result


def _order_queued_record(**overrides: object) -> dict[str, object]:
    result = {
        "id": "ORDER_QUEUED_CONTRACT_1",
        "status": "ORDER_QUEUED",
        "order_id": "ORDER_CONTRACT_1",
        "source_signal_id": "SIGNAL_CONTRACT_1",
        "execution_id": "EXEC_CONTRACT_1",
        "request_hash": "HASH_CONTRACT_1",
        "lock_id": "LOCK_CONTRACT_1",
        "send_order_called": False,
        "execution_enabled": False,
    }
    result.update(overrides)
    return result


def _guard(**overrides: object) -> dict[str, object]:
    result = {
        "real_trade_enabled": True,
        "kiwoom_logged_in": True,
        "account_selected": True,
        "account_no": "12345678",
        "operator_confirmed": True,
    }
    result.update(overrides)
    return result


def _context(**overrides: object) -> dict[str, object]:
    result = {"manual_final_send_confirmed": True}
    result.update(overrides)
    return result


def _identity(**overrides: object) -> dict[str, object]:
    result = {
        "order_id": "ORDER_CONTRACT_1",
        "source_signal_id": "SIGNAL_CONTRACT_1",
        "execution_id": "EXEC_CONTRACT_1",
        "request_hash": "HASH_CONTRACT_1",
        "lock_id": "LOCK_CONTRACT_1",
    }
    result.update(overrides)
    return result


def _final_send_gate_input(**overrides: object) -> dict[str, object]:
    result = {
        "adapter_preview_result": _adapter_preview(),
        "send_order_request_preview": _send_order_request_preview(),
        "order_queued_record": _order_queued_record(),
        "identity": _identity(),
        "current_guard": _guard(),
        "context": _context(),
    }
    result.update(overrides)
    return result


def _open_policy(**overrides: object) -> dict[str, object]:
    result = {
        "policy_type": "EXECUTION_FINAL_SEND_GATE_OPEN_POLICY",
        "status": "READY_TO_OPEN_FINAL_SEND_GATE",
        "final_send_gate_call_allowed": True,
        "preview_only": True,
        "runtime_write": False,
        "queue_write": False,
        "send_order_called": False,
        "final_send_gate_called": False,
        "issues": [],
        "warnings": [],
    }
    result.update(overrides)
    return result


def _orchestrator(**overrides: object) -> dict[str, object]:
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
        "final_send_gate_input": _final_send_gate_input(),
        "identity": _identity(),
        "issues": [],
        "warnings": [],
    }
    result.update(overrides)
    return result


class ExecutionFinalSendGateCallOrchestratorContractTest(unittest.TestCase):
    def test_ready_policy_and_ready_orchestrator_calls_gate_and_preserves_result(self) -> None:
        gate_result = {
            "final_send_gate_ok": True,
            "next_stage": "SEND_ORDER_ENTRYPOINT_REQUIRED",
            "send_order_called": False,
            "blocked_reasons": [],
            "warnings": ["gate-preview"],
        }

        with mock.patch("execution_final_send_gate_call_orchestrator.evaluate_final_send_gate") as gate:
            gate.return_value = copy.deepcopy(gate_result)
            result = call_final_send_gate_after_open_policy(_open_policy(), _orchestrator())

        self.assertEqual("FINAL_SEND_GATE_PASSED", result["status"])
        self.assertEqual("SEND_ORDER_ENTRYPOINT_REQUIRED", result["next_stage"])
        self.assertEqual(gate_result, result["final_send_gate_result"])
        self.assertTrue(result["final_send_gate_called"])
        self.assertFalse(result["send_order_called"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_write"])
        gate.assert_called_once()

    def test_gate_called_only_when_all_policy_and_orchestrator_conditions_are_valid(self) -> None:
        with mock.patch("execution_final_send_gate_call_orchestrator.evaluate_final_send_gate") as gate:
            gate.return_value = {
                "final_send_gate_ok": True,
                "next_stage": "SEND_ORDER_ENTRYPOINT_REQUIRED",
                "send_order_called": False,
                "blocked_reasons": [],
                "warnings": [],
            }
            ready = call_final_send_gate_after_open_policy(_open_policy(), _orchestrator())
            blocked_policy = call_final_send_gate_after_open_policy(
                _open_policy(status="BLOCKED", issues=["OPEN_POLICY_BLOCKED"]),
                _orchestrator(),
            )
            blocked_orchestrator = call_final_send_gate_after_open_policy(
                _open_policy(),
                _orchestrator(status="BLOCKED", issues=["ORCHESTRATOR_BLOCKED"]),
            )

        self.assertEqual("FINAL_SEND_GATE_PASSED", ready["status"])
        self.assertEqual("BLOCKED", blocked_policy["status"])
        self.assertEqual("BLOCKED", blocked_orchestrator["status"])
        self.assertEqual(1, gate.call_count)

    def test_open_policy_blocked_invalid_malformed_and_disallowed_do_not_call_gate(self) -> None:
        cases = [
            (_open_policy(status="BLOCKED", issues=["OPEN_POLICY_BLOCKED"]), "BLOCKED"),
            (_open_policy(status="INVALID", issues=["OPEN_POLICY_INVALID"]), "INVALID"),
            ("malformed", "INVALID"),
            (_open_policy(final_send_gate_call_allowed=False), "BLOCKED"),
        ]

        for policy, expected in cases:
            with self.subTest(expected=expected):
                with mock.patch("execution_final_send_gate_call_orchestrator.evaluate_final_send_gate") as gate:
                    result = call_final_send_gate_after_open_policy(policy, _orchestrator())

                self.assertEqual(expected, result["status"])
                self.assertFalse(result["final_send_gate_called"])
                gate.assert_not_called()

    def test_orchestrator_blocked_invalid_and_malformed_do_not_call_gate(self) -> None:
        cases = [
            (_orchestrator(status="BLOCKED", issues=["ORCHESTRATOR_BLOCKED"]), "BLOCKED"),
            (_orchestrator(status="INVALID", issues=["ORCHESTRATOR_INVALID"]), "INVALID"),
            ("malformed", "INVALID"),
        ]

        for orchestrator, expected in cases:
            with self.subTest(expected=expected):
                with mock.patch("execution_final_send_gate_call_orchestrator.evaluate_final_send_gate") as gate:
                    result = call_final_send_gate_after_open_policy(_open_policy(), orchestrator)

                self.assertEqual(expected, result["status"])
                self.assertFalse(result["final_send_gate_called"])
                gate.assert_not_called()

    def test_missing_final_send_gate_input_blocks_without_calling_gate(self) -> None:
        with mock.patch("execution_final_send_gate_call_orchestrator.evaluate_final_send_gate") as gate:
            result = call_final_send_gate_after_open_policy(
                _open_policy(),
                _orchestrator(final_send_gate_input=None),
            )

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("FINAL_SEND_GATE_INPUT_REQUIRED", result["issues"])
        self.assertFalse(result["final_send_gate_called"])
        gate.assert_not_called()

    def test_gate_blocked_result_becomes_blocked(self) -> None:
        gate_result = {
            "final_send_gate_ok": False,
            "next_stage": "BLOCKED",
            "send_order_called": False,
            "blocked_reasons": ["FINAL_SEND_GATE_BLOCKED_BY_CONTRACT"],
            "warnings": [],
        }

        with mock.patch("execution_final_send_gate_call_orchestrator.evaluate_final_send_gate") as gate:
            gate.return_value = copy.deepcopy(gate_result)
            result = call_final_send_gate_after_open_policy(_open_policy(), _orchestrator())

        self.assertEqual("BLOCKED", result["status"])
        self.assertTrue(result["final_send_gate_called"])
        self.assertEqual(gate_result, result["final_send_gate_result"])
        self.assertIn("FINAL_SEND_GATE_BLOCKED_BY_CONTRACT", result["issues"])

    def test_send_order_queue_commit_and_runtime_commit_are_never_called(self) -> None:
        with (
            mock.patch("send_order_entrypoint.execute_send_order") as send_order,
            mock.patch("execution_queue_commit_service.commit_execution_queue_manually") as queue_commit,
            mock.patch("execution_runtime_commit_service.commit_execution_runtime_plan") as runtime_commit,
        ):
            result = call_final_send_gate_after_open_policy(_open_policy(), _orchestrator())

        self.assertEqual("FINAL_SEND_GATE_PASSED", result["status"])
        self.assertFalse(result["send_order_called"])
        send_order.assert_not_called()
        queue_commit.assert_not_called()
        runtime_commit.assert_not_called()

    def test_runtime_order_queue_and_rules_are_unchanged(self) -> None:
        protected_paths = [
            ROOT / "runtime" / "order_queue.json",
            ROOT / "runtime" / "order_executions.json",
            ROOT / "runtime" / "order_locks.json",
            ROOT / "routines" / "지표추종매매" / "rules.json",
        ]
        before = {str(path): _sha256(path) for path in protected_paths}

        call_final_send_gate_after_open_policy(_open_policy(), _orchestrator())
        call_final_send_gate_after_open_policy(_open_policy(status="BLOCKED"), _orchestrator())

        self.assertEqual(before, {str(path): _sha256(path) for path in protected_paths})


if __name__ == "__main__":
    unittest.main()
