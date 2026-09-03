# -*- coding: utf-8 -*-
"""Production-contract checks for signal-driven BUY follow-up rounds."""
from __future__ import annotations

from copy import deepcopy
import unittest

from tests.test_indicator_follow_buy_execution_connection import (
    IndicatorFollowBuyExecutionConnectionTest as _BuyHelper,
)


class BuyRepeatProductionCompletionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.helper = _BuyHelper()

    def test_terminal_confirmed_round_creates_only_next_round_on_new_signal(self) -> None:
        result = self.helper._build(cycle=self.helper._cycle(1), price=100)
        self.assertEqual("READY", result["status"])
        intent = result["execution_intent"]
        self.assertEqual("REPEAT", intent["buy_phase"])
        self.assertEqual(2, intent["buy_round"])
        self.assertEqual(1, intent["confirmed_previous_round"])

    def test_open_round_blocks_follow_up_without_retry_or_round_increment(self) -> None:
        result = self.helper._build(cycle=self.helper._cycle(1, pending_buy_rounds=[2]))
        self.assertEqual("BLOCKED", result["status"])
        self.assertEqual("BUY_ROUND_ALREADY_PENDING", result["reason"])
        self.assertIsNone(result["execution_intent"])

    def test_cancelled_no_fill_keeps_confirmed_round(self) -> None:
        result = self.helper._build(cycle=self.helper._cycle(0, pending_buy_rounds=[]), price=100)
        self.assertEqual(1, result["execution_intent"]["buy_round"])
        self.assertEqual("BASE", result["execution_intent"]["buy_phase"])

    def test_active_buy_repeat_remains_fail_closed(self) -> None:
        result = self.helper._build(cycle=self.helper._cycle(1), rules=self.helper._rules(repeat_mode="ACTIVE_BUY"))
        self.assertEqual("BLOCKED", result["status"])
        self.assertEqual("ACTIVE_BUY_NOT_IMPLEMENTED", result["reason"])


if __name__ == "__main__":
    unittest.main()
