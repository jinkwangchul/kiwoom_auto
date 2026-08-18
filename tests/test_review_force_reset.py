import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication

import gui_review_required_window as review_window
import gui_stock_register_window as stock_window
import event_journal_production


class FakeConfirmationBox:
    Warning = object()
    AcceptRole = object()
    RejectRole = object()
    Information = object()
    Critical = object()
    Question = object()
    clicked_accept = True
    last = None
    information_calls = []

    def __init__(self, parent):
        type(self).last = self
        self.parent = parent
        self.title = ""
        self.text = ""
        self.accept_button = object()
        self.cancel_button = object()
        self.default_button = None
        self.escape_button = None

    @classmethod
    def information(cls, parent, title, text):
        cls.information_calls.append((parent, title, text))

    def setIcon(self, _icon):
        pass

    def setWindowTitle(self, title):
        self.title = title

    def setText(self, text):
        self.text = text

    def addButton(self, _label, role):
        return self.accept_button if role is self.AcceptRole else self.cancel_button

    def setDefaultButton(self, button):
        self.default_button = button

    def setEscapeButton(self, button):
        self.escape_button = button

    def exec_(self):
        return 0

    def clickedButton(self):
        return self.accept_button if type(self).clicked_accept else self.cancel_button


class ReviewForceResetTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        FakeConfirmationBox.clicked_accept = True
        FakeConfirmationBox.last = None
        FakeConfirmationBox.information_calls = []

    @staticmethod
    def _make_review_stock(root: Path, *, holding: int = 0, pending: int = 0) -> Path:
        stock_dir = root / "stocks" / "111111_대상"
        stock_dir.mkdir(parents=True)
        (stock_dir / "config.json").write_text(
            json.dumps({"routine_instance_name": "루틴A"}, ensure_ascii=False),
            encoding="utf-8",
        )
        (stock_dir / "state.json").write_text(
            json.dumps(
                {
                    "status": "REVIEW_REQUIRED",
                    "review_required": True,
                    "holding_qty": holding,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        orders = []
        if pending:
            orders.append({"status": "OPEN", "side": "BUY", "pending_qty": pending})
        (stock_dir / "orders.json").write_text(
            json.dumps({"orders": orders}, ensure_ascii=False),
            encoding="utf-8",
        )
        (stock_dir / "logs").mkdir()
        (stock_dir / "logs" / "trade.log").write_text("evidence", encoding="utf-8")
        return stock_dir

    @staticmethod
    def _row(stock_dir: Path) -> dict[str, object]:
        return {
            "stock_dir": stock_dir,
            "code": "111111",
            "name": "대상",
            "routine_name": "루틴A",
            "display_status": "미해결",
            "review_location": "종목관리",
            "review_reason": "상태 불일치",
            "review_entered_at": "2026-08-09 10:00:00",
        }

    def test_force_preflight_allows_review_with_holding_and_pending(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            stock_dir = self._make_review_stock(root, holding=7, pending=3)
            with (
                patch.object(stock_window, "PROJECT_ROOT", root),
                patch.object(stock_window, "stock_reset_stock_dirs_for_stock", return_value=[stock_dir]),
            ):
                result = stock_window.force_stock_reset_preflight("111111", "대상", stock_dir)

        self.assertEqual(stock_window.STOCK_RESET_INITIALIZABLE, result["status"])

    def test_force_preflight_blocks_non_review_and_selected_identity_mismatch(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            stock_dir = self._make_review_stock(root)
            other_dir = root / "stocks" / "222222_다른대상"
            other_dir.mkdir()
            with (
                patch.object(stock_window, "PROJECT_ROOT", root),
                patch.object(stock_window, "stock_reset_stock_dirs_for_stock", return_value=[stock_dir]),
            ):
                mismatch = stock_window.force_stock_reset_preflight("111111", "대상", other_dir)
                (stock_dir / "state.json").write_text(
                    json.dumps({"status": "STOPPED", "review_required": False}),
                    encoding="utf-8",
                )
                non_review = stock_window.force_stock_reset_preflight("111111", "대상", stock_dir)

        self.assertEqual("선택 대상 identity 불일치", mismatch["reason"])
        self.assertEqual("검토관리 대상이 아님", non_review["reason"])

    def test_shared_delete_removes_entire_stock_and_verifies_registration(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            stock_dir = self._make_review_stock(root)
            with (
                patch.object(stock_window, "PROJECT_ROOT", root),
                patch.object(stock_window, "read_base_stocks", return_value=[]),
                patch.object(stock_window, "stock_runtime_dirs_for_stock", return_value=[]),
            ):
                result = stock_window.delete_stock_project_data("111111", "대상", stock_dir)

            self.assertEqual("DELETED", result["status"])
            self.assertFalse(stock_dir.exists())

    def test_shared_delete_does_not_report_success_on_remove_or_readback_failure(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            stock_dir = self._make_review_stock(root)
            with (
                patch.object(stock_window, "PROJECT_ROOT", root),
                patch.object(stock_window.shutil, "rmtree", side_effect=OSError("blocked")),
            ):
                remove_failed = stock_window.delete_stock_project_data("111111", "대상", stock_dir)
            self.assertEqual("FAILED", remove_failed["status"])

        with TemporaryDirectory() as temp:
            root = Path(temp)
            stock_dir = self._make_review_stock(root)
            with (
                patch.object(stock_window, "PROJECT_ROOT", root),
                patch.object(stock_window, "read_base_stocks", side_effect=OSError("readback")),
            ):
                readback_failed = stock_window.delete_stock_project_data("111111", "대상", stock_dir)
            self.assertEqual("FAILED", readback_failed["status"])

    def test_review_force_reset_no_selection_with_review_row_uses_toast(self):
        with TemporaryDirectory() as temp_dir:
            stock_dir = self._make_review_stock(Path(temp_dir))
            row = self._row(stock_dir)
            with (
                patch.object(
                    review_window,
                    "collect_global_review_required_rows",
                    return_value=[row],
                ),
                patch.object(review_window, "show_toast") as toast,
                patch.object(review_window.QMessageBox, "information") as information,
                patch.object(stock_window, "force_stock_reset_preflight") as preflight,
                patch.object(stock_window, "confirm_force_stock_reset") as confirm,
                patch.object(stock_window, "delete_stock_project_data") as delete_data,
                patch.object(review_window, "append_production_event") as journal,
            ):
                window = review_window.GlobalReviewRequiredWindow()
                self.addCleanup(window.close)
                window.table.clearSelection()
                window.delete_selected_review_items()

            toast.assert_called_once_with(
                window,
                "강제초기화할 검토종목을 선택하세요.",
            )
            information.assert_not_called()
            preflight.assert_not_called()
            confirm.assert_not_called()
            delete_data.assert_not_called()
            journal.assert_not_called()

    def test_review_force_reset_empty_table_uses_toast(self):
        with (
            patch.object(
                review_window,
                "collect_global_review_required_rows",
                return_value=[],
            ),
            patch.object(review_window, "show_toast") as toast,
            patch.object(review_window.QMessageBox, "information") as information,
            patch.object(stock_window, "force_stock_reset_preflight") as preflight,
            patch.object(stock_window, "confirm_force_stock_reset") as confirm,
            patch.object(stock_window, "delete_stock_project_data") as delete_data,
            patch.object(review_window, "append_production_event") as journal,
        ):
            window = review_window.GlobalReviewRequiredWindow()
            self.addCleanup(window.close)
            window.delete_selected_review_items()

        toast.assert_called_once_with(
            window,
            "강제초기화할 검토종목을 선택하세요.",
        )
        information.assert_not_called()
        preflight.assert_not_called()
        confirm.assert_not_called()
        delete_data.assert_not_called()
        journal.assert_not_called()

    def test_review_force_reset_cancel_changes_nothing(self):
        with TemporaryDirectory() as temp:
            stock_dir = self._make_review_stock(Path(temp))
            row = self._row(stock_dir)
            FakeConfirmationBox.clicked_accept = False
            with (
                patch.object(review_window, "collect_global_review_required_rows", return_value=[row]),
                patch.object(
                    stock_window,
                    "force_stock_reset_preflight",
                    return_value={
                        "status": stock_window.STOCK_RESET_INITIALIZABLE,
                        "reason": "",
                        "stock_dir": stock_dir,
                    },
                ),
                patch.object(stock_window, "confirm_force_stock_reset", return_value=False) as confirm,
                patch.object(stock_window, "delete_stock_project_data") as delete_data,
                patch.object(review_window, "append_production_event") as journal,
            ):
                window = review_window.GlobalReviewRequiredWindow()
                self.addCleanup(window.close)
                window.table.selectRow(0)
                window.delete_selected_review_items()

            self.assertTrue(stock_dir.exists())
            confirm.assert_called_once_with(window, [("111111", "대상")])
            delete_data.assert_not_called()
            journal.assert_not_called()

    def test_review_force_reset_uses_shared_delete_and_refreshes(self):
        with TemporaryDirectory() as temp:
            stock_dir = self._make_review_stock(Path(temp), holding=2, pending=1)
            row = self._row(stock_dir)
            ready = {
                "status": stock_window.STOCK_RESET_INITIALIZABLE,
                "reason": "",
                "stock_dir": stock_dir,
            }
            with (
                patch.object(review_window, "collect_global_review_required_rows", return_value=[row]),
                patch.object(review_window, "show_toast") as toast,
                patch.object(stock_window, "force_stock_reset_preflight", return_value=ready),
                patch.object(stock_window, "confirm_force_stock_reset", return_value=True) as confirm,
                patch.object(
                    stock_window,
                    "delete_stock_project_data",
                    return_value={"status": "DELETED", "reason": ""},
                ) as delete_data,
                patch.object(review_window, "append_production_event") as journal,
            ):
                window = review_window.GlobalReviewRequiredWindow()
                self.addCleanup(window.close)
                window.table.selectRow(0)
                window.delete_selected_review_items()

            self.assertEqual("강제초기화", window.btn_delete.text())
            confirm.assert_called_once_with(
                window,
                [("111111", "대상")],
            )
            delete_data.assert_called_once_with("111111", "대상", stock_dir)
            journal.assert_called_once()
            self.assertEqual("REVIEW_FORCE_RESET", journal.call_args.args[0])
            self.assertEqual("COMPLETED", journal.call_args.kwargs["result"])
            self.assertEqual("UNREGISTERED", journal.call_args.kwargs["details"]["final_state"])
            self.assertTrue(journal.call_args.kwargs["details"]["post_delete_verified"])
            toast.assert_called_once_with(window, "강제초기화 완료: 1개")

    def test_review_force_reset_records_preflight_block(self):
        with TemporaryDirectory() as temp:
            stock_dir = self._make_review_stock(Path(temp))
            row = self._row(stock_dir)
            with (
                patch.object(review_window, "collect_global_review_required_rows", return_value=[row]),
                patch.object(
                    stock_window,
                    "force_stock_reset_preflight",
                    return_value={"status": "NOT_INITIALIZABLE", "reason": "identity 불일치"},
                ),
                patch.object(review_window.QMessageBox, "information"),
                patch.object(review_window, "append_production_event") as journal,
            ):
                window = review_window.GlobalReviewRequiredWindow()
                self.addCleanup(window.close)
                window.table.selectRow(0)
                window.delete_selected_review_items()

            journal.assert_called_once()
            self.assertEqual("REVIEW_FORCE_RESET", journal.call_args.args[0])
            self.assertEqual("BLOCKED", journal.call_args.kwargs["result"])
            self.assertEqual("identity 불일치", journal.call_args.kwargs["details"]["reason"])
            self.assertFalse(journal.call_args.kwargs["details"]["delete_target_verified"])

    def test_review_force_reset_records_delete_and_readback_failures(self):
        for reason in ("종목 데이터 삭제 실패", "삭제 후 상태 확인 실패"):
            with self.subTest(reason=reason), TemporaryDirectory() as temp:
                stock_dir = self._make_review_stock(Path(temp))
                row = self._row(stock_dir)
                ready = {
                    "status": stock_window.STOCK_RESET_INITIALIZABLE,
                    "reason": "",
                    "stock_dir": stock_dir,
                }
                with (
                    patch.object(review_window, "collect_global_review_required_rows", return_value=[row]),
                    patch.object(stock_window, "force_stock_reset_preflight", return_value=ready),
                    patch.object(stock_window, "confirm_force_stock_reset", return_value=True),
                    patch.object(
                        stock_window,
                        "delete_stock_project_data",
                        return_value={"status": "FAILED", "reason": reason},
                    ),
                    patch.object(review_window, "show_toast"),
                    patch.object(review_window, "append_production_event") as journal,
                ):
                    window = review_window.GlobalReviewRequiredWindow()
                    self.addCleanup(window.close)
                    window.table.selectRow(0)
                    window.delete_selected_review_items()

                journal.assert_called_once()
                self.assertEqual("FAILED", journal.call_args.kwargs["result"])
                self.assertEqual(reason, journal.call_args.kwargs["details"]["reason"])
                self.assertFalse(journal.call_args.kwargs["details"]["post_delete_verified"])

    def test_review_force_reset_event_journal_failure_is_fail_open(self):
        with TemporaryDirectory() as temp:
            stock_dir = self._make_review_stock(Path(temp))
            row = self._row(stock_dir)
            ready = {
                "status": stock_window.STOCK_RESET_INITIALIZABLE,
                "reason": "",
                "stock_dir": stock_dir,
            }
            with (
                patch.object(review_window, "collect_global_review_required_rows", return_value=[row]),
                patch.object(stock_window, "force_stock_reset_preflight", return_value=ready),
                patch.object(stock_window, "confirm_force_stock_reset", return_value=True),
                patch.object(
                    stock_window,
                    "delete_stock_project_data",
                    return_value={"status": "DELETED", "reason": ""},
                ) as delete_data,
                patch.object(review_window, "show_toast") as toast,
                patch.object(
                    event_journal_production._WRITER,
                    "append_event",
                    side_effect=OSError("journal unavailable"),
                ),
            ):
                window = review_window.GlobalReviewRequiredWindow()
                self.addCleanup(window.close)
                window.table.selectRow(0)
                window.delete_selected_review_items()

            delete_data.assert_called_once()
            toast.assert_called_once_with(window, "강제초기화 완료: 1개")

    def test_review_force_reset_multi_selection_records_one_event_per_stock(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            targets = [
                (root / f"stocks/{code}_{name}", code, name)
                for code, name in (
                    ("111111", "대상A"),
                    ("222222", "대상B"),
                    ("333333", "대상C"),
                )
            ]
            for stock_dir, _, _ in targets:
                stock_dir.mkdir(parents=True)
                (stock_dir / "state.json").write_text(
                    json.dumps({"status": "REVIEW_REQUIRED", "review_required": True}),
                    encoding="utf-8",
                )

            def ready(_code, _name, selected_dir):
                return {
                    "status": stock_window.STOCK_RESET_INITIALIZABLE,
                    "reason": "",
                    "stock_dir": Path(selected_dir),
                }

            with (
                patch.object(review_window, "collect_global_review_required_rows", return_value=[]),
                patch.object(stock_window, "force_stock_reset_preflight", side_effect=ready),
                patch.object(stock_window, "confirm_force_stock_reset", return_value=True),
                patch.object(
                    stock_window,
                    "delete_stock_project_data",
                    side_effect=(
                        {"status": "DELETED", "reason": ""},
                        {"status": "FAILED", "reason": "삭제 실패"},
                        {"status": "DELETED", "reason": ""},
                    ),
                ),
                patch.object(review_window, "append_production_event") as journal,
                patch.object(review_window, "show_toast") as toast,
            ):
                window = review_window.GlobalReviewRequiredWindow()
                self.addCleanup(window.close)
                with patch.object(window, "selected_stock_dirs", return_value=targets):
                    window.delete_selected_review_items()

            self.assertEqual(3, journal.call_count)
            self.assertEqual(
                ["COMPLETED", "FAILED", "COMPLETED"],
                [call.kwargs["result"] for call in journal.call_args_list],
            )
            self.assertEqual(
                ["111111", "222222", "333333"],
                [call.kwargs["stock_code"] for call in journal.call_args_list],
            )
            toast.assert_called_once_with(window, "강제초기화 완료: 2개 / 실패 1개")

    def test_force_reset_confirmation_reuses_stock_reset_dialog_structure(self):
        parent = review_window.QWidget()
        self.addCleanup(parent.close)
        with patch.object(stock_window, "QMessageBox", FakeConfirmationBox):
            stock_window.confirm_stock_reset(parent, "111111", "대상")
            normal_dialog = FakeConfirmationBox.last
            confirmed = stock_window.confirm_force_stock_reset(
                parent,
                [("111111", "대상")],
            )

        dialog = FakeConfirmationBox.last
        self.assertTrue(confirmed)
        self.assertEqual("⚠ 종목초기화 확인", normal_dialog.title)
        self.assertEqual("강제초기화 확인", dialog.title)
        self.assertEqual(normal_dialog.text, dialog.text)
        self.assertIn("해당 종목의 모든 기록을 삭제하고", dialog.text)
        self.assertIn("미등록 상태로 초기화합니다.", dialog.text)
        self.assertIn("삭제된 데이터는 복구할 수 없습니다.", dialog.text)
        self.assertIn("초기화 대상:\n111111 대상", dialog.text)
        self.assertNotIn("Broker", dialog.text)
        self.assertNotIn("\n- 111111 대상", dialog.text)
        self.assertIs(dialog.default_button, dialog.cancel_button)
        self.assertIs(dialog.escape_button, dialog.cancel_button)

    def test_general_stock_reset_still_blocks_review_state(self):
        with TemporaryDirectory() as temp:
            stock_dir = self._make_review_stock(Path(temp))
            with patch.object(stock_window, "stock_reset_stock_dirs_for_stock", return_value=[stock_dir]):
                result = stock_window.stock_reset_eligibility("111111", "대상")

        self.assertEqual(stock_window.STOCK_RESET_NOT_INITIALIZABLE, result["status"])
        self.assertEqual("검토관리 상태", result["reason"])


if __name__ == "__main__":
    unittest.main()
