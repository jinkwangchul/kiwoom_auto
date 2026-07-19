from __future__ import annotations

import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch
from PyQt5.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt5.QtGui import QMouseEvent
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QHeaderView, QLabel, QWidget

import gui_main_table_loader
from routine_instance_registry import RoutineDefinitionRecord, RoutineInstanceRecord
from gui_auto_trade_display import (
    ROUTINE_PROFIT_SIGNAL_COLORS,
    format_routine_buy_limit,
    format_routine_buy_limit_usage,
    format_routine_used_amount,
    routine_profit_signal,
)
from gui_main_table_loader import (
    routine_instance_buy_limit_text,
    routine_instance_consumed_text,
    routine_instance_profit_text,
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


@unittest.skipIf(
    getattr(QApplication, "__name__", "") == "_QtImportStub",
    "requires real PyQt widgets; the legacy GUI test module installed global stubs",
)
class MainRoutineMonitoringDisplayTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_routine_operation_confirmations_use_project_copy(self) -> None:
        import gui_windows

        for command, title, message in (
            ("EARLY_CLOSE", "조기마감", "조기마감을 적용합니다."),
            ("IMMEDIATE_LIQUIDATION", "즉시청산", "즉시청산을 적용합니다."),
        ):
            dialog = gui_windows._create_routine_operation_confirmation(
                None,
                command,
            )
            try:
                self.assertEqual(title, dialog.windowTitle())
                self.assertEqual(message, dialog.text())
                self.assertEqual("진행", dialog.button(gui_windows.QMessageBox.Yes).text())
                self.assertEqual("취소", dialog.button(gui_windows.QMessageBox.No).text())
                self.assertIs(
                    dialog.button(gui_windows.QMessageBox.No),
                    dialog.defaultButton(),
                )
            finally:
                dialog.close()

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

    def test_routine_instance_metric_formatting_contract(self) -> None:
        self.assertEqual(
            routine_instance_buy_limit_text(enabled=True, amount=2_000_000),
            "한도(2,000,000)",
        )
        self.assertEqual(
            routine_instance_buy_limit_text(enabled=False, amount=None),
            "한도(미사용)",
        )
        self.assertEqual(
            routine_instance_buy_limit_text(enabled=True, amount=0),
            "한도(확인 필요)",
        )
        self.assertEqual(
            routine_instance_consumed_text(
                consumed_amount=1_000_000,
                buy_limit_enabled=True,
                buy_limit_amount=2_000_000,
            ),
            "소모(1,000,000 / 50.0%)",
        )
        self.assertEqual(
            routine_instance_consumed_text(
                consumed_amount=1_000_000,
                buy_limit_enabled=False,
                buy_limit_amount=None,
            ),
            "소모(1,000,000 / -)",
        )
        self.assertEqual(
            routine_instance_consumed_text(
                consumed_amount=1_000_000,
                buy_limit_enabled=True,
                buy_limit_amount=0,
            ),
            "소모(1,000,000 / 확인 필요)",
        )
        self.assertEqual(
            routine_instance_profit_text(
                profit_amount=35_200,
                cost_basis=1_248_227,
            )[0],
            "수익(+35,200 / +2.82%)",
        )
        self.assertEqual(
            routine_instance_profit_text(
                profit_amount=-12_500,
                cost_basis=1_250_000,
            )[0],
            "수익(-12,500 / -1.00%)",
        )
        self.assertEqual(
            routine_instance_profit_text(profit_amount=0, cost_basis=0)[0],
            "수익(0 / 0.00%)",
        )
        self.assertEqual(
            routine_instance_profit_text(
                profit_amount=0,
                cost_basis=0,
                unknown=True,
            )[0],
            "수익(확인 필요 / 확인 필요)",
        )

    def test_instance_stock_counts_aggregate_instance_usage_and_profit(self) -> None:
        instance = RoutineInstanceRecord(
            instance_id="a52f539d-4f18-4ef6-b0cf-f471567982a1",
            definition_id="indicator_follow",
            display_name="대형주 추세형",
            source_routine_name="지표추종매매",
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
                if "assigned" in str(path):
                    return {"assigned_routine_instance_id": instance.instance_id}
                return {"assigned_routine_instance_id": "other-instance"}
            if name == "state.json":
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
                    {"stock_path": "stocks/assigned"},
                    {"stock_path": "stocks/other"},
                ],
            ),
            patch.object(gui_main_table_loader, "read_json_dict", side_effect=read_json),
        ):
            counts = gui_main_table_loader._instance_stock_counts()

        self.assertEqual(1, counts[instance.instance_id]["registered"])
        self.assertEqual(10_000, counts[instance.instance_id]["consumed_amount"])
        self.assertEqual(10_000, counts[instance.instance_id]["profit_cost_basis"])
        self.assertAlmostEqual(352, counts[instance.instance_id]["profit_amount"])
        self.assertFalse(counts[instance.instance_id]["consumed_unknown"])
        self.assertFalse(counts[instance.instance_id]["profit_unknown"])
        self.assertNotIn("other-instance", counts)

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
            "기본운영": "#2563EB",
            "즉시청산": "#DC2626",
            "조기마감": "#D97706",
            "매매완료": "#16A34A",
            "일부완료": "#7C3AED",
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
                running=4,
                stopped=1,
                error=0,
                enabled=True,
            )
            stamp = widget.findChild(QWidget, "routineInstanceStatusStamp")
            dot = widget.findChild(QLabel, "routineInstanceStatusDot")
            status_text = widget.findChild(QLabel, "routineInstanceStatusText")
            registered = widget.findChild(QLabel, "routineInstanceRegistered")
            running = widget.findChild(QLabel, "routineInstanceRunning")
            stopped = widget.findChild(QLabel, "routineInstanceStopped")
            error = widget.findChild(QLabel, "routineInstanceError")
            self.assertEqual("●", dot.text())
            self.assertEqual(status, status_text.text())
            self.assertEqual(108, stamp.width())
            self.assertEqual(22, stamp.height())
            self.assertIn(f"border: 1px solid {color}", stamp.styleSheet())
            self.assertIn(f"color: {color}", dot.styleSheet())
            self.assertIn(f"color: {color}", status_text.styleSheet())
            self.assertEqual("등록(4)", registered.text())
            self.assertEqual("실행(4)", running.text())
            self.assertEqual("정지(1)", stopped.text())
            self.assertEqual("오류(0)", error.text())
            self.assertEqual(
                gui_main_table_loader.routine_instance_grid_columns(widget.font())[
                    "registered"
                ],
                registered.width(),
            )
            separators = widget.findChildren(QLabel, "routineInstanceSeparator")
            self.assertEqual(7, len(separators))
            self.assertTrue(all(separator.text() == "|" for separator in separators))
        self.assertEqual(("", ""), gui_main_table_loader.routine_status_stamp_spec("UNKNOWN"))

    def test_routine_instance_grid_columns_keep_shared_x_axis(self) -> None:
        first = gui_main_table_loader.create_routine_instance_status_widget(
            "기본운영",
            registered=0,
            running=0,
            stopped=0,
            error=0,
            buy_limit_text="한도(미사용)",
            consumed_text="소모(0 / -)",
            profit_text="수익(0 / 0.00%)",
            enabled=True,
        )
        second = gui_main_table_loader.create_routine_instance_status_widget(
            "즉시청산",
            registered=125,
            running=120,
            stopped=5,
            error=2,
            buy_limit_text="한도(100,000,000)",
            consumed_text="소모(98,765,432 / 98.8%)",
            profit_text="수익(-1,250,000 / -12.50%)",
            profit_color="#2563EB",
            enabled=True,
        )
        first.show()
        second.show()
        self.app.processEvents()
        try:
            for object_name in (
                "routineInstanceRegistered",
                "routineInstanceRunning",
                "routineInstanceStopped",
                "routineInstanceError",
            ):
                self.assertEqual(
                    first.findChild(QLabel, object_name).x(),
                    second.findChild(QLabel, object_name).x(),
                )
            for object_name in (
                "routineInstanceBuyLimit",
                "routineInstanceConsumed",
                "routineInstanceProfit",
            ):
                self.assertEqual(
                    first.findChild(QWidget, object_name).x(),
                    second.findChild(QWidget, object_name).x(),
                )
            first_separators = first.findChildren(QLabel, "routineInstanceSeparator")
            second_separators = second.findChildren(QLabel, "routineInstanceSeparator")
            self.assertEqual(7, len(first_separators))
            self.assertEqual(7, len(second_separators))
            for first_separator, second_separator in zip(first_separators, second_separators):
                self.assertEqual(first_separator.x(), second_separator.x())
            column_widths = gui_main_table_loader.routine_instance_grid_columns(
                second.font()
            )
            for key, object_name in (
                ("registered", "routineInstanceRegistered"),
                ("running", "routineInstanceRunning"),
                ("stopped", "routineInstanceStopped"),
                ("error", "routineInstanceError"),
            ):
                label = second.findChild(QLabel, object_name)
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
            for key in ("registered", "running", "stopped", "error"):
                self.assertLessEqual(column_widths[key], 64)
            self.assertEqual(
                {gui_main_table_loader.routine_instance_separator_width(first.font())},
                {separator.width() for separator in first_separators},
            )
        finally:
            first.close()
            second.close()

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
            patch.object(
                gui_main_table_loader,
                "_instance_stock_counts",
                return_value={},
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
        self.assertEqual(
            [table.item(0, col).text() for col in range(10)],
            ["▼ 지표추종매매", "", "", "", "", "", "", "", "", ""],
        )
        self.assertEqual((1, 10), table.spans[(0, 0)])
        self.assertIsNone(
            table.item(0, 0).data(gui_main_table_loader.ROUTINE_CHILD_STATUS_ROLE)
        )
        self.assertEqual(
            "등록(0) | 실행(0) | 정지(0) | 오류(0)",
            table.item(0, 0).data(gui_main_table_loader.ROUTINE_PARENT_AGGREGATE_ROLE),
        )
        self.assertNotIn("총예산", [table.item(0, col).text() for col in range(10)])

        self.assertIsNone(table.cellWidget(0, 9))

    def test_routine_table_reload_removes_stale_child_cell_widgets(self) -> None:
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
            description="",
            buy_limit_enabled=False,
            buy_limit_amount=None,
            rules_path=Path("instance-rules.json"),
        )
        first_widget = FakeCellWidget()
        second_widget = FakeCellWidget()

        with (
            patch.object(gui_main_table_loader, "load_routine_definitions", return_value=[definition]),
            patch.object(gui_main_table_loader, "load_persisted_routine_instances", return_value=[instance]),
            patch.object(gui_main_table_loader, "_routine_stock_counts_from_base_stocks", return_value={}),
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

            window._collapsed_routine_definition_ids = {"indicator_follow"}
            gui_main_table_loader.main_load_routine_table(window)
            self.assertTrue(first_widget.deleted)
            self.assertEqual(1, table.row_count)
            self.assertIsNone(table.cellWidget(1, 1))
            self.assertIsNone(table.cellWidget(0, 1))

            window._collapsed_routine_definition_ids = set()
            gui_main_table_loader.main_load_routine_table(window)
            self.assertEqual(2, table.row_count)
            self.assertIs(second_widget, table.cellWidget(1, 1))

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
            patch.object(
                gui_main_table_loader,
                "_instance_stock_counts",
                return_value={
                    instance.instance_id: {
                        "registered": 2,
                        "running": 1,
                        "stopped": 1,
                        "error": 0,
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
            {instance.instance_id: 2},
            window._routine_assigned_stock_count_by_instance,
        )
        self.assertEqual(28, table.row_heights[0])
        self.assertEqual(28, table.row_heights[1])
        self.assertEqual("▼ 지표추종매매", table.item(0, 0).text())
        self.assertEqual("대형주 추세형", table.item(1, 0).text())
        self.assertEqual(Qt.Checked, table.item(0, 0).checkState())
        self.assertEqual(Qt.Checked, table.item(1, 0).checkState())
        self.assertEqual("", table.item(0, 1).text())
        self.assertEqual("", table.item(1, 1).text())
        self.assertEqual("", table.item(1, 2).text())
        self.assertEqual((1, 9), table.spans[(1, 1)])
        self.assertEqual(
            "기본운영",
            table.item(1, 0).data(
                gui_main_table_loader.ROUTINE_CHILD_STATUS_ROLE
            ),
        )
        self.assertEqual(
            "| 등록(2) | 실행(1) | 정지(1) | 오류(0)",
            table.item(1, 0).data(
                gui_main_table_loader.ROUTINE_CHILD_AGGREGATE_ROLE
            ),
        )
        status_widget = table.cellWidget(1, 1)
        self.assertIsNotNone(status_widget)
        stamp = status_widget.findChild(QWidget, "routineInstanceStatusStamp")
        dot = status_widget.findChild(QLabel, "routineInstanceStatusDot")
        status_text = status_widget.findChild(QLabel, "routineInstanceStatusText")
        registered = status_widget.findChild(QLabel, "routineInstanceRegistered")
        running = status_widget.findChild(QLabel, "routineInstanceRunning")
        stopped = status_widget.findChild(QLabel, "routineInstanceStopped")
        error = status_widget.findChild(QLabel, "routineInstanceError")
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
        self.assertEqual("●", dot.text())
        self.assertEqual("기본운영", status_text.text())
        self.assertEqual(108, stamp.width())
        self.assertEqual(22, stamp.height())
        self.assertIn("border: 1px solid #2563EB", stamp.styleSheet())
        self.assertIn("color: #2563EB", dot.styleSheet())
        self.assertEqual("등록(2)", registered.text())
        self.assertEqual("실행(1)", running.text())
        self.assertEqual("정지(1)", stopped.text())
        self.assertEqual("오류(0)", error.text())
        self.assertEqual("12,000,000", buy_limit_amount.text())
        self.assertEqual("7,843,650", consumed_amount.text())
        self.assertEqual("65.4%", consumed_rate.text())
        self.assertEqual("+35,200", profit_amount.text())
        self.assertEqual("+2.82%", profit_rate.text())
        self.assertIn("color: #DC2626", profit_amount.styleSheet())
        column_widths = gui_main_table_loader.routine_instance_grid_columns(
            status_widget.font()
        )
        for key, label in (
            ("registered", registered),
            ("running", running),
            ("stopped", stopped),
            ("error", error),
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
        self.assertEqual(7, len(separators))
        self.assertTrue(all(separator.text() == "|" for separator in separators))
        self.assertEqual(
            "등록(2) | 실행(1) | 정지(1) | 오류(0)",
            table.item(0, 0).data(
                gui_main_table_loader.ROUTINE_PARENT_AGGREGATE_ROLE
            ),
        )
        self.assertTrue(table.item(0, 0).flags() & Qt.ItemIsUserCheckable)
        self.assertTrue(table.item(1, 0).flags() & Qt.ItemIsUserCheckable)
        self.assertEqual("", table.item(0, 7).text())
        self.assertEqual("", table.item(1, 7).text())
        self.assertEqual("", table.item(0, 8).text())
        self.assertEqual("", table.item(0, 0).toolTip())
        self.assertEqual(
            "대형주 추세형\n\n대형주 중심의 보수적 추세 진입",
            table.item(1, 0).toolTip(),
        )

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
                self.assertEqual(
                    "기본운영",
                    window.routine_table.cellWidget(1, 1)
                    .findChild(QLabel, "routineInstanceStatusText")
                    .text(),
                )
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
                self.assertEqual(
                    "등록(0) | 실행(0) | 정지(0) | 오류(0)",
                    window.routine_table.item(0, 0).data(
                        gui_main_table_loader.ROUTINE_PARENT_AGGREGATE_ROLE
                    ),
                )
                self.assertEqual(Qt.CustomContextMenu, window.routine_table.contextMenuPolicy())
                self.assertFalse(window.grab().isNull())

                parent_index = window.routine_table.model().index(0, 0)
                parent_font = gui_windows._routine_parent_font(window.routine_table.font())
                self.assertAlmostEqual(
                    window.routine_table.font().pointSizeF() + 1.0,
                    parent_font.pointSizeF(),
                )
                self.assertEqual(
                    gui_main_table_loader.ROUTINE_INSTANCE_ROW_HEIGHT,
                    window.routine_table.rowHeight(0),
                )
                self.assertEqual(
                    window.routine_table.rowHeight(0),
                    window.routine_table.rowHeight(1),
                )
                self.assertEqual(
                    "▼ 지표추종매매",
                    window._routine_tree_item_delegate.display_text(
                        parent_index, window.routine_table
                    ),
                )
                parent_name_rect = window._routine_checkbox_controller._parent_name_rect(
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
                    window._routine_checkbox_controller.eventFilter(
                        window.routine_table.viewport(), event
                    )

                move_routine_pointer(parent_name_rect.center())
                self.app.processEvents()
                self.assertEqual(
                    definition.definition_id,
                    window.routine_table._hovered_routine_definition_id,
                )
                self.assertEqual(
                    "▼ 지표추종매매    등록(0) | 실행(0) | 정지(0) | 오류(0)",
                    window._routine_tree_item_delegate.display_text(
                        parent_index, window.routine_table
                    ),
                )

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
                self.assertEqual(
                    "▼ 지표추종매매",
                    window._routine_tree_item_delegate.display_text(
                        parent_index, window.routine_table
                    ),
                )

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
                parent_actions = [MagicMock(), MagicMock()]
                parent_menu.addAction.side_effect = parent_actions
                with (
                    patch.object(gui_windows, "QMenu", return_value=parent_menu),
                    patch.object(gui_windows, "routine_definition_by_id", return_value=definition),
                ):
                    window.open_routine_context_menu(parent_name_rect.center())
                self.assertEqual(
                    ["조기마감", "즉시청산"],
                    [call.args[0] for call in parent_menu.addAction.call_args_list],
                )
                for action in parent_actions:
                    action.setEnabled.assert_called_once_with(False)
                    action.setStatusTip.assert_called_once_with(
                        "등록된 종목이 없어 실행할 수 없습니다."
                    )

                with patch.object(gui_windows, "QMenu") as menu_factory:
                    window.open_routine_context_menu(
                        QPoint(parent_rect.right() - 4, parent_rect.center().y())
                    )
                    menu_factory.assert_not_called()

                child_rect = window.routine_table.visualItemRect(window.routine_table.item(1, 0))
                child_index = window.routine_table.model().index(1, 0)
                child_name_rect = window._routine_checkbox_controller._child_name_rect(
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
                    window._routine_checkbox_controller.eventFilter(
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
                blocked_points = (
                    QPoint(
                        child_rect.left()
                        + gui_main_table_loader.ROUTINE_CHILD_CHECKBOX_OFFSET
                        + 2,
                        child_rect.center().y(),
                    ),
                    QPoint(
                        min(child_rect.right() - 2, child_name_rect.right() + 6),
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
                )
                with patch.object(
                    window,
                    "open_routine_settings_from_main_table",
                ) as settings_open:
                    with patch.object(
                        window,
                        "start_routine_instance_name_edit",
                    ) as name_edit:
                        double_click_routine(child_name_rect.center())
                        name_edit.assert_called_once_with(1)
                        for blocked_point in blocked_points:
                            double_click_routine(blocked_point)
                        self.assertEqual(1, name_edit.call_count)
                    settings_open.assert_not_called()

                fake_menu = MagicMock()
                child_actions = [MagicMock(), MagicMock(), MagicMock()]
                fake_menu.addAction.side_effect = child_actions
                with (
                    patch.object(gui_windows, "QMenu", return_value=fake_menu),
                    patch.object(gui_windows, "routine_instance_by_id", return_value=instance),
                ):
                    window.open_routine_context_menu(child_rect.center())
                self.assertEqual(
                    ["설정변경", "조기마감", "즉시청산"],
                    [call.args[0] for call in fake_menu.addAction.call_args_list],
                )
                fake_menu.addSeparator.assert_called_once_with()
                child_actions[0].triggered.connect.assert_called_once()
                for action in child_actions[1:]:
                    action.setEnabled.assert_called_once_with(False)
                    action.setStatusTip.assert_called_once_with(
                        "등록된 종목이 없어 실행할 수 없습니다."
                    )

                window._routine_assigned_stock_count_by_instance[instance.instance_id] = 1
                active_parent_menu = MagicMock()
                active_parent_actions = [MagicMock(), MagicMock()]
                active_parent_menu.addAction.side_effect = active_parent_actions
                with (
                    patch.object(gui_windows, "QMenu", return_value=active_parent_menu),
                    patch.object(gui_windows, "routine_definition_by_id", return_value=definition),
                ):
                    window.open_routine_context_menu(parent_name_rect.center())
                for action in active_parent_actions:
                    action.setEnabled.assert_called_once_with(True)

                active_child_menu = MagicMock()
                active_child_actions = [MagicMock(), MagicMock(), MagicMock()]
                active_child_menu.addAction.side_effect = active_child_actions
                with (
                    patch.object(gui_windows, "QMenu", return_value=active_child_menu),
                    patch.object(gui_windows, "routine_instance_by_id", return_value=instance),
                ):
                    window.open_routine_context_menu(child_rect.center())
                self.assertEqual(
                    ["설정변경", "조기마감", "즉시청산"],
                    [call.args[0] for call in active_child_menu.addAction.call_args_list],
                )
                active_child_menu.addSeparator.assert_called_once_with()
                active_child_actions[0].triggered.connect.assert_called_once()
                for action in active_child_actions[1:]:
                    action.setEnabled.assert_called_once_with(True)

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
                        "EARLY_CLOSE",
                        "조기마감",
                    )
                request = command_service.apply.call_args.args[0]
                self.assertEqual("ROUTINE_INSTANCE", request.target_scope)
                self.assertEqual(instance.instance_id, request.target_id)
                self.assertEqual("EARLY_CLOSE", request.command)
                self.assertEqual(
                    "조기마감",
                    window.routine_table.cellWidget(1, 1)
                    .findChild(QLabel, "routineInstanceStatusText")
                    .text(),
                )

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
                        "IMMEDIATE_LIQUIDATION",
                        "즉시청산",
                    )
                request = command_service.apply.call_args.args[0]
                self.assertEqual("IMMEDIATE_LIQUIDATION", request.command)
                self.assertEqual(
                    "즉시청산",
                    window.routine_table.cellWidget(1, 1)
                    .findChild(QLabel, "routineInstanceStatusText")
                    .text(),
                )

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
                        "EARLY_CLOSE",
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
                        "EARLY_CLOSE",
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
                        "IMMEDIATE_LIQUIDATION",
                        "즉시청산",
                    )
                self.assertEqual(
                    ["IMMEDIATE_LIQUIDATION", "IMMEDIATE_LIQUIDATION"],
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
                        "IMMEDIATE_LIQUIDATION",
                        "즉시청산",
                    )
                service_factory.assert_not_called()
                window._routine_instance_selection[instance.instance_id] = True

                with patch.object(gui_windows, "routine_instance_by_id", return_value=instance):
                    self.assertTrue(
                        window.reflect_routine_completion_result(
                            instance.instance_id,
                            "매매완료",
                        )
                    )
                self.assertEqual(
                    "매매완료",
                    window.routine_table.cellWidget(1, 1)
                    .findChild(QLabel, "routineInstanceStatusText")
                    .text(),
                )
                with patch.object(window, "update_review_required_button_text") as review_refresh:
                    self.assertFalse(
                        window.reflect_routine_completion_result(
                            instance.instance_id,
                            "일부완료",
                            data_mismatch=True,
                        )
                    )
                review_refresh.assert_called_once_with()
                self.assertEqual(
                    "매매완료",
                    window.routine_table.cellWidget(1, 1)
                    .findChild(QLabel, "routineInstanceStatusText")
                    .text(),
                )

                window.routine_table.item(0, 0).setCheckState(Qt.Unchecked)
                self.app.processEvents()
                self.assertEqual(1, window.routine_table.rowCount())
                self.assertEqual(Qt.Unchecked, window.routine_table.item(0, 0).checkState())
                collapsed_parent_index = window.routine_table.model().index(0, 0)
                self.assertEqual(
                    "▶ 지표추종매매    등록(0) | 실행(0) | 정지(0) | 오류(0)",
                    window._routine_tree_item_delegate.display_text(
                        collapsed_parent_index, window.routine_table
                    ),
                )
                window.routine_table.item(0, 0).setCheckState(Qt.Checked)
                self.app.processEvents()
                self.assertEqual(2, window.routine_table.rowCount())
                self.assertEqual(Qt.Checked, window.routine_table.item(0, 0).checkState())
                self.assertEqual(
                    "매매완료",
                    window.routine_table.cellWidget(1, 1)
                    .findChild(QLabel, "routineInstanceStatusText")
                    .text(),
                )

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
                self.assertIsNone(window.routine_table.cellWidget(0, 9))
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
                self.assertIsNone(window.routine_table.cellWidget(1, 9))

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
                self.assertIsNone(window.routine_table.cellWidget(0, 9))
                self.assertIsNone(window.routine_table.cellWidget(1, 9))

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
                self.assertIsNone(window.routine_table.cellWidget(1, 9))
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
