# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import hashlib
import unittest
from pathlib import Path
from unittest import mock

from execution_readiness_snapshot_writer_dryrun import validate_snapshot_write_dryrun


class ExecutionReadinessSnapshotWriterDryrunTest(unittest.TestCase):
    def _export(self, **overrides: object) -> dict:
        snapshot_export = {
            "export_version": 1,
            "export_type": "EXECUTION_READINESS_PREVIEW",
            "preview_mode": True,
            "generated_at": "2026-07-05T10:30:00",
            "export_filename": "execution_readiness_preview_20260705_103000.txt",
            "export_path": "audit/preview/",
            "content_type": "text/plain",
            "content": "Execution Readiness Snapshot\nREADY\nEnd of Preview",
            "metadata": {
                "preview_only": True,
                "test_mode": True,
            },
        }
        snapshot_export.update(overrides)
        return snapshot_export

    def _sha256(self, path: Path) -> str | None:
        if not path.exists():
            return None
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def test_ready_export_can_write_in_dryrun_only(self) -> None:
        snapshot_export = self._export()

        result = validate_snapshot_write_dryrun(snapshot_export)

        self.assertEqual("READY", result["status"])
        self.assertTrue(result["can_write"])
        self.assertTrue(result["validated"])
        self.assertEqual("SNAPSHOT_WRITE_DRYRUN_READY", result["summary"])
        self.assertEqual("PASS", result["checks"]["Filename"])
        self.assertEqual("PASS", result["checks"]["ExportPath"])
        self.assertEqual("PASS", result["checks"]["Content"])
        self.assertEqual("PASS", result["checks"]["PreviewMode"])
        self.assertEqual("PASS", result["checks"]["ExportType"])
        self.assertEqual([], result["issues"])
        self.assertEqual(
            "audit/preview/execution_readiness_preview_20260705_103000.txt",
            result["write_plan"]["target_path"],
        )
        self.assertEqual(snapshot_export["export_filename"], result["write_plan"]["target_filename"])
        self.assertEqual(len(snapshot_export["content"]), result["write_plan"]["estimated_size"])
        self.assertEqual("text/plain", result["write_plan"]["content_type"])
        self.assertTrue(result["write_plan"]["preview_only"])
        self.assertIn("Preview only", result["warnings"])
        self.assertIn("File creation disabled", result["warnings"])

    def test_missing_filename_path_and_content_are_invalid(self) -> None:
        result = validate_snapshot_write_dryrun(
            self._export(export_filename="", export_path="", content="")
        )

        self.assertEqual("INVALID", result["status"])
        self.assertFalse(result["can_write"])
        self.assertFalse(result["validated"])
        self.assertEqual("SNAPSHOT_WRITE_INVALID", result["summary"])
        self.assertIn("MISSING_FILENAME", result["issues"])
        self.assertIn("MISSING_EXPORT_PATH", result["issues"])
        self.assertIn("EMPTY_CONTENT", result["issues"])
        self.assertEqual("FAIL", result["checks"]["Filename"])
        self.assertEqual("FAIL", result["checks"]["ExportPath"])
        self.assertEqual("FAIL", result["checks"]["Content"])

    def test_preview_disabled_is_blocked(self) -> None:
        result = validate_snapshot_write_dryrun(self._export(preview_mode=False))

        self.assertEqual("BLOCKED", result["status"])
        self.assertFalse(result["can_write"])
        self.assertFalse(result["validated"])
        self.assertEqual(["PREVIEW_DISABLED"], result["issues"])
        self.assertEqual("FAIL", result["checks"]["PreviewMode"])

    def test_invalid_export_type_is_blocked(self) -> None:
        result = validate_snapshot_write_dryrun(self._export(export_type="OTHER_EXPORT"))

        self.assertEqual("BLOCKED", result["status"])
        self.assertFalse(result["can_write"])
        self.assertFalse(result["validated"])
        self.assertEqual(["INVALID_EXPORT_TYPE"], result["issues"])
        self.assertEqual("FAIL", result["checks"]["ExportType"])

    def test_blocked_takes_priority_over_invalid(self) -> None:
        result = validate_snapshot_write_dryrun(
            self._export(preview_mode=False, export_filename="", content="")
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertEqual(["PREVIEW_DISABLED"], result["issues"])
        self.assertFalse(result["can_write"])

    def test_input_dict_is_not_mutated(self) -> None:
        snapshot_export = self._export()
        original = deepcopy(snapshot_export)

        validate_snapshot_write_dryrun(snapshot_export)

        self.assertEqual(original, snapshot_export)

    def test_writer_dryrun_does_not_create_files_dirs_runtime_rules_or_call_execution_send_order(self) -> None:
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
            result = validate_snapshot_write_dryrun(self._export())

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
