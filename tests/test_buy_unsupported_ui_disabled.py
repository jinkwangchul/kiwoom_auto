# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QDialog

from gui_indicator_follow_routine_settings_dialog import IndicatorFollowRoutineSettingsDialog
from tests.qt_test_support import create_qt_widget_shell, dispose_qt_widget


class BuyUnsupportedUiDisabledTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dialog = create_qt_widget_shell(IndicatorFollowRoutineSettingsDialog, QDialog)

    def tearDown(self) -> None:
        dispose_qt_widget(self.dialog)

    def test_additional_policy_is_visible_but_explicitly_disabled(self) -> None:
        box = self.dialog._make_buy_method_overview_controls(("additional",))
        self.addCleanup(dispose_qt_widget, box)
        self.assertFalse(self.dialog.buy_price_compare_skip_row_widget.isEnabled())
        self.assertFalse(self.dialog.buy_additional_active_row_widget.isEnabled())
        self.assertFalse(self.dialog.buy_additional_active_check.isEnabled())
        self.assertEqual(
            "현재 지원되지 않는 설정입니다.",
            self.dialog.buy_additional_active_row_widget.toolTip(),
        )
        self.assertEqual(
            "현재 지원되지 않는 설정입니다.",
            self.dialog.buy_additional_active_check.toolTip(),
        )

    def test_average_price_cycle_policy_is_visible_but_explicitly_disabled(self) -> None:
        box = self.dialog._make_buy_avg_overview_controls(("cycle",))
        self.addCleanup(dispose_qt_widget, box)
        self.assertFalse(self.dialog.buy_cycle_column_widget.isEnabled())
        self.assertFalse(self.dialog.buy_cycle_hoga_mode_combo.isEnabled())
        self.assertEqual(
            "현재 지원되지 않는 설정입니다.",
            self.dialog.buy_cycle_column_widget.toolTip(),
        )
        self.assertEqual(
            "현재 지원되지 않는 설정입니다.",
            self.dialog.buy_cycle_hoga_mode_combo.toolTip(),
        )


if __name__ == "__main__":
    unittest.main()
