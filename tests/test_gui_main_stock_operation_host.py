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
import gui_auto_trade_run_control as run_control
import gui_auto_trade_timer
import gui_main_stock_context_menu as main_context_menu
import gui_windows
from auto_trade_order_execution_boundary import AutoTradeOrderExecutionBoundary
from gui_auto_trade_operation_host import AutoTradeOperationHost
from gui_auto_trade_setting_window import AutoTradeSettingWindow
from gui_auto_trade_run_control import (
    _operation_start_resolved_starting_budget,
    initial_buy_start_validation,
)
from gui_main_table_loader import main_stock_resolved_starting_budget
from tests.participant_owner_fixture import attach_participant_owner, participant_codes
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
        self.sequence = []
        self.enabled = True
        if not title:
            _Menu.root = self

    def setToolTipsVisible(self, _visible: bool) -> None:
        return

    def addAction(self, text: str):
        action = _Action(text)
        self.actions.append(action)
        self.sequence.append(text)
        return action

    def addSeparator(self):
        action = _Action("", separator=True)
        self.actions.append(action)
        self.sequence.append("<separator>")
        return action

    def addMenu(self, title: str):
        menu = _Menu(title=title)
        self.submenus.append(menu)
        self.sequence.append(title)
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
        open_charts=Mock(),
        time_change=Mock(),
        time_reset=Mock(),
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

    def test_main_monitoring_adapter_has_no_application_command_borrowing(self) -> None:
        method_names = {
            name
            for name, member in inspect.getmembers(
                MainMonitoringStockOperationAdapter,
                predicate=inspect.isfunction,
            )
            if not (name.startswith("__") and name.endswith("__"))
        }
        removed_command_proxies = {
            "close",
            "target_snapshot",
            "set_stock_operation_exclusion",
            "toggle_stock_operation_exclusion",
            "set_selected_stock_operation_exclusions",
            "clear_selected_stock_operation_exclusions",
            "selected_stock_trade_permission_label",
            "selected_stock_trade_permission_available",
            "toggle_selected_stock_trade_permission",
            "execute_selected_emergency_stop",
            "unregister_selected_stocks",
            "unregister_available",
            "require_startup_recovery_session",
            "show_auto_trade_result_dialog",
            "start_selected_auto_trades",
            "set_selected_individual_schedule_time",
            "reset_selected_schedule_to_global",
            "set_selected_continuous_operation_mode",
            "handle_operation_mode_double_click",
            "selected_manual_ats_state",
            "save_selected_manual_ats_state",
            "selected_manual_ats_execution_method_state",
            "set_selected_manual_ats_execution_method",
            "selected_manual_ats_liquidation_available",
            "execute_selected_manual_ats_liquidation",
            "show_operation_failure_dialog",
            "apply_selected_early_close_profit_loss",
            "cancel_selected_early_close",
            "apply_selected_early_close",
            "apply_selected_individual_liquidation_method",
        }

        self.assertTrue(removed_command_proxies.isdisjoint(method_names))
        self.assertLessEqual(len(method_names), 40)
        adapter_source = inspect.getsource(MainMonitoringStockOperationAdapter)
        self.assertNotIn("AutoTradeSettingWindow", adapter_source)
        self.assertNotIn("StockRepository", adapter_source)
        self.assertNotIn("patch_stock_config", adapter_source)

    def test_host_is_widget_free_and_does_not_create_setting_window(self) -> None:
        owner = Mock()
        with patch(
            "gui_auto_trade_setting_window.AutoTradeSettingWindow",
        ) as setting_window:
            host = AutoTradeOperationHost(owner)

        self.assertNotIsInstance(host, QWidget)
        setting_window.assert_not_called()
        self.assertIs(owner, host.parent())

    def test_host_has_no_dynamic_setting_window_method_provider(self) -> None:
        source = inspect.getsource(AutoTradeOperationHost)

        self.assertNotIn("def __getattr__", source)
        self.assertNotIn("MethodType", source)
        self.assertNotIn("from gui_auto_trade_setting_window import", source)

    def test_host_order_entries_delegate_to_shared_boundary(self) -> None:
        host = AutoTradeOperationHost(Mock())
        boundary = Mock()
        boundary.process_executable_order_for_auto_trade.return_value = {
            "processed": False,
            "stage": "test",
        }
        host._order_execution_boundary = boundary

        result = host.process_executable_order_for_auto_trade(
            "ORDER_1",
            send_order_callable_override=Mock(),
        )

        self.assertEqual("test", result["stage"])
        boundary.process_executable_order_for_auto_trade.assert_called_once()

    def test_settings_and_host_use_same_order_execution_boundary_class(self) -> None:
        host = AutoTradeOperationHost(Mock())
        settings_window = Mock()

        settings_boundary = AutoTradeSettingWindow.order_execution_boundary(
            settings_window
        )

        self.assertIsInstance(
            host._order_execution_boundary,
            AutoTradeOrderExecutionBoundary,
        )
        self.assertIsInstance(
            settings_boundary,
            AutoTradeOrderExecutionBoundary,
        )

    def test_monitor_operation_adapter_is_widget_free(self) -> None:
        adapter = MainMonitoringStockOperationAdapter(Mock(), [])

        self.assertNotIsInstance(adapter, QWidget)

    def test_monitor_adapter_has_no_direct_settings_window_refresh_coupling(self) -> None:
        source = inspect.getsource(MainMonitoringStockOperationAdapter)

        self.assertNotIn("auto_trade_setting_window", source)
        self.assertIn("refresh_auto_trade_assignment_views", source)

    def test_main_monitor_uses_excluded_management_policy_only_for_scheduled_excluded(self) -> None:
        context_target = SimpleNamespace(
            stock_dir=Path("stocks/005930_test"),
            code="005930",
            name="test",
            routine_instance_id="instance-a",
        )
        table = Mock()
        item = Mock()
        item.row.return_value = 3
        table.itemAt.return_value = item
        table.viewport.return_value.mapToGlobal.return_value = QPoint()
        window = SimpleNamespace(
            routine_table=table,
            open_routine_instance_stock_register_from_main_table=Mock(),
        )

        for selected_modes, operation_excluded, expected in (
            ({"SCHEDULED"}, True, True),
            ({"SCHEDULED"}, False, False),
            ({"CONTINUOUS"}, True, False),
        ):
            with self.subTest(
                selected_modes=selected_modes,
                operation_excluded=operation_excluded,
            ):
                adapter = Mock()
                adapter.selected_operation_mode_set.return_value = selected_modes
                adapter.selected_stocks_are_operation_excluded.return_value = (
                    operation_excluded
                )
                adapter.selected_stock_infos.return_value = [
                    (context_target.stock_dir, context_target.code, context_target.name)
                ]
                with patch.object(
                    main_context_menu,
                    "_stock_target_for_row",
                    return_value=context_target,
                ), patch.object(
                    main_context_menu,
                    "ensure_main_monitoring_context_stock_selected",
                ), patch.object(
                    main_context_menu,
                    "selected_main_monitoring_stock_targets",
                    return_value=[context_target],
                ), patch.object(
                    main_context_menu,
                    "MainMonitoringStockOperationAdapter",
                    return_value=adapter,
                ), patch.object(
                    main_context_menu,
                    "selected_emergency_context_state",
                    return_value=(False, True),
                ), patch.object(
                    main_context_menu,
                    "show_monitor_stock_context_menu",
                ) as renderer:
                    shown = main_context_menu.show_main_monitoring_stock_context_menu(
                        window,
                        QPoint(),
                    )

                self.assertTrue(shown)
                self.assertIs(
                    expected,
                    renderer.call_args.kwargs["scheduled_excluded_management"],
                )

    def test_operation_cycle_completion_uses_owner_view_synchronization(self) -> None:
        owner = SimpleNamespace(
            _main_window_closing=False,
            refresh_auto_trade_assignment_views=Mock(),
            refresh_all=Mock(),
        )

        gui_windows.MainWindow._on_main_operation_cycle_completed(owner, {})

        owner.refresh_auto_trade_assignment_views.assert_called_once_with()
        owner.refresh_all.assert_not_called()

    def test_monitor_refresh_preserves_selection_and_scroll_around_owner_sync(self) -> None:
        owner = SimpleNamespace(
            routine_table=Mock(),
            refresh_auto_trade_assignment_views=Mock(),
        )
        adapter = MainMonitoringStockOperationAdapter(owner, [])
        adapter.capture_stock_table_view_state = Mock(return_value=({"stock-a"}, 17))
        adapter.restore_stock_table_view_state = Mock()

        adapter.refresh_all()

        owner.refresh_auto_trade_assignment_views.assert_called_once_with()
        adapter.restore_stock_table_view_state.assert_called_once_with(
            {"stock-a"},
            17,
        )

    def test_monitor_adapter_forwards_main_operation_host_identity(self) -> None:
        host = object()
        owner = SimpleNamespace(
            main_monitoring_auto_trade_operation_host=Mock(return_value=host),
            routine_table=Mock(),
        )
        adapter = MainMonitoringStockOperationAdapter(owner, [])

        self.assertIs(adapter.main_monitoring_auto_trade_operation_host(), host)
        owner.main_monitoring_auto_trade_operation_host.assert_called_once_with()

    def test_monitor_adapter_and_main_window_resolve_same_fresh_starting_budget(self) -> None:
        fresh = SimpleNamespace(
            connection_epoch=7,
            login_session_id="SESSION-7",
            last_price=12_855,
        )
        host = SimpleNamespace(
            fresh_monitoring_market_information_state=Mock(return_value=fresh)
        )
        owner = SimpleNamespace(
            main_monitoring_auto_trade_operation_host=Mock(return_value=host),
            routine_table=Mock(),
        )
        adapter = MainMonitoringStockOperationAdapter(owner, [])
        stock = {
            "code": "012210",
            "name": "삼미금속",
            "stock_path": "stocks/012210_삼미금속",
        }
        config = {"trade_amount_type": "AMOUNT", "buy_amount": 0, "buy_qty": 4}

        main_amount = main_stock_resolved_starting_budget(owner, stock, config)
        start_amount = _operation_start_resolved_starting_budget(
            adapter,
            Path("stocks/012210_삼미금속"),
            "012210",
            "삼미금속",
            config,
        )
        validation = initial_buy_start_validation(
            config,
            {},
            resolved_starting_budget=start_amount,
        )

        self.assertEqual(main_amount, start_amount)
        self.assertGreater(start_amount, 0)
        self.assertTrue(validation["allowed"])
        self.assertEqual("fresh_current_price", validation["starting_budget_source"])

    def test_monitor_adapter_start_validation_still_fails_closed_without_fresh_price(self) -> None:
        host = SimpleNamespace(
            fresh_monitoring_market_information_state=Mock(return_value=None)
        )
        owner = SimpleNamespace(
            main_monitoring_auto_trade_operation_host=Mock(return_value=host),
            routine_table=Mock(),
        )
        adapter = MainMonitoringStockOperationAdapter(owner, [])
        config = {"trade_amount_type": "AMOUNT", "buy_amount": 0, "buy_qty": 4}

        start_amount = _operation_start_resolved_starting_budget(
            adapter,
            Path("stocks/012210_삼미금속"),
            "012210",
            "삼미금속",
            config,
        )
        validation = initial_buy_start_validation(
            config,
            {},
            resolved_starting_budget=start_amount,
        )

        self.assertIsNone(start_amount)
        self.assertFalse(validation["allowed"])
        self.assertEqual("STARTING_BUDGET_UNRESOLVED", validation["reason"])

    def test_monitor_adapter_explicit_amount_does_not_depend_on_fresh_price(self) -> None:
        host = SimpleNamespace(
            fresh_monitoring_market_information_state=Mock(return_value=None)
        )
        owner = SimpleNamespace(
            main_monitoring_auto_trade_operation_host=Mock(return_value=host),
            routine_table=Mock(),
        )
        adapter = MainMonitoringStockOperationAdapter(owner, [])
        config = {"trade_amount_type": "AMOUNT", "buy_amount": 1_000_000}

        start_amount = _operation_start_resolved_starting_budget(
            adapter,
            Path("stocks/012210_삼미금속"),
            "012210",
            "삼미금속",
            config,
        )
        validation = initial_buy_start_validation(
            config,
            {},
            resolved_starting_budget=start_amount,
        )

        self.assertIsNone(start_amount)
        self.assertTrue(validation["allowed"])
        self.assertEqual("explicit", validation["starting_budget_source"])

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
        self.assertIs(first.market_data_host(), second.market_data_host())
        self.assertNotIsInstance(first, QWidget)

    def test_settings_reopen_does_not_create_operation_or_market_host(self) -> None:
        owner = SimpleNamespace(auto_trade_setting_window=None)

        def setting_window(_owner):
            window = Mock()
            window.isVisible.return_value = False
            window.isMinimized.return_value = False
            return window

        with patch.object(gui_windows, "AutoTradeSettingWindow", side_effect=setting_window), patch.object(
            gui_windows, "AutoTradeOperationHost"
        ) as operation_host, patch.object(gui_windows.sip, "isdeleted", return_value=False):
            gui_windows.MainWindow.open_auto_trade_setting_window(owner)
            gui_windows.MainWindow.open_auto_trade_setting_window(owner)

        operation_host.assert_not_called()

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

    def test_recovery_start_syncs_realtime_targets_without_full_operation_cycle(self) -> None:
        host = AutoTradeOperationHost(QObject())
        identity = object()
        snapshot = SimpleNamespace(execution_stock_codes=("005930",))
        sync_result = {
            "ok": True,
            "changed": True,
            "active": True,
            "reason_code": "REGISTER_CALL_RETURNED",
            "snapshot": object(),
        }

        with patch(
            "production_recovery_timer_lifecycle.start_recovery_bound_timers",
            return_value={
                "started": True,
                "started_count": 1,
                "reason_code": "RECOVERY_TIMER_STARTED",
            },
        ) as timer_start, patch(
            "gui_auto_trade_operation_host.project_execution_universe",
            return_value=snapshot,
        ) as projector, patch.object(
            host,
            "sync_realtime_shadow_targets",
            return_value=sync_result,
        ) as sync, patch.object(
            host,
            "run_operation_cycle",
        ) as operation_cycle, patch(
            "gui_auto_trade_operation_host.append_production_event",
        ):
            result = host.start_after_recovery(identity)

        self.assertTrue(result["started"])
        self.assertEqual(1, result["started_count"])
        self.assertEqual("RECOVERY_TIMER_STARTED", result["reason_code"])
        timer_start.assert_called_once()
        projector.assert_called_once_with(host)
        sync.assert_called_once_with(snapshot)
        operation_cycle.assert_not_called()
        self.assertIs(sync_result, result["immediate_realtime_shadow_result"])
        self.assertNotIn("immediate_operation_cycle_result", result)

    def test_recovery_start_preserves_success_when_immediate_realtime_sync_fails(self) -> None:
        host = AutoTradeOperationHost(QObject())
        identity = object()

        with patch(
            "production_recovery_timer_lifecycle.start_recovery_bound_timers",
            return_value={
                "started": True,
                "started_count": 1,
                "reason_code": "RECOVERY_TIMER_STARTED",
            },
        ), patch(
            "gui_auto_trade_operation_host.project_execution_universe",
            return_value=SimpleNamespace(execution_stock_codes=("005930",)),
        ), patch.object(
            host,
            "sync_realtime_shadow_targets",
            side_effect=RuntimeError("sync failed"),
        ), patch.object(
            host,
            "run_operation_cycle",
        ) as operation_cycle, patch(
            "gui_auto_trade_operation_host.LOGGER.exception",
        ), patch(
            "gui_auto_trade_operation_host.append_production_event",
        ):
            result = host.start_after_recovery(identity)

        self.assertTrue(result["started"])
        self.assertEqual(1, result["started_count"])
        self.assertEqual("RECOVERY_TIMER_STARTED", result["reason_code"])
        self.assertEqual(
            "REALTIME_SHADOW_SYNC_FAILED",
            result["immediate_realtime_shadow_result"]["reason_code"],
        )
        self.assertIn("sync failed", result["immediate_realtime_shadow_result"]["error"])
        operation_cycle.assert_not_called()

    def test_recovery_start_does_not_rebind_market_data_signals(self) -> None:
        host = AutoTradeOperationHost(QObject())
        identity = object()

        with patch(
            "production_recovery_timer_lifecycle.start_recovery_bound_timers",
            return_value={
                "started": True,
                "started_count": 1,
                "reason_code": "RECOVERY_TIMER_STARTED",
            },
        ), patch.object(
            host,
            "_bind_market_data_host_signals_once",
        ) as bind_market, patch.object(
            host,
            "_bind_bar_committed_signal_once",
        ) as bind_bar, patch.object(
            host,
            "_bind_realtime_shadow_signals_once",
        ) as bind_shadow, patch.object(
            host,
            "sync_realtime_shadow_targets",
            return_value={"ok": True, "active": False},
        ), patch(
            "gui_auto_trade_operation_host.project_execution_universe",
            return_value=SimpleNamespace(execution_stock_codes=()),
        ), patch(
            "gui_auto_trade_operation_host.append_production_event",
        ):
            host.start_after_recovery(identity)

        bind_market.assert_not_called()
        bind_bar.assert_not_called()
        bind_shadow.assert_not_called()

    def test_recovery_start_has_no_signal_consumer_or_order_execution_side_effect(self) -> None:
        host = AutoTradeOperationHost(QObject())
        identity = object()

        with patch(
            "production_recovery_timer_lifecycle.start_recovery_bound_timers",
            return_value={
                "started": True,
                "started_count": 1,
                "reason_code": "RECOVERY_TIMER_STARTED",
            },
        ), patch(
            "gui_auto_trade_operation_host.project_execution_universe",
            return_value=SimpleNamespace(execution_stock_codes=("005930",)),
        ), patch.object(
            host,
            "sync_realtime_shadow_targets",
            return_value={"ok": True, "active": True},
        ), patch.object(
            host,
            "auto_process_executable_orders_for_real_trade",
        ) as auto_executor, patch.object(
            host,
            "send_order_for_order_queued_automatically",
        ) as send_order, patch(
            "gui_auto_trade_timer.consume_pending_routine_signals_dry_run",
        ) as consumer, patch(
            "gui_auto_trade_operation_host.append_production_event",
        ):
            host.start_after_recovery(identity)

        consumer.assert_not_called()
        auto_executor.assert_not_called()
        send_order.assert_not_called()

    def test_operation_host_owns_one_timer_and_shutdown_stops_it(self) -> None:
        host = AutoTradeOperationHost(QObject())
        timer = host.operation_timer()
        timer.start()
        self.assertTrue(timer.isActive())

        result = host.shutdown()

        self.assertTrue(result["stopped"])
        self.assertFalse(timer.isActive())

    def test_recovery_starts_host_only_with_current_session_participation(self) -> None:
        source = Path(gui_windows.__file__).read_text(encoding="utf-8")
        recovery_at = source.index("current_session_participants = (")
        status_at = source.index("self._production_recovery_status_result()", recovery_at)
        recovery_block = source[recovery_at:status_at]
        self.assertIn("and current_session_participants", recovery_block)
        self.assertIn("NO_CURRENT_SESSION_OPERATION_PARTICIPATION", recovery_block)
        self.assertIn("main_monitoring_auto_trade_operation_host", recovery_block)
        self.assertIn("start_after_recovery", recovery_block)
        self.assertNotIn("start_periodic_timers_after_recovery", recovery_block)

    def test_explicit_operation_start_activates_existing_host_once(self) -> None:
        identity = object()
        host = SimpleNamespace(
            start_after_recovery=Mock(
                return_value={
                    "started": True,
                    "started_count": 1,
                    "reason_code": "RECOVERY_TIMER_STARTED",
                }
            )
        )
        owner = SimpleNamespace(
            _production_recovery_identity=identity,
            startup_recovery_session_ready=Mock(return_value=True),
            main_monitoring_auto_trade_operation_host=Mock(return_value=host),
        )

        result = run_control._start_operation_host_after_explicit_operation_start(owner)

        self.assertTrue(result["started"])
        owner.startup_recovery_session_ready.assert_called_once_with(refresh=False)
        owner.main_monitoring_auto_trade_operation_host.assert_called_once_with()
        host.start_after_recovery.assert_called_once_with(identity)

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
            "project_execution_universe",
            return_value=SimpleNamespace(
                entries=[
                    SimpleNamespace(stock_code="003550", execution_ready=True),
                ]
            ),
        ), patch.object(
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
            allowed_stock_codes=("003550",),
            signal_cutoff_by_stock_code={"003550": ""},
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
                "선택해제",
                "시간변경",
                "변경리셋",
                "등록해제",
                "간이차트",
            ],
            commands,
        )
        self.assertEqual(5, sum(action.separator for action in _Menu.root.actions))

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
        self.assertEqual("간이차트", continuous[-1])
        self.assertIn(
            "ATS설정",
            [submenu.title for submenu in _Menu.root.submenus],
        )
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
        self.assertNotIn(
            "ATS설정",
            [submenu.title for submenu in _Menu.root.submenus],
        )
        self.assertNotIn("시간변경", mixed)
        self.assertEqual(4, sum(action.separator for action in _Menu.root.actions))

    def test_monitor_and_settings_menu_structures_match(self) -> None:
        for modes in ({"SCHEDULED"}, {"CONTINUOUS"}, set()):
            with self.subTest(modes=modes), patch.object(
                context_menu,
                "QMenu",
                _Menu,
            ), patch.object(
                context_menu,
                "selected_emergency_context_state",
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

    def test_settings_stock_menu_delegates_to_shared_renderer(self) -> None:
        settings_window = Mock()
        settings_window.stock_table.itemAt.return_value = None
        settings_window.stock_table.viewport.return_value.mapToGlobal.return_value = (
            QPoint()
        )
        settings_window.selected_stock_infos.return_value = [
            (Path("stocks") / "005930_test", "005930", "test")
        ]
        settings_window.selected_operation_mode_set.return_value = {"SCHEDULED"}
        settings_window._stock_status_filter = "running"
        settings_window._all_stocks_scope_active = False
        settings_window.current_selected_routine_row_metadata.return_value = {
            "row_kind": "instance",
            "instance_id": "instance",
        }

        with patch.object(
            context_menu,
            "selected_emergency_context_state",
            return_value=(False, True),
        ), patch.object(
            context_menu,
            "show_monitor_stock_context_menu",
        ) as shared_renderer:
            context_menu.show_auto_trade_stock_context_menu(
                settings_window,
                QPoint(),
            )

        shared_renderer.assert_called_once()
        kwargs = shared_renderer.call_args.kwargs
        self.assertIsInstance(
            kwargs["callbacks"],
            context_menu.StockContextMenuCallbacks,
        )
        self.assertEqual({"SCHEDULED"}, kwargs["selected_modes"])
        self.assertEqual("set", kwargs["operation_exclusion_action"])
        self.assertTrue(kwargs["stock_register_enabled"])

    def test_monitor_and_settings_full_menu_order_matches(self) -> None:
        callbacks = context_menu.StockContextMenuCallbacks(
            start=Mock(),
            emergency_stop=Mock(),
            select_all=Mock(),
            clear_selection=Mock(),
            set_operation_exclusion=Mock(),
            early_close=Mock(),
            early_close_profit_loss=Mock(),
            early_close_cancel=Mock(),
            individual_liquidation=Mock(),
            open_charts=Mock(),
            time_change=Mock(),
            time_reset=Mock(),
            stock_register=Mock(),
            unregister=Mock(),
        )
        expected = [
            "운영시작",
            "검토정지",
            "<separator>",
            "전체선택",
            "선택해제",
            "운영제외",
            "<separator>",
            "조기마감",
            "개별청산",
            "<separator>",
            "시간변경",
            "변경리셋",
            "<separator>",
            "종목등록",
            "등록해제",
            "<separator>",
            "간이차트",
        ]

        with patch.object(context_menu, "QMenu", _Menu):
            context_menu.show_monitor_stock_context_menu(
                Mock(),
                QPoint(),
                has_selection=True,
                callbacks=callbacks,
                selected_modes={"SCHEDULED"},
            )
            monitor_sequence = list(_Menu.root.sequence)

            settings_window = Mock()
            attach_participant_owner(settings_window)
            settings_window.stock_table.itemAt.return_value = None
            settings_window.stock_table.viewport.return_value.mapToGlobal.return_value = (
                QPoint()
            )
            settings_window.selected_stock_infos.return_value = [
                (Path("stocks") / "005930_test", "005930", "test")
            ]
            settings_window.selected_operation_mode_set.return_value = {"SCHEDULED"}
            settings_window._stock_status_filter = "running"
            settings_window._all_stocks_scope_active = False
            settings_window.current_selected_routine_row_metadata.return_value = {
                "row_kind": "instance",
                "instance_id": "instance",
            }
            with patch.object(
                context_menu,
                "selected_emergency_context_state",
                return_value=(False, True),
            ):
                context_menu.show_auto_trade_stock_context_menu(
                    settings_window,
                    QPoint(),
                )
            settings_sequence = list(_Menu.root.sequence)

        self.assertEqual(expected, monitor_sequence)
        self.assertEqual(expected, settings_sequence)

    def test_start_split_uses_only_explicit_targets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            stock_dir = Path(temp_dir) / "005930_test"
            stock_dir.mkdir()
            (stock_dir / "state.json").write_text(
                '{"status":"STOPPED","review_required":false}',
                encoding="utf-8",
            )
            host = AutoTradeOperationHost(Mock())

            with patch(
                "gui_auto_trade_policy.auto_trade_operation_session_phase",
                return_value={
                    "evaluable": True,
                    "phase": "ACTIVE_SESSION",
                    "mode": "SCHEDULED",
                },
            ):
                targets, skipped = host.split_start_targets(
                    [(stock_dir, "005930", "test")]
                )

        self.assertEqual([(stock_dir, "005930", "test")], targets)
        self.assertEqual([], skipped)

    def test_start_split_allows_stale_running_without_current_participant(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            stock_dir = Path(temp_dir) / "005930_test"
            stock_dir.mkdir()
            (stock_dir / "state.json").write_text(
                '{"status":"RUNNING","trade_enabled":true}',
                encoding="utf-8",
            )
            host = AutoTradeOperationHost(Mock())
            self.assertEqual((), host.current_session_operation_participant_stock_codes())
            host.startup_recovery_session_ready = lambda refresh=False: True

            with patch(
                "gui_auto_trade_policy.auto_trade_operation_session_phase",
                return_value={
                    "evaluable": True,
                    "phase": "ACTIVE_SESSION",
                    "mode": "SCHEDULED",
                },
            ):
                targets, skipped = host.split_start_targets(
                    [(stock_dir, "005930", "test")]
                )

        self.assertEqual([(stock_dir, "005930", "test")], targets)
        self.assertEqual([], skipped)

    def test_start_split_rejects_running_current_participant(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            stock_dir = Path(temp_dir) / "005930_test"
            stock_dir.mkdir()
            (stock_dir / "state.json").write_text(
                '{"status":"RUNNING","trade_enabled":true}',
                encoding="utf-8",
            )
            host = AutoTradeOperationHost(Mock())
            host.register_current_session_operation_participants({"005930"})
            host.startup_recovery_session_ready = lambda refresh=False: True

            targets, skipped = host.split_start_targets(
                [(stock_dir, "005930", "test")]
            )

        self.assertEqual([], targets)
        self.assertEqual(1, len(skipped))


if __name__ == "__main__":
    unittest.main()
