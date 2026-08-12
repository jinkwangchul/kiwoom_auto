import inspect
import sys
import unittest
from unittest import mock

from PyQt5.QtWidgets import QApplication

import gui_auto_trade_setting_window as settings
import gui_stock_instance_chart_window as chart_module
import gui_windows
from pnl_ui_refresh import PNL_REFRESH_INTERVAL_MS


class PnlOneSecondRefreshTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def test_common_interval_is_one_second_and_all_windows_reference_it(self):
        self.assertEqual(1000, PNL_REFRESH_INTERVAL_MS)
        self.assertIn("PNL_REFRESH_INTERVAL_MS", inspect.getsource(settings.AutoTradeSettingWindow.__init__))
        self.assertIn("PNL_REFRESH_INTERVAL_MS", inspect.getsource(gui_windows.MainWindow.__init__))
        self.assertIn("PNL_REFRESH_INTERVAL_MS", inspect.getsource(chart_module._common_pnl_refresh_timer))

    def test_chart_pnl_tick_does_not_reload_candle_projection(self):
        calls = []
        def provider(code, trade_date):
            calls.append((code, trade_date))
            return {"stock_code": code, "trade_date": trade_date, "candles": [], "buy_signal_markers": [], "sell_signal_markers": [], "diagnostics": {}, "pnl_available": False}
        with mock.patch.object(chart_module, "project_current_stock_pnl", return_value={"available": True, "cumulative_profit": 1234, "cumulative_rate": 1.25, "boundary_id": "B1", "evaluation_price": 50000, "evaluation_price_at": "2026-08-11T10:00:00+09:00"}):
            with mock.patch.object(chart_module, "project_stock_instance_day", side_effect=provider):
                window = chart_module.open_stock_instance_chart("005930", chart_module._today_trade_date())
            self.assertEqual("0(0.00%)", window.info_labels["cumulative_pnl"].text())
            initial_calls = len(calls)
            timer = chart_module._common_pnl_refresh_timer()
            self.assertTrue(timer.isActive())
            self.assertEqual(1000, timer.interval())
            self.assertFalse(hasattr(window, "_pnl_refresh_timer"))
            window.refresh_pnl_only()
            self.assertEqual(initial_calls, len(calls))
            self.assertEqual("+1,234(+1.25%)", window.info_labels["cumulative_pnl"].text())
            window.close()
            self.assertFalse(timer.isActive())

    def test_past_chart_has_no_pnl_timer(self):
        with mock.patch.object(chart_module, "project_stock_instance_day", side_effect=lambda c, d: {"stock_code": c, "trade_date": d, "candles": [], "buy_signal_markers": [], "sell_signal_markers": [], "diagnostics": {}, "pnl_available": False}):
            window = chart_module.open_stock_instance_chart("005930", "2000-01-01")
        timer = chart_module._common_pnl_refresh_timer()
        self.assertTrue(timer is None or not timer.isActive())
        self.assertFalse(hasattr(window, "_pnl_refresh_timer"))
        window.close()


if __name__ == "__main__":
    unittest.main()
