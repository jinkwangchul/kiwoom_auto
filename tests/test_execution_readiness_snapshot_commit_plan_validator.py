# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import hashlib
import unittest
from pathlib import Path
from unittest import mock

from execution_readiness_snapshot_commit_plan_validator import validate_snapshot_commit_plan


class ExecutionReadinessSnapshotCommitPlanValidatorTest(unittest.TestCase):
    def _approval(self, **overrides: object) -> dict:
        approval = {
            "status": "APPROVED",
            "approved": True,
            "approval_token": "SNAPSHOT_APPROVAL_PREVIEW",
            "approval_reason": "DRYRUN_VALIDATION_PASSED",
            "blocked_reason": None,
            "summary": "SNAPSHOT_WRITE_APPROVED_PREVIEW",
            "checks": {
                "DryrunValidated": "PASS",
                "CanWrite": "PASS",
                "PreviewOnly": "PASS",
                "RuntimeWriteDisabled": "PASS",
            },
            "warnings": [
                "Preview only",
                "Runtime write disabled",
                "Audit write disabled",
                "Commit disabled",
            ],
            "issues": [],
            "commit_plan": {
                "approval_token": "SNAPSHOT_APPROVAL_PREVIEW",
                "target_path": "audit/preview/execution_readiness_preview_20260705_103000.txt",
                "target_filename": "execution_readiness_preview_20260705_103000.txt",
                "estimated_size": 48,
                "preview_only": True,
            },
        }
        approval.update(overrides)
        return approval

    def _sha256(self, path: Path) -> str | None:
        if not path.exists():
            return None
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def test_approved_commit_plan_is_valid(self) -> None:
        approval = self._approval()

        result = validate_snapshot_commit_plan(approval)

        self.assertEqual("VALID", result["status"])
        self.assertTrue(result["valid"])
        self.assertTrue(result["validated"])
        self.assertEqual("SNAPSHOT_COMMIT_PLAN_VALID", result["summary"])
        self.assertEqual("PASS", result["checks"]["ApprovalStatus"])
        self.assertEqual("PASS", result["checks"]["ApprovalToken"])
        self.assertEqual("PASS", result["checks"]["CommitPlan"])
        self.assertEqual("PASS", result["checks"]["TargetPath"])
        self.assertEqual("PASS", result["checks"]["TargetFilename"])
        self.assertEqual("PASS", result["checks"]["EstimatedSize"])
        self.assertEqual("PASS", result["checks"]["PreviewOnly"])
        self.assertEqual([], result["issues"])
        self.assertEqual(approval["commit_plan"], result["validated_commit_plan"])
        self.assertIn("Commit disabled", result["warnings"])

    def test_blocked_when_approval_not_approved(self) -> None:
        result = validate_snapshot_commit_plan(
            self._approval(
                status="BLOCKED",
                approved=False,
                blocked_reason="DRYRUN_VALIDATION_FAILED",
                issues=["DRYRUN_VALIDATION_FAILED"],
            )
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertFalse(result["valid"])
        self.assertFalse(result["validated"])
        self.assertEqual("SNAPSHOT_COMMIT_PLAN_BLOCKED", result["summary"])
        self.assertEqual(["DRYRUN_VALIDATION_FAILED"], result["issues"])
        self.assertEqual("FAIL", result["checks"]["ApprovalStatus"])
        self.assertEqual("SKIP", result["checks"]["CommitPlan"])

    def test_none_approval_is_invalid(self) -> None:
        result = validate_snapshot_commit_plan(None)

        self.assertEqual("INVALID", result["status"])
        self.assertFalse(result["valid"])
        self.assertFalse(result["validated"])
        self.assertEqual(["APPROVAL_RESULT_REQUIRED"], result["issues"])
        self.assertEqual("SKIP", result["checks"]["ApprovalStatus"])

    def test_missing_required_fields_are_invalid(self) -> None:
        approval = self._approval()
        approval.pop("commit_plan")

        result = validate_snapshot_commit_plan(approval)

        self.assertEqual("INVALID", result["status"])
        self.assertFalse(result["valid"])
        self.assertIn("MISSING_REQUIRED_APPROVAL_FIELDS", result["issues"][0])
        self.assertIn("commit_plan", result["issues"][0])

    def test_missing_commit_plan_is_invalid(self) -> None:
        result = validate_snapshot_commit_plan(self._approval(commit_plan=None))

        self.assertEqual("INVALID", result["status"])
        self.assertEqual(["MISSING_COMMIT_PLAN"], result["issues"])

    def test_missing_commit_plan_fields_are_invalid(self) -> None:
        commit_plan = {
            "approval_token": "",
            "target_path": "",
            "target_filename": "",
            "estimated_size": -1,
            "preview_only": False,
        }

        result = validate_snapshot_commit_plan(self._approval(commit_plan=commit_plan))

        self.assertEqual("INVALID", result["status"])
        self.assertFalse(result["valid"])
        self.assertIn("MISSING_APPROVAL_TOKEN", result["issues"])
        self.assertIn("MISSING_TARGET_PATH", result["issues"])
        self.assertIn("MISSING_TARGET_FILENAME", result["issues"])
        self.assertIn("INVALID_ESTIMATED_SIZE", result["issues"])
        self.assertIn("PREVIEW_FLAG_DISABLED", result["issues"])
        self.assertEqual("FAIL", result["checks"]["ApprovalToken"])
        self.assertEqual("FAIL", result["checks"]["TargetPath"])
        self.assertEqual("FAIL", result["checks"]["TargetFilename"])
        self.assertEqual("FAIL", result["checks"]["EstimatedSize"])
        self.assertEqual("FAIL", result["checks"]["PreviewOnly"])

    def test_estimated_size_zero_is_valid(self) -> None:
        approval = self._approval()
        approval["commit_plan"]["estimated_size"] = 0

        result = validate_snapshot_commit_plan(approval)

        self.assertEqual("VALID", result["status"])
        self.assertTrue(result["valid"])
        self.assertEqual("PASS", result["checks"]["EstimatedSize"])

    def test_input_dict_is_not_mutated(self) -> None:
        approval = self._approval()
        original = deepcopy(approval)

        validate_snapshot_commit_plan(approval)

        self.assertEqual(original, approval)

    def test_validator_does_not_create_files_dirs_runtime_rules_commit_or_call_execution_send_order(self) -> None:
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
            result = validate_snapshot_commit_plan(self._approval())

        self.assertEqual("VALID", result["status"])
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
