from __future__ import annotations

import unittest

from engines.condition_engine import evaluate_condition
from engines.indicator_engine import rsi
from indicator_follow_signal_validation_visualization import (
    FAMILY_RSI,
    build_validation_filter_universe,
    build_validation_indicator_cache,
)
from routines.지표추종매매.routine import market_bar_projection_request
from routines.지표추종매매.routine_macd_engine import (
    evaluate_indicator_follow_routine,
)
from routines.지표추종매매.routine_validation_batch import (
    scan_indicator_follow_validation_batch,
)
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
from routines.지표추종매매.routine_validation_trace import ValidationTraceObserver


class IndicatorFollowRsiPeriodParityTest(unittest.TestCase):
    stock = ValidationStockRef("005930", "삼성전자")
    closes = (
        100, 101, 102, 103, 104, 105, 104, 103, 102, 101,
        100, 99, 100, 101, 102, 103, 104, 103, 102, 101,
    )

    @classmethod
    def candles(cls):
        return [
            {"time": f"20260925{90000 + index * 100:06d}", "close": float(close)}
            for index, close in enumerate(cls.closes)
        ]

    @staticmethod
    def rules(period=2, threshold=20.0):
        return {
            "enabled": True,
            "indicators": {"rsi": {"period": 14}},
            "buy": {"enabled": False},
            "sell": {
                "enabled": True,
                "signal_logic": "OR",
                "signals": {
                    "rsi_sell": {
                        "enabled": True,
                        "order_delay_bars": 0,
                        "groups": [{
                            "enabled": True,
                            "conditions": [{
                                "enabled": True,
                                "expression_id": "RSI_0",
                                "target": "RSI",
                                "period": period,
                                "operator": "<=",
                                "value": threshold,
                            }],
                        }],
                    },
                },
            },
        }

    def test_rsi_series_uses_wilder_initial_average_and_recursive_smoothing(self):
        values = [100.0, 102.0, 101.0, 104.0, 103.0]
        actual = rsi(values, 3)

        self.assertEqual([None, None, None], actual[:3])
        self.assertAlmostEqual(83.33333333333333, actual[3])
        self.assertAlmostEqual(66.66666666666666, actual[4])

    def test_production_sell_uses_condition_period_not_global_period(self):
        observer = ValidationTraceObserver()
        result = evaluate_indicator_follow_routine(
            self.candles(),
            self.rules(),
            {
                "_indicator_follow_evaluate_side": "SELL",
                "decision_trace_observer": observer,
            },
        )

        expected = rsi(list(self.closes), 2)[-1]
        global_value = rsi(list(self.closes), 14)[-1]
        self.assertLess(expected, 20.0)
        self.assertGreater(global_value, 20.0)
        self.assertEqual("SELL", result.signal)
        payload = next(
            item for item in observer.snapshot()["conditions"]
            if item["expression_id"] == "RSI_0"
        )
        self.assertEqual("RSI", payload["left_operand"]["key"])
        self.assertAlmostEqual(expected, payload["left_operand"]["value"], places=10)
        self.assertEqual(2, payload["indicator_snapshots"][0]["period"])

    def test_invalid_explicit_sell_rsi_period_fails_closed(self):
        rules = self.rules(period=0, threshold=100.0)
        result = evaluate_indicator_follow_routine(
            self.candles(),
            rules,
            {"_indicator_follow_evaluate_side": "SELL"},
        )
        self.assertIsNone(result.signal)

    def test_forged_private_series_override_is_rejected(self):
        condition = {
            "target": "RSI",
            "period": 2,
            "operator": "<=",
            "value": 20.0,
            "_series_key_override": "CLOSE",
        }
        series_map = {
            "RSI": [50.0, 50.0],
            "CLOSE": [10.0, 10.0],
            "_INDICATOR_FOLLOW_RSI_2": [12.0, 12.0],
        }
        result = evaluate_condition(condition, series_map, 1)
        self.assertFalse(result.passed)

    def test_persisted_private_override_is_discarded_and_rederived(self):
        rules = self.rules()
        condition = rules["sell"]["signals"]["rsi_sell"]["groups"][0]["conditions"][0]
        condition["_series_key_override"] = "_INDICATOR_FOLLOW_RSI_14"
        result = evaluate_indicator_follow_routine(
            self.candles(),
            rules,
            {"_indicator_follow_evaluate_side": "SELL"},
        )
        self.assertEqual("SELL", result.signal)

    def test_sell_condition_period_controls_warmup(self):
        projection = market_bar_projection_request(self.rules(period=30, threshold=30))
        self.assertEqual(31, projection["warmup_bars"])

    def _historical(self):
        return ValidationHistoricalSnapshot(
            stock=self.stock,
            timeframe_minutes=5,
            requested_count=len(self.closes),
            request_id="RSI-PERIOD-PARITY",
            rows=[
                {
                    "체결시간": f"20260925{90000 + index * 100:06d}",
                    "시가": str(close),
                    "고가": str(close),
                    "저가": str(close),
                    "현재가": str(close),
                    "거래량": "1",
                }
                for index, close in reversed(list(enumerate(self.closes)))
            ],
        )
    def test_validation_replay_and_batch_match_condition_period(self):
        rules = self.rules()
        settings = ValidationSettingsSnapshot(rules)
        session = ValidationSession(
            ValidationRequest(self.stock, settings, 5),
            operation_active_reader=lambda: False,
        )
        replay = ValidationHistoricalReplay(session).evaluate(
            self._historical(),
            start_index=len(self.closes) - 1,
            end_index=len(self.closes) - 1,
        )
        self.assertTrue(replay.ok, replay)
        replay_sell = next(
            entry for entry in replay.snapshot.to_entries()
            if entry.evaluation_side == "SELL" and entry.signal == "SELL"
        )
        self.assertEqual(len(self.closes) - 1, replay_sell.signal_index)

        batch = scan_indicator_follow_validation_batch(
            self.candles(),
            rules,
            start_index=len(self.closes) - 1,
            end_index=len(self.closes) - 1,
        )
        self.assertTrue(batch.supported, batch)
        batch_sell = next(
            record for record in batch.records
            if record.evaluation_side == "SELL" and record.signal == "SELL"
        )
        self.assertEqual(replay_sell.signal_index, batch_sell.routine_signal.signal_index)

    def test_validation_visualization_uses_same_rsi_period(self):
        candles = self.candles()
        rules = self.rules()
        descriptors = build_validation_filter_universe(rules)
        descriptor = next(
            item for item in descriptors
            if item.family == FAMILY_RSI and item.parameters.get("period") == 2
        )
        cache = build_validation_indicator_cache(candles, rules, descriptors)
        values = cache.values_for(descriptor.identity, "RSI")

        self.assertEqual(len(candles), len(values))
        self.assertAlmostEqual(
            rsi(list(self.closes), 2)[-1],
            values[-1],
            places=10,
        )


if __name__ == "__main__":
    unittest.main()
