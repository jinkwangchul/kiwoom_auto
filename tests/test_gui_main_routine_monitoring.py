from __future__ import annotations

import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch
from PyQt5.QtCore import QPoint, Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

import gui_main_table_loader
from routine_instance_registry import RoutineDefinitionRecord, RoutineInstanceRecord
from gui_auto_trade_display import (
    ROUTINE_PROFIT_SIGNAL_COLORS,
    format_routine_buy_limit,
    format_routine_buy_limit_usage,
    format_routine_used_amount,
    routine_profit_signal,
)


class FakeRoutineTable:
    def __init__(self) -> None:
        self.row_count = 0
        self.items: dict[tuple[int, int], object] = {}
        self.widgets: dict[tuple[int, int], object] = {}

    def columnCount(self) -> int:
        return len(gui_main_table_loader.ROUTINE_MONITORING_HEADERS)

    def setRowCount(self, count: int) -> None:
        self.row_count = count

    def setItem(self, row: int, column: int, item: object) -> None:
        self.items[(row, column)] = item

    def setCellWidget(self, row: int, column: int, widget: object) -> None:
        self.widgets[(row, column)] = widget

    def item(self, row: int, column: int):
        return self.items[(row, column)]

    def cellWidget(self, row: int, column: int):
        return self.widgets.get((row, column))


@unittest.skipIf(
    getattr(QApplication, "__name__", "") == "_QtImportStub",
    "requires real PyQt widgets; the legacy GUI test module installed global stubs",
)
class MainRoutineMonitoringDisplayTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_used_amount_buy_limit_and_usage_rate_are_independent(self) -> None:
        self.assertEqual(format_routine_used_amount(7_843_650), "₩7,843,650")
        self.assertEqual(
            format_routine_buy_limit(enabled=True, amount=12_500_000),
            "₩12,500,000",
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
        self.assertNotEqual(format_routine_buy_limit(enabled=False), "₩0 (0%)")
        self.assertEqual(format_routine_used_amount(), "-")
        self.assertEqual(format_routine_buy_limit_usage(enabled=False), "-")

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

    def test_main_window_routine_headers_match_monitoring_contract(self) -> None:
        self.assertEqual(
            list(gui_main_table_loader.ROUTINE_MONITORING_HEADERS),
            [
                "루틴명",
                "상태",
                "등록",
                "실행",
                "정지",
                "오류",
                "사용금액",
                "매수한도",
                "사용률",
                "수익률",
            ],
        )

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
            display_name="지표추종매매",
            package_dir=Path("routine-path"),
            schema_version="1.0",
            version="0.1.0",
            routine_type="auto_trade",
            entry_file="routine.py",
            module_name="indicator_follow_routine",
            settings_ui="indicator_follow",
            default_rules_file="rules.json",
            package_enabled=True,
            source_name="지표추종매매",
        )

        with (
            patch.object(gui_main_table_loader, "load_routine_definitions", return_value=[definition]),
            patch.object(gui_main_table_loader, "load_persisted_routine_instances", return_value=[]),
            patch.object(
                gui_main_table_loader,
                "_routine_stock_counts_from_base_stocks",
                return_value={"지표추종매매": 3},
            ),
            patch.object(gui_main_table_loader, "_instance_stock_counts", return_value={}),
            patch.object(
                gui_main_table_loader,
                "create_routine_profit_signal_widget",
                return_value="profit-widget",
            ),
        ):
            gui_main_table_loader.main_load_routine_table(window)

        self.assertEqual(table.row_count, 1)
        self.assertEqual(table.columnCount(), 10)
        self.assertEqual(
            [table.item(0, col).text() for col in range(10)],
            ["▼ 지표추종매매", "", "", "", "", "", "", "", "", ""],
        )
        self.assertNotIn("총예산", [table.item(0, col).text() for col in range(10)])

        self.assertEqual(table.cellWidget(0, 9), "profit-widget")

        window._collapsed_routine_definition_ids.add("indicator_follow")
        with (
            patch.object(gui_main_table_loader, "load_routine_definitions", return_value=[definition]),
            patch.object(gui_main_table_loader, "load_persisted_routine_instances", return_value=[]),
            patch.object(
                gui_main_table_loader,
                "_routine_stock_counts_from_base_stocks",
                return_value={"지표추종매매": 3},
            ),
            patch.object(gui_main_table_loader, "_instance_stock_counts", return_value={}),
            patch.object(
                gui_main_table_loader,
                "create_routine_profit_signal_widget",
                return_value="profit-widget",
            ),
        ):
            gui_main_table_loader.main_load_routine_table(window)
        self.assertEqual(
            [table.item(0, col).text() for col in range(10)],
            ["▶ 지표추종매매", "", "3", "0", "3", "0", "-", "-", "-", "-"],
        )

    def test_parent_and_registered_instance_rows_show_independent_buy_limits(self) -> None:
        table = FakeRoutineTable()
        window = SimpleNamespace(
            routine_table=table,
            _main_routine_sort_column=-1,
            _main_routine_sort_order=0,
            _collapsed_routine_definition_ids=set(),
        )
        definition = RoutineDefinitionRecord(
            definition_id="indicator_follow",
            display_name="지표추종매매",
            package_dir=Path("routine-path"),
            schema_version="1.0",
            version="0.1.0",
            routine_type="auto_trade",
            entry_file="routine.py",
            module_name="indicator_follow_routine",
            settings_ui="indicator_follow",
            default_rules_file="rules.json",
            package_enabled=True,
            source_name="지표추종매매",
        )
        instance = RoutineInstanceRecord(
            instance_id="a52f539d-4f18-4ef6-b0cf-f471567982a1",
            definition_id="indicator_follow",
            display_name="대형주 추세형",
            source_routine_name="지표추종매매",
            persisted=True,
            source="PERSISTED",
            enabled=False,
            real_trade_allowed=False,
            description="대형주 중심의 보수적 추세 진입",
            buy_limit_enabled=True,
            buy_limit_amount=12_000_000,
            rules_path=Path("instance-rules.json"),
        )

        with (
            patch.object(gui_main_table_loader, "load_routine_definitions", return_value=[definition]),
            patch.object(gui_main_table_loader, "load_persisted_routine_instances", return_value=[instance]),
            patch.object(gui_main_table_loader, "_routine_stock_counts_from_base_stocks", return_value={}),
            patch.object(gui_main_table_loader, "_instance_stock_counts", return_value={}),
            patch.object(gui_main_table_loader, "create_routine_profit_signal_widget", return_value="profit-widget"),
        ):
            gui_main_table_loader.main_load_routine_table(window)

        self.assertEqual(2, table.row_count)
        self.assertEqual("▼ 지표추종매매", table.item(0, 0).text())
        self.assertEqual("대형주 추세형", table.item(1, 0).text())
        self.assertEqual(Qt.Checked, table.item(0, 0).checkState())
        self.assertEqual(Qt.Checked, table.item(1, 0).checkState())
        self.assertEqual("", table.item(0, 1).text())
        self.assertEqual("기본운영", table.item(1, 1).text())
        self.assertTrue(table.item(0, 0).flags() & Qt.ItemIsUserCheckable)
        self.assertTrue(table.item(1, 0).flags() & Qt.ItemIsUserCheckable)
        self.assertEqual("", table.item(0, 7).text())
        self.assertEqual("₩12,000,000", table.item(1, 7).text())
        self.assertEqual("", table.item(0, 8).text())
        self.assertEqual("", table.item(0, 0).toolTip())
        self.assertEqual("대형주 중심의 보수적 추세 진입", table.item(1, 0).toolTip())

        window._hovered_routine_definition_id = "indicator_follow"
        with (
            patch.object(gui_main_table_loader, "load_routine_definitions", return_value=[definition]),
            patch.object(gui_main_table_loader, "load_persisted_routine_instances", return_value=[instance]),
            patch.object(gui_main_table_loader, "_routine_stock_counts_from_base_stocks", return_value={}),
            patch.object(gui_main_table_loader, "_instance_stock_counts", return_value={}),
            patch.object(gui_main_table_loader, "create_routine_profit_signal_widget", return_value="profit-widget"),
        ):
            gui_main_table_loader.main_load_routine_table(window)
        self.assertEqual("기본운영", table.item(0, 1).text())
        self.assertEqual("₩12,000,000", table.item(0, 7).text())

    def test_actual_main_window_renders_and_toggles_parent_child_rows(self) -> None:
        import gui_windows

        definition = RoutineDefinitionRecord(
            definition_id="indicator_follow",
            display_name="지표추종매매",
            package_dir=Path("routine-path"),
            schema_version="1.0",
            version="0.1.0",
            routine_type="auto_trade",
            entry_file="routine.py",
            module_name="indicator_follow_routine",
            settings_ui="indicator_follow",
            default_rules_file="rules.json",
            package_enabled=True,
            source_name="지표추종매매",
        )
        instance = RoutineInstanceRecord(
            instance_id="a52f539d-4f18-4ef6-b0cf-f471567982a1",
            definition_id="indicator_follow",
            display_name="대형주 추세형",
            source_routine_name="지표추종매매",
            persisted=True,
            source="PERSISTED",
            enabled=False,
            real_trade_allowed=False,
            description="대형주 중심의 보수적 추세 진입",
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

                self.assertEqual(2, window.routine_table.rowCount())
                self.assertEqual("▼ 지표추종매매", window.routine_table.item(0, 0).text())
                self.assertEqual("대형주 추세형", window.routine_table.item(1, 0).text())
                self.assertEqual(Qt.Checked, window.routine_table.item(0, 0).checkState())
                self.assertEqual(Qt.Checked, window.routine_table.item(1, 0).checkState())
                self.assertEqual("", window.routine_table.item(0, 1).text())
                window.on_routine_cell_entered(0, 0)
                self.assertEqual("기본운영", window.routine_table.item(0, 1).text())
                window.on_routine_cell_entered(1, 0)
                self.assertEqual("", window.routine_table.item(0, 1).text())
                self.assertEqual("기본운영", window.routine_table.item(1, 1).text())
                self.assertEqual(Qt.CustomContextMenu, window.routine_table.contextMenuPolicy())
                self.assertFalse(window.grab().isNull())
                screenshot_path = os.environ.get("ROUTINE_UI_SCREENSHOT_PATH", "").strip()
                if screenshot_path:
                    self.assertTrue(window.grab().save(screenshot_path))

                parent_rect = window.routine_table.visualItemRect(window.routine_table.item(0, 0))
                fake_menu = MagicMock()
                fake_menu.addAction.side_effect = [MagicMock(), MagicMock()]
                with (
                    patch.object(gui_windows, "QMenu", return_value=fake_menu),
                    patch.object(gui_windows, "routine_definition_by_id", return_value=definition),
                ):
                    window.open_routine_context_menu(parent_rect.center())
                self.assertEqual(
                    ["조기마감", "즉시청산"],
                    [call.args[0] for call in fake_menu.addAction.call_args_list],
                )

                child_rect = window.routine_table.visualItemRect(window.routine_table.item(1, 0))
                with patch.object(gui_windows, "QMenu") as menu_factory:
                    window.open_routine_context_menu(child_rect.center())
                    menu_factory.assert_not_called()

                fake_result = SimpleNamespace(
                    status="SUCCESS",
                    stock_results=(SimpleNamespace(status="APPLIED"),),
                    error="",
                )
                command_service = MagicMock()
                command_service.apply.return_value = fake_result
                with (
                    patch.object(gui_windows, "OperationCommandService", return_value=command_service),
                    patch.object(gui_windows.QMessageBox, "question", return_value=gui_windows.QMessageBox.Yes),
                ):
                    window.request_routine_definition_operation(
                        definition.definition_id,
                        definition.display_name,
                        "EARLY_CLOSE",
                        "조기마감",
                    )
                request = command_service.apply.call_args.args[0]
                self.assertEqual("ROUTINE_INSTANCE", request.target_scope)
                self.assertEqual(instance.instance_id, request.target_id)
                self.assertEqual("EARLY_CLOSE", request.command)
                self.assertEqual("조기마감", window.routine_table.item(1, 1).text())

                command_service.reset_mock()
                command_service.apply.return_value = fake_result
                with (
                    patch.object(gui_windows, "OperationCommandService", return_value=command_service),
                    patch.object(gui_windows.QMessageBox, "question", return_value=gui_windows.QMessageBox.Yes),
                ):
                    window.request_routine_definition_operation(
                        definition.definition_id,
                        definition.display_name,
                        "IMMEDIATE_LIQUIDATION",
                        "즉시청산",
                    )
                request = command_service.apply.call_args.args[0]
                self.assertEqual("IMMEDIATE_LIQUIDATION", request.command)
                self.assertEqual("즉시청산", window.routine_table.item(1, 1).text())

                with patch.object(gui_windows, "routine_instance_by_id", return_value=instance):
                    self.assertTrue(
                        window.reflect_routine_completion_result(
                            instance.instance_id,
                            "매매완료",
                        )
                    )
                self.assertEqual("매매완료", window.routine_table.item(1, 1).text())
                with patch.object(window, "update_review_required_button_text") as review_refresh:
                    self.assertFalse(
                        window.reflect_routine_completion_result(
                            instance.instance_id,
                            "일부완료",
                            data_mismatch=True,
                        )
                    )
                review_refresh.assert_called_once_with()
                self.assertEqual("매매완료", window.routine_table.item(1, 1).text())

                window.routine_table.item(0, 0).setCheckState(Qt.Unchecked)
                self.app.processEvents()
                self.assertEqual(1, window.routine_table.rowCount())
                self.assertEqual(Qt.Unchecked, window.routine_table.item(0, 0).checkState())
                window.routine_table.item(0, 0).setCheckState(Qt.Checked)
                self.app.processEvents()
                self.assertEqual(2, window.routine_table.rowCount())
                self.assertEqual(Qt.Checked, window.routine_table.item(0, 0).checkState())
                self.assertEqual("매매완료", window.routine_table.item(1, 1).text())

                parent_rect = window.routine_table.visualItemRect(window.routine_table.item(0, 0))
                QTest.mouseClick(
                    window.routine_table.viewport(),
                    Qt.LeftButton,
                    pos=parent_rect.topLeft() + QPoint(70, parent_rect.height() // 2),
                )
                self.app.processEvents()
                self.assertEqual(2, window.routine_table.rowCount())
                self.assertEqual("▼ 지표추종매매", window.routine_table.item(0, 0).text())

                QTest.mouseClick(
                    window.routine_table.viewport(),
                    Qt.LeftButton,
                    pos=parent_rect.topLeft() + QPoint(8, parent_rect.height() // 2),
                )
                self.app.processEvents()
                self.assertEqual(1, window.routine_table.rowCount())
                self.assertEqual("▶ 지표추종매매", window.routine_table.item(0, 0).text())
                self.assertEqual(Qt.Unchecked, window.routine_table.item(0, 0).checkState())
                self.assertFalse(
                    window.routine_table.item(0, 0).data(
                        gui_main_table_loader.ROUTINE_CHECKBOX_VISUAL_ENABLED_ROLE
                    )
                )
                self.assertEqual("#9ca3af", window.routine_table.item(0, 0).foreground().color().name())
                for column in range(window.routine_table.columnCount()):
                    self.assertEqual(
                        "#9ca3af",
                        window.routine_table.item(0, column).foreground().color().name(),
                    )
                self.assertFalse(window.routine_table.cellWidget(0, 9).isEnabled())
                self.assertEqual((instance.instance_id,), window.selected_routine_instance_ids())
                disabled_screenshot_path = os.environ.get(
                    "ROUTINE_UI_DISABLED_SCREENSHOT_PATH",
                    "",
                ).strip()
                if disabled_screenshot_path:
                    self.assertTrue(window.grab().save(disabled_screenshot_path))

                parent_rect = window.routine_table.visualItemRect(window.routine_table.item(0, 0))
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
                self.assertEqual("▼ 지표추종매매", window.routine_table.item(0, 0).text())
                self.assertEqual(Qt.Unchecked, window.routine_table.item(0, 0).checkState())
                self.assertEqual(Qt.Checked, window.routine_table.item(1, 0).checkState())
                self.assertFalse(window.routine_table.cellWidget(1, 9).isEnabled())

                parent_rect = window.routine_table.visualItemRect(window.routine_table.item(0, 0))
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
                self.assertEqual("▶ 지표추종매매", window.routine_table.item(0, 0).text())
                self.assertEqual(Qt.Unchecked, window.routine_table.item(0, 0).checkState())

                parent_rect = window.routine_table.visualItemRect(window.routine_table.item(0, 0))
                QTest.mouseClick(
                    window.routine_table.viewport(),
                    Qt.LeftButton,
                    pos=parent_rect.topLeft() + QPoint(8, parent_rect.height() // 2),
                )
                self.app.processEvents()
                self.assertEqual(2, window.routine_table.rowCount())
                self.assertEqual("▼ 지표추종매매", window.routine_table.item(0, 0).text())
                self.assertEqual(Qt.Checked, window.routine_table.item(0, 0).checkState())
                self.assertEqual(Qt.Checked, window.routine_table.item(1, 0).checkState())
                self.assertTrue(window.routine_table.cellWidget(0, 9).isEnabled())
                self.assertTrue(window.routine_table.cellWidget(1, 9).isEnabled())

                child_rect = window.routine_table.visualItemRect(window.routine_table.item(1, 0))
                QTest.mouseClick(
                    window.routine_table.viewport(),
                    Qt.LeftButton,
                    pos=child_rect.topLeft()
                    + QPoint(
                        gui_main_table_loader.ROUTINE_CHILD_CHECKBOX_OFFSET + 8,
                        child_rect.height() // 2,
                    ),
                )
                self.app.processEvents()
                self.assertEqual(2, window.routine_table.rowCount())
                self.assertEqual("▼ 지표추종매매", window.routine_table.item(0, 0).text())
                self.assertEqual(Qt.Checked, window.routine_table.item(0, 0).checkState())
                self.assertEqual(Qt.Unchecked, window.routine_table.item(1, 0).checkState())
                self.assertNotEqual(
                    "#9ca3af",
                    window.routine_table.item(0, 0).foreground().color().name(),
                )
                for column in range(window.routine_table.columnCount()):
                    self.assertEqual(
                        "#9ca3af",
                        window.routine_table.item(1, column).foreground().color().name(),
                    )
                self.assertFalse(
                    window.routine_table.item(1, 0).data(
                        gui_main_table_loader.ROUTINE_CHECKBOX_VISUAL_ENABLED_ROLE
                    )
                )
                self.assertFalse(window.routine_table.cellWidget(1, 9).isEnabled())
                self.assertEqual((), window.selected_routine_instance_ids())

                child_rect = window.routine_table.visualItemRect(window.routine_table.item(1, 0))
                QTest.mouseClick(
                    window.routine_table.viewport(),
                    Qt.LeftButton,
                    pos=child_rect.topLeft()
                    + QPoint(
                        gui_main_table_loader.ROUTINE_CHILD_CHECKBOX_OFFSET + 8,
                        child_rect.height() // 2,
                    ),
                )
                self.app.processEvents()
                self.assertEqual(Qt.Checked, window.routine_table.item(1, 0).checkState())
                self.assertNotEqual(
                    "#9ca3af",
                    window.routine_table.item(1, 0).foreground().color().name(),
                )

                child_rect = window.routine_table.visualItemRect(window.routine_table.item(1, 0))
                QTest.mouseClick(
                    window.routine_table.viewport(),
                    Qt.LeftButton,
                    pos=child_rect.topLeft()
                    + QPoint(
                        gui_main_table_loader.ROUTINE_CHILD_CHECKBOX_OFFSET + 8,
                        child_rect.height() // 2,
                    ),
                )
                self.app.processEvents()
                self.assertEqual(Qt.Unchecked, window.routine_table.item(1, 0).checkState())

                parent_rect = window.routine_table.visualItemRect(window.routine_table.item(0, 0))
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
                self.assertEqual("▶ 지표추종매매", window.routine_table.item(0, 0).text())
                self.assertEqual(Qt.Checked, window.routine_table.item(0, 0).checkState())
                self.assertEqual((), window.selected_routine_instance_ids())

                parent_rect = window.routine_table.visualItemRect(window.routine_table.item(0, 0))
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
                self.assertEqual(Qt.Checked, window.routine_table.item(0, 0).checkState())
                self.assertEqual(Qt.Unchecked, window.routine_table.item(1, 0).checkState())

                parent_rect = window.routine_table.visualItemRect(window.routine_table.item(0, 0))
                QTest.mouseClick(
                    window.routine_table.viewport(),
                    Qt.LeftButton,
                    pos=parent_rect.topLeft() + QPoint(8, parent_rect.height() // 2),
                )
                self.app.processEvents()
                self.assertEqual(1, window.routine_table.rowCount())
                self.assertEqual("▶ 지표추종매매", window.routine_table.item(0, 0).text())
                self.assertEqual(Qt.Unchecked, window.routine_table.item(0, 0).checkState())
                self.assertEqual((), window.selected_routine_instance_ids())

                parent_rect = window.routine_table.visualItemRect(window.routine_table.item(0, 0))
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
                self.assertEqual("▼ 지표추종매매", window.routine_table.item(0, 0).text())
                self.assertEqual(Qt.Unchecked, window.routine_table.item(0, 0).checkState())
                self.assertEqual(Qt.Unchecked, window.routine_table.item(1, 0).checkState())

                parent_rect = window.routine_table.visualItemRect(window.routine_table.item(0, 0))
                QTest.mouseClick(
                    window.routine_table.viewport(),
                    Qt.LeftButton,
                    pos=parent_rect.topLeft() + QPoint(8, parent_rect.height() // 2),
                )
                self.app.processEvents()
                self.assertEqual(2, window.routine_table.rowCount())
                self.assertEqual(Qt.Checked, window.routine_table.item(0, 0).checkState())
                self.assertEqual(Qt.Unchecked, window.routine_table.item(1, 0).checkState())
            finally:
                window.close()
                window.deleteLater()
                self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
