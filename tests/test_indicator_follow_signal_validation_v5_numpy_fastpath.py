from __future__ import annotations

import math
import struct
import unittest
import json
from pathlib import Path
from unittest.mock import patch

from engines.indicator_engine import (
    indicator_history_target_bars,
    macd_series_causal_history,
    rsi_causal_history,
)
import indicator_follow_signal_validation_visualization as visualization


def _exact(values):
    return tuple(
        None if value is None else struct.pack("!d", float(value))
        for value in values
    )


def _closes(count: int) -> list[float]:
    return [100000.0 + index * 17.0 + ((index % 19) - 9) * 31.0 for index in range(count)]


def _cached_or_synthetic_closes() -> list[float]:
    path = (Path(__file__).resolve().parents[1]
            / "runtime/signal_validation_v2_history_cache/000660_3_5900.json")
    if path.is_file():
        rows = json.loads(path.read_text(encoding="utf-8")).get("candles", [])
        closes = [float(row["close"]) for row in rows]
        if len(closes) == 5900 and all(math.isfinite(value) for value in closes):
            return closes
    return _closes(5900)


def _old_rsi(closes, period, history_window):
    if history_window <= 0 or period <= 0:
        return rsi_causal_history(closes, period, history_window)
    window = max(int(history_window), int(period) + 1)
    if len(closes) <= window or any(value is None or not math.isfinite(value) for value in closes):
        return rsi_causal_history(closes, period, history_window)
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
        for gain, loss in zip(gains[start + period + 1:index + 1], losses[start + period + 1:index + 1]):
            avg_gain = ((avg_gain * (period - 1)) + gain) / period
            avg_loss = ((avg_loss * (period - 1)) + loss) / period
        if avg_gain == 0 and avg_loss == 0:
            values.append(0.0)
        elif avg_loss == 0:
            values.append(100.0)
        else:
            values.append(100 - (100 / (1 + avg_gain / avg_loss)))
    return values


def _old_macd(closes, fast, slow, signal_period, history_window):
    window = max(int(history_window), int(slow) + int(signal_period))
    seed = max(int(fast), int(slow))
    if (min(fast, slow, signal_period) <= 0 or len(closes) <= window + 16
            or window < max(seed, int(signal_period))
            or any(value is None or not math.isfinite(value) for value in closes)):
        return macd_series_causal_history(closes, fast, slow, signal_period, history_window)
    result = macd_series_causal_history(closes[:window], fast, slow, signal_period, window)
    macd_values, signal_values, osc_values = result
    fm, sm, sigmult = 2 / (fast + 1), 2 / (slow + 1), 2 / (signal_period + 1)
    for index in range(window, len(closes)):
        local = closes[index - window + 1:index + 1]
        fs = ss = float(local[0])
        fp, sp = [fs], [ss]
        for close in local[1:seed]:
            fs = (float(close) - fs) * fm + fs
            ss = (float(close) - ss) * sm + ss
            fp.append(fs)
            sp.append(ss)
        fast_seed, slow_seed = fp[fast - 1], sp[slow - 1]
        signal_state = None
        for pos in range(seed):
            macd_value = (fp[pos] if pos >= fast else fast_seed) - (sp[pos] if pos >= slow else slow_seed)
            signal_state = macd_value if signal_state is None else (macd_value - signal_state) * sigmult + signal_state
        for close in local[seed:]:
            fs = (float(close) - fs) * fm + fs
            ss = (float(close) - ss) * sm + ss
            macd_value = fs - ss
            signal_state = (macd_value - signal_state) * sigmult + signal_state
        macd_values.append(macd_value)
        signal_values.append(signal_state)
        osc_values.append(macd_value - signal_state)
    return macd_values, signal_values, osc_values


class NumpyFastPathParityTest(unittest.TestCase):
    def test_dense_target_timeframes_match_preopt_reference_bitwise(self):
        closes = _cached_or_synthetic_closes()
        windows = ("M1", "M3", "M5", "M10", "M15", "M30", "M60", "M120", "M240", "D1", "W1", "MO1", "Y1")
        with patch.object(visualization, "_optional_numpy", wraps=visualization._optional_numpy) as loader:
            for timeframe in windows:
                window = indicator_history_target_bars(timeframe)
                data = closes[-(window + 32):]
                for period in (2, 14, 28):
                    self.assertEqual(_exact(_old_rsi(data, period, window)), _exact(visualization._display_rsi_causal_history(data, period, window)))
                for params in ((12, 26, 9), (5, 35, 5)):
                    self.assertEqual(tuple(map(_exact, _old_macd(data, *params, window))), tuple(map(_exact, visualization._display_macd_causal_history(data, *params, window))))
        self.assertGreater(loader.call_count, 0)

    def test_missing_numpy_and_unsupported_inputs_use_unchanged_fallback(self):
        dense = _closes(950)
        cases = ([None if index % 43 == 0 else value for index, value in enumerate(dense)], [float("nan") if index == 11 else value for index, value in enumerate(dense)], [float("inf") if index == 12 else value for index, value in enumerate(dense)], _closes(3))
        with patch.object(visualization, "_optional_numpy", return_value=None):
            self.assertEqual(_exact(_old_rsi(dense, 14, 900)), _exact(visualization._display_rsi_causal_history(dense, 14, 900)))
            self.assertEqual(tuple(map(_exact, _old_macd(dense, 12, 26, 9, 900))), tuple(map(_exact, visualization._display_macd_causal_history(dense, 12, 26, 9, 900))))
            for values in cases:
                self.assertEqual(_exact(_old_rsi(values, 14, 900)), _exact(visualization._display_rsi_causal_history(values, 14, 900)))
                self.assertEqual(tuple(map(_exact, _old_macd(values, 12, 26, 9, 900))), tuple(map(_exact, visualization._display_macd_causal_history(values, 12, 26, 9, 900))))

    def test_full_5000_bar_cache_projection_matches_preopt_reference(self):
        closes = _cached_or_synthetic_closes()[-5000:]
        candles = [{"time": f"20261016{index:06d}", "close": value} for index, value in enumerate(closes)]
        rsi = visualization.ValidationFilterDescriptor(
            "RSI:numpy-fastpath", visualization.FAMILY_RSI, "RSI", visualization.LOWER_AXIS,
            ("BUY",), ("RSI",), '{"period":14,"condition":{}}', (),
        )
        macd = visualization.ValidationFilterDescriptor(
            "MACD:numpy-fastpath", visualization.FAMILY_MACD_SIGNAL, "MACD", visualization.LOWER_AXIS,
            ("BUY",), ("MACD", "SIGNAL", "OSC"),
            '{"fast":12,"slow":26,"signal":9,"condition":{}}', (),
        )
        cache = visualization.build_validation_indicator_cache(
            candles, {"validation_timeframe": "M1"}, (rsi, macd),
        )
        self.assertEqual(_exact(_old_rsi(closes, 14, 900)), _exact(cache.values_for(rsi.identity, "RSI")))
        expected = _old_macd(closes, 12, 26, 9, 900)
        for channel, values in zip(("MACD", "SIGNAL", "OSC"), expected):
            self.assertEqual(_exact(values), _exact(cache.values_for(macd.identity, channel)))
