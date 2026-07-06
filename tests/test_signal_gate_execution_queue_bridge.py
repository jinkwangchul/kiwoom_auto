# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import hashlib
import unittest
from pathlib import Path
from unittest import mock

from signal_gate_execution_queue_bridge import build_signal_gate_execution_queue_bridge


class SignalGateExecutionQueueBridgeTest(unittest.TestCase):
    def _gate(self, *, gate_result: str = "OPEN") -> dict:
        return {
            "ok": True,
            "stage": "SIGNAL_QUEUE_GATE",
            "gate_result": gate_result,
            "gate_reason": "test gate",
            "candidate_result": "READY",
            "signal": "BUY",
            "decision": "ACCEPT",
            "policy_result": "PASS",
            "rule_source": "rules.json",
            "matched_rule_paths": ["buy.groups[0]"],
            "condition_summary": [{"name": "rsi", "ok": True}],
            "applied_policies": ["TIME"],
            "blocked_policy": None,
            "signal_index": 1,
            "delay_bar": 0,
            "queue_connected": False,
            "runtime_write": False,
            "execution_connected": False,
            "send_order_connected": False,
        }

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

    def _guard(self) -> dict:
        return {
            "operator_confirmed": True,
            "real_trade_enabled": True,
            "account_no": "12345678",
        }

    def _runtime_queue_hash(self) -> str | None:
        path = Path("runtime") / "order_queue.json"
        if not path.exists():
            return None
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def test_open_gate_and_real_ready_order_connects_writer_preview(self) -> None:
        before_hash = self._runtime_queue_hash()

        result = build_signal_gate_execution_queue_bridge(
            self._gate(),
            self._order(),
            guard=self._guard(),
        )

        self.assertTrue(result["ok"])
        self.assertEqual("SIGNAL_GATE_EXECUTION_QUEUE_BRIDGE", result["stage"])
        self.assertEqual("QUEUE_WRITER_PREVIEW_READY", result["bridge_result"])
        self.assertTrue(result["queue_writer_preview_connected"])
        self.assertFalse(result["queue_connected"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["execution_connected"])
        self.assertFalse(result["send_order_connected"])
        write_preview = result["queue_write_preview_result"]
        self.assertTrue(write_preview["write_preview"])
        self.assertTrue(write_preview["preview_only"])
        self.assertTrue(write_preview["no_write"])
        self.assertEqual("order_queued_record_preview_created", write_preview["write_stage"])
        self.assertEqual("ORDER_QUEUED", write_preview["order_queued_record_preview"]["status"])
        self.assertFalse(write_preview["order_queued_record_preview"]["execution_enabled"])
        self.assertFalse(write_preview["order_queued_record_preview"]["send_order_called"])
        self.assertEqual(before_hash, self._runtime_queue_hash())

    def test_blocked_waiting_invalid_and_ignore_gates_do_not_call_writer_preview(self) -> None:
        for gate_result in ("BLOCKED", "WAITING", "INVALID", "IGNORE"):
            with self.subTest(gate_result=gate_result):
                builder = mock.Mock(return_value={"queue_write_preview_result": {"write_preview": True}})

                result = build_signal_gate_execution_queue_bridge(
                    self._gate(gate_result=gate_result),
                    self._order(),
                    guard=self._guard(),
                    execution_preview_builder=builder,
                )

                builder.assert_not_called()
                self.assertFalse(result["queue_writer_preview_connected"])
                self.assertIsNone(result["queue_write_preview_result"])
                self.assertFalse(result["queue_connected"])
                self.assertFalse(result["runtime_write"])
                self.assertFalse(result["execution_connected"])
                self.assertFalse(result["send_order_connected"])
                if gate_result == "IGNORE":
                    self.assertTrue(result["ok"])
                    self.assertEqual("IGNORE", result["bridge_result"])
                else:
                    self.assertFalse(result["ok"])
                    self.assertEqual("BLOCKED", result["bridge_result"])

    def test_open_gate_with_non_real_ready_order_does_not_call_writer_preview(self) -> None:
        builder = mock.Mock(return_value={"queue_write_preview_result": {"write_preview": True}})

        result = build_signal_gate_execution_queue_bridge(
            self._gate(),
            self._order(status="APPROVED"),
            guard=self._guard(),
            execution_preview_builder=builder,
        )

        builder.assert_not_called()
        self.assertFalse(result["ok"])
        self.assertEqual("BLOCKED", result["bridge_result"])
        self.assertIn("REAL_READY", result["bridge_reason"])
        self.assertFalse(result["queue_writer_preview_connected"])

    def test_invalid_gate_is_blocked_without_writer_preview(self) -> None:
        builder = mock.Mock(return_value={"queue_write_preview_result": {"write_preview": True}})

        result = build_signal_gate_execution_queue_bridge(
            {"stage": "OTHER", "gate_result": "OPEN"},
            self._order(),
            guard=self._guard(),
            execution_preview_builder=builder,
        )

        builder.assert_not_called()
        self.assertFalse(result["ok"])
        self.assertEqual("BLOCKED", result["bridge_result"])
        self.assertFalse(result["queue_writer_preview_connected"])

    def test_writer_preview_block_is_reported_without_runtime_write(self) -> None:
        blocked_writer = {
            "queue_write_preview_result": {
                "write_preview": False,
                "write_stage": "queue_pending",
                "preview_only": True,
                "no_write": True,
                "blocked_reasons": ["queue pending blocked"],
                "order_queued_record_preview": None,
            }
        }

        result = build_signal_gate_execution_queue_bridge(
            self._gate(),
            self._order(),
            guard=self._guard(),
            execution_preview_builder=mock.Mock(return_value=blocked_writer),
        )

        self.assertFalse(result["ok"])
        self.assertEqual("BLOCKED", result["bridge_result"])
        self.assertEqual("queue pending blocked", result["bridge_reason"])
        self.assertTrue(result["queue_writer_preview_connected"])
        self.assertFalse(result["runtime_write"])
        self.assertIsNone(result["queue_write_preview_result"]["order_queued_record_preview"])

    def test_existing_order_duplicates_are_checked_by_writer_preview(self) -> None:
        existing_orders = [{"request_hash": "not-the-current-hash"}]
        first = build_signal_gate_execution_queue_bridge(
            self._gate(),
            self._order(),
            guard=self._guard(),
            existing_orders=existing_orders,
        )
        current_hash = first["queue_write_preview_result"]["order_queued_record_preview"]["request_hash"]

        result = build_signal_gate_execution_queue_bridge(
            self._gate(),
            self._order(),
            guard=self._guard(),
            existing_orders=[{"request_hash": current_hash, "lock_id": "OTHER", "order_id": "OTHER"}],
        )

        self.assertFalse(result["ok"])
        self.assertEqual("BLOCKED", result["bridge_result"])
        self.assertEqual("duplicate request_hash", result["bridge_reason"])
        self.assertTrue(result["queue_writer_preview_connected"])
        self.assertFalse(result["runtime_write"])

    def test_input_dicts_are_not_mutated(self) -> None:
        gate = self._gate()
        order = self._order()
        guard = self._guard()
        original_gate = deepcopy(gate)
        original_order = deepcopy(order)
        original_guard = deepcopy(guard)

        build_signal_gate_execution_queue_bridge(gate, order, guard=guard)

        self.assertEqual(original_gate, gate)
        self.assertEqual(original_order, order)
        self.assertEqual(original_guard, guard)

    def test_success_does_not_write_runtime_or_call_send_order(self) -> None:
        with (
            mock.patch("pathlib.Path.write_text") as write_text,
            mock.patch("execution_queue_writer.commit_execution_queue_write") as commit_write,
            mock.patch("kiwoom_order_adapter.send_order_stub") as send_order_stub,
        ):
            result = build_signal_gate_execution_queue_bridge(
                self._gate(),
                self._order(),
                guard=self._guard(),
            )

        self.assertTrue(result["ok"])
        write_text.assert_not_called()
        commit_write.assert_not_called()
        send_order_stub.assert_not_called()


if __name__ == "__main__":
    unittest.main()
