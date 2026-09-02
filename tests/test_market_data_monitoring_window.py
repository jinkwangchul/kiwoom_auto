from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from PyQt5 import sip
from PyQt5.QtWidgets import QApplication, QWidget

from gui_windows import MainWindow, _MarketDataMonitoringWindow
from tests.qt_test_support import flush_deferred_deletes


class _MonitoringHost:
    def __init__(self) -> None:
        self.market = SimpleNamespace(
            connection_epoch=7,
            login_session_id="SESSION-7",
            broker_connected=True,
            realtime_registration_active=True,
            realtime_target_stock_count=3,
            received_tick_count=10,
            processed_tick_count=9,
            current_queue_depth=1,
            queue_high_watermark=4,
            overflow_count=2,
            last_receive_sequence=101,
            last_processed_sequence=100,
            last_tick_received_at="2026-08-24T10:00:00+09:00",
            last_tick_processed_at="2026-08-24T10:00:00.001+09:00",
            last_processing_latency_ms=1.25,
            max_processing_latency_ms=3.5,
            data_quality="UNCERTAIN",
        )
        self.tr = SimpleNamespace(
            total_enqueued=11,
            total_dispatched=10,
            current_queue_depth=1,
            last_rqname="OPT10080_TEST",
            last_trcode="opt10080",
            last_dispatch_monotonic=123456,
            dispatch_count_last_60s=4,
            last_queue_wait_ms=25.0,
            max_queue_wait_ms=1250.0,
            timeout_count=1,
            stale_count=2,
            error_count=3,
            last_error_reason="CommRqData failed",
        )
        self.CommRqData = Mock()
        self.SetRealReg = Mock()
        self.SetRealRemove = Mock()

    def high_resolution_market_data_snapshot(self):
        return self.market

    def tr_governor_metrics_snapshot(self):
        return self.tr


class _MainHarness(QWidget):
    def __init__(self, host: _MonitoringHost) -> None:
        super().__init__()
        self.host = host
        self.market_data_monitoring_window = None

    def main_monitoring_auto_trade_operation_host(self):
        return self.host


class MarketDataMonitoringWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.host = _MonitoringHost()
        self.parent = _MainHarness(self.host)

    def tearDown(self) -> None:
        window = getattr(self.parent, "market_data_monitoring_window", None)
        if window is not None and not sip.isdeleted(window):
            window.close()
            window.deleteLater()
        self.parent.close()
        flush_deferred_deletes(self.app)

    def test_open_ten_times_reuses_single_window_and_observes_only(self) -> None:
        windows = [
            MainWindow.open_market_data_monitoring_window(self.parent)
            for _index in range(10)
        ]

        self.assertEqual(1, len({id(window) for window in windows}))
        self.host.CommRqData.assert_not_called()
        self.host.SetRealReg.assert_not_called()
        self.host.SetRealRemove.assert_not_called()

    def test_close_then_reopen_creates_a_live_window(self) -> None:
        first = MainWindow.open_market_data_monitoring_window(self.parent)
        first.close()
        flush_deferred_deletes(self.app)
        second = MainWindow.open_market_data_monitoring_window(self.parent)

        self.assertIsNot(first, second)
        self.assertFalse(sip.isdeleted(second))

    def test_snapshot_tick_rate_quality_and_tr_metrics_are_displayed(self) -> None:
        window = _MarketDataMonitoringWindow(self.parent, self.host)
        window._refresh_timer.stop()
        window._last_tick_rate_count = 10
        window._last_tick_rate_monotonic = 100.0
        self.host.market.received_tick_count = 15
        window.refresh_snapshot(now_monotonic=102.0)

        self.assertEqual("", window.value_text("price_signal"))
        self.assertEqual("2.5", window.value_text("tick_rate"))
        self.assertEqual("UNCERTAIN", window.value_text("data_quality"))
        self.assertEqual("15", window.value_text("received_tick_count"))
        self.assertEqual("9", window.value_text("processed_tick_count"))
        self.assertEqual("1 / 4", window.value_text("queue"))
        self.assertEqual("1.250 ms / 3.500 ms", window.value_text("latency"))
        self.assertEqual("11", window.value_text("total_enqueued"))
        self.assertEqual("OPT10080_TEST", window.value_text("last_rqname"))
        self.assertEqual("25.000 ms / 1250.000 ms", window.value_text("queue_wait"))
        window.close()

    def test_close_stops_refresh_timer(self) -> None:
        window = _MarketDataMonitoringWindow(self.parent, self.host)
        self.assertTrue(window._refresh_timer.isActive())
        window.close()
        self.assertFalse(window._refresh_timer.isActive())

    def test_all_rows_are_readable_in_a_compact_initial_window(self) -> None:
        window = _MarketDataMonitoringWindow(self.parent, self.host)
        window._refresh_timer.stop()
        window.show()
        self.app.processEvents()
        font_height = window.fontMetrics().height()

        self.assertEqual(
            set(dict(window.REALTIME_ROWS)) | set(dict(window.TR_ROWS)),
            set(window._row_labels),
        )
        for caption, value in window._row_labels.values():
            self.assertGreaterEqual(caption.minimumHeight(), font_height)
            self.assertGreaterEqual(value.minimumHeight(), font_height)
            self.assertGreaterEqual(caption.height(), font_height)
            self.assertGreaterEqual(value.height(), font_height)
        self.assertLess(window.width(), 700)
        self.assertLess(window.height(), 750)
        self.assertGreaterEqual(window.minimumHeight(), window.minimumSizeHint().height())
        self.assertLessEqual(window._row_minimum_height, font_height + 5)
        self.assertEqual(750, window._refresh_timer.interval())
        window.close()

    def test_long_values_are_elided_without_inflating_window_and_keep_tooltips(self) -> None:
        long_session = "KIWOOM_LOGIN_SESSION_" + ("F" * 128)
        long_error = "CommRqData failed: " + ("diagnostic " * 80)
        self.host.market.login_session_id = long_session
        self.host.tr.last_error_reason = long_error

        window = _MarketDataMonitoringWindow(self.parent, self.host)
        window._refresh_timer.stop()
        window.show()
        self.app.processEvents()
        window.refresh_snapshot()
        self.app.processEvents()

        session_label = window._row_labels["login_session_id"][1]
        error_label = window._row_labels["last_error_reason"][1]
        self.assertLess(window.width(), 700)
        self.assertLess(window.height(), 750)
        self.assertNotEqual(long_session, session_label.text())
        self.assertTrue(session_label.text().endswith("…"))
        self.assertEqual(long_session, session_label.toolTip())
        self.assertNotEqual(long_error, error_label.text())
        self.assertTrue(error_label.text().endswith("…"))
        self.assertEqual(long_error, error_label.toolTip())
        self.host.CommRqData.assert_not_called()
        self.host.SetRealReg.assert_not_called()
        window.close()


if __name__ == "__main__":
    unittest.main()
