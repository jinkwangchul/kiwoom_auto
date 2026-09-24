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
    def test_virtual_fill_price_uses_positive_close_only(self):
        self.assertEqual(
            110.0,
            validation_virtual_fill_price({
                "open": 100,
                "high": 110,
                "low": 90,
                "close": 110,
            }),
        )
        self.assertEqual(100.0, validation_virtual_fill_price({"close": 100}))
        self.assertEqual(105.0, validation_virtual_fill_price({
            "open": 100, "high": 110, "low": 0, "close": 105,
        }))
        self.assertIsNone(validation_virtual_fill_price({"close": -1}))

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

    def test_averaging_disabled_buys_exactly_one_share_per_buy_signal(self):
        candles = _candles([100, 100, 100, 120])
        simulation = simulate_validation_execution(
            candles,
            [
                _entry("BUY", 0, candles),
                _entry("BUY", 1, candles),
                _entry("BUY", 2, candles),
                _entry("SELL", 3, candles),
            ],
            {
                "enabled": False,
                "first_buy_quantity": 9,
                "repeat_mode": "BUDGET",
                "budget_ratio": 5.0,
            },
        )

        self.assertEqual([1, 1, 1], [
            fill.quantity for fill in simulation.fills if fill.side == "BUY"
        ])
        cycle = simulation.cycles[0]
        self.assertEqual(3, cycle.buy_quantity)
        self.assertEqual(300.0, cycle.buy_cost)
        self.assertEqual(100.0, cycle.average_buy_price)
        self.assertEqual(360.0, cycle.sell_price * cycle.sell_quantity)

    def test_averaging_enabled_uses_starting_quantity_before_repeat_policy(self):
        candles = _candles([100, 100, 120])
        simulation = simulate_validation_execution(
            candles,
            [
                _entry("BUY", 0, candles),
                _entry("BUY", 1, candles),
                _entry("SELL", 2, candles),
            ],
            {
                "enabled": True,
                "first_buy_quantity": 2,
                "repeat_mode": "BUDGET",
                "budget_ratio": 2.0,
            },
        )

        self.assertEqual([2, 4], [
            fill.quantity for fill in simulation.fills if fill.side == "BUY"
        ])
        cycle = simulation.cycles[0]
        self.assertEqual(6, cycle.buy_quantity)
        self.assertEqual(600.0, cycle.buy_cost)
        self.assertEqual(100.0, cycle.average_buy_price)

    def test_round_add_reuses_production_budget_formula(self):
        candles = _candles([100, 100, 100, 120])
        policy = {
            "enabled": True,
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

    def test_budget_mode_compounds_previous_approved_budget_without_shrinking_to_spend(self):
        candles = _candles([10_000, 10_000, 10_000, 10_000, 12_000])
        policy = {
            "enabled": True,
            "repeat_mode": "BUDGET",
            "budget_ratio": 1.3,
        }
        simulation = simulate_validation_execution(
            candles,
            [
                _entry("BUY", 0, candles),
                _entry("BUY", 1, candles),
                _entry("BUY", 2, candles),
                _entry("BUY", 3, candles),
                _entry("SELL", 4, candles),
            ],
            policy,
        )

        # Approved budgets progress 10,000 -> 13,000 -> 16,900 -> 21,970.
        # Integer-share execution spends 10,000, 10,000, 10,000, 20,000,
        # but the unspent part never shrinks the next approved budget.
        self.assertEqual([1, 1, 1, 2], [
            fill.quantity for fill in simulation.fills if fill.side == "BUY"
        ])
        cycle = simulation.cycles[0]
        self.assertEqual(5, cycle.buy_quantity)
        self.assertEqual(50_000.0, cycle.buy_cost)
        self.assertEqual(10_000.0, cycle.average_buy_price)

    def test_active_buy_uses_position_average_and_virtual_fill_price(self):
        candles = _candles([100, 80, 90])
        policy = {
            "enabled": True,
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

    def test_active_buy_target_and_fill_use_same_candle_close_price(self):
        candles = [
            {
                "time": "20260918090000",
                "open": 150.0,
                "high": 150.0,
                "low": 150.0,
                "close": 150.0,
                "volume": 1,
            },
            {
                "time": "20260918090100",
                "open": 70.0,
                "high": 120.0,
                "low": 70.0,
                "close": 110.0,
                "volume": 1,
            },
            {
                "time": "20260918090200",
                "open": 120.0,
                "high": 120.0,
                "low": 120.0,
                "close": 120.0,
                "volume": 1,
            },
        ]
        simulation = simulate_validation_execution(
            candles,
            [
                _entry("BUY", 0, candles),
                _entry("BUY", 1, candles),
                _entry("SELL", 2, candles),
            ],
            {
                "enabled": True,
                "repeat_mode": "ACTIVE_BUY",
                "active_direction": "UP",
                "active_ratio": 10.0,
                "active_compare": "<=",
            },
        )

        buys = [fill for fill in simulation.fills if fill.side == "BUY"]
        self.assertEqual([1, 3], [fill.quantity for fill in buys])
        self.assertEqual(110.0, buys[1].price)
        self.assertAlmostEqual(
            (150.0 + 110.0 * 3) / 4,
            simulation.cycles[0].average_buy_price,
        )

    def test_budget_ratio_must_be_greater_than_one_when_budget_mode_is_enabled(self):
        for value in (0, 0.5, 1, 1.0):
            with self.subTest(value=value), self.assertRaisesRegex(
                ValueError,
                "BUDGET_RATIO_INVALID",
            ):
                normalize_validation_execution_policy({
                    "enabled": True,
                    "repeat_mode": "BUDGET",
                    "budget_ratio": value,
                })

        normalized = normalize_validation_execution_policy({
            "enabled": True,
            "repeat_mode": "ROUND",
            "budget_ratio": 0.5,
        })
        self.assertEqual(2.0, normalized["budget_ratio"])

    def test_active_buy_preserves_user_selected_direction_comparator_combination(self):
        policy = normalize_validation_execution_policy({
            "enabled": True,
            "repeat_mode": "ACTIVE_BUY",
            "active_direction": "UP",
            "active_ratio": 1.25,
            "active_compare": "OUTSIDE",
        })

        self.assertEqual("UP", policy["active_direction"])
        self.assertEqual("OUTSIDE", policy["active_compare"])

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
            "enabled": True,
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

    def test_policy_normalizes_averaging_enable_and_starting_quantity(self):
        fresh_policy = normalize_validation_execution_policy(None)
        self.assertFalse(fresh_policy["enabled"])

        policy = normalize_validation_execution_policy({
            "enabled": False,
            "first_buy_quantity": "3",
        })
        self.assertFalse(policy["enabled"])
        self.assertEqual(3, policy["first_buy_quantity"])

        disabled_with_invalid_details = normalize_validation_execution_policy({
            "enabled": False,
            "first_buy_quantity": "invalid",
            "repeat_mode": "invalid",
            "round_operator": "invalid",
            "round_budget_value": "invalid",
            "budget_ratio": "invalid",
            "active_direction": "invalid",
            "active_ratio": "invalid",
            "active_compare": "invalid",
        })
        self.assertFalse(disabled_with_invalid_details["enabled"])
        self.assertEqual(1, disabled_with_invalid_details["first_buy_quantity"])

        with self.assertRaisesRegex(
            ValueError,
            "VALIDATION_FIRST_BUY_QUANTITY_INVALID",
        ):
            normalize_validation_execution_policy({
                "enabled": True,
                "first_buy_quantity": "0",
            })

    def test_invalid_repeat_configuration_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "VALIDATION_REPEAT_MODE_INVALID"):
            normalize_validation_execution_policy({
                "enabled": True,
                "repeat_mode": "UNKNOWN",
            })


if __name__ == "__main__":
    unittest.main()
