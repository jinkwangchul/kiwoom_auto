from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch
from PyQt5.QtCore import QCoreApplication, QEvent, QPoint, QPointF, QRect, Qt, QTimer
from PyQt5.QtGui import QColor, QFont, QFontMetrics, QMouseEvent, QPainter, QPixmap
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

import gui_main_table_loader
import gui_windows
import gui_auto_trade_setting_window as setting_window
from gui_routine_registry import GroupRecord
from gui_order_utils import (
    DIRECTIONAL_NEGATIVE_COLOR,
    DIRECTIONAL_NEUTRAL_COLOR,
    DIRECTIONAL_POSITIVE_COLOR,
    directional_value_color,
    format_signed_percent,
)
from routine_instance_registry import RoutineDefinitionRecord, RoutineInstanceRecord
from gui_auto_trade_display import (
    RatioMetricDisplay,
    ROUTINE_PROFIT_SIGNAL_COLORS,
    draw_limit_metric,
    draw_ratio_metric_display,
    draw_stock_position_metric_display,
    format_routine_buy_limit,
    format_routine_buy_limit_usage,
    profit_loss_value_color,
    ratio_metric_layout,
    format_routine_used_amount,
    stock_position_display_values,
    routine_profit_signal,
    stock_position_metric_values,
)
from gui_main_table_loader import (
    routine_instance_buy_limit_text,
    routine_instance_consumed_text,
    routine_instance_profit_text,
    stock_initial_buy_display,
)


class FakeRoutineTable:
    def __init__(self) -> None:
        self.row_count = 0
        self.items: dict[tuple[int, int], object] = {}
        self.widgets: dict[tuple[int, int], object] = {}
        self.spans: dict[tuple[int, int], tuple[int, int]] = {}
        self.row_heights: dict[int, int] = {}

    def columnCount(self) -> int:
        return len(gui_main_table_loader.ROUTINE_MONITORING_HEADERS)

    def setRowCount(self, count: int) -> None:
        self.row_count = count

    def rowCount(self) -> int:
        return self.row_count

    def clearSpans(self) -> None:
        self.spans.clear()

    def setSpan(
        self,
        row: int,
        column: int,
        row_span: int,
        column_span: int,
    ) -> None:
        self.spans[(row, column)] = (row_span, column_span)

    def setItem(self, row: int, column: int, item: object) -> None:
        self.items[(row, column)] = item

    def setRowHeight(self, row: int, height: int) -> None:
        self.row_heights[row] = height

    def setCellWidget(self, row: int, column: int, widget: object) -> None:
        self.widgets[(row, column)] = widget

    def item(self, row: int, column: int):
        return self.items[(row, column)]

    def cellWidget(self, row: int, column: int):
        return self.widgets.get((row, column))

    def removeCellWidget(self, row: int, column: int) -> None:
        self.widgets.pop((row, column), None)


class FakeCellWidget:
    def __init__(self) -> None:
        self.deleted = False

    def deleteLater(self) -> None:
        self.deleted = True


def _main_group(name: str = "Parent") -> GroupRecord:
    return GroupRecord(
        name=name,
        path=Path("group-root") / f"_{name}",
        source_type="legacy_folder",
        budget={},
        valid=True,
    )


def _assigned_stock(
    instance_id: str,
    *,
    group_name: str = "Parent",
    code: str = "000001",
    name: str = "Stock",
) -> dict[str, object]:
    return {
        "code": code,
        "name": name,
        "stock_path": f"stocks/{code}_{name}",
        "routines": [group_name],
        "assigned_routine_instance_id": instance_id,
    }


def _main_static_cache(definitions, instances, stocks) -> dict[str, object]:
    return {
        "definitions": tuple(definitions),
        "instances": tuple(instances),
        "stocks": tuple(stocks),
    }


@unittest.skipIf(
    getattr(QApplication, "__name__", "") == "_QtImportStub",
    "requires real PyQt widgets; the legacy GUI test module installed global stubs",
)
class MainRoutineMonitoringDisplayTest(unittest.TestCase):
    def test_routine_instance_name_display_keeps_seven_and_truncates_eight(self) -> None:
        self.assertEqual(
            "지표추종매매C",
            gui_main_table_loader.routine_instance_name_display("지표추종매매C"),
        )
        self.assertEqual(
            "지표추종매매...",
            gui_main_table_loader.routine_instance_name_display("지표추종매매CD"),
        )

    def test_valid_summary_uses_projected_relation_stock_rows(self) -> None:
        group = SimpleNamespace(
            group_id="group-a",
            instances=(
                SimpleNamespace(instance_id="inst-a"),
                SimpleNamespace(instance_id="inst-b"),
            ),
        )
        relation_counts = {
            gui_main_table_loader.main_group_instance_relation_id(
                "group-a", "inst-a"
            ): {"stocks": [{}] * 4},
            gui_main_table_loader.main_group_instance_relation_id(
                "group-a", "inst-b"
            ): {"stocks": [{}] * 6},
        }
        instance_counts = {
            "inst-a": {"waiting": 4, "review": 2},
            "inst-b": {"waiting": 6, "review": 4},
        }
        window = SimpleNamespace(
            _main_routine_valid_only=True,
            _update_main_routine_summary=MagicMock(),
        )

        gui_main_table_loader._update_main_routine_summary(
            window,
            [SimpleNamespace(definition_id="indicator_follow")],
            [
                SimpleNamespace(
                    instance_id="inst-a", definition_id="indicator_follow"
                ),
                SimpleNamespace(
                    instance_id="inst-b", definition_id="indicator_follow"
                ),
            ],
            instance_counts,
            group_projection=(group,),
            relation_counts=relation_counts,
        )

        projection = window._update_main_routine_summary.call_args.args[0]
        self.assertEqual(
            (
                ("group", "그룹", 1),
                ("routine", "루틴", 2),
                ("stock", "종목", 10),
                ("operation", "운영", 0),
                ("waiting", "대기", 10),
                ("excluded", "제외", 0),
                ("review", "검토", 6),
            ),
            projection["count_badges"],
        )
    def test_market_early_close_runtime_drives_stock_status_method_and_liquidation(self) -> None:
        requested_at = datetime.now().astimezone().isoformat(timespec="seconds")
        state = {
            "status": "EARLY_CLOSE",
            "holding_qty": 4,
            "trade_enabled": True,
            "trade_started_at": requested_at,
            "operation_command_mode": "EARLY_CLOSE",
            "operation_command_source": "main_routine_context_menu",
            "early_close_requested_at": requested_at,
            "early_close_source": "main_routine_context_menu",
            "early_close_method": "시장가",
            "early_close_policy": {"method": "시장가"},
            "liquidation_policy_forced": True,
            "liquidation_policy_reason": "EARLY_CLOSE",
        }
        config = {"operation_mode": "CONTINUOUS"}

        def read_runtime(path):
            return state if Path(path).name == "state.json" else config

        with (
            patch.object(
                gui_main_table_loader,
                "read_json_dict",
                side_effect=read_runtime,
            ),
            patch.object(
                gui_main_table_loader,
                "pending_order_side_quantities",
                return_value=(0, 0),
            ),
            patch.object(
                gui_main_table_loader,
                "auto_trade_setting_current_session_trade_started",
                return_value=True,
            ),
            patch(
                "gui_auto_trade_policy.auto_trade_setting_liquidation_phase_active",
                return_value=False,
            ),
            patch(
                "gui_auto_trade_policy.auto_trade_setting_is_after_regular_end",
                return_value=False,
            ),
        ):
            values = gui_main_table_loader._routine_tree_stock_display_values(
                SimpleNamespace(),
                {
                    "code": "005930",
                    "name": "?�성?�자",
                    "stock_path": "stocks/005930_Samsung",
                },
            )

        self.assertEqual("\uC870\uAE30\uB9C8\uAC10", values[4])
        self.assertEqual("\uC2DC\uC7A5\uAC00", values[5])
        self.assertEqual("5\uBD84/\uC2DC\uC7A5\uAC00", values[6])

    def test_instance_operation_status_uses_runtime_running_count_only(self) -> None:
        self.assertEqual(
            gui_main_table_loader.ROUTINE_STATUS_RUNNING,
            gui_main_table_loader.routine_instance_operation_status(1),
        )
        self.assertEqual(
            gui_main_table_loader.ROUTINE_STATUS_STOPPED,
            gui_main_table_loader.routine_instance_operation_status(0),
        )
        self.assertEqual(
            gui_main_table_loader.ROUTINE_STATUS_STOPPED,
            gui_main_table_loader.routine_instance_operation_status("invalid"),
        )

    def test_auto_trade_setting_window_visible_reopen_reuses_and_activates(self) -> None:
        window = MagicMock()
        owner = SimpleNamespace(auto_trade_setting_window=window)

        with patch.object(gui_windows.sip, "isdeleted", return_value=False):
            window.isVisible.return_value = True
            window.isMinimized.return_value = False
            gui_windows.MainWindow.open_auto_trade_setting_window(owner)

            window.reset_default_filters_for_open.assert_not_called()
            window.show.assert_called_once_with()
            window.showNormal.assert_not_called()
            window.raise_.assert_called_once_with()
            window.activateWindow.assert_called_once_with()

            window.reset_mock()
            window.isMinimized.return_value = True
            gui_windows.MainWindow.open_auto_trade_setting_window(owner)

            window.reset_default_filters_for_open.assert_not_called()
            window.show.assert_not_called()
            window.showNormal.assert_called_once_with()
            window.raise_.assert_called_once_with()
            window.activateWindow.assert_called_once_with()
            self.assertIs(window, owner.auto_trade_setting_window)

    def test_auto_trade_setting_window_recreates_after_close(self) -> None:
        class ProbeWindow(QDialog):
            created = 0

            def __init__(self, parent=None) -> None:
                super().__init__(parent)
                ProbeWindow.created += 1
                self.time_timer = QTimer(self)
                self.runtime_timer = QTimer(self)
                self.time_timer.timeout.connect(self.update)
                self.runtime_timer.timeout.connect(self.update)

            def reset_default_filters_for_open(self) -> None:
                pass

        owner = QMainWindow()
        with patch.object(gui_windows, "AutoTradeSettingWindow", ProbeWindow):
            gui_windows.MainWindow.open_auto_trade_setting_window(owner)
            first = owner.auto_trade_setting_window
            first.close()
            gui_windows.MainWindow.open_auto_trade_setting_window(owner)
            second = owner.auto_trade_setting_window
            self.assertIsNot(first, second)
            self.assertEqual(2, ProbeWindow.created)
            self.assertEqual(2, len(second.findChildren(QTimer)))
            self.assertEqual(1, second.time_timer.receivers(second.time_timer.timeout))
            self.assertEqual(1, second.runtime_timer.receivers(second.runtime_timer.timeout))

            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            self.app.processEvents()
            self.assertTrue(gui_windows.sip.isdeleted(first))
            self.assertIs(second, owner.auto_trade_setting_window)

            gui_windows.MainWindow.open_auto_trade_setting_window(owner)
            self.assertIs(second, owner.auto_trade_setting_window)
            self.assertEqual(2, ProbeWindow.created)

            second.close()
            second.deleteLater()
            owner.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            self.app.processEvents()

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_group_parent_context_menu_uses_group_scope(self) -> None:
        group = _main_group("지표추종매매")
        group_id = str(group.path.resolve())
        table = QTableWidget(1, 1)
        item = QTableWidgetItem(group.name)
        item.setData(
            gui_main_table_loader.ROUTINE_ROW_KIND_ROLE,
            gui_main_table_loader.ROUTINE_ROW_PARENT,
        )
        item.setData(
            gui_main_table_loader.ROUTINE_GROUP_ID_ROLE,
            group_id,
        )
        item.setData(
            gui_main_table_loader.ROUTINE_GROUP_PATH_ROLE,
            str(group.path),
        )
        item.setData(
            gui_main_table_loader.ROUTINE_PARENT_NAME_ROLE,
            group.name,
        )
        table.setItem(0, 0, item)
        table.resize(480, 120)
        table.show()
        self.app.processEvents()

        def set_operation_actions_enabled(actions, enabled):
            gui_windows.MainWindow._set_routine_operation_actions_enabled(
                actions,
                enabled,
            )

        window = SimpleNamespace(
            routine_table=table,
            handle_routine_group_name_double_click=MagicMock(return_value=True),
            request_routine_group_operation=MagicMock(),
            _routine_instance_ids_by_group={group_id: ("instance-a",)},
            _routine_stock_paths_by_group={group_id: ("stocks/000001_Stock",)},
            _set_routine_operation_actions_enabled=set_operation_actions_enabled,
        )
        controller = gui_windows._RoutineTreeInteractionController(window)
        window._routine_tree_interaction_controller = controller
        index = table.model().index(0, 0)
        parent_name_point = controller._parent_name_rect(index).center()

        double_click = QMouseEvent(
            QEvent.MouseButtonDblClick,
            QPointF(parent_name_point),
            Qt.LeftButton,
            Qt.LeftButton,
            Qt.NoModifier,
        )
        self.assertTrue(controller.eventFilter(table.viewport(), double_click))
        window.handle_routine_group_name_double_click.assert_called_once_with(0)
        menu = MagicMock()
        actions = [MagicMock(), MagicMock()]
        menu.addAction.side_effect = actions
        menu.exec_.return_value = None
        with (
            patch.object(gui_windows, "QMenu", return_value=menu),
            patch.object(
                gui_windows,
                "routine_instance_checked",
                return_value=True,
            ),
        ):
            gui_windows.MainWindow.open_routine_context_menu(
                window,
                parent_name_point,
            )

        self.assertEqual(
            ["조기마감", "즉시청산"],
            [call_item.args[0] for call_item in menu.addAction.call_args_list],
        )
        menu.addSeparator.assert_not_called()
        for action in actions:
            action.setEnabled.assert_called_once_with(True)
        actions[0].triggered.connect.call_args.args[0]()
        actions[1].triggered.connect.call_args.args[0]()
        self.assertEqual(
            [
                call(
                    group_id,
                    group.name,
                    "루틴",
                    gui_windows.ROUTINE_STATUS_EARLY_CLOSE,
                ),
                call(
                    group_id,
                    group.name,
                    gui_windows.POLICY_MARKET,
                    gui_windows.ROUTINE_STATUS_IMMEDIATE_LIQUIDATION,
                ),
            ],
            window.request_routine_group_operation.call_args_list,
        )
        table.close()

    def test_routine_operation_confirmations_use_project_copy(self) -> None:
        import gui_windows

        for display_status in (
            gui_windows.ROUTINE_STATUS_EARLY_CLOSE,
            gui_windows.ROUTINE_STATUS_IMMEDIATE_LIQUIDATION,
        ):
            dialog = gui_windows._create_routine_operation_confirmation(
                None,
                display_status,
            )
            try:
                self.assertTrue(dialog.windowTitle())
                self.assertTrue(dialog.text())
                self.assertTrue(dialog.button(gui_windows.QMessageBox.Yes).text())
                self.assertTrue(dialog.button(gui_windows.QMessageBox.No).text())
                self.assertIs(
                    dialog.button(gui_windows.QMessageBox.No),
                    dialog.defaultButton(),
                )
            finally:
                dialog.close()

    def test_child_context_menu_close_actions_call_existing_routine_callbacks(self) -> None:
        instance = SimpleNamespace(
            instance_id="instance-a",
            display_name="오전루틴",
        )
        table = QTableWidget(1, 1)
        item = QTableWidgetItem(instance.display_name)
        item.setData(
            gui_main_table_loader.ROUTINE_ROW_KIND_ROLE,
            gui_main_table_loader.ROUTINE_ROW_CHILD,
        )
        item.setData(
            gui_main_table_loader.ROUTINE_INSTANCE_ID_ROLE,
            instance.instance_id,
        )
        group_id = "group-a"
        item.setData(gui_main_table_loader.ROUTINE_GROUP_ID_ROLE, group_id)
        table.setItem(0, 0, item)
        table.resize(480, 120)
        table.show()
        self.app.processEvents()

        window = SimpleNamespace(
            routine_table=table,
            request_routine_operation=MagicMock(),
            open_routine_settings_from_main_table=MagicMock(),
            delete_routine_instance_from_main_table=MagicMock(),
            start_routine_instance_name_edit=MagicMock(),
            open_routine_instance_stock_register_from_main_table=MagicMock(),
            _routine_stock_paths_by_group_instance={
                gui_windows.main_group_instance_relation_id(
                    group_id,
                    instance.instance_id,
                ): ("stocks/000001_Stock",),
            },
            _set_routine_operation_actions_enabled=lambda actions, enabled: (
                gui_windows.MainWindow._set_routine_operation_actions_enabled(
                    actions,
                    enabled,
                )
            ),
        )
        window._routine_tree_interaction_controller = (
            gui_windows._RoutineTreeInteractionController(window)
        )
        menu = MagicMock()
        actions = [MagicMock() for _ in range(6)]
        menu.addAction.side_effect = actions
        menu.exec_.return_value = None
        with (
            patch.object(gui_windows, "QMenu", return_value=menu),
            patch.object(
                gui_windows,
                "routine_instance_by_id",
                return_value=instance,
            ),
        ):
            gui_windows.MainWindow.open_routine_context_menu(
                window,
                table.visualItemRect(item).center(),
            )

        self.assertEqual(
            ["설정변경", "루틴삭제", "이름변경", "종목등록", "조기마감", "즉시청산"],
            [call_item.args[0] for call_item in menu.addAction.call_args_list],
        )
        menu.addSeparator.assert_called_once_with()
        for action in actions:
            action.triggered.connect.assert_called_once()

        for action in actions:
            action.triggered.connect.call_args.args[0]()
        window.open_routine_settings_from_main_table.assert_called_once_with(item)
        window.delete_routine_instance_from_main_table.assert_called_once_with(
            instance.instance_id,
            instance.display_name,
        )
        window.start_routine_instance_name_edit.assert_called_once_with(0)
        window.open_routine_instance_stock_register_from_main_table.assert_called_once_with(
            instance.instance_id
        )
        self.assertEqual(
            [
                call(
                    instance.instance_id,
                    instance.display_name,
                    "루틴",
                    gui_windows.ROUTINE_STATUS_EARLY_CLOSE,
                    stock_paths=("stocks/000001_Stock",),
                ),
                call(
                    instance.instance_id,
                    instance.display_name,
                    gui_windows.POLICY_MARKET,
                    gui_windows.ROUTINE_STATUS_IMMEDIATE_LIQUIDATION,
                    stock_paths=("stocks/000001_Stock",),
                ),
            ],
            window.request_routine_operation.call_args_list,
        )
        table.close()

    def test_main_routine_delete_blocks_assigned_stock(self) -> None:
        window = SimpleNamespace(refresh_all=MagicMock())
        assigned = [{"assigned_routine_instance_id": "instance-a"}]
        with (
            patch.object(setting_window, "read_base_stocks", return_value=assigned),
            patch.object(setting_window, "RoutineInstanceRepository") as repository,
            patch.object(setting_window.QMessageBox, "warning") as warning,
            patch.object(setting_window.QMessageBox, "question") as question,
        ):
            gui_windows.MainWindow.delete_routine_instance_from_main_table(
                window, "instance-a", "오전루틴"
            )

        repository.assert_not_called()
        question.assert_not_called()
        warning.assert_called_once()
        window.refresh_all.assert_not_called()

    def test_main_routine_delete_no_confirmation_does_not_delete(self) -> None:
        window = SimpleNamespace(refresh_all=MagicMock())
        with (
            patch.object(setting_window, "read_base_stocks", return_value=[]),
            patch.object(setting_window, "RoutineInstanceRepository") as repository,
            patch.object(
                setting_window.QMessageBox,
                "question",
                return_value=setting_window.QMessageBox.No,
            ),
        ):
            gui_windows.MainWindow.delete_routine_instance_from_main_table(
                window, "instance-a", "오전루틴"
            )

        repository.assert_not_called()
        window.refresh_all.assert_not_called()

    def test_main_routine_delete_success_uses_repository_and_refreshes(self) -> None:
        window = SimpleNamespace(refresh_all=MagicMock())
        repository = MagicMock()
        repository.delete_instance.return_value = SimpleNamespace(success=True, error="")
        with (
            patch.object(setting_window, "read_base_stocks", return_value=[]),
            patch.object(setting_window, "RoutineInstanceRepository", return_value=repository),
            patch.object(
                setting_window.QMessageBox,
                "question",
                return_value=setting_window.QMessageBox.Yes,
            ),
        ):
            gui_windows.MainWindow.delete_routine_instance_from_main_table(
                window, "instance-a", "오전루틴"
            )

        repository.delete_instance.assert_called_once_with("instance-a")
        window.refresh_all.assert_called_once_with()

    def test_main_routine_delete_failure_warns_without_refresh(self) -> None:
        window = SimpleNamespace(refresh_all=MagicMock())
        repository = MagicMock()
        repository.delete_instance.return_value = SimpleNamespace(
            success=False,
            error="삭제 실패",
        )
        with (
            patch.object(setting_window, "read_base_stocks", return_value=[]),
            patch.object(setting_window, "RoutineInstanceRepository", return_value=repository),
            patch.object(
                setting_window.QMessageBox,
                "question",
                return_value=setting_window.QMessageBox.Yes,
            ),
            patch.object(setting_window.QMessageBox, "warning") as warning,
        ):
            gui_windows.MainWindow.delete_routine_instance_from_main_table(
                window, "instance-a", "오전루틴"
            )

        repository.delete_instance.assert_called_once_with("instance-a")
        warning.assert_called_once()
        window.refresh_all.assert_not_called()

    def test_used_amount_buy_limit_and_usage_rate_are_independent(self) -> None:
        self.assertEqual(format_routine_used_amount(7_843_650), "\u20A97,843,650")
        self.assertEqual(
            format_routine_buy_limit(enabled=True, amount=12_500_000),
            "\u20A912,500,000",
        )
        self.assertEqual(
            format_routine_buy_limit_usage(
                enabled=True,
                limit_amount=12_500_000,
                used_amount=7_843_650,
            ),
            "62.75%",
        )
        self.assertEqual(
            format_routine_buy_limit_usage(
                enabled=True,
                limit_amount=10_000_000,
                used_amount=2_000_000,
            ),
            "20%",
        )

    def test_buy_limit_disabled_or_invalid_is_not_shown_as_zero(self) -> None:
        self.assertEqual(format_routine_buy_limit(enabled=False), "-")
        self.assertEqual(
            format_routine_buy_limit(enabled=True, amount=0),
            "-",
        )
        self.assertNotEqual(format_routine_buy_limit(enabled=False), "?? (0%)")
        self.assertEqual(format_routine_used_amount(), "-")
        self.assertEqual(format_routine_buy_limit_usage(enabled=False), "-")

    def test_initial_buy_display_uses_fixed_mode_specific_value(self) -> None:
        self.assertEqual(
            {
                "mode": "QUANTITY",
                "badge": "\uC8FC\uC218",
                "value": 20,
                "value_text": "20\uC8FC",
            },
            stock_initial_buy_display(
                {"trade_amount_type": "QUANTITY", "buy_qty": 20}
            ),
        )
        self.assertEqual(
            {
                "mode": "AMOUNT",
                "badge": "\uAE08\uC561",
                "value": 1_000_000,
                "value_text": "1,000,000\uC6D0",
            },
            stock_initial_buy_display(
                {"trade_amount_type": "AMOUNT", "buy_amount": 1_000_000}
            ),
        )

    def test_initial_buy_display_defaults_to_one_share(self) -> None:
        self.assertEqual(
            "1\uC8FC",
            stock_initial_buy_display({})["value_text"],
        )

    def test_initial_buy_mode_sort_prioritizes_requested_mode(self) -> None:
        first_instance = [
            {"code": "Q1", "initial_buy": {"mode": "QUANTITY"}},
            {"code": "A1", "initial_buy": {"mode": "AMOUNT"}},
        ]
        second_instance = [
            {"code": "Q2", "initial_buy": {"mode": "QUANTITY"}},
            {"code": "A2", "initial_buy": {"mode": "AMOUNT"}},
        ]
        stocks = [*first_instance, *second_instance]

        amount_first = list(stocks)
        gui_main_table_loader.sort_routine_stock_rows_by_initial_buy_mode(
            amount_first,
            "AMOUNT",
        )
        quantity_first = list(stocks)
        gui_main_table_loader.sort_routine_stock_rows_by_initial_buy_mode(
            quantity_first,
            "QUANTITY",
        )

        self.assertEqual(
            ["A1", "A2", "Q1", "Q2"],
            [stock["code"] for stock in amount_first],
        )
        self.assertEqual(
            ["Q1", "Q2", "A1", "A2"],
            [stock["code"] for stock in quantity_first],
        )

    def test_stock_trade_counts_use_distinct_orders_for_current_trading_day(self) -> None:
        records = (
            {
                "code": "A005930",
                "side": "BUY",
                "event_type": "PARTIAL_FILL",
                "broker_order_no": "BUY-1",
                "account_no": "123",
                "received_at": "2026-07-27 09:01:00",
            },
            {
                "code": "005930",
                "side": "BUY",
                "event_type": "FULL_FILL",
                "broker_order_no": "BUY-1",
                "account_no": "123",
                "received_at": "2026-07-27 09:02:00",
            },
            {
                "code": "005930",
                "side": "BUY",
                "event_type": "FULL_FILL",
                "broker_order_no": "BUY-2",
                "account_no": "123",
                "received_at": "2026-07-27 10:00:00",
            },
            {
                "code": "005930",
                "side": "SELL",
                "event_type": "FULL_FILL",
                "broker_order_no": "SELL-1",
                "account_no": "123",
                "received_at": "2026-07-27 11:00:00",
            },
            {
                "code": "005930",
                "side": "SELL",
                "event_type": "FULL_FILL",
                "broker_order_no": "OLD-SELL",
                "account_no": "123",
                "received_at": "2026-07-26 11:00:00",
            },
            {
                "code": "000660",
                "side": "SELL",
                "event_type": "FULL_FILL",
                "broker_order_no": "SELL-2",
                "account_no": "123",
                "received_at": "2026-07-27 11:30:00",
            },
        )

        self.assertEqual(
            {
                "005930": (2, 1),
                "000660": (0, 1),
            },
            gui_main_table_loader.stock_trade_counts_by_code(
                records,
                trading_day="2026-07-27",
            ),
        )

    def test_routine_stock_row_displays_trade_counts_instead_of_pending(self) -> None:
        row = gui_main_table_loader._routine_tree_stock_row(
            SimpleNamespace(),
            definition_id="indicator_follow",
            instance_id="instance-a",
            stock={
                "code": "005930",
                "name": "?�성?�자",
                "enabled": True,
                "stock_path": "",
                "state": {
                    "pending_buy_qty": 9,
                    "pending_sell_qty": 8,
                },
                "config": {},
            },
            trade_counts=(3, 2),
        )

        self.assertEqual("매매(3 / 2)", row["stock_values"][10])
        trade_metric = row["stock_metrics"][3]
        self.assertEqual(
            ("매매", "3", "2"),
            (trade_metric.label, trade_metric.value1, trade_metric.value2),
        )
        self.assertEqual(5, row["sort_metrics"]["trade"])
        self.assertIn(row["stock_values"][10], row["name"])

    def test_routine_stock_row_stores_token_style_snapshots(self) -> None:
        row = gui_main_table_loader._routine_tree_stock_row(
            SimpleNamespace(startup_recovery_session_ready=lambda **_kwargs: True),
            definition_id="indicator_follow",
            instance_id="instance-a",
            stock={
                "code": "005380",
                "name": "?��?�?",
                "enabled": True,
                "stock_path": "",
                "state": {
                    "status": "MONITORING",
                    "holding_qty": 0,
                    "trade_enabled": False,
                },
                "config": {
                    "operation_mode": "CONTINUOUS",
                    "routine": "지?�추종매�?",
                },
            },
            trade_counts=(0, 0),
        )

        tokens = row["stock_display_tokens"]
        self.assertEqual(len(row["stock_values"]), len(tokens))
        self.assertEqual(row["stock_values"][0], tokens[0]["text"])
        self.assertEqual(row["stock_values"][2], tokens[2]["text"])
        self.assertEqual(row["stock_values"][3], tokens[3]["text"])
        self.assertEqual(row["stock_values"][4], tokens[4]["text"])
        self.assertEqual(row["stock_values"][5], tokens[5]["text"])
        self.assertEqual(row["stock_values"][6], tokens[6]["text"])
        self.assertTrue(str(tokens[2]["foreground"]).startswith("#"))
        self.assertTrue(str(tokens[3]["foreground"]).startswith("#"))
        self.assertTrue(str(tokens[4]["background"]).startswith("#"))
        self.assertTrue(str(tokens[5]["background"]).startswith("#"))
        self.assertTrue(str(tokens[6]["background"]).startswith("#"))

    def test_routine_stock_profit_token_reuses_auto_trade_directional_contract(self) -> None:
        cases = (
            (120, DIRECTIONAL_NEGATIVE_COLOR),
            (80, DIRECTIONAL_POSITIVE_COLOR),
            (100, DIRECTIONAL_NEUTRAL_COLOR),
        )
        for current_price, expected_color in cases:
            profit_amount = (current_price - 100) * 10
            with self.subTest(current_price=current_price), patch.object(
                gui_main_table_loader,
                "project_confirmable_cumulative_pnl",
                return_value={
                    "available": True,
                    "cumulative_profit": profit_amount,
                    "cumulative_rate": float(current_price - 100),
                },
            ):
                row = gui_main_table_loader._routine_tree_stock_row(
                    SimpleNamespace(startup_recovery_session_ready=lambda **_kwargs: True),
                    definition_id="indicator_follow",
                    instance_id="instance-a",
                    stock={
                        "code": "005380",
                        "name": "Hyundai",
                        "enabled": True,
                        "stock_path": "",
                        "state": {
                            "holding_qty": 10,
                            "avg_price": 100,
                            "current_price": current_price,
                        },
                        "config": {
                            "operation_mode": "CONTINUOUS",
                            "routine": "routine",
                        },
                    },
                    trade_counts=(0, 0),
                )

                profit_token = row["stock_display_tokens"][9]
                profit_text = row["stock_values"][9]
                self.assertEqual(profit_text, profit_token["text"])
                self.assertEqual(profit_text, profit_token["tooltip"])
                self.assertEqual(
                    QColor(profit_loss_value_color(profit_amount)).name().lower(),
                    profit_token["foreground"],
                )
                self.assertEqual(QColor(expected_color).name().lower(), profit_token["foreground"])
                self.assertEqual(int(Qt.AlignCenter), profit_token["alignment"])

    def test_routine_stock_metric_sequence_uses_profit_token_foreground(self) -> None:
        app = QApplication.instance() or QApplication([])
        pixmap = QPixmap(760, 32)
        painter = QPainter(pixmap)
        painter.setFont(QFont())
        captured: list[tuple[str, str]] = []

        def capture_metric_draw(active_painter, text, _rects, *, hide_left_value=False):
            captured.append((text, active_painter.pen().color().name().lower()))

        try:
            with patch.object(
                gui_windows,
                "_draw_main_stock_metric_components",
                side_effect=capture_metric_draw,
            ):
                gui_windows._draw_routine_stock_metric_text_sequence(
                    painter,
                    row_rect=QRect(0, 0, 760, 24),
                    start_x=0,
                    texts=[
                        "보유(10�?/ 1,000)",
                        "가�?100 / 120)",
                        "?�익(+200 / +20.00%)",
                        "매매(0 / 0)",
                    ],
                    foregrounds=[
                        QColor(DIRECTIONAL_NEUTRAL_COLOR),
                        QColor(DIRECTIONAL_NEUTRAL_COLOR),
                        QColor(DIRECTIONAL_NEGATIVE_COLOR),
                        QColor(DIRECTIONAL_NEUTRAL_COLOR),
                    ],
                )
        finally:
            painter.end()

        self.assertEqual(QColor(DIRECTIONAL_NEGATIVE_COLOR).name().lower(), captured[2][1])

    def test_routine_stock_delegate_prefers_token_style_over_row_visual_state(self) -> None:
        app = QApplication.instance() or QApplication([])
        option = SimpleNamespace(
            state=0,
            palette=app.palette(),
        )
        selected_option = SimpleNamespace(
            state=QStyle.State_Selected,
            palette=app.palette(),
        )

        self.assertEqual(
            "#16a34a",
            gui_windows._RoutineTreeItemDelegate._stock_token_foreground(
                {"foreground": "#16a34a"},
                option,
                visually_enabled=False,
            ).name().lower(),
        )
        self.assertEqual(
            "#9ca3af",
            gui_windows._RoutineTreeItemDelegate._stock_token_foreground(
                {},
                option,
                visually_enabled=False,
            ).name().lower(),
        )
        self.assertEqual(
            selected_option.palette.highlightedText().color().name().lower(),
            gui_windows._RoutineTreeItemDelegate._stock_token_foreground(
                {"foreground": "#16a34a"},
                selected_option,
                visually_enabled=False,
            ).name().lower(),
        )

    def test_price_metric_sort_uses_current_price_not_average_price(self) -> None:
        low_current = gui_main_table_loader._routine_tree_stock_metric_values(
            SimpleNamespace(),
            {
                "code": "000001",
                "name": "High Average",
                "stock_path": "",
                "state": {
                    "avg_price": 90_000,
                    "current_price": 1_500,
                },
                "config": {},
            },
        )
        high_current = gui_main_table_loader._routine_tree_stock_metric_values(
            SimpleNamespace(),
            {
                "code": "000002",
                "name": "Low Average",
                "stock_path": "",
                "state": {
                    "avg_price": 1_000,
                    "current_price": 75_000,
                },
                "config": {},
            },
        )
        rows = [
            ("high-average", low_current[-1]),
            ("low-average", high_current[-1]),
        ]

        self.assertEqual(1_500, low_current[-1]["price"])
        self.assertEqual(75_000, high_current[-1]["price"])
        self.assertEqual(
            ["high-average", "low-average"],
            [
                name
                for name, _metrics in sorted(
                    rows,
                    key=lambda row: row[1]["price"],
                )
            ],
        )
        self.assertEqual(
            ["low-average", "high-average"],
            [
                name
                for name, _metrics in sorted(
                    rows,
                    key=lambda row: row[1]["price"],
                    reverse=True,
                )
            ],
        )
        self.assertEqual("90,000", low_current[0][1].value1)
        self.assertEqual("1,500", low_current[0][1].value2)

    def test_main_stock_profit_metric_uses_numeric_zero_when_pnl_unavailable(self) -> None:
        unavailable = {
            "available": False,
            "reason": "BROKER_NOT_CONNECTED",
        }
        stock = {
            "code": "005930",
            "name": "삼성전자",
            "stock_path": "",
            "state": {
                "holding_qty": 0,
                "avg_price": 0,
                "current_price": None,
            },
            "config": {},
        }

        for connected in (False, True):
            window = SimpleNamespace(
                kiwoom_api=SimpleNamespace(is_connected=lambda: connected)
            )
            with patch.object(
                gui_main_table_loader,
                "project_confirmable_cumulative_pnl",
                return_value=unavailable,
            ):
                metrics, _led, _limit, _consumed, sort_values = (
                    gui_main_table_loader._routine_tree_stock_metric_values(
                        window,
                        stock,
                    )
                )

            profit_metric = metrics[2]
            self.assertEqual(("0", "0.00%"), (profit_metric.value1, profit_metric.value2))
            self.assertNotIn("확인", f"{profit_metric.value1} / {profit_metric.value2}")
            self.assertNotIn("-", f"{profit_metric.value1} / {profit_metric.value2}")
            self.assertEqual(0, sort_values["profit"])
        self.assertEqual(
            {"available": False, "reason": "BROKER_NOT_CONNECTED"},
            unavailable,
        )

    def test_main_stock_profit_row_preserves_numeric_value_and_direction_color(self) -> None:
        cases = (
            (
                {"available": True, "cumulative_profit": 1_250, "cumulative_rate": 0.53},
                "수익(+1,250 / +0.53%)",
                DIRECTIONAL_NEGATIVE_COLOR,
            ),
            (
                {"available": True, "cumulative_profit": -2_340, "cumulative_rate": -0.97},
                "수익(-2,340 / -0.97%)",
                DIRECTIONAL_POSITIVE_COLOR,
            ),
            (
                {"available": True, "cumulative_profit": 0, "cumulative_rate": None},
                "수익(0 / 0.00%)",
                DIRECTIONAL_NEUTRAL_COLOR,
            ),
        )
        stock = {
            "code": "005930",
            "name": "삼성전자",
            "stock_path": "",
            "state": {},
            "config": {},
        }

        for projection, expected_text, expected_color in cases:
            with self.subTest(expected_text=expected_text), patch.object(
                gui_main_table_loader,
                "project_confirmable_cumulative_pnl",
                return_value=projection,
            ):
                row = gui_main_table_loader._routine_tree_stock_row(
                    SimpleNamespace(),
                    definition_id="indicator_follow",
                    instance_id="instance-a",
                    stock=stock,
                )

            self.assertEqual(expected_text, row["stock_values"][9])
            self.assertEqual(expected_text, row["stock_display_tokens"][9]["text"])
            self.assertEqual(expected_text, row["stock_display_tokens"][9]["tooltip"])
            self.assertEqual(
                expected_color.lower(),
                row["stock_display_tokens"][9]["foreground"],
            )

    def test_visible_main_window_stock_row_uses_numeric_profit_fallback(self) -> None:
        api = SimpleNamespace(
            unavailable_reason=lambda: "test double",
            login_state_changed=None,
            raw_chejan_received=None,
        )
        host = SimpleNamespace(
            operation_cycle_completed=SimpleNamespace(connect=MagicMock()),
            shutdown=MagicMock(),
        )
        stock = {
            "code": "005930",
            "name": "삼성전자",
            "stock_path": "",
            "state": {},
            "config": {},
        }

        with (
            patch.object(gui_windows, "KiwoomApi", return_value=api),
            patch.object(gui_windows, "normalize_base_stock_single_routine_file"),
            patch.object(
                gui_windows.MainWindow,
                "main_monitoring_auto_trade_operation_host",
                return_value=host,
            ),
            patch.object(
                gui_windows.MainWindow,
                "refresh_startup_recovery_status",
                return_value={},
            ),
            patch.object(gui_windows.MainWindow, "refresh_all"),
            patch.object(gui_windows, "append_owner_event_once"),
            patch.object(
                gui_main_table_loader,
                "project_confirmable_cumulative_pnl",
                return_value={"available": False, "reason": "BROKER_NOT_CONNECTED"},
            ),
        ):
            window = gui_windows.MainWindow()
            try:
                row = gui_main_table_loader._routine_tree_stock_row(
                    window,
                    definition_id="indicator_follow",
                    instance_id="instance-a",
                    stock=stock,
                )
                item = QTableWidgetItem("")
                item.setData(
                    gui_main_table_loader.ROUTINE_ROW_KIND_ROLE,
                    gui_main_table_loader.ROUTINE_ROW_STOCK,
                )
                item.setData(
                    gui_main_table_loader.ROUTINE_STOCK_VALUES_ROLE,
                    row["stock_values"],
                )
                item.setData(
                    gui_main_table_loader.ROUTINE_STOCK_METRICS_ROLE,
                    row["stock_metrics"],
                )
                item.setData(
                    gui_main_table_loader.ROUTINE_STOCK_DISPLAY_ROLE,
                    row["stock_display_tokens"],
                )
                item.setData(
                    gui_main_table_loader.ROUTINE_CHECKBOX_VISUAL_ENABLED_ROLE,
                    True,
                )
                window.routine_table.setRowCount(1)
                window.routine_table.setItem(0, 0, item)
                window.resize(1280, 720)
                window.show()
                self.app.processEvents()

                visible_metrics = gui_windows._routine_stock_metric_texts(
                    list(
                        item.data(gui_main_table_loader.ROUTINE_STOCK_VALUES_ROLE)
                    ),
                    tuple(
                        item.data(gui_main_table_loader.ROUTINE_STOCK_METRICS_ROLE)
                    ),
                )
                self.assertTrue(window.routine_table.isVisible())
                self.assertEqual("수익(0 / 0.00%)", visible_metrics[2])
                self.assertNotIn("확인 필요", " | ".join(visible_metrics))
            finally:
                window.close()

    def test_initial_buy_slot_fits_maximum_amount_and_share_text(self) -> None:
        font_metrics = QFontMetrics(QFont("Malgun Gothic", 9))
        slot_width = gui_main_table_loader.ROUTINE_STOCK_BASE_COLUMN_WIDTHS[1]
        required_amount_width = (
            gui_windows.INITIAL_BUY_BADGE_WIDTH
            + gui_windows.INITIAL_BUY_BADGE_GAP
            + font_metrics.horizontalAdvance("99,999,999??")
            + 1
        )
        required_quantity_width = (
            gui_windows.INITIAL_BUY_BADGE_WIDTH
            + gui_windows.INITIAL_BUY_BADGE_GAP
            + font_metrics.horizontalAdvance("99,999�?")
            + 1
        )

        self.assertGreaterEqual(slot_width, required_amount_width)
        self.assertGreaterEqual(slot_width, required_quantity_width)
        badge_rect = gui_windows._initial_buy_component_rects(QRect(0, 0, 176, 24))[
            "badge"
        ]
        self.assertEqual(64, badge_rect.width())
        self.assertEqual(
            gui_windows.AUTO_TRADE_SETTING_TOP_CONTROL_ROW_HEIGHT,
            badge_rect.height(),
        )
        filter_font = QApplication.font("QPushButton")
        badge_font = gui_windows._initial_buy_badge_font()
        self.assertEqual(filter_font.family(), badge_font.family())
        self.assertEqual(filter_font.pointSize(), badge_font.pointSize())
        self.assertEqual(QFont.DemiBold, badge_font.weight())
        self.assertEqual("#B98200", gui_windows.INITIAL_BUY_AMOUNT_COLOR)
        self.assertEqual("#6F52B5", gui_windows.INITIAL_BUY_QUANTITY_COLOR)

    def test_routine_instance_metric_formatting_contract(self) -> None:
        self.assertEqual(
            routine_instance_buy_limit_text(enabled=True, amount=2_000_000),
            "\uD55C\uB3C4(2,000,000)",
        )
        self.assertEqual(
            routine_instance_buy_limit_text(enabled=False, amount=None),
            "\uD55C\uB3C4(\uBBF8\uC124\uC815)",
        )
        self.assertEqual(
            routine_instance_buy_limit_text(enabled=True, amount=None),
            "\uD55C\uB3C4(\uB300\uAE30)",
        )
        self.assertEqual(
            routine_instance_buy_limit_text(enabled=True, amount=0),
            "\uD55C\uB3C4(\uD655\uC778 \uD544\uC694)",
        )
        self.assertEqual(
            routine_instance_consumed_text(
                consumed_amount=1_000_000,
                buy_limit_enabled=True,
                buy_limit_amount=2_000_000,
            ),
            "\uC18C\uBAA8(1,000,000 / 50.0%)",
        )
        self.assertEqual(
            routine_instance_consumed_text(
                consumed_amount=1_000_000,
                buy_limit_enabled=False,
                buy_limit_amount=None,
            ),
            "\uC18C\uBAA8(1,000,000 / -)",
        )
        self.assertEqual(
            routine_instance_consumed_text(
                consumed_amount=1_000_000,
                buy_limit_enabled=True,
                buy_limit_amount=0,
            ),
            "\uC18C\uBAA8(1,000,000 / \uD655\uC778 \uD544\uC694)",
        )
        self.assertEqual(
            routine_instance_profit_text(
                profit_amount=35_200,
                cost_basis=1_248_227,
            ),
            ("\uC218\uC775(+35,200 / +2.82%)", DIRECTIONAL_NEGATIVE_COLOR),
        )
        self.assertEqual(
            routine_instance_profit_text(
                profit_amount=-12_500,
                cost_basis=1_250_000,
            ),
            ("\uC218\uC775(-12,500 / -1.00%)", DIRECTIONAL_POSITIVE_COLOR),
        )
        self.assertEqual(
            routine_instance_profit_text(profit_amount=0, cost_basis=0),
            ("\uC218\uC775(0 / 0.00%)", DIRECTIONAL_NEUTRAL_COLOR),
        )
        self.assertEqual(
            gui_main_table_loader.routine_group_profit_projection(
                ("routine-a", "routine-b"),
                {
                    "routine-a": {
                        "profit_amount": 35_200,
                        "profit_cost_basis": 1_248_227,
                        "profit_unknown": False,
                    },
                    "routine-b": {
                        "profit_amount": -12_500,
                        "profit_cost_basis": 1_250_000,
                        "profit_unknown": False,
                    },
                },
            ),
            ("\uC218\uC775(+22,700 / +0.91%)", DIRECTIONAL_NEGATIVE_COLOR, 22_700.0),
        )
        self.assertEqual(
            routine_instance_profit_text(
                profit_amount=0,
                cost_basis=0,
                unknown=True,
            )[0],
            "\uC218\uC775(\uD655\uC778 \uD544\uC694 / \uD655\uC778 \uD544\uC694)",
        )
        self.assertEqual(DIRECTIONAL_POSITIVE_COLOR, directional_value_color("+1,250"))
        self.assertEqual(DIRECTIONAL_NEGATIVE_COLOR, directional_value_color("-325"))
        self.assertEqual(DIRECTIONAL_NEUTRAL_COLOR, directional_value_color("0.00"))
        self.assertEqual("+3.25%", format_signed_percent(3.25))
        self.assertEqual("-1.40%", format_signed_percent(-1.4))
        self.assertEqual("0.00%", format_signed_percent(0))
        self.assertEqual("0.00%", format_signed_percent(-0.004))

    def test_instance_stock_counts_aggregate_instance_usage_and_profit(self) -> None:
        instance = RoutineInstanceRecord(
            instance_id="a52f539d-4f18-4ef6-b0cf-f471567982a1",
            definition_id="indicator_follow",
            display_name="?�?�주 추세??",
            source_routine_name="지?�추종매�?",
            persisted=True,
            source="PERSISTED",
            enabled=False,
            real_trade_allowed=False,
            buy_limit_enabled=True,
            buy_limit_amount=2_000_000,
            rules_path=Path("instance-rules.json"),
        )

        def read_json(path):
            name = Path(path).name
            if name == "config.json":
                if "assigned" in str(path) or "review" in str(path):
                    return {"assigned_routine_instance_id": instance.instance_id}
                return {"assigned_routine_instance_id": "other-instance"}
            if name == "state.json":
                if "review" in str(path):
                    return {
                        "status": "REVIEW_REQUIRED",
                        "review_required": True,
                        "holding_qty": 20,
                        "avg_price": 2000,
                        "current_price": 2100,
                    }
                if "assigned" in str(path):
                    return {
                        "status": "RUNNING",
                        "trade_started": True,
                        "holding_qty": 10,
                        "avg_price": 1000,
                        "current_price": 1035.2,
                    }
                return {"status": "RUNNING", "holding_qty": 10, "avg_price": 1000}
            return {}

        with (
            patch.object(gui_main_table_loader, "load_persisted_routine_instances", return_value=[instance]),
            patch.object(
                gui_main_table_loader,
                "read_base_stocks",
                return_value=[
                    {
                        "code": "111111",
                        "name": "?�상종목",
                        "stock_path": "stocks/assigned",
                    },
                    {
                        "code": "222222",
                        "name": "검?�종�?",
                        "stock_path": "stocks/review",
                    },
                    {
                        "code": "333333",
                        "name": "?�른종목",
                        "stock_path": "stocks/other",
                    },
                ],
            ),
            patch.object(gui_main_table_loader, "read_json_dict", side_effect=read_json),
            patch.object(
                gui_main_table_loader,
                "project_current_stock_pnl_snapshot",
                return_value={
                    "111111": {
                        "available": True,
                        "cumulative_profit": 352,
                        "completed_buy_cost": 10_000,
                        "open_cost": 0,
                    }
                },
            ),
        ):
            counts = gui_main_table_loader._instance_stock_counts()
            gui_main_table_loader._refresh_instance_pnl_from_batch(counts)

        self.assertEqual(2, counts[instance.instance_id]["registered"])
        self.assertEqual(1, counts[instance.instance_id]["operation_or_stopped"])
        self.assertEqual(1, counts[instance.instance_id]["review"])
        self.assertEqual(10_000, counts[instance.instance_id]["consumed_amount"])
        self.assertEqual(10_000, counts[instance.instance_id]["profit_cost_basis"])
        self.assertAlmostEqual(352, counts[instance.instance_id]["profit_amount"])
        self.assertFalse(counts[instance.instance_id]["consumed_unknown"])
        self.assertFalse(counts[instance.instance_id]["profit_unknown"])
        self.assertEqual(
            ["stocks/assigned"],
            [
                stock["stock_path"]
                for stock in counts[instance.instance_id]["stocks"]
            ],
        )
        self.assertNotIn("other-instance", counts)

    def test_instance_stock_counts_exclude_review_stocks_from_rows_and_totals(self) -> None:
        instance = SimpleNamespace(instance_id="instance-a")
        stock_records = [
            {
                "code": f"11111{index}",
                "name": f"?�상{index}",
                "stock_path": f"stocks/normal-{index}",
            }
            for index in range(3)
        ] + [
            {
                "code": f"22222{index}",
                "name": f"review-{index}",
                "stock_path": f"stocks/review-{index}",
            }
            for index in range(2)
        ]

        def read_json(path):
            if Path(path).name == "config.json":
                return {"assigned_routine_instance_id": instance.instance_id}
            if "review-" in str(path):
                return {
                    "status": "REVIEW_REQUIRED",
                    "review_required": True,
                }
            return {"status": "STOPPED", "trade_enabled": False}

        with (
            patch.object(
                gui_main_table_loader,
                "load_persisted_routine_instances",
                return_value=[instance],
            ),
            patch.object(
                gui_main_table_loader,
                "read_base_stocks",
                return_value=stock_records,
            ),
            patch.object(
                gui_main_table_loader,
                "read_json_dict",
                side_effect=read_json,
            ),
        ):
            counts = gui_main_table_loader._instance_stock_counts()

        instance_count = counts[instance.instance_id]
        self.assertEqual(5, instance_count["registered"])
        self.assertEqual(3, instance_count["operation_or_stopped"])
        self.assertEqual(2, instance_count["review"])
        self.assertEqual(
            instance_count["registered"],
            instance_count["excluded"]
            + instance_count["operation_or_stopped"]
            + instance_count["review"],
        )
        self.assertEqual(
            {"111110", "111111", "111112"},
            {stock["code"] for stock in instance_count["stocks"]},
        )

    def test_instance_stock_counts_use_exclusive_priority(self) -> None:
        instance = SimpleNamespace(instance_id="instance-a")
        stock_records = [
            {
                "code": str(index + 1).zfill(6),
                "name": name,
                "stock_path": f"stocks/{name}",
            }
            for index, name in enumerate(
                ("normal", "excluded", "review", "excluded-review")
            )
        ]
        review_names = {"review", "excluded-review"}

        def read_json(path):
            stock_name = Path(path).parent.name
            if Path(path).name == "config.json":
                return {
                    "assigned_routine_instance_id": instance.instance_id,
                    "operation_excluded": stock_name in {"excluded", "excluded-review"},
                }
            return {
                "status": "ERROR" if stock_name in review_names else "RUNNING",
                "review_required": stock_name in review_names,
                "trade_started": stock_name == "normal",
            }

        with (
            patch.object(
                gui_main_table_loader,
                "load_persisted_routine_instances",
                return_value=[instance],
            ),
            patch.object(
                gui_main_table_loader,
                "read_base_stocks",
                return_value=stock_records,
            ),
            patch.object(
                gui_main_table_loader,
                "read_json_dict",
                side_effect=read_json,
            ),
            patch.object(
                gui_main_table_loader,
                "auto_trade_running_registered_operation_targets",
                return_value=[
                    (
                        Path(gui_main_table_loader.__file__).resolve().parent
                        / "stocks"
                        / "normal",
                        "000001",
                        "normal",
                    )
                ],
            ),
        ):
            count = gui_main_table_loader._instance_stock_counts(
                window=SimpleNamespace()
            )[instance.instance_id]
            review_names.remove("excluded-review")
            released_count = gui_main_table_loader._instance_stock_counts(
                window=SimpleNamespace()
            )[instance.instance_id]

        self.assertEqual(4, count["registered"])
        self.assertEqual(1, count["excluded"])
        self.assertEqual(2, count["review"])
        self.assertEqual(1, count["operation_or_stopped"])
        self.assertEqual(1, count["operation_running"])
        self.assertEqual(
            count["registered"],
            count["review"] + count["excluded"] + count["operation_or_stopped"],
        )
        self.assertEqual(2, released_count["excluded"])
        self.assertEqual(1, released_count["review"])

    def test_instance_stock_counts_ignore_stale_running_outside_current_session(self) -> None:
        instance = SimpleNamespace(instance_id="instance-a")
        codes = (
            "000660",
            "003550",
            "005930",
            "012330",
            "068270",
            "086520",
            "247540",
            "323410",
        )
        review_codes = {"000660", "323410"}
        stale_running_codes = {"068270", "086520", "247540"}
        stock_records = [
            {
                "code": code,
                "name": f"stock-{code}",
                "stock_path": f"stocks/{code}_stock-{code}",
            }
            for code in codes
        ]

        def read_json(path):
            code = Path(path).parent.name.split("_", 1)[0]
            if Path(path).name == "config.json":
                return {
                    "assigned_routine_instance_id": instance.instance_id,
                    "operation_excluded": code == "000660",
                }
            if code in review_codes:
                return {
                    "status": "REVIEW_REQUIRED",
                    "review_required": True,
                    "trade_enabled": False,
                }
            return {
                "status": "RUNNING" if code in stale_running_codes else "STOPPED",
                "trade_enabled": code in stale_running_codes,
            }

        with (
            patch.object(
                gui_main_table_loader,
                "load_persisted_routine_instances",
                return_value=[instance],
            ),
            patch.object(
                gui_main_table_loader,
                "read_base_stocks",
                return_value=stock_records,
            ),
            patch.object(gui_main_table_loader, "read_json_dict", side_effect=read_json),
            patch.object(
                gui_main_table_loader,
                "auto_trade_running_registered_operation_targets",
                return_value=[],
            ),
            patch.object(
                gui_main_table_loader,
                "project_confirmable_cumulative_pnl",
                return_value={"available": False},
            ),
        ):
            count = gui_main_table_loader._instance_stock_counts(
                window=SimpleNamespace()
            )[instance.instance_id]

        self.assertEqual(8, count["registered"])
        self.assertEqual(0, count["excluded"])
        self.assertEqual(2, count["review"])
        self.assertEqual(6, count["normal"])
        self.assertEqual(0, count["operation_running"])
        self.assertEqual(0, count["running"])
        self.assertEqual(6, count["stopped"])
        self.assertEqual(
            gui_main_table_loader.ROUTINE_STATUS_STOPPED,
            gui_main_table_loader.routine_instance_operation_status(
                count["operation_running"]
            ),
        )
        widget = gui_main_table_loader.create_routine_instance_status_widget(
            gui_main_table_loader.ROUTINE_STATUS_STOPPED,
            registered=count["registered"],
            excluded=count["excluded"],
            operation_or_stopped=count["stopped"],
            review=count["review"],
        )
        self.assertEqual(
            "정지",
            widget.findChild(
                QLabel,
                "routineInstanceOperationOrStoppedLabel",
            ).text(),
        )
        widget.close()

    def test_instance_stock_counts_filter_current_running_by_instance(self) -> None:
        instances = [
            SimpleNamespace(instance_id="instance-a"),
            SimpleNamespace(instance_id="instance-b"),
        ]
        stock_records = [
            {
                "code": f"00000{index + 1}",
                "name": f"stock-{index + 1}",
                "stock_path": f"stocks/stock-{index + 1}",
            }
            for index in range(4)
        ]
        project_root = Path(gui_main_table_loader.__file__).resolve().parent
        running_paths = {
            str((project_root / "stocks" / "stock-1").resolve()),
            str((project_root / "stocks" / "stock-2").resolve()),
        }

        def read_json(path):
            stock_number = int(Path(path).parent.name.rsplit("-", 1)[-1])
            if Path(path).name == "config.json":
                return {
                    "assigned_routine_instance_id": (
                        "instance-a" if stock_number <= 3 else "instance-b"
                    )
                }
            return {"status": "RUNNING", "trade_enabled": True}

        current_running = [
            (Path(path), Path(path).name, Path(path).name)
            for path in sorted(running_paths)
        ]
        with (
            patch.object(
                gui_main_table_loader,
                "load_persisted_routine_instances",
                return_value=instances,
            ),
            patch.object(
                gui_main_table_loader,
                "read_base_stocks",
                return_value=stock_records,
            ),
            patch.object(gui_main_table_loader, "read_json_dict", side_effect=read_json),
            patch.object(
                gui_main_table_loader,
                "auto_trade_running_registered_operation_targets",
                return_value=current_running,
            ),
            patch.object(
                gui_main_table_loader,
                "project_confirmable_cumulative_pnl",
                return_value={"available": False},
            ),
        ):
            counts = gui_main_table_loader._instance_stock_counts(
                window=SimpleNamespace()
            )

        self.assertEqual(2, counts["instance-a"]["operation_running"])
        self.assertEqual(1, counts["instance-a"]["stopped"])
        self.assertEqual(0, counts["instance-b"]["operation_running"])
        self.assertEqual(1, counts["instance-b"]["stopped"])

    def test_main_and_setting_instance_counts_share_current_running_source(self) -> None:
        instance = SimpleNamespace(instance_id="instance-a")
        stock_record = {
            "code": "005930",
            "name": "삼성전자",
            "stock_path": "stocks/005930_삼성전자",
        }
        stock_dir = (
            Path(gui_main_table_loader.__file__).resolve().parent
            / stock_record["stock_path"]
        )
        main_window = SimpleNamespace(name="main")
        setting_window = SimpleNamespace(name="setting")

        def read_json(path):
            if Path(path).name == "config.json":
                return {"assigned_routine_instance_id": instance.instance_id}
            return {"status": "RUNNING", "trade_enabled": True}

        with (
            patch.object(
                gui_main_table_loader,
                "load_persisted_routine_instances",
                return_value=[instance],
            ),
            patch.object(
                gui_main_table_loader,
                "read_base_stocks",
                return_value=[stock_record],
            ),
            patch.object(gui_main_table_loader, "read_json_dict", side_effect=read_json),
            patch.object(
                gui_main_table_loader,
                "auto_trade_running_registered_operation_targets",
                return_value=[(stock_dir, "005930", "삼성전자")],
            ) as current_running,
            patch.object(
                gui_main_table_loader,
                "project_confirmable_cumulative_pnl",
                return_value={"available": False},
            ),
        ):
            main_counts = gui_main_table_loader._instance_stock_counts(
                window=main_window
            )
            setting_counts = (
                gui_windows.AutoTradeSettingWindow._routine_instance_operation_counts(
                    setting_window
                )
            )

        self.assertEqual(
            main_counts[instance.instance_id]["operation_running"],
            setting_counts[instance.instance_id]["operation_running"],
        )
        self.assertEqual(
            [main_window, setting_window],
            [call.args[0] for call in current_running.call_args_list],
        )

    def test_profit_signal_uses_gross_and_net_rates_without_cost_hardcoding(self) -> None:
        self.assertEqual(routine_profit_signal(-1.25, -1.4)[0:2], ("LOSS", "-1.25%"))
        self.assertEqual(
            routine_profit_signal(0.08, -0.02)[0:2],
            ("COST_NOT_RECOVERED", "+0.08%"),
        )
        self.assertEqual(routine_profit_signal(1.42, 1.1)[0:2], ("NET_PROFIT", "+1.42%"))
        self.assertEqual(routine_profit_signal(0, 0)[0:2], ("NEUTRAL", "0.00%"))
        self.assertEqual(routine_profit_signal(None, None)[0:2], ("NEUTRAL", "-"))
        self.assertEqual(routine_profit_signal(0.08, None)[0], "NEUTRAL")

        for gross_rate, net_rate, expected_signal in (
            (-1.25, -1.4, "LOSS"),
            (0.08, -0.02, "COST_NOT_RECOVERED"),
            (1.42, 1.1, "NET_PROFIT"),
            (None, None, "NEUTRAL"),
        ):
            signal, _text, color = routine_profit_signal(gross_rate, net_rate)
            self.assertEqual(signal, expected_signal)
            self.assertEqual(color, ROUTINE_PROFIT_SIGNAL_COLORS[expected_signal])

    def test_routine_instance_status_stamp_mapping_is_fixed(self) -> None:
        expected = {
            gui_main_table_loader.ROUTINE_STATUS_RUNNING: "#16A34A",
            gui_main_table_loader.ROUTINE_STATUS_STOPPED: "#DC2626",
        }
        self.assertEqual(expected, gui_main_table_loader.ROUTINE_STATUS_STAMP_COLORS)
        for status, color in expected.items():
            self.assertEqual(
                (status, color),
                gui_main_table_loader.routine_status_stamp_spec(status),
            )
            widget = gui_main_table_loader.create_routine_instance_status_widget(
                status,
                registered=4,
                excluded=1,
                operation_or_stopped=3,
                review=0,
                enabled=True,
            )
            stamp = widget.findChild(QWidget, "routineInstanceStatusStamp")
            dot = widget.findChild(QLabel, "routineInstanceStatusDot")
            status_text = widget.findChild(QLabel, "routineInstanceStatusText")
            registered = widget.findChild(QWidget, "routineInstanceRegistered")
            excluded = widget.findChild(QWidget, "routineInstanceExcluded")
            operation = widget.findChild(QWidget, "routineInstanceOperationOrStopped")
            review = widget.findChild(QWidget, "routineInstanceReview")
            self.assertIsNone(dot)
            self.assertEqual(status, status_text.text())
            self.assertEqual(
                gui_main_table_loader.ROUTINE_STATUS_STAMP_WIDTH,
                stamp.width(),
            )
            self.assertEqual(
                gui_main_table_loader.ROUTINE_STATUS_STAMP_HEIGHT,
                stamp.height(),
            )
            self.assertIn(f"border: 1px solid {color}", stamp.styleSheet())
            self.assertIn(f"color: {color}", status_text.styleSheet())
            def aggregate_metric_text(metric_widget):
                object_name = metric_widget.objectName()
                return "".join(
                    metric_widget.findChild(QLabel, f"{object_name}{suffix}").text()
                    for suffix in ("Label", "OpenParen", "Number", "CloseParen")
                )

            self.assertEqual("\uB4F1\uB85D(4)", aggregate_metric_text(registered))
            self.assertEqual("\uC81C\uC678(1)", aggregate_metric_text(excluded))
            expected_label = "\uC6B4\uC601" if status == gui_main_table_loader.ROUTINE_STATUS_RUNNING else "\uC815\uC9C0"
            self.assertEqual(f"{expected_label}(3)", aggregate_metric_text(operation))
            self.assertEqual("\uAC80\uD1A0(0)", aggregate_metric_text(review))
            for metric_widget in (registered, excluded, operation, review):
                number_label = metric_widget.findChild(
                    QLabel,
                    f"{metric_widget.objectName()}Number",
                )
                self.assertEqual(
                    int(Qt.AlignCenter),
                    int(number_label.alignment()),
                )
            self.assertEqual(
                gui_main_table_loader.routine_instance_grid_columns(widget.font())[
                    "registered"
                ],
                registered.width(),
            )
            separators = widget.findChildren(QLabel, "routineInstanceSeparator")
            self.assertEqual(5, len(separators))
            self.assertTrue(all(separator.text() == "|" for separator in separators))
        self.assertEqual(("", ""), gui_main_table_loader.routine_status_stamp_spec("UNKNOWN"))

    def test_routine_instance_grid_columns_keep_shared_x_axis(self) -> None:
        first = gui_main_table_loader.create_routine_instance_status_widget(
            "?? 지",
            registered=0,
            excluded=0,
            operation_or_stopped=0,
            review=0,
            buy_limit_text="?�도(미설??",
            consumed_text="?�모(0 / -)",
            profit_text="?�익(0 / 0.00%)",
            enabled=True,
        )
        second = gui_main_table_loader.create_routine_instance_status_widget(
            "?? ??",
            registered=125,
            excluded=2,
            operation_or_stopped=121,
            review=2,
            buy_limit_text="?�도(100,000,000)",
            consumed_text="?�모(98,765,432 / 98.8%)",
            profit_text="?�익(-1,250,000 / -12.50%)",
            profit_color="#2563EB",
            buy_limit_configured=True,
            enabled=True,
        )
        first.show()
        second.show()
        self.app.processEvents()
        try:
            for object_name in (
                "routineInstanceRegistered",
                "routineInstanceExcluded",
                "routineInstanceOperationOrStopped",
                "routineInstanceReview",
            ):
                self.assertEqual(
                    first.findChild(QWidget, object_name).x(),
                    second.findChild(QWidget, object_name).x(),
                )
            for object_name in ("routineInstanceBuyLimit", "routineInstanceProfit"):
                self.assertEqual(
                    first.findChild(QWidget, object_name).x(),
                    second.findChild(QWidget, object_name).x(),
                )
            self.assertIsNone(first.findChild(QWidget, "routineInstanceConsumed"))
            self.assertIsNotNone(second.findChild(QWidget, "routineInstanceConsumed"))
            first_separators = first.findChildren(QLabel, "routineInstanceSeparator")
            second_separators = second.findChildren(QLabel, "routineInstanceSeparator")
            self.assertEqual(5, len(first_separators))
            self.assertEqual(6, len(second_separators))
            for first_separator, second_separator in zip(first_separators, second_separators):
                self.assertEqual(first_separator.x(), second_separator.x())
            self.assertEqual(
                gui_main_table_loader.routine_aggregate_slot_lefts(
                    gui_main_table_loader.ROUTINE_INSTANCE_NAME_WIDTH,
                    first.font(),
                ),
                gui_main_table_loader.routine_aggregate_slot_lefts(
                    gui_main_table_loader.ROUTINE_INSTANCE_NAME_WIDTH,
                    second.font(),
                ),
            )
            column_widths = gui_main_table_loader.routine_instance_grid_columns(
                second.font()
            )
            for key, object_name in (
                ("registered", "routineInstanceRegistered"),
                ("excluded", "routineInstanceExcluded"),
                ("operation_or_stopped", "routineInstanceOperationOrStopped"),
                ("review", "routineInstanceReview"),
            ):
                label = second.findChild(QWidget, object_name)
                sample = gui_main_table_loader.ROUTINE_INSTANCE_GRID_COLUMN_SAMPLES[key]
                self.assertEqual(column_widths[key], label.width())
                self.assertGreaterEqual(
                    label.width(),
                    label.fontMetrics().horizontalAdvance(sample)
                    + gui_main_table_loader.routine_instance_grid_padding(key),
                )
            for key, object_name in (
                ("limit", "routineInstanceBuyLimit"),
                ("consumed", "routineInstanceConsumed"),
                ("profit", "routineInstanceProfit"),
            ):
                label = second.findChild(QWidget, object_name)
                self.assertEqual(column_widths[key], label.width())
            number_widths = gui_main_table_loader.routine_instance_number_widths(
                second.font()
            )
            for key, object_name in (
                ("limit_amount", "routineInstanceBuyLimitAmount"),
                ("consumed_amount", "routineInstanceConsumedAmount"),
                ("consumed_rate", "routineInstanceConsumedRate"),
                ("profit_amount", "routineInstanceProfitAmount"),
                ("profit_rate", "routineInstanceProfitRate"),
            ):
                label = second.findChild(QLabel, object_name)
                self.assertEqual(number_widths[key], label.width())
                self.assertEqual(Qt.AlignRight | Qt.AlignVCenter, label.alignment())
            self.assertEqual(
                {gui_main_table_loader.routine_aggregate_separator_width(first.font())},
                {separator.width() for separator in first_separators},
            )
            self.assertEqual(
                gui_main_table_loader.routine_instance_separator_width(first.font())
                + (gui_main_table_loader.ROUTINE_INSTANCE_SEPARATOR_PADDING * 2),
                gui_main_table_loader.routine_aggregate_separator_width(first.font()),
            )
            slot_lefts = gui_main_table_loader.routine_aggregate_slot_lefts(
                gui_main_table_loader.ROUTINE_INSTANCE_NAME_WIDTH,
                first.font(),
            )
            aggregate_separator_width = (
                gui_main_table_loader.routine_aggregate_separator_width(first.font())
            )
            for index, column_key in enumerate(
                gui_main_table_loader.ROUTINE_AGGREGATE_COLUMN_KEYS[:-1]
            ):
                self.assertEqual(
                    column_widths[column_key] + aggregate_separator_width,
                    slot_lefts[index + 1] - slot_lefts[index],
                )
        finally:
            first.close()
            second.close()

    def test_parent_and_instance_aggregate_slots_share_gap_contract(self) -> None:
        widgets = [
            gui_main_table_loader.create_routine_instance_status_widget(
                gui_main_table_loader.ROUTINE_STATUS_STOPPED,
                registered=value,
                excluded=value,
                operation_or_stopped=value,
                review=value,
                enabled=True,
            )
            for value in (0, 1, 9, 10, 15, 99, 100, 123, 199, 999)
        ]
        for widget in widgets:
            widget.show()
        self.app.processEvents()
        try:
            object_names = (
                "routineInstanceRegistered",
                "routineInstanceExcluded",
                "routineInstanceOperationOrStopped",
                "routineInstanceReview",
            )
            reference_x = tuple(
                widgets[0].findChild(QWidget, name).x() for name in object_names
            )
            status_stamp = widgets[0].findChild(
                QWidget,
                "routineInstanceStatusStamp",
            )
            self.assertEqual(
                gui_main_table_loader.ROUTINE_AGGREGATE_LEADING_GAP,
                reference_x[0] - (status_stamp.x() + status_stamp.width()),
            )
            for widget in widgets[1:]:
                self.assertEqual(
                    reference_x,
                    tuple(widget.findChild(QWidget, name).x() for name in object_names),
                )
            for widget in widgets:
                for object_name in object_names:
                    metric_widget = widget.findChild(QWidget, object_name)
                    label = metric_widget.findChild(QLabel, f"{object_name}Label")
                    open_paren = metric_widget.findChild(
                        QLabel,
                        f"{object_name}OpenParen",
                    )
                    number_label = metric_widget.findChild(
                        QLabel,
                        f"{object_name}Number",
                    )
                    close_paren = metric_widget.findChild(
                        QLabel,
                        f"{object_name}CloseParen",
                    )
                    self.assertEqual(
                        gui_main_table_loader.routine_aggregate_number_slot_width(
                            widget.font()
                        ),
                        number_label.width(),
                    )
                    self.assertEqual(int(Qt.AlignCenter), int(number_label.alignment()))
                    self.assertEqual(label.x() + label.width(), open_paren.x())
                    self.assertEqual(
                        open_paren.x() + open_paren.width(),
                        number_label.x(),
                    )
                    self.assertEqual(
                        number_label.x() + number_label.width(),
                        close_paren.x(),
                    )

            separator_width = gui_main_table_loader.routine_aggregate_separator_width(
                widgets[0].font()
            )
            self.assertEqual(
                gui_main_table_loader.routine_instance_separator_width(widgets[0].font())
                + (gui_main_table_loader.ROUTINE_INSTANCE_SEPARATOR_PADDING * 2),
                separator_width,
            )
            self.assertEqual(
                gui_main_table_loader.ROUTINE_INSTANCE_SEPARATOR_PADDING * 2,
                separator_width
                - QFontMetrics(widgets[0].font()).horizontalAdvance("|"),
            )
            for widget in widgets:
                self.assertEqual(
                    {separator_width},
                    {
                        separator.width()
                        for separator in widget.findChildren(
                            QLabel,
                            "routineInstanceSeparator",
                        )
                    },
                )
            reference_separator_x = tuple(
                separator.x()
                for separator in widgets[0].findChildren(
                    QLabel,
                    "routineInstanceSeparator",
                )
            )
            for widget in widgets[1:]:
                self.assertEqual(
                    reference_separator_x,
                    tuple(
                        separator.x()
                        for separator in widget.findChildren(
                            QLabel,
                            "routineInstanceSeparator",
                        )
                    ),
                )

            parent_slot_lefts = [
                gui_main_table_loader.routine_aggregate_slot_lefts(
                    gui_main_table_loader.ROUTINE_INSTANCE_NAME_WIDTH,
                    widgets[0].font(),
                )
                for _value in (0, 1, 9, 10, 15, 99, 100, 123, 199, 999)
            ]
            self.assertTrue(
                all(lefts == parent_slot_lefts[0] for lefts in parent_slot_lefts[1:])
            )
            self.assertEqual(
                gui_main_table_loader.ROUTINE_AGGREGATE_LEADING_GAP,
                parent_slot_lefts[0][0]
                - gui_main_table_loader.ROUTINE_INSTANCE_NAME_WIDTH,
            )
            column_widths = gui_main_table_loader.routine_instance_grid_columns(
                widgets[0].font()
            )
            for index, column_key in enumerate(
                gui_main_table_loader.ROUTINE_AGGREGATE_COLUMN_KEYS[:-1]
            ):
                self.assertEqual(
                    column_widths[column_key] + separator_width,
                    parent_slot_lefts[0][index + 1] - parent_slot_lefts[0][index],
                )
        finally:
            for widget in widgets:
                widget.close()

    def test_child_name_keeps_arrow_space_without_stock_rows(self) -> None:
        table = QTableWidget(2, 1)
        table.setColumnWidth(0, 300)
        for row, has_stocks in enumerate((True, False)):
            item = QTableWidgetItem("인스턴스")
            item.setData(gui_main_table_loader.ROUTINE_CHILD_HAS_STOCKS_ROLE, has_stocks)
            table.setItem(row, 0, item)
        table.resize(320, 120)
        table.show()
        self.app.processEvents()
        try:
            controller = gui_windows._RoutineTreeInteractionController(
                SimpleNamespace(routine_table=table)
            )
            with_stocks = controller._child_name_rect(table.model().index(0, 0))
            without_stocks = controller._child_name_rect(table.model().index(1, 0))
            self.assertEqual(with_stocks.left(), without_stocks.left())
            self.assertFalse(
                controller._child_expand_rect(table.model().index(0, 0)).isNull()
            )
            self.assertTrue(
                controller._child_expand_rect(table.model().index(1, 0)).isNull()
            )
        finally:
            table.close()

    def test_child_arrow_is_visible_but_disabled_without_stock_rows(self) -> None:
        delegate = gui_windows._RoutineTreeItemDelegate()

        self.assertEqual(delegate._child_arrow_state(True, True), ("▶", True))
        self.assertEqual(delegate._child_arrow_state(True, False), ("▼", True))
        self.assertEqual(delegate._child_arrow_state(False, True), ("▶", False))
        self.assertEqual(delegate._child_arrow_state(False, False), ("▶", False))

    def test_main_window_routine_headers_match_monitoring_contract(self) -> None:
        self.assertEqual(
            list(gui_main_table_loader.ROUTINE_MONITORING_HEADERS),
            [
                "\uB8E8\uD2F4\uBA85",
                "\uC0C1\uD0DC",
                "\uB4F1\uB85D",
                "\uC81C\uC678",
                "\uC6B4\uC601/\uC815\uC9C0",
                "\uAC80\uD1A0\uAD00\uB9AC",
                "\uC0AC\uC6A9\uAE08\uC561",
                "\uB9E4\uC218\uD55C\uB3C4",
                "\uC0AC\uC6A9\uB960",
                "\uC218\uC775\uB960",
            ],
        )

    def test_stock_position_metric_values_return_structured_slots(self) -> None:
        holding, price, profit, pending, profit_amount, profit_rate = stock_position_metric_values(
            holding_qty=120,
            avg_price=28750,
            current_price=29100,
            buy_pending_qty=10,
            sell_pending_qty=0,
        )

        self.assertIsInstance(holding, RatioMetricDisplay)
        self.assertTrue(str(holding.value1).startswith("120"))
        self.assertEqual("3,450,000", holding.value2)
        self.assertEqual(("28,750", "29,100"), (price.value1, price.value2))
        self.assertEqual(("+42,000", "+1.22%"), (profit.value1, profit.value2))
        self.assertEqual(("10", "0"), (pending.value1, pending.value2))
        self.assertEqual(42000, int(round(profit_amount)))
        self.assertAlmostEqual(1.217391, profit_rate, places=5)

    def test_stock_position_display_values_omit_inner_labels(self) -> None:
        holding, price, profit, pending, *_ = stock_position_display_values(
            holding_qty=0,
            avg_price=0,
            current_price=None,
            buy_pending_qty=0,
            sell_pending_qty=0,
        )
        self.assertEqual("0\uC8FC / 0", holding)
        self.assertEqual("0 / 0", price)
        self.assertEqual("0 / 0.00%", profit)
        self.assertEqual("0 / 0", pending)

        separated_holding, separated_price, separated_profit, separated_pending, *_ = stock_position_display_values(
            holding_qty=120,
            avg_price=28750,
            current_price=29100,
            buy_pending_qty=10,
            sell_pending_qty=0,
            include_separator=True,
        )
        self.assertEqual("| 120\uC8FC / 3,450,000", separated_holding)
        self.assertEqual("| 28,750 / 29,100", separated_price)
        self.assertEqual("| +42,000 / +1.22%", separated_profit)
        self.assertEqual("| 10 / 0", separated_pending)

    def test_empty_and_negative_price_slots_normalize_to_right_aligned_zero(self) -> None:
        _, empty_price, *_ = stock_position_metric_values(
            holding_qty=0,
            avg_price=0,
            current_price=None,
        )
        self.assertEqual("0", empty_price.value1)
        self.assertEqual("0", empty_price.value2)
        self.assertEqual(Qt.AlignRight | Qt.AlignVCenter, empty_price.value1_alignment)
        self.assertEqual(Qt.AlignRight | Qt.AlignVCenter, empty_price.value2_alignment)
        self.assertEqual("9,999,999", empty_price.value1_sample)
        self.assertEqual("9,999,999", empty_price.value2_sample)

        _, mixed_price, *_ = stock_position_metric_values(
            holding_qty=1,
            avg_price=1234,
            current_price=None,
        )
        self.assertEqual("1,234", mixed_price.value1)
        self.assertEqual("0", mixed_price.value2)
        self.assertEqual(Qt.AlignRight | Qt.AlignVCenter, mixed_price.value1_alignment)
        self.assertEqual(Qt.AlignRight | Qt.AlignVCenter, mixed_price.value2_alignment)

        _, right_only_price, *_ = stock_position_metric_values(
            holding_qty=0,
            avg_price=0,
            current_price=5678,
        )
        self.assertEqual("0", right_only_price.value1)
        self.assertEqual("5,678", right_only_price.value2)
        self.assertEqual(Qt.AlignRight | Qt.AlignVCenter, right_only_price.value1_alignment)
        self.assertEqual(Qt.AlignRight | Qt.AlignVCenter, right_only_price.value2_alignment)

        _, negative_price, *_ = stock_position_metric_values(
            holding_qty=1,
            avg_price=-1_234,
            current_price=-5_678,
        )
        self.assertEqual(("0", "0"), (negative_price.value1, negative_price.value2))

    def test_price_metric_keeps_fixed_slots_for_empty_and_max_values(self) -> None:
        metrics = QFontMetrics(QFont())
        _, empty_price, *_ = stock_position_metric_values(
            holding_qty=0,
            avg_price=0,
            current_price=None,
        )
        _, max_price, *_ = stock_position_metric_values(
            holding_qty=1,
            avg_price=9_999_999,
            current_price=9_999_999,
        )
        _, mixed_price, *_ = stock_position_metric_values(
            holding_qty=1,
            avg_price=65_500,
            current_price=1_234_567,
        )

        empty_layout = ratio_metric_layout(metrics, empty_price, outer_padding=2)
        max_layout = ratio_metric_layout(metrics, max_price, outer_padding=2)
        mixed_layout = ratio_metric_layout(metrics, mixed_price, outer_padding=2)

        self.assertEqual(max_layout.value1_width, empty_layout.value1_width)
        self.assertEqual(max_layout.value2_width, empty_layout.value2_width)
        self.assertEqual(max_layout.slash_width, empty_layout.slash_width)
        self.assertEqual(max_layout.close_width, empty_layout.close_width)
        self.assertEqual(max_layout.total_width, empty_layout.total_width)
        self.assertEqual(max_layout.total_width, mixed_layout.total_width)
        price_column_width = gui_main_table_loader.routine_stock_column_widths(QFont())[7]
        self.assertGreaterEqual(price_column_width, max_layout.total_width + 6)

    def test_price_metric_draws_fixed_value_slot_rects(self) -> None:
        _, price_metric, *_ = stock_position_metric_values(
            holding_qty=0,
            avg_price=0,
            current_price=None,
        )
        painter = MagicMock()
        painter.fontMetrics.return_value = QFontMetrics(QFont())

        draw_stock_position_metric_display(
            painter,
            QRect(0, 0, 242, 24),
            price_metric,
            outer_padding=2,
        )

        draw_calls = painter.drawText.call_args_list
        self.assertEqual("", draw_calls[0].args[-1])
        self.assertEqual("0", draw_calls[1].args[-1])
        self.assertEqual(Qt.AlignRight | Qt.AlignVCenter, draw_calls[1].args[-2])
        self.assertEqual(" / ", draw_calls[2].args[-1])
        self.assertEqual("0", draw_calls[3].args[-1])
        self.assertEqual(Qt.AlignRight | Qt.AlignVCenter, draw_calls[3].args[-2])

        layout = ratio_metric_layout(QFontMetrics(QFont()), price_metric, outer_padding=2)
        self.assertEqual(layout.value1_width, draw_calls[1].args[2])
        self.assertEqual(layout.slash_width, draw_calls[2].args[2])
        self.assertEqual(layout.value2_width, draw_calls[3].args[2])
        self.assertEqual(layout.value1_width, layout.value2_width)
        self.assertGreaterEqual(
            layout.value1_width,
            QFontMetrics(QFont()).horizontalAdvance("9,999,999"),
        )

    def test_main_stock_metric_display_can_keep_labels(self) -> None:
        holding_metric, *_ = stock_position_metric_values(
            holding_qty=120,
            avg_price=28750,
            current_price=29100,
        )
        painter = MagicMock()
        painter.fontMetrics.return_value = QFontMetrics(QFont())

        draw_stock_position_metric_display(
            painter,
            QRect(0, 0, 260, 24),
            holding_metric,
            outer_padding=2,
            show_label=True,
        )

        drawn_text = [call.args[-1] for call in painter.drawText.call_args_list]
        self.assertIn("\ubcf4\uc720(", drawn_text)
        self.assertIn("120\uc8fc", drawn_text)
        self.assertIn(" / ", drawn_text)
        self.assertIn("3,450,000", drawn_text)
        self.assertIn(")", drawn_text)

    def test_main_stock_metric_sequence_uses_fixed_separator_gap(self) -> None:
        painter = MagicMock()
        painter.fontMetrics.return_value = QFontMetrics(QFont())
        texts = [
            "보유(99999�?/ 999,999,999)",
            "가�?9,999,999 / 9,999,999)",
            "?�익(-99,999,999 / -00.00%)",
            "매매(99 / 99)",
            "?�도(999,999,999)",
            "?�모(999,999,999 / 00.0%)",
        ]

        texts = list(gui_windows.MAIN_STOCK_METRIC_MAX_TEXTS)

        rows, _end_x = gui_windows._draw_routine_stock_metric_text_sequence(
            painter,
            row_rect=QRect(0, 0, 1200, 24),
            start_x=100,
            texts=texts,
        )

        for _text, _text_start, text_end, separator_start, next_text_start in rows[:-1]:
            self.assertEqual(
                gui_windows.ROUTINE_STOCK_METRIC_SEPARATOR_GAP,
                separator_start - text_end,
            )
            self.assertEqual(
                gui_windows.ROUTINE_STOCK_METRIC_SEPARATOR_GAP,
                next_text_start
                - (
                    separator_start
                    + painter.fontMetrics().horizontalAdvance("|")
                ),
            )

        drawn_texts = [call.args[-1] for call in painter.drawText.call_args_list]
        for text in texts:
            self.assertNotIn(text, drawn_texts)
        self.assertEqual(len(texts) - 1, drawn_texts.count("|"))
        self.assertEqual(
            gui_windows._main_stock_metric_slot_widths(
                painter.fontMetrics()
            )[: len(texts)],
            tuple(row[2] - row[1] for row in rows),
        )

    def test_main_stock_metric_sequence_uses_max_text_slots(self) -> None:
        painter = MagicMock()
        painter.fontMetrics.return_value = QFontMetrics(QFont())
        actual_texts = [
            "\ubcf4\uc720(0\uc8fc / 0)",
            "\uac00\uaca9(0 / 0)",
            "\uc218\uc775(0 / 0.00%)",
            "\ub9e4\ub9e4(0 / 0)",
            "\ud55c\ub3c4(\ubbf8\uc124\uc815)",
            "\uc18c\ubaa8(0 / 0.0%)",
        ]

        max_rows, _ = gui_windows._draw_routine_stock_metric_text_sequence(
            painter,
            row_rect=QRect(0, 0, 1600, 24),
            start_x=100,
            texts=list(gui_windows.MAIN_STOCK_METRIC_MAX_TEXTS),
        )
        painter.reset_mock()
        painter.fontMetrics.return_value = QFontMetrics(QFont())
        actual_rows, _ = gui_windows._draw_routine_stock_metric_text_sequence(
            painter,
            row_rect=QRect(0, 0, 1600, 24),
            start_x=100,
            texts=actual_texts,
        )

        self.assertEqual(
            [row[1] for row in max_rows],
            [row[1] for row in actual_rows],
        )
        self.assertEqual(
            [row[3] for row in max_rows[:-1]],
            [row[3] for row in actual_rows[:-1]],
        )

    def test_main_stock_metric_layout_rects_are_text_independent(self) -> None:
        row_rect = QRect(0, 5, 1600, 24)
        metrics = QFontMetrics(QFont())
        preview_metric_rects, preview_separator_rects, preview_end_x = (
            gui_windows._routine_stock_metric_layout_rects(
                row_rect=row_rect,
                start_x=100,
                count=len(gui_windows.MAIN_STOCK_METRIC_MAX_TEXTS),
                metrics=metrics,
            )
        )
        actual_metric_rects, actual_separator_rects, actual_end_x = (
            gui_windows._routine_stock_metric_layout_rects(
                row_rect=row_rect,
                start_x=100,
                count=6,
                metrics=metrics,
            )
        )

        self.assertEqual(preview_metric_rects, actual_metric_rects)
        self.assertEqual(preview_separator_rects, actual_separator_rects)
        self.assertEqual(preview_end_x, actual_end_x)
        self.assertEqual(
            list(gui_windows._main_stock_metric_slot_widths(metrics)),
            [rect.width() for rect in actual_metric_rects],
        )
        self.assertEqual(
            [
                metrics.horizontalAdvance(text)
                for text in gui_windows.MAIN_STOCK_METRIC_MAX_TEXTS
            ],
            [rect.width() for rect in actual_metric_rects],
        )
        self.assertEqual(
            [metrics.horizontalAdvance("|")] * 5,
            [rect.width() for rect in actual_separator_rects],
        )

    def test_main_stock_metric_component_rects_are_text_independent(self) -> None:
        row_rect = QRect(0, 5, 1600, 24)
        metrics = QFontMetrics(QFont())
        metric_rects, _separator_rects, _end_x = gui_windows._routine_stock_metric_layout_rects(
            row_rect=row_rect,
            start_x=100,
            count=6,
            metrics=metrics,
        )
        preview_components = gui_windows._main_stock_metric_component_layouts(
            metrics,
            metric_rects,
        )
        actual_components = gui_windows._main_stock_metric_component_layouts(
            metrics,
            metric_rects,
        )

        self.assertEqual(preview_components, actual_components)
        self.assertIn("label", actual_components[0])
        self.assertIn("open_paren", actual_components[0])
        self.assertIn("left_value", actual_components[0])
        self.assertIn("slash", actual_components[0])
        self.assertIn("right_value", actual_components[0])
        self.assertIn("close_paren", actual_components[0])
        self.assertNotIn("slash", actual_components[4])

    def test_main_stock_limit_hit_rect_uses_display_layout_rect(self) -> None:
        class FakeIndex:
            def data(self, role):
                if role == gui_main_table_loader.ROUTINE_STOCK_VALUES_ROLE:
                    return [""] * 13
                return None

        class FakeTable:
            def visualRect(self, _index):
                return QRect(0, 7, 2400, 24)

            def font(self):
                return QFont()

        table = FakeTable()
        index = FakeIndex()
        controller = gui_windows._RoutineTreeInteractionController.__new__(
            gui_windows._RoutineTreeInteractionController
        )
        controller.table = table
        legacy_holding_rect = controller._stock_legacy_metric_rect(index, 7)
        expected_metric_rects, _separator_rects, _end_x = (
            gui_windows._routine_stock_metric_layout_rects(
                row_rect=table.visualRect(index),
                start_x=legacy_holding_rect.left()
                + gui_windows.ROUTINE_STOCK_METRIC_SEPARATOR_GAP,
                count=5,
            )
        )

        self.assertEqual(expected_metric_rects[4], controller._stock_metric_rect(index, 11))

    def test_initial_buy_badge_has_no_leading_separator_slot(self) -> None:
        class FakeIndex:
            def data(self, role):
                if role == gui_main_table_loader.ROUTINE_STOCK_VALUES_ROLE:
                    return [""] * 13
                return None

        class FakeTable:
            def visualRect(self, _index):
                return QRect(0, 0, 2600, 24)

            def font(self):
                return QFont()

        controller = gui_windows._RoutineTreeInteractionController.__new__(
            gui_windows._RoutineTreeInteractionController
        )
        controller.table = FakeTable()
        index = FakeIndex()
        name_rect = controller._stock_legacy_metric_rect(index, 0)
        initial_buy_rect = controller._stock_legacy_metric_rect(index, 1)

        self.assertEqual(name_rect.right() + 1, initial_buy_rect.left())

    def test_stock_buy_limit_editor_rect_uses_limit_value_display_slot(self) -> None:
        class FakeIndex:
            def isValid(self):
                return True

            def data(self, role):
                if role == gui_main_table_loader.ROUTINE_STOCK_VALUES_ROLE:
                    return [""] * 13
                return None

        class FakeModel:
            def __init__(self, index):
                self._index = index

            def index(self, _row, _column):
                return self._index

        class FakeTable:
            def __init__(self, index):
                self._index = index

            def model(self):
                return FakeModel(self._index)

            def visualRect(self, _index):
                return QRect(0, 7, 2400, 24)

            def font(self):
                return QFont()

        index = FakeIndex()
        table = FakeTable(index)
        controller = gui_windows._RoutineTreeInteractionController.__new__(
            gui_windows._RoutineTreeInteractionController
        )
        controller.table = table
        window = gui_windows.MainWindow.__new__(gui_windows.MainWindow)
        window.routine_table = table
        window._routine_tree_interaction_controller = controller

        limit_rect = controller._stock_metric_rect(index, 11)
        component_rects = gui_windows._main_stock_metric_component_rects(
            QFontMetrics(table.font()),
            limit_rect,
            gui_windows.MAIN_STOCK_METRIC_LAYOUT["metrics"][4],
        )
        value_rect = component_rects["left_value"]

        self.assertEqual(
            QRect(
                value_rect.left(),
                value_rect.top() + 2,
                value_rect.width(),
                max(20, limit_rect.height() - 4),
            ),
            window._routine_stock_buy_limit_value_rect(0),
        )

    def test_main_stock_limit_edit_hides_only_limit_value_slot(self) -> None:
        painter = MagicMock()
        painter.fontMetrics.return_value = QFontMetrics(QFont())
        texts = [
            "\ubcf4\uc720(0\uc8fc / 0)",
            "\uac00\uaca9(0 / 0)",
            "\uc190\uc775(0 / 0.00%)",
            "\ubbf8\uccb4\uacb0(0 / 0)",
            "\ud55c\ub3c4(\ubbf8\uc124\uc815)",
            "\uc18c\ubaa8(0 / 0.0%)",
        ]

        gui_windows._draw_routine_stock_metric_text_sequence(
            painter,
            row_rect=QRect(0, 0, 1600, 24),
            start_x=100,
            texts=texts,
            hidden_value_indexes={4},
        )

        drawn_texts = [call_args.args[-1] for call_args in painter.drawText.call_args_list]
        self.assertIn("\ud55c\ub3c4", drawn_texts)
        self.assertIn("(", drawn_texts)
        self.assertIn(")", drawn_texts)
        self.assertNotIn("\ubbf8\uc124\uc815", drawn_texts)
        self.assertIn("\uc18c\ubaa8", drawn_texts)
        self.assertIn("0.0%", drawn_texts)

    def test_main_stock_metric_texts_omits_consumed_when_limit_unconfigured(self) -> None:
        holding_metric, price_metric, profit_metric, pending_metric, *_ = (
            stock_position_metric_values(
                holding_qty=0,
                avg_price=None,
                current_price=None,
            )
        )
        values = [
            "003550 LG",
            "09:30~13:30",
            "",
            "\uac10\uc2dc/\ub300\uae30",
            "\ub8e8\ud2f4",
            "10\ubd84/\uc2dc\uc7a5\uac00",
            "\ubcf4\uc720(0\uc8fc / 0)",
            "\uac00\uaca9(0 / 0)",
            "\uc190\uc775(0 / 0.00%)",
            "\ubbf8\uccb4\uacb0(0 / 0)",
            "\ud55c\ub3c4(\ubbf8\uc124\uc815)",
        ]

        texts = gui_windows._routine_stock_metric_texts(
            values,
            (holding_metric, price_metric, profit_metric, pending_metric, None),
        )

        self.assertEqual("\ud55c\ub3c4(\ubbf8\uc124\uc815)", texts[-1])
        self.assertNotIn("\uc18c\ubaa8(0 / 0.0%)", texts)
        self.assertEqual(5, len(texts))

    def test_main_stock_metric_texts_includes_consumed_when_limit_configured(self) -> None:
        row = gui_main_table_loader._routine_tree_stock_row(
            SimpleNamespace(),
            definition_id="indicator_follow",
            instance_id="instance-a",
            stock={
                "code": "003550",
                "name": "LG",
                "enabled": True,
                "stock_path": "",
                "state": {
                    "holding_qty": 1,
                    "avg_price": 1_223_344,
                },
                "config": {
                    "buy_limit_enabled": True,
                    "buy_limit_amount": 1_223_344,
                },
            },
        )

        texts = gui_windows._routine_stock_metric_texts(
            list(row["stock_values"]),
            tuple(row["stock_metrics"]),
        )

        self.assertEqual("\ud55c\ub3c4(1,223,344)", texts[-2])
        self.assertTrue(texts[-1].startswith("\uc18c\ubaa8("))
        self.assertEqual(6, len(texts))

    def test_routine_stock_row_stores_structured_metric_role(self) -> None:
        row = gui_main_table_loader._routine_tree_stock_row(
            SimpleNamespace(),
            definition_id="indicator_follow",
            instance_id="instance-a",
            stock={
                "code": "003550",
                "name": "LG",
                "enabled": True,
                "stock_path": "",
                "state": {
                    "holding_qty": 120,
                    "avg_price": 28750,
                    "current_price": 29100,
                    "pending_buy_qty": 10,
                    "pending_sell_qty": 0,
                },
                "config": {},
            },
        )

        metrics = row["stock_metrics"]
        self.assertEqual(5, len(metrics))
        self.assertIsNone(metrics[4])
        self.assertTrue(str(metrics[0].value1).startswith("120"))
        self.assertEqual("3,450,000", metrics[0].value2)
        self.assertEqual("29,100", metrics[1].value2)
        self.assertEqual("gray", row["stock_profit_led"])
        self.assertTrue(row["stock_values"][1])
        self.assertTrue(row["stock_values"][11])
        self.assertEqual(12, len(row["stock_values"]))

    def test_routine_stock_row_price_uses_existing_average_price_aliases(self) -> None:
        row = gui_main_table_loader._routine_tree_stock_row(
            SimpleNamespace(),
            definition_id="indicator_follow",
            instance_id="instance-a",
            stock={
                "code": "003550",
                "name": "LG",
                "enabled": True,
                "stock_path": "",
                "state": {
                    "holding_qty": 3,
                    "average_price": 65000,
                    "last_checked_price": 66100,
                },
                "config": {},
            },
        )

        price_metric = row["stock_metrics"][1]
        self.assertTrue(price_metric.label)
        self.assertEqual("65,000", price_metric.value1)
        self.assertEqual("66,100", price_metric.value2)

    def test_routine_stock_row_adds_limit_and_consumed_when_limit_configured(self) -> None:
        row = gui_main_table_loader._routine_tree_stock_row(
            SimpleNamespace(),
            definition_id="indicator_follow",
            instance_id="instance-a",
            stock={
                "code": "003550",
                "name": "LG",
                "enabled": True,
                "stock_path": "",
                "state": {
                    "holding_qty": 120,
                    "avg_price": 28750,
                    "current_price": 29100,
                },
                "config": {
                    "buy_limit_enabled": True,
                    "buy_limit_amount": 10_000_000,
                },
            },
        )

        self.assertEqual("\uD55C\uB3C4(10,000,000)", row["stock_values"][11])
        self.assertEqual("\uC18C\uBAA8(3,450,000 / 34.5%)", row["stock_values"][12])
        self.assertEqual("\uC18C\uBAA8", row["stock_metrics"][5].label)
        self.assertEqual(13, len(row["stock_values"]))

    def test_stock_buy_limit_config_writer_keeps_stock_limits_independent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            stock_a = root / "003550_LG" / "config.json"
            stock_b = root / "005930_Samsung" / "config.json"
            stock_c = root / "006400_SDI" / "config.json"
            stock_a.parent.mkdir()
            stock_b.parent.mkdir()
            stock_c.parent.mkdir()
            stock_a.write_text(json.dumps({"name": "LG"}, ensure_ascii=False), encoding="utf-8")
            stock_b.write_text(
                json.dumps({"name": "Samsung"}, ensure_ascii=False),
                encoding="utf-8",
            )
            stock_c.write_text(
                json.dumps(
                    {
                        "name": "?�성SDI",
                        "buy_limit_enabled": False,
                        "buy_limit_amount": None,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            gui_windows.MainWindow._write_stock_buy_limit_config(
                stock_a,
                enabled=True,
                amount=100_000,
            )
            gui_windows.MainWindow._write_stock_buy_limit_config(
                stock_b,
                enabled=True,
                amount=200_000,
            )

            config_a = json.loads(stock_a.read_text(encoding="utf-8"))
            config_b = json.loads(stock_b.read_text(encoding="utf-8"))
            config_c = json.loads(stock_c.read_text(encoding="utf-8"))
            self.assertEqual(100_000, config_a["buy_limit_amount"])
            self.assertEqual(200_000, config_b["buy_limit_amount"])
            self.assertFalse(config_c["buy_limit_enabled"])
            self.assertIsNone(config_c["buy_limit_amount"])

            gui_windows.MainWindow._write_stock_buy_limit_config(
                stock_a,
                enabled=False,
                amount=None,
            )
            config_a = json.loads(stock_a.read_text(encoding="utf-8"))
            config_b = json.loads(stock_b.read_text(encoding="utf-8"))
            config_c = json.loads(stock_c.read_text(encoding="utf-8"))
            self.assertFalse(config_a["buy_limit_enabled"])
            self.assertIsNone(config_a["buy_limit_amount"])
            self.assertEqual(200_000, config_b["buy_limit_amount"])
            self.assertFalse(config_c["buy_limit_enabled"])
            self.assertIsNone(config_c["buy_limit_amount"])

    def test_stock_initial_buy_writer_preserves_mode_specific_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "trade_amount_type": "QUANTITY",
                        "buy_qty": 20,
                        "buy_amount": 700_000,
                        "unrelated": "keep",
                    }
                ),
                encoding="utf-8",
            )

            gui_windows.MainWindow._write_stock_initial_buy_config(
                config_path,
                mode="AMOUNT",
                value=0,
            )
            amount_config = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual("AMOUNT", amount_config["trade_amount_type"])
            self.assertEqual(0, amount_config["buy_amount"])
            self.assertEqual(20, amount_config["buy_qty"])
            self.assertEqual("keep", amount_config["unrelated"])

            gui_windows.MainWindow._write_stock_initial_buy_config(
                config_path,
                mode="QUANTITY",
                value=1,
            )
            quantity_config = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual("QUANTITY", quantity_config["trade_amount_type"])
            self.assertEqual(1, quantity_config["buy_qty"])
            self.assertEqual(0, quantity_config["buy_amount"])

    def test_stock_initial_buy_badge_interaction_is_stock_scope_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "trade_amount_type": "AMOUNT",
                        "buy_amount": 700_000,
                        "buy_qty": 20,
                    }
                ),
                encoding="utf-8",
            )
            window = gui_windows.MainWindow.__new__(gui_windows.MainWindow)
            window._stock_config_path_for_routine_row = MagicMock(
                return_value=config_path
            )
            window.finish_routine_stock_initial_buy_edit = MagicMock()
            window.load_routine_table = MagicMock()

            for disabled_level in ("routine", "group"):
                window._main_routine_display_level = disabled_level
                window.toggle_routine_stock_initial_buy_mode(0)
                window.start_routine_stock_initial_buy_edit(0)

            unchanged = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual("AMOUNT", unchanged["trade_amount_type"])
            window._stock_config_path_for_routine_row.assert_not_called()
            window.finish_routine_stock_initial_buy_edit.assert_not_called()
            window.load_routine_table.assert_not_called()

            window._main_routine_display_level = "stock"
            window.toggle_routine_stock_initial_buy_mode(0)

            changed = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual("QUANTITY", changed["trade_amount_type"])
            self.assertEqual(1, changed["buy_qty"])
            window._stock_config_path_for_routine_row.assert_called_once_with(0)
            window.finish_routine_stock_initial_buy_edit.assert_called_once_with(
                save=True
            )
            window.load_routine_table.assert_called_once_with()

    def test_stock_buy_limit_editor_finish_writes_selected_stock_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            stock_a = root / "003550_LG" / "config.json"
            stock_b = root / "005930_Samsung" / "config.json"
            stock_a.parent.mkdir()
            stock_b.parent.mkdir()
            stock_a.write_text(json.dumps({"name": "LG"}, ensure_ascii=False), encoding="utf-8")
            (stock_a.parent / "state.json").write_text(
                json.dumps({"current_price": 1_000}),
                encoding="utf-8",
            )
            stock_b.write_text(
                json.dumps(
                    {
                        "name": "Samsung",
                        "buy_limit_enabled": True,
                        "buy_limit_amount": 200_000,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            editor = QLineEdit()
            editor.setText("100,000")
            window = gui_windows.MainWindow.__new__(gui_windows.MainWindow)
            window._routine_stock_buy_limit_editor = editor
            window._routine_stock_buy_limit_editor_config_path = str(stock_a)
            window._routine_stock_buy_limit_edit_finishing = False
            window.routine_table = SimpleNamespace(
                _editing_stock_buy_limit_path="003550_LG",
                viewport=lambda: SimpleNamespace(update=MagicMock()),
            )
            window.load_routine_table = MagicMock()

            window.finish_routine_stock_buy_limit_edit(save=True)

            config_a = json.loads(stock_a.read_text(encoding="utf-8"))
            config_b = json.loads(stock_b.read_text(encoding="utf-8"))
            self.assertTrue(config_a["buy_limit_enabled"])
            self.assertEqual(100_000, config_a["buy_limit_amount"])
            self.assertEqual(200_000, config_b["buy_limit_amount"])
            window.load_routine_table.assert_called_once_with()

    def test_draw_limit_metric_can_hide_only_value_slot_while_editing(self) -> None:
        painter = MagicMock()
        painter.fontMetrics.return_value = QFontMetrics(QFont())

        self.assertTrue(
            draw_limit_metric(
                painter,
                QRect(0, 0, 220, 24),
                "\uD55C\uB3C4(\uBBF8\uC124\uC815)",
                value_width=90,
                hide_value=True,
            )
        )

        drawn_texts = [call_args.args[-1] for call_args in painter.drawText.call_args_list]
        self.assertIn("\uD55C\uB3C4(", drawn_texts)
        self.assertIn(")", drawn_texts)
        self.assertNotIn("\uBBF8\uC124\uC815", drawn_texts)

    def test_stock_buy_limit_editor_cancel_clears_edit_state_without_saving(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            stock_config = Path(temp_dir) / "003550_LG" / "config.json"
            stock_config.parent.mkdir()
            stock_config.write_text(
                json.dumps(
                    {
                        "name": "LG",
                        "buy_limit_enabled": True,
                        "buy_limit_amount": 100_000,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            editor = QLineEdit()
            editor.setText("200,000")
            window = gui_windows.MainWindow.__new__(gui_windows.MainWindow)
            window._routine_stock_buy_limit_editor = editor
            window._routine_stock_buy_limit_editor_config_path = str(stock_config)
            window._routine_stock_buy_limit_edit_finishing = False
            window.routine_table = SimpleNamespace(
                _editing_stock_buy_limit_path="003550_LG",
                viewport=lambda: SimpleNamespace(update=MagicMock()),
            )
            window.load_routine_table = MagicMock()

            window.finish_routine_stock_buy_limit_edit(save=False)

            config = json.loads(stock_config.read_text(encoding="utf-8"))
            self.assertEqual(100_000, config["buy_limit_amount"])
            self.assertEqual("", window.routine_table._editing_stock_buy_limit_path)
            window.load_routine_table.assert_not_called()

    def test_unconfigured_buy_limit_value_is_center_aligned(self) -> None:
        widget = gui_main_table_loader.create_routine_instance_status_widget(
            "?? 지",
            registered=0,
            excluded=0,
            operation_or_stopped=0,
            review=0,
            buy_limit_text="\uD55C\uB3C4(\uBBF8\uC124\uC815)",
            profit_text="\uC218\uC775(0 / 0.00%)",
            enabled=True,
        )
        try:
            amount_label = widget.findChild(QLabel, "routineInstanceBuyLimitAmount")
            self.assertIsNotNone(amount_label)
            self.assertEqual(int(Qt.AlignCenter | Qt.AlignVCenter), int(amount_label.alignment()))
        finally:
            widget.close()

    def test_routine_buy_limit_always_shows_settings_without_inner_separator(self) -> None:
        for buy_limit_text in (
            "\uD55C\uB3C4(\uBBF8\uC124\uC815)",
            "\uD55C\uB3C4(\uB300\uAE30)",
            "\uD55C\uB3C4(12,000,000)",
        ):
            widget = gui_main_table_loader.create_routine_instance_status_widget(
                "\uC815\uC9C0",
                instance_id="instance-a",
                registered=0,
                excluded=0,
                operation_or_stopped=0,
                review=0,
                buy_limit_text=buy_limit_text,
                profit_text="\uC218\uC775(0 / 0.00%)",
                enabled=True,
            )
            try:
                limit_widget = widget.findChild(QWidget, "routineInstanceBuyLimit")
                amount_label = widget.findChild(
                    QLabel,
                    "routineInstanceBuyLimitAmount",
                )
                settings_label = widget.findChild(
                    QLabel,
                    "routineInstanceBuyLimitSettings",
                )
                self.assertIsNotNone(limit_widget)
                self.assertIsNotNone(amount_label)
                self.assertIsNotNone(settings_label)
                self.assertEqual(" [\uC124\uC815]", settings_label.text())
                self.assertEqual("instance-a", settings_label.property("routine_instance_id"))
                self.assertEqual([], limit_widget.findChildren(QLabel, "routineInstanceSeparator"))
                window = gui_windows.MainWindow.__new__(gui_windows.MainWindow)
                window.open_routine_instance_buy_limit_settings = MagicMock(
                    return_value=True
                )
                self.assertTrue(
                    window.handle_routine_instance_buy_limit_settings_click(
                        settings_label
                    )
                )
                window.open_routine_instance_buy_limit_settings.assert_called_once_with(
                    "instance-a"
                )
                if amount_label.text() in {"\uBBF8\uC124\uC815", "\uB300\uAE30"}:
                    self.assertEqual(
                        int(Qt.AlignCenter | Qt.AlignVCenter),
                        int(amount_label.alignment()),
                    )
            finally:
                widget.close()

    def test_routine_buy_limit_double_click_toggles_three_states(self) -> None:
        transitions = (
            (False, None, True, None),
            (True, None, False, None),
            (True, 12_000_000, False, None),
        )
        for enabled, amount, expected_enabled, expected_amount in transitions:
            label = QLabel()
            label.setProperty("routine_instance_id", "instance-a")
            window = gui_windows.MainWindow.__new__(gui_windows.MainWindow)
            window.finish_routine_stock_buy_limit_edit = MagicMock()
            window.finish_routine_instance_buy_limit_edit = MagicMock()
            window.refresh_all = MagicMock()
            result = SimpleNamespace(success=True, error="")
            repository = MagicMock()
            repository.update_buy_limit.return_value = result
            instance = SimpleNamespace(
                buy_limit_enabled=enabled,
                buy_limit_amount=amount,
            )

            with (
                patch.object(gui_windows, "routine_instance_by_id", return_value=instance),
                patch.object(
                    gui_windows,
                    "routine_instance_suggested_buy_limits",
                    return_value=(None, None),
                ),
                patch.object(gui_windows, "RoutineInstanceRepository", return_value=repository),
            ):
                window.handle_routine_instance_buy_limit_double_click(label)

            repository.update_buy_limit.assert_called_once_with(
                "instance-a",
                enabled=expected_enabled,
                amount=expected_amount,
            )
            window.refresh_all.assert_called_once_with()
            label.close()

    def test_blank_routine_buy_limit_edit_preserves_enabled_state(self) -> None:
        widget = gui_main_table_loader.create_routine_instance_status_widget(
            "\uC815\uC9C0",
            instance_id="instance-a",
            registered=0,
            excluded=0,
            operation_or_stopped=0,
            review=0,
            buy_limit_text="\uD55C\uB3C4(\uB300\uAE30)",
            profit_text="\uC218\uC775(0 / 0.00%)",
            enabled=True,
        )
        try:
            editor = widget.findChild(QLineEdit, "routineInstanceBuyLimitEditor")
            label = widget.findChild(QLabel, "routineInstanceBuyLimitAmount")
            editor.setText("")
            window = gui_windows.MainWindow.__new__(gui_windows.MainWindow)
            window._routine_instance_buy_limit_editor = editor
            window._routine_instance_buy_limit_editor_instance_id = "instance-a"
            window._routine_instance_buy_limit_editor_label = label
            window._routine_instance_buy_limit_edit_finishing = False
            window.refresh_all = MagicMock()

            with patch.object(gui_windows, "RoutineInstanceRepository") as repository:
                window.finish_routine_instance_buy_limit_edit(save=True)

            repository.assert_not_called()
            window.refresh_all.assert_not_called()
        finally:
            widget.close()

    def test_routine_table_keeps_counts_and_replaces_budget_columns(self) -> None:
        table = FakeRoutineTable()
        window = SimpleNamespace(
            routine_table=table,
            _main_routine_sort_column=-1,
            _main_routine_sort_order=0,
            _collapsed_routine_definition_ids=set(),
        )

        definition = RoutineDefinitionRecord(
            definition_id="indicator_follow",
            display_name="지?�추종매�?",
            package_dir=Path("routine-path"),
            schema_version="1.0",
            version="0.1.0",
            routine_type="auto_trade",
            entry_file="routine.py",
            module_name="indicator_follow_routine",
            settings_ui="indicator_follow",
            default_rules_file="rules.json",
            package_enabled=True,
            source_name="지?�추종매�?",
        )

        with (
            patch.object(gui_main_table_loader, "load_routine_definitions", return_value=[definition]),
            patch.object(gui_main_table_loader, "load_persisted_routine_instances", return_value=[]),
            patch.object(
                gui_main_table_loader,
                "_routine_stock_counts_from_base_stocks",
                return_value={"지?�추종매�?": 3},
            ),
            patch.object(
                gui_main_table_loader,
                "_instance_stock_counts",
                return_value={"instance": {"excluded": 2}},
            ),
            patch.object(
                gui_main_table_loader,
                "create_routine_profit_signal_widget",
                return_value="profit-widget",
            ),
        ):
            gui_main_table_loader.main_load_routine_table(window)

        self.assertEqual(table.row_count, 1)
        self.assertEqual(table.columnCount(), 10)
        row_texts = [table.item(0, col).text() for col in range(10)]
        self.assertTrue(row_texts[0])
        self.assertEqual([""] * 9, row_texts[1:])
        self.assertEqual((1, 10), table.spans[(0, 0)])
        self.assertIsNone(
            table.item(0, 0).data(gui_main_table_loader.ROUTINE_CHILD_STATUS_ROLE)
        )
        self.assertTrue(table.item(0, 0).data(gui_main_table_loader.ROUTINE_PARENT_AGGREGATE_ROLE))
        self.assertNotIn("budget", " ".join(table.item(0, col).text() for col in range(10)))
        self.assertIsNone(table.cellWidget(0, 9))





    def test_routine_table_reload_removes_stale_child_cell_widgets(self) -> None:
        table = FakeRoutineTable()
        window = SimpleNamespace(
            routine_table=table,
            _main_routine_sort_column=-1,
            _main_routine_sort_order=0,
            _collapsed_main_group_ids=set(),
            _collapsed_main_group_instance_ids=set(),
        )
        definition = RoutineDefinitionRecord(
            definition_id="indicator_follow",
            display_name="지?�추종매�?",
            package_dir=Path("routine-path"),
            schema_version="1.0",
            version="0.1.0",
            routine_type="auto_trade",
            entry_file="routine.py",
            module_name="indicator_follow_routine",
            settings_ui="indicator_follow",
            default_rules_file="rules.json",
            package_enabled=True,
            source_name="지?�추종매�?",
        )
        instance = RoutineInstanceRecord(
            instance_id="a52f539d-4f18-4ef6-b0cf-f471567982a1",
            definition_id="indicator_follow",
            display_name="?�?�주 추세??",
            source_routine_name="지?�추종매�?",
            persisted=True,
            source="PERSISTED",
            enabled=False,
            real_trade_allowed=False,
            description="",
            buy_limit_enabled=False,
            buy_limit_amount=None,
            rules_path=Path("instance-rules.json"),
        )
        group = _main_group("지표추종매매")
        group_id = str(group.path.resolve())
        assigned_stocks = [
            _assigned_stock(instance.instance_id, group_name=group.name)
        ]
        first_widget = FakeCellWidget()
        second_widget = FakeCellWidget()

        with (
            patch.object(gui_main_table_loader, "load_routine_definitions", return_value=[definition]),
            patch.object(gui_main_table_loader, "load_persisted_routine_instances", return_value=[instance]),
            patch.object(gui_main_table_loader, "get_group_records", return_value=[group]),
            patch.object(
                gui_main_table_loader,
                "_main_pnl_refresh_static_cache",
                return_value=_main_static_cache(
                    [definition], [instance], assigned_stocks
                ),
            ),
            patch.object(gui_main_table_loader, "_instance_stock_counts", return_value={}),
            patch.object(
                gui_main_table_loader,
                "create_routine_instance_status_widget",
                side_effect=[first_widget, second_widget],
            ),
        ):
            gui_main_table_loader.main_load_routine_table(window)
            self.assertEqual(2, table.row_count)
            self.assertIs(first_widget, table.cellWidget(1, 1))

            window._collapsed_main_group_ids = {group_id}
            gui_main_table_loader.main_load_routine_table(window)
            self.assertTrue(first_widget.deleted)
            self.assertEqual(1, table.row_count)
            self.assertIsNone(table.cellWidget(1, 1))
            self.assertIsNone(table.cellWidget(0, 1))

            window._collapsed_main_group_ids = set()
            gui_main_table_loader.main_load_routine_table(window)
            self.assertEqual(2, table.row_count)
            self.assertIs(second_widget, table.cellWidget(1, 1))

    def test_parent_and_registered_instance_rows_show_independent_buy_limits(self) -> None:
        table = FakeRoutineTable()
        window = SimpleNamespace(
            routine_table=table,
            _main_routine_sort_column=-1,
            _main_routine_sort_order=0,
            _collapsed_main_group_ids=set(),
            _collapsed_main_group_instance_ids=set(),
        )
        definition = RoutineDefinitionRecord(
            definition_id="indicator_follow",
            display_name="지?�추종매�?",
            package_dir=Path("routine-path"),
            schema_version="1.0",
            version="0.1.0",
            routine_type="auto_trade",
            entry_file="routine.py",
            module_name="indicator_follow_routine",
            settings_ui="indicator_follow",
            default_rules_file="rules.json",
            package_enabled=True,
            source_name="지?�추종매�?",
        )
        instance = RoutineInstanceRecord(
            instance_id="a52f539d-4f18-4ef6-b0cf-f471567982a1",
            definition_id="indicator_follow",
            display_name="?�?�주 추세??",
            source_routine_name="지?�추종매�?",
            persisted=True,
            source="PERSISTED",
            enabled=False,
            real_trade_allowed=False,
            description="?�?�주 중심??보수??추세 진입",
            buy_limit_enabled=True,
            buy_limit_amount=12_000_000,
            rules_path=Path("instance-rules.json"),
        )
        group = _main_group("지표추종매매")
        assigned_stocks = [
            _assigned_stock(instance.instance_id, group_name=group.name)
        ]

        with (
            patch.object(gui_main_table_loader, "load_routine_definitions", return_value=[definition]),
            patch.object(gui_main_table_loader, "load_persisted_routine_instances", return_value=[instance]),
            patch.object(gui_main_table_loader, "get_group_records", return_value=[group]),
            patch.object(
                gui_main_table_loader,
                "_main_pnl_refresh_static_cache",
                return_value=_main_static_cache(
                    [definition], [instance], assigned_stocks
                ),
            ),
            patch.object(
                gui_main_table_loader,
                "_instance_stock_counts",
                return_value={
                    instance.instance_id: {
                        "registered": 4,
                        "excluded": 1,
                        "operation_or_stopped": 2,
                        "operation_running": 1,
                        "review": 1,
                        "consumed_amount": 7_843_650,
                        "consumed_unknown": False,
                        "profit_amount": 35_200,
                        "profit_cost_basis": 1_248_227,
                        "profit_unknown": False,
                    }
                },
            ),
            patch.object(gui_main_table_loader, "create_routine_profit_signal_widget", return_value="profit-widget"),
        ):
            gui_main_table_loader.main_load_routine_table(window)

        self.assertEqual(2, table.row_count)
        self.assertEqual(
            {instance.instance_id: 4},
            window._routine_assigned_stock_count_by_instance,
        )
        self.assertEqual(28, table.row_heights[0])
        self.assertEqual(28, table.row_heights[1])
        self.assertTrue(table.item(0, 0).text())
        self.assertTrue(table.item(1, 0).text())
        self.assertFalse(table.item(0, 0).flags() & Qt.ItemIsUserCheckable)
        self.assertFalse(table.item(1, 0).flags() & Qt.ItemIsUserCheckable)
        self.assertIsNone(table.item(0, 0).data(Qt.CheckStateRole))
        self.assertIsNone(table.item(1, 0).data(Qt.CheckStateRole))
        self.assertEqual("", table.item(0, 1).text())
        self.assertEqual("", table.item(1, 1).text())
        self.assertEqual("", table.item(1, 2).text())
        self.assertEqual((1, 9), table.spans[(1, 1)])
        self.assertEqual(gui_main_table_loader.ROUTINE_STATUS_RUNNING, table.item(1, 0).data(gui_main_table_loader.ROUTINE_CHILD_STATUS_ROLE))





        expected_aggregate = "등록(4) | 제외(1) | 운영(1) | 검토(1)"
        self.assertEqual(
            expected_aggregate,
            table.item(0, 0).data(gui_main_table_loader.ROUTINE_PARENT_AGGREGATE_ROLE),
        )
        self.assertEqual(
            (("등록", "4"), ("제외", "1"), ("운영", "1"), ("검토", "1")),
            table.item(0, 0).data(
                gui_main_table_loader.ROUTINE_PARENT_AGGREGATE_VALUES_ROLE
            ),
        )
        self.assertEqual(
            ("수익(+35,200 / +2.82%)", DIRECTIONAL_NEGATIVE_COLOR),
            table.item(0, 0).data(
                gui_main_table_loader.ROUTINE_PARENT_PROFIT_ROLE
            ),
        )
        self.assertEqual(
            expected_aggregate,
            table.item(1, 0).data(gui_main_table_loader.ROUTINE_CHILD_AGGREGATE_ROLE),
        )
        for aggregate in (
            table.item(0, 0).data(gui_main_table_loader.ROUTINE_PARENT_AGGREGATE_ROLE),
            table.item(1, 0).data(gui_main_table_loader.ROUTINE_CHILD_AGGREGATE_ROLE),
        ):
            self.assertNotIn("오류", aggregate)
            self.assertNotIn("실행", aggregate)

        status_widget = table.cellWidget(1, 1)
        self.assertIsNotNone(status_widget)
        stamp = status_widget.findChild(QWidget, "routineInstanceStatusStamp")
        dot = status_widget.findChild(QLabel, "routineInstanceStatusDot")
        status_text = status_widget.findChild(QLabel, "routineInstanceStatusText")
        registered = status_widget.findChild(QWidget, "routineInstanceRegistered")
        excluded = status_widget.findChild(QWidget, "routineInstanceExcluded")
        operation = status_widget.findChild(QWidget, "routineInstanceOperationOrStopped")
        review = status_widget.findChild(QWidget, "routineInstanceReview")
        buy_limit = status_widget.findChild(QWidget, "routineInstanceBuyLimit")
        consumed = status_widget.findChild(QWidget, "routineInstanceConsumed")
        profit = status_widget.findChild(QWidget, "routineInstanceProfit")
        buy_limit_amount = status_widget.findChild(QLabel, "routineInstanceBuyLimitAmount")
        consumed_amount = status_widget.findChild(QLabel, "routineInstanceConsumedAmount")
        consumed_rate = status_widget.findChild(QLabel, "routineInstanceConsumedRate")
        profit_amount = status_widget.findChild(QLabel, "routineInstanceProfitAmount")
        profit_rate = status_widget.findChild(QLabel, "routineInstanceProfitRate")
        self.assertIsNotNone(stamp)
        self.assertIsNotNone(registered)
        self.assertIsNotNone(buy_limit)
        self.assertIsNotNone(consumed)
        self.assertIsNotNone(profit)
        self.assertIsNone(dot)
        self.assertEqual(gui_main_table_loader.ROUTINE_STATUS_RUNNING, status_text.text())
        self.assertEqual(
            gui_main_table_loader.ROUTINE_STATUS_STAMP_WIDTH,
            stamp.width(),
        )
        self.assertEqual(
            gui_main_table_loader.ROUTINE_STATUS_STAMP_HEIGHT,
            stamp.height(),
        )
        self.assertIn("border: 1px solid #16A34A", stamp.styleSheet())
        self.assertIn("color: #16A34A", status_text.styleSheet())
        self.assertEqual(
            "4",
            registered.findChild(QLabel, "routineInstanceRegisteredNumber").text(),
        )
        self.assertEqual(
            "1",
            excluded.findChild(QLabel, "routineInstanceExcludedNumber").text(),
        )
        self.assertEqual(
            "1",
            operation.findChild(
                QLabel,
                "routineInstanceOperationOrStoppedNumber",
            ).text(),
        )
        self.assertEqual(
            "1",
            review.findChild(QLabel, "routineInstanceReviewNumber").text(),
        )
        self.assertEqual("12,000,000", buy_limit_amount.text())
        self.assertEqual("7,843,650", consumed_amount.text())
        self.assertEqual("65.4%", consumed_rate.text())
        self.assertEqual("+35,200", profit_amount.text())
        self.assertEqual("+2.82%", profit_rate.text())
        self.assertIn(f"color: {DIRECTIONAL_NEGATIVE_COLOR}", profit_amount.styleSheet())
        self.assertIn(f"color: {DIRECTIONAL_NEGATIVE_COLOR}", profit_rate.styleSheet())
        column_widths = gui_main_table_loader.routine_instance_grid_columns(
            status_widget.font()
        )
        for key, label in (
            ("registered", registered),
            ("excluded", excluded),
            ("operation_or_stopped", operation),
            ("review", review),
        ):
            self.assertEqual(column_widths[key], label.width())
        for key, label in (
            ("limit", buy_limit),
            ("consumed", consumed),
            ("profit", profit),
        ):
            self.assertEqual(column_widths[key], label.width())
        number_widths = gui_main_table_loader.routine_instance_number_widths(
            status_widget.font()
        )
        for key, label in (
            ("limit_amount", buy_limit_amount),
            ("consumed_amount", consumed_amount),
            ("consumed_rate", consumed_rate),
            ("profit_amount", profit_amount),
            ("profit_rate", profit_rate),
        ):
            self.assertEqual(number_widths[key], label.width())
            self.assertEqual(Qt.AlignRight | Qt.AlignVCenter, label.alignment())
        separators = status_widget.findChildren(QLabel, "routineInstanceSeparator")
        self.assertEqual(6, len(separators))
        self.assertTrue(all(separator.text() == "|" for separator in separators))
        self.assertTrue(table.item(0, 0).data(gui_main_table_loader.ROUTINE_PARENT_AGGREGATE_ROLE))





        self.assertEqual("", table.item(0, 7).text())
        self.assertEqual("", table.item(1, 7).text())
        self.assertEqual("", table.item(0, 8).text())
        self.assertEqual("", table.item(0, 0).toolTip())
        self.assertIn("\n\n", table.item(1, 0).toolTip())

    def test_parent_aggregate_uses_all_children_before_visible_filter(self) -> None:
        table = FakeRoutineTable()
        window = SimpleNamespace(
            routine_table=table,
            _main_routine_sort_column=-1,
            _main_routine_sort_order=Qt.AscendingOrder,
            _collapsed_main_group_ids=set(),
            _collapsed_main_group_instance_ids=set(),
            _main_routine_display_level="routine",
            _main_routine_display_level_applied=True,
            _main_routine_valid_only=False,
            _main_routine_excluded_only=False,
        )
        definition = RoutineDefinitionRecord(
            definition_id="definition",
            display_name="Parent",
            package_dir=Path("routine-path"),
            schema_version="1.0",
            version="1.0",
            routine_type="auto_trade",
            entry_file="routine.py",
            module_name="routine",
            settings_ui="",
            default_rules_file="rules.json",
            package_enabled=True,
            source_name="Parent",
        )

        def instance(instance_id: str) -> RoutineInstanceRecord:
            return RoutineInstanceRecord(
                instance_id=instance_id,
                definition_id="definition",
                display_name=instance_id,
                source_routine_name="Parent",
                persisted=True,
                source="PERSISTED",
                enabled=True,
                real_trade_allowed=False,
                description="",
                buy_limit_enabled=False,
                buy_limit_amount=None,
                rules_path=Path(f"{instance_id}.json"),
            )

        instances = [instance("visible"), instance("review-only")]
        group = _main_group()
        group_id = str(group.path.resolve())
        assigned_stocks = [
            _assigned_stock("visible", group_name=group.name, code="000001"),
            _assigned_stock("review-only", group_name=group.name, code="000002"),
        ]
        window._collapsed_main_group_instance_ids = {
            gui_main_table_loader.main_group_instance_relation_id(
                group_id,
                item.instance_id,
            )
            for item in instances
        }
        counts = {
            "visible": {
                "registered": 10,
                "excluded": 2,
                "operation_or_stopped": 7,
                "operation_running": 0,
                "review": 1,
                "stocks": [{"code": "000001", "name": "Visible"}],
            },
            "review-only": {
                "registered": 4,
                "excluded": 0,
                "operation_or_stopped": 0,
                "operation_running": 0,
                "review": 4,
                "stocks": [],
            },
        }
        stock_row = {
            "kind": gui_main_table_loader.ROUTINE_ROW_STOCK,
            "definition_id": "definition",
            "instance_id": "visible",
        }

        with (
            patch.object(
                gui_main_table_loader,
                "load_routine_definitions",
                return_value=[definition],
            ),
            patch.object(
                gui_main_table_loader,
                "load_persisted_routine_instances",
                return_value=instances,
            ),
            patch.object(
                gui_main_table_loader,
                "get_group_records",
                return_value=[group],
            ),
            patch.object(
                gui_main_table_loader,
                "_main_pnl_refresh_static_cache",
                return_value=_main_static_cache(
                    [definition], instances, assigned_stocks
                ),
            ),
            patch.object(
                gui_main_table_loader,
                "_instance_stock_counts",
                return_value=counts,
            ),
            patch.object(
                gui_main_table_loader,
                "_routine_tree_stock_row",
                return_value=stock_row,
            ),
            patch.object(
                gui_main_table_loader,
                "current_stock_trade_counts_by_code",
                return_value={},
            ),
        ):
            gui_main_table_loader.main_load_routine_table(window)
            before = table.item(0, 0).data(
                gui_main_table_loader.ROUTINE_PARENT_AGGREGATE_VALUES_ROLE
            )
            self.assertEqual(3, table.row_count)

            window._main_routine_valid_only = True
            gui_main_table_loader.main_load_routine_table(window)
            after = table.item(0, 0).data(
                gui_main_table_loader.ROUTINE_PARENT_AGGREGATE_VALUES_ROLE
            )

            aggregates_by_level = {}
            window._main_routine_valid_only = False
            for level in ("group", "routine", "stock"):
                window._main_routine_display_level = level
                gui_main_table_loader.main_load_routine_table(window)
                aggregates_by_level[level] = table.item(0, 0).data(
                    gui_main_table_loader.ROUTINE_PARENT_AGGREGATE_VALUES_ROLE
                )

            window._main_routine_display_level = "group"
            window._main_routine_valid_only = True
            window._main_routine_excluded_only = True
            gui_main_table_loader.main_load_routine_table(window)
            excluded_view = table.item(0, 0).data(
                gui_main_table_loader.ROUTINE_PARENT_AGGREGATE_VALUES_ROLE
            )

        self.assertEqual(before, after)
        self.assertTrue(
            all(value == before for value in aggregates_by_level.values())
        )
        self.assertEqual(before, excluded_view)
        self.assertEqual(
            (("등록", "14"), ("제외", "2"), ("정지", "7"), ("검토", "5")),
            after,
        )
        self.assertEqual(2, table.row_count)




    def test_parent_instance_and_stock_rows_have_no_checkboxes(self) -> None:
        table = FakeRoutineTable()
        window = SimpleNamespace(
            routine_table=table,
            _main_routine_sort_column=-1,
            _main_routine_sort_order=0,
            _collapsed_main_group_ids=set(),
            _collapsed_main_group_instance_ids=set(),
        )
        definition = RoutineDefinitionRecord(
            definition_id="definition-a",
            display_name="Parent",
            package_dir=Path("routine-path"),
            schema_version="1.0",
            version="1.0",
            routine_type="auto_trade",
            entry_file="routine.py",
            module_name="routine",
            settings_ui="",
            default_rules_file="rules.json",
            package_enabled=True,
            source_name="Parent",
        )
        instance = RoutineInstanceRecord(
            instance_id="instance-a",
            definition_id="definition-a",
            display_name="Instance",
            source_routine_name="Parent",
            persisted=True,
            source="PERSISTED",
            enabled=True,
            real_trade_allowed=False,
            description="",
            buy_limit_enabled=False,
            buy_limit_amount=None,
            rules_path=Path("instance-rules.json"),
        )
        count = {
            "registered": 1,
            "running": 0,
            "stopped": 1,
            "error": 0,
            "consumed_amount": 0,
            "consumed_unknown": False,
            "profit_amount": 0,
            "profit_cost_basis": 0,
            "profit_unknown": False,
            "stocks": [
                {
                    "code": "000001",
                    "name": "Stock",
                    "enabled": True,
                    "stock_path": "stocks/000001_Stock",
                    "state": {},
                    "config": {},
                }
            ],
        }
        group = _main_group()
        assigned_stocks = [
            _assigned_stock(instance.instance_id, group_name=group.name)
        ]

        with (
            patch.object(gui_main_table_loader, "load_routine_definitions", return_value=[definition]),
            patch.object(gui_main_table_loader, "load_persisted_routine_instances", return_value=[instance]),
            patch.object(gui_main_table_loader, "get_group_records", return_value=[group]),
            patch.object(
                gui_main_table_loader,
                "_main_pnl_refresh_static_cache",
                return_value=_main_static_cache(
                    [definition], [instance], assigned_stocks
                ),
            ),
            patch.object(
                gui_main_table_loader,
                "_instance_stock_counts",
                return_value={instance.instance_id: count},
            ),
        ):
            gui_main_table_loader.main_load_routine_table(window)

        self.assertEqual(3, table.row_count)
        self.assertEqual(
            [
                gui_main_table_loader.ROUTINE_ROW_PARENT,
                gui_main_table_loader.ROUTINE_ROW_CHILD,
                gui_main_table_loader.ROUTINE_ROW_STOCK,
            ],
            [
                table.item(row, 0).data(gui_main_table_loader.ROUTINE_ROW_KIND_ROLE)
                for row in range(3)
            ],
        )
        for row in range(3):
            item = table.item(row, 0)
            self.assertFalse(item.flags() & Qt.ItemIsUserCheckable)
            self.assertIsNone(item.data(Qt.CheckStateRole))

    def test_stock_metric_badge_sort_keeps_hierarchy_and_sorts_within_instance(
        self,
    ) -> None:
        table = FakeRoutineTable()
        window = SimpleNamespace(
            routine_table=table,
            _main_routine_sort_column=-1,
            _main_routine_sort_order=Qt.AscendingOrder,
            _collapsed_main_group_ids=set(),
            _collapsed_main_group_instance_ids=set(),
            _main_routine_display_level="stock",
            _main_routine_display_level_applied=True,
            _main_routine_valid_only=False,
            _main_routine_metric_sort_active=True,
            _main_routine_metric_sort_key="profit",
        )
        definition = RoutineDefinitionRecord(
            definition_id="definition-a",
            display_name="Parent",
            package_dir=Path("routine-path"),
            schema_version="1.0",
            version="1.0",
            routine_type="auto_trade",
            entry_file="routine.py",
            module_name="routine",
            settings_ui="",
            default_rules_file="rules.json",
            package_enabled=True,
            source_name="Parent",
        )
        instance = RoutineInstanceRecord(
            instance_id="instance-a",
            definition_id="definition-a",
            display_name="Instance",
            source_routine_name="Parent",
            persisted=True,
            source="PERSISTED",
            enabled=True,
            real_trade_allowed=False,
            description="",
            buy_limit_enabled=False,
            buy_limit_amount=None,
            rules_path=Path("instance-rules.json"),
        )
        stocks = [
            {
                "code": "000001",
                "name": "Low",
                "enabled": True,
                "stock_path": "stocks/000001_Low",
            },
            {
                "code": "000002",
                "name": "High",
                "enabled": True,
                "stock_path": "stocks/000002_High",
            },
        ]
        group = _main_group()
        assigned_stocks = [
            {
                **stock,
                "routines": [group.name],
                "assigned_routine_instance_id": instance.instance_id,
            }
            for stock in stocks
        ]

        def metric_values(_window, stock, **_kwargs):
            profit = 10 if stock["name"] == "High" else -5
            return (
                (),
                "gray",
                "?�도(0)",
                None,
                {
                    "holding": 0,
                    "price": 0,
                    "profit": profit,
                    "trade": 0,
                    "limit": 0,
                },
            )

        with (
            patch.object(
                gui_main_table_loader,
                "load_routine_definitions",
                return_value=[definition],
            ),
            patch.object(
                gui_main_table_loader,
                "load_persisted_routine_instances",
                return_value=[instance],
            ),
            patch.object(
                gui_main_table_loader,
                "get_group_records",
                return_value=[group],
            ),
            patch.object(
                gui_main_table_loader,
                "_main_pnl_refresh_static_cache",
                return_value=_main_static_cache(
                    [definition], [instance], assigned_stocks
                ),
            ),
            patch.object(
                gui_main_table_loader,
                "_instance_stock_counts",
                return_value={
                    instance.instance_id: {
                        "registered": 2,
                        "running": 0,
                        "stopped": 2,
                        "error": 0,
                        "consumed_amount": 0,
                        "consumed_unknown": False,
                        "profit_amount": 0,
                        "profit_cost_basis": 0,
                        "profit_unknown": False,
                        "stocks": stocks,
                    }
                },
            ),
            patch.object(
                gui_main_table_loader,
                "_routine_tree_stock_display_values",
                side_effect=lambda _window, stock, **_kwargs: [stock["name"]],
            ),
            patch.object(
                gui_main_table_loader,
                "_routine_tree_stock_metric_values",
                side_effect=metric_values,
            ),
        ):
            gui_main_table_loader.main_load_routine_table(window)
            hierarchical_kinds = [
                table.item(row, 0).data(
                    gui_main_table_loader.ROUTINE_ROW_KIND_ROLE
                )
                for row in range(4)
            ]
            hierarchical_names = [
                table.item(row, 0).data(
                    gui_main_table_loader.ROUTINE_STOCK_NAME_ROLE
                )
                for row in (2, 3)
            ]
            window._main_routine_valid_only = True
            gui_main_table_loader.main_load_routine_table(window)
            flat_valid_kinds = [
                table.item(row, 0).data(
                    gui_main_table_loader.ROUTINE_ROW_KIND_ROLE
                )
                for row in range(table.row_count)
            ]
            flat_valid_names = [
                table.item(row, 0).data(
                    gui_main_table_loader.ROUTINE_STOCK_NAME_ROLE
                )
                for row in range(table.row_count)
            ]

        self.assertEqual(
            [
                gui_main_table_loader.ROUTINE_ROW_PARENT,
                gui_main_table_loader.ROUTINE_ROW_CHILD,
                gui_main_table_loader.ROUTINE_ROW_STOCK,
                gui_main_table_loader.ROUTINE_ROW_STOCK,
            ],
            hierarchical_kinds,
        )
        self.assertEqual(
            ["High | ?�도(0)", "Low | ?�도(0)"],
            hierarchical_names,
        )
        self.assertEqual(
            [
                gui_main_table_loader.ROUTINE_ROW_STOCK,
                gui_main_table_loader.ROUTINE_ROW_STOCK,
            ],
            flat_valid_kinds,
        )
        self.assertEqual(
            ["High | ?�도(0)", "Low | ?�도(0)"],
            flat_valid_names,
        )

    def test_routine_metric_badges_sort_by_profit_and_limit(self) -> None:
        table = FakeRoutineTable()
        window = SimpleNamespace(
            routine_table=table,
            _main_routine_sort_column=-1,
            _main_routine_sort_order=Qt.AscendingOrder,
            _collapsed_main_group_ids=set(),
            _collapsed_main_group_instance_ids=set(),
            _main_routine_display_level="routine",
            _main_routine_display_level_applied=True,
            _main_routine_valid_only=False,
            _main_routine_metric_sort_active=True,
            _main_routine_metric_sort_key="profit",
        )
        definition = RoutineDefinitionRecord(
            definition_id="definition-a",
            display_name="Parent",
            package_dir=Path("routine-path"),
            schema_version="1.0",
            version="1.0",
            routine_type="auto_trade",
            entry_file="routine.py",
            module_name="routine",
            settings_ui="",
            default_rules_file="rules.json",
            package_enabled=True,
            source_name="Parent",
        )
        high_limit = RoutineInstanceRecord(
            instance_id="high-limit",
            definition_id="definition-a",
            display_name="High Limit",
            source_routine_name="Parent",
            persisted=True,
            source="PERSISTED",
            enabled=True,
            real_trade_allowed=False,
            description="",
            buy_limit_enabled=True,
            buy_limit_amount=9_000_000,
            rules_path=Path("high-limit-rules.json"),
        )
        high_profit = RoutineInstanceRecord(
            instance_id="high-profit",
            definition_id="definition-a",
            display_name="High Profit",
            source_routine_name="Parent",
            persisted=True,
            source="PERSISTED",
            enabled=True,
            real_trade_allowed=False,
            description="",
            buy_limit_enabled=True,
            buy_limit_amount=1_000_000,
            rules_path=Path("high-profit-rules.json"),
        )
        group = _main_group()
        assigned_stocks = [
            _assigned_stock(
                high_limit.instance_id,
                group_name=group.name,
                code="000001",
            ),
            _assigned_stock(
                high_profit.instance_id,
                group_name=group.name,
                code="000002",
            ),
        ]

        def count(profit_amount):
            return {
                "registered": 0,
                "running": 0,
                "stopped": 0,
                "error": 0,
                "consumed_amount": 0,
                "consumed_unknown": False,
                "profit_amount": profit_amount,
                "profit_cost_basis": 1_000_000,
                "profit_unknown": False,
                "stocks": [],
            }

        with (
            patch.object(
                gui_main_table_loader,
                "load_routine_definitions",
                return_value=[definition],
            ),
            patch.object(
                gui_main_table_loader,
                "load_persisted_routine_instances",
                return_value=[high_limit, high_profit],
            ),
            patch.object(
                gui_main_table_loader,
                "get_group_records",
                return_value=[group],
            ),
            patch.object(
                gui_main_table_loader,
                "_main_pnl_refresh_static_cache",
                return_value=_main_static_cache(
                    [definition], [high_limit, high_profit], assigned_stocks
                ),
            ),
            patch.object(
                gui_main_table_loader,
                "_instance_stock_counts",
                return_value={
                    high_limit.instance_id: count(10_000),
                    high_profit.instance_id: count(90_000),
                },
            ),
            patch.object(
                gui_main_table_loader,
                "current_stock_trade_counts_by_code",
                return_value={},
            ),
        ):
            gui_main_table_loader.main_load_routine_table(window)
            self.assertEqual(
                ["high-profit", "high-limit"],
                [
                    table.item(row, 0).data(
                        gui_main_table_loader.ROUTINE_INSTANCE_ID_ROLE
                    )
                    for row in (1, 2)
                ],
            )

            window._main_routine_metric_sort_key = "limit"
            gui_main_table_loader.main_load_routine_table(window)
            self.assertEqual(
                ["high-limit", "high-profit"],
                [
                    table.item(row, 0).data(
                        gui_main_table_loader.ROUTINE_INSTANCE_ID_ROLE
                    )
                    for row in (1, 2)
                ],
            )

    def test_actual_main_window_renders_parent_child_rows_without_checkboxes(self) -> None:
        import gui_windows

        definition = RoutineDefinitionRecord(
            definition_id="indicator_follow",
            display_name="지?�추종매�?",
            package_dir=Path("routine-path"),
            schema_version="1.0",
            version="0.1.0",
            routine_type="auto_trade",
            entry_file="routine.py",
            module_name="indicator_follow_routine",
            settings_ui="indicator_follow",
            default_rules_file="rules.json",
            package_enabled=True,
            source_name="지?�추종매�?",
        )
        instance = RoutineInstanceRecord(
            instance_id="a52f539d-4f18-4ef6-b0cf-f471567982a1",
            definition_id="indicator_follow",
            display_name="?�?�주 추세??",
            source_routine_name="지?�추종매�?",
            persisted=True,
            source="PERSISTED",
            enabled=False,
            real_trade_allowed=False,
            description="?�?�주 중심??보수??추세 진입",
            buy_limit_enabled=True,
            buy_limit_amount=12_000_000,
            rules_path=Path("instance-rules.json"),
        )
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
            patch.object(gui_windows.MainWindow, "load_running_stock_table"),
            patch.object(gui_main_table_loader, "load_routine_definitions", return_value=[definition]),
            patch.object(gui_main_table_loader, "load_persisted_routine_instances", return_value=[instance]),
            patch.object(gui_main_table_loader, "_routine_stock_counts_from_base_stocks", return_value={}),
            patch.object(gui_main_table_loader, "_instance_stock_counts", return_value={}),
        ):
            window = gui_windows.MainWindow()
            try:
                gui_main_table_loader.main_load_routine_table(window)
                window.show()
                self.app.processEvents()

                self.assertTrue(window._main_routine_valid_only)
                self.assertEqual("stock", window._main_routine_display_level)
                self.assertEqual(0, window.routine_table.rowCount())
                self.assertIn(
                    gui_windows.AUTO_TRADE_SETTING_BADGE_ACTIVE_COLOR,
                    window._main_routine_valid_button.styleSheet(),
                )
                self.assertIn(
                    gui_windows.AUTO_TRADE_SETTING_BADGE_ACTIVE_COLOR,
                    window._main_routine_summary_count_buttons[
                        "operation"
                    ].styleSheet(),
                )
                window._set_main_routine_stock_scope("operation", False)

                window._main_routine_valid_button.click()
                window._main_routine_level_buttons["group"].click()
                self.app.processEvents()
                self.assertEqual(1, window.routine_table.rowCount())
                self.assertTrue(
                    all(
                        not button.isEnabled()
                        for button in window._main_routine_metric_buttons.values()
                    )
                )
                window._main_routine_metric_buttons["profit"].click()
                self.assertFalse(window._main_routine_metric_sort_active)
                window._main_routine_level_buttons["routine"].click()
                self.app.processEvents()
                self.assertEqual(2, window.routine_table.rowCount())
                self.assertEqual(
                    {"profit", "limit"},
                    {
                        metric
                        for metric, button in window._main_routine_metric_buttons.items()
                        if button.isEnabled()
                    },
                )
                window._main_routine_metric_buttons["holding"].click()
                self.assertFalse(window._main_routine_metric_sort_active)
                window._main_routine_metric_buttons["profit"].click()
                self.assertTrue(window._main_routine_metric_sort_active)
                self.assertEqual("profit", window._main_routine_metric_sort_key)
                window._main_routine_level_buttons["stock"].click()
                window._main_routine_metric_buttons["holding"].click()
                self.assertEqual("holding", window._main_routine_metric_sort_key)
                window._main_routine_level_buttons["routine"].click()
                self.assertFalse(window._main_routine_metric_sort_active)
                self.assertEqual("", window._main_routine_metric_sort_key)
                self.assertTrue(window.routine_table.item(0, 0).text())
                self.assertTrue(window.routine_table.item(1, 0).text())
                self.assertFalse(
                    window.routine_table.item(0, 0).flags()
                    & Qt.ItemIsUserCheckable
                )
                self.assertFalse(
                    window.routine_table.item(1, 0).flags()
                    & Qt.ItemIsUserCheckable
                )
                self.assertIsNone(
                    window.routine_table.item(0, 0).data(Qt.CheckStateRole)
                )
                self.assertIsNone(
                    window.routine_table.item(1, 0).data(Qt.CheckStateRole)
                )
                self.assertEqual(gui_main_table_loader.ROUTINE_STATUS_STOPPED, window.routine_table.cellWidget(1, 1).findChild(QLabel, "routineInstanceStatusText").text())





                self.assertFalse(window.routine_table.horizontalHeader().isVisible())
                self.assertEqual(
                    Qt.ScrollBarAlwaysOff,
                    window.routine_table.horizontalScrollBarPolicy(),
                )
                self.assertFalse(window.routine_table.horizontalScrollBar().isVisible())
                self.assertEqual(
                    QHeaderView.Stretch,
                    window.routine_table.horizontalHeader().sectionResizeMode(9),
                )
                status_container = window.routine_table.cellWidget(1, 1)
                self.assertEqual(
                    "12,000,000",
                    status_container.findChild(
                        QLabel,
                        "routineInstanceBuyLimitAmount",
                    ).text(),
                )
                self.assertEqual(
                    "0",
                    status_container.findChild(
                        QLabel,
                        "routineInstanceConsumedAmount",
                    ).text(),
                )
                self.assertEqual(
                    "0.0%",
                    status_container.findChild(
                        QLabel,
                        "routineInstanceConsumedRate",
                    ).text(),
                )
                self.assertEqual(
                    "0",
                    status_container.findChild(
                        QLabel,
                        "routineInstanceProfitAmount",
                    ).text(),
                )
                self.assertEqual(
                    "0.00%",
                    status_container.findChild(
                        QLabel,
                        "routineInstanceProfitRate",
                    ).text(),
                )
                window.resize(900, 720)
                self.app.processEvents()
                self.assertFalse(window.routine_table.horizontalScrollBar().isVisible())
                window.resize(1120, 720)
                self.app.processEvents()
                self.assertEqual(10, window.routine_table.columnSpan(0, 0))
                self.assertTrue(window.routine_table.item(0, 0).data(gui_main_table_loader.ROUTINE_PARENT_AGGREGATE_ROLE))





                self.assertEqual(Qt.CustomContextMenu, window.routine_table.contextMenuPolicy())
                self.assertFalse(window.grab().isNull())

                parent_index = window.routine_table.model().index(0, 0)
                parent_font = gui_windows._routine_parent_font(window.routine_table.font())
                self.assertAlmostEqual(
                    window.routine_table.font().pointSizeF() + 1.0,
                    parent_font.pointSizeF(),
                )
                expected_row_height = max(
                    gui_main_table_loader.ROUTINE_INSTANCE_ROW_HEIGHT,
                    window.routine_table.verticalHeader().minimumSectionSize(),
                )
                self.assertEqual(expected_row_height, window.routine_table.rowHeight(0))
                self.assertEqual(
                    window.routine_table.rowHeight(0),
                    window.routine_table.rowHeight(1),
                )
                self.assertTrue(window._routine_tree_item_delegate.display_text(parent_index, window.routine_table))





                parent_name_rect = window._routine_tree_interaction_controller._parent_name_rect(
                    parent_index
                )
                def move_routine_pointer(point: QPoint) -> None:
                    event = QMouseEvent(
                        QEvent.MouseMove,
                        QPointF(point),
                        Qt.NoButton,
                        Qt.NoButton,
                        Qt.NoModifier,
                    )
                    window._routine_tree_interaction_controller.eventFilter(
                        window.routine_table.viewport(), event
                    )

                move_routine_pointer(parent_name_rect.center())
                self.app.processEvents()
                self.assertEqual(
                    definition.definition_id,
                    window.routine_table._hovered_routine_definition_id,
                )
                self.assertIn("(0)", window._routine_tree_item_delegate.display_text(parent_index, window.routine_table))






                parent_rect = window.routine_table.visualRect(parent_index)
                move_routine_pointer(
                    QPoint(
                        parent_rect.left()
                        + gui_main_table_loader.ROUTINE_PARENT_CHECKBOX_OFFSET
                        + 2,
                        parent_rect.center().y(),
                    )
                )
                self.app.processEvents()
                self.assertEqual(
                    "", window.routine_table._hovered_routine_definition_id
                )
                self.assertTrue(window._routine_tree_item_delegate.display_text(parent_index, window.routine_table))






                move_routine_pointer(
                    QPoint(
                        parent_rect.left()
                        + gui_main_table_loader.ROUTINE_PARENT_EXPAND_OFFSET
                        + 2,
                        parent_rect.center().y(),
                    )
                )
                self.app.processEvents()
                self.assertEqual(
                    "", window.routine_table._hovered_routine_definition_id
                )

                move_routine_pointer(
                    QPoint(parent_rect.right() - 4, parent_rect.center().y())
                )
                self.app.processEvents()
                self.assertEqual(
                    "", window.routine_table._hovered_routine_definition_id
                )

                screenshot_path = os.environ.get("ROUTINE_UI_SCREENSHOT_PATH", "").strip()
                if screenshot_path:
                    self.assertTrue(window.grab().save(screenshot_path))

                parent_rect = window.routine_table.visualItemRect(window.routine_table.item(0, 0))
                parent_menu = MagicMock()
                parent_actions = [MagicMock(), MagicMock(), MagicMock()]
                parent_menu.addAction.side_effect = parent_actions
                with (
                    patch.object(gui_windows, "QMenu", return_value=parent_menu),
                    patch.object(gui_windows, "routine_definition_by_id", return_value=definition),
                ):
                    window.open_routine_context_menu(parent_name_rect.center())
                self.assertEqual(3, len(parent_menu.addAction.call_args_list))
                self.assertEqual(
                    ["신규루틴", "조기마감", "즉시청산"],
                    [call.args[0] for call in parent_menu.addAction.call_args_list],
                )
                parent_menu.addSeparator.assert_called_once_with()
                parent_actions[0].triggered.connect.assert_called_once()



                for action in parent_actions[1:]:
                    action.setEnabled.assert_called_once_with(False)
                    action.setStatusTip.assert_called_once()



                with patch.object(gui_windows, "QMenu") as menu_factory:
                    window.open_routine_context_menu(
                        QPoint(parent_rect.right() - 4, parent_rect.center().y())
                    )
                    menu_factory.assert_not_called()

                child_rect = window.routine_table.visualItemRect(window.routine_table.item(1, 0))
                child_index = window.routine_table.model().index(1, 0)
                child_name_rect = window._routine_tree_interaction_controller._child_name_rect(
                    child_index
                )

                def double_click_routine(point: QPoint) -> None:
                    event = QMouseEvent(
                        QEvent.MouseButtonDblClick,
                        QPointF(point),
                        Qt.LeftButton,
                        Qt.LeftButton,
                        Qt.NoModifier,
                    )
                    window._routine_tree_interaction_controller.eventFilter(
                        window.routine_table.viewport(),
                        event,
                    )

                status_container = window.routine_table.cellWidget(1, 1)
                status_stamp = status_container.findChild(
                    QWidget,
                    "routineInstanceStatusStamp",
                )
                aggregate_label = status_container.findChild(
                    QLabel,
                    "routineInstanceRegistered",
                )
                blocked_points = [
                    QPoint(
                        child_rect.left()
                        + gui_main_table_loader.ROUTINE_CHILD_CHECKBOX_OFFSET
                        + 2,
                        child_rect.center().y(),
                    ),
                    status_stamp.mapTo(
                        window.routine_table.viewport(),
                        status_stamp.rect().center(),
                    ),
                    aggregate_label.mapTo(
                        window.routine_table.viewport(),
                        aggregate_label.rect().center(),
                    ),
                ]
                blank_cell_x = child_name_rect.right() + 6
                if blank_cell_x < child_rect.right():
                    blocked_points.append(
                        QPoint(blank_cell_x, child_rect.center().y())
                    )
                with patch.object(
                    window,
                    "open_routine_settings_from_main_table",
                ) as settings_open:
                    double_click_routine(parent_name_rect.center())
                    with patch.object(
                        window,
                        "start_routine_instance_name_edit",
                    ) as name_edit:
                        double_click_routine(child_name_rect.center())
                        name_edit.assert_not_called()
                        for blocked_point in blocked_points:
                            double_click_routine(blocked_point)
                        name_edit.assert_not_called()
                    settings_open.assert_not_called()

                with patch.object(
                    window,
                    "open_routine_settings_from_main_table",
                ) as settings_open:
                    new_routine_slot = parent_actions[0].triggered.connect.call_args.args[0]
                    new_routine_slot()
                settings_open.assert_called_once_with(window.routine_table.item(0, 0))

                fake_menu = MagicMock()
                child_actions = [MagicMock() for _index in range(6)]
                fake_menu.addAction.side_effect = child_actions
                with (
                    patch.object(gui_windows, "QMenu", return_value=fake_menu),
                    patch.object(gui_windows, "routine_instance_by_id", return_value=instance),
                ):
                    window.open_routine_context_menu(child_rect.center())
                self.assertEqual(
                    ["설정변경", "루틴삭제", "이름변경", "종목등록", "조기마감", "즉시청산"],
                    [call.args[0] for call in fake_menu.addAction.call_args_list],
                )
                fake_menu.addSeparator.assert_called_once_with()
                for action in child_actions[:4]:
                    action.triggered.connect.assert_called_once()
                for action in child_actions[4:]:
                    action.setEnabled.assert_called_once_with(False)
                    action.setStatusTip.assert_called_once()



                window._routine_assigned_stock_count_by_instance[instance.instance_id] = 1
                active_parent_menu = MagicMock()
                active_parent_actions = [MagicMock(), MagicMock(), MagicMock()]
                active_parent_menu.addAction.side_effect = active_parent_actions
                with (
                    patch.object(gui_windows, "QMenu", return_value=active_parent_menu),
                    patch.object(gui_windows, "routine_definition_by_id", return_value=definition),
                ):
                    window.open_routine_context_menu(parent_name_rect.center())
                for action in active_parent_actions[1:]:
                    action.setEnabled.assert_called_once_with(True)

                active_child_menu = MagicMock()
                active_child_actions = [MagicMock() for _index in range(6)]
                active_child_menu.addAction.side_effect = active_child_actions
                with (
                    patch.object(gui_windows, "QMenu", return_value=active_child_menu),
                    patch.object(gui_windows, "routine_instance_by_id", return_value=instance),
                ):
                    window.open_routine_context_menu(child_rect.center())
                self.assertEqual(
                    ["설정변경", "루틴삭제", "이름변경", "종목등록", "조기마감", "즉시청산"],
                    [call.args[0] for call in active_child_menu.addAction.call_args_list],
                )
                active_child_menu.addSeparator.assert_called_once_with()
                for action in active_child_actions[:4]:
                    action.triggered.connect.assert_called_once()
                for action in active_child_actions[4:]:
                    action.setEnabled.assert_called_once_with(True)
                with patch.object(
                    window,
                    "open_routine_instance_stock_register_from_main_table",
                ) as register_open:
                    register_slot = active_child_actions[3].triggered.connect.call_args.args[0]
                    register_slot()
                register_open.assert_called_once_with(instance.instance_id)

                fake_result = SimpleNamespace(
                    status="SUCCESS",
                    stock_results=(SimpleNamespace(status="APPLIED"),),
                    error="",
                )
                command_service = MagicMock()
                command_service.apply.return_value = fake_result
                early_dialog = MagicMock()
                early_dialog.exec_.return_value = gui_windows.QMessageBox.Yes
                with (
                    patch.object(gui_windows, "OperationCommandService", return_value=command_service),
                    patch.object(
                        gui_windows,
                        "_create_routine_operation_confirmation",
                        return_value=early_dialog,
                    ),
                ):
                    window.request_routine_operation(
                        instance.instance_id,
                        instance.display_name,
                        "루틴",
                        "조기마감",
                    )
                request = command_service.apply.call_args.args[0]
                self.assertEqual("ROUTINE_INSTANCE", request.target_scope)
                self.assertEqual(instance.instance_id, request.target_id)
                self.assertEqual("EARLY_CLOSE", request.command)
                self.assertEqual(gui_main_table_loader.ROUTINE_STATUS_STOPPED, window.routine_table.cellWidget(1, 1).findChild(QLabel, "routineInstanceStatusText").text())






                command_service.reset_mock()
                command_service.apply.return_value = fake_result
                immediate_dialog = MagicMock()
                immediate_dialog.exec_.return_value = gui_windows.QMessageBox.Yes
                with (
                    patch.object(gui_windows, "OperationCommandService", return_value=command_service),
                    patch.object(
                        gui_windows,
                        "_create_routine_operation_confirmation",
                        return_value=immediate_dialog,
                    ),
                ):
                    window.request_routine_operation(
                        instance.instance_id,
                        instance.display_name,
                        gui_windows.POLICY_MARKET,
                        "즉시�?��",
                    )
                request = command_service.apply.call_args.args[0]
                self.assertEqual("EARLY_CLOSE", request.command)
                self.assertEqual(gui_main_table_loader.ROUTINE_STATUS_STOPPED, window.routine_table.cellWidget(1, 1).findChild(QLabel, "routineInstanceStatusText").text())






                second_instance_id = "00000000-0000-0000-0000-000000000002"
                third_instance_id = "00000000-0000-0000-0000-000000000003"
                window._routine_instance_ids_by_definition[definition.definition_id] = (
                    instance.instance_id,
                    second_instance_id,
                    third_instance_id,
                )
                window._routine_instance_selection[second_instance_id] = False
                window._routine_instance_selection[third_instance_id] = True
                window._routine_assigned_stock_count_by_instance.update(
                    {
                        instance.instance_id: 1,
                        second_instance_id: 0,
                        third_instance_id: 1,
                    }
                )
                category_cancel_dialog = MagicMock()
                category_cancel_dialog.exec_.return_value = gui_windows.QMessageBox.No
                with (
                    patch.object(gui_windows, "OperationCommandService") as service_factory,
                    patch.object(
                        gui_windows,
                        "_create_routine_operation_confirmation",
                        return_value=category_cancel_dialog,
                    ),
                ):
                    window.request_routine_definition_operation(
                        definition.definition_id,
                        definition.display_name,
                        "루틴",
                        "조기마감",
                    )
                service_factory.assert_not_called()

                category_service = MagicMock()
                category_service.apply.side_effect = [
                    fake_result,
                    SimpleNamespace(
                        status="PARTIAL_SUCCESS",
                        stock_results=(SimpleNamespace(status="FAILED"),),
                        error="",
                    ),
                ]
                category_early_dialog = MagicMock()
                category_early_dialog.exec_.return_value = gui_windows.QMessageBox.Yes
                with (
                    patch.object(
                        gui_windows,
                        "OperationCommandService",
                        return_value=category_service,
                    ),
                    patch.object(
                        gui_windows,
                        "_create_routine_operation_confirmation",
                        return_value=category_early_dialog,
                    ),
                    patch.object(gui_windows.QMessageBox, "warning"),
                ):
                    window.request_routine_definition_operation(
                        definition.definition_id,
                        definition.display_name,
                        "루틴",
                        "조기마감",
                    )
                category_requests = [
                    call_item.args[0]
                    for call_item in category_service.apply.call_args_list
                ]
                self.assertEqual(
                    sorted((instance.instance_id, third_instance_id)),
                    [request.target_id for request in category_requests],
                )
                self.assertNotIn(
                    second_instance_id,
                    [request.target_id for request in category_requests],
                )
                self.assertTrue(
                    all(request.target_scope == "ROUTINE_INSTANCE" for request in category_requests)
                )
                self.assertTrue(
                    all(request.command == "EARLY_CLOSE" for request in category_requests)
                )

                window._routine_instance_ids_by_definition[definition.definition_id] = (
                    instance.instance_id,
                    second_instance_id,
                    third_instance_id,
                )
                window._routine_instance_selection[second_instance_id] = False
                window._routine_instance_selection[third_instance_id] = True
                window._routine_assigned_stock_count_by_instance.update(
                    {
                        instance.instance_id: 1,
                        second_instance_id: 0,
                        third_instance_id: 1,
                    }
                )
                category_service.reset_mock()
                category_service.apply.side_effect = [fake_result, fake_result]
                category_immediate_dialog = MagicMock()
                category_immediate_dialog.exec_.return_value = gui_windows.QMessageBox.Yes
                with (
                    patch.object(
                        gui_windows,
                        "OperationCommandService",
                        return_value=category_service,
                    ),
                    patch.object(
                        gui_windows,
                        "_create_routine_operation_confirmation",
                        return_value=category_immediate_dialog,
                    ),
                ):
                    window.request_routine_definition_operation(
                        definition.definition_id,
                        definition.display_name,
                        gui_windows.POLICY_MARKET,
                        "즉시�?��",
                    )
                self.assertEqual(
                    ["EARLY_CLOSE", "EARLY_CLOSE"],
                    [
                        call_item.args[0].command
                        for call_item in category_service.apply.call_args_list
                    ],
                )

                window._routine_instance_ids_by_definition[definition.definition_id] = (
                    instance.instance_id,
                    second_instance_id,
                )
                window._routine_instance_selection[instance.instance_id] = False
                window._routine_instance_selection[second_instance_id] = False
                with (
                    patch.object(gui_windows, "OperationCommandService") as service_factory,
                    patch.object(gui_windows.QMessageBox, "warning"),
                ):
                    window.request_routine_definition_operation(
                        definition.definition_id,
                        definition.display_name,
                        gui_windows.POLICY_MARKET,
                        "즉시�?��",
                    )
                service_factory.assert_not_called()
                window._routine_instance_selection[instance.instance_id] = True

                with patch.object(gui_windows, "routine_instance_by_id", return_value=instance):
                    self.assertTrue(
                        window.reflect_routine_completion_result(
                            instance.instance_id,
                            gui_main_table_loader.ROUTINE_STATUS_COMPLETED,
                        )
                    )
                self.assertEqual(gui_main_table_loader.ROUTINE_STATUS_STOPPED, window.routine_table.cellWidget(1, 1).findChild(QLabel, "routineInstanceStatusText").text())





                with patch.object(window, "update_review_required_button_text") as review_refresh:
                    self.assertFalse(
                        window.reflect_routine_completion_result(
                            instance.instance_id,
                            "?��??�료",
                            data_mismatch=True,
                        )
                    )
                review_refresh.assert_called_once_with()
                self.assertEqual(gui_main_table_loader.ROUTINE_STATUS_STOPPED, window.routine_table.cellWidget(1, 1).findChild(QLabel, "routineInstanceStatusText").text())






                parent_rect = window.routine_table.visualItemRect(
                    window.routine_table.item(0, 0)
                )
                QTest.mouseClick(
                    window.routine_table.viewport(),
                    Qt.LeftButton,
                    pos=parent_rect.topLeft()
                    + QPoint(
                        gui_main_table_loader.ROUTINE_PARENT_EXPAND_OFFSET + 8,
                        parent_rect.height() // 2,
                    ),
                )
                self.app.processEvents()
                self.assertEqual(1, window.routine_table.rowCount())
                parent_rect = window.routine_table.visualItemRect(
                    window.routine_table.item(0, 0)
                )
                QTest.mouseClick(
                    window.routine_table.viewport(),
                    Qt.LeftButton,
                    pos=parent_rect.topLeft()
                    + QPoint(
                        gui_main_table_loader.ROUTINE_PARENT_EXPAND_OFFSET + 8,
                        parent_rect.height() // 2,
                    ),
                )
                self.app.processEvents()
                self.assertEqual(2, window.routine_table.rowCount())
                badge_texts = [
                    window._main_routine_valid_button.text(),
                    *[
                        button.text()
                        for button in window._main_routine_level_buttons.values()
                    ],
                    *[
                        button.text()
                        for button in window._main_routine_metric_buttons.values()
                    ],
                ]
                self.assertEqual(9, len(badge_texts))
                self.assertIsNotNone(
                    window.findChild(QWidget, "mainRoutineFilterBadgeArea")
                )
                self.assertIsNone(
                    window.findChild(QWidget, "routineDummyTabArea")
                )
                all_badges = [
                    *window._main_routine_metric_buttons.values(),
                    window._main_routine_initial_buy_sort_button,
                ]
                for button in all_badges:
                    self.assertEqual(64, button.width())
                    self.assertEqual(
                        gui_windows.AUTO_TRADE_SETTING_TOP_CONTROL_ROW_HEIGHT,
                        button.height(),
                    )
                self.assertEqual(
                    gui_windows.MAIN_ROUTINE_SUMMARY_VALID_BADGE_WIDTH,
                    window._main_routine_valid_button.width(),
                )
                self.assertEqual(
                    window._main_routine_summary_count_buttons["group"].height(),
                    window._main_routine_valid_button.height(),
                )
                self.assertIs(
                    window.findChild(QWidget, "mainRoutineSummary"),
                    window._main_routine_valid_button.parentWidget(),
                )
                self.assertIsNotNone(
                    window.findChild(QFrame, "mainRoutineValidSeparator")
                )
                separators = [
                    window.findChild(QFrame, "mainRoutineValidSeparator"),
                    window.findChild(QFrame, "mainRoutineInitialBuySeparator"),
                ]
                self.assertIsNone(
                    window.findChild(QFrame, "mainRoutineMetricSeparator")
                )
                self.assertTrue(all(separator is not None for separator in separators))
                self.assertTrue(
                    all(
                        separator.testAttribute(Qt.WA_TransparentForMouseEvents)
                        for separator in separators
                    )
                )
                for separator in separators:
                    self.assertEqual((52, 2), (separator.width(), separator.height()))
                    self.assertTrue(
                        separator.testAttribute(Qt.WA_StyledBackground)
                    )
                    self.assertIn("#64748B", separator.styleSheet())
                self.assertLess(
                    separators[0].geometry().bottom(),
                    window._main_routine_metric_buttons["holding"].geometry().top(),
                )
                for upper, separator, lower in (
                    (
                        window._main_routine_metric_buttons["limit"],
                        separators[1],
                        window._main_routine_initial_buy_sort_button,
                    ),
                ):
                    upper_gap = (
                        separator.geometry().top()
                        - upper.geometry().bottom()
                        - 1
                    )
                    lower_gap = (
                        lower.geometry().top()
                        - separator.geometry().bottom()
                        - 1
                    )
                    self.assertEqual(upper_gap, lower_gap)
                    self.assertGreaterEqual(upper_gap, 12)
                self.assertIn(
                    gui_windows.AUTO_TRADE_SETTING_BADGE_ACTIVE_COLOR,
                    window._main_routine_level_buttons["routine"].styleSheet(),
                )
                self.assertIn(
                    "color: " + gui_windows.MAIN_ROUTINE_BADGE_IDLE_TEXT_COLOR,
                    window._main_routine_valid_button.styleSheet(),
                )
                self.assertIn(
                    "color: " + gui_windows.MAIN_ROUTINE_BADGE_IDLE_TEXT_COLOR,
                    window._main_routine_level_buttons["group"].styleSheet(),
                )
                self.assertIn(
                    "border: 1px solid "
                    + gui_windows.AUTO_TRADE_SETTING_BADGE_INACTIVE_COLOR,
                    window._main_routine_level_buttons["group"].styleSheet(),
                )
                initial_buy_sort_button = (
                    window._main_routine_initial_buy_sort_button
                )
                self.assertEqual("금액", initial_buy_sort_button.text())
                self.assertFalse(initial_buy_sort_button.isEnabled())
                initial_buy_sort_button.click()
                self.assertEqual("", window._main_routine_initial_buy_sort_mode)
                self.assertEqual("금액", initial_buy_sort_button.text())

                window._main_routine_valid_button.click()
                self.app.processEvents()
                self.assertEqual(0, window.routine_table.rowCount())
                self.assertEqual("routine", window._main_routine_display_level)
                window._main_routine_valid_button.click()
                self.app.processEvents()
                self.assertEqual(2, window.routine_table.rowCount())

                window._main_routine_level_buttons["group"].click()
                self.app.processEvents()
                self.assertEqual(1, window.routine_table.rowCount())
                self.assertFalse(initial_buy_sort_button.isEnabled())
                window._main_routine_level_buttons["stock"].click()
                self.app.processEvents()
                self.assertEqual(2, window.routine_table.rowCount())
                self.assertTrue(initial_buy_sort_button.isEnabled())
                initial_buy_sort_button.click()
                self.app.processEvents()
                self.assertEqual(
                    "AMOUNT",
                    window._main_routine_initial_buy_sort_mode,
                )
                self.assertEqual("주수", initial_buy_sort_button.text())
                for metric in ("holding", "price", "profit", "trade", "limit"):
                    window._main_routine_metric_buttons[metric].click()
                    self.app.processEvents()
                    self.assertTrue(window._main_routine_metric_sort_active)
                    self.assertEqual(metric, window._main_routine_metric_sort_key)
                window._main_routine_level_buttons["group"].click()
                self.app.processEvents()
                self.assertFalse(window._main_routine_metric_sort_active)
                self.assertEqual("", window._main_routine_metric_sort_key)
                self.assertFalse(initial_buy_sort_button.isEnabled())
                self.assertEqual("주수", initial_buy_sort_button.text())
                for button in window._main_routine_metric_buttons.values():
                    self.assertFalse(button.isEnabled())
                    self.assertEqual(Qt.ArrowCursor, button.cursor().shape())
                    self.assertIn("#9CA3AF", button.styleSheet())
                window._main_routine_level_buttons["routine"].click()
                self.app.processEvents()
                self.assertEqual(
                    {"profit", "limit"},
                    {
                        metric
                        for metric, button in window._main_routine_metric_buttons.items()
                        if button.isEnabled()
                    },
                )
                for metric in ("holding", "price", "trade"):
                    button = window._main_routine_metric_buttons[metric]
                    self.assertFalse(button.isEnabled())
                    self.assertEqual(Qt.ArrowCursor, button.cursor().shape())
                    self.assertIn("#9CA3AF", button.styleSheet())
                return
            finally:
                window.close()
                window.deleteLater()
                self.app.processEvents()

    def test_main_routine_context_stock_register_uses_target_instance_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            rules_path = Path(temp_dir) / "instance-a" / "rules.json"
            rules_path.parent.mkdir()
            rules_path.write_text("{}", encoding="utf-8")
            instance = SimpleNamespace(
                instance_id="instance-a",
                definition_id="indicator-follow",
                display_name="루틴A",
                rules_path=rules_path,
            )
            definition = SimpleNamespace(
                definition_id="indicator-follow",
                display_name="지표추종매매",
                package_dir=Path(temp_dir) / "routine-package",
            )
            dialog = MagicMock()
            window = gui_windows.MainWindow.__new__(gui_windows.MainWindow)

            with (
                patch.object(gui_windows, "routine_instance_by_id", return_value=instance),
                patch.object(gui_windows, "routine_definition_by_id", return_value=definition),
                patch.object(
                    gui_windows,
                    "InstanceStockSearchRegisterDialog",
                    return_value=dialog,
                ) as dialog_factory,
            ):
                gui_windows.MainWindow.open_routine_instance_stock_register_from_main_table(
                    window,
                    "instance-a",
                )

            dialog_factory.assert_called_once()
            metadata = dialog_factory.call_args.kwargs["instance_metadata"]
            self.assertEqual("instance", metadata["row_kind"])
            self.assertEqual("instance-a", metadata["instance_id"])
            self.assertEqual("루틴A", metadata["instance_name"])
            self.assertEqual("indicator-follow", metadata["definition_id"])
            self.assertEqual("지표추종매매", metadata["definition_name"])
            self.assertEqual(str(rules_path.parent), metadata["instance_dir"])
            dialog.show.assert_called_once_with()

    def test_actual_main_window_renders_directional_profit_contract(self) -> None:
        definition = RoutineDefinitionRecord(
            definition_id="directional_fixture",
            display_name="?�익?�상검�?",
            package_dir=Path("fixture"),
            schema_version="1.0",
            version="1.0",
            routine_type="auto_trade",
            entry_file="routine.py",
            module_name="fixture",
            settings_ui="",
            default_rules_file="rules.json",
            package_enabled=True,
            source_name="fixture",
        )
        instances = [
            RoutineInstanceRecord(
                instance_id=f"directional-{name}",
                definition_id=definition.definition_id,
                display_name=display_name,
                source_routine_name=definition.display_name,
                persisted=True,
                source="PERSISTED",
                enabled=False,
                real_trade_allowed=False,
                rules_path=Path(f"{name}.json"),
                schema_version="1.0",
            )
            for name, display_name in (
                ("positive", "?�수 ?�스?�스"),
                ("negative", "?�수 ?�스?�스"),
                ("zero", "중립 ?�스?�스"),
            )
        ]
        counts = {
            "directional-positive": {
                "registered": 1,
                "running": 0,
                "stopped": 1,
                "error": 0,
                "consumed_amount": 0,
                "consumed_unknown": False,
                "profit_amount": 125000,
                "profit_cost_basis": 3846153.846,
                "profit_unknown": False,
                "stocks": [],
            },
            "directional-negative": {
                "registered": 1,
                "running": 0,
                "stopped": 1,
                "error": 0,
                "consumed_amount": 0,
                "consumed_unknown": False,
                "profit_amount": -48000,
                "profit_cost_basis": 3428571.429,
                "profit_unknown": False,
                "stocks": [],
            },
            "directional-zero": {
                "registered": 1,
                "running": 0,
                "stopped": 1,
                "error": 0,
                "consumed_amount": 0,
                "consumed_unknown": False,
                "profit_amount": 0,
                "profit_cost_basis": 0,
                "profit_unknown": False,
                "stocks": [],
            },
        }
        group = _main_group("손익방향검증")
        assigned_stocks = [
            _assigned_stock(
                instance.instance_id,
                group_name=group.name,
                code=f"{index:06d}",
                name=instance.instance_id,
            )
            for index, instance in enumerate(instances, start=1)
        ]
        for instance, stock in zip(instances, assigned_stocks):
            counts[instance.instance_id]["stocks"] = [stock]
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
            patch.object(gui_windows.MainWindow, "load_running_stock_table"),
            patch.object(
                gui_main_table_loader,
                "load_routine_definitions",
                return_value=[definition],
            ),
            patch.object(
                gui_main_table_loader,
                "load_persisted_routine_instances",
                return_value=instances,
            ),
            patch.object(
                gui_main_table_loader,
                "get_group_records",
                return_value=[group],
            ),
            patch.object(
                gui_main_table_loader,
                "_main_pnl_refresh_static_cache",
                return_value=_main_static_cache(
                    [definition], instances, assigned_stocks
                ),
            ),
            patch.object(
                gui_main_table_loader,
                "_instance_stock_counts",
                return_value=counts,
            ),
        ):
            window = gui_windows.MainWindow()
            try:
                gui_main_table_loader.main_load_routine_table(window)
                window._main_routine_level_buttons["routine"].click()
                window.resize(1280, 720)
                window.show()
                self.app.processEvents()

                expected = {
                    "directional-positive": (
                        "+125,000",
                        "+3.25%",
                        DIRECTIONAL_NEGATIVE_COLOR,
                    ),
                    "directional-negative": (
                        "-48,000",
                        "-1.40%",
                        DIRECTIONAL_POSITIVE_COLOR,
                    ),
                    "directional-zero": (
                        "0",
                        "0.00%",
                        DIRECTIONAL_NEUTRAL_COLOR,
                    ),
                }
                right_edges = set()
                rendered_instance_ids = set()
                for row in range(1, window.routine_table.rowCount()):
                    instance_id = str(
                        window.routine_table.item(row, 0).data(
                            gui_main_table_loader.ROUTINE_INSTANCE_ID_ROLE
                        )
                        or ""
                    )
                    amount, rate, color = expected[instance_id]
                    rendered_instance_ids.add(instance_id)
                    widget = window.routine_table.cellWidget(row, 1)
                    amount_label = widget.findChild(
                        QLabel,
                        "routineInstanceProfitAmount",
                    )
                    rate_label = widget.findChild(
                        QLabel,
                        "routineInstanceProfitRate",
                    )
                    self.assertEqual(amount, amount_label.text())
                    self.assertEqual(rate, rate_label.text())
                    self.assertIn(f"color: {color}", amount_label.styleSheet())
                    self.assertIn(f"color: {color}", rate_label.styleSheet())
                    right_edges.add(
                        rate_label.mapTo(widget, rate_label.rect().topRight()).x()
                    )
                self.assertEqual(set(expected), rendered_instance_ids)
                self.assertEqual(1, len(right_edges))

                screenshot_path = os.environ.get(
                    "MAIN_DIRECTIONAL_PROFIT_SCREENSHOT_PATH",
                    "",
                ).strip()
                if screenshot_path:
                    self.assertTrue(window.grab().save(screenshot_path))
            finally:
                window.close()
                window.deleteLater()
                self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
