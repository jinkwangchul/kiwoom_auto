# -*- coding: utf-8 -*-
from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError
from pathlib import Path
import unittest
from unittest.mock import Mock

from engines.signal_result import RoutineSignal
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
    project_validation_candles,
)
from routines.지표추종매매.routine_validation_session import (
    REASON_OPERATION_ACTIVE,
    ValidationSession,
)


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
            requested_count=max(len(rows), 1),
            request_id="OPT10080-REPLAY-1",
            rows=rows,
        )

    @staticmethod
    def _none_signal() -> RoutineSignal:
        return RoutineSignal(None, "none", [], [], -1, 0)

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

    def test_operation_active_before_replay_prevents_evaluator_calls(self) -> None:
        evaluator = Mock(return_value=self._none_signal())
        reader = Mock(return_value=True)

        result = ValidationHistoricalReplay(
            self._session(reader),
            evaluator=evaluator,
        ).evaluate(self._historical())

        self.assertFalse(result.ok)
        self.assertEqual(REASON_OPERATION_ACTIVE, result.reason)
        evaluator.assert_not_called()
        reader.assert_called_once_with()

    def test_operation_start_during_replay_discards_computed_result(self) -> None:
        evaluator = Mock(return_value=self._none_signal())
        reader = Mock(side_effect=(False, True))

        result = ValidationHistoricalReplay(
            self._session(reader),
            evaluator=evaluator,
        ).evaluate(self._historical(closes=(10, 11, 12)))

        self.assertFalse(result.ok)
        self.assertEqual(REASON_OPERATION_ACTIVE, result.reason)
        self.assertIsNone(result.snapshot)
        self.assertEqual(6, evaluator.call_count)
        self.assertEqual(2, reader.call_count)

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
