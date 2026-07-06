# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import hashlib
import unittest
from pathlib import Path
from unittest import mock

from execution_candidate_inspector import inspect_execution_candidate


class ExecutionCandidateInspectorTest(unittest.TestCase):
    def _gate(self, *, gate_result: str = "OPEN", blocked_reasons: list[str] | None = None) -> dict:
        return {
            "ok": gate_result == "OPEN",
            "stage": "SIGNAL_QUEUE_GATE",
            "gate_result": gate_result,
            "blocked_reasons": blocked_reasons or [],
            "signal": "BUY",
            "runtime_write": False,
            "execution_connected": False,
            "send_order_connected": False,
        }

    def _order(self, **overrides: object) -> dict:
        order = {
            "id": "ORDER_1",
            "status": "REAL_READY",
            "source_signal_id": "SIG_1",
            "price": 85000,
            "quantity": 10,
            "order_intent": {
                "side": "BUY",
                "hoga": "\uc2dc\uc7a5\uac00",
            },
        }
        order.update(overrides)
        return order

    def _queue_preview(self, **overrides: object) -> dict:
        preview = {
            "ok": True,
            "stage": "SIGNAL_GATE_EXECUTION_QUEUE_BRIDGE",
            "gate_result": "OPEN",
            "order_status": "REAL_READY",
            "queue_writer_preview_connected": True,
            "queue_write_preview_result": {
                "write_preview": True,
                "preview_only": True,
                "no_write": True,
                "blocked_reasons": [],
            },
            "runtime_write": False,
            "execution_connected": False,
            "send_order_connected": False,
        }
        preview.update(overrides)
        return preview

    def _sha256(self, path: Path) -> str | None:
        if not path.exists():
            return None
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def test_ready_candidate_is_eligible(self) -> None:
        result = inspect_execution_candidate(
            self._gate(),
            self._order(),
            self._queue_preview(),
        )

        self.assertEqual("READY", result["status"])
        self.assertTrue(result["eligible"])
        self.assertEqual([], result["issues"])
        self.assertEqual("READY_FOR_EXECUTION_PREVIEW", result["summary"])
        self.assertIn("Preview mode", result["warnings"])
        self.assertIn("Runtime write disabled", result["warnings"])
        self.assertIn("Execution disabled", result["warnings"])
        self.assertIn("SendOrder disabled", result["warnings"])

    def test_gate_not_open_is_blocked_with_gate_issue(self) -> None:
        for gate_result in ("POLICY_BLOCKED", "PRECHECK_FAILED", "INVALID_SIGNAL", "WAITING", "IGNORE"):
            with self.subTest(gate_result=gate_result):
                result = inspect_execution_candidate(
                    self._gate(gate_result=gate_result),
                    self._order(),
                    self._queue_preview(queue_writer_preview_connected=False),
                )

                self.assertEqual("BLOCKED", result["status"])
                self.assertFalse(result["eligible"])
                self.assertEqual([gate_result], result["issues"])
                self.assertEqual("BLOCKED_BY_POLICY", result["summary"])

    def test_gate_blocked_reasons_are_preserved_as_issues(self) -> None:
        result = inspect_execution_candidate(
            self._gate(gate_result="BLOCKED", blocked_reasons=["POLICY_BLOCKED", "WAITING"]),
            self._order(),
            self._queue_preview(),
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertEqual(["POLICY_BLOCKED", "WAITING"], result["issues"])

    def test_candidate_none_is_invalid(self) -> None:
        result = inspect_execution_candidate(
            self._gate(),
            None,
            self._queue_preview(),
        )

        self.assertEqual("INVALID", result["status"])
        self.assertFalse(result["eligible"])
        self.assertEqual(["NO_ORDER_CANDIDATE"], result["issues"])
        self.assertEqual("INVALID_EXECUTION_CANDIDATE", result["summary"])

    def test_missing_price_qty_order_type_and_hoga_are_invalid(self) -> None:
        result = inspect_execution_candidate(
            self._gate(),
            self._order(price=None, quantity=0, order_intent={}),
            self._queue_preview(),
        )

        self.assertEqual("INVALID", result["status"])
        self.assertFalse(result["eligible"])
        self.assertIn("MISSING_ORDER_PRICE", result["issues"])
        self.assertIn("MISSING_ORDER_QTY", result["issues"])
        self.assertIn("INVALID_ORDER_TYPE", result["issues"])
        self.assertIn("INVALID_HOGA", result["issues"])

    def test_candidate_before_real_ready_is_not_ready(self) -> None:
        result = inspect_execution_candidate(
            self._gate(),
            self._order(status="EXECUTABLE"),
            self._queue_preview(order_status="EXECUTABLE"),
        )

        self.assertEqual("NOT_READY", result["status"])
        self.assertFalse(result["eligible"])
        self.assertEqual(["SIGNAL_NOT_READY"], result["issues"])
        self.assertEqual("SIGNAL_NOT_READY", result["summary"])

    def test_queue_preview_failed_is_not_ready(self) -> None:
        result = inspect_execution_candidate(
            self._gate(),
            self._order(),
            self._queue_preview(
                queue_writer_preview_connected=False,
                queue_write_preview_result={
                    "write_preview": False,
                    "preview_only": True,
                    "no_write": True,
                    "blocked_reasons": ["QUEUE_PREVIEW_FAILED"],
                },
            ),
        )

        self.assertEqual("NOT_READY", result["status"])
        self.assertFalse(result["eligible"])
        self.assertEqual(["QUEUE_PREVIEW_FAILED"], result["issues"])
        self.assertEqual("QUEUE_PREVIEW_FAILED", result["summary"])

    def test_safety_flags_enabled_are_not_ready(self) -> None:
        result = inspect_execution_candidate(
            self._gate(),
            self._order(),
            self._queue_preview(
                runtime_write=True,
                execution_connected=True,
                send_order_connected=True,
            ),
        )

        self.assertEqual("NOT_READY", result["status"])
        self.assertFalse(result["eligible"])
        self.assertIn("RUNTIME_WRITE_ENABLED", result["issues"])
        self.assertIn("EXECUTION_CONNECTED", result["issues"])
        self.assertIn("SEND_ORDER_CONNECTED", result["issues"])

    def test_input_dicts_are_not_mutated(self) -> None:
        gate = self._gate()
        order = self._order()
        queue_preview = self._queue_preview()
        originals = (deepcopy(gate), deepcopy(order), deepcopy(queue_preview))

        inspect_execution_candidate(gate, order, queue_preview)

        self.assertEqual(originals[0], gate)
        self.assertEqual(originals[1], order)
        self.assertEqual(originals[2], queue_preview)

    def test_inspector_does_not_write_runtime_rules_or_call_execution_send_order(self) -> None:
        runtime_path = Path("runtime") / "order_queue.json"
        rules_path = Path("routines") / "\uc9c0\ud45c\ucd94\uc885\ub9e4\ub9e4" / "rules.json"
        before_runtime = self._sha256(runtime_path)
        before_rules = self._sha256(rules_path)

        with (
            mock.patch("pathlib.Path.write_text") as write_text,
            mock.patch("builtins.open", mock.mock_open()) as open_mock,
            mock.patch("execution_controller.build_execution_preview") as execution_controller,
            mock.patch("kiwoom_order_adapter.send_order_stub") as send_order_stub,
        ):
            result = inspect_execution_candidate(
                self._gate(),
                self._order(),
                self._queue_preview(),
            )

        self.assertEqual("READY", result["status"])
        write_text.assert_not_called()
        open_mock.assert_not_called()
        execution_controller.assert_not_called()
        send_order_stub.assert_not_called()
        self.assertEqual(before_runtime, self._sha256(runtime_path))
        self.assertEqual(before_rules, self._sha256(rules_path))


if __name__ == "__main__":
    unittest.main()
