from __future__ import annotations

from copy import deepcopy
import unittest

from indicator_follow_strategy_price_contract import (
    canonicalize_buy_strategy_price_compare_filter,
    canonicalize_buy_strategy_price_comparison_rules,
)
from routines.지표추종매매 import routine_macd_engine


class IndicatorFollowStrategyPriceContractTest(unittest.TestCase):
    def test_helper_normalizes_only_approved_buy_filter_fields(self):
        rules = {
            "buy": {
                "filters": {
                    "price_compare": {
                        "conditions": [
                            {"target": "ORDER_PRICE", "compare_target": "CLOSE"},
                        ],
                    },
                },
                "execution": {"price_basis": "ORDER_PRICE"},
            },
            "sell": {
                "method": {"price_basis": "ORDER_PRICE"},
                "signals": {
                    "ui_condition_c": {
                        "groups": [{
                            "conditions": [{
                                "target": "ORDER_PRICE",
                                "compare_target": "CLOSE",
                            }],
                        }],
                    },
                },
            },
            "order_policy": {"price_basis": "ORDER_PRICE"},
            "cancel_policy": {"compare_target": "ORDER_PRICE"},
        }
        original = deepcopy(rules)

        normalized = canonicalize_buy_strategy_price_comparison_rules(rules)

        condition = normalized["buy"]["filters"]["price_compare"]["conditions"][0]
        self.assertEqual("SIGNAL_PRICE", condition["target"])
        self.assertEqual("CURRENT_PRICE", condition["compare_target"])
        self.assertEqual(original["buy"]["execution"], normalized["buy"]["execution"])
        self.assertEqual(original["sell"], normalized["sell"])
        self.assertEqual(original["order_policy"], normalized["order_policy"])
        self.assertEqual(original["cancel_policy"], normalized["cancel_policy"])
        self.assertEqual(original, rules)

        filter_source = rules["buy"]["filters"]["price_compare"]
        filter_copy = canonicalize_buy_strategy_price_compare_filter(filter_source)
        self.assertIsNot(filter_source, filter_copy)
        self.assertEqual("SIGNAL_PRICE", filter_copy["conditions"][0]["target"])

    def test_sell_legacy_order_price_fails_closed_with_order_context(self):
        candles = [{"close": value, "volume": 100} for value in (10, 11, 12)]
        for legacy_field in ("target", "compare_target"):
            with self.subTest(legacy_field=legacy_field):
                legacy_condition = {
                    "enabled": True,
                    "not": False,
                    "target": "CLOSE",
                    "operator": ">=",
                    "compare_target": "CLOSE",
                }
                legacy_condition[legacy_field] = "ORDER_PRICE"
                config = {
                    "buy": {"delay_bar": 0, "groups": []},
                    "sell": {
                        "delay_bar": 0,
                        "signals": {
                            "legacy_sell": {
                                "enabled": True,
                                "groups": [{
                                    "enabled": True,
                                    "name": "legacy_order_price",
                                    "conditions": [legacy_condition],
                                }],
                            },
                        },
                    },
                }

                result = routine_macd_engine.evaluate_indicator_follow_routine(
                    candles,
                    config,
                    {
                        "order_price": 12,
                        "current_price": 12,
                        "_indicator_follow_evaluate_side": "SELL",
                    },
                )

                self.assertIsNone(result.signal)
                normalized = routine_macd_engine._normalize_sell_runtime_groups(
                    config["sell"]["signals"]["legacy_sell"]["groups"]
                )
                condition = normalized[0]["conditions"][0]
                self.assertEqual("LEGACY_SELL_ORDER_PRICE_UNRESOLVED", condition["target"])
                self.assertEqual("RESELECTION_REQUIRED", condition["operator"])
                issue = condition["_strategy_price_contract_issue"]
                self.assertEqual(
                    "LEGACY_SELL_ORDER_PRICE_RESELECTION_REQUIRED",
                    issue["reason"],
                )
                self.assertEqual([legacy_field], issue["fields"])


if __name__ == "__main__":
    unittest.main()
