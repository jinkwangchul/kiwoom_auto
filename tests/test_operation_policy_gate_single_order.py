# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import operation_policy_gate


class OperationPolicyGateSingleOrderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.runtime_dir = self.root / "runtime"
        self.stocks_dir = self.root / "stocks"
        self.order_queue_path = self.runtime_dir / "order_queue.json"
        self.operation_state_path = self.runtime_dir / "operation_state.json"
        self.stock_dir = self.stocks_dir / "003550_LG"
        self.stock_dir.mkdir(parents=True)
        self.runtime_dir.mkdir(parents=True)
        self._patches = [
            patch.object(operation_policy_gate, "RUNTIME_DIR", self.runtime_dir),
            patch.object(operation_policy_gate, "STOCKS_DIR", self.stocks_dir),
            patch.object(operation_policy_gate, "ORDER_QUEUE_PATH", self.order_queue_path),
            patch.object(operation_policy_gate, "OPERATION_STATE_PATH", self.operation_state_path),
        ]
        for item in self._patches:
            item.start()
        self._write_json(self.operation_state_path, {})

    def tearDown(self) -> None:
        for item in reversed(self._patches):
            item.stop()
        self.tmp.cleanup()

    def _write_json(self, path: Path, value: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")

    def _read_queue(self) -> dict:
        return json.loads(self.order_queue_path.read_text(encoding="utf-8"))

    def _write_state(self, **extra: object) -> None:
        state = {
            "status": "MONITORING",
            "trade_enabled": True,
            "real_trade_enabled": False,
            "buy_enabled": False,
            "sell_enabled": False,
        }
        state.update(extra)
        self._write_json(self.stock_dir / "state.json", state)

    def _write_queue(self, status: str = "APPROVED", order_id: str = "ORDER_1") -> None:
        self._write_json(
            self.order_queue_path,
            {
                "version": 1,
                "updated_at": "",
                "orders": [
                    {
                        "id": order_id,
                        "status": status,
                        "approval_status": "APPROVED" if status == "APPROVED" else "",
                        "code": "003550",
                        "name": "LG",
                        "side": "SELL",
                        "candidate_status": "CANDIDATE_READY",
                        "quantity": 10,
                        "price": 100.0,
                        "execution_enabled": False,
                    }
                ],
            },
        )

    def _single_order(self) -> dict:
        orders = self._read_queue().get("orders", [])
        self.assertEqual(1, len(orders))
        return orders[0]

    def test_approved_order_promotes_to_executable(self) -> None:
        self._write_state()
        self._write_queue(status="APPROVED")

        result = operation_policy_gate.apply_operation_policy_gate_for_order(
            "ORDER_1",
            queue_path=self.order_queue_path,
        )
        order = self._single_order()

        self.assertTrue(result["ok"])
        self.assertTrue(result["changed"])
        self.assertEqual("EXECUTABLE", result["after_status"])
        self.assertEqual("EXECUTABLE", order.get("status"))
        self.assertEqual("EXECUTABLE", order.get("policy_status"))
        self.assertFalse(order.get("execution_enabled"))

    def test_approved_order_blocked_by_policy(self) -> None:
        self._write_state(review_required=True)
        self._write_queue(status="APPROVED")

        result = operation_policy_gate.apply_operation_policy_gate_for_order(
            "ORDER_1",
            queue_path=self.order_queue_path,
        )
        order = self._single_order()

        self.assertTrue(result["ok"])
        self.assertTrue(result["changed"])
        self.assertEqual("BLOCKED_POLICY", result["after_status"])
        self.assertEqual("BLOCKED_POLICY", order.get("status"))
        self.assertEqual("BLOCKED_POLICY", order.get("policy_status"))
        self.assertFalse(order.get("execution_enabled"))

    def test_non_approved_order_is_skipped(self) -> None:
        self._write_state()
        self._write_queue(status="PENDING")

        result = operation_policy_gate.apply_operation_policy_gate_for_order(
            "ORDER_1",
            queue_path=self.order_queue_path,
        )
        order = self._single_order()

        self.assertTrue(result["ok"])
        self.assertFalse(result["changed"])
        self.assertEqual("skipped", result["status"])
        self.assertEqual("PENDING", order.get("status"))
        self.assertFalse(order.get("execution_enabled"))

    def test_missing_order_id_is_not_found(self) -> None:
        self._write_state()
        self._write_queue(status="APPROVED")

        result = operation_policy_gate.apply_operation_policy_gate_for_order(
            "ORDER_MISSING",
            queue_path=self.order_queue_path,
        )
        order = self._single_order()

        self.assertFalse(result["ok"])
        self.assertFalse(result["changed"])
        self.assertEqual("not_found", result["status"])
        self.assertEqual("APPROVED", order.get("status"))
        self.assertFalse(order.get("execution_enabled"))


if __name__ == "__main__":
    unittest.main()
