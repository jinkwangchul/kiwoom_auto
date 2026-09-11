# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication

from engines.condition_engine import parse_condition_expression
from gui_indicator_follow_routine_settings_dialog import (
    IndicatorFollowRoutineSettingsDialog,
    _settings_validation_user_reason,
)
from routines.지표추종매매 import routine_rule_mapper
from routines.지표추종매매.routine_macd_engine import (
    evaluate_indicator_follow_routine,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROUTINE_DIR = PROJECT_ROOT / "routines" / "지표추종매매"
SOURCE_RULES_PATH = ROUTINE_DIR / "rules.json"


class IndicatorFollowSellExpressionValidationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _dialog(self, rules_path: Path, *, instance_id: str = ""):
        return IndicatorFollowRoutineSettingsDialog(
            rules_path=rules_path,
            routine_path=ROUTINE_DIR,
            routine_name="SELL Expression 검증 루틴",
            definition_id="indicator_follow",
            instance_id=instance_id,
            settings_mode="edit" if instance_id else "registration",
        )

    @staticmethod
    def _state(base_state: dict, expression: str, active_groups: set[str]) -> dict:
        state = deepcopy(base_state)
        state["basic"]["sell_signal_expr_line"] = expression
        for condition_name, condition in state["sell_ui"]["signal_conditions"].items():
            group_name = condition_name[-1].upper()
            for key in condition:
                if key.endswith("_check"):
                    condition[key] = group_name in active_groups
        return state

    def test_required_ten_case_matrix_is_expression_centric(self) -> None:
        dialog = self._dialog(SOURCE_RULES_PATH)
        try:
            base_state = dialog.collect_indicator_follow_ui_state()
            cases = (
                ("A and B", {"A", "B"}, True, None),
                ("A and B", {"A", "B", "C"}, True, None),
                ("A and B and C", {"A", "B"}, False, "C"),
                ("A and B and C", {"A", "B", "C"}, True, None),
                ("A", {"A", "B", "C"}, True, None),
                ("C", set(), False, "C"),
                ("C", {"C"}, True, None),
                ("A or C", {"A", "C"}, True, None),
                ("A or C", {"A"}, False, "C"),
                ("(A and B) or C", {"A", "B", "C"}, True, None),
            )
            for expression, active_groups, expected_valid, missing_group in cases:
                with self.subTest(expression=expression, active_groups=active_groups):
                    state = self._state(base_state, expression, active_groups)
                    result = routine_rule_mapper.validate_settings_candidate(
                        state,
                        dialog.rules_data,
                    )
                    self.assertEqual(expected_valid, result["valid"], result["blocked_reasons"])
                    if missing_group is not None:
                        self.assertIn(
                            f"sell condition {missing_group} is referenced by expression "
                            "but has no active conditions",
                            result["blocked_reasons"],
                        )
        finally:
            dialog.close()

    def test_a_b_c_use_the_same_referenced_empty_group_rule(self) -> None:
        dialog = self._dialog(SOURCE_RULES_PATH)
        try:
            base_state = dialog.collect_indicator_follow_ui_state()
            for group_name in ("A", "B", "C"):
                with self.subTest(group_name=group_name):
                    state = self._state(base_state, group_name, set())
                    result = routine_rule_mapper.validate_settings_candidate(
                        state,
                        dialog.rules_data,
                    )
                    self.assertFalse(result["valid"])
                    self.assertIn(
                        f"sell condition {group_name} is referenced by expression "
                        "but has no active conditions",
                        result["blocked_reasons"],
                    )
        finally:
            dialog.close()

    def test_candidate_presence_does_not_define_validation_or_participation(self) -> None:
        dialog = self._dialog(SOURCE_RULES_PATH)
        try:
            base_state = dialog.collect_indicator_follow_ui_state()
            inactive_c = self._state(base_state, "A and B", {"A", "B"})
            preview = routine_rule_mapper.build_engine_rules_preview_from_ui_state(
                inactive_c,
                dialog.rules_data,
            )
            candidates = preview["preview_rules"]["indicator_follow_rule_preview"][
                "candidates"
            ]["sell"]["add_signal_candidates"]
            self.assertEqual(
                {
                    "sell.signals.ui_preview_condition_a",
                    "sell.signals.ui_preview_condition_b",
                },
                set(candidates),
            )
            self.assertNotIn(
                "sell condition C candidate group was not generated",
                preview["validation_warnings"],
            )

            configured_c = self._state(base_state, "A and B", {"A", "B", "C"})
            preview = routine_rule_mapper.build_engine_rules_preview_from_ui_state(
                configured_c,
                dialog.rules_data,
            )
            candidates = preview["preview_rules"]["indicator_follow_rule_preview"][
                "candidates"
            ]["sell"]["add_signal_candidates"]
            self.assertIn("sell.signals.ui_preview_condition_c", candidates)
            self.assertEqual(
                ["A", "B"],
                candidates["sell.signals.ui_preview_condition_c"]["value"][
                    "signal_expression"
                ]["identifiers"],
            )
        finally:
            dialog.close()

    def test_referenced_empty_group_uses_explicit_user_message(self) -> None:
        for group_name in ("A", "B", "C"):
            with self.subTest(group_name=group_name):
                self.assertEqual(
                    f"매도조건 {group_name}가 신호 조합에 포함되어 있지만\n"
                    "활성화된 조건이 없습니다.",
                    _settings_validation_user_reason(
                        f"sell condition {group_name} is referenced by expression "
                        "but has no active conditions"
                    ),
                )

        dialog = self._dialog(SOURCE_RULES_PATH)
        try:
            state = self._state(
                dialog.collect_indicator_follow_ui_state(),
                "A and B and C",
                {"A", "B"},
            )
            with patch.object(dialog, "collect_indicator_follow_ui_state", return_value=state):
                result = dialog.build_registration_rules_from_current_ui_state()
        finally:
            dialog.close()

        self.assertFalse(result["success"])
        self.assertEqual(
            "매도조건 C가 신호 조합에 포함되어 있지만\n활성화된 조건이 없습니다.",
            result["user_messages"][0],
        )

    def test_unused_condition_c_state_survives_commit_and_reopen(self) -> None:
        source = json.loads(SOURCE_RULES_PATH.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as temp_dir:
            rules_path = Path(temp_dir) / "rules.json"
            rules_path.write_text(
                json.dumps(source, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            instance = SimpleNamespace(
                instance_id="sell-expression-instance",
                definition_id="indicator_follow",
                rules_path=rules_path,
            )
            dialog = self._dialog(rules_path, instance_id=instance.instance_id)
            try:
                dialog.sell_signal_expr_line.setText("A and B")
                dialog.sell_signal_condition_c_macd_check.setChecked(True)
                dialog.sell_signal_condition_c_macd_value_line.setText("1.23")
                with patch(
                    "gui_indicator_follow_routine_settings_dialog.routine_instance_by_id",
                    return_value=instance,
                ):
                    result = dialog.commit_current_settings()
            finally:
                dialog.close()

            self.assertTrue(result["success"], result.get("blocked_reasons"))
            reopened = self._dialog(rules_path, instance_id=instance.instance_id)
            try:
                self.assertEqual("A and B", reopened.sell_signal_expr_line.text())
                self.assertTrue(reopened.sell_signal_condition_c_macd_check.isChecked())
                self.assertEqual("1.23", reopened.sell_signal_condition_c_macd_value_line.text())
                saved_state = reopened.collect_indicator_follow_ui_state()
                self.assertEqual(
                    "1.23",
                    saved_state["sell_ui"]["signal_conditions"]["condition_c"][
                        "macd_value_line"
                    ],
                )
                self.assertEqual(
                    ["A", "B"],
                    reopened.rules_data["sell"]["signals"]["ui_condition_c"][
                        "signal_expression"
                    ]["identifiers"],
                )
            finally:
                reopened.close()

    def test_unused_invalid_condition_value_is_preserved_without_candidate(self) -> None:
        source = json.loads(SOURCE_RULES_PATH.read_text(encoding="utf-8"))
        old_expression = parse_condition_expression(
            "A and B and C",
            allowed_identifiers={"A", "B", "C"},
            allow_duplicate_identifiers=False,
        )
        existing_c_groups = [{
            "enabled": True,
            "conditions": [{"target": "CLOSE", "operator": "<", "value": 0}],
        }]
        source["sell"]["signals"]["ui_condition_c"] = {
            "enabled": True,
            "groups": deepcopy(existing_c_groups),
            "signal_expression": {
                "source": "A and B and C",
                "normalized": old_expression["normalized"],
                "ast": old_expression["ast"],
                "identifiers": old_expression["identifiers"],
                "identifier_map": {
                    "A": "ui_condition_a",
                    "B": "ui_condition_b",
                    "C": "ui_condition_c",
                },
            },
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            rules_path = Path(temp_dir) / "rules.json"
            rules_path.write_text(
                json.dumps(source, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            instance = SimpleNamespace(
                instance_id="sell-expression-unused-invalid-c",
                definition_id="indicator_follow",
                rules_path=rules_path,
            )
            dialog = self._dialog(rules_path, instance_id=instance.instance_id)
            try:
                dialog.sell_signal_expr_line.setText("A and B")
                dialog.sell_signal_condition_c_gap_check.setChecked(False)
                dialog.sell_signal_condition_c_macd_check.setChecked(True)
                dialog.sell_signal_condition_c_array_check.setChecked(False)
                dialog.sell_signal_condition_c_macd_value_line.setText("unused-value")
                with patch(
                    "gui_indicator_follow_routine_settings_dialog.routine_instance_by_id",
                    return_value=instance,
                ):
                    result = dialog.commit_current_settings()
            finally:
                dialog.close()

            self.assertTrue(result["success"], result.get("blocked_reasons"))
            reopened = self._dialog(rules_path, instance_id=instance.instance_id)
            try:
                self.assertEqual("A and B", reopened.sell_signal_expr_line.text())
                self.assertTrue(reopened.sell_signal_condition_c_macd_check.isChecked())
                self.assertEqual(
                    "unused-value",
                    reopened.sell_signal_condition_c_macd_value_line.text(),
                )
                applied_c = reopened.rules_data["sell"]["signals"]["ui_condition_c"]
                self.assertTrue(applied_c["enabled"])
                self.assertEqual(existing_c_groups, applied_c["groups"])
                self.assertEqual(
                    ["A", "B"],
                    applied_c["signal_expression"]["identifiers"],
                )
            finally:
                reopened.close()

    def test_original_a_and_b_with_inactive_c_commits_and_reopens(self) -> None:
        source = json.loads(SOURCE_RULES_PATH.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as temp_dir:
            rules_path = Path(temp_dir) / "rules.json"
            rules_path.write_text(
                json.dumps(source, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            instance = SimpleNamespace(
                instance_id="sell-expression-inactive-c",
                definition_id="indicator_follow",
                rules_path=rules_path,
            )
            dialog = self._dialog(rules_path, instance_id=instance.instance_id)
            try:
                dialog.sell_signal_expr_line.setText("A and B")
                dialog.sell_signal_condition_c_gap_check.setChecked(False)
                dialog.sell_signal_condition_c_macd_check.setChecked(False)
                dialog.sell_signal_condition_c_array_check.setChecked(False)
                with patch(
                    "gui_indicator_follow_routine_settings_dialog.routine_instance_by_id",
                    return_value=instance,
                ):
                    result = dialog.commit_current_settings()
            finally:
                dialog.close()

            self.assertTrue(result["success"], result.get("blocked_reasons"))
            reopened = self._dialog(rules_path, instance_id=instance.instance_id)
            try:
                self.assertEqual("A and B", reopened.sell_signal_expr_line.text())
                self.assertFalse(reopened.sell_signal_condition_c_gap_check.isChecked())
                self.assertFalse(reopened.sell_signal_condition_c_macd_check.isChecked())
                self.assertFalse(reopened.sell_signal_condition_c_array_check.isChecked())
            finally:
                reopened.close()


class IndicatorFollowSellExpressionRuntimeTest(unittest.TestCase):
    @staticmethod
    def _expression(source: str) -> dict:
        parsed = parse_condition_expression(
            source,
            allowed_identifiers={"A", "B", "C"},
            allow_duplicate_identifiers=False,
        )
        if parsed.get("ok") is not True:
            raise AssertionError(parsed)
        return {
            "source": source,
            "normalized": parsed["normalized"],
            "ast": parsed["ast"],
            "identifiers": parsed["identifiers"],
            "identifier_map": {
                "A": "ui_condition_a",
                "B": "ui_condition_b",
                "C": "ui_condition_c",
            },
        }

    @staticmethod
    def _signal(passed: bool, expression: dict) -> dict:
        return {
            "enabled": True,
            "groups": [{
                "enabled": True,
                "conditions": [{
                    "target": "CLOSE",
                    "operator": ">" if passed else "<",
                    "value": 0,
                }],
            }],
            "signal_expression": deepcopy(expression),
        }

    def _evaluate(self, expression_text: str, passed_groups: set[str]):
        expression = self._expression(expression_text)
        signals = {
            f"ui_condition_{group.lower()}": self._signal(
                group in passed_groups,
                expression,
            )
            for group in ("A", "B", "C")
        }
        config = {
            "enabled": True,
            "macd": {"fast": 12, "slow": 26, "signal": 9},
            "rsi": {"period": 14},
            "moving_averages": [5, 20, 60],
            "buy": {"delay_bar": 0, "groups": []},
            "sell": {
                "delay_bar": 0,
                "signal_logic": "AND",
                "signals": signals,
            },
        }
        candles = [
            {
                "open": value,
                "high": value + 1,
                "low": value - 1,
                "close": value,
                "volume": 1000,
            }
            for value in range(10, 50)
        ]
        return evaluate_indicator_follow_routine(candles, config)

    def test_unreferenced_configured_groups_do_not_change_sell_result(self) -> None:
        result = self._evaluate("A and B", {"A", "B"})
        self.assertEqual("SELL", result.signal)

        result = self._evaluate("A", {"A"})
        self.assertEqual("SELL", result.signal)

        result = self._evaluate("A and B and C", {"A", "B"})
        self.assertIsNone(result.signal)


if __name__ == "__main__":
    unittest.main()
