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


class _AuthorityOperationContext:
    def __init__(self, evidence_price, legacy_price=999_999):
        self.evidence_price = evidence_price
        self.legacy_price = legacy_price
        self.evidence_calls = []
        self.legacy_calls = []

    def production_current_price_evidence(self, stock_code: str):
        self.evidence_calls.append(stock_code)
        if stock_code != "005930" or self.evidence_price is None:
            return None
        return SimpleNamespace(current_price=self.evidence_price)

    def fresh_monitoring_market_information_state(self, stock_code: str):
        self.legacy_calls.append(stock_code)
        return SimpleNamespace(last_price=self.legacy_price)


class _HostOwner:
    def __init__(self, price):
        self.host = _DirectOperationContext(price)

    def main_monitoring_auto_trade_operation_host(self):
        return self.host


class _AuthorityHostOwner:
    def __init__(self, evidence_price, legacy_price=999_999):
        self.host = _AuthorityOperationContext(evidence_price, legacy_price)

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

    def test_actionable_price_prefers_authorized_production_evidence(self) -> None:
        context = _AuthorityOperationContext(81_000, legacy_price=71_000)

        self.assertEqual(81_000, actionable_current_price(context, "005930"))
        self.assertEqual(["005930"], context.evidence_calls)
        self.assertEqual([], context.legacy_calls)

    def test_missing_authority_evidence_does_not_fallback_to_legacy_state(self) -> None:
        context = _AuthorityOperationContext(None, legacy_price=71_000)

        self.assertIsNone(actionable_current_price(context, "005930"))
        self.assertEqual(["005930"], context.evidence_calls)
        self.assertEqual([], context.legacy_calls)

    def test_main_owner_uses_operation_host_authority_evidence(self) -> None:
        owner = _AuthorityHostOwner(82_000, legacy_price=72_000)

        self.assertEqual(82_000, actionable_current_price(owner, "005930"))
        self.assertEqual(["005930"], owner.host.evidence_calls)
        self.assertEqual([], owner.host.legacy_calls)


if __name__ == "__main__":
    unittest.main()
