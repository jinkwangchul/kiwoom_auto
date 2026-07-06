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


if __name__ == "__main__":
    unittest.main()
