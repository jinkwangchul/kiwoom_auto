# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import unittest
from unittest import mock

import execution_controller
from execution_controller import build_execution_preview


class ExecutionControllerPreviewTest(unittest.TestCase):
    def _order(self, *, status: str = "REAL_READY") -> dict:
        return {
            "id": "ORDER_1",
            "status": status,
            "side": "BUY",
            "code": "003550",
            "quantity": 10,
            "price": 85000,
            "execution_enabled": False,
            "order_intent": {
                "side": "BUY",
                "hoga": "\uc2dc\uc7a5\uac00",
            },
        }

    def test_real_ready_order_builds_preview_in_memory(self) -> None:
        result = build_execution_preview(self._order(), guard={"account_no": "12345678"})

        self.assertTrue(result["ok"])
        self.assertEqual("EXECUTION_PREVIEW", result["stage"])
        self.assertEqual("ORDER_1", result["order_id"])
        self.assertEqual("REAL_READY", result["status"])
        self.assertTrue(result["status_is_real_ready"])
        self.assertEqual("MARKET", result["hoga_preview"]["hoga"])
        self.assertEqual("BUY", result["order_type_preview"]["order_type"])
        self.assertFalse(result["unresolved"])
        self.assertTrue(result["adapter_request_preview"]["available"])
        self.assertTrue(result["adapter_request_preview"]["request_preview_built"])
        self.assertFalse(result["adapter_request_preview"]["send_order_called"])

    def test_non_real_ready_status_stays_unresolved(self) -> None:
        result = build_execution_preview(self._order(status="APPROVED"))

        self.assertFalse(result["ok"])
        self.assertEqual("APPROVED", result["status"])
        self.assertFalse(result["status_is_real_ready"])
        self.assertTrue(result["unresolved"])
        self.assertTrue(result["warnings"])

    def test_mapper_unresolved_stays_unresolved(self) -> None:
        order = self._order()
        order["order_intent"]["hoga"] = "\ubbf8\ud655\uc815"

        result = build_execution_preview(order)

        self.assertFalse(result["ok"])
        self.assertTrue(result["hoga_preview"]["unresolved"])
        self.assertTrue(result["unresolved"])

    def test_adapter_preview_can_be_checked_without_guard(self) -> None:
        result = build_execution_preview(self._order())

        self.assertTrue(result["adapter_request_preview"]["available"])
        self.assertFalse(result["adapter_request_preview"]["request_preview_built"])
        self.assertFalse(result["adapter_request_preview"]["send_order_called"])

    def test_input_order_is_not_mutated(self) -> None:
        order = self._order()
        original = deepcopy(order)

        build_execution_preview(order, guard={"account_no": "12345678"})

        self.assertEqual(original, order)

    def test_send_order_is_not_called(self) -> None:
        with mock.patch("kiwoom_order_adapter.send_order_stub") as send_stub:
            result = build_execution_preview(self._order(), guard={"account_no": "12345678"})

        self.assertTrue(result["adapter_request_preview"]["request_preview_built"])
        send_stub.assert_not_called()

    def test_adapter_unavailable_is_reported_without_failure(self) -> None:
        with mock.patch.object(execution_controller, "build_kiwoom_order_request", None):
            result = build_execution_preview(self._order(), guard={"account_no": "12345678"})

        self.assertFalse(result["adapter_request_preview"]["available"])
        self.assertFalse(result["adapter_request_preview"]["request_preview_built"])
        self.assertFalse(result["adapter_request_preview"]["send_order_called"])
        self.assertTrue(result["warnings"])


if __name__ == "__main__":
    unittest.main()
