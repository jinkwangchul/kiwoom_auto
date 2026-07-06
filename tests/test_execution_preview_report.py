# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import hashlib
import unittest
from pathlib import Path
from unittest import mock

from execution_preview_report import build_execution_preview_report


class ExecutionPreviewReportTest(unittest.TestCase):
    def _gate(self, *, gate_result: str = "OPEN", blocked_reasons: list[str] | None = None) -> dict:
        return {
            "ok": gate_result == "OPEN",
            "stage": "SIGNAL_QUEUE_GATE",
            "gate_result": gate_result,
            "gate_reason": "test gate reason",
            "candidate_result": "READY",
            "signal": "BUY",
            "decision": "ACCEPT",
            "policy_result": "PASS",
            "blocked_reasons": blocked_reasons or [],
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
        }

    def _queue_preview(self, *, connected: bool = True) -> dict:
        return {
            "ok": connected,
            "stage": "SIGNAL_GATE_EXECUTION_QUEUE_BRIDGE",
            "bridge_result": "QUEUE_WRITER_PREVIEW_READY" if connected else "BLOCKED",
            "bridge_reason": "gate OPEN and REAL_READY order connected to queue writer preview"
            if connected
            else "PRECHECK_FAILED",
            "gate_result": "OPEN",
            "signal": "BUY",
            "order_status": "REAL_READY",
            "queue_writer_preview_connected": connected,
            "queue_write_preview_result": {
                "write_preview": connected,
                "write_stage": "order_queued_record_preview_created" if connected else "queue_pending",
                "preview_only": True,
                "no_write": True,
                "blocked_reasons": [] if connected else ["PRECHECK_FAILED"],
                "order_queued_record_preview": {"status": "ORDER_QUEUED"} if connected else None,
            },
            "queue_connected": False,
            "runtime_write": False,
            "execution_connected": False,
            "send_order_connected": False,
        }

    def _sha256(self, path: Path) -> str | None:
        if not path.exists():
            return None
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def test_open_real_ready_preview_connected_builds_eligible_report(self) -> None:
        result = build_execution_preview_report(
            self._gate(),
            self._order(),
            self._queue_preview(),
        )

        self.assertTrue(result["ok"])
        self.assertEqual("EXECUTION_PREVIEW_REPORT", result["stage"])
        self.assertTrue(result["eligible"])
        self.assertEqual("OPEN", result["gate"])
        self.assertEqual("BUY", result["signal"])
        self.assertEqual("REAL_READY", result["candidate"])
        self.assertTrue(result["preview_connected"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["execution_connected"])
        self.assertFalse(result["send_order_connected"])
        self.assertEqual("READY_FOR_EXECUTION_PREVIEW", result["reason"])
        self.assertEqual([], result["blocked_reasons"])
        self.assertIn("Execution Preview Report", result["text"])
        self.assertIn("Gate: OPEN", result["text"])
        self.assertIn("Eligible : True", result["text"])
        self.assertIn("Blocked Reason\n---------\nNone", result["text"])

    def test_non_open_gate_is_not_eligible_and_reports_existing_gate_result(self) -> None:
        for gate_result in ("POLICY_BLOCKED", "PRECHECK_FAILED", "INVALID_SIGNAL", "WAITING", "IGNORE"):
            with self.subTest(gate_result=gate_result):
                result = build_execution_preview_report(
                    self._gate(gate_result=gate_result),
                    self._order(),
                    self._queue_preview(connected=False),
                )

                self.assertFalse(result["eligible"])
                self.assertFalse(result["preview_connected"])
                self.assertEqual([gate_result], result["blocked_reasons"])
                self.assertIn(f"- {gate_result}", result["text"])

    def test_gate_blocked_reasons_are_preserved(self) -> None:
        result = build_execution_preview_report(
            self._gate(gate_result="BLOCKED", blocked_reasons=["POLICY_BLOCKED", "WAITING"]),
            self._order(),
            self._queue_preview(connected=False),
        )

        self.assertFalse(result["eligible"])
        self.assertEqual(["POLICY_BLOCKED", "WAITING"], result["blocked_reasons"])
        self.assertIn("- POLICY_BLOCKED", result["text"])
        self.assertIn("- WAITING", result["text"])

    def test_open_gate_without_real_ready_order_is_blocked(self) -> None:
        result = build_execution_preview_report(
            self._gate(),
            self._order(status="APPROVED"),
            self._queue_preview(),
        )

        self.assertFalse(result["eligible"])
        self.assertEqual("APPROVED", result["candidate"])
        self.assertEqual(["order.status is not REAL_READY"], result["blocked_reasons"])

    def test_open_gate_without_queue_preview_connection_is_blocked(self) -> None:
        result = build_execution_preview_report(
            self._gate(),
            self._order(),
            self._queue_preview(connected=False),
        )

        self.assertFalse(result["eligible"])
        self.assertFalse(result["preview_connected"])
        self.assertEqual(["PRECHECK_FAILED"], result["blocked_reasons"])
        self.assertIn("Preview Connected : False", result["text"])

    def test_input_dicts_are_not_mutated(self) -> None:
        gate = self._gate()
        order = self._order()
        queue_preview = self._queue_preview()
        originals = (deepcopy(gate), deepcopy(order), deepcopy(queue_preview))

        build_execution_preview_report(gate, order, queue_preview)

        self.assertEqual(originals[0], gate)
        self.assertEqual(originals[1], order)
        self.assertEqual(originals[2], queue_preview)

    def test_report_generation_does_not_write_runtime_rules_or_call_execution_send_order(self) -> None:
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
            result = build_execution_preview_report(
                self._gate(),
                self._order(),
                self._queue_preview(),
            )

        self.assertTrue(result["eligible"])
        write_text.assert_not_called()
        open_mock.assert_not_called()
        execution_controller.assert_not_called()
        send_order_stub.assert_not_called()
        self.assertEqual(before_runtime, self._sha256(runtime_path))
        self.assertEqual(before_rules, self._sha256(rules_path))


if __name__ == "__main__":
    unittest.main()
