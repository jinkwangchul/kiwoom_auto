# -*- coding: utf-8 -*-
from __future__ import annotations

from types import SimpleNamespace
import unittest

from gui_operation_ui_context import actionable_current_price


class _DirectOperationContext:
    def __init__(self, price):
        self.price = price

    def fresh_monitoring_market_information_state(self, stock_code: str):
        if stock_code != "005930" or self.price is None:
            return None
        return SimpleNamespace(last_price=self.price)


class _HostOwner:
    def __init__(self, price):
        self.host = _DirectOperationContext(price)

    def main_monitoring_auto_trade_operation_host(self):
        return self.host


class ExecutionPriceSemanticsE0aTest(unittest.TestCase):
    def test_actionable_price_projects_direct_canonical_host_state(self) -> None:
        self.assertEqual(
            71_000,
            actionable_current_price(_DirectOperationContext(71_000), "A005930"),
        )

    def test_actionable_price_projects_main_operation_host(self) -> None:
        self.assertEqual(72_000, actionable_current_price(_HostOwner(72_000), "005930"))

    def test_actionable_price_has_no_reference_fallback(self) -> None:
        self.assertIsNone(actionable_current_price(_DirectOperationContext(None), "005930"))
        self.assertIsNone(actionable_current_price(_DirectOperationContext(0), "005930"))


if __name__ == "__main__":
    unittest.main()
