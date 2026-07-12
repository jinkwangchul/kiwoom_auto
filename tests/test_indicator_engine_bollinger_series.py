import unittest

from engines.indicator_engine import build_indicator_series


class IndicatorEngineBollingerSeriesTest(unittest.TestCase):
    def _candles(self):
        return [
            {"close": 10 + index, "volume": 100 + index}
            for index in range(10)
        ]

    def test_bollinger_lower_alias_keeps_legacy_bollinger_contract(self):
        series = build_indicator_series(
            self._candles(),
            {"bollinger": {"period": 3, "std": 2.0}},
        )

        self.assertIn("BOLLINGER", series)
        self.assertIn("BOLLINGER_LOWER", series)
        self.assertEqual(series["BOLLINGER"], series["BOLLINGER_LOWER"])

    def test_bollinger_lower_middle_upper_are_created_with_close_length(self):
        series = build_indicator_series(
            self._candles(),
            {"bollinger": {"period": 3, "std": 2.0}},
        )
        close_length = len(series["CLOSE"])

        for key in ("BOLLINGER_LOWER", "BOLLINGER_MIDDLE", "BOLLINGER_UPPER"):
            self.assertIn(key, series)
            self.assertEqual(len(series[key]), close_length)

    def test_bollinger_data_shortage_none_alignment_is_preserved(self):
        series = build_indicator_series(
            self._candles(),
            {"bollinger": {"period": 3, "std": 2.0}},
        )

        for key in ("BOLLINGER", "BOLLINGER_LOWER", "BOLLINGER_MIDDLE", "BOLLINGER_UPPER"):
            self.assertEqual(series[key][:2], [None, None])
            self.assertIsNotNone(series[key][2])

    def test_bollinger_band_order_is_lower_middle_upper_when_available(self):
        series = build_indicator_series(
            self._candles(),
            {"bollinger": {"period": 3, "std": 2.0}},
        )

        for lower, middle, upper in zip(
            series["BOLLINGER_LOWER"],
            series["BOLLINGER_MIDDLE"],
            series["BOLLINGER_UPPER"],
        ):
            if lower is None or middle is None or upper is None:
                continue
            self.assertLessEqual(lower, middle)
            self.assertLessEqual(middle, upper)


if __name__ == "__main__":
    unittest.main()
