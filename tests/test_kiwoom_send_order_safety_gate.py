# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import unittest
from unittest import mock

from kiwoom_send_order_safety_gate import evaluate_kiwoom_send_order_safety


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _protected_paths() -> list[Path]:
    paths = [
        ROOT / "runtime" / "order_queue.json",
        ROOT / "runtime" / "order_executions.json",
        ROOT / "runtime" / "order_locks.json",
    ]
    paths.extend(sorted((ROOT / "routines").glob("*/rules.json")))
    return paths


class KiwoomSendOrderSafetyGateTest(unittest.TestCase):
    def _contract_result(self, **overrides: object) -> dict[str, object]:
        send_order_params = {
            "screen_no": "0101",
            "order_name": "BUY",
            "account_no": "12345678",
            "order_type": 1,
            "code": "003550",
            "quantity": 10,
            "price": 85000,
            "hoga": "03",
            "original_order_no": "",
        }
        result: dict[str, object] = {
            "status": "SEND_ORDER_CONTRACT_READY",
            "send_order_adapter_contract": {
                "dispatch_id": "DISPATCH_SAFETY_1",
                "order_id": "ORDER_SAFETY_1",
                "account_no": "12345678",
                "screen_no": "0101",
                "order_name": "BUY",
                "order_type": 1,
                "code": "003550",
                "quantity": 10,
                "price": 85000,
                "hoga": "03",
                "original_order_no": "",
                "send_order_params": deepcopy(send_order_params),
            },
            "send_order_params": send_order_params,
            "issues": [],
            "warnings": [],
            "preview_only": True,
            "send_order_called": False,
            "broker_called": False,
            "runtime_write": False,
            "queue_write": False,
        }
        result.update(overrides)
        return result

    def _runtime_snapshot(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "locks": [],
            "existing_dispatches": [],
            "emergency_stop": False,
        }
        result.update(overrides)
        return result

    def _connection_state(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "kiwoom_connected": True,
            "account_no": "12345678",
        }
        result.update(overrides)
        return result

    def _operator_context(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "operator_final_send_confirmed": True,
            "emergency_stop": False,
        }
        result.update(overrides)
        return result

    def test_send_order_safe_normal(self) -> None:
        result = evaluate_kiwoom_send_order_safety(
            self._contract_result(),
            self._runtime_snapshot(),
            self._connection_state(),
            self._operator_context(),
        )

        self.assertEqual("SEND_ORDER_SAFE", result["status"])
        self.assertTrue(result["send_order_allowed"])
        self.assertFalse(result["send_order_called"])
        self.assertFalse(result["broker_called"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_write"])
        safety = result["safety"]
        self.assertTrue(safety["contract_ready"])
        self.assertTrue(safety["kiwoom_connected"])
        self.assertTrue(safety["account_matched"])
        self.assertTrue(safety["operator_final_confirmed"])
        self.assertTrue(safety["runtime_lock_absent"])
        self.assertTrue(safety["duplicate_dispatch_absent"])

    def test_contract_blocked_returns_blocked(self) -> None:
        result = evaluate_kiwoom_send_order_safety(
            self._contract_result(status="BLOCKED", issues=["blocked"]),
            self._runtime_snapshot(),
            self._connection_state(),
            self._operator_context(),
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertFalse(result["send_order_allowed"])
        self.assertIn("send_order_adapter_contract_result.status is not SEND_ORDER_CONTRACT_READY", result["issues"])

    def test_contract_invalid_returns_invalid(self) -> None:
        result = evaluate_kiwoom_send_order_safety(
            self._contract_result(status="INVALID", issues=["bad"]),
            self._runtime_snapshot(),
            self._connection_state(),
            self._operator_context(),
        )

        self.assertEqual("INVALID", result["status"])
        self.assertFalse(result["send_order_allowed"])
        self.assertIn("send_order_adapter_contract_result.status is INVALID", result["issues"])

    def test_kiwoom_disconnected_blocks(self) -> None:
        result = evaluate_kiwoom_send_order_safety(
            self._contract_result(),
            self._runtime_snapshot(),
            self._connection_state(kiwoom_connected=False),
            self._operator_context(),
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("kiwoom is not connected", result["issues"])

    def test_account_mismatch_blocks(self) -> None:
        result = evaluate_kiwoom_send_order_safety(
            self._contract_result(),
            self._runtime_snapshot(),
            self._connection_state(account_no="87654321"),
            self._operator_context(),
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("account_no does not match kiwoom connection state", result["issues"])

    def test_operator_final_confirmation_false_blocks(self) -> None:
        result = evaluate_kiwoom_send_order_safety(
            self._contract_result(),
            self._runtime_snapshot(),
            self._connection_state(),
            self._operator_context(operator_final_send_confirmed=False),
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("operator final send confirmation is required", result["issues"])

    def test_runtime_lock_blocks(self) -> None:
        result = evaluate_kiwoom_send_order_safety(
            self._contract_result(),
            self._runtime_snapshot(locks=[{"order_id": "ORDER_SAFETY_1", "account_no": "12345678"}]),
            self._connection_state(),
            self._operator_context(),
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("runtime lock exists", result["issues"])

    def test_emergency_stop_blocks(self) -> None:
        result = evaluate_kiwoom_send_order_safety(
            self._contract_result(),
            self._runtime_snapshot(emergency_stop=True),
            self._connection_state(),
            self._operator_context(),
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("emergency stop is active", result["issues"])

    def test_duplicate_dispatch_blocks(self) -> None:
        result = evaluate_kiwoom_send_order_safety(
            self._contract_result(),
            self._runtime_snapshot(existing_dispatches=[{"dispatch_id": "DISPATCH_SAFETY_1"}]),
            self._connection_state(),
            self._operator_context(),
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("duplicate dispatch exists", result["issues"])

    def test_malformed_input_is_invalid(self) -> None:
        result = evaluate_kiwoom_send_order_safety(
            None,
            self._runtime_snapshot(),
            self._connection_state(),
            self._operator_context(),
        )

        self.assertEqual("INVALID", result["status"])
        self.assertIn("send_order_adapter_contract_result must be a dict", result["issues"])

    def test_invalid_screen_no_is_blocked(self) -> None:
        contract = self._contract_result()
        contract["send_order_adapter_contract"]["screen_no"] = "A101"
        contract["send_order_params"]["screen_no"] = "A101"

        result = evaluate_kiwoom_send_order_safety(
            contract,
            self._runtime_snapshot(),
            self._connection_state(),
            self._operator_context(),
        )

        self.assertEqual("INVALID", result["status"])
        self.assertIn("send_order_params.screen_no is invalid", result["issues"])

    def test_invalid_order_params_is_invalid(self) -> None:
        contract = self._contract_result()
        contract["send_order_params"]["quantity"] = 0

        result = evaluate_kiwoom_send_order_safety(
            contract,
            self._runtime_snapshot(),
            self._connection_state(),
            self._operator_context(),
        )

        self.assertEqual("INVALID", result["status"])
        self.assertIn("send_order_params.quantity must be greater than 0", result["issues"])

    def test_inputs_are_not_mutated(self) -> None:
        contract = self._contract_result()
        runtime_snapshot = self._runtime_snapshot()
        connection_state = self._connection_state()
        operator_context = self._operator_context()
        originals = (
            deepcopy(contract),
            deepcopy(runtime_snapshot),
            deepcopy(connection_state),
            deepcopy(operator_context),
        )

        result = evaluate_kiwoom_send_order_safety(
            contract,
            runtime_snapshot,
            connection_state,
            operator_context,
        )
        result["safety"]["order_id"] = "MUTATED"

        self.assertEqual(originals[0], contract)
        self.assertEqual(originals[1], runtime_snapshot)
        self.assertEqual(originals[2], connection_state)
        self.assertEqual(originals[3], operator_context)

    def test_runtime_order_queue_rules_hash_unchanged(self) -> None:
        before = {path: _sha256(path) for path in _protected_paths()}

        with mock.patch("send_order_entrypoint.execute_send_order") as send_order, \
            mock.patch("kiwoom_order_adapter.send_order_stub") as kiwoom_send_order, \
            mock.patch("execution_broker_dispatch_orchestrator.orchestrate_broker_dispatch") as broker_dispatch:
            result = evaluate_kiwoom_send_order_safety(
                self._contract_result(),
                self._runtime_snapshot(),
                self._connection_state(),
                self._operator_context(),
            )

        self.assertEqual("SEND_ORDER_SAFE", result["status"])
        send_order.assert_not_called()
        kiwoom_send_order.assert_not_called()
        broker_dispatch.assert_not_called()
        self.assertEqual(before, {path: _sha256(path) for path in _protected_paths()})
        self.assertFalse((ROOT / "runtime" / "order_executions.json").exists())
        self.assertFalse((ROOT / "runtime" / "order_locks.json").exists())


if __name__ == "__main__":
    unittest.main()
