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
        self.assertIn("PNL_REFRESH_INTERVAL_MS", inspect.getsource(chart_module.StockInstanceChartWindow.__init__))

    def test_chart_pnl_tick_does_not_reload_candle_projection(self):
        calls = []
        def provider(code, trade_date):
            calls.append((code, trade_date))
            return {"stock_code": code, "trade_date": trade_date, "candles": [], "buy_signal_markers": [], "sell_signal_markers": [], "diagnostics": {}, "pnl_available": False}
        with mock.patch.object(chart_module, "project_current_stock_pnl", return_value={"available": True, "cumulative_profit": 1234, "cumulative_rate": 1.25, "boundary_id": "B1", "evaluation_price": 50000, "evaluation_price_at": "2026-08-11T10:00:00+09:00"}):
            window = chart_module.StockInstanceChartWindow("005930", chart_module._today_trade_date(), projection_provider=provider)
            initial_calls = len(calls)
            self.assertTrue(window._pnl_refresh_timer.isActive())
            self.assertEqual(1000, window._pnl_refresh_timer.interval())
            window.refresh_pnl_only()
            self.assertEqual(initial_calls, len(calls))
            self.assertIn("1,234", window.info_labels["cumulative_pnl"].text())
            window.close()
            self.assertFalse(window._pnl_refresh_timer.isActive())

    def test_past_chart_has_no_pnl_timer(self):
        window = chart_module.StockInstanceChartWindow("005930", "2000-01-01", projection_provider=lambda c, d: {"stock_code": c, "trade_date": d, "candles": [], "buy_signal_markers": [], "sell_signal_markers": [], "diagnostics": {}, "pnl_available": False})
        self.assertFalse(window._pnl_refresh_timer.isActive())
        window.close()


if __name__ == "__main__":
    unittest.main()
