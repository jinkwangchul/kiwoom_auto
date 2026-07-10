from copy import deepcopy
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import unittest

from engines.condition_engine import evaluate_condition, evaluate_group, evaluate_groups_or
from engines.indicator_engine import build_indicator_series


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
            "MA60": [10.0, 10.0, 11.0],
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


class IndicatorFollowBuyMacdPositionFilterTest(unittest.TestCase):
    def setUp(self):
        self.module = _load_routine_engine_module()

    def _candles(self, closes=None):
        return [{"close": close, "volume": 100} for close in (closes or [10, 11, 12, 13])]

    def _series_map(self, closes=None, macd_cfg=None):
        config = {"macd": macd_cfg or {"fast": 2, "slow": 3, "signal": 2}}
        return build_indicator_series(self._candles(closes), config)

    def _config(self, *, delay_bar=0, macd_position_filter=None, sell_pass=False):
        config = deepcopy(self.module.DEFAULT_INDICATOR_FOLLOW_CONFIG)
        config["macd"] = {"fast": 2, "slow": 3, "signal": 2}
        config["buy"]["delay_bar"] = delay_bar
        config["buy"]["groups"] = [
            {
                "enabled": True,
                "name": "ui_buy_conditions",
                "conditions": [{"enabled": True, "target": "CLOSE", "operator": ">=", "value": 0}],
            }
        ]
        if macd_position_filter is not None:
            config["buy"]["filters"] = {"macd_position": deepcopy(macd_position_filter)}
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

    def _signal(self, macd_position_filter, *, closes=None, delay_bar=0, sell_pass=False):
        config = self._config(delay_bar=delay_bar, macd_position_filter=macd_position_filter, sell_pass=sell_pass)
        return self.module.evaluate_indicator_follow_routine(self._candles(closes), config, {})

    def _add_pending_ui_filter(self, config, ui_filter):
        config["indicator_follow_rule_pending"] = {
            "candidates": {
                "filters": {
                    "macd_position": {
                        "path": "buy.filters.macd_position",
                        "value": {
                            "enabled": True,
                            "direction": ui_filter.get("direction", "상향"),
                            "compare": ui_filter.get("compare", "이상"),
                            "value": ui_filter.get("value", 0),
                        },
                        "ui_filter": deepcopy(ui_filter),
                    }
                }
            }
        }

    def _macd_position_detail(self, signal):
        return next((detail for detail in signal.details if "filter_type=MACD_POSITION" in detail), "")

    def _expected_gap(self, closes=None, index=None):
        series_map = self._series_map(closes)
        idx = self._signal_index() if index is None else index
        macd_line = series_map["MACD"]
        signal_line = series_map["SIGNAL"]
        return macd_line[idx] - signal_line[idx]

    def _signal_index(self, delay_bar=0):
        closes = self._candles()
        return len(closes) - 1 - max(int(delay_bar or 0), 0)

    def test_buy_macd_position_filter_disabled_keeps_buy_result(self):
        signal = self._signal({"enabled": False, "direction": "상향", "compare": "이상", "value": 0})

        self.assertEqual(signal.signal, "BUY")
        self.assertIn("enabled=False", self._macd_position_detail(signal))

    def test_buy_macd_position_filter_up_gte_passes_on_equal_gap(self):
        signal = self._signal({"enabled": True, "direction": "상향", "compare": "이상", "value": 0})

        self.assertEqual(signal.signal, "BUY")
        self.assertIn("passed=True", self._macd_position_detail(signal))

    def test_buy_macd_position_filter_up_lte_blocks_when_above_value(self):
        signal = self._signal({"enabled": True, "direction": "상향", "compare": "이하", "value": -1000})

        self.assertIsNone(signal.signal)
        self.assertEqual(signal.reason, "BUY MACD position filter blocked")
        self.assertIn("reason=not_matched", self._macd_position_detail(signal))

    def test_buy_macd_position_filter_down_gte_passes_at_zero_gap(self):
        signal = self._signal({"enabled": True, "direction": "하향", "compare": "이상", "value": 0}, closes=[10, 10, 10, 10])

        self.assertEqual(signal.signal, "BUY")
        self.assertIn("direction=down", self._macd_position_detail(signal))

    def test_buy_macd_position_filter_down_lte_blocks_when_above_by_more_than_value(self):
        signal = self._signal({"enabled": True, "direction": "하향", "compare": "이하", "value": -1000}, closes=[8, 9, 10, 11])

        self.assertIsNone(signal.signal)
        self.assertIn("reason=not_matched", self._macd_position_detail(signal))

    def test_buy_macd_position_filter_both_within_passes_at_zero_gap(self):
        signal = self._signal({"enabled": True, "direction": "상하", "compare": "이내", "value": 0}, closes=[10, 10, 10, 10])

        self.assertEqual(signal.signal, "BUY")
        self.assertIn("direction=both", self._macd_position_detail(signal))
        self.assertIn("compare=within", self._macd_position_detail(signal))

    def test_buy_macd_position_filter_both_breakout_blocks_at_zero_gap(self):
        signal = self._signal({"enabled": True, "direction": "상하", "compare": "이탈", "value": 0}, closes=[10, 10, 10, 10])

        self.assertIsNone(signal.signal)
        self.assertIn("compare=breakout", self._macd_position_detail(signal))
        self.assertIn("reason=not_matched", self._macd_position_detail(signal))

    def test_buy_macd_position_filter_both_within_blocks_when_gap_exceeds_value(self):
        signal = self._signal({"enabled": True, "direction": "상하", "compare": "이내", "value": 0})

        self.assertIsNone(signal.signal)
        self.assertIn("reason=not_matched", self._macd_position_detail(signal))

    def test_buy_macd_position_filter_up_lte_passes_when_below_value(self):
        signal = self._signal({"enabled": True, "direction": "상향", "compare": "이하", "value": 1000})

        self.assertEqual(signal.signal, "BUY")
        self.assertIn("passed=True", self._macd_position_detail(signal))

    def test_buy_macd_position_filter_evaluated_gap_matches_indicator_engine(self):
        closes = [8, 9, 10, 11]
        signal = self._signal({"enabled": True, "direction": "상향", "compare": "이상", "value": 0}, closes=closes)

        expected_gap = self._expected_gap(closes)
        detail = self._macd_position_detail(signal)
        self.assertIn(f"evaluated_gap={round(expected_gap, 8)}", detail)

    def test_buy_macd_position_filter_uses_delay_bar_index(self):
        signal = self._signal(
            {"enabled": True, "direction": "상향", "compare": "이상", "value": 0},
            closes=[8, 9, 10, 11],
            delay_bar=1,
        )

        self.assertEqual(signal.signal_index, 2)
        self.assertIn("evaluation_index=2", self._macd_position_detail(signal))

    def test_buy_macd_position_filter_blocks_invalid_target(self):
        signal = self._signal({"enabled": True, "target": "RSI", "compare_target": "SIGNAL", "direction": "상향", "compare": "이상", "value": 0})

        self.assertIsNone(signal.signal)
        self.assertIn("reason=invalid_target", self._macd_position_detail(signal))

    def test_buy_macd_position_filter_blocks_invalid_compare_target(self):
        signal = self._signal({"enabled": True, "target": "MACD", "compare_target": "RSI", "direction": "상향", "compare": "이상", "value": 0})

        self.assertIsNone(signal.signal)
        self.assertIn("reason=invalid_compare_target", self._macd_position_detail(signal))

    def test_buy_macd_position_filter_blocks_identical_targets(self):
        signal = self._signal({"enabled": True, "target": "MACD", "compare_target": "MACD", "direction": "상향", "compare": "이상", "value": 0})

        self.assertIsNone(signal.signal)
        self.assertIn("reason=identical_targets", self._macd_position_detail(signal))

    def test_buy_macd_position_filter_blocks_unsupported_direction(self):
        signal = self._signal({"enabled": True, "direction": "측면", "compare": "이상", "value": 0})

        self.assertIsNone(signal.signal)
        self.assertIn("reason=unsupported_direction", self._macd_position_detail(signal))

    def test_buy_macd_position_filter_blocks_unsupported_compare(self):
        signal = self._signal({"enabled": True, "direction": "상향", "compare": "==", "value": 0})

        self.assertIsNone(signal.signal)
        self.assertIn("reason=unsupported_compare", self._macd_position_detail(signal))

    def test_buy_macd_position_filter_blocks_invalid_value(self):
        signal = self._signal({"enabled": True, "direction": "상향", "compare": "이상", "value": "bad"})

        self.assertIsNone(signal.signal)
        self.assertIn("reason=invalid_value", self._macd_position_detail(signal))

    def test_buy_macd_position_filter_blocks_when_index_out_of_range(self):
        config = self._config(macd_position_filter={"enabled": True, "direction": "상향", "compare": "이상", "value": 0})
        series_map = self._series_map()
        passed, detail = self.module._evaluate_buy_macd_position_filter(config, config["buy"], series_map, len(series_map["MACD"]) + 5)

        self.assertFalse(passed)
        self.assertIn("reason=insufficient_data", detail)

    def test_buy_macd_position_filter_uses_official_config_when_pending_ui_differs(self):
        config = self._config(macd_position_filter={"enabled": True, "direction": "상향", "compare": "이상", "value": 0})
        self._add_pending_ui_filter(config, {"direction": "하향", "compare": "이탈", "value": 0})

        signal = self.module.evaluate_indicator_follow_routine(self._candles(), config, {})

        self.assertEqual(signal.signal, "BUY")
        self.assertIn("direction=up", self._macd_position_detail(signal))
        self.assertIn("value=0", self._macd_position_detail(signal))

    def test_buy_macd_position_filter_ignores_pending_ui_without_official_filter(self):
        config = self._config()
        config["buy"].pop("filters", None)
        self._add_pending_ui_filter(config, {"direction": "하향", "compare": "이탈", "value": 0})

        signal = self.module.evaluate_indicator_follow_routine(self._candles(), config, {})

        self.assertEqual(signal.signal, "BUY")
        self.assertEqual("", self._macd_position_detail(signal))

    def test_buy_macd_position_filter_pending_value_changes_do_not_change_execution(self):
        first_config = self._config()
        second_config = self._config()
        first_config["buy"].pop("filters", None)
        second_config["buy"].pop("filters", None)
        self._add_pending_ui_filter(first_config, {"direction": "상향", "compare": "이상", "value": 0})
        self._add_pending_ui_filter(second_config, {"direction": "하향", "compare": "이탈", "value": 1000})

        first = self.module.signal_to_dict(self.module.evaluate_indicator_follow_routine(self._candles(), first_config, {}))
        second = self.module.signal_to_dict(self.module.evaluate_indicator_follow_routine(self._candles(), second_config, {}))

        self.assertEqual(first, second)
        self.assertEqual(first["signal"], "BUY")

    def test_buy_macd_position_filter_does_not_affect_sell_result(self):
        signal = self._signal(
            {"enabled": True, "direction": "하향", "compare": "이하", "value": 1000},
            sell_pass=True,
        )

        self.assertEqual(signal.signal, "SELL")
        self.assertEqual(signal.matched_groups, ["sell_pass"])
        self.assertEqual("", self._macd_position_detail(signal))

    def test_buy_macd_position_filter_does_not_mutate_inputs(self):
        config = self._config(macd_position_filter={"enabled": True, "direction": "상향", "compare": "이상", "value": 0})
        candles = self._candles()
        context = {"probe": {"value": 1}}
        original_config = deepcopy(config)
        original_candles = deepcopy(candles)
        original_context = deepcopy(context)

        self.module.evaluate_indicator_follow_routine(candles, config, context)

        self.assertEqual(config, original_config)
        self.assertEqual(candles, original_candles)
        self.assertEqual(context, original_context)

    def test_buy_macd_position_filter_is_deterministic_for_same_input(self):
        config = self._config(macd_position_filter={"enabled": True, "direction": "상향", "compare": "이상", "value": 0})
        candles = self._candles()

        first = self.module.signal_to_dict(self.module.evaluate_indicator_follow_routine(candles, config, {}))
        second = self.module.signal_to_dict(self.module.evaluate_indicator_follow_routine(candles, config, {}))

        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
