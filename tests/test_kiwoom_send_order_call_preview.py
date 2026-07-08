# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import unittest
from unittest import mock

from kiwoom_send_order_call_preview import preview_kiwoom_send_order_call


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


class KiwoomSendOrderCallPreviewTest(unittest.TestCase):
    def _send_order_params(self) -> dict[str, object]:
        return {
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

    def _adapter_contract(self, **overrides: object) -> dict[str, object]:
        params = self._send_order_params()
        result: dict[str, object] = {
            "status": "SEND_ORDER_CONTRACT_READY",
            "send_order_adapter_contract": {
                "dispatch_id": "DISPATCH_CALL_PREVIEW_1",
                "order_id": "ORDER_CALL_PREVIEW_1",
                "account_no": "12345678",
                "screen_no": "0101",
                "order_name": "BUY",
                "order_type": 1,
                "code": "003550",
                "quantity": 10,
                "price": 85000,
                "hoga": "03",
                "original_order_no": "",
                "send_order_params": deepcopy(params),
            },
            "send_order_params": params,
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

    def _safety(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "status": "SEND_ORDER_SAFE",
            "safety": {
                "dispatch_id": "DISPATCH_CALL_PREVIEW_1",
                "order_id": "ORDER_CALL_PREVIEW_1",
                "account_no": "12345678",
                "screen_no": "0101",
            },
            "issues": [],
            "warnings": [],
            "send_order_allowed": True,
            "send_order_called": False,
            "broker_called": False,
            "runtime_write": False,
            "queue_write": False,
        }
        result.update(overrides)
        return result

    def _call_context(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {"final_call_token": "FINAL_CALL_TOKEN_1"}
        result.update(overrides)
        return result

    def test_send_order_call_ready_normal(self) -> None:
        result = preview_kiwoom_send_order_call(
            self._safety(),
            self._adapter_contract(),
            self._call_context(),
        )

        self.assertEqual("SEND_ORDER_CALL_READY", result["status"])
        self.assertFalse(result["send_order_called"])
        self.assertFalse(result["broker_called"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_write"])
        self.assertEqual(
            ["0101", "BUY", "12345678", 1, "003550", 10, 85000, "03", ""],
            result["send_order_args"],
        )
        preview = result["send_order_call_preview"]
        self.assertEqual("KIWOOM_SEND_ORDER_CALL_PREVIEW", preview["preview_type"])
        self.assertEqual("FINAL_CALL_TOKEN_1", preview["final_call_token"])
        self.assertTrue(preview["send_order_args_ready"])
        self.assertEqual("ORDER_CALL_PREVIEW_1", preview["order_id"])

    def test_safety_blocked_returns_blocked(self) -> None:
        result = preview_kiwoom_send_order_call(
            self._safety(status="BLOCKED", send_order_allowed=False, issues=["blocked"]),
            self._adapter_contract(),
            self._call_context(),
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("safety_gate_result.status is not SEND_ORDER_SAFE", result["issues"])

    def test_safety_invalid_returns_invalid(self) -> None:
        result = preview_kiwoom_send_order_call(
            self._safety(status="INVALID", send_order_allowed=False, issues=["bad"]),
            self._adapter_contract(),
            self._call_context(),
        )

        self.assertEqual("INVALID", result["status"])
        self.assertIn("safety_gate_result.status is INVALID", result["issues"])

    def test_adapter_contract_blocked_returns_blocked(self) -> None:
        result = preview_kiwoom_send_order_call(
            self._safety(),
            self._adapter_contract(status="BLOCKED", issues=["blocked"]),
            self._call_context(),
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("adapter_contract_result.status is not SEND_ORDER_CONTRACT_READY", result["issues"])

    def test_adapter_contract_invalid_returns_invalid(self) -> None:
        result = preview_kiwoom_send_order_call(
            self._safety(),
            self._adapter_contract(status="INVALID", issues=["bad"]),
            self._call_context(),
        )

        self.assertEqual("INVALID", result["status"])
        self.assertIn("adapter_contract_result.status is INVALID", result["issues"])

    def test_final_call_token_missing_blocks(self) -> None:
        result = preview_kiwoom_send_order_call(
            self._safety(),
            self._adapter_contract(),
            {},
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("call_context.final_call_token is required", result["issues"])

    def test_send_order_params_missing_is_invalid(self) -> None:
        contract = self._adapter_contract()
        contract["send_order_params"] = {}
        contract["send_order_adapter_contract"]["send_order_params"] = {}

        result = preview_kiwoom_send_order_call(
            self._safety(),
            contract,
            self._call_context(),
        )

        self.assertEqual("INVALID", result["status"])
        self.assertIn("send_order_params is required", result["issues"])

    def test_malformed_input_is_invalid(self) -> None:
        result = preview_kiwoom_send_order_call(
            None,
            self._adapter_contract(),
            self._call_context(),
        )

        self.assertEqual("INVALID", result["status"])
        self.assertIn("safety_gate_result must be a dict", result["issues"])

    def test_inputs_are_not_mutated(self) -> None:
        safety = self._safety()
        contract = self._adapter_contract()
        context = self._call_context()
        originals = (deepcopy(safety), deepcopy(contract), deepcopy(context))

        result = preview_kiwoom_send_order_call(safety, contract, context)
        result["send_order_call_preview"]["order_id"] = "MUTATED"
        result["send_order_args"][0] = "9999"

        self.assertEqual(originals[0], safety)
        self.assertEqual(originals[1], contract)
        self.assertEqual(originals[2], context)

    def test_runtime_order_queue_rules_hash_unchanged(self) -> None:
        before = {path: _sha256(path) for path in _protected_paths()}

        with mock.patch("send_order_entrypoint.execute_send_order") as send_order, \
            mock.patch("kiwoom_order_adapter.send_order_stub") as kiwoom_send_order, \
            mock.patch("execution_broker_dispatch_orchestrator.orchestrate_broker_dispatch") as broker_dispatch:
            result = preview_kiwoom_send_order_call(
                self._safety(),
                self._adapter_contract(),
                self._call_context(),
            )

        self.assertEqual("SEND_ORDER_CALL_READY", result["status"])
        send_order.assert_not_called()
        kiwoom_send_order.assert_not_called()
        broker_dispatch.assert_not_called()
        self.assertEqual(before, {path: _sha256(path) for path in _protected_paths()})
        self.assertFalse((ROOT / "runtime" / "order_executions.json").exists())
        self.assertFalse((ROOT / "runtime" / "order_locks.json").exists())


if __name__ == "__main__":
    unittest.main()
