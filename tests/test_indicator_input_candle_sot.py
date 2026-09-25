from __future__ import annotations

from datetime import datetime, timedelta
import json
import unittest
from unittest.mock import patch

import engines.indicator_engine as indicator_engine
import indicator_follow_signal_validation_visualization as visualization
from candle_timeframe_aggregation import (
    SEOUL_TIMEZONE,
    aggregate_minute_candles,
)
from indicator_follow_signal_validation_visualization import (
    FAMILY_BOLLINGER,
    FAMILY_MACD_SIGNAL,
    FAMILY_MOVING_AVERAGE,
    FAMILY_OCR_OSC,
    FAMILY_PRICE_BOX,
    FAMILY_RSI,
    LOWER_AXIS,
    PRICE_AXIS,
    ValidationFilterDescriptor,
    build_validation_indicator_cache,
)


def _raw_minutes() -> list[dict]:
    start = datetime(2026, 9, 23, 9, 0, tzinfo=SEOUL_TIMEZONE)
    result = []
    for index in range(390):
        bar_time = start + timedelta(minutes=index)
        base = 100.0 + (index * 0.1)
        result.append({
            "time": bar_time.strftime("%Y%m%d%H%M%S"),
            "open": base,
            "high": base + 0.4,
            "low": base - 0.3,
            "close": base + 0.1,
            "volume": float(index + 1),
        })
    return result


def _thirty_minute_candles() -> list[dict]:
    projected = aggregate_minute_candles(
        _raw_minutes(),
        30,
        now=datetime(2026, 9, 24, 9, 0, tzinfo=SEOUL_TIMEZONE),
    )
    return [item for item in projected if item["is_complete"] is True]


def _indicator_config() -> dict:
    return {
        "indicators": {
            "macd": {"fast": 3, "slow": 5, "signal": 2},
            "rsi": {"period": 4},
            "moving_averages": [3, 5],
            "bollinger": {"period": 4, "std": 2.0},
            "price_box": {"period": 4},
        },
    }


def _descriptor(
    identity: str,
    family: str,
    series_keys: tuple[str, ...],
    parameters: dict,
    *,
    axis: str,
) -> ValidationFilterDescriptor:
    return ValidationFilterDescriptor(
        identity=identity,
        family=family,
        label=family,
        axis=axis,
        sides=("BUY",),
        series_keys=series_keys,
        parameter_json=json.dumps(parameters, sort_keys=True),
        evidence_keys=(),
    )


class IndicatorInputCandleSoTTest(unittest.TestCase):
    def test_30_minute_projection_and_equivalent_visible_candles_match(self):
        aggregated = _thirty_minute_candles()
        self.assertEqual(13, len(aggregated))

        visible = [
            {
                "time": item["bar_time"],
                "open": item["open"],
                "high": item["high"],
                "low": item["low"],
                "close": item["close"],
                "volume": item["volume"],
            }
            for item in aggregated
        ]
        self.assertEqual(
            [
                tuple(item[field] for field in ("open", "high", "low", "close", "volume"))
                for item in aggregated
            ],
            [
                tuple(item[field] for field in ("open", "high", "low", "close", "volume"))
                for item in visible
            ],
        )

        config = _indicator_config()
        from_aggregation = indicator_engine.build_indicator_series(aggregated, config)
        from_visible = indicator_engine.build_indicator_series(visible, config)
        self.assertEqual(from_aggregation, from_visible)
    def test_production_indicator_engine_shares_one_close_series(self):
        candles = _thirty_minute_candles()
        originals = {
            "macd_series": indicator_engine.macd_series,
            "rsi": indicator_engine.rsi,
            "bollinger_band": indicator_engine.bollinger_band,
            "price_box": indicator_engine.price_box,
            "simple_ma": indicator_engine.simple_ma,
        }
        seen: list[tuple[str, int, tuple[float | None, ...]]] = []

        def wrap(name):
            original = originals[name]

            def recorded(values, *args, **kwargs):
                seen.append((name, id(values), tuple(values)))
                return original(values, *args, **kwargs)

            return recorded

        with (
            patch.object(indicator_engine, "macd_series", wrap("macd_series")),
            patch.object(indicator_engine, "rsi", wrap("rsi")),
            patch.object(indicator_engine, "bollinger_band", wrap("bollinger_band")),
            patch.object(indicator_engine, "price_box", wrap("price_box")),
            patch.object(indicator_engine, "simple_ma", wrap("simple_ma")),
        ):
            series = indicator_engine.build_indicator_series(
                candles,
                _indicator_config(),
            )

        expected_close = tuple(indicator_engine.close_prices(candles))
        self.assertTrue(seen)
        self.assertEqual({expected_close}, {item[2] for item in seen})
        self.assertEqual(1, len({item[1] for item in seen}))
        self.assertEqual(expected_close, tuple(series["CLOSE"]))
        self.assertEqual(
            tuple(item["volume"] for item in candles),
            tuple(series["VOLUME"]),
        )

    def test_validation_cache_shares_one_close_series(self):
        candles = _thirty_minute_candles()
        descriptors = (
            _descriptor(
                "rsi",
                FAMILY_RSI,
                ("RSI", "CRITERION"),
                {"period": 4, "condition": {"threshold": 50.0}},
                axis=LOWER_AXIS,
            ),
            _descriptor(
                "macd",
                FAMILY_MACD_SIGNAL,
                ("MACD", "SIGNAL", "CRITERION"),
                {
                    "fast": 3,
                    "slow": 5,
                    "signal": 2,
                    "condition": {"target": "MACD", "threshold": 0.0},
                },
                axis=LOWER_AXIS,
            ),
            _descriptor(
                "osc",
                FAMILY_OCR_OSC,
                ("OSC",),
                {
                    "fast": 3,
                    "slow": 5,
                    "signal": 2,
                    "condition": {"target": "OSC", "operator": "TURN_UP"},
                },
                axis=LOWER_AXIS,
            ),
            _descriptor(
                "ma",
                FAMILY_MOVING_AVERAGE,
                ("MA3", "MA5"),
                {
                    "periods": [3, 5],
                    "condition": {"target": "MA3", "compare_target": "MA5"},
                },
                axis=PRICE_AXIS,
            ),
            _descriptor(
                "bollinger",
                FAMILY_BOLLINGER,
                ("CRITERION",),
                {
                    "period": 4,
                    "std": 2.0,
                    "condition": {
                        "target": "CLOSE",
                        "compare_target": "BOLLINGER_LOWER",
                        "value": 0.0,
                    },
                },
                axis=PRICE_AXIS,
            ),
            _descriptor(
                "price-box",
                FAMILY_PRICE_BOX,
                ("CRITERION",),
                {
                    "period": 4,
                    "condition": {
                        "target": "CLOSE",
                        "compare_target": "PRICE_BOX_LOWER",
                        "value": 0.0,
                    },
                },
                axis=PRICE_AXIS,
            ),
        )
        originals = {
            "macd_series": visualization.macd_series,
            "rsi": visualization.rsi,
            "bollinger_band": visualization.bollinger_band,
            "price_box": visualization.price_box,
            "simple_ma": visualization.simple_ma,
        }
        seen: list[tuple[str, int, tuple[float | None, ...]]] = []

        def wrap(name):
            original = originals[name]

            def recorded(values, *args, **kwargs):
                seen.append((name, id(values), tuple(values)))
                return original(values, *args, **kwargs)

            return recorded

        with (
            patch.object(visualization, "macd_series", wrap("macd_series")),
            patch.object(visualization, "rsi", wrap("rsi")),
            patch.object(visualization, "bollinger_band", wrap("bollinger_band")),
            patch.object(visualization, "price_box", wrap("price_box")),
            patch.object(visualization, "simple_ma", wrap("simple_ma")),
        ):
            cache = build_validation_indicator_cache(
                candles,
                _indicator_config(),
                descriptors,
            )
        expected_close = tuple(indicator_engine.close_prices(candles))
        self.assertTrue(seen)
        self.assertEqual({expected_close}, {item[2] for item in seen})
        self.assertEqual(1, len({item[1] for item in seen}))
        self.assertEqual(len(candles), cache.candle_count)


if __name__ == "__main__":
    unittest.main()
