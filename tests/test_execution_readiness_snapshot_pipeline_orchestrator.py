# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import hashlib
import unittest
from pathlib import Path
from unittest import mock

from execution_readiness_snapshot_pipeline_orchestrator import run_snapshot_pipeline_preview


class ExecutionReadinessSnapshotPipelineOrchestratorTest(unittest.TestCase):
    def _audit_record(self) -> dict:
        return {
            "record_version": 1,
            "created_at": "2026-07-05T10:30:00",
            "record_type": "EXECUTION_READINESS_PREVIEW",
            "decision": "READY_FOR_EXECUTION_PREVIEW",
            "overall_status": "READY",
            "ready": True,
            "score": 100,
            "summary": "READY_FOR_EXECUTION_PREVIEW",
            "checks": {
                "Gate": "PASS",
                "PreviewQueue": "PASS",
                "PreviewReport": "PASS",
                "CandidateInspector": "PASS",
            },
            "warnings": ["Preview mode", "Runtime write disabled"],
            "issues": [],
        }

    def _export(self) -> dict:
        return {
            "export_version": 1,
            "export_type": "EXECUTION_READINESS_PREVIEW",
            "preview_mode": True,
            "generated_at": "2026-07-05T10:30:00",
            "export_filename": "execution_readiness_preview_20260705_103000.txt",
            "export_path": "audit/preview/",
            "content_type": "text/plain",
            "content": "Execution Readiness Snapshot",
        }

    def _dryrun(self, *, status: str = "READY", issues: list[str] | None = None) -> dict:
        return {
            "status": status,
            "can_write": status == "READY",
            "validated": status == "READY",
            "summary": "SNAPSHOT_WRITE_DRYRUN_READY" if status == "READY" else "SNAPSHOT_WRITE_BLOCKED",
            "checks": {},
            "warnings": ["Preview only", "Runtime write disabled"],
            "issues": issues or [],
            "write_plan": {
                "target_path": "audit/preview/execution_readiness_preview_20260705_103000.txt",
                "target_filename": "execution_readiness_preview_20260705_103000.txt",
                "estimated_size": 28,
                "preview_only": True,
            },
        }

    def _approval(self, *, status: str = "APPROVED", issues: list[str] | None = None) -> dict:
        return {
            "status": status,
            "approved": status == "APPROVED",
            "approval_token": "SNAPSHOT_APPROVAL_PREVIEW" if status == "APPROVED" else None,
            "approval_reason": "DRYRUN_VALIDATION_PASSED" if status == "APPROVED" else None,
            "blocked_reason": None if status == "APPROVED" else "DRYRUN_VALIDATION_FAILED",
            "summary": "SNAPSHOT_WRITE_APPROVED_PREVIEW" if status == "APPROVED" else "SNAPSHOT_WRITE_APPROVAL_BLOCKED",
            "checks": {},
            "warnings": ["Preview only", "Commit disabled"],
            "issues": issues or [],
            "commit_plan": {
                "approval_token": "SNAPSHOT_APPROVAL_PREVIEW" if status == "APPROVED" else None,
                "target_path": "audit/preview/execution_readiness_preview_20260705_103000.txt",
                "target_filename": "execution_readiness_preview_20260705_103000.txt",
                "estimated_size": 28,
                "preview_only": True,
            },
        }

    def _commit_validation(self, *, status: str = "VALID", issues: list[str] | None = None) -> dict:
        return {
            "status": status,
            "valid": status == "VALID",
            "validated": status == "VALID",
            "summary": "SNAPSHOT_COMMIT_PLAN_VALID" if status == "VALID" else "SNAPSHOT_COMMIT_PLAN_BLOCKED",
            "checks": {},
            "warnings": ["Preview only", "Commit disabled"],
            "issues": issues or [],
            "validated_commit_plan": {
                "approval_token": "SNAPSHOT_APPROVAL_PREVIEW",
                "target_path": "audit/preview/execution_readiness_preview_20260705_103000.txt",
                "target_filename": "execution_readiness_preview_20260705_103000.txt",
                "estimated_size": 28,
                "preview_only": True,
            },
        }

    def _sha256(self, path: Path) -> str | None:
        if not path.exists():
            return None
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def test_ready_pipeline_runs_all_steps_in_order(self) -> None:
        audit_record = self._audit_record()
        calls: list[str] = []

        def export_step(record):
            calls.append("export")
            return self._export()

        def dryrun_step(export):
            calls.append("dryrun")
            return self._dryrun()

        def approval_step(dryrun):
            calls.append("approval")
            return self._approval()

        def validation_step(approval):
            calls.append("validation")
            return self._commit_validation()

        with (
            mock.patch("execution_readiness_snapshot_pipeline_orchestrator.build_execution_readiness_snapshot_export", side_effect=export_step),
            mock.patch("execution_readiness_snapshot_pipeline_orchestrator.validate_snapshot_write_dryrun", side_effect=dryrun_step),
            mock.patch("execution_readiness_snapshot_pipeline_orchestrator.approve_snapshot_write", side_effect=approval_step),
            mock.patch("execution_readiness_snapshot_pipeline_orchestrator.validate_snapshot_commit_plan", side_effect=validation_step),
        ):
            result = run_snapshot_pipeline_preview(audit_record)

        self.assertEqual(["export", "dryrun", "approval", "validation"], calls)
        self.assertEqual("READY", result["status"])
        self.assertTrue(result["completed"])
        self.assertEqual("SNAPSHOT_PIPELINE_READY", result["summary"])
        self.assertEqual("PASS", result["pipeline_steps"]["ExportPreview"])
        self.assertEqual("PASS", result["pipeline_steps"]["WriterDryrun"])
        self.assertEqual("PASS", result["pipeline_steps"]["ApprovalGate"])
        self.assertEqual("PASS", result["pipeline_steps"]["CommitPlanValidation"])
        self.assertEqual(self._export(), result["snapshot_export"])

    def test_approval_blocked_blocks_pipeline(self) -> None:
        with (
            mock.patch("execution_readiness_snapshot_pipeline_orchestrator.build_execution_readiness_snapshot_export", return_value=self._export()),
            mock.patch("execution_readiness_snapshot_pipeline_orchestrator.validate_snapshot_write_dryrun", return_value=self._dryrun()),
            mock.patch(
                "execution_readiness_snapshot_pipeline_orchestrator.approve_snapshot_write",
                return_value=self._approval(status="BLOCKED", issues=["DRYRUN_VALIDATION_FAILED"]),
            ),
            mock.patch(
                "execution_readiness_snapshot_pipeline_orchestrator.validate_snapshot_commit_plan",
                return_value=self._commit_validation(status="BLOCKED", issues=["DRYRUN_VALIDATION_FAILED"]),
            ),
        ):
            result = run_snapshot_pipeline_preview(self._audit_record())

        self.assertEqual("BLOCKED", result["status"])
        self.assertFalse(result["completed"])
        self.assertEqual("SNAPSHOT_PIPELINE_BLOCKED", result["summary"])
        self.assertEqual("FAIL", result["pipeline_steps"]["ApprovalGate"])
        self.assertEqual("FAIL", result["pipeline_steps"]["CommitPlanValidation"])
        self.assertEqual(["DRYRUN_VALIDATION_FAILED"], result["issues"])

    def test_dryrun_invalid_makes_pipeline_invalid_and_stops_later_steps(self) -> None:
        approval = mock.Mock()
        validation = mock.Mock()

        with (
            mock.patch("execution_readiness_snapshot_pipeline_orchestrator.build_execution_readiness_snapshot_export", return_value=self._export()),
            mock.patch(
                "execution_readiness_snapshot_pipeline_orchestrator.validate_snapshot_write_dryrun",
                return_value=self._dryrun(status="INVALID", issues=["EMPTY_CONTENT"]),
            ),
            mock.patch("execution_readiness_snapshot_pipeline_orchestrator.approve_snapshot_write", approval),
            mock.patch("execution_readiness_snapshot_pipeline_orchestrator.validate_snapshot_commit_plan", validation),
        ):
            result = run_snapshot_pipeline_preview(self._audit_record())

        approval.assert_not_called()
        validation.assert_not_called()
        self.assertEqual("INVALID", result["status"])
        self.assertFalse(result["completed"])
        self.assertEqual("SNAPSHOT_PIPELINE_INVALID", result["summary"])
        self.assertEqual("FAIL", result["pipeline_steps"]["WriterDryrun"])
        self.assertIn("WRITER_DRYRUN_INVALID", result["issues"])
        self.assertIn("EMPTY_CONTENT", result["issues"])

    def test_export_failure_makes_pipeline_invalid(self) -> None:
        with mock.patch(
            "execution_readiness_snapshot_pipeline_orchestrator.build_execution_readiness_snapshot_export",
            return_value={},
        ):
            result = run_snapshot_pipeline_preview(self._audit_record())

        self.assertEqual("INVALID", result["status"])
        self.assertFalse(result["completed"])
        self.assertEqual("SNAPSHOT_PIPELINE_INVALID", result["summary"])
        self.assertEqual("FAIL", result["pipeline_steps"]["ExportPreview"])
        self.assertEqual(["EXPORT_PREVIEW_FAILED"], result["issues"])

    def test_commit_validation_invalid_makes_pipeline_invalid(self) -> None:
        with (
            mock.patch("execution_readiness_snapshot_pipeline_orchestrator.build_execution_readiness_snapshot_export", return_value=self._export()),
            mock.patch("execution_readiness_snapshot_pipeline_orchestrator.validate_snapshot_write_dryrun", return_value=self._dryrun()),
            mock.patch("execution_readiness_snapshot_pipeline_orchestrator.approve_snapshot_write", return_value=self._approval()),
            mock.patch(
                "execution_readiness_snapshot_pipeline_orchestrator.validate_snapshot_commit_plan",
                return_value=self._commit_validation(status="INVALID", issues=["MISSING_APPROVAL_TOKEN"]),
            ),
        ):
            result = run_snapshot_pipeline_preview(self._audit_record())

        self.assertEqual("INVALID", result["status"])
        self.assertFalse(result["completed"])
        self.assertIn("COMMIT_PLAN_VALIDATION_INVALID", result["issues"])
        self.assertIn("MISSING_APPROVAL_TOKEN", result["issues"])

    def test_input_dict_is_not_mutated(self) -> None:
        audit_record = self._audit_record()
        original = deepcopy(audit_record)

        run_snapshot_pipeline_preview(audit_record)

        self.assertEqual(original, audit_record)

    def test_pipeline_does_not_create_files_dirs_runtime_rules_commit_or_call_execution_send_order(self) -> None:
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
            result = run_snapshot_pipeline_preview(self._audit_record())

        self.assertEqual("READY", result["status"])
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
