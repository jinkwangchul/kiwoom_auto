from __future__ import annotations

import json
import math
import struct
import unittest
from pathlib import Path

from engines.indicator_engine import indicator_history_target_bars, rsi_causal_history
from indicator_follow_signal_validation_visualization import (
    FAMILY_RSI,
    LOWER_AXIS,
    ValidationFilterDescriptor,
    _display_rsi_causal_history,
    build_validation_indicator_cache,
)


def _display_rsi_causal_history_pre_change(
    closes: list[float | None],
    period: int,
    history_window: int,
) -> list[float | None]:
    """Exact per-window RSI tails with precomputed price changes for chart display."""
    if history_window <= 0 or period <= 0:
        return rsi_causal_history(closes, period, history_window)
    window = max(int(history_window), int(period) + 1)
    if (len(closes) <= window
            or any(value is None or not math.isfinite(value) for value in closes)):
        return rsi_causal_history(closes, period, history_window)

    # Keep the canonical prefix and Wilder recurrence, including both zero cases.
    values = rsi_causal_history(closes[:window], period, window)
    gains = [0.0] * len(closes)
    losses = [0.0] * len(closes)
    for index in range(1, len(closes)):
        change = closes[index] - closes[index - 1]
        gains[index] = max(change, 0.0)
        losses[index] = max(-change, 0.0)
    for index in range(window, len(closes)):
        start = index - window + 1
        avg_gain = sum(gains[start + 1:start + period + 1]) / period
        avg_loss = sum(losses[start + 1:start + period + 1]) / period
        for local in range(start + period + 1, index + 1):
            avg_gain = ((avg_gain * (period - 1)) + gains[local]) / period
            avg_loss = ((avg_loss * (period - 1)) + losses[local]) / period
        if avg_gain == 0 and avg_loss == 0:
            values.append(0.0)
        elif avg_loss == 0:
            values.append(100.0)
        else:
            values.append(100 - (100 / (1 + avg_gain / avg_loss)))
    return values


def _exact(values: list[float | None] | tuple[float | None, ...]):
    return tuple(None if value is None else struct.pack("!d", float(value)) for value in values)


def _synthetic_closes(count: int) -> list[float]:
    return [100000.0 + (index * 17.0) + ((index % 19) - 9) * 31.0 for index in range(count)]


def _cached_or_synthetic_closes() -> list[float]:
    path = (Path(__file__).resolve().parents[1]
            / "runtime/signal_validation_v2_history_cache/000660_3_5900.json")
    if path.is_file():
        rows = json.loads(path.read_text(encoding="utf-8")).get("candles", [])
        closes = [float(row["close"]) for row in rows]
        if len(closes) == 5900 and all(math.isfinite(value) for value in closes):
            return closes
    return _synthetic_closes(5900)


class ValidationDisplayRsiLoopParityTest(unittest.TestCase):
    def assert_rsi_parity(self, closes, period, history_window):
        self.assertEqual(
            _exact(_display_rsi_causal_history_pre_change(closes, period, history_window)),
            _exact(_display_rsi_causal_history(closes, period, history_window)),
        )

    def test_all_target_timeframes_and_periods_are_bit_exact(self):
        closes = _cached_or_synthetic_closes()
        targets = {
            "M1": 900, "M3": 900, "M5": 900, "M10": 900, "M15": 900,
            "M30": 900, "M60": 900, "M120": 900, "M240": 900, "D1": 600,
            "W1": 300, "MO1": 120, "Y1": 120,
        }
        for timeframe, expected_window in targets.items():
            history_window = indicator_history_target_bars(timeframe)
            self.assertEqual(expected_window, history_window)
            for period in (2, 14, 28):
                with self.subTest(timeframe=timeframe, period=period):
                    self.assert_rsi_parity(closes[-(history_window + 32):], period, history_window)

    def test_fallbacks_and_warmup_boundary_are_bit_exact(self):
        cases = {
            "plateau": [100.0] * 40,
            "zero_gains": [100.0 - index for index in range(40)],
            "zero_losses": [100.0 + index for index in range(40)],
            "alternating": [100.0 + (1.0 if index % 2 else -1.0) for index in range(40)],
            "sparse_none": [None if index % 7 == 0 else float(index) for index in range(40)],
            "nonfinite": [float("nan") if index == 9 else float(index) for index in range(40)],
            "short_history": [100.0, 101.0, 99.0],
            "warmup_boundary": _synthetic_closes(16),
            "negative_positive": [-10.0, -8.0, -12.0, -1.0, 0.0, 3.0, -2.0, 9.0] * 5,
        }
        for name, closes in cases.items():
            with self.subTest(case=name):
                self.assert_rsi_parity(closes, 14, 15)

    def test_cache_rsi_projection_is_bit_exact_with_actual_descriptor(self):
        closes = _synthetic_closes(950)
        candles = [{"time": f"20261016{index:06d}", "close": close} for index, close in enumerate(closes)]
        descriptor = ValidationFilterDescriptor(
            "RSI:loop-parity", FAMILY_RSI, "RSI", LOWER_AXIS, ("BUY",),
            ("RSI",), '{"period":14,"condition":{}}', (),
        )
        cache = build_validation_indicator_cache(
            candles, {"validation_timeframe": "M1"}, (descriptor,),
        )
        self.assertEqual(
            _exact(_display_rsi_causal_history_pre_change(closes, 14, 900)),
            _exact(cache.values_for(descriptor.identity, "RSI")),
        )
