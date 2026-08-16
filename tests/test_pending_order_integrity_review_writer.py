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
        self.assertEqual("미체결 데이터 오류", saved["review_reason"])
        self.assertIn("PENDING_ORDER_QTY_MISSING", saved["review_detail"])
        self.assertEqual("2026-08-06 12:34:56", saved["review_entered_at"])
        self.assertFalse(saved["trade_enabled"])
        self.assertNotIn("buy_enabled", saved)
        self.assertNotIn("sell_enabled", saved)
        self.assertEqual("preserved", saved["extension"])
        stock_log.assert_called_once()
        self.assertEqual("ERROR", stock_log.call_args.args[1])

    def test_existing_legacy_permission_keys_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            stock_dir = self._stock_dir(
                root,
                {
                    "status": "STOPPED",
                    "trade_enabled": True,
                    "buy_enabled": True,
                    "sell_enabled": False,
                },
            )
            with (
                patch("gui_auto_trade_utils.now_text", return_value="2026-08-06 12:34:56"),
                patch("gui_auto_trade_utils.append_stock_log"),
            ):
                ok = mark_pending_order_integrity_review_required(
                    "루틴A",
                    stock_dir,
                    "000001",
                    "테스트",
                    ["PENDING_ORDER_QTY_MISSING"],
                    source="focused-test",
                )
            saved = json.loads(
                (stock_dir / "state.json").read_text(encoding="utf-8")
            )

        self.assertTrue(ok)
        self.assertIs(saved["buy_enabled"], True)
        self.assertIs(saved["sell_enabled"], False)
        self.assertEqual("REVIEW_REQUIRED", saved["status"])
        self.assertIs(saved["trade_enabled"], False)

    def test_production_sources_share_reason_and_use_actual_detection_location(self) -> None:
        cases = {
            "종목등록 창 미체결 데이터 무결성 오류": "종목 등록",
            "등록해제 미체결 데이터 무결성 오류": "종목 해제",
            "루틴 이동 미체결 데이터 무결성 오류": "루틴 등록",
            "루틴 해제 미체결 데이터 무결성 오류": "루틴 해제",
        }
        for index, (source, expected_location) in enumerate(cases.items(), start=1):
            with self.subTest(source=source), tempfile.TemporaryDirectory() as root:
                stock_dir = self._stock_dir(root, {"status": "STOPPED"})
                with patch("gui_auto_trade_utils.append_stock_log"):
                    ok = mark_pending_order_integrity_review_required(
                        "루틴A",
                        stock_dir,
                        f"{index:06d}",
                        "테스트",
                        ["PENDING_ORDER_QTY_MISSING"],
                        source=source,
                    )
                saved = json.loads(
                    (stock_dir / "state.json").read_text(encoding="utf-8")
                )
            self.assertTrue(ok)
            self.assertEqual("미체결 데이터 오류", saved["review_reason"])
            self.assertEqual(expected_location, saved["review_location"])
            self.assertIn("PENDING_ORDER_QTY_MISSING", saved["review_detail"])

    def test_same_review_reason_is_idempotent(self) -> None:
        reason = "미체결 데이터 오류"
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

    def test_different_existing_review_reason_is_merged_without_reentry(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            stock_dir = self._stock_dir(
                root,
                {
                    "status": "REVIEW_REQUIRED",
                    "review_required": True,
                    "review_status": "RESOLVED",
                    "review_reason": "운영 데이터 불일치",
                    "review_detail": "기존 내부 evidence",
                    "review_location": "운영 시작",
                    "review_entered_at": "2026-08-01 09:00:00",
                },
            )
            with (
                patch("gui_auto_trade_utils.now_text", return_value="2026-08-16 12:34:56"),
                patch("gui_auto_trade_utils.append_stock_log"),
            ):
                ok = mark_pending_order_integrity_review_required(
                    "루틴A",
                    stock_dir,
                    "000001",
                    "테스트",
                    ["PENDING_ORDER_QTY_MISSING"],
                    source="종목등록 창 미체결 데이터 무결성 오류",
                )
            saved = json.loads((stock_dir / "state.json").read_text(encoding="utf-8"))

        self.assertTrue(ok)
        self.assertEqual(
            "운영 데이터 불일치 / 미체결 데이터 오류",
            saved["review_reason"],
        )
        self.assertEqual("2026-08-01 09:00:00", saved["review_entered_at"])
        self.assertEqual("운영 시작", saved["review_location"])
        self.assertEqual("RESOLVED", saved["review_status"])
        self.assertIn("기존 내부 evidence", saved["review_detail"])
        self.assertIn("PENDING_ORDER_QTY_MISSING", saved["review_detail"])

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
