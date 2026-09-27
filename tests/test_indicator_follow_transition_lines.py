import json
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import QApplication

from gui_indicator_follow_signal_validation_window import (
    IndicatorFollowSignalValidationChartCanvas,
)
from indicator_follow_signal_validation_visualization import (
    FAMILY_MACD_SIGNAL,
    FAMILY_OCR_OSC,
    LOWER_AXIS,
    ValidationFilterDescriptor,
    ValidationIndicatorSeriesCache,
    indicator_transition_indexes,
)


class IndicatorFollowTransitionLineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_transition_indexes_use_pivot_for_turn_and_cross_bar_for_zero(self):
        values = (10.0, 20.0, 15.0, -5.0, 5.0)
        self.assertEqual((1,), indicator_transition_indexes(values, "TURN_DOWN"))
        self.assertEqual((3,), indicator_transition_indexes(values, "TURN_UP"))
        self.assertEqual((3,), indicator_transition_indexes(values, "ZERO_CROSS_DOWN"))
        self.assertEqual((4,), indicator_transition_indexes(values, "ZERO_CROSS_UP"))

    def test_turn_and_zero_cross_project_as_vertical_dotted_lines(self):
        values = (10.0, 20.0, 15.0, -5.0, 5.0)
        descriptors = (
            ValidationFilterDescriptor(
                identity="ocr-turn-down", family=FAMILY_OCR_OSC, label="OCR",
                axis=LOWER_AXIS, sides=("SELL",), series_keys=("OSC",),
                parameter_json=json.dumps({"condition": {"target": "OSC", "operator": "TURN_DOWN"}}),
                evidence_keys=("ocr-turn-down",),
            ),
            ValidationFilterDescriptor(
                identity="ocr-zero-down", family=FAMILY_OCR_OSC, label="OCR",
                axis=LOWER_AXIS, sides=("BUY",), series_keys=("OSC",),
                parameter_json=json.dumps({"condition": {"target": "OSC", "operator": "ZERO_CROSS_DOWN"}}),
                evidence_keys=("ocr-zero-down",),
            ),
            ValidationFilterDescriptor(
                identity="macd-turn-up", family=FAMILY_MACD_SIGNAL, label="MACD",
                axis=LOWER_AXIS, sides=("BUY",), series_keys=("MACD",),
                parameter_json=json.dumps({"condition": {"target": "MACD", "operator": "TURN_UP"}}),
                evidence_keys=("macd-turn-up",),
            ),
        )
        cache = ValidationIndicatorSeriesCache(
            candle_count=len(values),
            series=(
                ("ocr-turn-down", "OSC", values),
                ("ocr-zero-down", "OSC", values),
                ("macd-turn-up", "MACD", values),
            ),
        )
        candles = [
            {"time": f"2026092315{index:02d}00", "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1}
            for index in range(len(values))
        ]
        canvas = IndicatorFollowSignalValidationChartCanvas(
            candles, [], visualization_descriptors=descriptors, visualization_cache=cache
        )
        try:
            canvas.resize(900, 560)
            records = canvas.lower_transition_line_records(FAMILY_OCR_OSC)
            by_operator = {record["operators"][0]: record for record in records}
            self.assertEqual(1, by_operator["TURN_DOWN"]["index"])
            self.assertEqual(
                "TRANSITION_VERTICAL_LINE",
                by_operator["TURN_DOWN"]["render_mode"],
            )
            self.assertEqual(3, by_operator["ZERO_CROSS_DOWN"]["index"])
            self.assertEqual(
                "TRANSITION_VERTICAL_LINE",
                by_operator["ZERO_CROSS_DOWN"]["render_mode"],
            )
            self.assertEqual(3, by_operator["TURN_UP"]["index"])
            self.assertEqual(
                "TRANSITION_VERTICAL_LINE",
                by_operator["TURN_UP"]["render_mode"],
            )
            self.assertAlmostEqual(
                canvas._x_for_index(1),
                by_operator["TURN_DOWN"]["x"],
            )
            canvas.render(QPixmap(canvas.size()))
        finally:
            canvas.close()
            canvas.deleteLater()
            self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
