from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import integrity_checker


class LocalStockIntegrityCheckTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "stocks").mkdir()
        self.routine_dir = self.root / "routines" / "basic"
        self.routine_dir.mkdir(parents=True)
        self.entry_path = self.routine_dir / "routine.py"
        self.entry_path.write_text("# routine\n", encoding="utf-8")
        (self.routine_dir / "routine.json").write_text(
            json.dumps({"name": "basic", "entry_file": "routine.py"}, ensure_ascii=False),
            encoding="utf-8",
        )
        self.records_patch = mock.patch(
            "integrity_checker.get_routine_records_for_check",
            return_value=[
                {
                    "name": "basic",
                    "path": self.routine_dir,
                    "entry_file": "routine.py",
                }
            ],
        )
        self.records_patch.start()

    def tearDown(self) -> None:
        self.records_patch.stop()
        self.tmp.cleanup()

    def _write_json(self, path: Path, data: object) -> None:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _stock(
        self,
        folder: str = "005930_Samsung",
        *,
        config: object | None = None,
        state: object | None = None,
        orders: object | None = None,
        logs: bool = True,
    ) -> Path:
        stock_dir = self.root / "stocks" / folder
        stock_dir.mkdir()
        if config is None:
            config = {"routine": "basic"}
        if state is None:
            state = {"status": "STOPPED"}
        if orders is None:
            orders = {"orders": []}
        self._write_json(stock_dir / "config.json", config)
        self._write_json(stock_dir / "state.json", state)
        self._write_json(stock_dir / "orders.json", orders)
        if logs:
            (stock_dir / "logs").mkdir()
        return stock_dir

    def _result(self) -> dict[str, object]:
        return integrity_checker.run_local_stock_integrity_check(self.root)

    def _issue_codes(self, result: dict[str, object]) -> list[str]:
        return [str(issue.get("issue_code")) for issue in result["issues"]]

    def test_single_valid_stock_passes(self) -> None:
        self._stock()

        result = self._result()

        self.assertEqual("PASS", result["local_status"])
        self.assertEqual("SERVER_NOT_CHECKED", result["server_status"])
        self.assertEqual(1, result["checked_stock_count"])
        self.assertEqual([], result["issues"])

    def test_multiple_valid_stocks_pass(self) -> None:
        self._stock("005930_Samsung")
        self._stock("000660_Hynix")

        result = self._result()

        self.assertEqual("PASS", result["local_status"])
        self.assertEqual(2, result["checked_stock_count"])

    def test_zero_targets_is_check_error_not_pass(self) -> None:
        result = self._result()

        self.assertEqual("CHECK_ERROR", result["local_status"])
        self.assertEqual(0, result["checked_stock_count"])
        self.assertEqual(["NO_STOCK_TARGETS"], self._issue_codes(result))
        self.assertFalse(result["issues"][0]["requires_review"])

    def test_bad_folder_name_requires_review(self) -> None:
        self._stock("badfolder")

        result = self._result()

        self.assertEqual("REVIEW_REQUIRED", result["local_status"])
        self.assertEqual(["STOCK_FOLDER_IDENTITY"], self._issue_codes(result))

    def test_invalid_stock_code_requires_review(self) -> None:
        self._stock("000000_Zero")

        result = self._result()

        self.assertIn("STOCK_CODE_FORMAT", self._issue_codes(result))

    def test_empty_stock_name_requires_review(self) -> None:
        self._stock("005930_")

        result = self._result()

        self.assertIn("STOCK_NAME_PRESENT", self._issue_codes(result))

    def test_missing_required_paths_are_reported(self) -> None:
        stock_dir = self._stock()
        (stock_dir / "config.json").unlink()
        (stock_dir / "state.json").unlink()
        (stock_dir / "orders.json").unlink()
        (stock_dir / "logs").rmdir()

        result = self._result()

        self.assertEqual(4, self._issue_codes(result).count("REQUIRED_PATH_MISSING"))

    def test_missing_orders_file_does_not_report_orders_schema(self) -> None:
        stock_dir = self._stock()
        (stock_dir / "orders.json").unlink()

        result = self._result()

        self.assertIn("REQUIRED_PATH_MISSING", self._issue_codes(result))
        self.assertNotIn("ORDERS_REQUIRED_KEY_MISSING", self._issue_codes(result))

    def test_config_json_read_error_is_check_error(self) -> None:
        stock_dir = self._stock()
        (stock_dir / "config.json").write_text("{", encoding="utf-8")

        result = self._result()

        self.assertIn("JSON_READ_ERROR", self._issue_codes(result))
        self.assertEqual("CHECK_ERROR", result["local_status"])
        issue = next(issue for issue in result["issues"] if issue["issue_code"] == "JSON_READ_ERROR")
        self.assertFalse(issue["requires_review"])

    def test_state_json_read_error_is_check_error(self) -> None:
        stock_dir = self._stock()
        (stock_dir / "state.json").write_text("{", encoding="utf-8")

        result = self._result()

        self.assertIn("JSON_READ_ERROR", self._issue_codes(result))

    def test_orders_json_read_error_is_check_error(self) -> None:
        stock_dir = self._stock()
        (stock_dir / "orders.json").write_text("{", encoding="utf-8")

        result = self._result()

        self.assertIn("JSON_READ_ERROR", self._issue_codes(result))

    def test_json_root_list_requires_review(self) -> None:
        self._stock(config=[], state=[], orders=[])

        result = self._result()

        self.assertEqual(3, self._issue_codes(result).count("JSON_ROOT_TYPE_INVALID"))

    def test_orders_key_missing_requires_review(self) -> None:
        self._stock(orders={})

        result = self._result()

        self.assertIn("ORDERS_REQUIRED_KEY_MISSING", self._issue_codes(result))

    def test_orders_not_list_requires_review(self) -> None:
        self._stock(orders={"orders": {}})

        result = self._result()

        self.assertIn("ORDERS_REQUIRED_KEY_MISSING", self._issue_codes(result))

    def test_orders_non_dict_item_requires_review(self) -> None:
        self._stock(orders={"orders": [{"id": 1}, "bad"]})

        result = self._result()

        self.assertIn("ORDER_STRUCTURE_INVALID", self._issue_codes(result))

    def test_missing_routine_assignment_requires_review(self) -> None:
        self._stock(config={"routine": "missing"})

        result = self._result()

        self.assertIn("ROUTINE_ASSIGNMENT_INVALID", self._issue_codes(result))

    def test_missing_routine_entry_file_requires_review(self) -> None:
        self.entry_path.unlink()
        self._stock(config={"routine": "basic"})

        result = self._result()

        self.assertIn("ROUTINE_ENTRY_FILE_MISSING", self._issue_codes(result))

    def test_one_bad_stock_does_not_stop_next_stock(self) -> None:
        bad = self._stock("005930_Bad")
        (bad / "config.json").write_text("{", encoding="utf-8")
        self._stock("000660_Good")

        result = self._result()

        self.assertEqual(2, result["checked_stock_count"])
        self.assertIn("JSON_READ_ERROR", self._issue_codes(result))

    def test_stock_access_exception_is_check_error(self) -> None:
        with mock.patch("integrity_checker.get_central_stock_dirs", side_effect=OSError("blocked")):
            result = self._result()

        self.assertEqual("CHECK_ERROR", result["local_status"])
        self.assertEqual(["STOCK_TARGET_ACCESS_ERROR"], self._issue_codes(result))

    def test_service_does_not_write_files(self) -> None:
        self._stock()
        before = {
            path.relative_to(self.root): path.read_bytes()
            for path in self.root.rglob("*")
            if path.is_file()
        }

        result = self._result()

        after = {
            path.relative_to(self.root): path.read_bytes()
            for path in self.root.rglob("*")
            if path.is_file()
        }
        self.assertEqual("PASS", result["local_status"])
        self.assertEqual(before, after)
        self.assertFalse((self.root / "invalid_items.log").exists())

    def test_standard_issue_fields_are_present(self) -> None:
        self._stock("000000_Bad")

        result = self._result()
        issue = result["issues"][0]

        for key in (
            "check_scope",
            "execution_status",
            "stock_code",
            "stock_name",
            "issue_code",
            "severity",
            "message",
            "recommended_action",
            "requires_review",
            "checked_at",
            "source_path",
            "server_checked",
        ):
            self.assertIn(key, issue)
        self.assertFalse(issue["server_checked"])

    def test_standard_result_fields_are_present(self) -> None:
        self._stock()

        result = self._result()

        for key in (
            "local_status",
            "server_status",
            "checked_stock_count",
            "review_required_count",
            "check_error_count",
            "server_not_checked_count",
            "issues",
            "started_at",
            "completed_at",
        ):
            self.assertIn(key, result)
        self.assertEqual("SERVER_NOT_CHECKED", result["server_status"])
        self.assertEqual(0, result["server_not_checked_count"])

    def test_review_required_issue_is_registered_through_writer(self) -> None:
        self._stock("000000_Bad")
        writer = mock.Mock(return_value=True)

        result = integrity_checker.apply_integrity_review_required_issues(
            self._result(),
            project_root=self.root,
            review_writer=writer,
        )

        self.assertEqual("REVIEW_REQUIRED", result["local_status"])
        writer.assert_called_once()
        stock_dir, code, name, item = writer.call_args.args[:4]
        self.assertEqual(self.root / "stocks" / "000000_Bad", stock_dir)
        self.assertEqual("000000", code)
        self.assertEqual("Bad", name)
        self.assertIn("[STOCK_CODE_FORMAT]", " ".join(item["review_reasons"]))

    def test_check_error_issue_is_not_registered(self) -> None:
        stock_dir = self._stock()
        (stock_dir / "config.json").write_text("{", encoding="utf-8")
        writer = mock.Mock(return_value=True)

        result = integrity_checker.apply_integrity_review_required_issues(
            self._result(),
            project_root=self.root,
            review_writer=writer,
        )

        self.assertEqual("CHECK_ERROR", result["local_status"])
        writer.assert_not_called()

    def test_server_not_checked_issue_is_not_registered(self) -> None:
        writer = mock.Mock(return_value=True)
        result = {
            "local_status": "PASS",
            "server_status": "SERVER_NOT_CHECKED",
            "checked_stock_count": 1,
            "review_required_count": 0,
            "check_error_count": 0,
            "server_not_checked_count": 0,
            "issues": [
                {
                    "check_scope": "server_stock_integrity",
                    "execution_status": "SERVER_NOT_CHECKED",
                    "stock_code": "005930",
                    "stock_name": "Samsung",
                    "stock_dir": "stocks/005930_Samsung",
                    "issue_code": "SERVER_NOT_CHECKED",
                    "requires_review": False,
                    "source_path": "stocks/005930_Samsung",
                    "server_checked": False,
                }
            ],
            "started_at": "2026-01-01 00:00:00",
            "completed_at": "2026-01-01 00:00:00",
        }

        updated = integrity_checker.apply_integrity_review_required_issues(
            result,
            project_root=self.root,
            review_writer=writer,
        )

        self.assertEqual("PASS", updated["local_status"])
        writer.assert_not_called()

    def test_duplicate_same_issue_code_for_same_stock_is_registered_once(self) -> None:
        self._stock("000000_Bad")
        original = self._result()
        original["issues"].append(dict(original["issues"][0]))
        writer = mock.Mock(return_value=True)

        integrity_checker.apply_integrity_review_required_issues(
            original,
            project_root=self.root,
            review_writer=writer,
        )

        writer.assert_called_once()
        reasons = writer.call_args.args[3]["review_reasons"]
        self.assertEqual(1, sum("[STOCK_CODE_FORMAT]" in reason for reason in reasons))

    def test_existing_review_reason_is_preserved(self) -> None:
        self._stock(
            "000000_Bad",
            state={"status": "REVIEW_REQUIRED", "review_reason": "기존 사유"},
        )
        writer = mock.Mock(return_value=True)

        integrity_checker.apply_integrity_review_required_issues(
            self._result(),
            project_root=self.root,
            review_writer=writer,
        )

        reasons = writer.call_args.args[3]["review_reasons"]
        self.assertIn("기존 사유", reasons)
        self.assertTrue(any("[STOCK_CODE_FORMAT]" in reason for reason in reasons))

    def test_writer_failure_adds_check_error(self) -> None:
        self._stock("000000_Bad")
        writer = mock.Mock(return_value=False)

        result = integrity_checker.apply_integrity_review_required_issues(
            self._result(),
            project_root=self.root,
            review_writer=writer,
        )

        self.assertEqual("CHECK_ERROR", result["local_status"])
        self.assertIn("CHECK_ERROR", self._issue_codes(result))
        check_error = result["issues"][-1]
        self.assertFalse(check_error["requires_review"])

    def test_multiple_stocks_continue_when_one_writer_fails(self) -> None:
        self._stock("000000_Bad")
        self._stock("111111_AlsoBad", orders={})
        writer = mock.Mock(side_effect=[False, True])

        result = integrity_checker.apply_integrity_review_required_issues(
            self._result(),
            project_root=self.root,
            review_writer=writer,
        )

        self.assertEqual(2, writer.call_count)
        self.assertEqual("CHECK_ERROR", result["local_status"])
        self.assertIn("CHECK_ERROR", self._issue_codes(result))

    def test_two_issue_codes_for_same_stock_use_one_writer_call(self) -> None:
        stock_dir = self._stock("000000_Bad", orders={})
        (stock_dir / "logs").rmdir()
        writer = mock.Mock(return_value=True)

        integrity_checker.apply_integrity_review_required_issues(
            self._result(),
            project_root=self.root,
            review_writer=writer,
        )

        writer.assert_called_once()
        reasons_text = " ".join(writer.call_args.args[3]["review_reasons"])
        self.assertIn("[STOCK_CODE_FORMAT]", reasons_text)
        self.assertIn("[REQUIRED_PATH_MISSING]", reasons_text)
        self.assertIn("[ORDERS_REQUIRED_KEY_MISSING]", reasons_text)

    def test_read_only_check_does_not_call_writer(self) -> None:
        self._stock("000000_Bad")
        writer = mock.Mock(return_value=True)

        result = self._result()

        self.assertEqual("REVIEW_REQUIRED", result["local_status"])
        writer.assert_not_called()


class StockRegisterIntegrityAutoCheckTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt5.QtWidgets import QApplication, QWidget

        cls.app = QApplication.instance() or QApplication([])
        cls.QWidget = QWidget

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.parent = self._parent()
        self.patches = [
            mock.patch("gui_stock_register_window.PROJECT_ROOT", self.root),
            mock.patch("gui_stock_register_window.read_base_stocks", return_value=[]),
            mock.patch("gui_stock_register_window.show_toast"),
        ]
        for patcher in self.patches:
            patcher.start()

    def tearDown(self) -> None:
        for patcher in reversed(self.patches):
            patcher.stop()
        self.parent.close()
        self.parent.deleteLater()
        self.tmp.cleanup()

    def _parent(self):
        QWidget = self.QWidget

        class Parent(QWidget):
            def __init__(self) -> None:
                super().__init__()
                self.review_writer = mock.Mock(return_value=True)
                self.popup_messages: list[str] = []
                self.dynamicCall = mock.Mock()

            def mark_review_required(self, *args, **kwargs):
                return self.review_writer(*args, **kwargs)

            def showAutoTradePopupMessage(self, message: str, timeout_ms: int = 2500) -> None:
                self.popup_messages.append(message)

        return Parent()

    def _window(self):
        from gui_stock_register_window import StockRegisterWindow

        window = StockRegisterWindow(self.parent)
        self.addCleanup(window.close)
        self.addCleanup(window.deleteLater)
        return window

    def _result(
        self,
        status: str,
        *,
        checked: int = 1,
        issues: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        issues = list(issues or [])
        return {
            "local_status": status,
            "server_status": "SERVER_NOT_CHECKED",
            "checked_stock_count": checked,
            "review_required_count": sum(1 for item in issues if item.get("requires_review") is True),
            "check_error_count": sum(
                1 for item in issues if item.get("execution_status") == "CHECK_ERROR"
            ),
            "server_not_checked_count": 0,
            "issues": issues,
            "started_at": "2026-08-05 10:00:00",
            "completed_at": "2026-08-05 10:00:01",
        }

    def _review_issue(self, code: str = "005930", name: str = "삼성전자") -> dict[str, object]:
        return {
            "check_scope": "local_stock_integrity",
            "execution_status": "REVIEW_REQUIRED",
            "stock_code": code,
            "stock_name": name,
            "stock_dir": f"stocks/{code}_{name}",
            "issue_code": "STOCK_CODE_FORMAT",
            "severity": "REVIEW",
            "message": "검토 필요",
            "recommended_action": "검토관리에서 확인",
            "requires_review": True,
            "checked_at": "2026-08-05 10:00:00",
            "source_path": f"stocks/{code}_{name}/config.json",
            "server_checked": False,
        }

    def _check_error_issue(self, message: str = "읽기 오류") -> dict[str, object]:
        return {
            "check_scope": "local_stock_integrity",
            "execution_status": "CHECK_ERROR",
            "stock_code": "",
            "stock_name": "",
            "stock_dir": "",
            "issue_code": "CHECK_ERROR",
            "severity": "ERROR",
            "message": message,
            "recommended_action": "재시도",
            "requires_review": False,
            "checked_at": "2026-08-05 10:00:00",
            "source_path": "stocks",
            "server_checked": False,
        }

    def test_auto_run_once_after_window_show(self) -> None:
        from PyQt5.QtGui import QShowEvent

        window = self._window()
        result = self._result("PASS")
        with (
            mock.patch("gui_stock_register_window.run_local_stock_integrity_check", return_value=result) as check,
            mock.patch("gui_stock_register_window.QTimer.singleShot", side_effect=lambda _ms, fn: fn()),
        ):
            window.showEvent(QShowEvent())

        check.assert_called_once_with(self.root)

    def test_duplicate_show_event_does_not_run_twice(self) -> None:
        from PyQt5.QtGui import QShowEvent

        window = self._window()
        result = self._result("PASS")
        with (
            mock.patch("gui_stock_register_window.run_local_stock_integrity_check", return_value=result) as check,
            mock.patch("gui_stock_register_window.QTimer.singleShot", side_effect=lambda _ms, fn: fn()),
        ):
            window.showEvent(QShowEvent())
            window.showEvent(QShowEvent())

        check.assert_called_once()

    def test_start_status_text_is_set_before_check(self) -> None:
        window = self._window()
        seen: list[str] = []
        original = window._set_integrity_status_text

        def record(message: str) -> None:
            seen.append(message)
            original(message)

        with mock.patch.object(window, "_set_integrity_status_text", side_effect=record):
            with mock.patch(
                "gui_stock_register_window.run_local_stock_integrity_check",
                return_value=self._result("PASS"),
            ):
                window.run_initial_integrity_check()

        self.assertEqual("무결성 검사 중...", seen[0])

    def test_pass_sets_top_status_text(self) -> None:
        window = self._window()
        with mock.patch(
            "gui_stock_register_window.run_local_stock_integrity_check",
            return_value=self._result("PASS"),
        ):
            window.run_initial_integrity_check()

        self.assertEqual("로컬 무결성 통과 | 서버 정합성 미확인", window.integrity_status_label.text())

    def test_pass_shows_toast(self) -> None:
        window = self._window()
        with mock.patch(
            "gui_stock_register_window.run_local_stock_integrity_check",
            return_value=self._result("PASS"),
        ):
            window.run_initial_integrity_check()

        self.assertIn("로컬 무결성 검사 완료 | 서버 정합성 검사는 실행하지 않았습니다.", self.parent.popup_messages)

    def test_review_required_one_stock_text(self) -> None:
        window = self._window()
        result = self._result("REVIEW_REQUIRED", issues=[self._review_issue()])
        with (
            mock.patch("gui_stock_register_window.run_local_stock_integrity_check", return_value=result),
            mock.patch("gui_stock_register_window.apply_integrity_review_required_issues", return_value=result),
        ):
            window.run_initial_integrity_check()

        self.assertEqual("검토관리 005930 삼성전자", window.integrity_status_label.text())

    def test_review_required_multiple_stocks_text_counts_stocks(self) -> None:
        window = self._window()
        issues = [
            self._review_issue("005930", "삼성전자"),
            self._review_issue("000660", "SK하이닉스"),
        ]
        result = self._result("REVIEW_REQUIRED", issues=issues)
        with (
            mock.patch("gui_stock_register_window.run_local_stock_integrity_check", return_value=result),
            mock.patch("gui_stock_register_window.apply_integrity_review_required_issues", return_value=result),
        ):
            window.run_initial_integrity_check()

        self.assertEqual("검토관리 005930 삼성전자 외 1종목", window.integrity_status_label.text())

    def test_same_stock_multiple_issues_count_as_one_stock(self) -> None:
        window = self._window()
        second = dict(self._review_issue())
        second["issue_code"] = "REQUIRED_PATH_MISSING"
        result = self._result("REVIEW_REQUIRED", issues=[self._review_issue(), second])
        with (
            mock.patch("gui_stock_register_window.run_local_stock_integrity_check", return_value=result),
            mock.patch("gui_stock_register_window.apply_integrity_review_required_issues", return_value=result),
        ):
            window.run_initial_integrity_check()

        self.assertEqual("검토관리 005930 삼성전자", window.integrity_status_label.text())

    def test_writer_callback_is_passed_to_review_application(self) -> None:
        window = self._window()
        result = self._result("REVIEW_REQUIRED", issues=[self._review_issue()])

        def apply(result_arg, *, project_root, review_writer, source):
            review_writer(
                self.root / "stocks" / "005930_삼성전자",
                "005930",
                "삼성전자",
                {"review_reasons": ["x"]},
                source=source,
            )
            return result_arg

        with (
            mock.patch("gui_stock_register_window.run_local_stock_integrity_check", return_value=result),
            mock.patch("gui_stock_register_window.apply_integrity_review_required_issues", side_effect=apply),
        ):
            window.run_initial_integrity_check()

        self.parent.review_writer.assert_called_once()

    def test_check_error_does_not_call_writer(self) -> None:
        window = self._window()
        result = self._result("CHECK_ERROR", issues=[self._check_error_issue()])
        with (
            mock.patch("gui_stock_register_window.run_local_stock_integrity_check", return_value=result),
            mock.patch("gui_stock_register_window.apply_integrity_review_required_issues") as apply,
        ):
            window.run_initial_integrity_check()

        apply.assert_not_called()
        self.parent.review_writer.assert_not_called()

    def test_writer_fail_sets_top_status_text(self) -> None:
        window = self._window()
        result = self._result("REVIEW_REQUIRED", issues=[self._review_issue()])
        failed = self._result(
            "CHECK_ERROR",
            issues=[self._review_issue(), self._check_error_issue("Review writer failed for 005930 삼성전자")],
        )
        with (
            mock.patch("gui_stock_register_window.run_local_stock_integrity_check", return_value=result),
            mock.patch("gui_stock_register_window.apply_integrity_review_required_issues", return_value=failed),
        ):
            window.run_initial_integrity_check()

        self.assertEqual("무결성 문제 발견 | 검토관리 반영 실패", window.integrity_status_label.text())

    def test_writer_fail_shows_toast(self) -> None:
        window = self._window()
        result = self._result("REVIEW_REQUIRED", issues=[self._review_issue()])
        failed = self._result(
            "CHECK_ERROR",
            issues=[self._review_issue(), self._check_error_issue("Review writer failed for 005930 삼성전자")],
        )
        with (
            mock.patch("gui_stock_register_window.run_local_stock_integrity_check", return_value=result),
            mock.patch("gui_stock_register_window.apply_integrity_review_required_issues", return_value=failed),
        ):
            window.run_initial_integrity_check()

        self.assertIn("무결성 검사 처리 오류 | 검토관리 반영에 실패했습니다.", self.parent.popup_messages)

    def test_zero_targets_sets_text(self) -> None:
        window = self._window()
        with mock.patch(
            "gui_stock_register_window.run_local_stock_integrity_check",
            return_value=self._result("CHECK_ERROR", checked=0, issues=[self._check_error_issue()]),
        ):
            window.run_initial_integrity_check()

        self.assertEqual("검사 대상 종목 없음", window.integrity_status_label.text())

    def test_check_exception_keeps_window_usable(self) -> None:
        window = self._window()
        with (
            mock.patch(
                "gui_stock_register_window.run_local_stock_integrity_check",
                side_effect=RuntimeError("boom"),
            ),
            mock.patch("gui_stock_register_window.LOGGER.exception"),
        ):
            window.run_initial_integrity_check()

        self.assertTrue(window.isEnabled())
        self.assertEqual("무결성 검사 실패", window.integrity_status_label.text())

    def test_refresh_after_review_check(self) -> None:
        window = self._window()
        result = self._result("REVIEW_REQUIRED", issues=[self._review_issue()])
        with (
            mock.patch("gui_stock_register_window.run_local_stock_integrity_check", return_value=result),
            mock.patch("gui_stock_register_window.apply_integrity_review_required_issues", return_value=result),
            mock.patch.object(window, "refresh_stock_table") as refresh,
        ):
            window.run_initial_integrity_check()

        refresh.assert_called_once()

    def test_auto_check_does_not_call_server_api(self) -> None:
        window = self._window()
        with mock.patch(
            "gui_stock_register_window.run_local_stock_integrity_check",
            return_value=self._result("PASS"),
        ):
            window.run_initial_integrity_check()

        self.parent.dynamicCall.assert_not_called()

    def test_auto_check_does_not_call_changelog_writer(self) -> None:
        window = self._window()
        with (
            mock.patch("gui_stock_register_window.run_local_stock_integrity_check", return_value=self._result("PASS")),
            mock.patch("gui_stock_register_window.append_changelog") as changelog,
        ):
            window.run_initial_integrity_check()

        changelog.assert_not_called()

    def test_auto_check_does_not_write_invalid_items_log(self) -> None:
        window = self._window()
        invalid_log = self.root / "invalid_items.log"
        with mock.patch(
            "gui_stock_register_window.run_local_stock_integrity_check",
            return_value=self._result("PASS"),
        ):
            window.run_initial_integrity_check()

        self.assertFalse(invalid_log.exists())

    def test_manual_integrity_button_is_removed(self) -> None:
        window = self._window()

        self.assertFalse(hasattr(window, "btn_integrity_check"))

    def test_manual_integrity_dialog_handler_is_removed(self) -> None:
        window = self._window()

        self.assertFalse(hasattr(window, "open_integrity_check_window"))


if __name__ == "__main__":
    unittest.main()
