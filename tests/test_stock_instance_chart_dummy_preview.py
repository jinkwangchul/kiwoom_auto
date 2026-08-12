# -*- coding: utf-8 -*-

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication

import candle_manager
import execution_queue_writer
import gui_stock_instance_chart_window as chart_window
from gui_stock_instance_chart_window import StockInstanceChartWindow
import routine_signal_queue
from tools.stock_instance_chart_dummy_preview import (
    PREVIEW_TRADE_DATE,
    build_dummy_stock_instance_day_projection,
    dummy_projection_provider,
)


class StockInstanceChartDummyPreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_fixture_has_requested_bars_markers_and_exact_coordinates(self) -> None:
        projected = build_dummy_stock_instance_day_projection()
        candles = projected["candles"]
        buys = projected["buy_signal_markers"]
        sells = projected["sell_signal_markers"]
        by_time = {candle["bar_time"]: candle for candle in candles}

        self.assertEqual(55, len(candles))
        self.assertEqual("09:00", candles[0]["bar_time"][11:16])
        self.assertEqual("13:30", candles[-1]["bar_time"][11:16])
        self.assertEqual(
            ["09:35", "10:25", "12:40"],
            [marker["signal_bar_time"][11:16] for marker in buys],
        )
        self.assertEqual(
            ["11:20", "13:20"],
            [marker["signal_bar_time"][11:16] for marker in sells],
        )
        for marker in buys + sells:
            candle = by_time[marker["signal_bar_time"]]
            self.assertEqual(candle["close"], marker["signal_bar_close"])
        self.assertLess(min(candle["close"] for candle in candles), 71_000)
        self.assertGreater(max(candle["close"] for candle in candles), 73_000)

    def test_injected_preview_renders_without_default_projection_or_writers(self) -> None:
        with patch.object(chart_window, "project_stock_instance_day") as production, patch.object(
            candle_manager,
            "save_candles",
        ) as candle_writer, patch.object(
            routine_signal_queue,
            "enqueue_routine_signal",
        ) as signal_writer, patch.object(
            execution_queue_writer,
            "mutate_order_queue",
        ) as order_writer:
            window = StockInstanceChartWindow(
                "005380",
                PREVIEW_TRADE_DATE,
                projection_provider=dummy_projection_provider,
            )

        production.assert_not_called()
        candle_writer.assert_not_called()
        signal_writer.assert_not_called()
        order_writer.assert_not_called()
        self.assertEqual(55, len(window.chart.close_series))
        self.assertEqual("09:00", window.chart.fixed_time_range[0].strftime("%H:%M"))
        self.assertEqual("15:30", window.chart.fixed_time_range[1].strftime("%H:%M"))
        self.assertEqual(3, len(window.chart.buy_series))
        self.assertEqual(2, len(window.chart.sell_series))
        self.assertEqual(
            "005380 현대차 / 지표추종매매 / 인스턴스 A / 시간운영 / 5분봉 / 매수 3 / 매도 2",
            window.windowTitle(),
        )
        self.assertNotIn("status", window.info_labels)
        self.assertEqual("+21,750(+1.14%)", window.info_labels["cumulative_pnl"].text())
        self.assertFalse(hasattr(window, "buy_count_label"))
        self.assertFalse(hasattr(window, "sell_count_label"))
        self.assertNotIn("actual_order_count_label", vars(window))
        self.assertNotIn("buy_first_signal_label", vars(window))
        self.assertNotIn("sell_first_signal_label", vars(window))
        self.assertEqual("", window.notice_label.text())
        self.assertNotIn("notice_panel", vars(window))
        window.close()

    def test_default_provider_and_empty_data_contract_remain_production(self) -> None:
        empty = {
            "stock_code": "005380",
            "stock_name": "현대차",
            "trade_date": PREVIEW_TRADE_DATE,
            "instance_id": "instance-real",
            "bar_minutes": 5,
            "candles": [],
            "buy_signal_markers": [],
            "sell_signal_markers": [],
            "buy_signal_count": 0,
            "sell_signal_count": 0,
            "actual_order_count": 0,
            "diagnostics": {
                "raw_candle_count": 0,
                "completed_candle_count": 0,
                "issues": [],
            },
        }
        with patch.object(
            chart_window,
            "project_stock_instance_day",
            return_value=empty,
        ) as production:
            window = StockInstanceChartWindow("005380", PREVIEW_TRADE_DATE)

        production.assert_called_once_with("005380", PREVIEW_TRADE_DATE)
        self.assertEqual([], window.chart.close_series)
        self.assertEqual(
            "표시할 기준봉 데이터가 없습니다.",
            window.notice_label.text(),
        )
        window.close()


if __name__ == "__main__":
    unittest.main()
