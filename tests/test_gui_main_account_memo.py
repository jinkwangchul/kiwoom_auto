from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PyQt5.QtCore import QEvent, QObject, QPointF, QSettings, QTimer, Qt, pyqtSignal
from PyQt5.QtGui import QInputMethodEvent, QMouseEvent
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QMenu, QStyleOptionViewItem

from account_funds_foundation import AccountFundsSnapshot
import gui_windows


class _Settings:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def value(self, key: str, default=""):
        return self.values.get(key, default)

    def setValue(self, key: str, value) -> None:
        self.values[key] = value

    def sync(self) -> None:
        return None


class _Api(QObject):
    login_state_changed = pyqtSignal(dict)
    raw_chejan_received = pyqtSignal(dict)
    account_authentication_required = pyqtSignal(dict)

    def __init__(self) -> None:
        super().__init__()
        self.accounts = ["8129123456", "1234567890"]
        self.connected = True
        self.password_window_result = {"ok": True, "status": "REQUESTED"}
        self.password_window_calls = 0
        self.session_id = "LOGIN-SESSION-1"

    def unavailable_reason(self) -> str:
        return ""

    def is_available(self) -> bool:
        return True

    def is_connected(self) -> bool:
        return self.connected

    def account_numbers(self) -> list[str]:
        return list(self.accounts)

    def account_server_type(self) -> str:
        return "REAL"

    def login_session_id(self) -> str:
        return self.session_id

    def show_account_password_window(self) -> dict[str, object]:
        self.password_window_calls += 1
        return dict(self.password_window_result)


class MainAccountMemoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def _new_window(self, api: _Api | None = None):
        active_api = api or _Api()
        with (
            patch.object(gui_windows, "KiwoomApi", return_value=active_api),
            patch.object(gui_windows, "normalize_base_stock_single_routine_file"),
            patch.object(
                gui_windows.MainWindow,
                "refresh_startup_recovery_status",
                return_value={},
            ),
            patch.object(gui_windows.MainWindow, "refresh_all"),
            patch.object(gui_windows, "append_owner_event_once"),
        ):
            return gui_windows.MainWindow()

    def setUp(self) -> None:
        self.api = _Api()
        self.window = self._new_window(self.api)
        self.window._account_memo_settings = _Settings()

    def tearDown(self) -> None:
        self.window.account_combo.hidePopup()
        with patch.object(
            self.window,
            "_confirm_main_window_exit_if_required",
            return_value=True,
        ):
            self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def _select_popup_row(self, row: int) -> None:
        combo = self.window.account_combo
        view = combo.view()
        combo.showPopup()
        self.app.processEvents()
        index = combo.model().index(row, 0)
        QTest.mouseClick(
            view.viewport(),
            Qt.LeftButton,
            pos=view.visualRect(index).center(),
        )
        self.app.processEvents()

    def _prepare_ready_account_funds(self, account: str = "8129123456") -> None:
        self.api.accounts = [account]
        self.window.refresh_kiwoom_accounts()
        self.window.sync_account_funds_selection(connected=True)
        request = self.window._account_funds_projection.begin_request()
        self.assertIsNotNone(request)
        self.assertTrue(
            self.window._account_funds_projection.apply_result(
                request,
                {
                    "ok": True,
                    "account_id": account,
                    "deposit": 1_000_000,
                    "orderable_cash": 900_000,
                    "account_type": "REAL",
                },
            )
        )
        self.window.render_account_funds_snapshot(
            self.window._account_funds_projection.snapshot
        )

    def _recovery_identity(self, account: str = "8129123456"):
        return gui_windows.create_recovery_session_identity(
            login_session_id=self.api.session_id,
            account_no=account,
            trading_day=datetime.now().date().isoformat(),
            requested_at=datetime.now().isoformat(timespec="microseconds"),
        )

    def test_combo_displays_only_masked_account_and_keeps_original_roles(self) -> None:
        self.window.set_account_memo("8129123456", "자동매매")
        self.window.refresh_kiwoom_accounts()
        combo = self.window.account_combo
        self.assertEqual(["8129****", "1234****"], [combo.itemText(0), combo.itemText(1)])
        self.assertNotIn("|", combo.itemText(0))
        self.assertEqual("8129123456", combo.itemData(0, gui_windows.ACCOUNT_NO_ROLE))
        self.assertEqual(
            "자동매매",
            combo.itemData(0, gui_windows.ACCOUNT_POPUP_MEMO_ROLE),
        )
        self.assertTrue(combo.itemData(0, gui_windows.ACCOUNT_ACTIVE_ROLE))

        option = QStyleOptionViewItem()
        combo.view().itemDelegate().initStyleOption(option, combo.model().index(0, 0))
        self.assertEqual("8129****   자동매매", option.text)
        combo.view().itemDelegate().initStyleOption(option, combo.model().index(1, 0))
        self.assertEqual("1234****", option.text)
        combo.showPopup()
        self.app.processEvents()
        self.assertGreater(combo.view().width(), combo.width())
        combo.hidePopup()

        self.api.accounts = ["8129123456"]
        self.window.refresh_kiwoom_accounts()
        self.assertFalse(combo.itemData(1, gui_windows.ACCOUNT_ACTIVE_ROLE))
        self.assertEqual(
            "#9ca3af",
            combo.itemData(1, Qt.ForegroundRole).color().name(),
        )

    def test_popup_captured_memo_press_hides_without_changing_selected_account(self) -> None:
        self.api.accounts = ["8129123456"]
        self.window.refresh_kiwoom_accounts()
        self.window.show()
        self.app.processEvents()
        combo = self.window.account_combo
        selected_before = self.window.selected_account_no()
        combo.showPopup()
        self.app.processEvents()
        self.assertTrue(combo.view().isVisible())

        global_position = self.window.account_memo_edit.mapToGlobal(
            self.window.account_memo_edit.rect().center()
        )
        local_position = combo.view().mapFromGlobal(global_position)
        event = QMouseEvent(
            QEvent.MouseButtonPress,
            QPointF(local_position),
            QPointF(global_position),
            Qt.LeftButton,
            Qt.LeftButton,
            Qt.NoModifier,
        )
        QApplication.sendEvent(combo.view(), event)
        self.app.processEvents()
        self.assertFalse(combo.view().isVisible())
        self.assertEqual(selected_before, self.window.selected_account_no())

    def test_popup_captured_outside_press_hides_once_and_does_not_consume_target(self) -> None:
        self.api.accounts = ["8129123456"]
        self.window.refresh_kiwoom_accounts()
        self.window.show()
        self.app.processEvents()
        combo = self.window.account_combo
        view = combo.view()
        selected_before = self.window.selected_account_no()
        combo.showPopup()
        self.app.processEvents()

        global_position = self.window.btn_emergency_stop.mapToGlobal(
            self.window.btn_emergency_stop.rect().center()
        )
        local_position = view.viewport().mapFromGlobal(global_position)
        event = QMouseEvent(
            QEvent.MouseButtonPress,
            QPointF(local_position),
            QPointF(global_position),
            Qt.LeftButton,
            Qt.LeftButton,
            Qt.NoModifier,
        )
        with patch.object(combo, "hidePopup", wraps=combo.hidePopup) as hide_popup:
            consumed = self.window._account_combo_popup_controller.eventFilter(
                view,
                event,
            )
            self.app.processEvents()
            hide_popup.assert_called_once_with()
        self.assertFalse(consumed)
        self.assertFalse(view.isVisible())
        self.assertEqual(selected_before, self.window.selected_account_no())

    def test_popup_escape_closes_and_scrollbar_press_keeps_popup(self) -> None:
        self.window.refresh_kiwoom_accounts()
        self.window.show()
        self.app.processEvents()
        combo = self.window.account_combo
        view = combo.view()
        selected_before = self.window.selected_account_no()

        combo.showPopup()
        self.app.processEvents()
        QTest.keyClick(view, Qt.Key_Escape)
        self.app.processEvents()
        self.assertFalse(view.isVisible())
        self.assertEqual(selected_before, self.window.selected_account_no())

        view.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        combo.showPopup()
        self.app.processEvents()
        scrollbar = view.verticalScrollBar()
        QTest.mouseClick(scrollbar, Qt.LeftButton, pos=scrollbar.rect().center())
        self.app.processEvents()
        self.assertTrue(view.isVisible())
        self.assertEqual(selected_before, self.window.selected_account_no())
        combo.hidePopup()

    def test_disconnected_ui_is_empty_but_saved_accounts_and_memos_remain(self) -> None:
        self.window.remember_account_numbers(["8129123456", "1234567890"])
        self.window.set_account_memo("8129123456", "자동매매")
        self.api.connected = False
        self.window.refresh_kiwoom_accounts()

        self.assertEqual(0, self.window.account_combo.count())
        self.assertEqual("", self.window.account_combo.currentText())
        self.assertFalse(self.window.account_combo.isEnabled())
        self.assertEqual("", self.window.account_memo_edit.text())
        self.assertEqual("", self.window.account_memo_edit.placeholderText())
        self.assertFalse(self.window.account_memo_edit.isEnabled())
        self.assertEqual("", self.window.selected_account_no())
        self.assertIn("8129123456", self.window.remembered_account_numbers())
        self.assertEqual("자동매매", self.window.account_memos()["8129123456"])

        self.api.connected = True
        self.api.accounts = ["8129123456"]
        self.window.refresh_kiwoom_accounts()
        self.assertEqual("8129****", self.window.account_combo.currentText())
        self.assertEqual("자동매매", self.window.account_memo_edit.text())
        self.assertEqual("8129123456", self.window.selected_account_no())

        self.api.connected = False
        self.window.refresh_kiwoom_accounts()
        self.assertEqual("", self.window.account_combo.currentText())
        self.assertEqual("", self.window.account_memo_edit.text())
        self.assertEqual("", self.window.selected_account_no())
        self.assertEqual("자동매매", self.window.account_memos()["8129123456"])

    def test_memo_line_edit_enter_trim_korean_and_eight_character_limit(self) -> None:
        self.api.accounts = ["8129123456"]
        self.window.refresh_kiwoom_accounts()
        editor = self.window.account_memo_edit
        self.assertEqual(8, editor.maxLength())
        editor.setText("자동매매")
        QTest.keyClick(editor, Qt.Key_Return)
        self.assertEqual("자동매매", self.window.account_memos()["8129123456"])
        editor.setText("가나다라마바사아자")
        self.assertEqual("가나다라마바사아", editor.text())
        QTest.keyClick(editor, Qt.Key_Return)
        self.assertEqual("가나다라마바사아", self.window.account_memos()["8129123456"])
        self.assertEqual("8129****", self.window.account_combo.itemText(0))

        editor.setText("   ")
        QTest.keyClick(editor, Qt.Key_Return)
        self.assertNotIn("8129123456", self.window.account_memos())
        self.assertEqual("", editor.text())

    def test_memo_line_edit_accepts_korean_ime_commit_event(self) -> None:
        self.api.accounts = ["8129123456"]
        self.window.refresh_kiwoom_accounts()
        editor = self.window.account_memo_edit
        editor.clear()
        editor.setFocus()
        event = QInputMethodEvent()
        event.setCommitString("자동매매")
        QApplication.sendEvent(editor, event)
        QTest.keyClick(editor, Qt.Key_Return)
        self.assertEqual("자동매매", editor.text())
        self.assertEqual("자동매매", self.window.account_memos()["8129123456"])

    def test_focus_out_and_account_change_save_previous_then_load_new_memo(self) -> None:
        self.window.set_account_memo("1234567890", "둘째계좌")
        self.window.refresh_kiwoom_accounts()
        self.window.account_combo.setCurrentIndex(0)
        self.window.account_memo_edit.setText("첫계좌")
        self.window.account_combo.setCurrentIndex(1)
        self.assertEqual("첫계좌", self.window.account_memos()["8129123456"])
        self.assertEqual("둘째계좌", self.window.account_memo_edit.text())

        self.window.show()
        self.window.account_memo_edit.setFocus()
        self.app.processEvents()
        self.window.account_memo_edit.setText("포커스저장")
        self.window.btn_emergency_stop.setFocus()
        self.app.processEvents()
        self.assertEqual("포커스저장", self.window.account_memos()["1234567890"])

    def test_inactive_popup_selection_edits_memo_without_changing_production_account(self) -> None:
        self.window.refresh_kiwoom_accounts()
        self.window.account_combo.setCurrentIndex(0)
        self.assertEqual("8129123456", self.window.selected_account_no())
        self.api.accounts = ["8129123456"]
        self.window.refresh_kiwoom_accounts()

        self._select_popup_row(1)
        self.assertEqual("1234567890", self.window._account_memo_edit_account_no)
        self.window.account_memo_edit.setText("과거계좌")
        QTest.keyClick(self.window.account_memo_edit, Qt.Key_Return)
        self.assertEqual("과거계좌", self.window.account_memos()["1234567890"])
        self.assertEqual("8129123456", self.window.selected_account_no())

    def test_real_popup_context_menu_and_confirmed_inactive_delete(self) -> None:
        self.window.show()
        self.window.refresh_kiwoom_accounts()
        self.window.set_account_memo("1234567890", "장기투자")
        self.api.accounts = ["8129123456"]
        self.window.refresh_kiwoom_accounts()
        self._select_popup_row(1)
        combo = self.window.account_combo
        view = combo.view()
        observed: dict[str, object] = {}

        def click_delete_confirmation() -> None:
            for widget in QApplication.topLevelWidgets():
                if isinstance(widget, gui_windows.QMessageBox) and widget.isVisible():
                    observed["question"] = widget.text()
                    observed["default"] = widget.defaultButton().text()
                    buttons = {button.text(): button for button in widget.buttons()}
                    QTest.mouseClick(buttons["삭제"], Qt.LeftButton)

        def click_delete_action() -> None:
            for widget in QApplication.topLevelWidgets():
                if isinstance(widget, QMenu) and widget.isVisible():
                    action = widget.actions()[0]
                    observed["menu"] = action.text()
                    observed["enabled"] = action.isEnabled()
                    QTimer.singleShot(50, click_delete_confirmation)
                    QTest.mouseClick(
                        widget,
                        Qt.LeftButton,
                        pos=widget.actionGeometry(action).center(),
                    )

        combo.showPopup()
        self.app.processEvents()
        inactive_index = combo.model().index(1, 0)
        QTimer.singleShot(50, click_delete_action)
        QTest.mouseClick(
            view.viewport(),
            Qt.RightButton,
            pos=view.visualRect(inactive_index).center(),
        )
        self.assertEqual("정보삭제", observed.get("menu"))
        self.assertTrue(observed.get("enabled"))
        self.assertEqual(
            "저장된 계좌정보를 삭제하시겠습니까?",
            observed.get("question"),
        )
        self.assertEqual("취소", observed.get("default"))
        self.assertNotIn("1234567890", self.window.remembered_account_numbers())
        self.assertNotIn("1234567890", self.window.account_memos())
        self.assertNotEqual("1234567890", self.window._account_memo_edit_account_no)

        def inspect_active_delete_action() -> None:
            for widget in QApplication.topLevelWidgets():
                if isinstance(widget, QMenu) and widget.isVisible():
                    action = widget.actions()[0]
                    observed["active_menu"] = action.text()
                    observed["active_enabled"] = action.isEnabled()
                    widget.close()

        combo.showPopup()
        self.app.processEvents()
        active_index = combo.model().index(0, 0)
        QTimer.singleShot(50, inspect_active_delete_action)
        QTest.mouseClick(
            view.viewport(),
            Qt.RightButton,
            pos=view.visualRect(active_index).center(),
        )
        self.assertEqual("정보삭제", observed.get("active_menu"))
        self.assertFalse(observed.get("active_enabled"))

    def test_memo_persists_on_disk_across_main_window_recreation(self) -> None:
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        settings_path = Path(temporary_directory.name) / "account_ui.ini"
        self.window._account_memo_settings = QSettings(
            str(settings_path),
            QSettings.IniFormat,
        )
        self.api.accounts = ["8129123456"]
        self.window.refresh_kiwoom_accounts()
        self.window.account_memo_edit.setText("자동매매")
        QTest.keyClick(self.window.account_memo_edit, Qt.Key_Return)

        second_api = _Api()
        second_api.accounts = ["8129123456"]
        second_window = self._new_window(second_api)
        try:
            second_window._account_memo_settings = QSettings(
                str(settings_path),
                QSettings.IniFormat,
            )
            second_window.refresh_kiwoom_accounts()
            self.assertEqual("8129****", second_window.account_combo.itemText(0))
            self.assertEqual("자동매매", second_window.account_memo_edit.text())
            self.assertEqual("8129123456", second_window.selected_account_no())
        finally:
            second_window.account_combo.hidePopup()
            with patch.object(
                second_window,
                "_confirm_main_window_exit_if_required",
                return_value=True,
            ):
                second_window.close()

    def test_layout_is_compact_and_funds_recovery_use_original_account(self) -> None:
        self.api.accounts = ["8129123456"]
        self.window.refresh_kiwoom_accounts()
        self.window.show()
        self.app.processEvents()
        label_gap = (
            self.window.account_combo.geometry().left()
            - self.window.account_label.geometry().right()
            - 1
        )
        self.assertGreaterEqual(label_gap, 0)
        self.assertLessEqual(label_gap, 8)
        combo_memo_gap = (
            self.window.account_memo_edit.geometry().left()
            - self.window.account_combo.geometry().right()
            - 1
        )
        self.assertGreaterEqual(combo_memo_gap, 0)
        self.assertLessEqual(combo_memo_gap, 4)
        self.assertEqual(
            self.window.account_combo.fontMetrics().horizontalAdvance("8129****")
            + 20,
            self.window.account_combo.width(),
        )
        self.assertEqual(8, self.window.account_memo_edit.maxLength())
        self.assertFalse(self.window.account_memo_edit.hasFrame())
        memo_style = self.window.account_memo_edit.styleSheet()
        self.assertIn("border: none", memo_style)
        self.assertIn("background: transparent", memo_style)
        dashboard_style = self.window.centralWidget().styleSheet()
        self.assertIn("QComboBox#kiwoomAccountCombo", dashboard_style)
        self.assertIn("padding-left: 10px", dashboard_style)
        self.assertIn("padding-right: 10px", dashboard_style)
        self.assertIn("QComboBox#kiwoomAccountCombo::drop-down", dashboard_style)
        self.assertIn("QComboBox#kiwoomAccountCombo::down-arrow", dashboard_style)
        self.assertIn("background: transparent", dashboard_style)
        self.assertIn("border: none", dashboard_style)
        self.assertFalse(hasattr(self.window, "account_memo_separator"))

        funds_adapter = SimpleNamespace(set_active_account=Mock())
        self.window.account_funds_adapter = funds_adapter
        self.window.sync_account_funds_selection(connected=True)
        funds_adapter.set_active_account.assert_called_once_with("8129123456")
        self.assertEqual(
            "8129123456",
            self.window.main_monitoring_auto_trade_operation_host()._selected_account_no(),
        )
        self.assertNotIn("|", self.window.selected_account_no())

    def test_account_information_uses_three_contiguous_rows(self) -> None:
        self.api.accounts = ["8129123456"]
        self.window.set_account_memo("8129123456", "자동매매가상")
        self.window.refresh_kiwoom_accounts()
        self.window.resize(2137, 720)
        self.window.show()
        self.app.processEvents()

        account_widgets = (
            self.window.account_label,
            self.window.account_combo,
            self.window.account_memo_edit,
        )
        self.assertTrue(all(widget.isVisible() for widget in account_widgets))
        gaps = tuple(
            right.geometry().left() - left.geometry().right() - 1
            for left, right in zip(account_widgets, account_widgets[1:])
        )
        self.assertEqual((8, 4), gaps)
        account_combo_x = self.window.account_combo.mapTo(
            self.window.account_info_widget,
            self.window.account_combo.rect().topLeft(),
        ).x()
        auth_value_widget = next(
            widget
            for widget in (
                self.window.account_auth_neutral_label,
                self.window.account_auth_done_label,
                self.window.btn_account_authentication,
            )
            if widget.isVisible()
        )
        query_value_widget = next(
            widget
            for widget in (
                self.window.account_query_neutral_label,
                self.window.account_query_normal_label,
                self.window.btn_account_requery,
            )
            if widget.isVisible()
        )
        auth_value_x = auth_value_widget.mapTo(
            self.window.account_auth_widget,
            auth_value_widget.rect().topLeft(),
        ).x()
        query_value_x = query_value_widget.mapTo(
            self.window.account_query_status_widget,
            query_value_widget.rect().topLeft(),
        ).x()
        self.assertEqual(account_combo_x + 10, auth_value_x)
        self.assertEqual(account_combo_x + 10, query_value_x)
        self.assertTrue(self.window.account_auth_separator.isHidden())
        self.assertTrue(self.window.account_auth_label.isVisible())
        self.assertTrue(self.window.account_query_status_label.isVisible())
        self.assertLess(
            self.window.account_info_widget.geometry().top(),
            self.window.account_auth_widget.geometry().top(),
        )
        self.assertLess(
            self.window.account_auth_widget.geometry().top(),
            self.window.account_query_status_widget.geometry().top(),
        )

    def test_account_type_is_removed_from_visible_system_layout(self) -> None:
        self.assertEqual("계좌정보 :", self.window.account_label.text())
        self.assertTrue(self.window.account_type_label.isHidden())

    def test_account_authentication_ui_uses_message_and_ready_evidence_only(self) -> None:
        self.assertTrue(self.window.account_auth_separator.isHidden())
        self.assertFalse(self.window.account_auth_label.isHidden())
        self.assertFalse(self.window.account_auth_neutral_label.isHidden())
        self.assertFalse(self.window.account_query_status_label.isHidden())
        self.assertFalse(self.window.account_query_neutral_label.isHidden())
        self.window.refresh_kiwoom_accounts()
        self.window.account_combo.setCurrentIndex(0)
        self.assertTrue(self.window.account_auth_separator.isHidden())
        self.assertFalse(self.window.account_auth_label.isHidden())
        self.assertTrue(self.window.account_auth_neutral_label.isHidden())
        self.assertFalse(self.window.btn_account_authentication.isHidden())

        self.api.account_authentication_required.emit(
            {
                "account_id": "8129123456",
                "error_kind": gui_windows.ACCOUNT_AUTHENTICATION_REQUIRED,
            }
        )
        self.assertTrue(self.window.account_auth_separator.isHidden())
        self.assertEqual("계좌인증 :", self.window.account_auth_label.text())
        self.assertFalse(self.window.btn_account_authentication.isHidden())
        self.assertTrue(self.window.btn_account_authentication.isEnabled())
        self.assertTrue(self.window.account_auth_done_label.isHidden())
        authentication_style = self.window.btn_account_authentication.styleSheet()
        self.assertIn("background: transparent", authentication_style)
        self.assertIn("color: #DC2626", authentication_style)
        self.assertIn("border: 1px solid #DC2626", authentication_style)
        self.assertIn("background: #FEF2F2", authentication_style)

        ready = AccountFundsSnapshot(
            account_id="8129123456",
            status=gui_windows.ACCOUNT_FUNDS_READY,
            deposit=1_000_000,
            orderable_cash=900_000,
            account_type="REAL",
        )
        self.window.render_account_funds_snapshot(ready)
        self.assertTrue(self.window.btn_account_authentication.isHidden())
        self.assertTrue(self.window.account_auth_neutral_label.isHidden())
        self.assertFalse(self.window.account_auth_done_label.isHidden())
        self.assertEqual("완료", self.window.account_auth_done_label.text())

    def test_account_authentication_state_is_account_specific(self) -> None:
        self.window.refresh_kiwoom_accounts()
        self.window._account_authentication_states = {
            "8129123456": gui_windows.ACCOUNT_FUNDS_READY,
            "1234567890": gui_windows.ACCOUNT_AUTHENTICATION_REQUIRED,
        }

        self.window.account_combo.setCurrentIndex(0)
        self.window.refresh_account_authentication_ui()
        self.assertFalse(self.window.account_auth_done_label.isHidden())
        self.assertTrue(self.window.btn_account_authentication.isHidden())

        self.window.account_combo.setCurrentIndex(1)
        self.window.refresh_account_authentication_ui()
        self.assertTrue(self.window.account_auth_done_label.isHidden())
        self.assertFalse(self.window.btn_account_authentication.isHidden())

    def test_account_query_status_projects_unauthenticated_ready_and_retry_states(self) -> None:
        account = "8129123456"
        self.api.accounts = [account]
        self.window.refresh_kiwoom_accounts()
        self.window.show()
        self.app.processEvents()

        self.window._account_authentication_states[account] = (
            gui_windows.ACCOUNT_AUTHENTICATION_REQUIRED
        )
        self.window._account_query_states[account] = gui_windows.ACCOUNT_FUNDS_FAILED
        self.window.refresh_account_authentication_ui()
        self.app.processEvents()
        self.assertTrue(self.window.account_query_neutral_label.isVisible())
        self.assertFalse(self.window.btn_account_requery.isVisible())
        auth_button_x = self.window.btn_account_authentication.geometry().left()
        query_neutral_x = self.window.account_query_neutral_label.geometry().left()

        self.window._account_authentication_states[account] = (
            gui_windows.ACCOUNT_FUNDS_READY
        )
        self.window._account_query_states[account] = gui_windows.ACCOUNT_FUNDS_READY
        self.window.refresh_account_authentication_ui()
        self.app.processEvents()
        self.assertTrue(self.window.account_query_normal_label.isVisible())
        self.assertFalse(self.window.btn_account_requery.isVisible())
        auth_done_x = self.window.account_auth_done_label.geometry().left()
        query_normal_x = self.window.account_query_normal_label.geometry().left()

        self.window._account_query_states[account] = gui_windows.ACCOUNT_FUNDS_FAILED
        self.window.refresh_account_authentication_ui()
        self.app.processEvents()
        self.assertFalse(self.window.account_query_normal_label.isVisible())
        self.assertTrue(self.window.btn_account_requery.isVisible())
        query_requery_x = self.window.btn_account_requery.geometry().left()

        self.assertEqual(auth_button_x, auth_done_x)
        self.assertEqual(query_neutral_x, query_normal_x)
        self.assertEqual(query_normal_x, query_requery_x)
        self.assertEqual(
            self.window.account_auth_neutral_label.width(),
            self.window.account_auth_done_label.width(),
        )
        self.assertEqual(
            self.window.account_auth_done_label.width(),
            self.window.btn_account_authentication.width(),
        )
        self.assertEqual(
            self.window.account_query_neutral_label.width(),
            self.window.account_query_normal_label.width(),
        )
        self.assertEqual(
            self.window.account_query_normal_label.width(),
            self.window.btn_account_requery.width(),
        )

    def test_account_requery_button_reuses_existing_funds_query_path(self) -> None:
        account = "8129123456"
        self.api.accounts = [account]
        self.window.refresh_kiwoom_accounts()
        self.window._account_authentication_states[account] = (
            gui_windows.ACCOUNT_FUNDS_READY
        )
        self.window._account_query_states[account] = gui_windows.ACCOUNT_FUNDS_FAILED
        self.window.refresh_account_authentication_ui()

        with patch.object(
            self.window,
            "request_account_funds",
            return_value={"ok": True},
        ) as request_funds:
            self.window.btn_account_requery.click()

        request_funds.assert_called_once_with(query_reason="MANUAL_REQUERY")

    def test_account_journal_masks_identity_and_preserves_message_error_code(self) -> None:
        with patch.object(
            gui_windows,
            "append_production_event",
            return_value={"appended": True},
        ) as append_event:
            self.window._append_account_query_journal_event(
                "ACCOUNT_QUERY_FAILED",
                account_id="8129123456",
                request_id=7,
                query_reason="INITIAL_QUERY",
                result="FAILED",
                payload={"error": "account password is required. (44)"},
            )

        _event_type, kwargs = append_event.call_args.args[0], append_event.call_args.kwargs
        self.assertEqual("ACCOUNT_QUERY_FAILED", _event_type)
        self.assertEqual("8129****", kwargs["target_id"])
        self.assertEqual("44", kwargs["details"]["error_code"])
        self.assertNotIn("8129123456", str(kwargs))

    def test_authentication_button_opens_official_window_then_requeries_without_faking_done(self) -> None:
        self.api.accounts = ["8129123456"]
        self.window.refresh_kiwoom_accounts()
        self.api.account_authentication_required.emit(
            {"account_id": "8129123456"}
        )

        with patch.object(
            self.window,
            "request_account_funds",
            return_value={"ok": True},
        ) as request_funds:
            QTest.mouseClick(
                self.window.btn_account_authentication,
                Qt.LeftButton,
            )

        self.assertEqual(1, self.api.password_window_calls)
        request_funds.assert_called_once_with()
        self.assertEqual(
            gui_windows.ACCOUNT_AUTHENTICATION_REQUIRED,
            self.window._account_authentication_states["8129123456"],
        )
        self.assertFalse(self.window.btn_account_authentication.isHidden())

    def test_authentication_window_failure_keeps_unauthenticated_and_does_not_requery(self) -> None:
        self.api.accounts = ["8129123456"]
        self.api.password_window_result = {"ok": False, "status": "FAILED"}
        self.window.refresh_kiwoom_accounts()
        self.api.account_authentication_required.emit(
            {"account_id": "8129123456"}
        )

        with patch.object(self.window, "request_account_funds") as request_funds:
            QTest.mouseClick(
                self.window.btn_account_authentication,
                Qt.LeftButton,
            )

        request_funds.assert_not_called()
        self.assertFalse(self.window.btn_account_authentication.isHidden())

    def test_disconnect_restores_neutral_account_ui_and_clears_session_evidence(self) -> None:
        self.window.refresh_kiwoom_accounts()
        self.api.account_authentication_required.emit(
            {"account_id": "8129123456"}
        )
        self.api.connected = False
        with (
            patch.object(self.window, "_stop_production_recovery_timers"),
            patch.object(self.window, "_production_recovery_status_result"),
            patch.object(gui_windows.production_recovery_registry, "invalidate"),
        ):
            self.window.on_kiwoom_login_state_changed(
                {"connected": False, "message": "미연결 상태"}
            )

        self.assertEqual({}, self.window._account_authentication_states)
        self.assertEqual({}, self.window._account_query_states)
        self.assertTrue(self.window.account_auth_separator.isHidden())
        self.assertFalse(self.window.account_auth_label.isHidden())
        self.assertFalse(self.window.account_auth_neutral_label.isHidden())
        self.assertTrue(self.window.account_auth_done_label.isHidden())
        self.assertTrue(self.window.btn_account_authentication.isHidden())
        self.assertFalse(self.window.account_query_status_label.isHidden())
        self.assertTrue(self.window.account_query_normal_label.isHidden())
        self.assertFalse(self.window.account_query_neutral_label.isHidden())
        self.assertTrue(self.window.btn_account_requery.isHidden())

    def test_connected_account_query_runs_before_recovery(self) -> None:
        self.api.accounts = ["8129123456"]
        events: list[str] = []
        with (
            patch.object(
                self.window,
                "request_account_funds",
                side_effect=lambda: events.append("funds") or {"ok": True},
            ) as request_funds,
            patch.object(
                self.window,
                "start_production_recovery",
                side_effect=lambda: events.append("recovery"),
            ),
        ):
            self.window.on_kiwoom_login_state_changed(
                {"connected": True, "message": "연결됨"}
            )

        request_funds.assert_called_once_with()
        self.assertEqual(["funds", "recovery"], events)

    def test_failed_recovery_restarts_once_after_verified_funds_success(self) -> None:
        account = "8129123456"
        self._prepare_ready_account_funds(account)
        identity = self._recovery_identity(account)
        context = SimpleNamespace(
            identity=identity,
            account_status=gui_windows.ACCOUNT_FAILED,
            stocks=(),
        )
        self.window._production_recovery_identity = identity

        def request_funds(account_id, *, request_id, callback):
            callback(
                {
                    "ok": True,
                    "account_id": account_id,
                    "deposit": 1_000_000,
                    "orderable_cash": 900_000,
                    "account_type": "REAL",
                }
            )
            return {"ok": True, "request_id": request_id}

        self.window.account_funds_adapter = SimpleNamespace(
            request_account_funds=request_funds
        )
        with (
            patch.object(
                gui_windows.production_recovery_registry,
                "snapshot",
                return_value=context,
            ),
            patch.object(
                self.window,
                "start_production_recovery",
                return_value=True,
            ) as restart,
        ):
            result = self.window.request_account_funds()

        self.assertTrue(result["ok"])
        restart.assert_called_once_with()

    def test_recovery_restart_skips_complete_in_progress_and_account_mismatch(self) -> None:
        account = "8129123456"
        self._prepare_ready_account_funds(account)
        matching_identity = self._recovery_identity(account)
        mismatched_identity = self._recovery_identity("1234567890")

        cases = (
            (gui_windows.ACCOUNT_COMPLETED, matching_identity),
            ("COLLECTING", matching_identity),
            (gui_windows.ACCOUNT_FAILED, mismatched_identity),
        )
        for status, identity in cases:
            with self.subTest(status=status, account=identity.account_no):
                context = SimpleNamespace(
                    identity=identity,
                    account_status=status,
                    stocks=(),
                )
                self.window._production_recovery_identity = identity
                with (
                    patch.object(
                        gui_windows.production_recovery_registry,
                        "snapshot",
                        return_value=context,
                    ),
                    patch.object(
                        self.window,
                        "start_production_recovery",
                        return_value=True,
                    ) as restart,
                ):
                    restarted = self.window._restart_failed_production_recovery_after_account_funds_success(
                        account
                    )

                self.assertFalse(restarted)
                restart.assert_not_called()

    def test_recovery_restart_requires_authentication_and_ready_funds(self) -> None:
        account = "8129123456"
        self._prepare_ready_account_funds(account)
        identity = self._recovery_identity(account)
        context = SimpleNamespace(
            identity=identity,
            account_status=gui_windows.ACCOUNT_FAILED,
            stocks=(),
        )
        self.window._production_recovery_identity = identity

        with (
            patch.object(
                gui_windows.production_recovery_registry,
                "snapshot",
                return_value=context,
            ),
            patch.object(
                self.window,
                "start_production_recovery",
                return_value=True,
            ) as restart,
        ):
            self.window._account_authentication_states[account] = (
                gui_windows.ACCOUNT_AUTHENTICATION_REQUIRED
            )
            self.assertFalse(
                self.window._restart_failed_production_recovery_after_account_funds_success(
                    account
                )
            )
            self.window._account_authentication_states[account] = (
                gui_windows.ACCOUNT_FUNDS_READY
            )
            self.window._account_funds_projection.select_account(
                account,
                connected=False,
            )
            self.assertFalse(
                self.window._restart_failed_production_recovery_after_account_funds_success(
                    account
                )
            )

        restart.assert_not_called()

    def test_failed_recovery_restart_keeps_failure_when_existing_entrypoint_fails(self) -> None:
        account = "8129123456"
        self._prepare_ready_account_funds(account)
        identity = self._recovery_identity(account)
        context = SimpleNamespace(
            identity=identity,
            account_status=gui_windows.ACCOUNT_FAILED,
            stocks=(),
        )
        self.window._production_recovery_identity = identity
        with (
            patch.object(
                gui_windows.production_recovery_registry,
                "snapshot",
                return_value=context,
            ),
            patch.object(
                self.window,
                "start_production_recovery",
                return_value=False,
            ) as restart,
        ):
            restarted = self.window._restart_failed_production_recovery_after_account_funds_success(
                account
            )

        self.assertFalse(restarted)
        self.assertEqual(gui_windows.ACCOUNT_FAILED, context.account_status)
        restart.assert_called_once_with()

    def test_failed_account_funds_result_does_not_restart_recovery(self) -> None:
        account = "8129123456"
        self._prepare_ready_account_funds(account)

        def request_funds(account_id, *, request_id, callback):
            callback(
                {
                    "ok": False,
                    "account_id": account_id,
                    "error": "query failed",
                }
            )
            return {"ok": False, "request_id": request_id}

        self.window.account_funds_adapter = SimpleNamespace(
            request_account_funds=request_funds
        )
        with patch.object(
            self.window,
            "_restart_failed_production_recovery_after_account_funds_success",
        ) as restart:
            result = self.window.request_account_funds()

        self.assertFalse(result["ok"])
        restart.assert_not_called()

    def test_recovery_does_not_duplicate_ready_account_query(self) -> None:
        self.api.accounts = ["8129123456"]
        self.window.refresh_kiwoom_accounts()
        self.window.sync_account_funds_selection(connected=True)
        request = self.window._account_funds_projection.begin_request()
        self.assertIsNotNone(request)
        self.assertTrue(
            self.window._account_funds_projection.apply_result(
                request,
                {"ok": True, "deposit": 1000, "orderable_cash": 900},
            )
        )
        identity = SimpleNamespace(account_no="8129123456")

        with patch.object(self.window, "request_account_funds") as request_funds:
            result = self.window._request_account_funds_after_recovery(identity)

        request_funds.assert_not_called()
        self.assertEqual(gui_windows.ACCOUNT_FUNDS_READY, result["status"])


if __name__ == "__main__":
    unittest.main()
