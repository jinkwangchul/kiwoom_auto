# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import tempfile
import threading
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

    def _order(self, status: str = "APPROVED", order_id: str = "ORDER_1") -> dict:
        return {
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

    def _write_queue(
        self,
        status: str = "APPROVED",
        order_id: str = "ORDER_1",
        *,
        revision: int | None = None,
        orders: list[dict] | None = None,
    ) -> None:
        data = {
            "version": 1,
            "updated_at": "",
            "orders": orders if orders is not None else [self._order(status=status, order_id=order_id)],
        }
        if revision is not None:
            data["revision"] = revision
        self._write_json(
            self.order_queue_path,
            data,
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
        self.assertTrue(result["committed"])
        self.assertTrue(result["queue_committed"])
        self.assertEqual("EXECUTABLE", result["after_status"])
        self.assertEqual("EXECUTABLE", order.get("status"))
        self.assertEqual("EXECUTABLE", order.get("policy_status"))
        self.assertFalse(order.get("execution_enabled"))
        self.assertEqual(1, self._read_queue().get("revision"))

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
        self.assertTrue(result["committed"])
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

        self.assertFalse(result["ok"])
        self.assertFalse(result["changed"])
        self.assertEqual("blocked", result["status"])
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

    def test_duplicate_order_id_is_blocked_without_write(self) -> None:
        self._write_state()
        self._write_queue(orders=[self._order(order_id="ORDER_1"), self._order(order_id="ORDER_1")])

        result = operation_policy_gate.apply_operation_policy_gate_for_order(
            "ORDER_1",
            queue_path=self.order_queue_path,
        )
        queue = self._read_queue()

        self.assertFalse(result["ok"])
        self.assertFalse(result["changed"])
        self.assertFalse(result["committed"])
        self.assertEqual("duplicate_identity", result["status"])
        self.assertNotIn("revision", queue)
        self.assertEqual(["APPROVED", "APPROVED"], [order["status"] for order in queue["orders"]])

    def test_stale_expected_revision_is_blocked_without_write(self) -> None:
        self._write_state()
        self._write_queue(status="APPROVED", revision=2)

        result = operation_policy_gate.apply_operation_policy_gate_for_order(
            "ORDER_1",
            queue_path=self.order_queue_path,
            expected_revision=1,
        )
        queue = self._read_queue()

        self.assertFalse(result["committed"])
        self.assertFalse(result["changed"])
        self.assertTrue(result["cas_checked"])
        self.assertEqual(2, queue.get("revision"))
        self.assertEqual("APPROVED", queue["orders"][0]["status"])

    def test_same_result_reapply_is_noop(self) -> None:
        self._write_state()
        order = self._order(status="EXECUTABLE")
        order["policy_status"] = "EXECUTABLE"
        self._write_queue(orders=[order], revision=3)

        result = operation_policy_gate.apply_operation_policy_gate_for_order(
            "ORDER_1",
            queue_path=self.order_queue_path,
        )
        queue = self._read_queue()

        self.assertTrue(result["ok"])
        self.assertFalse(result["changed"])
        self.assertFalse(result["committed"])
        self.assertEqual("noop", result["status"])
        self.assertEqual(3, queue.get("revision"))
        self.assertEqual("EXECUTABLE", queue["orders"][0]["status"])

    def test_different_records_can_mutate_concurrently_without_loss(self) -> None:
        self._write_state()
        self._write_queue(orders=[self._order(order_id="ORDER_1"), self._order(order_id="ORDER_2")])
        results: list[dict] = []

        def worker(order_id: str) -> None:
            results.append(
                operation_policy_gate.apply_operation_policy_gate_for_order(
                    order_id,
                    queue_path=self.order_queue_path,
                )
            )

        threads = [threading.Thread(target=worker, args=(order_id,)) for order_id in ("ORDER_1", "ORDER_2")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        queue = self._read_queue()

        self.assertEqual(2, len(results))
        self.assertEqual(2, sum(1 for result in results if result.get("committed")))
        self.assertEqual(2, queue.get("revision"))
        self.assertEqual(["EXECUTABLE", "EXECUTABLE"], [order["status"] for order in queue["orders"]])

    def test_same_record_concurrent_mutation_commits_once(self) -> None:
        self._write_state()
        self._write_queue(status="APPROVED")
        results: list[dict] = []

        def worker() -> None:
            results.append(
                operation_policy_gate.apply_operation_policy_gate_for_order(
                    "ORDER_1",
                    queue_path=self.order_queue_path,
                )
            )

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        queue = self._read_queue()

        self.assertEqual(2, len(results))
        self.assertEqual(1, sum(1 for result in results if result.get("committed")))
        self.assertEqual(1, queue.get("revision"))
        self.assertEqual("EXECUTABLE", queue["orders"][0]["status"])

    def test_operation_policy_gate_has_no_direct_snapshot_writer(self) -> None:
        source = Path(operation_policy_gate.__file__).read_text(encoding="utf-8")

        self.assertNotIn("write_text", source)
        self.assertNotIn("json.dump", source)
        self.assertNotIn("write_order_queue(", source)
        self.assertNotIn("_write_order_queue", source)


if __name__ == "__main__":
    unittest.main()
