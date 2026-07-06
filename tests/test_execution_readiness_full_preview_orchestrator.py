# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import hashlib
import unittest
from pathlib import Path
from unittest import mock

from execution_readiness_full_preview_orchestrator import run_execution_readiness_preview


class ExecutionReadinessFullPreviewOrchestratorTest(unittest.TestCase):
    def _gate(self, *, gate_result: str = "OPEN") -> dict:
        return {
            "ok": gate_result == "OPEN",
            "stage": "SIGNAL_QUEUE_GATE",
            "gate_result": gate_result,
            "signal": "BUY",
            "blocked_reasons": [] if gate_result == "OPEN" else [gate_result],
        }

    def _order(self, *, status: str = "REAL_READY") -> dict:
        return {
            "id": "ORDER_1",
            "status": status,
            "source_signal_id": "SIG_1",
            "price": 85000,
            "quantity": 10,
            "order_intent": {"side": "BUY", "hoga": "\uc2dc\uc7a5\uac00"},
        }

    def _queue_preview(self) -> dict:
        return {
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

    def _report(self, *, ok: bool = True) -> dict:
        return {
            "ok": ok,
            "stage": "EXECUTION_PREVIEW_REPORT",
            "eligible": ok,
            "gate": "OPEN",
            "candidate": "REAL_READY",
            "preview_connected": ok,
            "blocked_reasons": [] if ok else ["PREVIEW_REPORT_FAILED"],
            "warnings": ["Preview mode"],
        }

    def _inspection(self, *, status: str = "READY", issues: list[str] | None = None) -> dict:
        return {
            "ok": True,
            "stage": "EXECUTION_CANDIDATE_INSPECTION",
            "status": status,
            "eligible": status == "READY",
            "issues": issues or [],
            "warnings": ["Runtime write disabled", "Execution disabled", "SendOrder disabled"],
            "summary": "READY_FOR_EXECUTION_PREVIEW" if status == "READY" else status,
        }

    def _summary(self, *, status: str = "READY", issues: list[str] | None = None) -> dict:
        return {
            "ok": True,
            "stage": "EXECUTION_READINESS_SUMMARY",
            "overall_status": status,
            "ready": status == "READY",
            "score": 100 if status == "READY" else 40,
            "decision": "READY_FOR_EXECUTION_PREVIEW" if status == "READY" else status,
            "summary": "READY_FOR_EXECUTION_PREVIEW" if status == "READY" else status,
            "checks": {"Gate": "PASS" if status != "BLOCKED" else "FAIL"},
            "warnings": ["Preview mode"],
            "issues": issues or [],
        }

    def _audit(self) -> dict:
        return {
            "record_version": 1,
            "created_at": "2026-07-05T10:30:00",
            "record_type": "EXECUTION_READINESS_PREVIEW",
            "decision": "READY_FOR_EXECUTION_PREVIEW",
            "overall_status": "READY",
            "ready": True,
            "score": 100,
            "summary": "READY_FOR_EXECUTION_PREVIEW",
            "checks": {},
            "warnings": ["Preview mode"],
            "issues": [],
            "preview_mode": True,
            "runtime_write": False,
            "execution_connected": False,
            "send_order_connected": False,
            "metadata": {},
        }

    def _snapshot(self, *, status: str = "READY", issues: list[str] | None = None) -> dict:
        return {
            "status": status,
            "completed": status == "READY",
            "summary": f"SNAPSHOT_PIPELINE_{status}",
            "pipeline_steps": {
                "ExportPreview": "PASS",
                "WriterDryrun": "PASS" if status == "READY" else "FAIL",
                "ApprovalGate": "PASS" if status == "READY" else "FAIL",
                "CommitPlanValidation": "PASS" if status == "READY" else "FAIL",
            },
            "warnings": ["Commit disabled"],
            "issues": issues or [],
        }

    def _sha256(self, path: Path) -> str | None:
        if not path.exists():
            return None
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def _run_ready_preview_with_mocked_steps(self, **kwargs) -> dict:
        with (
            mock.patch("execution_readiness_full_preview_orchestrator.build_execution_preview_report", return_value=self._report()),
            mock.patch("execution_readiness_full_preview_orchestrator.inspect_execution_candidate", return_value=self._inspection()),
            mock.patch("execution_readiness_full_preview_orchestrator.build_execution_readiness_summary", return_value=self._summary()),
            mock.patch("execution_readiness_full_preview_orchestrator.build_execution_readiness_audit_record", return_value=self._audit()),
            mock.patch("execution_readiness_full_preview_orchestrator.run_snapshot_pipeline_preview", return_value=self._snapshot()),
        ):
            return run_execution_readiness_preview(
                self._gate(),
                self._order(),
                self._queue_preview(),
                **kwargs,
            )

    def test_ready_flow_runs_all_steps_in_order(self) -> None:
        calls: list[str] = []

        with (
            mock.patch(
                "execution_readiness_full_preview_orchestrator.build_execution_preview_report",
                side_effect=lambda *args: calls.append("report") or self._report(),
            ),
            mock.patch(
                "execution_readiness_full_preview_orchestrator.inspect_execution_candidate",
                side_effect=lambda *args: calls.append("inspection") or self._inspection(),
            ),
            mock.patch(
                "execution_readiness_full_preview_orchestrator.build_execution_readiness_summary",
                side_effect=lambda *args: calls.append("summary") or self._summary(),
            ),
            mock.patch(
                "execution_readiness_full_preview_orchestrator.build_execution_readiness_audit_record",
                side_effect=lambda *args: calls.append("audit") or self._audit(),
            ),
            mock.patch(
                "execution_readiness_full_preview_orchestrator.run_snapshot_pipeline_preview",
                side_effect=lambda *args: calls.append("snapshot") or self._snapshot(),
            ),
        ):
            result = run_execution_readiness_preview(self._gate(), self._order(), self._queue_preview())

        self.assertEqual(["report", "inspection", "summary", "audit", "snapshot"], calls)
        self.assertEqual("READY", result["status"])
        self.assertTrue(result["completed"])
        self.assertEqual("EXECUTION_READINESS_PREVIEW_READY", result["summary"])
        self.assertEqual("PASS", result["preview_steps"]["ExecutionPreviewReport"])
        self.assertEqual("PASS", result["preview_steps"]["CandidateInspector"])
        self.assertEqual("PASS", result["preview_steps"]["ReadinessSummary"])
        self.assertEqual("PASS", result["preview_steps"]["AuditRecord"])
        self.assertEqual("PASS", result["preview_steps"]["SnapshotPipeline"])

    def test_runtime_catalog_extension_default_off_keeps_existing_result_identical(self) -> None:
        default_result = self._run_ready_preview_with_mocked_steps()
        explicit_off_result = self._run_ready_preview_with_mocked_steps(
            include_runtime_catalog_preview=False,
            runtime_catalog_payload={"status": "READY"},
        )

        self.assertEqual(default_result, explicit_off_result)
        self.assertNotIn("extensions", default_result)

    def test_runtime_catalog_extension_on_adds_payload_only_under_extensions(self) -> None:
        runtime_catalog_payload = {
            "adapter_type": "EXECUTION_RUNTIME_CATALOG_ADAPTER_PREVIEW",
            "preview_only": True,
            "runtime_write": False,
            "status": "READY",
            "execution_id": "EXEC_1",
        }
        original_payload = deepcopy(runtime_catalog_payload)

        result = self._run_ready_preview_with_mocked_steps(
            include_runtime_catalog_preview=True,
            runtime_catalog_payload=runtime_catalog_payload,
        )
        result["extensions"]["runtime_catalog_preview"]["execution_id"] = "MUTATED_RESULT_ONLY"

        self.assertEqual("READY", result["status"])
        self.assertTrue(result["completed"])
        self.assertEqual(original_payload, runtime_catalog_payload)
        self.assertEqual(
            original_payload,
            self._run_ready_preview_with_mocked_steps(
                include_runtime_catalog_preview=True,
                runtime_catalog_payload=runtime_catalog_payload,
            )["extensions"]["runtime_catalog_preview"],
        )

    def test_runtime_catalog_extension_on_missing_payload_warns_without_breaking_pipeline(self) -> None:
        result = self._run_ready_preview_with_mocked_steps(
            include_runtime_catalog_preview=True,
            runtime_catalog_payload=None,
        )

        self.assertEqual("READY", result["status"])
        self.assertTrue(result["completed"])
        self.assertNotIn("extensions", result)
        self.assertIn("RUNTIME_CATALOG_PREVIEW_MISSING", result["warnings"])

    def test_inspector_blocked_blocks_and_skips_later_steps(self) -> None:
        summary = mock.Mock()
        audit = mock.Mock()
        snapshot = mock.Mock()

        with (
            mock.patch("execution_readiness_full_preview_orchestrator.build_execution_preview_report", return_value=self._report()),
            mock.patch(
                "execution_readiness_full_preview_orchestrator.inspect_execution_candidate",
                return_value=self._inspection(status="BLOCKED", issues=["POLICY_BLOCKED"]),
            ),
            mock.patch("execution_readiness_full_preview_orchestrator.build_execution_readiness_summary", summary),
            mock.patch("execution_readiness_full_preview_orchestrator.build_execution_readiness_audit_record", audit),
            mock.patch("execution_readiness_full_preview_orchestrator.run_snapshot_pipeline_preview", snapshot),
        ):
            result = run_execution_readiness_preview(self._gate(), self._order(), self._queue_preview())

        summary.assert_not_called()
        audit.assert_not_called()
        snapshot.assert_not_called()
        self.assertEqual("BLOCKED", result["status"])
        self.assertFalse(result["completed"])
        self.assertEqual("EXECUTION_READINESS_PREVIEW_BLOCKED", result["summary"])
        self.assertEqual("SKIP", result["preview_steps"]["ReadinessSummary"])
        self.assertEqual(["POLICY_BLOCKED"], result["issues"])

    def test_inspector_invalid_makes_invalid(self) -> None:
        with (
            mock.patch("execution_readiness_full_preview_orchestrator.build_execution_preview_report", return_value=self._report()),
            mock.patch(
                "execution_readiness_full_preview_orchestrator.inspect_execution_candidate",
                return_value=self._inspection(status="INVALID", issues=["MISSING_ORDER_PRICE"]),
            ),
        ):
            result = run_execution_readiness_preview(self._gate(), self._order(), self._queue_preview())

        self.assertEqual("INVALID", result["status"])
        self.assertFalse(result["completed"])
        self.assertEqual("EXECUTION_READINESS_PREVIEW_INVALID", result["summary"])
        self.assertEqual(["MISSING_ORDER_PRICE"], result["issues"])

    def test_summary_blocked_blocks(self) -> None:
        with (
            mock.patch("execution_readiness_full_preview_orchestrator.build_execution_preview_report", return_value=self._report()),
            mock.patch("execution_readiness_full_preview_orchestrator.inspect_execution_candidate", return_value=self._inspection()),
            mock.patch(
                "execution_readiness_full_preview_orchestrator.build_execution_readiness_summary",
                return_value=self._summary(status="BLOCKED", issues=["POLICY_BLOCKED"]),
            ),
        ):
            result = run_execution_readiness_preview(self._gate(), self._order(), self._queue_preview())

        self.assertEqual("BLOCKED", result["status"])
        self.assertFalse(result["completed"])
        self.assertEqual("SKIP", result["preview_steps"]["AuditRecord"])
        self.assertEqual(["POLICY_BLOCKED"], result["issues"])

    def test_snapshot_invalid_makes_invalid(self) -> None:
        with (
            mock.patch("execution_readiness_full_preview_orchestrator.build_execution_preview_report", return_value=self._report()),
            mock.patch("execution_readiness_full_preview_orchestrator.inspect_execution_candidate", return_value=self._inspection()),
            mock.patch("execution_readiness_full_preview_orchestrator.build_execution_readiness_summary", return_value=self._summary()),
            mock.patch("execution_readiness_full_preview_orchestrator.build_execution_readiness_audit_record", return_value=self._audit()),
            mock.patch(
                "execution_readiness_full_preview_orchestrator.run_snapshot_pipeline_preview",
                return_value=self._snapshot(status="INVALID", issues=["COMMIT_PLAN_VALIDATION_INVALID"]),
            ),
        ):
            result = run_execution_readiness_preview(self._gate(), self._order(), self._queue_preview())

        self.assertEqual("INVALID", result["status"])
        self.assertFalse(result["completed"])
        self.assertEqual("FAIL", result["preview_steps"]["SnapshotPipeline"])
        self.assertEqual(["COMMIT_PLAN_VALIDATION_INVALID"], result["issues"])

    def test_input_dicts_are_not_mutated(self) -> None:
        gate = self._gate()
        order = self._order()
        queue_preview = self._queue_preview()
        originals = (deepcopy(gate), deepcopy(order), deepcopy(queue_preview))

        run_execution_readiness_preview(gate, order, queue_preview)

        self.assertEqual(originals[0], gate)
        self.assertEqual(originals[1], order)
        self.assertEqual(originals[2], queue_preview)

    def test_full_preview_does_not_create_files_dirs_runtime_rules_commit_or_call_execution_send_order(self) -> None:
        runtime_path = Path("runtime") / "order_queue.json"
        rules_path = Path("routines") / "\uc9c0\ud45c\ucd94\uc885\ub9e4\ub9e4" / "rules.json"
        before_runtime = self._sha256(runtime_path)
        before_rules = self._sha256(rules_path)

        with (
            mock.patch("pathlib.Path.write_text") as write_text,
            mock.patch("pathlib.Path.mkdir") as mkdir,
            mock.patch("builtins.open", mock.mock_open()) as open_mock,
            mock.patch("logging.Logger.info") as logger_info,
            mock.patch("execution_controller.build_execution_preview") as execution_controller,
            mock.patch("kiwoom_order_adapter.send_order_stub") as send_order_stub,
        ):
            result = run_execution_readiness_preview(self._gate(), self._order(), self._queue_preview())

        self.assertEqual("READY", result["status"])
        write_text.assert_not_called()
        mkdir.assert_not_called()
        open_mock.assert_not_called()
        logger_info.assert_not_called()
        execution_controller.assert_not_called()
        send_order_stub.assert_not_called()
        self.assertEqual(before_runtime, self._sha256(runtime_path))
        self.assertEqual(before_rules, self._sha256(rules_path))

    def test_runtime_catalog_extension_does_not_create_files_dirs_runtime_rules_commit_or_call_execution_send_order(self) -> None:
        runtime_path = Path("runtime") / "order_queue.json"
        rules_path = Path("routines") / "\uc9c0\ud45c\ucd94\uc885\ub9e4\ub9e4" / "rules.json"
        before_runtime = self._sha256(runtime_path)
        before_rules = self._sha256(rules_path)
        runtime_catalog_payload = {
            "adapter_type": "EXECUTION_RUNTIME_CATALOG_ADAPTER_PREVIEW",
            "preview_only": True,
            "runtime_write": False,
            "status": "READY",
        }

        with (
            mock.patch("pathlib.Path.write_text") as write_text,
            mock.patch("pathlib.Path.mkdir") as mkdir,
            mock.patch("builtins.open", mock.mock_open()) as open_mock,
            mock.patch("logging.Logger.info") as logger_info,
            mock.patch("execution_controller.build_execution_preview") as execution_controller,
            mock.patch("kiwoom_order_adapter.send_order_stub") as send_order_stub,
        ):
            result = self._run_ready_preview_with_mocked_steps(
                include_runtime_catalog_preview=True,
                runtime_catalog_payload=runtime_catalog_payload,
            )

        self.assertEqual("READY", result["status"])
        self.assertIn("runtime_catalog_preview", result["extensions"])
        write_text.assert_not_called()
        mkdir.assert_not_called()
        open_mock.assert_not_called()
        logger_info.assert_not_called()
        execution_controller.assert_not_called()
        send_order_stub.assert_not_called()
        self.assertEqual(before_runtime, self._sha256(runtime_path))
        self.assertEqual(before_rules, self._sha256(rules_path))


if __name__ == "__main__":
    unittest.main()
