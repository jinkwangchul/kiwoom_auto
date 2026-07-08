# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import unittest
from unittest import mock

from execution_order_dispatch_builder import build_order_dispatch_contract


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


class OrderDispatchBuilderTest(unittest.TestCase):
    def _queue_item(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "id": "ORDER_QUEUED_ORDER_DISPATCH_1",
            "status": "ORDER_QUEUED",
            "source_order_id": "ORDER_DISPATCH_1",
            "order_id": "ORDER_DISPATCH_1",
            "source_signal_id": "SIGNAL_DISPATCH_1",
            "code": "003550",
            "side": "BUY",
            "quantity": 10,
            "price": 85000,
            "hoga": "\uc2dc\uc7a5\uac00",
            "request_hash": "HASH_DISPATCH_1",
            "send_order_called": False,
            "execution_enabled": False,
        }
        result.update(overrides)
        return result

    def _review(self, **overrides: object) -> dict[str, object]:
        queue_item = self._queue_item()
        result: dict[str, object] = {
            "review_type": "EXECUTION_QUEUE_COMMIT_RESULT_REVIEW",
            "status": "REVIEW_OK",
            "review": {
                "commit_id": "QUEUE_COMMIT_DISPATCH_1",
                "order_id": "ORDER_DISPATCH_1",
                "queue_item": queue_item,
                "commit_report": {"committed_record": deepcopy(queue_item)},
            },
            "issues": [],
            "warnings": [],
            "send_order_ready": True,
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

    def _broker_profile(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {"broker_type": "KIWOOM"}
        result.update(overrides)
        return result

    def test_dispatch_ready_normal(self) -> None:
        result = build_order_dispatch_contract(
            self._review(),
            self._account_context(),
            self._broker_profile(),
        )

        self.assertEqual("DISPATCH_READY", result["status"])
        self.assertTrue(result["send_order_ready"])
        self.assertFalse(result["send_order_called"])
        self.assertFalse(result["broker_called"])
        contract = result["dispatch_contract"]
        self.assertEqual("12345678", contract["account_no"])
        self.assertEqual("KIWOOM", contract["broker_type"])
        self.assertEqual("ORDER_DISPATCH_1", contract["order_id"])
        self.assertEqual("SIGNAL_DISPATCH_1", contract["source_signal_id"])
        self.assertEqual("003550", contract["code"])
        self.assertEqual("BUY", contract["side"])
        self.assertEqual(10, contract["quantity"])
        self.assertEqual(85000, contract["price"])
        self.assertEqual("\uc2dc\uc7a5\uac00", contract["hoga"])
        self.assertEqual("HASH_DISPATCH_1", contract["request_hash"])
        self.assertTrue(contract["dispatch_id"])
        self.assertTrue(contract["created_at"])

    def test_review_blocked_returns_blocked(self) -> None:
        result = build_order_dispatch_contract(
            self._review(status="REVIEW_BLOCKED", issues=["queue item not found"], send_order_ready=False),
            self._account_context(),
            self._broker_profile(),
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("review_result.status is not REVIEW_OK", result["issues"])
        self.assertFalse(result["send_order_ready"])

    def test_review_invalid_returns_invalid(self) -> None:
        result = build_order_dispatch_contract(
            self._review(status="INVALID", issues=["bad"], send_order_ready=False),
            self._account_context(),
            self._broker_profile(),
        )

        self.assertEqual("INVALID", result["status"])
        self.assertIn("review_result.status is INVALID", result["issues"])

    def test_account_context_missing_is_invalid(self) -> None:
        result = build_order_dispatch_contract(
            self._review(),
            {},
            self._broker_profile(),
        )

        self.assertEqual("INVALID", result["status"])
        self.assertIn("account_context must be a non-empty dict", result["issues"])

    def test_broker_profile_malformed_is_invalid(self) -> None:
        result = build_order_dispatch_contract(
            self._review(),
            self._account_context(),
            {"name": "missing type"},
        )

        self.assertEqual("INVALID", result["status"])
        self.assertIn("broker_profile.broker_type is required", result["issues"])

    def test_inputs_are_not_mutated(self) -> None:
        review = self._review()
        account_context = self._account_context()
        broker_profile = self._broker_profile()
        originals = (deepcopy(review), deepcopy(account_context), deepcopy(broker_profile))

        result = build_order_dispatch_contract(review, account_context, broker_profile)
        result["dispatch_contract"]["queue_item"]["order_id"] = "MUTATED"

        self.assertEqual(originals[0], review)
        self.assertEqual(originals[1], account_context)
        self.assertEqual(originals[2], broker_profile)

    def test_runtime_order_queue_rules_hash_unchanged(self) -> None:
        before = {path: _sha256(path) for path in _protected_paths()}

        with mock.patch("send_order_entrypoint.execute_send_order") as send_order, \
            mock.patch("execution_broker_dispatch_orchestrator.orchestrate_broker_dispatch") as broker_dispatch:
            result = build_order_dispatch_contract(
                self._review(),
                self._account_context(),
                self._broker_profile(),
            )

        self.assertEqual("DISPATCH_READY", result["status"])
        send_order.assert_not_called()
        broker_dispatch.assert_not_called()
        self.assertEqual(before, {path: _sha256(path) for path in _protected_paths()})
        self.assertFalse((ROOT / "runtime" / "order_executions.json").exists())
        self.assertFalse((ROOT / "runtime" / "order_locks.json").exists())


if __name__ == "__main__":
    unittest.main()
