# -*- coding: utf-8 -*-

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
import unittest

from routine_instance_registry import default_routine_limit_response_policy
from routine_limit_response_ownership_service import INTENT_EARLY_CLOSE, INTENT_IMMEDIATE
from routine_limit_response_service import project_routine_limit_policy, select_routine_limit_candidate


def pnl(profit=0, rate=0, cost=100):
    return {"available": True, "cumulative_profit": profit, "cumulative_rate": rate, "open_cost": cost}


def candidate(code):
    return {
        "stock_code": code,
        "is_auto_trade_target": True,
        "position": {"code": code, "position_status": "OPEN", "quantity": 1},
        "state": {},
        "config": {},
        "orders": [],
    }


class RoutineLimitResponsePolicyTests(unittest.TestCase):
    def project(self, principal, *, policy=None, snapshots=None, limit=100):
        return project_routine_limit_policy(
            policy=policy or default_routine_limit_response_policy(),
            invested_principal=principal,
            buy_limit_amount=limit,
            pnl_by_stock=snapshots if snapshots is not None else {"005930": pnl()},
        )

    def test_general_boundary_is_strict(self) -> None:
        self.assertEqual("", self.project(100)["effective_response_intent"])
        self.assertEqual(INTENT_EARLY_CLOSE, self.project(101)["effective_response_intent"])
        policy = default_routine_limit_response_policy()
        policy["strategies"]["unified"]["response_mode"] = "즉시청산"
        self.assertEqual(INTENT_IMMEDIATE, self.project(101, policy=policy)["effective_response_intent"])

    def test_segment_boundaries_use_decimal_and_strict_greater_than(self) -> None:
        policy = default_routine_limit_response_policy()
        policy["strategies"]["unified"]["response_mode"] = "구간마감"
        self.assertEqual("", self.project(90, policy=policy)["effective_response_intent"])
        self.assertEqual(INTENT_EARLY_CLOSE, self.project(91, policy=policy)["effective_response_intent"])
        self.assertEqual(INTENT_EARLY_CLOSE, self.project(100, policy=policy)["effective_response_intent"])
        result = self.project(101, policy=policy)
        self.assertEqual(INTENT_IMMEDIATE, result["effective_response_intent"])
        self.assertIsInstance(result["usage_percent"], Decimal)

    def test_segmented_strategy_uses_confirmable_total_profit(self) -> None:
        policy = default_routine_limit_response_policy()
        policy["application_mode"] = "SEGMENTED"
        nonnegative = self.project(101, policy=policy, snapshots={"005930": pnl(10), "000660": pnl(-10)})
        negative = self.project(101, policy=policy, snapshots={"005930": pnl(9), "000660": pnl(-10)})
        self.assertEqual("profit", nonnegative["selected_strategy"])
        self.assertEqual("loss", negative["selected_strategy"])
        self.assertFalse(self.project(101, policy=policy, snapshots={"005930": {"available": False}})["available"])

    def test_factor_mapping_and_deterministic_sort(self) -> None:
        policies = []
        for factor in ("손익금액", "손익비율", "투입금액"):
            policy = default_routine_limit_response_policy()
            policy["strategies"]["unified"].update(evaluation_factor=factor, direction="낮은순")
            policies.append(policy)
        snapshots = {"005930": pnl(20, 2, 200), "000660": pnl(10, 1, 100)}
        for policy in policies:
            with self.subTest(factor=policy["strategies"]["unified"]["evaluation_factor"]):
                projection = self.project(101, policy=policy, snapshots=snapshots)
                selected = select_routine_limit_candidate(projection=projection, candidates=[candidate("005930"), candidate("000660")])
                self.assertEqual("000660", selected["selected_stock_code"])

        tied = self.project(101, snapshots={"005930": pnl(10), "000660": pnl(10)})
        selected = select_routine_limit_candidate(projection=tied, candidates=[candidate("005930"), candidate("000660")])
        self.assertEqual("000660", selected["selected_stock_code"])

    def test_duplicate_candidate_and_malformed_policy_fail_closed(self) -> None:
        projection = self.project(101)
        self.assertEqual("DUPLICATE_STOCK_IDENTITY", select_routine_limit_candidate(projection=projection, candidates=[candidate("005930"), candidate("005930")])["reason"])
        malformed = deepcopy(default_routine_limit_response_policy())
        malformed["application_mode"] = "UNKNOWN"
        self.assertFalse(self.project(101, policy=malformed)["available"])


if __name__ == "__main__":
    unittest.main()
