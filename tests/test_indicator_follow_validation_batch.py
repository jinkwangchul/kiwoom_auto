# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import json
import math
from pathlib import Path
from unittest.mock import patch
import unittest

from indicator_follow_signal_validation_execution import (
    ValidationVirtualPositionTracker,
)
from indicator_follow_signal_validation_projection import (
    build_validation_average_price_context,
)
from routines.지표추종매매.routine_macd_engine import (
    build_indicator_follow_base_series,
    evaluate_indicator_follow_routine,
)
from routines.지표추종매매.routine_validation_batch import (
    scan_indicator_follow_validation_batch,
)
from routines.지표추종매매.routine_validation_replay import (
    ValidationHistoricalReplay,
)
from routines.지표추종매매.routine_validation_contract import (
    ValidationRequest,
    ValidationSettingsSnapshot,
    ValidationStockRef,
)
from routines.지표추종매매.routine_validation_historical import (
    ValidationHistoricalSnapshot,
)
from routines.지표추종매매.routine_validation_session import ValidationSession
from routines.지표추종매매.routine_validation_trace import (
    ValidationTraceObserver,
)


def _candles(count: int = 180) -> list[dict]:
    rows = []
    for index in range(count):
        close = 100.0 + math.sin(index / 3.0) * 8.0 + index * 0.03
        rows.append({
            "time": f"202601{1 + index // 300:02d}{9 + (index // 60) % 6:02d}{index % 60:02d}00",
            "open": close - 0.4,
            "high": close + 1.2,
            "low": close - 1.1,
            "close": close,
            "volume": 1000.0 + index,
        })
    return rows


def _rules() -> dict:
    return {
        "enabled": True,
        "indicators": {
            "macd": {"fast": 3, "slow": 6, "signal": 3},
            "rsi": {"period": 5},
            "moving_averages": [3, 5, 8],
            "bollinger": {"period": 5, "stddev": 2.0},
        },
        "buy": {
            "enabled": True,
            "delay_bar": 0,
            "groups_logic": "OR",
            "groups": [{
                "enabled": True,
                "name": "buy_osc_turn",
                "conditions_logic": "AND",
                "conditions": [{
                    "enabled": True,
                    "not": False,
                    "target": "OSC",
                    "operator": "TURN_UP",
                }],
            }],
            "filters": {},
        },
        "sell": {
            "enabled": True,
            "signal_logic": "OR",
            "signals": {
                "macd_sell": {
                    "enabled": True,
                    "delay_bar": 0,
                    "groups_logic": "OR",
                    "groups": [{
                        "enabled": True,
                        "name": "sell_osc_turn",
                        "conditions_logic": "AND",
                        "conditions": [{
                            "enabled": True,
                            "not": False,
                            "target": "OSC",
                            "operator": "TURN_DOWN",
                        }],
                    }],
                },
                "profit_rate_sell": {"enabled": False},
            },
        },
    }


def _legacy_entries(candles, rules, context_provider):
    entries = []
    for evaluation_index in range(len(candles)):
        prefix = candles[: evaluation_index + 1]
        base_series = (
            build_indicator_follow_base_series(prefix, rules)
            if evaluation_index >= 2
            else None
        )
        for side in ("SELL", "BUY"):
            observer = ValidationTraceObserver()
            context = {
                "decision_trace_observer": observer,
                "_indicator_follow_evaluate_side": side,
            }
            supplied = context_provider(
                evaluation_index, side, prefix, entries
            )
            context.update(supplied)
            signal = evaluate_indicator_follow_routine(
                prefix,
                rules,
                context,
                _base_series_map=base_series,
            ) if base_series is not None else evaluate_indicator_follow_routine(
                prefix, rules, context
            )
            if signal.signal != side:
                continue
            entries.append(ValidationHistoricalReplay._entry_from_signal(
                side,
                evaluation_index,
                candles[evaluation_index]["time"],
                prefix,
                signal,
                ValidationHistoricalReplay._trace_with_context(
                    observer.snapshot(), context, assume_detached=True
                ),
            ))
    return entries


def _batch_entries(candles, rules, context_provider):
    result = scan_indicator_follow_validation_batch(
        candles,
        rules,
        context_provider=context_provider,
    )
    if not result.supported:
        raise AssertionError(result.fallback_reason)
    return [
        ValidationHistoricalReplay._entry_from_signal(
            record.evaluation_side,
            record.evaluation_index,
            record.evaluation_time,
            candles,
            record.routine_signal,
            ValidationHistoricalReplay._trace_with_context(
                record.trace, record.context, assume_detached=True
            ),
        )
        for record in result.records
    ]


class IndicatorFollowValidationBatchTest(unittest.TestCase):
    def assertParity(self, rules, *, tracker=False):
        candles = _candles()
        tracker_policy = {
            "enabled": True,
            "first_buy_quantity": 2,
            "repeat_mode": "ROUND",
            "round_operator": "ADD",
            "round_budget_value": 1.0,
        }
        legacy_context = (
            ValidationVirtualPositionTracker(tracker_policy)
            if tracker
            else build_validation_average_price_context
        )
        batch_context = (
            ValidationVirtualPositionTracker(tracker_policy)
            if tracker
            else build_validation_average_price_context
        )
        legacy = _legacy_entries(candles, rules, legacy_context)
        batch = _batch_entries(candles, rules, batch_context)
        self.assertEqual(
            [entry.to_dict() for entry in legacy],
            [entry.to_dict() for entry in batch],
        )

    def test_filter_family_parity(self):
        filter_configs = [
            {"rsi": {"enabled": True, "conditions": [
                {"period": 5, "operator": "<=", "threshold": 58.0}
            ]}},
            {"moving_average": {"enabled": True, "conditions": [
                {"target": "CLOSE", "compare_target": "MA", "period": 5,
                 "operator": ">="}
            ]}},
            {"price_compare": {"enabled": True, "conditions_logic": "AND",
                "conditions": [{"target": "CLOSE", "compare_target": "CLOSE",
                                "operator": ">="}]}},
            {"bollinger": {"enabled": True, "conditions": [
                {"target": "CLOSE", "compare_target": "BOLLINGER_LOWER",
                 "operator": "<=", "value": 0.5, "period": 5}
            ]}},
            {"ocr": {"enabled": True, "conditions_logic": "AND",
                "conditions": [{"target": "OSC", "operator": "TURN_UP"},
                               {"target": "OSC", "operator": "<=", "value": 0.0}]}},
        ]
        for filters in filter_configs:
            with self.subTest(filters=tuple(filters)):
                rules = _rules()
                rules["buy"]["filters"] = filters
                self.assertParity(rules)

    def test_and_or_not_and_delay_parity(self):
        rules = _rules()
        rules["buy"]["delay_bar"] = 2
        rules["buy"]["groups"] = [{
            "enabled": True,
            "name": "and_not",
            "conditions_logic": "AND",
            "conditions": [
                {"target": "CLOSE", "operator": ">=", "value": 90.0},
                {"target": "OSC", "operator": ">", "value": 0.0, "not": True},
            ],
        }, {
            "enabled": True,
            "name": "or_peer",
            "conditions_logic": "AND",
            "conditions": [{"target": "OSC", "operator": "TURN_UP"}],
        }]
        self.assertParity(rules)

    def test_cross_turn_trend_and_zero_cross_operator_parity(self):
        for operator, compare_target in (
            ("TURN_UP", None),
            ("TURN_DOWN", None),
            ("TREND_UP", None),
            ("TREND_DOWN", None),
            ("ZERO_CROSS_UP", None),
            ("ZERO_CROSS_DOWN", None),
            ("CROSS_UP", "SIGNAL"),
            ("CROSS_DOWN", "SIGNAL"),
        ):
            with self.subTest(operator=operator):
                rules = _rules()
                condition = {"target": "OSC", "operator": operator}
                if compare_target is not None:
                    condition["compare_target"] = compare_target
                rules["buy"]["groups"][0]["conditions"] = [condition]
                self.assertParity(rules)

    def test_expression_and_stateful_average_position_parity(self):
        rules = _rules()
        rules["buy"]["filters"] = {
            "rsi": {"enabled": True, "conditions": [
                {"period": 5, "operator": "<=", "threshold": 62.0}
            ]},
            "ocr": {"enabled": True, "conditions": [
                {"target": "OSC", "operator": "TURN_UP"}
            ]},
            "composite": {
                "enabled": True,
                "expression": {
                    "ast": {
                        "type": "binary", "operator": "NOT",
                        "left": {"type": "identifier", "name": "A"},
                        "right": {"type": "identifier", "name": "B"},
                    },
                    "identifiers": ["A", "B"],
                    "identifier_map": {"A": "rsi", "B": "ocr"},
                },
                "include_unreferenced_active_filters": "AND_REQUIRED",
            },
        }
        rules["sell"]["signals"]["profit_rate_sell"] = {
            "enabled": True,
            "profit_rate_percent": 1.0,
        }
        self.assertParity(rules, tracker=True)

    def test_repeated_buy_sell_cycles_preserve_weighted_position_state(self):
        rules = _rules()
        rules["buy"]["groups"][0]["conditions"] = [
            {"target": "CLOSE", "operator": ">", "value": 0.0}
        ]
        rules["sell"]["signals"]["macd_sell"]["enabled"] = False
        rules["sell"]["signals"]["profit_rate_sell"] = {
            "enabled": True,
            "profit_rate_percent": 0.5,
        }
        candles = _candles(180)
        policy = {
            "enabled": True,
            "first_buy_quantity": 2,
            "repeat_mode": "ROUND",
            "round_operator": "ADD",
            "round_budget_value": 1.0,
        }
        legacy = _legacy_entries(
            candles, rules, ValidationVirtualPositionTracker(policy)
        )
        batch = _batch_entries(
            candles, rules, ValidationVirtualPositionTracker(policy)
        )
        self.assertEqual(
            [entry.to_dict() for entry in legacy],
            [entry.to_dict() for entry in batch],
        )
        self.assertGreaterEqual(
            sum(entry.signal == "SELL" for entry in batch), 2
        )

    def test_current_rules_json_shape_is_supported(self):
        rules_path = next(
            Path("routine_instances").glob("*/rules.json")
        )
        rules = json.loads(rules_path.read_text(encoding="utf-8"))
        self.assertParity(rules, tracker=True)

    def test_parity_with_current_validation_historical_replay_scan(self):
        candles = _candles(90)
        rules = _rules()
        stock = ValidationStockRef("005930", "삼성전자")
        request = ValidationRequest(
            stock,
            ValidationSettingsSnapshot(rules),
            3,
        )
        session = ValidationSession(
            request,
            operation_active_reader=lambda: False,
        )
        rows = [{
            "체결시간": candle["time"],
            "시가": candle["open"],
            "고가": candle["high"],
            "저가": candle["low"],
            "현재가": candle["close"],
            "거래량": candle["volume"],
        } for candle in candles]
        historical = ValidationHistoricalSnapshot(
            stock=stock,
            timeframe_minutes=3,
            timeframe_key="M3",
            requested_count=len(rows),
            request_id="BATCH-AUTHORITATIVE-PARITY",
            rows=rows,
        )
        policy = {
            "enabled": True,
            "first_buy_quantity": 2,
            "repeat_mode": "ROUND",
            "round_operator": "ADD",
            "round_budget_value": 1.0,
        }
        authoritative = ValidationHistoricalReplay(session).scan_signal_entries(
            historical,
            context_provider=ValidationVirtualPositionTracker(policy),
        )
        batch = _batch_entries(
            candles,
            rules,
            ValidationVirtualPositionTracker(policy),
        )
        self.assertEqual(
            [entry.to_dict() for entry in authoritative],
            [entry.to_dict() for entry in batch],
        )

    def test_custom_context_provider_fails_closed(self):
        result = scan_indicator_follow_validation_batch(
            _candles(20),
            _rules(),
            context_provider=lambda *_args: {},
        )
        self.assertFalse(result.supported)
        self.assertEqual(
            "BATCH_CONTEXT_PROVIDER_UNSUPPORTED", result.fallback_reason
        )

    def test_authoritative_evaluator_runs_only_for_emitted_signals(self):
        candles = _candles(120)
        rules = _rules()
        with patch(
            "routines.지표추종매매.routine_validation_batch."
            "evaluate_indicator_follow_routine",
            wraps=evaluate_indicator_follow_routine,
        ) as evaluator:
            result = scan_indicator_follow_validation_batch(
                candles,
                rules,
                context_provider=build_validation_average_price_context,
            )
        self.assertTrue(result.supported, result.fallback_reason)
        self.assertEqual(len(result.records), evaluator.call_count)
        self.assertLess(evaluator.call_count, len(candles) * 2)

    def test_unsupported_operator_fails_closed_without_evaluator_calls(self):
        rules = _rules()
        rules["buy"]["groups"][0]["conditions"][0]["operator"] = "FUTURE_OP"
        with patch(
            "routines.지표추종매매.routine_validation_batch."
            "evaluate_indicator_follow_routine",
            wraps=evaluate_indicator_follow_routine,
        ) as evaluator:
            result = scan_indicator_follow_validation_batch(_candles(40), rules)
        self.assertFalse(result.supported)
        self.assertEqual("BATCH_CONDITION_UNSUPPORTED", result.fallback_reason)
        self.assertEqual(0, evaluator.call_count)

    def test_authoritative_signal_mismatch_fails_closed(self):
        from engines.signal_result import RoutineSignal

        with patch(
            "routines.지표추종매매.routine_validation_batch."
            "evaluate_indicator_follow_routine",
            return_value=RoutineSignal(None, "forced mismatch", [], [], -1, 0),
        ):
            result = scan_indicator_follow_validation_batch(
                _candles(120),
                _rules(),
                context_provider=build_validation_average_price_context,
            )
        self.assertFalse(result.supported)
        self.assertEqual("BATCH_AUTHORITATIVE_REPLAY_MISMATCH", result.fallback_reason)

    def test_inputs_are_not_mutated(self):
        candles = _candles(40)
        rules = _rules()
        before_candles = deepcopy(candles)
        before_rules = deepcopy(rules)
        result = scan_indicator_follow_validation_batch(
            candles, rules, context_provider=build_validation_average_price_context
        )
        self.assertTrue(result.supported, result.fallback_reason)
        self.assertEqual(before_candles, candles)
        self.assertEqual(before_rules, rules)


if __name__ == "__main__":
    unittest.main()
