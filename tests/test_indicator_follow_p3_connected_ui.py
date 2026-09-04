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

    def test_cycle_is_enabled_and_cancel_batch_is_unselectable_and_locked(self) -> None:
        self.assertTrue(self.dialog.buy_cycle_column_widget.isEnabled())
        self.assertTrue(self.dialog.buy_cycle_hoga_mode_combo.isEnabled())
        combo = self.dialog.buy_cycle_price_action_combo
        cancel_index = combo.findText("일괄취소")
        self.assertFalse(combo.model().item(cancel_index).isEnabled())

        combo.setCurrentText("매수리셋")
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
        combo.setCurrentText("일괄취소")
        locked = self._candidate("cycle")
        self.assertFalse(locked["execution_connected"])
        self.assertEqual("CYCLE_OPTION_EXECUTION_NOT_CONNECTED", locked["execution_lock_reason"])

    def test_exit_time_remains_mutually_exclusive_with_cycle_time_and_unfilled(self) -> None:
        self.dialog.buy_cycle_time_mode_combo.setCurrentText("선택없음")
        self.dialog.buy_cycle_situation_mode_combo.setCurrentText("가격비교")
        self.assertTrue(self.dialog.buy_exit_time_check.isEnabled())
        self.dialog.buy_exit_time_check.setChecked(True)
        self.assertTrue(self.dialog.buy_exit_time_line.isEnabled())

        self.dialog.buy_cycle_time_mode_combo.setCurrentText("다중시간")
        self.assertFalse(self.dialog.buy_exit_time_check.isChecked())
        self.assertFalse(self.dialog.buy_exit_time_check.isEnabled())
        self.assertFalse(self.dialog.buy_exit_time_line.isEnabled())
        blocked_state = deepcopy(self._state())
        reapplied = self.dialog.apply_indicator_follow_ui_state(blocked_state)
        self.assertFalse(reapplied["sync_errors"])
        self.assertFalse(self.dialog.buy_exit_time_check.isEnabled())

        self.dialog.buy_cycle_time_mode_combo.setCurrentText("선택없음")
        self.dialog.buy_cycle_situation_mode_combo.setCurrentText("미체결")
        self.assertFalse(self.dialog.buy_exit_time_check.isEnabled())
        self.dialog.buy_cycle_situation_mode_combo.setCurrentText("가격비교")
        self.assertTrue(self.dialog.buy_exit_time_check.isEnabled())

    def test_repeat_active_items_remain_reserved(self) -> None:
        for combo in (
            self.dialog.buy_base_detail_mode_combo,
            self.dialog.buy_price_compare_above_mode_combo,
        ):
            index = combo.findText("능동매수")
            self.assertGreaterEqual(index, 0)
            self.assertFalse(combo.model().item(index).isEnabled())

        helper = buy_helper_module.IndicatorFollowBuyExecutionConnectionTest()
        rules = helper._rules(repeat_mode="ACTIVE_BUY")
        blocked = helper._build(rules=rules, cycle=helper._cycle(1), price=100)
        self.assertEqual("ACTIVE_BUY_NOT_IMPLEMENTED", blocked["reason"])

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

        self.dialog.buy_cycle_price_action_combo.setCurrentText("매수리셋")
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
        self.assertIn("CYCLE_OPTION_EXECUTION_NOT_CONNECTED", advanced_text)


if __name__ == "__main__":
    unittest.main()
