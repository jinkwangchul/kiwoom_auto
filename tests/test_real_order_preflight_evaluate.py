# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest

from real_order_preflight import evaluate_real_order_preflight


class RealOrderPreflightEvaluateTests(unittest.TestCase):
    def _order(self, *, status: str = "EXECUTABLE", execution_enabled: bool = True) -> dict:
        return {
            "id": "ORDER_1",
            "status": status,
            "side": "BUY",
            "quantity": 10,
            "order_type": "BUY_SIGNAL_CANDIDATE",
            "execution_enabled": execution_enabled,
        }

    def _guard(self, **overrides: object) -> dict:
        guard = {
            "real_trade_enabled": True,
            "kiwoom_logged_in": True,
            "account_selected": True,
            "operator_confirmed": True,
            "account_no": "12345678",
        }
        guard.update(overrides)
        return guard

    def test_executable_with_all_guard_passes_returns_real_ready(self) -> None:
        result = evaluate_real_order_preflight(self._order(), self._guard())

        self.assertEqual("REAL_READY", result.get("real_preflight_status"))

    def test_executable_with_real_trade_disabled_blocks(self) -> None:
        result = evaluate_real_order_preflight(
            self._order(),
            self._guard(real_trade_enabled=False),
        )

        self.assertEqual("BLOCKED_REAL", result.get("real_preflight_status"))

    def test_executable_without_operator_confirmation_blocks(self) -> None:
        result = evaluate_real_order_preflight(
            self._order(),
            self._guard(operator_confirmed=False),
        )

        self.assertEqual("BLOCKED_REAL", result.get("real_preflight_status"))

    def test_executable_without_kiwoom_login_blocks(self) -> None:
        result = evaluate_real_order_preflight(
            self._order(),
            self._guard(kiwoom_logged_in=False),
        )

        self.assertEqual("BLOCKED_REAL", result.get("real_preflight_status"))

    def test_executable_without_account_selection_blocks(self) -> None:
        result = evaluate_real_order_preflight(
            self._order(),
            self._guard(account_selected=False),
        )

        self.assertEqual("BLOCKED_REAL", result.get("real_preflight_status"))

    def test_executable_without_account_number_blocks(self) -> None:
        result = evaluate_real_order_preflight(
            self._order(),
            self._guard(account_no=""),
        )

        self.assertEqual("BLOCKED_REAL", result.get("real_preflight_status"))

    def test_non_executable_statuses_are_ignored(self) -> None:
        for status in ("PENDING", "APPROVED", "BLOCKED"):
            with self.subTest(status=status):
                result = evaluate_real_order_preflight(
                    self._order(status=status),
                    self._guard(),
                )
                self.assertEqual("IGNORED", result.get("real_preflight_status"))


if __name__ == "__main__":
    unittest.main()
