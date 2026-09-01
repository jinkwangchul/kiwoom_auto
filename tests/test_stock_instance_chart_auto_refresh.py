# -*- coding: utf-8 -*-

from __future__ import annotations

import os
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QObject, pyqtSignal
from PyQt5.QtWidgets import QApplication, QDialog

import gui_auto_trade_timer
from gui_auto_trade_operation_host import AutoTradeOperationHost
import gui_stock_instance_chart_window as chart_window
from gui_stock_instance_chart_window import StockInstanceChartWindow


TODAY = "2026-08-10"


class CycleHost(QObject):
    operation_cycle_completed = pyqtSignal(dict)


class ChartApi(QObject):
    bar_committed = pyqtSignal(object)


class ChartOwner(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.operation_host = CycleHost(self)
        self.kiwoom_api = ChartApi(self)

    def main_monitoring_auto_trade_operation_host(self):
        return self.operation_host


def _projection(
    code: str,
    *,
    bar_minutes: int = 5,
    candle_count: int = 1,
    buy_count: int = 0,
    sell_count: int = 0,
    actual_orders: int = 0,
    status: str = "RUNNING",
    cumulative_pnl: float = 1_000.0,
    cumulative_return_rate: float = 1.0,
) -> dict[str, object]:
    candles = [
        {
            "bar_time": f"2026-08-10T09:{index * bar_minutes:02d}:00+09:00",
            "close": 100 + index,
        }
        for index in range(candle_count)
    ]
    buys = [
        {
            "signal_bar_time": candles[-1]["bar_time"],
            "signal_bar_close": candles[-1]["close"],
        }
        for _index in range(buy_count)
    ] if candles else []
    sells = [
        {
            "signal_bar_time": candles[-1]["bar_time"],
            "signal_bar_close": candles[-1]["close"],
        }
        for _index in range(sell_count)
    ] if candles else []
    return {
        "stock_code": code,
        "stock_name": f"종목{code}",
        "trade_date": TODAY,
        "instance_id": f"instance-{code}",
        "instance_name": f"루틴-{code}",
        "bar_minutes": bar_minutes,
        "operation_mode_display": "시간",
        "operation_title_display": "시간운영",
        "operation_start_time": "09:00:00",
        "operation_end_buy_time": "13:30:00",
        "operation_time": "09:00~13:30",
        "current_status": status,
        "current_status_display": "감시/대기" if status == "STOPPED" else "매수/매도",
        "cumulative_pnl": cumulative_pnl,
        "cumulative_return_rate": cumulative_return_rate,
        "pnl_available": True,
        "cumulative_return_available": True,
        "pnl_bar_time": candles[-1]["bar_time"] if candles else None,
        "candles": candles,
        "buy_signal_markers": buys,
        "sell_signal_markers": sells,
        "buy_signal_count": buy_count,
        "sell_signal_count": sell_count,
        "actual_order_count": actual_orders,
        "diagnostics": {
            "raw_candle_count": candle_count,
            "completed_candle_count": candle_count,
            "issues": [],
        },
    }


def _completed_result() -> dict[str, object]:
    return {
        "processed": True,
        "reason_code": "OPERATION_CYCLE_COMPLETED",
        "candle_refresh_result": {"completed": True},
        "signal_result": {},
    }


class StockInstanceChartAutoRefreshTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_today_cycle_and_refresh_method_share_projection_reload_path(self) -> None:
        owner = ChartOwner()
        initial = _projection("005930")
        automatic = _projection(
            "005930",
            candle_count=2,
            buy_count=1,
            sell_count=1,
            actual_orders=1,
            cumulative_pnl=2_000,
            cumulative_return_rate=2.0,
        )
        manual = _projection(
            "005930",
            candle_count=3,
            buy_count=2,
            sell_count=1,
            actual_orders=2,
        )
        with patch.object(chart_window, "_today_trade_date", return_value=TODAY), patch.object(
            chart_window,
            "project_stock_instance_day",
            side_effect=[initial, automatic, manual],
        ) as loader:
            window = StockInstanceChartWindow("005930", TODAY, owner)
            fixed_range = window.chart.fixed_time_range
            self.assertEqual("09:00", fixed_range[0].strftime("%H:%M"))
            self.assertEqual("15:30", fixed_range[1].strftime("%H:%M"))
            owner.operation_host.operation_cycle_completed.emit(_completed_result())
            self.assertEqual(fixed_range, window.chart.fixed_time_range)
            self.assertEqual(2, len(window.chart.close_series))
            self.assertEqual(1, len(window.chart.buy_series))
            self.assertEqual(1, len(window.chart.sell_series))
            self.assertEqual("+2,000(+2.00%)", window.info_labels["cumulative_pnl"].text())
            self.assertNotIn("refresh_button", vars(window))

            window.refresh_projection()
            self.assertEqual(3, len(window.chart.close_series))
            self.assertEqual(
                "005930 종목005930 / 루틴-005930 / 시간운영 / 5분봉 / 매수 2 / 매도 1",
                window.windowTitle(),
            )
            self.assertEqual(3, loader.call_count)
            self.assertTrue(
                all(call.args == ("005930", TODAY) for call in loader.call_args_list)
            )
            window.close()
        owner.close()

    def test_same_completed_bar_keeps_pnl_until_a_new_bar_is_added(self) -> None:
        for bar_minutes in (1, 5):
            with self.subTest(bar_minutes=bar_minutes):
                owner = ChartOwner()
                initial = _projection("005930", bar_minutes=bar_minutes, candle_count=2, cumulative_pnl=1_000)
                same_bar = _projection("005930", bar_minutes=bar_minutes, candle_count=2, cumulative_pnl=9_000)
                new_bar = _projection("005930", bar_minutes=bar_minutes, candle_count=3, cumulative_pnl=2_000)
                with patch.object(chart_window, "_today_trade_date", return_value=TODAY), patch.object(
                    chart_window,
                    "project_stock_instance_day",
                    side_effect=[initial, same_bar, new_bar],
                ):
                    window = StockInstanceChartWindow("005930", TODAY, owner)
                    owner.operation_host.operation_cycle_completed.emit(_completed_result())
                    self.assertEqual("+1,000(+1.00%)", window.info_labels["cumulative_pnl"].text())
                    owner.operation_host.operation_cycle_completed.emit(_completed_result())
                    self.assertEqual("+2,000(+1.00%)", window.info_labels["cumulative_pnl"].text())
                    window.close()
                owner.close()

    def test_past_date_is_not_subscribed_but_refresh_method_remains_available(self) -> None:
        owner = ChartOwner()
        with patch.object(chart_window, "_today_trade_date", return_value=TODAY), patch.object(
            chart_window,
            "project_stock_instance_day",
            return_value=_projection("005930"),
        ) as loader:
            window = StockInstanceChartWindow("005930", "2026-08-09", owner)
            self.assertFalse(window._operation_cycle_refresh_connected)
            self.assertFalse(window._bar_committed_refresh_connected)
            owner.operation_host.operation_cycle_completed.emit(_completed_result())
            self.assertEqual(1, loader.call_count)
            self.assertNotIn("refresh_button", vars(window))
            window.refresh_projection()
            self.assertEqual(2, loader.call_count)
            window.close()
        owner.close()

    def test_close_disconnects_cycle_callback(self) -> None:
        owner = ChartOwner()
        with patch.object(chart_window, "_today_trade_date", return_value=TODAY), patch.object(
            chart_window,
            "project_stock_instance_day",
            return_value=_projection("005930"),
        ) as loader:
            window = StockInstanceChartWindow("005930", TODAY, owner)
            self.assertEqual(
                1,
                owner.operation_host.receivers(
                    owner.operation_host.operation_cycle_completed
                ),
            )
            self.assertEqual(1, owner.kiwoom_api.receivers(owner.kiwoom_api.bar_committed))
            window.close()
            self.assertEqual(
                0,
                owner.operation_host.receivers(
                    owner.operation_host.operation_cycle_completed
                ),
            )
            self.assertEqual(0, owner.kiwoom_api.receivers(owner.kiwoom_api.bar_committed))
            owner.operation_host.operation_cycle_completed.emit(_completed_result())
            self.assertEqual(1, loader.call_count)
        owner.close()

    def test_matching_bar_committed_refreshes_current_chart_only(self) -> None:
        owner = ChartOwner()
        initial = _projection("005930", candle_count=1)
        updated = _projection("005930", candle_count=2)
        with patch.object(chart_window, "_today_trade_date", return_value=TODAY), patch.object(
            chart_window,
            "project_stock_instance_day",
            side_effect=[initial, updated],
        ) as loader:
            window = StockInstanceChartWindow("005930", TODAY, owner)
            owner.kiwoom_api.bar_committed.emit(
                {"event_type": "BAR_COMMITTED", "stock_code": "000660", "trade_date": TODAY}
            )
            owner.kiwoom_api.bar_committed.emit(
                {"event_type": "BAR_COMMITTED", "stock_code": "005930", "trade_date": "2026-08-09"}
            )
            self.assertEqual(1, loader.call_count)

            owner.kiwoom_api.bar_committed.emit(
                {"event_type": "BAR_COMMITTED", "stock_code": "005930", "trade_date": TODAY}
            )
            owner.kiwoom_api.bar_committed.emit(
                {"event_type": "BAR_COMMITTED", "stock_code": "005930", "trade_date": TODAY}
            )
            self.app.processEvents()
            self.assertEqual(2, loader.call_count)
            self.assertEqual(2, len(window.chart.close_series))
            window.close()

        owner.close()

    def test_bar_committed_rehydrates_chart_after_initial_not_ready(self) -> None:
        owner = ChartOwner()
        unavailable = _projection("005930", candle_count=0)
        unavailable["projection_status"] = "NOT_READY"
        hydrated = _projection("005930", candle_count=2)
        with patch.object(chart_window, "_today_trade_date", return_value=TODAY), patch.object(
            chart_window,
            "project_stock_instance_day",
            side_effect=[unavailable, hydrated],
        ):
            window = StockInstanceChartWindow("005930", TODAY, owner)
            self.assertEqual([], window.chart.close_series)
            owner.kiwoom_api.bar_committed.emit(
                {"event_type": "BAR_COMMITTED", "stock_code": "005930", "trade_date": TODAY}
            )
            self.app.processEvents()
            self.assertEqual(2, len(window.chart.close_series))
            self.assertEqual("VALID", window.last_refresh_status)
            window.close()

        owner.close()

    def test_empty_and_exception_refresh_preserve_last_valid_series(self) -> None:
        owner = ChartOwner()
        initial = _projection("005930", bar_minutes=1, candle_count=0)
        initial["candles"] = [
            {
                "bar_time": (
                    f"{TODAY}T{9 + index // 60:02d}:{index % 60:02d}:00+09:00"
                ),
                "close": 100 + index,
            }
            for index in range(100)
        ]
        initial["diagnostics"]["raw_candle_count"] = 100
        initial["diagnostics"]["completed_candle_count"] = 100
        empty = _projection("005930", bar_minutes=1, candle_count=0)
        empty["projection_status"] = "NO_DAY_DATA"
        outside_session = _projection("005930", bar_minutes=1, candle_count=0)
        outside_session["projection_status"] = "VALID"
        outside_session["candles"] = [
            {"bar_time": f"{TODAY}T08:55:00+09:00", "close": 99}
        ]
        replacement = _projection("005930", bar_minutes=1, candle_count=2)
        with patch.object(chart_window, "_today_trade_date", return_value=TODAY), patch.object(
            chart_window,
            "project_stock_instance_day",
            side_effect=[
                initial,
                empty,
                outside_session,
                RuntimeError("temporary read failure"),
                replacement,
            ],
        ):
            window = StockInstanceChartWindow("005930", TODAY, owner)
            original = list(window.chart.close_series)
            self.assertEqual(100, len(original))

            window.refresh_projection()
            self.assertEqual(original, window.chart.close_series)
            self.assertEqual("NO_DAY_DATA", window.last_refresh_status)
            self.assertIn("이전 그래프", window.notice_label.text())

            window.refresh_projection()
            self.assertEqual(original, window.chart.close_series)
            self.assertEqual("STALE_REJECTED", window.last_refresh_status)
            self.assertIn("그래프", window.notice_label.text())

            window.refresh_projection()
            self.assertEqual(original, window.chart.close_series)
            self.assertEqual("REFRESH_FAILED", window.last_refresh_status)
            self.assertIn("이전 그래프", window.notice_label.text())

            window.refresh_projection()
            self.assertEqual(2, len(window.chart.close_series))
            self.assertEqual(1, window.chart.timeframe_minutes)
            self.assertEqual("VALID", window.last_refresh_status)
            window.close()

        owner.close()

    def test_same_identity_structured_failure_preserves_last_valid_series(self) -> None:
        owner = ChartOwner()
        initial = _projection("005930", bar_minutes=5, candle_count=2)
        failed = _projection("005930", bar_minutes=5, candle_count=0)
        failed["projection_status"] = "REFRESH_FAILED"
        provider = Mock(side_effect=[initial, failed])

        with patch.object(chart_window, "_today_trade_date", return_value=TODAY):
            window = StockInstanceChartWindow(
                "005930",
                TODAY,
                owner,
                projection_provider=provider,
            )
            original = list(window.chart.close_series)
            original_identity = window._last_valid_projection_identity
            window.refresh_projection()

            self.assertEqual(original, window.chart.close_series)
            self.assertEqual(original_identity, window._last_valid_projection_identity)
            self.assertEqual("REFRESH_FAILED", window.last_refresh_status)
            window.close()

        owner.close()

    def test_cross_identity_unavailable_or_failure_clears_last_valid_series(self) -> None:
        cases = (
            (
                "trade_date",
                {"trade_date": "2026-08-11", "projection_status": "NO_DAY_DATA"},
            ),
            (
                "instance",
                {"instance_id": "instance-reassigned", "projection_status": "REFRESH_FAILED"},
            ),
            (
                "timeframe",
                {"bar_minutes": 15, "projection_status": "NOT_READY"},
            ),
            (
                "rules_unavailable",
                {
                    "instance_id": "",
                    "bar_minutes": None,
                    "projection_status": "RULES_UNAVAILABLE",
                },
            ),
        )
        for label, updates in cases:
            with self.subTest(identity=label):
                owner = ChartOwner()
                initial = _projection("005930", bar_minutes=5, candle_count=2)
                unavailable = _projection("005930", bar_minutes=5, candle_count=0)
                unavailable.update(updates)
                provider = Mock(side_effect=[initial, unavailable])

                with patch.object(chart_window, "_today_trade_date", return_value=TODAY):
                    window = StockInstanceChartWindow(
                        "005930",
                        TODAY,
                        owner,
                        projection_provider=provider,
                    )
                    self.assertTrue(window.chart.close_series)
                    window.refresh_projection()

                    self.assertEqual([], window.chart.close_series)
                    self.assertIsNone(window._last_valid_projection_identity)
                    self.assertEqual(updates["projection_status"], window.last_refresh_status)
                    window.close()

                owner.close()

    def test_provider_exception_after_requested_date_change_clears_series(self) -> None:
        owner = ChartOwner()
        provider = Mock(
            side_effect=[
                _projection("005930", bar_minutes=5, candle_count=2),
                RuntimeError("temporary read failure"),
            ]
        )
        with patch.object(chart_window, "_today_trade_date", return_value=TODAY):
            window = StockInstanceChartWindow(
                "005930",
                TODAY,
                owner,
                projection_provider=provider,
            )
            window.trade_date = "2026-08-11"
            window.refresh_projection()

            self.assertEqual([], window.chart.close_series)
            self.assertIsNone(window._last_valid_projection_identity)
            self.assertEqual("REFRESH_FAILED", window.last_refresh_status)
            window.close()

        owner.close()

    def test_bar_committed_same_identity_failure_preserves_last_valid_series(self) -> None:
        owner = ChartOwner()
        initial = _projection("005930", bar_minutes=5, candle_count=2)
        failed = _projection("005930", bar_minutes=5, candle_count=0)
        failed["projection_status"] = "REFRESH_FAILED"
        provider = Mock(side_effect=[initial, failed])

        with patch.object(chart_window, "_today_trade_date", return_value=TODAY):
            window = StockInstanceChartWindow(
                "005930",
                TODAY,
                owner,
                projection_provider=provider,
            )
            original = list(window.chart.close_series)
            owner.kiwoom_api.bar_committed.emit(
                {
                    "event_type": "BAR_COMMITTED",
                    "stock_code": "005930",
                    "trade_date": TODAY,
                }
            )
            self.app.processEvents()

            self.assertEqual(original, window.chart.close_series)
            self.assertEqual("REFRESH_FAILED", window.last_refresh_status)
            window.close()

        owner.close()

    def test_multiple_windows_refresh_independently_and_isolate_failure(self) -> None:
        owner = ChartOwner()
        calls: dict[str, int] = {"005930": 0, "000660": 0}

        def load(code: str, _trade_date: str):
            calls[code] += 1
            if code == "005930" and calls[code] == 2:
                raise ValueError("temporary projection read failure")
            return _projection(
                code,
                candle_count=calls[code],
                actual_orders=max(0, calls[code] - 1),
                status="STOPPED" if code == "000660" else "RUNNING",
            )

        with patch.object(chart_window, "_today_trade_date", return_value=TODAY), patch.object(
            chart_window,
            "project_stock_instance_day",
            side_effect=load,
        ):
            first = StockInstanceChartWindow("005930", TODAY, owner)
            nested_parent = QDialog(owner)
            second = StockInstanceChartWindow("000660", TODAY, nested_parent)
            owner.operation_host.operation_cycle_completed.emit(_completed_result())

            self.assertEqual({"005930": 2, "000660": 2}, calls)
            self.assertIn("이전 그래프", first.notice_label.text())
            self.assertEqual(1, len(first.chart.close_series))
            self.assertEqual(2, len(second.chart.close_series))
            self.assertNotIn("status", second.info_labels)
            self.assertEqual("", second.notice_label.text())
            first.close()
            second.close()
            nested_parent.close()
        owner.close()


class OperationCycleCompletionBoundaryTests(unittest.TestCase):
    def test_operation_host_defers_signal_until_async_completion(self) -> None:
        owner = QObject()
        host = AutoTradeOperationHost(owner)
        emitted: list[dict[str, object]] = []
        host.operation_cycle_completed.connect(emitted.append)
        deferred = {
            "processed": True,
            "reason_code": "OPERATION_CYCLE_COMPLETED",
            "signal_result": {"deferred_for_candle_refresh": True},
        }
        final = {
            "processed": True,
            "reason_code": "OPERATION_CYCLE_COMPLETED",
            "signal_result": {"orders_created": 1},
        }
        with patch.object(
            gui_auto_trade_timer,
            "auto_trade_run_operation_cycle",
            return_value=deferred,
        ):
            self.assertEqual(deferred, host.run_operation_cycle())
        self.assertEqual([], emitted)

        host.complete_deferred_operation_cycle(final)
        self.assertEqual([final], emitted)

    def test_async_candle_callback_notifies_with_final_signal_result(self) -> None:
        host = Mock()
        host.startup_recovery_session_ready.return_value = True
        host._last_time_policy_minute_key = ""
        host.recalculate_all_status_by_operation_policy.return_value = {
            "changed": 0,
            "failed": 0,
        }
        host.complete_deferred_operation_cycle = Mock()
        market_data_host = Mock()
        host.market_data_host.return_value = market_data_host
        callbacks: list[object] = []

        def begin_refresh(_minute_key, *, on_complete):
            callbacks.append(on_complete)
            return {
                "accepted": True,
                "completed": False,
                "reason_code": "CANDLE_REFRESH_STARTED",
            }

        with patch.object(
            gui_auto_trade_timer,
            "auto_trade_current_time_policy_minute_key",
            return_value="2026-08-10 10:00",
        ), patch.object(
            gui_auto_trade_timer,
            "auto_trade_continue_pending_close_liquidations",
            return_value={"processed": 0, "blocked": 0},
        ), patch.object(
            gui_auto_trade_timer,
            "auto_trade_continue_pending_manual_ats_liquidations",
            return_value={"processed": 0, "failed": 0},
        ), patch.object(
            gui_auto_trade_timer,
            "_process_pending_signal_pipeline",
            return_value={"orders_created": 1},
        ):
            market_data_host.refresh_operation_candles.side_effect = begin_refresh
            returned = gui_auto_trade_timer.auto_trade_run_operation_cycle(host)
            self.assertTrue(
                returned["signal_result"]["deferred_for_candle_refresh"]
            )
            host.complete_deferred_operation_cycle.assert_not_called()
            callbacks[0](
                {
                    "completed": True,
                    "reason_code": "CANDLE_REFRESH_COMPLETED",
                }
            )

        final = host.complete_deferred_operation_cycle.call_args.args[0]
        self.assertEqual("CANDLE_REFRESH_COMPLETED", final["candle_refresh_result"]["reason_code"])
        self.assertEqual(1, final["signal_result"]["orders_created"])


if __name__ == "__main__":
    unittest.main()
