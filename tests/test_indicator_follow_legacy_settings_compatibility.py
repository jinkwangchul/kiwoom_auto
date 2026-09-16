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
    legacy_buy_bollinger_sign,
    legacy_sell_signed_percent_sign,
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

    def test_rules_load_records_legacy_and_instance_sources_separately(self) -> None:
        registration = self._dialog()
        edit = self._dialog(mode="edit")
        try:
            self.assertEqual(
                STATE_AUTHORITY_LEGACY_TEMPLATE_FALLBACK,
                registration._last_ui_state_apply_result["source"],
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

    def test_legacy_order_price_is_preserved_unresolved_and_blocks_registration(self) -> None:
        dialog = self._dialog()
        try:
            state = dialog.collect_indicator_follow_ui_state()
            result = dialog.build_registration_rules_from_current_ui_state()
        finally:
            dialog.close()

        self.assertEqual(STATE_AUTHORITY_LEGACY_TEMPLATE_FALLBACK, dialog._registration_initial_state_source)
        for group in "abc":
            self.assertEqual(
                "주문가",
                state["sell_ui"]["signal_conditions"][f"condition_{group}"][
                    "gap_left_combo"
                ],
            )
        self.assertFalse(result["success"])
        self.assertTrue(
            all(
                "가격 기준 재선택 필요" in reason
                for reason in result["internal_blocked_reasons"]
            )
        )

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


if __name__ == "__main__":
    unittest.main()
