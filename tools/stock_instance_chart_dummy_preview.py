# -*- coding: utf-8 -*-
"""Development-only in-memory preview for StockInstanceChartWindow."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PyQt5.QtCore import QTimer, Qt
from PyQt5.QtWidgets import QApplication

from gui_stock_instance_chart_window import StockInstanceChartWindow


PREVIEW_STOCK_CODE = "005380"
PREVIEW_TRADE_DATE = "2026-08-10"
SEOUL = timezone(timedelta(hours=9))
PRICE_ANCHORS = {
    0: 72_000,
    6: 71_300,
    12: 70_850,
    18: 71_950,
    24: 72_700,
    30: 73_400,
    36: 72_800,
    44: 71_850,
    49: 72_200,
    54: 73_100,
}
PRICE_WIGGLE = (0, 45, -35, 70, -55, 25)


def _interpolated_close(index: int) -> int:
    anchors = sorted(PRICE_ANCHORS)
    left = max(anchor for anchor in anchors if anchor <= index)
    right = min(anchor for anchor in anchors if anchor >= index)
    if left == right:
        return PRICE_ANCHORS[left]
    ratio = (index - left) / (right - left)
    base = PRICE_ANCHORS[left] + (
        PRICE_ANCHORS[right] - PRICE_ANCHORS[left]
    ) * ratio
    return int(round(base + PRICE_WIGGLE[index % len(PRICE_WIGGLE)]))


def build_dummy_stock_instance_day_projection() -> dict[str, Any]:
    """Return a deterministic 55-bar Projection fixture without any I/O."""
    start = datetime(2026, 8, 10, 9, 0, tzinfo=SEOUL)
    candles: list[dict[str, Any]] = []
    previous_close = _interpolated_close(0)
    for index in range(55):
        close = _interpolated_close(index)
        bar_time = start + timedelta(minutes=index * 5)
        candles.append(
            {
                "bar_time": bar_time.isoformat(timespec="seconds"),
                "open": previous_close,
                "high": max(previous_close, close) + 110 + (index % 3) * 15,
                "low": min(previous_close, close) - 100 - (index % 4) * 10,
                "close": close,
                "volume": 8_000 + index * 137,
                "timeframe_minutes": 5,
                "is_complete": True,
                "trade_date": PREVIEW_TRADE_DATE,
            }
        )
        previous_close = close

    candle_by_time = {
        str(candle["bar_time"])[11:16]: candle for candle in candles
    }

    def marker(signal: str, clock: str, number: int) -> dict[str, Any]:
        candle = candle_by_time[clock]
        return {
            "signal_id": f"PREVIEW-{signal}-{number}",
            "signal": signal,
            "signal_bar_time": candle["bar_time"],
            "signal_bar_close": candle["close"],
            "signal_timeframe_minutes": 5,
            "signal_trade_date": PREVIEW_TRADE_DATE,
            "signal_input_hash": "PREVIEW_ONLY",
            "signal_index": candles.index(candle),
            "delay_bar": 0,
            "created_at": candle["bar_time"],
            "actual_order_count": 1 if number == 1 else 0,
        }

    buys = [
        marker("BUY", "09:35", 1),
        marker("BUY", "10:25", 2),
        marker("BUY", "12:40", 3),
    ]
    sells = [
        marker("SELL", "11:20", 1),
        marker("SELL", "13:20", 2),
    ]
    return {
        "stock_code": PREVIEW_STOCK_CODE,
        "stock_name": "현대차",
        "trade_date": PREVIEW_TRADE_DATE,
        "instance_id": "preview-instance-a",
        "instance_name": "지표추종매매 / 인스턴스 A",
        "bar_minutes": 5,
        "operation_mode": "SCHEDULED",
        "operation_mode_display": "시간",
        "operation_title_display": "시간운영",
        "operation_start_time": "09:00:00",
        "operation_end_buy_time": "13:30:00",
        "operation_time": "09:00~13:30",
        "current_status": "RUNNING",
        "current_status_display": "운영중",
        "holding_quantity": 10,
        "average_price": 71_500,
        "daily_realized_gross": 12_000,
        "completed_buy_cost": 1_200_000,
        "open_position_cost": 715_000,
        "unrealized_pnl_at_bar_close": 9_750,
        "cumulative_pnl": 21_750,
        "cumulative_return_rate": 1.1357702349,
        "pnl_bar_time": candles[-1]["bar_time"],
        "pnl_bar_close": candles[-1]["close"],
        "pnl_available": True,
        "cumulative_return_available": True,
        "pnl_unavailable_reason": "",
        "pnl_source": "development preview fixture only",
        "pnl_basis": "GROSS",
        "candles": candles,
        "buy_signal_markers": buys,
        "sell_signal_markers": sells,
        "buy_signal_count": 3,
        "sell_signal_count": 2,
        "actual_order_count": 2,
        "actual_order_source": "development preview fixture only",
        "diagnostics": {
            "stock_found": True,
            "instance_rules_available": True,
            "raw_candle_count": 275,
            "completed_candle_count": 55,
            "legacy_signal_marker_unavailable_count": 0,
            "order_queue_available": True,
            "completed_input_hash": "PREVIEW_ONLY",
            "issues": [],
        },
    }


def dummy_projection_provider(
    _stock_code: str,
    _trade_date: str,
) -> dict[str, Any]:
    return build_dummy_stock_instance_day_projection()


def _save_screenshot(window: StockInstanceChartWindow, path_text: str) -> None:
    if not path_text:
        return
    path = Path(path_text).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    if window.grab().save(str(path)):
        print(f"preview screenshot: {path}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--screenshot", default="")
    parser.add_argument("--screenshot-delay-ms", type=int, default=700)
    args = parser.parse_args()

    app = QApplication.instance() or QApplication(sys.argv)
    window = StockInstanceChartWindow(
        PREVIEW_STOCK_CODE,
        PREVIEW_TRADE_DATE,
        projection_provider=dummy_projection_provider,
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
