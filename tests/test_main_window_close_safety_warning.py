from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PyQt5.QtWidgets import QApplication
from PyQt5.QtGui import QCloseEvent

import gui_windows
import routine_order_permission


class _FakeMessageBox:
    Warning = 1
    AcceptRole = 2
    RejectRole = 3
    next_click = "cancel"
    last = None

    def __init__(self, parent) -> None:
        self.parent = parent
        self.title = ""
        self.text = ""
        self.buttons = {}
        self.default_button = None
        self.escape_button = None
        self.clicked = None
        type(self).last = self

    def setIcon(self, _icon) -> None:
        pass

    def setWindowTitle(self, title: str) -> None:
        self.title = title

    def setText(self, text: str) -> None:
        self.text = text

    def addButton(self, text: str, role):
        button = object()
        self.buttons[text] = (button, role)
        return button

    def setDefaultButton(self, button) -> None:
        self.default_button = button

    def setEscapeButton(self, button) -> None:
        self.escape_button = button

    def exec_(self) -> None:
        if self.next_click == "exit":
            self.clicked = self.buttons["종료"][0]
        elif self.next_click == "cancel":
            self.clicked = self.buttons["취소"][0]
        else:
            self.clicked = None

    def clickedButton(self):
        return self.clicked


class MainWindowCloseSafetyWarningTests(unittest.TestCase):
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
            patch.object(
                gui_windows.MainWindow,
                "refresh_startup_recovery_status",
                return_value={},
            ),
            patch.object(gui_windows.MainWindow, "refresh_all"),
            patch.object(gui_windows, "append_owner_event_once"),
        ):
            return gui_windows.MainWindow()

    @staticmethod
    def _target():
        return (Path("C:/test/stocks/005930_삼성전자"), "005930", "삼성전자")

    def test_current_running_inside_scheduled_time_requires_warning(self) -> None:
        window = self._create_window()
        try:
            config = {"operation_mode": "SCHEDULED"}
            state = {"status": "RUNNING", "trade_enabled": True}
            with (
                patch.object(
                    gui_windows,
                    "auto_trade_running_registered_operation_targets",
                    return_value=[self._target()],
                ),
                patch.object(gui_windows, "read_json_dict", side_effect=[config, state]),
                patch.object(
                    routine_order_permission,
                    "scheduled_status_for_now",
                    return_value="RUNNING",
                ) as scheduled,
            ):
                self.assertTrue(
                    window._main_exit_warning_required(datetime(2026, 8, 13, 10, 0))
                )
            scheduled.assert_called_once_with(config, datetime(2026, 8, 13, 10, 0))
        finally:
            with patch.object(window, "_confirm_main_window_exit_if_required", return_value=True):
                window.close()

    def test_current_running_inside_selected_ats_time_requires_warning(self) -> None:
        window = self._create_window()
        try:
            config = {"operation_mode": "CONTINUOUS"}
            state = {
                "status": "RUNNING",
                "trade_enabled": True,
                "manual_ats_selection": {"selected_sessions": ["extra1"]},
            }
            now_dt = datetime(2026, 8, 13, 8, 20)
            with (
                patch.object(
                    gui_windows,
                    "auto_trade_running_registered_operation_targets",
                    return_value=[self._target()],
                ),
                patch.object(gui_windows, "read_json_dict", side_effect=[config, state]),
                patch.object(
                    routine_order_permission,
                    "in_manual_trading_session",
                    return_value=False,
                ),
                patch.object(
                    routine_order_permission,
                    "manual_ats_active_now",
                    return_value=True,
                ) as ats_active,
            ):
                self.assertTrue(window._main_exit_warning_required(now_dt))
            ats_active.assert_called_once_with(config, state, now_dt)
        finally:
            with patch.object(window, "_confirm_main_window_exit_if_required", return_value=True):
                window.close()

    def test_all_current_running_targets_outside_time_skip_warning(self) -> None:
        window = self._create_window()
        try:
            with (
                patch.object(
                    gui_windows,
                    "auto_trade_running_registered_operation_targets",
                    return_value=[self._target()],
                ),
                patch.object(
                    gui_windows,
                    "read_json_dict",
                    side_effect=[
                        {"operation_mode": "SCHEDULED"},
                        {"status": "RUNNING", "trade_enabled": True},
                    ],
                ),
                patch.object(
                    routine_order_permission,
                    "scheduled_status_for_now",
                    return_value="MONITORING",
                ),
            ):
                self.assertFalse(
                    window._main_exit_warning_required(datetime(2026, 8, 13, 20, 0))
                )
        finally:
            with patch.object(window, "_confirm_main_window_exit_if_required", return_value=True):
                window.close()

    def test_no_current_running_target_skips_persisted_running(self) -> None:
        window = self._create_window()
        try:
            with (
                patch.object(
                    gui_windows,
                    "auto_trade_running_registered_operation_targets",
                    return_value=[],
                ),
                patch.object(gui_windows, "read_json_dict") as reader,
            ):
                self.assertFalse(window._main_exit_warning_required())
            reader.assert_not_called()
        finally:
            with patch.object(window, "_confirm_main_window_exit_if_required", return_value=True):
                window.close()

    def test_unavailable_time_for_current_running_target_warns_fail_closed(self) -> None:
        window = self._create_window()
        try:
            with (
                patch.object(
                    gui_windows,
                    "auto_trade_running_registered_operation_targets",
                    return_value=[self._target()],
                ),
                patch.object(gui_windows, "read_json_dict", side_effect=[{}, {}]),
            ):
                self.assertTrue(window._main_exit_warning_required())
        finally:
            with patch.object(window, "_confirm_main_window_exit_if_required", return_value=True):
                window.close()

    def test_dialog_defaults_escape_and_close_to_cancel(self) -> None:
        window = self._create_window()
        try:
            with (
                patch.object(window, "_main_exit_warning_required", return_value=True),
                patch.object(gui_windows, "QMessageBox", _FakeMessageBox),
            ):
                for choice in ("cancel", "close"):
                    with self.subTest(choice=choice):
                        _FakeMessageBox.next_click = choice
                        self.assertFalse(window._confirm_main_window_exit_if_required())
                        dialog = _FakeMessageBox.last
                        self.assertEqual("취소", next(
                            text for text, (button, _role) in dialog.buttons.items()
                            if button is dialog.default_button
                        ))
                        self.assertIs(dialog.default_button, dialog.escape_button)
                        self.assertEqual(
                            "운영 중입니다. 지금 종료하면 심각한 손실이 발생할 수 있습니다.",
                            dialog.text,
                        )

                _FakeMessageBox.next_click = "exit"
                self.assertTrue(window._confirm_main_window_exit_if_required())
        finally:
            with patch.object(window, "_confirm_main_window_exit_if_required", return_value=True):
                window.close()

    def test_cancel_ignores_close_without_shutdown(self) -> None:
        window = self._create_window()
        event = Mock()
        with (
            patch.object(window, "_confirm_main_window_exit_if_required", return_value=False),
            patch.object(gui_windows, "close_persistent_feature_windows") as close_features,
        ):
            window.closeEvent(event)
        event.ignore.assert_called_once_with()
        close_features.assert_not_called()
        self.assertFalse(bool(getattr(window, "_main_window_closing", False)))
        with patch.object(window, "_confirm_main_window_exit_if_required", return_value=True):
            window.close()

    def test_confirmed_exit_continues_existing_close_path(self) -> None:
        window = self._create_window()
        event = QCloseEvent()
        with (
            patch.object(window, "_confirm_main_window_exit_if_required", return_value=True),
            patch.object(gui_windows, "close_persistent_feature_windows") as close_features,
            patch.object(gui_windows, "append_owner_event_once") as append_event,
        ):
            window.closeEvent(event)
        self.assertTrue(event.isAccepted())
        self.assertTrue(window._main_window_closing)
        close_features.assert_called_once_with(window)
        append_event.assert_called_once()


if __name__ == "__main__":
    unittest.main()
