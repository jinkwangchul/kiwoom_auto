# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import hashlib
import unittest
from pathlib import Path
from unittest import mock

from execution_readiness_full_preview_formatter import format_execution_readiness_preview
from execution_readiness_gui_adapter import build_execution_readiness_view_model


class ExecutionReadinessGuiAdapterTest(unittest.TestCase):
    def _preview_text(
        self,
        *,
        status: str = "READY",
        completed: bool = True,
        summary: str = "EXECUTION_READINESS_PREVIEW_READY",
        warnings: list[str] | None = None,
        issues: list[str] | None = None,
    ) -> dict:
        preview_result = {
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
            "readiness_summary": {
                "decision": "READY_FOR_EXECUTION_PREVIEW" if status == "READY" else summary,
                "checks": {
                    "Gate": "PASS" if status != "BLOCKED" else "FAIL",
                    "PreviewQueue": "PASS",
                    "PreviewReport": "PASS",
                    "CandidateInspector": "PASS" if status == "READY" else "FAIL",
                },
            },
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
        return format_execution_readiness_preview(preview_result)

    def _sha256(self, path: Path) -> str | None:
        if not path.exists():
            return None
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def test_ready_preview_builds_gui_view_model(self) -> None:
        result = build_execution_readiness_view_model(self._preview_text())

        self.assertEqual("READY", result["status"])
        self.assertEqual("Execution Readiness Preview", result["title"])
        self.assertEqual("READY", result["subtitle"])
        self.assertTrue(result["ready"])
        self.assertEqual("EXECUTION_READINESS_PREVIEW_READY", result["summary"])
        self.assertEqual("PASS", dict(result["table_rows"])["Pipeline"])
        self.assertIn("Ready", result["badges"])
        self.assertIn("Preview", result["badges"])
        self.assertIn("Runtime Locked", result["badges"])
        self.assertIn("Execution Disabled", result["badges"])
        self.assertIn("SendOrder Disabled", result["badges"])
        self.assertIn("Commit Disabled", result["badges"])

    def test_blocked_preview_builds_non_ready_view_model(self) -> None:
        result = build_execution_readiness_view_model(
            self._preview_text(
                status="BLOCKED",
                completed=False,
                summary="EXECUTION_READINESS_PREVIEW_BLOCKED",
                issues=["POLICY_BLOCKED"],
            )
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertEqual("BLOCKED", result["subtitle"])
        self.assertFalse(result["ready"])
        self.assertIn("Blocked", result["badges"])
        self.assertEqual(["POLICY_BLOCKED"], result["issues"])
        self.assertEqual("1", dict(result["table_rows"])["Issues"])

    def test_invalid_preview_builds_non_ready_view_model(self) -> None:
        result = build_execution_readiness_view_model(
            self._preview_text(
                status="INVALID",
                completed=False,
                summary="EXECUTION_READINESS_PREVIEW_INVALID",
                issues=["MISSING_ORDER_PRICE"],
            )
        )

        self.assertEqual("INVALID", result["status"])
        self.assertEqual("INVALID", result["subtitle"])
        self.assertFalse(result["ready"])
        self.assertIn("Invalid", result["badges"])
        self.assertEqual(["MISSING_ORDER_PRICE"], result["issues"])

    def test_sections_and_footer_are_preserved_from_formatter(self) -> None:
        preview_text = self._preview_text()
        result = build_execution_readiness_view_model(preview_text)

        for name in ("Header", "Pipeline", "Warnings", "Issues", "Checks", "Footer"):
            self.assertEqual(preview_text["sections"][name], result["sections"][name])
        self.assertEqual(preview_text["sections"]["Footer"], result["footer"])

    def test_table_rows_are_string_pairs_for_gui_tables(self) -> None:
        result = build_execution_readiness_view_model(
            self._preview_text(warnings=["Preview mode"], issues=[])
        )

        self.assertEqual(
            [
                ("Overall Status", "READY"),
                ("Completed", "True"),
                ("Summary", "EXECUTION_READINESS_PREVIEW_READY"),
                ("Pipeline", "PASS"),
                ("Warnings", "1"),
                ("Issues", "0"),
            ],
            result["table_rows"],
        )
        for key, value in result["table_rows"]:
            self.assertIsInstance(key, str)
            self.assertIsInstance(value, str)

    def test_input_dict_is_not_mutated(self) -> None:
        preview_text = self._preview_text()
        original = deepcopy(preview_text)

        build_execution_readiness_view_model(preview_text)

        self.assertEqual(original, preview_text)

    def test_adapter_does_not_touch_gui_output_save_runtime_queue_or_execution_send_order(self) -> None:
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
            result = build_execution_readiness_view_model(self._preview_text())

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
