from __future__ import annotations

import builtins
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PyQt5.QtCore import QObject, pyqtSignal
from PyQt5.QtWidgets import QApplication, QDialog

import gui_stock_instance_chart_window as chart_window
from gui_stock_instance_chart_window import StockInstanceChartWindow


TODAY = "2026-08-24"


def _projection(stock_code: str = "005930", trade_date: str = TODAY):
    return {
        "stock_code": stock_code,
        "stock_name": "삼성전자",
        "trade_date": trade_date,
        "instance_id": "instance-a",
        "instance_name": "지표추종A",
        "bar_minutes": 5,
        "operation_title_display": "시간운영",
        "candles": [
            {"bar_time": f"{trade_date}T09:00:00+09:00", "close": 70000},
            {"bar_time": f"{trade_date}T09:05:00+09:00", "close": 70100},
            {"bar_time": f"{trade_date}T09:10:00+09:00", "close": 70200},
        ],
        "buy_signal_markers": [],
        "sell_signal_markers": [],
        "buy_signal_count": 0,
        "sell_signal_count": 0,
        "pnl_available": False,
        "diagnostics": {"issues": []},
    }


class _LiveHost(QObject):
    operation_cycle_completed = pyqtSignal(dict)
    high_resolution_price_observed = pyqtSignal(object)

    def __init__(self) -> None:
        super().__init__()
        self.gate_enabled = False
        self.states: dict[str, object] = {}
        self.snapshot = SimpleNamespace(
            broker_connected=True,
            connection_epoch=7,
            login_session_id="SESSION-7",
        )
        self.CommRqData = Mock()
        self.SetRealReg = Mock()
        self.SetRealRemove = Mock()

    def price_signal_observation_enabled(self) -> bool:
        return self.gate_enabled

    def high_resolution_market_state(self, stock_code: str):
        return self.states.get(str(stock_code))

    def high_resolution_market_data_snapshot(self):
        return self.snapshot


class _Owner(QDialog):
    def __init__(self, host: _LiveHost) -> None:
        super().__init__()
        self.host = host

    def main_monitoring_auto_trade_operation_host(self):
        return self.host


def _state(
    stock_code: str = "005930",
    *,
    price: int = 70350,
    epoch: int = 7,
    session_id: str = "SESSION-7",
    quality: str = "NORMAL",
):
    return SimpleNamespace(
        stock_code=stock_code,
        connection_epoch=epoch,
        login_session_id=session_id,
        last_market_datetime=f"{TODAY}T09:13:27+09:00",
        last_price=price,
        data_quality=quality,
    )


class StockInstanceChartLivePriceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.host = _LiveHost()
        self.owner = _Owner(self.host)
        self.provider = Mock(side_effect=lambda code, date: _projection(code, date))

    def tearDown(self) -> None:
        self.owner.close()

    def _window(self, *, trade_date: str = TODAY) -> StockInstanceChartWindow:
        with patch.object(chart_window, "_today_trade_date", return_value=TODAY):
            window = StockInstanceChartWindow(
                "005930",
                trade_date,
                self.owner,
                projection_provider=self.provider,
            )
        if window._live_price_refresh_timer is not None:
            window._live_price_refresh_timer.stop()
        self.addCleanup(window.close)
        return window

    def _refresh(self, window: StockInstanceChartWindow) -> bool:
        with patch.object(chart_window, "_today_trade_date", return_value=TODAY):
            return window.refresh_live_price_projection()

    def test_off_keeps_canonical_projection_identical(self) -> None:
        window = self._window()
        canonical = list(window.chart.close_series)

        self.host.states["005930"] = _state()
        self.assertFalse(self._refresh(window))

        self.assertEqual(canonical, window.chart.close_series)
        self.assertIsNone(window.chart.live_price_point)
        self.assertEqual(1, self.provider.call_count)

    def test_on_projects_live_price_without_mutating_completed_candles(self) -> None:
        self.host.gate_enabled = True
        self.host.states["005930"] = _state(price=70350, quality="UNCERTAIN")
        observed = []
        self.host.high_resolution_price_observed.connect(observed.append)
        window = self._window()
        canonical = list(window.chart.close_series)

        self.assertEqual(70350.0, window.chart.live_price_point[1])
        self.assertEqual("UNCERTAIN", window.chart.live_price_data_quality)
        self.assertEqual(canonical, window.chart.close_series)
        self.assertEqual([], observed)
        self.assertEqual(1, self.provider.call_count)

    def test_gate_transitions_apply_existing_state_then_remove_live_only(self) -> None:
        self.host.states["005930"] = _state(price=70400)
        window = self._window()
        canonical = list(window.chart.close_series)

        self.host.gate_enabled = True
        self.assertTrue(self._refresh(window))
        self.assertEqual(70400.0, window.chart.live_price_point[1])
        self.host.gate_enabled = False
        self.assertTrue(self._refresh(window))

        self.assertIsNone(window.chart.live_price_point)
        self.assertEqual(canonical, window.chart.close_series)
        self.assertEqual(1, self.provider.call_count)

    def test_no_tick_wrong_stock_and_stale_session_never_overlay(self) -> None:
        self.host.gate_enabled = True
        window = self._window()
        self.assertFalse(self._refresh(window))

        self.host.states["005930"] = _state(stock_code="000660")
        self.assertFalse(self._refresh(window))
        self.host.states["005930"] = _state(epoch=6, session_id="STALE")
        self.assertFalse(self._refresh(window))

        self.assertIsNone(window.chart.live_price_point)
        self.assertEqual(1, self.provider.call_count)

    def test_past_date_has_no_live_timer_or_overlay(self) -> None:
        self.host.gate_enabled = True
        self.host.states["005930"] = _state()
        window = self._window(trade_date="2026-08-23")

        self.assertIsNone(window._live_price_refresh_timer)
        self.assertFalse(self._refresh(window))
        self.assertIsNone(window.chart.live_price_point)

    def test_other_stock_updates_do_not_repaint_this_chart(self) -> None:
        self.host.gate_enabled = True
        self.host.states["005930"] = _state(price=70300)
        window = self._window()
        window.chart.update = Mock(wraps=window.chart.update)

        self.host.states["000660"] = _state(stock_code="000660", price=200000)
        self.assertFalse(self._refresh(window))

        window.chart.update.assert_not_called()
        self.assertEqual(70300.0, window.chart.live_price_point[1])

    def test_high_frequency_state_changes_coalesce_to_one_ui_refresh(self) -> None:
        self.host.gate_enabled = True
        self.host.states["005930"] = _state(price=70000)
        window = self._window()
        window.chart.update = Mock(wraps=window.chart.update)

        for price in range(70100, 70200):
            self.host.states["005930"] = _state(price=price)
        window.chart.update.assert_not_called()
        self.assertTrue(self._refresh(window))

        self.assertEqual(70199.0, window.chart.live_price_point[1])
        self.assertEqual(1, window.chart.update.call_count)

    def test_live_refresh_has_no_projection_io_or_broker_side_effect(self) -> None:
        self.host.gate_enabled = True
        self.host.states["005930"] = _state(price=70500)
        window = self._window()
        self.host.states["005930"] = _state(price=70600)

        with patch.object(Path, "read_text") as read_text, patch.object(
            Path,
            "write_text",
        ) as write_text, patch.object(Path, "write_bytes") as write_bytes, patch.object(
            builtins,
            "open",
        ) as open_file:
            self.assertTrue(self._refresh(window))

        self.assertEqual(1, self.provider.call_count)
        read_text.assert_not_called()
        write_text.assert_not_called()
        write_bytes.assert_not_called()
        open_file.assert_not_called()
        self.host.CommRqData.assert_not_called()
        self.host.SetRealReg.assert_not_called()
        self.host.SetRealRemove.assert_not_called()

    def test_close_stops_live_refresh_timer(self) -> None:
        self.host.gate_enabled = True
        self.host.states["005930"] = _state()
        window = self._window()
        timer = window._live_price_refresh_timer
        self.assertEqual(333, timer.interval())
        self.assertGreaterEqual(timer.interval(), 250)
        self.assertLessEqual(timer.interval(), 500)
        timer.start()
        self.assertTrue(timer.isActive())

        window.close()

        self.assertFalse(timer.isActive())
        self.assertIsNone(window._live_price_refresh_timer)
        self.assertIsNone(window._live_price_operation_host)


if __name__ == "__main__":
    unittest.main()
