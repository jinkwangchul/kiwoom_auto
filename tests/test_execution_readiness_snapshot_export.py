# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import unittest
from pathlib import Path
from unittest import mock

from execution_readiness_snapshot_export import build_execution_readiness_snapshot_export


class ExecutionReadinessSnapshotExportTest(unittest.TestCase):
    def _record(
        self,
        *,
        decision: str = "READY_FOR_EXECUTION_PREVIEW",
        overall_status: str = "READY",
        ready: bool = True,
        score: int = 100,
        issues: list[str] | None = None,
    ) -> dict:
        return {
            "record_version": 1,
            "created_at": "2026-07-05T10:30:00",
            "record_type": "EXECUTION_READINESS_PREVIEW",
            "decision": decision,
            "overall_status": overall_status,
            "ready": ready,
            "score": score,
            "summary": decision,
            "checks": {
                "Gate": "PASS",
                "PreviewQueue": "PASS",
                "PreviewReport": "PASS",
                "CandidateInspector": "PASS",
            },
            "warnings": [
                "Preview mode",
                "Runtime write disabled",
                "Execution disabled",
                "SendOrder disabled",
            ],
            "issues": issues or [],
            "preview_mode": True,
            "runtime_write": False,
            "execution_connected": False,
            "send_order_connected": False,
            "metadata": {
                "gate_result": "OPEN",
                "candidate_state": "REAL_READY",
                "preview_connected": True,
            },
        }

    def _sha256(self, path: Path) -> str | None:
        if not path.exists():
            return None
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def test_ready_record_builds_snapshot_export_preview(self) -> None:
        result = build_execution_readiness_snapshot_export(self._record())

        self.assertEqual(1, result["export_version"])
        self.assertEqual("EXECUTION_READINESS_PREVIEW", result["export_type"])
        self.assertTrue(result["preview_mode"])
        self.assertEqual("2026-07-05T10:30:00", result["generated_at"])
        self.assertEqual("execution_readiness_preview_20260705_103000.txt", result["export_filename"])
        self.assertEqual("audit/preview/", result["export_path"])
        self.assertEqual("text/plain", result["content_type"])
        self.assertIn("Execution Readiness Snapshot", result["content"])
        self.assertIn("READY_FOR_EXECUTION_PREVIEW", result["content"])
        self.assertIn("Overall Status", result["content"])
        self.assertIn("READY", result["content"])
        self.assertIn("Score", result["content"])
        self.assertIn("100", result["content"])
        self.assertIn("End of Preview", result["content"])

    def test_blocked_and_invalid_values_are_reflected_in_content(self) -> None:
        blocked = build_execution_readiness_snapshot_export(
            self._record(
                decision="BLOCKED",
                overall_status="BLOCKED",
                ready=False,
                score=40,
                issues=["POLICY_BLOCKED"],
            )
        )
        invalid = build_execution_readiness_snapshot_export(
            self._record(
                decision="INVALID",
                overall_status="INVALID",
                ready=False,
                score=80,
                issues=["MISSING_ORDER_PRICE"],
            )
        )

        self.assertIn("BLOCKED", blocked["content"])
        self.assertIn("POLICY_BLOCKED", blocked["content"])
        self.assertIn("INVALID", invalid["content"])
        self.assertIn("MISSING_ORDER_PRICE", invalid["content"])

    def test_issues_none_is_rendered_when_empty(self) -> None:
        result = build_execution_readiness_snapshot_export(self._record(issues=[]))

        self.assertIn("Issues\n\nNone", result["content"])

    def test_metadata_contains_preview_only_export_context(self) -> None:
        result = build_execution_readiness_snapshot_export(self._record())
        metadata = result["metadata"]

        self.assertEqual(1, metadata["record_version"])
        self.assertEqual("EXECUTION_READINESS_PREVIEW", metadata["record_type"])
        self.assertEqual("execution_readiness_audit_record", metadata["generated_source"])
        self.assertEqual("execution_readiness_snapshot_export_preview", metadata["project_phase"])
        self.assertTrue(metadata["preview_only"])
        self.assertTrue(metadata["test_mode"])

    def test_now_and_datetime_provider_can_override_generated_at(self) -> None:
        now_result = build_execution_readiness_snapshot_export(
            self._record(),
            now=datetime(2026, 7, 5, 11, 0, 0),
        )
        provider_result = build_execution_readiness_snapshot_export(
            self._record(),
            datetime_provider=lambda: datetime(2026, 7, 5, 11, 1, 0),
        )

        self.assertEqual("2026-07-05T11:00:00", now_result["generated_at"])
        self.assertEqual("execution_readiness_preview_20260705_110000.txt", now_result["export_filename"])
        self.assertEqual("2026-07-05T11:01:00", provider_result["generated_at"])
        self.assertEqual("execution_readiness_preview_20260705_110100.txt", provider_result["export_filename"])

    def test_input_dict_is_not_mutated(self) -> None:
        record = self._record()
        original = deepcopy(record)

        build_execution_readiness_snapshot_export(record)

        self.assertEqual(original, record)

    def test_export_preview_does_not_create_files_dirs_runtime_rules_or_call_execution_send_order(self) -> None:
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
            result = build_execution_readiness_snapshot_export(self._record())

        self.assertEqual("text/plain", result["content_type"])
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
