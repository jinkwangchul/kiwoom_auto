# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import hashlib
import unittest
from pathlib import Path
from unittest import mock

from execution_readiness_snapshot_writer_approval import approve_snapshot_write


class ExecutionReadinessSnapshotWriterApprovalTest(unittest.TestCase):
    def _dryrun(self, **overrides: object) -> dict:
        dryrun = {
            "status": "READY",
            "can_write": True,
            "validated": True,
            "summary": "SNAPSHOT_WRITE_DRYRUN_READY",
            "checks": {
                "Filename": "PASS",
                "ExportPath": "PASS",
                "Content": "PASS",
                "PreviewMode": "PASS",
                "ExportType": "PASS",
            },
            "warnings": [
                "Preview only",
                "Runtime write disabled",
                "Audit write disabled",
                "File creation disabled",
            ],
            "issues": [],
            "write_plan": {
                "target_path": "audit/preview/execution_readiness_preview_20260705_103000.txt",
                "target_filename": "execution_readiness_preview_20260705_103000.txt",
                "estimated_size": 48,
                "content_type": "text/plain",
                "preview_only": True,
            },
        }
        dryrun.update(overrides)
        return dryrun

    def _sha256(self, path: Path) -> str | None:
        if not path.exists():
            return None
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def test_ready_dryrun_is_approved(self) -> None:
        dryrun = self._dryrun()

        result = approve_snapshot_write(dryrun)

        self.assertEqual("APPROVED", result["status"])
        self.assertTrue(result["approved"])
        self.assertEqual("SNAPSHOT_APPROVAL_PREVIEW", result["approval_token"])
        self.assertEqual("DRYRUN_VALIDATION_PASSED", result["approval_reason"])
        self.assertIsNone(result["blocked_reason"])
        self.assertEqual("SNAPSHOT_WRITE_APPROVED_PREVIEW", result["summary"])
        self.assertEqual("PASS", result["checks"]["DryrunValidated"])
        self.assertEqual("PASS", result["checks"]["CanWrite"])
        self.assertEqual("PASS", result["checks"]["PreviewOnly"])
        self.assertEqual("PASS", result["checks"]["RuntimeWriteDisabled"])
        self.assertEqual([], result["issues"])
        self.assertEqual("SNAPSHOT_APPROVAL_PREVIEW", result["commit_plan"]["approval_token"])
        self.assertEqual(dryrun["write_plan"]["target_path"], result["commit_plan"]["target_path"])
        self.assertEqual(dryrun["write_plan"]["target_filename"], result["commit_plan"]["target_filename"])
        self.assertEqual(dryrun["write_plan"]["estimated_size"], result["commit_plan"]["estimated_size"])
        self.assertTrue(result["commit_plan"]["preview_only"])
        self.assertIn("Commit disabled", result["warnings"])

    def test_can_write_false_is_blocked(self) -> None:
        result = approve_snapshot_write(
            self._dryrun(can_write=False, issues=["MISSING_CONTENT"])
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertFalse(result["approved"])
        self.assertIsNone(result["approval_token"])
        self.assertEqual("MISSING_CONTENT", result["blocked_reason"])
        self.assertEqual("FAIL", result["checks"]["CanWrite"])
        self.assertEqual(["MISSING_CONTENT"], result["issues"])

    def test_validated_false_is_blocked(self) -> None:
        result = approve_snapshot_write(
            self._dryrun(validated=False, issues=["DRYRUN_VALIDATION_FAILED"])
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertFalse(result["approved"])
        self.assertEqual("DRYRUN_VALIDATION_FAILED", result["blocked_reason"])
        self.assertEqual("FAIL", result["checks"]["DryrunValidated"])

    def test_dryrun_status_not_ready_is_blocked_with_dryrun_issue(self) -> None:
        for issue in ("INVALID_EXPORT_TYPE", "PREVIEW_DISABLED"):
            with self.subTest(issue=issue):
                result = approve_snapshot_write(
                    self._dryrun(status="BLOCKED", can_write=False, validated=False, issues=[issue])
                )

                self.assertEqual("BLOCKED", result["status"])
                self.assertFalse(result["approved"])
                self.assertEqual(issue, result["blocked_reason"])
                self.assertEqual([issue], result["issues"])

    def test_none_dryrun_is_invalid(self) -> None:
        result = approve_snapshot_write(None)

        self.assertEqual("INVALID", result["status"])
        self.assertFalse(result["approved"])
        self.assertEqual("DRYRUN_RESULT_REQUIRED", result["blocked_reason"])
        self.assertEqual(["DRYRUN_RESULT_REQUIRED"], result["issues"])
        self.assertEqual("SKIP", result["checks"]["DryrunValidated"])

    def test_missing_required_fields_are_invalid(self) -> None:
        dryrun = self._dryrun()
        dryrun.pop("write_plan")

        result = approve_snapshot_write(dryrun)

        self.assertEqual("INVALID", result["status"])
        self.assertFalse(result["approved"])
        self.assertIn("MISSING_REQUIRED_DRYRUN_FIELDS", result["blocked_reason"])
        self.assertIn("write_plan", result["blocked_reason"])

    def test_input_dict_is_not_mutated(self) -> None:
        dryrun = self._dryrun()
        original = deepcopy(dryrun)

        approve_snapshot_write(dryrun)

        self.assertEqual(original, dryrun)

    def test_approval_gate_does_not_create_files_dirs_runtime_rules_commit_or_call_execution_send_order(self) -> None:
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
            result = approve_snapshot_write(self._dryrun())

        self.assertEqual("APPROVED", result["status"])
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
