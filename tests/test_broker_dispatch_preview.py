# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import unittest
from unittest import mock

from broker_dispatch_preview import preview_broker_dispatch


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


class BrokerDispatchPreviewTest(unittest.TestCase):
    def _dispatch_builder(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "status": "DISPATCH_READY",
            "dispatch_contract": {
                "account_no": "12345678",
                "broker_type": "KIWOOM",
                "order_id": "ORDER_BROKER_PREVIEW_1",
                "source_order_id": "ORDER_BROKER_PREVIEW_1",
                "source_signal_id": "SIGNAL_BROKER_PREVIEW_1",
                "code": "003550",
                "side": "BUY",
                "quantity": 10,
                "price": 85000,
                "hoga": "\uc2dc\uc7a5\uac00",
                "request_hash": "HASH_BROKER_PREVIEW_1",
                "dispatch_id": "DISPATCH_BROKER_PREVIEW_1",
                "created_at": "2026-07-07 10:00:00",
            },
            "issues": [],
            "warnings": [],
            "send_order_ready": True,
            "send_order_called": False,
            "broker_called": False,
        }
        result.update(overrides)
        return result

    def _capabilities(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "supported_brokers": ["KIWOOM"],
            "supported_sides": ["BUY", "SELL"],
            "supported_hogas": ["MARKET", "LIMIT"],
        }
        result.update(overrides)
        return result

    def _market(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "market_open": True,
            "session_open": True,
            "status": "OPEN",
        }
        result.update(overrides)
        return result

    def test_broker_dispatch_ready_normal(self) -> None:
        result = preview_broker_dispatch(
            self._dispatch_builder(),
            self._capabilities(),
            self._market(),
        )

        self.assertEqual("BROKER_DISPATCH_READY", result["status"])
        self.assertTrue(result["preview_only"])
        self.assertFalse(result["broker_called"])
        self.assertFalse(result["send_order_called"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_write"])
        self.assertTrue(result["broker_dispatch_preview"]["send_order_params_ready"])
        params = result["send_order_params_preview"]
        self.assertEqual("12345678", params["account_no"])
        self.assertEqual("KIWOOM", params["broker_type"])
        self.assertEqual("ORDER_BROKER_PREVIEW_1", params["order_id"])
        self.assertEqual("BUY", params["side"])
        self.assertEqual(10, params["quantity"])
        self.assertEqual(85000, params["price"])
        self.assertEqual("MARKET", params["hoga"])

    def test_dispatch_blocked_returns_blocked(self) -> None:
        result = preview_broker_dispatch(
            self._dispatch_builder(status="BLOCKED", send_order_ready=False, issues=["blocked"]),
            self._capabilities(),
            self._market(),
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("dispatch_builder_result.status is not DISPATCH_READY", result["issues"])

    def test_dispatch_invalid_returns_invalid(self) -> None:
        result = preview_broker_dispatch(
            self._dispatch_builder(status="INVALID", send_order_ready=False, issues=["bad"]),
            self._capabilities(),
            self._market(),
        )

        self.assertEqual("INVALID", result["status"])
        self.assertIn("dispatch_builder_result.status is INVALID", result["issues"])

    def test_broker_unsupported_blocks(self) -> None:
        result = preview_broker_dispatch(
            self._dispatch_builder(),
            self._capabilities(supported_brokers=["OTHER"]),
            self._market(),
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("broker_type is not supported", result["issues"])

    def test_market_closed_blocks(self) -> None:
        result = preview_broker_dispatch(
            self._dispatch_builder(),
            self._capabilities(),
            self._market(market_open=False),
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("market/session is not open", result["issues"])

    def test_malformed_broker_capabilities_is_invalid(self) -> None:
        result = preview_broker_dispatch(
            self._dispatch_builder(),
            {"supported_brokers": "KIWOOM"},
            self._market(),
        )

        self.assertEqual("INVALID", result["status"])
        self.assertIn("broker_capabilities.supported_brokers must be a list", result["issues"])

    def test_send_order_params_preview_created_for_limit(self) -> None:
        builder = self._dispatch_builder()
        builder["dispatch_contract"]["hoga"] = "\uc9c0\uc815\uac00"
        result = preview_broker_dispatch(builder, self._capabilities(), self._market())

        self.assertEqual("BROKER_DISPATCH_READY", result["status"])
        self.assertEqual("LIMIT", result["send_order_params_preview"]["hoga"])

    def test_inputs_are_not_mutated(self) -> None:
        builder = self._dispatch_builder()
        capabilities = self._capabilities()
        market = self._market()
        originals = (deepcopy(builder), deepcopy(capabilities), deepcopy(market))

        result = preview_broker_dispatch(builder, capabilities, market)
        result["send_order_params_preview"]["order_id"] = "MUTATED"
        result["broker_dispatch_preview"]["dispatch_contract"]["order_id"] = "MUTATED"

        self.assertEqual(originals[0], builder)
        self.assertEqual(originals[1], capabilities)
        self.assertEqual(originals[2], market)

    def test_runtime_order_queue_rules_hash_unchanged(self) -> None:
        before = {path: _sha256(path) for path in _protected_paths()}

        with mock.patch("send_order_entrypoint.execute_send_order") as send_order, \
            mock.patch("execution_broker_dispatch_orchestrator.orchestrate_broker_dispatch") as broker_dispatch:
            result = preview_broker_dispatch(
                self._dispatch_builder(),
                self._capabilities(),
                self._market(),
            )

        self.assertEqual("BROKER_DISPATCH_READY", result["status"])
        send_order.assert_not_called()
        broker_dispatch.assert_not_called()
        self.assertEqual(before, {path: _sha256(path) for path in _protected_paths()})
        self.assertFalse((ROOT / "runtime" / "order_executions.json").exists())
        self.assertFalse((ROOT / "runtime" / "order_locks.json").exists())


if __name__ == "__main__":
    unittest.main()
