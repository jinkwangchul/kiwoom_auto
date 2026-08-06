# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from gui_order_utils import pending_order_integrity_issue_codes


class PendingOrderIntegrityIssueCodesTest(unittest.TestCase):
    def _issues(
        self,
        orders: list[dict[str, object]],
        state: dict[str, object] | None = None,
    ) -> list[str]:
        with tempfile.TemporaryDirectory() as temp:
            stock_dir = Path(temp)
            (stock_dir / "orders.json").write_text(
                json.dumps({"orders": orders}, ensure_ascii=False),
                encoding="utf-8",
            )
            return pending_order_integrity_issue_codes(stock_dir, state or {})

    def test_valid_pending_order_has_no_integrity_issue(self) -> None:
        issues = self._issues(
            [{"status": "OPEN", "side": "BUY", "order_qty": 5, "filled_qty": 2}]
        )

        self.assertEqual([], issues)

    def test_pending_order_without_quantity_reports_missing_quantity(self) -> None:
        issues = self._issues([{"status": "OPEN", "side": "SELL"}])

        self.assertEqual(["PENDING_ORDER_QTY_MISSING"], issues)

    def test_pending_order_with_unknown_side_reports_unknown_side(self) -> None:
        issues = self._issues(
            [{"status": "OPEN", "side": "UNKNOWN", "pending_qty": 1}]
        )

        self.assertEqual(["PENDING_ORDER_SIDE_UNKNOWN"], issues)

    def test_legacy_pending_summary_without_side_quantities_reports_issue(self) -> None:
        issues = self._issues(
            [],
            {
                "pending_order": True,
                "pending_qty": 3,
                "buy_pending_qty": 0,
                "sell_pending_qty": 0,
            },
        )

        self.assertEqual(["LEGACY_PENDING_SUMMARY_ONLY"], issues)

    def test_duplicate_issue_codes_are_returned_once_in_detection_order(self) -> None:
        issues = self._issues(
            [
                {"status": "OPEN", "side": "UNKNOWN"},
                {"status": "OPEN", "side": "UNKNOWN"},
            ]
        )

        self.assertEqual(
            ["PENDING_ORDER_SIDE_UNKNOWN", "PENDING_ORDER_QTY_MISSING"],
            issues,
        )


if __name__ == "__main__":
    unittest.main()
