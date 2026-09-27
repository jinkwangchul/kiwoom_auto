# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from inspect import signature
import unittest

from indicator_follow_backtest_parity import (
    BROKER_DIRECT_SELECTED_TIMEFRAME_FULL_MARKET,
    CANDLE_PROJECTION_PARITY_UNKNOWN_INVALID,
    COMPLETED_BAR_SOURCE_PARITY_NOT_VERIFIED,
    EXECUTION_MODEL_SIMPLIFIED_CLOSE_FILL,
    EXECUTION_PARITY_NOT_PRODUCTION_EQUIVALENT,
    FORMING_BAR_PARITY_NOT_VERIFIED,
    FILTER_SIGNAL_BACKTEST_AUTHORIZED,
    FILTER_SIGNAL_BACKTEST_BLOCKED,
    MARKET_SESSION_PARITY_FAIL,
    MARKET_SESSION_PARITY_UNKNOWN,
    PRODUCTION_DECISION_SCOPE_FILTER_SIGNAL_ONLY,
    SIGNAL_PARITY_FAIL,
    SIGNAL_PARITY_PASS,
    candle_projection_parity_report,
    compare_signal_replay_entries,
    emitted_signal_entries,
    execution_model_report,
    filter_signal_backtest_authorization_report,
    market_session_parity_report,
    production_decision_scope_report,
    require_signal_replay_parity,
    signal_rule_support_report,
)
from indicator_follow_signal_validation_projection import (
    project_signal_validation_rules,
)
from routines.지표추종매매.routine_validation_contract import ValidationStockRef
from routines.지표추종매매.routine_validation_historical import (
    ValidationHistoricalSnapshot,
)


@dataclass(frozen=True)
class _Entry:
    evaluation_side: str
    evaluation_index: int
    evaluation_time: str
    signal: str | None
    signal_index: int | None
    signal_time: str | None
    delay_bar: int
    matched_groups: tuple[str, ...] = ()
    details: tuple[str, ...] = ()


class IndicatorFollowBacktestParityTest(unittest.TestCase):
    def _buy(self, index: int = 3, signal_index: int = 2) -> _Entry:
        return _Entry(
            "BUY",
            index,
            f"20260923090{index}00",
            "BUY",
            signal_index,
            f"20260923090{signal_index}00",
            index - signal_index,
            ("A",),
        )

    def test_emitted_entries_keep_only_matching_buy_sell_signals(self):
        entries = (
            self._buy(),
            _Entry("SELL", 4, "t4", None, None, None, 0),
            _Entry("BUY", 5, "t5", "SELL", 5, "t5", 0),
        )
        self.assertEqual((self._buy(),), emitted_signal_entries(entries))

    def test_identical_authoritative_and_optimized_entries_pass(self):
        report = compare_signal_replay_entries((self._buy(),), (self._buy(),))
        self.assertEqual(SIGNAL_PARITY_PASS, report.status)
        self.assertTrue(report.ok)
        self.assertEqual(1, report.authoritative_count)
        self.assertEqual(1, report.optimized_count)

    def test_missing_fast_path_signal_fails_closed(self):
        report = compare_signal_replay_entries((self._buy(),), ())
        self.assertEqual(SIGNAL_PARITY_FAIL, report.status)
        self.assertEqual((("BUY", 3, "20260923090300"),), report.missing)
        with self.assertRaisesRegex(ValueError, "BACKTEST_SIGNAL_PARITY_MISMATCH"):
            require_signal_replay_parity((self._buy(),), ())

    def test_extra_fast_path_signal_false_positive_fails_closed(self):
        report = compare_signal_replay_entries((), (self._buy(),))
        self.assertEqual(SIGNAL_PARITY_FAIL, report.status)
        self.assertEqual((('BUY', 3, '20260923090300'),), report.extra)
        with self.assertRaisesRegex(ValueError, "BACKTEST_SIGNAL_PARITY_MISMATCH"):
            require_signal_replay_parity((), (self._buy(),))

    def test_wrong_signal_index_is_reported_as_mismatch(self):
        optimized = self._buy(signal_index=1)
        report = compare_signal_replay_entries((self._buy(),), (optimized,))
        self.assertEqual(SIGNAL_PARITY_FAIL, report.status)
        self.assertEqual((("BUY", 3, "20260923090300"),), report.mismatched)
        evidence = report.to_dict()["mismatch_evidence"][0]
        self.assertEqual(2, evidence["authoritative"]["signal_index"])
        self.assertEqual(1, evidence["optimized"]["signal_index"])

    def test_execution_report_never_authorizes_pnl_with_virtual_broker(self):
        report = execution_model_report({
            "buy": {"execution": {"base": {"execution_mode": "MULTI_HOGA"}}},
            "sell": {"method": {"setting_a": {"enabled": True}}},
            "signal_runtime_policy": {"duplicate_priority": "TRAILING"},
        })
        self.assertEqual(
            EXECUTION_PARITY_NOT_PRODUCTION_EQUIVALENT,
            report["status"],
        )
        self.assertEqual(
            EXECUTION_MODEL_SIMPLIFIED_CLOSE_FILL,
            report["model_name"],
        )
        self.assertFalse(report["production_pnl_realistic"])
        self.assertFalse(report["pnl_backtest_authorized"])
        self.assertIn(
            "BUY_EXECUTION_POLICY_SIMPLIFIED",
            report["observed_policy_gaps"],
        )
        self.assertIn("MULTI_HOGA_NOT_REPRODUCED", report["limitations"])
        self.assertIn("BROKER_FILLS_NOT_REPRODUCED", report["limitations"])

    def _historical_snapshot(self, candle_time: str) -> ValidationHistoricalSnapshot:
        return ValidationHistoricalSnapshot(
            stock=ValidationStockRef("005930", "Samsung"),
            timeframe_minutes=5,
            requested_count=1,
            request_id="CANDLE-PROJECTION-PARITY",
            rows=[{
                "\uccb4\uacb0\uc2dc\uac04": candle_time,
                "\uc2dc\uac00": "100",
                "\uace0\uac00": "101",
                "\uc800\uac00": "99",
                "\ud604\uc7ac\uac00": "100",
                "\uac70\ub798\ub7c9": "1",
            }],
        )

    def test_candle_projection_report_distinguishes_forming_and_completed_minute_row(self):
        historical = self._historical_snapshot("20260923093000")

        forming = candle_projection_parity_report(
            historical,
            as_of=datetime(2026, 9, 23, 9, 32),
        )
        completed = candle_projection_parity_report(
            historical,
            as_of=datetime(2026, 9, 23, 9, 36),
        )

        self.assertEqual(FORMING_BAR_PARITY_NOT_VERIFIED, forming["status"])
        self.assertTrue(forming["latest_row_forming"])
        self.assertEqual(
            COMPLETED_BAR_SOURCE_PARITY_NOT_VERIFIED,
            completed["status"],
        )
        self.assertFalse(completed["latest_row_forming"])
        for report in (forming, completed):
            self.assertEqual("BROKER_DIRECT_TIMEFRAME", report["validation_source"])
            self.assertEqual(
                "MINUTE_PLUS_REALTIME_SHADOW_FORMING_BASE_BAR",
                report["production_source"],
            )
            self.assertFalse(report["production_equivalent"])

    def test_candle_projection_report_fails_closed_for_no_rows(self):
        historical = ValidationHistoricalSnapshot(
            stock=ValidationStockRef("005930", "Samsung"),
            timeframe_minutes=5,
            requested_count=1,
            request_id="CANDLE-PROJECTION-EMPTY",
            rows=[],
        )

        report = candle_projection_parity_report(historical)

        self.assertEqual(CANDLE_PROJECTION_PARITY_UNKNOWN_INVALID, report["status"])
        self.assertFalse(report["latest_row_forming"])
        self.assertFalse(report["production_equivalent"])

    def test_production_decision_scope_reports_filter_only_coverage_and_gate_gaps(self):
        rules = {"buy": {"enabled": True}}

        report = production_decision_scope_report(rules)

        self.assertEqual(
            PRODUCTION_DECISION_SCOPE_FILTER_SIGNAL_ONLY,
            report["status"],
        )
        self.assertFalse(report["production_order_signal_equivalent"])
        self.assertEqual(
            ["RAW_FILTER_EVALUATOR", "BUY_SELL_CONFLICT_SELECTOR"],
            report["covered"],
        )
        self.assertEqual(
            [
                "CYCLE_UNRESOLVED_GATE_NOT_REPLAYED",
                "BUY_PHASE_COMPLETED_GATE_NOT_REPLAYED",
                "BUY_EXECUTION_INTENT_READINESS_NOT_REPLAYED",
                "SELL_EXECUTION_INTENT_READINESS_NOT_REPLAYED",
            ],
            report["gaps"],
        )

    def test_market_session_parity_reports_both_current_validation_modes(self):
        production_contract = {
            "status": "PRODUCTION_SESSION_READY",
            "ready": True,
            "production_equivalent": True,
            "session_windows": [{
                "name": "regular",
                "start_time": "09:00:00",
                "end_time": "15:20:00",
            }],
        }

        full_market = market_session_parity_report(
            production_contract,
            {"regular_market_only": False},
            timeframe_key="M5",
            timeframe_minutes=5,
        )
        regular_only = market_session_parity_report(
            production_contract,
            {"regular_market_only": True},
            timeframe_key="M5",
            timeframe_minutes=5,
        )

        for report in (full_market, regular_only):
            self.assertEqual(MARKET_SESSION_PARITY_FAIL, report["status"])
            self.assertFalse(report["production_equivalent"])
        self.assertEqual(
            BROKER_DIRECT_SELECTED_TIMEFRAME_FULL_MARKET,
            full_market["validation_mode"],
        )
        self.assertEqual(full_market["validation_mode"], full_market["validation_source"])
        self.assertEqual(
            BROKER_DIRECT_SELECTED_TIMEFRAME_FULL_MARKET,
            regular_only["validation_mode"],
        )
        self.assertEqual(regular_only["validation_mode"], regular_only["validation_source"])

        unknown = market_session_parity_report(
            {"status": "PRODUCTION_SESSION_UNAVAILABLE", "ready": False},
            {"regular_market_only": False},
            timeframe_key="M5",
            timeframe_minutes=5,
        )
        self.assertEqual(MARKET_SESSION_PARITY_UNKNOWN, unknown["status"])
        self.assertFalse(unknown["production_equivalent"])

    def test_production_m1_completed_source_can_pass_only_matching_session_and_coverage(self):
        self.assertIn("calculation_provenance", signature(market_session_parity_report).parameters)
        self.assertIn("calculation_provenance", signature(candle_projection_parity_report).parameters)
        contract = {
            "status": "PRODUCTION_SESSION_READY", "ready": True,
            "session_windows": [{"name": "regular", "start_time": "09:00:00", "end_time": "15:20:00"}],
            "selected_ats": [], "selection_source": "none",
        }
        provenance = {
            "calculation_source": "PRODUCTION_M1_SESSION_AGGREGATION",
            "source_timeframe_key": "M1", "timeframe_key": "M5",
            "source_coverage_sufficient": True,
            "production_calculation_cache_identity": ("005930", "M1", "M5", "SESSION"),
            "forming_bar_status": "FORMING_BAR_PARITY_NOT_VERIFIED",
            "forming_bar_included": False,
            "production_session_contract": contract,
        }
        market = market_session_parity_report(
            contract, {"regular_market_only": True},
            timeframe_key="M5", timeframe_minutes=5,
            calculation_provenance=provenance,
        )
        candle = candle_projection_parity_report(
            self._historical_snapshot("20260923093000"),
            as_of=datetime(2026, 9, 23, 9, 36),
            calculation_provenance=provenance,
        )

        self.assertTrue(market["production_equivalent"])
        self.assertTrue(candle["production_equivalent"])
        self.assertEqual("FORMING_BAR_PARITY_NOT_VERIFIED", candle["forming_bar_status"])
        authorization = filter_signal_backtest_authorization_report(
            {"signal_backtest_authorized": True},
            {"status": SIGNAL_PARITY_PASS}, market, candle,
        )
        self.assertEqual(FILTER_SIGNAL_BACKTEST_AUTHORIZED, authorization["status"])
        self.assertEqual(
            EXECUTION_PARITY_NOT_PRODUCTION_EQUIVALENT,
            execution_model_report({})["status"],
        )

        altered = dict(contract, session_windows=[{
            "name": "regular", "start_time": "09:00:00", "end_time": "15:30:00",
        }])
        drift = market_session_parity_report(
            altered, {}, timeframe_key="M5", timeframe_minutes=5,
            calculation_provenance=provenance,
        )
        self.assertFalse(drift["production_equivalent"])
        unavailable = market_session_parity_report(
            dict(contract, ready=False), {}, timeframe_key="M5",
            timeframe_minutes=5, calculation_provenance=provenance,
        )
        self.assertFalse(unavailable["production_equivalent"])
        insufficient = candle_projection_parity_report(
            self._historical_snapshot("20260923093000"),
            calculation_provenance=dict(provenance, source_coverage_sufficient=False),
        )
        self.assertFalse(insufficient["production_equivalent"])
        forming = candle_projection_parity_report(
            self._historical_snapshot("20260923093000"),
            calculation_provenance=dict(provenance, forming_bar_included=True),
        )
        self.assertEqual(FORMING_BAR_PARITY_NOT_VERIFIED, forming["status"])
        self.assertFalse(forming["production_equivalent"])
        self.assertEqual(
            FILTER_SIGNAL_BACKTEST_BLOCKED,
            filter_signal_backtest_authorization_report(
                {"signal_backtest_authorized": True},
                {"status": SIGNAL_PARITY_PASS}, market, forming,
            )["status"],
        )

    def test_filter_signal_authorization_requires_only_the_four_trust_gates(self):
        authorized = filter_signal_backtest_authorization_report(
            {"signal_backtest_authorized": True},
            {"status": SIGNAL_PARITY_PASS},
            {"production_equivalent": True},
            {"production_equivalent": True},
        )

        self.assertEqual(FILTER_SIGNAL_BACKTEST_AUTHORIZED, authorized["status"])
        self.assertTrue(authorized["filter_signal_backtest_authorized"])
        self.assertEqual([], authorized["blockers"])

        blocked = filter_signal_backtest_authorization_report(
            {"signal_backtest_authorized": True},
            {"status": SIGNAL_PARITY_PASS},
            {"production_equivalent": False},
            {"production_equivalent": False},
        )
        self.assertEqual(FILTER_SIGNAL_BACKTEST_BLOCKED, blocked["status"])
        self.assertFalse(blocked["filter_signal_backtest_authorized"])
        self.assertEqual(
            ["MARKET_SESSION_PARITY", "CANDLE_PROJECTION_PARITY"],
            blocked["blockers"],
        )

    def test_unreferenced_ui_sell_legacy_price_does_not_block_supported_expression(self):
        expression = {
            "source": "A OR B",
            "identifiers": ["A", "B"],
            "identifier_map": {
                "A": "ui_condition_a",
                "B": "ui_condition_b",
                "C": "ui_condition_c",
            },
            "ast": {
                "type": "binary",
                "operator": "OR",
                "left": {"type": "identifier", "name": "A"},
                "right": {"type": "identifier", "name": "B"},
            },
        }
        report = signal_rule_support_report({
            "sell": {
                "signals": {
                    "ui_condition_a": {
                        "enabled": True,
                        "signal_expression": deepcopy(expression),
                        "groups": [{"conditions": [{
                            "target": "CURRENT_PRICE",
                            "operator": ">=",
                            "compare_target": "AVG_PRICE",
                        }]}],
                    },
                    "ui_condition_b": {
                        "enabled": True,
                        "signal_expression": deepcopy(expression),
                        "groups": [{"conditions": [{
                            "target": "CURRENT_PRICE",
                            "operator": ">=",
                            "compare_target": "AVG_PRICE",
                        }]}],
                    },
                    "ui_condition_c": {
                        "enabled": True,
                        "signal_expression": deepcopy(expression),
                        "groups": [{"conditions": [{
                            "target": "ORDER_PRICE",
                            "operator": ">=",
                            "compare_target": "CURRENT_PRICE",
                        }]}],
                    },
                },
            },
        })

        self.assertEqual(SIGNAL_PARITY_PASS, report["status"])
        self.assertTrue(report["signal_backtest_authorized"])
        self.assertEqual([], report["issues"])

    def test_validation_projection_preserves_signal_rules_but_strips_execution(self):
        rules = {
            "enabled": True,
            "buy": {
                "enabled": True,
                "filters": {
                    "price_compare": {
                        "enabled": True,
                        "conditions_logic": "OR",
                        "conditions": [{
                            "target": "AVG_PRICE",
                            "operator": "<=",
                            "compare_target": "ORDER_PRICE",
                        }],
                    },
                    "composite": {
                        "enabled": True,
                        "groups": [{
                            "filters": ["price_compare"],
                        }],
                    },
                },
                "execution": {
                    "base": {"execution_mode": "MULTI_HOGA"},
                },
            },
            "sell": {
                "enabled": True,
                "signals": {
                    "profit_rate_sell": {
                        "enabled": True,
                        "profit_rate_percent": 1.5,
                        "basis": "average_price",
                    },
                    "legacy_signal": {
                        "enabled": True,
                        "groups": [],
                    },
                },
                "method": {
                    "setting_a": {"enabled": True},
                },
            },
            "order_policy": {"enabled": True},
            "cancel_policy": {"enabled": True},
            "signal_runtime_policy": {"duplicate_priority": "TRAILING"},
        }
        projected = project_signal_validation_rules(rules)

        self.assertIn("price_compare", projected["buy"]["filters"])
        self.assertEqual(
            "SIGNAL_PRICE",
            projected["buy"]["filters"]["price_compare"]["conditions"][0]["compare_target"],
        )
        self.assertIn(
            "price_compare",
            projected["buy"]["filters"]["composite"]["groups"][0]["filters"],
        )
        self.assertNotIn("execution", projected["buy"])
        self.assertIn("profit_rate_sell", projected["sell"]["signals"])
        self.assertIn("legacy_signal", projected["sell"]["signals"])
        self.assertNotIn("method", projected["sell"])
        self.assertNotIn("order_policy", projected)
        self.assertNotIn("cancel_policy", projected)
        self.assertNotIn("signal_runtime_policy", projected)

    def test_unsaved_profit_rate_sell_preview_survives_projection(self):
        rules = {
            "sell": {
                "signals": {
                    "profit_rate_sell": {
                        "enabled": False,
                        "profit_rate_percent": 1.0,
                        "basis": "average_price",
                    },
                },
            },
            "indicator_follow_rule_preview": {
                "candidates": {
                    "sell": {
                        "set_signal_candidates": {
                            "sell.signals.profit_rate_sell": {
                                "candidate_type": "set_signal",
                                "path": "sell.signals.profit_rate_sell",
                                "value": {
                                    "enabled": True,
                                    "profit_rate_percent": 2.5,
                                    "basis": "average_price",
                                },
                            },
                        },
                    },
                },
            },
        }
        projected = project_signal_validation_rules(rules)
        self.assertEqual(
            {
                "enabled": True,
                "profit_rate_percent": 2.5,
                "basis": "average_price",
            },
            projected["sell"]["signals"]["profit_rate_sell"],
        )
        self.assertNotIn("indicator_follow_rule_preview", projected)

    def test_signal_rule_support_normalizes_buy_legacy_but_blocks_sell_order_price(self):
        supported = signal_rule_support_report({
            "sell": {
                "signals": {
                    "profit_rate_sell": {
                        "enabled": True,
                        "profit_rate_percent": 1.0,
                        "basis": "average_price",
                    },
                },
            },
        })
        self.assertEqual(SIGNAL_PARITY_PASS, supported["status"])
        self.assertTrue(supported["signal_backtest_authorized"])

        buy_legacy = signal_rule_support_report({
            "buy": {
                "filters": {
                    "price_compare": {
                        "conditions": [{
                            "target": "ORDER_PRICE",
                            "operator": ">=",
                            "compare_target": "AVG_PRICE",
                        }],
                    },
                },
            },
        })
        self.assertEqual(SIGNAL_PARITY_PASS, buy_legacy["status"])
        self.assertTrue(buy_legacy["signal_backtest_authorized"])

        blocked = signal_rule_support_report({
            "buy": {
                "filters": {
                    "price_compare": {
                        "conditions": [{
                            "target": "ORDER_PRICE",
                            "operator": ">=",
                            "compare_target": "AVG_PRICE",
                        }],
                    },
                },
            },
            "sell": {
                "signals": {
                    "ui_condition_c": {
                        "groups": [{
                            "conditions": [{
                                "target": "ORDER_PRICE",
                                "operator": ">=",
                                "compare_target": "CURRENT_PRICE",
                            }],
                        }],
                    },
                },
            },
        })
        self.assertEqual(SIGNAL_PARITY_FAIL, blocked["status"])
        self.assertFalse(blocked["signal_backtest_authorized"])
        self.assertEqual("ORDER_PRICE", blocked["issues"][0]["target"])


    def test_signal_rule_support_ignores_validation_visualization_only_prices(self):
        report = signal_rule_support_report({
            "sell": {
                "signals": {
                    "ui_condition_a": {
                        "enabled": True,
                        "groups": [{
                            "conditions": [{
                                "target": "CURRENT_PRICE",
                                "operator": ">=",
                                "compare_target": "AVG_PRICE",
                            }],
                        }],
                    },
                },
            },
            "validation_visualization_rules": {
                "sell": {
                    "signals": {
                        "ui_condition_c": {
                            "enabled": True,
                            "groups": [{
                                "conditions": [{
                                    "target": "ORDER_PRICE",
                                    "operator": "PERCENT_GAP",
                                    "compare_target": "CURRENT_PRICE",
                                }],
                            }],
                        },
                    },
                },
            },
        })
        self.assertEqual(SIGNAL_PARITY_PASS, report["status"])
        self.assertTrue(report["signal_backtest_authorized"])
        self.assertEqual([], report["issues"])

    def test_signal_rule_support_still_blocks_real_signal_order_price_with_visualization(self):
        report = signal_rule_support_report({
            "sell": {
                "signals": {
                    "ui_condition_c": {
                        "enabled": True,
                        "groups": [{
                            "conditions": [{
                                "target": "ORDER_PRICE",
                                "operator": ">=",
                                "compare_target": "CURRENT_PRICE",
                            }],
                        }],
                    },
                },
            },
            "validation_visualization_rules": {
                "sell": {
                    "signals": {
                        "ui_condition_c": {
                            "groups": [{
                                "conditions": [{
                                    "target": "CURRENT_PRICE",
                                    "operator": ">=",
                                    "compare_target": "AVG_PRICE",
                                }],
                            }],
                        },
                    },
                },
            },
        })
        self.assertEqual(SIGNAL_PARITY_FAIL, report["status"])
        self.assertFalse(report["signal_backtest_authorized"])
        self.assertEqual("ORDER_PRICE", report["issues"][0]["target"])


if __name__ == "__main__":
    unittest.main()
