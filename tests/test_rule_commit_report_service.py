from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import rule_commit_report_service


class RuleCommitReportServiceTest(unittest.TestCase):
    def _project_root(self) -> Path:
        return Path(__file__).resolve().parents[1]

    def _rules_json_hash(self) -> str:
        rules_path = next((self._project_root() / "routines").glob("*/rules.json"))
        return hashlib.sha256(rules_path.read_bytes()).hexdigest().upper()

    def _runtime_order_queue_hash(self) -> str:
        queue_path = self._project_root() / "runtime" / "order_queue.json"
        return hashlib.sha256(queue_path.read_bytes()).hexdigest().upper()

    def _success_commit_result(self) -> dict[str, object]:
        return {
            "ok": True,
            "stage": "RULE_APPLY_COMMIT",
            "committed": True,
            "commit_id": "20260705_120000_ABCD1234",
            "rules_path": "TEMP/rules.json",
            "backup_path": "TEMP/backups/rules/rules_backup.json",
            "pre_file_sha256": "A" * 64,
            "post_file_sha256": "B" * 64,
            "pre_rules_hash": "pre_hash",
            "post_rules_hash": "post_hash",
            "apply_preview_hash": "apply_hash",
            "apply_preview_hash_algorithm": "stable_json_sha256",
            "applied_patches": [{"path": "buy.groups[0].conditions"}],
            "skipped_patches": [],
            "final_diff": [{"operation": "merge_conditions"}],
            "post_validation": {"ok": True, "checks": [], "unexpected_changes": []},
            "warnings": [],
            "applied_rules_preview": {"must": "not be written"},
            "current_rules": {"must": "not be written"},
            "post_rules": {"must": "not be written"},
        }

    def _failure_commit_result(self) -> dict[str, object]:
        return {
            "ok": False,
            "stage": "RULE_APPLY_COMMIT_BLOCKED",
            "committed": False,
            "write_completed": True,
            "manual_restore_required": True,
            "rollback_attempted": False,
            "commit_id": "20260705_120001_DEADBEAF",
            "rules_path": "TEMP/rules.json",
            "backup_path": "TEMP/backups/rules/rules_backup.json",
            "pre_file_sha256": "A" * 64,
            "post_file_sha256": "C" * 64,
            "post_validation": {
                "ok": False,
                "checks": [],
                "unexpected_changes": [{"path": "sell.signals.macd_sell"}],
            },
            "blocked_reasons": ["post validation deep compare failed"],
            "warnings": ["manual restore required"],
        }

    def _write_report(self, commit_result: dict[str, object], report_dir: Path) -> tuple[dict[str, object], dict[str, object]]:
        result = rule_commit_report_service.write_rule_commit_report(commit_result, report_dir)
        self.assertTrue(result["ok"], result)
        report_path = Path(str(result["report_path"]))
        report = json.loads(report_path.read_text(encoding="utf-8"))
        return result, report

    def test_success_commit_result_report_is_created(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result, report = self._write_report(self._success_commit_result(), Path(temp_dir) / "rule_commits")

        self.assertEqual("RULE_COMMIT_REPORT_WRITTEN", result["stage"])
        self.assertEqual("rule_apply_commit", report["report_type"])
        self.assertTrue(report["ok"])
        self.assertTrue(report["committed"])
        self.assertTrue(report["commit_accepted"])
        self.assertTrue(report["write_completed"])
        self.assertEqual("stable_json_sha256", report["apply_preview_hash_algorithm"])

    def test_failure_commit_result_report_is_created(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            _result, report = self._write_report(self._failure_commit_result(), Path(temp_dir) / "rule_commits")

        self.assertFalse(report["ok"])
        self.assertFalse(report["committed"])
        self.assertTrue(report["write_completed"])
        self.assertTrue(report["manual_restore_required"])
        self.assertFalse(report["rollback_attempted"])
        self.assertEqual(["post validation deep compare failed"], report["blocked_reasons"])
        self.assertEqual([{"path": "sell.signals.macd_sell"}], report["unexpected_changes"])

    def test_report_dir_is_required(self) -> None:
        result = rule_commit_report_service.write_rule_commit_report(self._success_commit_result(), "")

        self.assertFalse(result["ok"])
        self.assertEqual("RULE_COMMIT_REPORT_BLOCKED", result["stage"])
        self.assertIn("report_dir is required", result["blocked_reasons"])

    def test_commit_result_must_be_dict(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result = rule_commit_report_service.write_rule_commit_report([], Path(temp_dir))  # type: ignore[arg-type]

        self.assertFalse(result["ok"])
        self.assertIn("commit_result must be a dict", result["blocked_reasons"])

    def test_full_rules_snapshot_keys_are_excluded(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            _result, report = self._write_report(self._success_commit_result(), Path(temp_dir) / "rule_commits")

        self.assertNotIn("applied_rules_preview", report)
        self.assertNotIn("current_rules", report)
        self.assertNotIn("post_rules", report)

    def test_report_reload_json_parse_success(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result, _report = self._write_report(self._success_commit_result(), Path(temp_dir) / "rule_commits")
            report_path = Path(str(result["report_path"]))

            loaded = json.loads(report_path.read_text(encoding="utf-8"))

        self.assertEqual("rule_apply_commit", loaded["report_type"])

    def test_temp_file_is_removed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            report_dir = Path(temp_dir) / "rule_commits"
            result, _report = self._write_report(self._success_commit_result(), report_dir)
            report_path = Path(str(result["report_path"]))

            temp_path = report_path.with_name(f".{report_path.name}.tmp")
            temp_files = list(report_dir.glob("*.tmp")) + list(report_dir.glob(".*.tmp"))

        self.assertFalse(temp_path.exists())
        self.assertEqual([], temp_files)

    def test_report_filename_includes_commit_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result, _report = self._write_report(self._success_commit_result(), Path(temp_dir) / "rule_commits")

        self.assertIn("20260705_120000_ABCD1234", Path(str(result["report_path"])).name)

    def test_report_filename_uses_short_hash_without_commit_id(self) -> None:
        commit_result = self._success_commit_result()
        commit_result.pop("commit_id")
        with tempfile.TemporaryDirectory() as temp_dir:
            result, report = self._write_report(commit_result, Path(temp_dir) / "rule_commits")

        name = Path(str(result["report_path"])).name
        suffix = name.removeprefix("rule_commit_").removesuffix(".json").split("_")[-1]
        self.assertEqual(12, len(suffix))
        self.assertIsNone(report["commit_id"])

    def test_actual_rules_json_and_runtime_queue_are_unchanged(self) -> None:
        rules_before = self._rules_json_hash()
        queue_before = self._runtime_order_queue_hash()
        actual_report_dir = self._project_root() / "reports" / "rule_commits"
        actual_report_dir_existed = actual_report_dir.exists()

        with tempfile.TemporaryDirectory() as temp_dir:
            self._write_report(self._success_commit_result(), Path(temp_dir) / "rule_commits")

        self.assertEqual(rules_before, self._rules_json_hash())
        self.assertEqual(queue_before, self._runtime_order_queue_hash())
        self.assertEqual(actual_report_dir_existed, actual_report_dir.exists())

    def test_service_does_not_reference_commit_rollback_or_order_paths(self) -> None:
        source = Path(rule_commit_report_service.__file__).read_text(encoding="utf-8")

        self.assertNotIn("commit_approved_rule_patch_to_rules", source)
        self.assertNotIn("restore_rules_from_backup", source)
        self.assertNotIn("SendOrder", source)
        self.assertNotIn("Chejan", source)
        self.assertNotIn("kiwoom", source.lower())
