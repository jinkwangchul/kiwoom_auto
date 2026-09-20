# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from indicator_follow_signal_validation_historical_cache import (
    IndicatorFollowSignalValidationHistoricalCache,
    merge_validation_candles,
)


def _candle(index: int, *, close: float | None = None) -> dict[str, object]:
    value = float(index if close is None else close)
    return {
        "time": f"2026091810{index:02d}00",
        "open": value - 1.0,
        "high": value + 1.0,
        "low": value - 2.0,
        "close": value,
        "volume": float(index + 10),
    }


class SignalValidationHistoricalCacheTest(unittest.TestCase):
    def test_store_round_trip_is_exact_keyed_and_readback_verified(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = IndicatorFollowSignalValidationHistoricalCache(temp_dir)
            candles = [_candle(index) for index in range(5)]

            self.assertTrue(cache.store(
                stock_code="005930",
                stock_name="삼성전자",
                timeframe_minutes=3,
                requested_count=5,
                candles=candles,
            ))

            entry = cache.load("005930", 3, 5)
            self.assertIsNotNone(entry)
            self.assertEqual("005930", entry.stock_code)
            self.assertEqual("삼성전자", entry.stock_name)
            self.assertEqual(3, entry.timeframe_minutes)
            self.assertEqual(5, entry.requested_count)
            self.assertEqual(candles, list(entry.candles))
            self.assertIsNone(cache.load("005930", 5, 5))
            self.assertIsNone(cache.load("005930", 3, 6))

    def test_identity_corruption_fails_closed_without_cross_key_reuse(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = IndicatorFollowSignalValidationHistoricalCache(temp_dir)
            self.assertTrue(cache.store(
                stock_code="005930",
                stock_name="삼성전자",
                timeframe_minutes=3,
                requested_count=3,
                candles=[_candle(index) for index in range(3)],
            ))
            path = cache.path_for("005930", 3, 3)
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["stock"]["code"] = "000660"
            path.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )

            self.assertIsNone(cache.load("005930", 3, 3))

    def test_merge_deduplicates_by_time_sorts_and_trims_to_requested_count(self):
        existing = [_candle(index) for index in range(4)]
        replacement = _candle(3, close=999.0)
        incoming = [replacement, _candle(4), _candle(5)]

        merged = merge_validation_candles(existing, incoming, 4)

        self.assertEqual(
            ["20260918100200", "20260918100300", "20260918100400", "20260918100500"],
            [item["time"] for item in merged],
        )
        self.assertEqual(999.0, merged[1]["close"])

    def test_invalid_payload_is_a_cache_miss(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = IndicatorFollowSignalValidationHistoricalCache(temp_dir)
            path = cache.path_for("005930", 3, 5)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{not-json", encoding="utf-8")

            self.assertIsNone(cache.load("005930", 3, 5))


if __name__ == "__main__":
    unittest.main()