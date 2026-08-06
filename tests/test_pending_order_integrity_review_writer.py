# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from gui_auto_trade_utils import mark_pending_order_integrity_review_required


class PendingOrderIntegrityReviewWriterTest(unittest.TestCase):
    def _stock_dir(self, root: str, state: dict[str, object]) -> Path:
        stock_dir = Path(root) / "000001_TEST"
        stock_dir.mkdir(parents=True)
        (stock_dir / "state.json").write_text(
            json.dumps(state, ensure_ascii=False),
            encoding="utf-8",
        )
        return stock_dir

    def test_moves_integrity_failure_to_review_and_disables_trading(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            stock_dir = self._stock_dir(
                root,
                {
                    "status": "STOPPED",
                    "trade_enabled": True,
                    "buy_enabled": True,
                    "sell_enabled": True,
                    "extension": "preserved",
                },
            )
            with (
                patch("gui_auto_trade_utils.now_text", return_value="2026-08-06 12:34:56"),
                patch("gui_auto_trade_utils.append_stock_log") as stock_log,
            ):
                ok = mark_pending_order_integrity_review_required(
                    "루틴A",
                    stock_dir,
                    "000001",
                    "테스트",
                    ["PENDING_ORDER_QTY_MISSING", "PENDING_ORDER_QTY_MISSING"],
                    source="focused-test",
                )

            saved = json.loads((stock_dir / "state.json").read_text(encoding="utf-8"))

        self.assertTrue(ok)
        self.assertEqual("REVIEW_REQUIRED", saved["status"])
        self.assertTrue(saved["review_required"])
        self.assertEqual("PENDING", saved["review_status"])
        self.assertEqual("focused-test", saved["review_location"])
        self.assertEqual(
            "PENDING_ORDER_DATA_INTEGRITY: PENDING_ORDER_QTY_MISSING",
            saved["review_reason"],
        )
        self.assertEqual("2026-08-06 12:34:56", saved["review_entered_at"])
        self.assertFalse(saved["trade_enabled"])
        self.assertFalse(saved["buy_enabled"])
        self.assertFalse(saved["sell_enabled"])
        self.assertEqual("preserved", saved["extension"])
        stock_log.assert_called_once()
        self.assertEqual("ERROR", stock_log.call_args.args[1])

    def test_same_review_reason_is_idempotent(self) -> None:
        reason = "PENDING_ORDER_DATA_INTEGRITY: LEGACY_PENDING_SUMMARY_ONLY"
        with tempfile.TemporaryDirectory() as root:
            stock_dir = self._stock_dir(
                root,
                {
                    "status": "REVIEW_REQUIRED",
                    "review_required": True,
                    "review_reason": reason,
                },
            )
            with (
                patch("gui_auto_trade_utils.write_state_json") as writer,
                patch("gui_auto_trade_utils.append_stock_log") as stock_log,
            ):
                ok = mark_pending_order_integrity_review_required(
                    "루틴A",
                    stock_dir,
                    "000001",
                    "테스트",
                    ["LEGACY_PENDING_SUMMARY_ONLY"],
                    source="focused-test",
                )

        self.assertTrue(ok)
        writer.assert_not_called()
        stock_log.assert_not_called()

    def test_write_failure_returns_false_and_records_critical_log(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            stock_dir = self._stock_dir(root, {"status": "STOPPED"})
            with (
                patch("gui_auto_trade_utils.write_state_json", return_value=False),
                patch("gui_auto_trade_utils.append_stock_log") as stock_log,
            ):
                ok = mark_pending_order_integrity_review_required(
                    "루틴A",
                    stock_dir,
                    "000001",
                    "테스트",
                    ["PENDING_ORDER_QTY_MISSING"],
                    source="focused-test",
                )

        self.assertFalse(ok)
        self.assertEqual("CRITICAL", stock_log.call_args.args[1])

    def test_read_back_failure_returns_false_and_records_critical_log(self) -> None:
        initial_state = {"status": "STOPPED"}
        with tempfile.TemporaryDirectory() as root:
            stock_dir = self._stock_dir(root, initial_state)
            with (
                patch(
                    "gui_auto_trade_utils.read_json_dict",
                    side_effect=[dict(initial_state), {}],
                ),
                patch("gui_auto_trade_utils.write_state_json", return_value=True),
                patch("gui_auto_trade_utils.append_stock_log") as stock_log,
            ):
                ok = mark_pending_order_integrity_review_required(
                    "루틴A",
                    stock_dir,
                    "000001",
                    "테스트",
                    ["PENDING_ORDER_SIDE_UNKNOWN"],
                    source="focused-test",
                )

        self.assertFalse(ok)
        self.assertEqual(2, stock_log.call_count)
        self.assertEqual("CRITICAL", stock_log.call_args_list[-1].args[1])


if __name__ == "__main__":
    unittest.main()
