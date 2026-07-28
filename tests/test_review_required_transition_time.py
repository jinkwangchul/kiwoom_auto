import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication

import gui_auto_trade_status_ops as status_ops
import gui_review_required_window as review_window


class ReviewRequiredTransitionTimeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_reader_uses_only_official_transition_timestamp(self) -> None:
        self.assertEqual(
            "2026-07-28 11:42:15",
            review_window.review_entered_at_display(
                {"review_entered_at": "2026-07-28 11:42:15"}
            ),
        )
        self.assertEqual(
            "미기록",
            review_window.review_entered_at_display(
                {"review_checked_at": "2026-07-28 12:00:00"}
            ),
        )

    def test_review_window_places_transition_time_after_status(self) -> None:
        row = {
            "routine_name": "지표추종매매",
            "stock_dir": Path("stocks/005930_삼성전자"),
            "code": "005930",
            "name": "삼성전자",
            "review_location": "운영시작",
            "review_reason": "검토 필요",
            "review_entered_at": "2026-07-28 11:42:15",
            "holding_qty": 0,
            "avg_price": 0,
            "buy_pending_qty": 0,
            "sell_pending_qty": 0,
            "return_availability": "해결",
        }
        with (
            patch.object(
                review_window.GlobalReviewRequiredWindow,
                "_central_review_rows",
                return_value=[row],
            ),
            patch.object(
                review_window.GlobalReviewRequiredWindow,
                "load_runtime_reconciliation_items",
            ),
        ):
            window = review_window.GlobalReviewRequiredWindow()

        self.assertEqual(
            "검토 전환 시각",
            window.table.horizontalHeaderItem(4).text(),
        )
        self.assertEqual("2026-07-28 11:42:15", window.table.item(0, 4).text())
        self.assertEqual("검토 필요", window.table.item(0, 5).text())
        window.close()

    def test_writer_preserves_current_entry_and_reentry_gets_new_time(self) -> None:
        with TemporaryDirectory() as temp:
            stock_dir = Path(temp) / "005930_삼성전자"
            stock_dir.mkdir()
            state_path = stock_dir / "state.json"
            state_path.write_text(
                json.dumps({"status": "STOPPED"}, ensure_ascii=False),
                encoding="utf-8",
            )
            window = MagicMock()

            with (
                patch.object(status_ops, "now_text", return_value="2026-07-28 11:42:15"),
                patch.object(status_ops, "append_stock_log"),
            ):
                self.assertTrue(
                    status_ops.auto_trade_update_stock_status(
                        window,
                        stock_dir,
                        "005930",
                        "삼성전자",
                        "REVIEW_REQUIRED",
                        {"review_checked_at": "2026-07-28 11:42:15"},
                    )
                )
            first = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual("2026-07-28 11:42:15", first["review_entered_at"])

            with (
                patch.object(status_ops, "now_text", return_value="2026-07-28 12:00:00"),
                patch.object(status_ops, "append_stock_log"),
            ):
                self.assertTrue(
                    status_ops.auto_trade_update_stock_status(
                        window,
                        stock_dir,
                        "005930",
                        "삼성전자",
                        "REVIEW_REQUIRED",
                        {"review_checked_at": "2026-07-28 12:00:00"},
                    )
                )
            refreshed = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual("2026-07-28 11:42:15", refreshed["review_entered_at"])

            with patch.object(status_ops, "append_stock_log"):
                status_ops.auto_trade_update_stock_status(
                    window,
                    stock_dir,
                    "005930",
                    "삼성전자",
                    "STOPPED",
                )
            with (
                patch.object(status_ops, "now_text", return_value="2026-07-29 09:05:00"),
                patch.object(status_ops, "append_stock_log"),
            ):
                self.assertTrue(
                    status_ops.auto_trade_update_stock_status(
                        window,
                        stock_dir,
                        "005930",
                        "삼성전자",
                        "REVIEW_REQUIRED",
                    )
                )
            reentered = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual("2026-07-29 09:05:00", reentered["review_entered_at"])


if __name__ == "__main__":
    unittest.main()
