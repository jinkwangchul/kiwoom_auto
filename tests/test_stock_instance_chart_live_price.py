from __future__ import annotations

import builtins
from datetime import datetime
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
        self.nxt_states: dict[str, object] = {}
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

    def nxt_display_live_price_state(self, stock_code: str):
        return self.nxt_states.get(str(stock_code))


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


def _nxt_state(
    stock_code: str = "005930",
    *,
    price: int = 261000,
    epoch: int = 7,
    session_id: str = "SESSION-7",
):
    return SimpleNamespace(
        canonical_stock_code=stock_code,
        broker_code_identity=f"{stock_code}_NX",
        market_source="NXT",
        source_real_type="ECN주식체결",
        connection_epoch=epoch,
        login_session_id=session_id,
        last_market_datetime=f"{TODAY}T18:00:01+09:00",
        last_price=price,
        data_quality="NORMAL",
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

    def test_price_signal_gate_off_keeps_current_price_marker_independent(self) -> None:
        window = self._window()
        canonical = list(window.chart.close_series)

        self.host.states["005930"] = _state()
        self.assertTrue(self._refresh(window))

        self.assertEqual(canonical, window.chart.close_series)
        self.assertEqual(70350.0, window.chart.live_price_point[1])
        self.assertEqual(1, self.provider.call_count)

    def test_live_price_source_uses_nxt_only_windows(self) -> None:
        def at(hour: int, minute: int) -> datetime:
            return datetime(
                2026, 8, 24, hour, minute,
                tzinfo=chart_window.SEOUL_TIMEZONE,
            )

        expected = (
            (True, 8, 20, "NXT"),
            (True, 8, 50, "KRX"),
            (True, 10, 0, "KRX"),
            (True, 15, 30, "KRX"),
            (True, 15, 40, "NXT"),
            (True, 19, 59, "NXT"),
            (True, 20, 0, "KRX"),
            (False, 18, 0, "KRX"),
        )
        for nxt_available, hour, minute, source in expected:
            with self.subTest(
                nxt_available=nxt_available,
                hour=hour,
                minute=minute,
            ):
                self.assertEqual(
                    source,
                    chart_window._live_price_market_source(
                        nxt_available=nxt_available,
                        now_dt=at(hour, minute),
                    ),
                )

    def test_nxt_only_window_uses_separate_nxt_display_state(self) -> None:
        self.provider.side_effect = lambda code, date: {
            **_projection(code, date),
            "nxt_available": True,
        }
        self.host.states["005930"] = _state(price=259500)
        self.host.nxt_states["005930"] = _nxt_state(price=261000)
        with patch.object(
            chart_window,
            "_live_price_market_source",
            return_value="NXT",
        ):
            window = self._window()

        self.assertEqual(261000.0, window.chart.live_price_point[1])

    def test_nxt_only_window_never_falls_back_to_krx_state(self) -> None:
        self.provider.side_effect = lambda code, date: {
            **_projection(code, date),
            "nxt_available": True,
        }
        self.host.states["005930"] = _state(price=259500)
        with patch.object(
            chart_window,
            "_live_price_market_source",
            return_value="NXT",
        ):
            window = self._window()

        self.assertIsNone(window.chart.live_price_point)

    def test_regular_window_keeps_krx_when_nxt_state_exists(self) -> None:
        self.provider.side_effect = lambda code, date: {
            **_projection(code, date),
            "nxt_available": True,
        }
        self.host.states["005930"] = _state(price=259500)
        self.host.nxt_states["005930"] = _nxt_state(price=261000)
        with patch.object(
            chart_window,
            "_live_price_market_source",
            return_value="KRX",
        ):
            window = self._window()

        self.assertEqual(259500.0, window.chart.live_price_point[1])

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

    def test_gate_transitions_do_not_control_chart_current_price(self) -> None:
        self.host.states["005930"] = _state(price=70400)
        window = self._window()
        canonical = list(window.chart.close_series)

        self.assertEqual(70400.0, window.chart.live_price_point[1])
        self.host.gate_enabled = True
        self.assertFalse(self._refresh(window))
        self.assertEqual(70400.0, window.chart.live_price_point[1])
        self.host.gate_enabled = False
        self.assertFalse(self._refresh(window))

        self.assertEqual(70400.0, window.chart.live_price_point[1])
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

    def test_live_price_diagnostic_records_failure_and_recovery(self) -> None:
        window = self._window()
        failed = window.live_price_diagnostic_snapshot()
        self.assertEqual("NO_LIVE_PRICE_STATE", failed["stage"])
        self.assertEqual("KRX", failed["market_source"])

        self.host.states["005930"] = _state(price=70350)
        self.assertTrue(self._refresh(window))

        recovered = window.live_price_diagnostic_snapshot()
        self.assertEqual("LIVE_PRICE_APPLIED", recovered["stage"])
        self.assertEqual("KRX", recovered["market_source"])
        self.assertEqual(70350.0, recovered["price"])
        self.assertGreater(recovered["count"], failed["count"])

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
