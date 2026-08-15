from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont, QFontMetrics
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QGroupBox

import gui_windows


class _Settings:
    def __init__(self) -> None:
        self.values = {}

    def value(self, key, default=""):
        return self.values.get(key, default)

    def setValue(self, key, value) -> None:
        self.values[key] = value

    def sync(self) -> None:
        return None


class MainTopThreePartLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _create_window(self):
        api = SimpleNamespace(
            unavailable_reason=lambda: "test double",
            login_state_changed=None,
            raw_chejan_received=None,
        )
        with (
            patch.object(gui_windows, "KiwoomApi", return_value=api),
            patch.object(gui_windows, "normalize_base_stock_single_routine_file"),
            patch.object(gui_windows.MainWindow, "refresh_startup_recovery_status", return_value={}),
            patch.object(gui_windows.MainWindow, "refresh_all"),
            patch.object(gui_windows, "append_owner_event_once"),
        ):
            window = gui_windows.MainWindow()
        window._account_memo_settings = _Settings()
        return window

    def test_top_row_uses_three_named_parts_and_existing_widgets(self) -> None:
        window = self._create_window()
        try:
            window.resize(1920, 720)
            window.show()
            self.app.processEvents()

            connection, basic, budget = window._main_top_part_boxes
            self.assertEqual(
                (
                    "시스템",
                    "현황정보",
                    "예산설정",
                ),
                (connection.title(), basic.title(), budget.title()),
            )
            self.assertEqual(
                (
                    "mainServerConnectionStatusPart",
                    "mainServerBasicInfoPart",
                    "mainBudgetSettingPart",
                ),
                (connection.objectName(), basic.objectName(), budget.objectName()),
            )
            self.assertTrue(all(isinstance(part, QGroupBox) for part in window._main_top_part_boxes))

            self.assertIs(connection, window.btn_kiwoom_login.parentWidget())
            self.assertIs(basic, window.btn_emergency_stop.parentWidget())
            self.assertEqual(-1, connection.layout().indexOf(window.btn_emergency_stop))
            self.assertGreaterEqual(
                basic.layout().indexOf(window.btn_emergency_stop),
                0,
            )
            emergency_layout_index = basic.layout().indexOf(
                window.btn_emergency_stop
            )
            self.assertEqual(
                (0, 0, 2, 1),
                basic.layout().getItemPosition(emergency_layout_index),
            )
            self.assertEqual(
                basic.layout().contentsRect().left(),
                window.btn_emergency_stop.geometry().left(),
            )
            self.assertTrue(window.auto_status_label.isHidden())
            self.assertTrue(window.buy_time_status_label.isHidden())
            self.assertIsNone(window.auto_status_label.parentWidget())
            self.assertIsNone(window.buy_time_status_label.parentWidget())
            self.assertIs(connection, window.account_info_widget.parentWidget())
            self.assertEqual("계좌정보 :", window.account_label.text())
            self.assertIs(window.account_info_widget, window.account_label.parentWidget())
            self.assertIs(window.account_info_widget, window.account_combo.parentWidget())
            self.assertIs(
                window.account_info_widget,
                window.account_memo_edit.parentWidget(),
            )
            self.assertFalse(hasattr(window, "account_memo_separator"))
            self.assertEqual(8, window.account_memo_edit.maxLength())
            self.assertEqual(
                0,
                window.account_info_widget.layout().spacing(),
            )
            self.assertEqual(
                window.account_label.width(),
                window.account_auth_label.width(),
            )
            self.assertEqual(
                window.account_label.width(),
                window.account_query_status_label.width(),
            )
            account_combo_x = window.account_combo.mapTo(
                connection,
                window.account_combo.rect().topLeft(),
            ).x()
            value_x = account_combo_x + 10
            self.assertEqual(
                value_x,
                window.account_auth_neutral_label.mapTo(
                    connection,
                    window.account_auth_neutral_label.rect().topLeft(),
                ).x(),
            )
            self.assertEqual(
                value_x,
                window.account_query_neutral_label.mapTo(
                    connection,
                    window.account_query_neutral_label.rect().topLeft(),
                ).x(),
            )
            self.assertEqual(
                8,
                account_combo_x
                - (
                    window.account_label.mapTo(
                        connection,
                        window.account_label.rect().topLeft(),
                    ).x()
                    + window.account_label.width()
                ),
            )
            window.account_auth_neutral_label.hide()
            window.account_auth_done_label.hide()
            window.btn_account_authentication.show()
            window.account_query_neutral_label.hide()
            window.account_query_normal_label.hide()
            window.btn_account_requery.show()
            self.app.processEvents()
            self.assertEqual(
                value_x,
                window.btn_account_authentication.mapTo(
                    connection,
                    window.btn_account_authentication.rect().topLeft(),
                ).x(),
            )
            self.assertEqual(
                value_x,
                window.btn_account_requery.mapTo(
                    connection,
                    window.btn_account_requery.rect().topLeft(),
                ).x(),
            )
            self.assertIsNone(window.account_type_label.parentWidget())
            self.assertTrue(window.account_type_label.isHidden())
            self.assertIs(connection, window.login_status_label.parentWidget())
            self.assertTrue(window.login_status_label.isHidden())
            for widget in (
                window.account_total_deposit_label,
                window.account_order_available_label,
            ):
                self.assertIs(basic, widget.parentWidget())
            self.assertEqual(
                window.account_total_deposit_title_label.width(),
                window.account_order_available_title_label.width(),
            )
            system_button_text_gap = (
                window.account_label.mapTo(
                    connection,
                    window.account_label.rect().topLeft(),
                ).x()
                - window.btn_kiwoom_login.geometry().right()
                - 1
            )
            basic_button_text_gap = (
                window.account_total_deposit_title_label.geometry().left()
                - window.btn_emergency_stop.geometry().right()
                - 1
            )
            self.assertEqual(8, system_button_text_gap)
            self.assertEqual(system_button_text_gap, basic_button_text_gap)
            self.assertEqual(
                window.account_total_deposit_label.width(),
                window.account_order_available_label.width(),
            )
            self.assertEqual(
                Qt.AlignCenter,
                window.account_total_deposit_label.alignment(),
            )
            self.assertEqual(
                Qt.AlignCenter,
                window.account_order_available_label.alignment(),
            )
            self.assertEqual(
                window.account_total_deposit_label.geometry().right(),
                window.account_order_available_label.geometry().right(),
            )
            fund_metrics = QFontMetrics(QFont("Malgun Gothic", 12, QFont.Bold))
            removed_currency_width = (
                fund_metrics.horizontalAdvance("500,000,000원")
                - fund_metrics.horizontalAdvance("500,000,000")
            )
            self.assertEqual(
                20 + removed_currency_width,
                window.account_total_deposit_label.geometry().left()
                - window.account_total_deposit_title_label.geometry().right()
                - 1,
            )
            self.assertEqual(
                20 + removed_currency_width,
                window.account_order_available_label.geometry().left()
                - window.account_order_available_title_label.geometry().right()
                - 1,
            )
            self.assertEqual(
                fund_metrics.horizontalAdvance("500,000,000") + 8,
                window.account_total_deposit_label.width(),
            )
            self.assertGreaterEqual(
                basic.rect().right()
                - window.account_total_deposit_label.geometry().right(),
                12,
            )
            self.assertLessEqual(
                basic.rect().right()
                - window.account_total_deposit_label.geometry().right(),
                16,
            )
            for widget in (
                window.budget_total_label,
                window.budget_available_label,
                window.budget_reserve_label,
            ):
                self.assertIs(budget, widget.parentWidget())
            self.assertEqual(
                3,
                sum(
                    widget.isVisible()
                    for widget in (
                        window.budget_total_label,
                        window.budget_available_label,
                        window.budget_reserve_label,
                    )
                ),
            )
            for widget in (
                window.budget_used_label,
                window.budget_usage_rate_label,
                window.budget_routine_count_label,
                window.budget_stock_count_label,
                window.budget_status_label,
            ):
                self.assertTrue(widget.isHidden())
                self.assertIsNone(widget.parentWidget())
            self.assertEqual(
                window.budget_total_title_label.width(),
                window.budget_available_title_label.width(),
            )
            self.assertEqual(
                window.budget_total_title_label.width(),
                window.budget_reserve_title_label.width(),
            )
            self.assertEqual(
                window.budget_total_label.width(),
                window.budget_available_label.width(),
            )
            self.assertEqual(
                window.budget_total_label.width(),
                window.budget_reserve_label.width(),
            )
            self.assertEqual(
                window.budget_total_label.geometry().right(),
                window.budget_available_label.geometry().right(),
            )
            self.assertEqual(
                window.budget_total_label.geometry().right(),
                window.budget_reserve_label.geometry().right(),
            )

            self.assertGreaterEqual(window.btn_kiwoom_login.receivers(window.btn_kiwoom_login.clicked), 1)
            self.assertGreaterEqual(
                window.account_combo.receivers(
                    window.account_combo.currentIndexChanged
                ),
                1,
            )
            self.assertEqual(
                0,
                window.btn_emergency_stop.receivers(window.btn_emergency_stop.clicked),
            )
            self.assertGreaterEqual(
                window.btn_emergency_stop.receivers(
                    window.btn_emergency_stop.doubleClicked
                ),
                1,
            )
            self.assertEqual(
                window.btn_kiwoom_login.size(),
                window.btn_emergency_stop.size(),
            )
            self.assertEqual(92, window.btn_emergency_stop.width())
            self.assertEqual(92, window.btn_emergency_stop.height())
            self.assertEqual(
                window.btn_kiwoom_login.mapTo(window, window.btn_kiwoom_login.rect().topLeft()).y(),
                window.btn_emergency_stop.mapTo(window, window.btn_emergency_stop.rect().topLeft()).y(),
            )
            self.assertIn("background: #DC2626", window.btn_emergency_stop.styleSheet())
            self.assertIn("border-radius: 6px", window.btn_emergency_stop.styleSheet())
            self.assertFalse(
                window.btn_emergency_stop.geometry().intersects(
                    window.account_total_deposit_label.geometry()
                )
            )
            self.assertFalse(
                window.btn_emergency_stop.geometry().intersects(
                    window.account_order_available_label.geometry()
                )
            )
            self.assertLess(
                window.btn_emergency_stop.geometry().right(),
                window.account_total_deposit_label.geometry().left(),
            )
            self.assertLess(
                window.btn_emergency_stop.geometry().right(),
                window.account_order_available_label.geometry().left(),
            )
            self.assertEqual(
                "더블클릭하여 긴급정지",
                window.btn_emergency_stop.toolTip(),
            )

            self.assertEqual(connection.geometry().top(), basic.geometry().top())
            self.assertEqual(basic.geometry().top(), budget.geometry().top())
            self.assertEqual(connection.geometry().bottom(), basic.geometry().bottom())
            self.assertEqual(basic.geometry().bottom(), budget.geometry().bottom())
            for part in window._main_top_part_boxes:
                self.assertGreaterEqual(part.height(), part.minimumSizeHint().height())
            self.assertLess(connection.geometry().right(), basic.geometry().left())
            self.assertLess(basic.geometry().right(), budget.geometry().left())
            self.assertLess(basic.width(), connection.width())
            self.assertGreater(budget.width(), basic.width())
            routine_table_top = window.routine_table.mapTo(
                window,
                window.routine_table.rect().topLeft(),
            ).y()
            self.assertLess(
                budget.geometry().bottom(),
                routine_table_top,
            )
        finally:
            window.close()
            window.deleteLater()
            self.app.processEvents()

    def test_emergency_stop_requires_double_click_and_reuses_existing_handler(self) -> None:
        with patch.object(gui_windows, "emergency_on_emergency_stop_clicked") as handler:
            window = self._create_window()
            try:
                window.resize(1920, 720)
                window.show()
                self.app.processEvents()

                QTest.mouseClick(window.btn_emergency_stop, Qt.LeftButton)
                self.app.processEvents()
                handler.assert_not_called()

                QTest.mouseDClick(window.btn_emergency_stop, Qt.LeftButton)
                self.app.processEvents()
                handler.assert_called_once_with(window)
            finally:
                window.close()
                window.deleteLater()
                self.app.processEvents()

    def test_login_state_button_reuses_one_widget_for_all_visual_states(self) -> None:
        window = self._create_window()
        try:
            window.resize(1920, 720)
            window.show()
            self.app.processEvents()
            button = window.btn_kiwoom_login
            self.assertEqual((92, 92), (button.width(), button.height()))
            self.assertEqual(button.width(), button.height())
            self.assertEqual("키움\n로그인", button.text())
            self.assertEqual("DISCONNECTED", button.property("kiwoomLoginState"))
            self.assertTrue(button.isEnabled())
            self.assertEqual(Qt.PointingHandCursor, button.cursor().shape())

            contracts = (
                ("LOGIN_IN_PROGRESS", "로그인\n중...", "#E2E8F0", "#94A3B8", "#334155"),
                ("REAL_CONNECTED", "실전\n연결됨", "#F97316", "#F97316", "#FFFFFF"),
                ("SIMULATION_CONNECTED", "모의\n연결됨", "#FACC15", "#FACC15", "#1E3A8A"),
            )
            for state, text, background, border, color in contracts:
                with self.subTest(state=state):
                    window._apply_kiwoom_login_button_state(state)
                    style = button.styleSheet().upper()
                    self.assertEqual(text, button.text())
                    self.assertEqual(state, button.property("kiwoomLoginState"))
                    self.assertIn(background, style)
                    self.assertIn(border, style)
                    self.assertIn(color, style)
                    self.assertEqual((92, 92), (button.width(), button.height()))
                    self.assertFalse(button.isEnabled())
                    self.assertEqual(Qt.ArrowCursor, button.cursor().shape())

            window.kiwoom_api = SimpleNamespace(account_server_type=lambda: "SIMULATION")
            window._apply_connected_kiwoom_login_button_state()
            self.assertEqual("모의\n연결됨", button.text())
            self.assertFalse(button.isEnabled())
            window.kiwoom_api = SimpleNamespace(account_server_type=lambda: "REAL")
            window._apply_connected_kiwoom_login_button_state()
            self.assertEqual("실전\n연결됨", button.text())
            self.assertFalse(button.isEnabled())

            connection = window._main_top_part_boxes[0]
            button_top_left = button.mapTo(connection, button.rect().topLeft())
            self.assertEqual(connection.layout().contentsRect().left(), button_top_left.x())
            self.assertEqual(connection.layout().contentsRect().top(), button_top_left.y())
        finally:
            window.close()
            window.deleteLater()
            self.app.processEvents()

    def test_existing_login_handlers_drive_the_same_state_button(self) -> None:
        window = self._create_window()
        try:
            api = SimpleNamespace(
                is_available=lambda: True,
                unavailable_reason=lambda: "",
                is_connected=lambda: False,
                login=lambda: {
                    "ok": True,
                    "status": "login_requested",
                    "connected": False,
                },
                account_server_type=lambda: "SIMULATION",
            )
            window.kiwoom_api = api
            with patch.object(window, "refresh_kiwoom_accounts", return_value=[]):
                window.login_kiwoom_manually()
            self.assertEqual("로그인\n중...", window.btn_kiwoom_login.text())
            self.assertFalse(window.btn_kiwoom_login.isEnabled())

            with (
                patch.object(window, "refresh_kiwoom_accounts", return_value=[]),
                patch.object(window, "sync_account_funds_selection"),
                patch.object(window, "start_production_recovery"),
            ):
                window.on_kiwoom_login_state_changed({"connected": True})
            self.assertEqual("모의\n연결됨", window.btn_kiwoom_login.text())
            self.assertFalse(window.btn_kiwoom_login.isEnabled())

            window.account_combo.addItem("1234567890")
            window.account_combo.setEnabled(True)
            window._production_recovery_identity = object()
            window._production_recovery_parts = {"holdings": object()}
            with (
                patch.object(window, "_stop_production_recovery_timers") as stop_timers,
                patch.object(window, "_production_recovery_status_result") as recovery_status,
                patch.object(
                    gui_windows.production_recovery_registry,
                    "invalidate",
                ) as invalidate,
            ):
                window.on_kiwoom_login_state_changed({"connected": False})
            self.assertEqual("키움\n로그인", window.btn_kiwoom_login.text())
            self.assertTrue(window.btn_kiwoom_login.isEnabled())
            self.assertEqual(0, window.account_combo.count())
            self.assertFalse(window.account_combo.isEnabled())
            self.assertEqual(
                gui_windows.ACCOUNT_FUNDS_DISCONNECTED,
                window._account_funds_projection.snapshot.status,
            )
            self.assertEqual("미연결", window.account_total_deposit_label.text())
            self.assertEqual("미연결", window.account_order_available_label.text())
            self.assertIsNone(window._production_recovery_identity)
            self.assertEqual({}, window._production_recovery_parts)
            stop_timers.assert_called_once_with()
            invalidate.assert_called_once_with("login disconnected")
            recovery_status.assert_called_once_with()
        finally:
            window.close()
            window.deleteLater()
            self.app.processEvents()

    def test_button_only_invokes_login_while_disconnected(self) -> None:
        window = self._create_window()
        try:
            login = Mock(
                return_value={
                    "ok": True,
                    "status": "login_requested",
                    "connected": False,
                }
            )
            window.kiwoom_api = SimpleNamespace(
                is_available=lambda: True,
                unavailable_reason=lambda: "",
                is_connected=lambda: False,
                login=login,
                account_server_type=lambda: "REAL",
            )
            with patch.object(window, "refresh_kiwoom_accounts", return_value=[]):
                window.btn_kiwoom_login.click()
                window.btn_kiwoom_login.click()
            login.assert_called_once_with()
            self.assertEqual(
                "LOGIN_IN_PROGRESS",
                window.btn_kiwoom_login.property("kiwoomLoginState"),
            )
            self.assertFalse(window.btn_kiwoom_login.isEnabled())

            window._apply_kiwoom_login_button_state("REAL_CONNECTED")
            window.btn_kiwoom_login.click()
            login.assert_called_once_with()
            self.assertFalse(window.btn_kiwoom_login.isEnabled())

            window._apply_kiwoom_login_button_state("SIMULATION_CONNECTED")
            window.btn_kiwoom_login.click()
            login.assert_called_once_with()
            self.assertFalse(window.btn_kiwoom_login.isEnabled())
        finally:
            window.close()
            window.deleteLater()
            self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
