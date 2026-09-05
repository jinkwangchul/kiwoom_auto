# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
from importlib.util import module_from_spec, spec_from_file_location
import json
import os
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication

import gui_indicator_follow_routine_settings_dialog as dialog_module
from tests import test_indicator_follow_buy_execution_connection as buy_helper_module


ROOT = Path(__file__).resolve().parents[1]
ROUTINE_DIR = next((ROOT / "routines").glob("*/routine_rule_mapper.py")).parent


def _load(name: str, filename: str):
    spec = spec_from_file_location(name, ROUTINE_DIR / filename)
    module = module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class ConnectedBuyUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.rules_path = Path(self.temp.name) / "rules.json"
        self.rules_path.write_bytes((ROUTINE_DIR / "rules.json").read_bytes())
        self.dialog = dialog_module.IndicatorFollowRoutineSettingsDialog(
            rules_path=self.rules_path
        )
        self.mapper = _load("p3_mapper", "routine_rule_mapper.py")
        self.validator = _load("p3_validator", "routine_rule_commit_validator.py")
        self.base_rules = {
            "bar": {"bar_minutes": 1},
            "buy": {"groups": [{"conditions": []}]},
            "sell": {"signals": {}},
            "indicators": {"rsi": {"period": 14}},
        }

    def tearDown(self) -> None:
        self.dialog.close()
        self.dialog.deleteLater()
        self.temp.cleanup()

    def _state(self) -> dict:
        return self.dialog.collect_indicator_follow_ui_state()

    def _preview(self) -> dict:
        return self.mapper.build_engine_rules_preview_from_ui_state(
            self._state(),
            deepcopy(self.base_rules),
        )

    def _candidate(self, key: str) -> dict:
        return self._preview()["preview_rules"]["indicator_follow_rule_preview"][
            "candidates"
        ]["execution"][key]

    def test_previous_price_and_last_plus_one_state_controls_are_independent(self) -> None:
        self.assertTrue(self.dialog.buy_price_compare_skip_check.isEnabled())
        self.assertFalse(self.dialog.buy_price_compare_skip_direction_combo.isEnabled())
        self.assertTrue(self.dialog.buy_additional_active_check.isEnabled())
        self.assertFalse(self.dialog.buy_additional_active_method_combo.isEnabled())

        self.dialog.buy_price_compare_skip_check.setChecked(True)
        self.assertTrue(self.dialog.buy_price_compare_skip_direction_combo.isEnabled())
        self.assertFalse(self.dialog.buy_additional_active_method_combo.isEnabled())

        self.dialog.buy_additional_active_check.setChecked(True)
        self.assertTrue(self.dialog.buy_additional_active_method_combo.isEnabled())
        for method, detail_enabled in (("시장가", False), ("현재가", False), ("능동", True)):
            with self.subTest(method=method):
                self.dialog.buy_additional_active_method_combo.setCurrentText(method)
                self.assertEqual(
                    detail_enabled,
                    self.dialog.buy_additional_active_direction_combo.isEnabled(),
                )

    def test_additional_save_load_round_trip_keeps_namespaces_separate(self) -> None:
        self.dialog.buy_price_compare_skip_check.setChecked(True)
        self.dialog.buy_price_compare_skip_direction_combo.setCurrentText("하향")
        self.dialog.buy_price_compare_skip_ratio_line.setText("1.25")
        self.dialog.buy_price_compare_skip_compare_combo.setCurrentText("이내")
        self.dialog.buy_additional_active_check.setChecked(True)
        self.dialog.buy_additional_active_method_combo.setCurrentText("능동")
        self.dialog.buy_additional_active_direction_combo.setCurrentText("상하")
        self.dialog.buy_additional_active_ratio_line.setText("0.77")
        self.dialog.buy_additional_active_compare_combo.setCurrentText("이탈")
        expected = deepcopy(self._state()["buy_ui"]["additional"])

        saved = self.dialog.save_indicator_follow_ui_state_to_rules()
        self.assertTrue(saved["success"], saved)
        self.dialog.buy_price_compare_skip_ratio_line.setText("9")
        self.dialog.buy_additional_active_ratio_line.setText("8")
        payload = json.loads(self.rules_path.read_text(encoding="utf-8"))
        applied = self.dialog.apply_indicator_follow_ui_state(
            payload["indicator_follow_ui_state"]["state"]
        )

        self.assertFalse(applied["sync_errors"])
        self.assertEqual(expected, self._state()["buy_ui"]["additional"])

    def test_last_round_active_prerequisite_preserves_checked_value(self) -> None:
        self.dialog.buy_base_time_mode_combo.setCurrentText("선택없음")
        self.assertFalse(self.dialog.buy_last_round_active_check.isEnabled())
        self.dialog.buy_last_round_active_check.setChecked(True)
        state = self._state()["buy_ui"]["base"]["last_round_active_buy"]
        self.assertTrue(state["checked"])
        self.assertFalse(state["enabled"])

        for mode in ("다중시간", "다중비율"):
            with self.subTest(mode=mode):
                self.dialog.buy_base_time_mode_combo.setCurrentText(mode)
                self.assertTrue(self.dialog.buy_last_round_active_check.isEnabled())
                self.assertTrue(self.dialog.buy_last_round_active_direction_combo.isEnabled())
                self.assertTrue(
                    self._state()["buy_ui"]["base"]["last_round_active_buy"]["enabled"]
                )

        self.dialog.buy_base_time_mode_combo.setCurrentText("선택없음")
        self.assertTrue(self.dialog.buy_last_round_active_check.isChecked())
        self.assertFalse(self.dialog.buy_last_round_active_direction_combo.isEnabled())

    def test_last_round_active_direction_uses_common_comparator_rule(self) -> None:
        self.dialog.buy_base_time_mode_combo.setCurrentText("다중시간")
        self.dialog.buy_last_round_active_check.setChecked(True)
        self.dialog.buy_last_round_active_direction_combo.setCurrentText("상하")
        self.assertEqual("이내", self.dialog.buy_last_round_active_compare_combo.currentText())
        hidden = self.dialog.buy_last_round_active_compare_combo.view().isRowHidden
        self.assertTrue(hidden(self.dialog.buy_last_round_active_compare_combo.findText("이상")))
        self.assertFalse(hidden(self.dialog.buy_last_round_active_compare_combo.findText("이내")))
        self.dialog.buy_last_round_active_direction_combo.setCurrentText("하향")
        self.assertEqual("이상", self.dialog.buy_last_round_active_compare_combo.currentText())

    def test_base_multi_ratio_direction_uses_common_comparator_rule(self) -> None:
        direction = self.dialog.buy_base_ratio_direction_combo
        comparator = self.dialog.buy_base_ratio_compare_combo

        for direction_text, allowed in (
            ("상향", {"이상", "이하"}),
            ("하향", {"이상", "이하"}),
            ("상하", {"이내", "이탈"}),
        ):
            with self.subTest(direction=direction_text):
                direction.setCurrentText(direction_text)
                visible = {
                    text
                    for text in ("이상", "이하", "이내", "이탈")
                    if not comparator.view().isRowHidden(comparator.findText(text))
                }
                self.assertEqual(allowed, visible)
                self.assertIn(comparator.currentText(), allowed)

        direction.setCurrentText("상하")
        comparator.setCurrentText("이탈")
        direction.setCurrentText("상향")
        self.assertEqual("이상", comparator.currentText())

        comparator.setCurrentText("이하")
        direction.setCurrentText("상하")
        self.assertEqual("이내", comparator.currentText())

    def test_base_multi_ratio_round_trip_and_legacy_apply_normalize_pair(self) -> None:
        self.dialog.buy_base_time_mode_combo.setCurrentText("다중비율")
        self.dialog.buy_base_ratio_direction_combo.setCurrentText("상하")
        self.dialog.buy_base_ratio_compare_combo.setCurrentText("이탈")
        expected = deepcopy(self._state()["buy_ui"]["base"])

        saved = self.dialog.save_indicator_follow_ui_state_to_rules()
        self.assertTrue(saved["success"], saved)
        payload = json.loads(self.rules_path.read_text(encoding="utf-8"))
        self.dialog.buy_base_ratio_direction_combo.setCurrentText("상향")
        applied = self.dialog.apply_indicator_follow_ui_state(
            payload["indicator_follow_ui_state"]["state"]
        )
        self.assertFalse(applied["sync_errors"])
        self.assertEqual(expected, self._state()["buy_ui"]["base"])

        legacy = deepcopy(self._state())
        legacy["buy_ui"]["base"].update({
            "ratio_direction_combo": "상향",
            "ratio_compare_combo": "이탈",
        })
        before = self.rules_path.read_bytes()
        applied = self.dialog.apply_indicator_follow_ui_state(legacy)
        self.assertFalse(applied["sync_errors"])
        self.assertEqual("상향", self.dialog.buy_base_ratio_direction_combo.currentText())
        self.assertEqual("이상", self.dialog.buy_base_ratio_compare_combo.currentText())
        self.assertEqual(before, self.rules_path.read_bytes())

    def test_base_multi_ratio_mapper_and_validator_guard_direction_comparator_pair(self) -> None:
        base_state = self._state()
        valid_pairs = (
            ("상향", "이상", "UP", ">="),
            ("상향", "이하", "UP", "<="),
            ("하향", "이상", "DOWN", ">="),
            ("하향", "이하", "DOWN", "<="),
            ("상하", "이내", "BOTH", "WITHIN"),
            ("상하", "이탈", "BOTH", "OUTSIDE"),
        )
        valid_rules = None
        for direction, comparator, expected_direction, expected_comparator in valid_pairs:
            with self.subTest(valid=(direction, comparator)):
                state = deepcopy(base_state)
                state["buy_ui"]["base"].update({
                    "time_mode_combo": "다중비율",
                    "ratio_direction_combo": direction,
                    "ratio_compare_combo": comparator,
                })
                preview = self.mapper.build_engine_rules_preview_from_ui_state(
                    state, deepcopy(self.base_rules)
                )
                candidate = preview["preview_rules"]["indicator_follow_rule_preview"][
                    "candidates"
                ]["execution"]["base"]
                self.assertEqual(expected_direction, candidate["value"]["ratio_direction"])
                self.assertEqual(expected_comparator, candidate["value"]["ratio_compare"])
                rules = preview["preview_rules"]
                validation = self.validator.validate_committed_rules(
                    deepcopy(rules), rules, [], {}
                )
                checks = {item["name"]: item["ok"] for item in validation["checks"]}
                self.assertTrue(checks["buy_base_multi_ratio_direction_comparator_valid"])
                valid_rules = rules

        self.assertIsNotNone(valid_rules)
        for direction, comparator in (
            ("상향", "이내"),
            ("상향", "이탈"),
            ("하향", "이내"),
            ("하향", "이탈"),
            ("상하", "이상"),
            ("상하", "이하"),
        ):
            with self.subTest(invalid=(direction, comparator)):
                state = deepcopy(base_state)
                state["buy_ui"]["base"].update({
                    "time_mode_combo": "다중비율",
                    "ratio_direction_combo": direction,
                    "ratio_compare_combo": comparator,
                })
                preview = self.mapper.build_engine_rules_preview_from_ui_state(
                    state, deepcopy(self.base_rules)
                )
                candidates = preview["preview_rules"]["indicator_follow_rule_preview"][
                    "candidates"
                ].get("execution", {})
                self.assertNotIn("base", candidates)
                self.assertIn(
                    "buy base MULTI_RATIO direction/comparator pair is invalid",
                    preview["validation_warnings"],
                )

                rules = deepcopy(valid_rules)
                policy = rules["buy"]["execution"]["base"]
                policy.update({
                    "ratio_direction": self.mapper._direction_token(direction),
                    "ratio_compare": self.mapper._ratio_compare_token(comparator),
                })
                validation = self.validator.validate_committed_rules(
                    deepcopy(rules), rules, [], {}
                )
                checks = {item["name"]: item["ok"] for item in validation["checks"]}
                self.assertFalse(checks["buy_base_multi_ratio_direction_comparator_valid"])

        legacy_rules = deepcopy(self.base_rules)
        legacy_rules.setdefault("buy", {}).setdefault("execution", {})["base"] = deepcopy(
            valid_rules["buy"]["execution"]["base"]
        )
        invalid_state = deepcopy(base_state)
        invalid_state["buy_ui"]["base"].update({
            "time_mode_combo": "다중비율",
            "ratio_direction_combo": "상향",
            "ratio_compare_combo": "이탈",
        })
        legacy_preview = self.mapper.build_engine_rules_preview_from_ui_state(
            invalid_state, legacy_rules
        )
        legacy_candidates = legacy_preview["preview_rules"][
            "indicator_follow_rule_preview"
        ]["candidates"].get("execution", {})
        self.assertNotIn("base", legacy_candidates)

    def test_situation_response_direction_uses_common_comparator_rule(self) -> None:
        self.dialog.buy_situation_response_setting2_left_combo.setCurrentText("주문가")
        for slot in ("setting1", "setting2"):
            direction = getattr(self.dialog, f"buy_situation_response_{slot}_direction_combo")
            comparator = getattr(self.dialog, f"buy_situation_response_{slot}_compare_combo")
            for direction_text, allowed in (
                ("상향", {"이상", "이하"}),
                ("하향", {"이상", "이하"}),
                ("상하", {"이내", "이탈"}),
            ):
                with self.subTest(slot=slot, direction=direction_text):
                    direction.setCurrentText(direction_text)
                    visible = {
                        text
                        for text in ("이상", "이하", "이내", "이탈")
                        if not comparator.view().isRowHidden(comparator.findText(text))
                    }
                    self.assertEqual(allowed, visible)
                    self.assertIn(comparator.currentText(), allowed)

    def test_situation_response_round_trip_and_legacy_apply_normalize_pair(self) -> None:
        self.assertFalse(hasattr(self.dialog, "buy_situation_response_type_combo"))
        self.dialog.buy_situation_response_unfilled_enabled_check.setChecked(False)
        self.dialog.buy_situation_response_price_enabled_check.setChecked(False)
        self.dialog.buy_situation_response_unfilled_enabled_check.setChecked(True)
        self.assertFalse(self.dialog.buy_situation_response_price_enabled_check.isChecked())
        self.dialog.buy_situation_response_price_enabled_check.setChecked(True)
        self.assertTrue(self.dialog.buy_situation_response_unfilled_enabled_check.isChecked())
        self.dialog.buy_situation_response_setting2_left_combo.setCurrentText("주문가")
        self.dialog.buy_situation_response_setting1_direction_combo.setCurrentText("상하")
        self.dialog.buy_situation_response_setting1_compare_combo.setCurrentText("이탈")
        self.dialog.buy_situation_response_setting1_action_combo.setCurrentText("매수리셋")
        self.dialog.buy_situation_response_setting2_direction_combo.setCurrentText("하향")
        self.dialog.buy_situation_response_setting2_compare_combo.setCurrentText("이하")
        expected = deepcopy(self._state()["buy_ui"]["situation"])

        saved = self.dialog.save_indicator_follow_ui_state_to_rules()
        self.assertTrue(saved["success"], saved)
        payload = json.loads(self.rules_path.read_text(encoding="utf-8"))
        self.dialog.buy_situation_response_setting1_direction_combo.setCurrentText("상향")
        applied = self.dialog.apply_indicator_follow_ui_state(
            payload["indicator_follow_ui_state"]["state"]
        )
        self.assertFalse(applied["sync_errors"])
        self.assertEqual(expected, self._state()["buy_ui"]["situation"])

        legacy = deepcopy(self._state())
        legacy["buy_ui"]["situation"] = {
            "type_combo": "가격비교",
            "left_combo": "주문가",
            "right_combo": "현재가",
            "direction_combo": "상향",
            "ratio_line": "0.4",
            "compare_combo": "이상",
            "action_combo": "매수리셋",
        }
        before = self.rules_path.read_bytes()
        applied = self.dialog.apply_indicator_follow_ui_state(legacy)
        self.assertFalse(applied["sync_errors"])
        self.assertEqual("상향", self.dialog.buy_situation_response_setting1_direction_combo.currentText())
        self.assertEqual("이상", self.dialog.buy_situation_response_setting1_compare_combo.currentText())
        self.assertFalse(self.dialog.buy_situation_response_setting2_enabled_check.isChecked())
        self.assertEqual("무설정", self.dialog.buy_situation_response_setting2_left_combo.currentText())
        self.assertEqual(before, self.rules_path.read_bytes())

    def test_cycle_section_is_above_exit_conditions(self) -> None:
        layout = self.dialog.buy_overview_finish.layout()
        cycle_index = layout.indexOf(self.dialog.buy_cycle_column_widget)
        finish_index = layout.indexOf(self.dialog.buy_finish_column_widget)
        self.assertGreaterEqual(cycle_index, 0)
        self.assertGreaterEqual(finish_index, 0)
        cycle_row, cycle_column, _, _ = layout.getItemPosition(cycle_index)
        finish_row, finish_column, _, _ = layout.getItemPosition(finish_index)
        self.assertEqual((0, 0), (cycle_row, cycle_column))
        self.assertEqual((1, 0), (finish_row, finish_column))
        self.assertEqual(
            28,
            self.dialog.buy_situation_response_price_slots_widget.layout().contentsMargins().left(),
        )

    def test_situation_response_mapper_accepts_six_pairs_and_blocks_invalid_pairs(self) -> None:
        base_state = self._state()
        valid_pairs = (
            ("상향", "이상", "UP", ">="),
            ("상향", "이하", "UP", "<="),
            ("하향", "이상", "DOWN", ">="),
            ("하향", "이하", "DOWN", "<="),
            ("상하", "이내", "BOTH", "WITHIN"),
            ("상하", "이탈", "BOTH", "OUTSIDE"),
        )
        for direction, comparator, expected_direction, expected_comparator in valid_pairs:
            with self.subTest(valid=(direction, comparator)):
                state = deepcopy(base_state)
                state["buy_ui"]["situation"].update({
                    "price_enabled_check": True,
                    "setting1_enabled_check": True,
                    "setting2_enabled_check": False,
                    "setting1_action_combo": "매수리셋",
                    "setting1_direction_combo": direction,
                    "setting1_compare_combo": comparator,
                })
                preview = self.mapper.build_engine_rules_preview_from_ui_state(
                    state, deepcopy(self.base_rules)
                )
                candidate = preview["preview_rules"]["indicator_follow_rule_preview"][
                    "candidates"
                ]["execution"]["base"]
                policy = candidate["value"]["buy_price_response_policies"][0]
                self.assertEqual(expected_direction, policy["direction"])
                self.assertEqual(expected_comparator, policy["compare"])

        for direction, comparator in (
            ("상향", "이내"),
            ("상향", "이탈"),
            ("하향", "이내"),
            ("하향", "이탈"),
            ("상하", "이상"),
            ("상하", "이하"),
        ):
            with self.subTest(invalid=(direction, comparator)):
                state = deepcopy(base_state)
                state["buy_ui"]["situation"].update({
                    "price_enabled_check": True,
                    "setting1_enabled_check": True,
                    "setting2_enabled_check": False,
                    "setting1_action_combo": "매수리셋",
                    "setting1_direction_combo": direction,
                    "setting1_compare_combo": comparator,
                })
                preview = self.mapper.build_engine_rules_preview_from_ui_state(
                    state, deepcopy(self.base_rules)
                )
                candidates = preview["preview_rules"]["indicator_follow_rule_preview"][
                    "candidates"
                ].get("execution", {})
                self.assertNotIn("base", candidates)
                self.assertIn(
                    "buy situation price slot SETTING1 is invalid",
                    preview["validation_warnings"],
                )

    def test_situation_response_commit_validator_guards_pair_and_cycle_authority(self) -> None:
        state = self._state()
        state["buy_ui"]["situation"].update({
            "unfilled_enabled_check": True,
            "unfilled_scope_combo": "매회",
            "unfilled_time_line": "10",
            "unfilled_unit_combo": "초",
            "price_enabled_check": True,
            "setting1_enabled_check": True,
            "setting2_enabled_check": False,
            "setting1_action_combo": "매수리셋",
            "setting1_direction_combo": "상향",
            "setting1_compare_combo": "이상",
        })
        preview = self.mapper.build_engine_rules_preview_from_ui_state(
            state, deepcopy(self.base_rules)
        )
        valid_rules = preview["preview_rules"]

        for direction, comparator in (
            ("UP", ">="),
            ("UP", "<="),
            ("DOWN", ">="),
            ("DOWN", "<="),
            ("BOTH", "WITHIN"),
            ("BOTH", "OUTSIDE"),
        ):
            with self.subTest(valid=(direction, comparator)):
                rules = deepcopy(valid_rules)
                policy = rules["buy"]["execution"]["base"]["buy_price_reset_policy"]
                policy.update({"direction": direction, "compare": comparator})
                result = self.validator.validate_committed_rules(
                    deepcopy(rules), rules, [], {}
                )
                check = next(
                    item for item in result["checks"]
                    if item["name"] == "buy_price_reset_direction_comparator_valid"
                )
                self.assertTrue(check["ok"], result)

        for direction, comparator in (
            ("UP", "WITHIN"),
            ("UP", "OUTSIDE"),
            ("DOWN", "WITHIN"),
            ("DOWN", "OUTSIDE"),
            ("BOTH", ">="),
            ("BOTH", "<="),
        ):
            with self.subTest(invalid=(direction, comparator)):
                rules = deepcopy(valid_rules)
                rules["buy"]["execution"]["base"]["buy_price_reset_policy"].update({
                    "direction": direction,
                    "compare": comparator,
                })
                result = self.validator.validate_committed_rules(
                    deepcopy(rules), rules, [], {}
                )
                checks = {item["name"]: item["ok"] for item in result["checks"]}
                self.assertFalse(checks["buy_price_reset_direction_comparator_valid"])
                self.assertFalse(checks["buy_price_reset_policy_valid"])

        cycle_rules = deepcopy(valid_rules)
        cycle_rules["buy"]["execution"]["cycle"]["buy_price_response_policies"] = []
        cycle_result = self.validator.validate_committed_rules(
            deepcopy(cycle_rules), cycle_rules, [], {}
        )
        cycle_checks = {item["name"]: item["ok"] for item in cycle_result["checks"]}
        self.assertFalse(cycle_checks["buy_cycle_situation_authority_valid"])
        self.assertFalse(cycle_checks["buy_cycle_policy_valid"])

        combined_rules = deepcopy(valid_rules)
        combined_result = self.validator.validate_committed_rules(
            deepcopy(combined_rules), combined_rules, [], {}
        )
        combined_checks = {
            item["name"]: item["ok"] for item in combined_result["checks"]
        }
        self.assertNotIn("buy_situation_response_modes_exclusive", combined_checks)
        self.assertTrue(combined_checks["buy_unfilled_timeout_policy_valid"])
        self.assertTrue(combined_checks["buy_price_response_slots_valid"])

    def test_last_round_active_round_trip_and_mapper_connection(self) -> None:
        self.dialog.buy_base_hoga_combo.setCurrentText("단일호가")
        self.dialog.buy_base_order_combo.setCurrentText("현재가")
        self.dialog.buy_base_time_mode_combo.setCurrentText("다중비율")
        self.dialog.buy_last_round_active_check.setChecked(True)
        self.dialog.buy_last_round_active_direction_combo.setCurrentText("하향")
        self.dialog.buy_last_round_active_ratio_line.setText("0.62")
        self.dialog.buy_last_round_active_compare_combo.setCurrentText("이하")
        expected = deepcopy(self._state()["buy_ui"]["base"]["last_round_active_buy"])

        saved = self.dialog.save_indicator_follow_ui_state_to_rules()
        self.assertTrue(saved["success"], saved)
        self.dialog.buy_last_round_active_ratio_line.setText("9")
        payload = json.loads(self.rules_path.read_text(encoding="utf-8"))
        self.dialog.apply_indicator_follow_ui_state(
            payload["indicator_follow_ui_state"]["state"]
        )
        self.assertEqual(expected, self._state()["buy_ui"]["base"]["last_round_active_buy"])

        self.dialog.apply_indicator_follow_ui_state({
            "buy_ui": {"base": {"time_mode_combo": "선택없음", "last_round_active_buy": expected}}
        })
        disabled = self._state()["buy_ui"]["base"]["last_round_active_buy"]
        self.assertTrue(disabled["checked"])
        self.assertFalse(disabled["enabled"])
        self.dialog.buy_base_time_mode_combo.setCurrentText("다중비율")
        self.assertEqual(expected, self._state()["buy_ui"]["base"]["last_round_active_buy"])

        candidate = self._candidate("base")
        self.assertTrue(candidate["execution_connected"])
        self.assertTrue(candidate["value"]["last_round_active_buy"]["enabled"])
        self.assertEqual("LAST_MULTI_POINT_CHILD", candidate["value"]["last_round_active_buy"]["applies_to"])

        helper = buy_helper_module.IndicatorFollowBuyExecutionConnectionTest()
        rules = helper._rules()
        rules["buy"]["execution"]["base"] = deepcopy(candidate["value"])
        result = helper._build(
            rules=rules,
            cycle=helper._cycle(
                1,
                avg_price=110,
                base_filled_buy_amount=300,
                last_filled_buy_amount=300,
                cumulative_filled_buy_amount=300,
            ),
            config={"trade_amount_type": "QUANTITY", "buy_qty": 6},
            price=100,
        )
        self.assertEqual("READY", result["status"], result)
        self.assertTrue(result["execution_intents"][-1]["last_round_active_decision"]["matched"])

    def test_legacy_base_without_last_round_active_defaults_disabled_without_write(self) -> None:
        before = self.rules_path.read_bytes()
        self.dialog.buy_base_time_mode_combo.setCurrentText("다중시간")
        self.dialog.buy_last_round_active_check.setChecked(True)
        self.dialog.buy_last_round_active_ratio_line.setText("9")

        applied = self.dialog.apply_indicator_follow_ui_state({
            "buy_ui": {"base": {"time_mode_combo": "다중시간"}}
        })
        state = self._state()["buy_ui"]["base"]["last_round_active_buy"]

        self.assertFalse(applied["sync_errors"])
        self.assertFalse(state["checked"])
        self.assertFalse(state["enabled"])
        self.assertEqual("0.45", state["ratio_line"])
        self.assertEqual(before, self.rules_path.read_bytes())

    def test_cycle_is_enabled_and_cancel_batch_is_connected(self) -> None:
        self.assertTrue(self.dialog.buy_cycle_column_widget.isEnabled())
        self.assertTrue(self.dialog.buy_cycle_hoga_mode_combo.isEnabled())
        self.assertFalse(hasattr(self.dialog, "buy_cycle_situation_mode_combo"))
        self.assertFalse(hasattr(self.dialog, "buy_cycle_price_action_combo"))

        self.dialog.buy_cycle_time_mode_combo.setCurrentText("다중비율")
        self.dialog.buy_cycle_ratio_value_line.setText("0.37")
        expected_cycle = deepcopy(self._state()["buy_ui"]["cycle"])
        saved = self.dialog.save_indicator_follow_ui_state_to_rules()
        self.assertTrue(saved["success"], saved)
        self.dialog.buy_cycle_ratio_value_line.setText("9")
        payload = json.loads(self.rules_path.read_text(encoding="utf-8"))
        self.dialog.apply_indicator_follow_ui_state(
            payload["indicator_follow_ui_state"]["state"]
        )
        self.assertEqual(expected_cycle, self._state()["buy_ui"]["cycle"])
        self.assertTrue(self._candidate("cycle")["execution_connected"])
        self.dialog.buy_situation_response_unfilled_enabled_check.setChecked(True)
        self.dialog.buy_situation_response_price_enabled_check.setChecked(True)
        self.assertTrue(self.dialog.buy_situation_response_unfilled_enabled_check.isChecked())
        self.dialog.buy_situation_response_setting1_action_combo.setCurrentText("일괄취소")
        connected = self._candidate("cycle")
        self.assertTrue(connected["execution_connected"])
        self.assertEqual(
            "CANCEL_BATCH",
            connected["value"]["buy_price_response_policies"][0]["action"],
        )
        self.assertEqual(
            self._candidate("base")["value"]["buy_price_response_policies"],
            connected["value"]["buy_price_response_policies"],
        )
        self.assertEqual(
            self._candidate("base")["value"]["unfilled_timeout_policy"],
            connected["value"]["unfilled_timeout_policy"],
        )
        self.assertTrue(connected["value"]["unfilled_timeout_policy"]["enabled"])

    def test_exit_conditions_are_independent_and_use_or(self) -> None:
        self.dialog.buy_cycle_time_mode_combo.setCurrentText("선택없음")
        self.assertTrue(self.dialog.buy_exit_time_check.isEnabled())
        self.dialog.buy_exit_time_check.setChecked(True)
        self.assertTrue(self.dialog.buy_exit_time_line.isEnabled())

        self.dialog.buy_cycle_time_mode_combo.setCurrentText("다중시간")
        self.assertTrue(self.dialog.buy_exit_time_check.isChecked())
        self.assertTrue(self.dialog.buy_exit_time_check.isEnabled())
        self.assertTrue(self.dialog.buy_exit_time_line.isEnabled())
        self.dialog.buy_exit_price_check.setChecked(True)
        self.dialog.buy_exit_count_check.setChecked(True)
        state = deepcopy(self._state())
        reapplied = self.dialog.apply_indicator_follow_ui_state(state)
        self.assertFalse(reapplied["sync_errors"])
        self.assertTrue(self.dialog.buy_exit_time_check.isEnabled())
        exit_policy = self._candidate("base")["value"]["buy_exit_policy"]
        self.assertEqual("OR", exit_policy["logic"])
        self.assertEqual({"PRICE", "COUNT", "TIME"}, {
            item["condition_type"] for item in exit_policy["conditions"]
        })

    def test_repeat_active_connected_while_price_compare_active_remains_reserved(self) -> None:
        repeat_index = self.dialog.buy_base_detail_mode_combo.findText("능동매수")
        price_compare_index = self.dialog.buy_price_compare_above_mode_combo.findText("능동매수")
        self.assertTrue(self.dialog.buy_base_detail_mode_combo.model().item(repeat_index).isEnabled())
        self.assertFalse(self.dialog.buy_price_compare_above_mode_combo.model().item(price_compare_index).isEnabled())

        self.dialog.buy_base_apply_all_check.setChecked(True)
        self.dialog.buy_base_detail_mode_combo.setCurrentText("능동매수")
        self.dialog.buy_base_active_direction_combo.setCurrentText("상하")
        self.dialog.buy_base_active_ratio_line.setText("1.25")
        self.dialog.buy_base_active_compare_combo.setCurrentText("이탈")
        repeat = self._candidate("repeat")["value"]
        self.assertEqual("ACTIVE_BUY", repeat["detail_mode"])
        self.assertEqual(("BOTH", 1.25, "OUTSIDE"), (
            repeat["active_direction"], repeat["active_ratio"], repeat["active_compare"]
        ))

        helper = buy_helper_module.IndicatorFollowBuyExecutionConnectionTest()
        rules = helper._rules(repeat_mode="ACTIVE_BUY")
        ready = helper._build(rules=rules, cycle=helper._cycle(1), price=100)
        self.assertEqual("READY", ready["status"], ready)

    def test_ui_policies_reach_p2_consumers_without_generic_downgrade(self) -> None:
        helper = buy_helper_module.IndicatorFollowBuyExecutionConnectionTest()

        self.dialog.buy_price_compare_skip_check.setChecked(True)
        self.dialog.buy_price_compare_skip_direction_combo.setCurrentText("상향")
        self.dialog.buy_price_compare_skip_ratio_line.setText("5")
        self.dialog.buy_price_compare_skip_compare_combo.setCurrentText("이상")
        additional = self._candidate("additional")["value"]
        rules = helper._rules()
        rules["buy"]["execution"]["additional"] = additional
        skipped = helper._build(
            rules=rules,
            cycle=helper._cycle(1, last_confirmed_buy_order_price=100),
            price=110,
        )
        self.assertEqual("BUY_GENERATION_SKIPPED_BY_PREVIOUS_ROUND_PRICE", skipped["reason"])

        self.dialog.buy_situation_response_price_enabled_check.setChecked(True)
        self.dialog.buy_situation_response_setting1_enabled_check.setChecked(True)
        self.dialog.buy_situation_response_setting2_enabled_check.setChecked(False)
        self.dialog.buy_situation_response_setting1_action_combo.setCurrentText("매수리셋")
        cycle = self._candidate("cycle")["value"]
        rules = helper._rules()
        rules["buy"]["execution"]["cycle"] = cycle
        no_signal = buy_helper_module.bridge.build_indicator_follow_buy_intent(
            buy_signal_result={"signal": "HOLD"},
            context={
                "cycle": helper._cycle(),
                "stock_config": {"trade_amount_type": "QUANTITY", "buy_qty": 1},
                "rules": rules,
                "reference_price": 100,
                "actionable_current_price": 100,
            },
        )
        self.assertEqual("BUY_SOURCE_SIGNAL_REQUIRED", no_signal["reason"])

    def test_last_plus_one_ui_methods_reach_dedicated_p2_consumer(self) -> None:
        helper = buy_helper_module.IndicatorFollowBuyExecutionConnectionTest()
        self.dialog.buy_price_compare_skip_check.setChecked(False)
        self.dialog.buy_additional_active_check.setChecked(True)
        self.dialog.buy_additional_active_direction_combo.setCurrentText("하향")
        self.dialog.buy_additional_active_ratio_line.setText("5")
        self.dialog.buy_additional_active_compare_combo.setCurrentText("이상")
        cycle = helper._cycle(
            2,
            last_normal_round_approved_budget=300,
            last_confirmed_buy_order_price=100,
            avg_price=90,
        )

        for method, expected in (
            ("시장가", "MARKET"),
            ("현재가", "CURRENT_PRICE"),
            ("능동", "ACTIVE"),
        ):
            with self.subTest(method=method):
                self.dialog.buy_additional_active_method_combo.setCurrentText(method)
                additional = self._candidate("additional")["value"]
                self.assertEqual(expected, additional["last_plus_one"]["method"])
                rules = helper._rules(max_rounds=2)
                rules["buy"]["execution"]["additional"] = additional
                result = helper._build(rules=rules, cycle=cycle, price=100)
                self.assertEqual("READY", result["status"], result)
                self.assertEqual("LAST_PLUS_ONE", result["execution_intent"]["generation_kind"])

    def test_validation_display_reflects_connected_and_reserved_features(self) -> None:
        self.assertIn("연결 기능 사용 가능", self.dialog.validation_buy_line.text())
        self.dialog._build_advanced_tab()
        advanced_text = self.dialog.advanced_tab.findChild(
            dialog_module.QTextEdit
        ).toPlainText()
        self.assertIn("직전회차주문가 대비 현재주문가", advanced_text)
        self.assertIn("ACTIVE_BUY_NOT_IMPLEMENTED", advanced_text)
        self.assertIn("순환 가격비교 일괄취소", advanced_text)
        self.assertNotIn("CYCLE_OPTION_EXECUTION_NOT_CONNECTED", advanced_text)


if __name__ == "__main__":
    unittest.main()
