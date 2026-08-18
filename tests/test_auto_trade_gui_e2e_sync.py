from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class _TableItem:
    def __init__(self, text: str) -> None:
        self._text = text

    def text(self) -> str:
        return self._text


class _SelectedIndex:
    def row(self) -> int:
        return 0


class _RegistrationTable:
    def selectionModel(self):
        return SimpleNamespace(selectedRows=lambda: [_SelectedIndex()])

    def item(self, row: int, column: int):
        values = ("111111", "테스트종목", "등록대기")
        return _TableItem(values[column]) if row == 0 and column < len(values) else None


class AutoTradeGuiE2ESyncTest(unittest.TestCase):
    def setUp(self) -> None:
        import gui_main_emergency_ops as emergency

        preflight = patch.object(
            emergency,
            "global_emergency_release_preflight",
            return_value=(True, ""),
        )
        preflight.start()
        self.addCleanup(preflight.stop)

    def _registration_dialog(self, parent):
        return SimpleNamespace(
            result_table=_RegistrationTable(),
            parent=lambda: parent,
            search_stocks=Mock(),
        )

    def test_registration_backend_failure_is_not_reported_or_refreshed_as_success(self) -> None:
        import gui_search_stock_register_dialog as registration

        main = SimpleNamespace(refresh_all=Mock())
        parent = SimpleNamespace(
            refresh_stock_table=Mock(),
            parent=lambda: main,
        )
        dialog = self._registration_dialog(parent)

        with (
            patch.object(
                registration,
                "load_stock_library",
                return_value=[{"code": "111111", "name": "테스트종목", "market": "KOSPI"}],
            ),
            patch.object(registration, "read_base_stocks", return_value=[]),
            patch.object(registration, "append_base_stock", return_value=False),
            patch.object(registration, "append_changelog") as append_changelog,
            patch.object(registration.QMessageBox, "information"),
        ):
            registration.SearchStockRegisterDialog.register_selected_stocks(dialog)

        append_changelog.assert_not_called()
        parent.refresh_stock_table.assert_not_called()
        main.refresh_all.assert_not_called()
        dialog.search_stocks.assert_called_once_with()

    def test_registration_success_refreshes_register_and_monitoring_once(self) -> None:
        import gui_search_stock_register_dialog as registration

        main = SimpleNamespace(refresh_all=Mock())
        parent = SimpleNamespace(
            refresh_stock_table=Mock(),
            parent=lambda: main,
        )
        dialog = self._registration_dialog(parent)

        with (
            patch.object(
                registration,
                "load_stock_library",
                return_value=[{"code": "111111", "name": "테스트종목", "market": "KOSPI"}],
            ),
            patch.object(registration, "read_base_stocks", return_value=[]),
            patch.object(registration, "append_base_stock", return_value=True),
            patch.object(registration, "append_changelog"),
            patch.object(registration.QMessageBox, "information"),
        ):
            registration.SearchStockRegisterDialog.register_selected_stocks(dialog)

        parent.refresh_stock_table.assert_called_once_with()
        main.refresh_all.assert_called_once_with()
        dialog.search_stocks.assert_called_once_with()

    def test_search_register_status_text_uses_policy_display_terms(self) -> None:
        import gui_search_stock_register_dialog as registration

        with tempfile.TemporaryDirectory() as temp:
            stock_dir = Path(temp) / "111111_테스트종목"
            stock_dir.mkdir()
            (stock_dir / "state.json").write_text(
                json.dumps({"status": "REVIEW_REQUIRED", "review_required": True}, ensure_ascii=False),
                encoding="utf-8",
            )
            repository = SimpleNamespace(resolve_stock_dir=Mock(return_value=stock_dir))

            with (
                patch.object(registration, "stock_repository_factory", return_value=repository),
                patch.object(registration, "base_stock_routines_for_stock", return_value=(True, ["루틴A"])),
            ):
                review_status = registration.SearchStockRegisterDialog._registration_status_text(
                    None,
                    "111111",
                    "테스트종목",
                )

            (stock_dir / "state.json").write_text(
                json.dumps({"status": "RUNNING"}, ensure_ascii=False),
                encoding="utf-8",
            )
            with (
                patch.object(registration, "stock_repository_factory", return_value=repository),
                patch.object(registration, "base_stock_routines_for_stock", return_value=(True, ["루틴A"])),
            ):
                routine_status = registration.SearchStockRegisterDialog._registration_status_text(
                    None,
                    "111111",
                    "테스트종목",
                )

            with (
                patch.object(registration, "stock_repository_factory", return_value=repository),
                patch.object(registration, "base_stock_routines_for_stock", return_value=(True, [])),
            ):
                pending_status = registration.SearchStockRegisterDialog._registration_status_text(
                    None,
                    "111111",
                    "테스트종목",
                )

        self.assertEqual("검토관리", review_status)
        self.assertEqual("루틴A", routine_status)
        self.assertEqual("등록대기", pending_status)

    def test_search_register_result_table_shows_policy_columns(self) -> None:
        from PyQt5.QtWidgets import QApplication
        import gui_search_stock_register_dialog as registration

        app = QApplication.instance() or QApplication([])
        self._qt_app = app
        library = [
            {"code": "111111", "name": "검토종목", "market": "KOSPI"},
            {"code": "222222", "name": "이동종목", "market": "KOSDAQ"},
            {"code": "333333", "name": "대기종목", "market": "KOSPI"},
        ]
        base_stocks = [
            {"code": "111111", "name": "검토종목"},
            {"code": "222222", "name": "이동종목"},
        ]
        status_by_code = {
            "111111": "검토관리",
            "222222": "루틴A",
        }

        with (
            patch.object(registration, "load_stock_library", return_value=library),
            patch.object(registration, "read_base_stocks", return_value=base_stocks),
            patch.object(
                registration.SearchStockRegisterDialog,
                "_registration_status_text",
                lambda _self, code, _name: status_by_code.get(code, "등록대기"),
            ),
        ):
            dialog = registration.SearchStockRegisterDialog()
            self.addCleanup(lambda: dialog.close() if dialog is not None else None)
            app.processEvents()

        headers = [
            dialog.result_table.horizontalHeaderItem(column).text()
            for column in range(dialog.result_table.columnCount())
        ]
        self.assertEqual(["종목코드", "종목명", "분류"], headers)
        self.assertEqual(3, dialog.result_table.rowCount())
        self.assertEqual(
            ["111111", "검토종목", "검토관리"],
            [dialog.result_table.item(0, column).text() for column in range(3)],
        )
        self.assertEqual(
            ["222222", "이동종목", "루틴A"],
            [dialog.result_table.item(1, column).text() for column in range(3)],
        )
        self.assertEqual(
            ["333333", "대기종목", "등록대기"],
            [dialog.result_table.item(2, column).text() for column in range(3)],
        )
        app.processEvents()

    def test_emergency_stop_refreshes_monitoring_and_open_settings(self) -> None:
        import gui_main_emergency_ops as emergency
        import operation_policy_gate

        with tempfile.TemporaryDirectory() as temp:
            operation_state_path = Path(temp) / "runtime" / "operation_state.json"
            operation_state_path.parent.mkdir()
            status_bar = SimpleNamespace(showMessage=Mock())
            button = SimpleNamespace(setText=Mock())
            main = SimpleNamespace(
                all_runtime_stock_dirs=lambda: [],
                refresh_auto_trade_assignment_views=Mock(),
                refresh_all=Mock(),
                statusBar=lambda: status_bar,
                btn_emergency_stop=button,
            )

            with (
                patch.object(operation_policy_gate, "OPERATION_STATE_PATH", operation_state_path),
                patch.object(emergency, "append_changelog"),
                patch.object(emergency, "show_toast") as toast,
                patch.object(emergency.QMessageBox, "information") as information,
            ):
                emergency.execute_emergency_stop(main)

        main.refresh_auto_trade_assignment_views.assert_called_once_with()
        main.refresh_all.assert_not_called()
        toast.assert_called_once_with(
            parent=main,
            message="긴급정지 완료 | 대상종목 : 0개 | 매수/매도 : 차단",
            duration_ms=2500,
            position="center",
        )
        information.assert_not_called()

    def test_emergency_release_refreshes_monitoring_and_open_settings(self) -> None:
        import gui_main_emergency_ops as emergency
        import operation_policy_gate

        with tempfile.TemporaryDirectory() as temp:
            operation_state_path = Path(temp) / "runtime" / "operation_state.json"
            operation_state_path.parent.mkdir()
            status_bar = SimpleNamespace(showMessage=Mock())
            button = SimpleNamespace(setText=Mock())
            main = SimpleNamespace(
                all_runtime_stock_dirs=lambda: [],
                refresh_auto_trade_assignment_views=Mock(),
                refresh_all=Mock(),
                statusBar=lambda: status_bar,
                btn_emergency_stop=button,
            )

            with (
                patch.object(operation_policy_gate, "OPERATION_STATE_PATH", operation_state_path),
                patch.object(emergency, "append_changelog"),
                patch.object(emergency, "show_toast") as toast,
                patch.object(emergency.QMessageBox, "information") as information,
            ):
                emergency.release_emergency_stop(main)

        main.refresh_auto_trade_assignment_views.assert_called_once_with()
        main.refresh_all.assert_not_called()
        toast.assert_called_once_with(
            parent=main,
            message="정지해제 완료 | 감시/대기 전환 : 0종목 | 검토관리 : 0종목",
            duration_ms=2500,
            position="center",
        )
        information.assert_not_called()

    def test_emergency_release_does_not_count_existing_review_as_moved(self) -> None:
        import gui_main_emergency_ops as emergency
        import operation_policy_gate

        with tempfile.TemporaryDirectory() as temp:
            operation_state_path = Path(temp) / "runtime" / "operation_state.json"
            operation_state_path.parent.mkdir()
            stock_dir = Path(temp) / "000660_SK하이닉스"
            stock_dir.mkdir()
            (stock_dir / "state.json").write_text(
                json.dumps(
                    {
                        "status": "EMERGENCY_STOPPED",
                        "review_required": True,
                        "review_status": "PENDING",
                    }
                ),
                encoding="utf-8",
            )
            status_bar = SimpleNamespace(showMessage=Mock())
            main = SimpleNamespace(
                all_runtime_stock_dirs=lambda: [stock_dir],
                routine_name_for_stock_dir=lambda _stock_dir: "루틴",
                production_recovery_stock_is_review_required=lambda _code: True,
                refresh_auto_trade_assignment_views=Mock(),
                statusBar=lambda: status_bar,
            )

            with (
                patch.object(
                    emergency,
                    "emergency_review_reason_for_stock",
                    return_value=(True, "검토 유지"),
                ),
                patch.object(
                    emergency,
                    "update_runtime_stock_status",
                    return_value=True,
                ),
                patch.object(operation_policy_gate, "OPERATION_STATE_PATH", operation_state_path),
                patch.object(emergency, "append_changelog"),
                patch.object(emergency, "show_toast") as toast,
            ):
                emergency.release_emergency_stop(main)

        self.assertEqual(
            "정지해제 완료 | 감시/대기 전환 : 0종목 | 검토관리 : 0종목",
            toast.call_args.kwargs["message"],
        )

    def test_emergency_button_restores_from_global_operation_state(self) -> None:
        import gui_main_emergency_ops as emergency
        import operation_policy_gate

        with tempfile.TemporaryDirectory() as temp:
            operation_state_path = Path(temp) / "runtime" / "operation_state.json"
            operation_state_path.parent.mkdir()
            button = SimpleNamespace(setText=Mock())
            window = SimpleNamespace(btn_emergency_stop=button)

            with patch.object(operation_policy_gate, "OPERATION_STATE_PATH", operation_state_path):
                operation_state_path.write_text(
                    json.dumps({"emergency_stop": True}, ensure_ascii=False),
                    encoding="utf-8",
                )
                emergency.update_emergency_button_state(window)
                self.assertEqual("정지해제", button.setText.call_args.args[0])

                operation_state_path.write_text(
                    json.dumps({"emergency_stop": False}, ensure_ascii=False),
                    encoding="utf-8",
                )
                emergency.update_emergency_button_state(window)
                self.assertEqual("긴급정지", button.setText.call_args.args[0])

                operation_state_path.write_text("{}", encoding="utf-8")
                emergency.update_emergency_button_state(window)
                self.assertEqual("긴급정지", button.setText.call_args.args[0])

    def test_emergency_button_ignores_per_stock_emergency_without_global_latch(self) -> None:
        import gui_main_emergency_ops as emergency
        import operation_policy_gate

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            operation_state_path = root / "runtime" / "operation_state.json"
            operation_state_path.parent.mkdir()
            operation_state_path.write_text(
                json.dumps({"emergency_stop": False}, ensure_ascii=False),
                encoding="utf-8",
            )
            first = root / "stocks" / "111111_첫번째"
            second = root / "stocks" / "222222_두번째"
            first.mkdir(parents=True)
            second.mkdir(parents=True)
            (first / "state.json").write_text(
                json.dumps({"status": "EMERGENCY_STOPPED"}, ensure_ascii=False),
                encoding="utf-8",
            )
            (second / "state.json").write_text(
                json.dumps({"status": "EMERGENCY_STOPPED"}, ensure_ascii=False),
                encoding="utf-8",
            )
            button = SimpleNamespace(setText=Mock())
            window = SimpleNamespace(
                btn_emergency_stop=button,
                all_runtime_stock_dirs=lambda: [first, second],
            )

            with patch.object(operation_policy_gate, "OPERATION_STATE_PATH", operation_state_path):
                self.assertTrue(emergency.has_emergency_stopped_stock(window))
                emergency.update_emergency_button_state(window)

        self.assertEqual("긴급정지", button.setText.call_args.args[0])

    def test_emergency_click_branches_from_global_state_not_button_text(self) -> None:
        import gui_main_emergency_ops as emergency

        window = SimpleNamespace(btn_emergency_stop=SimpleNamespace(text=lambda: "긴급정지"))
        with (
            patch.object(emergency, "read_operation_state", return_value={"emergency_stop": True}),
            patch.object(emergency, "release_emergency_stop") as release,
            patch.object(emergency, "execute_emergency_stop") as execute,
        ):
            emergency.on_emergency_stop_clicked(window)

        release.assert_called_once_with(window)
        execute.assert_not_called()

        window = SimpleNamespace(btn_emergency_stop=SimpleNamespace(text=lambda: "정지해제"))
        with (
            patch.object(emergency, "read_operation_state", return_value={"emergency_stop": False}),
            patch.object(emergency, "release_emergency_stop") as release,
            patch.object(emergency, "execute_emergency_stop") as execute,
        ):
            emergency.on_emergency_stop_clicked(window)

        execute.assert_called_once_with(window)
        release.assert_not_called()

    def test_global_emergency_stop_writes_operation_state_before_stock_states(self) -> None:
        import gui_main_emergency_ops as emergency
        import operation_policy_gate
        from routine_order_permission import canonical_routine_order_permission

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            operation_state_path = root / "runtime" / "operation_state.json"
            operation_state_path.parent.mkdir()
            operation_state_path.write_text(
                json.dumps({"existing_key": "preserve"}, ensure_ascii=False),
                encoding="utf-8",
            )
            stock_dir = root / "stocks" / "003550_LG"
            stock_dir.mkdir(parents=True)
            (stock_dir / "state.json").write_text("{}", encoding="utf-8")
            status_bar = SimpleNamespace(showMessage=Mock())
            button = SimpleNamespace(setText=Mock())
            main = SimpleNamespace(
                all_runtime_stock_dirs=lambda: [stock_dir],
                refresh_auto_trade_assignment_views=Mock(),
                refresh_all=Mock(),
                statusBar=lambda: status_bar,
                btn_emergency_stop=button,
            )

            with (
                patch.object(operation_policy_gate, "OPERATION_STATE_PATH", operation_state_path),
                patch.object(emergency, "append_changelog"),
                patch.object(emergency, "append_stock_log"),
                patch.object(emergency, "show_toast"),
                patch.object(emergency.QMessageBox, "critical"),
            ):
                emergency.execute_emergency_stop(main)

            operation_state = json.loads(operation_state_path.read_text(encoding="utf-8"))
            stock_state = json.loads((stock_dir / "state.json").read_text(encoding="utf-8"))

        self.assertTrue(operation_state["emergency_stop"])
        self.assertEqual("정지해제", button.setText.call_args.args[0])
        self.assertEqual("USER_EMERGENCY_STOP", operation_state["emergency_reason"])
        self.assertEqual("CONTROL_WINDOW", operation_state["emergency_source"])
        self.assertEqual("preserve", operation_state["existing_key"])
        self.assertIn("emergency_stopped_at", operation_state)
        self.assertEqual("EMERGENCY_STOPPED", stock_state["status"])
        self.assertFalse(stock_state["trade_enabled"])
        self.assertNotIn("buy_enabled", stock_state)
        self.assertNotIn("sell_enabled", stock_state)
        for signal_type in ("BUY", "SELL"):
            permission = canonical_routine_order_permission(
                state=stock_state,
                signal_type=signal_type,
                operation_state=operation_state,
            )
            self.assertFalse(permission["allowed"])
        self.assertEqual("정지해제", button.setText.call_args.args[0])

    def test_global_emergency_stop_write_failure_blocks_stock_state_writes(self) -> None:
        import gui_main_emergency_ops as emergency

        stock_dir = Path("C:/temp/003550_LG")
        status_bar = SimpleNamespace(showMessage=Mock())
        button = SimpleNamespace(setText=Mock())
        main = SimpleNamespace(
            all_runtime_stock_dirs=lambda: [stock_dir],
            refresh_auto_trade_assignment_views=Mock(),
            refresh_all=Mock(),
            statusBar=lambda: status_bar,
            btn_emergency_stop=button,
        )

        with (
            patch.object(
                emergency,
                "write_global_emergency_stop_state",
                return_value={"ok": False, "error": "fail"},
            ),
            patch.object(emergency, "read_operation_state", return_value={"emergency_stop": False}),
            patch.object(emergency, "update_runtime_stock_status") as update_status,
            patch.object(emergency.QMessageBox, "critical") as critical,
            patch.object(emergency, "show_toast") as toast,
        ):
            emergency.execute_emergency_stop(main)

        update_status.assert_not_called()
        critical.assert_called_once()
        toast.assert_called_once()
        main.refresh_auto_trade_assignment_views.assert_not_called()
        main.refresh_all.assert_not_called()
        self.assertEqual("긴급정지", button.setText.call_args.args[0])

    def test_global_emergency_stop_keeps_latch_when_stock_write_partially_fails(self) -> None:
        import gui_main_emergency_ops as emergency
        import operation_policy_gate

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            operation_state_path = root / "runtime" / "operation_state.json"
            operation_state_path.parent.mkdir()
            first = root / "stocks" / "111111_첫번째"
            second = root / "stocks" / "222222_두번째"
            first.mkdir(parents=True)
            second.mkdir(parents=True)
            status_bar = SimpleNamespace(showMessage=Mock())
            button = SimpleNamespace(setText=Mock())
            main = SimpleNamespace(
                all_runtime_stock_dirs=lambda: [first, second],
                refresh_auto_trade_assignment_views=Mock(),
                refresh_all=Mock(),
                statusBar=lambda: status_bar,
                btn_emergency_stop=button,
            )

            with (
                patch.object(operation_policy_gate, "OPERATION_STATE_PATH", operation_state_path),
                patch.object(emergency, "update_runtime_stock_status", side_effect=[True, False]),
                patch.object(emergency, "append_changelog"),
                patch.object(emergency, "show_toast") as toast,
            ):
                emergency.execute_emergency_stop(main)

            operation_state = json.loads(operation_state_path.read_text(encoding="utf-8"))

        self.assertTrue(operation_state["emergency_stop"])
        self.assertIn("실패 : 1개", toast.call_args.kwargs["message"])
        self.assertEqual("정지해제", button.setText.call_args.args[0])

    def test_global_emergency_stop_with_no_targets_still_sets_global_latch(self) -> None:
        import gui_main_emergency_ops as emergency
        import operation_policy_gate

        with tempfile.TemporaryDirectory() as temp:
            operation_state_path = Path(temp) / "runtime" / "operation_state.json"
            operation_state_path.parent.mkdir()
            status_bar = SimpleNamespace(showMessage=Mock())
            main = SimpleNamespace(
                all_runtime_stock_dirs=lambda: [],
                refresh_auto_trade_assignment_views=Mock(),
                refresh_all=Mock(),
                statusBar=lambda: status_bar,
            )

            with (
                patch.object(operation_policy_gate, "OPERATION_STATE_PATH", operation_state_path),
                patch.object(emergency, "append_changelog"),
                patch.object(emergency, "show_toast"),
            ):
                emergency.execute_emergency_stop(main)

            operation_state = json.loads(operation_state_path.read_text(encoding="utf-8"))

        self.assertTrue(operation_state["emergency_stop"])

    def test_global_emergency_release_writes_false_preserves_stopped_at_and_existing_keys(self) -> None:
        import gui_main_emergency_ops as emergency
        import operation_policy_gate

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            operation_state_path = root / "runtime" / "operation_state.json"
            operation_state_path.parent.mkdir()
            operation_state_path.write_text(
                json.dumps(
                    {
                        "existing_key": "preserve",
                        "emergency_stop": True,
                        "emergency_stopped_at": "2026-07-29 09:00:00",
                        "emergency_reason": "USER_EMERGENCY_STOP",
                        "emergency_source": "CONTROL_WINDOW",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            stock_dir = root / "stocks" / "003550_LG"
            stock_dir.mkdir(parents=True)
            status_bar = SimpleNamespace(showMessage=Mock())
            button = SimpleNamespace(setText=Mock())
            main = SimpleNamespace(
                all_runtime_stock_dirs=lambda: [stock_dir],
                refresh_auto_trade_assignment_views=Mock(),
                refresh_all=Mock(),
                statusBar=lambda: status_bar,
                btn_emergency_stop=button,
            )

            with (
                patch.object(operation_policy_gate, "OPERATION_STATE_PATH", operation_state_path),
                patch.object(emergency, "append_changelog"),
                patch.object(emergency, "show_toast"),
            ):
                emergency.release_emergency_stop(main)

            operation_state = json.loads(operation_state_path.read_text(encoding="utf-8"))

        self.assertFalse(operation_state["emergency_stop"])
        self.assertEqual("긴급정지", button.setText.call_args.args[0])
        self.assertEqual("2026-07-29 09:00:00", operation_state["emergency_stopped_at"])
        self.assertEqual("", operation_state["emergency_reason"])
        self.assertEqual("", operation_state["emergency_source"])
        self.assertEqual("preserve", operation_state["existing_key"])
        self.assertIn("emergency_released_at", operation_state)
        self.assertEqual("긴급정지", button.setText.call_args.args[0])

    def test_global_emergency_release_allows_review_required_but_keeps_latch_on_failed_write(self) -> None:
        import gui_main_emergency_ops as emergency
        import operation_policy_gate

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            operation_state_path = root / "runtime" / "operation_state.json"
            operation_state_path.parent.mkdir()
            operation_state_path.write_text(
                json.dumps({"emergency_stop": True}, ensure_ascii=False),
                encoding="utf-8",
            )
            first = root / "stocks" / "111111_첫번째"
            second = root / "stocks" / "222222_두번째"
            first.mkdir(parents=True)
            second.mkdir(parents=True)
            for stock_dir in (first, second):
                (stock_dir / "state.json").write_text(
                    json.dumps(
                        {
                            "status": "EMERGENCY_STOPPED",
                            "review_required": False,
                            "emergency_scope": "GLOBAL",
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
            status_bar = SimpleNamespace(showMessage=Mock())
            button = SimpleNamespace(setText=Mock())
            main = SimpleNamespace(
                all_runtime_stock_dirs=lambda: [first, second],
                refresh_auto_trade_assignment_views=Mock(),
                refresh_all=Mock(),
                statusBar=lambda: status_bar,
                btn_emergency_stop=button,
            )

            with (
                patch.object(operation_policy_gate, "OPERATION_STATE_PATH", operation_state_path),
                patch.object(
                    emergency,
                    "_release_global_emergency_stop_target",
                    side_effect=[emergency.RELEASED_TO_REVIEW, emergency.RELEASE_FAILED],
                ),
                patch.object(emergency, "append_changelog"),
                patch.object(emergency, "show_toast") as toast,
            ):
                emergency.release_emergency_stop(main)

            operation_state = json.loads(operation_state_path.read_text(encoding="utf-8"))

        self.assertTrue(operation_state["emergency_stop"])
        self.assertIn("정지해제 미완료", toast.call_args.kwargs["message"])
        self.assertEqual("정지해제", button.setText.call_args.args[0])

    def test_global_emergency_release_with_no_targets_retries_false_write(self) -> None:
        import gui_main_emergency_ops as emergency
        import operation_policy_gate

        with tempfile.TemporaryDirectory() as temp:
            operation_state_path = Path(temp) / "runtime" / "operation_state.json"
            operation_state_path.parent.mkdir()
            operation_state_path.write_text(
                json.dumps({"emergency_stop": True}, ensure_ascii=False),
                encoding="utf-8",
            )
            status_bar = SimpleNamespace(showMessage=Mock())
            main = SimpleNamespace(
                all_runtime_stock_dirs=lambda: [],
                refresh_auto_trade_assignment_views=Mock(),
                refresh_all=Mock(),
                statusBar=lambda: status_bar,
            )

            with (
                patch.object(operation_policy_gate, "OPERATION_STATE_PATH", operation_state_path),
                patch.object(emergency, "append_changelog"),
                patch.object(emergency, "show_toast"),
            ):
                emergency.release_emergency_stop(main)

            operation_state = json.loads(operation_state_path.read_text(encoding="utf-8"))

        self.assertFalse(operation_state["emergency_stop"])

    def test_global_emergency_release_false_write_failure_is_not_success(self) -> None:
        import gui_main_emergency_ops as emergency

        status_bar = SimpleNamespace(showMessage=Mock())
        button = SimpleNamespace(setText=Mock())
        main = SimpleNamespace(
            all_runtime_stock_dirs=lambda: [],
            refresh_auto_trade_assignment_views=Mock(),
            refresh_all=Mock(),
            statusBar=lambda: status_bar,
            btn_emergency_stop=button,
        )

        with (
            patch.object(
                emergency,
                "write_global_emergency_stop_state",
                return_value={"ok": False, "error": "fail"},
            ) as writer,
            patch.object(emergency, "read_operation_state", return_value={"emergency_stop": True}),
            patch.object(emergency, "append_changelog"),
            patch.object(emergency, "show_toast") as toast,
        ):
            emergency.release_emergency_stop(main)

        writer.assert_called_once()
        self.assertIn("정지해제 미완료", toast.call_args.kwargs["message"])
        self.assertIn("전역 차단 유지", status_bar.showMessage.call_args.args[0])
        self.assertEqual("정지해제", button.setText.call_args.args[0])

    def test_selected_emergency_paths_do_not_modify_global_operation_state(self) -> None:
        import gui_main_emergency_ops as emergency
        import operation_policy_gate

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            operation_state_path = root / "runtime" / "operation_state.json"
            operation_state_path.parent.mkdir()
            before = {"emergency_stop": False, "existing_key": "preserve"}
            operation_state_path.write_text(
                json.dumps(before, ensure_ascii=False),
                encoding="utf-8",
            )
            stock_dir = root / "stocks" / "003550_LG"
            stock_dir.mkdir(parents=True)
            (stock_dir / "state.json").write_text(
                json.dumps({"status": "STOPPED"}, ensure_ascii=False),
                encoding="utf-8",
            )
            window = SimpleNamespace(
                refresh_all=Mock(),
                statusBarMessage=Mock(),
            )

            with (
                patch.object(operation_policy_gate, "OPERATION_STATE_PATH", operation_state_path),
                patch.object(emergency, "append_changelog"),
                patch.object(emergency, "append_stock_log"),
                patch.object(emergency, "show_toast"),
                patch.object(emergency.QMessageBox, "critical"),
            ):
                emergency.execute_selected_emergency_stop(
                    window,
                    [(stock_dir, "003550", "LG")],
                )
                emergency.execute_selected_emergency_release(
                    window,
                    [(stock_dir, "003550", "LG")],
                )

            after = json.loads(operation_state_path.read_text(encoding="utf-8"))

        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
