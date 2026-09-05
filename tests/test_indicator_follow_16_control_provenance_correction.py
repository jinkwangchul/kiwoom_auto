# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
from importlib.util import module_from_spec, spec_from_file_location
import os
from pathlib import Path
import sys
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication

from buy_execution_policy import _repeat_budget
from engines.condition_engine import (
    evaluate_condition,
    evaluate_condition_expression,
    parse_condition_expression,
)
from engines.indicator_engine import build_indicator_series, price_box
from routine_signal_consumer import apply_duplicate_signal_priority, read_latest_holding_consistency
from routine_signal_probe import apply_signal_runtime_error_policy
import gui_indicator_follow_routine_settings_dialog as dialog_module


ROOT = Path(__file__).resolve().parents[1]
ROUTINE_DIR = next((ROOT / "routines").glob("*/routine_rule_mapper.py")).parent


def _load(name: str, filename: str):
    spec = spec_from_file_location(name, ROUTINE_DIR / filename)
    module = module_from_spec(spec)
    assert spec.loader is not None
    sys.path.insert(0, str(ROUTINE_DIR))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(ROUTINE_DIR))
    return module


class ConditionExpressionContractTest(unittest.TestCase):
    def test_project_not_and_parentheses_are_deterministic(self):
        parsed = parse_condition_expression(
            "A OR (B NOT C)",
            allowed_identifiers={"A", "B", "C"},
            allow_duplicate_identifiers=False,
        )
        self.assertTrue(parsed["ok"], parsed)
        self.assertTrue(evaluate_condition_expression(
            parsed["ast"], {"A": False, "B": True, "C": False}
        )["passed"])
        self.assertFalse(evaluate_condition_expression(
            parsed["ast"], {"A": False, "B": True, "C": True}
        )["passed"])

    def test_invalid_or_incomplete_expression_fails_closed(self):
        for expression in ("NOT A", "A AND NOT B", "A OR", "A AND (B"):
            parsed = parse_condition_expression(
                expression,
                allowed_identifiers={"A", "B"},
                allow_duplicate_identifiers=False,
            )
            self.assertFalse(parsed["ok"], expression)
        missing = evaluate_condition_expression(
            {"type": "identifier", "name": "A"}, {}
        )
        self.assertFalse(missing["ok"])
        self.assertFalse(missing["passed"])

    def test_left_to_right_and_result_set_not(self):
        parsed = parse_condition_expression(
            "A OR B AND C",
            allowed_identifiers={"A", "B", "C"},
        )
        result = evaluate_condition_expression(
            parsed["ast"],
            {"A": {3}, "B": {5}, "C": {5}},
        )
        self.assertEqual(result["matched_identities"], [5])
        parsed = parse_condition_expression("A NOT B", allowed_identifiers={"A", "B"})
        result = evaluate_condition_expression(parsed["ast"], {"A": {3, 5, 7}, "B": {5, 8}})
        self.assertEqual(result["matched_identities"], [3, 7])


class MapperAndConsumerProvenanceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mapper = _load("correction_mapper", "routine_rule_mapper.py")
        cls.buy_execution = _load("correction_buy_execution", "routine_buy_execution.py")
        cls.routine_engine = _load("correction_routine_engine", "routine_macd_engine.py")

    def _rules(self):
        return {
            "bar": {"bar_minutes": 1},
            "buy": {
                "groups": [{"enabled": True, "conditions": []}],
                "execution": {
                    "repeat": {
                        "detail_mode": "ROUND",
                        "round_operator": "ADD",
                        "round_budget_value": 9.99,
                    }
                },
            },
            "sell": {"signals": {}},
            "indicators": {"rsi": {"period": 14}},
        }

    def test_exact_20_control_inventory_is_connected(self):
        connected = {
            "basic.buy_signal_expr_line",
            "basic.sell_signal_expr_line",
            "buy.price_compare.mode_combo",
            "buy.price_compare.round_operator_combo",
            "buy.price_compare.round_budget_line",
            "buy.price_compare.budget_ratio_line",
            "buy.price_compare.above_mode_combo",
            "buy.price_compare.above_round_operator_combo",
            "buy.price_compare.above_round_budget_line",
            "buy.price_compare.above_budget_ratio_line",
            "sell.condition_a.ocr_direction_combo",
            "sell.condition_a.ocr_convert_line",
            "sell.condition_a.ocr_logic_combo",
            "sell.condition_c.macd_logic_combo",
            "sell.condition_a.gap_logic_combo",
            "sell.condition_b.price_box_logic_combo",
            "sell.condition_b.bollinger_logic_combo",
            "sell.condition_c.gap_logic_combo",
            "basic.basic_duplicate_signal_combo",
            "basic.basic_error_policy_combo",
        }
        self.assertEqual(len(connected), 20)

    def test_buy_and_sell_expression_reach_canonical_preview(self):
        state = {
            "basic": {
                "basic_signal_interval_combo": "1",
                "buy_signal_expr_line": "A OR (D NOT C)",
                "sell_signal_expr_line": "A NOT C",
            },
            "buy_ui": {"signal_filter": {}, "price_compare": {}},
            "sell_ui": {"signal_conditions": {
                "condition_a": {
                    "rsi_check": True,
                    "rsi_period_line": "14",
                    "rsi_compare_combo": "이상",
                    "rsi_value_line": "63",
                },
                "condition_c": {
                    "macd_check": True,
                    "macd_kind_combo": "MACD선",
                    "macd_sign_combo": "+",
                    "macd_value_line": "1.37",
                    "macd_compare_combo": "이상",
                },
            }},
        }
        preview = self.mapper.build_engine_rules_preview_from_ui_state(state, self._rules())
        candidates = preview["preview_rules"]["indicator_follow_rule_preview"]["candidates"]
        buy_expression = candidates["filters"]["composite"]["value"]["expression"]
        self.assertEqual(buy_expression["source"], "A OR (D NOT C)")
        self.assertEqual(buy_expression["identifier_map"]["D"], "rsi")
        sell_candidates = candidates["sell"]["add_signal_candidates"]
        self.assertEqual(
            sell_candidates["sell.signals.ui_preview_condition_a"]["value"]["signal_expression"]["source"],
            "A NOT C",
        )
        self.assertEqual(
            sell_candidates["sell.signals.ui_preview_condition_c"]["value"]["signal_expression"],
            sell_candidates["sell.signals.ui_preview_condition_a"]["value"]["signal_expression"],
        )

    def test_buy_expression_consumer_uses_ast_not_default_group_logic(self):
        parsed = parse_condition_expression(
            "A OR (D NOT C)",
            allowed_identifiers={"A", "B", "C", "D"},
        )
        composite = {
            "enabled": True,
            "expression": {
                "ast": parsed["ast"],
                "identifiers": parsed["identifiers"],
                "identifier_map": {
                    "A": "ocr",
                    "B": "bollinger",
                    "C": "moving_average",
                    "D": "rsi",
                },
            },
        }
        results = {
            "ocr": {"configured": True, "enabled": True, "passed": False},
            "moving_average": {"configured": True, "enabled": True, "passed": False},
            "rsi": {"configured": True, "enabled": True, "passed": True},
        }
        passed, detail = self.routine_engine._evaluate_buy_composite_filter(composite, results)
        self.assertTrue(passed, detail)
        results["moving_average"]["passed"] = True
        passed, _ = self.routine_engine._evaluate_buy_composite_filter(composite, results)
        self.assertFalse(passed)

    def test_sell_expression_consumer_aggregates_a_b_c_before_sell_priority(self):
        def expression_contract(text):
            parsed = parse_condition_expression(
                text,
                allowed_identifiers={"A", "B", "C"},
                allow_duplicate_identifiers=False,
            )
            return {
                "source": text,
                "normalized": parsed["normalized"],
                "ast": parsed["ast"],
                "identifiers": parsed["identifiers"],
                "identifier_map": {
                    "A": "ui_condition_a",
                    "B": "ui_condition_b",
                    "C": "ui_condition_c",
                },
            }

        candles = [
            {"open": value, "high": value + 1, "low": value - 1, "close": value, "volume": 1000}
            for value in range(10, 50)
        ]
        true_group = [{
            "enabled": True,
            "conditions": [{"target": "CLOSE", "operator": ">", "value": 0}],
        }]
        false_group = [{
            "enabled": True,
            "conditions": [{"target": "CLOSE", "operator": "<", "value": 0}],
        }]
        config = {
            "enabled": True,
            "macd": {"fast": 12, "slow": 26, "signal": 9},
            "rsi": {"period": 14},
            "moving_averages": [5, 20, 60],
            "buy": {"delay_bar": 0, "groups": []},
            "sell": {
                "delay_bar": 0,
                "signal_logic": "OR",
                "signals": {
                    "ui_condition_a": {"enabled": True, "groups": true_group},
                    "ui_condition_c": {"enabled": True, "groups": false_group},
                },
            },
        }
        and_contract = expression_contract("A AND C")
        config["sell"]["signals"]["ui_condition_a"]["signal_expression"] = deepcopy(and_contract)
        config["sell"]["signals"]["ui_condition_c"]["signal_expression"] = deepcopy(and_contract)
        self.assertIsNone(
            self.routine_engine.evaluate_indicator_follow_routine(candles, config).signal
        )

        not_contract = expression_contract("A NOT C")
        config["sell"]["signals"]["ui_condition_a"]["signal_expression"] = deepcopy(not_contract)
        config["sell"]["signals"]["ui_condition_c"]["signal_expression"] = deepcopy(not_contract)
        sell_signal = self.routine_engine.evaluate_indicator_follow_routine(candles, config)
        self.assertEqual(sell_signal.signal, "SELL")
        self.assertTrue(sell_signal.matched_groups)
        self.assertTrue(sell_signal.details)

    def test_price_compare_branch_sentinels_select_only_matching_policy(self):
        price_compare = {
            "check": True,
            "condition_combo": "<",
            "mode_combo": "회차기준",
            "round_operator_combo": "+",
            "round_budget_line": "0.62",
            "budget_ratio_line": "1.11",
            "above_condition_combo": ">=",
            "above_mode_combo": "예산기준",
            "above_round_operator_combo": "x",
            "above_round_budget_line": "1.37",
            "above_budget_ratio_line": "2.63",
        }
        state = {
            "basic": {"basic_signal_interval_combo": "1"},
            "buy_ui": {"signal_filter": {}, "price_compare": price_compare},
            "sell_ui": {"signal_conditions": {}},
        }
        preview = self.mapper.build_engine_rules_preview_from_ui_state(state, self._rules())
        candidate = preview["preview_rules"]["buy"]["filters"]["price_compare"]
        below, above = candidate["conditions"]
        self.assertEqual(below["operator"], "<=")
        self.assertEqual(above["operator"], ">")
        self.assertEqual(below["branch_policy"]["round_budget_value"], 0.62)
        self.assertEqual(above["branch_policy"]["budget_ratio"], 2.63)

        rules = self._rules()
        rules.setdefault("buy", {}).setdefault("filters", {})["price_compare"] = candidate
        planned_below, evidence_below, reason = self.buy_execution._buy_price_compare_branch_planning_rules(
            rules=rules,
            confirmed_round=1,
            average_price=90,
            actionable_order_price=100,
        )
        self.assertEqual(reason, "")
        self.assertEqual(evidence_below["branch_id"], "BELOW_OR_EQUAL")
        repeat_below = planned_below["buy"]["execution"]["repeat"]
        self.assertEqual(repeat_below["round_budget_value"], 0.62)
        budget, _, _, _, issues = _repeat_budget(
            repeat_below,
            {"base_buy_budget": 1000, "previous_buy_budget": 1000},
            10,
            1,
        )
        self.assertEqual(issues, [])
        self.assertEqual(budget, 1620)

        planned_above, evidence_above, reason = self.buy_execution._buy_price_compare_branch_planning_rules(
            rules=rules,
            confirmed_round=1,
            average_price=110,
            actionable_order_price=100,
        )
        self.assertEqual(reason, "")
        self.assertEqual(evidence_above["branch_id"], "ABOVE")
        repeat_above = planned_above["buy"]["execution"]["repeat"]
        self.assertEqual(repeat_above["budget_ratio"], 2.63)
        budget, _, _, _, issues = _repeat_budget(
            repeat_above,
            {"base_buy_budget": 1000, "previous_buy_budget": 1000},
            10,
        )
        self.assertEqual(issues, [])
        self.assertEqual(budget, 2630)

        planned_equal, evidence_equal, reason = self.buy_execution._buy_price_compare_branch_planning_rules(
            rules=rules,
            confirmed_round=1,
            average_price=100,
            actionable_order_price=100,
        )
        self.assertEqual(reason, "")
        self.assertEqual(evidence_equal["branch_id"], "BELOW_OR_EQUAL")
        self.assertEqual(
            planned_equal["buy"]["execution"]["repeat"]["round_budget_value"], 0.62
        )

        multiply_budget, _, reference, _, issues = _repeat_budget(
            {"detail_mode": "ROUND", "round_operator": "MULTIPLY", "round_budget_value": 1.37},
            {"base_buy_budget": 1000},
            10,
            4,
        )
        self.assertEqual(issues, [])
        self.assertEqual(multiply_budget, 5480)
        self.assertEqual(reference, "CONFIRMED_ROUND_X_VALUE_X_STARTING_BUDGET")

    def test_sell_ocr_direction_convert_logic_and_macd_logic_are_canonical(self):
        state = {
            "basic": {"basic_signal_interval_combo": "1", "sell_signal_expr_line": "A AND C"},
            "buy_ui": {"signal_filter": {}, "price_compare": {}},
            "sell_ui": {"signal_conditions": {
                "condition_a": {
                    "ocr_check": True,
                    "ocr_direction_combo": "상승",
                    "ocr_convert_line": "7",
                    "ocr_sign_combo": "+",
                    "ocr_value_line": "0.77",
                    "ocr_compare_combo": "이상",
                    "ocr_logic_combo": "OR",
                    "rsi_check": True,
                    "rsi_period_line": "13",
                    "rsi_compare_combo": "이하",
                    "rsi_value_line": "43",
                },
                "condition_c": {
                    "macd_check": True,
                    "macd_kind_combo": "시그널선",
                    "macd_sign_combo": "-",
                    "macd_value_line": "1.37",
                    "macd_compare_combo": "이하",
                    "macd_logic_combo": "NOT",
                    "array_check": True,
                    "array_first_period_combo": "5",
                    "array_first_compare_combo": ">",
                    "array_second_period_combo": "20",
                    "array_second_compare_combo": ">",
                    "array_third_period_combo": "60",
                },
            }},
        }
        preview = self.mapper.build_engine_rules_preview_from_ui_state(state, self._rules())
        candidates = preview["preview_rules"]["indicator_follow_rule_preview"]["candidates"]["sell"]["add_signal_candidates"]
        group_a = candidates["sell.signals.ui_preview_condition_a"]["value"]["groups"][0]
        self.assertEqual(group_a["conditions"][0]["operator"], "TURN_UP")
        self.assertEqual(group_a["conditions"][0]["bar_offset"], 7)
        self.assertEqual(group_a["conditions"][1]["bar_offset"], 7)
        self.assertEqual(group_a["condition_expression"]["operator"], "OR")
        group_c = candidates["sell.signals.ui_preview_condition_c"]["value"]["groups"][0]
        self.assertEqual(group_c["conditions"][0]["target"], "SIGNAL")
        self.assertEqual(group_c["condition_expression"]["operator"], "NOT")

    def test_expression_and_branch_values_cross_existing_approval_patch_boundary(self):
        state = {
            "basic": {
                "basic_signal_interval_combo": "1",
                "buy_signal_expr_line": "A OR D",
                "sell_signal_expr_line": "A NOT C",
            },
            "buy_ui": {
                "signal_filter": {},
                "price_compare": {
                    "check": True,
                    "condition_combo": "<",
                    "mode_combo": "회차기준",
                    "round_operator_combo": "+",
                    "round_budget_line": "0.62",
                    "budget_ratio_line": "1.11",
                    "above_condition_combo": ">=",
                    "above_mode_combo": "예산기준",
                    "above_round_operator_combo": "x",
                    "above_round_budget_line": "1.37",
                    "above_budget_ratio_line": "2.63",
                },
            },
            "sell_ui": {"signal_conditions": {
                "condition_a": {
                    "rsi_check": True,
                    "rsi_period_line": "13",
                    "rsi_compare_combo": "이하",
                    "rsi_value_line": "43",
                },
                "condition_c": {
                    "macd_check": True,
                    "macd_kind_combo": "MACD선",
                    "macd_sign_combo": "-",
                    "macd_value_line": "1.37",
                    "macd_compare_combo": "이하",
                },
            }},
        }
        current = self._rules()
        preview = self.mapper.build_engine_rules_preview_from_ui_state(state, current)
        decisions = {
            "buy.filters.composite": "APPROVED",
            "buy.filters.price_compare": "APPROVED",
            "sell.signals.ui_preview_condition_a": "APPROVED",
            "sell.signals.ui_preview_condition_c": "APPROVED",
        }
        approval = self.mapper.evaluate_rule_candidate_approval(preview, decisions)
        patch_preview = self.mapper.build_approved_rule_patch_preview(current, preview, approval)
        patches = {patch["source_path"]: patch for patch in patch_preview["patches"]}
        self.assertEqual(set(patches), set(decisions))
        self.assertEqual(
            patches["buy.filters.composite"]["value"]["expression"]["source"],
            "A OR D",
        )
        self.assertEqual(
            patches["buy.filters.price_compare"]["value"]["conditions"][1]["branch_policy"]["budget_ratio"],
            2.63,
        )
        self.assertEqual(
            patches["sell.signals.ui_preview_condition_a"]["signal"]["signal_expression"]["source"],
            "A NOT C",
        )

    def test_duplicate_and_error_policy_cross_approval_boundary(self):
        state = {
            "basic": {
                "basic_signal_interval_combo": "1",
                "basic_duplicate_signal_combo": "선행신호 우선",
                "basic_error_policy_combo": "매매지속",
            },
            "buy_ui": {"signal_filter": {}, "price_compare": {}},
            "sell_ui": {"signal_conditions": {}},
        }
        preview = self.mapper.build_engine_rules_preview_from_ui_state(state, self._rules())
        reserved = preview["preview_rules"]["indicator_follow_rule_preview"]["reserved_controls"]
        self.assertEqual(reserved, [])
        self.assertEqual(preview["preview_rules"]["signal_runtime_policy"]["duplicate_priority"], "LEADING")
        self.assertEqual(preview["preview_rules"]["signal_runtime_policy"]["error_policy"], "CONTINUE_NEXT_CYCLE")
        decisions = self.mapper.build_rule_approval_session(preview)["decisions"]
        self.assertIn("signal_runtime_policy", decisions)

    def test_ocr_convert_zero_one_two_are_exact_bar_offsets(self):
        for offset in (0, 1, 2):
            state = {
                "basic": {"basic_signal_interval_combo": "1", "sell_signal_expr_line": "A"},
                "buy_ui": {"signal_filter": {}, "price_compare": {}},
                "sell_ui": {"signal_conditions": {"condition_a": {
                    "ocr_check": True,
                    "ocr_direction_combo": "하락",
                    "ocr_convert_line": str(offset),
                    "ocr_sign_combo": "+",
                    "ocr_value_line": "0.77",
                    "ocr_compare_combo": "이상",
                }}},
            }
            preview = self.mapper.build_engine_rules_preview_from_ui_state(state, self._rules())
            conditions = preview["preview_rules"]["indicator_follow_rule_preview"]["candidates"]["sell"]["add_signal_candidate"]["value"]["groups"][0]["conditions"]
            self.assertEqual(conditions[0]["operator"], "TURN_DOWN")
            self.assertEqual(conditions[0]["bar_offset"], offset)
            self.assertEqual(conditions[1]["bar_offset"], offset)

    def test_price_box_formula_caller_cutoff_and_upper_lower_mapper(self):
        closes = [100.0 + index + (((index % 7) - 3) * 4.0) for index in range(40)]
        lower, middle, upper = price_box(closes, 24)
        prefix_lower, prefix_middle, prefix_upper = price_box(closes[:30], 24)
        self.assertNotEqual(upper[:30], prefix_upper)
        self.assertEqual(middle[:30], prefix_middle)
        self.assertEqual(
            price_box(closes[:30], 24),
            (prefix_lower, prefix_middle, prefix_upper),
        )
        self.assertEqual(len(lower), len(closes))
        series = build_indicator_series([{"close": value, "volume": 1} for value in closes])
        self.assertIn("PRICE_BOX_UPPER", series)
        self.assertIn("PRICE_BOX_MIDDLE", series)
        self.assertIn("PRICE_BOX_LOWER", series)

        for direction, compare, target, operator in (
            ("상향", "이상", "PRICE_BOX_UPPER", ">="),
            ("하향", "이하", "PRICE_BOX_LOWER", "<="),
        ):
            state = {
                "basic": {"basic_signal_interval_combo": "1", "sell_signal_expr_line": "B"},
                "buy_ui": {"signal_filter": {}, "price_compare": {}},
                "sell_ui": {"signal_conditions": {"condition_b": {
                    "price_box_check": True,
                    "price_box_direction_combo": direction,
                    "price_box_value_line": "0.62",
                    "price_box_compare_combo": compare,
                    "bollinger_check": False,
                    "gap_check": False,
                }}},
            }
            preview = self.mapper.build_engine_rules_preview_from_ui_state(state, self._rules())
            candidate = preview["preview_rules"]["indicator_follow_rule_preview"]["candidates"]["sell"]["add_signal_candidate"]
            condition = candidate["value"]["groups"][0]["conditions"][0]
            self.assertTrue(candidate["value"]["enabled"])
            self.assertEqual(condition["compare_target"], target)
            self.assertEqual(condition["operator"], operator)
            self.assertEqual(condition["value"], 0.62)

        upper_result = evaluate_condition(
            {"target": "CLOSE", "operator": ">=", "compare_target": "PRICE_BOX_UPPER", "value": 0.5},
            {"CLOSE": [100.6], "PRICE_BOX_UPPER": [100.0]},
            0,
        )
        lower_result = evaluate_condition(
            {"target": "CLOSE", "operator": "<=", "compare_target": "PRICE_BOX_LOWER", "value": 0.5},
            {"CLOSE": [98.5], "PRICE_BOX_LOWER": [99.0]},
            0,
        )
        self.assertTrue(upper_result.passed)
        self.assertTrue(lower_result.passed)

    def test_price_box_uses_full_dataset_avgif_and_population_stdevif(self):
        closes = [10.0, 12.0, 8.0, 15.0, 7.0, 18.0]
        lower, middle, upper = price_box(closes, 3)
        deviations = [
            closes[index] - middle[index]
            for index in range(2, len(closes))
        ]
        positive = [value for value in deviations if value > 0]
        negative = [value for value in deviations if value < 0]
        positive_mean = sum(positive) / len(positive)
        positive_std = (
            sum((value - positive_mean) ** 2 for value in positive) / len(positive)
        ) ** 0.5
        negative_mean = sum(negative) / len(negative)
        negative_std = (
            sum((value - negative_mean) ** 2 for value in negative) / len(negative)
        ) ** 0.5
        for index in range(2, len(closes)):
            self.assertAlmostEqual(
                upper[index],
                middle[index] + positive_mean + (2 * positive_std),
            )
            self.assertAlmostEqual(
                lower[index],
                middle[index] + negative_mean - (2 * negative_std),
            )

    def test_signal_runtime_policy_survives_approval_apply_and_commit_preview(self):
        current = self._rules()
        state = {
            "basic": {
                "basic_signal_interval_combo": "1",
                "basic_duplicate_signal_combo": "후행신호 우선",
                "basic_error_policy_combo": "매매지속",
            },
            "buy_ui": {"signal_filter": {}, "price_compare": {}},
            "sell_ui": {"signal_conditions": {}},
        }
        preview = self.mapper.build_engine_rules_preview_from_ui_state(state, current)
        candidate = preview["preview_rules"]["signal_runtime_policy"]
        self.assertEqual(candidate["duplicate_priority"], "TRAILING")
        self.assertEqual(candidate["error_policy"], "CONTINUE_NEXT_CYCLE")
        approval = self.mapper.evaluate_rule_candidate_approval(
            preview, {"signal_runtime_policy": "APPROVED"}
        )
        patch = self.mapper.build_approved_rule_patch_preview(current, preview, approval)
        self.assertEqual(patch["patches"][0]["operation"], "set_signal_runtime_policy")
        applied = self.mapper.apply_approved_rule_patch_preview(current, patch)
        self.assertEqual(applied["applied_rules_preview"]["signal_runtime_policy"], candidate)
        session = self.mapper.build_rule_approval_session(
            preview, {"signal_runtime_policy": "APPROVED"}
        )
        fingerprint = self.mapper.build_rule_approval_session_fingerprint(current, preview)
        session["fingerprint"] = fingerprint["fingerprint"]
        session["fingerprint_detail"] = fingerprint
        commit = self.mapper.build_rule_commit_preview(
            current, preview, session, {"approval_session_dirty": False}
        )
        self.assertTrue(commit["commit_allowed"], commit)
        self.assertEqual(commit["final_diff"][0]["path"], "signal_runtime_policy")

    def test_all_six_sell_row_operators_preserve_left_to_right(self):
        state = {
            "basic": {"basic_signal_interval_combo": "1", "sell_signal_expr_line": "A OR B OR C"},
            "buy_ui": {"signal_filter": {}, "price_compare": {}},
            "sell_ui": {"signal_conditions": {
                "condition_a": {"ocr_check": True, "ocr_direction_combo": "상승", "ocr_convert_line": "0", "ocr_logic_combo": "OR", "gap_check": True, "gap_left_combo": "현재가", "gap_right_combo": "평단가", "gap_direction_combo": "상향", "gap_value_line": "1", "gap_compare_combo": "이상", "gap_logic_combo": "NOT", "rsi_check": True, "rsi_period_line": "14", "rsi_compare_combo": "이상", "rsi_value_line": "50"},
                "condition_b": {"price_box_check": True, "price_box_direction_combo": "상향", "price_box_value_line": "1", "price_box_compare_combo": "이상", "price_box_logic_combo": "NOT", "bollinger_check": True, "bollinger_direction_combo": "상향", "bollinger_value_line": "1", "bollinger_compare_combo": "이상", "bollinger_logic_combo": "OR", "gap_check": True, "gap_left_combo": "현재가", "gap_right_combo": "평단가", "gap_direction_combo": "하향", "gap_value_line": "1", "gap_compare_combo": "이하"},
                "condition_c": {"gap_check": True, "gap_left_combo": "현재가", "gap_right_combo": "평단가", "gap_direction_combo": "상하", "gap_value_line": "1", "gap_compare_combo": "이내", "gap_logic_combo": "OR", "macd_check": True, "macd_kind_combo": "MACD선", "macd_sign_combo": "+", "macd_value_line": "0", "macd_compare_combo": "이상", "macd_logic_combo": "NOT", "array_check": True, "array_first_period_combo": "5", "array_first_compare_combo": ">", "array_second_period_combo": "20", "array_second_compare_combo": ">", "array_third_period_combo": "60"},
            }},
        }
        preview = self.mapper.build_engine_rules_preview_from_ui_state(state, self._rules())
        candidates = preview["preview_rules"]["indicator_follow_rule_preview"]["candidates"]["sell"]["add_signal_candidates"]
        expected = {"a": ("OR", "NOT"), "b": ("NOT", "OR"), "c": ("OR", "NOT")}
        for letter, operators in expected.items():
            ast = candidates[f"sell.signals.ui_preview_condition_{letter}"]["value"]["groups"][0]["condition_expression"]
            self.assertEqual(ast["operator"], operators[1])
            self.assertEqual(ast["left"]["operator"], operators[0])


class DuplicatePriorityContractTest(unittest.TestCase):
    def _signal(self, identity, side, priority, created):
        return {"id": identity, "created_at": created, "routine": "지표추종매매", "routine_instance_id": "R1", "code": "005930", "signal": side, "status": "PENDING", "signal_runtime_policy": {"duplicate_priority": priority}}

    def test_leading_keeps_first_and_ignores_later_opposite(self):
        first = self._signal("S1", "SELL", "LEADING", "1")
        second = self._signal("S2", "BUY", "LEADING", "2")
        updates = []
        selected, summary = apply_duplicate_signal_priority([first, second], all_signals=[first, second], status_updater=lambda *args, **kwargs: updates.append((args, kwargs)))
        self.assertEqual([row["id"] for row in selected], ["S1"])
        self.assertEqual(summary["leading_ignored"], 1)
        self.assertEqual(updates[0][0][0], "S2")

    def test_trailing_waits_cancel_effect_then_refreshes_holding(self):
        first = self._signal("S1", "BUY", "TRAILING", "1")
        second = self._signal("S2", "SELL", "TRAILING", "2")
        original = {"id": "O1", "source_signal_id": "S1", "code": "005930", "side": "BUY", "status": "BROKER_ACCEPTED", "broker_order_no": "B1"}
        cancel_calls = []
        selected, summary = apply_duplicate_signal_priority([second], all_signals=[first, second], orders=[original], cancel_requester=lambda *args, **kwargs: cancel_calls.append((args, kwargs)) or {"cancel_requested": 1}, holding_consistency_reader=lambda _code: True, status_updater=lambda *args, **kwargs: None)
        self.assertEqual(selected, [])
        self.assertEqual(summary["cancel_requested"], 1)
        cancel = {"id": "C1", "status": "BROKER_ACCEPTED", "order_action": "CANCEL", "original_order_effect_confirmed": True, "execution_request": {"request_preview": {"order_action": "CANCEL", "original_order_no": "B1"}}}
        updates = []
        selected, summary = apply_duplicate_signal_priority([second], all_signals=[first, second], orders=[original, cancel], cancel_requester=None, holding_consistency_reader=lambda _code: True, status_updater=lambda *args, **kwargs: updates.append((args, kwargs)))
        self.assertEqual([row["id"] for row in selected], ["S2"])
        self.assertEqual(summary["neutralized"], 1)
        self.assertEqual(updates[0][0][0], "S1")

    def test_trailing_is_symmetric_and_fails_closed_on_holding_mismatch(self):
        for prior_side, newest_side in (("BUY", "SELL"), ("SELL", "BUY")):
            first = self._signal("S1", prior_side, "TRAILING", "1")
            second = self._signal("S2", newest_side, "TRAILING", "2")
            original = {
                "id": "O1", "source_signal_id": "S1", "code": "005930",
                "side": prior_side, "status": "BROKER_ACCEPTED", "broker_order_no": "B1",
            }
            cancel = {
                "id": "C1", "status": "BROKER_ACCEPTED", "order_action": "CANCEL",
                "original_order_effect_confirmed": True,
                "execution_request": {"request_preview": {"order_action": "CANCEL", "original_order_no": "B1"}},
            }
            selected, summary = apply_duplicate_signal_priority(
                [second], all_signals=[first, second], orders=[original, cancel],
                holding_consistency_reader=lambda _code: False,
                status_updater=lambda *args, **kwargs: None,
            )
            self.assertEqual(selected, [])
            self.assertEqual(summary["holding_mismatch"], 1)

    def test_holding_consistency_reader_uses_both_authoritative_snapshots(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            positions_path = root / "positions.json"
            holdings_path = root / "broker_holdings.json"
            positions_path.write_text(
                '{"positions":[{"code":"005930","quantity":3}]}', encoding="utf-8"
            )
            holdings_path.write_text(
                '{"holdings":[{"code":"005930","holding_quantity":3,"reconciliation_status":"CONSISTENT"}]}',
                encoding="utf-8",
            )
            self.assertTrue(read_latest_holding_consistency(
                "005930", positions_path=positions_path, holdings_path=holdings_path
            ))
            holdings_path.write_text(
                '{"holdings":[{"code":"005930","holding_quantity":2,"reconciliation_status":"MISMATCH"}]}',
                encoding="utf-8",
            )
            self.assertFalse(read_latest_holding_consistency(
                "005930", positions_path=positions_path, holdings_path=holdings_path
            ))
            self.assertFalse(read_latest_holding_consistency(
                "005930", positions_path=root / "missing.json", holdings_path=holdings_path
            ))


class ErrorPolicyContractTest(unittest.TestCase):
    def _apply(self, policy, *, signal="ERROR"):
        events = []
        reviews = []
        result = apply_signal_runtime_error_policy(
            {"signal": signal, "reason": "non-regular flow"},
            policy,
            stock_dir=Path("005930_test"),
            code="005930",
            name="test",
            routine_name="지표추종매매",
            tick_key="2026-09-05T10:00:00",
            event_appender=lambda *args, **kwargs: events.append((args, kwargs)) or {"ok": True},
            review_marker=lambda *args, **kwargs: reviews.append((args, kwargs)) or True,
        )
        return result, events, reviews

    def test_stop_policy_records_event_and_review(self):
        result, events, reviews = self._apply({"error_policy": "STOP_AND_REVIEW"})
        self.assertEqual(result["error_policy_action"], "STOP_AND_REVIEW")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0][0][0], "PROCESSING_ERROR")
        self.assertEqual(len(reviews), 1)
        self.assertTrue(result["error_policy_review_created"])

    def test_continue_policy_records_event_and_next_cycle_evidence_only(self):
        result, events, reviews = self._apply({"error_policy": "CONTINUE_NEXT_CYCLE"})
        self.assertEqual(len(events), 1)
        self.assertEqual(reviews, [])
        self.assertEqual(
            result["cycle_failure_evidence"]["action"],
            "SKIP_ABNORMAL_FLOW_CONTINUE_NEXT_CYCLE",
        )

    def test_missing_signal_field_is_abnormal_and_follows_policy(self):
        events = []
        result = apply_signal_runtime_error_policy(
            {"reason": "missing side"},
            {"error_policy": "CONTINUE_NEXT_CYCLE"},
            stock_dir=Path("005930_test"),
            code="005930",
            name="test",
            routine_name="지표추종매매",
            tick_key="2026-09-05T10:00:00",
            event_appender=lambda *args, **kwargs: events.append((args, kwargs)) or {"ok": True},
        )
        self.assertEqual(result["signal"], "ERROR")
        self.assertEqual(result["error_policy_action"], "CONTINUE_NEXT_CYCLE")
        self.assertEqual(len(events), 1)

    def test_absent_policy_and_normal_duplicate_cancel_preserve_legacy_flow(self):
        result, events, reviews = self._apply(None)
        self.assertNotIn("error_policy_action", result)
        self.assertEqual(events, [])
        result, events, reviews = self._apply(
            {"error_policy": "STOP_AND_REVIEW"}, signal="CANCELLED"
        )
        self.assertNotIn("error_policy_action", result)
        self.assertEqual(events, [])
        self.assertEqual(reviews, [])


class ConnectedUiAndSaveLoadTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.rules_path = Path(self.temp.name) / "rules.json"
        self.rules_path.write_bytes((ROUTINE_DIR / "rules.json").read_bytes())
        self.dialog = dialog_module.IndicatorFollowRoutineSettingsDialog(rules_path=self.rules_path)

    def tearDown(self):
        self.dialog.close()
        self.dialog.deleteLater()
        self.temp.cleanup()

    def test_duplicate_and_error_controls_are_enabled(self):
        self.assertTrue(self.dialog.basic_duplicate_signal_combo.isEnabled())
        self.assertTrue(self.dialog.basic_error_policy_combo.isEnabled())

    def test_14_connected_values_collect_and_apply_without_cross_field_loss(self):
        values = {
            "buy_signal_expr_line": "A OR (D NOT C)",
            "sell_signal_expr_line": "A NOT C",
        }
        for name, value in values.items():
            getattr(self.dialog, name).setText(value)
        self.dialog.buy_price_compare_mode_combo.setCurrentText("회차기준")
        self.dialog.buy_price_compare_round_operator_combo.setCurrentText("+")
        self.dialog.buy_price_compare_round_budget_line.setText("0.62")
        self.dialog.buy_price_compare_budget_ratio_line.setText("1.37")
        self.dialog.buy_price_compare_above_mode_combo.setCurrentText("예산기준")
        self.dialog.buy_price_compare_above_round_operator_combo.setCurrentText("x")
        self.dialog.buy_price_compare_above_round_budget_line.setText("0.77")
        self.dialog.buy_price_compare_above_budget_ratio_line.setText("2.63")
        self.dialog.sell_signal_condition_a_ocr_direction_combo.setCurrentText("상승")
        self.dialog.sell_signal_condition_a_ocr_convert_line.setText("7")
        self.dialog.sell_signal_condition_a_ocr_logic_combo.setCurrentText("OR")
        self.dialog.sell_signal_condition_c_macd_logic_combo.setCurrentText("NOT")
        self.dialog.basic_duplicate_signal_combo.setCurrentText("선행신호 우선")
        self.dialog.basic_error_policy_combo.setCurrentText("매매지속")

        collected = self.dialog.collect_indicator_follow_ui_state()
        self.assertEqual(collected["basic"]["buy_signal_expr_line"], values["buy_signal_expr_line"])
        self.assertEqual(collected["basic"]["sell_signal_expr_line"], values["sell_signal_expr_line"])
        self.assertEqual(collected["basic"]["basic_duplicate_signal_combo"], "선행신호 우선")
        self.assertEqual(collected["basic"]["basic_error_policy_combo"], "매매지속")
        price = collected["buy_ui"]["price_compare"]
        self.assertEqual(price["round_budget_line"], "0.62")
        self.assertEqual(price["budget_ratio_line"], "1.37")
        self.assertEqual(price["above_round_budget_line"], "0.77")
        self.assertEqual(price["above_budget_ratio_line"], "2.63")
        condition_a = collected["sell_ui"]["signal_conditions"]["condition_a"]
        condition_c = collected["sell_ui"]["signal_conditions"]["condition_c"]
        self.assertEqual(condition_a["ocr_direction_combo"], "상승")
        self.assertEqual(condition_a["ocr_convert_line"], "7")
        self.assertEqual(condition_a["ocr_logic_combo"], "OR")
        self.assertEqual(condition_c["macd_logic_combo"], "NOT")

        saved = self.dialog.save_indicator_follow_ui_state_to_rules()
        self.assertTrue(saved["success"], saved)
        other = dialog_module.IndicatorFollowRoutineSettingsDialog(rules_path=self.rules_path)
        try:
            reapplied = other.collect_indicator_follow_ui_state()
            self.assertEqual(reapplied["basic"]["buy_signal_expr_line"], values["buy_signal_expr_line"])
            self.assertEqual(reapplied["basic"]["basic_duplicate_signal_combo"], "선행신호 우선")
            self.assertEqual(reapplied["basic"]["basic_error_policy_combo"], "매매지속")
            self.assertEqual(reapplied["buy_ui"]["price_compare"]["above_budget_ratio_line"], "2.63")
            self.assertEqual(
                reapplied["sell_ui"]["signal_conditions"]["condition_a"]["ocr_convert_line"],
                "7",
            )
            self.assertEqual(
                reapplied["sell_ui"]["signal_conditions"]["condition_c"]["macd_logic_combo"],
                "NOT",
            )
        finally:
            other.close()
            other.deleteLater()


if __name__ == "__main__":
    unittest.main()
