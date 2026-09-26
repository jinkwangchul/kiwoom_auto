from __future__ import annotations

import math
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace

from candle_timeframe_aggregation import (
    project_candle_supply,
    validate_market_bar_projection_request,
)
from engines.indicator_engine import (
    DEFAULT_INDICATOR_HISTORY_TARGET_BARS,
    price_box,
)
from gui_market_data_host import MarketDataHost
from indicator_follow_signal_validation_visualization import (
    FAMILY_MACD_SIGNAL,
    FAMILY_OCR_OSC,
    FAMILY_RSI,
    LOWER_AXIS,
    ValidationFilterDescriptor,
    build_validation_indicator_cache,
    required_validation_warmup_bars,
)
from indicator_follow_validation_history_contract import (
    required_validation_history_context_bars,
)
from routines.지표추종매매.routine import market_bar_projection_request
from routines.지표추종매매.routine_validation_contract import (
    ValidationRequest,
    ValidationSettingsSnapshot,
    ValidationStockRef,
)
from routines.지표추종매매.routine_validation_historical import (
    ValidationHistoricalSnapshot,
)
from routines.지표추종매매.routine_validation_replay import (
    ValidationHistoricalReplay,
)
from routines.지표추종매매.routine_validation_session import ValidationSession


def _stats(values: list[float]) -> tuple[float, float] | None:
    if not values:
        return None
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    return mean, math.sqrt(variance)
def _reference_price_box(
    values: list[float],
    period: int,
    history_window: int,
):
    lower = [None] * len(values)
    middle = [None] * len(values)
    upper = [None] * len(values)
    for index in range(period - 1, len(values)):
        start = max(0, index - history_window + 1)
        local = values[start : index + 1]
        local_middle = [None] * len(local)
        for local_index in range(period - 1, len(local)):
            window = local[local_index - period + 1 : local_index + 1]
            local_middle[local_index] = sum(window) / period
        center = local_middle[-1]
        middle[index] = center
        deviations = [
            local[local_index] - local_middle[local_index]
            for local_index in range(period - 1, len(local))
            if local_middle[local_index] is not None
        ]
        positive = _stats([value for value in deviations if value > 0])
        negative = _stats([value for value in deviations if value < 0])
        if positive is not None:
            upper[index] = center + positive[0] + (2 * positive[1])
        if negative is not None:
            lower[index] = center + negative[0] - (2 * negative[1])
    return lower, middle, upper


class IndicatorFollowKiwoomHistoryParityTest(unittest.TestCase):
    def test_projection_schema_separates_warmup_and_history_target(self):
        request = validate_market_bar_projection_request(
            {
                "projection": "FORMING_BASE_BAR",
                "warmup_bars": 15,
                "history_target_bars": 600,
            },
            require_warmup=True,
        )
        self.assertEqual(15, request["warmup_bars"])
        self.assertEqual(600, request["history_target_bars"])

        with self.assertRaises(ValueError):
            validate_market_bar_projection_request(
                {
                    "projection": "FORMING_BASE_BAR",
                    "warmup_bars": 35,
                    "history_target_bars": 34,
                },
                require_warmup=True,
            )
    def test_indicator_follow_declares_minimum_warmup_and_600_history_target(self):
        request = market_bar_projection_request(
            {
                "indicators": {"rsi": {"period": 14}},
                "buy": {"enabled": False},
                "sell": {
                    "enabled": True,
                    "signals": {
                        "rsi": {
                            "enabled": True,
                            "order_delay_bars": 0,
                            "groups": [{
                                "enabled": True,
                                "conditions": [{
                                    "target": "RSI",
                                    "period": 14,
                                    "operator": "<=",
                                    "value": 30,
                                }],
                            }],
                        },
                    },
                },
            }
        )
        self.assertEqual(15, request["warmup_bars"])
        self.assertEqual(
            DEFAULT_INDICATOR_HISTORY_TARGET_BARS,
            request["history_target_bars"],
        )
    def test_history_target_does_not_turn_into_availability_requirement(self):
        raw = [{"close": float(index)} for index in range(20)]
        result = project_candle_supply(
            raw,
            {"bar": {"bar_minutes": 1}},
            {
                "projection": "COMPLETED_TIMEFRAME",
                "warmup_bars": 15,
                "history_target_bars": 600,
            },
            completed_projector=lambda rows, _rules, **_kwargs: list(rows),
        )
        self.assertTrue(result["available"], result)
        self.assertEqual(20, len(result["candles"]))
        self.assertEqual(600, result["history_target_bars"])

    def test_history_target_caps_supplied_timeframe_history(self):
        raw = [{"close": float(index)} for index in range(700)]
        result = project_candle_supply(
            raw,
            {"bar": {"bar_minutes": 1}},
            {
                "projection": "COMPLETED_TIMEFRAME",
                "warmup_bars": 15,
                "history_target_bars": 600,
            },
            completed_projector=lambda rows, _rules, **_kwargs: list(rows),
        )
        self.assertTrue(result["available"], result)
        self.assertEqual(600, len(result["candles"]))
        self.assertEqual(100.0, result["candles"][0]["close"])
        self.assertEqual(699.0, result["candles"][-1]["close"])

    def test_market_data_retention_uses_history_target_not_minimum_warmup(self):
        host = SimpleNamespace(
            _candle_standby_required_by_stock={},
            _candle_standby_stock_codes=(),
        )
        result = MarketDataHost.sync_candle_standby_requirements(
            host,
            [{
                "stock_code": "005930",
                "rules": {"bar": {"bar_minutes": 5}},
                "projection_request": {
                    "projection": "FORMING_BASE_BAR",
                    "warmup_bars": 35,
                    "history_target_bars": 600,
                },
            }],
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual(
            3300,
            host._candle_standby_required_by_stock["005930"],
        )

    def test_240_minute_600_bar_history_target_stays_within_global_limit(self):
        host = SimpleNamespace(
            _candle_standby_required_by_stock={},
            _candle_standby_stock_codes=(),
        )
        result = MarketDataHost.sync_candle_standby_requirements(
            host,
            [{
                "stock_code": "005930",
                "rules": {"bar": {"bar_minutes": 240}},
                "projection_request": {
                    "projection": "FORMING_BASE_BAR",
                    "warmup_bars": 35,
                    "history_target_bars": 600,
                },
            }],
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual(
            158400,
            host._candle_standby_required_by_stock["005930"],
        )

    def test_validation_keeps_minimum_warmup_separate_from_hidden_history(self):
        rules = {
            "indicators": {"price_box": {"period": 24}},
            "buy": {"enabled": False},
            "sell": {
                "enabled": True,
                "signals": {
                    "box": {
                        "enabled": True,
                        "groups": [{
                            "enabled": True,
                            "conditions": [{
                                "target": "CLOSE",
                                "operator": ">=",
                                "compare_target": "PRICE_BOX_UPPER",
                            }],
                        }],
                    },
                },
            },
        }
        self.assertEqual(24, required_validation_warmup_bars(rules))
        self.assertEqual(
            600,
            required_validation_history_context_bars(rules),
        )
    def test_price_box_matches_causal_trailing_loaded_history_reference(self):
        values = [
            100.0
            + (index * 0.17)
            + (((index % 17) - 8) * 2.5)
            + (((index % 5) - 2) * 0.8)
            for index in range(750)
        ]
        actual = price_box(values, 24, history_window=600)
        expected = _reference_price_box(values, 24, 600)
        for actual_series, expected_series in zip(actual, expected):
            self.assertEqual(len(expected_series), len(actual_series))
            for actual_value, expected_value in zip(
                actual_series,
                expected_series,
            ):
                if expected_value is None:
                    self.assertIsNone(actual_value)
                else:
                    self.assertAlmostEqual(
                        expected_value,
                        actual_value,
                        places=9,
                    )

    def test_price_box_uses_all_available_history_before_600_bar_cap(self):
        values = [
            150.0 + (index * 0.21) + (((index % 9) - 4) * 1.7)
            for index in range(180)
        ]
        actual = price_box(values, 24, history_window=600)
        expected = _reference_price_box(values, 24, 600)
        for actual_series, expected_series in zip(actual, expected):
            for actual_value, expected_value in zip(
                actual_series,
                expected_series,
            ):
                if expected_value is None:
                    self.assertIsNone(actual_value)
                else:
                    self.assertAlmostEqual(
                        expected_value,
                        actual_value,
                        places=9,
                    )

    def test_price_box_remains_prefix_causal(self):
        values = [
            200.0 + index + (((index % 11) - 5) * 3.0)
            for index in range(700)
        ]
        full = price_box(values, 24, history_window=600)
        prefix = price_box(values[:640], 24, history_window=600)
        for full_series, prefix_series in zip(full, prefix):
            self.assertEqual(prefix_series, full_series[:640])

    def test_recursive_indicator_series_match_trailing_600_window(self):
        from routines.지표추종매매.routine_macd_engine import (
            build_indicator_follow_base_series,
        )

        candles = [
            {
                "close": 100000.0
                + (index * 3.0)
                + math.sin(index / 13.0) * 5000.0,
                "volume": 1,
            }
            for index in range(650)
        ]
        rules = {
            "indicators": {
                "rsi": {"period": 14},
                "macd": {"fast": 12, "slow": 26, "signal": 9},
            },
            "buy": {"enabled": False},
            "sell": {"enabled": False, "signals": {}},
        }
        full = build_indicator_follow_base_series(candles, rules)
        trailing = build_indicator_follow_base_series(candles[-600:], rules)
        for key in ("RSI", "MACD", "SIGNAL", "OSC"):
            self.assertAlmostEqual(
                trailing[key][-1],
                full[key][-1],
                places=10,
            )

    def test_visualization_recursive_series_match_trailing_600_window(self):
        candles = [
            {
                "close": 100000.0
                + (index * 3.0)
                + math.sin(index / 13.0) * 5000.0,
                "volume": 1,
            }
            for index in range(650)
        ]
        rules = {
            "indicators": {
                "rsi": {"period": 14},
                "macd": {"fast": 12, "slow": 26, "signal": 9},
            },
        }
        descriptors = (
            ValidationFilterDescriptor(
                "RSI:parity",
                FAMILY_RSI,
                "RSI",
                LOWER_AXIS,
                ("BUY",),
                ("RSI",),
                '{"period":14,"condition":{}}',
                (),
            ),
            ValidationFilterDescriptor(
                "MACD:parity",
                FAMILY_MACD_SIGNAL,
                "MACD",
                LOWER_AXIS,
                ("BUY",),
                ("MACD", "SIGNAL"),
                '{"fast":12,"slow":26,"signal":9,"condition":{}}',
                (),
            ),
            ValidationFilterDescriptor(
                "OSC:parity",
                FAMILY_OCR_OSC,
                "OCR",
                LOWER_AXIS,
                ("BUY",),
                ("OSC",),
                '{"fast":12,"slow":26,"signal":9,"condition":{}}',
                (),
            ),
        )
        full = build_validation_indicator_cache(
            candles,
            rules,
            descriptors,
        )
        trailing = build_validation_indicator_cache(
            candles[-600:],
            rules,
            descriptors,
        )
        for identity, channel in (
            ("RSI:parity", "RSI"),
            ("MACD:parity", "MACD"),
            ("MACD:parity", "SIGNAL"),
            ("OSC:parity", "OSC"),
        ):
            self.assertAlmostEqual(
                trailing.values_for(identity, channel)[-1],
                full.values_for(identity, channel)[-1],
                places=10,
            )

    def test_batch_fast_path_supports_history_over_600_bars(self):
        from routines.지표추종매매.routine_validation_batch import (
            scan_indicator_follow_validation_batch,
        )

        candles = [
            {"time": f"20260925{index:06d}", "close": float(index + 1)}
            for index in range(601)
        ]
        result = scan_indicator_follow_validation_batch(
            candles,
            {
                "enabled": True,
                "buy": {"enabled": False},
                "sell": {"enabled": False, "signals": {}},
            },
        )
        self.assertTrue(result.supported, result)
        self.assertIsNone(result.fallback_reason)

    def test_replay_caps_indicator_history_and_remaps_global_indexes(self):
        stock = ValidationStockRef("005930", "삼성전자")
        rules = {
            "enabled": True,
            "bar_minutes": 1,
            "buy": {"enabled": False},
            "sell": {
                "enabled": True,
                "signal_logic": "OR",
                "signals": {
                    "sell": {
                        "enabled": True,
                        "order_delay_bars": 1,
                        "groups": [{
                            "enabled": True,
                            "conditions": [{
                                "enabled": True,
                                "target": "CLOSE",
                                "operator": ">=",
                                "value": 0.0,
                            }],
                        }],
                    },
                },
            },
        }
        settings = ValidationSettingsSnapshot(rules)
        session = ValidationSession(
            ValidationRequest(stock, settings, 1),
            operation_active_reader=lambda: False,
        )
        rows = []
        start = datetime(2026, 9, 1, 9, 0)
        for index in reversed(range(651)):
            close = float(1000 + index)
            rows.append({
                "체결시간": (
                    start + timedelta(minutes=index)
                ).strftime("%Y%m%d%H%M%S"),
                "시가": str(close),
                "고가": str(close),
                "저가": str(close),
                "현재가": str(close),
                "거래량": "1",
            })
        historical = ValidationHistoricalSnapshot(
            stock=stock,
            timeframe_minutes=1,
            requested_count=len(rows),
            request_id="HISTORY-600-REMAP",
            rows=rows,
        )

        result = ValidationHistoricalReplay(session).evaluate(
            historical,
            start_index=650,
            end_index=650,
        )
        self.assertTrue(result.ok, result)
        sell = next(
            entry
            for entry in result.snapshot.to_entries()
            if entry.evaluation_side == "SELL"
            and entry.signal == "SELL"
        )
        self.assertEqual(650, sell.evaluation_index)
        self.assertEqual(649, sell.signal_index)
        condition = next(
            item
            for item in sell.trace["conditions"]
            if item["left_operand"]["key"] == "CLOSE"
        )
        self.assertEqual(649, condition["left_operand"]["index"])
        self.assertEqual(
            1649.0,
            condition["left_operand"]["value"],
        )
        snapshot = condition["indicator_snapshots"][0]
        self.assertEqual(649, snapshot["index"])

        scanned = ValidationHistoricalReplay(session).scan_signal_entries(
            historical,
            start_index=650,
            end_index=650,
        )
        scanned_sell = next(
            entry
            for entry in scanned
            if entry.evaluation_side == "SELL"
            and entry.signal == "SELL"
        )
        self.assertEqual(650, scanned_sell.evaluation_index)
        self.assertEqual(649, scanned_sell.signal_index)
        scanned_condition = next(
            item
            for item in scanned_sell.trace["conditions"]
            if item["left_operand"]["key"] == "CLOSE"
        )
        self.assertEqual(
            649,
            scanned_condition["left_operand"]["index"],
        )


if __name__ == "__main__":
    unittest.main()
