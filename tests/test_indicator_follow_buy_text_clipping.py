# -*- coding: utf-8 -*-
from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QPoint, QRect
from PyQt5.QtWidgets import QApplication, QComboBox, QLabel, QStyle

from gui_indicator_follow_routine_settings_dialog import (
    IndicatorFollowRoutineSettingsDialog,
)


ROOT = Path(__file__).resolve().parents[1]
ROUTINE_RULES = next((ROOT / "routines").glob("*/rules.json"))


def _text_width(widget, text: str) -> int:
    metrics = widget.fontMetrics()
    return max(
        metrics.horizontalAdvance(text),
        metrics.boundingRect(text).width(),
    )


def _checkbox_width(widget) -> int:
    style = widget.style()
    return (
        _text_width(widget, widget.text())
        + style.pixelMetric(QStyle.PM_IndicatorWidth, None, widget)
        + style.pixelMetric(QStyle.PM_CheckBoxLabelSpacing, None, widget)
        + 8
    )


class IndicatorFollowBuyTextClippingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.rules_path = Path(self.temp.name) / "rules.json"
        self.rules_path.write_bytes(ROUTINE_RULES.read_bytes())
        self.dialog = IndicatorFollowRoutineSettingsDialog(
            rules_path=self.rules_path
        )
        self.dialog.buy_detail_widget.setVisible(True)
        self.dialog.show()
        self.app.processEvents()

    def tearDown(self) -> None:
        self.dialog.close()
        self.dialog.deleteLater()
        self.temp.cleanup()

    def test_last_round_active_row_keeps_full_text_and_spacing(self) -> None:
        check = self.dialog.buy_last_round_active_check
        label = self.dialog.buy_last_round_active_set_price_label
        row = self.dialog.buy_last_round_active_row_widget

        self.assertEqual("마지막회차 능동매수", check.text())
        self.assertEqual("설정가에 평단이", label.text())
        self.assertGreaterEqual(check.minimumWidth(), _checkbox_width(check))
        self.assertGreaterEqual(
            label.minimumWidth(),
            _text_width(label, label.text()) + 8,
        )
        self.assertGreater(check.maximumWidth(), check.minimumWidth())
        self.assertGreater(label.maximumWidth(), label.minimumWidth())

        # The offscreen Qt platform exposes an 800px virtual screen and cannot
        # honor this dialog's 1600px minimum width.  Exercise the row at its own
        # physical minimum instead of treating that test-host constraint as a
        # production overlap.
        row.layout().setGeometry(
            QRect(0, 0, row.minimumSizeHint().width(), row.height())
        )
        check_right = check.mapTo(row, QPoint(check.width(), 0)).x()
        label_left = label.mapTo(row, QPoint(0, 0)).x()
        self.assertGreaterEqual(
            label_left - check_right,
            row.layout().spacing(),
        )

        self.dialog.buy_base_time_mode_combo.setCurrentText("선택없음")
        self.assertFalse(check.isEnabled())
        self.assertGreaterEqual(check.minimumWidth(), _checkbox_width(check))

    def test_buy_checkbox_text_capacity_is_not_shrinkable(self) -> None:
        checks = (
            self.dialog.buy_base_apply_all_check,
            self.dialog.buy_price_compare_check,
            self.dialog.buy_last_round_active_check,
            self.dialog.buy_price_compare_skip_check,
            self.dialog.buy_additional_active_check,
        )
        for check in checks:
            with self.subTest(text=check.text()):
                self.assertGreaterEqual(check.minimumWidth(), _checkbox_width(check))
                self.assertGreaterEqual(check.width(), check.minimumWidth())

    def test_repeat_active_and_price_compare_text_controls_fit(self) -> None:
        labels = [
            child
            for child in self.dialog.buy_base_active_detail_widget.findChildren(QLabel)
            if child.text()
        ]
        self.assertEqual(
            {"매수가", "대비", "평단가", "%"},
            {label.text() for label in labels},
        )
        for label in labels:
            with self.subTest(text=label.text()):
                self.assertGreaterEqual(label.width(), _text_width(label, label.text()))

        combos = (
            self.dialog.buy_base_hoga_combo,
            self.dialog.buy_base_time_mode_combo,
            self.dialog.buy_base_ratio_direction_combo,
            self.dialog.buy_base_ratio_compare_combo,
            self.dialog.buy_base_detail_mode_combo,
            self.dialog.buy_base_active_direction_combo,
            self.dialog.buy_base_active_compare_combo,
            self.dialog.buy_price_compare_mode_combo,
            self.dialog.buy_price_compare_above_mode_combo,
        )
        for combo in combos:
            self.assertIsInstance(combo, QComboBox)
            with self.subTest(items=[combo.itemText(i) for i in range(combo.count())]):
                self.assertGreaterEqual(combo.width(), combo.sizeHint().width())

    def test_ui_only_width_change_preserves_active_buy_mode_contract(self) -> None:
        repeat_index = self.dialog.buy_base_detail_mode_combo.findText("능동매수")
        price_compare_index = (
            self.dialog.buy_price_compare_above_mode_combo.findText("능동매수")
        )
        self.assertTrue(
            self.dialog.buy_base_detail_mode_combo.model().item(repeat_index).isEnabled()
        )
        self.assertFalse(
            self.dialog.buy_price_compare_above_mode_combo.model()
            .item(price_compare_index)
            .isEnabled()
        )

        self.dialog.buy_base_ratio_direction_combo.setCurrentText("상하")
        self.assertEqual("이내", self.dialog.buy_base_ratio_compare_combo.currentText())
        self.dialog.buy_situation_response_direction_combo.setCurrentText("상하")
        self.assertEqual(
            "이내",
            self.dialog.buy_situation_response_compare_combo.currentText(),
        )


if __name__ == "__main__":
    unittest.main()
