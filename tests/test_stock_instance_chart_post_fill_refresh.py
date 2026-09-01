# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication, QDialog

import gui_stock_instance_chart_window as chart_window
import gui_windows
from gui_stock_instance_chart_window import StockInstanceChartWindow


TRADE_DATE = "2026-09-01"


def _projection(
    code: str = "005070",
    *,
    markers: list[dict] | None = None,
    processes: list[dict] | None = None,
    average_price: int | None = None,
) -> dict:
    return {
        "stock_code": code,
        "stock_name": "테스트",
        "trade_date": TRADE_DATE,
        "instance_id": "INSTANCE_1",
        "instance_name": "인스턴스",
        "bar_minutes": 1,
        "operation_title_display": "시간운영",
        "projection_status": "VALID",
        "candles": [
            {"bar_time": f"{TRADE_DATE}T09:00:00+09:00", "close": 40000},
            {"bar_time": f"{TRADE_DATE}T09:31:00+09:00", "close": 40300},
        ],
        "buy_signal_markers": [],
        "sell_signal_markers": [],
        "buy_signal_count": 0,
        "sell_signal_count": 0,
        "actual_fill_markers": deepcopy(markers or []),
        "execution_process_rails": deepcopy(processes or []),
        "average_price": average_price,
        "average_price_visible": average_price is not None,
        "pnl_available": False,
        "diagnostics": {"raw_candle_count": 2, "issues": []},
    }


def _marker(marker_id: str, cumulative: int, delta: int, second: int) -> dict:
    return {
        "marker_id": marker_id,
        "fill_id": marker_id,
        "side": "BUY",
        "filled_price": 40250,
        "filled_quantity": cumulative,
        "filled_quantity_delta": delta,
        "occurred_at": f"{TRADE_DATE}T09:30:{second:02d}+09:00",
        "execution_process_id": "PROCESS_1",
        "execution_time_source": "BROKER_FID_908",
        "execution_time_quality": "EXACT",
    }


def _process(*, filled_quantity: int = 10, status: str = "COMPLETED") -> dict:
    return {
        "execution_process_id": "PROCESS_1",
        "side": "BUY",
        "option_summary": "단일 주문",
        "status": status,
        "child_completed": 1 if status == "COMPLETED" else 0,
        "child_total": 1,
        "children": [
            {
                "child_sequence_index": 1,
                "child_sequence_total": 1,
                "child_kind": "SINGLE_ORDER",
                "status": status,
                "filled_quantity": filled_quantity,
                "fill_ids": ["F1", "F2", "F3"],
            }
        ],
    }


class RefreshProbe(QDialog):
    def __init__(self, stock_code: str) -> None:
        super().__init__()
        self.stock_code = stock_code
        self.refresh_count = 0
        self.preserve_flags: list[bool] = []
        self.show()

    def refresh_projection(self, *, preserve_pnl_if_same_bar: bool = False) -> None:
        self.refresh_count += 1
        self.preserve_flags.append(preserve_pnl_if_same_bar)


class PostFillChartRefreshTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        chart_window.clear_pending_stock_instance_chart_refreshes()
        chart_window._OPEN_STOCK_INSTANCE_CHARTS.clear()
        self.windows: list[QDialog] = []

    def tearDown(self) -> None:
        chart_window.clear_pending_stock_instance_chart_refreshes()
        chart_window._OPEN_STOCK_INSTANCE_CHARTS.clear()
        for window in self.windows:
            window.close()
            window.deleteLater()
        self.app.processEvents()

    def _probe(self, code: str) -> RefreshProbe:
        probe = RefreshProbe(code)
        self.windows.append(probe)
        chart_window._OPEN_STOCK_INSTANCE_CHARTS[code] = probe
        return probe

    def test_same_stock_burst_is_canonicalized_and_coalesced_once(self) -> None:
        probe = self._probe("005070")
        callbacks: list[object] = []
        with patch.object(
            chart_window.QTimer,
            "singleShot",
            side_effect=lambda _delay, callback: callbacks.append(callback),
        ):
            self.assertTrue(chart_window.queue_open_stock_instance_chart_refresh("A005070"))
            self.assertTrue(chart_window.queue_open_stock_instance_chart_refresh("005070"))
            self.assertTrue(chart_window.queue_open_stock_instance_chart_refresh("005070"))

        self.assertEqual(1, len(callbacks))
        self.assertEqual(0, probe.refresh_count)
        callbacks[0]()
        self.assertEqual(1, probe.refresh_count)
        self.assertEqual([True], probe.preserve_flags)
        self.assertEqual(["005070"], list(chart_window._OPEN_STOCK_INSTANCE_CHARTS))

    def test_closed_chart_is_not_queued_or_auto_opened(self) -> None:
        callbacks: list[object] = []
        with patch.object(
            chart_window.QTimer,
            "singleShot",
            side_effect=lambda _delay, callback: callbacks.append(callback),
        ):
            self.assertFalse(chart_window.queue_open_stock_instance_chart_refresh("005070"))
        self.assertEqual([], callbacks)
        self.assertEqual({}, chart_window._OPEN_STOCK_INSTANCE_CHARTS)

    def test_close_race_rechecks_visibility_at_drain(self) -> None:
        probe = self._probe("005070")
        callbacks: list[object] = []
        with patch.object(
            chart_window.QTimer,
            "singleShot",
            side_effect=lambda _delay, callback: callbacks.append(callback),
        ):
            chart_window.queue_open_stock_instance_chart_refresh("005070")
        probe.hide()
        callbacks[0]()
        self.assertEqual(0, probe.refresh_count)
        self.assertNotIn("005070", chart_window._OPEN_STOCK_INSTANCE_CHARTS)

    def test_shutdown_clear_invalidates_posted_callback(self) -> None:
        probe = self._probe("005070")
        callbacks: list[object] = []
        with patch.object(
            chart_window.QTimer,
            "singleShot",
            side_effect=lambda _delay, callback: callbacks.append(callback),
        ):
            chart_window.queue_open_stock_instance_chart_refresh("005070")
        chart_window.clear_pending_stock_instance_chart_refreshes()
        self.assertEqual(0, callbacks[0]())
        self.assertEqual(0, probe.refresh_count)

    def test_10_15_20_open_stocks_refresh_independently_once(self) -> None:
        for stock_count in (10, 15, 20):
            with self.subTest(stock_count=stock_count):
                chart_window.clear_pending_stock_instance_chart_refreshes()
                chart_window._OPEN_STOCK_INSTANCE_CHARTS.clear()
                probes = [self._probe(f"{index + 1:06d}") for index in range(stock_count)]
                callbacks: list[object] = []
                with patch.object(
                    chart_window.QTimer,
                    "singleShot",
                    side_effect=lambda _delay, callback: callbacks.append(callback),
                ):
                    for probe in probes:
                        chart_window.queue_open_stock_instance_chart_refresh(probe.stock_code)
                        chart_window.queue_open_stock_instance_chart_refresh(probe.stock_code)
                self.assertEqual(1, len(callbacks))
                self.assertEqual(stock_count, callbacks[0]())
                self.assertEqual([1] * stock_count, [probe.refresh_count for probe in probes])
                for probe in probes:
                    probe.hide()

    def test_20_stock_events_with_five_open_charts_refresh_only_five(self) -> None:
        open_codes = {f"{index + 1:06d}" for index in range(5)}
        probes = {code: self._probe(code) for code in open_codes}
        callbacks: list[object] = []
        with patch.object(
            chart_window.QTimer,
            "singleShot",
            side_effect=lambda _delay, callback: callbacks.append(callback),
        ):
            queued = [
                chart_window.queue_open_stock_instance_chart_refresh(f"{index + 1:06d}")
                for index in range(20)
            ]
        self.assertEqual(5, sum(queued))
        self.assertEqual(1, len(callbacks))
        self.assertEqual(5, callbacks[0]())
        self.assertTrue(all(probe.refresh_count == 1 for probe in probes.values()))

    def test_partial_fill_evidence_survives_one_coalesced_projection_refresh(self) -> None:
        state = {"projection": _projection()}
        provider_calls: list[str] = []

        def provider(code: str, _trade_date: str) -> dict:
            provider_calls.append(code)
            return deepcopy(state["projection"])

        window = StockInstanceChartWindow(
            "005070", TRADE_DATE, projection_provider=provider
        )
        window.show()
        self.windows.append(window)
        chart_window._OPEN_STOCK_INSTANCE_CHARTS["005070"] = window
        state["projection"] = _projection(
            markers=[
                _marker("F1", 3, 3, 1),
                _marker("F2", 5, 2, 2),
                _marker("F3", 10, 5, 3),
            ],
            processes=[_process()],
            average_price=40250,
        )
        callbacks: list[object] = []
        with patch.object(
            chart_window.QTimer,
            "singleShot",
            side_effect=lambda _delay, callback: callbacks.append(callback),
        ):
            for _ in range(3):
                chart_window.queue_open_stock_instance_chart_refresh("005070")
        callbacks[0]()

        self.assertEqual(2, len(provider_calls))
        self.assertEqual(
            [3, 2, 5],
            [item["filled_quantity_delta"] for item in window.chart.actual_fill_marker_records],
        )
        self.assertEqual(40250, window.chart.average_price)
        self.assertEqual(10, window.process_rail.processes[0]["children"][0]["filled_quantity"])

    def test_marker_and_process_selection_are_preserved_then_safely_cleared(self) -> None:
        marker = _marker("F1", 3, 3, 1)
        state = {
            "projection": _projection(
                markers=[marker], processes=[_process(filled_quantity=3, status="PARTIAL")]
            )
        }

        def provider(_code: str, _trade_date: str) -> dict:
            return deepcopy(state["projection"])

        window = StockInstanceChartWindow(
            "005070", TRADE_DATE, projection_provider=provider
        )
        self.windows.append(window)
        window.chart.selected_actual_fill_marker_id = "F1"
        window.chart.selected_execution_process_id = "PROCESS_1"
        window.process_rail.select_process("PROCESS_1")
        window._on_actual_fill_marker_selected(marker)

        window.refresh_projection(preserve_pnl_if_same_bar=True)
        self.assertEqual("F1", window.chart.selected_actual_fill_marker_id)
        self.assertEqual("PROCESS_1", window.chart.selected_execution_process_id)
        self.assertEqual("PROCESS_1", window.process_rail.selected_execution_process_id)
        self.assertTrue(window.fill_detail_label.isVisibleTo(window))

        state["projection"] = _projection(processes=[_process()])
        window.refresh_projection(preserve_pnl_if_same_bar=True)
        self.assertEqual("", window.chart.selected_actual_fill_marker_id)
        self.assertEqual("PROCESS_1", window.chart.selected_execution_process_id)
        self.assertEqual("PROCESS_1", window.process_rail.selected_execution_process_id)

        state["projection"] = _projection()
        window.refresh_projection(preserve_pnl_if_same_bar=True)
        self.assertEqual("", window.chart.selected_actual_fill_marker_id)
        self.assertEqual("", window.chart.selected_execution_process_id)
        self.assertEqual("", window.process_rail.selected_execution_process_id)
        self.assertFalse(window.fill_detail_label.isVisible())

    def test_main_window_queues_only_after_durable_fill_pipeline_returns(self) -> None:
        order: list[str] = []
        result = {
            "recorded": True,
            "stage": "chejan_record",
            "normalized_event": {"code": "005070"},
            "fill_result": {"fill_recorded": True, "fill_id": "F1"},
            "position_result": {"position_updated": True},
            "reconciliation_persisted": True,
        }
        main = type(
            "Main",
            (),
            {"auto_trade_setting_window": None, "_main_window_closing": False},
        )()

        def handle(*_args, **_kwargs):
            order.append("durable-writers-finished")
            return result

        with patch.object(gui_windows, "handle_kiwoom_raw_chejan_event", side_effect=handle), patch.object(
            gui_windows, "observe_owner_failure_transition"
        ), patch.object(
            gui_windows, "main_window_buffer_response_integration_ready", return_value=False
        ), patch.object(
            gui_windows,
            "queue_open_stock_instance_chart_refresh",
            side_effect=lambda code: order.append(f"chart-queued:{code}"),
        ):
            gui_windows.MainWindow.on_kiwoom_raw_chejan_received(main, {"gubun": "0"})

        self.assertEqual(
            ["durable-writers-finished", "chart-queued:005070"], order
        )

    def test_balance_evidence_queues_and_non_durable_result_does_not(self) -> None:
        main = type(
            "Main",
            (),
            {"auto_trade_setting_window": None, "_main_window_closing": False},
        )()
        results = [
            {"recorded": True, "holding_recorded": True, "code": "A005070"},
            {"recorded": False, "stage": "normalize"},
        ]
        with patch.object(
            gui_windows, "handle_kiwoom_raw_chejan_event", side_effect=results
        ), patch.object(
            gui_windows, "observe_owner_failure_transition"
        ), patch.object(
            gui_windows, "main_window_buffer_response_integration_ready", return_value=False
        ), patch.object(
            gui_windows, "queue_open_stock_instance_chart_refresh"
        ) as queue:
            gui_windows.MainWindow.on_kiwoom_raw_chejan_received(main, {"gubun": "1"})
            gui_windows.MainWindow.on_kiwoom_raw_chejan_received(main, {"gubun": "0"})
        queue.assert_called_once_with("A005070")


if __name__ == "__main__":
    unittest.main()
