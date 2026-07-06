# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from order_queue_reader import read_real_ready_order_by_id


class OrderQueueRealReadyReaderTest(unittest.TestCase):
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

    def _order(self, *, order_id: str = "ORDER_1", status: str = "REAL_READY") -> dict:
        return {
            "id": order_id,
            "status": status,
            "code": "003550",
            "side": "BUY",
            "quantity": 10,
            "price": 85000,
            "execution_enabled": True,
        }

    def test_reads_real_ready_order(self) -> None:
        order = self._order()
        self._write_queue([order])

        result = read_real_ready_order_by_id("ORDER_1", queue_path=self.queue_path)

        self.assertTrue(result["ok"])
        self.assertEqual("ORDER_QUEUE_REAL_READY_READ", result["stage"])
        self.assertEqual(order, result["order"])
        self.assertEqual([], result["blocked_reasons"])
        self.assertIsNone(result["error"])

    def test_missing_file_returns_not_found(self) -> None:
        missing_path = self.root / "missing_order_queue.json"

        result = read_real_ready_order_by_id("ORDER_1", queue_path=missing_path)

        self.assertFalse(result["ok"])
        self.assertIsNone(result["order"])
        self.assertIn("order_queue file not found", result["blocked_reasons"])
        self.assertIsNone(result["error"])

    def test_json_error_returns_error(self) -> None:
        self.queue_path.write_text("{not-json", encoding="utf-8")

        result = read_real_ready_order_by_id("ORDER_1", queue_path=self.queue_path)

        self.assertFalse(result["ok"])
        self.assertIsNone(result["order"])
        self.assertTrue(result["error"])

    def test_missing_order_id_returns_not_found(self) -> None:
        self._write_queue([self._order()])

        result = read_real_ready_order_by_id("", queue_path=self.queue_path)

        self.assertFalse(result["ok"])
        self.assertIsNone(result["order"])
        self.assertIn("order_id is required", result["blocked_reasons"])
        self.assertIsNone(result["error"])

    def test_unmatched_order_id_returns_not_found(self) -> None:
        self._write_queue([self._order(order_id="ORDER_1")])

        result = read_real_ready_order_by_id("ORDER_MISSING", queue_path=self.queue_path)

        self.assertFalse(result["ok"])
        self.assertIsNone(result["order"])
        self.assertIn("order_id not found", result["blocked_reasons"])
        self.assertIsNone(result["error"])

    def test_non_real_ready_status_is_blocked(self) -> None:
        self._write_queue([self._order(status="APPROVED")])

        result = read_real_ready_order_by_id("ORDER_1", queue_path=self.queue_path)

        self.assertFalse(result["ok"])
        self.assertIsNone(result["order"])
        self.assertIn("order status is not REAL_READY: APPROVED", result["blocked_reasons"])
        self.assertIsNone(result["error"])

    def test_file_content_is_not_mutated(self) -> None:
        self._write_queue([self._order()])
        before = self.queue_path.read_text(encoding="utf-8")

        read_real_ready_order_by_id("ORDER_1", queue_path=self.queue_path)

        self.assertEqual(before, self.queue_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
