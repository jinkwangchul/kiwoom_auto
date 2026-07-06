# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from execution_preview_order_service import preview_execution_for_real_ready_order


class ExecutionPreviewOrderServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.queue_path = self.root / "order_queue.json"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write_queue(self, orders: list[dict]) -> None:
        self.queue_path.write_text(
            json.dumps({"version": 1, "updated_at": "", "orders": orders}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _order(self, *, status: str = "REAL_READY") -> dict:
        return {
            "id": "ORDER_1",
            "status": status,
            "source_signal_id": "SIG_1",
            "code": "003550",
            "side": "BUY",
            "quantity": 10,
            "price": 85000,
            "execution_enabled": True,
            "order_intent": {
                "side": "BUY",
                "hoga": "\uc2dc\uc7a5\uac00",
            },
        }

    def _guard(self, *, operator_confirmed: bool = True) -> dict:
        return {
            "operator_confirmed": operator_confirmed,
            "real_trade_enabled": True,
            "account_no": "12345678",
        }

    def test_real_ready_order_preview_success(self) -> None:
        self._write_queue([self._order()])

        result = preview_execution_for_real_ready_order(
            "ORDER_1",
            self._guard(),
            queue_path=self.queue_path,
        )

        self.assertTrue(result["ok"])
        self.assertEqual("REAL_READY_ORDER_EXECUTION_PREVIEW", result["stage"])
        self.assertTrue(result["read_result"]["ok"])
        self.assertTrue(result["preview_result"]["ok"])
        self.assertTrue(result["preview_result"]["summary"]["ready_for_execution_request"])
        self.assertTrue(result["preview_result"]["approval_result"]["approved"])
        self.assertEqual("EXECUTION_CANDIDATE", result["preview_result"]["approval_result"]["next_stage"])

    def test_preview_is_not_called_when_order_read_fails(self) -> None:
        self._write_queue([self._order()])

        with mock.patch("execution_preview_order_service.preview_execution_for_order") as preview:
            result = preview_execution_for_real_ready_order(
                "ORDER_MISSING",
                self._guard(),
                queue_path=self.queue_path,
            )

        self.assertFalse(result["ok"])
        self.assertFalse(result["read_result"]["ok"])
        self.assertIsNone(result["preview_result"])
        preview.assert_not_called()

    def test_non_real_ready_order_is_blocked(self) -> None:
        self._write_queue([self._order(status="APPROVED")])

        result = preview_execution_for_real_ready_order(
            "ORDER_1",
            self._guard(),
            queue_path=self.queue_path,
        )

        self.assertFalse(result["ok"])
        self.assertFalse(result["read_result"]["ok"])
        self.assertIsNone(result["preview_result"])
        self.assertIn("order status is not REAL_READY: APPROVED", result["read_result"]["blocked_reasons"])

    def test_guard_block_is_reflected_in_summary(self) -> None:
        self._write_queue([self._order()])

        result = preview_execution_for_real_ready_order(
            "ORDER_1",
            self._guard(operator_confirmed=False),
            queue_path=self.queue_path,
        )

        self.assertFalse(result["ok"])
        self.assertTrue(result["read_result"]["ok"])
        self.assertFalse(result["preview_result"]["ok"])
        self.assertEqual("final_guard", result["preview_result"]["summary"]["blocked_stage"])
        self.assertIn(
            "guard.operator_confirmed is not true",
            result["preview_result"]["summary"]["blocked_reasons"],
        )

    def test_file_content_is_not_mutated(self) -> None:
        self._write_queue([self._order()])
        before = self.queue_path.read_text(encoding="utf-8")

        preview_execution_for_real_ready_order(
            "ORDER_1",
            self._guard(),
            queue_path=self.queue_path,
        )

        self.assertEqual(before, self.queue_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
