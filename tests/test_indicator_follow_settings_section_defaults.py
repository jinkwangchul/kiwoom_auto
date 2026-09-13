from __future__ import annotations

import copy
import os
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication

from gui_indicator_follow_routine_settings_dialog import (
    IndicatorFollowRoutineSettingsDialog,
)


@unittest.skipIf(
    getattr(QApplication, "__name__", "") == "_QtImportStub",
    "requires real PyQt widgets",
)
class IndicatorFollowSettingsSectionDefaultsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        cls.project_root = Path(__file__).resolve().parents[1]
        cls.routine_dir = cls.project_root / "routines" / "지표추종매매"
        cls.rules_path = cls.routine_dir / "rules.json"

    def _create_dialog(self, settings_mode: str):
        kwargs = {
            "rules_path": self.rules_path,
            "routine_path": self.routine_dir,
            "routine_name": "지표추종매매",
            "definition_id": "indicator_follow",
            "settings_mode": settings_mode,
        }
        if settings_mode == "edit":
            kwargs["instance_id"] = "focused-section-default-test"

        dialog = IndicatorFollowRoutineSettingsDialog(**kwargs)
        self.app.processEvents()
        self.app.processEvents()
        return dialog

    def _assert_initial_section_state(self, dialog) -> None:
        self.assertFalse(dialog.isMaximized())
        self.assertFalse(bool(dialog.windowState() & Qt.WindowMaximized))
        self.assertEqual("buy", dialog._control_section_mode)
        self.assertGreaterEqual(dialog.height(), 720)
        self.assertLessEqual(dialog.height(), 1180)
        screen = dialog.screen()
        self.assertIsNotNone(screen)
        self.assertLessEqual(
            (
                dialog.frameGeometry().center()
                - screen.availableGeometry().center()
            ).manhattanLength(),
            4,
        )
        self.assertTrue(dialog.basic_box.isVisible())
        self.assertTrue(dialog.basic_header_widget.isVisible())
        self.assertTrue(dialog.buy_detail_expanded)
        self.assertTrue(dialog.buy_detail_widget.isVisible())
        self.assertFalse(dialog.sell_detail_expanded)
        self.assertFalse(dialog.sell_detail_widget.isVisible())
        self.assertEqual("▶ 기본설정", dialog.basic_title.text())
        self.assertEqual("▼ 매수설정", dialog.buy_title.text())
        self.assertEqual("▶ 매도설정", dialog.sell_title.text())

    def test_registration_and_edit_open_with_buy_expanded_sell_collapsed(self) -> None:
        for settings_mode, save_text in (("registration", "등록"), ("edit", "변경")):
            with self.subTest(settings_mode=settings_mode):
                dialog = self._create_dialog(settings_mode)
                try:
                    self._assert_initial_section_state(dialog)
                    self.assertEqual(save_text, dialog.save_button.text())
                    self.assertGreater(dialog.reload_button.receivers(dialog.reload_button.clicked), 0)
                    self.assertGreater(
                        dialog.validation_chart_button.receivers(
                            dialog.validation_chart_button.clicked
                        ),
                        0,
                    )
                    self.assertGreater(dialog.save_button.receivers(dialog.save_button.clicked), 0)
                    self.assertGreater(dialog.close_button.receivers(dialog.close_button.clicked), 0)
                finally:
                    dialog.close()
                    self.app.processEvents()

    def test_edit_sell_values_load_while_collapsed_and_survive_visibility_changes(self) -> None:
        dialog = self._create_dialog("edit")
        try:
            self._assert_initial_section_state(dialog)
            self.assertEqual("A and B and C", dialog.sell_signal_expr_line.text())
            before = copy.deepcopy(dialog.collect_indicator_follow_ui_state())

            dialog._toggle_control_section_mode("sell")
            self.app.processEvents()
            self.assertTrue(dialog.sell_detail_widget.isVisible())
            self.assertEqual("A and B and C", dialog.sell_signal_expr_line.text())
            self.assertEqual(before, dialog.collect_indicator_follow_ui_state())

            dialog._toggle_control_section_mode("sell")
            self.app.processEvents()
            self.assertFalse(dialog.sell_detail_widget.isVisible())
            self.assertEqual(before, dialog.collect_indicator_follow_ui_state())
        finally:
            dialog.close()
            self.app.processEvents()

    def test_buy_and_sell_toggle_without_hiding_basic_settings(self) -> None:
        dialog = self._create_dialog("registration")
        try:
            self._assert_initial_section_state(dialog)

            dialog._toggle_control_section_mode("buy")
            self.app.processEvents()
            self.assertFalse(dialog.buy_detail_widget.isVisible())
            self.assertTrue(dialog.basic_box.isVisible())

            dialog._toggle_control_section_mode("buy")
            self.app.processEvents()
            self.assertTrue(dialog.buy_detail_widget.isVisible())
            self.assertTrue(dialog.basic_box.isVisible())

            dialog._toggle_control_section_mode("sell")
            self.app.processEvents()
            self.assertTrue(dialog.sell_detail_widget.isVisible())
            self.assertTrue(dialog.basic_box.isVisible())

            dialog._toggle_control_section_mode("sell")
            self.app.processEvents()
            self.assertFalse(dialog.sell_detail_widget.isVisible())
            self.assertTrue(dialog.basic_box.isVisible())
        finally:
            dialog.close()
            self.app.processEvents()

    def test_section_state_is_not_persisted_between_dialog_instances(self) -> None:
        first = self._create_dialog("edit")
        try:
            first._toggle_control_section_mode("buy")
            first._toggle_control_section_mode("sell")
            self.app.processEvents()
            self.assertEqual("sell", first._control_section_mode)
        finally:
            first.close()
            self.app.processEvents()

        second = self._create_dialog("edit")
        try:
            self._assert_initial_section_state(second)
        finally:
            second.close()
            self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
