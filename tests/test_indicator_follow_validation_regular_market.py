# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest

from indicator_follow_validation_regular_market import (
    project_regular_market_candles,
    regular_market_source_minutes,
    required_regular_market_source_candles,
)


def _candle(stamp, price, volume=1):
    value = float(price)
    return {
        "time": stamp,
        "open": value,
        "high": value,
        "low": value,
        "close": value,
        "volume": float(volume),
    }


class IndicatorFollowValidationRegularMarketTest(unittest.TestCase):
    def test_source_interval_uses_five_minutes_for_large_minute_bars(self):
        self.assertEqual(1, regular_market_source_minutes(1))
        self.assertEqual(3, regular_market_source_minutes(3))
        for target in (5, 10, 15, 30, 60, 120, 240):
            with self.subTest(target=target):
                self.assertEqual(5, regular_market_source_minutes(target))

    def test_15_minute_keeps_1530_closing_auction_as_regular_bar(self):
        rows = [
            _candle("20260918150000", 100, 10),
            _candle("20260918150500", 101, 11),
            _candle("20260918151000", 102, 12),
            _candle("20260918151500", 103, 13),
            _candle("20260918153000", 110, 100),
            _candle("20260918153500", 111, 200),
            _candle("20260918160000", 120, 300),
        ]
        projected = project_regular_market_candles(rows, 15)
        self.assertEqual(
            ["20260918150000", "20260918151500", "20260918153000"],
            [item["time"] for item in projected],
        )
        self.assertEqual(110.0, projected[-1]["close"])
        self.assertEqual(100.0, projected[-1]["volume"])
        self.assertTrue(all(item["time"] < "20260918153500" for item in projected))

    def test_120_minute_merges_1530_auction_but_excludes_after_hours(self):
        rows = [
            _candle("20260918130000", 95, 10),
            _candle("20260918145500", 99, 20),
            _candle("20260918150000", 100, 30),
            _candle("20260918151500", 101, 40),
            _candle("20260918153000", 105, 500),
            _candle("20260918153500", 130, 1000),
            _candle("20260918160000", 140, 2000),
        ]
        projected = project_regular_market_candles(rows, 120)
        final_bar = projected[-1]
        self.assertEqual("20260918150000", final_bar["time"])
        self.assertEqual(100.0, final_bar["open"])
        self.assertEqual(105.0, final_bar["high"])
        self.assertEqual(100.0, final_bar["low"])
        self.assertEqual(105.0, final_bar["close"])
        self.assertEqual(570.0, final_bar["volume"])

    def test_240_minute_source_requirement_stays_within_broker_ceiling(self):
        required = required_regular_market_source_candles(240, 5060)
        self.assertLessEqual(required, 200_000)
        self.assertGreater(required, 190_000)

    def test_projection_does_not_create_empty_gap_bars(self):
        rows = [
            _candle("20260918090000", 100),
            _candle("20260918150000", 101),
            _candle("20260918153000", 102),
        ]
        projected = project_regular_market_candles(rows, 30)
        self.assertEqual(
            ["20260918090000", "20260918150000", "20260918153000"],
            [item["time"] for item in projected],
        )


if __name__ == "__main__":
    unittest.main()
