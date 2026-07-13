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


def _load_mapper_module():
    project_root = Path(__file__).resolve().parents[1]
    mapper_path = next((project_root / "routines").glob("*/routine_rule_mapper.py"))
    spec = spec_from_file_location("routine_rule_mapper_for_commit_service_test", mapper_path)
    module = module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class RuleApplyCommitServiceTest(unittest.TestCase):
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

    def _rules_json_hash(self):
        project_root = Path(__file__).resolve().parents[1]
        rules_path = next((project_root / "routines").glob("*/rules.json"))
        return hashlib.sha256(rules_path.read_bytes()).hexdigest().upper()

    def _runtime_order_queue_hash(self):
        project_root = Path(__file__).resolve().parents[1]
        order_queue_path = project_root / "runtime" / "order_queue.json"
        return hashlib.sha256(order_queue_path.read_bytes()).hexdigest().upper()

    def _file_sha256(self, path):
        return hashlib.sha256(Path(path).read_bytes()).hexdigest().upper()

    def _write_rules(self, path, rules):
        Path(path).write_text(json.dumps(rules, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _composite_config(self):
        return {
            "enabled": True,
            "logic": "OR",
            "include_unreferenced_active_filters": "AND_REQUIRED",
            "groups": [
                {
                    "enabled": True,
                    "logic": "AND",
                    "filters": ["rsi", "moving_average"],
                },
                {
                    "enabled": True,
                    "logic": "AND",
                    "filters": ["bollinger", "ocr"],
                },
            ],
        }

    def _enable_composite_ui_state(self):
        self.ui_state["buy_ui"]["signal_filter"]["buy_composite"] = self._composite_config()

    def _build_preview(self, rules=None):
        return self.mapper.build_engine_rules_preview_from_ui_state(
            deepcopy(self.ui_state),
            deepcopy(self.current_rules if rules is None else rules),
        )

    def _build_apply_and_gate(self, rules_path, session_path, decisions, rules=None):
        rules = deepcopy(self.current_rules if rules is None else rules)
        preview = self._build_preview(rules)
        session = self.mapper.build_rule_approval_session(preview, decisions)
        fingerprint = self.mapper.build_rule_approval_session_fingerprint(rules, preview)
        session["fingerprint"] = fingerprint["fingerprint"]
        session["fingerprint_detail"] = fingerprint
        saved = rule_approval_session_file_service.save_rule_approval_session(session, session_path)
        self.assertTrue(saved["saved"])
        pipeline = self.mapper.build_rule_pipeline_preview(rules, preview, session)
        gate = self.mapper.evaluate_rule_commit_gate_from_saved_session(
            rules,
            preview,
            session_path,
            {
                "expected_rules_hash": self.mapper._stable_hash(rules),
                "approval_session_dirty": False,
                "manual_rule_commit_confirmed": True,
            },
        )
        self.assertTrue(gate["commit_allowed"])
        context = {
            "allowed_rules_path": str(Path(rules_path).resolve()),
            "expected_file_sha256": self._file_sha256(rules_path),
            "expected_rules_hash": self.mapper._stable_hash(rules),
        }
        return pipeline["apply_preview"], gate, context

    def _bind_gate_to_apply_preview(self, gate, apply_preview):
        changed_gate = deepcopy(gate)
        changed_hash = rule_apply_commit_service._apply_preview_hash(apply_preview)
        changed_gate["apply_preview_hash"] = changed_hash
        changed_gate["commit_preview"]["apply_preview_hash"] = changed_hash
        return changed_gate

    def _assert_post_validation_blocked(self, result, expected_path):
        self.assertFalse(result["ok"])
        self.assertIn("post validation deep compare failed", result["blocked_reasons"])
        self.assertTrue(result["write_completed"])
        self.assertFalse(result["post_validation_ok"])
        self.assertFalse(result["commit_accepted"])
        self.assertTrue(result["manual_restore_required"])
        self.assertFalse(result["rollback_attempted"])
        self.assertTrue(result["backup_path"])
        self.assertFalse(result["post_validation"]["ok"])
        self.assertTrue(
            any(change.get("path") == expected_path for change in result["post_validation"]["unexpected_changes"]),
            result["post_validation"]["unexpected_changes"],
        )

    def _commit_mutated_apply_preview(self, decisions, mutate, rules=None):
        temp_dir_context = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir_context.cleanup)
        temp_dir = temp_dir_context.name
        rules_path = Path(temp_dir) / "rules.json"
        session_path = Path(temp_dir) / "approval_session.json"
        base_rules = deepcopy(self.current_rules if rules is None else rules)
        self._write_rules(rules_path, base_rules)
        apply_preview, gate, context = self._build_apply_and_gate(
            rules_path,
            session_path,
            decisions,
            base_rules,
        )
        apply_preview = deepcopy(apply_preview)
        mutate(apply_preview)
        gate = self._bind_gate_to_apply_preview(gate, apply_preview)
        return rule_apply_commit_service.commit_approved_rule_patch_to_rules(
            rules_path,
            apply_preview,
            gate,
            context,
        )

    def test_commit_allowed_false_is_blocked(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            rules_path = Path(temp_dir) / "rules.json"
            session_path = Path(temp_dir) / "approval_session.json"
            self._write_rules(rules_path, self.current_rules)
            apply_preview, gate, context = self._build_apply_and_gate(
                rules_path,
                session_path,
                {"buy.filters.ocr": "APPROVED"},
            )
            gate["commit_allowed"] = False
            before = self._file_sha256(rules_path)

            result = rule_apply_commit_service.commit_approved_rule_patch_to_rules(
                rules_path,
                apply_preview,
                gate,
                context,
            )

            self.assertFalse(result["ok"])
            self.assertIn("commit gate is not allowed", result["blocked_reasons"])
            self.assertEqual(before, self._file_sha256(rules_path))

    def test_apply_preview_missing_is_blocked(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            rules_path = Path(temp_dir) / "rules.json"
            session_path = Path(temp_dir) / "approval_session.json"
            self._write_rules(rules_path, self.current_rules)
            _apply_preview, gate, context = self._build_apply_and_gate(
                rules_path,
                session_path,
                {"buy.filters.ocr": "APPROVED"},
            )

            result = rule_apply_commit_service.commit_approved_rule_patch_to_rules(
                rules_path,
                {},
                gate,
                context,
            )

            self.assertFalse(result["ok"])
            self.assertIn("apply_preview.applied_rules_preview is required", result["blocked_reasons"])

    def test_applied_rules_preview_missing_is_blocked(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            rules_path = Path(temp_dir) / "rules.json"
            session_path = Path(temp_dir) / "approval_session.json"
            self._write_rules(rules_path, self.current_rules)
            apply_preview, gate, context = self._build_apply_and_gate(
                rules_path,
                session_path,
                {"buy.filters.ocr": "APPROVED"},
            )
            apply_preview = deepcopy(apply_preview)
            apply_preview.pop("applied_rules_preview", None)

            result = rule_apply_commit_service.commit_approved_rule_patch_to_rules(
                rules_path,
                apply_preview,
                gate,
                context,
            )

            self.assertFalse(result["ok"])
            self.assertIn("apply_preview.applied_rules_preview is required", result["blocked_reasons"])

    def test_expected_file_sha256_mismatch_is_blocked(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            rules_path = Path(temp_dir) / "rules.json"
            session_path = Path(temp_dir) / "approval_session.json"
            self._write_rules(rules_path, self.current_rules)
            apply_preview, gate, context = self._build_apply_and_gate(
                rules_path,
                session_path,
                {"buy.filters.ocr": "APPROVED"},
            )
            context["expected_file_sha256"] = "mismatch"

            result = rule_apply_commit_service.commit_approved_rule_patch_to_rules(
                rules_path,
                apply_preview,
                gate,
                context,
            )

            self.assertFalse(result["ok"])
            self.assertIn("expected file SHA256 mismatch", result["blocked_reasons"])

    def test_expected_stable_hash_mismatch_is_blocked(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            rules_path = Path(temp_dir) / "rules.json"
            session_path = Path(temp_dir) / "approval_session.json"
            self._write_rules(rules_path, self.current_rules)
            apply_preview, gate, context = self._build_apply_and_gate(
                rules_path,
                session_path,
                {"buy.filters.ocr": "APPROVED"},
            )
            context["expected_rules_hash"] = "mismatch"

            result = rule_apply_commit_service.commit_approved_rule_patch_to_rules(
                rules_path,
                apply_preview,
                gate,
                context,
            )

            self.assertFalse(result["ok"])
            self.assertIn("expected rules stable hash mismatch", result["blocked_reasons"])

    def test_gate_hash_mismatch_is_blocked(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            rules_path = Path(temp_dir) / "rules.json"
            session_path = Path(temp_dir) / "approval_session.json"
            self._write_rules(rules_path, self.current_rules)
            apply_preview, gate, context = self._build_apply_and_gate(
                rules_path,
                session_path,
                {"buy.filters.ocr": "APPROVED"},
            )
            gate = deepcopy(gate)
            gate["rules_hash_check"]["current_rules_hash"] = "mismatch"

            result = rule_apply_commit_service.commit_approved_rule_patch_to_rules(
                rules_path,
                apply_preview,
                gate,
                context,
            )

            self.assertFalse(result["ok"])
            self.assertIn("commit gate rules hash mismatch", result["blocked_reasons"])

    def test_allowed_rules_path_missing_is_blocked(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            rules_path = Path(temp_dir) / "rules.json"
            session_path = Path(temp_dir) / "approval_session.json"
            self._write_rules(rules_path, self.current_rules)
            apply_preview, gate, context = self._build_apply_and_gate(
                rules_path,
                session_path,
                {"buy.filters.ocr": "APPROVED"},
            )
            context.pop("allowed_rules_path", None)

            result = rule_apply_commit_service.commit_approved_rule_patch_to_rules(
                rules_path,
                apply_preview,
                gate,
                context,
            )

            self.assertFalse(result["ok"])
            self.assertIn("allowed_rules_path is required", result["blocked_reasons"])

    def test_disallowed_rules_path_is_blocked(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            allowed_path = Path(temp_dir) / "allowed" / "rules.json"
            rules_path = Path(temp_dir) / "other" / "rules.json"
            allowed_path.parent.mkdir()
            rules_path.parent.mkdir()
            self._write_rules(allowed_path, self.current_rules)
            self._write_rules(rules_path, self.current_rules)
            session_path = Path(temp_dir) / "approval_session.json"
            apply_preview, gate, context = self._build_apply_and_gate(
                rules_path,
                session_path,
                {"buy.filters.ocr": "APPROVED"},
            )
            context["allowed_rules_path"] = str(allowed_path.resolve())

            result = rule_apply_commit_service.commit_approved_rule_patch_to_rules(
                rules_path,
                apply_preview,
                gate,
                context,
            )

            self.assertFalse(result["ok"])
            self.assertIn("rules path is not allowed", result["blocked_reasons"])

    def test_relative_traversal_rules_path_is_blocked(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            allowed_path = root / "allowed" / "rules.json"
            other_path = root / "other" / "rules.json"
            allowed_path.parent.mkdir()
            other_path.parent.mkdir()
            self._write_rules(allowed_path, self.current_rules)
            self._write_rules(other_path, self.current_rules)
            traversal_path = root / "allowed" / ".." / "other" / "rules.json"
            session_path = root / "approval_session.json"
            apply_preview, gate, context = self._build_apply_and_gate(
                other_path,
                session_path,
                {"buy.filters.ocr": "APPROVED"},
            )
            context["allowed_rules_path"] = str(allowed_path.resolve())

            result = rule_apply_commit_service.commit_approved_rule_patch_to_rules(
                traversal_path,
                apply_preview,
                gate,
                context,
            )

            self.assertFalse(result["ok"])
            self.assertIn("rules path is not allowed", result["blocked_reasons"])

    def test_rules_file_name_must_be_rules_json(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            rules_path = Path(temp_dir) / "not_rules.json"
            session_path = Path(temp_dir) / "approval_session.json"
            self._write_rules(rules_path, self.current_rules)
            apply_preview, gate, context = self._build_apply_and_gate(
                rules_path,
                session_path,
                {"buy.filters.ocr": "APPROVED"},
            )
            context["allowed_rules_path"] = str(rules_path.resolve())

            result = rule_apply_commit_service.commit_approved_rule_patch_to_rules(
                rules_path,
                apply_preview,
                gate,
                context,
            )

            self.assertFalse(result["ok"])
            self.assertIn("rules file name must be rules.json", result["blocked_reasons"])

    def test_apply_preview_hash_match_allows_existing_success_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            rules_path = Path(temp_dir) / "rules.json"
            session_path = Path(temp_dir) / "approval_session.json"
            self._write_rules(rules_path, self.current_rules)
            apply_preview, gate, context = self._build_apply_and_gate(
                rules_path,
                session_path,
                {"buy.filters.ocr": "APPROVED"},
            )

            result = rule_apply_commit_service.commit_approved_rule_patch_to_rules(
                rules_path,
                apply_preview,
                gate,
                context,
            )

            self.assertTrue(result["ok"])
            self.assertEqual(result["apply_preview_hash"], gate["apply_preview_hash"])
            self.assertEqual(result["apply_preview_hash_algorithm"], "stable_json_sha256")

    def test_apply_preview_hash_mismatch_is_blocked_before_backup_or_write(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            rules_path = Path(temp_dir) / "rules.json"
            session_path = Path(temp_dir) / "approval_session.json"
            self._write_rules(rules_path, self.current_rules)
            before = self._file_sha256(rules_path)
            apply_preview, gate, context = self._build_apply_and_gate(
                rules_path,
                session_path,
                {"buy.filters.ocr": "APPROVED"},
            )
            changed = deepcopy(apply_preview)
            changed["applied_rules_preview"]["buy"]["groups"][0]["conditions"].append({
                "target": "OSC",
                "operator": "<=",
                "value": -92.0,
            })

            result = rule_apply_commit_service.commit_approved_rule_patch_to_rules(
                rules_path,
                changed,
                gate,
                context,
            )

            self.assertFalse(result["ok"])
            self.assertIn(
                "apply preview changed after commit gate; rerun commit preview and gate",
                result["blocked_reasons"],
            )
            self.assertEqual(before, self._file_sha256(rules_path))
            self.assertFalse((Path(temp_dir) / "backups").exists())

    def test_apply_preview_hash_missing_from_gate_is_blocked(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            rules_path = Path(temp_dir) / "rules.json"
            session_path = Path(temp_dir) / "approval_session.json"
            self._write_rules(rules_path, self.current_rules)
            before = self._file_sha256(rules_path)
            apply_preview, gate, context = self._build_apply_and_gate(
                rules_path,
                session_path,
                {"buy.filters.ocr": "APPROVED"},
            )
            gate = deepcopy(gate)
            gate.pop("apply_preview_hash", None)

            result = rule_apply_commit_service.commit_approved_rule_patch_to_rules(
                rules_path,
                apply_preview,
                gate,
                context,
            )

            self.assertFalse(result["ok"])
            self.assertIn("apply preview hash is required", result["blocked_reasons"])
            self.assertEqual(before, self._file_sha256(rules_path))
            self.assertFalse((Path(temp_dir) / "backups").exists())

    def test_gate_and_commit_preview_hash_mismatch_is_blocked(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            rules_path = Path(temp_dir) / "rules.json"
            session_path = Path(temp_dir) / "approval_session.json"
            self._write_rules(rules_path, self.current_rules)
            before = self._file_sha256(rules_path)
            apply_preview, gate, context = self._build_apply_and_gate(
                rules_path,
                session_path,
                {"buy.filters.ocr": "APPROVED"},
            )
            gate = deepcopy(gate)
            gate["apply_preview_hash"] = "mismatch"

            result = rule_apply_commit_service.commit_approved_rule_patch_to_rules(
                rules_path,
                apply_preview,
                gate,
                context,
            )

            self.assertFalse(result["ok"])
            self.assertIn(
                "apply preview hash mismatch between commit gate and commit preview",
                result["blocked_reasons"],
            )
            self.assertEqual(before, self._file_sha256(rules_path))
            self.assertFalse((Path(temp_dir) / "backups").exists())

    def test_backup_created_atomic_write_and_tmp_removed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            rules_path = Path(temp_dir) / "rules.json"
            session_path = Path(temp_dir) / "approval_session.json"
            self._write_rules(rules_path, self.current_rules)
            apply_preview, gate, context = self._build_apply_and_gate(
                rules_path,
                session_path,
                {"buy.filters.ocr": "APPROVED"},
            )

            result = rule_apply_commit_service.commit_approved_rule_patch_to_rules(
                rules_path,
                apply_preview,
                gate,
                context,
            )

            self.assertTrue(result["ok"])
            self.assertTrue(Path(result["backup_path"]).exists())
            self.assertEqual(Path(result["backup_path"]).parent, Path(temp_dir) / "backups" / "rules")
            self.assertFalse((Path(temp_dir) / ".rules.json.tmp").exists())
            self.assertNotEqual(result["pre_file_sha256"], result["post_file_sha256"])

    def test_backup_failure_is_blocked(self):
        original_backup = rule_apply_commit_service._create_backup

        def failing_backup(*args, **kwargs):
            raise OSError("backup denied")

        with tempfile.TemporaryDirectory() as temp_dir:
            rules_path = Path(temp_dir) / "rules.json"
            session_path = Path(temp_dir) / "approval_session.json"
            self._write_rules(rules_path, self.current_rules)
            apply_preview, gate, context = self._build_apply_and_gate(
                rules_path,
                session_path,
                {"buy.filters.ocr": "APPROVED"},
            )
            before = self._file_sha256(rules_path)
            rule_apply_commit_service._create_backup = failing_backup
            try:
                result = rule_apply_commit_service.commit_approved_rule_patch_to_rules(
                    rules_path,
                    apply_preview,
                    gate,
                    context,
                )
            finally:
                rule_apply_commit_service._create_backup = original_backup

            self.assertFalse(result["ok"])
            self.assertIn("failed to create rules backup: backup denied", result["blocked_reasons"])
            self.assertEqual(before, self._file_sha256(rules_path))

    def test_buy_condition_added_and_existing_turn_up_preserved(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            rules_path = Path(temp_dir) / "rules.json"
            session_path = Path(temp_dir) / "approval_session.json"
            self._write_rules(rules_path, self.current_rules)
            apply_preview, gate, context = self._build_apply_and_gate(
                rules_path,
                session_path,
                {"buy.filters.ocr": "APPROVED"},
            )

            result = rule_apply_commit_service.commit_approved_rule_patch_to_rules(
                rules_path,
                apply_preview,
                gate,
                context,
            )
            saved = json.loads(rules_path.read_text(encoding="utf-8"))
            conditions = saved["buy"]["groups"][0]["conditions"]
            ocr_filter = saved["buy"]["filters"]["ocr"]

            self.assertTrue(result["ok"])
            self.assertTrue(any(condition.get("target") == "OSC" and condition.get("operator") == "TURN_UP" for condition in conditions))
            self.assertEqual(ocr_filter["conditions_logic"], "AND")
            self.assertTrue(any(condition.get("target") == "OSC" and condition.get("operator") == "<=" and condition.get("value") == -91.0 for condition in ocr_filter["conditions"]))
            self.assertEqual(len(saved["buy"]["groups"]), len(self.current_rules["buy"]["groups"]))
            self.assertEqual(saved.get("bar"), self.current_rules.get("bar"))
            self.assertEqual(saved["sell"], self.current_rules["sell"])
            self.assertEqual(
                [patch["target_path"] for patch in result["applied_patches"]],
                ["buy.filters.ocr"],
            )

    def test_buy_ma_price_compare_and_bollinger_conditions_added_and_other_sections_unchanged(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            rules_path = Path(temp_dir) / "rules.json"
            session_path = Path(temp_dir) / "approval_session.json"
            self._write_rules(rules_path, self.current_rules)
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
                apply_preview, gate, context = self._build_apply_and_gate(
                    rules_path,
                    session_path,
                    {
                        "buy.filters.moving_average": "APPROVED",
                        "buy.filters.price_compare": "APPROVED",
                        "buy.filters.ocr": "APPROVED",
                    },
                )
            finally:
                self.ui_state = original_ui_state

            result = rule_apply_commit_service.commit_approved_rule_patch_to_rules(
                rules_path,
                apply_preview,
                gate,
                context,
            )
            saved = json.loads(rules_path.read_text(encoding="utf-8"))
            conditions = saved["buy"]["groups"][0]["conditions"]

            self.assertTrue(result["ok"], result)
            self.assertEqual(
                saved["buy"]["filters"]["moving_average"],
                {
                    "enabled": True,
                    "conditions": [{
                        "enabled": True,
                        "not": False,
                        "target": "CLOSE",
                        "operator": "CROSS_UP",
                        "compare_target": "MA",
                        "period": 60,
                        "description": "UI preview: BUY current price / 60MA filter",
                    }],
                },
            )
            self.assertEqual(
                saved["buy"]["filters"]["price_compare"],
                {
                    "enabled": True,
                    "conditions_logic": "OR",
                    "conditions": [{
                        "enabled": True,
                        "not": False,
                        "target": "CLOSE",
                        "operator": ">=",
                        "compare_target": "AVG_PRICE",
                        "value": 0.15,
                        "description": "UI preview: BUY price compare filter condition",
                    }],
                },
            )
            # bollinger candidate exists in the preview but is NOT approved in
            # this test, so it must not be applied to the rules.
            self.assertNotIn("bollinger", saved.get("buy", {}).get("filters", {}))
            self.assertFalse(any(condition.get("compare_target") == "AVG_PRICE" for condition in conditions))
            self.assertEqual(saved.get("bar"), self.current_rules.get("bar"))
            self.assertEqual(saved["sell"], self.current_rules["sell"])
            self.assertEqual(saved["indicators"], self.current_rules["indicators"])
            self.assertEqual(
                [patch["target_path"] for patch in result["applied_patches"]],
                ["buy.filters.moving_average", "buy.filters.price_compare"],
            )
            self.assertTrue(result["post_validation"]["ok"])
            self.assertEqual(result["post_validation"]["unexpected_changes"], [])

    def test_buy_bollinger_filter_committed_via_set_filter(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            rules_path = Path(temp_dir) / "rules.json"
            session_path = Path(temp_dir) / "approval_session.json"
            self._write_rules(rules_path, self.current_rules)
            original_ui_state = deepcopy(self.ui_state)
            self.ui_state["buy_ui"]["signal_filter"] = {
                "buy_ocr_value_line": "",
                "buy_rsi_value_line": "",
                "buy_bollinger_enabled": True,
                "buy_bollinger_direction_combo": "하향",
                "buy_bollinger_value_line": "0.1",
                "buy_bollinger_compare_combo": "이상",
            }
            try:
                apply_preview, gate, context = self._build_apply_and_gate(
                    rules_path,
                    session_path,
                    {"buy.filters.bollinger": "APPROVED"},
                )
            finally:
                self.ui_state = original_ui_state

            result = rule_apply_commit_service.commit_approved_rule_patch_to_rules(
                rules_path,
                apply_preview,
                gate,
                context,
            )
            saved = json.loads(rules_path.read_text(encoding="utf-8"))

            self.assertTrue(result["ok"], result)
            self.assertEqual(
                saved["buy"]["filters"]["bollinger"],
                {
                    "enabled": True,
                    "conditions": [{
                        "enabled": True,
                        "not": False,
                        "target": "CLOSE",
                        "operator": ">=",
                        "compare_target": "BOLLINGER",
                        "value": -0.1,
                        "description": "UI preview: BUY current price / Bollinger filter",
                    }],
                },
            )
            self.assertEqual(saved.get("bar"), self.current_rules.get("bar"))
            self.assertEqual(saved["sell"], self.current_rules["sell"])
            self.assertEqual(saved["indicators"], self.current_rules["indicators"])
            self.assertEqual(saved["buy"]["groups"], self.current_rules["buy"]["groups"])
            self.assertEqual(
                [patch["target_path"] for patch in result["applied_patches"]],
                ["buy.filters.bollinger"],
            )
            self.assertTrue(result["post_validation"]["ok"])
            self.assertEqual(result["post_validation"]["unexpected_changes"], [])

    def test_buy_rsi_filter_committed_via_set_filter(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            rules_path = Path(temp_dir) / "rules.json"
            session_path = Path(temp_dir) / "approval_session.json"
            self._write_rules(rules_path, self.current_rules)
            apply_preview, gate, context = self._build_apply_and_gate(
                rules_path,
                session_path,
                {"buy.filters.rsi": "APPROVED"},
            )

            result = rule_apply_commit_service.commit_approved_rule_patch_to_rules(
                rules_path,
                apply_preview,
                gate,
                context,
            )
            saved = json.loads(rules_path.read_text(encoding="utf-8"))

            self.assertTrue(result["ok"], result)
            self.assertEqual(
                saved["buy"]["filters"]["rsi"],
                {
                    "enabled": True,
                    "conditions": [{
                        "enabled": True,
                        "operator": "<=",
                        "threshold": 45.0,
                        "period": 14,
                    }],
                },
            )
            self.assertEqual(saved.get("bar"), self.current_rules.get("bar"))
            self.assertEqual(saved["sell"], self.current_rules["sell"])
            self.assertEqual(saved["indicators"], self.current_rules["indicators"])
            self.assertEqual(saved["buy"]["groups"], self.current_rules["buy"]["groups"])
            self.assertEqual(
                [patch["target_path"] for patch in result["applied_patches"]],
                ["buy.filters.rsi"],
            )
            self.assertTrue(result["post_validation"]["ok"])
            self.assertEqual(result["post_validation"]["unexpected_changes"], [])

    def test_buy_composite_filter_committed_via_set_filter(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            rules_path = Path(temp_dir) / "rules.json"
            session_path = Path(temp_dir) / "approval_session.json"
            self._write_rules(rules_path, self.current_rules)
            original_ui_state = deepcopy(self.ui_state)
            self._enable_composite_ui_state()
            try:
                apply_preview, gate, context = self._build_apply_and_gate(
                    rules_path,
                    session_path,
                    {"buy.filters.composite": "APPROVED"},
                )
            finally:
                self.ui_state = original_ui_state

            result = rule_apply_commit_service.commit_approved_rule_patch_to_rules(
                rules_path,
                apply_preview,
                gate,
                context,
            )
            saved = json.loads(rules_path.read_text(encoding="utf-8"))

            self.assertTrue(result["ok"], result)
            self.assertEqual(saved["buy"]["filters"]["composite"], self._composite_config())
            self.assertEqual(saved.get("bar"), self.current_rules.get("bar"))
            self.assertEqual(saved["sell"], self.current_rules["sell"])
            self.assertEqual(saved["indicators"], self.current_rules["indicators"])
            self.assertEqual(saved["buy"]["groups"], self.current_rules["buy"]["groups"])
            self.assertEqual(
                [patch["target_path"] for patch in result["applied_patches"]],
                ["buy.filters.composite"],
            )
            self.assertTrue(result["post_validation"]["ok"])
            self.assertEqual(result["post_validation"]["unexpected_changes"], [])

    def test_buy_execution_base_and_repeat_committed_via_set_execution_policy(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            rules_path = Path(temp_dir) / "rules.json"
            session_path = Path(temp_dir) / "approval_session.json"
            self._write_rules(rules_path, self.current_rules)
            original_ui_state = deepcopy(self.ui_state)
            self.ui_state["buy_ui"]["base"] = {
                "hoga_combo": "\ub2e8\uc77c\ud638\uac00",
                "order_combo": "\uc8fc\ubb38\uac00",
                "up_line": "2",
                "down_line": "1",
                "time_mode_combo": "\ub2e4\uc911\uc2dc\uac04",
                "time_value_line": "3",
                "time_unit_combo": "\ubd84",
                "time_range_combo": "\uc774\ub0b4",
                "time_count_line": "4",
                "time_order_combo": "\ud604\uc7ac\uac00",
                "ratio_left_combo": "\uc8fc\ubb38\uac00",
                "ratio_right_combo": "\ud3c9\ub2e8\uac00",
                "ratio_direction_combo": "\uc0c1\ud5a5",
                "ratio_value_line": "1.5",
                "ratio_compare_combo": "\uc774\uc0c1",
                "ratio_count_line": "2",
            }
            self.ui_state["buy_ui"]["repeat"] = {
                "apply_all_check": True,
                "detail_mode_combo": "\ud68c\ucc28\uae30\uc900",
                "round_operator_combo": "+",
                "round_budget_line": "100000",
                "budget_ratio_line": "25",
                "active_direction_combo": "\ud558\ud5a5",
                "active_ratio_line": "0.7",
                "active_compare_combo": "\uc774\ud558",
            }
            try:
                apply_preview, gate, context = self._build_apply_and_gate(
                    rules_path,
                    session_path,
                    {
                        "buy.execution.base": "APPROVED",
                        "buy.execution.repeat": "APPROVED",
                    },
                )
            finally:
                self.ui_state = original_ui_state

            result = rule_apply_commit_service.commit_approved_rule_patch_to_rules(
                rules_path,
                apply_preview,
                gate,
                context,
            )
            saved = json.loads(rules_path.read_text(encoding="utf-8"))

            self.assertTrue(result["ok"], result)
            self.assertEqual(
                saved["buy"]["execution"]["base"],
                {
                    "hoga_mode": "SINGLE",
                    "order_price_basis": "ORDER_PRICE",
                    "hoga_up": 2,
                    "hoga_down": 1,
                    "point_mode": "MULTI_TIME",
                    "point_value": 3.0,
                    "point_unit": "MINUTE",
                    "point_range": "WITHIN",
                    "point_count": 4,
                    "ratio_left": "ORDER_PRICE",
                    "ratio_right": "AVG_PRICE",
                    "ratio_direction": "UP",
                    "ratio_value": 1.5,
                    "ratio_compare": ">=",
                    "ratio_count": 2,
                },
            )
            self.assertEqual(
                saved["buy"]["execution"]["repeat"],
                {
                    "apply_all": True,
                    "detail_mode": "ROUND",
                    "round_operator": "ADD",
                    "round_budget_value": 100000.0,
                    "budget_ratio": 25.0,
                    "active_direction": "DOWN",
                    "active_ratio": 0.7,
                    "active_compare": "<=",
                },
            )
            self.assertEqual(saved["buy"]["groups"], self.current_rules["buy"]["groups"])
            self.assertEqual(saved["sell"], self.current_rules["sell"])
            self.assertEqual(saved["indicators"], self.current_rules["indicators"])
            self.assertEqual(
                [patch["target_path"] for patch in result["applied_patches"]],
                ["buy.execution.base", "buy.execution.repeat"],
            )
            self.assertTrue(result["post_validation"]["ok"])
            self.assertEqual(result["post_validation"]["unexpected_changes"], [])

    def test_unapproved_buy_execution_extra_path_is_blocked_by_post_validation(self):
        def mutate(apply_preview):
            apply_preview["applied_rules_preview"].setdefault("buy", {}).setdefault("execution", {})["extra"] = {
                "enabled": True,
            }

        result = self._commit_mutated_apply_preview(
            {"buy.filters.ocr": "APPROVED"},
            mutate,
        )

        self._assert_post_validation_blocked(result, "buy.execution")

    def test_rsi_indicator_updated_and_other_sections_unchanged(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            rules_path = Path(temp_dir) / "rules.json"
            session_path = Path(temp_dir) / "approval_session.json"
            self._write_rules(rules_path, self.current_rules)
            apply_preview, gate, context = self._build_apply_and_gate(
                rules_path,
                session_path,
                {"indicators.rsi": "APPROVED"},
            )

            result = rule_apply_commit_service.commit_approved_rule_patch_to_rules(
                rules_path,
                apply_preview,
                gate,
                context,
            )
            saved = json.loads(rules_path.read_text(encoding="utf-8"))

            self.assertTrue(result["ok"], result)
            self.assertEqual(saved["indicators"]["rsi"], {"period": 14})
            self.assertEqual(saved["indicators"]["macd"], self.current_rules["indicators"]["macd"])
            self.assertEqual(saved.get("bar"), self.current_rules.get("bar"))
            self.assertEqual(saved["buy"], self.current_rules["buy"])
            self.assertEqual(saved["sell"], self.current_rules["sell"])
            self.assertEqual(
                [patch["target_path"] for patch in result["applied_patches"]],
                ["indicators.rsi"],
            )
            self.assertTrue(result["post_validation"]["ok"])
            self.assertEqual(result["post_validation"]["unexpected_changes"], [])

    def test_sell_profit_rate_signal_committed_via_set_signal_merge(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            rules_path = Path(temp_dir) / "rules.json"
            session_path = Path(temp_dir) / "approval_session.json"
            current_rules = deepcopy(self.current_rules)
            current_rules["sell"]["signals"]["profit_rate_sell"] = {
                "enabled": False,
                "profit_rate_percent": 1.0,
                "target_profit_rate": 2.0,
                "basis": "average_price",
                "description": "keep me",
            }
            current_rules["sell"]["signals"]["ui_condition_a"] = {"enabled": False, "groups": []}
            current_rules["sell"]["signals"]["ui_condition_b"] = {"enabled": False, "groups": []}
            current_rules["sell"]["signals"]["ui_condition_c"] = {"enabled": False, "groups": []}
            self._write_rules(rules_path, current_rules)
            original_ui_state = deepcopy(self.ui_state)
            self.ui_state["sell_ui"]["signal_conditions"]["condition_c"]["macd_check"] = False
            self.ui_state["sell_ui"]["profit_rate_sell"] = {
                "enabled": True,
                "profit_rate_percent": "3.0",
                "basis": "average_price",
            }
            try:
                apply_preview, gate, context = self._build_apply_and_gate(
                    rules_path,
                    session_path,
                    {"sell.signals.profit_rate_sell": "APPROVED"},
                    current_rules,
                )
            finally:
                self.ui_state = original_ui_state

            result = rule_apply_commit_service.commit_approved_rule_patch_to_rules(
                rules_path,
                apply_preview,
                gate,
                context,
            )
            saved = json.loads(rules_path.read_text(encoding="utf-8"))
            signals = saved["sell"]["signals"]

            self.assertTrue(result["ok"], result)
            self.assertEqual(
                [patch["target_path"] for patch in result["applied_patches"]],
                ["sell.signals.profit_rate_sell"],
            )
            self.assertEqual(signals["profit_rate_sell"]["enabled"], True)
            self.assertEqual(signals["profit_rate_sell"]["profit_rate_percent"], 3.0)
            self.assertEqual(signals["profit_rate_sell"]["target_profit_rate"], 2.0)
            self.assertEqual(signals["profit_rate_sell"]["description"], "keep me")
            self.assertEqual(signals["macd_sell"], current_rules["sell"]["signals"]["macd_sell"])
            self.assertEqual(signals["ui_condition_a"], current_rules["sell"]["signals"]["ui_condition_a"])
            self.assertEqual(signals["ui_condition_b"], current_rules["sell"]["signals"]["ui_condition_b"])
            self.assertEqual(signals["ui_condition_c"], current_rules["sell"]["signals"]["ui_condition_c"])
            self.assertTrue(result["post_validation"]["ok"])
            self.assertEqual(result["post_validation"]["unexpected_changes"], [])

    def test_sell_signal_added_disabled_and_macd_sell_unchanged(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            rules_path = Path(temp_dir) / "rules.json"
            session_path = Path(temp_dir) / "approval_session.json"
            self._write_rules(rules_path, self.current_rules)
            apply_preview, gate, context = self._build_apply_and_gate(
                rules_path,
                session_path,
                {"sell.signals.ui_preview_condition_c": "APPROVED"},
            )

            result = rule_apply_commit_service.commit_approved_rule_patch_to_rules(
                rules_path,
                apply_preview,
                gate,
                context,
            )
            saved = json.loads(rules_path.read_text(encoding="utf-8"))
            signals = saved["sell"]["signals"]

            self.assertTrue(result["ok"])
            self.assertIn("ui_condition_c", signals)
            self.assertFalse(signals["ui_condition_c"]["enabled"])
            self.assertEqual(signals["macd_sell"], self.current_rules["sell"]["signals"]["macd_sell"])

    def test_sell_condition_b_signal_added_disabled_and_macd_sell_unchanged(self):
        self.ui_state["sell_ui"]["signal_conditions"]["condition_b"] = {
            "bollinger_check": True,
            "bollinger_direction_combo": "상향",
            "bollinger_compare_combo": "이상",
            "bollinger_value_line": "0.1",
            "bollinger_logic_combo": "AND",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            rules_path = Path(temp_dir) / "rules.json"
            session_path = Path(temp_dir) / "approval_session.json"
            self._write_rules(rules_path, self.current_rules)
            apply_preview, gate, context = self._build_apply_and_gate(
                rules_path,
                session_path,
                {"sell.signals.ui_preview_condition_b": "APPROVED"},
            )

            result = rule_apply_commit_service.commit_approved_rule_patch_to_rules(
                rules_path,
                apply_preview,
                gate,
                context,
            )
            saved = json.loads(rules_path.read_text(encoding="utf-8"))
            signals = saved["sell"]["signals"]

            self.assertTrue(result["ok"])
            self.assertIn("ui_condition_b", signals)
            self.assertFalse(signals["ui_condition_b"]["enabled"])
            self.assertEqual(signals["macd_sell"], self.current_rules["sell"]["signals"]["macd_sell"])

    def test_buy_and_sell_allowed_changes_pass_deep_compare(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            rules_path = Path(temp_dir) / "rules.json"
            session_path = Path(temp_dir) / "approval_session.json"
            self._write_rules(rules_path, self.current_rules)
            apply_preview, gate, context = self._build_apply_and_gate(
                rules_path,
                session_path,
                {
                    "buy.filters.ocr": "APPROVED",
                    "sell.signals.ui_preview_condition_c": "APPROVED",
                },
            )

            result = rule_apply_commit_service.commit_approved_rule_patch_to_rules(
                rules_path,
                apply_preview,
                gate,
                context,
            )

            self.assertTrue(result["ok"])
            self.assertTrue(result["post_validation"]["ok"])
            self.assertEqual(result["post_validation"]["unexpected_changes"], [])

    def test_bar_buy_and_sell_allowed_changes_pass_deep_compare(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            rules_path = Path(temp_dir) / "rules.json"
            session_path = Path(temp_dir) / "approval_session.json"
            self._write_rules(rules_path, self.current_rules)
            apply_preview, gate, context = self._build_apply_and_gate(
                rules_path,
                session_path,
                {
                    "bar.bar_minutes": "APPROVED",
                    "buy.filters.ocr": "APPROVED",
                    "sell.signals.ui_preview_condition_c": "APPROVED",
                },
            )

            result = rule_apply_commit_service.commit_approved_rule_patch_to_rules(
                rules_path,
                apply_preview,
                gate,
                context,
            )
            saved = json.loads(rules_path.read_text(encoding="utf-8"))

            self.assertTrue(result["ok"], result)
            self.assertEqual(saved["bar"]["bar_minutes"], 5)
            self.assertEqual(saved["bar"].get("buy_delay_bar"), None)
            self.assertTrue(result["post_validation"]["ok"])
            self.assertEqual(result["post_validation"]["unexpected_changes"], [])

    def test_post_validation_failure_returns_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            rules_path = Path(temp_dir) / "rules.json"
            session_path = Path(temp_dir) / "approval_session.json"
            self._write_rules(rules_path, self.current_rules)
            apply_preview, gate, context = self._build_apply_and_gate(
                rules_path,
                session_path,
                {"sell.signals.ui_preview_condition_c": "APPROVED"},
            )
            apply_preview = deepcopy(apply_preview)
            apply_preview["applied_rules_preview"]["sell"]["signals"].pop("macd_sell", None)
            gate = self._bind_gate_to_apply_preview(gate, apply_preview)

            result = rule_apply_commit_service.commit_approved_rule_patch_to_rules(
                rules_path,
                apply_preview,
                gate,
                context,
            )

            self._assert_post_validation_blocked(result, "sell.signals.macd_sell")

    def test_deep_compare_detects_non_target_buy_group_change(self):
        def mutate(apply_preview):
            apply_preview["applied_rules_preview"]["buy"]["groups"][1]["enabled"] = True

        result = self._commit_mutated_apply_preview(
            {"buy.filters.ocr": "APPROVED"},
            mutate,
        )

        self._assert_post_validation_blocked(result, "buy.groups[1:]")

    def test_deep_compare_detects_buy_group0_metadata_change(self):
        def mutate(apply_preview):
            apply_preview["applied_rules_preview"]["buy"]["groups"][0]["name"] = "changed"

        result = self._commit_mutated_apply_preview(
            {"buy.filters.ocr": "APPROVED"},
            mutate,
        )

        self._assert_post_validation_blocked(result, "buy.groups[0]")

    def test_deep_compare_detects_existing_osc_turn_up_removed(self):
        def mutate(apply_preview):
            conditions = apply_preview["applied_rules_preview"]["buy"]["groups"][0]["conditions"]
            apply_preview["applied_rules_preview"]["buy"]["groups"][0]["conditions"] = [
                condition for condition in conditions if condition.get("operator") != "TURN_UP"
            ]

        result = self._commit_mutated_apply_preview(
            {"buy.filters.ocr": "APPROVED"},
            mutate,
        )

        self._assert_post_validation_blocked(result, "buy.groups[0].conditions")

    def test_deep_compare_detects_existing_buy_condition_modified(self):
        def mutate(apply_preview):
            apply_preview["applied_rules_preview"]["buy"]["groups"][0]["conditions"][0]["operator"] = "TURN_DOWN"

        result = self._commit_mutated_apply_preview(
            {"buy.filters.ocr": "APPROVED"},
            mutate,
        )

        self._assert_post_validation_blocked(result, "buy.groups[0].conditions")

    def test_deep_compare_detects_duplicate_buy_condition(self):
        def mutate(apply_preview):
            conditions = apply_preview["applied_rules_preview"]["buy"]["groups"][0]["conditions"]
            conditions.append(deepcopy(conditions[-1]))

        result = self._commit_mutated_apply_preview(
            {"buy.filters.ocr": "APPROVED"},
            mutate,
        )

        self._assert_post_validation_blocked(result, "buy.groups[0].conditions")

    def test_deep_compare_detects_existing_sell_macd_changed(self):
        def mutate(apply_preview):
            apply_preview["applied_rules_preview"]["sell"]["signals"]["macd_sell"]["enabled"] = False

        result = self._commit_mutated_apply_preview(
            {"sell.signals.ui_preview_condition_c": "APPROVED"},
            mutate,
        )

        self._assert_post_validation_blocked(result, "sell.signals.macd_sell")

    def test_deep_compare_detects_existing_sell_signal_deleted(self):
        def mutate(apply_preview):
            apply_preview["applied_rules_preview"]["sell"]["signals"].pop("macd_sell", None)

        result = self._commit_mutated_apply_preview(
            {"sell.signals.ui_preview_condition_c": "APPROVED"},
            mutate,
        )

        self._assert_post_validation_blocked(result, "sell.signals.macd_sell")

    def test_deep_compare_detects_existing_sell_signal_modified(self):
        rules = deepcopy(self.current_rules)
        rules["sell"]["signals"]["extra_sell"] = {"enabled": True, "groups": []}

        def mutate(apply_preview):
            apply_preview["applied_rules_preview"]["sell"]["signals"]["extra_sell"]["enabled"] = False

        result = self._commit_mutated_apply_preview(
            {"sell.signals.ui_preview_condition_c": "APPROVED"},
            mutate,
            rules,
        )

        self._assert_post_validation_blocked(result, "sell.signals.extra_sell")

    def test_deep_compare_detects_unapproved_new_sell_signal(self):
        def mutate(apply_preview):
            apply_preview["applied_rules_preview"]["sell"]["signals"]["other_ui_signal"] = {
                "enabled": False,
                "groups": [],
            }

        result = self._commit_mutated_apply_preview(
            {"sell.signals.ui_preview_condition_c": "APPROVED"},
            mutate,
        )

        self._assert_post_validation_blocked(result, "sell.signals.other_ui_signal")

    def test_deep_compare_detects_unrelated_namespace_changes(self):
        rules = deepcopy(self.current_rules)
        rules["indicators"] = {"osc": {"period": 10}}
        rules["order_policy"] = {"max_qty": 10}
        rules["cancel_policy"] = {"enabled": True}
        rules["safety"] = {"enabled": True}

        cases = [
            ("bar", lambda preview: preview["applied_rules_preview"]["bar"].update({"bar_minutes": 30})),
            ("indicators", lambda preview: preview["applied_rules_preview"]["indicators"]["osc"].update({"period": 20})),
            ("order_policy", lambda preview: preview["applied_rules_preview"]["order_policy"].update({"max_qty": 20})),
            ("cancel_policy", lambda preview: preview["applied_rules_preview"]["cancel_policy"].update({"enabled": False})),
            ("safety", lambda preview: preview["applied_rules_preview"]["safety"].update({"enabled": False})),
        ]
        for expected_path, mutate in cases:
            with self.subTest(expected_path=expected_path):
                result = self._commit_mutated_apply_preview(
                    {"buy.filters.ocr": "APPROVED"},
                    mutate,
                    rules,
                )
                self.assertFalse(result["ok"])
                self.assertTrue(
                    any(
                        change.get("path", "").startswith(expected_path)
                        for change in result["post_validation"]["unexpected_changes"]
                    ),
                    result["post_validation"]["unexpected_changes"],
                )

    def test_deep_compare_detects_unknown_top_level_namespace_added(self):
        def mutate(apply_preview):
            apply_preview["applied_rules_preview"]["unknown_namespace"] = {"value": True}

        result = self._commit_mutated_apply_preview(
            {"buy.filters.ocr": "APPROVED"},
            mutate,
        )

        self._assert_post_validation_blocked(result, "unknown_namespace")

    def test_deep_compare_detects_final_diff_buy_condition_missing(self):
        def mutate(apply_preview):
            conditions = apply_preview["applied_rules_preview"]["buy"]["filters"]["ocr"]["conditions"]
            apply_preview["applied_rules_preview"]["buy"]["filters"]["ocr"]["conditions"] = [
                condition
                for condition in conditions
                if not (
                    condition.get("target") == "OSC"
                    and condition.get("operator") == "<="
                    and condition.get("value") == -91.0
                )
            ]

        result = self._commit_mutated_apply_preview(
            {
                "buy.filters.ocr": "APPROVED",
                "sell.signals.ui_preview_condition_c": "APPROVED",
            },
            mutate,
        )

        self._assert_post_validation_blocked(result, "buy.filters.ocr")

    def test_deep_compare_detects_final_diff_buy_rsi_filter_changed(self):
        def mutate(apply_preview):
            apply_preview["applied_rules_preview"]["buy"]["filters"]["rsi"]["conditions"][0]["threshold"] = 46.0

        result = self._commit_mutated_apply_preview(
            {"buy.filters.rsi": "APPROVED"},
            mutate,
        )

        self._assert_post_validation_blocked(result, "buy.filters.rsi")

    def test_deep_compare_detects_final_diff_buy_composite_filter_changed(self):
        def mutate(apply_preview):
            apply_preview["applied_rules_preview"]["buy"]["filters"]["composite"]["logic"] = "AND"

        original_ui_state = deepcopy(self.ui_state)
        self._enable_composite_ui_state()
        try:
            result = self._commit_mutated_apply_preview(
                {"buy.filters.composite": "APPROVED"},
                mutate,
            )
        finally:
            self.ui_state = original_ui_state

        self._assert_post_validation_blocked(result, "buy.filters.composite")

    def test_approval_session_file_is_not_modified(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            rules_path = Path(temp_dir) / "rules.json"
            session_path = Path(temp_dir) / "approval_session.json"
            self._write_rules(rules_path, self.current_rules)
            apply_preview, gate, context = self._build_apply_and_gate(
                rules_path,
                session_path,
                {"buy.filters.ocr": "APPROVED"},
            )
            session_before = self._file_sha256(session_path)

            result = rule_apply_commit_service.commit_approved_rule_patch_to_rules(
                rules_path,
                apply_preview,
                gate,
                context,
            )

            self.assertTrue(result["ok"])
            self.assertEqual(session_before, self._file_sha256(session_path))

    def test_runtime_order_queue_and_project_rules_are_not_modified(self):
        rules_before = self._rules_json_hash()
        order_queue_before = self._runtime_order_queue_hash()
        with tempfile.TemporaryDirectory() as temp_dir:
            rules_path = Path(temp_dir) / "rules.json"
            session_path = Path(temp_dir) / "approval_session.json"
            self._write_rules(rules_path, self.current_rules)
            apply_preview, gate, context = self._build_apply_and_gate(
                rules_path,
                session_path,
                {"buy.filters.ocr": "APPROVED"},
            )

            result = rule_apply_commit_service.commit_approved_rule_patch_to_rules(
                rules_path,
                apply_preview,
                gate,
                context,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(rules_before, self._rules_json_hash())
        self.assertEqual(order_queue_before, self._runtime_order_queue_hash())

    def test_inputs_are_not_mutated(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            rules_path = Path(temp_dir) / "rules.json"
            session_path = Path(temp_dir) / "approval_session.json"
            self._write_rules(rules_path, self.current_rules)
            apply_preview, gate, context = self._build_apply_and_gate(
                rules_path,
                session_path,
                {"buy.filters.ocr": "APPROVED"},
            )
            original_apply = deepcopy(apply_preview)
            original_gate = deepcopy(gate)

            rule_apply_commit_service.commit_approved_rule_patch_to_rules(
                rules_path,
                apply_preview,
                gate,
                context,
            )

            self.assertEqual(apply_preview, original_apply)
            self.assertEqual(gate, original_gate)

    def _rollback_rules(self):
        changed_rules = deepcopy(self.current_rules)
        changed_rules["buy"]["groups"][0]["conditions"].append({
            "target": "OSC",
            "operator": "<=",
            "value": -91.0,
        })
        backup_rules = deepcopy(self.current_rules)
        return changed_rules, backup_rules

    def _write_json_value(self, path, value):
        Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _rollback_context(self, rules_path, expected_sha256):
        return {
            "allowed_rules_path": str(Path(rules_path).resolve()),
            "expected_current_file_sha256": expected_sha256,
        }

    def test_restore_rules_from_backup_success(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            rules_path = Path(temp_dir) / "rules.json"
            backup_path = Path(temp_dir) / "backup_rules.json"
            changed_rules, backup_rules = self._rollback_rules()
            self._write_rules(rules_path, changed_rules)
            self._write_rules(backup_path, backup_rules)
            before = self._file_sha256(rules_path)

            result = rule_apply_commit_service.restore_rules_from_backup(
                rules_path,
                backup_path,
                self._rollback_context(rules_path, before),
            )

            restored = json.loads(rules_path.read_text(encoding="utf-8"))
            safety_backup = json.loads(Path(result["rollback_safety_backup_path"]).read_text(encoding="utf-8"))
            self.assertTrue(result["ok"])
            self.assertEqual("RULE_ROLLBACK", result["stage"])
            self.assertTrue(result["rollback_completed"])
            self.assertEqual(backup_rules, restored)
            self.assertEqual(changed_rules, safety_backup)
            self.assertEqual(result["backup_rules_hash"], result["post_rollback_rules_hash"])
            self.assertTrue(Path(result["rollback_safety_backup_path"]).exists())
            self.assertEqual(
                Path(result["rollback_safety_backup_path"]).parent,
                Path(temp_dir) / "backups" / "rollback_safety",
            )

    def test_restore_rules_path_required(self):
        result = rule_apply_commit_service.restore_rules_from_backup("", "backup.json", {})

        self.assertFalse(result["ok"])
        self.assertEqual("RULE_ROLLBACK_BLOCKED", result["stage"])
        self.assertIn("rules_path is required", result["blocked_reasons"])

    def test_restore_backup_path_required(self):
        result = rule_apply_commit_service.restore_rules_from_backup("rules.json", "", {})

        self.assertFalse(result["ok"])
        self.assertIn("backup_path is required", result["blocked_reasons"])

    def test_restore_allowed_rules_path_missing_is_blocked(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            rules_path = Path(temp_dir) / "rules.json"
            backup_path = Path(temp_dir) / "backup_rules.json"
            changed_rules, backup_rules = self._rollback_rules()
            self._write_rules(rules_path, changed_rules)
            self._write_rules(backup_path, backup_rules)
            before = self._file_sha256(rules_path)

            result = rule_apply_commit_service.restore_rules_from_backup(
                rules_path,
                backup_path,
                {"expected_current_file_sha256": before},
            )

        self.assertFalse(result["ok"])
        self.assertIn("allowed_rules_path is required", result["blocked_reasons"])

    def test_restore_disallowed_rules_path_is_blocked(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            allowed_path = Path(temp_dir) / "allowed" / "rules.json"
            rules_path = Path(temp_dir) / "other" / "rules.json"
            backup_path = Path(temp_dir) / "backup_rules.json"
            allowed_path.parent.mkdir()
            rules_path.parent.mkdir()
            changed_rules, backup_rules = self._rollback_rules()
            self._write_rules(allowed_path, changed_rules)
            self._write_rules(rules_path, changed_rules)
            self._write_rules(backup_path, backup_rules)
            before = self._file_sha256(rules_path)

            result = rule_apply_commit_service.restore_rules_from_backup(
                rules_path,
                backup_path,
                {
                    "allowed_rules_path": str(allowed_path.resolve()),
                    "expected_current_file_sha256": before,
                },
            )

        self.assertFalse(result["ok"])
        self.assertIn("rules path is not allowed", result["blocked_reasons"])

    def test_restore_relative_traversal_rules_path_is_blocked(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            allowed_path = root / "allowed" / "rules.json"
            other_path = root / "other" / "rules.json"
            backup_path = root / "backup_rules.json"
            allowed_path.parent.mkdir()
            other_path.parent.mkdir()
            changed_rules, backup_rules = self._rollback_rules()
            self._write_rules(allowed_path, changed_rules)
            self._write_rules(other_path, changed_rules)
            self._write_rules(backup_path, backup_rules)
            traversal_path = root / "allowed" / ".." / "other" / "rules.json"
            before = self._file_sha256(other_path)

            result = rule_apply_commit_service.restore_rules_from_backup(
                traversal_path,
                backup_path,
                {
                    "allowed_rules_path": str(allowed_path.resolve()),
                    "expected_current_file_sha256": before,
                },
            )

        self.assertFalse(result["ok"])
        self.assertIn("rules path is not allowed", result["blocked_reasons"])

    def test_restore_rules_file_name_must_be_rules_json(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            rules_path = Path(temp_dir) / "not_rules.json"
            backup_path = Path(temp_dir) / "backup_rules.json"
            changed_rules, backup_rules = self._rollback_rules()
            self._write_rules(rules_path, changed_rules)
            self._write_rules(backup_path, backup_rules)
            before = self._file_sha256(rules_path)

            result = rule_apply_commit_service.restore_rules_from_backup(
                rules_path,
                backup_path,
                {
                    "allowed_rules_path": str(rules_path.resolve()),
                    "expected_current_file_sha256": before,
                },
            )

        self.assertFalse(result["ok"])
        self.assertIn("rules file name must be rules.json", result["blocked_reasons"])

    def test_restore_current_rules_missing_is_blocked(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            rules_path = Path(temp_dir) / "missing" / "rules.json"
            backup_path = Path(temp_dir) / "backup_rules.json"
            self._write_rules(backup_path, self.current_rules)

            result = rule_apply_commit_service.restore_rules_from_backup(
                rules_path,
                backup_path,
                {"allowed_rules_path": str(rules_path.resolve())},
            )

        self.assertFalse(result["ok"])
        self.assertIn("rules file does not exist", result["blocked_reasons"])

    def test_restore_backup_missing_is_blocked(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            rules_path = Path(temp_dir) / "rules.json"
            backup_path = Path(temp_dir) / "missing_backup.json"
            self._write_rules(rules_path, self.current_rules)

            result = rule_apply_commit_service.restore_rules_from_backup(
                rules_path,
                backup_path,
                {"allowed_rules_path": str(rules_path.resolve())},
            )

        self.assertFalse(result["ok"])
        self.assertIn("backup file does not exist", result["blocked_reasons"])

    def test_restore_expected_current_file_sha256_required(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            rules_path = Path(temp_dir) / "rules.json"
            backup_path = Path(temp_dir) / "backup_rules.json"
            changed_rules, backup_rules = self._rollback_rules()
            self._write_rules(rules_path, changed_rules)
            self._write_rules(backup_path, backup_rules)

            result = rule_apply_commit_service.restore_rules_from_backup(
                rules_path,
                backup_path,
                {"allowed_rules_path": str(rules_path.resolve())},
            )

        self.assertFalse(result["ok"])
        self.assertIn("expected_current_file_sha256 is required", result["blocked_reasons"])

    def test_restore_current_file_hash_mismatch_is_blocked(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            rules_path = Path(temp_dir) / "rules.json"
            backup_path = Path(temp_dir) / "backup_rules.json"
            changed_rules, backup_rules = self._rollback_rules()
            self._write_rules(rules_path, changed_rules)
            self._write_rules(backup_path, backup_rules)
            before = self._file_sha256(rules_path)
            self._write_rules(rules_path, {"changed": True})

            result = rule_apply_commit_service.restore_rules_from_backup(
                rules_path,
                backup_path,
                self._rollback_context(rules_path, before),
            )

        self.assertFalse(result["ok"])
        self.assertIn("expected current file SHA256 mismatch", result["blocked_reasons"])

    def test_restore_corrupted_backup_is_blocked(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            rules_path = Path(temp_dir) / "rules.json"
            backup_path = Path(temp_dir) / "backup_rules.json"
            self._write_rules(rules_path, self.current_rules)
            backup_path.write_text("{bad", encoding="utf-8")
            before = self._file_sha256(rules_path)

            result = rule_apply_commit_service.restore_rules_from_backup(
                rules_path,
                backup_path,
                self._rollback_context(rules_path, before),
            )

        self.assertFalse(result["ok"])
        self.assertTrue(result["blocked_reasons"][0].startswith("failed to load backup rules"))

    def test_restore_backup_root_non_dict_is_blocked(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            rules_path = Path(temp_dir) / "rules.json"
            backup_path = Path(temp_dir) / "backup_rules.json"
            self._write_rules(rules_path, self.current_rules)
            self._write_json_value(backup_path, [])
            before = self._file_sha256(rules_path)

            result = rule_apply_commit_service.restore_rules_from_backup(
                rules_path,
                backup_path,
                self._rollback_context(rules_path, before),
            )

        self.assertFalse(result["ok"])
        self.assertIn("rules JSON root must be a dict", result["blocked_reasons"][0])

    def test_restore_current_rules_corrupted_is_blocked(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            rules_path = Path(temp_dir) / "rules.json"
            backup_path = Path(temp_dir) / "backup_rules.json"
            rules_path.write_text("{bad", encoding="utf-8")
            self._write_rules(backup_path, self.current_rules)
            before = self._file_sha256(rules_path)

            result = rule_apply_commit_service.restore_rules_from_backup(
                rules_path,
                backup_path,
                self._rollback_context(rules_path, before),
            )

        self.assertFalse(result["ok"])
        self.assertTrue(result["blocked_reasons"][0].startswith("failed to read rules JSON"))

    def test_restore_safety_backup_failure_blocks_rollback(self):
        original_create = rule_apply_commit_service._create_rollback_safety_backup

        def failing_safety_backup(*args, **kwargs):
            raise OSError("safety denied")

        with tempfile.TemporaryDirectory() as temp_dir:
            rules_path = Path(temp_dir) / "rules.json"
            backup_path = Path(temp_dir) / "backup_rules.json"
            changed_rules, backup_rules = self._rollback_rules()
            self._write_rules(rules_path, changed_rules)
            self._write_rules(backup_path, backup_rules)
            before = self._file_sha256(rules_path)
            rule_apply_commit_service._create_rollback_safety_backup = failing_safety_backup
            try:
                result = rule_apply_commit_service.restore_rules_from_backup(
                    rules_path,
                    backup_path,
                    self._rollback_context(rules_path, before),
                )
            finally:
                rule_apply_commit_service._create_rollback_safety_backup = original_create

            saved = json.loads(rules_path.read_text(encoding="utf-8"))

        self.assertFalse(result["ok"])
        self.assertIn("failed to create rollback safety backup: safety denied", result["blocked_reasons"])
        self.assertEqual(changed_rules, saved)

    def test_restore_temp_file_is_removed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            rules_path = Path(temp_dir) / "rules.json"
            backup_path = Path(temp_dir) / "backup_rules.json"
            changed_rules, backup_rules = self._rollback_rules()
            self._write_rules(rules_path, changed_rules)
            self._write_rules(backup_path, backup_rules)
            before = self._file_sha256(rules_path)

            result = rule_apply_commit_service.restore_rules_from_backup(
                rules_path,
                backup_path,
                self._rollback_context(rules_path, before),
            )

            tmp_path = rules_path.with_name(".rules.json.tmp")

        self.assertTrue(result["ok"])
        self.assertFalse(tmp_path.exists())

    def test_restore_rules_path_and_backup_path_same_is_blocked(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            rules_path = Path(temp_dir) / "rules.json"
            self._write_rules(rules_path, self.current_rules)

            result = rule_apply_commit_service.restore_rules_from_backup(
                rules_path,
                rules_path,
                {"allowed_rules_path": str(rules_path.resolve())},
            )

        self.assertFalse(result["ok"])
        self.assertIn("rules_path and backup_path must be different", result["blocked_reasons"])

    def test_restore_does_not_modify_actual_project_rules_or_create_report(self):
        rules_before = self._rules_json_hash()
        order_queue_before = self._runtime_order_queue_hash()
        actual_report_dir = Path(__file__).resolve().parents[1] / "reports" / "rule_commits"
        actual_report_dir_existed = actual_report_dir.exists()
        with tempfile.TemporaryDirectory() as temp_dir:
            rules_path = Path(temp_dir) / "rules.json"
            backup_path = Path(temp_dir) / "backup_rules.json"
            changed_rules, backup_rules = self._rollback_rules()
            self._write_rules(rules_path, changed_rules)
            self._write_rules(backup_path, backup_rules)
            before = self._file_sha256(rules_path)

            result = rule_apply_commit_service.restore_rules_from_backup(
                rules_path,
                backup_path,
                self._rollback_context(rules_path, before),
            )

        self.assertTrue(result["ok"])
        self.assertEqual(rules_before, self._rules_json_hash())
        self.assertEqual(order_queue_before, self._runtime_order_queue_hash())
        self.assertEqual(actual_report_dir_existed, actual_report_dir.exists())

    def test_no_engine_sendorder_or_chejan_references(self):
        source = Path(rule_apply_commit_service.__file__).read_text(encoding="utf-8")

        self.assertNotIn("SendOrder", source)
        self.assertNotIn("Chejan", source)
        self.assertNotIn("kiwoom", source.lower())
        self.assertNotIn("QPushButton", source)
        self.assertNotIn("write_rule_commit_report", source)


if __name__ == "__main__":
    unittest.main()


