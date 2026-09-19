# -*- coding: utf-8 -*-
from __future__ import annotations

from types import SimpleNamespace
import unittest

from indicator_follow_signal_validation_execution import (
    ValidationVirtualPositionTracker,
    normalize_validation_execution_policy,
    simulate_validation_execution,
    validation_virtual_fill_price,
)


def _candles(prices):
    return [
        {
            "time": f"2026091809{index:02d}00",
            "open": float(price),
            "high": float(price),
            "low": float(price),
            "close": float(price),
            "volume": 1,
        }
        for index, price in enumerate(prices)
    ]


def _entry(side, index, candles):
    return SimpleNamespace(
        evaluation_side=side,
        evaluation_index=index,
        evaluation_time=candles[index]["time"],
        signal=side,
    )


class ValidationVirtualExecutionTest(unittest.TestCase):
    def test_virtual_fill_price_requires_complete_positive_ohlc(self):
        self.assertEqual(
            102.5,
            validation_virtual_fill_price({
                "open": 100,
                "high": 110,
                "low": 90,
                "close": 110,
            }),
        )
        self.assertIsNone(validation_virtual_fill_price({"close": 100}))
        self.assertIsNone(validation_virtual_fill_price({
            "open": 100, "high": 110, "low": 0, "close": 105,
        }))

    def test_first_buy_is_one_share_and_single_sell_closes_all(self):
        candles = _candles([100, 110])
        simulation = simulate_validation_execution(
            candles,
            [_entry("BUY", 0, candles), _entry("SELL", 1, candles)],
        )

        self.assertEqual([("BUY", 1), ("SELL", 1)], [
            (fill.side, fill.quantity) for fill in simulation.fills
        ])
        self.assertEqual(1, len(simulation.cycles))
        cycle = simulation.cycles[0]
        self.assertEqual(1, cycle.buy_count)
        self.assertEqual(1, cycle.buy_quantity)
        self.assertEqual(100.0, cycle.buy_cost)
        self.assertEqual(100.0, cycle.average_buy_price)
        self.assertEqual(110.0, cycle.sell_price)
        self.assertAlmostEqual(10.0, cycle.estimated_return_percent)
        self.assertEqual(0, simulation.open_quantity)

    def test_round_add_reuses_production_budget_formula(self):
        candles = _candles([100, 100, 100, 120])
        policy = {
            "repeat_mode": "ROUND",
            "round_operator": "ADD",
            "round_budget_value": 0.5,
        }
        simulation = simulate_validation_execution(
            candles,
            [
                _entry("BUY", 0, candles),
                _entry("BUY", 1, candles),
                _entry("BUY", 2, candles),
                _entry("SELL", 3, candles),
            ],
            policy,
        )

        self.assertEqual([1, 1, 2], [
            fill.quantity for fill in simulation.fills if fill.side == "BUY"
        ])
        self.assertEqual([1, 2, 3], [
            fill.buy_round for fill in simulation.fills if fill.side == "BUY"
        ])
        self.assertEqual(4, simulation.cycles[0].buy_quantity)
        self.assertEqual(400.0, simulation.cycles[0].buy_cost)

    def test_budget_mode_compounds_previous_actual_buy_cost(self):
        candles = _candles([100, 100, 100, 110])
        policy = {
            "repeat_mode": "BUDGET",
            "budget_ratio": 2.0,
        }
        simulation = simulate_validation_execution(
            candles,
            [
                _entry("BUY", 0, candles),
                _entry("BUY", 1, candles),
                _entry("BUY", 2, candles),
                _entry("SELL", 3, candles),
            ],
            policy,
        )

        self.assertEqual([1, 2, 4], [
            fill.quantity for fill in simulation.fills if fill.side == "BUY"
        ])
        cycle = simulation.cycles[0]
        self.assertEqual(7, cycle.buy_quantity)
        self.assertEqual(700.0, cycle.buy_cost)
        self.assertEqual(100.0, cycle.average_buy_price)

    def test_active_buy_uses_position_average_and_virtual_fill_price(self):
        candles = _candles([100, 80, 90])
        policy = {
            "repeat_mode": "ACTIVE_BUY",
            "active_direction": "UP",
            "active_ratio": 10.0,
            "active_compare": "<=",
        }
        simulation = simulate_validation_execution(
            candles,
            [
                _entry("BUY", 0, candles),
                _entry("BUY", 1, candles),
                _entry("SELL", 2, candles),
            ],
            policy,
        )

        buys = [fill for fill in simulation.fills if fill.side == "BUY"]
        self.assertEqual([1, 2], [fill.quantity for fill in buys])
        self.assertAlmostEqual((100 + 80 * 2) / 3, simulation.cycles[0].average_buy_price)
        self.assertEqual(3, simulation.cycles[0].buy_quantity)

    def test_same_bar_sell_wins_and_does_not_reopen_buy(self):
        candles = _candles([100, 110, 120])
        simulation = simulate_validation_execution(
            candles,
            [
                _entry("BUY", 0, candles),
                _entry("SELL", 1, candles),
                _entry("BUY", 1, candles),
                _entry("SELL", 2, candles),
            ],
        )

        self.assertEqual([("BUY", 0), ("SELL", 1)], [
            (fill.side, fill.evaluation_index) for fill in simulation.fills
        ])
        self.assertEqual(1, len(simulation.cycles))
        self.assertEqual(0, simulation.open_quantity)

    def test_incremental_tracker_uses_quantity_weighted_average_before_sell(self):
        candles = _candles([100, 50, 80])
        entries = [
            _entry("BUY", 0, candles),
            _entry("BUY", 1, candles),
            _entry("SELL", 2, candles),
        ]
        tracker = ValidationVirtualPositionTracker({
            "repeat_mode": "BUDGET",
            "budget_ratio": 2.0,
        })

        first = tracker(0, "SELL", candles[:1], [])
        self.assertIsNone(first["average_price"])
        tracker(1, "SELL", candles[:2], entries[:1])
        sell_context = tracker(2, "SELL", candles, entries[:2])

        self.assertEqual(5, sell_context["validation_trace_context"]["position_quantity"])
        self.assertEqual([0, 1], sell_context["validation_trace_context"]["contributing_buy_indexes"])
        self.assertAlmostEqual(60.0, sell_context["average_price"])
        self.assertEqual(
            [None, 100.0, 60.0],
            sell_context["average_price_series"],
        )

        simulation = tracker.finalize(candles, entries)
        self.assertEqual(1, len(simulation.cycles))
        self.assertAlmostEqual(60.0, simulation.cycles[0].average_buy_price)

    def test_invalid_repeat_configuration_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "VALIDATION_REPEAT_MODE_INVALID"):
            normalize_validation_execution_policy({"repeat_mode": "UNKNOWN"})


if __name__ == "__main__":
    unittest.main()
