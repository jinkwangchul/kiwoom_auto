# -*- coding: utf-8 -*-
from __future__ import annotations

from types import SimpleNamespace
import unittest

from indicator_follow_signal_validation_execution import (
    ValidationVirtualPositionTracker,
)
from indicator_follow_signal_validation_projection import (
    build_validation_average_price_context,
)
from routines.지표추종매매 import routine
from routines.지표추종매매.routine_validation_batch import (
    _IncrementalAverageContext,
)


def _candles(prices: list[float]) -> list[dict[str, object]]:
    return [
        {
            "time": f"2026092609{index:02d}00",
            "bar_time": f"2026092609{index:02d}00",
            "close": price,
        }
        for index, price in enumerate(prices)
    ]


def _entry(side: str, index: int, candles: list[dict[str, object]]) -> SimpleNamespace:
    return SimpleNamespace(
        evaluation_side=side,
        evaluation_index=index,
        evaluation_time=candles[index]["time"],
        signal=side,
    )


def _production_selection(holding_quantity: int) -> dict[str, object]:
    candles = _candles([100.0])

    def both_true(_candles, _config, context):
        side = context["_indicator_follow_evaluate_side"]
        return {
            "signal": side,
            "reason": "fixture",
            "matched_groups": [],
            "details": [],
            "signal_index": 0,
            "delay_bar": 0,
        }

    result = routine.evaluate_signal_selection(
        candles,
        {"enabled": True},
        {"cycle": {"holding_qty": holding_quantity}},
        evaluator=both_true,
        converter=lambda value: dict(value),
    )
    return result


class IndicatorFollowSignalSelectionParityTest(unittest.TestCase):
    def _assert_average_contexts(
        self,
        evaluation_index: int,
        candles: list[dict[str, object]],
        entries: list[SimpleNamespace],
        expected_quantity: int,
    ) -> None:
        projected = build_validation_average_price_context(
            evaluation_index,
            "SELL",
            candles,
            entries,
        )
        incremental = _IncrementalAverageContext().context_for_fast(
            evaluation_index,
            "SELL",
            candles,
            entries,
        )
        for context in (projected, incremental):
            self.assertEqual(
                expected_quantity,
                context["validation_trace_context"]["position_quantity"],
            )

    def test_both_true_without_holding_selects_buy_everywhere(self):
        candles = _candles([100.0, 110.0])
        entries = [_entry("SELL", 0, candles), _entry("BUY", 0, candles)]
        tracker = ValidationVirtualPositionTracker()

        simulation = tracker.finalize(candles, entries)
        production = _production_selection(0)

        self.assertEqual("BUY", production["signal"])
        self.assertEqual({
            "conflict": "BUY_AND_SELL_TRUE",
            "holding_quantity": 0,
            "fallback": "SELL_WHEN_HOLDING_ELSE_BUY",
            "selected_side": "BUY",
            "buy_signal_index": 0,
            "sell_signal_index": 0,
        }, production["signal_conflict_evidence"])
        self.assertEqual([("BUY", 0)], [
            (fill.side, fill.evaluation_index) for fill in simulation.fills
        ])
        self.assertEqual(1, simulation.open_quantity)
        self._assert_average_contexts(1, candles, entries, 1)

    def test_both_true_with_holding_selects_sell_everywhere(self):
        candles = _candles([100.0, 110.0, 120.0])
        entries = [
            _entry("BUY", 0, candles),
            _entry("SELL", 1, candles),
            _entry("BUY", 1, candles),
        ]
        tracker = ValidationVirtualPositionTracker()

        simulation = tracker.finalize(candles, entries)
        production = _production_selection(1)

        self.assertEqual("SELL", production["signal"])
        self.assertEqual("SELL", production["signal_conflict_evidence"]["selected_side"])
        self.assertEqual([("BUY", 0), ("SELL", 1)], [
            (fill.side, fill.evaluation_index) for fill in simulation.fills
        ])
        self.assertEqual(0, simulation.open_quantity)
        self._assert_average_contexts(2, candles, entries, 0)


if __name__ == "__main__":
    unittest.main()
