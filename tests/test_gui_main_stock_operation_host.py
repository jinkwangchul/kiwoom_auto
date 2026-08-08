# -*- coding: utf-8 -*-

from __future__ import annotations

import inspect
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QObject, QPoint
from PyQt5.QtWidgets import QApplication, QWidget

import gui_auto_trade_context_menu as context_menu
import gui_auto_trade_timer
import gui_windows
from gui_auto_trade_operation_host import AutoTradeOperationHost
from gui_main_stock_context_menu import MainMonitoringStockOperationAdapter


class _Action:
    def __init__(self, text: str, separator: bool = False) -> None:
        self.text = text
        self.separator = separator
        self.enabled = True
        self.properties = {}

    def setEnabled(self, enabled: bool) -> None:
        self.enabled = bool(enabled)

    def setText(self, text: str) -> None:
        self.text = text

    def setIcon(self, _icon) -> None:
        return

    def setProperty(self, key: str, value) -> None:
        self.properties[key] = value


class _Menu:
    root = None

    def __init__(self, _parent=None, title: str = "") -> None:
        self.title = title
        self.actions = []
        self.submenus = []
        self.enabled = True
        if not title:
            _Menu.root = self

    def setToolTipsVisible(self, _visible: bool) -> None:
        return

    def addAction(self, text: str):
        action = _Action(text)
        self.actions.append(action)
        return action

    def addSeparator(self):
        action = _Action("", separator=True)
        self.actions.append(action)
        return action

    def addMenu(self, title: str):
        menu = _Menu(title=title)
        self.submenus.append(menu)
        return menu

    def setEnabled(self, enabled: bool) -> None:
        self.enabled = bool(enabled)

    def exec_(self, _position):
        return None


def _callbacks() -> context_menu.StockContextMenuCallbacks:
    return context_menu.StockContextMenuCallbacks(
        start=Mock(),
        select_all=Mock(),
        clear_selection=Mock(),
        unregister=Mock(),
        early_close=Mock(),
        early_close_profit_loss=Mock(),
        early_close_cancel=Mock(),
        individual_liquidation=Mock(),
        time_change=Mock(),
        time_reset=Mock(),
        ats_settings=Mock(),
    )


def _menu_signature(menu):
    return (
        tuple(
            "<separator>" if action.separator else action.text
            for action in menu.actions
        ),
        tuple(
            (submenu.title, _menu_signature(submenu))
            for submenu in menu.submenus
        ),
    )


class MainStockOperationHostTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_host_is_widget_free_and_does_not_create_setting_window(self) -> None:
        owner = Mock()
        with patch(
            "gui_auto_trade_setting_window.AutoTradeSettingWindow",
        ) as setting_window:
            host = AutoTradeOperationHost(owner)

        self.assertNotIsInstance(host, QWidget)
        setting_window.assert_not_called()
        self.assertIs(owner, host.parent())

    def test_monitor_operation_adapter_is_widget_free(self) -> None:
        adapter = MainMonitoringStockOperationAdapter(Mock(), [])

        self.assertNotIsInstance(adapter, QWidget)

    def test_main_window_host_factory_never_constructs_setting_window(self) -> None:
        owner = SimpleNamespace()
        with patch.object(gui_windows, "AutoTradeSettingWindow") as setting_window:
            first = gui_windows.MainWindow.main_monitoring_auto_trade_operation_host(
                owner
            )
            second = gui_windows.MainWindow.main_monitoring_auto_trade_operation_host(
                owner
            )

        setting_window.assert_not_called()
        self.assertIs(first, second)
        self.assertNotIsInstance(first, QWidget)

    def test_operation_cycle_does_not_require_a_visible_settings_window(self) -> None:
        owner = QObject()
        host = AutoTradeOperationHost(owner)
        expected = {
            "processed": True,
            "reason_code": "OPERATION_CYCLE_COMPLETED",
        }
        with patch(
            "gui_auto_trade_timer.auto_trade_run_operation_cycle",
            return_value=expected,
        ) as cycle:
            result = host.run_operation_cycle()

        self.assertEqual(expected, result)
        cycle.assert_called_once_with(host)

    def test_operation_cycle_reentry_is_blocked(self) -> None:
        host = AutoTradeOperationHost(QObject())
        host._operation_cycle_running = True

        result = host.run_operation_cycle()

        self.assertEqual("OPERATION_CYCLE_REENTRY", result["reason_code"])

    def test_operation_host_owns_one_timer_and_shutdown_stops_it(self) -> None:
        host = AutoTradeOperationHost(QObject())
        timer = host.operation_timer()
        timer.start()
        self.assertTrue(timer.isActive())

        result = host.shutdown()

        self.assertTrue(result["stopped"])
        self.assertFalse(timer.isActive())

    def test_main_source_starts_host_after_recovery_not_settings_window(self) -> None:
        source = Path(gui_windows.__file__).read_text(encoding="utf-8")
        recovery_at = source.index("if recovery_account_allows_isolated_stock_operation(context):")
        status_at = source.index("self._production_recovery_status_result()", recovery_at)
        recovery_block = source[recovery_at:status_at]
        self.assertIn("main_monitoring_auto_trade_operation_host", recovery_block)
        self.assertIn("start_after_recovery", recovery_block)
        self.assertNotIn("start_periodic_timers_after_recovery", recovery_block)

    def test_settings_close_cannot_stop_main_operation_host(self) -> None:
        from gui_auto_trade_setting_window import AutoTradeSettingWindow

        close_source = inspect.getsource(AutoTradeSettingWindow.closeEvent)
        self.assertIn("stop_periodic_timers_for_recovery", close_source)
        self.assertNotIn("stop_operation_timers", close_source)
        self.assertNotIn("main_monitoring_auto_trade_operation_host", close_source)

    def test_main_close_shuts_down_operation_host(self) -> None:
        close_source = inspect.getsource(gui_windows.MainWindow.closeEvent)
        self.assertIn("shutdown", close_source)

    def test_hidden_settings_gui_refresh_does_nothing(self) -> None:
        window = Mock()
        window.isVisible.return_value = False

        gui_auto_trade_timer.auto_trade_on_time_policy_gui_timer_tick(window)

        window.refresh_all.assert_not_called()

    def test_recovery_block_stops_operation_cycle_before_mutation(self) -> None:
        host = Mock()
        host.startup_recovery_session_ready.return_value = False

        result = gui_auto_trade_timer.auto_trade_run_operation_cycle(host)

        self.assertEqual("RECOVERY_NOT_READY", result["reason_code"])
        host.stop_operation_timers.assert_called_once_with()
        host.recalculate_all_status_by_operation_policy.assert_not_called()

    def test_operation_cycle_consumes_and_executes_once_without_visibility(self) -> None:
        host = Mock()
        host.startup_recovery_session_ready.return_value = True
        host._last_time_policy_minute_key = ""
        host.recalculate_all_status_by_operation_policy.return_value = {
            "changed": 0,
            "failed": 0,
        }
        host.auto_process_executable_orders_for_real_trade.return_value = {
            "processed": 1,
            "blocked": 0,
        }
        with patch.object(
            gui_auto_trade_timer,
            "auto_trade_current_time_policy_minute_key",
            return_value="2026-08-08 10:00",
        ), patch.object(
            gui_auto_trade_timer,
            "reset_expired_manual_ats_runtime_selections",
        ), patch.object(
            gui_auto_trade_timer,
            "auto_trade_continue_pending_close_liquidations",
            return_value={"processed": 0, "blocked": 0},
        ), patch.object(
            gui_auto_trade_timer,
            "probe_all_enabled_routine_stocks_once",
            return_value={"logged": 1, "error": 0},
        ), patch.object(
            gui_auto_trade_timer,
            "consume_pending_routine_signals_dry_run",
            return_value={"summary": {"signals_checked": 1, "approved": 1}},
        ) as consumer, patch.object(
            gui_auto_trade_timer,
            "auto_trade_signal_probe_only_active",
            return_value=False,
        ), patch.object(
            gui_auto_trade_timer,
            "auto_trade_real_execution_active",
            return_value=True,
        ):
            result = gui_auto_trade_timer.auto_trade_run_operation_cycle(host)

        self.assertTrue(result["processed"])
        consumer.assert_called_once_with(
            limit=5,
            mark_previewed=True,
            write_order_queue=True,
            apply_approval=True,
        )
        host.auto_process_executable_orders_for_real_trade.assert_called_once_with(
            limit=5
        )
        self.assertFalse(hasattr(host, "isVisible") and host.isVisible.called)

    def test_monitor_menu_matches_scheduled_profile(self) -> None:
        with patch.object(context_menu, "QMenu", _Menu):
            context_menu.show_monitor_stock_context_menu(
                Mock(),
                QPoint(),
                has_selection=True,
                callbacks=_callbacks(),
                selected_modes={"SCHEDULED"},
            )

        commands = [
            action.text
            for action in _Menu.root.actions
            if not action.separator
        ]
        self.assertEqual(
            [
                "운영시작",
                "전체선택",
                "전체해제",
                "등록해제",
                "시간변경",
                "변경리셋",
            ],
            commands,
        )
        self.assertEqual(3, sum(action.separator for action in _Menu.root.actions))

    def test_monitor_menu_matches_continuous_and_mixed_profiles(self) -> None:
        with patch.object(context_menu, "QMenu", _Menu):
            context_menu.show_monitor_stock_context_menu(
                Mock(),
                QPoint(),
                has_selection=True,
                callbacks=_callbacks(),
                selected_modes={"CONTINUOUS"},
            )
        continuous = [
            action.text
            for action in _Menu.root.actions
            if not action.separator
        ]
        self.assertEqual("ATS설정", continuous[-1])
        self.assertNotIn("시간변경", continuous)

        with patch.object(context_menu, "QMenu", _Menu):
            context_menu.show_monitor_stock_context_menu(
                Mock(),
                QPoint(),
                has_selection=True,
                callbacks=_callbacks(),
                selected_modes={"CONTINUOUS", "SCHEDULED"},
            )
        mixed = [
            action.text
            for action in _Menu.root.actions
            if not action.separator
        ]
        self.assertNotIn("ATS설정", mixed)
        self.assertNotIn("시간변경", mixed)
        self.assertEqual(2, sum(action.separator for action in _Menu.root.actions))

    def test_monitor_and_settings_menu_structures_match(self) -> None:
        for modes in ({"SCHEDULED"}, {"CONTINUOUS"}, set()):
            with self.subTest(modes=modes), patch.object(
                context_menu,
                "QMenu",
                _Menu,
            ), patch.object(
                context_menu,
                "_selected_emergency_state",
                return_value=(False, False),
            ):
                context_menu.show_monitor_stock_context_menu(
                    Mock(),
                    QPoint(),
                    has_selection=True,
                    callbacks=_callbacks(),
                    selected_modes=modes,
                )
                monitor_signature = _menu_signature(_Menu.root)

                settings_window = Mock()
                settings_window.stock_table.itemAt.return_value = None
                settings_window.stock_table.viewport.return_value.mapToGlobal.return_value = (
                    QPoint()
                )
                settings_window.selected_stock_infos.return_value = [
                    (Path("stocks") / "005930_test", "005930", "test")
                ]
                settings_window.selected_operation_mode_set.return_value = modes
                context_menu.show_auto_trade_stock_context_menu(
                    settings_window,
                    QPoint(),
                )

                self.assertEqual(
                    _menu_signature(_Menu.root),
                    monitor_signature,
                )

    def test_start_split_uses_only_explicit_targets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            stock_dir = Path(temp_dir) / "005930_test"
            stock_dir.mkdir()
            (stock_dir / "state.json").write_text(
                '{"status":"STOPPED","review_required":false}',
                encoding="utf-8",
            )
            host = AutoTradeOperationHost(Mock())

            targets, skipped = host.split_start_targets(
                [(stock_dir, "005930", "test")]
            )

        self.assertEqual([(stock_dir, "005930", "test")], targets)
        self.assertEqual([], skipped)


if __name__ == "__main__":
    unittest.main()
