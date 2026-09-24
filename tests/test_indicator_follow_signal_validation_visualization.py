# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import unittest

from engines.condition_engine import evaluate_condition
from engines.indicator_engine import bollinger_band, close_prices
from indicator_follow_signal_validation_presentation import (
    signal_evidence_records_for_entry,
)
from indicator_follow_signal_validation_visualization import (
    FAMILY_BOLLINGER,
    FAMILY_MACD_SIGNAL,
    FAMILY_MA_ARRANGEMENT,
    FAMILY_MOVING_AVERAGE,
    FAMILY_OCR_OSC,
    FAMILY_PRICE_BOX,
    FAMILY_PRICE_COMPARISON,
    FAMILY_RSI,
    LOWER_AXIS,
    PRICE_AXIS,
    active_filter_identities_for_entry,
    build_validation_filter_universe,
    build_validation_indicator_cache,
    required_validation_warmup_bars,
)
from routines.지표추종매매.routine_validation_replay import ValidationReplayEntry


def _identifier(name):
    return {"type": "identifier", "name": name}


def _binary(operator, left, right):
    return {
        "type": "binary",
        "operator": operator,
        "left": left,
        "right": right,
    }


def _rules():
    sell_expression = {
        "ast": _identifier("B"),
        "identifiers": ["B"],
        "identifier_map": {"B": "ui_condition_b"},
    }
    buy_filters = {
        "rsi": {
            "enabled": True,
            "conditions": [{
                "enabled": True,
                "target": "RSI",
                "operator": "<=",
                "value": 45,
            }],
        },
        "moving_average": {
            "enabled": True,
            "conditions": [
                {
                    "enabled": True,
                    "target": "CLOSE",
                    "operator": "CROSS_UP",
                    "compare_target": "MA20",
                },
                {
                    "enabled": True,
                    "target": "CLOSE",
                    "operator": "CROSS_UP",
                    "compare_target": "MA60",
                },
            ],
        },
        "bollinger": {
            "enabled": True,
            "conditions": [{
                "enabled": True,
                "target": "CLOSE",
                "operator": ">=",
                "compare_target": "BOLLINGER_LOWER",
            }],
        },
        "ocr": {
            "enabled": True,
            "conditions": [{
                "enabled": True,
                "target": "OSC",
                "operator": "TURN_UP",
            }],
        },
    }
    buy_price_compare = {
        "enabled": True,
        "conditions": [{
            "enabled": True,
            "target": "AVG_PRICE",
            "operator": "PERCENT_GAP",
            "compare_target": "SIGNAL_PRICE",
            "direction": "UP",
            "compare_mode": "GTE",
            "value": 0.5,
        }],
    }
    sell_conditions = [
        {
            "enabled": True,
            "expression_id": "MACD_0",
            "target": "MACD",
            "operator": "<=",
            "value": 0,
        },
        {
            "enabled": True,
            "expression_id": "RSI_0",
            "target": "RSI",
            "operator": "<=",
            "value": 45,
        },
        {
            "enabled": True,
            "expression_id": "OCR_0",
            "target": "OSC",
            "operator": "TURN_DOWN",
        },
        {
            "enabled": True,
            "expression_id": "GAP_0",
            "target": "CLOSE",
            "operator": "PERCENT_GAP",
            "compare_target": "AVG_PRICE",
            "direction": "UP",
            "compare_mode": "GTE",
            "value": 0.25,
        },
        {
            "enabled": True,
            "expression_id": "PRICE_BOX_0",
            "target": "CLOSE",
            "operator": ">=",
            "compare_target": "PRICE_BOX_LOWER",
        },
        {
            "enabled": True,
            "expression_id": "BOLLINGER_0",
            "target": "CLOSE",
            "operator": ">=",
            "compare_target": "BOLLINGER_LOWER",
        },
        {
            "enabled": True,
            "expression_id": "ARRAY_0",
            "target": "MA",
            "period": 5,
            "operator": ">",
            "compare_target": "MA",
            "compare_period": 20,
        },
        {
            "enabled": True,
            "expression_id": "ARRAY_1",
            "target": "MA",
            "period": 20,
            "operator": ">",
            "compare_target": "MA",
            "compare_period": 60,
        },
    ]
    sell_signal = {
        "enabled": True,
        "signal_expression": sell_expression,
        "groups": [{
            "enabled": True,
            "condition_expression": _binary(
                "OR",
                _identifier("MACD_0"),
                _identifier("RSI_0"),
            ),
            "conditions": sell_conditions,
        }],
    }
    return {
        "indicators": {
            "macd": {"fast": 12, "slow": 26, "signal": 9},
            "rsi": {"period": 14},
            "moving_averages": [5, 20, 60],
            "bollinger": {"period": 20, "std": 2.0},
            "price_box": {"period": 24},
        },
        "validation_execution": {
            "repeat_mode": "BUDGET",
            "budget_ratio": 2.0,
            "round_operator": "ADD",
            "round_budget_value": 0.5,
            "active_direction": "UP",
            "active_ratio": 0.45,
            "active_compare": ">=",
        },
        "buy": {
            "filters": deepcopy(buy_filters),
        },
        "sell": {
            "signals": {"ui_condition_b": deepcopy(sell_signal)},
        },
        "validation_visualization_rules": {
            "buy": {
                "filters": {
                    **deepcopy(buy_filters),
                    "price_compare": deepcopy(buy_price_compare),
                },
                "groups": [],
            },
            "sell": {
                "filters": {},
                "signals": {"ui_condition_b": deepcopy(sell_signal)},
            },
        },
    }


def _candles(count, *, future_spike_from=None):
    result = []
    for index in range(count):
        price = 100.0 + ((index % 11) - 5) * 1.25
        if future_spike_from is not None and index >= future_spike_from:
            price = 10000.0 + index * 100.0
        result.append({
            "time": f"202609{1 + index // 300:02d}{9 + (index % 300) // 60:02d}{index % 60:02d}00",
            "open": price - 0.5,
            "high": price + 1.0,
            "low": price - 1.0,
            "close": price,
            "volume": 100 + index,
        })
    return result


def _signal_entry():
    rules = _rules()
    conditions = rules["sell"]["signals"]["ui_condition_b"]["groups"][0]["conditions"]
    group_path = "sell.signals.ui_condition_b.groups[0]"
    payloads = []
    for index, condition in enumerate(conditions[:2]):
        result = index == 0
        payloads.append({
            "path": f"{group_path}.conditions[{index}]",
            "expression_id": condition["expression_id"],
            "condition_type": condition["target"],
            "operator": condition["operator"],
            "left_operand": {
                "key": condition["target"],
                "index": 1,
                "value": -1.0 if index == 0 else 55.0,
            },
            "right_operand": {
                "key": "value",
                "index": None,
                "value": condition["value"],
            },
            "raw_result": result,
            "final_result": result,
            "indicator_snapshots": [],
        })
    trace = {
        "conditions": payloads,
        "groups": [{
            "path": group_path,
            "condition_paths": [payload["path"] for payload in payloads],
            "condition_expression": _binary(
                "OR",
                _identifier("MACD_0"),
                _identifier("RSI_0"),
            ),
            "expression_values": {"MACD_0": True, "RSI_0": False},
            "result": True,
        }],
        "aggregations": [{
            "side": "SELL",
            "payload": {
                "matched_group_paths": [group_path],
                "ui_signal_expression": {
                    "ast": _identifier("B"),
                    "identifiers": ["B"],
                    "identifier_map": {"B": "ui_condition_b"},
                },
                "ui_expression_values": {"B": True},
                "ui_expression_result": True,
                "result": True,
            },
        }],
    }
    return ValidationReplayEntry(
        evaluation_side="SELL",
        evaluation_index=1,
        evaluation_time="20260901090100",
        signal="SELL",
        reason="fixture",
        signal_index=1,
        signal_time="20260901090100",
        delay_bar=0,
        matched_groups=["B"],
        details=[],
        trace=trace,
    )


class _ConditionObserver:
    def __init__(self):
        self.payloads = []

    def observe_condition(self, payload):
        self.payloads.append(deepcopy(payload))


class ValidationVisualizationDataTest(unittest.TestCase):
    def test_required_warmup_uses_largest_configured_filter_period(self):
        rules = _rules()
        condition = {
            "enabled": True,
            "target": "CLOSE",
            "operator": "CROSS_UP",
            "compare_target": "MA200",
        }
        rules["buy"]["filters"]["moving_average"]["conditions"].append(
            deepcopy(condition)
        )
        rules["validation_visualization_rules"]["buy"]["filters"][
            "moving_average"
        ]["conditions"].append(deepcopy(condition))

        self.assertEqual(200, required_validation_warmup_bars(rules))

    def test_operator_visible_labels_do_not_expose_engine_tokens(self):
        labels = {
            str(descriptor.parameters.get("criterion_label") or "")
            for descriptor in build_validation_filter_universe(_rules())
        }
        self.assertIn("60이평 상향돌파", labels)
        self.assertIn("OCR 상승전환", labels)
        self.assertIn("OCR 하락전환", labels)
        self.assertIn("MACD 0 이하", labels)
        self.assertIn("RSI(14) 45 이하", labels)
        self.assertIn("평단 대비 +0.25% 이상", labels)
        self.assertIn("가격박스 하단 이상", labels)
        self.assertIn("볼린저 하단 이상", labels)
        self.assertIn("평단 신호가 대비 +0.5% 이상", labels)
        joined = "\n".join(labels)
        for token in (
            "CLOSE",
            "CROSS_UP",
            "CROSS_DOWN",
            "TURN_UP",
            "TURN_DOWN",
            "AVG_PRICE",
            "PRICE_BOX",
            "BOLLINGER_",
            "OSC ",
            "SIGNAL",
        ):
            self.assertNotIn(token, joined)

    def test_macd_caption_preserves_reversed_operand_direction(self):
        rules = _rules()
        actual = rules["sell"]["signals"]["ui_condition_b"]["groups"][0]["conditions"][0]
        visual = rules["validation_visualization_rules"]["sell"]["signals"]["ui_condition_b"]["groups"][0]["conditions"][0]
        for condition in (actual, visual):
            condition.update({
                "target": "SIGNAL",
                "compare_target": "MACD",
                "operator": "CROSS_UP",
            })
            condition.pop("value", None)

        descriptor = next(
            item
            for item in build_validation_filter_universe(rules)
            if item.family == FAMILY_MACD_SIGNAL
        )

        self.assertEqual(
            "시그널 MACD 상향돌파",
            descriptor.parameters["criterion_label"],
        )

    def test_macd_visualization_always_projects_both_macd_and_signal_lines(self):
        for target in ("MACD", "SIGNAL"):
            with self.subTest(target=target):
                rules = _rules()
                actual = rules["sell"]["signals"]["ui_condition_b"]["groups"][0]["conditions"][0]
                visual = rules["validation_visualization_rules"]["sell"]["signals"]["ui_condition_b"]["groups"][0]["conditions"][0]
                for condition in (actual, visual):
                    condition.update({
                        "target": target,
                        "operator": "<=",
                        "value": 0,
                    })
                    condition.pop("compare_target", None)

                descriptor = next(
                    item
                    for item in build_validation_filter_universe(rules)
                    if item.family == FAMILY_MACD_SIGNAL
                )
                self.assertEqual(
                    ("MACD", "SIGNAL", "CRITERION"),
                    descriptor.series_keys,
                )

                cache = build_validation_indicator_cache(
                    _candles(70),
                    rules,
                    (descriptor,),
                )
                self.assertEqual(
                    ("MACD", "SIGNAL", "CRITERION"),
                    cache.channels_for(descriptor.identity),
                )

    def test_universe_contains_all_eight_families_and_separate_ma_parameters(self):
        descriptors = build_validation_filter_universe(_rules())
        families = {descriptor.family for descriptor in descriptors}
        self.assertEqual({
            FAMILY_MOVING_AVERAGE,
            FAMILY_MA_ARRANGEMENT,
            FAMILY_BOLLINGER,
            FAMILY_PRICE_BOX,
            FAMILY_PRICE_COMPARISON,
            FAMILY_RSI,
            FAMILY_MACD_SIGNAL,
            FAMILY_OCR_OSC,
        }, families)

        moving = [
            descriptor
            for descriptor in descriptors
            if descriptor.family == FAMILY_MOVING_AVERAGE
        ]
        self.assertEqual(
            {(20,), (60,)},
            {tuple(item.parameters["periods"]) for item in moving},
        )
        arrangement = next(
            item for item in descriptors
            if item.family == FAMILY_MA_ARRANGEMENT
        )
        self.assertEqual((5, 20, 60), tuple(arrangement.parameters["periods"]))
        self.assertEqual(PRICE_AXIS, arrangement.axis)

        lower = {
            item.family for item in descriptors
            if item.axis == LOWER_AXIS
        }
        self.assertEqual(
            {FAMILY_RSI, FAMILY_MACD_SIGNAL, FAMILY_OCR_OSC},
            lower,
        )

    def test_buy_ma_compare_target_uses_engine_period_fallback(self):
        rules = _rules()
        condition = {
            "enabled": True,
            "target": "CLOSE",
            "operator": "CROSS_UP",
            "compare_target": "MA",
            "period": 20,
        }
        rules["validation_visualization_rules"]["buy"]["filters"]["moving_average"]["conditions"] = [
            deepcopy(condition)
        ]
        rules["buy"]["filters"]["moving_average"]["conditions"] = [
            deepcopy(condition)
        ]
        descriptor = next(
            item
            for item in build_validation_filter_universe(rules)
            if item.family == FAMILY_MOVING_AVERAGE and "BUY" in item.sides
        )
        self.assertEqual(("MA20",), descriptor.series_keys)
        candles = _candles(40)
        cache = build_validation_indicator_cache(
            candles,
            rules,
            (descriptor,),
        )
        self.assertEqual(
            40,
            len(cache.values_for(descriptor.identity, "MA20")),
        )

    def test_bollinger_projects_only_the_effective_selected_criterion_line(self):
        rules = _rules()
        condition = rules["validation_visualization_rules"]["buy"]["filters"]["bollinger"]["conditions"][0]
        condition.update({
            "compare_target": "BOLLINGER_LOWER",
            "operator": "<=",
            "value": 0.5,
            "signed_percent_offset": True,
        })
        rules["buy"]["filters"]["bollinger"]["conditions"][0] = deepcopy(condition)
        descriptors = build_validation_filter_universe(rules)
        descriptor = next(
            item for item in descriptors
            if item.family == FAMILY_BOLLINGER and "BUY" in item.sides
        )
        self.assertEqual(("CRITERION",), descriptor.series_keys)
        self.assertNotIn("BOLLINGER_MIDDLE", descriptor.series_keys)
        self.assertEqual("BOLLINGER_LOWER", descriptor.parameters["condition"]["compare_target"])
        self.assertEqual(0.5, descriptor.parameters["condition"]["value"])
        self.assertEqual(
            "볼린저 하단 +0.5% 이하",
            descriptor.parameters["criterion_label"],
        )

        candles = _candles(40)
        cache = build_validation_indicator_cache(candles, rules, descriptors)
        criterion = cache.values_for(descriptor.identity, "CRITERION")
        self.assertEqual(40, len(criterion))

        lower, _middle, _upper = bollinger_band(close_prices(candles), 20, 2.0)
        for index, base in enumerate(lower):
            if base is None:
                self.assertIsNone(criterion[index])
            else:
                self.assertAlmostEqual(base * 1.005, criterion[index])

    def test_bollinger_reference_line_matches_engine_right_operand(self):
        rules = _rules()
        condition = {
            "enabled": True,
            "target": "CLOSE",
            "operator": "<=",
            "compare_target": "BOLLINGER_LOWER",
            "value": 2.0,
        }
        rules["validation_visualization_rules"]["buy"]["filters"]["bollinger"]["conditions"] = [
            deepcopy(condition)
        ]
        rules["buy"]["filters"]["bollinger"]["conditions"] = [
            deepcopy(condition)
        ]
        descriptors = build_validation_filter_universe(rules)
        descriptor = next(
            item for item in descriptors
            if item.family == FAMILY_BOLLINGER and "BUY" in item.sides
        )
        candles = _candles(40)
        cache = build_validation_indicator_cache(
            candles,
            rules,
            descriptors,
        )
        criterion = cache.values_for(descriptor.identity, "CRITERION")

        closes = close_prices(candles)
        lower, _middle, _upper = bollinger_band(closes, 20, 2.0)
        index = 30
        observer = _ConditionObserver()
        evaluate_condition(
            condition,
            {
                "CLOSE": closes,
                "BOLLINGER_LOWER": lower,
            },
            index,
            observer,
            "buy.filters.bollinger.conditions[0]",
        )
        self.assertEqual(1, len(observer.payloads))
        engine_right = observer.payloads[0]["right_operand"]["value"]
        self.assertIsNotNone(engine_right)
        self.assertAlmostEqual(engine_right, criterion[index])
        self.assertAlmostEqual(lower[index] * 0.98, criterion[index])

    def test_rsi_thresholds_are_condition_specific_reference_lines(self):
        rules = _rules()
        buy_condition = rules["validation_visualization_rules"]["buy"]["filters"]["rsi"]["conditions"][0]
        buy_condition.update({
            "target": "RSI",
            "period": 14,
            "operator": "<=",
            "value": 30.0,
        })
        rules["buy"]["filters"]["rsi"]["conditions"][0] = deepcopy(buy_condition)

        sell_rsi = next(
            condition
            for condition in rules["validation_visualization_rules"]["sell"]["signals"]["ui_condition_b"]["groups"][0]["conditions"]
            if condition.get("target") == "RSI"
        )
        sell_rsi["period"] = 14
        sell_rsi["operator"] = ">="
        sell_rsi["value"] = 70.0
        actual_sell_rsi = next(
            condition
            for condition in rules["sell"]["signals"]["ui_condition_b"]["groups"][0]["conditions"]
            if condition.get("target") == "RSI"
        )
        actual_sell_rsi.update(deepcopy(sell_rsi))

        descriptors = [
            item
            for item in build_validation_filter_universe(rules)
            if item.family == FAMILY_RSI
        ]
        self.assertEqual(2, len(descriptors))
        self.assertEqual(
            {30.0, 70.0},
            {
                float(item.parameters["condition"]["threshold"])
                for item in descriptors
            },
        )
        self.assertTrue(all(
            item.series_keys == ("RSI", "CRITERION")
            for item in descriptors
        ))

        candles = _candles(40)
        cache = build_validation_indicator_cache(
            candles,
            rules,
            tuple(descriptors),
        )
        for descriptor in descriptors:
            threshold = float(descriptor.parameters["condition"]["threshold"])
            criterion = cache.values_for(descriptor.identity, "CRITERION")
            self.assertEqual(
                tuple(threshold for _ in candles),
                criterion,
            )

    def test_buy_execution_price_compare_is_visible_but_marked_unsupported(self):
        descriptors = build_validation_filter_universe(_rules())
        price_descriptors = [
            item for item in descriptors
            if item.family == FAMILY_PRICE_COMPARISON
        ]
        buy_only = next(
            item for item in price_descriptors
            if "BUY" in item.sides
            and item.parameters["condition"]["compare_target"] == "SIGNAL_PRICE"
        )
        self.assertIn("CRITERION", buy_only.series_keys)
        self.assertIn("BUY", buy_only.unsupported_sides)
        self.assertNotIn("BUY", buy_only.supported_sides)
        self.assertIn("VALIDATION_FILTER_NOT_EVALUATED", buy_only.unavailable_reason)

        sell = next(
            item for item in price_descriptors
            if "SELL" in item.sides
            and item.parameters["condition"]["compare_target"] == "AVG_PRICE"
        )
        self.assertIn("CRITERION", sell.series_keys)
        self.assertIn("SELL", sell.supported_sides)

    def test_structured_evidence_maps_only_surviving_or_branch_to_identity(self):
        rules = _rules()
        descriptors = build_validation_filter_universe(rules)
        entry = _signal_entry()

        records = signal_evidence_records_for_entry(entry, rules)
        self.assertEqual(1, len(records))
        self.assertEqual("MACD", records[0].label)

        active = active_filter_identities_for_entry(
            entry,
            rules,
            descriptors,
        )
        active_families = {
            descriptor.family
            for descriptor in descriptors
            if descriptor.identity in active
        }
        self.assertEqual({FAMILY_MACD_SIGNAL}, active_families)

    def test_cache_is_descriptor_scoped_and_contains_virtual_average(self):
        rules = _rules()
        descriptors = build_validation_filter_universe(rules)
        candles = _candles(70)
        entries = [
            ValidationReplayEntry(
                evaluation_side="BUY",
                evaluation_index=30,
                evaluation_time=candles[30]["time"],
                signal="BUY",
                reason="fixture",
                signal_index=30,
                signal_time=candles[30]["time"],
                delay_bar=0,
                matched_groups=[],
                details=[],
                trace={"conditions": [], "groups": [], "aggregations": []},
            ),
            ValidationReplayEntry(
                evaluation_side="BUY",
                evaluation_index=40,
                evaluation_time=candles[40]["time"],
                signal="BUY",
                reason="fixture",
                signal_index=40,
                signal_time=candles[40]["time"],
                delay_bar=0,
                matched_groups=[],
                details=[],
                trace={"conditions": [], "groups": [], "aggregations": []},
            ),
        ]
        cache = build_validation_indicator_cache(
            candles,
            rules,
            descriptors,
            entries=entries,
        )
        self.assertEqual(70, cache.candle_count)

        for descriptor in descriptors:
            for channel in descriptor.series_keys:
                values = cache.values_for(descriptor.identity, channel)
                self.assertEqual(
                    70,
                    len(values),
                    (descriptor.family, channel),
                )

        sell_gap = next(
            item for item in descriptors
            if item.family == FAMILY_PRICE_COMPARISON
            and "SELL" in item.sides
        )
        criterion = cache.values_for(sell_gap.identity, "CRITERION")
        self.assertIsNone(criterion[30])
        self.assertIsNotNone(criterion[31])
        self.assertIsNotNone(criterion[41])

    def test_price_box_prefix_values_do_not_change_when_future_candles_are_extreme(self):
        rules = _rules()
        descriptors = build_validation_filter_universe(rules)
        base_candles = _candles(50)
        extended = _candles(60, future_spike_from=50)

        base_cache = build_validation_indicator_cache(
            base_candles,
            rules,
            descriptors,
        )
        extended_cache = build_validation_indicator_cache(
            extended,
            rules,
            descriptors,
        )
        price_box_descriptor = next(
            item for item in descriptors
            if item.family == FAMILY_PRICE_BOX
        )
        self.assertEqual(("CRITERION",), price_box_descriptor.series_keys)
        base_criterion = base_cache.values_for(
            price_box_descriptor.identity,
            "CRITERION",
        )
        extended_criterion = extended_cache.values_for(
            price_box_descriptor.identity,
            "CRITERION",
        )
        self.assertEqual(50, len(base_criterion))
        self.assertEqual(60, len(extended_criterion))
        self.assertEqual(
            base_criterion,
            extended_criterion[:50],
        )


if __name__ == "__main__":
    unittest.main()
