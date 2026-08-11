import os
from pathlib import Path
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication

import gui_main_emergency_ops as emergency_ops
import gui_review_required_window as review_window
import event_journal_production


class ReviewReturnToastTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _window(self):
        with patch.object(review_window, "collect_global_review_required_rows", return_value=[]):
            window = review_window.GlobalReviewRequiredWindow()
        self.addCleanup(window.close)
        return window

    @staticmethod
    def _targets(count: int) -> list[tuple[Path, str, str]]:
        return [
            (Path("stocks") / f"{index:06d}_대상{index}", f"{index:06d}", f"대상{index}")
            for index in range(1, count + 1)
        ]

    def test_return_without_selection_uses_toast_with_window_parent(self):
        window = self._window()
        with (
            patch.object(window, "selected_stock_dirs", return_value=[]),
            patch.object(review_window, "show_toast") as toast,
            patch.object(review_window.QMessageBox, "information") as information,
        ):
            window.return_selected_items_to_auto_list()

        toast.assert_called_once_with(window, "복귀할 검토종목을 선택하세요.")
        information.assert_not_called()

    def test_return_result_toast_count_contract(self):
        cases = [
            ("success_one", ["NORMALIZED"], "복귀 완료 1개"),
            ("success_three", ["NORMALIZED"] * 3, "복귀 완료 3개"),
            ("failed_one", ["BLOCKED"], "복귀 불가 1개"),
            ("failed_two", ["BLOCKED", "FAILED"], "복귀 불가 2개"),
            (
                "mixed",
                ["NORMALIZED", "BLOCKED", "NORMALIZED"],
                "복귀 완료 2개 | 복귀 불가 1개",
            ),
        ]

        for label, statuses, expected in cases:
            with self.subTest(label=label):
                window = self._window()
                targets = self._targets(len(statuses))
                results = [
                    {
                        "status": status,
                        "reason": "상세 무결성 사유" if status == "BLOCKED" else "",
                    }
                    for status in statuses
                ]
                with (
                    patch.object(window, "selected_stock_dirs", return_value=targets),
                    patch.object(window, "_refresh_after_review_action"),
                    patch.object(
                        emergency_ops,
                        "normalize_review_emergency_target",
                        side_effect=results,
                    ) as normalize,
                    patch.object(review_window, "append_stock_log") as stock_log,
                    patch.object(review_window, "append_production_event") as journal,
                    patch.object(review_window, "show_toast") as toast,
                    patch.object(review_window.QMessageBox, "information") as information,
                ):
                    window.return_selected_items_to_auto_list()

                toast.assert_called_once_with(window, expected)
                information.assert_not_called()
                normalize.assert_called()
                self.assertEqual(statuses.count("NORMALIZED"), stock_log.call_count)
                self.assertEqual(len(statuses), journal.call_count)
                expected_results = [
                    "COMPLETED" if status == "NORMALIZED" else
                    "BLOCKED" if status == "BLOCKED" else "FAILED"
                    for status in statuses
                ]
                self.assertEqual(
                    expected_results,
                    [call.kwargs["result"] for call in journal.call_args_list],
                )
                self.assertTrue(
                    all(call.args == ("REVIEW_RETURNED",) for call in journal.call_args_list)
                )
                for call in journal.call_args_list:
                    self.assertEqual("STOCK", call.kwargs["target_type"])
                for status, call in zip(statuses, journal.call_args_list):
                    if status == "BLOCKED":
                        self.assertEqual("상세 무결성 사유", call.kwargs["details"]["reason"])
                self.assertNotIn(":", expected)
                self.assertNotIn("상세 무결성 사유", expected)
                for part in expected.split(" | "):
                    self.assertTrue(part.endswith("개"))

    def test_event_journal_write_failure_does_not_change_return_result(self):
        window = self._window()
        target = self._targets(1)
        with (
            patch.object(window, "selected_stock_dirs", return_value=target),
            patch.object(window, "_refresh_after_review_action"),
            patch.object(
                emergency_ops,
                "normalize_review_emergency_target",
                return_value={"status": "NORMALIZED", "reason": ""},
            ),
            patch.object(review_window, "append_stock_log"),
            patch.object(review_window, "show_toast") as toast,
            patch.object(
                event_journal_production._WRITER,
                "append_event",
                side_effect=OSError("journal unavailable"),
            ),
        ):
            window.return_selected_items_to_auto_list()

        toast.assert_called_once_with(window, "복귀 완료 1개")


if __name__ == "__main__":
    unittest.main()
