# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import unittest
from pathlib import Path
from unittest import mock

from execution_readiness_audit_record import build_execution_readiness_audit_record


class ExecutionReadinessAuditRecordTest(unittest.TestCase):
    def _summary(
        self,
        *,
        decision: str = "READY_FOR_EXECUTION_PREVIEW",
        overall_status: str = "READY",
        ready: bool = True,
        score: int = 100,
        issues: list[str] | None = None,
    ) -> dict:
        return {
            "ok": True,
            "stage": "EXECUTION_READINESS_SUMMARY",
            "overall_status": overall_status,
            "ready": ready,
            "score": score,
            "decision": decision,
            "summary": decision,
            "checks": {
                "Gate": "PASS" if overall_status != "BLOCKED" else "FAIL",
                "PreviewQueue": "PASS",
                "PreviewReport": "PASS",
                "CandidateInspector": "PASS" if overall_status == "READY" else "FAIL",
                "RuntimeWriteDisabled": "PASS",
                "ExecutionDisabled": "PASS",
                "SendOrderDisabled": "PASS",
            },
            "warnings": [
                "Preview mode",
                "Runtime write disabled",
                "Execution disabled",
                "SendOrder disabled",
            ],
            "issues": issues or [],
            "gate_result": {"stage": "SIGNAL_QUEUE_GATE", "gate_result": "OPEN"},
            "order_candidate": {"id": "ORDER_1", "status": "REAL_READY"},
            "queue_preview_result": {
                "stage": "SIGNAL_GATE_EXECUTION_QUEUE_BRIDGE",
                "queue_writer_preview_connected": True,
            },
        }

    def _preview_report(self) -> dict:
        return {
            "ok": True,
            "stage": "EXECUTION_PREVIEW_REPORT",
            "gate": "OPEN",
            "candidate": "REAL_READY",
            "preview_connected": True,
        }

    def _inspection(self, *, status: str = "READY", issues: list[str] | None = None) -> dict:
        return {
            "ok": True,
            "stage": "EXECUTION_CANDIDATE_INSPECTION",
            "status": status,
            "issues": issues or [],
            "candidate_status": "REAL_READY",
            "preview_connected": True,
        }

    def _sha256(self, path: Path) -> str | None:
        if not path.exists():
            return None
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def test_ready_record_uses_standard_fields_and_fixed_preview_flags(self) -> None:
        result = build_execution_readiness_audit_record(
            self._summary(),
            self._preview_report(),
            self._inspection(),
            now=datetime(2026, 7, 5, 10, 30, 0),
        )

        self.assertEqual(1, result["record_version"])
        self.assertEqual("2026-07-05T10:30:00", result["created_at"])
        self.assertEqual("EXECUTION_READINESS_PREVIEW", result["record_type"])
        self.assertEqual("READY_FOR_EXECUTION_PREVIEW", result["decision"])
        self.assertEqual("READY", result["overall_status"])
        self.assertTrue(result["ready"])
        self.assertEqual(100, result["score"])
        self.assertEqual("READY_FOR_EXECUTION_PREVIEW", result["summary"])
        self.assertTrue(result["preview_mode"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["execution_connected"])
        self.assertFalse(result["send_order_connected"])
        self.assertEqual([], result["issues"])

    def test_blocked_summary_is_preserved(self) -> None:
        summary = self._summary(
            decision="BLOCKED",
            overall_status="BLOCKED",
            ready=False,
            score=40,
            issues=["POLICY_BLOCKED"],
        )

        result = build_execution_readiness_audit_record(
            summary,
            self._preview_report(),
            self._inspection(status="BLOCKED", issues=["POLICY_BLOCKED"]),
            now="2026-07-05T10:31:00",
        )

        self.assertEqual("BLOCKED", result["decision"])
        self.assertEqual("BLOCKED", result["overall_status"])
        self.assertFalse(result["ready"])
        self.assertEqual(40, result["score"])
        self.assertEqual(["POLICY_BLOCKED"], result["issues"])

    def test_invalid_summary_is_preserved(self) -> None:
        summary = self._summary(
            decision="INVALID",
            overall_status="INVALID",
            ready=False,
            score=80,
            issues=["MISSING_ORDER_PRICE"],
        )

        result = build_execution_readiness_audit_record(
            summary,
            self._preview_report(),
            self._inspection(status="INVALID", issues=["MISSING_ORDER_PRICE"]),
            now="2026-07-05T10:32:00",
        )

        self.assertEqual("INVALID", result["decision"])
        self.assertEqual("INVALID", result["overall_status"])
        self.assertFalse(result["ready"])
        self.assertEqual(["MISSING_ORDER_PRICE"], result["issues"])

    def test_metadata_contains_preview_only_context(self) -> None:
        result = build_execution_readiness_audit_record(
            self._summary(),
            self._preview_report(),
            self._inspection(),
            now="2026-07-05T10:33:00",
        )

        metadata = result["metadata"]
        self.assertEqual("OPEN", metadata["gate_result"])
        self.assertEqual("REAL_READY", metadata["candidate_state"])
        self.assertTrue(metadata["preview_connected"])
        self.assertEqual("execution_readiness_preview", metadata["project_phase"])
        self.assertTrue(metadata["test_mode"])
        self.assertEqual("execution_readiness_summary", metadata["source"])
        self.assertEqual("SIGNAL_GATE_EXECUTION_QUEUE_BRIDGE", metadata["queue_preview_stage"])
        self.assertEqual("EXECUTION_PREVIEW_REPORT", metadata["preview_report_stage"])
        self.assertEqual("EXECUTION_CANDIDATE_INSPECTION", metadata["inspection_stage"])

    def test_datetime_provider_is_supported(self) -> None:
        result = build_execution_readiness_audit_record(
            self._summary(),
            self._preview_report(),
            self._inspection(),
            datetime_provider=lambda: datetime(2026, 7, 5, 10, 34, 0),
        )

        self.assertEqual("2026-07-05T10:34:00", result["created_at"])

    def test_input_dicts_are_not_mutated(self) -> None:
        summary = self._summary()
        report = self._preview_report()
        inspection = self._inspection()
        originals = (deepcopy(summary), deepcopy(report), deepcopy(inspection))

        build_execution_readiness_audit_record(summary, report, inspection, now="2026-07-05T10:35:00")

        self.assertEqual(originals[0], summary)
        self.assertEqual(originals[1], report)
        self.assertEqual(originals[2], inspection)

    def test_record_builder_does_not_write_runtime_rules_log_or_call_execution_send_order(self) -> None:
        runtime_path = Path("runtime") / "order_queue.json"
        rules_path = Path("routines") / "\uc9c0\ud45c\ucd94\uc885\ub9e4\ub9e4" / "rules.json"
        before_runtime = self._sha256(runtime_path)
        before_rules = self._sha256(rules_path)

        with (
            mock.patch("pathlib.Path.write_text") as write_text,
            mock.patch("builtins.open", mock.mock_open()) as open_mock,
            mock.patch("logging.Logger.info") as logger_info,
            mock.patch("execution_controller.build_execution_preview") as execution_controller,
            mock.patch("kiwoom_order_adapter.send_order_stub") as send_order_stub,
        ):
            result = build_execution_readiness_audit_record(
                self._summary(),
                self._preview_report(),
                self._inspection(),
                now="2026-07-05T10:36:00",
            )

        self.assertEqual("EXECUTION_READINESS_PREVIEW", result["record_type"])
        write_text.assert_not_called()
        open_mock.assert_not_called()
        logger_info.assert_not_called()
        execution_controller.assert_not_called()
        send_order_stub.assert_not_called()
        self.assertEqual(before_runtime, self._sha256(runtime_path))
        self.assertEqual(before_rules, self._sha256(rules_path))


if __name__ == "__main__":
    unittest.main()
