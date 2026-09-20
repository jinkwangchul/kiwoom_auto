# -*- coding: utf-8 -*-
from __future__ import annotations

import ast
from copy import deepcopy
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta
import json
from pathlib import Path
import unittest
from unittest.mock import Mock

from engines.condition_engine import parse_condition_expression
from engines.signal_result import RoutineSignal
from routines.지표추종매매 import routine_macd_engine
from indicator_follow_signal_validation_execution import ValidationVirtualPositionTracker
from indicator_follow_signal_validation_projection import (
    build_validation_average_price_context,
)
from indicator_follow_signal_validation_presentation import (
    signal_evidence_lines_for_entry,
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
    REASON_HISTORICAL_IDENTITY_MISMATCH,
    REASON_INVALID_EVALUATION_RANGE,
    REASON_INVALID_HISTORICAL_SNAPSHOT,
    REASON_INVALID_ROUTINE_SIGNAL,
    REASON_NO_VALID_CANDLES,
    ValidationHistoricalReplay,
    ValidationReplayEntry,
    project_validation_candles,
)
from routines.지표추종매매.routine_validation_session import ValidationSession
from routines.지표추종매매.routine_validation_trace import ValidationTraceObserver


class ValidationHistoricalReplayTest(unittest.TestCase):
    def setUp(self) -> None:
        self.stock = ValidationStockRef("005930", "삼성전자")
        self.rules = self._rules()
        self.settings = ValidationSettingsSnapshot(self.rules)
        self.request = ValidationRequest(self.stock, self.settings, 3)

    @staticmethod
    def _rules() -> dict:
        return {
            "enabled": True,
            "bar_minutes": 3,
            "macd": {"fast": 2, "slow": 3, "signal": 2},
            "buy": {
                "delay_bar": 0,
                "groups": [
                    {
                        "enabled": True,
                        "name": "buy_close",
                        "conditions": [
                            {
                                "enabled": True,
                                "not": False,
                                "target": "CLOSE",
                                "operator": ">=",
                                "value": 12,
                            }
                        ],
                    }
                ],
            },
            "sell": {
                "delay_bar": 0,
                "signal_logic": "OR",
                "signals": {
                    "macd_sell": {"enabled": False, "groups": []},
                    "ui_condition_c_macd_sell": {
                        "enabled": True,
                        "order_delay_bars": 0,
                        "groups": [
                            {
                                "enabled": True,
                                "name": "sell_close",
                                "conditions": [
                                    {
                                        "enabled": True,
                                        "not": False,
                                        "target": "CLOSE",
                                        "operator": ">=",
                                        "value": 13,
                                    }
                                ],
                            }
                        ],
                    },
                    "profit_rate_sell": {"enabled": False},
                },
            },
        }

    def _session(self, reader=None) -> ValidationSession:
        return ValidationSession(
            self.request,
            operation_active_reader=reader or Mock(return_value=False),
        )

    def _historical(
        self,
        closes=(10, 11, 12, 13, 9),
        *,
        stock=None,
        timeframe=3,
        timeframe_key=None,
        rows=None,
    ) -> ValidationHistoricalSnapshot:
        if rows is None:
            rows = []
            for index, close in reversed(list(enumerate(closes))):
                rows.append(
                    {
                        "체결시간": f"2026091309{index:02d}00",
                        "시가": str(close),
                        "고가": str(close + 1),
                        "저가": str(close - 1),
                        "현재가": str(close),
                        "거래량": str(100 + index),
                    }
                )
        return ValidationHistoricalSnapshot(
            stock=stock or self.stock,
            timeframe_minutes=timeframe,
            timeframe_key=timeframe_key,
            requested_count=max(len(rows), 1),
            request_id="OPT10080-REPLAY-1",
            rows=rows,
        )

    @staticmethod
    def _none_signal() -> RoutineSignal:
        return RoutineSignal(None, "none", [], [], -1, 0)

    def test_display_count_keeps_warmup_candles_outside_result_window(self) -> None:
        evaluator = Mock(return_value=self._none_signal())
        result = ValidationHistoricalReplay(
            self._session(),
            evaluator=evaluator,
        ).evaluate(
            self._historical(closes=(10, 11, 12, 13, 14)),
            display_count=2,
        )

        self.assertTrue(result.ok, result)
        snapshot = result.snapshot
        self.assertEqual(3, snapshot.evaluated_start_index)
        self.assertEqual(4, snapshot.evaluated_end_index)
        self.assertEqual(5, len(snapshot.to_candles()))
        self.assertEqual(
            ["20260913090300", "20260913090400"],
            [candle["time"] for candle in snapshot.to_display_candles()],
        )
        self.assertEqual(4, len(snapshot.to_entries()))
        display_entries = snapshot.to_display_entries()
        self.assertEqual(4, len(display_entries))
        self.assertEqual({0, 1}, {entry.evaluation_index for entry in display_entries})
        self.assertEqual(4, evaluator.call_count)

    def test_projection_is_chronological_normalized_and_deterministic(self) -> None:
        rows = [
            {
                "체결시간": "20260913090200",
                "시가": "+1,290",
                "고가": "-1,310",
                "저가": "1,280",
                "현재가": "+1,300",
                "거래량": "+200",
            },
            {"체결시간": "20260913090300", "현재가": "bad"},
            {
                "체결시간": " 20260913090100 ",
                "시가": " -1,190 ",
                "고가": "+1,210",
                "저가": "-1,180",
                "현재가": " -1,200 ",
                "거래량": " 1,000 ",
            },
            {"체결시간": "not-a-time", "현재가": "100"},
            {
                "체결시간": "20260913090200",
                "시가": "1,240",
                "고가": "1,260",
                "저가": "1,230",
                "현재가": "-1,250",
                "거래량": "150",
            },
        ]
        historical = self._historical(rows=rows)
        original_rows = historical.to_rows()

        candles, dropped = project_validation_candles(historical)

        self.assertEqual(["20260913090100", "20260913090200"], [c["time"] for c in candles])
        self.assertEqual(
            {
                "time": "20260913090100",
                "open": 1190.0,
                "high": 1210.0,
                "low": 1180.0,
                "close": 1200.0,
                "volume": 1000.0,
            },
            candles[0],
        )
        self.assertEqual(1250.0, candles[1]["close"])
        self.assertEqual(3, dropped)
        self.assertEqual(original_rows, historical.to_rows())

    def test_projection_excludes_only_the_current_forming_bucket(self) -> None:
        historical = self._historical(
            timeframe=5,
            rows=[
                {
                    "체결시간": "20260918100000",
                    "시가": "110",
                    "고가": "115",
                    "저가": "108",
                    "현재가": "112",
                    "거래량": "10",
                },
                {
                    "체결시간": "20260918095500",
                    "시가": "100",
                    "고가": "111",
                    "저가": "99",
                    "현재가": "110",
                    "거래량": "20",
                },
            ],
        )

        candles, dropped = project_validation_candles(
            historical,
            as_of=datetime(2026, 9, 18, 10, 3, 30),
        )

        self.assertEqual(["20260918095500"], [candle["time"] for candle in candles])
        self.assertEqual(1, dropped)

    def test_day_projection_uses_trading_day_instead_of_minute_bucket(self) -> None:
        historical = self._historical(
            timeframe=5,
            timeframe_key="D1",
            rows=[{
                "체결시간": "20260918000000",
                "시가": "100",
                "고가": "111",
                "저가": "99",
                "현재가": "110",
                "거래량": "20",
            }],
        )

        forming, forming_dropped = project_validation_candles(
            historical,
            as_of=datetime(2026, 9, 18, 10, 3, 30),
        )
        completed, completed_dropped = project_validation_candles(
            historical,
            as_of=datetime(2026, 9, 18, 15, 30),
        )

        self.assertEqual([], forming)
        self.assertEqual(1, forming_dropped)
        self.assertEqual(["20260918000000"], [candle["time"] for candle in completed])
        self.assertEqual(0, completed_dropped)

    def test_week_projection_uses_iso_week_instead_of_minute_bucket(self) -> None:
        historical = self._historical(
            timeframe=5,
            timeframe_key="W1",
            rows=[{
                "체결시간": "20260914000000",
                "시가": "100",
                "고가": "111",
                "저가": "99",
                "현재가": "110",
                "거래량": "20",
            }],
        )

        forming, forming_dropped = project_validation_candles(
            historical,
            as_of=datetime(2026, 9, 16, 10, 3, 30),
        )
        completed, completed_dropped = project_validation_candles(
            historical,
            as_of=datetime(2026, 9, 18, 15, 30),
        )

        self.assertEqual([], forming)
        self.assertEqual(1, forming_dropped)
        self.assertEqual(["20260914000000"], [candle["time"] for candle in completed])
        self.assertEqual(0, completed_dropped)

    def test_year_projection_uses_calendar_year_instead_of_minute_bucket(self) -> None:
        historical = self._historical(
            timeframe=5,
            timeframe_key="Y1",
            rows=[{
                "체결시간": "20260101000000",
                "시가": "100",
                "고가": "111",
                "저가": "99",
                "현재가": "110",
                "거래량": "20",
            }],
        )

        forming, forming_dropped = project_validation_candles(
            historical,
            as_of=datetime(2026, 9, 18, 15, 30),
        )
        completed, completed_dropped = project_validation_candles(
            historical,
            as_of=datetime(2027, 1, 2, 10, 0),
        )

        self.assertEqual([], forming)
        self.assertEqual(1, forming_dropped)
        self.assertEqual(["20260101000000"], [candle["time"] for candle in completed])
        self.assertEqual(0, completed_dropped)

    def test_projection_keeps_latest_completed_bucket_when_current_bucket_has_no_row(self) -> None:
        historical = self._historical(
            timeframe=5,
            rows=[{
                "체결시간": "20260918095500",
                "시가": "100",
                "고가": "111",
                "저가": "99",
                "현재가": "110",
                "거래량": "20",
            }],
        )

        candles, dropped = project_validation_candles(
            historical,
            as_of=datetime(2026, 9, 18, 10, 3, 30),
        )

        self.assertEqual(["20260918095500"], [candle["time"] for candle in candles])
        self.assertEqual(0, dropped)

    def test_projection_keeps_last_regular_session_bucket_after_close(self) -> None:
        historical = self._historical(
            timeframe=240,
            rows=[{
                "체결시간": "20260918130000",
                "시가": "100",
                "고가": "111",
                "저가": "99",
                "현재가": "110",
                "거래량": "20",
            }],
        )

        candles, dropped = project_validation_candles(
            historical,
            as_of=datetime(2026, 9, 18, 15, 31, 0),
        )

        self.assertEqual(["20260918130000"], [candle["time"] for candle in candles])
        self.assertEqual(0, dropped)

    def test_projection_excludes_missing_close_and_invalid_time(self) -> None:
        historical = self._historical(
            rows=[
                {"체결시간": "20260913090000", "현재가": ""},
                {"체결시간": "", "현재가": "10"},
            ]
        )
        replay = ValidationHistoricalReplay(self._session(), evaluator=Mock())

        result = replay.evaluate(historical)

        self.assertFalse(result.ok)
        self.assertEqual(REASON_NO_VALID_CANDLES, result.reason)
        replay._evaluator.assert_not_called()

    def test_settings_snapshot_to_dict_is_the_only_config_source(self) -> None:
        seen_configs = []

        def evaluator(candles, config, context):
            seen_configs.append(dict(config))
            config["mutated_by_test_seam"] = True
            return self._none_signal()

        replay = ValidationHistoricalReplay(self._session(), evaluator=evaluator)
        before = self.settings.to_dict()

        result = replay.evaluate(self._historical(closes=(10, 11, 12)), start_index=2)

        self.assertTrue(result.ok)
        self.assertEqual([before, before], seen_configs)
        self.assertEqual(before, self.settings.to_dict())

    def test_prefix_only_evaluation_and_observers_are_side_local(self) -> None:
        calls = []

        def evaluator(candles, config, context):
            calls.append(
                (
                    len(candles),
                    context["_indicator_follow_evaluate_side"],
                    context["decision_trace_observer"],
                    set(context),
                )
            )
            return self._none_signal()

        replay = ValidationHistoricalReplay(self._session(), evaluator=evaluator)

        result = replay.evaluate(self._historical(), start_index=2, end_index=4)

        self.assertTrue(result.ok)
        self.assertEqual([3, 3, 4, 4, 5, 5], [call[0] for call in calls])
        self.assertEqual(["SELL", "BUY"] * 3, [call[1] for call in calls])
        self.assertEqual(6, len({call[2] for call in calls}))
        self.assertTrue(
            all(
                keys
                == {
                    "decision_trace_observer",
                    "_indicator_follow_evaluate_side",
                }
                for _, _, _, keys in calls
            )
        )

    def test_actual_evaluator_replays_buy_sell_and_none(self) -> None:
        result = ValidationHistoricalReplay(self._session()).evaluate(self._historical())

        self.assertTrue(result.ok, result)
        entries = result.snapshot.to_entries()
        by_bar_side = {(e.evaluation_index, e.evaluation_side): e for e in entries}
        self.assertIsNone(by_bar_side[(1, "BUY")].signal)
        self.assertEqual("BUY", by_bar_side[(2, "BUY")].signal)
        self.assertEqual("SELL", by_bar_side[(3, "SELL")].signal)
        self.assertIsNone(by_bar_side[(4, "SELL")].signal)

    def test_virtual_fill_price_does_not_move_close_only_signal_locations(self) -> None:
        closes = (10.0, 11.0, 12.0, 13.0, 9.0)
        baseline = self._historical(closes=closes)
        varied_rows = []
        for index, close in reversed(list(enumerate(closes))):
            varied_rows.append({
                "체결시간": f"2026091309{index:02d}00",
                "시가": str(close * 0.9),
                "고가": str(close * 1.4),
                "저가": str(close * 0.8),
                "현재가": str(close),
                "거래량": str(100 + index),
            })
        varied = self._historical(rows=varied_rows)

        baseline_result = ValidationHistoricalReplay(self._session()).evaluate_with_context(
            baseline,
            context_provider=build_validation_average_price_context,
        )
        varied_result = ValidationHistoricalReplay(self._session()).evaluate_with_context(
            varied,
            context_provider=build_validation_average_price_context,
        )

        self.assertTrue(baseline_result.ok, baseline_result)
        self.assertTrue(varied_result.ok, varied_result)
        fields = lambda entry: (
            entry.evaluation_index,
            entry.evaluation_side,
            entry.signal,
            entry.signal_index,
            entry.delay_bar,
        )
        self.assertEqual(
            [fields(entry) for entry in baseline_result.snapshot.to_entries()],
            [fields(entry) for entry in varied_result.snapshot.to_entries()],
        )

    def test_incremental_virtual_position_context_is_weighted_before_next_sell_evaluation(self) -> None:
        observed = []

        def evaluator(candles, config, context):
            side = context["_indicator_follow_evaluate_side"]
            if side == "SELL":
                observed.append((
                    len(candles),
                    context.get("average_price"),
                    context.get("validation_trace_context", {}).get("position_quantity"),
                ))
                return self._none_signal()
            if len(candles) <= 2:
                return RoutineSignal(
                    "BUY",
                    "fixture buy",
                    ["fixture"],
                    [],
                    len(candles) - 1,
                    0,
                )
            return self._none_signal()

        rows = []
        for index, price in reversed(list(enumerate((100.0, 50.0, 70.0)))):
            rows.append({
                "체결시간": f"2026091309{index:02d}00",
                "시가": str(price),
                "고가": str(price),
                "저가": str(price),
                "현재가": str(price),
                "거래량": "1",
            })
        tracker = ValidationVirtualPositionTracker({
            "enabled": True,
            "repeat_mode": "BUDGET",
            "budget_ratio": 2.0,
        })
        result = ValidationHistoricalReplay(
            self._session(),
            evaluator=evaluator,
        ).evaluate_with_context(
            self._historical(rows=rows),
            context_provider=tracker,
        )

        self.assertTrue(result.ok, result)
        self.assertEqual((3, 60.0, 5), observed[-1])

    def test_actual_evaluator_trace_contains_existing_three_levels(self) -> None:
        result = ValidationHistoricalReplay(self._session()).evaluate(
            self._historical(),
            start_index=2,
            end_index=3,
        )

        self.assertTrue(result.ok, result)
        for entry in result.snapshot.to_entries():
            trace = entry.trace
            self.assertTrue(trace["conditions"])
            self.assertTrue(trace["groups"])
            self.assertTrue(trace["aggregations"])

    def test_bollinger_only_expression_replay_is_not_limited_by_legacy_osc_group(self) -> None:
        rules = self._rules()
        rules["buy"]["groups"] = [{
            "enabled": True,
            "name": "legacy_osc_turn_up",
            "conditions": [{
                "enabled": True,
                "not": False,
                "target": "OSC",
                "operator": "TURN_UP",
            }],
        }]
        parsed = parse_condition_expression(
            "B",
            allowed_identifiers={"A", "B", "C", "D"},
        )
        self.assertTrue(parsed["ok"], parsed)
        rules["buy"]["filters"] = {
            "bollinger": {
                "enabled": True,
                "conditions": [{
                    "enabled": True,
                    "not": False,
                    "target": "CLOSE",
                    "operator": ">=",
                    "compare_target": "BOLLINGER_LOWER",
                    "value": -0.1,
                }],
            },
            "composite": {
                "enabled": True,
                "expression": {
                    "source": "B",
                    "normalized": parsed["normalized"],
                    "ast": parsed["ast"],
                    "identifiers": parsed["identifiers"],
                    "identifier_map": {
                        "A": "ocr",
                        "B": "bollinger",
                        "C": "moving_average",
                        "D": "rsi",
                    },
                },
                "include_unreferenced_active_filters": "AND_REQUIRED",
                "groups": [],
            },
        }
        rules["sell"] = {
            "delay_bar": 0,
            "signal_logic": "OR",
            "signals": {"macd_sell": {"enabled": False, "groups": []}},
        }
        settings = ValidationSettingsSnapshot(rules)
        request = ValidationRequest(self.stock, settings, 3)
        session = ValidationSession(
            request,
            operation_active_reader=Mock(return_value=False),
        )
        historical = self._historical(closes=(100,) * 30)

        expression_result = ValidationHistoricalReplay(session).evaluate(historical)

        legacy_rules = deepcopy(rules)
        legacy_rules["buy"]["filters"].pop("composite")
        legacy_settings = ValidationSettingsSnapshot(legacy_rules)
        legacy_request = ValidationRequest(self.stock, legacy_settings, 3)
        legacy_session = ValidationSession(
            legacy_request,
            operation_active_reader=Mock(return_value=False),
        )
        legacy_result = ValidationHistoricalReplay(legacy_session).evaluate(historical)

        self.assertTrue(expression_result.ok, expression_result)
        self.assertTrue(legacy_result.ok, legacy_result)
        expression_buys = [
            entry
            for entry in expression_result.snapshot.to_entries()
            if entry.evaluation_side == "BUY" and entry.signal == "BUY"
        ]
        legacy_buys = [
            entry
            for entry in legacy_result.snapshot.to_entries()
            if entry.evaluation_side == "BUY" and entry.signal == "BUY"
        ]
        self.assertEqual(11, len(expression_buys))
        self.assertEqual(0, len(legacy_buys))
        for entry in expression_buys:
            self.assertEqual(entry.evaluation_index, entry.signal_index)
            self.assertEqual(0, entry.delay_bar)
            self.assertEqual([], entry.matched_groups)
            self.assertTrue(any("filter_type=BOLLINGER" in detail for detail in entry.details))
            self.assertTrue(any("referenced_filters=bollinger" in detail for detail in entry.details))
            self.assertFalse(
                any(
                    str(group.get("group_ref", {}).get("path", "")).startswith("buy.groups")
                    for group in entry.trace.get("groups", [])
                    if isinstance(group, dict)
                )
            )
            self.assertFalse(
                any(
                    aggregation.get("side") == "BUY"
                    and aggregation.get("payload", {}).get("active_group_paths")
                    for aggregation in entry.trace.get("aggregations", [])
                    if isinstance(aggregation, dict)
                )
            )
            evidence = signal_evidence_lines_for_entry(entry, rules)
            self.assertEqual(1, len(evidence))
            self.assertIn("볼린저밴드", evidence[0])
            self.assertNotIn("OCR", evidence[0])

    def test_signal_and_evaluation_times_are_preserved_separately(self) -> None:
        rules = self._rules()
        rules["buy"]["delay_bar"] = 1
        rules["buy"]["groups"][0]["conditions"][0]["value"] = 12
        settings = ValidationSettingsSnapshot(rules)
        request = ValidationRequest(self.stock, settings, 3)
        session = ValidationSession(request, operation_active_reader=Mock(return_value=False))

        result = ValidationHistoricalReplay(session).evaluate(
            self._historical(closes=(10, 11, 12, 13)),
            start_index=3,
        )

        self.assertTrue(result.ok, result)
        buy = next(e for e in result.snapshot.to_entries() if e.evaluation_side == "BUY")
        self.assertEqual("BUY", buy.signal)
        self.assertEqual(2, buy.signal_index)
        self.assertEqual("20260913090200", buy.signal_time)
        self.assertEqual("20260913090300", buy.evaluation_time)
        self.assertEqual(1, buy.delay_bar)

    def test_non_signal_never_exposes_evaluator_negative_index_as_marker(self) -> None:
        result = ValidationHistoricalReplay(self._session()).evaluate(
            self._historical(closes=(10, 11)),
        )

        self.assertTrue(result.ok)
        for entry in result.snapshot.to_entries():
            self.assertIsNone(entry.signal)
            self.assertIsNone(entry.signal_index)
            self.assertIsNone(entry.signal_time)

    def test_opposite_side_or_invalid_signal_index_fails_closed(self) -> None:
        cases = (
            RoutineSignal("BUY", "wrong side", [], [], 0, 0),
            RoutineSignal("SELL", "bad index", [], [], 99, 0),
        )
        for signal in cases:
            with self.subTest(signal=signal):
                replay = ValidationHistoricalReplay(
                    self._session(),
                    evaluator=Mock(return_value=signal),
                )
                result = replay.evaluate(self._historical(closes=(10,)), start_index=0)
                self.assertFalse(result.ok)
                self.assertEqual(REASON_INVALID_ROUTINE_SIGNAL, result.reason)
                self.assertIsNone(result.snapshot)

    def test_operation_state_does_not_block_replay(self) -> None:
        evaluator = Mock(return_value=self._none_signal())
        reader = Mock(side_effect=AssertionError("operation reader must not be called"))

        result = ValidationHistoricalReplay(
            self._session(reader),
            evaluator=evaluator,
        ).evaluate(self._historical())

        self.assertTrue(result.ok)
        self.assertIsNotNone(result.snapshot)
        self.assertGreater(evaluator.call_count, 0)
        reader.assert_not_called()

    def test_operation_change_during_replay_does_not_discard_result(self) -> None:
        evaluator = Mock(return_value=self._none_signal())
        reader = Mock(side_effect=AssertionError("operation reader must not be called"))

        result = ValidationHistoricalReplay(
            self._session(reader),
            evaluator=evaluator,
        ).evaluate(self._historical(closes=(10, 11, 12)))

        self.assertTrue(result.ok)
        self.assertIsNotNone(result.snapshot)
        self.assertEqual(6, evaluator.call_count)
        reader.assert_not_called()

    def test_historical_stock_and_timeframe_mismatch_fail_closed(self) -> None:
        cases = (
            self._historical(stock=ValidationStockRef("000660", "SK하이닉스")),
            self._historical(timeframe=5),
        )
        for historical in cases:
            with self.subTest(historical=historical):
                evaluator = Mock(return_value=self._none_signal())
                result = ValidationHistoricalReplay(
                    self._session(), evaluator=evaluator
                ).evaluate(historical)
                self.assertFalse(result.ok)
                self.assertEqual(REASON_HISTORICAL_IDENTITY_MISMATCH, result.reason)
                evaluator.assert_not_called()

    def test_non_historical_snapshot_fails_closed(self) -> None:
        result = ValidationHistoricalReplay(self._session()).evaluate(object())
        self.assertFalse(result.ok)
        self.assertEqual(REASON_INVALID_HISTORICAL_SNAPSHOT, result.reason)

    def test_invalid_evaluation_ranges_fail_closed(self) -> None:
        cases = (
            {"start_index": True},
            {"start_index": -1},
            {"start_index": 3},
            {"start_index": 2, "end_index": 1},
            {"start_index": 0, "end_index": 3},
            {"start_index": 0, "end_index": 1.0},
        )
        for indexes in cases:
            with self.subTest(indexes=indexes):
                evaluator = Mock(return_value=self._none_signal())
                result = ValidationHistoricalReplay(
                    self._session(), evaluator=evaluator
                ).evaluate(self._historical(closes=(10, 11, 12)), **indexes)
                self.assertFalse(result.ok)
                self.assertEqual(REASON_INVALID_EVALUATION_RANGE, result.reason)
                evaluator.assert_not_called()

    def test_replay_snapshot_and_nested_results_are_detached(self) -> None:
        source_rows = self._historical().to_rows()
        historical = ValidationHistoricalSnapshot(
            stock=self.stock,
            timeframe_minutes=3,
            requested_count=len(source_rows),
            request_id="IMMUTABLE",
            rows=source_rows,
        )
        result = ValidationHistoricalReplay(self._session()).evaluate(
            historical,
            start_index=2,
            end_index=3,
        )
        self.assertTrue(result.ok, result)
        snapshot = result.snapshot
        expected_candles = snapshot.to_candles()
        expected_entries = [entry.to_dict() for entry in snapshot.to_entries()]

        source_rows[0]["현재가"] = "1"
        candles = snapshot.to_candles()
        candles[0]["close"] = 1
        candles.append({})
        entries = snapshot.to_entries()
        entries.pop()
        trace = entries[0].trace
        trace["conditions"].clear()

        self.assertEqual(expected_candles, snapshot.to_candles())
        self.assertEqual(expected_entries, [entry.to_dict() for entry in snapshot.to_entries()])
        with self.assertRaises(FrozenInstanceError):
            snapshot.candle_count = 0

    def test_context_prior_entries_container_and_nested_values_are_detached(self) -> None:
        observed = {
            "container_mutated": False,
            "entry_frozen": False,
            "nested_values_mutated": False,
        }

        def evaluator(candles, config, context):
            side = context["_indicator_follow_evaluate_side"]
            observer = context["decision_trace_observer"]
            observer.observe_condition({"nested": {"value": len(candles)}})
            return RoutineSignal(
                side,
                "fixture",
                ["fixture_group"],
                ["fixture_detail"],
                len(candles) - 1,
                0,
            )

        def mutating_context_provider(_index, _side, _candles, prior_entries):
            if prior_entries:
                entry = prior_entries[0]
                with self.assertRaises(FrozenInstanceError):
                    entry.reason = "mutated"
                observed["entry_frozen"] = True

                matched_groups = entry.matched_groups
                details = entry.details
                trace = entry.trace
                matched_groups.append("mutated")
                details.append("mutated")
                trace["conditions"][0]["nested"]["value"] = -1
                observed["nested_values_mutated"] = True

            prior_entries.append("foreign")
            prior_entries.clear()
            observed["container_mutated"] = True
            return {}

        result = ValidationHistoricalReplay(
            self._session(),
            evaluator=evaluator,
        ).evaluate_with_context(
            self._historical(closes=(10, 11, 12)),
            context_provider=mutating_context_provider,
        )

        self.assertTrue(result.ok, result)
        self.assertEqual(
            {
                "container_mutated": True,
                "entry_frozen": True,
                "nested_values_mutated": True,
            },
            observed,
        )
        entries = result.snapshot.to_entries()
        self.assertEqual(6, len(entries))
        self.assertTrue(all(entry.reason == "fixture" for entry in entries))
        self.assertTrue(all(entry.matched_groups == ["fixture_group"] for entry in entries))
        self.assertTrue(all(entry.details == ["fixture_detail"] for entry in entries))
        self.assertTrue(all(
            entry.trace["conditions"][0]["nested"]["value"]
            == entry.evaluation_index + 1
            for entry in entries
        ))

    def test_context_entry_reuse_is_byte_equivalent_to_deep_recreation(self) -> None:
        def deep_recreated_context_provider(index, side, candles, prior_entries):
            recreated = [
                ValidationReplayEntry(**entry.to_dict())
                for entry in prior_entries
            ]
            return build_validation_average_price_context(
                index,
                side,
                candles,
                recreated,
            )

        historical = self._historical(closes=(10, 11, 12, 13, 9))
        optimized = ValidationHistoricalReplay(self._session()).evaluate_with_context(
            historical,
            context_provider=build_validation_average_price_context,
        )
        reference = ValidationHistoricalReplay(self._session()).evaluate_with_context(
            historical,
            context_provider=deep_recreated_context_provider,
        )

        self.assertTrue(optimized.ok, optimized)
        self.assertTrue(reference.ok, reference)

        def canonical(snapshot):
            return json.dumps(
                {
                    "stock": {
                        "code": snapshot.stock.code,
                        "name": snapshot.stock.name,
                    },
                    "timeframe_minutes": snapshot.timeframe_minutes,
                    "settings_hash": snapshot.settings_hash,
                    "historical_request_id": snapshot.historical_request_id,
                    "candle_count": snapshot.candle_count,
                    "evaluated_start_index": snapshot.evaluated_start_index,
                    "evaluated_end_index": snapshot.evaluated_end_index,
                    "dropped_raw_rows_count": snapshot.dropped_raw_rows_count,
                    "candles": snapshot.to_candles(),
                    "entries": [
                        entry.to_dict()
                        for entry in snapshot.to_entries()
                    ],
                },
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )

        self.assertEqual(canonical(reference.snapshot), canonical(optimized.snapshot))

    def test_same_prefix_base_series_reuse_is_byte_equivalent_to_reference(self) -> None:
        rules = json.loads(
            (
                Path(__file__).resolve().parents[1]
                / "routines"
                / "지표추종매매"
                / "rules.json"
            ).read_text(encoding="utf-8")
        )
        settings = ValidationSettingsSnapshot(rules)
        request = ValidationRequest(self.stock, settings, 5)

        def session():
            return ValidationSession(
                request,
                operation_active_reader=Mock(return_value=False),
            )

        start = datetime(2026, 9, 13, 9, 0)
        closes = tuple(100 + ((index % 17) - 8) * 0.75 for index in range(80))
        historical = ValidationHistoricalSnapshot(
            stock=self.stock,
            timeframe_minutes=5,
            requested_count=len(closes),
            request_id="SAME-PREFIX-RICH-RULES",
            rows=[
                {
                    "체결시간": (start + timedelta(minutes=5 * index)).strftime(
                        "%Y%m%d%H%M%S"
                    ),
                    "시가": str(close - 0.2),
                    "고가": str(close + 0.8),
                    "저가": str(close - 0.9),
                    "현재가": str(close),
                    "거래량": str(1000 + index),
                }
                for index, close in reversed(list(enumerate(closes)))
            ],
        )
        optimized = ValidationHistoricalReplay(session()).evaluate_with_context(
            historical,
            context_provider=build_validation_average_price_context,
        )

        def reference_evaluator(candles, config, context):
            return routine_macd_engine.evaluate_indicator_follow_routine(
                candles,
                config,
                context,
            )

        reference = ValidationHistoricalReplay(
            session(),
            evaluator=reference_evaluator,
        ).evaluate_with_context(
            historical,
            context_provider=build_validation_average_price_context,
        )

        self.assertTrue(optimized.ok, optimized)
        self.assertTrue(reference.ok, reference)
        self.assertEqual(
            json.dumps(reference.snapshot.to_candles(), sort_keys=True),
            json.dumps(optimized.snapshot.to_candles(), sort_keys=True),
        )
        self.assertEqual(
            json.dumps(
                [entry.to_dict() for entry in reference.snapshot.to_entries()],
                ensure_ascii=False,
                sort_keys=True,
            ),
            json.dumps(
                [entry.to_dict() for entry in optimized.snapshot.to_entries()],
                ensure_ascii=False,
                sort_keys=True,
            ),
        )
        for field in (
            "stock",
            "timeframe_minutes",
            "settings_hash",
            "historical_request_id",
            "candle_count",
            "evaluated_start_index",
            "evaluated_end_index",
            "dropped_raw_rows_count",
        ):
            self.assertEqual(
                getattr(reference.snapshot, field),
                getattr(optimized.snapshot, field),
            )

    def test_signal_scan_matches_detailed_prefix_replay(self) -> None:
        historical = self._historical(
            closes=tuple(10 + (index % 7) for index in range(60))
        )
        replay = ValidationHistoricalReplay(self._session())
        detailed = replay.evaluate(historical)
        scanned = replay.scan_signal_entries(historical)

        self.assertTrue(detailed.ok, detailed)
        expected = [
            entry.to_dict()
            for entry in detailed.snapshot.to_entries()
            if entry.signal == entry.evaluation_side
        ]
        self.assertEqual(expected, [entry.to_dict() for entry in scanned])

    def test_signal_scan_price_box_is_prefix_equivalent_and_future_invariant(self) -> None:
        rules = deepcopy(self.rules)
        rules["indicators"] = {
            "price_box": {"period": 5},
            "moving_averages": [5, 20],
        }
        rules["buy"]["groups"][0]["conditions"] = [{
            "enabled": True,
            "not": False,
            "target": "CLOSE",
            "operator": ">=",
            "compare_target": "PRICE_BOX_LOWER",
            "value": 0.0,
        }]
        settings = ValidationSettingsSnapshot(rules)
        session = ValidationSession(
            ValidationRequest(self.stock, settings, 3),
            operation_active_reader=Mock(return_value=False),
        )
        closes = tuple(
            100 + ((index * 7) % 17) - (index % 5)
            for index in range(70)
        )
        historical = self._historical(closes=closes)
        replay = ValidationHistoricalReplay(session)
        detailed = replay.evaluate(historical)
        scanned = replay.scan_signal_entries(historical)

        self.assertTrue(detailed.ok, detailed)
        def marker_identity(entry):
            return (
                entry.evaluation_side,
                entry.evaluation_index,
                entry.evaluation_time,
                entry.signal,
                entry.signal_index,
                entry.signal_time,
                entry.delay_bar,
            )

        expected = [
            marker_identity(entry)
            for entry in detailed.snapshot.to_entries()
            if entry.signal == entry.evaluation_side
        ]
        self.assertEqual(expected, [marker_identity(entry) for entry in scanned])

        extended = self._historical(closes=closes + (1000, 1, 900, 2, 800))
        extended_scan = replay.scan_signal_entries(extended)
        cutoff_time = detailed.snapshot.to_candles()[-1]["time"]
        prefix_entries = [
            marker_identity(entry)
            for entry in extended_scan
            if entry.evaluation_time <= cutoff_time
        ]
        self.assertEqual(expected, prefix_entries)

    def test_default_replay_builds_base_series_once_per_eligible_prefix(self) -> None:
        historical = self._historical(closes=tuple(range(10, 20)))
        original = routine_macd_engine.build_indicator_series
        calls = []

        def counting_builder(candles, config):
            calls.append(len(candles))
            return original(candles, config)

        with unittest.mock.patch.object(
            routine_macd_engine,
            "build_indicator_series",
            side_effect=counting_builder,
        ):
            optimized = ValidationHistoricalReplay(self._session()).evaluate(historical)

        self.assertTrue(optimized.ok, optimized)
        self.assertEqual(list(range(3, 11)), calls)

    def test_shared_base_series_is_not_mutated_by_side_context_enrichment(self) -> None:
        candles = project_validation_candles(self._historical())[0]
        base_series = routine_macd_engine.build_indicator_follow_base_series(
            candles,
            self.rules,
        )
        original = deepcopy(base_series)

        for side, average_price in (("SELL", 11.0), ("BUY", 12.0)):
            routine_macd_engine.evaluate_indicator_follow_routine(
                deepcopy(candles),
                deepcopy(self.rules),
                {
                    "_indicator_follow_evaluate_side": side,
                    "average_price": average_price,
                },
                _base_series_map=base_series,
            )
            self.assertEqual(original, base_series)
            self.assertNotIn("AVG_PRICE", base_series)
            self.assertNotIn("ORDER_PRICE", base_series)

    def test_default_evaluator_and_average_context_provider_are_read_only(self) -> None:
        candles = project_validation_candles(self._historical())[0]
        rules = deepcopy(self.rules)
        prior_entries = [
            ValidationReplayEntry(
                evaluation_side="BUY",
                evaluation_index=1,
                evaluation_time=candles[1]["time"],
                signal="BUY",
                reason="fixture",
                signal_index=1,
                signal_time=candles[1]["time"],
                delay_bar=0,
                matched_groups=["fixture"],
                details=["fixture"],
                trace={"conditions": [{"nested": [1]}]},
            )
        ]
        candles_before = deepcopy(candles)
        rules_before = deepcopy(rules)
        entries_before = [entry.to_dict() for entry in prior_entries]

        sell_context = build_validation_average_price_context(
            len(candles) - 1,
            "SELL",
            candles,
            prior_entries,
        )
        sell_context_before = deepcopy(sell_context)
        base_series = routine_macd_engine.build_indicator_follow_base_series(
            candles,
            rules,
        )
        for side, supplied_context in (
            ("SELL", sell_context),
            (
                "BUY",
                build_validation_average_price_context(
                    len(candles) - 1,
                    "BUY",
                    candles,
                    prior_entries,
                ),
            ),
        ):
            observer = ValidationTraceObserver()
            routine_macd_engine.evaluate_indicator_follow_routine(
                candles,
                rules,
                {
                    **supplied_context,
                    "decision_trace_observer": observer,
                    "_indicator_follow_evaluate_side": side,
                },
                _base_series_map=base_series,
            )
            observer.snapshot()
        buy_context = build_validation_average_price_context(
            len(candles) - 1,
            "BUY",
            candles,
            prior_entries,
        )

        self.assertEqual(candles_before, candles)
        self.assertEqual(rules_before, rules)
        self.assertEqual(entries_before, [entry.to_dict() for entry in prior_entries])
        self.assertEqual(sell_context_before, sell_context)
        self.assertIsNot(sell_context, buy_context)
        self.assertIsNot(
            sell_context["average_price_series"],
            buy_context["average_price_series"],
        )
        sell_context["average_price_series"][0] = -1
        self.assertNotEqual(
            sell_context["average_price_series"],
            buy_context["average_price_series"],
        )

    def test_entry_creation_does_not_mutate_prefix_signal_or_trace(self) -> None:
        prefix = project_validation_candles(self._historical())[0]
        signal = RoutineSignal(
            "BUY",
            "fixture",
            ["fixture_group"],
            ["fixture_detail"],
            1,
            0,
        )
        trace = {"conditions": [{"nested": [1]}]}
        prefix_before = deepcopy(prefix)
        matched_before = deepcopy(signal.matched_groups)
        details_before = deepcopy(signal.details)
        trace_before = deepcopy(trace)

        entry = ValidationHistoricalReplay._entry_from_signal(
            "BUY",
            2,
            prefix[2]["time"],
            prefix,
            signal,
            trace,
        )

        self.assertEqual(prefix_before, prefix)
        self.assertEqual(matched_before, signal.matched_groups)
        self.assertEqual(details_before, signal.details)
        self.assertEqual(trace_before, trace)
        self.assertEqual(trace_before, entry.trace)

    def test_custom_evaluator_nested_mutation_remains_isolated(self) -> None:
        observed = []

        def evaluator(candles, config, _context):
            observed.append(
                (
                    candles[0]["time"],
                    config["buy"]["groups"][0]["conditions"][0]["value"],
                )
            )
            candles[0]["time"] = "19990101000000"
            config["buy"]["groups"][0]["conditions"][0]["value"] = -1
            return self._none_signal()

        before = self.settings.to_dict()
        result = ValidationHistoricalReplay(
            self._session(),
            evaluator=evaluator,
        ).evaluate(self._historical(closes=(10, 11, 12)), start_index=2)

        self.assertTrue(result.ok, result)
        self.assertEqual(2, len(observed))
        self.assertEqual(observed[0], observed[1])
        self.assertEqual(before, self.settings.to_dict())
        self.assertEqual("20260913090000", result.snapshot.to_candles()[0]["time"])

    def test_custom_context_provider_prefix_mutation_remains_isolated(self) -> None:
        evaluator_inputs = []

        def evaluator(candles, _config, _context):
            evaluator_inputs.append(candles[0]["time"])
            return self._none_signal()

        def context_provider(_index, _side, candles, _prior_entries):
            candles[0]["time"] = "19990101000000"
            return {"nested": {"value": 1}}

        result = ValidationHistoricalReplay(
            self._session(),
            evaluator=evaluator,
        ).evaluate_with_context(
            self._historical(closes=(10, 11, 12)),
            start_index=2,
            context_provider=context_provider,
        )

        self.assertTrue(result.ok, result)
        self.assertEqual(["20260913090000", "20260913090000"], evaluator_inputs)
        self.assertEqual("20260913090000", result.snapshot.to_candles()[0]["time"])

    def test_unhashable_custom_context_provider_keeps_existing_contract(self) -> None:
        class UnhashableContextProvider:
            __hash__ = None

            def __call__(self, _index, _side, _candles, _prior_entries):
                return {}

        result = ValidationHistoricalReplay(self._session()).evaluate_with_context(
            self._historical(closes=(10, 11, 12)),
            start_index=2,
            context_provider=UnhashableContextProvider(),
        )

        self.assertTrue(result.ok, result)

    def test_future_candles_do_not_change_index_99_series_signal_or_trace(self) -> None:
        prefix = tuple(100 + ((index % 23) - 11) * 0.8 for index in range(100))

        def historical(closes):
            start = datetime(2026, 9, 13, 9, 0)
            rows = [
                {
                    "체결시간": (start + timedelta(minutes=index)).strftime(
                        "%Y%m%d%H%M%S"
                    ),
                    "현재가": str(close),
                    "거래량": str(100 + index),
                }
                for index, close in reversed(list(enumerate(closes)))
            ]
            return self._historical(rows=rows)

        historical_a = historical(prefix)
        historical_b = historical(
            prefix + (10000.0, 1.0, 20000.0, 2.0, 30000.0)
        )

        candles_a = project_validation_candles(historical_a)[0]
        candles_b = project_validation_candles(historical_b)[0]
        series_a = routine_macd_engine.build_indicator_follow_base_series(
            candles_a[:100],
            self.rules,
        )
        series_b = routine_macd_engine.build_indicator_follow_base_series(
            candles_b[:100],
            self.rules,
        )
        for key in (
            "MACD",
            "OSC",
            "RSI",
            "MA5",
            "MA20",
            "MA60",
            "BOLLINGER_LOWER",
            "BOLLINGER_MIDDLE",
            "BOLLINGER_UPPER",
            "PRICE_BOX_LOWER",
            "PRICE_BOX_MIDDLE",
            "PRICE_BOX_UPPER",
        ):
            self.assertEqual(series_a[key], series_b[key], key)

        result_a = ValidationHistoricalReplay(self._session()).evaluate_with_context(
            historical_a,
            end_index=99,
            context_provider=build_validation_average_price_context,
        )
        result_b = ValidationHistoricalReplay(self._session()).evaluate_with_context(
            historical_b,
            end_index=99,
            context_provider=build_validation_average_price_context,
        )
        self.assertTrue(result_a.ok, result_a)
        self.assertTrue(result_b.ok, result_b)
        entries_a = [
            entry.to_dict()
            for entry in result_a.snapshot.to_entries()
            if entry.evaluation_index == 99
        ]
        entries_b = [
            entry.to_dict()
            for entry in result_b.snapshot.to_entries()
            if entry.evaluation_index == 99
        ]
        self.assertEqual(entries_a, entries_b)

    def test_snapshot_metadata_preserves_validation_identity(self) -> None:
        result = ValidationHistoricalReplay(self._session()).evaluate(
            self._historical(),
            start_index=2,
            end_index=4,
        )

        snapshot = result.snapshot
        self.assertEqual(self.stock, snapshot.stock)
        self.assertEqual(3, snapshot.timeframe_minutes)
        self.assertEqual(self.settings.rules_hash, snapshot.settings_hash)
        self.assertEqual("OPT10080-REPLAY-1", snapshot.historical_request_id)
        self.assertEqual(5, snapshot.candle_count)
        self.assertEqual(2, snapshot.evaluated_start_index)
        self.assertEqual(4, snapshot.evaluated_end_index)
        self.assertEqual(6, len(snapshot.to_entries()))

    def test_implementation_imports_only_allowed_validation_and_evaluator_boundaries(self) -> None:
        source_path = (
            Path(__file__).resolve().parents[1]
            / "routines"
            / "지표추종매매"
            / "routine_validation_replay.py"
        )
        tree = ast.parse(source_path.read_text(encoding="utf-8-sig"))
        imported_modules = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.append(node.module)
        forbidden = (
            "kiwoom_api",
            "kiwoom_candle_adapter",
            "candle_manager",
            "stock_repository",
            "gui_market_data_host",
            "gui_auto_trade_",
            "routine_signal_",
            "order",
            "execution",
            "mock_validation",
        )
        for module_name in imported_modules:
            for fragment in forbidden:
                with self.subTest(module=module_name, fragment=fragment):
                    self.assertNotIn(fragment, module_name.lower())


if __name__ == "__main__":
    unittest.main()
