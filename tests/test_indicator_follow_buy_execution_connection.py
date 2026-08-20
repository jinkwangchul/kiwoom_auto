# -*- coding: utf-8 -*-
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

import routine_signal_queue
from order_queue import signal_to_order_candidate


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROUTINE_DIR = PROJECT_ROOT / "routines" / "지표추종매매"


def _load_module(filename: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROUTINE_DIR / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(ROUTINE_DIR))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(ROUTINE_DIR))
    return module


bridge = _load_module("routine_buy_execution.py", "indicator_follow_buy_execution_test")


class IndicatorFollowBuyExecutionConnectionTest(unittest.TestCase):
    def _rules(self, *, repeat_mode: str = "ROUND", max_rounds: int | None = None) -> dict:
        repeat = {
            "buy_phase": "REPEAT",
            "starts_from_round": 2,
            "apply_all": True,
            "detail_mode": repeat_mode,
            "round_operator": "ADD",
            "round_budget_value": 1,
            "budget_ratio": 2,
        }
        if max_rounds is not None:
            repeat["max_buy_rounds"] = max_rounds
        return {
            "buy": {
                "execution": {
                    "base": {
                        "buy_phase": "BASE",
                        "buy_round": 1,
                        "hoga_mode": "SINGLE",
                        "order_price_basis": "CURRENT_PRICE",
                        "hoga_up": 0,
                        "hoga_down": 0,
                    },
                    "repeat": repeat,
                }
            }
        }

    def _cycle(self, confirmed: int = 0, **overrides) -> dict:
        amounts = {} if confirmed == 0 else {1: 100.0, confirmed: 100.0}
        value = {
            "status": "resolved",
            "active": confirmed > 0,
            "confirmed_buy_round": confirmed,
            "cumulative_filled_buy_amount": 100.0 * confirmed,
            "base_filled_buy_amount": amounts.get(1, 0.0),
            "last_filled_buy_amount": amounts.get(confirmed, 0.0),
            "filled_buy_amount_by_round": amounts,
            "pending_buy_rounds": [],
            "pending_buy_order_identities": [],
            "cycle_identity": "CYCLE_1" if confirmed else None,
        }
        value.update(overrides)
        return value

    def _build(self, *, cycle=None, config=None, rules=None, price=100.0, account_budget=None) -> dict:
        context = {
            "cycle": cycle if cycle is not None else self._cycle(),
            "stock_config": config or {"trade_amount_type": "QUANTITY", "buy_qty": 1},
            "rules": rules if rules is not None else self._rules(),
            "current_price": price,
            "routine_instance_id": "INSTANCE_A",
        }
        if account_budget is not None:
            context["account_budget"] = account_budget
        return bridge.build_indicator_follow_buy_intent(
            buy_signal_result={"signal": "BUY", "reason": "indicator"},
            context=context,
        )

    def test_account_total_budget_blocks_4000_plus_100_before_queueing(self) -> None:
        result = self._build(
            price=100,
            account_budget={
                "account_no": "12345678",
                "system_total_budget": 4_000,
                "account_consumed_amount": 4_000,
            },
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertEqual("SYSTEM_TOTAL_BUDGET_EXCEEDED", result["reason"])
        evidence = result["preview"]["execution_policy_result"]["evidence"]
        self.assertEqual(4_100, evidence["projected_account_consumption"])
        self.assertEqual(4_000, evidence["system_total_budget"])

    def test_base_quantity_creates_round_one_intent(self) -> None:
        result = self._build(config={"trade_amount_type": "QUANTITY", "buy_qty": 2}, price=80000)

        self.assertEqual("READY", result["status"])
        self.assertEqual("BASE", result["execution_intent"]["buy_phase"])
        self.assertEqual(1, result["execution_intent"]["buy_round"])
        self.assertEqual(2, result["execution_intent"]["quantity"])
        self.assertEqual(160000, result["execution_intent"]["budget"])
        self.assertEqual(0, result["execution_intent"]["confirmed_previous_round"])

    def test_base_amount_uses_only_whole_shares(self) -> None:
        result = self._build(
            config={"trade_amount_type": "AMOUNT", "buy_amount": 120000},
            price=80000,
        )

        self.assertEqual("READY", result["status"])
        self.assertEqual(1, result["execution_intent"]["quantity"])
        self.assertEqual(80000, result["execution_intent"]["budget"])

    def test_confirmed_fill_drives_repeat_round_and_actual_budget(self) -> None:
        result = self._build(cycle=self._cycle(1), price=100)

        self.assertEqual("READY", result["status"])
        self.assertEqual("REPEAT", result["execution_intent"]["buy_phase"])
        self.assertEqual(2, result["execution_intent"]["buy_round"])
        self.assertEqual(2, result["execution_intent"]["quantity"])
        self.assertEqual(200, result["execution_intent"]["budget"])

    def test_live_same_round_is_blocked_and_cancelled_round_can_retry(self) -> None:
        blocked = self._build(cycle=self._cycle(1, pending_buy_rounds=[2]))
        retry = self._build(cycle=self._cycle(1, pending_buy_rounds=[]))

        self.assertEqual("BUY_ROUND_ALREADY_PENDING", blocked["reason"])
        self.assertEqual("READY", retry["status"])
        self.assertEqual(2, retry["execution_intent"]["buy_round"])

    def test_partial_prior_buy_blocks_next_round_until_order_finishes(self) -> None:
        result = self._build(cycle=self._cycle(1, pending_buy_rounds=[1]))

        self.assertEqual("BLOCKED", result["status"])
        self.assertEqual("BUY_ORDER_STILL_PENDING", result["reason"])

    def test_max_round_active_buy_unresolved_and_pending_rules_fail_closed(self) -> None:
        exceeded = self._build(cycle=self._cycle(1), rules=self._rules(max_rounds=1))
        active_buy = self._build(cycle=self._cycle(1), rules=self._rules(repeat_mode="ACTIVE_BUY"))
        unresolved = self._build(cycle={"status": "unresolved", "unresolved_reason": "LEDGER_MISMATCH"})
        pending_only = self._build(rules={"indicator_follow_rule_pending": {"candidates": {"execution": {}}}})

        self.assertEqual("BUY_ROUND_COUNT_EXCEEDED", exceeded["reason"])
        self.assertEqual("ACTIVE_BUY_NOT_IMPLEMENTED", active_buy["reason"])
        self.assertEqual("LEDGER_MISMATCH", unresolved["reason"])
        self.assertEqual("APPROVED_BASE_EXECUTION_RULE_MISSING", pending_only["reason"])

    def test_routine_signal_intent_reaches_common_candidate_without_recalculation(self) -> None:
        routine = _load_module("routine.py", "indicator_follow_buy_execution_routine_test")
        routine.evaluate_indicator_follow_routine = lambda candles, config, context: {"raw": True}
        routine.signal_to_dict = lambda signal: {"signal": "BUY", "reason": "indicator"}
        result = routine.evaluate({
            "candles": [],
            "rules": self._rules(),
            "cycle": self._cycle(),
            "stock_config": {"trade_amount_type": "QUANTITY", "buy_qty": 3},
            "current_price": 50000,
            "routine_instance_id": "INSTANCE_A",
        })

        with tempfile.TemporaryDirectory() as tmpdir:
            queue_path = Path(tmpdir) / "routine_signals.json"
            with mock.patch.object(routine_signal_queue, "QUEUE_PATH", queue_path):
                queued = routine_signal_queue.enqueue_routine_signal(
                    result,
                    routine="지표추종매매",
                    code="005930",
                    name="삼성전자",
                    tick_key="TICK_1",
                )
            signal = json.loads(queue_path.read_text(encoding="utf-8"))["signals"][0]
            candidate = signal_to_order_candidate(signal, 0)

        self.assertEqual("queued", queued["status"])
        self.assertEqual("CANDIDATE_READY", candidate["candidate_status"])
        self.assertEqual(3, candidate["quantity"])
        self.assertEqual(150000, candidate["amount"])
        self.assertEqual(signal["execution_intent"], candidate["execution_intent"])


if __name__ == "__main__":
    unittest.main()
