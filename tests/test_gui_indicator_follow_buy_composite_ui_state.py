from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import sys
import types
import unittest

from tests.test_gui_execution_preview_button import _install_pyqt5_import_stubs


_install_pyqt5_import_stubs()
sys.modules["PyQt5"].sip = types.ModuleType("PyQt5.sip")
sys.modules["PyQt5.sip"] = sys.modules["PyQt5"].sip

import gui_indicator_follow_buy_controls as buy_controls_module
import gui_indicator_follow_routine_settings_dialog as dialog_module
from gui_indicator_follow_routine_settings_dialog import IndicatorFollowRoutineSettingsDialog


ROOT = Path(__file__).resolve().parents[1]
RULES_PATH = next((ROOT / "routines").glob("*/rules.json"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class _FakeSignal:
    def __init__(self) -> None:
        self.callbacks = []

    def connect(self, callback) -> None:
        self.callbacks.append(callback)

    def emit(self) -> None:
        for callback in self.callbacks:
            callback()


class _FakeWidget:
    def __init__(self, *args, **kwargs) -> None:
        self.enabled = True
        self.visible = True
        self.text_value = str(args[0]) if args else ""

    def setEnabled(self, value: bool) -> None:
        self.enabled = bool(value)

    def setVisible(self, value: bool) -> None:
        self.visible = bool(value)

    def setStyleSheet(self, value: str) -> None:
        self.style = value

    def setFixedHeight(self, value: int) -> None:
        self.fixed_height = value

    def setFixedWidth(self, value: int) -> None:
        self.fixed_width = value

    def setContentsMargins(self, *args) -> None:
        self.contents_margins = args

    def setSpacing(self, value: int) -> None:
        self.spacing = value

    def setAlignment(self, value) -> None:
        self.alignment = value

    def setText(self, value: str) -> None:
        self.text_value = value

    def text(self) -> str:
        return self.text_value


class _FakeCheckBox(_FakeWidget):
    def __init__(self, text: str = "") -> None:
        super().__init__(text)
        self.checked = False
        self.toggled = _FakeSignal()

    def setChecked(self, value: bool) -> None:
        self.checked = bool(value)
        self.toggled.emit()

    def isChecked(self) -> bool:
        return self.checked


class _FakeComboBox(_FakeWidget):
    def __init__(self) -> None:
        super().__init__()
        self.items = []
        self.current = ""
        self.currentIndexChanged = _FakeSignal()

    def addItems(self, items) -> None:
        self.items.extend(items)
        if not self.current and self.items:
            self.current = self.items[0]

    def setCurrentText(self, value: str) -> None:
        self.current = str(value)

    def currentText(self) -> str:
        return self.current

    def findText(self, value: str) -> int:
        try:
            return self.items.index(str(value))
        except ValueError:
            return -1

    def setCurrentIndex(self, index: int) -> None:
        self.current = self.items[index]
        self.currentIndexChanged.emit()


class _FakeLineEdit(_FakeWidget):
    pass


class _FakeLayout:
    def __init__(self, *args, **kwargs) -> None:
        self.items = []

    def addWidget(self, widget, *args) -> None:
        self.items.append(("widget", widget, args))

    def addLayout(self, layout, *args) -> None:
        self.items.append(("layout", layout, args))

    def addStretch(self, value: int = 0) -> None:
        self.items.append(("stretch", value))

    def setContentsMargins(self, *args) -> None:
        self.contents_margins = args

    def setSpacing(self, value: int) -> None:
        self.spacing = value


class GuiIndicatorFollowBuyCompositeUiStateTest(unittest.TestCase):
    def setUp(self) -> None:
        self._patches = [
            (dialog_module, "QCheckBox", _FakeCheckBox),
            (dialog_module, "QComboBox", _FakeComboBox),
            (dialog_module, "QLineEdit", _FakeLineEdit),
            (buy_controls_module, "QCheckBox", _FakeCheckBox),
            (buy_controls_module, "QComboBox", _FakeComboBox),
            (buy_controls_module, "QLabel", _FakeWidget),
            (buy_controls_module, "QGroupBox", _FakeWidget),
            (buy_controls_module, "QHBoxLayout", _FakeLayout),
            (buy_controls_module, "QVBoxLayout", _FakeLayout),
        ]
        self._originals = []
        for module, name, replacement in self._patches:
            self._originals.append((module, name, getattr(module, name)))
            setattr(module, name, replacement)

        self.dialog = IndicatorFollowRoutineSettingsDialog.__new__(IndicatorFollowRoutineSettingsDialog)
        self.dialog._buy_exit_time_state_updaters = []
        self.dialog._update_all_buy_method_states = lambda: None
        self.dialog._update_hoga_total = lambda: None
        self.dialog._update_hoga_mode = lambda: None
        self.dialog._update_time_mode = lambda: None
        self.dialog._update_apply_all_enabled = lambda: None
        self.dialog._update_additional_active_state = lambda: None
        self.dialog._update_situation_response_state = lambda: None
        self.dialog._make_buy_composite_filter_controls()

    def tearDown(self) -> None:
        for module, name, original in reversed(self._originals):
            setattr(module, name, original)

    def _set_group_filters(self, group_index: int, filters: set[str]) -> None:
        for name in self.dialog._buy_composite_filter_names():
            getattr(self.dialog, f"buy_composite_group_{group_index}_{name}_check").setChecked(name in filters)

    def _sample_state(self) -> dict:
        return {
            "enabled": True,
            "logic": "AND",
            "include_unreferenced_active_filters": "AND_REQUIRED",
            "groups": [
                {
                    "enabled": True,
                    "logic": "OR",
                    "filters": ["rsi", "price_compare"],
                },
                {
                    "enabled": False,
                    "logic": "AND",
                    "filters": ["bollinger", "ocr"],
                },
            ],
        }

    def test_composite_ui_widgets_are_created(self) -> None:
        expected_names = [
            "buy_composite_enabled_check",
            "buy_composite_logic_combo",
            "buy_composite_include_unreferenced_combo",
            "buy_composite_group_1_enabled_check",
            "buy_composite_group_1_logic_combo",
            "buy_composite_group_1_rsi_check",
            "buy_composite_group_1_moving_average_check",
            "buy_composite_group_1_price_compare_check",
            "buy_composite_group_1_bollinger_check",
            "buy_composite_group_1_ocr_check",
            "buy_composite_group_2_enabled_check",
            "buy_composite_group_2_logic_combo",
            "buy_composite_group_2_rsi_check",
            "buy_composite_group_2_moving_average_check",
            "buy_composite_group_2_price_compare_check",
            "buy_composite_group_2_bollinger_check",
            "buy_composite_group_2_ocr_check",
        ]

        for name in expected_names:
            self.assertTrue(hasattr(self.dialog, name), name)

    def test_default_composite_state_is_collected(self) -> None:
        self.assertEqual(
            self.dialog._collect_buy_composite_ui_state(),
            self.dialog._default_buy_composite_ui_state(),
        )

    def test_enabled_and_logic_are_collected(self) -> None:
        self.dialog.buy_composite_enabled_check.setChecked(True)
        self.dialog.buy_composite_logic_combo.setCurrentText("AND")

        state = self.dialog._collect_buy_composite_ui_state()

        self.assertTrue(state["enabled"])
        self.assertEqual(state["logic"], "AND")
        self.assertEqual(state["include_unreferenced_active_filters"], "AND_REQUIRED")

    def test_group_filter_selection_is_collected(self) -> None:
        self.dialog.buy_composite_enabled_check.setChecked(True)
        self.dialog.buy_composite_group_1_logic_combo.setCurrentText("OR")
        self._set_group_filters(1, {"rsi", "price_compare"})
        self._set_group_filters(2, {"bollinger", "ocr"})

        state = self.dialog._collect_buy_composite_ui_state()

        self.assertEqual(state["groups"][0]["logic"], "OR")
        self.assertEqual(state["groups"][0]["filters"], ["rsi", "price_compare"])
        self.assertEqual(state["groups"][1]["filters"], ["bollinger", "ocr"])

    def test_same_filter_can_be_selected_across_groups(self) -> None:
        self._set_group_filters(1, {"rsi"})
        self._set_group_filters(2, {"rsi", "ocr"})

        state = self.dialog._collect_buy_composite_ui_state()

        self.assertEqual(state["groups"][0]["filters"], ["rsi"])
        self.assertEqual(state["groups"][1]["filters"], ["rsi", "ocr"])

    def test_disabled_state_preserves_child_values(self) -> None:
        state = self._sample_state()
        state["enabled"] = False

        self.dialog._apply_buy_composite_ui_state(state)

        self.assertEqual(self.dialog._collect_buy_composite_ui_state(), state)
        self.assertFalse(self.dialog.buy_composite_group_1_logic_combo.enabled)

    def test_collect_after_restore_round_trip_matches(self) -> None:
        state = self._sample_state()

        result = self.dialog.apply_indicator_follow_ui_state({
            "buy_ui": {
                "signal_filter": {
                    "buy_composite": deepcopy(state),
                }
            }
        })

        self.assertEqual([], result["sync_errors"])
        self.assertEqual(self.dialog._collect_buy_composite_ui_state(), state)

    def test_missing_composite_state_restores_defaults(self) -> None:
        self.dialog._apply_buy_composite_ui_state(self._sample_state())

        self.dialog.apply_indicator_follow_ui_state({"buy_ui": {"signal_filter": {}}})

        self.assertEqual(
            self.dialog._collect_buy_composite_ui_state(),
            self.dialog._default_buy_composite_ui_state(),
        )

    def test_three_or_more_groups_are_not_truncated_into_ui(self) -> None:
        state = self._sample_state()
        original = deepcopy(state)
        self.dialog._apply_buy_composite_ui_state(original)
        state["groups"].append({"enabled": True, "logic": "AND", "filters": ["moving_average"]})

        result = self.dialog.apply_indicator_follow_ui_state({
            "buy_ui": {
                "signal_filter": {
                    "buy_composite": state,
                }
            }
        })

        self.assertEqual(original, self.dialog._collect_buy_composite_ui_state())
        self.assertIn(
            {
                "name": "buy_ui.signal_filter.buy_composite",
                "reason": "unsupported_group_count",
                "groups": 3,
            },
            result["skipped"],
        )
        self.assertTrue(self.dialog.buy_composite_warning_label.visible)
        self.assertEqual(3, len(state["groups"]))

    def test_unknown_filter_is_skipped_without_checking_it(self) -> None:
        state = self._sample_state()
        state["groups"][0]["filters"] = ["unknown", "rsi"]

        result = self.dialog.apply_indicator_follow_ui_state({
            "buy_ui": {
                "signal_filter": {
                    "buy_composite": state,
                }
            }
        })

        self.assertEqual(["rsi"], self.dialog._collect_buy_composite_ui_state()["groups"][0]["filters"])
        self.assertTrue(any(item.get("reason") == "unknown_filter" for item in result["skipped"]))

    def test_collect_indicator_follow_ui_state_includes_composite_and_existing_filters(self) -> None:
        self.dialog.buy_ocr_value_line = _FakeLineEdit("91")
        self.dialog.buy_ma_value_line = _FakeLineEdit("60")
        self.dialog.buy_bollinger_value_line = _FakeLineEdit("0.1")
        self.dialog.buy_rsi_value_line = _FakeLineEdit("45")
        self.dialog.buy_composite_enabled_check.setChecked(True)

        state = self.dialog.collect_indicator_follow_ui_state()
        signal_filter = state["buy_ui"]["signal_filter"]

        self.assertEqual("91", signal_filter["buy_ocr_value_line"])
        self.assertEqual("60", signal_filter["buy_ma_value_line"])
        self.assertEqual("0.1", signal_filter["buy_bollinger_value_line"])
        self.assertEqual("45", signal_filter["buy_rsi_value_line"])
        self.assertEqual(self.dialog._collect_buy_composite_ui_state(), signal_filter["buy_composite"])

    def test_rules_json_is_not_modified_by_collect_or_restore(self) -> None:
        before = _sha256(RULES_PATH)

        self.dialog.collect_indicator_follow_ui_state()
        self.dialog.apply_indicator_follow_ui_state({"buy_ui": {"signal_filter": {"buy_composite": self._sample_state()}}})

        self.assertEqual(before, _sha256(RULES_PATH))


if __name__ == "__main__":
    unittest.main()
