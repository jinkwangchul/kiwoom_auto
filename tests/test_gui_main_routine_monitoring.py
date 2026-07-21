from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch
from PyQt5.QtCore import QEvent, QPoint, QPointF, QRect, Qt
from PyQt5.QtGui import QFont, QFontMetrics, QMouseEvent
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QHeaderView, QLabel, QLineEdit, QWidget

import gui_main_table_loader
import gui_windows
from routine_instance_registry import RoutineDefinitionRecord, RoutineInstanceRecord
from gui_auto_trade_display import (
    RatioMetricDisplay,
    ROUTINE_PROFIT_SIGNAL_COLORS,
    draw_limit_metric,
    draw_ratio_metric_display,
    draw_stock_position_metric_display,
    format_routine_buy_limit,
    format_routine_buy_limit_usage,
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
            "한도(미설정)",
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
            buy_limit_text="한도(미설정)",
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
            buy_limit_configured=True,
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

    def test_stock_position_metric_values_return_structured_slots(self) -> None:
        holding, price, profit, pending, profit_amount, profit_rate = stock_position_metric_values(
            holding_qty=120,
            avg_price=28750,
            current_price=29100,
            buy_pending_qty=10,
            sell_pending_qty=0,
        )

        self.assertIsInstance(holding, RatioMetricDisplay)
        self.assertEqual(("보유", "120주", "3,450,000"), (holding.label, holding.value1, holding.value2))
        self.assertEqual(("가격", "28,750", "29,100"), (price.label, price.value1, price.value2))
        self.assertEqual(("손익", "+42,000", "+1.22%"), (profit.label, profit.value1, profit.value2))
        self.assertEqual(("미체결", "10", "0"), (pending.label, pending.value1, pending.value2))
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
        self.assertEqual("0주 / 0", holding)
        self.assertEqual("- / -", price)
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
        self.assertEqual("| 120주 / 3,450,000", separated_holding)
        self.assertEqual("| 28,750 / 29,100", separated_price)
        self.assertEqual("| +42,000 / +1.22%", separated_profit)
        self.assertEqual("| 10 / 0", separated_pending)

    def test_empty_price_slots_are_center_aligned_independently(self) -> None:
        _, empty_price, *_ = stock_position_metric_values(
            holding_qty=0,
            avg_price=0,
            current_price=None,
        )
        self.assertEqual("-", empty_price.value1)
        self.assertEqual("-", empty_price.value2)
        self.assertEqual(Qt.AlignCenter | Qt.AlignVCenter, empty_price.value1_alignment)
        self.assertEqual(Qt.AlignCenter | Qt.AlignVCenter, empty_price.value2_alignment)
        self.assertEqual("9,999,999", empty_price.value1_sample)
        self.assertEqual("9,999,999", empty_price.value2_sample)

        _, mixed_price, *_ = stock_position_metric_values(
            holding_qty=1,
            avg_price=1234,
            current_price=None,
        )
        self.assertEqual("1,234", mixed_price.value1)
        self.assertEqual("-", mixed_price.value2)
        self.assertEqual(Qt.AlignRight | Qt.AlignVCenter, mixed_price.value1_alignment)
        self.assertEqual(Qt.AlignCenter | Qt.AlignVCenter, mixed_price.value2_alignment)

        _, right_only_price, *_ = stock_position_metric_values(
            holding_qty=0,
            avg_price=0,
            current_price=5678,
        )
        self.assertEqual("-", right_only_price.value1)
        self.assertEqual("5,678", right_only_price.value2)
        self.assertEqual(Qt.AlignCenter | Qt.AlignVCenter, right_only_price.value1_alignment)
        self.assertEqual(Qt.AlignRight | Qt.AlignVCenter, right_only_price.value2_alignment)

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
        self.assertEqual("-", draw_calls[1].args[-1])
        self.assertEqual(Qt.AlignCenter | Qt.AlignVCenter, draw_calls[1].args[-2])
        self.assertEqual(" / ", draw_calls[2].args[-1])
        self.assertEqual("-", draw_calls[3].args[-1])
        self.assertEqual(Qt.AlignCenter | Qt.AlignVCenter, draw_calls[3].args[-2])

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
            "보유(99999주 / 999,999,999)",
            "가격(9,999,999 / 9,999,999)",
            "손익(-99,999,999 / -00.00%)",
            "미체결(99 / 99)",
            "한도(999,999,999)",
            "소모(999,999,999 / 00.0%)",
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
                next_text_start - (separator_start + gui_windows.ROUTINE_STOCK_METRIC_SEPARATOR_WIDTH),
            )

        drawn_texts = [call.args[-1] for call in painter.drawText.call_args_list]
        for text in texts:
            self.assertNotIn(text, drawn_texts)
        self.assertEqual(len(texts) - 1, drawn_texts.count("|"))
        self.assertEqual(gui_windows.MAIN_STOCK_METRIC_SLOT_WIDTHS[: len(texts)], tuple(row[2] - row[1] for row in rows))

    def test_main_stock_metric_sequence_uses_max_text_slots(self) -> None:
        painter = MagicMock()
        painter.fontMetrics.return_value = QFontMetrics(QFont())
        actual_texts = [
            "\ubcf4\uc720(0\uc8fc / 0)",
            "\uac00\uaca9(- / -)",
            "\uc190\uc775(0 / 0.00%)",
            "\ubbf8\uccb4\uacb0(0 / 0)",
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
        preview_metric_rects, preview_separator_rects, preview_end_x = (
            gui_windows._routine_stock_metric_layout_rects(
                row_rect=row_rect,
                start_x=100,
                count=len(gui_windows.MAIN_STOCK_METRIC_MAX_TEXTS),
            )
        )
        actual_metric_rects, actual_separator_rects, actual_end_x = (
            gui_windows._routine_stock_metric_layout_rects(
                row_rect=row_rect,
                start_x=100,
                count=6,
            )
        )

        self.assertEqual(preview_metric_rects, actual_metric_rects)
        self.assertEqual(preview_separator_rects, actual_separator_rects)
        self.assertEqual(preview_end_x, actual_end_x)
        self.assertEqual(
            list(gui_windows.MAIN_STOCK_METRIC_SLOT_WIDTHS),
            [rect.width() for rect in actual_metric_rects],
        )
        self.assertEqual(
            [gui_windows.ROUTINE_STOCK_METRIC_SEPARATOR_WIDTH] * 5,
            [rect.width() for rect in actual_separator_rects],
        )

    def test_main_stock_metric_component_rects_are_text_independent(self) -> None:
        row_rect = QRect(0, 5, 1600, 24)
        metric_rects, _separator_rects, _end_x = gui_windows._routine_stock_metric_layout_rects(
            row_rect=row_rect,
            start_x=100,
            count=6,
        )
        metrics = QFontMetrics(QFont())
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
                    return [""] * 12
                return None

        class FakeTable:
            def visualRect(self, _index):
                return QRect(0, 7, 2400, 24)

            def font(self):
                return QFont()

        table = FakeTable()
        index = FakeIndex()
        controller = gui_windows._RoutineCheckBoxController.__new__(
            gui_windows._RoutineCheckBoxController
        )
        controller.table = table
        legacy_holding_rect = controller._stock_legacy_metric_rect(index, 6)
        expected_metric_rects, _separator_rects, _end_x = (
            gui_windows._routine_stock_metric_layout_rects(
                row_rect=table.visualRect(index),
                start_x=legacy_holding_rect.left()
                + gui_windows.ROUTINE_STOCK_METRIC_SEPARATOR_GAP,
                count=5,
            )
        )

        self.assertEqual(expected_metric_rects[4], controller._stock_metric_rect(index, 10))

    def test_stock_buy_limit_editor_rect_uses_limit_value_display_slot(self) -> None:
        class FakeIndex:
            def isValid(self):
                return True

            def data(self, role):
                if role == gui_main_table_loader.ROUTINE_STOCK_VALUES_ROLE:
                    return [""] * 12
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
        controller = gui_windows._RoutineCheckBoxController.__new__(
            gui_windows._RoutineCheckBoxController
        )
        controller.table = table
        window = gui_windows.MainWindow.__new__(gui_windows.MainWindow)
        window.routine_table = table
        window._routine_checkbox_controller = controller

        limit_rect = controller._stock_metric_rect(index, 10)
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
            "\uac00\uaca9(- / -)",
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
            "\uac00\uaca9(- / -)",
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
        self.assertEqual(
            ["보유", "가격", "손익", "미체결", None],
            [getattr(metric, "label", None) for metric in metrics],
        )
        self.assertEqual("120주", metrics[0].value1)
        self.assertEqual("3,450,000", metrics[0].value2)
        self.assertEqual("29,100", metrics[1].value2)
        self.assertEqual("gray", row["stock_profit_led"])
        self.assertEqual("한도(미설정)", row["stock_values"][10])
        self.assertEqual(11, len(row["stock_values"]))

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
        self.assertEqual("가격", price_metric.label)
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

        self.assertEqual("한도(10,000,000)", row["stock_values"][10])
        self.assertEqual("소모(3,450,000 / 34.5%)", row["stock_values"][11])
        self.assertEqual("소모", row["stock_metrics"][5].label)
        self.assertEqual(12, len(row["stock_values"]))

    def test_stock_buy_limit_config_writer_keeps_stock_limits_independent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            stock_a = root / "003550_LG" / "config.json"
            stock_b = root / "005930_삼성전자" / "config.json"
            stock_c = root / "006400_삼성SDI" / "config.json"
            stock_a.parent.mkdir()
            stock_b.parent.mkdir()
            stock_c.parent.mkdir()
            stock_a.write_text(json.dumps({"name": "LG"}, ensure_ascii=False), encoding="utf-8")
            stock_b.write_text(
                json.dumps({"name": "삼성전자"}, ensure_ascii=False),
                encoding="utf-8",
            )
            stock_c.write_text(
                json.dumps(
                    {
                        "name": "삼성SDI",
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

    def test_stock_buy_limit_editor_finish_writes_selected_stock_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            stock_a = root / "003550_LG" / "config.json"
            stock_b = root / "005930_삼성전자" / "config.json"
            stock_a.parent.mkdir()
            stock_b.parent.mkdir()
            stock_a.write_text(json.dumps({"name": "LG"}, ensure_ascii=False), encoding="utf-8")
            stock_b.write_text(
                json.dumps(
                    {
                        "name": "삼성전자",
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
                "한도(미설정)",
                value_width=90,
                hide_value=True,
            )
        )

        drawn_texts = [call_args.args[-1] for call_args in painter.drawText.call_args_list]
        self.assertIn("한도(", drawn_texts)
        self.assertIn(")", drawn_texts)
        self.assertNotIn("미설정", drawn_texts)

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
            "기본운영",
            registered=0,
            running=0,
            stopped=0,
            error=0,
            buy_limit_text="한도(미설정)",
            profit_text="수익(0 / 0.00%)",
            enabled=True,
        )
        try:
            amount_label = widget.findChild(QLabel, "routineInstanceBuyLimitAmount")
            self.assertIsNotNone(amount_label)
            self.assertEqual(Qt.AlignCenter | Qt.AlignVCenter, amount_label.alignment())
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
