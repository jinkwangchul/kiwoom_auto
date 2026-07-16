# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import unittest
from unittest import mock

from kiwoom_send_order_adapter_contract import build_kiwoom_send_order_adapter_contract


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


class KiwoomSendOrderAdapterContractTest(unittest.TestCase):
    def _broker_preview(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "status": "BROKER_DISPATCH_READY",
            "broker_dispatch_preview": {
                "preview_type": "BROKER_DISPATCH_PREVIEW",
                "broker_type": "KIWOOM",
                "send_order_params_ready": True,
            },
            "send_order_params_preview": {
                "account_no": "12345678",
                "broker_type": "KIWOOM",
                "order_id": "ORDER_KIWOOM_CONTRACT_1",
                "source_order_id": "ORDER_KIWOOM_CONTRACT_1",
                "source_signal_id": "SIGNAL_KIWOOM_CONTRACT_1",
                "code": "003550",
                "side": "BUY",
                "quantity": 10,
                "price": 85000,
                "hoga": "MARKET",
                "request_hash": "HASH_KIWOOM_CONTRACT_1",
                "dispatch_id": "DISPATCH_KIWOOM_CONTRACT_1",
            },
            "issues": [],
            "warnings": [],
            "preview_only": True,
            "broker_called": False,
            "send_order_called": False,
            "runtime_write": False,
            "queue_write": False,
        }
        result.update(overrides)
        return result

    def _account_context(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {"account_no": "12345678"}
        result.update(overrides)
        return result

    def _screen_context(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {"screen_no": "0101"}
        result.update(overrides)
        return result

    def test_send_order_contract_ready_normal(self) -> None:
        result = build_kiwoom_send_order_adapter_contract(
            self._broker_preview(),
            self._account_context(),
            self._screen_context(),
        )

        self.assertEqual("SEND_ORDER_CONTRACT_READY", result["status"])
        self.assertTrue(result["preview_only"])
        self.assertFalse(result["send_order_called"])
        self.assertFalse(result["broker_called"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_write"])

        contract = result["send_order_adapter_contract"]
        for field in (
            "dispatch_id",
            "order_id",
            "account_no",
            "screen_no",
            "order_name",
            "order_type",
            "code",
            "quantity",
            "price",
            "hoga",
            "original_order_no",
            "send_order_params",
        ):
            self.assertIn(field, contract)

        self.assertEqual("DISPATCH_KIWOOM_CONTRACT_1", contract["dispatch_id"])
        self.assertEqual("ORDER_KIWOOM_CONTRACT_1", contract["order_id"])
        self.assertEqual("12345678", contract["account_no"])
        self.assertEqual("0101", contract["screen_no"])
        self.assertEqual("BUY", contract["order_name"])
        self.assertEqual(1, contract["order_type"])
        self.assertEqual("003550", contract["code"])
        self.assertEqual(10, contract["quantity"])
        self.assertEqual(85000, contract["price"])
        self.assertEqual("03", contract["hoga"])
        self.assertEqual("", contract["original_order_no"])
        self.assertEqual(contract["send_order_params"], result["send_order_params"])

    def test_broker_dispatch_blocked_returns_blocked(self) -> None:
        result = build_kiwoom_send_order_adapter_contract(
            self._broker_preview(status="BLOCKED", issues=["blocked"]),
            self._account_context(),
            self._screen_context(),
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("broker_dispatch_preview_result.status is not BROKER_DISPATCH_READY", result["issues"])

    def test_broker_dispatch_invalid_returns_invalid(self) -> None:
        result = build_kiwoom_send_order_adapter_contract(
            self._broker_preview(status="INVALID", issues=["bad"]),
            self._account_context(),
            self._screen_context(),
        )

        self.assertEqual("INVALID", result["status"])
        self.assertIn("broker_dispatch_preview_result.status is INVALID", result["issues"])

    def test_broker_type_not_kiwoom_blocks(self) -> None:
        preview = self._broker_preview()
        preview["send_order_params_preview"]["broker_type"] = "OTHER"

        result = build_kiwoom_send_order_adapter_contract(
            preview,
            self._account_context(),
            self._screen_context(),
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("broker_type is not KIWOOM", result["issues"])

    def test_account_no_missing_is_invalid(self) -> None:
        preview = self._broker_preview()
        preview["send_order_params_preview"]["account_no"] = ""

        result = build_kiwoom_send_order_adapter_contract(
            preview,
            {},
            self._screen_context(),
        )

        self.assertEqual("INVALID", result["status"])
        self.assertIn("account_no is required", result["issues"])

    def test_screen_no_missing_is_invalid(self) -> None:
        result = build_kiwoom_send_order_adapter_contract(
            self._broker_preview(),
            self._account_context(),
            {},
        )

        self.assertEqual("INVALID", result["status"])
        self.assertIn("screen_no is required", result["issues"])

    def test_hoga_mapping_failure_is_invalid(self) -> None:
        preview = self._broker_preview()
        preview["send_order_params_preview"]["hoga"] = "UNKNOWN"

        result = build_kiwoom_send_order_adapter_contract(
            preview,
            self._account_context(),
            self._screen_context(),
        )

        self.assertEqual("INVALID", result["status"])
        self.assertIn("hoga mapping failed", result["issues"])

    def test_order_type_mapping_failure_is_invalid(self) -> None:
        preview = self._broker_preview()
        preview["send_order_params_preview"]["side"] = "HOLD"

        result = build_kiwoom_send_order_adapter_contract(
            preview,
            self._account_context(),
            self._screen_context(),
        )

        self.assertEqual("INVALID", result["status"])
        self.assertIn("order_type mapping failed", result["issues"])

    def test_limit_order_maps_to_kiwoom_limit_code(self) -> None:
        preview = self._broker_preview()
        preview["send_order_params_preview"]["side"] = "SELL"
        preview["send_order_params_preview"]["hoga"] = "LIMIT"
        preview["send_order_params_preview"]["price"] = 85000

        result = build_kiwoom_send_order_adapter_contract(
            preview,
            self._account_context(),
            self._screen_context(),
        )

        self.assertEqual("SEND_ORDER_CONTRACT_READY", result["status"])
        self.assertEqual(2, result["send_order_params"]["order_type"])
        self.assertEqual("00", result["send_order_params"]["hoga"])

    def test_buy_cancel_maps_to_kiwoom_cancel_type_with_original_order_no(self) -> None:
        preview = self._broker_preview()
        preview["send_order_params_preview"].update(
            {
                "side": "BUY",
                "order_action": "CANCEL",
                "quantity": 3,
                "price": 0,
                "hoga": "LIMIT",
                "original_order_no": "12345",
            }
        )

        result = build_kiwoom_send_order_adapter_contract(
            preview,
            self._account_context(),
            self._screen_context(),
        )

        self.assertEqual("SEND_ORDER_CONTRACT_READY", result["status"], result)
        self.assertEqual("BUY_CANCEL", result["send_order_params"]["order_name"])
        self.assertEqual(3, result["send_order_params"]["order_type"])
        self.assertEqual(0, result["send_order_params"]["price"])
        self.assertEqual("12345", result["send_order_params"]["original_order_no"])

    def test_sell_modify_maps_to_kiwoom_modify_type(self) -> None:
        preview = self._broker_preview()
        preview["send_order_params_preview"].update(
            {
                "side": "SELL",
                "order_action": "MODIFY",
                "quantity": 2,
                "price": 1200,
                "hoga": "LIMIT",
                "original_order_no": "67890",
            }
        )

        result = build_kiwoom_send_order_adapter_contract(
            preview,
            self._account_context(),
            self._screen_context(),
        )

        self.assertEqual("SEND_ORDER_CONTRACT_READY", result["status"], result)
        self.assertEqual("SELL_MODIFY", result["send_order_params"]["order_name"])
        self.assertEqual(6, result["send_order_params"]["order_type"])
        self.assertEqual("67890", result["send_order_params"]["original_order_no"])

    def test_cancel_modify_requires_original_order_no(self) -> None:
        preview = self._broker_preview()
        preview["send_order_params_preview"].update({"order_action": "CANCEL", "price": 0, "hoga": "LIMIT"})

        result = build_kiwoom_send_order_adapter_contract(
            preview,
            self._account_context(),
            self._screen_context(),
        )

        self.assertEqual("INVALID", result["status"])
        self.assertIn("original_order_no is required for cancel/modify", result["issues"])

    def test_inputs_are_not_mutated(self) -> None:
        preview = self._broker_preview()
        account_context = self._account_context()
        screen_context = self._screen_context()
        originals = (deepcopy(preview), deepcopy(account_context), deepcopy(screen_context))

        result = build_kiwoom_send_order_adapter_contract(preview, account_context, screen_context)
        result["send_order_adapter_contract"]["order_id"] = "MUTATED"
        result["send_order_params"]["account_no"] = "MUTATED"

        self.assertEqual(originals[0], preview)
        self.assertEqual(originals[1], account_context)
        self.assertEqual(originals[2], screen_context)

    def test_runtime_order_queue_rules_hash_unchanged(self) -> None:
        before = {path: _sha256(path) for path in _protected_paths()}

        with mock.patch("send_order_entrypoint.execute_send_order") as send_order, \
            mock.patch("execution_broker_dispatch_orchestrator.orchestrate_broker_dispatch") as broker_dispatch:
            result = build_kiwoom_send_order_adapter_contract(
                self._broker_preview(),
                self._account_context(),
                self._screen_context(),
            )

        self.assertEqual("SEND_ORDER_CONTRACT_READY", result["status"])
        send_order.assert_not_called()
        broker_dispatch.assert_not_called()
        self.assertEqual(before, {path: _sha256(path) for path in _protected_paths()})
        self.assertFalse((ROOT / "runtime" / "order_executions.json").exists())
        self.assertFalse((ROOT / "runtime" / "order_locks.json").exists())


if __name__ == "__main__":
    unittest.main()
