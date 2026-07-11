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

import gui_indicator_follow_routine_settings_dialog as dialog_module


ROOT = Path(__file__).resolve().parents[1]
RULES_PATH = next((ROOT / "routines").glob("*/rules.json"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class _FakeLine:
    def __init__(self, value: str = "") -> None:
        self.value = value

    def setText(self, value: str) -> None:
        self.value = value

    def text(self) -> str:
        return self.value


class IndicatorFollowBuyExprRestoreTest(unittest.TestCase):
    def test_normalize_restores_test_expression_to_default(self):
        state = {"basic": {"buy_signal_expr_line": "RESTORE_TEST_BUY_EXPR"}}

        normalized = dialog_module.normalize_indicator_follow_basic_ui_state(state)

        self.assertEqual(
            dialog_module.DEFAULT_BUY_SIGNAL_EXPR,
            normalized["basic"]["buy_signal_expr_line"],
        )
        self.assertEqual("RESTORE_TEST_BUY_EXPR", state["basic"]["buy_signal_expr_line"])

    def test_normalize_blank_expression_to_default(self):
        state = {"basic": {"buy_signal_expr_line": ""}}

        normalized = dialog_module.normalize_indicator_follow_basic_ui_state(state)

        self.assertEqual(
            dialog_module.DEFAULT_BUY_SIGNAL_EXPR,
            normalized["basic"]["buy_signal_expr_line"],
        )

    def test_existing_normal_user_expression_is_preserved(self):
        state = {"basic": {"buy_signal_expr_line": "A or B"}}

        normalized = dialog_module.normalize_indicator_follow_basic_ui_state(state)

        self.assertEqual("A or B", normalized["basic"]["buy_signal_expr_line"])

    def test_apply_ui_state_restores_expression_without_writing_rules_json(self):
        before = _sha256(RULES_PATH)
        dialog = dialog_module.IndicatorFollowRoutineSettingsDialog.__new__(
            dialog_module.IndicatorFollowRoutineSettingsDialog
        )
        dialog.buy_signal_expr_line = _FakeLine()
        applied = []

        def apply_named(values, *, result):
            if "buy_signal_expr_line" in values:
                dialog.buy_signal_expr_line.setText(values["buy_signal_expr_line"])
                applied.append("buy_signal_expr_line")

        dialog._apply_named_ui_values = apply_named
        dialog._apply_prefixed_ui_values = lambda *args, **kwargs: None
        dialog._apply_existing_prefixed_ui_values = lambda *args, **kwargs: None
        dialog._sync_indicator_follow_ui_after_apply = lambda: []

        result = dialog.apply_indicator_follow_ui_state({
            "basic": {"buy_signal_expr_line": "RESTORE_TEST_BUY_EXPR"},
        })

        self.assertEqual([], result["sync_errors"])
        self.assertEqual(["buy_signal_expr_line"], applied)
        self.assertEqual(dialog_module.DEFAULT_BUY_SIGNAL_EXPR, dialog.buy_signal_expr_line.text())
        self.assertEqual(before, _sha256(RULES_PATH))

    def test_save_reload_state_keeps_normal_expression_in_memory(self):
        state = {"basic": {"buy_signal_expr_line": "A"}}
        original = deepcopy(state)

        first = dialog_module.normalize_indicator_follow_basic_ui_state(state)
        second = dialog_module.normalize_indicator_follow_basic_ui_state(first)

        self.assertEqual("A", first["basic"]["buy_signal_expr_line"])
        self.assertEqual(first, second)
        self.assertEqual(original, state)

    def test_rules_json_no_restore_test_expression_after_restore(self):
        text = RULES_PATH.read_text(encoding="utf-8")

        self.assertNotIn("RESTORE_TEST_BUY_EXPR", text)
        self.assertIn('"buy_signal_expr_line": "A and B and C and D"', text)


if __name__ == "__main__":
    unittest.main()
