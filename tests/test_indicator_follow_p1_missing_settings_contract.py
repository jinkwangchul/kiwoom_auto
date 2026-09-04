from __future__ import annotations

from copy import deepcopy
from importlib.util import module_from_spec, spec_from_file_location
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication

import gui_indicator_follow_routine_settings_dialog as dialog_module


ROOT = Path(__file__).resolve().parents[1]
ROUTINE_DIR = next((ROOT / "routines").glob("*/routine_rule_mapper.py")).parent


def _load(name: str, filename: str):
    spec = spec_from_file_location(name, ROUTINE_DIR / filename)
    module = module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class AdditionalNamespaceContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.rules_path = Path(self.temp.name) / "rules.json"
        self.rules_path.write_bytes((ROUTINE_DIR / "rules.json").read_bytes())
        self.dialog = dialog_module.IndicatorFollowRoutineSettingsDialog(rules_path=self.rules_path)

    def tearDown(self) -> None:
        self.dialog.close()
        self.dialog.deleteLater()
        self.temp.cleanup()

    def test_collect_keeps_price_skip_and_last_plus_one_independent(self) -> None:
        self.dialog.buy_price_compare_skip_check.setChecked(True)
        self.dialog.buy_price_compare_skip_direction_combo.setCurrentText("하향")
        self.dialog.buy_price_compare_skip_ratio_line.setText("1.25")
        self.dialog.buy_price_compare_skip_compare_combo.setCurrentText("이내")
        self.dialog.buy_additional_active_check.setChecked(True)
        self.dialog.buy_additional_active_method_combo.setCurrentText("능동")
        self.dialog.buy_additional_active_direction_combo.setCurrentText("상하")
        self.dialog.buy_additional_active_ratio_line.setText("0.77")
        self.dialog.buy_additional_active_compare_combo.setCurrentText("이탈")

        additional = self.dialog.collect_indicator_follow_ui_state()["buy_ui"]["additional"]

        self.assertEqual(True, additional["price_compare_skip"]["check"])
        self.assertEqual("하향", additional["price_compare_skip"]["direction_combo"])
        self.assertEqual("1.25", additional["price_compare_skip"]["ratio_line"])
        self.assertEqual("SKIP_CURRENT_GENERATION", additional["price_compare_skip"]["action"])
        self.assertEqual(True, additional["last_plus_one"]["check"])
        self.assertEqual("능동", additional["last_plus_one"]["method_combo"])
        self.assertEqual("상하", additional["last_plus_one"]["direction_combo"])
        self.assertEqual("0.77", additional["last_plus_one"]["ratio_line"])

    def test_new_nested_state_round_trip_has_no_collision(self) -> None:
        additional = {
            "price_compare_skip": {
                "check": True, "direction_combo": "하향", "ratio_line": "1.5",
                "compare_combo": "이내", "action": "SKIP_CURRENT_GENERATION",
            },
            "last_plus_one": {
                "check": True, "method_combo": "능동", "direction_combo": "상하",
                "ratio_line": "0.33", "compare_combo": "이탈",
            },
        }
        result = self.dialog.apply_indicator_follow_ui_state({"buy_ui": {"additional": additional}})
        collected = self.dialog.collect_indicator_follow_ui_state()["buy_ui"]["additional"]

        self.assertEqual([], result["compatibility_warnings"])
        self.assertEqual(additional, collected)

    def test_legacy_flat_recovers_only_provable_fields(self) -> None:
        legacy = {
            "check": True,
            "direction_combo": "하향",
            "ratio_line": "2.5",
            "compare_combo": "이내",
            "method_combo": "현재가",
        }
        normalized = dialog_module.normalize_buy_additional_ui_state(legacy)

        self.assertTrue(normalized["legacy_flat"])
        self.assertEqual(
            [dialog_module.LEGACY_ADDITIONAL_STATE_PARTIAL_LOSS],
            normalized["warnings"],
        )
        self.assertTrue(normalized["state"]["price_compare_skip"]["check"])
        self.assertEqual("현재가", normalized["state"]["last_plus_one"]["method_combo"])
        self.assertFalse(normalized["state"]["last_plus_one"]["check"])
        self.assertEqual("상향", normalized["state"]["last_plus_one"]["direction_combo"])
        self.assertEqual("0.45", normalized["state"]["last_plus_one"]["ratio_line"])

    def test_legacy_load_does_not_rewrite_disk(self) -> None:
        before = hashlib.sha256(self.rules_path.read_bytes()).hexdigest()
        result = self.dialog.apply_indicator_follow_ui_state({
            "buy_ui": {"additional": {
                "check": True, "direction_combo": "하향", "ratio_line": "2",
                "compare_combo": "이하", "method_combo": "능동",
            }}
        })
        after = hashlib.sha256(self.rules_path.read_bytes()).hexdigest()

        self.assertEqual(before, after)
        self.assertEqual(
            [dialog_module.LEGACY_ADDITIONAL_STATE_PARTIAL_LOSS],
            result["compatibility_warnings"],
        )
        self.assertFalse(self.dialog.buy_additional_active_check.isChecked())

    def test_normal_save_writes_nested_schema_and_reload_is_identical(self) -> None:
        self.dialog.buy_price_compare_skip_check.setChecked(True)
        self.dialog.buy_price_compare_skip_ratio_line.setText("0.8")
        self.dialog.buy_additional_active_method_combo.setCurrentText("현재가")
        expected = self.dialog.collect_indicator_follow_ui_state()["buy_ui"]["additional"]

        saved = self.dialog.save_indicator_follow_ui_state_to_rules()
        payload = json.loads(self.rules_path.read_text(encoding="utf-8"))
        actual = payload["indicator_follow_ui_state"]["state"]["buy_ui"]["additional"]

        self.assertTrue(saved["success"], saved)
        self.assertEqual(expected, actual)
        self.assertEqual({"price_compare_skip", "last_plus_one"}, set(actual))

    def test_cycle_round_trip_remains_full(self) -> None:
        state = self.dialog.collect_indicator_follow_ui_state()
        cycle = deepcopy(state["buy_ui"]["cycle"])
        self.dialog.buy_cycle_ratio_value_line.setText("9.9")
        result = self.dialog.apply_indicator_follow_ui_state({"buy_ui": {"cycle": cycle}})
        collected = self.dialog.collect_indicator_follow_ui_state()["buy_ui"]["cycle"]

        self.assertFalse(result["sync_errors"])
        self.assertEqual(cycle, collected)


class MapperContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.mapper = _load("p1_mapper", "routine_rule_mapper.py")
        self.validator = _load("p1_validator", "routine_rule_commit_validator.py")
        self.rules = {
            "bar": {"bar_minutes": 1},
            "buy": {"groups": [{"conditions": []}]},
            "sell": {"signals": {}},
            "indicators": {"rsi": {"period": 14}},
        }

    def _preview(self, buy_ui: dict) -> dict:
        return self.mapper.build_engine_rules_preview_from_ui_state(
            {"basic": {"basic_signal_interval_combo": "1"}, "buy_ui": buy_ui},
            deepcopy(self.rules),
        )

    def _additional(self, *, price=False, last=False, method="시장가") -> dict:
        return {
            "price_compare_skip": {
                "check": price, "direction_combo": "하향", "ratio_line": "0.5",
                "compare_combo": "이하", "action": "SKIP_CURRENT_GENERATION",
            },
            "last_plus_one": {
                "check": last, "method_combo": method, "direction_combo": "상하",
                "ratio_line": "0.45", "compare_combo": "이상",
            },
        }

    def _base(self, *, point="다중시간", active=False) -> dict:
        return {
            "hoga_combo": "단일호가", "order_combo": "현재가", "up_line": "0", "down_line": "0",
            "time_mode_combo": point, "time_value_line": "30", "time_unit_combo": "초",
            "time_range_combo": "간격", "time_count_line": "3", "time_order_combo": "현재가",
            "ratio_left_combo": "주문가", "ratio_right_combo": "현재가",
            "ratio_direction_combo": "상향", "ratio_value_line": "0.15",
            "ratio_compare_combo": "이상", "ratio_count_line": "3",
            "last_round_active_buy": {
                "enabled": active, "direction": "상향", "ratio_percent": "0.45", "comparator": "이상",
            },
        }

    def _cycle(self) -> dict:
        return {
            "buy_cycle_hoga_mode_combo": "단일호가",
            "buy_cycle_order_combo": "현재가",
            "buy_cycle_hoga_up_line": "0",
            "buy_cycle_hoga_down_line": "2",
            "buy_cycle_time_mode_combo": "다중비율",
            "buy_cycle_time_value_line": "30",
            "buy_cycle_time_unit_combo": "초",
            "buy_cycle_time_range_combo": "이내",
            "buy_cycle_time_count_line": "3",
            "buy_cycle_time_order_combo": "현재가",
            "buy_cycle_ratio_left_combo": "주문가",
            "buy_cycle_ratio_right_combo": "현재가",
            "buy_cycle_ratio_direction_combo": "상향",
            "buy_cycle_ratio_value_line": "0.15",
            "buy_cycle_ratio_compare_combo": "이상",
            "buy_cycle_ratio_count_line": "3",
            "buy_cycle_situation_mode_combo": "가격비교",
            "buy_cycle_price_left_combo": "주문가",
            "buy_cycle_price_right_combo": "현재가",
            "buy_cycle_price_direction_combo": "상향",
            "buy_cycle_price_value_line": "0.15",
            "buy_cycle_price_compare_combo": "이상",
            "buy_cycle_price_action_combo": "일괄취소",
        }

    def _execution_candidate(self, preview: dict, key: str) -> dict:
        return preview["preview_rules"]["indicator_follow_rule_preview"]["candidates"]["execution"][key]

    def test_previous_round_price_skip_canonical_contract(self) -> None:
        preview = self._preview({"additional": self._additional(price=True)})
        candidate = self._execution_candidate(preview, "additional")
        policy = candidate["value"]["previous_round_price_skip"]

        self.assertEqual("PREVIOUS_CONFIRMED_BUY_ORDER_PRICE", policy["reference_source"])
        self.assertEqual("ACTIONABLE_ORDER_PRICE", policy["current_source"])
        self.assertEqual("SKIP_CURRENT_GENERATION", policy["action"])
        self.assertFalse(policy["skipped_round_increment"])
        self.assertTrue(candidate["execution_connected"])

    def test_last_plus_one_three_methods_and_active_detail(self) -> None:
        for text, token in (("시장가", "MARKET"), ("현재가", "CURRENT_PRICE"), ("능동", "ACTIVE")):
            with self.subTest(text=text):
                preview = self._preview({"additional": self._additional(last=True, method=text)})
                policy = self._execution_candidate(preview, "additional")["value"]["last_plus_one"]
                self.assertEqual(token, policy["method"])
                self.assertEqual("LAST_PLUS_ONE", policy["generation_kind"])
                self.assertEqual(1, policy["max_occurrences"])
                self.assertEqual("LAST_NORMAL_ROUND_APPROVED_BUDGET", policy["budget_basis"])
                self.assertTrue(policy["terminal_after_completed_fill"])
                if token == "ACTIVE":
                    self.assertEqual("AVERAGE_PRICE", policy["active_condition"]["rhs_source"])

    def test_additional_invalid_comparator_and_method_fail_closed(self) -> None:
        invalid_price = self._additional(price=True)
        invalid_price["price_compare_skip"]["compare_combo"] = "UNKNOWN"
        invalid_last = self._additional(last=True)
        invalid_last["last_plus_one"]["method_combo"] = "UNKNOWN"

        price_preview = self._preview({"additional": invalid_price})
        last_preview = self._preview({"additional": invalid_last})

        self.assertNotIn("buy.execution.additional", price_preview["mapped_paths"])
        self.assertNotIn("buy.execution.additional", last_preview["mapped_paths"])
        self.assertTrue(any("invalid" in item for item in price_preview["validation_warnings"]))
        self.assertTrue(any("invalid" in item for item in last_preview["validation_warnings"]))

    def test_last_round_active_is_dedicated_base_policy(self) -> None:
        preview = self._preview({"base": self._base(active=True)})
        candidate = self._execution_candidate(preview, "base")
        policy = candidate["value"]["last_round_active_buy"]

        self.assertEqual("LAST_MULTI_POINT_CHILD", policy["applies_to"])
        self.assertEqual("BUY_METHOD_SPECIAL_ACTION", policy["purpose"])
        self.assertEqual("AVERAGE_PRICE", policy["subject"])
        self.assertEqual("MULTI_POINT_SET_PRICE", policy["reference"])
        self.assertTrue(candidate["execution_connected"])
        self.assertNotIn("last_round_active_buy", candidate["value"].get("repeat", {}))

    def test_last_round_active_defaults_disabled_and_requires_multi_point(self) -> None:
        default_preview = self._preview({"base": self._base(active=False)})
        invalid_preview = self._preview({"base": self._base(point="선택없음", active=True)})

        self.assertFalse(
            self._execution_candidate(default_preview, "base")["value"]["last_round_active_buy"]["enabled"]
        )
        self.assertNotIn("buy.execution.base", invalid_preview["mapped_paths"])

    def test_cycle_is_signal_scoped_and_execution_locked(self) -> None:
        preview = self._preview({"cycle": self._cycle()})
        candidate = self._execution_candidate(preview, "cycle")
        policy = candidate["value"]

        self.assertEqual("SIGNAL_SCOPED_BUY_CYCLE", policy["scope"])
        self.assertTrue(policy["requires_source_signal"])
        self.assertFalse(policy["autonomous_scheduler"])
        self.assertEqual("REQUIRE_NEW_BUY_SIGNAL", policy["after_cycle_completion"])
        self.assertEqual("MULTI_RATIO", policy["point_policy"]["mode"])
        self.assertEqual("CANCEL_BATCH", policy["situation_response"]["action"])
        self.assertFalse(candidate["execution_connected"])

    def test_cycle_reset_option_is_connected_and_committable(self) -> None:
        cycle = self._cycle()
        cycle["buy_cycle_price_action_combo"] = "매수리셋"
        preview = self._preview({"cycle": cycle})
        candidate = self._execution_candidate(preview, "cycle")
        self.assertTrue(candidate["execution_connected"])
        self.assertEqual("", candidate["execution_lock_reason"])

        session = self.mapper.build_rule_approval_session(
            preview,
            {"buy.execution.cycle": "APPROVED"},
        )
        pipeline = self.mapper.build_rule_pipeline_preview(self.rules, preview, session)
        self.assertEqual(
            ["buy.execution.cycle"],
            [item["target_path"] for item in pipeline["patch_preview"]["patches"]],
        )
        post = deepcopy(self.rules)
        post.setdefault("buy", {}).setdefault("execution", {})["cycle"] = deepcopy(candidate["value"])
        validated = self.validator.validate_committed_rules(
            self.rules,
            post,
            [{"operation": "set_execution_policy", "path": "buy.execution.cycle", "value": candidate["value"]}],
            {"rules_json_write": False, "engine_connected": False, "buy_groups_replace": False, "macd_sell_replace": False},
        )
        self.assertTrue(validated["ok"], validated)

    def test_preview_keeps_three_active_concepts_separate(self) -> None:
        preview = self._preview({
            "base": self._base(active=True),
            "repeat": {"apply_all_check": True, "detail_mode_combo": "능동매수"},
            "additional": self._additional(last=True, method="능동"),
        })
        execution = preview["preview_rules"]["indicator_follow_rule_preview"]["candidates"]["execution"]

        self.assertEqual("ACTIVE_BUY", execution["repeat"]["value"]["detail_mode"])
        self.assertEqual("ACTIVE", execution["additional"]["value"]["last_plus_one"]["method"])
        self.assertTrue(execution["base"]["value"]["last_round_active_buy"]["enabled"])

    def test_stale_situation_and_exit_postponed_messages_are_removed(self) -> None:
        preview = self._preview({})
        text = "\n".join(preview["postponed"])

        self.assertNotIn("situation price response", text)
        self.assertNotIn("exit condition", text)
        self.assertNotIn("additional feature", text)
        self.assertNotIn("cycle setting", text)

    def test_connected_additional_can_commit_while_unsupported_cycle_stays_locked(self) -> None:
        preview = self._preview({"additional": self._additional(price=True), "cycle": self._cycle()})
        session = self.mapper.build_rule_approval_session(preview, {
            "buy.execution.additional": "APPROVED",
            "buy.execution.cycle": "APPROVED",
        })
        pipeline = self.mapper.build_rule_pipeline_preview(self.rules, preview, session)

        self.assertEqual(
            ["buy.execution.additional"],
            [item["target_path"] for item in pipeline["patch_preview"]["patches"]],
        )
        reasons = {item["reason"] for item in pipeline["patch_preview"]["skipped_paths"]}
        self.assertIn(self.mapper.CYCLE_OPTION_EXECUTION_LOCK_REASON, reasons)
        self.assertEqual(
            ["buy.execution.additional"],
            [item["target_path"] for item in pipeline["apply_preview"]["applied_patches"]],
        )

    def test_validator_accepts_connected_enabled_additional(self) -> None:
        preview = self._preview({"additional": self._additional(price=True)})
        value = self._execution_candidate(preview, "additional")["value"]
        post = deepcopy(self.rules)
        post["buy"].setdefault("execution", {})["additional"] = deepcopy(value)
        result = self.validator.validate_committed_rules(
            self.rules,
            post,
            [{"operation": "set_execution_policy", "path": "buy.execution.additional", "value": value}],
            {"rules_json_write": False, "engine_connected": False, "buy_groups_replace": False, "macd_sell_replace": False},
        )

        self.assertTrue(result["ok"], result)
        self.assertEqual([], result["unexpected_changes"])

    def test_mapper_files_have_no_broker_or_mock_dependency(self) -> None:
        combined = "\n".join(
            (ROUTINE_DIR / filename).read_text(encoding="utf-8")
            for filename in ("routine_rule_mapper.py", "routine_rule_commit_validator.py")
        )
        for forbidden in ("SendOrder", "routine_signal_queue", "mock_validation_", "real_trade_enabled"):
            self.assertNotIn(forbidden, combined)


if __name__ == "__main__":
    unittest.main()
