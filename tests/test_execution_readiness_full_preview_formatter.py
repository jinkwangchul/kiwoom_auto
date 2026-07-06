# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import hashlib
import unittest
from pathlib import Path
from unittest import mock

from execution_readiness_full_preview_formatter import format_execution_readiness_preview


class ExecutionReadinessFullPreviewFormatterTest(unittest.TestCase):
    def _preview(
        self,
        *,
        status: str = "READY",
        completed: bool = True,
        summary: str = "EXECUTION_READINESS_PREVIEW_READY",
        warnings: list[str] | None = None,
        issues: list[str] | None = None,
    ) -> dict:
        return {
            "status": status,
            "completed": completed,
            "summary": summary,
            "preview_steps": {
                "ExecutionPreviewReport": "PASS",
                "CandidateInspector": "PASS" if status == "READY" else "FAIL",
                "ReadinessSummary": "PASS" if status == "READY" else "FAIL",
                "AuditRecord": "PASS" if status == "READY" else "SKIP",
                "SnapshotPipeline": "PASS" if status == "READY" else "FAIL",
            },
            "preview_report": {},
            "inspection_result": {},
            "readiness_summary": {
                "decision": "READY_FOR_EXECUTION_PREVIEW" if status == "READY" else summary,
                "checks": {
                    "Gate": "PASS" if status != "BLOCKED" else "FAIL",
                    "PreviewQueue": "PASS",
                    "PreviewReport": "PASS",
                    "CandidateInspector": "PASS" if status == "READY" else "FAIL",
                },
            },
            "audit_record": {},
            "snapshot_pipeline": {"status": status},
            "warnings": warnings
            if warnings is not None
            else [
                "Preview mode",
                "Runtime write disabled",
                "Execution disabled",
                "SendOrder disabled",
                "Commit disabled",
            ],
            "issues": issues if issues is not None else [],
        }

    def _sha256(self, path: Path) -> str | None:
        if not path.exists():
            return None
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def test_ready_preview_formats_standard_text(self) -> None:
        result = format_execution_readiness_preview(self._preview())

        self.assertEqual("READY", result["status"])
        self.assertEqual("EXECUTION_READINESS_PREVIEW_READY", result["summary"])
        self.assertIn("Header", result["sections"])
        self.assertIn("Pipeline", result["sections"])
        self.assertIn("Warnings", result["sections"])
        self.assertIn("Issues", result["sections"])
        self.assertIn("Checks", result["sections"])
        self.assertIn("Footer", result["sections"])
        self.assertIn("Execution Readiness Preview", result["text"])
        self.assertIn("Overall Status\nREADY", result["text"])
        self.assertIn("Completed\nTrue", result["text"])
        self.assertIn("Execution Preview Report\nPASS", result["text"])
        self.assertIn("Candidate Inspector\nPASS", result["text"])
        self.assertIn("Snapshot Pipeline\nPASS", result["text"])
        self.assertIn("Result\nREADY_FOR_EXECUTION_PREVIEW", result["text"])
        self.assertIn("End of Preview", result["text"])

    def test_blocked_preview_formats_blocked_status(self) -> None:
        result = format_execution_readiness_preview(
            self._preview(
                status="BLOCKED",
                completed=False,
                summary="EXECUTION_READINESS_PREVIEW_BLOCKED",
                issues=["POLICY_BLOCKED"],
            )
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("Overall Status\nBLOCKED", result["text"])
        self.assertIn("Completed\nFalse", result["text"])
        self.assertIn("POLICY_BLOCKED", result["text"])
        self.assertIn("Gate\nFAIL", result["text"])

    def test_invalid_preview_formats_invalid_status(self) -> None:
        result = format_execution_readiness_preview(
            self._preview(
                status="INVALID",
                completed=False,
                summary="EXECUTION_READINESS_PREVIEW_INVALID",
                issues=["MISSING_ORDER_PRICE"],
            )
        )

        self.assertEqual("INVALID", result["status"])
        self.assertIn("Overall Status\nINVALID", result["text"])
        self.assertIn("MISSING_ORDER_PRICE", result["text"])

    def test_empty_warnings_and_issues_render_none(self) -> None:
        result = format_execution_readiness_preview(
            self._preview(warnings=[], issues=[])
        )

        self.assertIn("Warnings\nNone", result["text"])
        self.assertIn("Issues\nNone", result["text"])

    def test_line_count_matches_text_lines_and_no_tabs(self) -> None:
        result = format_execution_readiness_preview(self._preview())

        self.assertEqual(len(result["text"].splitlines()), result["line_count"])
        self.assertNotIn("\t", result["text"])
        result["text"].encode("ascii")

    def test_input_dict_is_not_mutated(self) -> None:
        preview = self._preview()
        original = deepcopy(preview)

        format_execution_readiness_preview(preview)

        self.assertEqual(original, preview)

    def test_formatter_does_not_output_save_log_runtime_queue_or_call_execution_send_order(self) -> None:
        runtime_path = Path("runtime") / "order_queue.json"
        rules_path = Path("routines") / "\uc9c0\ud45c\ucd94\uc885\ub9e4\ub9e4" / "rules.json"
        before_runtime = self._sha256(runtime_path)
        before_rules = self._sha256(rules_path)

        with (
            mock.patch("builtins.print") as print_mock,
            mock.patch("pathlib.Path.write_text") as write_text,
            mock.patch("pathlib.Path.mkdir") as mkdir,
            mock.patch("builtins.open", mock.mock_open()) as open_mock,
            mock.patch("logging.Logger.info") as logger_info,
            mock.patch("execution_controller.build_execution_preview") as execution_controller,
            mock.patch("kiwoom_order_adapter.send_order_stub") as send_order_stub,
        ):
            result = format_execution_readiness_preview(self._preview())

        self.assertEqual("READY", result["status"])
        print_mock.assert_not_called()
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
