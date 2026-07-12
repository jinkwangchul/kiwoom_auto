from copy import deepcopy
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import unittest

from engines.condition_engine import evaluate_condition, evaluate_group, evaluate_groups_or


def _load_routine_engine_module():
    project_root = Path(__file__).resolve().parents[1]
    engine_path = next((project_root / "routines").glob("*/routine_macd_engine.py"))
    spec = spec_from_file_location("routine_macd_engine_for_condition_test", engine_path)
    module = module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class ConditionEngineTest(unittest.TestCase):
    def setUp(self):
        self.series_map = {
            "OSC": [3.0, 2.0, 3.0],
            "RSI": [60.0, 50.0, 45.0],
            "MACD": [0.1, 0.2, 0.5],
            "SIGNAL": [0.2, 0.3, 0.4],
            "CLOSE": [9.0, 10.0, 12.0],
            "MA5": [13.0, 14.0, 15.0],
            "MA20": [10.0, 11.0, 12.0],
            "MA60": [10.0, 10.0, 11.0],
            "MA120": [13.0, 12.0, 12.0],
            "AVG_PRICE": [11.0, 11.0, 11.5],
        }

    def assertConditionPassed(self, condition):
        result = evaluate_condition(condition, self.series_map)
        self.assertTrue(result.passed, result)

    def test_ocr_condition_turn_up(self):
        self.assertConditionPassed({"target": "OSC", "operator": "TURN_UP"})

    def test_rsi_condition_threshold(self):
        self.assertConditionPassed({"target": "RSI", "operator": "<=", "value": 45})

    def test_macd_signal_position_condition(self):
        self.assertConditionPassed({"target": "MACD", "operator": ">", "compare_target": "SIGNAL"})

    def test_moving_average_cross_condition(self):
        self.assertConditionPassed({
            "target": "CLOSE",
            "operator": "CROSS_UP",
            "compare_target": "MA",
            "period": 60,
        })

    def test_explicit_moving_average_series_key_condition(self):
        self.assertConditionPassed({"target": "MA5", "operator": ">", "compare_target": "MA20"})

    def test_moving_average_compare_period_contract(self):
        self.assertConditionPassed({
            "target": "MA",
            "period": 5,
            "operator": ">",
            "compare_target": "MA",
            "compare_period": 20,
        })
        self.assertConditionPassed({
            "target": "MA",
            "period": 20,
            "operator": ">",
            "compare_target": "MA",
            "compare_period": 60,
        })
        self.assertConditionPassed({
            "target": "MA",
            "period": 60,
            "operator": "<",
            "compare_target": "MA",
            "compare_period": 120,
        })

    def test_moving_average_legacy_compare_target_matches_compare_period_contract(self):
        official = evaluate_condition(
            {
                "target": "MA",
                "period": 5,
                "operator": ">",
                "compare_target": "MA",
                "compare_period": 20,
            },
            self.series_map,
        )
        legacy = evaluate_condition(
            {"target": "MA", "period": 5, "operator": ">", "compare_target": "MA20"},
            self.series_map,
        )

        self.assertEqual(official.passed, legacy.passed)
        self.assertTrue(legacy.passed, legacy)

    def test_price_compare_condition_uses_percent_offset(self):
        self.assertConditionPassed({
            "target": "CLOSE",
            "operator": ">=",
            "compare_target": "AVG_PRICE",
            "value": 0.15,
        })

    def test_bollinger_candidate_threshold_condition(self):
        self.assertConditionPassed({"target": "CLOSE", "operator": ">=", "value": -0.1})

    def test_and_group_requires_all_conditions(self):
        group = {
            "conditions_logic": "AND",
            "conditions": [
                {"target": "OSC", "operator": "TURN_UP"},
                {"target": "RSI", "operator": "<=", "value": 45},
            ],
        }

        result = evaluate_group(group, self.series_map)

        self.assertTrue(result.passed, result)

    def test_or_group_accepts_any_condition(self):
        group = {
            "conditions_logic": "OR",
            "conditions": [
                {"target": "RSI", "operator": "<", "value": 10},
                {"target": "OSC", "operator": "TURN_UP"},
            ],
        }

        result = evaluate_group(group, self.series_map)

        self.assertTrue(result.passed, result)

    def test_not_condition_inverts_result(self):
        result = evaluate_condition(
            {"not": True, "target": "RSI", "operator": "<", "value": 10},
            self.series_map,
        )

        self.assertTrue(result.passed, result)

    def test_empty_group_is_false(self):
        result = evaluate_group({"conditions": []}, self.series_map)

        self.assertFalse(result.passed)

    def test_disabled_condition_keeps_existing_pass_through_meaning(self):
        result = evaluate_condition(
            {"enabled": False, "target": "RSI", "operator": "<", "value": 10},
            self.series_map,
        )

        self.assertTrue(result.passed, result)

    def test_groups_are_or_by_default(self):
        passed, results = evaluate_groups_or(
            [
                {"name": "fail", "conditions": [{"target": "RSI", "operator": "<", "value": 10}]},
                {"name": "pass", "conditions": [{"target": "OSC", "operator": "TURN_UP"}]},
            ],
            self.series_map,
        )

        self.assertTrue(passed)
        self.assertEqual([result.group_name for result in results if result.passed], ["pass"])

    def test_evaluate_indicator_follow_routine_uses_buy_group_conditions(self):
        module = _load_routine_engine_module()
        config = deepcopy(module.DEFAULT_INDICATOR_FOLLOW_CONFIG)
        config["buy"]["delay_bar"] = 0
        config["buy"]["groups"] = [
            {
                "enabled": True,
                "name": "ui_buy_conditions",
                "conditions": [
                    {"enabled": True, "not": False, "target": "CLOSE", "operator": ">=", "value": 12}
                ],
            }
        ]
        config["sell"] = {
            "delay_bar": 0,
            "signals": {"macd_sell": {"enabled": False, "groups": []}},
        }
        candles = [
            {"close": 10, "volume": 100},
            {"close": 11, "volume": 100},
            {"close": 12, "volume": 100},
        ]

        signal = module.evaluate_indicator_follow_routine(candles, config, {})

        self.assertEqual(signal.signal, "BUY")
        self.assertEqual(signal.matched_groups, ["ui_buy_conditions"])

    def test_evaluate_indicator_follow_routine_returns_none_when_buy_conditions_fail(self):
        module = _load_routine_engine_module()
        config = deepcopy(module.DEFAULT_INDICATOR_FOLLOW_CONFIG)
        config["buy"]["delay_bar"] = 0
        config["buy"]["groups"] = [
            {
                "enabled": True,
                "name": "ui_buy_conditions",
                "conditions": [
                    {"enabled": True, "not": False, "target": "CLOSE", "operator": ">", "value": 99}
                ],
            }
        ]
        config["sell"] = {
            "delay_bar": 0,
            "signals": {"macd_sell": {"enabled": False, "groups": []}},
        }
        candles = [
            {"close": 10, "volume": 100},
            {"close": 11, "volume": 100},
            {"close": 12, "volume": 100},
        ]

        signal = module.evaluate_indicator_follow_routine(candles, config, {})

        self.assertIsNone(signal.signal)

    def test_buy_signal_to_dict_preserves_public_shape(self):
        module = _load_routine_engine_module()
        config = deepcopy(module.DEFAULT_INDICATOR_FOLLOW_CONFIG)
        config["buy"]["delay_bar"] = 0
        config["buy"]["groups"] = [
            {
                "enabled": True,
                "name": "ui_buy_conditions",
                "conditions": [
                    {"enabled": True, "not": False, "target": "CLOSE", "operator": ">=", "value": 12}
                ],
            }
        ]
        config["sell"] = {
            "delay_bar": 0,
            "signals": {"macd_sell": {"enabled": False, "groups": []}},
        }
        candles = [
            {"close": 10, "volume": 100},
            {"close": 11, "volume": 100},
            {"close": 12, "volume": 100},
        ]

        signal = module.evaluate_indicator_follow_routine(candles, config, {})
        payload = module.signal_to_dict(signal)

        self.assertEqual(payload["signal"], "BUY")
        self.assertTrue(payload["reason"])
        self.assertEqual(payload["matched_groups"], ["ui_buy_conditions"])
        self.assertTrue(payload["details"])
        self.assertIsInstance(payload["signal_index"], int)
        self.assertEqual(payload["delay_bar"], 0)
        self.assertEqual(
            sorted(payload),
            ["delay_bar", "details", "matched_groups", "reason", "signal", "signal_index"],
        )

    def test_buy_signal_is_returned_when_sell_is_disabled(self):
        module = _load_routine_engine_module()
        config = deepcopy(module.DEFAULT_INDICATOR_FOLLOW_CONFIG)
        config["buy"]["delay_bar"] = 0
        config["buy"]["groups"] = [
            {
                "enabled": True,
                "name": "ui_buy_conditions",
                "conditions": [
                    {"enabled": True, "not": False, "target": "CLOSE", "operator": ">=", "value": 12}
                ],
            }
        ]
        config["sell"] = {
            "delay_bar": 0,
            "signals": {
                "macd_sell": {
                    "enabled": False,
                    "groups": [
                        {
                            "enabled": True,
                            "name": "disabled_sell",
                            "conditions": [{"target": "CLOSE", "operator": ">=", "value": 12}],
                        }
                    ],
                }
            },
        }
        candles = [
            {"close": 10, "volume": 100},
            {"close": 11, "volume": 100},
            {"close": 12, "volume": 100},
        ]

        signal = module.evaluate_indicator_follow_routine(candles, config, {})

        self.assertEqual(signal.signal, "BUY")
        self.assertEqual(signal.matched_groups, ["ui_buy_conditions"])

    def test_disabled_ui_condition_c_signal_is_not_evaluated(self):
        module = _load_routine_engine_module()
        config = deepcopy(module.DEFAULT_INDICATOR_FOLLOW_CONFIG)
        config["buy"]["delay_bar"] = 0
        config["buy"]["groups"] = [
            {
                "enabled": True,
                "name": "ui_buy_conditions",
                "conditions": [
                    {"enabled": True, "not": False, "target": "CLOSE", "operator": ">=", "value": 12}
                ],
            }
        ]
        config["sell"] = {
            "delay_bar": 0,
            "signals": {
                "macd_sell": {"enabled": False, "groups": []},
                "ui_condition_c": {
                    "enabled": False,
                    "groups": [
                        {
                            "enabled": True,
                            "name": "disabled_ui_condition_c",
                            "conditions": [{"target": "CLOSE", "operator": ">=", "value": 12}],
                        }
                    ],
                },
            },
        }
        candles = [
            {"close": 10, "volume": 100},
            {"close": 11, "volume": 100},
            {"close": 12, "volume": 100},
        ]

        signal = module.evaluate_indicator_follow_routine(candles, config, {})

        self.assertEqual(signal.signal, "BUY")
        self.assertEqual(signal.matched_groups, ["ui_buy_conditions"])

    def test_disabled_ui_condition_a_signal_is_not_evaluated(self):
        module = _load_routine_engine_module()
        config = deepcopy(module.DEFAULT_INDICATOR_FOLLOW_CONFIG)
        config["buy"]["delay_bar"] = 0
        config["buy"]["groups"] = [
            {
                "enabled": True,
                "name": "ui_buy_conditions",
                "conditions": [
                    {"enabled": True, "not": False, "target": "CLOSE", "operator": ">=", "value": 12}
                ],
            }
        ]
        config["sell"] = {
            "delay_bar": 0,
            "signals": {
                "macd_sell": {"enabled": False, "groups": []},
                "ui_condition_a": {
                    "enabled": False,
                    "groups": [
                        {
                            "enabled": True,
                            "name": "disabled_ui_condition_a",
                            "conditions": [{"target": "CLOSE", "operator": ">=", "value": 12}],
                        }
                    ],
                },
            },
        }
        candles = [
            {"close": 10, "volume": 100},
            {"close": 11, "volume": 100},
            {"close": 12, "volume": 100},
        ]

        signal = module.evaluate_indicator_follow_routine(candles, config, {})

        self.assertEqual(signal.signal, "BUY")
        self.assertEqual(signal.matched_groups, ["ui_buy_conditions"])

    def test_sell_signal_is_returned_when_ui_sell_signal_conditions_pass(self):
        module = _load_routine_engine_module()
        config = deepcopy(module.DEFAULT_INDICATOR_FOLLOW_CONFIG)
        config["buy"]["delay_bar"] = 0
        config["buy"]["groups"] = [
            {"enabled": True, "name": "buy_fail", "conditions": [{"target": "CLOSE", "operator": ">", "value": 99}]}
        ]
        config["sell"] = {
            "delay_bar": 0,
            "signals": {
                "macd_sell": {"enabled": False, "groups": []},
                "ui_condition_c_macd_sell": {
                    "enabled": True,
                    "groups": [
                        {
                            "enabled": True,
                            "name": "ui_sell_conditions",
                            "conditions": [{"target": "CLOSE", "operator": ">=", "value": 12}],
                        }
                    ],
                },
            },
        }
        candles = [
            {"close": 10, "volume": 100},
            {"close": 11, "volume": 100},
            {"close": 12, "volume": 100},
        ]

        signal = module.evaluate_indicator_follow_routine(candles, config, {})

        self.assertEqual(signal.signal, "SELL")
        self.assertEqual(signal.matched_groups, ["ui_sell_conditions"])
        self.assertTrue(signal.details)

    def test_sell_condition_false_keeps_existing_none_return_when_buy_is_false(self):
        module = _load_routine_engine_module()
        config = deepcopy(module.DEFAULT_INDICATOR_FOLLOW_CONFIG)
        config["buy"]["delay_bar"] = 0
        config["buy"]["groups"] = [
            {"enabled": True, "name": "buy_fail", "conditions": [{"target": "CLOSE", "operator": ">", "value": 99}]}
        ]
        config["sell"] = {
            "delay_bar": 0,
            "signals": {
                "macd_sell": {
                    "enabled": True,
                    "groups": [
                        {
                            "enabled": True,
                            "name": "sell_fail",
                            "conditions": [{"target": "CLOSE", "operator": ">", "value": 99}],
                        }
                    ],
                }
            },
        }
        candles = [
            {"close": 10, "volume": 100},
            {"close": 11, "volume": 100},
            {"close": 12, "volume": 100},
        ]

        signal = module.evaluate_indicator_follow_routine(candles, config, {})

        self.assertIsNone(signal.signal)

    def test_buy_true_and_sell_false_returns_buy(self):
        module = _load_routine_engine_module()
        config = deepcopy(module.DEFAULT_INDICATOR_FOLLOW_CONFIG)
        config["buy"]["delay_bar"] = 0
        config["buy"]["groups"] = [
            {
                "enabled": True,
                "name": "ui_buy_conditions",
                "conditions": [{"target": "CLOSE", "operator": ">=", "value": 12}],
            }
        ]
        config["sell"] = {
            "delay_bar": 0,
            "signals": {
                "macd_sell": {
                    "enabled": True,
                    "groups": [
                        {
                            "enabled": True,
                            "name": "sell_fail",
                            "conditions": [{"target": "CLOSE", "operator": ">", "value": 99}],
                        }
                    ],
                }
            },
        }
        candles = [
            {"close": 10, "volume": 100},
            {"close": 11, "volume": 100},
            {"close": 12, "volume": 100},
        ]

        signal = module.evaluate_indicator_follow_routine(candles, config, {})

        self.assertEqual(signal.signal, "BUY")
        self.assertEqual(signal.matched_groups, ["ui_buy_conditions"])

    def test_buy_false_and_sell_true_returns_sell(self):
        module = _load_routine_engine_module()
        config = deepcopy(module.DEFAULT_INDICATOR_FOLLOW_CONFIG)
        config["buy"]["delay_bar"] = 0
        config["buy"]["groups"] = [
            {"enabled": True, "name": "buy_fail", "conditions": [{"target": "CLOSE", "operator": ">", "value": 99}]}
        ]
        config["sell"] = {
            "delay_bar": 0,
            "signals": {
                "macd_sell": {
                    "enabled": True,
                    "groups": [
                        {
                            "enabled": True,
                            "name": "sell_pass",
                            "conditions": [{"target": "CLOSE", "operator": ">=", "value": 12}],
                        }
                    ],
                }
            },
        }
        candles = [
            {"close": 10, "volume": 100},
            {"close": 11, "volume": 100},
            {"close": 12, "volume": 100},
        ]

        signal = module.evaluate_indicator_follow_routine(candles, config, {})

        self.assertEqual(signal.signal, "SELL")
        self.assertEqual(signal.matched_groups, ["sell_pass"])

    def test_buy_true_and_sell_true_keeps_existing_sell_priority(self):
        module = _load_routine_engine_module()
        config = deepcopy(module.DEFAULT_INDICATOR_FOLLOW_CONFIG)
        config["buy"]["delay_bar"] = 0
        config["buy"]["groups"] = [
            {
                "enabled": True,
                "name": "ui_buy_conditions",
                "conditions": [{"target": "CLOSE", "operator": ">=", "value": 12}],
            }
        ]
        config["sell"] = {
            "delay_bar": 0,
            "signals": {
                "macd_sell": {
                    "enabled": True,
                    "groups": [
                        {
                            "enabled": True,
                            "name": "sell_pass",
                            "conditions": [{"target": "CLOSE", "operator": ">=", "value": 12}],
                        }
                    ],
                }
            },
        }
        candles = [
            {"close": 10, "volume": 100},
            {"close": 11, "volume": 100},
            {"close": 12, "volume": 100},
        ]

        signal = module.evaluate_indicator_follow_routine(candles, config, {})

        self.assertEqual(signal.signal, "SELL")
        self.assertEqual(signal.matched_groups, ["sell_pass"])

    def test_sell_signal_to_dict_preserves_public_shape(self):
        module = _load_routine_engine_module()
        config = deepcopy(module.DEFAULT_INDICATOR_FOLLOW_CONFIG)
        config["buy"]["delay_bar"] = 0
        config["buy"]["groups"] = [
            {"enabled": True, "name": "buy_fail", "conditions": [{"target": "CLOSE", "operator": ">", "value": 99}]}
        ]
        config["sell"] = {
            "delay_bar": 0,
            "signals": {
                "macd_sell": {
                    "enabled": True,
                    "groups": [
                        {
                            "enabled": True,
                            "name": "sell_pass",
                            "conditions": [{"target": "CLOSE", "operator": ">=", "value": 12}],
                        }
                    ],
                }
            },
        }
        candles = [
            {"close": 10, "volume": 100},
            {"close": 11, "volume": 100},
            {"close": 12, "volume": 100},
        ]

        signal = module.evaluate_indicator_follow_routine(candles, config, {})
        payload = module.signal_to_dict(signal)

        self.assertEqual(payload["signal"], "SELL")
        self.assertTrue(payload["reason"])
        self.assertEqual(payload["matched_groups"], ["sell_pass"])
        self.assertTrue(payload["details"])
        self.assertIsInstance(payload["signal_index"], int)
        self.assertEqual(payload["delay_bar"], 0)
        self.assertEqual(
            sorted(payload),
            ["delay_bar", "details", "matched_groups", "reason", "signal", "signal_index"],
        )


class IndicatorFollowBuyRsiFilterTest(unittest.TestCase):
    def setUp(self):
        self.module = _load_routine_engine_module()

    def _candles(self, closes=None):
        return [{"close": close, "volume": 100} for close in (closes or [10, 11, 12])]

    def _config(self, *, delay_bar=0, rsi_filter=None, sell_pass=False):
        config = deepcopy(self.module.DEFAULT_INDICATOR_FOLLOW_CONFIG)
        config["indicators"] = {"rsi": {"period": 2}}
        config["buy"]["delay_bar"] = delay_bar
        config["buy"]["groups"] = [
            {
                "enabled": True,
                "name": "ui_buy_conditions",
                "conditions": [{"enabled": True, "target": "CLOSE", "operator": ">=", "value": 0}],
            }
        ]
        if rsi_filter is not None:
            config["buy"]["filters"] = {"rsi": deepcopy(rsi_filter)}
        config["sell"] = {
            "delay_bar": delay_bar,
            "signals": {
                "macd_sell": {
                    "enabled": bool(sell_pass),
                    "groups": [
                        {
                            "enabled": True,
                            "name": "sell_pass",
                            "conditions": [{"target": "CLOSE", "operator": ">=", "value": 0}],
                        }
                    ],
                }
            },
        }
        return config

    def _signal(self, rsi_filter, *, closes=None, delay_bar=0, sell_pass=False, context=None):
        config = self._config(delay_bar=delay_bar, rsi_filter=rsi_filter, sell_pass=sell_pass)
        return self.module.evaluate_indicator_follow_routine(self._candles(closes), config, context or {})

    def _add_pending_ui_filter(self, config, ui_filter):
        config["indicator_follow_rule_pending"] = {
            "candidates": {
                "indicators": {
                    "rsi": {
                        "path": "indicators.rsi",
                        "value": {"period": ui_filter.get("period", 2)},
                        "ui_filter": deepcopy(ui_filter),
                    }
                }
            }
        }

    def _rsi_detail(self, signal):
        return next((detail for detail in signal.details if "filter_type=RSI" in detail), "")

    def test_buy_rsi_filter_disabled_keeps_buy_result(self):
        signal = self._signal({"enabled": False, "period": 2, "operator": "<=", "threshold": 1})

        self.assertEqual(signal.signal, "BUY")
        self.assertIn("enabled=False", self._rsi_detail(signal))

    def test_buy_rsi_filter_lte_passes_on_equal_threshold(self):
        signal = self._signal({"enabled": True, "period": 2, "operator": "<=", "threshold": 100})

        self.assertEqual(signal.signal, "BUY")
        self.assertIn("evaluated_value=100.0", self._rsi_detail(signal))
        self.assertIn("passed=True", self._rsi_detail(signal))

    def test_buy_rsi_filter_lte_blocks_when_above_threshold(self):
        signal = self._signal({"enabled": True, "period": 2, "operator": "<=", "threshold": 99})

        self.assertIsNone(signal.signal)
        self.assertEqual(signal.reason, "BUY RSI filter blocked")
        self.assertIn("reason=not_matched", self._rsi_detail(signal))

    def test_buy_rsi_filter_ignores_pending_ui_filter_without_official_filter(self):
        config = self._config()
        config["buy"].pop("filters", None)
        self._add_pending_ui_filter(config, {"period": 2, "operator": "<=", "threshold": 99})

        signal = self.module.evaluate_indicator_follow_routine(self._candles(), config, {})

        self.assertEqual(signal.signal, "BUY")
        self.assertEqual("", self._rsi_detail(signal))

    def test_buy_rsi_filter_uses_official_filter_when_pending_differs(self):
        config = self._config(rsi_filter={"enabled": True, "period": 2, "operator": ">=", "threshold": 100})
        self._add_pending_ui_filter(config, {"period": 2, "operator": "<=", "threshold": 99})

        signal = self.module.evaluate_indicator_follow_routine(self._candles(), config, {})

        self.assertEqual(signal.signal, "BUY")
        self.assertIn("operator=>=", self._rsi_detail(signal))
        self.assertIn("threshold=100.0", self._rsi_detail(signal))

    def test_buy_rsi_filter_pending_threshold_changes_do_not_change_execution(self):
        first_config = self._config()
        second_config = self._config()
        first_config["buy"].pop("filters", None)
        second_config["buy"].pop("filters", None)
        self._add_pending_ui_filter(first_config, {"period": 2, "operator": "<=", "threshold": 99})
        self._add_pending_ui_filter(second_config, {"period": 2, "operator": "<=", "threshold": 1})

        first = self.module.signal_to_dict(self.module.evaluate_indicator_follow_routine(self._candles(), first_config, {}))
        second = self.module.signal_to_dict(self.module.evaluate_indicator_follow_routine(self._candles(), second_config, {}))

        self.assertEqual(first, second)
        self.assertEqual(first["signal"], "BUY")

    def test_buy_rsi_filter_bad_pending_operator_does_not_block_without_official_filter(self):
        config = self._config()
        config["buy"].pop("filters", None)
        self._add_pending_ui_filter(config, {"period": 2, "operator": "!=", "threshold": 50})

        signal = self.module.evaluate_indicator_follow_routine(self._candles(), config, {})

        self.assertEqual(signal.signal, "BUY")
        self.assertEqual("", self._rsi_detail(signal))

    def test_buy_rsi_filter_gte_passes_on_equal_threshold(self):
        signal = self._signal({"enabled": True, "period": 2, "operator": ">=", "threshold": 100})

        self.assertEqual(signal.signal, "BUY")
        self.assertIn("passed=True", self._rsi_detail(signal))

    def test_buy_rsi_filter_gte_blocks_when_below_threshold(self):
        signal = self._signal({"enabled": True, "period": 2, "operator": ">=", "threshold": 101})

        self.assertIsNone(signal.signal)
        self.assertIn("reason=not_matched", self._rsi_detail(signal))

    def test_buy_rsi_filter_blocks_when_data_is_insufficient(self):
        signal = self._signal({"enabled": True, "period": 14, "operator": "<=", "threshold": 50})

        self.assertIsNone(signal.signal)
        self.assertIn("reason=insufficient_data", self._rsi_detail(signal))

    def test_buy_rsi_filter_blocks_invalid_period(self):
        signal = self._signal({"enabled": True, "period": 0, "operator": "<=", "threshold": 50})

        self.assertIsNone(signal.signal)
        self.assertIn("reason=invalid_period", self._rsi_detail(signal))

    def test_buy_rsi_filter_blocks_invalid_threshold(self):
        signal = self._signal({"enabled": True, "period": 2, "operator": "<=", "threshold": "bad"})

        self.assertIsNone(signal.signal)
        self.assertIn("reason=invalid_threshold", self._rsi_detail(signal))

    def test_buy_rsi_filter_blocks_unsupported_operator(self):
        signal = self._signal({"enabled": True, "period": 2, "operator": "!=", "threshold": 50})

        self.assertIsNone(signal.signal)
        self.assertIn("reason=unsupported_operator", self._rsi_detail(signal))

    def test_buy_rsi_filter_uses_delay_bar_zero_index(self):
        signal = self._signal(
            {"enabled": True, "period": 2, "operator": "<=", "threshold": 70},
            closes=[10, 12, 14, 13],
            delay_bar=0,
        )

        self.assertEqual(signal.signal, "BUY")
        self.assertEqual(signal.signal_index, 3)
        self.assertIn("evaluated_value=66.666667", self._rsi_detail(signal))
        self.assertIn("evaluation_index=3", self._rsi_detail(signal))

    def test_buy_rsi_filter_uses_delay_bar_one_index(self):
        signal = self._signal(
            {"enabled": True, "period": 2, "operator": ">=", "threshold": 90},
            closes=[10, 12, 14, 13],
            delay_bar=1,
        )

        self.assertEqual(signal.signal, "BUY")
        self.assertEqual(signal.signal_index, 2)
        self.assertIn("evaluated_value=100.0", self._rsi_detail(signal))
        self.assertIn("evaluation_index=2", self._rsi_detail(signal))

    def test_buy_rsi_filter_does_not_affect_sell_result(self):
        signal = self._signal(
            {"enabled": True, "period": 2, "operator": ">=", "threshold": 101},
            sell_pass=True,
        )

        self.assertEqual(signal.signal, "SELL")
        self.assertEqual(signal.matched_groups, ["sell_pass"])
        self.assertEqual("", self._rsi_detail(signal))

    def test_buy_rsi_filter_does_not_mutate_inputs(self):
        config = self._config(rsi_filter={"enabled": True, "period": 2, "operator": "<=", "threshold": 100})
        candles = self._candles()
        context = {"probe": {"value": 1}}
        original_config = deepcopy(config)
        original_candles = deepcopy(candles)
        original_context = deepcopy(context)

        self.module.evaluate_indicator_follow_routine(candles, config, context)

        self.assertEqual(config, original_config)
        self.assertEqual(candles, original_candles)
        self.assertEqual(context, original_context)

    def test_buy_rsi_filter_is_deterministic_for_same_input(self):
        config = self._config(rsi_filter={"enabled": True, "period": 2, "operator": "<=", "threshold": 100})
        candles = self._candles()

        first = self.module.signal_to_dict(self.module.evaluate_indicator_follow_routine(candles, config, {}))
        second = self.module.signal_to_dict(self.module.evaluate_indicator_follow_routine(candles, config, {}))

        self.assertEqual(first, second)


class IndicatorFollowBuyMovingAverageFilterTest(unittest.TestCase):
    def setUp(self):
        self.module = _load_routine_engine_module()

    def _candles(self, closes):
        return [{"close": close, "volume": 100} for close in closes]

    def _config(self, moving_average_filter=None, *, sell_pass=False):
        config = deepcopy(self.module.DEFAULT_INDICATOR_FOLLOW_CONFIG)
        config["moving_averages"] = [60]
        config["buy"]["delay_bar"] = 0
        config["buy"]["groups"] = [
            {
                "enabled": True,
                "name": "ui_buy_conditions",
                "conditions": [{"enabled": True, "target": "CLOSE", "operator": ">=", "value": 0}],
            }
        ]
        if moving_average_filter is not None:
            config["buy"]["filters"] = {"moving_average": deepcopy(moving_average_filter)}
        config["sell"] = {
            "delay_bar": 0,
            "signals": {
                "macd_sell": {
                    "enabled": bool(sell_pass),
                    "groups": [
                        {
                            "enabled": True,
                            "name": "sell_pass",
                            "conditions": [{"target": "CLOSE", "operator": ">=", "value": 0}],
                        }
                    ],
                }
            },
        }
        return config

    def _filter(self, *, enabled=True, period=60, operator="CROSS_UP"):
        return {
            "enabled": enabled,
            "conditions": [{
                "enabled": True,
                "not": False,
                "target": "CLOSE",
                "operator": operator,
                "compare_target": "MA",
                "period": period,
            }],
        }

    def _signal(self, closes, moving_average_filter=None, *, sell_pass=False):
        config = self._config(moving_average_filter, sell_pass=sell_pass)
        return self.module.evaluate_indicator_follow_routine(self._candles(closes), config, {})

    def _ma_detail(self, signal):
        return next((detail for detail in signal.details if "filter_type=MOVING_AVERAGE" in detail), "")

    def test_buy_moving_average_filter_absent_keeps_buy_result(self):
        signal = self._signal([10] * 60 + [9], None)

        self.assertEqual(signal.signal, "BUY")
        self.assertEqual("", self._ma_detail(signal))

    def test_buy_moving_average_filter_cross_up_passes(self):
        signal = self._signal([10] * 60 + [20], self._filter())

        self.assertEqual(signal.signal, "BUY")
        self.assertIn("period=60", self._ma_detail(signal))
        self.assertIn("operator=CROSS_UP", self._ma_detail(signal))
        self.assertIn("passed=True", self._ma_detail(signal))

    def test_buy_moving_average_filter_cross_up_blocks_buy_only(self):
        signal = self._signal([10] * 60 + [9], self._filter())

        self.assertIsNone(signal.signal)
        self.assertEqual(signal.reason, "BUY moving average filter blocked")
        self.assertIn("reason=not_matched", self._ma_detail(signal))

    def test_buy_moving_average_filter_disabled_keeps_buy_result(self):
        signal = self._signal([10] * 60 + [9], self._filter(enabled=False))

        self.assertEqual(signal.signal, "BUY")
        self.assertIn("enabled=False", self._ma_detail(signal))

    def test_buy_moving_average_filter_blocks_insufficient_data(self):
        signal = self._signal([10, 11, 12], self._filter())

        self.assertIsNone(signal.signal)
        self.assertIn("reason=insufficient_data", self._ma_detail(signal))

    def test_buy_moving_average_filter_blocks_invalid_period(self):
        signal = self._signal([10] * 60 + [20], self._filter(period=0))

        self.assertIsNone(signal.signal)
        self.assertIn("reason=invalid_period", self._ma_detail(signal))

    def test_buy_moving_average_filter_does_not_affect_sell_result(self):
        signal = self._signal([10] * 60 + [9], self._filter(), sell_pass=True)

        self.assertEqual(signal.signal, "SELL")
        self.assertEqual("", self._ma_detail(signal))

    def test_buy_moving_average_filter_does_not_mutate_inputs(self):
        config = self._config(self._filter())
        candles = self._candles([10] * 60 + [20])
        original_config = deepcopy(config)
        original_candles = deepcopy(candles)

        self.module.evaluate_indicator_follow_routine(candles, config, {})

        self.assertEqual(config, original_config)
        self.assertEqual(candles, original_candles)


class IndicatorFollowBuyPriceCompareFilterTest(unittest.TestCase):
    def setUp(self):
        self.module = _load_routine_engine_module()

    def _candles(self):
        return [{"close": close, "volume": 100} for close in [9, 10, 12, 13]]

    def _config(self, price_compare_filter=None, *, sell_pass=False, pending_filter=None):
        config = deepcopy(self.module.DEFAULT_INDICATOR_FOLLOW_CONFIG)
        config["buy"]["delay_bar"] = 0
        config["buy"]["groups"] = [
            {
                "enabled": True,
                "name": "ui_buy_conditions",
                "conditions": [{"enabled": True, "target": "CLOSE", "operator": ">=", "value": 0}],
            }
        ]
        if price_compare_filter is not None:
            config["buy"]["filters"] = {"price_compare": deepcopy(price_compare_filter)}
        if pending_filter is not None:
            config["indicator_follow_rule_pending"] = {
                "candidates": {"filters": {"price_compare": deepcopy(pending_filter)}}
            }
        config["sell"] = {
            "delay_bar": 0,
            "signals": {
                "macd_sell": {
                    "enabled": bool(sell_pass),
                    "groups": [
                        {
                            "enabled": True,
                            "name": "sell_pass",
                            "conditions": [{"target": "CLOSE", "operator": ">=", "value": 0}],
                        }
                    ],
                }
            },
        }
        return config

    def _filter(self, *, enabled=True, operator="<=", target="AVG_PRICE", compare_target="ORDER_PRICE"):
        return {
            "enabled": enabled,
            "conditions": [{
                "enabled": True,
                "not": False,
                "target": target,
                "operator": operator,
                "compare_target": compare_target,
            }],
        }

    def _actual_gui_filter(self):
        return {
            "enabled": True,
            "conditions_logic": "OR",
            "conditions": [
                {"enabled": True, "not": False, "target": "AVG_PRICE", "operator": "<", "compare_target": "ORDER_PRICE"},
                {"enabled": True, "not": False, "target": "AVG_PRICE", "operator": ">", "compare_target": "ORDER_PRICE"},
            ],
        }

    def _signal(self, price_compare_filter=None, *, context=None, sell_pass=False, pending_filter=None):
        config = self._config(price_compare_filter, sell_pass=sell_pass, pending_filter=pending_filter)
        return self.module.evaluate_indicator_follow_routine(self._candles(), config, context or {})

    def _price_detail(self, signal):
        return next((detail for detail in signal.details if "filter_type=PRICE_COMPARE" in detail), "")

    def test_buy_price_compare_filter_absent_keeps_buy_result(self):
        signal = self._signal(None, context={"order_price": 10, "average_price": 10})

        self.assertEqual(signal.signal, "BUY")
        self.assertEqual("", self._price_detail(signal))

    def test_buy_price_compare_filter_passes_after_buy_main_signal(self):
        signal = self._signal(self._filter(), context={"order_price": 10, "average_price": 9})

        self.assertEqual(signal.signal, "BUY")
        self.assertIn("filter_type=PRICE_COMPARE", self._price_detail(signal))
        self.assertIn("passed=True", self._price_detail(signal))

    def test_buy_price_compare_filter_blocks_buy_only(self):
        signal = self._signal(self._filter(), context={"order_price": 10, "average_price": 11})

        self.assertIsNone(signal.signal)
        self.assertEqual(signal.reason, "BUY price compare filter blocked")
        self.assertIn("reason=not_matched", self._price_detail(signal))

    def test_buy_price_compare_filter_disabled_keeps_buy_result(self):
        signal = self._signal(self._filter(enabled=False), context={"order_price": 10, "average_price": 11})

        self.assertEqual(signal.signal, "BUY")
        self.assertIn("enabled=False", self._price_detail(signal))

    def test_buy_price_compare_filter_blocks_missing_context_data(self):
        signal = self._signal(self._filter(), context={"average_price": 9})

        self.assertIsNone(signal.signal)
        self.assertEqual(signal.reason, "BUY price compare filter blocked")
        self.assertIn("reason=insufficient_data", self._price_detail(signal))

    def test_buy_price_compare_filter_blocks_unsupported_target(self):
        signal = self._signal(self._filter(target="UNKNOWN"), context={"order_price": 10, "average_price": 9})

        self.assertIsNone(signal.signal)
        self.assertIn("reason=unsupported_target", self._price_detail(signal))

    def test_actual_gui_price_compare_filter_blocks_equality_gap(self):
        signal = self._signal(self._actual_gui_filter(), context={"order_price": 10, "average_price": 10})

        self.assertIsNone(signal.signal)
        self.assertEqual(signal.reason, "BUY price compare filter blocked")

    def test_buy_price_compare_pending_candidate_does_not_affect_execution(self):
        pending = {"path": "buy.filters.price_compare", "value": self._filter()}
        signal = self._signal(None, context={"order_price": 10, "average_price": 11}, pending_filter=pending)

        self.assertEqual(signal.signal, "BUY")
        self.assertEqual("", self._price_detail(signal))

    def test_buy_price_compare_filter_does_not_affect_sell_result(self):
        signal = self._signal(self._filter(), context={"order_price": 10, "average_price": 11}, sell_pass=True)

        self.assertEqual(signal.signal, "SELL")
        self.assertEqual("", self._price_detail(signal))

    def test_buy_price_compare_filter_does_not_mutate_inputs_and_is_deterministic(self):
        config = self._config(self._filter())
        candles = self._candles()
        context = {"order_price": 10, "average_price": 9}
        original_config = deepcopy(config)
        original_candles = deepcopy(candles)
        original_context = deepcopy(context)

        first = self.module.evaluate_indicator_follow_routine(candles, config, context)
        second = self.module.evaluate_indicator_follow_routine(candles, config, context)

        self.assertEqual(first, second)
        self.assertEqual(config, original_config)
        self.assertEqual(candles, original_candles)
        self.assertEqual(context, original_context)


if __name__ == "__main__":
    unittest.main()
