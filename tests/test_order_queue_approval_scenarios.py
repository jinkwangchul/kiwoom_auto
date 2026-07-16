# -*- coding: utf-8 -*-
from __future__ import annotations

import importlib
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import order_candidate_engine
import order_approval_engine
import order_queue
import operation_policy_gate
import routine_signal_consumer
import routine_signal_order_bridge
import routine_signal_queue


class _FakeStockRepository:
    def __init__(self, stock_dir: Path):
        self.stock_dir = stock_dir

    def resolve_stock_dir(self, code: str, name: str = "") -> Path:
        return self.stock_dir


class OrderQueueApprovalScenarioTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.runtime_dir = self.root / "runtime"
        self.stocks_dir = self.root / "stocks"
        self.stock_dir = self.stocks_dir / "003550_LG"
        self.queue_path = self.runtime_dir / "routine_signals.json"
        self.order_queue_path = self.runtime_dir / "order_queue.json"
        self.stock_dir.mkdir(parents=True)
        self.runtime_dir.mkdir(parents=True)
        self._patches = [
            patch.object(order_candidate_engine, "STOCKS_DIR", self.stocks_dir),
            patch.object(order_queue, "RUNTIME_DIR", self.runtime_dir),
            patch.object(order_queue, "SIGNAL_QUEUE_PATH", self.queue_path),
            patch.object(order_queue, "ORDER_QUEUE_PATH", self.order_queue_path),
            patch.object(order_approval_engine, "RUNTIME_DIR", self.runtime_dir),
            patch.object(order_approval_engine, "ORDER_QUEUE_PATH", self.order_queue_path),
            patch.object(operation_policy_gate, "RUNTIME_DIR", self.runtime_dir),
            patch.object(operation_policy_gate, "STOCKS_DIR", self.stocks_dir),
            patch.object(operation_policy_gate, "ORDER_QUEUE_PATH", self.order_queue_path),
            patch.object(operation_policy_gate, "OPERATION_STATE_PATH", self.runtime_dir / "operation_state.json"),
            patch.object(routine_signal_queue, "RUNTIME_DIR", self.runtime_dir),
            patch.object(routine_signal_queue, "QUEUE_PATH", self.queue_path),
            patch.object(routine_signal_order_bridge, "RUNTIME_DIR", self.runtime_dir),
            patch.object(routine_signal_order_bridge, "SIGNAL_QUEUE_PATH", self.queue_path),
            patch.object(
                routine_signal_order_bridge,
                "StockRepository",
                lambda: _FakeStockRepository(self.stock_dir),
            ),
        ]
        for item in self._patches:
            item.start()
        importlib.reload(routine_signal_consumer)

    def tearDown(self) -> None:
        importlib.reload(routine_signal_consumer)
        for item in reversed(self._patches):
            item.stop()
        self.tmp.cleanup()

    def _write_json(self, path: Path, value: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")

    def _setup_stock(self, *, holding_qty: int = 0, entry_amount: int | None = None) -> None:
        config = {"routine": "지표추종매매"}
        if entry_amount is not None:
            config["entry_amount"] = entry_amount
        state = {
            "status": "MONITORING",
            "trade_enabled": True,
            "real_trade_enabled": False,
            "buy_enabled": False,
            "sell_enabled": False,
            "holding_qty": holding_qty,
        }
        candles = [{"time": "2026-07-03 09:00:00", "close": 100.0}]
        self._write_json(self.stock_dir / "config.json", config)
        self._write_json(self.stock_dir / "state.json", state)
        self._write_json(self.stock_dir / "candles.json", candles)
        self._write_json(self.stock_dir / "orders.json", {"orders": []})
        self._write_json(self.runtime_dir / "operation_state.json", {})

    def _write_signal(self, *, signal: str, signal_id: str) -> None:
        self._write_json(
            self.queue_path,
            {
                "version": 1,
                "updated_at": "",
                "signals": [
                    {
                        "id": signal_id,
                        "status": "PENDING",
                        "routine": "지표추종매매",
                        "code": "003550",
                        "name": "LG",
                        "signal": signal,
                        "reason": "test signal",
                        "tick_key": "unit-test",
                        "execution_enabled": False,
                    }
                ],
            },
        )

    def _consume(self) -> dict:
        return routine_signal_consumer.consume_pending_routine_signals_dry_run(
            limit=None,
            mark_previewed=True,
            write_order_queue=True,
            apply_approval=True,
        )

    def _single_order(self) -> dict:
        data = json.loads(self.order_queue_path.read_text(encoding="utf-8"))
        orders = data.get("orders", [])
        self.assertEqual(1, len(orders))
        return orders[0]

    def _single_signal(self) -> dict:
        data = json.loads(self.queue_path.read_text(encoding="utf-8"))
        signals = data.get("signals", [])
        self.assertEqual(1, len(signals))
        return signals[0]

    def _write_pending_order_queue(self) -> None:
        self._write_json(
            self.order_queue_path,
            {
                "version": 1,
                "revision": 0,
                "updated_at": "",
                "orders": [
                    {
                        "id": "ORDER_APPROVAL_1",
                        "status": "PENDING",
                        "source_signal_id": "SIG_APPROVAL_1",
                        "code": "003550",
                        "name": "LG",
                        "side": "BUY",
                        "order_type": "BUY_SIGNAL_CANDIDATE",
                        "quantity": 10,
                        "amount": 1000,
                        "price": 100,
                        "candidate_status": "CANDIDATE_READY",
                        "execution_enabled": False,
                    }
                ],
            },
        )

    def test_sell_without_holding_creates_blocked_candidate(self) -> None:
        self._setup_stock(holding_qty=0)
        self._write_signal(signal="SELL", signal_id="SIG_SELL_ZERO")

        with patch.object(operation_policy_gate, "apply_operation_policy_gate_for_order", wraps=operation_policy_gate.apply_operation_policy_gate_for_order) as policy_gate:
            result = self._consume()
        order = self._single_order()
        signal = self._single_signal()

        self.assertEqual("NO_HOLDING_QTY", order.get("candidate_status"))
        self.assertEqual(0, order.get("quantity"))
        self.assertEqual("BLOCKED", order.get("status"))
        self.assertEqual("BLOCKED", order.get("approval_status"))
        self.assertFalse(order.get("execution_enabled"))
        self.assertEqual(1, result["summary"]["approval_checked"])
        self.assertEqual(0, result["summary"]["approved"])
        self.assertEqual(0, result["summary"]["policy_checked"])
        policy_gate.assert_not_called()
        self.assertEqual("BLOCKED", signal.get("status"))
        self.assertEqual("NO_HOLDING_QTY", signal.get("payload_candidate_status"))
        intent = order.get("order_intent", {})
        self.assertEqual("SELL", intent.get("side"))
        self.assertEqual("order_candidate_engine", intent.get("source"))
        self.assertEqual("REAL_OR_STATE_HOLDING_ZERO", intent.get("holding_source"))
        self.assertEqual("latest_price", intent.get("price_basis"))
        self.assertIsNone(intent.get("source_ui_path"))
        self.assertTrue(intent.get("unresolved"))
        provenance = order.get("order_provenance", {})
        self.assertEqual("routine_signals", provenance.get("source"))
        self.assertEqual("SIG_SELL_ZERO", provenance.get("source_signal_id"))
        self.assertEqual("003550", provenance.get("code"))
        self.assertEqual("LG", provenance.get("name"))
        self.assertEqual("SELL", provenance.get("signal"))
        self.assertEqual("test signal", provenance.get("reason"))
        self.assertEqual([], provenance.get("matched_groups"))
        self.assertEqual([], provenance.get("details"))
        self.assertIsNone(provenance.get("source_ui_path"))
        self.assertIsNone(provenance.get("rule_path"))
        self.assertIsNone(provenance.get("setting_set"))
        self.assertTrue(provenance.get("unresolved"))

    def test_sell_with_holding_keeps_execution_disabled(self) -> None:
        self._setup_stock(holding_qty=10)
        self._write_signal(signal="SELL", signal_id="SIG_SELL_HOLDING")

        result = self._consume()
        order = self._single_order()
        signal = self._single_signal()

        self.assertEqual("CANDIDATE_READY", order.get("candidate_status"))
        self.assertGreater(order.get("quantity"), 0)
        self.assertEqual("EXECUTABLE", order.get("status"))
        self.assertEqual("APPROVED", order.get("approval_status"))
        self.assertEqual("EXECUTABLE", order.get("policy_status"))
        self.assertFalse(order.get("execution_enabled"))
        self.assertEqual(1, result["summary"]["approval_checked"])
        self.assertEqual(1, result["summary"]["approved"])
        self.assertEqual(1, result["summary"]["policy_checked"])
        self.assertEqual(1, result["summary"]["policy_executable"])
        self.assertEqual(0, result["summary"]["policy_blocked"])
        self.assertEqual(0, result["summary"]["policy_errors"])
        self.assertEqual("BLOCKED", signal.get("status"))
        self.assertNotIn(order.get("status"), {"REAL_READY"})
        intent = order.get("order_intent", {})
        self.assertEqual("SELL", intent.get("side"))
        self.assertEqual("order_candidate_engine", intent.get("source"))
        self.assertEqual("REAL_OR_STATE_HOLDING", intent.get("holding_source"))
        self.assertEqual("latest_price", intent.get("price_basis"))
        self.assertIsNone(intent.get("source_ui_path"))
        self.assertTrue(intent.get("unresolved"))
        provenance = order.get("order_provenance", {})
        self.assertEqual("SIG_SELL_HOLDING", provenance.get("source_signal_id"))
        self.assertEqual("SELL", provenance.get("signal"))
        self.assertTrue(provenance.get("unresolved"))

    def test_buy_candidate_keeps_execution_disabled(self) -> None:
        self._setup_stock(holding_qty=0, entry_amount=1000)
        self._write_signal(signal="BUY", signal_id="SIG_BUY")

        result = self._consume()
        order = self._single_order()
        signal = self._single_signal()

        self.assertEqual("BUY", order.get("side"))
        self.assertEqual("CANDIDATE_READY", order.get("candidate_status"))
        self.assertGreater(order.get("quantity"), 0)
        self.assertEqual("EXECUTABLE", order.get("status"))
        self.assertEqual("APPROVED", order.get("approval_status"))
        self.assertEqual("EXECUTABLE", order.get("policy_status"))
        self.assertFalse(order.get("execution_enabled"))
        self.assertEqual(1, result["summary"]["approval_checked"])
        self.assertEqual(1, result["summary"]["approved"])
        self.assertEqual(1, result["summary"]["policy_checked"])
        self.assertEqual(1, result["summary"]["policy_executable"])
        self.assertEqual(0, result["summary"]["policy_blocked"])
        self.assertEqual(0, result["summary"]["policy_errors"])
        self.assertEqual("BLOCKED", signal.get("status"))
        self.assertNotIn(order.get("status"), {"REAL_READY"})
        intent = order.get("order_intent", {})
        self.assertEqual("BUY", intent.get("side"))
        self.assertEqual("order_candidate_engine", intent.get("source"))
        self.assertEqual("entry_amount", intent.get("budget_source"))
        self.assertEqual("latest_price", intent.get("price_basis"))
        self.assertIsNone(intent.get("source_ui_path"))
        self.assertTrue(intent.get("unresolved"))
        provenance = order.get("order_provenance", {})
        self.assertEqual("SIG_BUY", provenance.get("source_signal_id"))
        self.assertEqual("BUY", provenance.get("signal"))
        self.assertTrue(provenance.get("unresolved"))

    def test_policy_gate_failure_blocks_signal_status_update(self) -> None:
        self._setup_stock(holding_qty=10)
        self._write_signal(signal="SELL", signal_id="SIG_POLICY_ERROR")

        with patch.object(
            operation_policy_gate,
            "apply_operation_policy_gate_for_order",
            side_effect=RuntimeError("policy gate failed"),
        ):
            result = self._consume()

        order = self._single_order()
        signal = self._single_signal()

        self.assertFalse(result["order_queue"]["ok"])
        self.assertEqual(1, result["summary"]["orders_created"])
        self.assertEqual(1, result["summary"]["policy_checked"])
        self.assertEqual(1, result["summary"]["policy_errors"])
        self.assertEqual(1, result["summary"]["marked_error"])
        self.assertEqual("APPROVED", order.get("status"))
        self.assertEqual("APPROVED", order.get("approval_status"))
        self.assertFalse(order.get("execution_enabled"))
        self.assertEqual("PENDING", signal.get("status"))
        self.assertEqual(
            "operation policy gate failed; signal status update skipped",
            result["status_updates"][0]["reason"],
        )

    def test_duplicate_candidate_does_not_call_policy_gate(self) -> None:
        self._setup_stock(holding_qty=10)
        self._write_signal(signal="SELL", signal_id="SIG_DUPLICATE")
        existing = order_queue.signal_to_order_candidate(self._single_signal(), 1)
        self.assertIsNotNone(existing)
        if existing is not None:
            existing["status"] = "APPROVED"
            existing["approval_status"] = "APPROVED"
            self._write_json(
                self.order_queue_path,
                {"version": 1, "revision": 0, "updated_at": "", "orders": [existing]},
            )

        with patch.object(operation_policy_gate, "apply_operation_policy_gate_for_order") as policy_gate:
            result = self._consume()

        queue = json.loads(self.order_queue_path.read_text(encoding="utf-8"))
        self.assertEqual(1, len(queue.get("orders", [])))
        self.assertEqual(0, result["summary"]["orders_created"])
        self.assertEqual(0, result["summary"]["policy_checked"])
        self.assertEqual(0, result["summary"]["policy_errors"])
        policy_gate.assert_not_called()

    def test_apply_order_approval_uses_canonical_writer_metadata(self) -> None:
        self._write_pending_order_queue()

        result = order_approval_engine.apply_order_approval_to_queue()
        order = self._single_order()
        queue = json.loads(self.order_queue_path.read_text(encoding="utf-8"))

        self.assertEqual(1, result["checked"])
        self.assertEqual(1, result["approved"])
        self.assertTrue(result["committed"])
        self.assertTrue(result["queue_committed"])
        self.assertTrue(result["post_write_verified"])
        self.assertEqual(0, result["revision_before"])
        self.assertEqual(1, result["revision_after"])
        self.assertEqual(1, queue["revision"])
        self.assertEqual("legacy_order_approval_apply", result["approval_result"]["operation_name"])
        self.assertEqual("APPROVED", order["status"])
        self.assertEqual("APPROVED", order["approval_status"])
        self.assertFalse(order["execution_enabled"])
        self.assertNotIn(order["status"], {"EXECUTABLE", "REAL_READY"})

    def test_apply_order_approval_has_no_direct_write_text_path(self) -> None:
        source = Path(order_approval_engine.__file__).read_text(encoding="utf-8")
        self.assertNotIn("write_text(", source)
        self.assertNotIn("json.dump(", source)
        self.assertNotIn("def write_order_queue", source)
        self.assertNotIn("_apply_order_approval_to_queue_legacy_snapshot_replace", source)

    def test_two_threads_apply_approval_once_with_canonical_noop_second(self) -> None:
        self._write_pending_order_queue()
        start = threading.Event()
        results: list[dict] = []

        def worker() -> None:
            start.wait(5)
            results.append(order_approval_engine.apply_order_approval_to_queue())

        threads = [threading.Thread(target=worker), threading.Thread(target=worker)]
        for thread in threads:
            thread.start()
        start.set()
        for thread in threads:
            thread.join(10)

        self.assertEqual(2, len(results))
        queue = json.loads(self.order_queue_path.read_text(encoding="utf-8"))
        order = self._single_order()
        self.assertEqual("APPROVED", order["status"])
        self.assertEqual("APPROVED", order["approval_status"])
        self.assertEqual(1, queue["revision"])
        self.assertEqual(1, sum(1 for item in results if item.get("approved") == 1 and item.get("committed") is True))
        self.assertEqual(1, sum(1 for item in results if item.get("checked") == 0 and item.get("committed") is False))


if __name__ == "__main__":
    unittest.main()
