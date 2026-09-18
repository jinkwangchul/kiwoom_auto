from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import unittest

from PyQt5.QtWidgets import QApplication

from gui_indicator_follow_routine_settings_dialog import (
    IndicatorFollowRoutineSettingsDialog,
    STATE_AUTHORITY_CANONICAL_DEFAULT,
    STATE_AUTHORITY_INSTANCE_CURRENT,
    STATE_AUTHORITY_LEGACY_TEMPLATE_FALLBACK,
    normalize_buy_situation_ui_state,
)
from indicator_follow_settings_compatibility import (
    canonical_indicator_follow_ui_state,
    get_canonical_fresh_defaults,
    legacy_buy_bollinger_sign,
    legacy_sell_signed_percent_sign,
    normalize_sell_selected_set_authority,
)


class IndicatorFollowLegacySettingsCompatibilityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        cls.project_root = Path(__file__).resolve().parents[1]
        cls.routine_dir = cls.project_root / "routines" / "지표추종매매"
        cls.rules_path = cls.routine_dir / "rules.json"

    def _dialog(self, *, mode: str = "registration"):
        return IndicatorFollowRoutineSettingsDialog(
            rules_path=self.rules_path,
            routine_path=self.routine_dir,
            routine_name="호환성 검증",
            definition_id="indicator_follow",
            instance_id="instance-id" if mode == "edit" else "",
            settings_mode=mode,
        )

    def test_situation_normalizer_does_not_overwrite_explicit_modern_fields(self) -> None:
        situation = {
            "price_enabled_check": True,
            "setting1_enabled_check": False,
            "setting1_left_combo": "평단가",
            "setting2_enabled_check": False,
            "setting2_left_combo": "현재가",
        }

        normalized = normalize_buy_situation_ui_state(situation)

        self.assertFalse(normalized["setting1_enabled_check"])
        self.assertFalse(normalized["setting2_enabled_check"])
        self.assertEqual("현재가", normalized["setting2_left_combo"])

    def test_rules_load_and_registration_authority_sources_remain_distinct(self) -> None:
        registration = self._dialog()
        edit = self._dialog(mode="edit")
        try:
            self.assertEqual(
                STATE_AUTHORITY_LEGACY_TEMPLATE_FALLBACK,
                registration._last_ui_state_apply_result["source"],
            )
            self.assertEqual(
                STATE_AUTHORITY_CANONICAL_DEFAULT,
                registration._registration_initial_state_source,
            )
            self.assertEqual(
                STATE_AUTHORITY_INSTANCE_CURRENT,
                edit._last_ui_state_apply_result["source"],
            )
        finally:
            registration.close()
            edit.close()

    def test_dialog_preserves_setting2_operand_while_ui_derives_slot_enablement(self) -> None:
        dialog = self._dialog(mode="edit")
        try:
            result = dialog.apply_indicator_follow_ui_state(
                {
                    "buy_ui": {
                        "situation": {
                            "price_enabled_check": True,
                            "setting1_enabled_check": True,
                            "setting1_left_combo": "평단가",
                            "setting2_enabled_check": False,
                            "setting2_left_combo": "현재가",
                        }
                    }
                },
                source=STATE_AUTHORITY_INSTANCE_CURRENT,
            )
            state = dialog.collect_indicator_follow_ui_state()["buy_ui"]["situation"]
        finally:
            dialog.close()

        self.assertFalse(result["sync_errors"])
        self.assertEqual("현재가", state["setting2_left_combo"])
        self.assertTrue(state["setting2_enabled_check"])

    def test_known_legacy_combo_alias_is_applied_deterministically(self) -> None:
        dialog = self._dialog()
        try:
            result = dialog.apply_indicator_follow_ui_state(
                {
                    "buy_ui": {
                        "price_compare": {"condition_combo": "=<"}
                    }
                },
                source=STATE_AUTHORITY_LEGACY_TEMPLATE_FALLBACK,
            )
            actual = dialog.buy_price_compare_condition_combo.currentText()
        finally:
            dialog.close()

        self.assertEqual("<=", actual)
        self.assertFalse(result["sync_errors"])

    def test_unknown_combo_value_is_unresolved_and_reported(self) -> None:
        dialog = self._dialog(mode="edit")
        try:
            result = dialog.apply_indicator_follow_ui_state(
                {"basic": {"basic_signal_interval_combo": "UNSUPPORTED"}},
                source=STATE_AUTHORITY_INSTANCE_CURRENT,
            )
            index = dialog.basic_signal_interval_combo.currentIndex()
        finally:
            dialog.close()

        self.assertEqual(-1, index)
        self.assertEqual(
            [{
                "name": "basic_signal_interval_combo",
                "reason": "combo_value_not_found",
                "value": "UNSUPPORTED",
            }],
            result["sync_errors"],
        )

    def test_explicit_signed_percent_values_are_never_overwritten(self) -> None:
        dialog = self._dialog(mode="edit")
        state = deepcopy(dialog.collect_indicator_follow_ui_state())
        state["buy_ui"]["signal_filter"]["buy_bollinger_sign_combo"] = "+"
        condition_b = state["sell_ui"]["signal_conditions"]["condition_b"]
        condition_b["price_box_sign_combo"] = "-"
        condition_b["bollinger_sign_combo"] = "-"
        try:
            result = dialog.apply_indicator_follow_ui_state(
                state,
                source=STATE_AUTHORITY_INSTANCE_CURRENT,
            )
            actual = dialog.collect_indicator_follow_ui_state()
        finally:
            dialog.close()

        self.assertFalse(result["sync_errors"])
        self.assertEqual(
            "+",
            actual["buy_ui"]["signal_filter"]["buy_bollinger_sign_combo"],
        )
        actual_b = actual["sell_ui"]["signal_conditions"]["condition_b"]
        self.assertEqual("-", actual_b["price_box_sign_combo"])
        self.assertEqual("-", actual_b["bollinger_sign_combo"])

    def test_signed_percent_legacy_inference_has_one_pure_contract(self) -> None:
        self.assertEqual(
            "-",
            legacy_buy_bollinger_sign({"buy_bollinger_direction_combo": "하향"}),
        )
        self.assertEqual(
            "+",
            legacy_buy_bollinger_sign({"buy_bollinger_direction_combo": "상향"}),
        )
        for compare, expected in (("이상", "+"), (">=", "+"), ("GTE", "+"),
                                  ("이하", "-"), ("<=", "-"), ("LTE", "-")):
            with self.subTest(compare=compare):
                self.assertEqual(
                    expected,
                    legacy_sell_signed_percent_sign(
                        {"compare": compare},
                        compare_field="compare",
                    ),
                )

    def test_legacy_template_order_price_does_not_override_fresh_operand_state(self) -> None:
        dialog = self._dialog()
        try:
            state = dialog.collect_indicator_follow_ui_state()
        finally:
            dialog.close()

        self.assertEqual(
            STATE_AUTHORITY_CANONICAL_DEFAULT,
            dialog._registration_initial_state_source,
        )
        for group in "abc":
            condition = state["sell_ui"]["signal_conditions"][f"condition_{group}"]
            self.assertEqual("평단가", condition["gap_left_combo"])
            self.assertEqual("현재가", condition["gap_right_combo"])
            self.assertNotEqual("주문가", condition["gap_left_combo"])
            self.assertNotEqual("주문가", condition["gap_right_combo"])

    def test_canonical_default_does_not_run_missing_sign_inference(self) -> None:
        dialog = self._dialog()
        state = deepcopy(dialog.collect_indicator_follow_ui_state())
        state["buy_ui"]["signal_filter"].pop("buy_bollinger_sign_combo", None)
        try:
            result = dialog.apply_indicator_follow_ui_state(
                state,
                source=STATE_AUTHORITY_CANONICAL_DEFAULT,
            )
            index = dialog.buy_bollinger_sign_combo.currentIndex()
        finally:
            dialog.close()

        self.assertEqual(STATE_AUTHORITY_CANONICAL_DEFAULT, result["source"])
        self.assertEqual(-1, index)

    def test_fresh_default_provider_matches_user_approved_screen_snapshot(self) -> None:
        state = get_canonical_fresh_defaults("indicator_follow")
        self.assertIsInstance(state, dict)
        self.assertIsNone(get_canonical_fresh_defaults("other"))

        def leaf_count(value):
            if isinstance(value, dict):
                return sum(leaf_count(item) for item in value.values())
            if isinstance(value, list):
                return sum(leaf_count(item) for item in value)
            return 1

        self.assertEqual(382, leaf_count(state))
        self.assertEqual("3", state["basic"]["basic_signal_interval_combo"])
        self.assertEqual("선행신호 우선", state["basic"]["basic_duplicate_signal_combo"])
        self.assertEqual("매매중지", state["basic"]["basic_error_policy_combo"])
        self.assertEqual("A", state["basic"]["buy_signal_expr_line"])
        self.assertEqual("A", state["basic"]["sell_signal_expr_line"])
        self.assertEqual("0.5", state["buy_ui"]["signal_filter"]["buy_bollinger_value_line"])
        self.assertEqual("30", state["buy_ui"]["signal_filter"]["buy_rsi_value_line"])
        self.assertTrue(state["buy_ui"]["repeat"]["apply_all_check"])
        self.assertTrue(state["buy_ui"]["situation"]["unfilled_enabled_check"])
        self.assertFalse(state["buy_ui"]["situation"]["price_enabled_check"])
        self.assertEqual("평단가", state["sell_ui"]["signal_conditions"]["condition_a"]["gap_left_combo"])
        self.assertEqual("평단가", state["sell_ui"]["signal_conditions"]["condition_b"]["gap_left_combo"])
        self.assertEqual("평단가", state["sell_ui"]["signal_conditions"]["condition_c"]["gap_left_combo"])
        self.assertEqual(
            state["sell_ui"]["setting_a"],
            state["sell_ui"]["setting_b"],
        )
        self.assertEqual(
            state["sell_ui"]["setting_a"],
            state["sell_ui"]["setting_c"],
        )

        state["basic"]["buy_signal_expr_line"] = "B"
        self.assertEqual(
            "A",
            get_canonical_fresh_defaults("indicator_follow")["basic"]["buy_signal_expr_line"],
        )

    def test_registration_collect_matches_canonical_defaults(self) -> None:
        dialog = self._dialog()
        try:
            actual = canonical_indicator_follow_ui_state(
                dialog.collect_indicator_follow_ui_state()
            )
            expected = canonical_indicator_follow_ui_state(
                get_canonical_fresh_defaults("indicator_follow")
            )
        finally:
            dialog.close()

        self.assertEqual(STATE_AUTHORITY_CANONICAL_DEFAULT, dialog._registration_initial_state_source)
        self.assertEqual(expected, actual)

    def test_sell_selected_sets_are_canonical_and_legacy_fields_are_derived(self) -> None:
        state = {
            "basic": {
                "sell_method_select_a_check": False,
                "sell_method_select_b_check": True,
                "sell_method_select_c_check": False,
            },
            "sell_ui": {
                "selected_sets": {"a": True, "b": False, "c": True},
                "legacy_summary": {
                    "sell_method_select_a_check": False,
                    "sell_method_select_b_check": True,
                    "sell_method_select_c_check": False,
                    "other_legacy_value": "kept",
                },
            },
        }

        normalized = normalize_sell_selected_set_authority(state)

        self.assertEqual(
            {"a": True, "b": False, "c": True},
            normalized["sell_ui"]["selected_sets"],
        )
        self.assertNotIn("sell_method_select_a_check", normalized["basic"])
        self.assertEqual(
            {
                "sell_method_select_a_check": True,
                "sell_method_select_b_check": False,
                "sell_method_select_c_check": True,
                "other_legacy_value": "kept",
            },
            normalized["sell_ui"]["legacy_summary"],
        )

    def test_legacy_selected_set_is_used_only_when_canonical_value_is_absent(self) -> None:
        normalized = normalize_sell_selected_set_authority(
            {
                "basic": {
                    "sell_method_select_a_check": False,
                    "sell_method_select_b_check": True,
                    "sell_method_select_c_check": False,
                },
                "sell_ui": {"legacy_summary": {}},
            }
        )

        self.assertEqual(
            {"a": False, "b": True, "c": False},
            normalized["sell_ui"]["selected_sets"],
        )
        self.assertEqual(
            False,
            normalized["sell_ui"]["legacy_summary"][
                "sell_method_select_a_check"
            ],
        )

    def test_full_collect_apply_collect_is_canonically_equal(self) -> None:
        source = self._dialog()
        target = self._dialog()
        try:
            source.sell_method_select_a_check.setChecked(False)
            source.sell_method_select_b_check.setChecked(True)
            source.sell_method_select_c_check.setChecked(False)
            before = source.collect_indicator_follow_ui_state()
            result = target.apply_indicator_follow_ui_state(
                before,
                source=STATE_AUTHORITY_LEGACY_TEMPLATE_FALLBACK,
            )
            after = target.collect_indicator_follow_ui_state()
        finally:
            source.close()
            target.close()

        self.assertFalse(result["sync_errors"])
        self.assertEqual(
            canonical_indicator_follow_ui_state(before),
            canonical_indicator_follow_ui_state(after),
        )
        self.assertNotIn(
            "sell_method_select_a_check",
            after["basic"],
        )
        self.assertEqual(
            {"a": False, "b": True, "c": False},
            after["sell_ui"]["selected_sets"],
        )

    def test_normal_direct_writers_are_blocked_and_template_is_unchanged(self) -> None:
        dialog = self._dialog()
        template_before = self.rules_path.read_bytes()
        try:
            dialog.basic_signal_interval_combo.setCurrentText("3")
            ui_result = dialog.save_indicator_follow_ui_state_to_rules()
            pending_result = dialog.save_indicator_follow_rule_pending_to_rules()
        finally:
            dialog.close()

        self.assertFalse(ui_result["success"])
        self.assertFalse(pending_result["success"])
        self.assertEqual("MAINTENANCE_ONLY", ui_result["writer_authority"])
        self.assertEqual("MAINTENANCE_ONLY", pending_result["writer_authority"])
        self.assertEqual(template_before, self.rules_path.read_bytes())


if __name__ == "__main__":
    unittest.main()
