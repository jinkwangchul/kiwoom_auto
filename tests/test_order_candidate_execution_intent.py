from __future__ import annotations

import unittest
from unittest import mock

import order_candidate_engine as engine
import order_queue


class OrderCandidateExecutionIntentTest(unittest.TestCase):
    def _intent(self, **overrides):
        value = {
            "side": "BUY",
            "buy_phase": "REPEAT",
            "buy_round": 2,
            "budget": 150000,
            "quantity": 3,
            "price_basis": "ORDER_PRICE",
            "price": 50000,
            "hoga": "LIMIT",
            "hoga_mode": "SINGLE",
            "reason": "INDICATOR_FOLLOW_BUY_SIGNAL",
            "source_signal_id": "signal-1",
            "routine_type": "INDICATOR_FOLLOW",
            "routine_instance_id": "routine-1",
            "cycle_identity": "cycle-1",
            "confirmed_previous_round": 1,
            "unresolved": False,
        }
        value.update(overrides)
        return value

    def test_intent_quantity_and_budget_are_not_recalculated(self):
        intent = self._intent()

        result = engine.build_order_candidate_from_execution_intent(intent)

        self.assertEqual("CANDIDATE_READY", result["candidate_status"])
        self.assertEqual(3, result["quantity"])
        self.assertEqual(150000, result["amount"])
        self.assertEqual("execution_intent", result["budget_source"])
        self.assertEqual(intent, result["order_intent"])
        self.assertFalse(result["execution_enabled"])

    def test_phase_and_round_contract_is_validated(self):
        result = engine.build_order_candidate_from_execution_intent(
            self._intent(buy_phase="BASE", buy_round=2)
        )

        self.assertEqual("EXECUTION_INTENT_INVALID", result["candidate_status"])
        self.assertIn("EXECUTION_INTENT_PHASE_ROUND_MISMATCH", result["candidate_issues"])

    def test_market_intent_does_not_require_limit_price(self):
        result = engine.build_order_candidate_from_execution_intent(
            self._intent(price_basis="MARKET", price=None, hoga="MARKET")
        )

        self.assertEqual("CANDIDATE_READY", result["candidate_status"])
        self.assertIsNone(result["price"])

    def test_legacy_candidate_path_still_reads_stock_config(self):
        with (
            mock.patch.object(engine, "read_stock_config", return_value={"buy_qty": 2}),
            mock.patch.object(engine, "read_stock_state", return_value={}),
            mock.patch.object(engine, "read_latest_price", return_value=50000),
        ):
            result = engine.build_order_candidate({
                "signal": "BUY",
                "code": "005930",
                "name": "삼성전자",
            })

        self.assertEqual("CANDIDATE_READY", result["candidate_status"])
        self.assertEqual(2, result["quantity"])
        self.assertEqual("entry_quantity", result["budget_source"])
        self.assertFalse(result["execution_enabled"])

    def test_live_same_round_is_duplicate_but_cancelled_round_can_retry(self):
        candidate = {
            "source_signal_id": "signal-2",
            "status": "PENDING",
            "execution_intent": self._intent(),
        }
        live = {
            "source_signal_id": "signal-1",
            "status": "PENDING",
            "execution_intent": self._intent(),
        }
        cancelled = dict(live, status="CANCELLED")

        self.assertEqual(
            "duplicate live routine buy round",
            order_queue._candidate_duplicate_reason(candidate, [live]),
        )
        self.assertIsNone(order_queue._candidate_duplicate_reason(candidate, [cancelled]))


if __name__ == "__main__":
    unittest.main()
