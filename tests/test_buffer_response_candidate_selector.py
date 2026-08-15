# -*- coding: utf-8 -*-

from __future__ import annotations

import inspect
import unittest
from decimal import Decimal

import buffer_response_candidate_selector as selector
import buffer_response_policy_projection as policy


class _TextControl:
    def __init__(self, text: str) -> None:
        self.value = text

    def currentText(self) -> str:
        return self.value

    def text(self) -> str:
        return self.value


class _Surface:
    def __init__(self, factor: str, direction: str) -> None:
        self.buffer_close_ratio_combo = _TextControl("80%")
        self.strategy_rows = {
            "unified": [(_TextControl(factor), _TextControl(direction))],
        }
        self.strategy_action_badges = {"unified": _TextControl("구간마감")}

    def isVisible(self) -> bool:
        return True

    def application_mode(self) -> str:
        return "UNIFIED"


def _pnl(profit: object, rate: object, open_cost: object, reserve: object = 0):
    return {
        "available": True,
        "cumulative_profit": profit,
        "cumulative_rate": rate,
        "open_cost": open_cost,
        "open_buy_reservation": reserve,
    }


def _policy(factor: str, direction: str, pnl_by_stock):
    return policy.project_buffer_response_policy(
        settings_surface=_Surface(factor, direction),
        pnl_by_stock=pnl_by_stock,
        budget_activity={"available": True, "entry_amount": 1, "entry_ratio": 50},
    )


def _candidate(code: str, **overrides):
    candidate = {
        "stock_code": code,
        "stock_dir": f"stocks/{code}",
        "routine_instance_id": f"routine-{code}",
        "is_auto_trade_target": True,
        "position": {
            "code": code,
            "position_status": "OPEN",
            "quantity": 10,
            "cost_basis": 1000,
        },
        "state": {"status": "RUNNING"},
        "config": {"operation_excluded": False},
        "orders": [],
    }
    candidate.update(overrides)
    return candidate


class BufferResponseCandidateSelectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.pnl = {
            "000001": _pnl(300, "3.0", 2000),
            "000002": _pnl(-100, "-5.0", 5000),
            "000003": _pnl(50, "1.5", 1000),
        }
        self.candidates = [_candidate(code) for code in self.pnl]

    def test_all_six_single_filter_combinations(self) -> None:
        cases = (
            ("손익금액", "높은순", "000001", Decimal("300")),
            ("손익금액", "낮은순", "000002", Decimal("-100")),
            ("손익비율", "높은순", "000001", Decimal("3.0")),
            ("손익비율", "낮은순", "000002", Decimal("-5.0")),
            ("투입금액", "높은순", "000002", Decimal("5000")),
            ("투입금액", "낮은순", "000003", Decimal("1000")),
        )
        for factor, direction, expected_code, expected_value in cases:
            with self.subTest(factor=factor, direction=direction):
                result = selector.select_buffer_response_candidate(
                    policy_projection=_policy(factor, direction, self.pnl),
                    candidates=self.candidates,
                )
                self.assertTrue(result["selectable"])
                self.assertEqual(expected_code, result["selected_stock_code"])
                self.assertEqual(expected_value, result["selected_evaluation_value"])
                self.assertEqual(3, result["candidate_count"])

    def test_zero_holding_and_non_open_position_are_excluded(self) -> None:
        zero = _candidate("000001")
        zero["position"] = {"code": "000001", "position_status": "CLOSED", "quantity": 0}
        result = selector.select_buffer_response_candidate(
            policy_projection=_policy("손익금액", "높은순", self.pnl),
            candidates=[zero, _candidate("000002")],
        )
        self.assertEqual("000002", result["selected_stock_code"])
        self.assertEqual("POSITION_NOT_OPEN", result["excluded"]["000001"])

    def test_existing_early_auto_and_liquidation_evidence_are_excluded(self) -> None:
        early = _candidate("000001", state={
            "status": "RUNNING",
            "early_close_requested_at": "2099-01-01 00:00:00",
        })
        auto = _candidate("000002", state={"status": "AUTO_CLOSING"})
        liquidating = _candidate("000003", state={"status": "LIQUIDATING"})
        result = selector.select_buffer_response_candidate(
            policy_projection=_policy("손익금액", "높은순", self.pnl),
            candidates=[early, auto, liquidating],
        )
        self.assertFalse(result["selectable"])
        self.assertEqual("EARLY_CLOSE_ALREADY_REQUESTED", result["excluded"]["000001"])
        self.assertEqual("CLOSE_OR_LIQUIDATION_IN_PROGRESS", result["excluded"]["000002"])
        self.assertEqual("CLOSE_OR_LIQUIDATION_IN_PROGRESS", result["excluded"]["000003"])

    def test_active_sell_order_is_excluded_but_active_buy_is_not(self) -> None:
        sell = _candidate("000001", orders=[{
            "code": "000001",
            "side": "SELL",
            "status": "BROKER_ACCEPTED",
            "remaining_quantity": 2,
        }])
        buy = _candidate("000002", orders=[{
            "code": "000002",
            "side": "BUY",
            "status": "BROKER_ACCEPTED",
            "remaining_quantity": 100,
        }])
        result = selector.select_buffer_response_candidate(
            policy_projection=_policy("손익금액", "높은순", self.pnl),
            candidates=[sell, buy],
        )
        self.assertEqual("000002", result["selected_stock_code"])
        self.assertEqual("ACTIVE_SELL_ORDER", result["excluded"]["000001"])

    def test_uncertain_evaluation_and_caller_ownership_are_excluded(self) -> None:
        projected = _policy("손익금액", "높은순", self.pnl)
        projected["candidate_factor_values"] = {"000001": Decimal("300")}
        result = selector.select_buffer_response_candidate(
            policy_projection=projected,
            candidates=self.candidates,
            already_buffer_selected={"000001"},
        )
        self.assertFalse(result["selectable"])
        self.assertEqual("ALREADY_BUFFER_SELECTED", result["excluded"]["000001"])
        self.assertEqual("EVALUATION_VALUE_UNAVAILABLE", result["excluded"]["000002"])
        self.assertEqual("EVALUATION_VALUE_UNAVAILABLE", result["excluded"]["000003"])

    def test_no_candidate_one_candidate_and_exactly_one_selection(self) -> None:
        projected = _policy("손익금액", "높은순", self.pnl)
        none_result = selector.select_buffer_response_candidate(
            policy_projection=projected,
            candidates=[],
        )
        self.assertFalse(none_result["selectable"])
        self.assertEqual("NO_ELIGIBLE_CANDIDATE", none_result["reason"])

        one_result = selector.select_buffer_response_candidate(
            policy_projection=projected,
            candidates=[_candidate("000003")],
        )
        self.assertEqual("000003", one_result["selected_stock_code"])
        self.assertEqual(1, one_result["candidate_count"])

        many_result = selector.select_buffer_response_candidate(
            policy_projection=projected,
            candidates=self.candidates,
        )
        self.assertIsInstance(many_result["selected_stock"], dict)
        self.assertEqual("000001", many_result["selected_stock_code"])

    def test_equal_values_use_stock_code_only_as_deterministic_tie_break(self) -> None:
        pnl = {
            "000003": _pnl(10, 1, 100),
            "000001": _pnl(10, 9, 9000),
            "000002": _pnl(10, -2, 1),
        }
        result = selector.select_buffer_response_candidate(
            policy_projection=_policy("손익금액", "높은순", pnl),
            candidates=[_candidate("000003"), _candidate("000002"), _candidate("000001")],
        )
        self.assertEqual("000001", result["selected_stock_code"])

    def test_invested_amount_is_open_cost_and_ignores_buy_reservation(self) -> None:
        pnl = {
            "000001": _pnl(0, 0, 100, reserve=999999),
            "000002": _pnl(0, 0, 200, reserve=0),
            "000003": _pnl(0, 0, 50, reserve=5000000),
        }
        result = selector.select_buffer_response_candidate(
            policy_projection=_policy("투입금액", "높은순", pnl),
            candidates=[_candidate(code) for code in pnl],
        )
        self.assertEqual("000002", result["selected_stock_code"])
        self.assertEqual(Decimal("200"), result["selected_evaluation_value"])

    def test_non_executable_stage_two_projection_selects_nothing(self) -> None:
        result = selector.select_buffer_response_candidate(
            policy_projection={
                "available": False,
                "applicable": False,
                "evaluation_factor": "손익금액",
                "direction": "높은순",
                "reason": "BUFFER_NOT_ENTERED",
            },
            candidates=self.candidates,
        )
        self.assertFalse(result["selectable"])
        self.assertEqual("POLICY_PROJECTION_NOT_APPLICABLE", result["reason"])

    def test_selector_has_no_writer_or_execution_dependency(self) -> None:
        source = inspect.getsource(selector)
        for forbidden in (
            "close_intent_service",
            "operation_command_service",
            "auto_trade_order_execution_boundary",
            "runtime_atomic_writer",
            "write_json",
            "SendOrder",
            "chejan_event_recorder",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
