# -*- coding: utf-8 -*-
from __future__ import annotations

import copy
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


def _request(**overrides: object) -> dict[str, object]:
    result = {
        "order_id": "ORDER_ENTRY_CONTRACT_1",
        "source_signal_id": "SIGNAL_ENTRY_CONTRACT_1",
        "execution_id": "EXEC_ENTRY_CONTRACT_1",
        "request_hash": "HASH_ENTRY_CONTRACT_1",
        "lock_id": "LOCK_ENTRY_CONTRACT_1",
        "account_no": "12345678",
        "side": "BUY",
        "code": "005930",
        "quantity": 1,
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
        "send_order_request_preview": _request(),
        "blocked_reasons": [],
        "warnings": [],
    }
    result.update(overrides)
    return result


def _record(**overrides: object) -> dict[str, object]:
    result = {
        "id": "ORDER_QUEUED_ENTRY_CONTRACT_1",
        "status": "ORDER_QUEUED",
        "order_id": "ORDER_ENTRY_CONTRACT_1",
        "source_signal_id": "SIGNAL_ENTRY_CONTRACT_1",
        "execution_id": "EXEC_ENTRY_CONTRACT_1",
        "request_hash": "HASH_ENTRY_CONTRACT_1",
        "lock_id": "LOCK_ENTRY_CONTRACT_1",
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


def _final_input(**overrides: object) -> dict[str, object]:
    result = {
        "adapter_preview_result": _adapter_preview(),
        "send_order_request_preview": _request(),
        "order_queued_record": _record(),
        "current_guard": _guard(),
        "context": _context(),
    }
    result.update(overrides)
    return result


def _final_gate_result(**overrides: object) -> dict[str, object]:
    result = {
        "final_send_gate_ok": True,
        "send_gate_stage": "final_send_gate_approved",
        "next_stage": "SEND_ORDER_ENTRYPOINT_REQUIRED",
        "preview_only": True,
        "no_send": True,
        "send_order_called": False,
        "order_id": "ORDER_ENTRY_CONTRACT_1",
        "order_queued_id": "ORDER_QUEUED_ENTRY_CONTRACT_1",
        "request_hash": "HASH_ENTRY_CONTRACT_1",
        "lock_id": "LOCK_ENTRY_CONTRACT_1",
        "execution_id": "EXEC_ENTRY_CONTRACT_1",
        "blocked_reasons": [],
        "warnings": [],
    }
    result.update(overrides)
    return result


def _call_result(**overrides: object) -> dict[str, object]:
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
        "final_send_gate_result": _final_gate_result(),
        "final_send_gate_input": _final_input(),
        "issues": [],
        "warnings": [],
    }
    result.update(overrides)
    return result


def _open_policy(**overrides: object) -> dict[str, object]:
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


class ExecutionSendOrderEntrypointOrchestratorContractTest(unittest.TestCase):
    def test_ready_policy_and_final_gate_pass_calls_entrypoint_with_preview_broker(self) -> None:
        result = orchestrate_send_order_entrypoint(_open_policy(), _call_result())

        self.assertEqual("SEND_ORDER_ENTRYPOINT_PASSED", result["status"])
        self.assertEqual("BROKER_SEND_REQUIRED", result["next_stage"])
        self.assertTrue(result["entrypoint_called"])
        self.assertTrue(result["send_order_called"])
        self.assertTrue(result["send_order_entrypoint_result"]["send_order_called"])
        self.assertEqual("SEND_ORDER_ENTRYPOINT_PREVIEW_BROKER", result["send_order_entrypoint_result"]["broker"])

    def test_entrypoint_returned_send_order_called_value_is_preserved(self) -> None:
        with mock.patch("execution_send_order_entrypoint_orchestrator.execute_send_order") as entrypoint:
            entrypoint.return_value = {
                "send_order_executed": False,
                "next_stage": "BLOCKED",
                "send_order_called": False,
                "blocked_reasons": ["blocked-before-broker-send"],
                "warnings": [],
            }
            result = orchestrate_send_order_entrypoint(_open_policy(), _call_result())

        self.assertEqual("BLOCKED", result["status"])
        self.assertTrue(result["entrypoint_called"])
        self.assertFalse(result["send_order_called"])
        self.assertFalse(result["send_order_entrypoint_result"]["send_order_called"])

    def test_open_policy_blocked_invalid_malformed_and_disallowed_do_not_call_entrypoint(self) -> None:
        cases = [
            (_open_policy(status="BLOCKED", issues=["OPEN_POLICY_BLOCKED"]), "BLOCKED"),
            (_open_policy(status="INVALID", issues=["OPEN_POLICY_INVALID"]), "INVALID"),
            ("malformed", "INVALID"),
            (_open_policy(send_order_entrypoint_allowed=False), "BLOCKED"),
        ]

        for policy, expected in cases:
            with self.subTest(expected=expected):
                with mock.patch("execution_send_order_entrypoint_orchestrator.execute_send_order") as entrypoint:
                    result = orchestrate_send_order_entrypoint(policy, _call_result())

                self.assertEqual(expected, result["status"])
                self.assertFalse(result["entrypoint_called"])
                entrypoint.assert_not_called()

    def test_final_gate_call_blocked_invalid_and_malformed_do_not_call_entrypoint(self) -> None:
        cases = [
            (_call_result(status="BLOCKED", issues=["FINAL_GATE_BLOCKED"]), "BLOCKED"),
            (_call_result(status="INVALID", issues=["FINAL_GATE_INVALID"]), "INVALID"),
            ("malformed", "INVALID"),
        ]

        for call_result, expected in cases:
            with self.subTest(expected=expected):
                with mock.patch("execution_send_order_entrypoint_orchestrator.execute_send_order") as entrypoint:
                    result = orchestrate_send_order_entrypoint(_open_policy(), call_result)

                self.assertEqual(expected, result["status"])
                self.assertFalse(result["entrypoint_called"])
                entrypoint.assert_not_called()

    def test_missing_final_gate_payload_blocks_without_calling_entrypoint(self) -> None:
        cases = [
            (_call_result(final_send_gate_result=None), "FINAL_SEND_GATE_RESULT_REQUIRED"),
            (_call_result(final_send_gate_input=None), "FINAL_SEND_GATE_INPUT_REQUIRED"),
        ]

        for call_result, expected_issue in cases:
            with self.subTest(expected_issue=expected_issue):
                with mock.patch("execution_send_order_entrypoint_orchestrator.execute_send_order") as entrypoint:
                    result = orchestrate_send_order_entrypoint(_open_policy(), call_result)

                self.assertEqual("BLOCKED", result["status"])
                self.assertIn(expected_issue, result["issues"])
                self.assertFalse(result["entrypoint_called"])
                entrypoint.assert_not_called()

    def test_entrypoint_blocked_result_blocks(self) -> None:
        with mock.patch("execution_send_order_entrypoint_orchestrator.execute_send_order") as entrypoint:
            entrypoint.return_value = {
                "send_order_executed": False,
                "next_stage": "BLOCKED",
                "send_order_called": False,
                "blocked_reasons": ["ENTRYPOINT_BLOCKED_BY_CONTRACT"],
                "warnings": [],
            }
            result = orchestrate_send_order_entrypoint(_open_policy(), _call_result())

        self.assertEqual("BLOCKED", result["status"])
        self.assertTrue(result["entrypoint_called"])
        self.assertIn("ENTRYPOINT_BLOCKED_BY_CONTRACT", result["issues"])

    def test_external_broker_kiwoom_queue_runtime_and_gui_are_not_called(self) -> None:
        external_broker = mock.Mock()
        with (
            mock.patch("execution_queue_commit_service.commit_execution_queue_manually") as queue_commit,
            mock.patch("execution_runtime_commit_service.commit_execution_runtime_plan") as runtime_commit,
        ):
            result = orchestrate_send_order_entrypoint(_open_policy(), _call_result())

        self.assertEqual("SEND_ORDER_ENTRYPOINT_PASSED", result["status"])
        external_broker.send_order.assert_not_called()
        queue_commit.assert_not_called()
        runtime_commit.assert_not_called()

    def test_inputs_are_deepcopied_before_entrypoint_mutation(self) -> None:
        captured: dict[str, object] = {}

        def mutating_entrypoint(final_gate: dict[str, object], request: dict[str, object], record: dict[str, object], *args: object, **kwargs: object) -> dict[str, object]:
            captured["final_gate"] = final_gate
            captured["request"] = request
            captured["record"] = record
            final_gate["order_id"] = "MUTATED"
            request["order_id"] = "MUTATED"
            record["order_id"] = "MUTATED"
            return {
                "send_order_executed": False,
                "next_stage": "BLOCKED",
                "send_order_called": False,
                "blocked_reasons": ["mutation-test"],
                "warnings": [],
            }

        original_call_result = _call_result()
        original_snapshot = copy.deepcopy(original_call_result)
        with mock.patch("execution_send_order_entrypoint_orchestrator.execute_send_order", side_effect=mutating_entrypoint):
            orchestrate_send_order_entrypoint(_open_policy(), original_call_result)

        self.assertEqual(original_snapshot, original_call_result)
        self.assertEqual("MUTATED", captured["final_gate"]["order_id"])
        self.assertEqual("MUTATED", captured["request"]["order_id"])
        self.assertEqual("MUTATED", captured["record"]["order_id"])

    def test_runtime_order_queue_and_rules_are_unchanged(self) -> None:
        protected_paths = [
            ROOT / "runtime" / "order_queue.json",
            ROOT / "runtime" / "order_executions.json",
            ROOT / "runtime" / "order_locks.json",
        ]
        protected_paths.extend((ROOT / "routines").glob("**/rules.json"))
        before = {str(path): _sha256(path) for path in protected_paths}

        orchestrate_send_order_entrypoint(_open_policy(), _call_result())
        orchestrate_send_order_entrypoint(_open_policy(status="BLOCKED"), _call_result())

        self.assertEqual(before, {str(path): _sha256(path) for path in protected_paths})


if __name__ == "__main__":
    unittest.main()
