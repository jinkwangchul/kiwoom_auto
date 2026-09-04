# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QDialog

from gui_indicator_follow_routine_settings_dialog import IndicatorFollowRoutineSettingsDialog
from tests.qt_test_support import create_qt_widget_shell, dispose_qt_widget


class BuyConnectedUiActivationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dialog = create_qt_widget_shell(IndicatorFollowRoutineSettingsDialog, QDialog)

    def tearDown(self) -> None:
        dispose_qt_widget(self.dialog)

    def test_connected_additional_policy_is_enabled_with_details_gated(self) -> None:
        box = self.dialog._make_buy_method_overview_controls(("additional",))
        self.addCleanup(dispose_qt_widget, box)
        self.assertTrue(self.dialog.buy_price_compare_skip_row_widget.isEnabled())
        self.assertTrue(self.dialog.buy_price_compare_skip_check.isEnabled())
        self.assertFalse(self.dialog.buy_price_compare_skip_direction_combo.isEnabled())
        self.dialog.buy_price_compare_skip_check.setChecked(True)
        self.assertTrue(self.dialog.buy_price_compare_skip_direction_combo.isEnabled())

        self.assertTrue(self.dialog.buy_additional_active_check.isEnabled())
        self.assertFalse(self.dialog.buy_additional_active_method_combo.isEnabled())
        self.dialog.buy_additional_active_check.setChecked(True)
        self.assertTrue(self.dialog.buy_additional_active_method_combo.isEnabled())
        self.assertFalse(self.dialog.buy_additional_active_direction_combo.isEnabled())
        self.dialog.buy_additional_active_method_combo.setCurrentText("능동")
        self.assertTrue(self.dialog.buy_additional_active_direction_combo.isEnabled())

    def test_connected_cycle_is_enabled_but_cancel_batch_remains_reserved(self) -> None:
        box = self.dialog._make_buy_avg_overview_controls(("cycle",))
        self.addCleanup(dispose_qt_widget, box)
        self.assertTrue(self.dialog.buy_cycle_column_widget.isEnabled())
        self.assertTrue(self.dialog.buy_cycle_hoga_mode_combo.isEnabled())
        reset_index = self.dialog.buy_cycle_price_action_combo.findText("매수리셋")
        cancel_index = self.dialog.buy_cycle_price_action_combo.findText("일괄취소")
        self.assertTrue(self.dialog.buy_cycle_price_action_combo.model().item(reset_index).isEnabled())
        self.assertFalse(self.dialog.buy_cycle_price_action_combo.model().item(cancel_index).isEnabled())
        self.assertIn(
            "CYCLE_OPTION_EXECUTION_NOT_CONNECTED",
            self.dialog.buy_cycle_price_action_combo.toolTip(),
        )


if __name__ == "__main__":
    unittest.main()
