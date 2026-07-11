from __future__ import annotations

from copy import deepcopy
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import hashlib
import json
import tempfile
import unittest

import rule_approval_session_file_service


def _load_mapper_module():
    project_root = Path(__file__).resolve().parents[1]
    mapper_path = next((project_root / "routines").glob("*/routine_rule_mapper.py"))
    spec = spec_from_file_location("routine_rule_mapper_under_test", mapper_path)
    module = module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class IndicatorFollowRuleMapperPreviewTest(unittest.TestCase):
    def setUp(self):
        self.mapper = _load_mapper_module()
        self.ui_state = {
            "basic": {
                "basic_signal_interval_combo": "5",
            },
            "buy_ui": {
                "signal_filter": {
                    "buy_ocr_bar_line": "0",
                    "buy_ocr_compare_combo": "\uc774\ud558",
                    "buy_ocr_sign_combo": "-",
                    "buy_ocr_turn_combo": "\uc0c1\uc2b9",
                    "buy_ocr_value_line": "91",
                    "buy_rsi_compare_combo": "\uc774\ud558",
                    "buy_rsi_period_line": "14",
                    "buy_rsi_value_line": "45",
                },
            },
            "sell_ui": {
                "signal_conditions": {
                    "condition_c": {
                        "macd_check": True,
                        "macd_compare_combo": "\uc774\ud558",
                        "macd_kind_combo": "MACD\uc120",
                        "macd_sign_combo": "-",
                        "macd_value_line": "1.0",
                    },
                },
            },
        }
        self.current_rules = {
            "bar": {
                "bar_minutes": 1,
                "buy_delay_bar": 2,
                "sell_delay_bar": 3,
                "delay_bar_note": "legacy bar note must not stay under preview.bar",
            },
            "buy": {
                "enabled": True,
                "groups_logic": "OR",
                "groups": [
                    {
                        "enabled": True,
                        "name": "buy_group_1",
                        "conditions_logic": "AND",
                        "conditions": [
                            {
                                "enabled": True,
                                "not": False,
                                "target": "OSC",
                                "operator": "TURN_UP",
                            }
                        ],
                    },
                    {"enabled": False, "name": "buy_group_2", "conditions_logic": "AND", "conditions": []},
                    {"enabled": False, "name": "buy_group_3", "conditions_logic": "AND", "conditions": []},
                    {"enabled": False, "name": "buy_group_4", "conditions_logic": "AND", "conditions": []},
                    {"enabled": False, "name": "buy_group_5", "conditions_logic": "AND", "conditions": []},
                ],
            },
            "sell": {
                "enabled": True,
                "signal_logic": "OR",
                "signals": {
                    "macd_sell": {
                        "enabled": True,
                        "delay_bar": 1,
                        "groups_logic": "OR",
                        "groups": [
                            {
                                "enabled": True,
                                "name": "sell_macd_osc_turn_down",
                                "conditions_logic": "AND",
                                "conditions": [
                                    {
                                        "enabled": True,
                                        "not": False,
                                        "target": "OSC",
                                        "operator": "TURN_DOWN",
                                    }
                                ],
                            }
                        ],
                    },
                    "profit_rate_sell": {
                        "enabled": False,
                    },
                },
            },
            "indicators": {
                "macd": {"enabled": True},
                "rsi": {"period": 10},
            },
        }

    def _build_preview(self):
        original_rules = deepcopy(self.current_rules)
        result = self.mapper.build_engine_rules_preview_from_ui_state(
            deepcopy(self.ui_state),
            self.current_rules,
        )
        self.assertEqual(self.current_rules, original_rules)
        return result

    def _rules_json_hash(self):
        project_root = Path(__file__).resolve().parents[1]
        rules_path = next((project_root / "routines").glob("*/rules.json"))
        return hashlib.sha256(rules_path.read_bytes()).hexdigest().upper()

    def _rules_json_path(self):
        project_root = Path(__file__).resolve().parents[1]
        return next((project_root / "routines").glob("*/rules.json"))

    def test_mapper_returns_preview_without_mutating_rules(self):
        result = self._build_preview()

        self.assertIn("preview_rules", result)
        self.assertIsInstance(result["preview_rules"], dict)

    def test_mapped_paths_match_expected_preview_paths(self):
        result = self._build_preview()

        self.assertEqual(
            result["mapped_paths"],
            [
                "bar.bar_minutes",
                "buy.groups[0].conditions",
                "indicators.rsi",
                "sell.signals.ui_preview_condition_c_macd_sell",
            ],
        )

    def test_warnings_are_ascii_strings(self):
        result = self._build_preview()

        self.assertTrue(result["warnings"])
        self.assertTrue(all(isinstance(warning, str) for warning in result["warnings"]))
        self.assertTrue(all(warning.isascii() for warning in result["warnings"]))

    def test_preview_bar_contains_only_bar_minutes(self):
        result = self._build_preview()
        preview_bar = result["preview_rules"]["bar"]

        self.assertEqual(preview_bar, {"bar_minutes": 5})

    def test_preview_namespace_exists(self):
        result = self._build_preview()
        namespace = result["preview_rules"]["indicator_follow_rule_preview"]

        self.assertEqual(namespace["mode"], "merge_add_candidate")
        self.assertIn("bar", namespace["candidates"])
        self.assertIn("buy", namespace["candidates"])
        self.assertIn("indicators", namespace["candidates"])
        self.assertIn("sell", namespace["candidates"])

    def test_buy_groups_are_not_replaced(self):
        result = self._build_preview()

        self.assertEqual(
            result["preview_rules"]["buy"]["groups"],
            self.current_rules["buy"]["groups"],
        )

    def test_buy_merge_candidate_records_existing_turn_up_and_threshold_add(self):
        result = self._build_preview()
        candidate = result["preview_rules"]["indicator_follow_rule_preview"]["candidates"]["buy"]

        self.assertEqual(candidate["merge_into"], "buy.groups[0].conditions")
        self.assertEqual(
            candidate["skip_existing"],
            [
                {
                    "target": "OSC",
                    "operator": "TURN_UP",
                    "reason": "already exists in current buy.groups[0]",
                }
            ],
        )
        self.assertEqual(len(candidate["add_conditions"]), 1)
        self.assertEqual(candidate["add_conditions"][0]["target"], "OSC")
        self.assertEqual(candidate["add_conditions"][0]["operator"], "<=")
        self.assertEqual(candidate["add_conditions"][0]["value"], -91.0)

    def test_rsi_indicator_candidate_uses_existing_indicator_shape(self):
        result = self._build_preview()
        candidate = result["preview_rules"]["indicator_follow_rule_preview"]["candidates"]["indicators"]["rsi"]

        self.assertEqual(candidate["path"], "indicators.rsi")
        self.assertEqual(candidate["value"], {"period": 14})
        self.assertEqual(
            candidate["ui_filter"],
            {
                "period": 14,
                "operator": "<=",
                "threshold": 45.0,
            },
        )
        self.assertEqual(result["preview_rules"]["indicators"]["rsi"], {"period": 14})

    def test_rsi_empty_value_does_not_create_candidate(self):
        state = deepcopy(self.ui_state)
        state["buy_ui"]["signal_filter"]["buy_rsi_value_line"] = ""

        result = self.mapper.build_engine_rules_preview_from_ui_state(
            state,
            deepcopy(self.current_rules),
        )
        candidates = result["preview_rules"]["indicator_follow_rule_preview"]["candidates"]

        self.assertNotIn("indicators", candidates)
        self.assertNotIn("indicators.rsi", self.mapper.build_rule_approval_session(result)["decisions"])

    def test_rsi_same_indicator_has_no_commit_diff(self):
        current_rules = deepcopy(self.current_rules)
        current_rules["indicators"]["rsi"] = {"period": 14}
        preview = self.mapper.build_engine_rules_preview_from_ui_state(
            deepcopy(self.ui_state),
            current_rules,
        )
        approval = self.mapper.evaluate_rule_candidate_approval(
            preview,
            {"indicators.rsi": "APPROVED"},
        )

        patch_preview = self.mapper.build_approved_rule_patch_preview(current_rules, preview, approval)
        session = self.mapper.build_rule_approval_session(
            preview,
            {"indicators.rsi": "APPROVED"},
        )
        fingerprint = self.mapper.build_rule_approval_session_fingerprint(current_rules, preview)
        session["fingerprint"] = fingerprint["fingerprint"]
        session["fingerprint_detail"] = fingerprint
        commit_preview = self.mapper.build_rule_commit_preview(
            current_rules,
            preview,
            session,
            {"approval_session_dirty": False},
        )

        self.assertEqual(patch_preview["patches"], [])
        self.assertFalse(commit_preview["commit_allowed"])
        self.assertEqual(commit_preview["final_diff"], [])
        self.assertIn("approval session has no approved patches", commit_preview["blocked_reasons"])

    def test_buy_ocr_empty_value_does_not_create_buy_candidate(self):
        state = deepcopy(self.ui_state)
        state["buy_ui"]["signal_filter"]["buy_ocr_value_line"] = ""

        result = self.mapper.build_engine_rules_preview_from_ui_state(
            state,
            deepcopy(self.current_rules),
        )
        candidates = result["preview_rules"]["indicator_follow_rule_preview"]["candidates"]

        self.assertNotIn("buy", candidates)
        self.assertNotIn("buy.groups[0].conditions", self.mapper.build_rule_approval_session(result)["decisions"])

    def test_buy_ma_price_compare_and_bollinger_candidates_use_buy_conditions(self):
        state = deepcopy(self.ui_state)
        state["buy_ui"]["signal_filter"] = {
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
        state["buy_ui"]["price_compare"] = {
            "enabled": True,
            "type_combo": "\uac00\uaca9\ube44\uad50",
            "left_combo": "\ud604\uc7ac\uac00",
            "right_combo": "\ud3c9\ub2e8\uac00",
            "ratio_line": "0.15",
            "compare_combo": "\uc774\uc0c1",
        }

        result = self.mapper.build_engine_rules_preview_from_ui_state(
            deepcopy(state),
            deepcopy(self.current_rules),
        )

        candidate = result["preview_rules"]["indicator_follow_rule_preview"]["candidates"]["buy"]
        filters = result["preview_rules"]["indicator_follow_rule_preview"]["candidates"]["filters"]
        ma_filter = filters["moving_average"]
        price_compare_filter = filters["price_compare"]
        self.assertEqual(candidate["merge_into"], "buy.groups[0].conditions")
        self.assertEqual(ma_filter["path"], "buy.filters.moving_average")
        self.assertEqual(ma_filter["value"], {
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
        })
        self.assertEqual(price_compare_filter["path"], "buy.filters.price_compare")
        self.assertEqual(price_compare_filter["value"], {
            "enabled": True,
            "conditions_logic": "OR",
            "conditions": [{
                "enabled": True,
                "not": False,
                "target": "CLOSE",
                "operator": ">=",
                "compare_target": "AVG_PRICE",
                "description": "UI preview: BUY price compare filter condition",
                "value": 0.15,
            }],
        })
        self.assertEqual(result["preview_rules"]["buy"]["filters"]["moving_average"], ma_filter["value"])
        self.assertEqual(result["preview_rules"]["buy"]["filters"]["price_compare"], price_compare_filter["value"])
        self.assertEqual(len(candidate["add_conditions"]), 1)
        self.assertEqual(candidate["add_conditions"][0], {
            "enabled": True,
            "not": False,
            "target": "CLOSE",
            "operator": ">=",
            "value": -0.1,
            "description": "UI preview: buy Bollinger threshold condition",
        })

    def test_actual_buy_price_compare_gui_fields_create_price_compare_filter(self):
        state = deepcopy(self.ui_state)
        state["buy_ui"]["signal_filter"]["buy_ocr_value_line"] = ""
        state["buy_ui"]["price_compare"] = {
            "check": True,
            "condition_combo": "=<",
            "mode_combo": "\ud68c\ucc28\uae30\uc900",
            "above_condition_combo": ">",
            "above_mode_combo": "\ud68c\ucc28\uae30\uc900",
        }

        result = self.mapper.build_engine_rules_preview_from_ui_state(
            deepcopy(state),
            deepcopy(self.current_rules),
        )

        candidates = result["preview_rules"]["indicator_follow_rule_preview"]["candidates"]
        price_compare_filter = candidates["filters"]["price_compare"]
        self.assertNotIn("buy", candidates)
        self.assertEqual(price_compare_filter["path"], "buy.filters.price_compare")
        self.assertEqual(price_compare_filter["value"], {
            "enabled": True,
            "conditions_logic": "OR",
            "conditions": [
                {
                    "enabled": True,
                    "not": False,
                    "target": "AVG_PRICE",
                    "operator": "<=",
                    "compare_target": "ORDER_PRICE",
                    "description": "UI preview: BUY price compare below-branch filter condition",
                },
                {
                    "enabled": True,
                    "not": False,
                    "target": "AVG_PRICE",
                    "operator": ">",
                    "compare_target": "ORDER_PRICE",
                    "description": "UI preview: BUY price compare above-branch filter condition",
                },
            ],
        })

    def test_buy_ma_price_compare_and_bollinger_disabled_do_not_create_candidate(self):
        state = deepcopy(self.ui_state)
        state["buy_ui"]["signal_filter"] = {
            "buy_ocr_value_line": "",
            "buy_rsi_value_line": "",
            "buy_ma_enabled": False,
            "buy_ma_value_line": "60",
            "buy_bollinger_enabled": False,
            "buy_bollinger_value_line": "0.1",
        }
        state["buy_ui"]["price_compare"] = {
            "enabled": False,
            "type_combo": "\uac00\uaca9\ube44\uad50",
            "left_combo": "\ud604\uc7ac\uac00",
            "right_combo": "\ud3c9\ub2e8\uac00",
            "ratio_line": "0.15",
            "compare_combo": "\uc774\uc0c1",
        }

        result = self.mapper.build_engine_rules_preview_from_ui_state(
            deepcopy(state),
            deepcopy(self.current_rules),
        )

        self.assertNotIn("buy", result["preview_rules"]["indicator_follow_rule_preview"]["candidates"])

    def test_buy_ma_same_filter_has_no_commit_diff(self):
        state = deepcopy(self.ui_state)
        state["buy_ui"]["signal_filter"] = {
            "buy_ocr_value_line": "",
            "buy_rsi_value_line": "",
            "buy_ma_enabled": True,
            "buy_ma_value_line": "60",
            "buy_ma_direction_combo": "\uc0c1\ud5a5",
            "buy_ma_compare_combo": "\ub3cc\ud30c",
        }
        current_rules = deepcopy(self.current_rules)
        current_rules.setdefault("buy", {}).setdefault("filters", {})["moving_average"] = {
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
        }
        preview = self.mapper.build_engine_rules_preview_from_ui_state(state, current_rules)
        approval = self.mapper.evaluate_rule_candidate_approval(
            preview,
            {"buy.filters.moving_average": "APPROVED"},
        )

        patch_preview = self.mapper.build_approved_rule_patch_preview(current_rules, preview, approval)
        session = self.mapper.build_rule_approval_session(
            preview,
            {"buy.filters.moving_average": "APPROVED"},
        )
        fingerprint = self.mapper.build_rule_approval_session_fingerprint(current_rules, preview)
        session["fingerprint"] = fingerprint["fingerprint"]
        session["fingerprint_detail"] = fingerprint
        commit_preview = self.mapper.build_rule_commit_preview(
            current_rules,
            preview,
            session,
            {"approval_session_dirty": False},
        )

        self.assertEqual(patch_preview["patches"], [])
        self.assertFalse(commit_preview["commit_allowed"])
        self.assertEqual(commit_preview["final_diff"], [])
        self.assertIn("approval session has no approved patches", commit_preview["blocked_reasons"])

    def test_buy_ocr_same_threshold_has_no_commit_diff(self):
        current_rules = deepcopy(self.current_rules)
        current_rules["buy"]["groups"][0]["conditions"].append({
            "enabled": True,
            "not": False,
            "target": "OSC",
            "operator": "<=",
            "value": -91.0,
        })
        preview = self.mapper.build_engine_rules_preview_from_ui_state(
            deepcopy(self.ui_state),
            current_rules,
        )
        approval = self.mapper.evaluate_rule_candidate_approval(
            preview,
            {"buy.groups[0].conditions": "APPROVED"},
        )

        patch_preview = self.mapper.build_approved_rule_patch_preview(current_rules, preview, approval)
        session = self.mapper.build_rule_approval_session(
            preview,
            {"buy.groups[0].conditions": "APPROVED"},
        )
        fingerprint = self.mapper.build_rule_approval_session_fingerprint(current_rules, preview)
        session["fingerprint"] = fingerprint["fingerprint"]
        session["fingerprint_detail"] = fingerprint
        commit_preview = self.mapper.build_rule_commit_preview(
            current_rules,
            preview,
            session,
            {"approval_session_dirty": False},
        )

        self.assertEqual(patch_preview["patches"], [])
        self.assertFalse(commit_preview["commit_allowed"])
        self.assertEqual(commit_preview["final_diff"], [])
        self.assertIn("approval session has no approved patches", commit_preview["blocked_reasons"])

    def test_sell_add_signal_candidate_does_not_replace_macd_sell(self):
        result = self._build_preview()
        preview_rules = result["preview_rules"]
        candidate = preview_rules["indicator_follow_rule_preview"]["candidates"]["sell"]["add_signal_candidate"]

        self.assertEqual(
            preview_rules["sell"]["signals"]["macd_sell"],
            self.current_rules["sell"]["signals"]["macd_sell"],
        )
        self.assertEqual(candidate["path"], "sell.signals.ui_preview_condition_c_macd_sell")
        self.assertEqual(candidate["groups"][0]["name"], "UI_PREVIEW_SELL_MACD_CONDITION_C")
        self.assertFalse(candidate["enabled"])
        self.assertTrue(candidate["preview_candidate"])
        self.assertEqual(candidate["groups"][0]["conditions"][0]["target"], "MACD")
        self.assertEqual(candidate["groups"][0]["conditions"][0]["operator"], "<=")
        self.assertEqual(candidate["groups"][0]["conditions"][0]["value"], -1.0)

    def test_compare_preview_reports_candidate_statuses(self):
        result = self._build_preview()
        original_rules = deepcopy(self.current_rules)
        original_preview = deepcopy(result)

        diff = self.mapper.compare_engine_rules_preview(self.current_rules, result)
        changes = {change["path"]: change for change in diff["changes"]}

        self.assertEqual(self.current_rules, original_rules)
        self.assertEqual(result, original_preview)
        self.assertEqual(changes["bar.bar_minutes"]["status"], "changed")
        self.assertEqual(changes["buy.groups[0].conditions"]["status"], "merge_candidate")
        self.assertEqual(
            changes["sell.signals.ui_preview_condition_c_macd_sell"]["status"],
            "add_signal_candidate",
        )
        self.assertEqual(changes["buy.groups[0].conditions"]["risk"], "medium")
        self.assertEqual(changes["sell.signals.ui_preview_condition_c_macd_sell"]["risk"], "low")
        self.assertNotIn("sell.signals.macd_sell", changes)

    def test_compare_preview_reports_same_status(self):
        diff = self.mapper.compare_engine_rules_preview(
            {"bar": {"bar_minutes": 5}},
            {
                "preview_rules": {"bar": {"bar_minutes": 5}},
                "mapped_paths": ["bar.bar_minutes"],
                "warnings": [],
            },
        )

        self.assertEqual(diff["changes"][0]["status"], "same")
        self.assertEqual(diff["summary"]["same"], 1)

    def test_compare_preview_reports_missing_status(self):
        diff = self.mapper.compare_engine_rules_preview(
            {"bar": {"bar_minutes": 5}},
            {
                "preview_rules": {"bar": {}},
                "mapped_paths": ["bar.bar_minutes"],
                "warnings": [],
            },
        )

        self.assertEqual(diff["changes"][0]["status"], "missing")
        self.assertEqual(diff["summary"]["missing"], 1)

    def test_compare_preview_uses_warning_count_for_postponed_summary(self):
        result = self._build_preview()
        diff = self.mapper.compare_engine_rules_preview(self.current_rules, result)

        self.assertEqual(diff["summary"]["postponed"], len(result["warnings"]))
        self.assertEqual(diff["warnings"], result["warnings"])

    def test_build_pending_namespace_without_mutating_rules(self):
        original_rules = deepcopy(self.current_rules)
        result = self.mapper.build_engine_rules_pending_from_ui_state(
            deepcopy(self.ui_state),
            self.current_rules,
        )

        self.assertEqual(self.current_rules, original_rules)
        pending = result["pending_rules"]["indicator_follow_rule_pending"]
        self.assertEqual(pending["version"], "0.1")
        self.assertEqual(pending["mode"], "merge_add_candidate")
        self.assertEqual(pending["source"], "indicator_follow_ui_state")
        self.assertIn("source_ui_state_hash", pending)
        self.assertEqual(pending["source_ui_state_hash"], self.mapper.build_ui_state_hash(self.ui_state))
        self.assertIn("buy", pending["candidates"])
        self.assertIn("sell", pending["candidates"])

    def test_preview_first_mapping_paths_are_minimal_and_stable(self):
        result = self._build_preview()
        preview_rules = result["preview_rules"]
        pending_preview = preview_rules["indicator_follow_rule_preview"]["candidates"]

        self.assertEqual(
            result["mapped_paths"],
            [
                "bar.bar_minutes",
                "buy.groups[0].conditions",
                "indicators.rsi",
                "sell.signals.ui_preview_condition_c_macd_sell",
            ],
        )
        self.assertNotIn("timeframe", result["mapped_paths"])
        self.assertEqual(preview_rules["bar"], {"bar_minutes": 5})
        self.assertEqual(pending_preview["bar"], {"path": "bar.bar_minutes", "value": 5})
        self.assertEqual(pending_preview["buy"]["merge_into"], "buy.groups[0].conditions")
        self.assertEqual(pending_preview["indicators"]["rsi"]["path"], "indicators.rsi")
        self.assertEqual(
            pending_preview["sell"]["add_signal_candidate"]["path"],
            "sell.signals.ui_preview_condition_c_macd_sell",
        )

    def test_rules_json_pending_matches_mapper_generated_pending_paths(self):
        rules_path = self._rules_json_path()
        saved_rules = json.loads(rules_path.read_text(encoding="utf-8"))
        ui_state = saved_rules["indicator_follow_ui_state"]["state"]

        generated = self.mapper.build_engine_rules_pending_from_ui_state(
            deepcopy(ui_state),
            deepcopy(saved_rules),
        )
        generated_pending = generated["pending_rules"]["indicator_follow_rule_pending"]
        saved_pending = saved_rules["indicator_follow_rule_pending"]

        self.assertEqual(saved_pending["mapped_paths"], generated_pending["mapped_paths"])
        self.assertEqual(
            saved_pending["mapped_paths"],
            [
                "bar.bar_minutes",
                "buy.groups[0].conditions",
                "buy.filters.moving_average",
                "indicators.rsi",
                "sell.signals.ui_preview_condition_c_macd_sell",
            ],
        )
        self.assertEqual(
            saved_pending["candidates"]["bar"],
            generated_pending["candidates"]["bar"],
        )
        self.assertEqual(saved_pending["candidates"]["bar"], {"path": "bar.bar_minutes", "value": 5})
        self.assertEqual(
            saved_pending["candidates"]["indicators"]["rsi"],
            generated_pending["candidates"]["indicators"]["rsi"],
        )
        self.assertEqual(
            saved_pending["candidates"]["buy"]["merge_into"],
            generated_pending["candidates"]["buy"]["merge_into"],
        )
        self.assertEqual(
            saved_pending["candidates"]["filters"]["moving_average"],
            generated_pending["candidates"]["filters"]["moving_average"],
        )
        self.assertEqual(
            saved_pending["candidates"]["filters"]["moving_average"]["path"],
            "buy.filters.moving_average",
        )
        self.assertEqual(
            saved_pending["candidates"]["sell"]["add_signal_candidate"]["path"],
            generated_pending["candidates"]["sell"]["add_signal_candidate"]["path"],
        )
        self.assertEqual(
            saved_pending["candidates"]["sell"]["add_signal_candidate"]["groups"][0]["name"],
            "UI_PREVIEW_SELL_MACD_CONDITION_C",
        )
        self.assertNotIn("ui_preview_condition_c_indicator_sell", json.dumps(saved_pending))
        self.assertNotIn("ui_condition_c_indicator_sell", json.dumps(saved_pending))

    def test_ui_state_hash_is_stable_for_same_ui_state(self):
        self.assertEqual(
            self.mapper.build_ui_state_hash(deepcopy(self.ui_state)),
            self.mapper.build_ui_state_hash(deepcopy(self.ui_state)),
        )

    def test_ui_state_hash_changes_when_ui_state_changes(self):
        changed_state = deepcopy(self.ui_state)
        changed_state["basic"]["basic_signal_interval_combo"] = "10"

        self.assertNotEqual(
            self.mapper.build_ui_state_hash(self.ui_state),
            self.mapper.build_ui_state_hash(changed_state),
        )

    def test_approve_buy_merge_appends_threshold_only(self):
        preview = self._build_preview()
        result = self.mapper.approve_engine_rule_candidates(
            self.current_rules,
            preview,
            ["buy.groups[0].conditions"],
        )
        conditions = result["rules"]["buy"]["groups"][0]["conditions"]

        self.assertIn("buy.groups[0].conditions", result["applied_paths"])
        self.assertEqual(len(conditions), 2)
        self.assertEqual(conditions[0], self.current_rules["buy"]["groups"][0]["conditions"][0])
        self.assertEqual(conditions[1]["target"], "OSC")
        self.assertEqual(conditions[1]["operator"], "<=")
        self.assertEqual(conditions[1]["value"], -91.0)

    def test_approve_buy_merge_does_not_duplicate_turn_up(self):
        preview = self._build_preview()
        result = self.mapper.approve_engine_rule_candidates(
            self.current_rules,
            preview,
            ["buy.groups[0].conditions"],
        )
        conditions = result["rules"]["buy"]["groups"][0]["conditions"]
        turn_up_count = sum(
            1
            for condition in conditions
            if condition.get("target") == "OSC" and condition.get("operator") == "TURN_UP"
        )

        self.assertEqual(turn_up_count, 1)

    def test_approve_bar_minutes_sets_only_bar_minutes(self):
        preview = self._build_preview()
        result = self.mapper.approve_engine_rule_candidates(
            self.current_rules,
            preview,
            ["bar.bar_minutes"],
        )
        bar = result["rules"]["bar"]

        self.assertIn("bar.bar_minutes", result["applied_paths"])
        self.assertEqual(bar["bar_minutes"], 5)
        self.assertEqual(bar["buy_delay_bar"], self.current_rules["bar"]["buy_delay_bar"])
        self.assertEqual(bar["sell_delay_bar"], self.current_rules["bar"]["sell_delay_bar"])

    def test_approve_rsi_indicator_updates_only_rsi_indicator(self):
        preview = self._build_preview()
        result = self.mapper.approve_engine_rule_candidates(
            self.current_rules,
            preview,
            ["indicators.rsi"],
        )
        indicators = result["rules"]["indicators"]

        self.assertIn("indicators.rsi", result["applied_paths"])
        self.assertEqual(indicators["rsi"], {"period": 14})
        self.assertEqual(indicators["macd"], self.current_rules["indicators"]["macd"])

    def test_approve_sell_add_signal_without_changing_macd_sell(self):
        preview = self._build_preview()
        result = self.mapper.approve_engine_rule_candidates(
            self.current_rules,
            preview,
            ["sell.signals.ui_preview_condition_c_macd_sell"],
        )
        signals = result["rules"]["sell"]["signals"]

        self.assertIn("sell.signals.ui_preview_condition_c_macd_sell", result["applied_paths"])
        self.assertEqual(signals["macd_sell"], self.current_rules["sell"]["signals"]["macd_sell"])
        self.assertIn("ui_condition_c_macd_sell", signals)
        self.assertFalse(signals["ui_condition_c_macd_sell"]["enabled"])
        self.assertNotIn("path", signals["ui_condition_c_macd_sell"])
        self.assertNotIn("preview_candidate", signals["ui_condition_c_macd_sell"])
        self.assertEqual(
            signals["ui_condition_c_macd_sell"]["groups"][0]["conditions"][0]["target"],
            "MACD",
        )

    def test_sell_condition_c_inactive_does_not_create_candidate(self):
        state = deepcopy(self.ui_state)
        state["sell_ui"]["signal_conditions"]["condition_c"]["macd_check"] = False

        result = self.mapper.build_engine_rules_preview_from_ui_state(
            state,
            deepcopy(self.current_rules),
        )
        candidates = result["preview_rules"]["indicator_follow_rule_preview"]["candidates"]

        self.assertNotIn("sell", candidates)
        self.assertNotIn(
            "sell.signals.ui_preview_condition_c_macd_sell",
            self.mapper.build_rule_approval_session(result)["decisions"],
        )

    def test_approval_does_not_mutate_current_rules_or_preview(self):
        preview = self._build_preview()
        original_rules = deepcopy(self.current_rules)
        original_preview = deepcopy(preview)

        self.mapper.approve_engine_rule_candidates(
            self.current_rules,
            preview,
            [
                "buy.groups[0].conditions",
                "sell.signals.ui_preview_condition_c_macd_sell",
            ],
        )

        self.assertEqual(self.current_rules, original_rules)
        self.assertEqual(preview, original_preview)

    def test_unknown_approval_path_is_skipped_with_warning(self):
        preview = self._build_preview()
        result = self.mapper.approve_engine_rule_candidates(
            self.current_rules,
            preview,
            ["unknown.path"],
        )

        self.assertEqual(result["rules"], self.current_rules)
        self.assertEqual(result["applied_paths"], [])
        self.assertEqual(result["skipped_paths"], ["unknown.path"])
        self.assertEqual(result["warnings"], ["unknown approval path skipped: unknown.path"])

    def test_approval_dict_form_is_supported(self):
        preview = self._build_preview()
        result = self.mapper.approve_engine_rule_candidates(
            self.current_rules,
            preview,
            {"approved_paths": ["buy.groups[0].conditions"]},
        )

        self.assertIn("buy.groups[0].conditions", result["applied_paths"])

    def test_evaluate_rule_candidate_approval_defaults_to_pending(self):
        preview = self._build_preview()

        result = self.mapper.evaluate_rule_candidate_approval(preview, None)

        self.assertEqual(result["mode"], "candidate_approval")
        self.assertEqual(result["status"], "PENDING_REVIEW")
        self.assertEqual(result["approved_paths"], [])
        self.assertEqual(result["rejected_paths"], [])
        self.assertEqual(result["deferred_paths"], [])
        self.assertEqual(
            result["candidate_decisions"]["buy.groups[0].conditions"],
            {
                "decision": "PENDING",
                "candidate_type": "merge_conditions",
            },
        )
        self.assertEqual(
            result["candidate_decisions"]["bar.bar_minutes"],
            {
                "decision": "PENDING",
                "candidate_type": "set_value",
            },
        )
        self.assertEqual(
            result["candidate_decisions"]["indicators.rsi"],
            {
                "decision": "PENDING",
                "candidate_type": "set_indicator",
            },
        )
        self.assertEqual(
            result["candidate_decisions"]["sell.signals.ui_preview_condition_c_macd_sell"],
            {
                "decision": "PENDING",
                "candidate_type": "add_signal",
            },
        )

    def test_evaluate_rule_candidate_approval_approves_bar_path(self):
        preview = self._build_preview()

        result = self.mapper.evaluate_rule_candidate_approval(
            preview,
            {"bar.bar_minutes": "APPROVED"},
        )

        self.assertEqual(result["approved_paths"], ["bar.bar_minutes"])
        self.assertEqual(
            result["candidate_decisions"]["bar.bar_minutes"]["candidate_type"],
            "set_value",
        )

    def test_evaluate_rule_candidate_approval_approves_rsi_path(self):
        preview = self._build_preview()

        result = self.mapper.evaluate_rule_candidate_approval(
            preview,
            {"indicators.rsi": "APPROVED"},
        )

        self.assertEqual(result["approved_paths"], ["indicators.rsi"])
        self.assertEqual(
            result["candidate_decisions"]["indicators.rsi"]["candidate_type"],
            "set_indicator",
        )

    def test_evaluate_rule_candidate_approval_approves_buy_path(self):
        preview = self._build_preview()

        result = self.mapper.evaluate_rule_candidate_approval(
            preview,
            {"buy.groups[0].conditions": "APPROVED"},
        )

        self.assertEqual(result["approved_paths"], ["buy.groups[0].conditions"])
        self.assertEqual(
            result["candidate_decisions"]["buy.groups[0].conditions"]["decision"],
            "APPROVED",
        )
        self.assertEqual(
            result["candidate_decisions"]["sell.signals.ui_preview_condition_c_macd_sell"]["decision"],
            "PENDING",
        )

    def test_evaluate_rule_candidate_approval_approves_sell_path(self):
        preview = self._build_preview()

        result = self.mapper.evaluate_rule_candidate_approval(
            preview,
            {"sell.signals.ui_preview_condition_c_macd_sell": "APPROVED"},
        )

        self.assertEqual(result["approved_paths"], ["sell.signals.ui_preview_condition_c_macd_sell"])
        self.assertEqual(
            result["candidate_decisions"]["sell.signals.ui_preview_condition_c_macd_sell"]["candidate_type"],
            "add_signal",
        )

    def test_evaluate_rule_candidate_approval_records_reject_and_defer(self):
        preview = self._build_preview()

        result = self.mapper.evaluate_rule_candidate_approval(
            preview,
            {
                "buy.groups[0].conditions": "REJECTED",
                "sell.signals.ui_preview_condition_c_macd_sell": "DEFERRED",
            },
        )

        self.assertEqual(result["rejected_paths"], ["buy.groups[0].conditions"])
        self.assertEqual(result["deferred_paths"], ["sell.signals.ui_preview_condition_c_macd_sell"])
        self.assertEqual(result["approved_paths"], [])

    def test_evaluate_rule_candidate_approval_records_applied_preview_only(self):
        preview = self._build_preview()

        result = self.mapper.evaluate_rule_candidate_approval(
            preview,
            {"buy.groups[0].conditions": "APPLIED_PREVIEW_ONLY"},
        )

        self.assertEqual(result["approved_paths"], [])
        self.assertEqual(result["rejected_paths"], [])
        self.assertEqual(result["deferred_paths"], [])
        self.assertEqual(
            result["candidate_decisions"]["buy.groups[0].conditions"]["decision"],
            "APPLIED_PREVIEW_ONLY",
        )

    def test_evaluate_rule_candidate_approval_blocks_unknown_decision(self):
        preview = self._build_preview()

        with self.assertRaises(ValueError):
            self.mapper.evaluate_rule_candidate_approval(
                preview,
                {"buy.groups[0].conditions": "UNKNOWN"},
            )

    def test_evaluate_rule_candidate_approval_warns_unknown_path(self):
        preview = self._build_preview()

        result = self.mapper.evaluate_rule_candidate_approval(
            preview,
            {"unknown.path": "APPROVED"},
        )

        self.assertEqual(result["approved_paths"], [])
        self.assertEqual(result["warnings"], ["unknown approval path ignored: unknown.path"])

    def test_evaluate_rule_candidate_approval_supports_candidate_decisions_shape(self):
        preview = self._build_preview()

        result = self.mapper.evaluate_rule_candidate_approval(
            preview,
            {
                "candidate_decisions": {
                    "buy.groups[0].conditions": {"decision": "APPROVED"},
                }
            },
        )

        self.assertEqual(result["approved_paths"], ["buy.groups[0].conditions"])

    def test_evaluate_rule_candidate_approval_does_not_mutate_inputs(self):
        preview = self._build_preview()
        approval_decisions = {
            "buy.groups[0].conditions": "APPROVED",
            "sell.signals.ui_preview_condition_c_macd_sell": "REJECTED",
        }
        original_preview = deepcopy(preview)
        original_decisions = deepcopy(approval_decisions)

        self.mapper.evaluate_rule_candidate_approval(preview, approval_decisions)

        self.assertEqual(preview, original_preview)
        self.assertEqual(approval_decisions, original_decisions)

    def test_evaluate_rule_candidate_approval_does_not_write_rules_json(self):
        before = self._rules_json_hash()
        preview = self._build_preview()

        self.mapper.evaluate_rule_candidate_approval(
            preview,
            {
                "buy.groups[0].conditions": "APPROVED",
                "sell.signals.ui_preview_condition_c_macd_sell": "DEFERRED",
            },
        )

        self.assertEqual(before, self._rules_json_hash())

    def test_build_approved_rule_patch_preview_empty_without_approval(self):
        preview = self._build_preview()
        approval = self.mapper.evaluate_rule_candidate_approval(preview, None)

        result = self.mapper.build_approved_rule_patch_preview(self.current_rules, preview, approval)

        self.assertEqual(result["mode"], "approved_rule_patch_preview")
        self.assertEqual(result["stage"], "RULE_PATCH_PREVIEW")
        self.assertEqual(result["patches"], [])
        self.assertEqual(result["summary"]["approved"], 0)
        self.assertEqual(result["summary"]["patches"], 0)

    def test_build_approved_rule_patch_preview_builds_buy_merge_patch(self):
        preview = self._build_preview()
        approval = self.mapper.evaluate_rule_candidate_approval(
            preview,
            {"buy.groups[0].conditions": "APPROVED"},
        )

        result = self.mapper.build_approved_rule_patch_preview(self.current_rules, preview, approval)
        patch = result["patches"][0]

        self.assertEqual(patch["operation"], "merge_conditions")
        self.assertEqual(patch["source_path"], "buy.groups[0].conditions")
        self.assertEqual(patch["target_path"], "buy.groups[0].conditions")
        self.assertNotIn("buy.groups", patch)
        self.assertEqual(patch["risk"], "medium")

    def test_build_approved_rule_patch_preview_builds_bar_patch(self):
        preview = self._build_preview()
        approval = self.mapper.evaluate_rule_candidate_approval(
            preview,
            {"bar.bar_minutes": "APPROVED"},
        )

        result = self.mapper.build_approved_rule_patch_preview(self.current_rules, preview, approval)
        patch = result["patches"][0]

        self.assertEqual(patch["operation"], "set_value")
        self.assertEqual(patch["source_path"], "bar.bar_minutes")
        self.assertEqual(patch["target_path"], "bar.bar_minutes")
        self.assertEqual(patch["value"], 5)
        self.assertEqual(patch["risk"], "low")

    def test_build_approved_rule_patch_preview_buy_patch_keeps_skip_and_adds_threshold(self):
        preview = self._build_preview()
        approval = self.mapper.evaluate_rule_candidate_approval(
            preview,
            {"buy.groups[0].conditions": "APPROVED"},
        )

        result = self.mapper.build_approved_rule_patch_preview(self.current_rules, preview, approval)
        patch = result["patches"][0]

        self.assertEqual(
            patch["skip_existing"],
            [
                {
                    "target": "OSC",
                    "operator": "TURN_UP",
                    "reason": "already exists in current buy.groups[0]",
                }
            ],
        )
        self.assertEqual(len(patch["add_conditions"]), 1)
        self.assertEqual(patch["add_conditions"][0]["target"], "OSC")
        self.assertEqual(patch["add_conditions"][0]["operator"], "<=")
        self.assertEqual(patch["add_conditions"][0]["value"], -91.0)

    def test_build_approved_rule_patch_preview_builds_sell_add_signal_patch(self):
        preview = self._build_preview()
        approval = self.mapper.evaluate_rule_candidate_approval(
            preview,
            {"sell.signals.ui_preview_condition_c_macd_sell": "APPROVED"},
        )

        result = self.mapper.build_approved_rule_patch_preview(self.current_rules, preview, approval)
        patch = result["patches"][0]

        self.assertEqual(patch["operation"], "add_signal")
        self.assertEqual(patch["source_path"], "sell.signals.ui_preview_condition_c_macd_sell")
        self.assertEqual(patch["target_path"], "sell.signals.ui_condition_c_macd_sell")
        self.assertNotEqual(patch["target_path"], "sell.signals.macd_sell")
        self.assertEqual(patch["risk"], "high")
        self.assertFalse(patch["signal"]["enabled"])
        self.assertNotIn("preview_candidate", patch["signal"])
        self.assertNotIn("path", patch["signal"])
        self.assertEqual(
            patch["signal"]["groups"][0]["conditions"][0]["target"],
            "MACD",
        )

    def test_build_approved_rule_patch_preview_skips_non_approved_decisions(self):
        preview = self._build_preview()
        approval = self.mapper.evaluate_rule_candidate_approval(
            preview,
            {
                "buy.groups[0].conditions": "REJECTED",
                "sell.signals.ui_preview_condition_c_macd_sell": "DEFERRED",
            },
        )

        result = self.mapper.build_approved_rule_patch_preview(self.current_rules, preview, approval)

        self.assertEqual(result["patches"], [])
        reasons = {item["path"]: item["reason"] for item in result["skipped_paths"]}
        self.assertEqual(reasons["buy.groups[0].conditions"], "decision is REJECTED")
        self.assertEqual(reasons["sell.signals.ui_preview_condition_c_macd_sell"], "decision is DEFERRED")

    def test_build_approved_rule_patch_preview_skips_applied_preview_only(self):
        preview = self._build_preview()
        approval = self.mapper.evaluate_rule_candidate_approval(
            preview,
            {"buy.groups[0].conditions": "APPLIED_PREVIEW_ONLY"},
        )

        result = self.mapper.build_approved_rule_patch_preview(self.current_rules, preview, approval)

        self.assertEqual(result["patches"], [])
        self.assertIn(
            {
                "path": "buy.groups[0].conditions",
                "reason": "decision is APPLIED_PREVIEW_ONLY",
            },
            result["skipped_paths"],
        )

    def test_build_approved_rule_patch_preview_warns_unknown_approved_path(self):
        preview = self._build_preview()
        approval = {
            "mode": "candidate_approval",
            "status": "PENDING_REVIEW",
            "approved_paths": ["unknown.path"],
            "candidate_decisions": {},
            "warnings": [],
        }

        result = self.mapper.build_approved_rule_patch_preview(self.current_rules, preview, approval)

        self.assertEqual(result["patches"], [])
        self.assertIn(
            {"path": "unknown.path", "reason": "approved path is not a preview candidate"},
            result["skipped_paths"],
        )
        self.assertEqual(result["warnings"], ["unknown approved path skipped: unknown.path"])

    def test_build_approved_rule_patch_preview_skips_existing_sell_target(self):
        preview = self._build_preview()
        approval = self.mapper.evaluate_rule_candidate_approval(
            preview,
            {"sell.signals.ui_preview_condition_c_macd_sell": "APPROVED"},
        )
        current_rules = deepcopy(self.current_rules)
        current_rules["sell"]["signals"]["ui_condition_c_macd_sell"] = {"enabled": False}

        result = self.mapper.build_approved_rule_patch_preview(current_rules, preview, approval)

        self.assertEqual(result["patches"], [])
        self.assertIn(
            {
                "path": "sell.signals.ui_preview_condition_c_macd_sell",
                "reason": "target path already exists: sell.signals.ui_condition_c_macd_sell",
            },
            result["skipped_paths"],
        )

    def test_build_approved_rule_patch_preview_skips_same_existing_sell_target_without_conflict(self):
        preview = self._build_preview()
        approval = self.mapper.evaluate_rule_candidate_approval(
            preview,
            {"sell.signals.ui_preview_condition_c_macd_sell": "APPROVED"},
        )
        base_patch = self.mapper.build_approved_rule_patch_preview(self.current_rules, preview, approval)["patches"][0]
        current_rules = deepcopy(self.current_rules)
        current_rules["sell"]["signals"]["ui_condition_c_macd_sell"] = deepcopy(base_patch["signal"])

        result = self.mapper.build_approved_rule_patch_preview(current_rules, preview, approval)
        session = self.mapper.build_rule_approval_session(
            preview,
            {"sell.signals.ui_preview_condition_c_macd_sell": "APPROVED"},
        )
        fingerprint = self.mapper.build_rule_approval_session_fingerprint(current_rules, preview)
        session["fingerprint"] = fingerprint["fingerprint"]
        session["fingerprint_detail"] = fingerprint
        commit_preview = self.mapper.build_rule_commit_preview(
            current_rules,
            preview,
            session,
            {"approval_session_dirty": False},
        )

        self.assertEqual(result["patches"], [])
        self.assertIn(
            {
                "path": "sell.signals.ui_preview_condition_c_macd_sell",
                "reason": "sell signal is unchanged",
            },
            result["skipped_paths"],
        )
        self.assertFalse(commit_preview["commit_allowed"])
        self.assertEqual(commit_preview["final_diff"], [])
        self.assertNotIn("target path conflict", commit_preview["blocked_reasons"])
        self.assertIn("approval session has no approved patches", commit_preview["blocked_reasons"])

    def test_build_approved_rule_patch_preview_does_not_mutate_inputs(self):
        preview = self._build_preview()
        approval = self.mapper.evaluate_rule_candidate_approval(
            preview,
            {
                "buy.groups[0].conditions": "APPROVED",
                "sell.signals.ui_preview_condition_c_macd_sell": "APPROVED",
            },
        )
        original_rules = deepcopy(self.current_rules)
        original_preview = deepcopy(preview)
        original_approval = deepcopy(approval)

        self.mapper.build_approved_rule_patch_preview(self.current_rules, preview, approval)

        self.assertEqual(self.current_rules, original_rules)
        self.assertEqual(preview, original_preview)
        self.assertEqual(approval, original_approval)

    def test_build_approved_rule_patch_preview_does_not_write_rules_json(self):
        before = self._rules_json_hash()
        preview = self._build_preview()
        approval = self.mapper.evaluate_rule_candidate_approval(
            preview,
            {
                "buy.groups[0].conditions": "APPROVED",
                "sell.signals.ui_preview_condition_c_macd_sell": "APPROVED",
            },
        )

        self.mapper.build_approved_rule_patch_preview(self.current_rules, preview, approval)

        self.assertEqual(before, self._rules_json_hash())

    def _build_patch_preview(self, approvals):
        preview = self._build_preview()
        approval = self.mapper.evaluate_rule_candidate_approval(preview, approvals)
        return self.mapper.build_approved_rule_patch_preview(self.current_rules, preview, approval)

    def test_apply_approved_rule_patch_preview_empty_patches(self):
        patch_preview = {
            "mode": "approved_rule_patch_preview",
            "stage": "RULE_PATCH_PREVIEW",
            "patches": [],
        }

        result = self.mapper.apply_approved_rule_patch_preview(self.current_rules, patch_preview)

        self.assertEqual(result["mode"], "approved_rule_apply_preview")
        self.assertEqual(result["stage"], "RULE_APPLY_PREVIEW")
        self.assertEqual(result["summary"], {"patches": 0, "applied": 0, "skipped": 0})
        self.assertEqual(result["applied_patches"], [])
        self.assertEqual(result["skipped_patches"], [])
        self.assertEqual(result["applied_rules_preview"], self.current_rules)

    def test_apply_approved_rule_patch_preview_bar_sets_bar_minutes_only(self):
        patch_preview = self._build_patch_preview({"bar.bar_minutes": "APPROVED"})

        result = self.mapper.apply_approved_rule_patch_preview(self.current_rules, patch_preview)
        bar = result["applied_rules_preview"]["bar"]

        self.assertEqual(result["summary"]["applied"], 1)
        self.assertEqual(result["applied_patches"][0]["operation"], "set_value")
        self.assertEqual(bar["bar_minutes"], 5)
        self.assertEqual(bar["buy_delay_bar"], self.current_rules["bar"]["buy_delay_bar"])
        self.assertEqual(bar["sell_delay_bar"], self.current_rules["bar"]["sell_delay_bar"])

    def test_apply_approved_rule_patch_preview_rsi_sets_existing_indicator_only(self):
        patch_preview = self._build_patch_preview({"indicators.rsi": "APPROVED"})

        result = self.mapper.apply_approved_rule_patch_preview(self.current_rules, patch_preview)
        indicators = result["applied_rules_preview"]["indicators"]

        self.assertEqual(result["summary"]["applied"], 1)
        self.assertEqual(result["applied_patches"][0]["operation"], "set_indicator")
        self.assertEqual(indicators["rsi"], {"period": 14})
        self.assertEqual(indicators["macd"], self.current_rules["indicators"]["macd"])

    def test_apply_approved_rule_patch_preview_buy_merge_adds_one_condition(self):
        patch_preview = self._build_patch_preview({"buy.groups[0].conditions": "APPROVED"})

        result = self.mapper.apply_approved_rule_patch_preview(self.current_rules, patch_preview)
        conditions = result["applied_rules_preview"]["buy"]["groups"][0]["conditions"]

        self.assertEqual(result["summary"]["applied"], 1)
        self.assertEqual(result["applied_patches"][0]["operation"], "merge_conditions")
        self.assertEqual(len(conditions), 2)
        self.assertEqual(conditions[1]["target"], "OSC")
        self.assertEqual(conditions[1]["operator"], "<=")
        self.assertEqual(conditions[1]["value"], -91.0)

    def test_apply_approved_rule_patch_preview_buy_keeps_existing_turn_up(self):
        patch_preview = self._build_patch_preview({"buy.groups[0].conditions": "APPROVED"})

        result = self.mapper.apply_approved_rule_patch_preview(self.current_rules, patch_preview)
        conditions = result["applied_rules_preview"]["buy"]["groups"][0]["conditions"]

        self.assertEqual(conditions[0], self.current_rules["buy"]["groups"][0]["conditions"][0])
        turn_up_count = sum(
            1
            for condition in conditions
            if condition.get("target") == "OSC" and condition.get("operator") == "TURN_UP"
        )
        self.assertEqual(turn_up_count, 1)

    def test_apply_approved_rule_patch_preview_buy_duplicate_condition_is_skipped(self):
        patch_preview = {
            "patches": [
                {
                    "source_path": "buy.groups[0].conditions",
                    "target_path": "buy.groups[0].conditions",
                    "operation": "merge_conditions",
                    "add_conditions": [
                        {
                            "target": "OSC",
                            "operator": "TURN_UP",
                        }
                    ],
                }
            ]
        }

        result = self.mapper.apply_approved_rule_patch_preview(self.current_rules, patch_preview)
        conditions = result["applied_rules_preview"]["buy"]["groups"][0]["conditions"]

        self.assertEqual(len(conditions), 1)
        self.assertEqual(result["summary"]["applied"], 0)
        self.assertEqual(result["summary"]["skipped"], 1)
        self.assertEqual(result["skipped_patches"][0]["reason"], "no new conditions to add")

    def test_apply_approved_rule_patch_preview_does_not_replace_buy_groups(self):
        patch_preview = self._build_patch_preview({"buy.groups[0].conditions": "APPROVED"})

        result = self.mapper.apply_approved_rule_patch_preview(self.current_rules, patch_preview)
        groups = result["applied_rules_preview"]["buy"]["groups"]

        self.assertEqual(len(groups), len(self.current_rules["buy"]["groups"]))
        self.assertEqual(groups[1:], self.current_rules["buy"]["groups"][1:])

    def test_apply_approved_rule_patch_preview_sell_add_signal(self):
        patch_preview = self._build_patch_preview({"sell.signals.ui_preview_condition_c_macd_sell": "APPROVED"})

        result = self.mapper.apply_approved_rule_patch_preview(self.current_rules, patch_preview)
        signals = result["applied_rules_preview"]["sell"]["signals"]

        self.assertEqual(result["summary"]["applied"], 1)
        self.assertIn("ui_condition_c_macd_sell", signals)
        self.assertFalse(signals["ui_condition_c_macd_sell"]["enabled"])
        self.assertNotIn("preview_candidate", signals["ui_condition_c_macd_sell"])
        self.assertEqual(
            signals["ui_condition_c_macd_sell"]["groups"][0]["conditions"][0]["target"],
            "MACD",
        )

    def test_apply_approved_rule_patch_preview_sell_macd_sell_unchanged(self):
        patch_preview = self._build_patch_preview({"sell.signals.ui_preview_condition_c_macd_sell": "APPROVED"})

        result = self.mapper.apply_approved_rule_patch_preview(self.current_rules, patch_preview)

        self.assertEqual(
            result["applied_rules_preview"]["sell"]["signals"]["macd_sell"],
            self.current_rules["sell"]["signals"]["macd_sell"],
        )

    def test_apply_approved_rule_patch_preview_sell_existing_target_is_skipped(self):
        current_rules = deepcopy(self.current_rules)
        current_rules["sell"]["signals"]["ui_condition_c_macd_sell"] = {"enabled": False}
        patch_preview = self._build_patch_preview({"sell.signals.ui_preview_condition_c_macd_sell": "APPROVED"})

        result = self.mapper.apply_approved_rule_patch_preview(current_rules, patch_preview)

        self.assertEqual(result["summary"]["applied"], 0)
        self.assertEqual(result["summary"]["skipped"], 1)
        self.assertEqual(result["skipped_patches"][0]["reason"], "target path already exists")

    def test_apply_approved_rule_patch_preview_unknown_operation_is_skipped_with_warning(self):
        patch_preview = {
            "patches": [
                {
                    "source_path": "unknown.path",
                    "target_path": "unknown.path",
                    "operation": "unknown_operation",
                }
            ]
        }

        result = self.mapper.apply_approved_rule_patch_preview(self.current_rules, patch_preview)

        self.assertEqual(result["summary"], {"patches": 1, "applied": 0, "skipped": 1})
        self.assertEqual(result["skipped_patches"][0]["reason"], "unsupported patch operation")
        self.assertEqual(result["warnings"], ["unsupported patch operation: unknown_operation"])

    def test_apply_approved_rule_patch_preview_does_not_mutate_inputs(self):
        patch_preview = self._build_patch_preview(
            {
                "buy.groups[0].conditions": "APPROVED",
                "sell.signals.ui_preview_condition_c_macd_sell": "APPROVED",
            }
        )
        original_rules = deepcopy(self.current_rules)
        original_patch_preview = deepcopy(patch_preview)

        self.mapper.apply_approved_rule_patch_preview(self.current_rules, patch_preview)

        self.assertEqual(self.current_rules, original_rules)
        self.assertEqual(patch_preview, original_patch_preview)

    def test_apply_approved_rule_patch_preview_does_not_write_rules_json(self):
        before = self._rules_json_hash()
        patch_preview = self._build_patch_preview(
            {
                "buy.groups[0].conditions": "APPROVED",
                "sell.signals.ui_preview_condition_c_macd_sell": "APPROVED",
            }
        )

        self.mapper.apply_approved_rule_patch_preview(self.current_rules, patch_preview)

        self.assertEqual(before, self._rules_json_hash())

    def test_build_rule_approval_session_defaults_candidates_to_pending(self):
        preview = self._build_preview()

        session = self.mapper.build_rule_approval_session(preview)

        self.assertEqual(session["mode"], "approval_session")
        self.assertEqual(session["session_status"], "ACTIVE")
        self.assertEqual(session["decisions"]["bar.bar_minutes"], "PENDING")
        self.assertEqual(session["decisions"]["buy.groups[0].conditions"], "PENDING")
        self.assertEqual(session["decisions"]["indicators.rsi"], "PENDING")
        self.assertEqual(session["decisions"]["sell.signals.ui_preview_condition_c_macd_sell"], "PENDING")
        self.assertTrue(session["updated_at"])

    def test_build_rule_approval_session_records_candidate_types(self):
        preview = self._build_preview()

        session = self.mapper.build_rule_approval_session(preview)

        self.assertEqual(session["candidate_types"]["bar.bar_minutes"], "set_value")
        self.assertEqual(session["candidate_types"]["buy.groups[0].conditions"], "merge_conditions")
        self.assertEqual(session["candidate_types"]["indicators.rsi"], "set_indicator")
        self.assertEqual(
            session["candidate_types"]["sell.signals.ui_preview_condition_c_macd_sell"],
            "add_signal",
        )

    def test_build_rule_approval_session_applies_initial_decisions(self):
        preview = self._build_preview()

        session = self.mapper.build_rule_approval_session(
            preview,
            {"buy.groups[0].conditions": "APPROVED"},
        )

        self.assertEqual(session["decisions"]["buy.groups[0].conditions"], "APPROVED")
        self.assertEqual(session["decisions"]["bar.bar_minutes"], "PENDING")
        self.assertEqual(session["decisions"]["indicators.rsi"], "PENDING")
        self.assertEqual(session["decisions"]["sell.signals.ui_preview_condition_c_macd_sell"], "PENDING")

    def test_build_rule_approval_session_warns_unknown_initial_path(self):
        preview = self._build_preview()

        session = self.mapper.build_rule_approval_session(
            preview,
            {"unknown.path": "APPROVED"},
        )

        self.assertEqual(session["warnings"], ["unknown approval session path ignored: unknown.path"])

    def test_build_rule_approval_session_blocks_unknown_decision(self):
        preview = self._build_preview()

        with self.assertRaises(ValueError):
            self.mapper.build_rule_approval_session(
                preview,
                {"buy.groups[0].conditions": "UNKNOWN"},
            )

    def test_update_rule_approval_session_updates_buy_approved(self):
        session = self.mapper.build_rule_approval_session(self._build_preview())

        updated = self.mapper.update_rule_approval_session(
            session,
            "buy.groups[0].conditions",
            "APPROVED",
        )

        self.assertEqual(updated["decisions"]["buy.groups[0].conditions"], "APPROVED")
        self.assertEqual(updated["decisions"]["sell.signals.ui_preview_condition_c_macd_sell"], "PENDING")

    def test_update_rule_approval_session_updates_sell_supported_decisions(self):
        session = self.mapper.build_rule_approval_session(self._build_preview())
        for decision in ("REJECTED", "DEFERRED", "APPLIED_PREVIEW_ONLY"):
            with self.subTest(decision=decision):
                updated = self.mapper.update_rule_approval_session(
                    session,
                    "sell.signals.ui_preview_condition_c_macd_sell",
                    decision,
                )
                self.assertEqual(
                    updated["decisions"]["sell.signals.ui_preview_condition_c_macd_sell"],
                    decision,
                )

    def test_update_rule_approval_session_blocks_unknown_path_or_decision(self):
        session = self.mapper.build_rule_approval_session(self._build_preview())

        with self.assertRaises(ValueError):
            self.mapper.update_rule_approval_session(session, "unknown.path", "APPROVED")

        with self.assertRaises(ValueError):
            self.mapper.update_rule_approval_session(session, "buy.groups[0].conditions", "UNKNOWN")

    def test_update_rule_approval_session_does_not_mutate_original(self):
        session = self.mapper.build_rule_approval_session(self._build_preview())
        original = deepcopy(session)

        self.mapper.update_rule_approval_session(session, "buy.groups[0].conditions", "APPROVED")

        self.assertEqual(session, original)

    def test_build_rule_approval_session_fingerprint_is_stable(self):
        preview = self._build_preview()

        first = self.mapper.build_rule_approval_session_fingerprint(self.current_rules, preview)
        second = self.mapper.build_rule_approval_session_fingerprint(deepcopy(self.current_rules), deepcopy(preview))

        self.assertEqual(first["mode"], "approval_candidate_fingerprint")
        self.assertEqual(first["preview_mode"], "merge_add_candidate")
        self.assertEqual(
            first["candidate_paths"],
            [
                "bar.bar_minutes",
                "buy.groups[0].conditions",
                "indicators.rsi",
                "sell.signals.ui_preview_condition_c_macd_sell",
            ],
        )
        self.assertEqual(first, second)

    def test_build_rule_approval_session_fingerprint_changes_for_candidate_payload(self):
        preview = self._build_preview()
        changed_preview = deepcopy(preview)
        changed_preview["preview_rules"]["indicator_follow_rule_preview"]["candidates"]["buy"][
            "add_conditions"
        ][0]["value"] = -92.0

        first = self.mapper.build_rule_approval_session_fingerprint(self.current_rules, preview)
        changed = self.mapper.build_rule_approval_session_fingerprint(self.current_rules, changed_preview)

        self.assertNotEqual(first["candidate_hash"], changed["candidate_hash"])
        self.assertNotEqual(first["fingerprint"], changed["fingerprint"])

    def test_build_rule_approval_session_fingerprint_changes_for_current_rule_target(self):
        preview = self._build_preview()
        changed_rules = deepcopy(self.current_rules)
        changed_rules["buy"]["groups"][0]["conditions"].append(
            {"target": "OSC", "operator": "<=", "value": -91.0}
        )

        first = self.mapper.build_rule_approval_session_fingerprint(self.current_rules, preview)
        changed = self.mapper.build_rule_approval_session_fingerprint(changed_rules, preview)

        self.assertNotEqual(first["current_rule_target_hash"], changed["current_rule_target_hash"])
        self.assertNotEqual(first["fingerprint"], changed["fingerprint"])

    def test_build_rule_approval_session_fingerprint_changes_for_current_bar_target(self):
        preview = self._build_preview()
        changed_rules = deepcopy(self.current_rules)
        changed_rules["bar"]["bar_minutes"] = 10

        first = self.mapper.build_rule_approval_session_fingerprint(self.current_rules, preview)
        changed = self.mapper.build_rule_approval_session_fingerprint(changed_rules, preview)

        self.assertNotEqual(first["current_rule_target_hash"], changed["current_rule_target_hash"])
        self.assertNotEqual(first["fingerprint"], changed["fingerprint"])

    def test_validate_rule_approval_session_for_preview_success(self):
        preview = self._build_preview()
        fingerprint = self.mapper.build_rule_approval_session_fingerprint(self.current_rules, preview)
        session = self.mapper.build_rule_approval_session(
            preview,
            {"buy.groups[0].conditions": "APPROVED"},
        )
        session["fingerprint"] = fingerprint["fingerprint"]

        result = self.mapper.validate_rule_approval_session_for_preview(
            session,
            self.current_rules,
            preview,
        )

        self.assertTrue(result["valid"])
        self.assertTrue(result["path_match"])
        self.assertTrue(result["type_match"])
        self.assertTrue(result["fingerprint_match"])

    def test_validate_rule_approval_session_for_preview_detects_path_mismatch(self):
        preview = self._build_preview()
        fingerprint = self.mapper.build_rule_approval_session_fingerprint(self.current_rules, preview)
        session = self.mapper.build_rule_approval_session(preview)
        session["fingerprint"] = fingerprint["fingerprint"]
        session["decisions"].pop("sell.signals.ui_preview_condition_c_macd_sell")

        result = self.mapper.validate_rule_approval_session_for_preview(
            session,
            self.current_rules,
            preview,
        )

        self.assertFalse(result["valid"])
        self.assertFalse(result["path_match"])
        self.assertIn("approval session candidate paths do not match current preview", result["blocked_reasons"])

    def test_validate_rule_approval_session_for_preview_detects_type_mismatch(self):
        preview = self._build_preview()
        fingerprint = self.mapper.build_rule_approval_session_fingerprint(self.current_rules, preview)
        session = self.mapper.build_rule_approval_session(preview)
        session["fingerprint"] = fingerprint["fingerprint"]
        session["candidate_types"]["buy.groups[0].conditions"] = "add_signal"

        result = self.mapper.validate_rule_approval_session_for_preview(
            session,
            self.current_rules,
            preview,
        )

        self.assertFalse(result["valid"])
        self.assertFalse(result["type_match"])
        self.assertIn("approval session candidate types do not match current preview", result["blocked_reasons"])

    def test_validate_rule_approval_session_for_preview_detects_fingerprint_mismatch(self):
        preview = self._build_preview()
        session = self.mapper.build_rule_approval_session(preview)
        session["fingerprint"] = "stale"

        result = self.mapper.validate_rule_approval_session_for_preview(
            session,
            self.current_rules,
            preview,
        )

        self.assertFalse(result["valid"])
        self.assertFalse(result["fingerprint_match"])
        self.assertIn("approval session fingerprint does not match current preview", result["blocked_reasons"])

    def test_restore_rule_approval_session_for_preview_restores_matching_decisions(self):
        preview = self._build_preview()
        fingerprint = self.mapper.build_rule_approval_session_fingerprint(self.current_rules, preview)
        saved = self.mapper.build_rule_approval_session(
            preview,
            {
                "buy.groups[0].conditions": "APPROVED",
                "sell.signals.ui_preview_condition_c_macd_sell": "DEFERRED",
            },
        )
        saved["fingerprint"] = fingerprint["fingerprint"]

        restored = self.mapper.restore_rule_approval_session_for_preview(saved, self.current_rules, preview)

        self.assertEqual(restored["restore_status"], "RESTORED")
        self.assertEqual(restored["decisions"]["buy.groups[0].conditions"], "APPROVED")
        self.assertEqual(restored["decisions"]["sell.signals.ui_preview_condition_c_macd_sell"], "DEFERRED")

    def test_restore_rule_approval_session_for_preview_resets_mismatch_to_pending(self):
        preview = self._build_preview()
        saved = self.mapper.build_rule_approval_session(
            preview,
            {
                "buy.groups[0].conditions": "APPROVED",
                "sell.signals.ui_preview_condition_c_macd_sell": "APPROVED",
            },
        )
        saved["fingerprint"] = "stale"

        restored = self.mapper.restore_rule_approval_session_for_preview(saved, self.current_rules, preview)

        self.assertEqual(restored["restore_status"], "RESET_TO_PENDING")
        self.assertEqual(set(restored["decisions"].values()), {"PENDING"})
        self.assertIn(
            "approval session fingerprint mismatch; decisions reset to PENDING",
            restored["warnings"],
        )

    def test_restore_rule_approval_session_for_preview_handles_unknown_decision_safely(self):
        preview = self._build_preview()
        fingerprint = self.mapper.build_rule_approval_session_fingerprint(self.current_rules, preview)
        saved = self.mapper.build_rule_approval_session(preview)
        saved["fingerprint"] = fingerprint["fingerprint"]
        saved["decisions"]["buy.groups[0].conditions"] = "UNKNOWN"

        restored = self.mapper.restore_rule_approval_session_for_preview(saved, self.current_rules, preview)

        self.assertEqual(restored["restore_status"], "RESTORED")
        self.assertEqual(restored["decisions"]["buy.groups[0].conditions"], "PENDING")
        self.assertIn(
            "unknown approval decision ignored for buy.groups[0].conditions: UNKNOWN",
            restored["warnings"],
        )

    def test_rule_approval_session_fingerprint_restore_does_not_mutate_inputs(self):
        preview = self._build_preview()
        session = self.mapper.build_rule_approval_session(
            preview,
            {"buy.groups[0].conditions": "APPROVED"},
        )
        original_rules = deepcopy(self.current_rules)
        original_preview = deepcopy(preview)
        original_session = deepcopy(session)

        self.mapper.build_rule_approval_session_fingerprint(self.current_rules, preview)
        self.mapper.validate_rule_approval_session_for_preview(session, self.current_rules, preview)
        self.mapper.restore_rule_approval_session_for_preview(session, self.current_rules, preview)

        self.assertEqual(self.current_rules, original_rules)
        self.assertEqual(preview, original_preview)
        self.assertEqual(session, original_session)

    def test_rule_approval_session_fingerprint_restore_does_not_write_rules_json(self):
        before = self._rules_json_hash()
        preview = self._build_preview()
        session = self.mapper.build_rule_approval_session(
            preview,
            {"buy.groups[0].conditions": "APPROVED"},
        )

        self.mapper.build_rule_approval_session_fingerprint(self.current_rules, preview)
        self.mapper.validate_rule_approval_session_for_preview(session, self.current_rules, preview)
        self.mapper.restore_rule_approval_session_for_preview(session, self.current_rules, preview)

        self.assertEqual(before, self._rules_json_hash())

    def test_build_rule_pipeline_preview_default_has_no_patch_or_apply(self):
        preview = self._build_preview()
        session = self.mapper.build_rule_approval_session(preview)

        result = self.mapper.build_rule_pipeline_preview(self.current_rules, preview, session)

        self.assertEqual(result["mode"], "rule_pipeline_preview")
        self.assertEqual(result["stage"], "RULE_PIPELINE_PREVIEW")
        self.assertEqual(result["patch_preview"]["patches"], [])
        self.assertEqual(result["apply_preview"]["applied_patches"], [])

    def test_build_rule_pipeline_preview_buy_approved_creates_merge_patch_and_apply(self):
        preview = self._build_preview()
        session = self.mapper.build_rule_approval_session(
            preview,
            {"buy.groups[0].conditions": "APPROVED"},
        )

        result = self.mapper.build_rule_pipeline_preview(self.current_rules, preview, session)

        self.assertEqual(result["patch_preview"]["patches"][0]["operation"], "merge_conditions")
        self.assertEqual(result["apply_preview"]["applied_patches"][0]["operation"], "merge_conditions")

    def test_build_rule_pipeline_preview_sell_approved_creates_add_signal_patch_and_apply(self):
        preview = self._build_preview()
        session = self.mapper.build_rule_approval_session(
            preview,
            {"sell.signals.ui_preview_condition_c_macd_sell": "APPROVED"},
        )

        result = self.mapper.build_rule_pipeline_preview(self.current_rules, preview, session)

        self.assertEqual(result["patch_preview"]["patches"][0]["operation"], "add_signal")
        self.assertEqual(result["apply_preview"]["applied_patches"][0]["operation"], "add_signal")

    def test_build_rule_pipeline_preview_rejected_deferred_preview_only_do_not_patch(self):
        preview = self._build_preview()
        for decision in ("REJECTED", "DEFERRED", "APPLIED_PREVIEW_ONLY"):
            with self.subTest(decision=decision):
                session = self.mapper.build_rule_approval_session(
                    preview,
                    {"buy.groups[0].conditions": decision},
                )

                result = self.mapper.build_rule_pipeline_preview(self.current_rules, preview, session)

                self.assertEqual(result["patch_preview"]["patches"], [])
                self.assertEqual(result["apply_preview"]["applied_patches"], [])

    def test_rule_approval_session_and_pipeline_do_not_mutate_inputs(self):
        preview = self._build_preview()
        session = self.mapper.build_rule_approval_session(
            preview,
            {
                "buy.groups[0].conditions": "APPROVED",
                "sell.signals.ui_preview_condition_c_macd_sell": "APPROVED",
            },
        )
        original_rules = deepcopy(self.current_rules)
        original_preview = deepcopy(preview)
        original_session = deepcopy(session)

        self.mapper.build_rule_pipeline_preview(self.current_rules, preview, session)

        self.assertEqual(self.current_rules, original_rules)
        self.assertEqual(preview, original_preview)
        self.assertEqual(session, original_session)

    def test_rule_approval_session_and_pipeline_do_not_write_rules_json(self):
        before = self._rules_json_hash()
        preview = self._build_preview()
        session = self.mapper.build_rule_approval_session(
            preview,
            {"buy.groups[0].conditions": "APPROVED"},
        )

        self.mapper.update_rule_approval_session(
            session,
            "sell.signals.ui_preview_condition_c_macd_sell",
            "DEFERRED",
        )
        self.mapper.build_rule_pipeline_preview(self.current_rules, preview, session)

        self.assertEqual(before, self._rules_json_hash())

    def _build_commit_session(self, decisions):
        preview = self._build_preview()
        session = self.mapper.build_rule_approval_session(preview, decisions)
        fingerprint = self.mapper.build_rule_approval_session_fingerprint(self.current_rules, preview)
        session["fingerprint"] = fingerprint["fingerprint"]
        session["fingerprint_detail"] = fingerprint
        return preview, session

    def _save_commit_gate_session(self, session, session_path):
        result = rule_approval_session_file_service.save_rule_approval_session(session, session_path)
        self.assertTrue(result["saved"])
        return result

    def _rules_stable_hash(self, rules=None):
        return self.mapper._stable_hash(self.current_rules if rules is None else rules)

    def test_build_apply_preview_hash_is_stable_for_same_subset(self):
        preview, session = self._build_commit_session({"buy.groups[0].conditions": "APPROVED"})
        pipeline = self.mapper.build_rule_pipeline_preview(self.current_rules, preview, session)
        apply_preview = pipeline["apply_preview"]

        self.assertEqual(
            self.mapper.build_apply_preview_hash(apply_preview),
            self.mapper.build_apply_preview_hash(deepcopy(apply_preview)),
        )

    def test_build_apply_preview_hash_changes_when_applied_rules_preview_changes(self):
        preview, session = self._build_commit_session({"buy.groups[0].conditions": "APPROVED"})
        pipeline = self.mapper.build_rule_pipeline_preview(self.current_rules, preview, session)
        apply_preview = deepcopy(pipeline["apply_preview"])
        changed = deepcopy(apply_preview)
        changed["applied_rules_preview"]["buy"]["groups"][0]["conditions"].append({
            "target": "OSC",
            "operator": "<=",
            "value": -92.0,
        })

        self.assertNotEqual(
            self.mapper.build_apply_preview_hash(apply_preview),
            self.mapper.build_apply_preview_hash(changed),
        )

    def test_build_apply_preview_hash_ignores_warnings(self):
        preview, session = self._build_commit_session({"buy.groups[0].conditions": "APPROVED"})
        pipeline = self.mapper.build_rule_pipeline_preview(self.current_rules, preview, session)
        apply_preview = deepcopy(pipeline["apply_preview"])
        changed = deepcopy(apply_preview)
        changed["warnings"] = ["display-only warning"]

        self.assertEqual(
            self.mapper.build_apply_preview_hash(apply_preview),
            self.mapper.build_apply_preview_hash(changed),
        )

    def test_build_rule_commit_preview_pending_is_blocked(self):
        preview, session = self._build_commit_session({})

        result = self.mapper.build_rule_commit_preview(
            self.current_rules,
            preview,
            session,
            {"approval_session_dirty": False},
        )

        self.assertEqual(result["mode"], "rule_commit_preview")
        self.assertEqual(result["stage"], "RULE_COMMIT_PREVIEW")
        self.assertFalse(result["commit_allowed"])
        self.assertIn("approval session has no approved patches", result["blocked_reasons"])
        self.assertEqual(result["final_diff"], [])
        self.assertIn("apply_preview_hash", result)
        self.assertEqual(result["apply_preview_hash_algorithm"], "stable_json_sha256")

    def test_build_rule_commit_preview_buy_approved_builds_merge_diff(self):
        preview, session = self._build_commit_session({"buy.groups[0].conditions": "APPROVED"})

        result = self.mapper.build_rule_commit_preview(
            self.current_rules,
            preview,
            session,
            {"approval_session_dirty": False},
        )

        self.assertTrue(result["commit_allowed"])
        self.assertIn("apply_preview_hash", result)
        self.assertEqual(result["apply_preview_hash_algorithm"], "stable_json_sha256")
        self.assertEqual(len(result["final_diff"]), 1)
        diff = result["final_diff"][0]
        self.assertEqual(diff["path"], "buy.groups[0].conditions")
        self.assertEqual(diff["operation"], "merge_conditions")
        self.assertEqual(diff["change_type"], "add_condition")
        self.assertEqual(diff["condition"]["target"], "OSC")
        self.assertEqual(diff["condition"]["operator"], "<=")
        self.assertEqual(diff["condition"]["value"], -91.0)
        self.assertIn("buy.groups", diff["preserved"])
        self.assertFalse(diff["replace"])

    def test_build_rule_commit_preview_bar_approved_builds_set_value_diff(self):
        preview, session = self._build_commit_session({"bar.bar_minutes": "APPROVED"})

        result = self.mapper.build_rule_commit_preview(
            self.current_rules,
            preview,
            session,
            {"approval_session_dirty": False},
        )

        self.assertTrue(result["commit_allowed"])
        self.assertEqual(len(result["final_diff"]), 1)
        diff = result["final_diff"][0]
        self.assertEqual(diff["path"], "bar.bar_minutes")
        self.assertEqual(diff["operation"], "set_value")
        self.assertEqual(diff["change_type"], "set_bar_minutes")
        self.assertEqual(diff["value"], 5)
        self.assertFalse(diff["replace"])

    def test_build_rule_commit_preview_rsi_approved_builds_set_indicator_diff(self):
        preview, session = self._build_commit_session({"indicators.rsi": "APPROVED"})

        result = self.mapper.build_rule_commit_preview(
            self.current_rules,
            preview,
            session,
            {"approval_session_dirty": False},
        )

        self.assertTrue(result["commit_allowed"])
        self.assertEqual(len(result["final_diff"]), 1)
        diff = result["final_diff"][0]
        self.assertEqual(diff["path"], "indicators.rsi")
        self.assertEqual(diff["operation"], "set_indicator")
        self.assertEqual(diff["change_type"], "set_rsi_indicator")
        self.assertEqual(diff["value"], {"period": 14})
        self.assertFalse(diff["replace"])

    def test_build_rule_commit_preview_sell_approved_builds_add_signal_diff(self):
        preview, session = self._build_commit_session({
            "sell.signals.ui_preview_condition_c_macd_sell": "APPROVED",
        })

        result = self.mapper.build_rule_commit_preview(
            self.current_rules,
            preview,
            session,
            {"approval_session_dirty": False},
        )

        self.assertTrue(result["commit_allowed"])
        self.assertEqual(len(result["final_diff"]), 1)
        diff = result["final_diff"][0]
        self.assertEqual(diff["path"], "sell.signals.ui_condition_c_macd_sell")
        self.assertEqual(diff["operation"], "add_signal")
        self.assertEqual(diff["change_type"], "add_disabled_signal")
        self.assertFalse(diff["enabled"])
        self.assertIn("sell.signals.macd_sell", diff["preserved"])
        self.assertFalse(diff["replace"])

    def test_build_rule_commit_preview_buy_and_sell_approved_builds_two_diffs(self):
        preview, session = self._build_commit_session(
            {
                "buy.groups[0].conditions": "APPROVED",
                "sell.signals.ui_preview_condition_c_macd_sell": "APPROVED",
            }
        )

        result = self.mapper.build_rule_commit_preview(
            self.current_rules,
            preview,
            session,
            {"approval_session_dirty": False},
        )

        self.assertTrue(result["commit_allowed"])
        self.assertEqual(len(result["final_diff"]), 2)
        self.assertEqual(
            [diff["operation"] for diff in result["final_diff"]],
            ["merge_conditions", "add_signal"],
        )

    def test_build_rule_commit_preview_all_three_approved_builds_three_diffs(self):
        preview, session = self._build_commit_session(
            {
                "bar.bar_minutes": "APPROVED",
                "buy.groups[0].conditions": "APPROVED",
                "sell.signals.ui_preview_condition_c_macd_sell": "APPROVED",
            }
        )

        result = self.mapper.build_rule_commit_preview(
            self.current_rules,
            preview,
            session,
            {"approval_session_dirty": False},
        )

        self.assertTrue(result["commit_allowed"])
        self.assertEqual(
            [diff["path"] for diff in result["final_diff"]],
            [
                "bar.bar_minutes",
                "buy.groups[0].conditions",
                "sell.signals.ui_condition_c_macd_sell",
            ],
        )

    def test_build_rule_commit_preview_dirty_session_is_blocked(self):
        preview, session = self._build_commit_session({"buy.groups[0].conditions": "APPROVED"})

        result = self.mapper.build_rule_commit_preview(
            self.current_rules,
            preview,
            session,
            {"approval_session_dirty": True},
        )

        self.assertFalse(result["commit_allowed"])
        self.assertIn(
            "approval session has unsaved decision changes; save approval session before commit preview",
            result["blocked_reasons"],
        )

    def test_build_rule_commit_preview_fingerprint_mismatch_is_blocked(self):
        preview, session = self._build_commit_session({"buy.groups[0].conditions": "APPROVED"})
        session["fingerprint"] = "stale"

        result = self.mapper.build_rule_commit_preview(
            self.current_rules,
            preview,
            session,
            {"approval_session_dirty": False},
        )

        self.assertFalse(result["commit_allowed"])
        self.assertIn("approval session fingerprint does not match current preview", result["blocked_reasons"])

    def test_build_rule_commit_preview_target_path_conflict_is_blocked(self):
        preview = self._build_preview()
        current_rules = deepcopy(self.current_rules)
        current_rules["sell"]["signals"]["ui_condition_c_macd_sell"] = {"enabled": False}
        session = self.mapper.build_rule_approval_session(
            preview,
            {"sell.signals.ui_preview_condition_c_macd_sell": "APPROVED"},
        )
        fingerprint = self.mapper.build_rule_approval_session_fingerprint(current_rules, preview)
        session["fingerprint"] = fingerprint["fingerprint"]
        session["fingerprint_detail"] = fingerprint

        result = self.mapper.build_rule_commit_preview(
            current_rules,
            preview,
            session,
            {"approval_session_dirty": False},
        )

        self.assertFalse(result["commit_allowed"])
        self.assertIn("target path conflict", result["blocked_reasons"])

    def test_build_rule_commit_preview_safety_checks_are_false(self):
        preview, session = self._build_commit_session({
            "buy.groups[0].conditions": "APPROVED",
            "sell.signals.ui_preview_condition_c_macd_sell": "APPROVED",
        })

        result = self.mapper.build_rule_commit_preview(
            self.current_rules,
            preview,
            session,
            {"approval_session_dirty": False},
        )

        self.assertEqual(
            result["safety_checks"],
            {
                "rules_json_write": False,
                "engine_connected": False,
                "buy_groups_replace": False,
                "macd_sell_replace": False,
            },
        )
        self.assertTrue(all(diff["path"] != "buy.groups" for diff in result["final_diff"]))
        self.assertTrue(all(diff["path"] != "sell.signals.macd_sell" for diff in result["final_diff"]))

    def test_build_rule_commit_preview_does_not_mutate_inputs(self):
        preview, session = self._build_commit_session({
            "buy.groups[0].conditions": "APPROVED",
            "sell.signals.ui_preview_condition_c_macd_sell": "APPROVED",
        })
        original_rules = deepcopy(self.current_rules)
        original_preview = deepcopy(preview)
        original_session = deepcopy(session)

        self.mapper.build_rule_commit_preview(
            self.current_rules,
            preview,
            session,
            {"approval_session_dirty": False},
        )

        self.assertEqual(self.current_rules, original_rules)
        self.assertEqual(preview, original_preview)
        self.assertEqual(session, original_session)

    def test_build_rule_commit_preview_does_not_write_rules_json(self):
        before = self._rules_json_hash()
        preview, session = self._build_commit_session({
            "buy.groups[0].conditions": "APPROVED",
            "sell.signals.ui_preview_condition_c_macd_sell": "APPROVED",
        })

        self.mapper.build_rule_commit_preview(
            self.current_rules,
            preview,
            session,
            {"approval_session_dirty": False},
        )

        self.assertEqual(before, self._rules_json_hash())

    def test_evaluate_rule_commit_gate_missing_session_is_blocked(self):
        preview = self._build_preview()
        with tempfile.TemporaryDirectory() as temp_dir:
            session_path = Path(temp_dir) / "approval_session.json"

            result = self.mapper.evaluate_rule_commit_gate_from_saved_session(
                self.current_rules,
                preview,
                session_path,
                {
                    "expected_rules_hash": self._rules_stable_hash(),
                    "approval_session_dirty": False,
                    "manual_rule_commit_confirmed": True,
                },
            )

        self.assertFalse(result["commit_allowed"])
        self.assertIn("session file missing", result["blocked_reasons"])

    def test_evaluate_rule_commit_gate_corrupted_session_is_blocked(self):
        preview = self._build_preview()
        with tempfile.TemporaryDirectory() as temp_dir:
            session_path = Path(temp_dir) / "approval_session.json"
            session_path.write_text("{bad", encoding="utf-8")

            result = self.mapper.evaluate_rule_commit_gate_from_saved_session(
                self.current_rules,
                preview,
                session_path,
                {
                    "expected_rules_hash": self._rules_stable_hash(),
                    "approval_session_dirty": False,
                    "manual_rule_commit_confirmed": True,
                },
            )

        self.assertFalse(result["commit_allowed"])
        self.assertFalse(result["session_load"]["ok"])
        self.assertTrue(result["blocked_reasons"])

    def test_evaluate_rule_commit_gate_stale_session_is_blocked(self):
        preview, session = self._build_commit_session({"buy.groups[0].conditions": "APPROVED"})
        session["fingerprint"] = "stale"
        with tempfile.TemporaryDirectory() as temp_dir:
            session_path = Path(temp_dir) / "approval_session.json"
            self._save_commit_gate_session(session, session_path)

            result = self.mapper.evaluate_rule_commit_gate_from_saved_session(
                self.current_rules,
                preview,
                session_path,
                {
                    "expected_rules_hash": self._rules_stable_hash(),
                    "approval_session_dirty": False,
                    "manual_rule_commit_confirmed": True,
                },
            )

        self.assertFalse(result["commit_allowed"])
        self.assertIn(
            "saved approval session is stale; rerun validation and save approval session",
            result["blocked_reasons"],
        )

    def test_evaluate_rule_commit_gate_dirty_true_is_blocked(self):
        preview, session = self._build_commit_session({"buy.groups[0].conditions": "APPROVED"})
        with tempfile.TemporaryDirectory() as temp_dir:
            session_path = Path(temp_dir) / "approval_session.json"
            self._save_commit_gate_session(session, session_path)

            result = self.mapper.evaluate_rule_commit_gate_from_saved_session(
                self.current_rules,
                preview,
                session_path,
                {
                    "expected_rules_hash": self._rules_stable_hash(),
                    "approval_session_dirty": True,
                    "manual_rule_commit_confirmed": True,
                },
            )

        self.assertFalse(result["commit_allowed"])
        self.assertIn(
            "approval session has unsaved decision changes; save approval session before commit preview",
            result["blocked_reasons"],
        )

    def test_evaluate_rule_commit_gate_manual_confirmation_missing_is_blocked(self):
        preview, session = self._build_commit_session({"buy.groups[0].conditions": "APPROVED"})
        with tempfile.TemporaryDirectory() as temp_dir:
            session_path = Path(temp_dir) / "approval_session.json"
            self._save_commit_gate_session(session, session_path)

            result = self.mapper.evaluate_rule_commit_gate_from_saved_session(
                self.current_rules,
                preview,
                session_path,
                {
                    "expected_rules_hash": self._rules_stable_hash(),
                    "approval_session_dirty": False,
                    "manual_rule_commit_confirmed": False,
                },
            )

        self.assertFalse(result["commit_allowed"])
        self.assertIn("manual rule commit confirmation is required", result["blocked_reasons"])

    def test_evaluate_rule_commit_gate_expected_hash_missing_is_blocked(self):
        preview, session = self._build_commit_session({"buy.groups[0].conditions": "APPROVED"})
        with tempfile.TemporaryDirectory() as temp_dir:
            session_path = Path(temp_dir) / "approval_session.json"
            self._save_commit_gate_session(session, session_path)

            result = self.mapper.evaluate_rule_commit_gate_from_saved_session(
                self.current_rules,
                preview,
                session_path,
                {
                    "approval_session_dirty": False,
                    "manual_rule_commit_confirmed": True,
                },
            )

        self.assertFalse(result["commit_allowed"])
        self.assertIn("expected rules hash is required", result["blocked_reasons"])

    def test_evaluate_rule_commit_gate_rules_hash_mismatch_is_blocked(self):
        preview, session = self._build_commit_session({"buy.groups[0].conditions": "APPROVED"})
        with tempfile.TemporaryDirectory() as temp_dir:
            session_path = Path(temp_dir) / "approval_session.json"
            self._save_commit_gate_session(session, session_path)

            result = self.mapper.evaluate_rule_commit_gate_from_saved_session(
                self.current_rules,
                preview,
                session_path,
                {
                    "expected_rules_hash": "mismatch",
                    "approval_session_dirty": False,
                    "manual_rule_commit_confirmed": True,
                },
            )

        self.assertFalse(result["commit_allowed"])
        self.assertIn(
            "rules changed after commit preview; rerun validation and commit preview",
            result["blocked_reasons"],
        )

    def test_evaluate_rule_commit_gate_no_approved_patches_is_blocked(self):
        preview, session = self._build_commit_session({})
        with tempfile.TemporaryDirectory() as temp_dir:
            session_path = Path(temp_dir) / "approval_session.json"
            self._save_commit_gate_session(session, session_path)

            result = self.mapper.evaluate_rule_commit_gate_from_saved_session(
                self.current_rules,
                preview,
                session_path,
                {
                    "expected_rules_hash": self._rules_stable_hash(),
                    "approval_session_dirty": False,
                    "manual_rule_commit_confirmed": True,
                },
            )

        self.assertFalse(result["commit_allowed"])
        self.assertIn("approval session has no approved patches", result["blocked_reasons"])

    def test_evaluate_rule_commit_gate_target_conflict_is_blocked(self):
        preview = self._build_preview()
        current_rules = deepcopy(self.current_rules)
        current_rules["sell"]["signals"]["ui_condition_c_macd_sell"] = {"enabled": False}
        session = self.mapper.build_rule_approval_session(
            preview,
            {"sell.signals.ui_preview_condition_c_macd_sell": "APPROVED"},
        )
        fingerprint = self.mapper.build_rule_approval_session_fingerprint(current_rules, preview)
        session["fingerprint"] = fingerprint["fingerprint"]
        session["fingerprint_detail"] = fingerprint
        with tempfile.TemporaryDirectory() as temp_dir:
            session_path = Path(temp_dir) / "approval_session.json"
            self._save_commit_gate_session(session, session_path)

            result = self.mapper.evaluate_rule_commit_gate_from_saved_session(
                current_rules,
                preview,
                session_path,
                {
                    "expected_rules_hash": self._rules_stable_hash(current_rules),
                    "approval_session_dirty": False,
                    "manual_rule_commit_confirmed": True,
                },
            )

        self.assertFalse(result["commit_allowed"])
        self.assertIn("target path conflict", result["blocked_reasons"])

    def test_evaluate_rule_commit_gate_allows_valid_saved_session_with_manual_confirm(self):
        preview, session = self._build_commit_session({"buy.groups[0].conditions": "APPROVED"})
        with tempfile.TemporaryDirectory() as temp_dir:
            session_path = Path(temp_dir) / "approval_session.json"
            self._save_commit_gate_session(session, session_path)

            result = self.mapper.evaluate_rule_commit_gate_from_saved_session(
                self.current_rules,
                preview,
                session_path,
                {
                    "expected_rules_hash": self._rules_stable_hash(),
                    "approval_session_dirty": False,
                    "manual_rule_commit_confirmed": True,
                },
            )

        self.assertTrue(result["commit_allowed"])
        self.assertTrue(result["rules_hash_check"]["match"])
        self.assertTrue(result["manual_confirmation"])
        self.assertEqual(result["apply_preview_hash"], result["commit_preview"]["apply_preview_hash"])
        self.assertEqual(result["apply_preview_hash_algorithm"], "stable_json_sha256")
        self.assertEqual(result["session_restore"]["restore_status"], "RESTORED")
        self.assertGreaterEqual(len(result["commit_preview"]["final_diff"]), 1)

    def test_evaluate_rule_commit_gate_does_not_write_rules_json_or_call_save(self):
        before = self._rules_json_hash()
        preview, session = self._build_commit_session({"buy.groups[0].conditions": "APPROVED"})
        original_save = rule_approval_session_file_service.save_rule_approval_session
        calls = {"save": 0}

        with tempfile.TemporaryDirectory() as temp_dir:
            session_path = Path(temp_dir) / "approval_session.json"
            self._save_commit_gate_session(session, session_path)

            def fake_save(*args, **kwargs):
                calls["save"] += 1
                raise AssertionError("commit gate must not save approval sessions")

            rule_approval_session_file_service.save_rule_approval_session = fake_save
            try:
                self.mapper.evaluate_rule_commit_gate_from_saved_session(
                    self.current_rules,
                    preview,
                    session_path,
                    {
                        "expected_rules_hash": self._rules_stable_hash(),
                        "approval_session_dirty": False,
                        "manual_rule_commit_confirmed": True,
                    },
                )
            finally:
                rule_approval_session_file_service.save_rule_approval_session = original_save

        self.assertEqual(calls["save"], 0)
        self.assertEqual(before, self._rules_json_hash())

    def test_evaluate_rule_commit_gate_does_not_mutate_inputs(self):
        preview, session = self._build_commit_session({"buy.groups[0].conditions": "APPROVED"})
        original_rules = deepcopy(self.current_rules)
        original_preview = deepcopy(preview)
        with tempfile.TemporaryDirectory() as temp_dir:
            session_path = Path(temp_dir) / "approval_session.json"
            self._save_commit_gate_session(session, session_path)

            self.mapper.evaluate_rule_commit_gate_from_saved_session(
                self.current_rules,
                preview,
                session_path,
                {
                    "expected_rules_hash": self._rules_stable_hash(),
                    "approval_session_dirty": False,
                    "manual_rule_commit_confirmed": True,
                },
            )

        self.assertEqual(self.current_rules, original_rules)
        self.assertEqual(preview, original_preview)

    def test_rules_json_is_not_written_by_preview_compare_or_approval(self):
        before = self._rules_json_hash()
        preview = self._build_preview()
        self.mapper.compare_engine_rules_preview(self.current_rules, preview)
        self.mapper.approve_engine_rule_candidates(
            self.current_rules,
            preview,
            [
                "buy.groups[0].conditions",
                "sell.signals.ui_preview_condition_c_macd_sell",
            ],
        )

        self.assertEqual(before, self._rules_json_hash())


if __name__ == "__main__":
    unittest.main()
