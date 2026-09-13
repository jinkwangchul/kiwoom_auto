# -*- coding: utf-8 -*-
from __future__ import annotations

import ast
import os
from pathlib import Path
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QPoint, Qt
from PyQt5.QtGui import QPixmap
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QPlainTextEdit, QScrollArea

from gui_indicator_follow_validation_chart_window import (
    IndicatorFollowValidationChartCanvas,
    IndicatorFollowValidationChartWindow,
)
from routines.지표추종매매.routine_validation_contract import ValidationStockRef
from routines.지표추종매매.routine_validation_replay import (
    ValidationReplayEntry,
    ValidationReplaySnapshot,
)


class IndicatorFollowValidationChartWindowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.snapshot = self._snapshot()
        self.window = IndicatorFollowValidationChartWindow(self.snapshot)
        self.addCleanup(self.window.close)

    @staticmethod
    def _candles(count: int = 6) -> list[dict]:
        return [
            {
                "time": f"2026091114{28 + index:02d}00",
                "open": 100.0 + index,
                "high": 102.0 + index,
                "low": 99.0 + index,
                "close": 101.0 + index,
                "volume": 1000.0 + index,
            }
            for index in range(count)
        ]

    @classmethod
    def _entry(
        cls,
        index: int,
        side: str,
        signal: str | None = None,
        *,
        signal_index: int | None = None,
        delay_bar: int = 0,
    ) -> ValidationReplayEntry:
        candles = cls._candles()
        resolved_signal_index = signal_index if signal is not None else None
        return ValidationReplayEntry(
            evaluation_side=side,
            evaluation_index=index,
            evaluation_time=candles[index]["time"],
            signal=signal,
            reason=f"{side.lower()}-reason-{index}",
            signal_index=resolved_signal_index,
            signal_time=(
                candles[resolved_signal_index]["time"]
                if resolved_signal_index is not None
                else None
            ),
            delay_bar=delay_bar,
            matched_groups=[f"{side.lower()}-group-{index}"],
            details=[f"{side.lower()}-detail-{index}"],
            trace={
                "conditions": [{"source": f"{side}-condition-{index}"}],
                "groups": [{"source": f"{side}-group-trace-{index}"}],
                "aggregations": [
                    {"side": side, "payload": {"source": f"{side}-aggregation-{index}"}}
                ],
            },
        )

    @classmethod
    def _snapshot(cls) -> ValidationReplaySnapshot:
        entries = []
        for index in range(1, 5):
            buy_signal = "BUY" if index == 2 else None
            sell_signal = "SELL" if index in {2, 4} else None
            entries.append(
                cls._entry(
                    index,
                    "BUY",
                    buy_signal,
                    signal_index=1 if buy_signal else None,
                    delay_bar=1 if buy_signal else 0,
                )
            )
            entries.append(
                cls._entry(
                    index,
                    "SELL",
                    sell_signal,
                    signal_index=index if sell_signal else None,
                )
            )
        return ValidationReplaySnapshot(
            stock=ValidationStockRef("005930", "삼성전자"),
            timeframe_minutes=3,
            settings_hash="settings-hash",
            historical_request_id="historical-request",
            evaluated_start_index=1,
            evaluated_end_index=4,
            dropped_raw_rows_count=0,
            candles=cls._candles(),
            entries=entries,
        )

    def test_only_validation_replay_snapshot_is_accepted(self) -> None:
        with self.assertRaises(TypeError):
            IndicatorFollowValidationChartWindow(object())

    def test_summary_and_canvas_use_snapshot_projection_counts(self) -> None:
        self.assertEqual(6, self.window.canvas.candle_count)
        summary = self.window.summary_label.text()
        self.assertIn("005930 삼성전자", summary)
        self.assertIn("3분봉", summary)
        self.assertIn("Candle 6", summary)
        self.assertIn("BUY 1", summary)
        self.assertIn("SELL 2", summary)

    def test_markers_count_only_official_buy_and_sell_signals(self) -> None:
        self.assertEqual(1, self.window.canvas.marker_count("BUY"))
        self.assertEqual(2, self.window.canvas.marker_count("SELL"))
        self.assertEqual(3, self.window.canvas.marker_count())
        self.assertFalse(
            any(marker["side"] not in {"BUY", "SELL"} for marker in self.window.canvas.marker_records())
        )

    def test_marker_uses_evaluation_bar_not_delayed_signal_bar(self) -> None:
        buy = next(
            marker
            for marker in self.window.canvas.marker_records()
            if marker["side"] == "BUY"
        )
        self.assertEqual(2, buy["evaluation_index"])
        self.assertEqual("20260911143000", buy["evaluation_time"])
        self.assertNotIn("signal_index", buy)
        self.assertNotIn("signal_time", buy)

    def test_same_evaluation_index_preserves_both_markers(self) -> None:
        markers = [
            marker
            for marker in self.window.canvas.marker_records()
            if marker["evaluation_index"] == 2
        ]
        self.assertEqual({"BUY", "SELL"}, {marker["side"] for marker in markers})

    def test_default_selection_is_evaluated_end_index(self) -> None:
        self.assertEqual(4, self.window.selected_evaluation_index)
        self.assertEqual(4, self.window.canvas.selected_index)

    def test_programmatic_selection_updates_buy_and_sell_details(self) -> None:
        self.assertTrue(self.window.select_evaluation_index(2))

        buy_text = self.window.buy_details.toPlainText()
        sell_text = self.window.sell_details.toPlainText()
        self.assertIn("공식 신호: BUY", buy_text)
        self.assertIn("평가시각: 2026-09-11 14:30", buy_text)
        self.assertIn("조건 근거봉: 2026-09-11 14:29", buy_text)
        self.assertIn("지연봉: 1", buy_text)
        self.assertIn("공식 신호: SELL", sell_text)
        self.assertIn("평가시각: 2026-09-11 14:30", sell_text)
        self.assertIn("조건 근거봉: 2026-09-11 14:30", sell_text)

    def test_canvas_click_selects_nearest_candle(self) -> None:
        self.window.canvas.resize(self.window.canvas.sizeHint())
        target_x = int(self.window.canvas._x_for_index(3))

        QTest.mouseClick(
            self.window.canvas,
            Qt.LeftButton,
            pos=QPoint(target_x, 100),
        )

        self.assertEqual(3, self.window.selected_evaluation_index)

    def test_out_of_range_selection_creates_no_fake_entry(self) -> None:
        original_entry_count = len(self.window._entries)

        self.assertTrue(self.window.select_evaluation_index(0))

        self.assertIn("Replay 평가 범위 밖", self.window.buy_details.toPlainText())
        self.assertIn("Replay 평가 범위 밖", self.window.sell_details.toPlainText())
        self.assertEqual(original_entry_count, len(self.window._entries))
        self.assertFalse(self.window.select_evaluation_index(99))

    def test_details_show_the_selected_side_trace_without_reinterpretation(self) -> None:
        self.window.select_evaluation_index(2)

        buy_text = self.window.buy_details.toPlainText()
        sell_text = self.window.sell_details.toPlainText()
        for heading in ("[Conditions]", "[Groups]", "[Aggregations]"):
            self.assertIn(heading, buy_text)
            self.assertIn(heading, sell_text)
        self.assertIn("BUY-condition-2", buy_text)
        self.assertIn("BUY-group-trace-2", buy_text)
        self.assertIn("BUY-aggregation-2", buy_text)
        self.assertNotIn("SELL-condition-2", buy_text)
        self.assertIn("SELL-condition-2", sell_text)

    def test_external_snapshot_copy_mutation_does_not_change_loaded_chart(self) -> None:
        detached = self.snapshot.to_candles()
        detached[0]["close"] = 1.0
        detached.append({})

        self.assertEqual(6, self.window.canvas.candle_count)
        self.assertEqual(101.0, self.window.canvas.to_candles()[0]["close"])

    def test_chart_local_data_cannot_mutate_replay_snapshot(self) -> None:
        original = self.snapshot.to_candles()
        self.window._candles[0]["close"] = 999.0
        canvas_copy = self.window.canvas.to_candles()
        canvas_copy[0]["close"] = 888.0
        marker_copy = self.window.canvas.marker_records()
        marker_copy.clear()

        self.assertEqual(original, self.snapshot.to_candles())
        self.assertEqual(101.0, self.window.canvas.to_candles()[0]["close"])
        self.assertEqual(3, self.window.canvas.marker_count())

    def test_detail_views_are_read_only(self) -> None:
        self.assertIsInstance(self.window.buy_details, QPlainTextEdit)
        self.assertIsInstance(self.window.sell_details, QPlainTextEdit)
        self.assertTrue(self.window.buy_details.isReadOnly())
        self.assertTrue(self.window.sell_details.isReadOnly())

    def test_canvas_render_smoke_handles_missing_ohlc(self) -> None:
        candles = self.snapshot.to_candles()
        candles[1]["open"] = None
        candles[1]["high"] = None
        candles[1]["low"] = None
        canvas = IndicatorFollowValidationChartCanvas(candles, [])
        self.addCleanup(canvas.close)
        canvas.resize(canvas.sizeHint())
        pixmap = QPixmap(canvas.size())

        canvas.render(pixmap)

        self.assertFalse(pixmap.isNull())

    def test_many_candles_create_horizontal_scroll_canvas(self) -> None:
        candles = self._candles(500)
        snapshot = ValidationReplaySnapshot(
            stock=ValidationStockRef("005930", "삼성전자"),
            timeframe_minutes=3,
            settings_hash="settings-hash",
            historical_request_id="historical-request-many",
            evaluated_start_index=0,
            evaluated_end_index=499,
            dropped_raw_rows_count=0,
            candles=candles,
            entries=[],
        )
        window = IndicatorFollowValidationChartWindow(snapshot)
        self.addCleanup(window.close)

        self.assertIsInstance(window.scroll_area, QScrollArea)
        self.assertGreater(window.canvas.minimumWidth(), 5000)
        self.assertEqual(500, window.canvas.candle_count)

    def test_source_imports_stay_inside_allowed_ui_and_replay_boundary(self) -> None:
        source_path = (
            Path(__file__).resolve().parents[1]
            / "gui_indicator_follow_validation_chart_window.py"
        )
        tree = ast.parse(source_path.read_text(encoding="utf-8-sig"))
        imported_modules = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.append(node.module)
        forbidden = (
            "gui_stock_instance_chart_window",
            "gui_market_data_host",
            "gui_auto_trade_",
            "pnl_ui_refresh",
            "state_policy",
            "stock_repository",
            "candle_manager",
            "kiwoom_api",
            "mock_validation",
            "routine_macd_engine",
            "condition_engine",
            "indicator_engine",
            "routine_signal_",
            "order",
            "execution",
        )
        for module_name in imported_modules:
            for fragment in forbidden:
                with self.subTest(module=module_name, fragment=fragment):
                    self.assertNotIn(fragment, module_name.lower())


if __name__ == "__main__":
    unittest.main()
