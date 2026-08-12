# -*- coding: utf-8 -*-

from __future__ import annotations

import inspect
import os
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QRectF, Qt
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import QApplication

import gui_stock_instance_chart_window as chart_window
from gui_stock_instance_chart_window import (
    StockInstanceChartWindow,
    StockInstanceCloseChart,
    open_stock_instance_chart,
)


def _projection(
    *,
    bar_minutes: int = 5,
    candles: list[dict[str, object]] | None = None,
    buys: list[dict[str, object]] | None = None,
    sells: list[dict[str, object]] | None = None,
    actual_orders: int = 1,
    instance_id: str = "instance-1",
    raw_candle_count: int = 3,
    operation_mode: str = "CONTINUOUS",
    operation_start_time: str = "09:00:00",
    operation_end_time: str = "13:30:00",
    ats_session_ranges: list[dict[str, str]] | None = None,
    cumulative_pnl: float | None = 154_000.0,
    cumulative_return_rate: float | None = 3.25,
    pnl_available: bool = True,
    cumulative_return_available: bool = True,
) -> dict[str, object]:
    candle_rows = candles if candles is not None else [
        {"bar_time": "2026-08-10T09:00:00+09:00", "close": 100},
        {"bar_time": "2026-08-10T09:05:00+09:00", "close": 110},
        {"bar_time": "2026-08-10T09:10:00+09:00", "close": 105},
    ]
    buy_rows = buys if buys is not None else [
        {
            "signal_id": "BUY-1",
            "signal_bar_time": "2026-08-10T09:05:00+09:00",
            "signal_bar_close": 110,
        }
    ]
    sell_rows = sells if sells is not None else [
        {
            "signal_id": "SELL-1",
            "signal_bar_time": "2026-08-10T09:10:00+09:00",
            "signal_bar_close": 105,
        }
    ]
    return {
        "stock_code": "005930",
        "stock_name": "삼성전자",
        "trade_date": "2026-08-10",
        "instance_id": instance_id,
        "instance_name": "지표추종-A" if instance_id else "",
        "bar_minutes": bar_minutes,
        "operation_mode": operation_mode,
        "operation_mode_display": "시간" if operation_mode == "SCHEDULED" else "수동",
        "operation_title_display": (
            "시간운영"
            if operation_mode == "SCHEDULED"
            else "수동+ATS" if ats_session_ranges else "수동운영"
        ),
        "operation_start_time": operation_start_time,
        "operation_end_buy_time": operation_end_time,
        "operation_time": "09:00~13:30",
        "ats_session_ranges": ats_session_ranges or [],
        "current_status": "RUNNING",
        "current_status_display": "매수/매도",
        "cumulative_pnl": cumulative_pnl,
        "cumulative_return_rate": cumulative_return_rate,
        "pnl_available": pnl_available,
        "cumulative_return_available": cumulative_return_available,
        "pnl_bar_time": candle_rows[-1]["bar_time"] if candle_rows else None,
        "candles": candle_rows,
        "buy_signal_markers": buy_rows,
        "sell_signal_markers": sell_rows,
        "buy_signal_count": len(buy_rows),
        "sell_signal_count": len(sell_rows),
        "actual_order_count": actual_orders,
        "diagnostics": {
            "raw_candle_count": raw_candle_count,
            "completed_candle_count": len(candle_rows),
            "issues": [],
        },
    }


class StockInstanceChartWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _window(self, projected: object) -> StockInstanceChartWindow:
        with patch.object(
            chart_window,
            "project_stock_instance_day",
            return_value=projected,
        ):
            return StockInstanceChartWindow("005930", "2026-08-10")

    def test_chart_pnl_display_contract(self) -> None:
        cases = (
            (None, None, False, "0(0.00%)"),
            (0, 0, True, "0(0.00%)"),
            (12_500, 1.25, True, "+12,500(+1.25%)"),
            (-8_300, -0.83, True, "-8,300(-0.83%)"),
        )
        for amount, rate, available, expected in cases:
            with self.subTest(amount=amount, rate=rate, available=available):
                self.assertEqual(
                    expected,
                    chart_window.format_chart_pnl_display(
                        amount,
                        rate,
                        available=available,
                    ),
                )

    def test_chart_pnl_font_matches_stock_identity_across_refresh_states(self) -> None:
        window = self._window(_projection())
        window.show()
        self.app.processEvents()
        stock_label = window.info_labels["stock"]
        pnl_label = window.info_labels["cumulative_pnl"]

        def assert_matching_font() -> None:
            self.assertEqual(stock_label.font().pixelSize(), pnl_label.font().pixelSize())
            self.assertEqual(stock_label.font().weight(), pnl_label.font().weight())
            self.assertEqual(stock_label.fontMetrics().height(), pnl_label.fontMetrics().height())

        self.assertEqual(21, stock_label.font().pixelSize())
        assert_matching_font()
        for result in (
            {"available": False},
            {"available": True, "cumulative_profit": 12_500, "cumulative_rate": 1.25},
            {"available": True, "cumulative_profit": -8_300, "cumulative_rate": -0.83},
        ):
            window.apply_pnl_result(result)
            self.app.processEvents()
            assert_matching_font()
        window.close()

    def test_window_title_builder_keeps_stock_code_when_name_is_unavailable(self) -> None:
        self.assertEqual(
            "999999 / - / - / - / 매수 0 / 매도 0",
            chart_window._build_window_title(stock_code="999999"),
        )

    def test_normal_projection_draws_close_line_and_exact_buy_sell_positions(self) -> None:
        projected = _projection()
        window = self._window(projected)
        self.assertEqual(3, len(window.chart.close_series))
        self.assertEqual(1, len(window.chart.buy_series))
        self.assertEqual(1, len(window.chart.sell_series))

        plot = QRectF(70, 34, 600, 300)
        buy_position = window.chart.position_for(
            "2026-08-10T09:05:00+09:00",
            110,
            plot,
        )
        line_position = window.chart.position_for(
            projected["candles"][1]["bar_time"],
            projected["candles"][1]["close"],
            plot,
        )
        sell_position = window.chart.position_for(
            "2026-08-10T09:10:00+09:00",
            105,
            plot,
        )
        self.assertEqual(line_position, buy_position)
        self.assertLess(sell_position.x(), plot.right())

        pixmap = QPixmap(760, 420)
        window.chart.resize(pixmap.size())
        window.chart.render(pixmap)
        self.assertFalse(pixmap.isNull())
        window.close()

    def test_scheduled_and_continuous_use_regular_fixed_axis_and_ignore_buy_end(self) -> None:
        candles = [
            {"bar_time": "2026-08-10T08:50:00+09:00", "close": 90},
            {"bar_time": "2026-08-10T10:00:00+09:00", "close": 100},
            {"bar_time": "2026-08-10T13:40:00+09:00", "close": 110},
            {"bar_time": "2026-08-10T15:30:00+09:00", "close": 115},
            {"bar_time": "2026-08-10T15:40:00+09:00", "close": 120},
        ]
        buys = [
            {"signal_bar_time": "2026-08-10T10:00:00+09:00", "signal_bar_close": 100},
        ]
        sells = [
            {"signal_bar_time": "2026-08-10T13:40:00+09:00", "signal_bar_close": 110},
            {"signal_bar_time": "2026-08-10T15:40:00+09:00", "signal_bar_close": 120},
        ]
        for operation_mode in ("SCHEDULED", "CONTINUOUS"):
            with self.subTest(operation_mode=operation_mode):
                window = self._window(_projection(
                    candles=candles,
                    buys=buys,
                    sells=sells,
                    operation_mode=operation_mode,
                    operation_start_time="10:00:00",
                    operation_end_time="11:00:00",
                    raw_candle_count=len(candles),
                ))
                start, end = window.chart.fixed_time_range
                self.assertEqual("09:00", start.strftime("%H:%M"))
                self.assertEqual("15:30", end.strftime("%H:%M"))
                self.assertEqual(
                    ["10:00", "13:40", "15:30"],
                    [item[0].strftime("%H:%M") for item in window.chart.close_series],
                )
                self.assertEqual(["10:00"], [item[0].strftime("%H:%M") for item in window.chart.buy_series])
                self.assertEqual(["13:40"], [item[0].strftime("%H:%M") for item in window.chart.sell_series])
                window.close()

    def test_empty_chart_keeps_regular_axis_without_virtual_line(self) -> None:
        for operation_mode in ("SCHEDULED", "CONTINUOUS"):
            window = self._window(_projection(
                candles=[],
                buys=[],
                sells=[],
                operation_mode=operation_mode,
                raw_candle_count=0,
            ))
            labels = window.chart._x_axis_label_points(QRectF(70, 34, 600, 300))
            self.assertEqual("09:00", labels[0][0].strftime("%H:%M"))
            self.assertEqual("15:30", labels[-1][0].strftime("%H:%M"))
            self.assertEqual([], window.chart.close_series)
            self.assertEqual("표시할 기준봉 데이터가 없습니다.", window.chart.empty_message)
            window.close()

    def test_selected_ats_ranges_extend_regular_axis(self) -> None:
        cases = (
            ([], "09:00", "15:30"),
            ([{"start_time": "08:00:00", "end_time": "08:50:00"}], "08:00", "15:30"),
            ([{"start_time": "15:40:00", "end_time": "19:50:00"}], "09:00", "19:50"),
            ([
                {"start_time": "08:00:00", "end_time": "08:50:00"},
                {"start_time": "15:40:00", "end_time": "19:50:00"},
            ], "08:00", "19:50"),
        )
        for ranges, expected_start, expected_end in cases:
            with self.subTest(ranges=ranges):
                window = self._window(_projection(ats_session_ranges=ranges))
                start, end = window.chart.fixed_time_range
                self.assertEqual(expected_start, start.strftime("%H:%M"))
                self.assertEqual(expected_end, end.strftime("%H:%M"))
                window.close()

    def test_missing_timeframe_candles_are_not_connected_across_gap(self) -> None:
        candles = [
            {"bar_time": "2026-08-10T08:00:00+09:00", "close": 100},
            {"bar_time": "2026-08-10T08:05:00+09:00", "close": 101},
            {"bar_time": "2026-08-10T08:55:00+09:00", "close": 999},
            {"bar_time": "2026-08-10T09:00:00+09:00", "close": 102},
            {"bar_time": "2026-08-10T09:05:00+09:00", "close": 103},
        ]
        window = self._window(_projection(
            candles=candles,
            buys=[],
            sells=[],
            bar_minutes=5,
            ats_session_ranges=[{"start_time": "08:00:00", "end_time": "08:50:00"}],
            raw_candle_count=len(candles),
        ))
        self.assertEqual([2, 2], [len(segment) for segment in window.chart._line_segments()])
        self.assertNotIn(999.0, [close for _bar_time, close in window.chart.close_series])
        window.close()

    def test_signal_price_labels_use_signal_bar_close(self) -> None:
        self.assertEqual("72,500", StockInstanceCloseChart._signal_label_text(72_500.0))
        self.assertEqual("73,200", StockInstanceCloseChart._signal_label_text(73_200.0))

    def test_plot_axes_draw_only_left_and_bottom_edges(self) -> None:
        painter = Mock()
        plot = QRectF(92, 24, 914, 459)

        StockInstanceCloseChart._draw_plot_axes(
            painter,
            plot,
            chart_window.QColor("#9CA3AF"),
        )

        self.assertEqual(2, painter.drawLine.call_count)
        self.assertEqual(
            (chart_window.QPointF(plot.left(), plot.top()), chart_window.QPointF(plot.left(), plot.bottom())),
            painter.drawLine.call_args_list[0].args,
        )
        self.assertEqual(
            (chart_window.QPointF(plot.left(), plot.bottom()), chart_window.QPointF(plot.right(), plot.bottom())),
            painter.drawLine.call_args_list[1].args,
        )
        painter.drawRect.assert_not_called()

    def test_bar_minutes_in_title_and_all_top_projection_fields_are_displayed(self) -> None:
        for minutes in (1, 5, 15):
            with self.subTest(minutes=minutes):
                window = self._window(_projection(bar_minutes=minutes))
                self.assertEqual(
                    f"005930 삼성전자 / 지표추종-A / 수동운영 / {minutes}분봉 / 매수 1 / 매도 1",
                    window.windowTitle(),
                )
                self.assertEqual("005930 삼성전자", window.info_labels["stock"].text())
                self.assertEqual("+154,000(+3.25%)", window.info_labels["cumulative_pnl"].text())
                self.assertNotIn("operation_mode", window.info_labels)
                self.assertNotIn("operation_time", window.info_labels)
                self.assertEqual(
                    {"stock", "cumulative_pnl"},
                    set(window.info_labels),
                )
                self.assertTrue(
                    window.info_labels["stock"].alignment() & Qt.AlignHCenter
                )
                self.assertTrue(
                    window.info_labels["cumulative_pnl"].alignment()
                    & Qt.AlignHCenter
                )
                self.assertTrue(
                    all(
                        label.alignment() & Qt.AlignLeft
                        for label in window.operation_info_labels.values()
                    )
                )
                title_labels = [
                    label
                    for label in window.findChildren(chart_window.QLabel)
                    if label.objectName() == "stockInstanceChartInfoTitle"
                ]
                self.assertEqual([], title_labels)
                window.close()

    def test_compact_chart_layout_exposes_context_and_unclipped_price_margin(self) -> None:
        with patch.object(chart_window, "_today_trade_date", return_value="2026-08-10"):
            window = self._window(_projection())

        self.assertEqual(
            "005930 삼성전자 / 지표추종-A / 수동운영 / 5분봉 / 매수 1 / 매도 1",
            window.windowTitle(),
        )
        self.assertNotIn("오늘", window.windowTitle())
        self.assertNotIn("chart_title_label", vars(window))
        self.assertNotIn("trade_date", window.info_labels)
        self.assertNotIn("refresh_button", vars(window))
        self.assertNotIn("buy_first_signal_label", vars(window))
        self.assertNotIn("sell_first_signal_label", vars(window))
        self.assertNotIn("actual_order_count_label", vars(window))
        self.assertIn("font-size: 21px", window.styleSheet())
        self.assertIn("font-size: 17px", window.styleSheet())
        visible_texts = [label.text() for label in window.findChildren(chart_window.QLabel)]
        self.assertNotIn("종목명 / 코드", visible_texts)
        self.assertNotIn("루틴 / 인스턴스", visible_texts)
        self.assertNotIn("거래기준봉", visible_texts)
        self.assertNotIn("운영시간", visible_texts)
        self.assertNotIn("09:00~13:30", visible_texts)
        self.assertNotIn("━  종가", visible_texts)
        self.assertNotIn("●  매수신호", visible_texts)
        self.assertNotIn("●  매도신호", visible_texts)
        self.assertNotIn("2026-08-10", visible_texts)
        self.assertNotIn("종가 차트 (오늘)", visible_texts)
        self.assertFalse(any("운영방식" in text for text in visible_texts))
        self.assertNotIn("운영상태", visible_texts)
        self.assertNotIn("매수/매도", visible_texts)
        self.assertFalse(any("실제주문" in text for text in visible_texts))
        self.assertFalse(any("첫 신호" in text for text in visible_texts))
        window.chart.resize(760, 420)
        plot = window.chart._plot_rect()
        self.assertGreaterEqual(plot.left(), 90)
        self.assertGreaterEqual(window.chart.width() - plot.right(), 30)
        window.close()

    def test_window_title_operation_modes_and_cumulative_pnl_states(self) -> None:
        scheduled = self._window(_projection(operation_mode="SCHEDULED"))
        manual = self._window(_projection(operation_mode="CONTINUOUS"))
        ats = self._window(
            _projection(
                operation_mode="CONTINUOUS",
                ats_session_ranges=[{"key": "extra1", "start_time": "08:00:00", "end_time": "08:50:00"}],
            )
        )
        loss = self._window(
            _projection(cumulative_pnl=-38_000, cumulative_return_rate=-0.82)
        )
        unavailable = self._window(
            _projection(
                cumulative_pnl=None,
                cumulative_return_rate=None,
                pnl_available=False,
                cumulative_return_available=False,
            )
        )

        self.assertEqual("005930 삼성전자 / 지표추종-A / 시간운영 / 5분봉 / 매수 1 / 매도 1", scheduled.windowTitle())
        self.assertEqual("005930 삼성전자 / 지표추종-A / 수동운영 / 5분봉 / 매수 1 / 매도 1", manual.windowTitle())
        self.assertEqual("005930 삼성전자 / 지표추종-A / 수동+ATS / 5분봉 / 매수 1 / 매도 1", ats.windowTitle())
        self.assertIn(
            chart_window.BUY_COLOR.name(),
            scheduled.info_labels["cumulative_pnl"].styleSheet().lower(),
        )
        self.assertEqual("-38,000(-0.82%)", loss.info_labels["cumulative_pnl"].text())
        self.assertIn(
            chart_window.SELL_COLOR.name(),
            loss.info_labels["cumulative_pnl"].styleSheet().lower(),
        )
        self.assertEqual("0(0.00%)", unavailable.info_labels["cumulative_pnl"].text())
        for window in (scheduled, manual, ats, loss, unavailable):
            window.close()

    def test_available_amount_with_zero_denominator_displays_unavailable_rate(self) -> None:
        window = self._window(
            _projection(
                cumulative_pnl=0,
                cumulative_return_rate=None,
                pnl_available=True,
                cumulative_return_available=False,
            )
        )
        self.assertEqual("0(0.00%)", window.info_labels["cumulative_pnl"].text())
        window.show()
        self.app.processEvents()
        self.assertEqual(
            window.info_labels["stock"].font().pixelSize(),
            window.info_labels["cumulative_pnl"].font().pixelSize(),
        )
        self.assertIn(
            chart_window.DIRECTIONAL_NEUTRAL_COLOR,
            window.info_labels["cumulative_pnl"].styleSheet().lower(),
        )
        window.close()

    def test_projection_error_and_unavailable_live_result_use_numeric_zero(self) -> None:
        with patch.object(
            chart_window,
            "project_stock_instance_day",
            side_effect=RuntimeError("projection failed"),
        ):
            window = StockInstanceChartWindow("005930", "2026-08-10")

        self.assertEqual("0(0.00%)", window.info_labels["cumulative_pnl"].text())
        window.show()
        self.app.processEvents()
        self.assertEqual(
            window.info_labels["stock"].font().pixelSize(),
            window.info_labels["cumulative_pnl"].font().pixelSize(),
        )
        self.assertIn(
            chart_window.DIRECTIONAL_NEUTRAL_COLOR,
            window.info_labels["cumulative_pnl"].styleSheet().lower(),
        )
        window.apply_pnl_result({"available": False, "reason": "DISCONNECTED"})
        self.assertEqual("0(0.00%)", window.info_labels["cumulative_pnl"].text())
        window.close()

    def test_top_controls_and_chart_form_one_panel_without_footer(self) -> None:
        window = self._window(_projection())

        info_panel = window.findChild(chart_window.QFrame, "stockInstanceChartInfoPanel")
        chart_panel = window.findChild(chart_window.QFrame, "stockInstanceChartPanel")
        summary_panel = window.findChild(chart_window.QFrame, "stockInstanceChartSummaryPanel")

        self.assertEqual(0, window.layout().spacing())
        self.assertEqual(8, info_panel.layout().contentsMargins().top())
        self.assertEqual(8, info_panel.layout().contentsMargins().bottom())
        self.assertEqual(0, chart_panel.layout().contentsMargins().top())
        self.assertEqual(0, chart_panel.layout().contentsMargins().bottom())
        self.assertIsNone(summary_panel)
        self.assertTrue(info_panel.isAncestorOf(window.early_close_button))
        self.assertTrue(info_panel.isAncestorOf(window.immediate_liquidation_button))
        self.assertEqual("조기마감", window.early_close_button.text())
        self.assertEqual("즉시청산", window.immediate_liquidation_button.text())
        self.assertFalse(window.early_close_button.isEnabled())
        self.assertFalse(window.immediate_liquidation_button.isEnabled())
        self.assertIn("background: #FFFFFF", window.styleSheet())
        self.assertIn("border: none", window.styleSheet())
        self.assertIn("QFrame#stockInstanceChartOperationInfo", window.styleSheet())
        self.assertIn("border-radius: 3px", window.styleSheet())
        self.assertFalse(
            any(
                frame.frameShape() == chart_window.QFrame.VLine
                for frame in window.findChildren(chart_window.QFrame)
            )
        )
        self.assertEqual(820, window.minimumWidth())
        self.assertEqual(428, window.minimumHeight())
        self.assertEqual(window.minimumSize(), window.size())
        window.close()

    def test_signal_counts_move_to_title_without_order_summary(self) -> None:
        projected = _projection(actual_orders=7)
        projected["buy_signal_count"] = 4
        projected["sell_signal_count"] = 3
        window = self._window(projected)
        self.assertEqual(
            "005930 삼성전자 / 지표추종-A / 수동운영 / 5분봉 / 매수 4 / 매도 3",
            window.windowTitle(),
        )
        self.assertFalse(hasattr(window, "buy_count_label"))
        self.assertFalse(hasattr(window, "sell_count_label"))
        self.assertNotIn("actual_order_count_label", vars(window))
        window.close()

    def test_chart_early_close_is_always_routine_regardless_of_policy(self) -> None:
        adapter = Mock()
        with patch.object(
            StockInstanceChartWindow,
            "_build_stock_operation_adapter",
            return_value=adapter,
        ), patch.object(
            StockInstanceChartWindow,
            "_early_close_is_excluded",
            return_value=False,
        ):
            window = self._window(_projection())
            self.assertTrue(window.early_close_button.isEnabled())
            self.assertTrue(window.immediate_liquidation_button.isEnabled())
            with patch.object(window, "refresh_projection") as refresh:
                for configured_method in ("시장가", "현재가", "이월"):
                    with self.subTest(configured_method=configured_method), patch(
                        "gui_auto_trade_policy.operation_policy_section",
                        return_value={"method": configured_method},
                    ) as operation_policy_section:
                        window.early_close_button.click()
                        operation_policy_section.assert_not_called()

        self.assertEqual(
            ["루틴", "루틴", "루틴"],
            [item.args[0] for item in adapter.apply_selected_early_close.call_args_list],
        )
        for item in adapter.apply_selected_early_close.call_args_list:
            self.assertEqual("간이차트", item.kwargs["source"])
            self.assertFalse(item.kwargs["show_error_dialog"])
            self.assertFalse(item.kwargs["show_result_toast"])
        adapter.apply_selected_individual_liquidation_method.assert_not_called()
        self.assertEqual(3, refresh.call_count)
        window.close()

    def test_chart_immediate_liquidation_is_early_close_market(self) -> None:
        adapter = Mock()
        with patch.object(
            StockInstanceChartWindow,
            "_build_stock_operation_adapter",
            return_value=adapter,
        ), patch.object(
            StockInstanceChartWindow,
            "_early_close_is_excluded",
            return_value=False,
        ):
            window = self._window(_projection())
            with patch(
                "gui_auto_trade_policy.operation_policy_section",
                return_value={
                    "method": "현재가",
                    "minutes_before_regular_close": "37",
                },
            ) as operation_policy_section, patch.object(
                window,
                "refresh_projection",
            ) as refresh:
                window.immediate_liquidation_button.click()

        operation_policy_section.assert_not_called()
        adapter.apply_selected_early_close.assert_called_once_with(
            "시장가",
            source="간이차트",
            show_error_dialog=False,
            show_result_toast=False,
        )
        adapter.apply_selected_individual_liquidation_method.assert_not_called()
        refresh.assert_called_once_with()
        operation_source = inspect.getsource(
            StockInstanceChartWindow._run_stock_operation
        )
        self.assertNotIn("operation_policy_section", operation_source)
        self.assertNotIn("minutes_before_regular_close", operation_source)
        self.assertNotIn("individual_liquidation", operation_source)
        self.assertNotIn("SendOrder", operation_source)
        self.assertNotIn("send_order", operation_source)
        self.assertNotIn("event_journal", operation_source.lower())
        window.close()

    def test_operation_failure_uses_chart_toast_once(self) -> None:
        adapter = Mock()
        adapter.apply_selected_early_close.return_value = {
            "ok": False,
            "message": "키움 서버에 로그인되어 있지 않습니다.",
        }
        with patch.object(
            StockInstanceChartWindow,
            "_build_stock_operation_adapter",
            return_value=adapter,
        ), patch.object(
            StockInstanceChartWindow,
            "_early_close_is_excluded",
            return_value=False,
        ), patch("gui_toast.show_toast") as show_toast:
            window = self._window(_projection())
            window.immediate_liquidation_button.click()

        show_toast.assert_called_once_with(
            window,
            "키움 서버에 로그인되어 있지 않습니다.",
            duration_ms=2500,
        )
        window.close()

    def test_early_close_failure_uses_chart_toast_once(self) -> None:
        adapter = Mock()
        adapter.apply_selected_early_close.return_value = {
            "ok": False,
            "message": "조기마감 불가: 청산 진행 중",
        }
        with patch.object(
            StockInstanceChartWindow,
            "_build_stock_operation_adapter",
            return_value=adapter,
        ), patch.object(
            StockInstanceChartWindow,
            "_early_close_is_excluded",
            return_value=False,
        ), patch("gui_toast.show_toast") as show_toast:
            window = self._window(_projection())
            window.early_close_button.click()

        show_toast.assert_called_once_with(
            window,
            "조기마감 불가: 청산 진행 중",
            duration_ms=2500,
        )
        window.close()

    def test_operation_button_enabled_state_reuses_existing_menu_selection_rule(self) -> None:
        adapter = Mock()
        with patch.object(
            StockInstanceChartWindow,
            "_build_stock_operation_adapter",
            return_value=adapter,
        ), patch.object(
            StockInstanceChartWindow,
            "_early_close_is_excluded",
            return_value=True,
        ):
            window = self._window(_projection())

        self.assertFalse(window.early_close_button.isEnabled())
        self.assertTrue(window.immediate_liquidation_button.isEnabled())
        window._operation_command_in_progress = True
        window._run_stock_operation("early_close")
        adapter.apply_selected_early_close.assert_not_called()
        window.close()

    def test_zero_signals_and_orders_are_normal_empty_states(self) -> None:
        window = self._window(_projection(buys=[], sells=[], actual_orders=0))
        self.assertEqual([], window.chart.buy_series)
        self.assertEqual([], window.chart.sell_series)
        self.assertEqual(
            "005930 삼성전자 / 지표추종-A / 수동운영 / 5분봉 / 매수 0 / 매도 0",
            window.windowTitle(),
        )
        self.assertEqual("", window.notice_label.text())
        self.assertNotIn("notice_panel", vars(window))
        window.close()

    def test_no_candles_and_no_completed_today_bar_have_distinct_messages(self) -> None:
        no_candles = self._window(
            _projection(candles=[], buys=[], sells=[], actual_orders=0, raw_candle_count=0)
        )
        self.assertEqual("표시할 기준봉 데이터가 없습니다.", no_candles.notice_label.text())
        self.assertEqual("표시할 기준봉 데이터가 없습니다.", no_candles.chart.empty_message)
        self.assertTrue(no_candles.windowTitle().startswith("005930 삼성전자 /"))
        self.assertNotIn("notice_panel", vars(no_candles))
        no_candles.close()

        not_completed = self._window(
            _projection(candles=[], buys=[], sells=[], actual_orders=0, raw_candle_count=2)
        )
        self.assertEqual("아직 오늘 기준봉이 없습니다.", not_completed.notice_label.text())
        self.assertEqual("아직 오늘 기준봉이 없습니다.", not_completed.chart.empty_message)
        not_completed.close()

    def test_no_instance_and_malformed_projection_do_not_crash(self) -> None:
        no_instance = self._window(
            _projection(
                candles=[],
                buys=[],
                sells=[],
                actual_orders=0,
                instance_id="",
                raw_candle_count=0,
            )
        )
        self.assertEqual("배정된 활성 인스턴스가 없습니다.", no_instance.notice_label.text())
        self.assertEqual("배정된 활성 인스턴스가 없습니다.", no_instance.chart.empty_message)
        no_instance.close()

        malformed = self._window(
            {
                "stock_code": "005930",
                "instance_id": "instance-1",
                "candles": "broken",
                "buy_signal_markers": None,
                "sell_signal_markers": [{"signal_bar_time": "bad", "signal_bar_close": "bad"}],
                "diagnostics": {"issues": ["CANDLE_DATA_MALFORMED"]},
            }
        )
        self.assertEqual([], malformed.chart.close_series)
        self.assertIn("데이터 손상", malformed.notice_label.text())
        self.assertIn("데이터 손상", malformed.chart.empty_message)
        pixmap = QPixmap(760, 420)
        malformed.chart.resize(pixmap.size())
        malformed.chart.render(pixmap)
        malformed.close()

        with patch.object(
            chart_window,
            "_stock_name_from_repository",
            return_value="삼성전자",
        ):
            error_window = self._window(None)
        self.assertIn("조회 오류", error_window.notice_label.text())
        self.assertIn("조회 오류", error_window.chart.empty_message)
        self.assertEqual("09:00", error_window.chart.fixed_time_range[0].strftime("%H:%M"))
        self.assertEqual("15:30", error_window.chart.fixed_time_range[1].strftime("%H:%M"))
        self.assertEqual(
            "005930 삼성전자 / - / - / - / 매수 0 / 매도 0",
            error_window.windowTitle(),
        )
        error_window.close()

    def test_single_candle_and_duplicate_markers_render_safely(self) -> None:
        one_candle = [{"bar_time": "2026-08-10T09:00:00+09:00", "close": 100}]
        duplicate = [
            {
                "signal_bar_time": "2026-08-10T09:00:00+09:00",
                "signal_bar_close": 100,
            },
            {
                "signal_bar_time": "2026-08-10T09:00:00+09:00",
                "signal_bar_close": 100,
            },
        ]
        chart = StockInstanceCloseChart()
        chart.set_projection(one_candle, duplicate, duplicate)
        pixmap = QPixmap(760, 420)
        chart.resize(pixmap.size())
        chart.render(pixmap)
        point = chart.position_for("2026-08-10T09:00:00+09:00", 100)
        self.assertIsNotNone(point)
        chart.close()

    def test_marker_draws_circle_centered_on_canonical_coordinate(self) -> None:
        painter = Mock()
        point = chart_window.QPointF(17.5, 23.25)
        StockInstanceCloseChart._draw_marker(painter, point, chart_window.BUY_COLOR)

        ellipse_args = painter.drawEllipse.call_args.args
        self.assertEqual(point, ellipse_args[0])
        self.assertEqual((5.0, 5.0), ellipse_args[1:])
        painter.setBrush.assert_called_once_with(chart_window.BUY_COLOR)
        painter.drawPolygon.assert_not_called()

    def test_refresh_projection_requeries_and_replaces_all_values_without_button(self) -> None:
        first = _projection(actual_orders=0)
        second = _projection(bar_minutes=1, buys=[], sells=[], actual_orders=2)
        with patch.object(
            chart_window,
            "project_stock_instance_day",
            side_effect=[first, second],
        ) as loader:
            window = StockInstanceChartWindow("005930", "2026-08-10")
            self.assertNotIn("refresh_button", vars(window))
            window.refresh_projection()
        self.assertEqual(2, loader.call_count)
        loader.assert_called_with("005930", "2026-08-10")
        self.assertEqual(
            "005930 삼성전자 / 지표추종-A / 수동운영 / 1분봉 / 매수 0 / 매도 0",
            window.windowTitle(),
        )
        self.assertEqual([], window.chart.buy_series)
        window.close()

    def test_common_open_api_keeps_date_argument_and_defaults_to_today(self) -> None:
        with patch.object(
            chart_window,
            "project_stock_instance_day",
            return_value=_projection(),
        ) as loader:
            explicit = open_stock_instance_chart("005930", "2026-08-09")
            self.assertTrue(explicit.isVisible())
            loader.assert_called_with("005930", "2026-08-09")
            explicit.close()

        with patch.object(chart_window, "_today_trade_date", return_value="2026-08-10"), patch.object(
            chart_window,
            "project_stock_instance_day",
            return_value=_projection(),
        ) as loader:
            defaulted = open_stock_instance_chart("005930")
            loader.assert_called_with("005930", "2026-08-10")
            defaulted.close()


if __name__ == "__main__":
    unittest.main()
