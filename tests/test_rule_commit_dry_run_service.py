from __future__ import annotations

from copy import deepcopy
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import hashlib
import json
import tempfile
import unittest

import rule_apply_commit_service
import rule_approval_session_file_service
import rule_commit_dry_run_service
import rule_commit_report_service


def _load_mapper_module():
    project_root = Path(__file__).resolve().parents[1]
    mapper_path = next((project_root / "routines").glob("*/routine_rule_mapper.py"))
    spec = spec_from_file_location("routine_rule_mapper_for_dry_run_test", mapper_path)
    module = module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class RuleCommitDryRunServiceTest(unittest.TestCase):
    def setUp(self):
        self.mapper = _load_mapper_module()
        self.ui_state = {
            "basic": {
                "basic_signal_interval_combo": "5",
            },
            "buy_ui": {
                "signal_filter": {
                    "buy_ocr_compare_combo": "이하",
                    "buy_ocr_sign_combo": "-",
                    "buy_ocr_turn_combo": "상승",
                    "buy_ocr_value_line": "91",
                    "buy_rsi_compare_combo": "<=",
                    "buy_rsi_period_line": "14",
                    "buy_rsi_value_line": "45",
                },
            },
            "sell_ui": {
                "signal_conditions": {
                    "condition_c": {
                        "macd_check": True,
                        "macd_compare_combo": "이하",
                        "macd_kind_combo": "MACD선",
                        "macd_sign_combo": "-",
                        "macd_value_line": "1.0",
                    },
                },
            },
        }
        self.current_rules = {
            "bar": {"bar_minutes": 1},
            "indicators": {
                "macd": {"fast": 12, "slow": 26, "signal": 9},
                "rsi": {"period": 10},
            },
            "buy": {
                "groups": [
                    {
                        "enabled": True,
                        "name": "buy_group_1",
                        "conditions": [
                            {
                                "enabled": True,
                                "target": "OSC",
                                "operator": "TURN_UP",
                            }
                        ],
                    },
                    {"enabled": False, "name": "buy_group_2", "conditions": []},
                ],
            },
            "sell": {
                "signals": {
                    "macd_sell": {
                        "enabled": True,
                        "groups": [
                            {
                                "conditions": [
                                    {
                                        "target": "OSC",
                                        "operator": "TURN_DOWN",
                                    }
                                ]
                            }
                        ],
                    }
                }
            },
        }

    def _project_root(self):
        return Path(__file__).resolve().parents[1]

    def _actual_project_rules_hash(self):
        rules_path = next((self._project_root() / "routines").glob("*/rules.json"))
        return hashlib.sha256(rules_path.read_bytes()).hexdigest().upper()

    def _write_json(self, path, value):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _file_sha256(self, path):
        return hashlib.sha256(Path(path).read_bytes()).hexdigest().upper()

    def _stable_hash(self, value):
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _build_preview(self, rules=None):
        return self.mapper.build_engine_rules_preview_from_ui_state(
            deepcopy(self.ui_state),
            deepcopy(self.current_rules if rules is None else rules),
        )

    def _prepare_actual_inputs(self, root, decisions=None, rules=None):
        rules = deepcopy(self.current_rules if rules is None else rules)
        decisions = {"buy.groups[0].conditions": "APPROVED"} if decisions is None else decisions
        actual_dir = Path(root) / "actual"
        rules_path = actual_dir / "rules.json"
        session_path = actual_dir / "approval_session.json"
        self._write_json(rules_path, rules)
        preview = self._build_preview(rules)
        session = self.mapper.build_rule_approval_session(preview, decisions)
        fingerprint = self.mapper.build_rule_approval_session_fingerprint(rules, preview)
        session["fingerprint"] = fingerprint["fingerprint"]
        session["fingerprint_detail"] = fingerprint
        saved = rule_approval_session_file_service.save_rule_approval_session(session, session_path)
        self.assertTrue(saved["saved"], saved)
        return rules_path, session_path, preview, rules

    def _run_success_preserved(self, temp_dir):
        rules_path, session_path, preview, rules = self._prepare_actual_inputs(temp_dir)
        workspace = Path(temp_dir) / "workspace"
        before = self._file_sha256(rules_path)
        result = rule_commit_dry_run_service.run_rule_commit_dry_run(
            rules_path,
            session_path,
            workspace,
            {
                "preview_result": preview,
                "preserve_workspace_on_success": True,
            },
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual(before, self._file_sha256(rules_path))
        return result, workspace, rules

    def test_success_dry_run_creates_temp_workspace_artifacts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            result, workspace, rules = self._run_success_preserved(temp_dir)

            temp_rules = json.loads((workspace / "rules.json").read_text(encoding="utf-8"))
            backup_path = Path(result["commit_result"]["backup_path"])
            report_path = Path(result["report_result"]["report_path"])
            rollback_safety_path = Path(result["rollback_result"]["rollback_safety_backup_path"])

            self.assertEqual("RULE_COMMIT_DRY_RUN", result["stage"])
            self.assertTrue(result["actual_rules_unchanged"])
            self.assertTrue(result["rollback_verified"])
            self.assertEqual(self._stable_hash(rules), self._stable_hash(temp_rules))
            self.assertTrue(backup_path.exists())
            self.assertEqual(backup_path.parent, workspace / "backups" / "rules")
            self.assertTrue(report_path.exists())
            self.assertEqual(report_path.parent, workspace / "reports" / "rule_commits")
            self.assertTrue(rollback_safety_path.exists())
            self.assertEqual(rollback_safety_path.parent, workspace / "backups" / "rollback_safety")
            self.assertEqual(
                [patch["target_path"] for patch in result["commit_result"]["applied_patches"]],
                ["buy.groups[0].conditions"],
            )

    def test_dry_run_commits_all_approved_candidates_then_rolls_back_temp_rules(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            decisions = {
                "bar.bar_minutes": "APPROVED",
                "buy.groups[0].conditions": "APPROVED",
                "sell.signals.ui_preview_condition_c_macd_sell": "APPROVED",
            }
            rules_path, session_path, preview, rules = self._prepare_actual_inputs(temp_dir, decisions)
            workspace = Path(temp_dir) / "workspace"
            actual_before = self._file_sha256(rules_path)

            result = rule_commit_dry_run_service.run_rule_commit_dry_run(
                rules_path,
                session_path,
                workspace,
                {
                    "preview_result": preview,
                    "preserve_workspace_on_success": True,
                },
            )
            temp_rules = json.loads((workspace / "rules.json").read_text(encoding="utf-8"))

            self.assertTrue(result["ok"], result)
            self.assertEqual(actual_before, self._file_sha256(rules_path))
            self.assertTrue(result["actual_rules_unchanged"])
            self.assertTrue(result["rollback_result"]["rollback_completed"])
            self.assertTrue(result["rollback_verified"])
            self.assertEqual(self._stable_hash(rules), self._stable_hash(temp_rules))
            self.assertEqual(
                [patch["target_path"] for patch in result["commit_result"]["applied_patches"]],
                [
                    "bar.bar_minutes",
                    "buy.groups[0].conditions",
                    "sell.signals.ui_condition_c_macd_sell",
                ],
            )

    def test_dry_run_commits_rsi_candidate_then_rolls_back_temp_rules(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            rules_path, session_path, preview, rules = self._prepare_actual_inputs(
                temp_dir,
                {"indicators.rsi": "APPROVED"},
            )
            workspace = Path(temp_dir) / "workspace"
            actual_before = self._file_sha256(rules_path)

            result = rule_commit_dry_run_service.run_rule_commit_dry_run(
                rules_path,
                session_path,
                workspace,
                {
                    "preview_result": preview,
                    "preserve_workspace_on_success": True,
                },
            )
            temp_rules = json.loads((workspace / "rules.json").read_text(encoding="utf-8"))

            self.assertTrue(result["ok"], result)
            self.assertEqual(actual_before, self._file_sha256(rules_path))
            self.assertTrue(result["actual_rules_unchanged"])
            self.assertTrue(result["rollback_result"]["rollback_completed"])
            self.assertTrue(result["rollback_verified"])
            self.assertEqual(self._stable_hash(rules), self._stable_hash(temp_rules))
            self.assertEqual(
                [patch["target_path"] for patch in result["commit_result"]["applied_patches"]],
                ["indicators.rsi"],
            )

    def test_dry_run_commits_macd_position_candidate_then_rolls_back_temp_rules(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            original_ui_state = deepcopy(self.ui_state)
            self.ui_state["buy_ui"]["signal_filter"]["buy_ocr_value_line"] = ""
            self.ui_state["buy_ui"]["signal_filter"].update({
                "buy_macd_position_enabled": True,
                "buy_macd_position_target": "MACD",
                "buy_macd_position_compare_target": "SIGNAL",
                "buy_macd_position_operator": "<=",
                "buy_macd_position_not": True,
            })
            try:
                rules_path, session_path, preview, rules = self._prepare_actual_inputs(
                    temp_dir,
                    {"buy.groups[0].conditions": "APPROVED"},
                )
            finally:
                self.ui_state = original_ui_state

            workspace = Path(temp_dir) / "workspace"
            actual_before = self._file_sha256(rules_path)

            result = rule_commit_dry_run_service.run_rule_commit_dry_run(
                rules_path,
                session_path,
                workspace,
                {
                    "preview_result": preview,
                    "preserve_workspace_on_success": True,
                },
            )
            temp_rules = json.loads((workspace / "rules.json").read_text(encoding="utf-8"))
            final_diff = result["commit_preview"]["final_diff"]

            self.assertTrue(result["ok"], result)
            self.assertEqual(actual_before, self._file_sha256(rules_path))
            self.assertTrue(result["actual_rules_unchanged"])
            self.assertTrue(result["rollback_result"]["rollback_completed"])
            self.assertTrue(result["rollback_verified"])
            self.assertEqual(self._stable_hash(rules), self._stable_hash(temp_rules))
            self.assertEqual(len(final_diff), 1)
            self.assertEqual(final_diff[0]["path"], "buy.groups[0].conditions")
            self.assertEqual(final_diff[0]["operation"], "merge_conditions")
            self.assertEqual(final_diff[0]["condition"]["target"], "MACD")
            self.assertEqual(final_diff[0]["condition"]["operator"], "<=")
            self.assertEqual(final_diff[0]["condition"]["compare_target"], "SIGNAL")
            self.assertIs(final_diff[0]["condition"]["not"], True)
            self.assertEqual(
                [patch["target_path"] for patch in result["commit_result"]["applied_patches"]],
                ["buy.groups[0].conditions"],
            )

    def test_dry_run_commits_ma_price_compare_and_bollinger_then_rolls_back_temp_rules(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            original_ui_state = deepcopy(self.ui_state)
            self.ui_state["buy_ui"]["signal_filter"] = {
                "buy_ocr_value_line": "",
                "buy_rsi_value_line": "",
                "buy_ma_enabled": True,
                "buy_ma_value_line": "60",
                "buy_ma_direction_combo": "\uc0c1\ud5a5",
                "buy_ma_compare_combo": "\ub3cc\ud30c",
                "buy_bollinger_enabled": True,
                "buy_bollinger_direction_combo": "\ud558\ud5a5",
                "buy_bollinger_value_line": "0.1",
                "buy_bollinger_compare_combo": "\uc774\uc0c1",
            }
            self.ui_state["buy_ui"]["price_compare"] = {
                "enabled": True,
                "type_combo": "\uac00\uaca9\ube44\uad50",
                "left_combo": "\ud604\uc7ac\uac00",
                "right_combo": "\ud3c9\ub2e8\uac00",
                "ratio_line": "0.15",
                "compare_combo": "\uc774\uc0c1",
            }
            try:
                rules_path, session_path, preview, rules = self._prepare_actual_inputs(
                    temp_dir,
                    {"buy.groups[0].conditions": "APPROVED"},
                )
            finally:
                self.ui_state = original_ui_state

            workspace = Path(temp_dir) / "workspace"
            actual_before = self._file_sha256(rules_path)

            result = rule_commit_dry_run_service.run_rule_commit_dry_run(
                rules_path,
                session_path,
                workspace,
                {
                    "preview_result": preview,
                    "preserve_workspace_on_success": True,
                },
            )
            temp_rules = json.loads((workspace / "rules.json").read_text(encoding="utf-8"))
            final_diff = result["commit_preview"]["final_diff"]

            self.assertTrue(result["ok"], result)
            self.assertEqual(actual_before, self._file_sha256(rules_path))
            self.assertTrue(result["actual_rules_unchanged"])
            self.assertTrue(result["rollback_result"]["rollback_completed"])
            self.assertTrue(result["rollback_verified"])
            self.assertEqual(self._stable_hash(rules), self._stable_hash(temp_rules))
            self.assertEqual([diff["path"] for diff in final_diff], [
                "buy.groups[0].conditions",
                "buy.groups[0].conditions",
                "buy.groups[0].conditions",
            ])
            self.assertEqual(final_diff[0]["condition"]["target"], "CLOSE")
            self.assertEqual(final_diff[0]["condition"]["operator"], "CROSS_UP")
            self.assertEqual(final_diff[0]["condition"]["compare_target"], "MA")
            self.assertEqual(final_diff[0]["condition"]["period"], 60)
            self.assertEqual(final_diff[1]["condition"]["value"], -0.1)
            self.assertEqual(final_diff[2]["condition"]["compare_target"], "AVG_PRICE")
            self.assertEqual(
                [patch["target_path"] for patch in result["commit_result"]["applied_patches"]],
                ["buy.groups[0].conditions"],
            )

    def test_success_cleanup_deletes_workspace_by_default(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            rules_path, session_path, preview, _rules = self._prepare_actual_inputs(temp_dir)
            workspace = Path(temp_dir) / "workspace"

            result = rule_commit_dry_run_service.run_rule_commit_dry_run(
                rules_path,
                session_path,
                workspace,
                {"preview_result": preview},
            )

            self.assertTrue(result["ok"], result)
            self.assertTrue(result["cleanup_result"]["ok"])
            self.assertFalse(workspace.exists())

    def test_existing_workspace_is_blocked_by_default(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            rules_path, session_path, preview, _rules = self._prepare_actual_inputs(temp_dir)
            workspace = Path(temp_dir) / "workspace"
            workspace.mkdir()

            result = rule_commit_dry_run_service.run_rule_commit_dry_run(
                rules_path,
                session_path,
                workspace,
                {"preview_result": preview},
            )

            self.assertFalse(result["ok"])
            self.assertEqual("workspace", result["blocked_stage"])
            self.assertIn("workspace already exists", result["blocked_reasons"])
            self.assertTrue(workspace.exists())

    def test_existing_workspace_rules_json_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            rules_path, session_path, preview, _rules = self._prepare_actual_inputs(temp_dir)
            workspace = Path(temp_dir) / "workspace"
            self._write_json(workspace / "rules.json", {"existing": True})

            result = rule_commit_dry_run_service.run_rule_commit_dry_run(
                rules_path,
                session_path,
                workspace,
                {
                    "preview_result": preview,
                    "allow_existing_workspace": True,
                },
            )

            self.assertFalse(result["ok"])
            self.assertIn("workspace rules.json already exists", result["blocked_reasons"])
            self.assertEqual({"existing": True}, json.loads((workspace / "rules.json").read_text(encoding="utf-8")))

    def test_actual_rules_read_only_and_project_rules_unchanged(self):
        project_before = self._actual_project_rules_hash()
        with tempfile.TemporaryDirectory() as temp_dir:
            rules_path, session_path, preview, _rules = self._prepare_actual_inputs(temp_dir)
            actual_before = self._file_sha256(rules_path)

            result = rule_commit_dry_run_service.run_rule_commit_dry_run(
                rules_path,
                session_path,
                Path(temp_dir) / "workspace",
                {"preview_result": preview},
            )

            self.assertTrue(result["ok"], result)
            self.assertEqual(actual_before, self._file_sha256(rules_path))
        self.assertEqual(project_before, self._actual_project_rules_hash())

    def test_session_missing_blocks_and_keeps_workspace(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            rules_path, _session_path, preview, _rules = self._prepare_actual_inputs(temp_dir)
            missing_session_path = Path(temp_dir) / "actual" / "missing_session.json"
            workspace = Path(temp_dir) / "workspace"

            result = rule_commit_dry_run_service.run_rule_commit_dry_run(
                rules_path,
                missing_session_path,
                workspace,
                {"preview_result": preview},
            )

            self.assertFalse(result["ok"])
            self.assertEqual("copy_session", result["blocked_stage"])
            self.assertIn("session file does not exist", result["blocked_reasons"])
            self.assertFalse(workspace.exists())

    def test_gate_blocked_does_not_run_executor(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            rules_path, session_path, preview, _rules = self._prepare_actual_inputs(temp_dir)
            workspace = Path(temp_dir) / "workspace"

            result = rule_commit_dry_run_service.run_rule_commit_dry_run(
                rules_path,
                session_path,
                workspace,
                {
                    "preview_result": preview,
                    "manual_rule_commit_confirmed": False,
                },
            )

            self.assertFalse(result["ok"])
            self.assertEqual("commit_gate", result["blocked_stage"])
            self.assertNotIn("commit_result", result)
            self.assertFalse((workspace / "backups" / "rules").exists())
            self.assertEqual([], list((workspace / "reports" / "rule_commits").glob("*.json")))

    def test_commit_blocked_does_not_write_report_or_rollback(self):
        original_commit = rule_apply_commit_service.commit_approved_rule_patch_to_rules

        def blocked_commit(*args, **kwargs):
            return {
                "ok": False,
                "stage": "RULE_APPLY_COMMIT_BLOCKED",
                "committed": False,
                "blocked_reasons": ["forced commit block"],
                "warnings": [],
            }

        with tempfile.TemporaryDirectory() as temp_dir:
            rules_path, session_path, preview, _rules = self._prepare_actual_inputs(temp_dir)
            workspace = Path(temp_dir) / "workspace"
            rule_apply_commit_service.commit_approved_rule_patch_to_rules = blocked_commit
            try:
                result = rule_commit_dry_run_service.run_rule_commit_dry_run(
                    rules_path,
                    session_path,
                    workspace,
                    {"preview_result": preview},
                )
            finally:
                rule_apply_commit_service.commit_approved_rule_patch_to_rules = original_commit

            self.assertFalse(result["ok"])
            self.assertEqual("commit_executor", result["blocked_stage"])
            self.assertIn("forced commit block", result["blocked_reasons"])
            self.assertEqual([], list((workspace / "reports" / "rule_commits").glob("*.json")))
            self.assertFalse((workspace / "backups" / "rollback_safety").exists())

    def test_report_failure_keeps_workspace(self):
        original_report = rule_commit_report_service.write_rule_commit_report

        def failed_report(*args, **kwargs):
            return {
                "ok": False,
                "stage": "RULE_COMMIT_REPORT_BLOCKED",
                "blocked_reasons": ["forced report failure"],
                "warnings": [],
            }

        with tempfile.TemporaryDirectory() as temp_dir:
            rules_path, session_path, preview, _rules = self._prepare_actual_inputs(temp_dir)
            workspace = Path(temp_dir) / "workspace"
            rule_commit_report_service.write_rule_commit_report = failed_report
            try:
                result = rule_commit_dry_run_service.run_rule_commit_dry_run(
                    rules_path,
                    session_path,
                    workspace,
                    {"preview_result": preview},
                )
            finally:
                rule_commit_report_service.write_rule_commit_report = original_report

            self.assertFalse(result["ok"])
            self.assertEqual("commit_report", result["blocked_stage"])
            self.assertIn("forced report failure", result["blocked_reasons"])
            self.assertTrue(workspace.exists())
            self.assertTrue(Path(result["commit_result"]["backup_path"]).exists())

    def test_cleanup_failure_is_warning_not_dry_run_failure(self):
        original_cleanup = rule_commit_dry_run_service._cleanup_workspace

        def failed_cleanup(workspace):
            return {
                "ok": False,
                "stage": "DRY_RUN_CLEANUP_FAILED",
                "workspace": str(workspace),
                "blocked_reasons": ["forced cleanup failure"],
                "warnings": [],
            }

        with tempfile.TemporaryDirectory() as temp_dir:
            rules_path, session_path, preview, _rules = self._prepare_actual_inputs(temp_dir)
            workspace = Path(temp_dir) / "workspace"
            rule_commit_dry_run_service._cleanup_workspace = failed_cleanup
            try:
                result = rule_commit_dry_run_service.run_rule_commit_dry_run(
                    rules_path,
                    session_path,
                    workspace,
                    {"preview_result": preview},
                )
            finally:
                rule_commit_dry_run_service._cleanup_workspace = original_cleanup

            self.assertTrue(result["ok"], result)
            self.assertFalse(result["cleanup_result"]["ok"])
            self.assertIn("forced cleanup failure", result["warnings"])
            self.assertTrue(workspace.exists())

    def test_preview_context_is_required_without_ui_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            rules_path, session_path, _preview, _rules = self._prepare_actual_inputs(temp_dir)
            workspace = Path(temp_dir) / "workspace"

            result = rule_commit_dry_run_service.run_rule_commit_dry_run(
                rules_path,
                session_path,
                workspace,
                {},
            )

            self.assertFalse(result["ok"])
            self.assertEqual("preview", result["blocked_stage"])
            self.assertIn("preview_result or ui_state is required", result["blocked_reasons"])

    def test_ui_state_can_build_preview(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            rules_path, session_path, _preview, _rules = self._prepare_actual_inputs(temp_dir)
            workspace = Path(temp_dir) / "workspace"

            result = rule_commit_dry_run_service.run_rule_commit_dry_run(
                rules_path,
                session_path,
                workspace,
                {
                    "ui_state": deepcopy(self.ui_state),
                    "preserve_workspace_on_success": True,
                },
            )

            self.assertTrue(result["ok"], result)
            self.assertTrue(workspace.exists())

    def test_actual_reports_and_backups_are_not_created(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            rules_path, session_path, preview, _rules = self._prepare_actual_inputs(temp_dir)
            actual_reports = rules_path.parent / "reports" / "rule_commits"
            actual_rule_backups = rules_path.parent / "backups"

            result = rule_commit_dry_run_service.run_rule_commit_dry_run(
                rules_path,
                session_path,
                Path(temp_dir) / "workspace",
                {"preview_result": preview},
            )

            self.assertTrue(result["ok"], result)
            self.assertFalse(actual_reports.exists())
            self.assertFalse(actual_rule_backups.exists())

    def test_service_has_no_gui_engine_order_references(self):
        source = Path(rule_commit_dry_run_service.__file__).read_text(encoding="utf-8")

        self.assertNotIn("SendOrder", source)
        self.assertNotIn("Chejan", source)
        self.assertNotIn("kiwoom", source.lower())
        self.assertNotIn("QPushButton", source)


if __name__ == "__main__":
    unittest.main()
