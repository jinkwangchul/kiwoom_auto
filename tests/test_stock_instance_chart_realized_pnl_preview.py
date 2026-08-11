from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication

from gui_stock_instance_chart_window import BUY_COLOR, SELL_COLOR, StockInstanceChartWindow
from tools.stock_instance_chart_dummy_preview import dummy_projection_provider
from tools.stock_instance_chart_realized_pnl_preview import (
    NEUTRAL_TEXT_COLOR,
    RealizedPnlPreviewChart,
    build_realized_pnl_preview_projection,
    realized_pnl_preview_provider,
)


class StockInstanceChartRealizedPnlPreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_preview_fixture_has_two_sell_snapshot_values_only_in_memory(self) -> None:
        projection = build_realized_pnl_preview_projection()
        sells = projection["sell_signal_markers"]

        self.assertEqual(154_000, sells[0]["preview_realized_profit_amount_at_signal"])
        self.assertEqual(3.25, sells[0]["preview_realized_profit_rate_at_signal"])
        self.assertEqual(-38_000, sells[1]["preview_realized_profit_amount_at_signal"])
        self.assertEqual(-0.82, sells[1]["preview_realized_profit_rate_at_signal"])
        self.assertFalse(
            any(
                "preview_realized_profit_amount_at_signal" in marker
                for marker in projection["buy_signal_markers"]
            )
        )

    def test_preview_format_and_directional_colors(self) -> None:
        self.assertEqual(" (+154,000 / +3.25%)", RealizedPnlPreviewChart.realized_text(154_000, 3.25))
        self.assertEqual(" (-38,000 / -0.82%)", RealizedPnlPreviewChart.realized_text(-38_000, -0.82))
        self.assertEqual(BUY_COLOR, RealizedPnlPreviewChart.realized_color(1))
        self.assertEqual(SELL_COLOR, RealizedPnlPreviewChart.realized_color(-1))
        self.assertEqual(NEUTRAL_TEXT_COLOR, RealizedPnlPreviewChart.realized_color(0))

    def test_chart_factory_is_preview_only_and_default_stays_production_chart(self) -> None:
        production = StockInstanceChartWindow(
            "005380",
            "2026-08-10",
            projection_provider=dummy_projection_provider,
        )
        preview = StockInstanceChartWindow(
            "005380",
            "2026-08-10",
            projection_provider=realized_pnl_preview_provider,
            chart_factory=RealizedPnlPreviewChart,
        )

        self.assertNotIsInstance(production.chart, RealizedPnlPreviewChart)
        self.assertIsInstance(preview.chart, RealizedPnlPreviewChart)
        self.assertEqual(2, len(preview.chart._preview_realized_by_sell_price))
        production.close()
        preview.close()


if __name__ == "__main__":
    unittest.main()
