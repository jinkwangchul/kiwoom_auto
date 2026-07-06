# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import hashlib
import unittest
from pathlib import Path
from unittest import mock

from execution_readiness_summary import build_execution_readiness_summary


class ExecutionReadinessSummaryTest(unittest.TestCase):
    def _gate(self, *, gate_result: str = "OPEN", blocked_reasons: list[str] | None = None) -> dict:
        return {
            "ok": gate_result == "OPEN",
            "stage": "SIGNAL_QUEUE_GATE",
            "gate_result": gate_result,
            "blocked_reasons": blocked_reasons or [],
            "signal": "BUY",
        }

    def _order(self, *, status: str = "REAL_READY") -> dict:
        return {
            "id": "ORDER_1",
            "status": status,
            "price": 85000,
            "quantity": 10,
            "order_intent": {"side": "BUY", "hoga": "\uc2dc\uc7a5\uac00"},
        }

    def _queue_preview(self, *, connected: bool = True, **overrides: object) -> dict:
        preview = {
            "ok": connected,
            "stage": "SIGNAL_GATE_EXECUTION_QUEUE_BRIDGE",
            "gate_result": "OPEN",
            "order_status": "REAL_READY",
            "queue_writer_preview_connected": connected,
            "queue_write_preview_result": {
                "write_preview": connected,
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

    def _report(self, *, ok: bool = True, eligible: bool = True, connected: bool = True) -> dict:
        return {
            "ok": ok,
            "stage": "EXECUTION_PREVIEW_REPORT",
            "eligible": eligible,
            "gate": "OPEN",
            "candidate": "REAL_READY",
            "preview_connected": connected,
            "runtime_write": False,
            "execution_connected": False,
            "send_order_connected": False,
            "reason": "READY_FOR_EXECUTION_PREVIEW" if eligible else "NOT_READY",
            "blocked_reasons": [],
        }

    def _inspection(self, *, status: str = "READY", issues: list[str] | None = None) -> dict:
        return {
            "ok": True,
            "stage": "EXECUTION_CANDIDATE_INSPECTION",
            "status": status,
            "eligible": status == "READY",
            "issues": issues or [],
            "warnings": [
                "Preview mode",
                "Runtime write disabled",
                "Execution disabled",
                "SendOrder disabled",
            ],
            "summary": "READY_FOR_EXECUTION_PREVIEW" if status == "READY" else "NOT_READY",
            "candidate_status": "REAL_READY",
            "preview_connected": True,
            "runtime_write": False,
            "execution_connected": False,
            "send_order_connected": False,
        }

    def _build(self, **overrides: object) -> dict:
        gate = overrides.get("gate", self._gate())
        order = overrides.get("order", self._order())
        queue_preview = overrides.get("queue_preview", self._queue_preview())
        report = overrides.get("report", self._report())
        inspection = overrides.get("inspection", self._inspection())
        return build_execution_readiness_summary(gate, order, queue_preview, report, inspection)

    def _sha256(self, path: Path) -> str | None:
        if not path.exists():
            return None
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def test_ready_summary_scores_100(self) -> None:
        result = self._build()

        self.assertEqual("READY", result["overall_status"])
        self.assertTrue(result["ready"])
        self.assertEqual(100, result["score"])
        self.assertEqual("READY_FOR_EXECUTION_PREVIEW", result["decision"])
        self.assertEqual("READY_FOR_EXECUTION_PREVIEW", result["summary"])
        self.assertEqual([], result["issues"])
        self.assertEqual("PASS", result["checks"]["Gate"])
        self.assertEqual("PASS", result["checks"]["PreviewQueue"])
        self.assertEqual("PASS", result["checks"]["PreviewReport"])
        self.assertEqual("PASS", result["checks"]["CandidateInspector"])
        self.assertEqual("PASS", result["checks"]["RuntimeWriteDisabled"])
        self.assertEqual("PASS", result["checks"]["ExecutionDisabled"])
        self.assertEqual("PASS", result["checks"]["SendOrderDisabled"])

    def test_partial_when_inspector_not_ready(self) -> None:
        result = self._build(
            inspection=self._inspection(status="NOT_READY", issues=["QUEUE_PREVIEW_FAILED"]),
        )

        self.assertEqual("PARTIAL", result["overall_status"])
        self.assertFalse(result["ready"])
        self.assertEqual(80, result["score"])
        self.assertEqual("NOT_READY", result["decision"])
        self.assertEqual(["QUEUE_PREVIEW_FAILED"], result["issues"])
        self.assertEqual("FAIL", result["checks"]["CandidateInspector"])

    def test_blocked_when_gate_not_open(self) -> None:
        result = self._build(
            gate=self._gate(gate_result="POLICY_BLOCKED", blocked_reasons=["POLICY_BLOCKED"]),
            queue_preview=self._queue_preview(connected=False, gate_result="POLICY_BLOCKED"),
            report=self._report(ok=True, eligible=False, connected=False),
            inspection=self._inspection(status="BLOCKED", issues=["POLICY_BLOCKED"]),
        )

        self.assertEqual("BLOCKED", result["overall_status"])
        self.assertFalse(result["ready"])
        self.assertEqual("BLOCKED", result["decision"])
        self.assertEqual(40, result["score"])
        self.assertEqual(["POLICY_BLOCKED"], result["issues"])
        self.assertEqual("FAIL", result["checks"]["Gate"])

    def test_invalid_when_inspector_invalid(self) -> None:
        result = self._build(
            inspection=self._inspection(status="INVALID", issues=["MISSING_ORDER_PRICE"]),
        )

        self.assertEqual("INVALID", result["overall_status"])
        self.assertFalse(result["ready"])
        self.assertEqual("INVALID", result["decision"])
        self.assertEqual(["MISSING_ORDER_PRICE"], result["issues"])

    def test_score_components_are_independent(self) -> None:
        result = self._build(
            order=self._order(status="EXECUTABLE"),
            queue_preview=self._queue_preview(connected=False, order_status="EXECUTABLE"),
            report=self._report(ok=False, eligible=False, connected=False),
            inspection=self._inspection(status="NOT_READY", issues=["SIGNAL_NOT_READY"]),
        )

        self.assertEqual("PARTIAL", result["overall_status"])
        self.assertEqual(20, result["score"])
        self.assertEqual("PASS", result["checks"]["Gate"])
        self.assertEqual("FAIL", result["checks"]["PreviewQueue"])
        self.assertEqual("FAIL", result["checks"]["PreviewReport"])
        self.assertEqual("FAIL", result["checks"]["CandidateInspector"])

    def test_safety_checks_fail_when_connections_are_enabled(self) -> None:
        result = self._build(
            queue_preview=self._queue_preview(
                runtime_write=True,
                execution_connected=True,
                send_order_connected=True,
            )
        )

        self.assertEqual("FAIL", result["checks"]["RuntimeWriteDisabled"])
        self.assertEqual("FAIL", result["checks"]["ExecutionDisabled"])
        self.assertEqual("FAIL", result["checks"]["SendOrderDisabled"])
        self.assertFalse(result["ready"])

    def test_input_dicts_are_not_mutated(self) -> None:
        gate = self._gate()
        order = self._order()
        queue_preview = self._queue_preview()
        report = self._report()
        inspection = self._inspection()
        originals = (
            deepcopy(gate),
            deepcopy(order),
            deepcopy(queue_preview),
            deepcopy(report),
            deepcopy(inspection),
        )

        build_execution_readiness_summary(gate, order, queue_preview, report, inspection)

        self.assertEqual(originals[0], gate)
        self.assertEqual(originals[1], order)
        self.assertEqual(originals[2], queue_preview)
        self.assertEqual(originals[3], report)
        self.assertEqual(originals[4], inspection)

    def test_summary_does_not_write_runtime_rules_or_call_execution_send_order(self) -> None:
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
            result = self._build()

        self.assertEqual("READY", result["overall_status"])
        write_text.assert_not_called()
        open_mock.assert_not_called()
        execution_controller.assert_not_called()
        send_order_stub.assert_not_called()
        self.assertEqual(before_runtime, self._sha256(runtime_path))
        self.assertEqual(before_rules, self._sha256(rules_path))


if __name__ == "__main__":
    unittest.main()
