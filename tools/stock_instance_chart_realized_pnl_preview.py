# -*- coding: utf-8 -*-
"""Development-only SELL cumulative realized P/L chart preview."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PyQt5.QtCore import QRectF, QTimer, Qt
from PyQt5.QtGui import QColor, QFont, QFontMetrics, QPainter
from PyQt5.QtWidgets import QApplication

from gui_stock_instance_chart_window import (
    BUY_COLOR,
    SELL_COLOR,
    StockInstanceChartWindow,
    StockInstanceCloseChart,
    _finite_number,
)
from tools.stock_instance_chart_dummy_preview import (
    PREVIEW_STOCK_CODE,
    PREVIEW_TRADE_DATE,
    build_dummy_stock_instance_day_projection,
)


NEUTRAL_TEXT_COLOR = QColor("#111827")


def build_realized_pnl_preview_projection() -> dict[str, Any]:
    """Add preview-only SELL snapshot values to the in-memory fixture."""
    projection = build_dummy_stock_instance_day_projection()
    sell_markers = projection.get("sell_signal_markers", [])
    preview_values = (
        (154_000, 3.25),
        (-38_000, -0.82),
    )
    for marker, (amount, rate) in zip(sell_markers, preview_values):
        marker["preview_realized_profit_amount_at_signal"] = amount
        marker["preview_realized_profit_rate_at_signal"] = rate
    return projection


def realized_pnl_preview_provider(
    _stock_code: str,
    _trade_date: str,
) -> dict[str, Any]:
    return build_realized_pnl_preview_projection()


class RealizedPnlPreviewChart(StockInstanceCloseChart):
    """Preview renderer; Production chart and Projection remain unchanged."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._preview_realized_by_sell_price: dict[float, tuple[float, float]] = {}

    def set_projection(self, candles, buy_markers, sell_markers, **kwargs) -> None:
        self._preview_realized_by_sell_price = {}
        if isinstance(sell_markers, list):
            for marker in sell_markers:
                if not isinstance(marker, dict):
                    continue
                price = _finite_number(marker.get("signal_bar_close"))
                amount = _finite_number(
                    marker.get("preview_realized_profit_amount_at_signal")
                )
                rate = _finite_number(
                    marker.get("preview_realized_profit_rate_at_signal")
                )
                if price is not None and amount is not None and rate is not None:
                    self._preview_realized_by_sell_price[price] = (amount, rate)
        super().set_projection(candles, buy_markers, sell_markers, **kwargs)

    @staticmethod
    def realized_text(amount: float, rate: float) -> str:
        return f" ({amount:+,.0f} / {rate:+.2f}%)"

    @staticmethod
    def realized_color(amount: float) -> QColor:
        if amount > 0:
            return BUY_COLOR
        if amount < 0:
            return SELL_COLOR
        return NEUTRAL_TEXT_COLOR

    def _draw_signal_label(
        self,
        painter: QPainter,
        plot: QRectF,
        point,
        value: float,
        color: QColor,
        *,
        above: bool,
    ) -> None:
        price_text = self._signal_label_text(value)
        realized = (
            self._preview_realized_by_sell_price.get(value)
            if color == SELL_COLOR
            else None
        )
        realized_text = self.realized_text(*realized) if realized is not None else ""

        label_font = QFont(painter.font())
        label_font.setPointSize(max(7, label_font.pointSize() - 1))
        label_font.setWeight(QFont.Medium)
        metrics = QFontMetrics(label_font)
        price_width = float(metrics.horizontalAdvance(price_text))
        realized_width = float(metrics.horizontalAdvance(realized_text))
        width = price_width + realized_width + 4.0
        height = 18.0
        x = point.x() + 8.0
        if x + width > plot.right():
            x = point.x() - width - 8.0
        y = point.y() - height - 7.0 if above else point.y() + 7.0
        if y < plot.top():
            y = point.y() + 7.0
        if y + height > plot.bottom():
            y = point.y() - height - 7.0

        painter.setFont(label_font)
        painter.setPen(NEUTRAL_TEXT_COLOR)
        painter.drawText(
            QRectF(x, y, price_width + 2.0, height),
            Qt.AlignLeft | Qt.AlignVCenter,
            price_text,
        )
        if realized is not None:
            painter.setPen(self.realized_color(realized[0]))
            painter.drawText(
                QRectF(x + price_width, y, realized_width + 4.0, height),
                Qt.AlignLeft | Qt.AlignVCenter,
                realized_text,
            )
        painter.setFont(self.font())


def _save_screenshot(window: StockInstanceChartWindow, path_text: str) -> None:
    if not path_text:
        return
    path = Path(path_text).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    if window.grab().save(str(path)):
        print(f"realized P/L preview screenshot: {path}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--screenshot", default="")
    parser.add_argument("--screenshot-delay-ms", type=int, default=700)
    args = parser.parse_args()

    app = QApplication.instance() or QApplication(sys.argv)
    window = StockInstanceChartWindow(
        PREVIEW_STOCK_CODE,
        PREVIEW_TRADE_DATE,
        projection_provider=realized_pnl_preview_provider,
        chart_factory=RealizedPnlPreviewChart,
    )
    window.setAttribute(Qt.WA_DeleteOnClose, True)
    window.show()
    window.raise_()
    window.activateWindow()
    if args.screenshot:
        QTimer.singleShot(
            max(0, args.screenshot_delay_ms),
            lambda: _save_screenshot(window, args.screenshot),
        )
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
