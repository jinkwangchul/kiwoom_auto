import unittest
import json
from dataclasses import replace
from datetime import datetime
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import MethodType, SimpleNamespace
from unittest.mock import MagicMock, patch

from PyQt5.QtCore import QObject, QPoint, Qt
from PyQt5.QtGui import QMouseEvent
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QStyleOptionGroupBox,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

from routine_instance_registry import RoutineDefinitionRecord, RoutineInstanceRecord

import gui_auto_trade_setting_window as setting_window
import gui_auto_trade_table_loader as table_loader
import gui_routine_policy as routine_policy
from gui_auto_trade_setting_window import AutoTradeSettingWindow


class AutoTradeSettingRoutineTreeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._app_env_patcher = patch.dict(
            os.environ,
            {setting_window.AUTO_TRADE_SETTING_APP_ENV: "production"},
        )
        self._app_env_patcher.start()

    def tearDown(self) -> None:
        self._app_env_patcher.stop()

    def test_operation_environment_uses_routine_close_display_terms_only(
        self,
    ) -> None:
        import gui_operation_environment as environment

        with patch.object(
            environment,
            "read_operation_policy",
            return_value=environment.default_operation_policy(),
        ):
            dialog = environment.OperationEnvironmentSettingsDialog()
        try:
            self.assertEqual("루틴마감", dialog.auto_close_signal.text())
            self.assertEqual("루틴마감", dialog.early_close_signal.text())
            self.assertEqual("루틴마감", dialog.auto_close_checks[0].text())
            self.assertEqual("루틴마감", dialog.early_close_checks[0].text())
            title_texts = {
                label.text()
                for label in dialog.findChildren(environment.QLabel)
            }
            self.assertTrue(
                {"4. 자동마감 설정", "5. 조기마감 설정"}.issubset(
                    title_texts
                )
            )
            self.assertEqual(
                "루틴매도신호",
                dialog.auto_close_method.itemText(0),
            )
            self.assertEqual(
                "루틴매도신호",
                dialog.early_close_method.itemText(0),
            )
            label_texts = [
                label.text()
                for label in dialog.findChildren(environment.QLabel)
            ]
            self.assertIn("|", label_texts)
            self.assertNotIn("/", label_texts)
            self.assertEqual("청산정책 적용", dialog.manual_liquidation.text())

            dialog.show()
            self._app.processEvents()
            self.assertGreaterEqual(
                dialog.manual_liquidation.width(),
                dialog.manual_liquidation.sizeHint().width(),
            )
            was_checked = dialog.manual_liquidation.isChecked()
            dialog.manual_liquidation.click()
            self.assertEqual(
                not was_checked,
                dialog.manual_liquidation.isChecked(),
            )

            screenshot_path = os.environ.get(
                "AUTO_TRADE_ROUTINE_CLOSE_TERMS_SCREENSHOT_PATH",
                "",
            ).strip()
            if screenshot_path:
                self.assertTrue(dialog.grab().save(screenshot_path))
        finally:
            dialog.close()
            dialog.deleteLater()
            self._app.processEvents()

    def test_operation_environment_omits_unapproved_after_buy_end_setting(
        self,
    ) -> None:
        import gui_operation_environment as environment

        legacy_policy = environment.default_operation_policy()
        legacy_policy["scheduled_operation"]["after_buy_end_status"] = "감시/대기"
        original_read_operation_policy = environment.read_operation_policy

        with (
            patch.object(
                environment,
                "read_operation_policy",
                return_value=legacy_policy,
            ),
            TemporaryDirectory() as temp,
        ):
            dialog = environment.OperationEnvironmentSettingsDialog()
            policy_path = Path(temp) / "operation_policy.json"
            try:
                labels = {
                    label.text()
                    for label in dialog.findChildren(environment.QLabel)
                }
                self.assertNotIn("매수종료 후", labels)
                self.assertFalse(hasattr(dialog, "scheduled_after_status"))

                built_policy = dialog.build_policy_from_widgets()
                self.assertEqual(
                    {
                        "default_start_time",
                        "default_end_buy_time",
                    },
                    set(built_policy["scheduled_operation"]),
                )

                with patch.object(
                    environment,
                    "OPERATION_POLICY_PATH",
                    policy_path,
                ):
                    environment.write_operation_policy(legacy_policy)
                    saved_policy = json.loads(
                        policy_path.read_text(encoding="utf-8")
                    )
                    self.assertNotIn(
                        "after_buy_end_status",
                        saved_policy["scheduled_operation"],
                    )

                    policy_path.write_text(
                        json.dumps(legacy_policy, ensure_ascii=False),
                        encoding="utf-8",
                    )
                    loaded_policy = original_read_operation_policy()
                    self.assertNotIn(
                        "after_buy_end_status",
                        loaded_policy["scheduled_operation"],
                    )
            finally:
                dialog.close()
                dialog.deleteLater()
                self._app.processEvents()

    def test_scheduled_status_contract_is_fixed_after_buy_end(self) -> None:
        import state_policy

        legacy_policy = state_policy.default_operation_policy()
        legacy_policy["scheduled_operation"]["after_buy_end_status"] = "감시/대기"
        config = {
            "operation_mode": "SCHEDULED",
            "start_time": "09:00:00",
            "end_buy_time": "13:30:00",
        }
        scenarios = (
            (datetime(2026, 7, 24, 8, 59), "MONITORING"),
            (datetime(2026, 7, 24, 9, 30), "RUNNING"),
            (datetime(2026, 7, 24, 13, 31), "AUTO_CLOSE"),
            (datetime(2026, 7, 24, 15, 21), "MONITORING"),
        )

        with TemporaryDirectory() as temp:
            policy_path = Path(temp) / "operation_policy.json"
            policy_path.write_text(
                json.dumps(legacy_policy, ensure_ascii=False),
                encoding="utf-8",
            )
            with patch.object(state_policy, "OPERATION_POLICY_PATH", policy_path):
                loaded_policy = state_policy.read_operation_policy()
                self.assertNotIn(
                    "after_buy_end_status",
                    loaded_policy["scheduled_operation"],
                )

                for now_dt, expected in scenarios:
                    with self.subTest(now_dt=now_dt):
                        self.assertEqual(
                            expected,
                            state_policy.scheduled_status_for_now(config, now_dt),
                        )

    def _definition(self) -> RoutineDefinitionRecord:
        return RoutineDefinitionRecord(
            definition_id="indicator_follow",
            display_name="지표추종매매",
            package_dir=Path("routines") / "indicator_follow",
            schema_version="1.0",
            version="1.0",
            routine_type="auto_trade",
            entry_file="routine.py",
            module_name="indicator_follow_routine",
            settings_ui="indicator_follow",
            default_rules_file="rules.json",
            package_enabled=True,
            source_name="routine.json",
        )

    def _instance(self, instance_id: str, name: str) -> RoutineInstanceRecord:
        return RoutineInstanceRecord(
            instance_id=instance_id,
            definition_id="indicator_follow",
            display_name=name,
            source_routine_name="지표추종매매",
            persisted=True,
            source="PERSISTED",
            enabled=False,
            real_trade_allowed=False,
            rules_path=Path("routine_instances") / instance_id / "rules.json",
            schema_version="1.0",
        )

    def _window_harness(self):
        class Harness(QObject):
            pass

        harness = Harness()
        harness.routine_table = QTableWidget(0, 1)
        harness.stock_table = QTableWidget(0, 1)
        harness.routine_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        harness.routine_table.setSelectionMode(QAbstractItemView.SingleSelection)
        harness._collapsed_auto_trade_definition_ids = set()
        harness._default_operation_instance_by_definition = {}
        harness._routine_operation_status_by_instance = {}
        harness._stock_status_filter = "all"
        harness._collapsed_auto_trade_instance_ids = set()
        harness._routine_tree_display_level = "category"
        harness._routine_tree_display_scope = ""
        harness._routine_tree_last_stock_scope = "all"
        harness._routine_tree_display_criterion = "profit"
        harness._routine_tree_stock_performance_sort_active = False
        harness._routine_tree_valid_only = False
        harness._hidden_historical_stock_fixture_keys = set()
        harness._routine_instance_name_editor = None
        harness._routine_instance_name_editor_instance_id = ""
        harness._routine_instance_name_editor_original = ""
        harness._routine_instance_name_edit_finishing = False
        for name in (
            "_setup_routine_table",
            "_setup_stock_table",
            "_sync_stock_table_header_background_to_body",
            "_apply_stock_table_column_widths",
            "_routine_instance_stock_counts",
            "_current_stock_entries_by_instance",
            "_current_stocks_by_instance",
            "_historical_stocks_by_instance",
            "_routine_instance_operation_counts",
            "_is_default_operation_instance",
            "_routine_status_text_for_metadata",
            "set_default_operation_instance_from_metadata",
            "_refresh_default_operation_stamps",
            "_routine_tree_stock_performance_source",
            "_routine_tree_stock_group_performance_source",
            "_routine_tree_group_stock_rows_by_code",
            "_routine_tree_performance_texts",
            "_routine_tree_metric_text_parts",
            "_routine_tree_metric_values",
            "_routine_tree_row_sort_value",
            "_routine_tree_sort_definition_blocks",
            "_routine_tree_sort_instance_blocks",
            "_routine_tree_row_widget",
            "_set_routine_tree_parent_summary_visible",
            "_setup_routine_tree_display_level_badges",
            "_position_routine_tree_display_level_badges",
            "_update_routine_tree_display_level_badges",
            "_routine_tree_scope_filter_available",
            "_set_routine_tree_valid_only",
            "_set_routine_tree_display_scope",
            "_refresh_routine_tree_display_state",
            "_set_routine_tree_display_criterion",
            "_apply_routine_tree_display_level_command",
            "_set_routine_tree_display_level",
            "_toggle_routine_definition_collapsed",
            "_toggle_routine_instance_collapsed",
            "_routine_tree_toggle_enabled",
            "_apply_routine_tree_collapse_visibility",
            "eventFilter",
            "load_routine_table",
            "current_selected_routine_row_metadata",
            "current_selected_definition_id",
            "current_selected_instance_id",
            "current_selected_instance_dir",
            "current_selected_target_instance_ids",
            "current_selected_routine_name",
            "current_selected_routine_dir",
            "restore_routine_selection",
            "restore_routine_selection_metadata",
            "on_routine_table_item_clicked",
            "on_routine_table_item_double_clicked",
            "on_routine_table_context_menu",
            "_open_routine_settings_dialog",
            "open_routine_registration",
            "open_routine_instance_settings",
            "rename_routine_instance",
            "finish_routine_instance_name_edit",
            "delete_routine_instance",
            "open_stock_register_window",
            "open_instance_stock_search_register_window",
            "convert_historical_stock_to_registered",
            "hide_historical_stock_display",
            "on_routine_selection_changed",
            "auto_trade_runtime_state_for_order",
            "update_selection_summary_panel",
            "_setup_selected_routine_status_bar",
            "set_stock_status_filter",
            "update_selected_routine_status_bar",
            "_stock_operation_status_label",
            "load_selected_routine_stocks",
        ):
            setattr(harness, name, MethodType(getattr(AutoTradeSettingWindow, name), harness))
        return harness

    def test_routine_tree_context_menu_contract_by_row_kind(self) -> None:
        window = self._window_harness()
        metadata_by_kind = {
            "definition": {
                "row_kind": "definition",
                "definition_id": "indicator_follow",
                "definition_name": "지표추종매매",
            },
            "instance": {
                "row_kind": "instance",
                "definition_id": "indicator_follow",
                "instance_id": "inst-a",
                "instance_name": "A 인스턴스",
            },
            "stock": {
                "row_kind": "stock",
                "definition_id": "indicator_follow",
                "instance_id": "inst-a",
                "stock_path": "stocks/005930_삼성전자",
            },
        }
        item = QTableWidgetItem()
        window.routine_table.setRowCount(1)
        window.routine_table.setItem(0, 0, item)

        for row_kind, expected_labels in (
            ("definition", ["루틴등록"]),
            ("instance", ["루틴수정", "루틴삭제", "이름변경", "종목등록"]),
        ):
            item.setData(Qt.UserRole, metadata_by_kind[row_kind])
            menu = MagicMock()
            actions = [MagicMock() for _label in expected_labels]
            menu.addAction.side_effect = actions
            with (
                patch.object(window.routine_table, "itemAt", return_value=item),
                patch.object(setting_window, "QMenu", return_value=menu),
            ):
                window.on_routine_table_context_menu(QPoint(1, 1))
            self.assertEqual(
                expected_labels,
                [call.args[0] for call in menu.addAction.call_args_list],
            )
            menu.exec_.assert_called_once()
            for action in actions:
                action.triggered.connect.assert_called_once()

        item.setData(Qt.UserRole, metadata_by_kind["stock"])
        with (
            patch.object(window.routine_table, "itemAt", return_value=item),
            patch.object(setting_window, "QMenu") as menu_factory,
        ):
            window.on_routine_table_context_menu(QPoint(1, 1))
        menu_factory.assert_not_called()

    def test_routine_tree_context_actions_dispatch_to_captured_target(self) -> None:
        window = self._window_harness()
        item = QTableWidgetItem()
        window.routine_table.setRowCount(1)
        window.routine_table.setItem(0, 0, item)

        cases = (
            (
                {
                    "row_kind": "definition",
                    "definition_id": "indicator_follow",
                },
                "open_routine_registration",
                0,
            ),
            (
                {
                    "row_kind": "instance",
                    "definition_id": "indicator_follow",
                    "instance_id": "inst-a",
                    "instance_name": "A 인스턴스",
                },
                "open_routine_instance_settings",
                0,
            ),
            (
                {
                    "row_kind": "instance",
                    "definition_id": "indicator_follow",
                    "instance_id": "inst-a",
                    "instance_name": "A 인스턴스",
                },
                "delete_routine_instance",
                1,
                True,
            ),
            (
                {
                    "row_kind": "instance",
                    "definition_id": "indicator_follow",
                    "instance_id": "inst-a",
                    "instance_name": "A 인스턴스",
                },
                "rename_routine_instance",
                2,
                True,
            ),
            (
                {
                    "row_kind": "instance",
                    "definition_id": "indicator_follow",
                    "instance_id": "inst-a",
                    "instance_name": "A 인스턴스",
                },
                "open_instance_stock_search_register_window",
                3,
                True,
            ),
        )
        for case in cases:
            if len(case) == 3:
                metadata, method_name, action_index = case
                expects_metadata = True
            else:
                metadata, method_name, action_index, expects_metadata = case
            item.setData(Qt.UserRole, metadata)
            menu = MagicMock()
            action_count = 1 if metadata["row_kind"] == "definition" else 4
            actions = [MagicMock() for _index in range(action_count)]
            callbacks = []
            for action in actions:
                action.triggered.connect.side_effect = callbacks.append
            menu.addAction.side_effect = actions
            with (
                patch.object(window.routine_table, "itemAt", return_value=item),
                patch.object(setting_window, "QMenu", return_value=menu),
                patch.object(window, method_name) as target_method,
            ):
                window.on_routine_table_context_menu(QPoint(1, 1))
                callbacks[action_index](False)
            if expects_metadata:
                target_method.assert_called_once_with(metadata)
            else:
                target_method.assert_called_once_with()

    def test_instance_stock_register_context_uses_search_dialog_not_legacy_window(self) -> None:
        window = self._window_harness()
        metadata = {
            "row_kind": "instance",
            "definition_id": "indicator_follow",
            "instance_id": "inst-a",
            "instance_name": "A 인스턴스",
        }
        item = QTableWidgetItem()
        item.setData(Qt.UserRole, metadata)
        window.routine_table.setRowCount(1)
        window.routine_table.setItem(0, 0, item)
        menu = MagicMock()
        actions = [MagicMock() for _index in range(4)]
        callbacks = []
        for action in actions:
            action.triggered.connect.side_effect = callbacks.append
        menu.addAction.side_effect = actions

        with (
            patch.object(window.routine_table, "itemAt", return_value=item),
            patch.object(setting_window, "QMenu", return_value=menu),
            patch.object(window, "open_stock_register_window") as legacy_open,
            patch.object(window, "open_instance_stock_search_register_window") as search_open,
        ):
            window.on_routine_table_context_menu(QPoint(1, 1))
            callbacks[3](False)

        legacy_open.assert_not_called()
        search_open.assert_called_once_with(metadata)

    def test_instance_stock_search_dialog_filters_library_and_keeps_instance_metadata(self) -> None:
        metadata = {
            "row_kind": "instance",
            "definition_id": "indicator_follow",
            "instance_id": "inst-a",
            "instance_name": "A 인스턴스",
        }
        library = [
            {"code": "005930", "name": "삼성전자", "market": "KOSPI"},
            {"code": "006400", "name": "삼성SDI", "market": "KOSPI"},
            {"code": "005380", "name": "현대차", "market": "KOSPI"},
            {"code": "000660", "name": "SKHynix"},
            {"code": "035420", "name": "NAVER"},
        ]
        with patch.object(setting_window, "load_stock_library", return_value=library), \
                patch.object(setting_window, "read_base_stocks", return_value=[]):
            dialog = setting_window.InstanceStockSearchRegisterDialog(
                None,
                instance_metadata=metadata,
            )
            self.addCleanup(dialog.close)

            self.assertEqual("종목등록", dialog.windowTitle())
            self.assertEqual(metadata, dialog.instance_metadata)
            self.assertEqual("검색", dialog.btn_search.text())
            self.assertEqual(
                QAbstractItemView.ExtendedSelection,
                dialog.result_table.selectionMode(),
            )
            self.assertIn("#dbeafe", dialog.result_table.styleSheet())
            self.assertEqual(3, dialog.result_table.columnCount())
            self.assertEqual(
                ["종목코드", "종목명", "분류"],
                [
                    dialog.result_table.horizontalHeaderItem(column).text()
                    for column in range(dialog.result_table.columnCount())
                ],
            )
            self.assertEqual(0, dialog.result_table.rowCount())

            dialog.search_input.setText("삼성")
            self.assertEqual(2, dialog.result_table.rowCount())
            self.assertEqual("005930", dialog.result_table.item(0, 0).text())
            self.assertEqual("삼성전자", dialog.result_table.item(0, 1).text())
            self.assertEqual("등록대기", dialog.result_table.item(0, 2).text())

            dialog.search_input.setText("삼, 현")
            self.assertEqual(
                ["005930", "006400", "005380"],
                [
                    dialog.result_table.item(row, 0).text()
                    for row in range(dialog.result_table.rowCount())
                ],
            )

            dialog.search_input.setText("삼， 현")
            self.assertEqual(
                ["005930", "006400", "005380"],
                [
                    dialog.result_table.item(row, 0).text()
                    for row in range(dialog.result_table.rowCount())
                ],
            )

            dialog.search_input.setText("066")
            self.assertEqual(1, dialog.result_table.rowCount())
            self.assertEqual("000660", dialog.result_table.item(0, 0).text())

            dialog.search_input.setText("s")
            self.assertEqual(
                ["006400", "000660"],
                [
                    dialog.result_table.item(row, 0).text()
                    for row in range(dialog.result_table.rowCount())
                ],
            )

            dialog.search_input.setText("SK")
            self.assertEqual(1, dialog.result_table.rowCount())
            self.assertEqual("000660", dialog.result_table.item(0, 0).text())

            dialog.search_input.setText("SDI")
            self.assertEqual(1, dialog.result_table.rowCount())
            self.assertEqual("006400", dialog.result_table.item(0, 0).text())

            dialog.search_input.setText("skhy")
            self.assertEqual(1, dialog.result_table.rowCount())
            self.assertEqual("SKHynix", dialog.result_table.item(0, 1).text())

            dialog.search_input.setText("")
            self.assertEqual(0, dialog.result_table.rowCount())

    def test_instance_stock_search_classification_uses_review_routine_or_pending(self) -> None:
        metadata = {
            "row_kind": "instance",
            "definition_id": "indicator_follow",
            "instance_id": "inst-a",
            "instance_name": "A 인스턴스",
        }
        with TemporaryDirectory() as temp:
            root = Path(temp)
            review_dir = root / "111111_검토종목"
            routine_dir = root / "222222_이동종목"
            pending_dir = root / "333333_대기종목"
            review_dir.mkdir()
            routine_dir.mkdir()
            pending_dir.mkdir()
            (review_dir / "state.json").write_text(
                json.dumps({"status": "REVIEW_REQUIRED"}, ensure_ascii=False),
                encoding="utf-8",
            )
            repository = MagicMock()

            def resolve_stock_dir(code, _name):
                return {
                    "111111": review_dir,
                    "222222": routine_dir,
                    "333333": pending_dir,
                }[code]

            repository.resolve_stock_dir.side_effect = resolve_stock_dir

            with (
                patch.object(setting_window, "load_stock_library", return_value=[]),
                patch.object(setting_window, "StockRepository", return_value=repository),
                patch.object(
                    setting_window,
                    "read_base_stocks",
                    return_value=[
                        {
                            "code": "222222",
                            "name": "이동종목",
                            "assigned_routine_instance_id": "inst-b",
                        }
                    ],
                ),
                patch.object(
                    setting_window,
                    "load_persisted_routine_instances",
                    return_value=[
                        SimpleNamespace(
                            instance_id="inst-b",
                            display_name="B 루틴",
                        )
                    ],
                ),
            ):
                dialog = setting_window.InstanceStockSearchRegisterDialog(
                    None,
                    instance_metadata=metadata,
                )
                self.addCleanup(dialog.close)

                self.assertEqual(
                    "검토관리",
                    dialog._classification_text("111111", "검토종목"),
                )
                self.assertEqual(
                    "B 루틴",
                    dialog._classification_text("222222", "이동종목"),
                )
                self.assertEqual(
                    "등록대기",
                    dialog._classification_text("333333", "대기종목"),
                )

    def test_instance_stock_search_headers_sort_all_columns_without_losing_row_data(self) -> None:
        metadata = {
            "row_kind": "instance",
            "definition_id": "indicator_follow",
            "instance_id": "inst-a",
            "instance_name": "A 인스턴스",
        }
        library = [
            {"code": "222222", "name": "나종목", "market": "KOSPI"},
            {"code": "111111", "name": "다종목", "market": "KOSPI"},
            {"code": "333333", "name": "가종목", "market": "KOSPI"},
        ]
        classification_by_code = {
            "111111": "A분류",
            "222222": "C분류",
            "333333": "B분류",
        }

        with (
            patch.object(setting_window, "load_stock_library", return_value=library),
            patch.object(setting_window, "read_base_stocks", return_value=[]),
            patch.object(
                setting_window.InstanceStockSearchRegisterDialog,
                "_classification_text",
                lambda _self, code, _name: classification_by_code[code],
            ),
        ):
            dialog = setting_window.InstanceStockSearchRegisterDialog(
                None,
                instance_metadata=metadata,
            )
            self.addCleanup(dialog.close)
            dialog.search_input.setText("종목")

        def column_values(column: int) -> list[str]:
            return [
                dialog.result_table.item(row, column).text()
                for row in range(dialog.result_table.rowCount())
            ]

        self.assertEqual(["222222", "111111", "333333"], column_values(0))

        dialog.on_result_header_clicked(0)
        self.assertEqual(["111111", "222222", "333333"], column_values(0))
        dialog.on_result_header_clicked(0)
        self.assertEqual(["333333", "222222", "111111"], column_values(0))

        dialog.on_result_header_clicked(1)
        self.assertEqual(["가종목", "나종목", "다종목"], column_values(1))

        dialog.on_result_header_clicked(2)
        self.assertEqual(["A분류", "B분류", "C분류"], column_values(2))

        self.assertEqual(("111111", "다종목"), dialog._result_stock_at_row(0))

    def test_instance_stock_search_register_success_refreshes_classification_cell(self) -> None:
        dialog, parent = self._instance_stock_search_dialog()
        dialog.instance_metadata = {
            "definition_id": "indicator_follow",
            "definition_name": "지표추종매매",
            "instance_id": "inst-a",
            "instance_name": "A 루틴",
        }
        self._set_instance_stock_search_rows(
            dialog,
            [
                ("222222", "다른종목"),
                ("111111", "대상종목"),
            ],
        )
        for row in range(dialog.result_table.rowCount()):
            dialog.result_table.setItem(row, 2, QTableWidgetItem("등록대기"))
        dialog.on_result_header_clicked(2)
        dialog.selectRow = dialog.result_table.selectRow
        dialog.result_table.selectRow(1)
        registered: list[dict[str, object]] = []

        def read_registered():
            return list(registered)

        def update_instance(
            code,
            name,
            *,
            instance_id,
            instance_name,
            definition_id,
            routine_type,
        ):
            registered[:] = [
                {
                    "code": code,
                    "name": name,
                    "assigned_routine_instance_id": instance_id,
                }
            ]
            return True

        stock_dir = Path("stocks") / "dummy"
        repository = MagicMock()
        repository.resolve_stock_dir.return_value = stock_dir
        repository.ensure_stock_folder.return_value = stock_dir

        with (
            patch.object(
                setting_window,
                "find_library_stock_by_code",
                return_value={"code": "111111", "name": "대상종목"},
            ),
            patch.object(setting_window, "read_base_stocks", side_effect=read_registered),
            patch.object(setting_window, "append_base_stock", return_value=True),
            patch.object(
                setting_window,
                "update_base_stock_routine_instance",
                side_effect=update_instance,
            ),
            patch.object(setting_window, "StockRepository", return_value=repository),
            patch.object(setting_window, "ensure_single_real_trade_routine_for_stock"),
            patch.object(setting_window, "append_changelog"),
            patch("gui_routine_assign_window.apply_default_operation_exclusion_for_new_running_assignment"),
            patch.object(setting_window, "show_toast"),
        ):
            self.assertTrue(dialog.register_or_assign_result_row(1))

        row = dialog._find_result_row_by_stock_code("111111")
        self.assertGreaterEqual(row, 0)
        self.assertEqual("111111", dialog.result_table.item(row, 0).text())
        self.assertEqual("대상종목", dialog.result_table.item(row, 1).text())
        self.assertEqual("A 루틴", dialog.result_table.item(row, 2).text())
        self.assertEqual(("111111", "대상종목"), dialog._result_stock_at_row(row))
        self.assertEqual([row], [index.row() for index in dialog.result_table.selectionModel().selectedRows()])
        parent.refresh_all.assert_called()

    def test_instance_stock_search_register_failure_keeps_classification_cell(self) -> None:
        dialog, _parent = self._instance_stock_search_dialog()
        dialog.instance_metadata = {
            "definition_id": "indicator_follow",
            "definition_name": "지표추종매매",
            "instance_id": "inst-a",
            "instance_name": "A 루틴",
        }
        self._set_instance_stock_search_rows(dialog, [("111111", "대상종목")])
        dialog.result_table.setItem(0, 2, QTableWidgetItem("등록대기"))

        with (
            patch.object(
                setting_window,
                "find_library_stock_by_code",
                return_value={"code": "111111", "name": "대상종목"},
            ),
            patch.object(setting_window, "read_base_stocks", return_value=[]),
            patch.object(setting_window, "append_base_stock", return_value=False),
            patch.object(setting_window, "show_toast"),
        ):
            self.assertFalse(dialog.register_or_assign_result_row(0))

        self.assertEqual("등록대기", dialog.result_table.item(0, 2).text())

    def test_instance_stock_search_review_classification_is_not_overwritten_by_routine(self) -> None:
        dialog, _parent = self._instance_stock_search_dialog()
        dialog.instance_metadata = {
            "definition_id": "indicator_follow",
            "definition_name": "지표추종매매",
            "instance_id": "inst-a",
            "instance_name": "A 루틴",
        }
        self._set_instance_stock_search_rows(dialog, [("111111", "검토종목")])
        dialog.result_table.setItem(0, 2, QTableWidgetItem("등록대기"))
        stock_dir = Path("stocks") / "review"
        repository = MagicMock()
        repository.resolve_stock_dir.return_value = stock_dir

        with (
            patch.object(setting_window, "StockRepository", return_value=repository),
            patch.object(
                setting_window,
                "read_json_dict",
                return_value={"status": "REVIEW_REQUIRED"},
            ),
            patch.object(
                setting_window,
                "read_base_stocks",
                return_value=[
                    {
                        "code": "111111",
                        "name": "검토종목",
                        "assigned_routine_instance_id": "inst-a",
                    }
                ],
            ),
        ):
            self.assertTrue(dialog._refresh_classification_for_stock("111111"))

        self.assertEqual("검토관리", dialog.result_table.item(0, 2).text())

    def test_instance_stock_search_result_context_menu_selects_and_clears_rows(self) -> None:
        dialog, _parent = self._instance_stock_search_dialog()
        dialog.result_table.setRowCount(3)
        for row, code in enumerate(("005930", "005380", "000660")):
            dialog.result_table.setItem(row, 0, QTableWidgetItem(code))
            dialog.result_table.setItem(row, 1, QTableWidgetItem(f"종목{row}"))

        menu = MagicMock()
        actions = [MagicMock(), MagicMock(), MagicMock(), MagicMock()]
        callbacks = []
        for action in actions:
            action.triggered.connect.side_effect = callbacks.append
        menu.addAction.side_effect = actions

        with patch.object(setting_window, "QMenu", return_value=menu):
            dialog.on_result_table_context_menu(QPoint(1, 1))

        self.assertEqual(
            ["전체선택", "선택해제", "선택등록", "등록해제"],
            [call.args[0] for call in menu.addAction.call_args_list],
        )

        callbacks[0](False)
        self.assertEqual(3, len(dialog.result_table.selectionModel().selectedRows()))

        callbacks[1](False)
        self.assertEqual(0, len(dialog.result_table.selectionModel().selectedRows()))

    def test_instance_stock_search_unregister_success_refreshes_classification_cell(self) -> None:
        dialog, parent = self._instance_stock_search_dialog()
        self._set_instance_stock_search_rows(dialog, [("005930", "삼성전자")])
        dialog.result_table.setItem(0, 2, QTableWidgetItem("A 인스턴스"))
        dialog.result_table.selectRow(0)
        registered = [
            {
                "code": "005930",
                "name": "삼성전자",
                "assigned_routine_instance_id": "inst-a",
            }
        ]
        stock_dir = Path("stocks") / "dummy"
        repository = MagicMock()
        repository.resolve_stock_dir.return_value = stock_dir

        def update_routines(code, name, routines):
            self.assertEqual(("005930", "삼성전자", []), (code, name, routines))
            registered[0]["assigned_routine_instance_id"] = ""
            return True

        with (
            patch.object(setting_window, "read_base_stocks", side_effect=lambda: list(registered)),
            patch.object(setting_window, "StockRepository", return_value=repository),
            patch.object(setting_window, "read_json_dict", return_value={}),
            patch.object(
                setting_window,
                "can_unassign_active_routine_from_stock",
                return_value=(True, "A 인스턴스", []),
            ),
            patch.object(setting_window.QMessageBox, "question") as question,
            patch.object(setting_window, "update_base_stock_routines", side_effect=update_routines) as update,
            patch.object(setting_window, "ensure_single_real_trade_routine_for_stock") as ensure_routine,
            patch.object(setting_window, "show_toast") as toast,
        ):
            self.assertTrue(dialog.unregister_selected_result_rows())

        update.assert_called_once()
        question.assert_not_called()
        ensure_routine.assert_called_once_with("005930", "삼성전자")
        parent.refresh_all.assert_called_once_with()
        self.assertEqual("등록대기", dialog.result_table.item(0, 2).text())
        toast.assert_called_with(dialog, "등록해제 1건 | 삼성전자")

    def test_instance_stock_search_unregister_does_not_request_confirmation(self) -> None:
        dialog, parent = self._instance_stock_search_dialog()
        self._set_instance_stock_search_rows(dialog, [("005930", "삼성전자")])
        dialog.result_table.setItem(0, 2, QTableWidgetItem("A 인스턴스"))
        dialog.result_table.selectRow(0)
        registered = [
            {
                "code": "005930",
                "name": "삼성전자",
                "assigned_routine_instance_id": "inst-a",
            }
        ]

        def update_routines(_code, _name, _routines):
            registered[0]["assigned_routine_instance_id"] = ""
            return True

        with (
            patch.object(setting_window, "read_base_stocks", side_effect=lambda: list(registered)),
            patch.object(setting_window, "read_json_dict", return_value={}),
            patch.object(
                setting_window,
                "can_unassign_active_routine_from_stock",
                return_value=(True, "A 인스턴스", []),
            ),
            patch.object(setting_window.QMessageBox, "question") as question,
            patch.object(setting_window, "update_base_stock_routines", side_effect=update_routines) as update,
            patch.object(setting_window, "ensure_single_real_trade_routine_for_stock"),
            patch.object(setting_window, "show_toast") as toast,
        ):
            self.assertTrue(dialog.unregister_selected_result_rows())

        question.assert_not_called()
        update.assert_called_once()
        parent.refresh_all.assert_called_once_with()
        self.assertEqual("등록대기", dialog.result_table.item(0, 2).text())
        toast.assert_called_with(dialog, "등록해제 1건 | 삼성전자")

    def test_instance_stock_search_unregister_partial_failure_updates_only_successes(self) -> None:
        dialog, parent = self._instance_stock_search_dialog()
        self._set_instance_stock_search_rows(
            dialog,
            [("005930", "삼성전자"), ("005380", "현대차")],
        )
        for row in range(2):
            dialog.result_table.setItem(row, 2, QTableWidgetItem("A 인스턴스"))
        dialog.result_table.selectAll()
        registered = [
            {
                "code": "005930",
                "name": "삼성전자",
                "assigned_routine_instance_id": "inst-a",
            },
            {
                "code": "005380",
                "name": "현대차",
                "assigned_routine_instance_id": "inst-a",
            },
        ]
        stock_dir = Path("stocks") / "dummy"
        repository = MagicMock()
        repository.resolve_stock_dir.return_value = stock_dir

        def update_routines(code, _name, _routines):
            if code == "005930":
                registered[0]["assigned_routine_instance_id"] = ""
                return True
            return False

        with (
            patch.object(setting_window, "read_base_stocks", side_effect=lambda: list(registered)),
            patch.object(setting_window, "StockRepository", return_value=repository),
            patch.object(setting_window, "read_json_dict", return_value={}),
            patch.object(
                setting_window,
                "can_unassign_active_routine_from_stock",
                return_value=(True, "A 인스턴스", []),
            ),
            patch.object(setting_window.QMessageBox, "question") as question,
            patch.object(setting_window, "update_base_stock_routines", side_effect=update_routines),
            patch.object(setting_window, "ensure_single_real_trade_routine_for_stock"),
            patch.object(setting_window, "show_toast") as toast,
        ):
            self.assertTrue(dialog.unregister_selected_result_rows())

        question.assert_not_called()
        parent.refresh_all.assert_called_once_with()
        self.assertEqual("등록대기", dialog.result_table.item(0, 2).text())
        self.assertEqual("A 인스턴스", dialog.result_table.item(1, 2).text())
        toast.assert_called_with(dialog, "등록해제 1건 | 삼성전자")

    def test_instance_stock_search_unregister_skips_review_required_stock(self) -> None:
        dialog, parent = self._instance_stock_search_dialog()
        self._set_instance_stock_search_rows(dialog, [("005930", "삼성전자")])
        dialog.result_table.setItem(0, 2, QTableWidgetItem("검토관리"))
        dialog.result_table.selectRow(0)

        with (
            patch.object(dialog, "_is_review_required_stock", return_value=True),
            patch.object(setting_window, "can_unassign_active_routine_from_stock") as policy,
            patch.object(setting_window, "update_base_stock_routines") as update,
            patch.object(setting_window, "show_toast") as toast,
        ):
            self.assertFalse(dialog.unregister_selected_result_rows())

        policy.assert_not_called()
        update.assert_not_called()
        parent.refresh_all.assert_not_called()
        toast.assert_called_once_with(dialog, "등록해제할 종목이 없습니다.")

    def test_instance_stock_search_context_register_without_selection_uses_toast(self) -> None:
        dialog, _parent = self._instance_stock_search_dialog()
        with patch.object(setting_window, "show_toast") as toast:
            self.assertFalse(dialog.register_selected_result_rows())

        toast.assert_called_once_with(dialog, "등록할 종목을 선택하세요.")

    def _instance_stock_search_dialog(self):
        parent = QWidget()
        parent.refresh_all = MagicMock()
        metadata = {
            "row_kind": "instance",
            "definition_id": "indicator_follow",
            "definition_name": "지표추종매매",
            "instance_id": "inst-a",
            "instance_name": "A 인스턴스",
        }
        dialog = setting_window.InstanceStockSearchRegisterDialog(
            parent,
            instance_metadata=metadata,
        )
        dialog._test_parent_ref = parent
        self.addCleanup(dialog.close)
        dialog.result_table.setRowCount(1)
        dialog.result_table.setItem(0, 0, QTableWidgetItem("005930"))
        dialog.result_table.setItem(0, 1, QTableWidgetItem("삼성전자"))
        return dialog, parent

    def _set_instance_stock_search_rows(self, dialog, rows):
        dialog.result_table.setRowCount(len(rows))
        for row, (code, name) in enumerate(rows):
            dialog.result_table.setItem(row, 0, QTableWidgetItem(code))
            dialog.result_table.setItem(row, 1, QTableWidgetItem(name))

    def test_instance_stock_search_context_register_batches_selected_rows(self) -> None:
        dialog, parent = self._instance_stock_search_dialog()
        rows = [
            ("005930", "삼성전자"),
            ("005380", "현대차"),
            ("000660", "SK하이닉스"),
            ("035420", "NAVER"),
            ("035720", "카카오"),
        ]
        self._set_instance_stock_search_rows(dialog, rows)
        dialog.result_table.selectAll()
        registered = [
            {"code": "005380", "name": "현대차", "assigned_routine_instance_id": ""},
            {"code": "000660", "name": "SK하이닉스", "assigned_routine_instance_id": "inst-a"},
            {"code": "035420", "name": "NAVER", "assigned_routine_instance_id": "inst-b"},
            {"code": "035720", "name": "카카오", "assigned_routine_instance_id": "inst-c"},
        ]
        stock_dir = Path("stocks") / "dummy"
        repository = MagicMock()
        repository.resolve_stock_dir.return_value = stock_dir
        repository.ensure_stock_folder.return_value = stock_dir

        def library_stock(code):
            for item_code, item_name in rows:
                if item_code == code:
                    return {"code": item_code, "name": item_name}
            return None

        def assignment_guard(code, _name, allow_unassigned=True):
            if code == "035720":
                return False, {"reasons": ["보유 1"]}
            return True, {"reasons": []}

        with (
            patch.object(setting_window, "find_library_stock_by_code", side_effect=library_stock),
            patch.object(setting_window, "read_base_stocks", return_value=registered),
            patch.object(setting_window, "routine_action_reasons_for_stock", side_effect=assignment_guard),
            patch.object(setting_window.QMessageBox, "question", return_value=QMessageBox.Yes) as question,
            patch.object(setting_window, "append_base_stock", return_value=True) as append_stock,
            patch.object(setting_window, "update_base_stock_routine_instance", return_value=True) as update_instance,
            patch.object(setting_window, "StockRepository", return_value=repository),
            patch.object(setting_window, "ensure_single_real_trade_routine_for_stock"),
            patch.object(setting_window, "append_changelog"),
            patch("gui_routine_assign_window.apply_default_operation_exclusion_for_new_running_assignment"),
            patch.object(setting_window, "show_toast") as toast,
        ):
            self.assertTrue(dialog.register_selected_result_rows())

        append_stock.assert_called_once_with("005930", "삼성전자")
        self.assertEqual(3, update_instance.call_count)
        updated_codes = [call.args[0] for call in update_instance.call_args_list]
        self.assertEqual(["005930", "005380", "035420"], updated_codes)
        question.assert_called_once()
        parent.refresh_all.assert_called_once_with()
        toast.assert_called_with(
            dialog,
            "등록 2건 | 이동 1건 | 중복 1건 | 차단 1건",
        )

    def test_instance_stock_search_context_register_skips_routine_move_when_declined(self) -> None:
        dialog, parent = self._instance_stock_search_dialog()
        rows = [
            ("005930", "삼성전자"),
            ("035420", "NAVER"),
        ]
        self._set_instance_stock_search_rows(dialog, rows)
        dialog.result_table.selectAll()
        registered = [
            {"code": "035420", "name": "NAVER", "assigned_routine_instance_id": "inst-b"},
        ]
        stock_dir = Path("stocks") / "dummy"
        repository = MagicMock()
        repository.resolve_stock_dir.return_value = stock_dir
        repository.ensure_stock_folder.return_value = stock_dir

        def library_stock(code):
            for item_code, item_name in rows:
                if item_code == code:
                    return {"code": item_code, "name": item_name}
            return None

        with (
            patch.object(setting_window, "find_library_stock_by_code", side_effect=library_stock),
            patch.object(setting_window, "read_base_stocks", return_value=registered),
            patch.object(setting_window, "routine_action_reasons_for_stock", return_value=(True, {"reasons": []})),
            patch.object(setting_window.QMessageBox, "question", return_value=QMessageBox.No),
            patch.object(setting_window, "append_base_stock", return_value=True),
            patch.object(setting_window, "update_base_stock_routine_instance", return_value=True) as update_instance,
            patch.object(setting_window, "StockRepository", return_value=repository),
            patch.object(setting_window, "ensure_single_real_trade_routine_for_stock"),
            patch.object(setting_window, "append_changelog"),
            patch("gui_routine_assign_window.apply_default_operation_exclusion_for_new_running_assignment"),
            patch.object(setting_window, "show_toast") as toast,
        ):
            self.assertTrue(dialog.register_selected_result_rows())

        self.assertEqual(1, update_instance.call_count)
        self.assertEqual("005930", update_instance.call_args.args[0])
        parent.refresh_all.assert_called_once_with()
        toast.assert_called_with(
            dialog,
            "등록 1건 | 등록 취소 1건",
        )

    def test_instance_stock_search_context_register_declined_move_only_shows_cancel_toast(self) -> None:
        dialog, parent = self._instance_stock_search_dialog()
        rows = [
            ("005930", "삼성전자"),
            ("035420", "NAVER"),
        ]
        self._set_instance_stock_search_rows(dialog, rows)
        dialog.result_table.selectAll()
        registered = [
            {"code": "005930", "name": "삼성전자", "assigned_routine_instance_id": "inst-b"},
            {"code": "035420", "name": "NAVER", "assigned_routine_instance_id": "inst-c"},
        ]

        def library_stock(code):
            for item_code, item_name in rows:
                if item_code == code:
                    return {"code": item_code, "name": item_name}
            return None

        with (
            patch.object(setting_window, "find_library_stock_by_code", side_effect=library_stock),
            patch.object(setting_window, "read_base_stocks", return_value=registered),
            patch.object(setting_window, "routine_action_reasons_for_stock", return_value=(True, {"reasons": []})),
            patch.object(setting_window.QMessageBox, "question", return_value=QMessageBox.No),
            patch.object(setting_window, "update_base_stock_routine_instance") as update_instance,
            patch.object(setting_window, "show_toast") as toast,
        ):
            self.assertFalse(dialog.register_selected_result_rows())

        update_instance.assert_not_called()
        parent.refresh_all.assert_not_called()
        toast.assert_called_once_with(dialog, "등록 취소")

    def test_instance_stock_search_double_click_registers_and_assigns_new_stock(self) -> None:
        dialog, parent = self._instance_stock_search_dialog()
        stock_dir = Path("stocks") / "005930_삼성전자"
        repository = MagicMock()
        repository.resolve_stock_dir.return_value = stock_dir
        repository.ensure_stock_folder.return_value = stock_dir

        with (
            patch.object(setting_window, "find_library_stock_by_code", return_value={"code": "005930", "name": "삼성전자"}),
            patch.object(setting_window, "read_base_stocks", return_value=[]),
            patch.object(setting_window, "append_base_stock", return_value=True) as append_stock,
            patch.object(setting_window, "update_base_stock_routine_instance", return_value=True) as update_instance,
            patch.object(setting_window, "StockRepository", return_value=repository),
            patch.object(setting_window, "ensure_single_real_trade_routine_for_stock") as ensure_routine,
            patch.object(setting_window, "append_changelog"),
            patch("gui_routine_assign_window.apply_default_operation_exclusion_for_new_running_assignment") as apply_exclusion,
            patch.object(setting_window, "show_toast") as toast,
        ):
            self.assertTrue(dialog.register_or_assign_result_row(0))

        append_stock.assert_called_once_with("005930", "삼성전자")
        update_instance.assert_called_once_with(
            "005930",
            "삼성전자",
            instance_id="inst-a",
            instance_name="A 인스턴스",
            definition_id="indicator_follow",
            routine_type="지표추종매매",
        )
        repository.ensure_stock_folder.assert_called_once_with(
            "005930",
            "삼성전자",
            routine="지표추종매매",
        )
        ensure_routine.assert_called_once_with("005930", "삼성전자", "지표추종매매")
        apply_exclusion.assert_called_once()
        parent.refresh_all.assert_called_once_with()
        toast.assert_called_with(dialog, "종목 등록 및 지정이 완료됐습니다.")

    def test_instance_stock_search_double_click_assigns_unassigned_stock_only(self) -> None:
        dialog, _parent = self._instance_stock_search_dialog()
        stock_dir = Path("stocks") / "005930_삼성전자"
        repository = MagicMock()
        repository.resolve_stock_dir.return_value = stock_dir
        repository.ensure_stock_folder.return_value = stock_dir
        registered = {
            "code": "005930",
            "name": "삼성전자",
            "assigned_routine_instance_id": "",
        }

        with (
            patch.object(setting_window, "find_library_stock_by_code", return_value={"code": "005930", "name": "삼성전자"}),
            patch.object(setting_window, "read_base_stocks", return_value=[registered]),
            patch.object(setting_window, "append_base_stock") as append_stock,
            patch.object(setting_window, "update_base_stock_routine_instance", return_value=True) as update_instance,
            patch.object(setting_window, "StockRepository", return_value=repository),
            patch.object(setting_window, "ensure_single_real_trade_routine_for_stock"),
            patch.object(setting_window, "append_changelog"),
            patch("gui_routine_assign_window.apply_default_operation_exclusion_for_new_running_assignment"),
            patch.object(setting_window, "show_toast") as toast,
        ):
            self.assertTrue(dialog.register_or_assign_result_row(0))

        append_stock.assert_not_called()
        update_instance.assert_called_once()
        toast.assert_called_with(dialog, "종목 지정이 완료됐습니다.")

    def test_instance_stock_search_double_click_skips_same_instance(self) -> None:
        dialog, parent = self._instance_stock_search_dialog()
        registered = {
            "code": "005930",
            "name": "삼성전자",
            "assigned_routine_instance_id": "inst-a",
        }

        with (
            patch.object(setting_window, "find_library_stock_by_code", return_value={"code": "005930", "name": "삼성전자"}),
            patch.object(setting_window, "read_base_stocks", return_value=[registered]),
            patch.object(setting_window, "append_base_stock") as append_stock,
            patch.object(setting_window, "update_base_stock_routine_instance") as update_instance,
            patch.object(setting_window, "show_toast") as toast,
        ):
            self.assertFalse(dialog.register_or_assign_result_row(0))

        append_stock.assert_not_called()
        update_instance.assert_not_called()
        parent.refresh_all.assert_not_called()
        toast.assert_called_once_with(dialog, "이미 같은 인스턴스에 지정된 종목입니다.")

    def test_instance_stock_search_double_click_reassigns_other_instance_after_confirmation(self) -> None:
        dialog, _parent = self._instance_stock_search_dialog()
        stock_dir = Path("stocks") / "005930_삼성전자"
        repository = MagicMock()
        repository.resolve_stock_dir.return_value = stock_dir
        repository.ensure_stock_folder.return_value = stock_dir
        registered = {
            "code": "005930",
            "name": "삼성전자",
            "assigned_routine_instance_id": "inst-b",
        }

        with (
            patch.object(setting_window, "find_library_stock_by_code", return_value={"code": "005930", "name": "삼성전자"}),
            patch.object(setting_window, "read_base_stocks", return_value=[registered]),
            patch.object(setting_window, "routine_action_reasons_for_stock", return_value=(True, {"reasons": []})),
            patch.object(setting_window.QMessageBox, "question", return_value=QMessageBox.Yes) as question,
            patch.object(setting_window, "update_base_stock_routine_instance", return_value=True) as update_instance,
            patch.object(setting_window, "StockRepository", return_value=repository),
            patch.object(setting_window, "ensure_single_real_trade_routine_for_stock"),
            patch.object(setting_window, "append_changelog"),
            patch("gui_routine_assign_window.apply_default_operation_exclusion_for_new_running_assignment"),
            patch.object(setting_window, "show_toast") as toast,
        ):
            self.assertTrue(dialog.register_or_assign_result_row(0))

        question.assert_called_once()
        update_instance.assert_called_once()
        toast.assert_called_with(dialog, "종목 지정이 변경됐습니다.")

    def test_instance_stock_search_double_click_reassign_no_keeps_existing_assignment(self) -> None:
        dialog, _parent = self._instance_stock_search_dialog()
        registered = {
            "code": "005930",
            "name": "삼성전자",
            "assigned_routine_instance_id": "inst-b",
        }

        with (
            patch.object(setting_window, "find_library_stock_by_code", return_value={"code": "005930", "name": "삼성전자"}),
            patch.object(setting_window, "read_base_stocks", return_value=[registered]),
            patch.object(setting_window, "routine_action_reasons_for_stock", return_value=(True, {"reasons": []})),
            patch.object(setting_window.QMessageBox, "question", return_value=QMessageBox.No),
            patch.object(setting_window, "update_base_stock_routine_instance") as update_instance,
            patch.object(setting_window, "show_toast") as toast,
        ):
            self.assertFalse(dialog.register_or_assign_result_row(0))

        update_instance.assert_not_called()
        toast.assert_called_once_with(dialog, "등록 취소")

    def test_instance_stock_search_double_click_blocks_disallowed_reassign_with_toast(self) -> None:
        dialog, _parent = self._instance_stock_search_dialog()
        registered = {
            "code": "005930",
            "name": "삼성전자",
            "assigned_routine_instance_id": "inst-b",
        }

        with (
            patch.object(setting_window, "find_library_stock_by_code", return_value={"code": "005930", "name": "삼성전자"}),
            patch.object(setting_window, "read_base_stocks", return_value=[registered]),
            patch.object(
                setting_window,
                "routine_action_reasons_for_stock",
                return_value=(False, {"reasons": ["검토관리 종목입니다."]}),
            ),
            patch.object(setting_window.QMessageBox, "question") as question,
            patch.object(setting_window, "update_base_stock_routine_instance") as update_instance,
            patch.object(setting_window, "show_toast") as toast,
        ):
            self.assertFalse(dialog.register_or_assign_result_row(0))

        question.assert_not_called()
        update_instance.assert_not_called()
        toast.assert_called_once_with(dialog, "검토관리 종목입니다.")

    def test_routine_move_policy_reports_unexpected_status_as_processing_error(self) -> None:
        with (
            patch.object(
                routine_policy,
                "routine_action_guard_info",
                return_value={
                    "routine_name": "루틴A",
                    "stock_dir": Path("stocks/005930_삼성전자"),
                    "raw_status": "UNKNOWN_LEGACY",
                    "display_status": "",
                    "holding_qty": 0,
                    "buy_pending_qty": 0,
                    "sell_pending_qty": 0,
                },
            ),
            self.assertLogs("gui_routine_policy", level="ERROR") as logs,
        ):
            allowed, info = routine_policy.routine_action_reasons_for_stock(
                "005930",
                "삼성전자",
            )

        self.assertFalse(allowed)
        self.assertEqual(["처리할 수 없는 종목입니다."], info["reasons"])
        self.assertNotIn("상태" + "확인필요", " ".join(info["reasons"]))
        self.assertIn("unexpected registration policy status", "\n".join(logs.output))

    def test_routine_move_policy_allows_running_without_position_or_pending(self) -> None:
        for raw_status in ("RUNNING", "STARTED", "AUTO", "TRADING", "SELL_ONLY", "EMERGENCY_STOP"):
            with self.subTest(raw_status=raw_status), patch.object(
                routine_policy,
                "routine_action_guard_info",
                return_value={
                    "routine_name": "루틴A",
                    "stock_dir": Path("stocks/005930_삼성전자"),
                    "raw_status": raw_status,
                    "display_status": "매수/매도",
                    "holding_qty": 0,
                    "buy_pending_qty": 0,
                    "sell_pending_qty": 0,
                },
            ):
                allowed, info = routine_policy.routine_action_reasons_for_stock(
                    "005930",
                    "삼성전자",
                )

            self.assertTrue(allowed)
            self.assertEqual([], info["reasons"])

    def test_routine_move_policy_blocks_review_required_as_review_management(self) -> None:
        with patch.object(
            routine_policy,
            "routine_action_guard_info",
            return_value={
                "routine_name": "루틴A",
                "stock_dir": Path("stocks/005930_삼성전자"),
                "raw_status": "REVIEW_REQUIRED",
                "display_status": "검토종목",
                "holding_qty": 0,
                "buy_pending_qty": 0,
                "sell_pending_qty": 0,
            },
        ):
            allowed, info = routine_policy.routine_action_reasons_for_stock(
                "005930",
                "삼성전자",
            )

        self.assertFalse(allowed)
        self.assertEqual(["검토관리 종목입니다."], info["reasons"])

    def test_routine_move_policy_moves_pending_integrity_error_to_review(self) -> None:
        with TemporaryDirectory() as temp:
            stock_dir = Path(temp) / "stocks" / "005930_삼성전자"
            stock_dir.mkdir(parents=True)
            (stock_dir / "state.json").write_text(
                json.dumps(
                    {
                        "status": "RUNNING",
                        "holding_qty": 0,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (stock_dir / "orders.json").write_text(
                json.dumps(
                    {
                        "orders": [
                            {
                                "status": "OPEN",
                                "side": "BUY",
                                "order_no": "A1",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            with patch.object(
                routine_policy,
                "routine_action_guard_info",
                return_value={
                    "routine_name": "루틴A",
                    "stock_dir": stock_dir,
                    "state": {"status": "RUNNING", "holding_qty": 0},
                    "raw_status": "RUNNING",
                    "display_status": "매수/매도",
                    "holding_qty": 0,
                    "buy_pending_qty": "?",
                    "sell_pending_qty": 0,
                },
            ):
                allowed, info = routine_policy.routine_action_reasons_for_stock(
                    "005930",
                    "삼성전자",
                )

            saved_state = json.loads((stock_dir / "state.json").read_text(encoding="utf-8"))

        self.assertFalse(allowed)
        self.assertEqual(
            ["처리할 수 없는 종목입니다.\n검토관리에서 확인하세요."],
            info["reasons"],
        )
        self.assertEqual("REVIEW_REQUIRED", saved_state["status"])
        self.assertTrue(saved_state["review_required"])
        self.assertIn("PENDING_ORDER_QTY_MISSING", saved_state["review_reason"])

    def test_routine_move_policy_treats_paused_as_unexpected_legacy_state(self) -> None:
        with (
            patch.object(
                routine_policy,
                "routine_action_guard_info",
                return_value={
                    "routine_name": "루틴A",
                    "stock_dir": Path("stocks/005930_삼성전자"),
                    "raw_status": "PAUSED",
                    "display_status": "검토종목",
                    "holding_qty": 0,
                    "buy_pending_qty": 0,
                    "sell_pending_qty": 0,
                },
            ),
            self.assertLogs("gui_routine_policy", level="ERROR"),
        ):
            allowed, info = routine_policy.routine_action_reasons_for_stock(
                "005930",
                "삼성전자",
            )

        self.assertFalse(allowed)
        self.assertEqual(["처리할 수 없는 종목입니다."], info["reasons"])
        self.assertNotIn("검토종목 상태", " ".join(info["reasons"]))

    def test_routine_unassign_policy_allows_sell_only_without_position_or_pending(self) -> None:
        with (
            patch.object(
                routine_policy,
                "base_stock_routines_for_stock",
                return_value=(True, ["루틴A"]),
            ),
            patch.object(
                routine_policy,
                "stock_runtime_dir_for_routine",
                return_value=Path("stocks/005930_삼성전자"),
            ),
            patch.object(
                routine_policy,
                "read_json_dict",
                return_value={
                    "status": "SELL_ONLY",
                    "holding_qty": 0,
                },
            ),
            patch.object(
                routine_policy,
                "pending_order_side_quantities",
                return_value=(0, 0),
            ),
        ):
            allowed, routine_name, reasons = routine_policy.can_unassign_active_routine_from_stock(
                "005930",
                "삼성전자",
            )

        self.assertTrue(allowed)
        self.assertEqual("루틴A", routine_name)
        self.assertEqual([], reasons)

    def test_routine_unassign_policy_reports_unexpected_status_as_processing_error(self) -> None:
        with (
            patch.object(
                routine_policy,
                "base_stock_routines_for_stock",
                return_value=(True, ["루틴A"]),
            ),
            patch.object(
                routine_policy,
                "stock_runtime_dir_for_routine",
                return_value=Path("stocks/005930_삼성전자"),
            ),
            patch.object(
                routine_policy,
                "read_json_dict",
                return_value={
                    "status": "UNKNOWN_LEGACY",
                    "holding_qty": 0,
                },
            ),
            patch.object(
                routine_policy,
                "pending_order_side_quantities",
                return_value=(0, 0),
            ),
            self.assertLogs("gui_routine_policy", level="ERROR") as logs,
        ):
            allowed, routine_name, reasons = routine_policy.can_unassign_active_routine_from_stock(
                "005930",
                "삼성전자",
            )

        self.assertFalse(allowed)
        self.assertEqual("루틴A", routine_name)
        self.assertEqual(["처리할 수 없는 종목입니다."], reasons)
        self.assertNotIn("상태" + "확인필요", " ".join(reasons))
        self.assertIn("unexpected routine unassign policy status", "\n".join(logs.output))

    def test_routine_instance_rename_uses_repository_and_refreshes(self) -> None:
        window = self._window_harness()
        window.refresh_all = MagicMock()
        metadata = {
            "row_kind": "instance",
            "definition_id": "indicator_follow",
            "instance_id": "inst-a",
            "instance_name": "변경 전",
        }
        item = QTableWidgetItem()
        item.setData(Qt.UserRole, metadata)
        window.routine_table.setRowCount(1)
        window.routine_table.setItem(0, 0, item)
        row_widget = QWidget()
        row_layout = QHBoxLayout(row_widget)
        row_layout.setContentsMargins(0, 0, 0, 0)
        title_label = QLabel("변경 전")
        title_label.setObjectName("autoTradeSettingRoutineTreeTitle")
        row_layout.addWidget(title_label)
        window.routine_table.setCellWidget(0, 0, row_widget)
        repository = MagicMock()
        repository.rename_instance.return_value = SimpleNamespace(success=True, error="")
        with (
            patch.object(setting_window, "RoutineInstanceRepository", return_value=repository),
        ):
            window.rename_routine_instance(metadata)
            self.assertIsNotNone(window._routine_instance_name_editor)
            self.assertEqual(
                "routineInstanceNameEditor",
                window._routine_instance_name_editor.objectName(),
            )
            window._routine_instance_name_editor.setText("변경 후")
            window.finish_routine_instance_name_edit(save=True)

        repository.rename_instance.assert_called_once_with("inst-a", "변경 후")
        window.refresh_all.assert_called_once_with()

    def test_routine_instance_delete_cancel_keeps_state_unchanged(self) -> None:
        window = self._window_harness()
        window.refresh_all = MagicMock()
        metadata = {
            "row_kind": "instance",
            "instance_id": "inst-a",
            "instance_name": "A 인스턴스",
        }
        repository = MagicMock()
        with (
            patch.object(setting_window, "read_base_stocks", return_value=[]),
            patch.object(
                setting_window.QMessageBox,
                "question",
                return_value=setting_window.QMessageBox.No,
            ),
            patch.object(setting_window, "RoutineInstanceRepository", return_value=repository),
        ):
            window.delete_routine_instance(metadata)

        repository.delete_instance.assert_not_called()
        window.refresh_all.assert_not_called()

    def test_routine_instance_delete_blocks_when_stocks_are_assigned(self) -> None:
        window = self._window_harness()
        window.refresh_all = MagicMock()
        metadata = {
            "row_kind": "instance",
            "instance_id": "inst-a",
            "instance_name": "A 인스턴스",
        }
        repository = MagicMock()
        with (
            patch.object(
                setting_window,
                "read_base_stocks",
                return_value=[{"assigned_routine_instance_id": "inst-a"}],
            ),
            patch.object(setting_window.QMessageBox, "warning") as warning,
            patch.object(setting_window, "RoutineInstanceRepository", return_value=repository),
        ):
            window.delete_routine_instance(metadata)

        warning.assert_called_once()
        repository.delete_instance.assert_not_called()
        window.refresh_all.assert_not_called()

    def test_routine_instance_delete_checks_config_assignment_fallback(self) -> None:
        window = self._window_harness()
        window.refresh_all = MagicMock()
        metadata = {
            "row_kind": "instance",
            "instance_id": "inst-a",
            "instance_name": "A 인스턴스",
        }
        repository = MagicMock()
        with (
            patch.object(
                setting_window,
                "read_base_stocks",
                return_value=[{"stock_path": "stocks/005930_삼성전자"}],
            ),
            patch.object(
                setting_window,
                "read_json_dict",
                return_value={"assigned_routine_instance_id": "inst-a"},
            ),
            patch.object(setting_window.QMessageBox, "warning") as warning,
            patch.object(setting_window, "RoutineInstanceRepository", return_value=repository),
        ):
            window.delete_routine_instance(metadata)

        warning.assert_called_once()
        repository.delete_instance.assert_not_called()
        window.refresh_all.assert_not_called()

    def test_routine_instance_delete_uses_repository_and_refreshes(self) -> None:
        window = self._window_harness()
        window.refresh_all = MagicMock()
        metadata = {
            "row_kind": "instance",
            "instance_id": "inst-a",
            "instance_name": "A 인스턴스",
        }
        repository = MagicMock()
        repository.delete_instance.return_value = SimpleNamespace(success=True, error="")
        with (
            patch.object(setting_window, "read_base_stocks", return_value=[]),
            patch.object(
                setting_window.QMessageBox,
                "question",
                return_value=setting_window.QMessageBox.Yes,
            ),
            patch.object(setting_window, "RoutineInstanceRepository", return_value=repository),
        ):
            window.delete_routine_instance(metadata)

        repository.delete_instance.assert_called_once_with("inst-a")
        window.refresh_all.assert_called_once_with()

    def test_top_table_uses_definition_and_instance_rows_without_stock_scope_rows(self) -> None:
        instances = [self._instance("inst-a", "A 인스턴스"), self._instance("inst-b", "B 인스턴스")]
        counts = {
            "inst-a": {"registered": 1, "running": 1, "stopped": 0, "error": 0},
            "inst-b": {"registered": 2, "running": 0, "stopped": 2, "error": 1},
        }
        window = self._window_harness()
        window._routine_instance_operation_counts = lambda: counts
        with patch.object(setting_window, "load_routine_definitions", return_value=[self._definition()]), \
                patch.object(setting_window, "load_persisted_routine_instances", return_value=instances), \
                patch.object(setting_window, "read_base_stocks", return_value=[]):
            window.load_routine_table()
            window._set_routine_tree_display_level("routine")

        self.assertEqual(3, window.routine_table.rowCount())
        self.assertEqual(1, window.routine_table.columnCount())
        parent_meta = window.routine_table.item(0, 0).data(setting_window.Qt.UserRole)
        child_a_meta = window.routine_table.item(1, 0).data(setting_window.Qt.UserRole)
        child_b_meta = window.routine_table.item(2, 0).data(setting_window.Qt.UserRole)
        self.assertEqual("definition", parent_meta["row_kind"])
        self.assertEqual("instance", child_a_meta["row_kind"])
        self.assertEqual("instance", child_b_meta["row_kind"])
        self.assertNotIn(
            "stock_scope",
            [
                window.routine_table.item(row, 0).data(setting_window.Qt.UserRole)["row_kind"]
                for row in range(window.routine_table.rowCount())
            ],
        )
        self.assertEqual("", window.routine_table.item(0, 0).text())
        self.assertEqual("", window.routine_table.item(1, 0).text())
        self.assertEqual("", window.routine_table.item(0, 0).data(setting_window.Qt.DisplayRole))
        self.assertEqual("", window.routine_table.item(0, 0).data(setting_window.Qt.ToolTipRole))
        self.assertEqual("", window.routine_table.item(1, 0).data(setting_window.Qt.ToolTipRole))
        self.assertNotIn("005930", window.routine_table.item(0, 0).text())
        self.assertIsNotNone(window.routine_table.cellWidget(0, 0))
        self.assertIsNotNone(window.routine_table.cellWidget(1, 0))
        self.assertGreaterEqual(window.routine_table.rowHeight(0), 30)
        parent_widget = window.routine_table.cellWidget(0, 0)
        parent_title = parent_widget.findChild(setting_window.QLabel, "autoTradeSettingRoutineTreeTitle")
        parent_icon = parent_widget.findChild(setting_window.QLabel, "autoTradeSettingRoutineTreeIcon")
        parent_stamp = parent_widget.findChild(setting_window.QPushButton, "autoTradeSettingDefaultOperationStamp")
        parent_instance_count = parent_widget.findChild(setting_window.QLabel, "autoTradeSettingRoutineTreeInstanceCount")
        parent_meta_group = parent_widget.findChild(setting_window.QWidget, "autoTradeSettingRoutineTreeMetaGroup")
        parent_status_group = parent_widget.findChild(setting_window.QWidget, "autoTradeSettingRoutineTreeStatusGroup")
        parent_period = parent_widget.findChild(setting_window.QWidget, "autoTradeSettingRoutineTreePerformancePeriod")
        parent_period_spacer = parent_widget.findChild(setting_window.QWidget, "autoTradeSettingRoutineTreePerformancePeriodSpacer")
        parent_identity_compensation = parent_widget.findChild(
            setting_window.QWidget,
            "autoTradeSettingRoutineTreeIdentityXCompensation",
        )
        parent_profit = parent_widget.findChild(setting_window.QWidget, "autoTradeSettingRoutineTreePerformanceProfit")
        parent_average = parent_widget.findChild(setting_window.QWidget, "autoTradeSettingRoutineTreePerformanceAverage")
        parent_efficiency = parent_widget.findChild(setting_window.QWidget, "autoTradeSettingRoutineTreePerformanceEfficiency")
        parent_widget.resize(900, parent_widget.sizeHint().height())
        parent_widget.show()
        self._app.processEvents()
        self.assertIsNotNone(parent_title)
        self.assertIsNotNone(parent_icon)
        self.assertIsNone(parent_stamp)
        self.assertIsNotNone(parent_instance_count)
        self.assertIsNotNone(parent_meta_group)
        self.assertIsNone(parent_status_group)
        self.assertIsNotNone(parent_period)
        self.assertIsNotNone(parent_profit)
        self.assertIsNone(parent_period_spacer)
        self.assertIsNone(parent_identity_compensation)
        self.assertIsNotNone(parent_average)
        self.assertIsNotNone(parent_efficiency)
        self.assertEqual(28, parent_icon.width())
        self.assertEqual(
            setting_window.routine_tree_title_width(parent_title.fontMetrics()),
            parent_title.width(),
        )
        self.assertEqual(setting_window.Qt.AlignCenter | setting_window.Qt.AlignVCenter, parent_title.alignment())
        self.assertEqual(
            parent_title.width()
            + 4
            + parent_instance_count.width(),
            parent_meta_group.width(),
        )
        self.assertEqual("루틴2", parent_instance_count.text())
        self.assertEqual(64, parent_instance_count.width())
        self.assertEqual(setting_window.Qt.AlignCenter, parent_instance_count.alignment())
        self.assertIn("background-color: transparent", parent_instance_count.styleSheet())
        self.assertIn("#A855F7", parent_instance_count.styleSheet())
        self.assertIn("padding: 0 6px", parent_instance_count.styleSheet())
        self.assertGreater(parent_icon.font().pointSize(), parent_title.font().pointSize())
        self.assertTrue(parent_title.font().bold())
        self.assertTrue(parent_period.isHidden())
        self.assertTrue(parent_profit.isHidden())
        self.assertTrue(parent_average.isHidden())
        self.assertTrue(parent_efficiency.isHidden())
        self.assertEqual("0", parent_profit.findChild(setting_window.QLabel, "autoTradeSettingRoutineTreePerformanceProfitLeftValue").text())
        self.assertEqual("0.00%", parent_profit.findChild(setting_window.QLabel, "autoTradeSettingRoutineTreePerformanceProfitRightValue").text())
        self.assertEqual("0", parent_average.findChild(setting_window.QLabel, "autoTradeSettingRoutineTreePerformanceAverageLeftValue").text())
        self.assertEqual("0.00%", parent_average.findChild(setting_window.QLabel, "autoTradeSettingRoutineTreePerformanceAverageRightValue").text())
        self.assertEqual("0.0", parent_efficiency.findChild(setting_window.QLabel, "autoTradeSettingRoutineTreePerformanceEfficiencyLeftValue").text())
        self.assertEqual("", parent_widget.toolTip())
        self.assertFalse(parent_instance_count.isHidden())
        window._set_routine_tree_parent_summary_visible(parent_widget, True)
        self.assertFalse(parent_period.isHidden())
        parent_widget.layout().activate()
        self._app.processEvents()
        self.assertFalse(parent_profit.isHidden())
        self.assertFalse(parent_average.isHidden())
        self.assertFalse(parent_efficiency.isHidden())
        parent_layout_widgets = [
            parent_widget.layout().itemAt(index).widget()
            for index in range(parent_widget.layout().count())
            if parent_widget.layout().itemAt(index).widget() is not None
        ]
        self.assertEqual(
            parent_period,
            parent_layout_widgets[parent_layout_widgets.index(parent_meta_group) + 1],
        )
        self.assertGreaterEqual(window.routine_table.item(0, 0).sizeHint().height(), parent_widget.sizeHint().height())
        child_widget = window.routine_table.cellWidget(1, 0)
        child_title = child_widget.findChild(setting_window.QLabel, "autoTradeSettingRoutineTreeTitle")
        child_indent = child_widget.findChild(setting_window.QWidget, "autoTradeSettingRoutineTreeIndent")
        stamp = child_widget.findChild(setting_window.QPushButton, "autoTradeSettingDefaultOperationStamp")
        child_default_slot = child_widget.findChild(setting_window.QWidget, "autoTradeSettingDefaultOperationSlot")
        child_instance_count = child_widget.findChild(setting_window.QLabel, "autoTradeSettingRoutineTreeInstanceCount")
        child_period = child_widget.findChild(setting_window.QWidget, "autoTradeSettingRoutineTreePerformancePeriod")
        child_period_spacer = child_widget.findChild(setting_window.QWidget, "autoTradeSettingRoutineTreePerformancePeriodSpacer")
        child_profit = child_widget.findChild(setting_window.QWidget, "autoTradeSettingRoutineTreePerformanceProfit")
        child_average = child_widget.findChild(setting_window.QWidget, "autoTradeSettingRoutineTreePerformanceAverage")
        child_efficiency = child_widget.findChild(setting_window.QWidget, "autoTradeSettingRoutineTreePerformanceEfficiency")
        self.assertIsNotNone(child_title)
        self.assertIsNotNone(child_indent)
        self.assertIsNone(stamp)
        self.assertIsNone(child_default_slot)
        self.assertIsNone(child_instance_count)
        self.assertIsNotNone(child_profit)
        self.assertIsNotNone(child_period)
        self.assertIsNone(child_period_spacer)
        self.assertIsNotNone(child_average)
        self.assertIsNotNone(child_efficiency)
        self.assertEqual(28, child_indent.width())
        self.assertEqual(setting_window.routine_tree_title_width(child_title.fontMetrics()), child_title.width())
        self.assertEqual(child_title.width(), child_title.minimumWidth())
        self.assertEqual(child_title.width(), child_title.maximumWidth())
        self.assertEqual(setting_window.QSizePolicy.Fixed, child_title.sizePolicy().horizontalPolicy())
        self.assertEqual(setting_window.Qt.AlignCenter | setting_window.Qt.AlignVCenter, child_title.alignment())
        self.assertIsNone(child_widget.findChild(setting_window.QLabel, "autoTradeSettingRoutineTreeRegistered"))
        self.assertIsNone(child_widget.findChild(setting_window.QLabel, "autoTradeSettingRoutineTreeRunning"))
        self.assertIsNone(child_widget.findChild(setting_window.QLabel, "autoTradeSettingRoutineTreeStopped"))
        self.assertIsNone(child_widget.findChild(setting_window.QLabel, "autoTradeSettingRoutineTreeError"))
        self.assertFalse(child_title.font().bold())
        self.assertGreaterEqual(
            child_title.mapTo(child_widget, child_title.rect().topLeft()).x()
            - parent_title.mapTo(parent_widget, parent_title.rect().topLeft()).x(),
            20,
        )
        self.assertGreater(parent_title.font().pointSize(), child_title.font().pointSize())
        self.assertEqual(setting_window.QFont.DemiBold, parent_title.font().weight())
        self.assertLess(parent_title.font().weight(), setting_window.QFont.Bold)
        self.assertEqual("0", child_period.findChild(setting_window.QLabel, "autoTradeSettingRoutineTreePerformancePeriodLeftValue").text())
        self.assertEqual("0", child_profit.findChild(setting_window.QLabel, "autoTradeSettingRoutineTreePerformanceProfitLeftValue").text())
        self.assertEqual("0.00%", child_profit.findChild(setting_window.QLabel, "autoTradeSettingRoutineTreePerformanceProfitRightValue").text())
        self.assertEqual("0", child_average.findChild(setting_window.QLabel, "autoTradeSettingRoutineTreePerformanceAverageLeftValue").text())
        self.assertEqual("0.00%", child_average.findChild(setting_window.QLabel, "autoTradeSettingRoutineTreePerformanceAverageRightValue").text())
        self.assertEqual("0.0", child_efficiency.findChild(setting_window.QLabel, "autoTradeSettingRoutineTreePerformanceEfficiencyLeftValue").text())
        self.assertFalse(child_period.isHidden())
        self.assertFalse(child_profit.isHidden())
        self.assertFalse(child_average.isHidden())
        self.assertFalse(child_efficiency.isHidden())
        self.assertEqual("", child_widget.toolTip())
        self.assertEqual("A 인스턴스", child_title.text())
        self.assertNotIn("기본운영", child_title.text())
        self.assertFalse(child_title.text().startswith(" "))
        self.assertEqual(2, window.routine_table.rowHeight(0) - window.routine_table.rowHeight(1))
        self.assertLessEqual(window.routine_table.rowHeight(0), 40)

    def test_tree_display_level_badges_reserve_control_area_without_changing_table_width(self) -> None:
        window = self._window_harness()
        window.eventFilter = MethodType(lambda _self, _obj, _event: False, window)
        window.routine_box = setting_window.QGroupBox("자동매매 루틴")
        window.routine_box.setAlignment(setting_window.Qt.AlignLeft)
        window.routine_box.setFlat(False)
        window.routine_box.setStyleSheet(
            setting_window.AUTO_TRADE_SETTING_WORKSPACE_GROUP_BOX_STYLE
        )
        layout = setting_window.QVBoxLayout(window.routine_box)
        layout.addWidget(window.routine_table)
        window.routine_box.resize(1000, 320)
        window.routine_box.show()
        self._app.processEvents()
        table_geometry_before = window.routine_table.geometry()
        group_geometry_before = window.routine_box.geometry()

        window._setup_routine_tree_display_level_badges()
        self._app.processEvents()

        badges = window._routine_tree_display_level_buttons
        valid_badge = window._routine_tree_valid_button
        self.assertEqual("유효", valid_badge.text())
        self.assertTrue(valid_badge.isCheckable())
        self.assertFalse(valid_badge.isChecked())
        self.assertEqual((64, 22), (valid_badge.width(), valid_badge.height()))
        self.assertIn("color: #111827", valid_badge.styleSheet())
        self.assertEqual(
            ["|", "|", "|"],
            [separator.text() for separator in window._routine_tree_display_separators],
        )
        self.assertEqual({"category", "routine", "stock"}, set(badges))
        self.assertEqual(["그룹", "루틴", "종목"], [badges[level].text() for level in ("category", "routine", "stock")])
        for badge in badges.values():
            self.assertEqual((64, 22), (badge.width(), badge.height()))
            self.assertIn("border-radius: 4px", badge.styleSheet())
            self.assertIn("padding: 0 6px", badge.styleSheet())
        self.assertIn("color: #16A34A", badges["category"].styleSheet())
        self.assertIn("color: #111827", badges["routine"].styleSheet())
        scopes = window._routine_tree_display_scope_buttons
        self.assertEqual(
            ["전체", "현재", "과거"],
            [
                scopes[key].text()
                for key in ("all", "current", "historical")
            ],
        )
        self.assertTrue(all(button.isEnabled() for button in scopes.values()))
        self.assertIn("color: #111827", scopes["all"].styleSheet())
        self.assertIn("color: #111827", scopes["current"].styleSheet())
        self.assertIn("color: #111827", scopes["historical"].styleSheet())
        scopes["current"].click()
        self.assertEqual("current", window._routine_tree_display_scope)
        criteria = window._routine_tree_display_criterion_buttons
        self.assertEqual(
            ["기간", "수익", "평균", "효율"],
            [criteria[key].text() for key in ("period", "profit", "average", "efficiency")],
        )
        self.assertTrue(criteria["period"].isEnabled())
        self.assertTrue(criteria["profit"].isEnabled())
        self.assertTrue(criteria["average"].isEnabled())
        self.assertTrue(criteria["efficiency"].isEnabled())
        self.assertIn("color: #16A34A", criteria["profit"].styleSheet())
        self.assertIn("color: #111827", criteria["period"].styleSheet())
        criteria["period"].click()
        self.assertEqual("period", window._routine_tree_display_criterion)
        badges["routine"].click()
        self.assertTrue(all(button.isEnabled() for button in scopes.values()))
        self.assertTrue(criteria["period"].isEnabled())
        self.assertTrue(criteria["average"].isEnabled())
        self.assertTrue(criteria["efficiency"].isEnabled())
        criteria["period"].click()
        self.assertEqual("period", window._routine_tree_display_criterion)
        badges["category"].click()
        self.assertEqual("period", window._routine_tree_display_criterion)
        badges["stock"].click()
        self.assertTrue(all(button.isEnabled() for button in scopes.values()))
        self.assertIn("color: #16A34A", scopes["current"].styleSheet())
        self.assertTrue(all(button.isEnabled() for button in criteria.values()))
        self.assertEqual("current", window._routine_tree_display_scope)
        scopes["historical"].click()
        self.assertEqual("historical", window._routine_tree_display_scope)
        self.assertIn(
            "color: #16A34A",
            scopes["historical"].styleSheet(),
        )
        self.assertIn("color: #111827", scopes["all"].styleSheet())
        selected_state = (
            window._routine_tree_display_level,
            window._routine_tree_display_scope,
            window._routine_tree_display_criterion,
        )
        valid_badge.click()
        self.assertTrue(window._routine_tree_valid_only)
        self.assertTrue(valid_badge.isChecked())
        self.assertIn("color: #16A34A", valid_badge.styleSheet())
        self.assertEqual(
            selected_state,
            (
                window._routine_tree_display_level,
                window._routine_tree_display_scope,
                window._routine_tree_display_criterion,
            ),
        )
        valid_badge.click()
        self.assertFalse(window._routine_tree_valid_only)

        badge_group = window._routine_tree_display_level_badges
        expected_x = window.routine_box.width() - layout.contentsMargins().right() - badge_group.width()
        self.assertEqual(expected_x, badge_group.x())
        self.assertEqual(window.routine_box.contentsRect().top(), badge_group.y())
        self.assertGreater(
            badge_group.y(),
            setting_window.AUTO_TRADE_SETTING_WORKSPACE_GROUP_BOX_FRAME_TOP,
        )
        self.assertFalse(badge_group.geometry().intersects(window.routine_table.geometry()))
        self.assertEqual(group_geometry_before, window.routine_box.geometry())
        self.assertEqual(table_geometry_before.x(), window.routine_table.geometry().x())
        self.assertEqual(table_geometry_before.width(), window.routine_table.geometry().width())
        self.assertEqual(table_geometry_before.bottom(), window.routine_table.geometry().bottom())

    def test_valid_badge_filters_only_rows_without_required_children(self) -> None:
        stocked_definition = self._definition()
        empty_instance_definition = replace(
            stocked_definition,
            definition_id="empty_instance_group",
            display_name="빈인스턴스그룹",
        )
        empty_definition = replace(
            stocked_definition,
            definition_id="empty_group",
            display_name="빈그룹",
        )
        stocked_instance = self._instance("inst-stocked", "종목보유")
        empty_instance = replace(
            self._instance("inst-empty", "종목없음"),
            definition_id="empty_instance_group",
        )
        stocks = [
            {
                "stock_path": "stocks/005930_A",
                "assigned_routine_instance_id": "inst-stocked",
                "code": "005930",
                "name": "삼성전자",
            },
        ]
        window = self._window_harness()
        window._routine_tree_display_level_buttons = {}
        window._routine_tree_display_scope_buttons = {}
        window._routine_tree_display_criterion_buttons = {}
        window._routine_instance_operation_counts = lambda: {
            "inst-stocked": {"registered": 1, "running": 0, "stopped": 1, "error": 0},
            "inst-empty": {"registered": 0, "running": 0, "stopped": 0, "error": 0},
        }

        def visible_counts() -> dict[str, int]:
            counts = {"definition": 0, "instance": 0, "stock": 0}
            for row in range(window.routine_table.rowCount()):
                metadata = window.routine_table.item(row, 0).data(setting_window.Qt.UserRole)
                if not window.routine_table.isRowHidden(row):
                    counts[str(metadata["row_kind"])] += 1
            return counts

        with (
            patch.object(
                setting_window,
                "load_routine_definitions",
                return_value=[stocked_definition, empty_instance_definition, empty_definition],
            ),
            patch.object(
                setting_window,
                "load_persisted_routine_instances",
                return_value=[stocked_instance, empty_instance],
            ),
            patch.object(setting_window, "read_base_stocks", return_value=stocks),
            patch.object(setting_window, "read_orders_data", return_value=[]),
        ):
            window.load_routine_table()
            window._set_routine_tree_display_level("category")
            self.assertEqual(
                {"definition": 3, "instance": 0, "stock": 0},
                visible_counts(),
            )

            selected_state = (
                window._routine_tree_display_level,
                window._routine_tree_display_scope,
                window._routine_tree_display_criterion,
            )
            window._set_routine_tree_valid_only(True)
            self.assertEqual(
                {"definition": 2, "instance": 0, "stock": 0},
                visible_counts(),
            )
            self.assertEqual(
                selected_state,
                (
                    window._routine_tree_display_level,
                    window._routine_tree_display_scope,
                    window._routine_tree_display_criterion,
                ),
            )

            window._set_routine_tree_display_level("routine")
            self.assertEqual(
                {"definition": 1, "instance": 1, "stock": 0},
                visible_counts(),
            )

            window._set_routine_tree_display_level("stock")
            self.assertEqual(
                {"definition": 0, "instance": 0, "stock": 1},
                visible_counts(),
            )
            for scope in ("current", "all"):
                window._set_routine_tree_display_scope(scope)
                self.assertEqual(
                    {"definition": 0, "instance": 0, "stock": 1},
                    visible_counts(),
                )
            window._set_routine_tree_display_scope("historical")
            self.assertEqual(
                {"definition": 0, "instance": 0, "stock": 0},
                visible_counts(),
            )
            window._set_routine_tree_display_scope("all")

            window.load_routine_table()
            self.assertTrue(window._routine_tree_valid_only)
            self.assertEqual(
                {"definition": 0, "instance": 0, "stock": 1},
                visible_counts(),
            )

            window._set_routine_tree_valid_only(False)
            self.assertEqual(
                {"definition": 3, "instance": 2, "stock": 1},
                visible_counts(),
            )

    def test_actual_window_badges_change_visible_hierarchy(self) -> None:
        instances = [self._instance("inst-a", "A 인스턴스")]
        stocks = [
            {
                "stock_path": "stocks/005930_A",
                "assigned_routine_instance_id": "inst-a",
                "code": "005930",
                "name": "삼성전자",
            },
        ]
        with patch.object(AutoTradeSettingWindow, "refresh_all", lambda _self: None), \
                patch.object(AutoTradeSettingWindow, "update_startup_recovery_controls", lambda _self: None), \
                patch.object(AutoTradeSettingWindow, "current_runtime_file_signature", lambda _self: tuple()):
            window = AutoTradeSettingWindow()
        self.addCleanup(window.close)
        window._routine_instance_operation_counts = lambda: {
            "inst-a": {"registered": 1, "running": 0, "stopped": 1, "error": 0},
        }

        with patch.object(setting_window, "load_routine_definitions", return_value=[self._definition()]), \
                patch.object(setting_window, "load_persisted_routine_instances", return_value=instances), \
                patch.object(setting_window, "read_base_stocks", return_value=stocks), \
                patch.object(setting_window, "read_orders_data", return_value=[]):
            window.load_routine_table()
            window.show()
            self._app.processEvents()

        def _visible_counts() -> dict[str, int]:
            counts = {"definition": 0, "instance": 0, "stock": 0}
            for row in range(window.routine_table.rowCount()):
                metadata = window.routine_table.item(row, 0).data(setting_window.Qt.UserRole)
                if not window.routine_table.isRowHidden(row):
                    counts[str(metadata["row_kind"])] += 1
            return counts

        level_buttons = window._routine_tree_display_level_buttons
        scope_buttons = window._routine_tree_display_scope_buttons
        criteria = window._routine_tree_display_criterion_buttons

        with patch.object(setting_window, "load_routine_definitions", return_value=[self._definition()]), \
                patch.object(setting_window, "load_persisted_routine_instances", return_value=instances), \
                patch.object(setting_window, "read_base_stocks", return_value=stocks), \
                patch.object(setting_window, "read_orders_data", return_value=[]):
            level_buttons["category"].click()
            self._app.processEvents()
            self.assertEqual({"definition": 1, "instance": 0, "stock": 0}, _visible_counts())
            self.assertTrue(all(button.isEnabled() for button in scope_buttons.values()))
            self.assertTrue(criteria["period"].isEnabled())
            self.assertTrue(criteria["average"].isEnabled())
            self.assertTrue(criteria["efficiency"].isEnabled())

            level_buttons["routine"].click()
            self._app.processEvents()
            self.assertEqual({"definition": 1, "instance": 1, "stock": 0}, _visible_counts())
            self.assertTrue(all(button.isEnabled() for button in scope_buttons.values()))
            self.assertTrue(criteria["period"].isEnabled())
            self.assertTrue(criteria["average"].isEnabled())
            self.assertTrue(criteria["efficiency"].isEnabled())

        with patch.object(setting_window, "load_routine_definitions", return_value=[self._definition()]), \
                patch.object(setting_window, "load_persisted_routine_instances", return_value=instances), \
                patch.object(setting_window, "read_base_stocks", return_value=stocks), \
                patch.object(setting_window, "read_orders_data", return_value=[]):
            window.routine_table.selectRow(1)
            level_buttons["stock"].click()
            self._app.processEvents()
            self.assertEqual({"definition": 1, "instance": 1, "stock": 1}, _visible_counts())
            self.assertTrue(all(button.isEnabled() for button in scope_buttons.values()))
            self.assertTrue(all(button.isEnabled() for button in criteria.values()))
            self.assertEqual(1, window.routine_table.currentRow())

            window._toggle_routine_instance_collapsed("inst-a")
            level_buttons["category"].click()
            level_buttons["stock"].click()
            self._app.processEvents()
            self.assertEqual({"definition": 1, "instance": 1, "stock": 1}, _visible_counts())
            self.assertEqual(set(), window._collapsed_auto_trade_instance_ids)

    def test_scope_buttons_update_state_even_when_routine_rows_are_collapsed(self) -> None:
        instances = [self._instance("inst-a", "A 인스턴스")]
        stocks = [
            {
                "stock_path": "stocks/005930_A",
                "assigned_routine_instance_id": "inst-a",
                "code": "005930",
                "name": "삼성전자",
            },
        ]
        with patch.object(AutoTradeSettingWindow, "refresh_all", lambda _self: None), \
                patch.object(AutoTradeSettingWindow, "update_startup_recovery_controls", lambda _self: None), \
                patch.object(AutoTradeSettingWindow, "current_runtime_file_signature", lambda _self: tuple()):
            window = AutoTradeSettingWindow()
        self.addCleanup(window.close)
        window._routine_instance_operation_counts = lambda: {
            "inst-a": {"registered": 1, "running": 0, "stopped": 1, "error": 0},
        }

        with patch.object(setting_window, "load_routine_definitions", return_value=[self._definition()]), \
                patch.object(setting_window, "load_persisted_routine_instances", return_value=instances), \
                patch.object(setting_window, "read_base_stocks", return_value=stocks), \
                patch.object(setting_window, "read_orders_data", return_value=[]):
            window.load_routine_table()
            window.show()
            self._app.processEvents()

            level_buttons = window._routine_tree_display_level_buttons
            scope_buttons = window._routine_tree_display_scope_buttons
            criteria = window._routine_tree_display_criterion_buttons

            level_buttons["routine"].click()
            self._app.processEvents()
            self.assertTrue(window.routine_table.isRowHidden(2))
            self.assertTrue(all(button.isEnabled() for button in scope_buttons.values()))

            scope_buttons["historical"].click()
            self._app.processEvents()
            self.assertEqual("historical", window._routine_tree_display_scope)
            self.assertIn("color: #16A34A", scope_buttons["historical"].styleSheet())

            criteria["profit"].click()
            self._app.processEvents()
            self.assertEqual("historical", window._routine_tree_display_scope)
            self.assertEqual("profit", window._routine_tree_display_criterion)

            window._toggle_routine_instance_collapsed("inst-a")
            self._app.processEvents()
            self.assertEqual("historical", window._routine_tree_display_scope)
            self.assertFalse(window.routine_table.isRowHidden(2))

            scope_buttons["all"].click()
            self._app.processEvents()
            self.assertEqual("all", window._routine_tree_display_scope)
            self.assertIn("color: #16A34A", scope_buttons["all"].styleSheet())

            window._toggle_routine_instance_collapsed("inst-a")
            self._app.processEvents()
            self.assertTrue(window.routine_table.isRowHidden(2))
            level_buttons["stock"].click()
            self._app.processEvents()
            self.assertEqual("all", window._routine_tree_display_scope)
            level_buttons["routine"].click()
            self._app.processEvents()
            self.assertEqual("all", window._routine_tree_display_scope)

    def test_routine_profit_order_survives_scope_button_transitions(self) -> None:
        instances = [
            self._instance("inst-a", "A 루틴"),
            self._instance("inst-b", "B 루틴"),
        ]
        current_stocks = {
            "inst-a": [],
            "inst-b": [
                {
                    "instance_id": "inst-b",
                    "stock_path": "fixture/current-zero",
                    "stock_code": "000001",
                    "stock_name": "현재0",
                },
            ],
        }
        historical_stocks = {
            "inst-a": [
                {
                    "instance_id": "inst-a",
                    "stock_path": "fixture/history-top",
                    "stock_code": "000002",
                    "stock_name": "과거최고",
                    "is_historical": True,
                },
            ],
            "inst-b": [
                {
                    "instance_id": "inst-b",
                    "stock_path": "fixture/history-plus",
                    "stock_code": "000003",
                    "stock_name": "과거수익",
                    "is_historical": True,
                },
                {
                    "instance_id": "inst-b",
                    "stock_path": "fixture/history-minus",
                    "stock_code": "000004",
                    "stock_name": "과거손실",
                    "is_historical": True,
                },
            ],
        }
        performance_by_path = {
            "fixture/current-zero": {"realized_profit": 0.0, "trade_days": 0},
            "fixture/history-top": {"realized_profit": 202000.0, "trade_days": 4},
            "fixture/history-plus": {"realized_profit": 125000.0, "trade_days": 3},
            "fixture/history-minus": {"realized_profit": -48000.0, "trade_days": 2},
        }
        with patch.object(AutoTradeSettingWindow, "refresh_all", lambda _self: None), \
                patch.object(AutoTradeSettingWindow, "update_startup_recovery_controls", lambda _self: None), \
                patch.object(AutoTradeSettingWindow, "current_runtime_file_signature", lambda _self: tuple()):
            window = AutoTradeSettingWindow()
        self.addCleanup(window.close)
        window._routine_instance_operation_counts = lambda: {
            "inst-a": {"registered": 0, "running": 0, "stopped": 0, "error": 0},
            "inst-b": {"registered": 1, "running": 0, "stopped": 1, "error": 0},
        }
        window._current_stocks_by_instance = lambda: current_stocks
        window._historical_stocks_by_instance = lambda: historical_stocks
        window._routine_tree_stock_performance_source = lambda stock: {
            **performance_by_path[str(stock.get("stock_path", ""))],
            "profit_rate": 0.0,
            "average": None,
            "average_rate": None,
            "profit_factor": 0.0,
        }

        with patch.object(setting_window, "load_routine_definitions", return_value=[self._definition()]), \
                patch.object(setting_window, "load_persisted_routine_instances", return_value=instances):
            window.load_routine_table()
            window.show()
            self._app.processEvents()

            level_buttons = window._routine_tree_display_level_buttons
            scope_buttons = window._routine_tree_display_scope_buttons
            criterion_buttons = window._routine_tree_display_criterion_buttons
            window._routine_tree_valid_button.click()
            level_buttons["routine"].click()
            self._app.processEvents()
            window._collapsed_auto_trade_instance_ids.clear()
            window._apply_routine_tree_collapse_visibility()
            scope_buttons["all"].click()
            criterion_buttons["profit"].click()
            self._app.processEvents()

            def visible_rows() -> list[dict[str, object]]:
                return [
                    window.routine_table.item(row, 0).data(setting_window.Qt.UserRole)
                    for row in range(window.routine_table.rowCount())
                    if not window.routine_table.isRowHidden(row)
                ]

            def ordered_profit_rows() -> list[tuple[str, str, str]]:
                rows = []
                for row in visible_rows():
                    if row.get("row_kind") not in {"instance", "stock"}:
                        continue
                    rows.append(
                        (
                            str(row.get("row_kind", "")),
                            str(row.get("display_name", "")),
                            str(row.get("performance_profit_amount", "")),
                        )
                    )
                return rows

            expected_all = [
                ("instance", "A 루틴", "+202,000"),
                ("stock", "과거최고", "+202,000"),
                ("instance", "B 루틴", "+77,000"),
                ("stock", "과거수익", "+125,000"),
                ("stock", "현재0", "0"),
                ("stock", "과거손실", "-48,000"),
            ]
            self.assertEqual(expected_all, ordered_profit_rows())

            scope_buttons["current"].click()
            scope_buttons["historical"].click()
            scope_buttons["all"].click()
            self._app.processEvents()

            self.assertEqual("all", window._routine_tree_display_scope)
            self.assertEqual("profit", window._routine_tree_display_criterion)
            self.assertEqual(expected_all, ordered_profit_rows())

    def test_routine_display_units_sort_by_each_metric_with_button_clicks(self) -> None:
        instances = [
            self._instance("inst-a", "기간우세"),
            self._instance("inst-b", "평균효율우세"),
        ]
        current_stocks = {
            "inst-a": [
                {
                    "instance_id": "inst-a",
                    "stock_path": "fixture/period-stock",
                    "stock_code": "000001",
                    "stock_name": "기간종목",
                },
            ],
            "inst-b": [
                {
                    "instance_id": "inst-b",
                    "stock_path": "fixture/average-stock",
                    "stock_code": "000002",
                    "stock_name": "평균종목",
                },
            ],
        }
        performance_by_path = {
            "fixture/period-stock": {
                "trade_days": 5,
                "realized_profit": 10.0,
                "average": 2.0,
                "profit_factor": 1.0,
            },
            "fixture/average-stock": {
                "trade_days": 2,
                "realized_profit": 20.0,
                "average": 10.0,
                "profit_factor": 9.0,
            },
        }
        with patch.object(AutoTradeSettingWindow, "refresh_all", lambda _self: None), \
                patch.object(AutoTradeSettingWindow, "update_startup_recovery_controls", lambda _self: None), \
                patch.object(AutoTradeSettingWindow, "current_runtime_file_signature", lambda _self: tuple()):
            window = AutoTradeSettingWindow()
        self.addCleanup(window.close)
        window._routine_instance_operation_counts = lambda: {
            "inst-a": {"registered": 1, "running": 0, "stopped": 1, "error": 0},
            "inst-b": {"registered": 1, "running": 0, "stopped": 1, "error": 0},
        }
        window._current_stocks_by_instance = lambda: current_stocks
        window._historical_stocks_by_instance = lambda: {}
        window._routine_tree_stock_performance_source = lambda stock: {
            **performance_by_path[str(stock.get("stock_path", ""))],
            "profit_rate": 0.0,
            "average_rate": 0.0,
        }

        with patch.object(setting_window, "load_routine_definitions", return_value=[self._definition()]), \
                patch.object(setting_window, "load_persisted_routine_instances", return_value=instances):
            window.load_routine_table()
            window.show()
            self._app.processEvents()

            window._routine_tree_valid_button.click()
            window._routine_tree_display_level_buttons["routine"].click()
            window._collapsed_auto_trade_instance_ids.clear()
            window._apply_routine_tree_collapse_visibility()

            def visible_unit_names() -> list[str]:
                names = []
                for row in range(window.routine_table.rowCount()):
                    item = window.routine_table.item(row, 0)
                    metadata = item.data(setting_window.Qt.UserRole) if item else None
                    if not isinstance(metadata, dict) or window.routine_table.isRowHidden(row):
                        continue
                    if metadata.get("row_kind") in {"instance", "stock"}:
                        names.append(str(metadata.get("display_name", "")))
                return names

            window._routine_tree_display_criterion_buttons["period"].click()
            self.assertEqual(
                ["기간우세", "기간종목", "평균효율우세", "평균종목"],
                visible_unit_names(),
            )

            window._routine_tree_display_criterion_buttons["average"].click()
            self.assertEqual(
                ["평균효율우세", "평균종목", "기간우세", "기간종목"],
                visible_unit_names(),
            )

            window._routine_tree_display_criterion_buttons["efficiency"].click()
            self.assertEqual(
                ["평균효율우세", "평균종목", "기간우세", "기간종목"],
                visible_unit_names(),
            )

    def test_tree_display_level_changes_visible_hierarchy_and_preserves_collapse(self) -> None:
        instances = [self._instance("inst-a", "A 인스턴스"), self._instance("inst-empty", "빈 인스턴스")]
        stocks = [
            {
                "stock_path": "stocks/005930_A",
                "assigned_routine_instance_id": "inst-a",
                "code": "005930",
                "name": "삼성전자",
            },
        ]
        window = self._window_harness()
        window._routine_instance_operation_counts = lambda: {
            "inst-a": {"registered": 1, "running": 0, "stopped": 1, "error": 0},
            "inst-empty": {"registered": 0, "running": 0, "stopped": 0, "error": 0},
        }
        window._routine_tree_display_level_buttons = {}

        with patch.object(setting_window, "load_routine_definitions", return_value=[self._definition()]), \
                patch.object(setting_window, "load_persisted_routine_instances", return_value=instances), \
                patch.object(setting_window, "read_base_stocks", return_value=stocks):
            window.load_routine_table()
            window._set_routine_tree_display_level("category")

            def _visible_counts() -> dict[str, int]:
                counts = {"definition": 0, "instance": 0, "stock": 0}
                for row in range(window.routine_table.rowCount()):
                    metadata = window.routine_table.item(row, 0).data(setting_window.Qt.UserRole)
                    if not window.routine_table.isRowHidden(row):
                        counts[str(metadata["row_kind"])] += 1
                return counts

            self.assertEqual(
                {"definition": 1, "instance": 0, "stock": 0},
                _visible_counts(),
            )
            window._set_routine_tree_display_level("routine")
            self.assertEqual(
                {"definition": 1, "instance": 2, "stock": 0},
                _visible_counts(),
            )
            window._set_routine_tree_display_level("stock")
            self.assertEqual(
                {"definition": 1, "instance": 2, "stock": 1},
                _visible_counts(),
            )

            window._toggle_routine_instance_collapsed("inst-a")
            self.assertEqual(
                {"definition": 1, "instance": 2, "stock": 0},
                _visible_counts(),
            )
            window._set_routine_tree_display_level("category")
            window._set_routine_tree_display_level("stock")
            self.assertEqual(
                {"definition": 1, "instance": 2, "stock": 1},
                _visible_counts(),
            )

        self.assertEqual("stock", window._routine_tree_display_level)
        self.assertEqual(set(), window._collapsed_auto_trade_instance_ids)
        self.assertFalse(window.routine_table.isRowHidden(1))
        self.assertFalse(window.routine_table.isRowHidden(2))

    def test_scope_badges_ignore_collapse_state_in_routine_level(self) -> None:
        instances = [self._instance("inst-a", "A 인스턴스")]
        stocks = [
            {
                "stock_path": "stocks/005930_A",
                "assigned_routine_instance_id": "inst-a",
                "code": "005930",
                "name": "삼성전자",
            },
        ]
        with patch.object(AutoTradeSettingWindow, "refresh_all", lambda _self: None), \
                patch.object(AutoTradeSettingWindow, "update_startup_recovery_controls", lambda _self: None), \
                patch.object(AutoTradeSettingWindow, "current_runtime_file_signature", lambda _self: tuple()):
            window = AutoTradeSettingWindow()
        self.addCleanup(window.close)
        window._routine_instance_operation_counts = lambda: {
            "inst-a": {"registered": 1, "running": 0, "stopped": 1, "error": 0},
        }

        with patch.object(setting_window, "load_routine_definitions", return_value=[self._definition()]), \
                patch.object(setting_window, "load_persisted_routine_instances", return_value=instances), \
                patch.object(setting_window, "read_base_stocks", return_value=stocks), \
                patch.object(setting_window, "read_orders_data", return_value=[]):
            window.load_routine_table()
            window.show()
            self._app.processEvents()

            scope_buttons = window._routine_tree_display_scope_buttons
            level_buttons = window._routine_tree_display_level_buttons

            level_buttons["routine"].click()
            self._app.processEvents()
            self.assertTrue(window.routine_table.isRowHidden(2))
            self.assertTrue(all(button.isEnabled() for button in scope_buttons.values()))

            window._toggle_routine_instance_collapsed("inst-a")
            self._app.processEvents()
            self.assertFalse(window.routine_table.isRowHidden(2))
            self.assertTrue(all(button.isEnabled() for button in scope_buttons.values()))

            window._toggle_routine_instance_collapsed("inst-a")
            self._app.processEvents()
            self.assertTrue(window.routine_table.isRowHidden(2))
            self.assertTrue(all(button.isEnabled() for button in scope_buttons.values()))

    def test_level_badges_apply_once_and_arrows_remain_authoritative(self) -> None:
        instances = [self._instance("inst-a", "A 인스턴스")]
        stocks = [
            {
                "stock_path": "stocks/005930_A",
                "assigned_routine_instance_id": "inst-a",
                "code": "005930",
                "name": "삼성전자",
            },
        ]
        window = self._window_harness()
        window._routine_tree_display_level_buttons = {}
        window._routine_tree_display_scope_buttons = {}
        window._routine_tree_display_criterion_buttons = {}
        window._routine_instance_operation_counts = lambda: {
            "inst-a": {"registered": 1, "running": 0, "stopped": 1, "error": 0},
        }

        with patch.object(setting_window, "load_routine_definitions", return_value=[self._definition()]), \
                patch.object(setting_window, "load_persisted_routine_instances", return_value=instances), \
                patch.object(setting_window, "read_base_stocks", return_value=stocks):
            window.load_routine_table()

        def _row_icon(row: int):
            return window.routine_table.cellWidget(row, 0).findChild(
                setting_window.QLabel,
                "autoTradeSettingRoutineTreeIcon",
            )

        with patch.object(setting_window, "load_routine_definitions", return_value=[self._definition()]), \
                patch.object(setting_window, "load_persisted_routine_instances", return_value=instances), \
                patch.object(setting_window, "read_base_stocks", return_value=stocks):
            window._set_routine_tree_display_level("category")
        self.assertEqual({"indicator_follow"}, window._collapsed_auto_trade_definition_ids)
        self.assertEqual("▶", _row_icon(0).text())
        self.assertTrue(window.routine_table.isRowHidden(1))
        self.assertTrue(window.routine_table.isRowHidden(2))

        window._toggle_routine_definition_collapsed("indicator_follow")
        self.assertEqual(set(), window._collapsed_auto_trade_definition_ids)
        self.assertEqual("▼", _row_icon(0).text())
        self.assertFalse(window.routine_table.isRowHidden(1))
        window._refresh_routine_tree_display_state()
        window.routine_table.viewport().update()
        self.assertEqual("▼", _row_icon(0).text())
        self.assertFalse(window.routine_table.isRowHidden(1))

        with patch.object(setting_window, "load_routine_definitions", return_value=[self._definition()]), \
                patch.object(setting_window, "load_persisted_routine_instances", return_value=instances), \
                patch.object(setting_window, "read_base_stocks", return_value=stocks):
            window._set_routine_tree_display_level("routine")
        self.assertEqual(set(), window._collapsed_auto_trade_definition_ids)
        self.assertEqual({"inst-a"}, window._collapsed_auto_trade_instance_ids)
        self.assertEqual("▼", _row_icon(0).text())
        self.assertEqual("▶", _row_icon(1).text())
        self.assertFalse(window.routine_table.isRowHidden(1))
        self.assertTrue(window.routine_table.isRowHidden(2))

        window._toggle_routine_instance_collapsed("inst-a")
        self.assertEqual(set(), window._collapsed_auto_trade_instance_ids)
        self.assertEqual("▼", _row_icon(1).text())
        self.assertFalse(window.routine_table.isRowHidden(2))
        window._refresh_routine_tree_display_state()
        window.routine_table.viewport().update()
        self.assertEqual("▼", _row_icon(1).text())
        self.assertFalse(window.routine_table.isRowHidden(2))

        window._toggle_routine_instance_collapsed("inst-a")
        self.assertEqual({"inst-a"}, window._collapsed_auto_trade_instance_ids)
        self.assertEqual("▶", _row_icon(1).text())
        self.assertTrue(window.routine_table.isRowHidden(2))

        with patch.object(setting_window, "load_routine_definitions", return_value=[self._definition()]), \
                patch.object(setting_window, "load_persisted_routine_instances", return_value=instances), \
                patch.object(setting_window, "read_base_stocks", return_value=stocks):
            window._set_routine_tree_display_level("stock")
        self.assertEqual(set(), window._collapsed_auto_trade_definition_ids)
        self.assertEqual(set(), window._collapsed_auto_trade_instance_ids)
        self.assertEqual("▼", _row_icon(0).text())
        self.assertEqual("▼", _row_icon(1).text())
        self.assertFalse(window.routine_table.isRowHidden(2))

    def test_empty_definition_keeps_default_performance_visible(self) -> None:
        empty_definition = RoutineDefinitionRecord(
            definition_id="review",
            display_name="등록확인루틴",
            package_dir=Path("routines") / "review",
            schema_version="1.0",
            version="1.0",
            routine_type="auto_trade",
            entry_file="routine.py",
            module_name="review_routine",
            settings_ui="",
            default_rules_file="rules.json",
            package_enabled=True,
            source_name="routine.json",
        )
        with patch.object(AutoTradeSettingWindow, "refresh_all", lambda _self: None), \
                patch.object(AutoTradeSettingWindow, "update_startup_recovery_controls", lambda _self: None), \
                patch.object(AutoTradeSettingWindow, "current_runtime_file_signature", lambda _self: tuple()):
            window = AutoTradeSettingWindow()
        self.addCleanup(window.close)
        window._routine_instance_operation_counts = lambda: {}

        with patch.object(setting_window, "load_routine_definitions", return_value=[empty_definition]), \
                patch.object(setting_window, "load_persisted_routine_instances", return_value=[]), \
                patch.object(setting_window, "read_base_stocks", return_value=[]):
            window.load_routine_table()
            window._set_routine_tree_display_level("category")
        window.show()
        self._app.processEvents()

        widget = window.routine_table.cellWidget(0, 0)
        icon = widget.findChild(
            setting_window.QLabel,
            "autoTradeSettingRoutineTreeIcon",
        )
        self.assertEqual("▶", icon.text())
        count_badge = widget.findChild(
            setting_window.QLabel,
            "autoTradeSettingRoutineTreeInstanceCount",
        )
        self.assertEqual("루틴0", count_badge.text())

        expected_labels = (
            ("autoTradeSettingRoutineTreePerformanceProfitLeftValue", "0"),
            ("autoTradeSettingRoutineTreePerformanceProfitRightValue", "0.00%"),
            ("autoTradeSettingRoutineTreePerformanceAverageLeftValue", "0"),
            ("autoTradeSettingRoutineTreePerformanceAverageRightValue", "0.00%"),
            ("autoTradeSettingRoutineTreePerformanceEfficiencyLeftValue", "0.0"),
        )

        def _assert_default_summary_visible() -> None:
            self.assertTrue(
                bool(widget.property("autoTradeSettingRoutineTreeSummaryPinned"))
            )
            for object_name, expected in expected_labels:
                label = widget.findChild(setting_window.QLabel, object_name)
                self.assertIsNotNone(label)
                self.assertFalse(label.isHidden())
                self.assertTrue(label.isVisible())
                self.assertGreater(label.width(), 0)
                self.assertEqual(expected, label.text())

        _assert_default_summary_visible()
        self._app.sendEvent(widget, setting_window.QEvent(setting_window.QEvent.Leave))
        self._app.processEvents()
        _assert_default_summary_visible()

        for level in ("routine", "stock", "category"):
            window._set_routine_tree_display_level(level)
            self._app.processEvents()
            _assert_default_summary_visible()

        window._refresh_routine_tree_display_state()
        window.routine_table.viewport().update()
        self._app.processEvents()
        _assert_default_summary_visible()

    def test_empty_definition_summary_follows_visible_tree_depth(self) -> None:
        empty_definition = RoutineDefinitionRecord(
            definition_id="review",
            display_name="등록확인루틴",
            package_dir=Path("routines") / "review",
            schema_version="1.0",
            version="1.0",
            routine_type="auto_trade",
            entry_file="routine.py",
            module_name="review_routine",
            settings_ui="",
            default_rules_file="rules.json",
            package_enabled=True,
            source_name="routine.json",
        )
        instances = [self._instance("inst-a", "A 인스턴스")]
        with patch.object(AutoTradeSettingWindow, "refresh_all", lambda _self: None), \
                patch.object(AutoTradeSettingWindow, "update_startup_recovery_controls", lambda _self: None), \
                patch.object(AutoTradeSettingWindow, "current_runtime_file_signature", lambda _self: tuple()):
            window = AutoTradeSettingWindow()
        self.addCleanup(window.close)
        window._routine_instance_operation_counts = lambda: {}

        with patch.object(
            setting_window,
            "load_routine_definitions",
            return_value=[self._definition(), empty_definition],
        ), patch.object(
            setting_window,
            "load_persisted_routine_instances",
            return_value=instances,
        ), patch.object(setting_window, "read_base_stocks", return_value=[]):
            window.load_routine_table()
        window.show()
        self._app.processEvents()

        def _definition_widget(definition_id: str):
            for row in range(window.routine_table.rowCount()):
                item = window.routine_table.item(row, 0)
                metadata = item.data(setting_window.Qt.UserRole)
                if (
                    metadata["row_kind"] == "definition"
                    and metadata["definition_id"] == definition_id
                ):
                    return window.routine_table.cellWidget(row, 0)
            self.fail(f"definition row not found: {definition_id}")

        def _summary_widgets(widget):
            return [
                child
                for child in widget.findChildren(setting_window.QWidget)
                if child.property("autoTradeSettingParentSummaryMetric")
            ]

        with patch.object(
            setting_window,
            "load_routine_definitions",
            return_value=[self._definition(), empty_definition],
        ), patch.object(
            setting_window,
            "load_persisted_routine_instances",
            return_value=instances,
        ), patch.object(setting_window, "read_base_stocks", return_value=[]):
            window._set_routine_tree_display_level("category")
        self._app.processEvents()
        review_widget = _definition_widget("review")
        self.assertTrue(
            bool(review_widget.property("autoTradeSettingRoutineTreeSummaryPinned"))
        )
        self.assertTrue(all(not child.isHidden() for child in _summary_widgets(review_widget)))

        with patch.object(
            setting_window,
            "load_routine_definitions",
            return_value=[self._definition(), empty_definition],
        ), patch.object(
            setting_window,
            "load_persisted_routine_instances",
            return_value=instances,
        ), patch.object(setting_window, "read_base_stocks", return_value=[]):
            window._set_routine_tree_display_level("routine")
        self._app.processEvents()
        indicator_widget = _definition_widget("indicator_follow")
        review_widget = _definition_widget("review")
        self.assertFalse(
            bool(review_widget.property("autoTradeSettingRoutineTreeSummaryPinned"))
        )
        self.assertTrue(all(child.isHidden() for child in _summary_widgets(review_widget)))
        self.assertFalse(
            bool(indicator_widget.property("autoTradeSettingRoutineTreeSummaryPinned"))
        )

        review_title = review_widget.findChild(
            setting_window.QLabel,
            "autoTradeSettingRoutineTreeTitle",
        )
        hover_position = review_title.mapTo(review_widget, review_title.rect().center())
        self._app.sendEvent(
            review_widget,
            QMouseEvent(
                setting_window.QEvent.MouseMove,
                hover_position,
                setting_window.Qt.NoButton,
                setting_window.Qt.NoButton,
                setting_window.Qt.NoModifier,
            ),
        )
        self._app.processEvents()
        self.assertTrue(all(not child.isHidden() for child in _summary_widgets(review_widget)))

        self._app.sendEvent(
            review_widget,
            setting_window.QEvent(setting_window.QEvent.Leave),
        )
        self._app.processEvents()
        self.assertTrue(all(child.isHidden() for child in _summary_widgets(review_widget)))

        window._toggle_routine_definition_collapsed("indicator_follow")
        self._app.processEvents()
        self.assertTrue(
            bool(review_widget.property("autoTradeSettingRoutineTreeSummaryPinned"))
        )
        self.assertTrue(all(not child.isHidden() for child in _summary_widgets(review_widget)))

        window._toggle_routine_definition_collapsed("indicator_follow")
        window._refresh_routine_tree_display_state()
        window.routine_table.viewport().update()
        self._app.processEvents()
        self.assertFalse(
            bool(review_widget.property("autoTradeSettingRoutineTreeSummaryPinned"))
        )
        self.assertTrue(all(child.isHidden() for child in _summary_widgets(review_widget)))

    def test_tree_display_scope_and_metric_are_independent_and_preserve_collapse(self) -> None:
        instances = [self._instance("inst-a", "A 인스턴스")]
        stocks = [
            {
                "stock_path": "stocks/005930_A",
                "assigned_routine_instance_id": "inst-a",
                "code": "005930",
                "name": "삼성전자",
            },
        ]
        window = self._window_harness()
        window._routine_tree_display_level_buttons = {}
        window._routine_tree_display_scope_buttons = {}
        window._routine_tree_display_criterion_buttons = {}
        window._routine_instance_operation_counts = lambda: {
            "inst-a": {"registered": 1, "running": 0, "stopped": 1, "error": 0},
        }

        with patch.object(setting_window, "load_routine_definitions", return_value=[self._definition()]), \
                patch.object(setting_window, "load_persisted_routine_instances", return_value=instances), \
                patch.object(setting_window, "read_base_stocks", return_value=stocks):
            window.load_routine_table()
            window._set_routine_tree_display_criterion("period")
            self.assertEqual("period", window._routine_tree_display_criterion)

            window._set_routine_tree_display_level("routine")
            window._set_routine_tree_display_criterion("period")
            self.assertEqual("period", window._routine_tree_display_criterion)
            instance_metadata = window.routine_table.item(1, 0).data(setting_window.Qt.UserRole)
            self.assertEqual("routine", instance_metadata["display_level"])
            self.assertEqual("all", instance_metadata["display_scope"])
            self.assertEqual("period", instance_metadata["display_metric"])

            window._set_routine_tree_display_level("category")
            self.assertEqual("period", window._routine_tree_display_criterion)

            window._set_routine_tree_display_level("stock")
            self.assertEqual("all", window._routine_tree_display_scope)
            window._toggle_routine_instance_collapsed("inst-a")
            collapsed_before = set(window._collapsed_auto_trade_instance_ids)
            window._set_routine_tree_display_scope("all")
            self.assertEqual("all", window._routine_tree_display_scope)
            self.assertEqual("period", window._routine_tree_display_criterion)
            self.assertEqual(collapsed_before, window._collapsed_auto_trade_instance_ids)
            self.assertEqual(
                ["definition", "instance", "stock"],
                [
                    window.routine_table.item(row, 0).data(setting_window.Qt.UserRole)["row_kind"]
                    for row in range(window.routine_table.rowCount())
                ],
            )
            stock_metadata = window.routine_table.item(2, 0).data(setting_window.Qt.UserRole)
            self.assertEqual("all", stock_metadata["display_scope"])
            self.assertEqual("stock", stock_metadata["display_level"])
            self.assertEqual("period", stock_metadata["display_metric"])
            self.assertTrue(window.routine_table.isRowHidden(2))

            window._set_routine_tree_display_scope("current")
            self.assertEqual("current", window._routine_tree_display_scope)
            self.assertEqual(collapsed_before, window._collapsed_auto_trade_instance_ids)
            window._set_routine_tree_display_level("routine")
            self.assertEqual("current", window._routine_tree_display_scope)
            window._set_routine_tree_display_level("stock")
            self.assertEqual("current", window._routine_tree_display_scope)

    def test_routine_period_uses_unique_filled_trade_days_and_excludes_zero_day_stocks(self) -> None:
        window = self._window_harness()
        orders_by_name = {
            "a": [
                {"side": "BUY", "filled_qty": 1, "filled_price": 100, "order_time": "2026-07-01 09:00:00"},
                {"side": "SELL", "filled_qty": 1, "filled_price": 110, "order_time": "2026-07-01 10:00:00"},
                {"side": "BUY", "filled_qty": 1, "filled_price": 120, "order_time": "2026-07-02 09:00:00"},
            ],
            "b": [
                {"side": "BUY", "filled_qty": 1, "filled_price": 200, "order_time": "2026-07-03 09:00:00"},
            ],
            "c": [],
        }
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            stocks = []
            for name, orders in orders_by_name.items():
                stock_dir = root / name
                stock_dir.mkdir()
                (stock_dir / "orders.json").write_text(
                    json.dumps({"orders": orders}, ensure_ascii=False),
                    encoding="utf-8",
                )
                stocks.append({"stock_path": str(stock_dir), "is_current": name != "c"})
            texts = window._routine_tree_performance_texts(stocks)
            empty_texts = window._routine_tree_performance_texts([stocks[-1]])
            source = window._routine_tree_stock_performance_source(stocks[0])

        self.assertEqual("기간(1)", texts["performance_period_text"])
        self.assertEqual("수익(+10 / 0.00%)", texts["performance_profit_text"])
        self.assertEqual("평균(0 / 0.00%)", texts["performance_average_text"])
        self.assertEqual("효율(0.0)", texts["performance_efficiency_text"])
        self.assertEqual("기간(0)", empty_texts["performance_period_text"])
        self.assertEqual("+10", texts["performance_profit_amount"])
        self.assertEqual("0.00%", texts["performance_profit_rate"])
        self.assertEqual("0", texts["performance_average_amount"])
        self.assertEqual("0.00%", texts["performance_average_rate"])
        self.assertEqual("0.0", texts["performance_efficiency_value"])
        self.assertEqual(
            {
                "trade_days",
                "realized_profit",
                "profit_rate",
                "average",
                "average_rate",
                "gross_profit",
                "gross_loss_abs",
                "profit_factor",
                "is_current",
            },
            set(source),
        )
        self.assertEqual(2, source["trade_days"])
        self.assertEqual(10.0, source["realized_profit"])
        self.assertTrue(source["is_current"])

    def test_level_and_metric_badges_update_actual_row_values_without_expanding_tree(self) -> None:
        instances = [self._instance("inst-a", "A 인스턴스")]
        stocks = [
            {
                "stock_path": "stocks/005930_A",
                "assigned_routine_instance_id": "inst-a",
                "code": "005930",
                "name": "삼성전자",
            },
        ]
        orders = [
            {"side": "BUY", "filled_qty": 1, "filled_price": 100, "order_time": "2026-07-01 09:00:00"},
            {"side": "SELL", "filled_qty": 1, "filled_price": 110, "order_time": "2026-07-02 09:00:00"},
        ]
        window = self._window_harness()
        window._routine_tree_display_level_buttons = {}
        window._routine_tree_display_scope_buttons = {}
        window._routine_tree_display_criterion_buttons = {}
        window._routine_instance_operation_counts = lambda: {
            "inst-a": {"registered": 1, "running": 0, "stopped": 1, "error": 0},
        }

        with patch.object(setting_window, "load_routine_definitions", return_value=[self._definition()]), \
                patch.object(setting_window, "load_persisted_routine_instances", return_value=instances), \
                patch.object(setting_window, "read_base_stocks", return_value=stocks), \
                patch.object(setting_window, "read_orders_data", return_value=orders):
            window.load_routine_table()
            window._toggle_routine_instance_collapsed("inst-a")
            collapsed_before = set(window._collapsed_auto_trade_instance_ids)

            window._set_routine_tree_display_level("routine")
            window._set_routine_tree_display_criterion("period")
            instance_widget = window.routine_table.cellWidget(1, 0)
            instance_period = instance_widget.findChild(
                setting_window.QLabel,
                "autoTradeSettingRoutineTreePerformancePeriodLeftValue",
            )
            instance_profit = instance_widget.findChild(
                setting_window.QLabel,
                "autoTradeSettingRoutineTreePerformanceProfitLeftValue",
            )
            self.assertEqual("2", instance_period.text())
            self.assertEqual("+10", instance_profit.text())
            instance_average = instance_widget.findChild(
                setting_window.QLabel,
                "autoTradeSettingRoutineTreePerformanceAverageLeftValue",
            )
            instance_efficiency = instance_widget.findChild(
                setting_window.QLabel,
                "autoTradeSettingRoutineTreePerformanceEfficiencyLeftValue",
            )
            self.assertEqual("0", instance_average.text())
            self.assertEqual("0.0", instance_efficiency.text())
            self.assertEqual(collapsed_before, window._collapsed_auto_trade_instance_ids)
            self.assertTrue(window.routine_table.isRowHidden(2))

            window._set_routine_tree_display_level("stock")
            stock_widget = window.routine_table.cellWidget(2, 0)
            stock_period = stock_widget.findChild(
                setting_window.QLabel,
                "autoTradeSettingRoutineTreePerformancePeriodLeftValue",
            )
            self.assertEqual("2", stock_period.text())
            window._set_routine_tree_display_criterion("profit")
            stock_profit = stock_widget.findChild(
                setting_window.QLabel,
                "autoTradeSettingRoutineTreePerformanceProfitLeftValue",
            )
            self.assertEqual("+10", stock_profit.text())
            self.assertEqual("2", stock_period.text())
            self.assertEqual("0", instance_average.text())
            self.assertEqual("0.0", instance_efficiency.text())
            self.assertEqual(set(), window._collapsed_auto_trade_instance_ids)
            self.assertFalse(window.routine_table.isRowHidden(2))

    def test_routine_tree_performance_formatter_contract(self) -> None:
        window = self._window_harness()
        cases = (
            (
                "positive",
                {
                    "trade_days": 125,
                    "realized_profit": 123456.0,
                    "profit_rate": 12.3,
                    "average": 62500.0,
                    "average_rate": 1.63,
                    "profit_factor": 123.4,
                    "is_current": True,
                },
                {
                    "performance_period_value": "125",
                    "performance_profit_amount": "+123,456",
                    "performance_profit_rate": "+12.30%",
                    "performance_profit_color": "#DC2626",
                    "performance_average_amount": "+62,500",
                    "performance_average_rate": "+1.63%",
                    "performance_average_color": "#DC2626",
                    "performance_efficiency_value": "123.4",
                },
            ),
            (
                "negative",
                {
                    "trade_days": 1,
                    "realized_profit": -2500.0,
                    "profit_rate": -4.2,
                    "average": -24000.0,
                    "average_rate": -0.7,
                    "profit_factor": -1.4,
                    "is_current": True,
                },
                {
                    "performance_period_value": "1",
                    "performance_profit_amount": "-2,500",
                    "performance_profit_rate": "-4.20%",
                    "performance_profit_color": "#2563EB",
                    "performance_average_amount": "-24,000",
                    "performance_average_rate": "-0.70%",
                    "performance_average_color": "#2563EB",
                    "performance_efficiency_value": "0.0",
                },
            ),
            (
                "empty",
                {
                    "trade_days": None,
                    "realized_profit": None,
                    "profit_rate": None,
                    "average": None,
                    "average_rate": None,
                    "profit_factor": None,
                    "is_current": True,
                },
                {
                    "performance_period_value": "0",
                    "performance_profit_amount": "0",
                    "performance_profit_rate": "0.00%",
                    "performance_profit_color": "#374151",
                    "performance_average_amount": "0",
                    "performance_average_rate": "0.00%",
                    "performance_average_color": "#374151",
                    "performance_efficiency_value": "0.0",
                },
            ),
        )

        for stock_path, source, expected in cases:
            texts = window._routine_tree_performance_texts(
                [{"stock_path": stock_path}],
                {stock_path: source},
            )
            for key, expected_value in expected.items():
                self.assertEqual(expected_value, texts[key])

    def test_historical_performance_uses_assignment_time_window(self) -> None:
        window = self._window_harness()
        orders = [
            {
                "side": "BUY",
                "filled_qty": 1,
                "filled_price": 10,
                "order_time": "2026-07-01 09:00:00",
            },
            {
                "side": "SELL",
                "filled_qty": 1,
                "filled_price": 1010,
                "order_time": "2026-07-01 10:00:00",
            },
            {
                "side": "BUY",
                "filled_qty": 10,
                "filled_price": 100,
                "order_time": "2026-07-02 09:00:00",
            },
            {
                "side": "SELL",
                "filled_qty": 10,
                "filled_price": 150,
                "order_time": "2026-07-03 10:00:00",
            },
            {
                "side": "BUY",
                "filled_qty": 1,
                "filled_price": 10,
                "order_time": "2026-07-04 09:00:00",
            },
            {
                "side": "SELL",
                "filled_qty": 1,
                "filled_price": 2010,
                "order_time": "2026-07-04 10:00:00",
            },
        ]
        historical_stock = {
            "stock_path": "stocks/000001_과거종목",
            "stock_code": "000001",
            "stock_name": "과거종목",
            "instance_id": "inst-a",
            "is_historical": True,
            "registered_at": "2026-07-02 00:00:00",
            "unregistered_at": "2026-07-03 23:59:59",
        }

        with patch.object(
            setting_window,
            "read_orders_data",
            return_value=orders,
        ):
            source = window._routine_tree_stock_performance_source(
                historical_stock
            )

        self.assertEqual(2, source["trade_days"])
        self.assertEqual(500.0, source["realized_profit"])
        self.assertFalse(source["is_current"])

    def test_performance_fixture_changes_group_routine_and_stock_tree_text(self) -> None:
        instances = [
            self._instance("inst-a", "A 인스턴스"),
            self._instance("inst-b", "B 인스턴스"),
        ]
        orders_by_code = {
            "000001": [
                {"side": "BUY", "filled_qty": 1, "filled_price": 100, "order_time": "2026-07-01 09:00:00"},
                {"side": "SELL", "filled_qty": 1, "filled_price": 120, "order_time": "2026-07-02 09:00:00"},
            ],
            "000002": [],
            "000003": [
                {"side": "BUY", "filled_qty": 1, "filled_price": 200, "order_time": "2026-07-03 09:00:00"},
                {"side": "SELL", "filled_qty": 1, "filled_price": 205, "order_time": "2026-07-03 10:00:00"},
            ],
        }
        assignments = {
            "000001": "inst-a",
            "000002": "inst-a",
            "000003": "inst-b",
        }
        window = self._window_harness()
        window._routine_tree_display_level_buttons = {}
        window._routine_tree_display_scope_buttons = {}
        window._routine_tree_display_criterion_buttons = {}
        window._routine_instance_operation_counts = lambda: {
            "inst-a": {"registered": 2, "running": 0, "stopped": 2, "error": 0},
            "inst-b": {"registered": 1, "running": 0, "stopped": 1, "error": 0},
        }

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            stocks = []
            for code, orders in orders_by_code.items():
                stock_dir = root / code
                stock_dir.mkdir()
                (stock_dir / "orders.json").write_text(
                    json.dumps({"orders": orders}, ensure_ascii=False),
                    encoding="utf-8",
                )
                stocks.append(
                    {
                        "stock_path": str(stock_dir),
                        "assigned_routine_instance_id": assignments[code],
                        "code": code,
                        "name": f"종목{code[-1]}",
                    }
                )

            with patch.object(setting_window, "load_routine_definitions", return_value=[self._definition()]), \
                    patch.object(setting_window, "load_persisted_routine_instances", return_value=instances), \
                    patch.object(setting_window, "read_base_stocks", return_value=stocks):
                window.load_routine_table()

                rows = {}
                for row in range(window.routine_table.rowCount()):
                    metadata = window.routine_table.item(row, 0).data(setting_window.Qt.UserRole)
                    key = (
                        str(metadata.get("row_kind", "")),
                        str(metadata.get("instance_id", "")),
                        str(metadata.get("stock_code", "")),
                    )
                    rows[key] = row

                def _left_value(row: int, metric: str) -> str:
                    widget = window.routine_table.cellWidget(row, 0)
                    label = widget.findChild(
                        setting_window.QLabel,
                        f"autoTradeSettingRoutineTreePerformance{metric.title()}LeftValue",
                    )
                    return label.text()

                def _left_color(row: int, metric: str) -> str:
                    widget = window.routine_table.cellWidget(row, 0)
                    label = widget.findChild(
                        setting_window.QLabel,
                        f"autoTradeSettingRoutineTreePerformance{metric.title()}LeftValue",
                    )
                    return label.styleSheet()

                group_row = rows[("definition", "", "")]
                instance_a_row = rows[("instance", "inst-a", "")]
                instance_b_row = rows[("instance", "inst-b", "")]
                stock_a_row = rows[("stock", "inst-a", "000001")]
                stock_empty_row = rows[("stock", "inst-a", "000002")]
                stock_b_row = rows[("stock", "inst-b", "000003")]

                text_changes = 0
                before = _left_value(group_row, "profit")
                self.assertEqual("+25", before)

                window._set_routine_tree_display_level("routine")
                after = _left_value(instance_a_row, "profit")
                text_changes += int(after != before)
                self.assertEqual("+20", after)
                self.assertEqual("+5", _left_value(instance_b_row, "profit"))
                self.assertIn("color: #DC2626", _left_color(instance_a_row, "profit"))
                self.assertIn("color: #DC2626", _left_color(instance_b_row, "profit"))

                before = _left_value(instance_a_row, "profit")
                window._set_routine_tree_display_criterion("period")
                after = _left_value(instance_a_row, "period")
                text_changes += int(after != before)
                self.assertEqual("2", after)
                self.assertEqual("1", _left_value(instance_b_row, "period"))

                window._set_routine_tree_display_level("stock")
                self.assertEqual("2", _left_value(stock_a_row, "period"))
                self.assertEqual("0", _left_value(stock_empty_row, "period"))
                self.assertEqual("1", _left_value(stock_b_row, "period"))

                before = _left_value(stock_a_row, "period")
                window._set_routine_tree_display_criterion("profit")
                after = _left_value(stock_a_row, "profit")
                text_changes += int(after != before)
                self.assertEqual("+20", after)
                self.assertEqual("0", _left_value(stock_empty_row, "profit"))
                self.assertEqual("+5", _left_value(stock_b_row, "profit"))
                self.assertIn("color: #DC2626", _left_color(stock_a_row, "profit"))
                self.assertIn("color: #374151", _left_color(stock_empty_row, "profit"))
                self.assertGreaterEqual(text_changes, 3)

    def test_actual_window_renders_positive_negative_and_zero_performance(self) -> None:
        instances = [
            self._instance("inst-positive", "양수 인스턴스"),
            self._instance("inst-negative", "음수 인스턴스"),
            self._instance("inst-zero", "중립 인스턴스"),
        ]
        stocks = [
            {
                "stock_path": "fixture/positive",
                "assigned_routine_instance_id": "inst-positive",
                "code": "000001",
                "name": "양수종목",
            },
            {
                "stock_path": "fixture/negative",
                "assigned_routine_instance_id": "inst-negative",
                "code": "000002",
                "name": "음수종목",
            },
            {
                "stock_path": "fixture/zero",
                "assigned_routine_instance_id": "inst-zero",
                "code": "000003",
                "name": "중립종목",
            },
            {
                "stock_path": "fixture/missing",
                "assigned_routine_instance_id": "inst-zero",
                "code": "000004",
                "name": "값없음현재종목",
            },
            {
                "stock_path": "fixture/balance",
                "assigned_routine_instance_id": "inst-negative",
                "code": "000005",
                "name": "부모합계균형종목",
            },
        ]
        performance_by_code = {
            "000001": {
                "trade_days": 3,
                "realized_profit": 125000.0,
                "profit_rate": 3.25,
                "average": 62500.0,
                "average_rate": 1.63,
                "profit_factor": 3.2,
                "is_current": True,
            },
            "000002": {
                "trade_days": 2,
                "realized_profit": -48000.0,
                "profit_rate": -1.40,
                "average": -24000.0,
                "average_rate": -0.70,
                "profit_factor": 0.0,
                "is_current": True,
            },
            "000003": {
                "trade_days": None,
                "realized_profit": 0.0,
                "profit_rate": 0.0,
                "average": 0.0,
                "average_rate": 0.0,
                "profit_factor": 0.0,
                "is_current": True,
            },
            "000004": {
                "trade_days": None,
                "realized_profit": None,
                "profit_rate": None,
                "average": None,
                "average_rate": None,
                "profit_factor": None,
                "is_current": True,
            },
            "000005": {
                "trade_days": 1,
                "realized_profit": -77000.0,
                "profit_rate": -2.00,
                "average": -38500.0,
                "average_rate": -0.93,
                "profit_factor": 0.0,
                "is_current": True,
            },
            "100001": {
                "trade_days": 3,
                "realized_profit": 125000.0,
                "profit_rate": 3.25,
                "average": 62500.0,
                "average_rate": 1.63,
                "profit_factor": 3.2,
                "is_current": False,
            },
            "100002": {
                "trade_days": 2,
                "realized_profit": -48000.0,
                "profit_rate": -1.40,
                "average": -24000.0,
                "average_rate": -0.70,
                "profit_factor": 0.0,
                "is_current": False,
            },
            "100003": {
                "trade_days": 0,
                "realized_profit": 0.0,
                "profit_rate": 0.0,
                "average": 0.0,
                "average_rate": 0.0,
                "profit_factor": 0.0,
                "is_current": False,
            },
            "100004": {
                "trade_days": None,
                "realized_profit": None,
                "profit_rate": None,
                "average": None,
                "average_rate": None,
                "profit_factor": None,
                "is_current": False,
            },
        }
        historical_stocks = {}
        for instance_id, code, name in (
                ("inst-positive", "100001", "과거양수종목"),
                ("inst-negative", "100002", "과거음수종목"),
                ("inst-zero", "100003", "과거중립종목"),
                ("inst-zero", "100004", "값없음과거종목"),
        ):
            historical_stocks.setdefault(instance_id, []).append(
                {
                    "instance_id": instance_id,
                    "stock_path": f"fixture/historical-{code}",
                    "stock_code": code,
                    "stock_name": name,
                    "is_historical": True,
                    "is_development_fixture": True,
                }
            )

        with (
            patch.object(AutoTradeSettingWindow, "refresh_all"),
            patch.object(
                AutoTradeSettingWindow,
                "update_startup_recovery_controls",
            ),
        ):
            window = AutoTradeSettingWindow()
        try:
            window._routine_instance_operation_counts = lambda: {
                instance.instance_id: {
                    "registered": 1,
                    "running": 0,
                    "stopped": 1,
                    "error": 0,
                }
                for instance in instances
            }
            window._historical_stocks_by_instance = lambda: historical_stocks

            def performance_source(
                _window,
                stock: dict[str, object],
            ) -> dict[str, object]:
                code = str(stock.get("stock_code", stock.get("code", "")))
                return dict(performance_by_code[code])

            window._routine_tree_stock_performance_source = MethodType(
                performance_source,
                window,
            )
            with (
                patch.object(
                    setting_window,
                    "load_routine_definitions",
                    return_value=[self._definition()],
                ),
                patch.object(
                    setting_window,
                    "load_persisted_routine_instances",
                    return_value=instances,
                ),
                patch.object(
                    setting_window,
                    "read_base_stocks",
                    return_value=stocks,
                ),
            ):
                window.load_routine_table()
                window._set_routine_tree_display_level("stock")
            window._routine_tree_display_criterion = "profit"
            window._update_routine_tree_display_level_badges()
            window._refresh_routine_tree_display_state()
            window.resize(1280, 720)
            window.show()
            self._app.processEvents()

            rows_by_code = {}
            for row in range(window.routine_table.rowCount()):
                item = window.routine_table.item(row, 0)
                metadata = item.data(Qt.UserRole) if item is not None else {}
                code = str((metadata or {}).get("stock_code", ""))
                if code:
                    rows_by_code[code] = row

            expected = {
                "000001": (
                    "+125,000",
                    "+3.25%",
                    "+62,500",
                    "+1.63%",
                    "#DC2626",
                    "#DC2626",
                ),
                "000002": (
                    "-48,000",
                    "-1.40%",
                    "-24,000",
                    "-0.70%",
                    "#2563EB",
                    "#2563EB",
                ),
                "000003": (
                    "0",
                    "0.00%",
                    "0",
                    "0.00%",
                    "#374151",
                    "#374151",
                ),
                "000004": (
                    "0",
                    "0.00%",
                    "0",
                    "0.00%",
                    "#374151",
                    "#374151",
                ),
                "000005": (
                    "-77,000",
                    "-2.00%",
                    "-38,500",
                    "-0.93%",
                    "#2563EB",
                    "#2563EB",
                ),
                "100001": (
                    "+125,000",
                    "+3.25%",
                    "+62,500",
                    "+1.63%",
                    "#DC2626",
                    "#DC2626",
                ),
                "100002": (
                    "-48,000",
                    "-1.40%",
                    "-24,000",
                    "-0.70%",
                    "#2563EB",
                    "#2563EB",
                ),
                "100003": (
                    "0",
                    "0.00%",
                    "0",
                    "0.00%",
                    "#374151",
                    "#374151",
                ),
                "100004": (
                    "0",
                    "0.00%",
                    "0",
                    "0.00%",
                    "#374151",
                    "#374151",
                ),
            }
            expected_profit_factors = {
                "000001": ("3.2", "#2563EB"),
                "000002": ("0.0", "#374151"),
                "000003": ("0.0", "#374151"),
                "000004": ("0.0", "#374151"),
                "000005": ("0.0", "#374151"),
                "100001": ("3.2", "#2563EB"),
                "100002": ("0.0", "#374151"),
                "100003": ("0.0", "#374151"),
                "100004": ("0.0", "#374151"),
            }
            profit_right_edges = set()
            average_right_edges = set()
            for code, values in expected.items():
                widget = window.routine_table.cellWidget(rows_by_code[code], 0)
                profit_amount = widget.findChild(
                    setting_window.QLabel,
                    "autoTradeSettingRoutineTreePerformanceProfitLeftValue",
                )
                profit_rate = widget.findChild(
                    setting_window.QLabel,
                    "autoTradeSettingRoutineTreePerformanceProfitRightValue",
                )
                average_amount = widget.findChild(
                    setting_window.QLabel,
                    "autoTradeSettingRoutineTreePerformanceAverageLeftValue",
                )
                average_rate = widget.findChild(
                    setting_window.QLabel,
                    "autoTradeSettingRoutineTreePerformanceAverageRightValue",
                )
                efficiency = widget.findChild(
                    setting_window.QLabel,
                    "autoTradeSettingRoutineTreePerformanceEfficiencyLeftValue",
                )
                self.assertEqual(values[0], profit_amount.text())
                self.assertEqual(values[1], profit_rate.text())
                self.assertEqual(values[2], average_amount.text())
                self.assertEqual(values[3], average_rate.text())
                icon = widget.findChild(
                    setting_window.QLabel,
                    "autoTradeSettingRoutineTreeIcon",
                )
                self.assertEqual(
                    "▪" if code.startswith("1") else "✓",
                    icon.text(),
                )
                for label in (profit_amount, profit_rate):
                    self.assertIn(f"color: {values[4]}", label.styleSheet())
                for label in (average_amount, average_rate):
                    self.assertIn(f"color: {values[5]}", label.styleSheet())
                self.assertEqual(
                    expected_profit_factors[code][0],
                    efficiency.text(),
                )
                self.assertIn(
                    f"color: {expected_profit_factors[code][1]}",
                    efficiency.styleSheet(),
                )
                self.assertNotIn("-", efficiency.text())
                profit_right_edges.add(
                    profit_rate.mapTo(widget, profit_rate.rect().topRight()).x()
                )
                average_right_edges.add(
                    average_rate.mapTo(widget, average_rate.rect().topRight()).x()
                )
            self.assertEqual(1, len(profit_right_edges))
            self.assertEqual(1, len(average_right_edges))

            parent_widget = window.routine_table.cellWidget(0, 0)
            window._set_routine_tree_parent_summary_visible(
                parent_widget,
                True,
            )
            parent_profit = parent_widget.findChild(
                setting_window.QLabel,
                "autoTradeSettingRoutineTreePerformanceProfitLeftValue",
            )
            parent_average = parent_widget.findChild(
                setting_window.QLabel,
                "autoTradeSettingRoutineTreePerformanceAverageLeftValue",
            )
            self.assertEqual("+77,000", parent_profit.text())
            self.assertEqual("+5,500", parent_average.text())

            zero_instance_row = next(
                row
                for row in range(window.routine_table.rowCount())
                if (
                    window.routine_table.item(row, 0)
                    .data(Qt.UserRole)
                    .get("row_kind")
                    == "instance"
                    and window.routine_table.item(row, 0)
                    .data(Qt.UserRole)
                    .get("instance_id")
                    == "inst-zero"
                )
            )
            zero_instance_widget = window.routine_table.cellWidget(
                zero_instance_row,
                0,
            )
            self.assertEqual(
                "0",
                zero_instance_widget.findChild(
                    setting_window.QLabel,
                    "autoTradeSettingRoutineTreePerformanceProfitLeftValue",
                ).text(),
            )

            anchor_object_names = (
                "autoTradeSettingRoutineTreePerformancePeriod",
                "autoTradeSettingRoutineTreePerformancePeriodLeftValue",
                "autoTradeSettingRoutineTreePerformancePeriodClose",
                "autoTradeSettingRoutineTreePerformanceProfit",
                "autoTradeSettingRoutineTreePerformanceProfitLeftValue",
                "autoTradeSettingRoutineTreePerformanceProfitSlash",
                "autoTradeSettingRoutineTreePerformanceProfitRightValue",
                "autoTradeSettingRoutineTreePerformanceProfitClose",
                "autoTradeSettingRoutineTreePerformanceAverage",
                "autoTradeSettingRoutineTreePerformanceAverageLeftValue",
                "autoTradeSettingRoutineTreePerformanceAverageSlash",
                "autoTradeSettingRoutineTreePerformanceAverageRightValue",
                "autoTradeSettingRoutineTreePerformanceAverageClose",
                "autoTradeSettingRoutineTreePerformanceEfficiency",
                "autoTradeSettingRoutineTreePerformanceEfficiencyLeftValue",
                "autoTradeSettingRoutineTreePerformanceEfficiencyClose",
            )
            expected_anchors = None
            for row in range(window.routine_table.rowCount()):
                widget = window.routine_table.cellWidget(row, 0)
                widget.layout().activate()
                anchors = []
                for object_name in anchor_object_names:
                    child = widget.findChild(
                        setting_window.QWidget,
                        object_name,
                    )
                    self.assertIsNotNone(
                        child,
                        f"row={row} object={object_name}",
                    )
                    top_left = child.mapTo(widget, child.rect().topLeft())
                    top_right = child.mapTo(widget, child.rect().topRight())
                    anchors.append((top_left.x(), top_right.x()))
                if expected_anchors is None:
                    expected_anchors = anchors
                else:
                    identity_compensation = widget.findChild(
                        setting_window.QWidget,
                        "autoTradeSettingRoutineTreeIdentityXCompensation",
                    )
                    self.assertEqual(
                        expected_anchors,
                        anchors,
                        (
                            f"row={row} metadata="
                            f"{window.routine_table.item(row, 0).data(Qt.UserRole)} "
                            f"font={widget.font().toString()} "
                            f"identity_compensation="
                            f"{identity_compensation.width() if identity_compensation else None}"
                        ),
                    )

                for label in widget.findChildren(setting_window.QLabel):
                    if label.objectName().startswith(
                        "autoTradeSettingRoutineTreePerformance"
                    ) and label.objectName().endswith(
                        ("LeftValue", "RightValue")
                    ):
                        self.assertNotEqual("-", label.text())

            screenshot_path = os.environ.get(
                "AUTO_TRADE_DIRECTIONAL_PROFIT_SCREENSHOT_PATH",
                "",
            ).strip()
            if screenshot_path:
                self.assertTrue(window.grab().save(screenshot_path))
        finally:
            window.close()
            window.deleteLater()
            self._app.processEvents()

    def test_parent_arrow_click_only_collapses_definition_rows(self) -> None:
        instances = [self._instance("inst-a", "A 인스턴스")]
        window = self._window_harness()
        window._routine_instance_operation_counts = lambda: {
            "inst-a": {"registered": 1, "running": 0, "stopped": 1, "error": 0}
        }

        with patch.object(setting_window, "load_routine_definitions", return_value=[self._definition()]), \
                patch.object(setting_window, "load_persisted_routine_instances", return_value=instances), \
                patch.object(setting_window, "read_base_stocks", return_value=[]):
            window.load_routine_table()
            window._set_routine_tree_display_level("routine")

            parent_icon = window.routine_table.cellWidget(0, 0).findChild(
                setting_window.QLabel,
                "autoTradeSettingRoutineTreeIcon",
            )
            self.assertEqual("▼", parent_icon.text())
            self.assertEqual(setting_window.Qt.PointingHandCursor, parent_icon.cursor().shape())
            self.assertFalse(parent_icon.testAttribute(setting_window.Qt.WA_TransparentForMouseEvents))

            window.on_routine_table_item_clicked(window.routine_table.item(0, 0))
            self.assertEqual(set(), window._collapsed_auto_trade_definition_ids)
            window.on_routine_table_item_double_clicked(window.routine_table.item(0, 0))
            self.assertEqual(set(), window._collapsed_auto_trade_definition_ids)
            window.load_routine_table = lambda: self.fail("definition collapse must not rebuild the routine table")
            window._toggle_routine_definition_collapsed("indicator_follow")

        self.assertEqual({"indicator_follow"}, window._collapsed_auto_trade_definition_ids)
        self.assertEqual(
            ["definition", "instance"],
            [
                window.routine_table.item(row, 0).data(setting_window.Qt.UserRole)["row_kind"]
                for row in range(window.routine_table.rowCount())
            ],
        )
        self.assertFalse(window.routine_table.isRowHidden(0))
        self.assertTrue(window.routine_table.isRowHidden(1))

    def test_parent_arrow_stays_locked_for_empty_definition(self) -> None:
        review_definition = RoutineDefinitionRecord(
            definition_id="review",
            display_name="등록확인루틴",
            package_dir=Path("routines") / "review",
            schema_version="1.0",
            version="1.0",
            routine_type="auto_trade",
            entry_file="routine.py",
            module_name="review_routine",
            settings_ui="",
            default_rules_file="rules.json",
            package_enabled=True,
            source_name="routine.json",
        )
        window = self._window_harness()
        window._routine_instance_operation_counts = lambda: {}

        with patch.object(setting_window, "load_routine_definitions", return_value=[self._definition(), review_definition]), \
                patch.object(setting_window, "load_persisted_routine_instances", return_value=[]), \
                patch.object(setting_window, "read_base_stocks", return_value=[]):
            window.load_routine_table()
            self.assertEqual(["definition", "definition"], [
                window.routine_table.item(row, 0).data(setting_window.Qt.UserRole)["row_kind"]
                for row in range(window.routine_table.rowCount())
            ])

            for _index in range(20):
                review_row = next(
                    row
                    for row in range(window.routine_table.rowCount())
                    if window.routine_table.item(row, 0).data(setting_window.Qt.UserRole)["definition_id"] == "review"
                )
                icon = window.routine_table.cellWidget(review_row, 0).findChild(
                    setting_window.QLabel,
                    "autoTradeSettingRoutineTreeIcon",
                )
                metadata = window.routine_table.item(review_row, 0).data(setting_window.Qt.UserRole)
                self.assertEqual("▶", icon.text())
                self.assertFalse(bool(metadata["has_toggle_children"]))
                self.assertFalse(bool(icon.property("autoTradeSettingRoutineTreeToggleEnabled")))
                window._apply_routine_tree_collapse_visibility = lambda: self.fail("locked parent arrow must not apply collapse")
                window._toggle_routine_definition_collapsed("review")

            self.assertEqual(set(), window._collapsed_auto_trade_definition_ids)
            self.assertFalse(window.routine_table.isRowHidden(review_row))

    def test_instance_arrow_stays_locked_when_no_stock_rows_exist(self) -> None:
        instances = [self._instance("inst-empty", "빈 인스턴스")]
        window = self._window_harness()
        window._routine_instance_operation_counts = lambda: {
            "inst-empty": {"registered": 0, "running": 0, "stopped": 0, "error": 0}
        }

        with patch.object(setting_window, "load_routine_definitions", return_value=[self._definition()]), \
                patch.object(setting_window, "load_persisted_routine_instances", return_value=instances), \
                patch.object(setting_window, "read_base_stocks", return_value=[]):
            window.load_routine_table()
            window._set_routine_tree_display_level("routine")

            self.assertEqual(
                ["definition", "instance"],
                [
                    window.routine_table.item(row, 0).data(setting_window.Qt.UserRole)["row_kind"]
                    for row in range(window.routine_table.rowCount())
                ],
            )
            instance_icon = window.routine_table.cellWidget(1, 0).findChild(
                setting_window.QLabel,
                "autoTradeSettingRoutineTreeIcon",
            )
            instance_metadata = window.routine_table.item(1, 0).data(setting_window.Qt.UserRole)
            self.assertEqual("▶", instance_icon.text())
            self.assertFalse(bool(instance_metadata["has_toggle_children"]))
            self.assertFalse(bool(instance_icon.property("autoTradeSettingRoutineTreeToggleEnabled")))
            self.assertFalse(window.routine_table.isRowHidden(1))

            original_apply_visibility = window._apply_routine_tree_collapse_visibility
            window._apply_routine_tree_collapse_visibility = lambda: self.fail("locked instance arrow must not apply collapse")
            window._toggle_routine_instance_collapsed("inst-empty")
            window._apply_routine_tree_collapse_visibility = original_apply_visibility

        self.assertEqual(set(), window._collapsed_auto_trade_instance_ids)
        self.assertFalse(window.routine_table.isRowHidden(1))

        with patch.object(setting_window, "load_routine_definitions", return_value=[self._definition()]), \
                patch.object(setting_window, "load_persisted_routine_instances", return_value=instances), \
                patch.object(setting_window, "read_base_stocks", return_value=[]):
            window.load_routine_table()
            window._toggle_routine_definition_collapsed("indicator_follow")
            self.assertTrue(window.routine_table.isRowHidden(1))
            window._toggle_routine_definition_collapsed("indicator_follow")
            self.assertFalse(window.routine_table.isRowHidden(1))

    def test_routine_tree_hides_table_header_and_grid(self) -> None:
        window = self._window_harness()
        window._setup_routine_table()

        self.assertEqual(1, window.routine_table.columnCount())
        self.assertTrue(window.routine_table.horizontalHeader().isHidden())
        self.assertTrue(window.routine_table.verticalHeader().isHidden())
        self.assertFalse(window.routine_table.showGrid())
        self.assertEqual(setting_window.Qt.ScrollBarAlwaysOn, window.routine_table.verticalScrollBarPolicy())
        self.assertIn("selection-background-color: #dbeafe", window.routine_table.styleSheet())
        self.assertIn("selection-color: #111827", window.routine_table.styleSheet())

    def test_stock_table_uses_light_selection_without_geometry_changes(self) -> None:
        window = self._window_harness()
        window._setup_stock_table()

        style = window.stock_table.styleSheet()
        self.assertIn("selection-background-color: #dbeafe", style)
        self.assertIn("selection-color: #111827", style)
        self.assertIn("QTableWidget::item:selected:!active", style)
        self.assertIn("gridline-color: #D1D5DB", style)
        self.assertIn("outline: 0", style)
        self.assertIn("QHeaderView::section:vertical", style)
        self.assertIn("QTableCornerButton::section", style)
        self.assertTrue(window.stock_table.showGrid())
        self.assertEqual(setting_window.Qt.SolidLine, window.stock_table.gridStyle())
        self.assertTrue(
            window.stock_table.horizontalHeader().property(
                setting_window.PLAIN_HEADER_USE_TABLE_BODY_BACKGROUND_PROPERTY
            )
        )
        self.assertEqual(
            "#D1D5DB",
            window.stock_table.horizontalHeader().property(
                setting_window.PLAIN_HEADER_GRID_COLOR_PROPERTY
            ),
        )
        body_color = window.stock_table.viewport().palette().color(setting_window.QPalette.Base)
        vertical_header = window.stock_table.verticalHeader()
        self.assertEqual(setting_window.Qt.AlignCenter, vertical_header.defaultAlignment())
        for role in (
            setting_window.QPalette.Button,
            setting_window.QPalette.Window,
            setting_window.QPalette.Base,
        ):
            self.assertEqual(body_color, vertical_header.palette().color(role))
            self.assertEqual(body_color, vertical_header.viewport().palette().color(role))
        self.assertEqual(
            setting_window.QAbstractItemView.ExtendedSelection,
            window.stock_table.selectionMode(),
        )
        self.assertEqual(
            [
                setting_window.AUTO_TRADE_SETTING_STOCK_TABLE_COLUMN_WIDTHS[column]
                for column in range(window.stock_table.columnCount())
            ],
            [
                window.stock_table.columnWidth(column)
                for column in range(window.stock_table.columnCount())
            ],
        )

    def test_window_uses_standard_minimize_maximize_close_title_buttons(self) -> None:
        window = setting_window.AutoTradeSettingWindow()
        try:
            flags = window.windowFlags()
            self.assertFalse(bool(flags & setting_window.Qt.WindowContextHelpButtonHint))
            self.assertTrue(bool(flags & setting_window.Qt.WindowMinimizeButtonHint))
            self.assertTrue(bool(flags & setting_window.Qt.WindowMaximizeButtonHint))
            self.assertTrue(bool(flags & setting_window.Qt.WindowCloseButtonHint))
            expected_minimum_width = (
                window.routine_box.minimumWidth()
                + window._right_workspace_initial_width()
                + window.strategy_workspace_splitter.handleWidth()
                + window.layout().contentsMargins().left()
                + window.layout().contentsMargins().right()
            )
            self.assertEqual(expected_minimum_width, window.minimumWidth())
            self.assertEqual(650, window.minimumHeight())
            self.assertGreater(window.maximumWidth(), window.minimumWidth())
            self.assertGreater(window.maximumHeight(), window.minimumHeight())
        finally:
            window.close()

    def test_parent_summary_counts_are_removed_from_tree_tooltip(self) -> None:
        window = self._window_harness()
        row_data = {
            "row_kind": "definition",
            "definition_id": "indicator_follow",
            "instance_id": "",
            "display_name": "지표추종매매",
            "tree_icon": "▼",
            "registered": 12,
            "running": 12,
            "stopped": 8,
            "error": 0,
        }

        widget = window._routine_tree_row_widget(row_data, "")
        window._set_routine_tree_parent_summary_visible(widget, True)
        widget.show()
        self._app.processEvents()
        for object_name in (
            "autoTradeSettingRoutineTreeRegistered",
            "autoTradeSettingRoutineTreeRunning",
            "autoTradeSettingRoutineTreeStopped",
            "autoTradeSettingRoutineTreeError",
        ):
            self.assertIsNone(widget.findChild(setting_window.QLabel, object_name))
        self.assertIsNone(widget.findChild(setting_window.QWidget, "autoTradeSettingRoutineTreeStatusGroup"))
        self.assertEqual("", widget.toolTip())

    def test_parent_title_uses_fixed_six_character_slot_and_fixed_columns(self) -> None:
        window = self._window_harness()
        samples = [
            ("단기", "단기"),
            ("단기매매", "단기매매"),
            ("지표추종매매", "지표추종매매"),
            ("지표추종매매A", "지표추종매매..."),
            ("지표추종매매BC", "지표추종매매..."),
            ("아주긴자동매매루틴", "아주긴자동매..."),
            ("123456", "123456"),
            ("1234567", "123456..."),
            ("12345678", "123456..."),
        ]
        badge_x_values = set()
        title_widths = set()

        for display_name, expected_title in samples:
            row_data = {
                "row_kind": "definition",
                "definition_id": "indicator_follow",
                "instance_id": "",
                "display_name": display_name,
                "tree_icon": "▼",
                "instance_count": 3,
                "registered": 12,
                "running": 4,
                "stopped": 8,
                "error": 0,
            }
            widget = window._routine_tree_row_widget(row_data, "")
            window._set_routine_tree_parent_summary_visible(widget, True)
            widget.show()
            self._app.processEvents()

            title = widget.findChild(setting_window.QLabel, "autoTradeSettingRoutineTreeTitle")
            badge = widget.findChild(setting_window.QLabel, "autoTradeSettingRoutineTreeInstanceCount")

            self.assertEqual(expected_title, title.text())
            self.assertEqual(
                setting_window.routine_tree_title_width(title.fontMetrics()),
                title.width(),
            )
            required_width = max(
                max(title.fontMetrics().horizontalAdvance(sample), title.fontMetrics().boundingRect(sample).width())
                for sample in ("가" * 6, "가" * 6 + "...", "123456", "123456...")
            )
            self.assertGreaterEqual(title.contentsRect().width(), required_width)
            self.assertEqual(title.width(), title.minimumWidth())
            self.assertEqual(title.width(), title.maximumWidth())
            self.assertEqual(setting_window.QSizePolicy.Fixed, title.sizePolicy().horizontalPolicy())
            self.assertEqual(setting_window.Qt.AlignCenter | setting_window.Qt.AlignVCenter, title.alignment())
            text_width = title.fontMetrics().horizontalAdvance(title.text())
            if len(display_name) <= 6:
                left_padding = (title.width() - text_width) // 2
                right_padding = title.width() - text_width - left_padding
                self.assertLessEqual(abs(left_padding - right_padding), 1)
            title_widths.add(title.width())
            badge_x = badge.mapTo(widget, badge.rect().topLeft()).x()
            badge_x_values.add(badge_x)
            self.assertEqual("", widget.toolTip())
            for object_name in (
                "autoTradeSettingRoutineTreeRegistered",
                "autoTradeSettingRoutineTreeRunning",
                "autoTradeSettingRoutineTreeStopped",
                "autoTradeSettingRoutineTreeError",
            ):
                self.assertIsNone(widget.findChild(setting_window.QLabel, object_name))

        self.assertEqual(1, len(title_widths))
        self.assertEqual(1, len(badge_x_values))

    def test_child_title_uses_fixed_name_slot_without_status_tooltip(self) -> None:
        window = self._window_harness()
        samples = [
            ("두자", "두자"),
            ("동전주", "동전주"),
            ("네글자명", "네글자명"),
            ("지표추종매매", "지표추종매매"),
            ("지표추종매매B", "지표추종매매B"),
            ("지표추종매매BC", "지표추종매매..."),
            ("지표추종매매01", "지표추종매매..."),
            ("아주긴자동매매루틴", "아주긴자동매..."),
            ("123456", "123456"),
            ("1234567", "1234567"),
            ("12345678", "123456..."),
            ("AB12가나다", "AB12가나다"),
            ("AB12가나다라", "AB12가나..."),
        ]
        title_x_values = set()
        title_widths = set()

        for display_name, expected_title in samples:
            row_data = {
                "row_kind": "instance",
                "definition_id": "indicator_follow",
                "instance_id": "inst-a",
                "display_name": display_name,
                "tree_icon": "●",
                "instance_count": 0,
                "registered": 4,
                "running": 4,
                "stopped": 0,
                "error": 0,
            }
            widget = window._routine_tree_row_widget(row_data, "")
            widget.resize(widget.sizeHint().width(), widget.sizeHint().height())
            widget.show()
            self._app.processEvents()

            title = widget.findChild(setting_window.QLabel, "autoTradeSettingRoutineTreeTitle")

            self.assertEqual(expected_title, title.text())
            self.assertEqual(
                setting_window.routine_tree_title_width(title.fontMetrics()),
                title.width(),
            )
            required_width = max(
                max(title.fontMetrics().horizontalAdvance(sample), title.fontMetrics().boundingRect(sample).width())
                for sample in ("가" * 6, "가" * 6 + "...", "123456", "123456...")
            )
            self.assertGreaterEqual(title.contentsRect().width(), required_width)
            self.assertEqual(title.width(), title.minimumWidth())
            self.assertEqual(title.width(), title.maximumWidth())
            self.assertEqual(setting_window.QSizePolicy.Fixed, title.sizePolicy().horizontalPolicy())
            self.assertEqual(setting_window.Qt.AlignCenter | setting_window.Qt.AlignVCenter, title.alignment())
            text_width = title.fontMetrics().horizontalAdvance(title.text())
            self.assertLessEqual(text_width, title.contentsRect().width())
            if len(display_name) <= 7:
                left_padding = (title.width() - text_width) // 2
                right_padding = title.width() - text_width - left_padding
                self.assertLessEqual(abs(left_padding - right_padding), 1)

            title_x_values.add(title.mapTo(widget, title.rect().topLeft()).x())
            title_widths.add(title.width())
            self.assertEqual("", widget.toolTip())
            for object_name in (
                "autoTradeSettingRoutineTreeRegistered",
                "autoTradeSettingRoutineTreeRunning",
                "autoTradeSettingRoutineTreeStopped",
                "autoTradeSettingRoutineTreeError",
            ):
                self.assertIsNone(widget.findChild(setting_window.QLabel, object_name))

        self.assertEqual(1, len(title_x_values))
        self.assertEqual(1, len(title_widths))

    def test_routine_tree_performance_columns_keep_fixed_x_axis_by_row_kind(self) -> None:
        window = self._window_harness()
        rows = [
            {
                "row_kind": "definition",
                "definition_id": "indicator_follow",
                "instance_id": "",
                "display_name": "지표추종매매",
                "tree_icon": "▼",
                "instance_count": 3,
                "performance_period_text": "기간(0123)",
                "performance_profit_text": "수익(12,345,678 / 18.42%)",
                "performance_average_text": "평균(102,345 / 0.83%)",
                "performance_efficiency_text": "효율(1.86)",
            },
            {
                "row_kind": "definition",
                "definition_id": "review",
                "instance_id": "",
                "display_name": "등록확인루틴",
                "tree_icon": "▶",
                "instance_count": 0,
                "performance_period_text": "기간(0000)",
                "performance_profit_text": "수익(0 / 0.00%)",
                "performance_average_text": "평균(0 / 0.00%)",
                "performance_efficiency_text": "효율(0.00)",
            },
            {
                "row_kind": "instance",
                "definition_id": "indicator_follow",
                "instance_id": "inst-a",
                "display_name": "지표추종매매B",
                "tree_icon": "●",
                "performance_period_text": "기간(0045)",
                "performance_profit_text": "수익(1,200 / 1.20%)",
                "performance_average_text": "평균(27 / 0.03%)",
                "performance_efficiency_text": "효율(1.20)",
            },
            {
                "row_kind": "instance",
                "definition_id": "indicator_follow",
                "instance_id": "inst-b",
                "display_name": "매우긴인스턴스이름",
                "tree_icon": "●",
                "performance_period_text": "기간(9999)",
                "performance_profit_text": "수익(99,999,999 / 000.0%)",
                "performance_average_text": "평균(99,999,999 / 00.0%)",
                "performance_efficiency_text": "효율(000.0)",
            },
            {
                "row_kind": "instance",
                "definition_id": "indicator_follow",
                "instance_id": "inst-c",
                "display_name": "단기매매",
                "tree_icon": "●",
                "performance_period_text": "기간(0001)",
                "performance_profit_text": "수익(125,000 / 8.40%)",
                "performance_average_text": "평균(125,000 / 8.40%)",
                "performance_efficiency_text": "효율(2.45)",
            },
            {
                "row_kind": "stock",
                "definition_id": "indicator_follow",
                "instance_id": "inst-c",
                "display_name": "삼성전자",
                "tree_icon": "",
                "performance_period_text": "기간(0000)",
                "performance_profit_text": "수익(0 / 0.0%)",
                "performance_average_text": "평균(0 / 0.0%)",
                "performance_efficiency_text": "효율(0.0)",
            },
        ]
        x_values = {
            "definition": {"period": set(), "profit": set(), "average": set(), "efficiency": set()},
            "instance": {"period": set(), "profit": set(), "average": set(), "efficiency": set()},
            "stock": {"period": set(), "profit": set(), "average": set(), "efficiency": set()},
        }
        widths = {"period": set(), "profit": set(), "average": set(), "efficiency": set()}

        for row_data in rows:
            widget = window._routine_tree_row_widget(row_data, "")
            if row_data["row_kind"] == "definition":
                window._set_routine_tree_parent_summary_visible(widget, True)
            widget.resize(widget.sizeHint().width(), widget.sizeHint().height())
            widget.show()
            self._app.processEvents()

            title = widget.findChild(setting_window.QLabel, "autoTradeSettingRoutineTreeTitle")
            period = widget.findChild(setting_window.QWidget, "autoTradeSettingRoutineTreePerformancePeriod")
            period_spacer = widget.findChild(setting_window.QWidget, "autoTradeSettingRoutineTreePerformancePeriodSpacer")
            identity_compensation = widget.findChild(
                setting_window.QWidget,
                "autoTradeSettingRoutineTreeIdentityXCompensation",
            )
            profit = widget.findChild(setting_window.QWidget, "autoTradeSettingRoutineTreePerformanceProfit")
            average = widget.findChild(setting_window.QWidget, "autoTradeSettingRoutineTreePerformanceAverage")
            efficiency = widget.findChild(setting_window.QWidget, "autoTradeSettingRoutineTreePerformanceEfficiency")

            self.assertIsNotNone(title)
            if row_data["row_kind"] == "definition":
                self.assertIsNotNone(period)
                self.assertIsNone(period_spacer)
                self.assertIsNone(identity_compensation)
            else:
                self.assertIsNotNone(period)
                self.assertIsNone(period_spacer)
                self.assertIsNotNone(identity_compensation)
            self.assertIsNotNone(profit)
            self.assertIsNotNone(average)
            self.assertIsNotNone(efficiency)
            self.assertGreater(
                period.mapTo(widget, period.rect().topLeft()).x(),
                title.mapTo(widget, title.rect().topLeft()).x() + title.width(),
            )
            labels = {
                "period": period,
                "profit": profit,
                "average": average,
                "efficiency": efficiency,
            }
            for key, label in labels.items():
                x_values[str(row_data["row_kind"])][key].add(label.mapTo(widget, label.rect().topLeft()).x())
                widths[key].add(label.width())
                left_value = label.findChild(
                    setting_window.QLabel,
                    f"autoTradeSettingRoutineTreePerformance{key.title()}LeftValue",
                )
                if key == "period":
                    left_value = label.findChild(
                        setting_window.QLabel,
                        "autoTradeSettingRoutineTreePerformancePeriodLeftValue",
                    )
                if key == "profit":
                    left_value = label.findChild(
                        setting_window.QLabel,
                        "autoTradeSettingRoutineTreePerformanceProfitLeftValue",
                    )
                if key == "average":
                    left_value = label.findChild(
                        setting_window.QLabel,
                        "autoTradeSettingRoutineTreePerformanceAverageLeftValue",
                    )
                if key == "efficiency":
                    left_value = label.findChild(
                        setting_window.QLabel,
                        "autoTradeSettingRoutineTreePerformanceEfficiencyLeftValue",
                    )
                self.assertIsNotNone(left_value)
                self.assertEqual(setting_window.Qt.AlignRight | setting_window.Qt.AlignVCenter, left_value.alignment())
                self.assertGreaterEqual(left_value.width(), left_value.fontMetrics().horizontalAdvance(left_value.text()))
                if key in {"profit", "average"}:
                    right_value = label.findChild(
                        setting_window.QLabel,
                        f"autoTradeSettingRoutineTreePerformance{key.title()}RightValue",
                    )
                    self.assertIsNotNone(right_value)
                    self.assertEqual(setting_window.Qt.AlignRight | setting_window.Qt.AlignVCenter, right_value.alignment())
                    self.assertGreaterEqual(right_value.width(), right_value.fontMetrics().horizontalAdvance(right_value.text()))
            self.assertEqual("", widget.toolTip())

        for row_kind, keys in (
            ("definition", ("profit", "average", "efficiency")),
            ("instance", ("period", "profit", "average", "efficiency")),
            ("stock", ("period", "profit", "average", "efficiency")),
        ):
            for key in keys:
                self.assertEqual(1, len(x_values[row_kind][key]))
                self.assertEqual(1, len(widths[key]))
        self.assertEqual(x_values["definition"]["profit"], x_values["instance"]["profit"])
        self.assertEqual(x_values["instance"]["profit"], x_values["stock"]["profit"])

    def test_routine_tree_numeric_slots_keep_fixed_right_edge_for_value_lengths(self) -> None:
        window = self._window_harness()
        widget = window._routine_tree_row_widget(
            {
                "row_kind": "instance",
                "definition_id": "indicator_follow",
                "instance_id": "inst-a",
                "display_name": "인스턴스",
                "tree_icon": "▼",
            },
            "",
        )
        widget.resize(widget.sizeHint().width(), widget.sizeHint().height())
        widget.show()
        widget.layout().activate()
        self._app.processEvents()

        values_by_object_name = {
            "autoTradeSettingRoutineTreePerformancePeriodLeftValue": (
                "0",
                "5",
                "25",
                "999",
                "1234",
            ),
            "autoTradeSettingRoutineTreePerformanceProfitLeftValue": (
                "0",
                "25",
                "1,234",
                "12,345",
                "123,456",
                "-2,500",
                "+12,345",
            ),
            "autoTradeSettingRoutineTreePerformanceProfitRightValue": (
                "0.0%",
                "+3.5%",
                "-4.2%",
                "123.4%",
            ),
            "autoTradeSettingRoutineTreePerformanceAverageLeftValue": (
                "0",
                "25",
                "1,234",
                "12,345",
                "123,456",
                "-2,500",
                "+12,345",
            ),
            "autoTradeSettingRoutineTreePerformanceAverageRightValue": (
                "0.0%",
                "+3.5%",
                "-4.2%",
                "12.3%",
            ),
            "autoTradeSettingRoutineTreePerformanceEfficiencyLeftValue": (
                "0.0",
                "5.0",
                "25.0",
                "999.0",
            ),
        }

        for object_name, values in values_by_object_name.items():
            label = widget.findChild(setting_window.QLabel, object_name)
            self.assertIsNotNone(label)
            initial_geometry = label.geometry()
            initial_width = label.width()
            right_edges = set()
            for value in values:
                label.setText(value)
                widget.layout().activate()
                self._app.processEvents()
                self.assertEqual(initial_geometry, label.geometry())
                self.assertEqual(initial_width, label.width())
                self.assertTrue(label.alignment() & setting_window.Qt.AlignRight)
                self.assertLessEqual(
                    label.fontMetrics().horizontalAdvance(value),
                    label.contentsRect().width(),
                )
                right_edges.add(
                    label.mapTo(widget, label.contentsRect().topRight()).x()
                )
            self.assertEqual(1, len(right_edges))

    def test_routine_tree_title_text_contract(self) -> None:
        samples = [
            ("가", "가"),
            ("동전주", "동전주"),
            ("단기매매", "단기매매"),
            ("지표추종매매", "지표추종매매"),
            ("지표추종매매B", "지표추종매매..."),
            ("지표추종매매BC", "지표추종매매..."),
            ("ABCDEFGHI", "ABCDEF..."),
            ("123456", "123456"),
            ("1234567", "123456..."),
            ("12345678", "123456..."),
        ]

        for display_name, expected in samples:
            with self.subTest(display_name=display_name):
                self.assertEqual(expected, setting_window.routine_tree_title_text(display_name))

    def test_collapsed_parent_shows_summary_without_hover(self) -> None:
        window = self._window_harness()
        row_data = {
            "row_kind": "definition",
            "definition_id": "indicator_follow",
            "instance_id": "",
            "display_name": "지표추종매매",
            "tree_icon": "▶",
            "instance_count": 3,
            "registered": 12,
            "running": 4,
            "stopped": 8,
            "error": 0,
        }

        widget = window._routine_tree_row_widget(row_data, "지표추종매매")

        count_badge = widget.findChild(setting_window.QLabel, "autoTradeSettingRoutineTreeInstanceCount")
        self.assertEqual("루틴3", count_badge.text())
        self.assertFalse(count_badge.isHidden())
        self.assertEqual("", widget.toolTip())
        for object_name in (
            "autoTradeSettingRoutineTreeRegistered",
            "autoTradeSettingRoutineTreeRunning",
            "autoTradeSettingRoutineTreeStopped",
            "autoTradeSettingRoutineTreeError",
        ):
            self.assertIsNone(widget.findChild(setting_window.QLabel, object_name))

    def test_parent_selection_is_view_scope_and_not_routine_dir(self) -> None:
        instances = [self._instance("inst-a", "A 인스턴스"), self._instance("inst-b", "B 인스턴스")]
        window = self._window_harness()
        window._routine_instance_operation_counts = lambda: {}
        with patch.object(setting_window, "load_routine_definitions", return_value=[self._definition()]), \
                patch.object(setting_window, "load_persisted_routine_instances", return_value=instances):
            window.load_routine_table()
            window.routine_table.selectRow(0)

            self.assertEqual("indicator_follow", window.current_selected_definition_id())
            self.assertEqual("", window.current_selected_instance_id())
            self.assertIsNone(window.current_selected_routine_dir())
            self.assertEqual(("inst-a", "inst-b"), window.current_selected_target_instance_ids())

    def test_instance_selection_returns_single_instance_scope(self) -> None:
        instances = [self._instance("inst-a", "A 인스턴스")]
        window = self._window_harness()
        window._routine_instance_operation_counts = lambda: {}
        with patch.object(setting_window, "load_routine_definitions", return_value=[self._definition()]), \
                patch.object(setting_window, "load_persisted_routine_instances", return_value=instances), \
                patch.object(Path, "exists", return_value=True):
            window.load_routine_table()
            window.routine_table.selectRow(1)

            self.assertEqual("inst-a", window.current_selected_instance_id())
            self.assertEqual(("inst-a",), window.current_selected_target_instance_ids())
            self.assertEqual(Path("routines") / "indicator_follow", window.current_selected_routine_dir())

    def test_stock_dirs_follow_selected_instance_ids(self) -> None:
        class Window:
            def current_selected_target_instance_ids(self):
                return ("inst-a", "inst-b")

        stocks = [
            {"stock_path": "stocks/005930_A", "assigned_routine_instance_id": "inst-a"},
            {"stock_path": "stocks/000660_B", "assigned_routine_instance_id": "other"},
            {"stock_path": "stocks/035420_C", "assigned_routine_instance_id": "inst-b"},
        ]
        with patch.object(table_loader, "read_base_stocks", return_value=stocks):
            dirs = table_loader._selected_instance_stock_dirs(Window())

        self.assertEqual(
            [table_loader.PROJECT_ROOT / "stocks" / "005930_A", table_loader.PROJECT_ROOT / "stocks" / "035420_C"],
            dirs,
        )

    def test_plain_header_body_background_is_stock_table_opt_in_only(self) -> None:
        normal_table = QTableWidget(0, 1)
        setting_window.apply_plain_table_header(normal_table)
        normal_header = normal_table.horizontalHeader()
        self.assertFalse(
            bool(
                normal_header.property(
                    setting_window.PLAIN_HEADER_USE_TABLE_BODY_BACKGROUND_PROPERTY
                )
            )
        )
        self.assertEqual(normal_header.palette().button(), normal_header._section_background())
        self.assertEqual(normal_header.palette().mid().color(), normal_header._section_grid_color())

        stock_table = QTableWidget(0, 1)
        setting_window.apply_plain_table_header(stock_table)
        stock_header = stock_table.horizontalHeader()
        stock_header.setProperty(
            setting_window.PLAIN_HEADER_USE_TABLE_BODY_BACKGROUND_PROPERTY,
            True,
        )
        stock_header.setProperty(setting_window.PLAIN_HEADER_GRID_COLOR_PROPERTY, "#D1D5DB")
        body_color = stock_table.viewport().palette().color(setting_window.QPalette.Base)
        self.assertEqual(body_color, stock_header._section_background())
        self.assertEqual("#D1D5DB", stock_header._section_grid_color().name().upper())


    def test_stock_selection_uses_parent_instance_status_scope(self) -> None:
        instances = [
            self._instance("inst-a", "A 인스턴스"),
            self._instance("inst-b", "B 인스턴스"),
        ]
        stocks = [
            {
                "stock_path": "stocks/005930_A",
                "assigned_routine_instance_id": "inst-a",
                "code": "005930",
                "name": "삼성전자",
            },
            {
                "stock_path": "stocks/005380_B",
                "assigned_routine_instance_id": "inst-a",
                "code": "005380",
                "name": "현대차",
            },
            {
                "stock_path": "stocks/035420_C",
                "assigned_routine_instance_id": "inst-b",
                "code": "035420",
                "name": "NAVER",
            },
        ]
        counts = {
            "inst-a": {
                "registered": 2,
                "running": 1,
                "stopped": 1,
                "error": 0,
                "normal": 1,
                "excluded": 1,
                "review": 0,
            },
            "inst-b": {
                "registered": 1,
                "running": 0,
                "stopped": 0,
                "error": 1,
                "normal": 0,
                "excluded": 0,
                "review": 1,
            },
        }
        window = self._window_harness()
        window._setup_selected_routine_status_bar()
        window._routine_instance_operation_counts = lambda: counts
        with patch.object(setting_window, "load_routine_definitions", return_value=[self._definition()]), \
                patch.object(setting_window, "load_persisted_routine_instances", return_value=instances), \
                patch.object(setting_window, "read_base_stocks", return_value=stocks):
            window.load_routine_table()

        def status_texts() -> tuple[str, str, str, str]:
            return (
                window.selected_routine_status_buttons["all"].text(),
                window.selected_routine_status_buttons["running"].text(),
                window.selected_routine_status_buttons["excluded"].text(),
                window.selected_routine_status_buttons["error"].text(),
            )

        window.routine_table.selectRow(1)
        window.update_selected_routine_status_bar()
        instance_texts = status_texts()
        instance_target_ids = window.current_selected_target_instance_ids()

        window.routine_table.selectRow(2)
        window.update_selected_routine_status_bar()
        first_stock_texts = status_texts()
        first_stock_target_ids = window.current_selected_target_instance_ids()

        window.routine_table.selectRow(3)
        window.update_selected_routine_status_bar()
        second_stock_texts = status_texts()
        second_stock_target_ids = window.current_selected_target_instance_ids()

        self.assertEqual(("inst-a",), instance_target_ids)
        self.assertEqual(instance_target_ids, first_stock_target_ids)
        self.assertEqual(instance_target_ids, second_stock_target_ids)
        self.assertEqual(
            ("종목(2)", "정지(1)", "제외(1)", "검토(0)"),
            instance_texts,
        )
        self.assertEqual(instance_texts, first_stock_texts)
        self.assertEqual(instance_texts, second_stock_texts)

        window.routine_table.selectRow(5)
        window.update_selected_routine_status_bar()
        self.assertEqual(("inst-b",), window.current_selected_target_instance_ids())
        self.assertEqual(
            ("종목(1)", "정지(0)", "제외(0)", "검토(1)"),
            status_texts(),
        )


    def test_performance_tree_keeps_review_required_current_stock_records(self) -> None:
        window = self._window_harness()
        stocks = [
            {
                "stock_path": "stocks/005930_A",
                "assigned_routine_instance_id": "inst-a",
                "code": "005930",
                "name": "삼성전자",
            },
        ]

        with patch.object(setting_window, "read_base_stocks", return_value=stocks), \
                patch.object(setting_window, "is_review_required_stock_dir", return_value=True):
            result = window._current_stocks_by_instance()

        self.assertEqual(["005930"], [stock["stock_code"] for stock in result["inst-a"]])

    def test_runtime_state_for_order_uses_selected_assignment_scope(self) -> None:
        window = self._window_harness()
        window.current_selected_target_instance_ids = lambda: ("inst-a",)
        window.current_selected_routine_row_metadata = lambda: {
            "row_kind": "definition",
            "definition_id": "indicator_follow",
        }
        stocks = [
            {"stock_path": "stocks/005930_A", "assigned_routine_instance_id": "other"},
            {"stock_path": "stocks/005930_B", "assigned_routine_instance_id": "inst-a"},
        ]

        def fake_read_json(path: Path):
            if str(path).endswith("config.json"):
                if "005930_B" in str(path):
                    return {"assigned_routine_instance_id": "inst-a", "real_trade_enabled": True}
                return {"assigned_routine_instance_id": "other"}
            return {"status": "RUNNING", "trade_enabled": True}

        with patch.object(setting_window, "read_base_stocks", return_value=stocks), \
                patch.object(setting_window, "read_json_dict", side_effect=fake_read_json):
            result = window.auto_trade_runtime_state_for_order({"code": "005930"})

        self.assertTrue(result["found"])
        self.assertIn("005930_B", result["stock_dir"])
        self.assertEqual("inst-a", result["config"]["assigned_routine_instance_id"])

    def test_runtime_state_for_order_blocks_parent_without_instance_scope(self) -> None:
        window = self._window_harness()
        window.current_selected_target_instance_ids = lambda: ()
        window.current_selected_routine_row_metadata = lambda: {
            "row_kind": "definition",
            "definition_id": "indicator_follow",
        }

        result = window.auto_trade_runtime_state_for_order({"code": "005930"})

        self.assertFalse(result["found"])
        self.assertEqual(
            [setting_window.ROUTINE_INSTANCE_REQUIRED_MESSAGE],
            result["issues"],
        )

    def test_selection_summary_area_is_removed_from_workspace(self) -> None:
        with patch.object(AutoTradeSettingWindow, "refresh_all", lambda _self: None), \
                patch.object(AutoTradeSettingWindow, "update_startup_recovery_controls", lambda _self: None), \
                patch.object(AutoTradeSettingWindow, "current_runtime_file_signature", lambda _self: tuple()):
            window = AutoTradeSettingWindow()
        self.addCleanup(window.close)

        group_titles = [group.title() for group in window.findChildren(setting_window.QGroupBox)]
        self.assertNotIn("Selection Summary", group_titles)
        self.assertFalse(hasattr(window, "selection_summary_box"))
        self.assertFalse(hasattr(window, "summary_routine_value"))

        workspace_layout = window.strategy_workspace_widget.layout()
        self.assertEqual(window.stock_box, workspace_layout.itemAt(0).widget())
        self.assertEqual(1, workspace_layout.count())
        self.assertEqual("자동매매운영실적", window.routine_box.title())
        self.assertEqual("등록종목상태", window.stock_box.title())
        self.assertEqual(window.routine_box.font(), window.stock_box.font())
        self.assertEqual(window.routine_box.alignment(), window.stock_box.alignment())
        self.assertEqual(setting_window.Qt.AlignLeft, window.routine_box.alignment())
        self.assertEqual(window.routine_box.isFlat(), window.stock_box.isFlat())
        self.assertFalse(window.routine_box.isFlat())
        window._position_routine_tree_display_level_badges()
        routine_margins = window.routine_box.layout().contentsMargins()
        stock_margins = window.stock_box.layout().contentsMargins()
        self.assertEqual(
            (
                routine_margins.left(),
                routine_margins.right(),
                routine_margins.bottom(),
            ),
            (
                stock_margins.left(),
                stock_margins.right(),
                stock_margins.bottom(),
            ),
        )
        self.assertGreaterEqual(
            routine_margins.top(),
            window._routine_tree_display_level_badges.height(),
        )
        self.assertGreater(routine_margins.top(), stock_margins.top())
        self.assertEqual(window.routine_box.styleSheet(), window.stock_box.styleSheet())
        self.assertEqual(
            setting_window.AUTO_TRADE_SETTING_WORKSPACE_GROUP_BOX_STYLE,
            window.routine_box.styleSheet(),
        )

        def _group_box_rects(group_box):
            option = QStyleOptionGroupBox()
            option.initFrom(group_box)
            option.text = group_box.title()
            option.lineWidth = 1
            option.subControls = (
                setting_window.QStyle.SC_GroupBoxFrame
                | setting_window.QStyle.SC_GroupBoxLabel
            )
            style = group_box.style()
            return (
                style.subControlRect(
                    setting_window.QStyle.CC_GroupBox,
                    option,
                    setting_window.QStyle.SC_GroupBoxLabel,
                    group_box,
                ),
                style.subControlRect(
                    setting_window.QStyle.CC_GroupBox,
                    option,
                    setting_window.QStyle.SC_GroupBoxFrame,
                    group_box,
                ),
                style.subControlRect(
                    setting_window.QStyle.CC_GroupBox,
                    option,
                    setting_window.QStyle.SC_GroupBoxContents,
                    group_box,
                ),
            )

        routine_label_rect, routine_frame_rect, routine_contents_rect = _group_box_rects(
            window.routine_box
        )
        stock_label_rect, stock_frame_rect, stock_contents_rect = _group_box_rects(
            window.stock_box
        )
        self.assertEqual(
            (routine_label_rect.x(), routine_label_rect.y(), routine_label_rect.height()),
            (stock_label_rect.x(), stock_label_rect.y(), stock_label_rect.height()),
        )
        self.assertEqual(
            (routine_frame_rect.y(), routine_frame_rect.height()),
            (stock_frame_rect.y(), stock_frame_rect.height()),
        )
        self.assertEqual(
            setting_window.AUTO_TRADE_SETTING_WORKSPACE_GROUP_BOX_FRAME_TOP,
            routine_frame_rect.y(),
        )
        self.assertEqual(
            (routine_contents_rect.y(), routine_contents_rect.height()),
            (stock_contents_rect.y(), stock_contents_rect.height()),
        )
        window._position_routine_tree_display_level_badges()
        badge_rect = window._routine_tree_display_level_badges.geometry()
        self.assertGreater(badge_rect.y(), routine_frame_rect.y())
        self.assertGreaterEqual(
            badge_rect.y(),
            routine_label_rect.bottom() + 1,
        )
        self.assertFalse(badge_rect.intersects(window.routine_table.geometry()))
        self.assertGreaterEqual(window.routine_table.geometry().y(), badge_rect.bottom() + 1)
        window.selected_routine_instance_count_badge.setText("루틴3")
        window.selected_routine_instance_count_badge.setVisible(True)
        window.show()
        self._app.processEvents()
        window._position_routine_tree_display_level_badges()
        self._app.processEvents()
        badge_center_y = window._routine_tree_display_level_badges.mapTo(
            window,
            window._routine_tree_display_level_badges.rect().center(),
        ).y()
        status_center_y = window.selected_routine_status_bar.mapTo(
            window,
            window.selected_routine_status_bar.rect().center(),
        ).y()
        self.assertLessEqual(abs(badge_center_y - status_center_y), 1)
        self.assertEqual(
            window._routine_tree_display_level_badges.height(),
            window.selected_routine_status_bar.height(),
        )
        aligned_controls = (
            *window._routine_tree_display_level_buttons.values(),
            *window._routine_tree_display_scope_buttons.values(),
            *window._routine_tree_display_criterion_buttons.values(),
            window.selected_routine_signal_label,
            window.selected_routine_name_button,
            window.selected_routine_group_count_badge,
            window.selected_routine_instance_count_badge,
            *window.selected_routine_status_buttons.values(),
            window.btn_all_stocks,
            window.all_stocks_command_separator,
            window.btn_early_close,
        )
        for control in aligned_controls:
            if control.isHidden():
                continue
            with self.subTest(control=control.objectName()):
                control_center_y = control.mapTo(window, control.rect().center()).y()
                self.assertLessEqual(abs(control_center_y - status_center_y), 1)
        self.assertEqual("전체", window.btn_all_stocks.text())
        self.assertNotIn(
            "전체종목",
            [button.text() for button in window.findChildren(setting_window.QPushButton)],
        )
        self.assertEqual("조기마감", window.btn_early_close.text())
        self.assertFalse(hasattr(window, "btn_stop"))
        self.assertEqual(
            [],
            [
                button
                for button in window.findChildren(setting_window.QPushButton)
                if button.objectName() == "autoTradeSettingStopButton"
            ],
        )
        self.assertEqual(
            (64, setting_window.AUTO_TRADE_SETTING_TOP_CONTROL_ROW_HEIGHT),
            (window.btn_all_stocks.width(), window.btn_all_stocks.height()),
        )
        self.assertEqual(
            window._routine_tree_display_scope_buttons["all"].font(),
            window.btn_all_stocks.font(),
        )
        expected_inactive_badge_style = setting_window.auto_trade_setting_badge_stylesheet(
            "QPushButton",
            text_color=setting_window.AUTO_TRADE_SETTING_BADGE_INACTIVE_COLOR,
            border_color=setting_window.AUTO_TRADE_SETTING_BADGE_INACTIVE_COLOR,
        )
        expected_active_badge_style = setting_window.auto_trade_setting_badge_stylesheet(
            "QPushButton",
            text_color=setting_window.AUTO_TRADE_SETTING_BADGE_ACTIVE_COLOR,
            border_color=setting_window.AUTO_TRADE_SETTING_BADGE_ACTIVE_COLOR,
        )
        self.assertEqual(
            expected_active_badge_style,
            window.btn_all_stocks.styleSheet(),
        )
        self.assertTrue(
            window._routine_tree_display_scope_buttons["all"].styleSheet().startswith(
                expected_active_badge_style
            )
        )
        self.assertEqual("|", window.all_stocks_command_separator.text())
        self.assertEqual(
            (12, setting_window.AUTO_TRADE_SETTING_TOP_CONTROL_ROW_HEIGHT),
            (
                window.all_stocks_command_separator.width(),
                window.all_stocks_command_separator.height(),
            ),
        )
        self.assertEqual(
            setting_window.Qt.NoFocus,
            window.all_stocks_command_separator.focusPolicy(),
        )
        self.assertTrue(
            window.all_stocks_command_separator.testAttribute(
                setting_window.Qt.WA_TransparentForMouseEvents
            )
        )
        all_stocks_x = window.btn_all_stocks.mapTo(
            window,
            window.btn_all_stocks.rect().topLeft(),
        ).x()
        separator_x = window.all_stocks_command_separator.mapTo(
            window,
            window.all_stocks_command_separator.rect().topLeft(),
        ).x()
        early_close_x = window.btn_early_close.mapTo(
            window,
            window.btn_early_close.rect().topLeft(),
        ).x()
        self.assertLess(
            all_stocks_x,
            separator_x,
        )
        self.assertLess(
            separator_x,
            early_close_x,
        )
        self.assertGreaterEqual(window.btn_early_close.height(), 28)
        self.assertGreater(
            window.btn_early_close.height(),
            window.btn_early_close.fontMetrics().height(),
        )

        window.routine_table.setRowCount(1)
        first_item = setting_window.QTableWidgetItem("첫 번째 루틴")
        window.routine_table.setItem(0, 0, first_item)
        self._app.processEvents()
        first_row_rect = window.routine_table.visualItemRect(first_item)
        first_row_y = window.routine_table.viewport().mapTo(
            window,
            first_row_rect.topLeft(),
        ).y()
        stock_header = window.stock_table.horizontalHeader()
        stock_header_y = stock_header.mapTo(
            window,
            stock_header.rect().topLeft(),
        ).y()
        self.assertLessEqual(abs(first_row_y - stock_header_y), 1)
        routine_label_rect, _, _ = _group_box_rects(window.routine_box)
        stock_label_rect, _, _ = _group_box_rects(window.stock_box)
        badge_rect = window._routine_tree_display_level_badges.geometry()
        status_rect = window.selected_routine_status_bar.geometry()
        routine_title_gap = badge_rect.top() - routine_label_rect.bottom() - 1
        stock_title_gap = status_rect.top() - stock_label_rect.bottom() - 1
        self.assertEqual(routine_title_gap, stock_title_gap)
        self.assertIn(routine_title_gap, range(6, 10))
        self.assertEqual(
            setting_window.AUTO_TRADE_SETTING_TOP_CONTROL_ROW_HEIGHT,
            badge_rect.height(),
        )
        self.assertEqual(badge_rect.height(), status_rect.height())
        badge_bottom_y = window._routine_tree_display_level_badges.mapTo(
            window,
            window._routine_tree_display_level_badges.rect().bottomLeft(),
        ).y()
        status_bottom_y = window.selected_routine_status_bar.mapTo(
            window,
            window.selected_routine_status_bar.rect().bottomLeft(),
        ).y()
        routine_body_gap = first_row_y - badge_bottom_y - 1
        stock_body_gap = stock_header_y - status_bottom_y - 1
        self.assertEqual(routine_body_gap, stock_body_gap)
        self.assertIn(routine_body_gap, range(6, 11))
        self.assertEqual(
            window.routine_table.geometry().bottom(),
            window.stock_table.geometry().bottom(),
        )

    def test_selected_routine_status_bar_omits_parent_routine_count_badge(self) -> None:
        instances = [self._instance("inst-a", "A 인스턴스")]
        window = self._window_harness()
        window._setup_selected_routine_status_bar()
        window.load_selected_routine_stocks = lambda: None
        counts = {
            "inst-a": {
                "registered": 7,
                "running": 3,
                "stopped": 4,
                "error": 1,
                "normal": 3,
                "excluded": 3,
                "review": 1,
            }
        }
        window._routine_instance_operation_counts = lambda: counts

        with patch.object(setting_window, "load_routine_definitions", return_value=[self._definition()]), \
                patch.object(setting_window, "load_persisted_routine_instances", return_value=instances):
            window.load_routine_table()
            window.routine_table.selectRow(0)
            window.update_selected_routine_status_bar()

            self.assertEqual("●", window.selected_routine_signal_label.text())
            self.assertEqual("지표추종매매", window.selected_routine_name_button.text())
            self.assertEqual("", window.selected_routine_instance_count_badge.text())
            self.assertTrue(window.selected_routine_instance_count_badge.isHidden())
            self.assertEqual("종목(7)", window.selected_routine_status_buttons["all"].text())
            self.assertEqual("정지(3)", window.selected_routine_status_buttons["running"].text())
            self.assertEqual("제외(3)", window.selected_routine_status_buttons["excluded"].text())
            self.assertEqual("검토(1)", window.selected_routine_status_buttons["error"].text())

            calls = []
            window.load_selected_routine_stocks = lambda: calls.append(window._stock_status_filter)
            window.selected_routine_status_buttons["running"].click()
            self.assertEqual("running", window._stock_status_filter)
            window.selected_routine_name_button.click()
            self.assertEqual("all", window._stock_status_filter)
            self.assertEqual(["running", "all"], calls)

            window.routine_table.selectRow(1)
            window.update_selected_routine_status_bar()
            self.assertEqual("A 인스턴스", window.selected_routine_name_button.text())
            self.assertTrue(window.selected_routine_instance_count_badge.isHidden())
            self.assertEqual("종목(7)", window.selected_routine_status_buttons["all"].text())
            self.assertEqual("정지(3)", window.selected_routine_status_buttons["running"].text())
            self.assertEqual("제외(3)", window.selected_routine_status_buttons["excluded"].text())
            self.assertEqual("검토(1)", window.selected_routine_status_buttons["error"].text())

    def test_all_stocks_button_selects_all_view_scope_and_restores_row_scope(self) -> None:
        with patch.object(AutoTradeSettingWindow, "refresh_all", lambda _self: None), \
                patch.object(AutoTradeSettingWindow, "update_startup_recovery_controls", lambda _self: None), \
                patch.object(AutoTradeSettingWindow, "current_runtime_file_signature", lambda _self: tuple()):
            window = AutoTradeSettingWindow()
        self.addCleanup(window.close)
        stock_loader = MagicMock()
        window.load_selected_routine_stocks = stock_loader
        window.routine_table.setRowCount(2)
        rows = (
            {
                "row_kind": "definition",
                "definition_id": "indicator_follow",
                "definition_name": "지표추종매매",
                "instance_count": 2,
                "registered": 7,
                "running": 3,
                "stopped": 4,
                "error": 1,
            },
            {
                "row_kind": "definition",
                "definition_id": "registration_review",
                "definition_name": "등록확인루틴",
                "instance_count": 1,
                "registered": 5,
                "running": 2,
                "stopped": 3,
                "error": 1,
            },
        )
        for row, metadata in enumerate(rows):
            item = QTableWidgetItem(str(metadata["definition_name"]))
            item.setData(Qt.UserRole, metadata)
            window.routine_table.setItem(row, 0, item)

        window.routine_table.selectRow(0)
        stock_loader.reset_mock()
        window.btn_all_stocks.click()

        self.assertTrue(window._all_stocks_scope_active)
        self.assertIsNone(window.current_selected_routine_row_metadata())
        self.assertEqual("all", window._stock_status_filter)
        self.assertEqual("전체", window.selected_routine_name_button.text())
        self.assertTrue(window.selected_routine_signal_label.isHidden())
        self.assertEqual("그룹(2)", window.selected_routine_group_count_badge.text())
        self.assertEqual("루틴(3)", window.selected_routine_instance_count_badge.text())
        self.assertEqual("종목(12)", window.selected_routine_status_buttons["all"].text())
        self.assertEqual("실행(5)", window.selected_routine_status_buttons["running"].text())
        self.assertEqual("정지(7)", window.selected_routine_status_buttons["stopped"].text())
        self.assertEqual("검토(2)", window.selected_routine_status_buttons["error"].text())
        self.assertEqual(
            setting_window.auto_trade_setting_badge_stylesheet(
                "QPushButton",
                text_color=setting_window.AUTO_TRADE_SETTING_BADGE_ACTIVE_COLOR,
                border_color=setting_window.AUTO_TRADE_SETTING_BADGE_ACTIVE_COLOR,
            ),
            window.btn_all_stocks.styleSheet(),
        )
        stock_loader.assert_called_once_with()

        window.routine_table.selectRow(1)
        self.assertFalse(window._all_stocks_scope_active)
        self.assertEqual("등록확인루틴", window.selected_routine_name_button.text())
        self.assertTrue(window.selected_routine_group_count_badge.isHidden())
        self.assertFalse(window.selected_routine_signal_label.isHidden())

    def test_auto_trade_setting_open_resets_default_filters(self) -> None:
        with patch.object(AutoTradeSettingWindow, "refresh_all", lambda _self: None), \
                patch.object(AutoTradeSettingWindow, "update_startup_recovery_controls", lambda _self: None), \
                patch.object(AutoTradeSettingWindow, "current_runtime_file_signature", lambda _self: tuple()):
            window = AutoTradeSettingWindow()
        self.addCleanup(window.close)
        stock_loader = MagicMock()
        window.load_selected_routine_stocks = stock_loader

        window._set_routine_tree_display_level("stock")
        window._set_routine_tree_display_scope("historical")
        window._set_routine_tree_display_criterion("period")
        window._set_routine_tree_valid_only(True)
        window._stock_status_filter = "running"
        window._all_stocks_scope_active = False
        stock_loader.reset_mock()

        window.reset_default_filters_for_open()

        self.assertEqual("stock", window._routine_tree_display_level)
        self.assertEqual("profit", window._routine_tree_display_criterion)
        self.assertEqual("all", window._routine_tree_display_scope)
        self.assertEqual("all", window._routine_tree_last_stock_scope)
        self.assertFalse(window._routine_tree_valid_only)
        self.assertEqual("all", window._stock_status_filter)
        self.assertTrue(window._all_stocks_scope_active)
        self.assertIn(
            "color: #16A34A",
            window._routine_tree_display_level_buttons["stock"].styleSheet(),
        )
        self.assertIn(
            "color: #16A34A",
            window._routine_tree_display_criterion_buttons["profit"].styleSheet(),
        )
        self.assertIn("color: #16A34A", window.btn_all_stocks.styleSheet())
        stock_loader.assert_called_once_with()

    def test_all_stocks_view_scope_loads_every_persisted_instance(self) -> None:
        window = SimpleNamespace(
            _all_stocks_scope_active=True,
            all_registered_instance_ids=lambda: ("inst-a", "inst-b"),
            current_selected_target_instance_ids=lambda: ("inst-a",),
        )
        stocks = [
            {
                "stock_path": "stocks/111111_A",
                "assigned_routine_instance_id": "inst-a",
            },
            {
                "stock_path": "stocks/222222_B",
                "assigned_routine_instance_id": "inst-b",
            },
            {
                "stock_path": "stocks/333333_C",
                "assigned_routine_instance_id": "inst-c",
            },
        ]

        with patch.object(table_loader, "read_base_stocks", return_value=stocks):
            result = table_loader._selected_instance_stock_dirs(window)

        self.assertEqual(
            ["111111_A", "222222_B"],
            [path.name for path in result],
        )

    def test_instance_renders_current_stock_rows_without_internal_scope_badges(self) -> None:
        instances = [self._instance("inst-a", "A 인스턴스")]
        stocks = [
            {
                "stock_path": "stocks/005930_A",
                "assigned_routine_instance_id": "inst-a",
                "code": "005930",
                "name": "삼성전자",
            },
            {
                "stock_path": "stocks/005380_B",
                "assigned_routine_instance_id": "inst-a",
                "code": "005380",
                "name": "현대차",
            },
        ]
        window = self._window_harness()
        window._routine_instance_operation_counts = lambda: {
            "inst-a": {"registered": 2, "running": 0, "stopped": 2, "error": 0}
        }

        with patch.object(setting_window, "load_routine_definitions", return_value=[self._definition()]), \
                patch.object(setting_window, "load_persisted_routine_instances", return_value=instances), \
                patch.object(setting_window, "read_base_stocks", return_value=stocks):
            window.load_routine_table()
            window._set_routine_tree_display_level("stock")

        row_kinds = [
            window.routine_table.item(row, 0).data(setting_window.Qt.UserRole)["row_kind"]
            for row in range(window.routine_table.rowCount())
        ]
        self.assertEqual(
            ["definition", "instance", "stock", "stock"],
            row_kinds,
        )
        self.assertNotIn("현재 종목", [window.routine_table.item(row, 0).data(setting_window.Qt.UserRole)["display_name"] for row in range(window.routine_table.rowCount())])
        self.assertNotIn("과거 종목", [window.routine_table.item(row, 0).data(setting_window.Qt.UserRole)["display_name"] for row in range(window.routine_table.rowCount())])

        stock_widget = window.routine_table.cellWidget(2, 0)
        second_stock_widget = window.routine_table.cellWidget(3, 0)
        instance_widget = window.routine_table.cellWidget(1, 0)
        stock_layout_margins = stock_widget.layout().contentsMargins()
        second_stock_layout_margins = second_stock_widget.layout().contentsMargins()
        self.assertEqual(
            (
                setting_window.AUTO_TRADE_SETTING_STOCK_ROW_MARGIN_X,
                setting_window.AUTO_TRADE_SETTING_INSTANCE_GROUP_TOP_GAP,
                setting_window.AUTO_TRADE_SETTING_STOCK_ROW_MARGIN_X,
                0,
            ),
            (
                stock_layout_margins.left(),
                stock_layout_margins.top(),
                stock_layout_margins.right(),
                stock_layout_margins.bottom(),
            ),
        )
        self.assertEqual(0, second_stock_layout_margins.top())
        self.assertEqual(setting_window.AUTO_TRADE_SETTING_STOCK_ROW_SPACING, stock_widget.layout().spacing())
        instance_icon = instance_widget.findChild(setting_window.QLabel, "autoTradeSettingRoutineTreeIcon")
        stock_icon = stock_widget.findChild(setting_window.QLabel, "autoTradeSettingRoutineTreeIcon")
        instance_title = instance_widget.findChild(setting_window.QLabel, "autoTradeSettingRoutineTreeTitle")
        stock_title = stock_widget.findChild(setting_window.QLabel, "autoTradeSettingRoutineTreeTitle")
        self.assertEqual("▼", instance_icon.text())
        self.assertEqual(setting_window.Qt.PointingHandCursor, instance_icon.cursor().shape())
        self.assertFalse(instance_icon.testAttribute(setting_window.Qt.WA_TransparentForMouseEvents))
        self.assertEqual("✓", stock_icon.text())
        self.assertIn("color: #7E22CE", stock_icon.styleSheet())
        self.assertEqual("삼성전자", stock_widget.findChild(setting_window.QLabel, "autoTradeSettingRoutineTreeTitle").text())
        self.assertEqual("삼성전자", stock_title.toolTip())
        self.assertEqual("삼성전자", window.routine_table.item(2, 0).data(setting_window.Qt.ToolTipRole))
        self.assertEqual(setting_window.Qt.AlignCenter | setting_window.Qt.AlignVCenter, instance_title.alignment())
        self.assertEqual(setting_window.Qt.AlignLeft | setting_window.Qt.AlignVCenter, stock_title.alignment())
        self.assertIn("color: #7E22CE", stock_title.styleSheet())
        self.assertEqual(
            instance_title.mapTo(instance_widget, instance_title.rect().topLeft()).x(),
            stock_title.mapTo(stock_widget, stock_title.rect().topLeft()).x(),
        )
        stock_title_spacer = stock_widget.findChild(
            setting_window.QWidget,
            "autoTradeSettingRoutineTreeStockTitleXCompensation",
        )
        self.assertEqual(
            setting_window.AUTO_TRADE_SETTING_STOCK_TITLE_X_COMPENSATION,
            stock_title_spacer.width(),
        )
        stock_performance_spacer = stock_widget.findChild(
            setting_window.QWidget,
            "autoTradeSettingRoutineTreeStockPerformanceXCompensation",
        )
        self.assertEqual(
            setting_window.AUTO_TRADE_SETTING_STOCK_PERFORMANCE_X_COMPENSATION,
            stock_performance_spacer.width(),
        )
        self.assertIsNotNone(stock_widget.findChild(setting_window.QWidget, "autoTradeSettingRoutineTreePerformancePeriod"))
        self.assertIsNotNone(stock_widget.findChild(setting_window.QWidget, "autoTradeSettingRoutineTreePerformanceProfit"))
        self.assertIsNotNone(stock_widget.findChild(setting_window.QWidget, "autoTradeSettingRoutineTreePerformanceAverage"))
        self.assertIsNotNone(stock_widget.findChild(setting_window.QWidget, "autoTradeSettingRoutineTreePerformanceEfficiency"))
        for label in stock_widget.findChildren(setting_window.QLabel):
            if label.objectName().startswith("autoTradeSettingRoutineTreePerformance"):
                expected_color = (
                    "#374151"
                    if any(
                        metric in label.objectName()
                        for metric in ("Profit", "Average", "Efficiency")
                    )
                    else "#7E22CE"
                )
                self.assertIn(f"color: {expected_color}", label.styleSheet())
        self.assertEqual(
            "0",
            stock_widget.findChild(
                setting_window.QLabel,
                "autoTradeSettingRoutineTreePerformancePeriodLeftValue",
            ).text(),
        )
        self.assertEqual(
            setting_window.AUTO_TRADE_SETTING_STOCK_ROW_HEIGHT
            + setting_window.AUTO_TRADE_SETTING_INSTANCE_GROUP_TOP_GAP,
            window.routine_table.rowHeight(2),
        )
        self.assertEqual(
            setting_window.AUTO_TRADE_SETTING_STOCK_ROW_HEIGHT,
            window.routine_table.rowHeight(3),
        )
        self.assertLessEqual(window.routine_table.rowHeight(3), window.routine_table.rowHeight(1) - 2)
        stock_widget.resize(max(stock_widget.sizeHint().width(), 960), stock_widget.sizeHint().height())
        stock_widget.layout().activate()
        previous_x = -1
        for object_name in (
            "autoTradeSettingRoutineTreeTitle",
            "autoTradeSettingRoutineTreePerformancePeriod",
            "autoTradeSettingRoutineTreePerformanceProfit",
            "autoTradeSettingRoutineTreePerformanceAverage",
            "autoTradeSettingRoutineTreePerformanceEfficiency",
        ):
            child = stock_widget.findChild(setting_window.QWidget, object_name)
            self.assertIsNotNone(child)
            child_x = child.mapTo(stock_widget, child.rect().topLeft()).x()
            self.assertGreaterEqual(child_x, previous_x)
            previous_x = child_x
        window.routine_table.selectRow(2)
        self.assertEqual(("inst-a",), window.current_selected_target_instance_ids())
        window.routine_table.selectRow(3)
        self.assertEqual(("inst-a",), window.current_selected_target_instance_ids())

    def test_current_and_historical_stocks_are_visually_and_operationally_separate(self) -> None:
        instances = [
            self._instance("inst-a", "과거 인스턴스"),
            self._instance("inst-b", "현재 인스턴스"),
        ]
        current_stocks = {
            "inst-b": [
                {
                    "stock_path": "stocks/003550_LG",
                    "stock_code": "003550",
                    "stock_name": "LG",
                }
            ]
        }
        historical_stocks = {
            "inst-a": [
                {
                    "stock_path": "stocks/003550_LG",
                    "stock_code": "003550",
                    "stock_name": "LG",
                    "is_historical": True,
                },
                {
                    "stock_path": "stocks/005930_삼성전자",
                    "stock_code": "005930",
                    "stock_name": "삼성전자",
                    "is_historical": True,
                },
            ]
        }
        window = self._window_harness()
        window._current_stocks_by_instance = lambda: current_stocks
        window._historical_stocks_by_instance = lambda: historical_stocks
        window._routine_instance_operation_counts = lambda: {}
        performance_paths = []

        def performance_source(stock):
            stock_path = str(stock.get("stock_path", ""))
            performance_paths.append(stock_path)
            negative = "005930" in stock_path
            return {
                "trade_days": 2 if negative else 3,
                "realized_profit": -48000 if negative else 125000,
                "profit_rate": -1.40 if negative else 3.25,
                "average": -24000 if negative else 62500,
                "average_rate": -0.70 if negative else 1.63,
                "profit_factor": 0.0 if negative else 3.2,
            }

        window._routine_tree_stock_performance_source = performance_source

        with (
            patch.object(setting_window, "load_routine_definitions", return_value=[self._definition()]),
            patch.object(setting_window, "load_persisted_routine_instances", return_value=instances),
        ):
            window.load_routine_table()
            window._set_routine_tree_display_level("stock")

            rows = [
                window.routine_table.item(row, 0).data(setting_window.Qt.UserRole)
                for row in range(window.routine_table.rowCount())
            ]
            stock_rows = [row for row in rows if row["row_kind"] == "stock"]
            self.assertEqual(
                [
                    ("inst-a", "003550", True),
                    ("inst-a", "005930", True),
                    ("inst-b", "003550", False),
                ],
                [
                    (
                        row["instance_id"],
                        row["stock_code"],
                        bool(row.get("is_historical", False)),
                    )
                    for row in stock_rows
                ],
            )

            historical_row = next(
                row
                for row in range(window.routine_table.rowCount())
                if bool(
                    window.routine_table.item(row, 0)
                    .data(setting_window.Qt.UserRole)
                    .get("is_historical", False)
                )
            )
            current_row = next(
                row
                for row in range(window.routine_table.rowCount())
                if window.routine_table.item(row, 0).data(setting_window.Qt.UserRole).get("row_kind")
                == "stock"
                and not bool(
                    window.routine_table.item(row, 0)
                    .data(setting_window.Qt.UserRole)
                    .get("is_historical", False)
                )
            )
            historical_widget = window.routine_table.cellWidget(historical_row, 0)
            current_widget = window.routine_table.cellWidget(current_row, 0)
            self.assertEqual(
                "▪",
                historical_widget.findChild(
                    setting_window.QLabel,
                    "autoTradeSettingRoutineTreeIcon",
                ).text(),
            )
            self.assertEqual(
                "✓",
                current_widget.findChild(
                    setting_window.QLabel,
                    "autoTradeSettingRoutineTreeIcon",
                ).text(),
            )
            historical_title = historical_widget.findChild(
                setting_window.QLabel,
                "autoTradeSettingRoutineTreeTitle",
            )
            self.assertIn(
                setting_window.AUTO_TRADE_SETTING_HISTORICAL_STOCK_ROW_TEXT_COLOR,
                historical_title.styleSheet(),
            )
            self.assertEqual(
                "+125,000",
                historical_widget.findChild(
                    setting_window.QLabel,
                    "autoTradeSettingRoutineTreePerformanceProfitLeftValue",
                ).text(),
            )
            self.assertEqual(
                "+3.25%",
                historical_widget.findChild(
                    setting_window.QLabel,
                    "autoTradeSettingRoutineTreePerformanceProfitRightValue",
                ).text(),
            )
            for metric, color in (
                ("Profit", "#DC2626"),
                ("Average", "#DC2626"),
                ("Efficiency", "#2563EB"),
            ):
                labels = historical_widget.findChildren(
                    setting_window.QLabel,
                )
                metric_labels = [
                    label
                    for label in labels
                    if metric in label.objectName()
                    and label.objectName().startswith(
                        "autoTradeSettingRoutineTreePerformance"
                    )
                ]
                self.assertTrue(metric_labels)
                for label in metric_labels:
                    self.assertIn(f"color: {color}", label.styleSheet())

            current_profit = current_widget.findChild(
                setting_window.QWidget,
                "autoTradeSettingRoutineTreePerformanceProfit",
            )
            historical_profit = historical_widget.findChild(
                setting_window.QWidget,
                "autoTradeSettingRoutineTreePerformanceProfit",
            )
            self.assertEqual(current_profit.width(), historical_profit.width())
            self.assertEqual(
                current_profit.mapTo(current_widget, current_profit.rect().topRight()).x(),
                historical_profit.mapTo(
                    historical_widget,
                    historical_profit.rect().topRight(),
                ).x(),
            )
            window.routine_table.selectRow(historical_row)
            self.assertEqual((), window.current_selected_target_instance_ids())
            self.assertEqual("", window.current_selected_instance_id())

            window._set_routine_tree_display_scope("current")
            self.assertEqual(
                [("inst-b", "003550")],
                [
                    (row["instance_id"], row["stock_code"])
                    for row in (
                        window.routine_table.item(index, 0).data(setting_window.Qt.UserRole)
                        for index in range(window.routine_table.rowCount())
                    )
                    if row["row_kind"] == "stock"
                ],
            )

            window._set_routine_tree_display_scope("historical")
            self.assertEqual(
                [
                    ("inst-a", "003550"),
                    ("inst-a", "005930"),
                ],
                [
                    (row["instance_id"], row["stock_code"])
                    for row in (
                        window.routine_table.item(index, 0).data(
                            setting_window.Qt.UserRole
                        )
                        for index in range(window.routine_table.rowCount())
                    )
                    if row["row_kind"] == "stock"
                ],
            )
            self.assertFalse(window.routine_table.isRowHidden(1))
            self.assertFalse(window.routine_table.isRowHidden(4))

            window._set_routine_tree_display_scope("all")
            window._set_routine_tree_valid_only(True)
            visible_rows = [
                window.routine_table.item(row, 0).data(setting_window.Qt.UserRole)
                for row in range(window.routine_table.rowCount())
                if not window.routine_table.isRowHidden(row)
            ]
            self.assertEqual(
                ["stock", "stock"],
                [str(row.get("row_kind", "")) for row in visible_rows],
            )
            self.assertEqual(
                {
                    ("inst-a", "005930"),
                    ("inst-b", "003550"),
                },
                {
                    (str(row.get("instance_id", "")), str(row.get("stock_code", "")))
                    for row in visible_rows
                },
            )
            self.assertIn("stocks/003550_LG", performance_paths)
            self.assertIn("stocks/005930_삼성전자", performance_paths)

    def test_stock_metrics_sort_current_and_historical_together_and_scope_resets_order(
        self,
    ) -> None:
        instance = self._instance("inst-sort", "정렬 인스턴스")
        current_stocks = [
            {
                "stock_path": "fixture/current-c1",
                "stock_code": "000001",
                "stock_name": "현재1",
            },
            {
                "stock_path": "fixture/current-c2",
                "stock_code": "000002",
                "stock_name": "현재2",
            },
        ]
        historical_stocks = [
            {
                "instance_id": "inst-sort",
                "stock_path": "fixture/history-h1",
                "stock_code": "100001",
                "stock_name": "과거1",
                "is_historical": True,
            },
            {
                "instance_id": "inst-sort",
                "stock_path": "fixture/history-h2",
                "stock_code": "100002",
                "stock_name": "과거2",
                "is_historical": True,
            },
        ]
        performance_by_code = {
            "000001": {
                "trade_days": 3,
                "realized_profit": 100.0,
                "average": 5.0,
                "profit_factor": 1.0,
            },
            "000002": {
                "trade_days": 8,
                "realized_profit": -50.0,
                "average": 30.0,
                "profit_factor": 4.0,
            },
            "100001": {
                "trade_days": 10,
                "realized_profit": 100.0,
                "average": -10.0,
                "profit_factor": 2.0,
            },
            "100002": {
                "trade_days": 1,
                "realized_profit": 200.0,
                "average": 10.0,
                "profit_factor": 0.0,
            },
        }

        with (
            patch.object(AutoTradeSettingWindow, "refresh_all"),
            patch.object(
                AutoTradeSettingWindow,
                "update_startup_recovery_controls",
            ),
        ):
            window = AutoTradeSettingWindow()
        try:
            window._routine_instance_operation_counts = lambda: {
                "inst-sort": {
                    "registered": 2,
                    "running": 0,
                    "stopped": 2,
                    "error": 0,
                },
            }
            window._current_stocks_by_instance = lambda: {
                "inst-sort": list(current_stocks)
            }
            window._historical_stocks_by_instance = lambda: {
                "inst-sort": list(historical_stocks)
            }

            def performance_source(
                _window,
                stock: dict[str, object],
            ) -> dict[str, object]:
                code = str(stock.get("stock_code", "") or "")
                return {
                    **performance_by_code[code],
                    "profit_rate": 0.0,
                    "average_rate": 0.0,
                    "is_current": not bool(stock.get("is_historical", False)),
                }

            window._routine_tree_stock_performance_source = MethodType(
                performance_source,
                window,
            )

            def stock_codes() -> list[str]:
                result = []
                for row in range(window.routine_table.rowCount()):
                    metadata = window.routine_table.item(
                        row,
                        0,
                    ).data(Qt.UserRole)
                    if metadata.get("row_kind") == "stock":
                        result.append(str(metadata.get("stock_code", "")))
                return result

            with (
                patch.object(
                    setting_window,
                    "load_routine_definitions",
                    return_value=[self._definition()],
                ),
                patch.object(
                    setting_window,
                    "load_persisted_routine_instances",
                    return_value=[instance],
                ),
            ):
                window._set_routine_tree_display_level("stock")
                window.load_routine_table()
                self.assertEqual(
                    ["000001", "000002", "100001", "100002"],
                    stock_codes(),
                )

                expected_by_criterion = {
                    "period": ["100001", "000002", "000001", "100002"],
                    "profit": ["100002", "000001", "100001", "000002"],
                    "average": ["000002", "100002", "000001", "100001"],
                    "efficiency": ["000002", "100001", "000001", "100002"],
                }
                for criterion, expected_codes in expected_by_criterion.items():
                    window._set_routine_tree_display_criterion(criterion)
                    self.assertEqual(expected_codes, stock_codes())

                screenshot_path = os.environ.get(
                    "AUTO_TRADE_STOCK_SORT_SCREENSHOT_PATH",
                    "",
                ).strip()
                if screenshot_path:
                    window._set_routine_tree_parent_summary_visible(
                        window.routine_table.cellWidget(0, 0),
                        False,
                    )
                    window.resize(1880, 720)
                    window.show()
                    self._app.processEvents()
                    self.assertTrue(window.grab().save(screenshot_path))

                window._set_routine_tree_display_scope("current")
                self.assertFalse(
                    window._routine_tree_stock_performance_sort_active
                )
                self.assertEqual(["000001", "000002"], stock_codes())
                current_expected_by_criterion = {
                    "period": ["000002", "000001"],
                    "profit": ["000001", "000002"],
                    "average": ["000002", "000001"],
                    "efficiency": ["000002", "000001"],
                }
                for criterion, expected_codes in (
                    current_expected_by_criterion.items()
                ):
                    window._set_routine_tree_display_criterion(criterion)
                    self.assertEqual(expected_codes, stock_codes())

                window._set_routine_tree_display_scope("current")
                self.assertFalse(
                    window._routine_tree_stock_performance_sort_active
                )
                self.assertEqual(["000001", "000002"], stock_codes())

                window._set_routine_tree_display_scope("all")
                self.assertEqual(
                    ["000001", "000002", "100001", "100002"],
                    stock_codes(),
                )

                window._set_routine_tree_display_scope("historical")
                self.assertEqual(["100001", "100002"], stock_codes())
                historical_stock_rows = [
                    row
                    for row in range(window.routine_table.rowCount())
                    if window.routine_table.item(row, 0)
                    .data(Qt.UserRole)
                    .get("row_kind")
                    == "stock"
                ]
                self.assertTrue(
                    all(
                        window.routine_table.item(row, 0)
                        .data(Qt.UserRole)
                        .get("is_historical")
                        for row in historical_stock_rows
                    )
                )
                self.assertEqual(
                    ["▪", "▪"],
                    [
                        window.routine_table.cellWidget(row, 0)
                        .findChild(
                            setting_window.QLabel,
                            "autoTradeSettingRoutineTreeIcon",
                        )
                        .text()
                        for row in historical_stock_rows
                    ],
                )
                historical_expected_by_criterion = {
                    "period": ["100001", "100002"],
                    "profit": ["100002", "100001"],
                    "average": ["100002", "100001"],
                    "efficiency": ["100001", "100002"],
                }
                for criterion, expected_codes in (
                    historical_expected_by_criterion.items()
                ):
                    window._set_routine_tree_display_criterion(criterion)
                    self.assertEqual(expected_codes, stock_codes())

                historical_screenshot_path = os.environ.get(
                    "AUTO_TRADE_HISTORICAL_SCOPE_SCREENSHOT_PATH",
                    "",
                ).strip()
                if historical_screenshot_path:
                    window._set_routine_tree_parent_summary_visible(
                        window.routine_table.cellWidget(0, 0),
                        False,
                    )
                    window.resize(1880, 720)
                    window.show()
                    self._app.processEvents()
                    self.assertTrue(
                        window.grab().save(historical_screenshot_path)
                    )

                window._set_routine_tree_display_scope("current")
                window._set_routine_tree_display_scope("historical")
                self.assertFalse(
                    window._routine_tree_stock_performance_sort_active
                )
                self.assertEqual(["100001", "100002"], stock_codes())

                window._set_routine_tree_display_scope("current")
                window._set_routine_tree_display_scope("all")
                self.assertEqual(
                    ["000001", "000002", "100001", "100002"],
                    stock_codes(),
                )
        finally:
            window.close()
            window.deleteLater()
            self._app.processEvents()

    def test_valid_stock_view_sorts_visible_stocks_globally_by_metric(
        self,
    ) -> None:
        instances = [
            self._instance("inst-a", "A 인스턴스"),
            self._instance("inst-b", "B 인스턴스"),
        ]
        current_stocks = {
            "inst-a": [
                {
                    "stock_path": "fixture/current-samsung",
                    "stock_code": "005930",
                    "stock_name": "삼성전자",
                },
                {
                    "stock_path": "fixture/current-hyundai",
                    "stock_code": "005380",
                    "stock_name": "현대차",
                },
            ],
            "inst-b": [
                {
                    "stock_path": "fixture/current-kakao",
                    "stock_code": "035720",
                    "stock_name": "카카오",
                },
            ],
        }
        historical_stocks = {
            "inst-b": [
                {
                    "instance_id": "inst-b",
                    "stock_path": "fixture/history-naver",
                    "stock_code": "035420",
                    "stock_name": "NAVER",
                    "is_historical": True,
                },
            ],
        }
        performance_by_code = {
            "005930": {
                "trade_days": 8,
                "realized_profit": 3.0,
                "average": 50.0,
                "profit_factor": 1.0,
            },
            "005380": {
                "trade_days": 5,
                "realized_profit": 10.0,
                "average": 10.0,
                "profit_factor": 9.0,
            },
            "035720": {
                "trade_days": 1,
                "realized_profit": 7.0,
                "average": 100.0,
                "profit_factor": 2.0,
            },
            "035420": {
                "trade_days": 20,
                "realized_profit": -2.0,
                "average": -1.0,
                "profit_factor": 5.0,
            },
        }
        window = self._window_harness()
        window._routine_instance_operation_counts = lambda: {
            "inst-a": {
                "registered": 2,
                "running": 0,
                "stopped": 2,
                "error": 0,
            },
            "inst-b": {
                "registered": 1,
                "running": 0,
                "stopped": 1,
                "error": 0,
            },
        }
        window._current_stocks_by_instance = lambda: current_stocks
        window._historical_stocks_by_instance = lambda: historical_stocks

        def performance_source(stock: dict[str, object]) -> dict[str, object]:
            code = str(stock.get("stock_code", "") or "")
            return {
                **performance_by_code[code],
                "profit_rate": 0.0,
                "average_rate": 0.0,
                "is_current": not bool(stock.get("is_historical", False)),
            }

        window._routine_tree_stock_performance_source = performance_source

        def visible_rows() -> list[dict[str, object]]:
            return [
                window.routine_table.item(row, 0).data(setting_window.Qt.UserRole)
                for row in range(window.routine_table.rowCount())
                if not window.routine_table.isRowHidden(row)
            ]

        def visible_stock_codes() -> list[str]:
            return [
                str(row.get("stock_code", "") or "")
                for row in visible_rows()
                if row.get("row_kind") == "stock"
            ]

        def all_row_kinds() -> list[str]:
            return [
                str(
                    window.routine_table.item(row, 0)
                    .data(setting_window.Qt.UserRole)
                    .get("row_kind", "")
                )
                for row in range(window.routine_table.rowCount())
            ]

        def all_stock_codes() -> list[str]:
            result: list[str] = []
            for row in range(window.routine_table.rowCount()):
                metadata = window.routine_table.item(row, 0).data(
                    setting_window.Qt.UserRole
                )
                if metadata.get("row_kind") == "stock":
                    result.append(str(metadata.get("stock_code", "")))
            return result

        with (
            patch.object(
                setting_window,
                "load_routine_definitions",
                return_value=[self._definition()],
            ),
            patch.object(
                setting_window,
                "load_persisted_routine_instances",
                return_value=instances,
            ),
        ):
            window.load_routine_table()
            window._set_routine_tree_valid_only(True)
            window._set_routine_tree_display_level("stock")
            window._set_routine_tree_display_scope("all")
            window._set_routine_tree_display_criterion("profit")

            self.assertTrue(window._routine_tree_stock_performance_sort_active)
            self.assertEqual(
                ["stock", "stock", "stock", "stock"],
                [str(row.get("row_kind", "")) for row in visible_rows()],
            )
            self.assertEqual(
                ["005380", "035720", "005930", "035420"],
                visible_stock_codes(),
            )

            expected_by_criterion = {
                "period": ["035420", "005930", "005380", "035720"],
                "profit": ["005380", "035720", "005930", "035420"],
                "average": ["035720", "005380", "005930", "035420"],
                "efficiency": ["005380", "035420", "035720", "005930"],
            }
            for criterion, expected_codes in expected_by_criterion.items():
                window._set_routine_tree_display_criterion(criterion)
                self.assertEqual(expected_codes, visible_stock_codes())

            window._set_routine_tree_display_criterion("profit")
            window._set_routine_tree_display_scope("current")
            self.assertTrue(window._routine_tree_stock_performance_sort_active)
            self.assertEqual(
                ["005380", "035720", "005930"],
                visible_stock_codes(),
            )

            window._set_routine_tree_display_scope("historical")
            self.assertTrue(window._routine_tree_stock_performance_sort_active)
            self.assertEqual(["035420"], visible_stock_codes())

            window._set_routine_tree_display_scope("all")
            self.assertEqual(
                ["005380", "035720", "005930", "035420"],
                visible_stock_codes(),
            )
            window._set_routine_tree_valid_only(False)
            self.assertEqual(
                [
                    "definition",
                    "instance",
                    "stock",
                    "stock",
                    "instance",
                    "stock",
                    "stock",
                ],
                all_row_kinds(),
            )
            self.assertEqual(
                ["005930", "005380", "035720", "035420"],
                all_stock_codes(),
            )
            self.assertTrue(
                any(row.get("row_kind") == "instance" for row in visible_rows())
            )

            window._set_routine_tree_valid_only(True)
            window._set_routine_tree_display_level("stock")
            self.assertEqual(
                ["005380", "035720", "005930", "035420"],
                visible_stock_codes(),
            )

            window._set_routine_tree_display_level("routine")
            self.assertEqual(
                [
                    "definition",
                    "instance",
                    "stock",
                    "stock",
                    "instance",
                    "stock",
                    "stock",
                ],
                all_row_kinds(),
            )
            self.assertEqual(["definition", "instance", "instance"], [
                str(row.get("row_kind", "")) for row in visible_rows()
            ])

            window._set_routine_tree_display_level("stock")
            self.assertEqual(
                ["005380", "035720", "005930", "035420"],
                visible_stock_codes(),
            )

            window._set_routine_tree_display_level("category")
            self.assertEqual(
                [
                    "definition",
                    "instance",
                    "stock",
                    "stock",
                    "instance",
                    "stock",
                    "stock",
                ],
                all_row_kinds(),
            )
            self.assertEqual(["definition"], [
                str(row.get("row_kind", "")) for row in visible_rows()
            ])

    def test_valid_routine_view_sorts_instance_rows_by_selected_metric(self) -> None:
        instances = [
            self._instance("inst-a", "A 루틴"),
            self._instance("inst-b", "B 루틴"),
            self._instance("inst-c", "C 루틴"),
            self._instance("inst-d", "D 루틴"),
        ]
        current_stocks = {
            instance.instance_id: [
                {
                    "instance_id": instance.instance_id,
                    "stock_path": f"fixture/{instance.instance_id}",
                    "stock_code": f"00000{index}",
                    "stock_name": f"{instance.display_name} 종목",
                }
            ]
            for index, instance in enumerate(instances, start=1)
        }
        performance_by_path = {
            "fixture/inst-a": {
                "trade_days": 3,
                "realized_profit": 10.0,
                "average": 1.0,
                "profit_factor": 2.0,
            },
            "fixture/inst-b": {
                "trade_days": 8,
                "realized_profit": 5.0,
                "average": 4.0,
                "profit_factor": 1.0,
            },
            "fixture/inst-c": {
                "trade_days": 1,
                "realized_profit": 20.0,
                "average": 3.0,
                "profit_factor": 4.0,
            },
            "fixture/inst-d": {
                "trade_days": None,
                "realized_profit": None,
                "average": None,
                "profit_factor": None,
            },
        }
        window = self._window_harness()
        window._routine_instance_operation_counts = lambda: {
            instance.instance_id: {
                "registered": 1,
                "running": 0,
                "stopped": 1,
                "error": 0,
            }
            for instance in instances
        }
        window._current_stocks_by_instance = lambda: current_stocks
        window._historical_stocks_by_instance = lambda: {}
        window._routine_tree_stock_performance_source = lambda stock: {
            **performance_by_path[str(stock.get("stock_path", ""))],
            "profit_rate": 0.0,
            "average_rate": 0.0,
            "is_current": True,
        }

        def visible_instance_ids() -> list[str]:
            return [
                window.routine_table.item(row, 0).data(setting_window.Qt.UserRole)[
                    "instance_id"
                ]
                for row in range(window.routine_table.rowCount())
                if not window.routine_table.isRowHidden(row)
                and window.routine_table.item(row, 0)
                .data(setting_window.Qt.UserRole)
                .get("row_kind")
                == "instance"
            ]

        with (
            patch.object(
                setting_window,
                "load_routine_definitions",
                return_value=[self._definition()],
            ),
            patch.object(
                setting_window,
                "load_persisted_routine_instances",
                return_value=instances,
            ),
        ):
            window.load_routine_table()
            window._set_routine_tree_valid_only(True)
            window._set_routine_tree_display_level("routine")

            expected_by_criterion = {
                "period": ["inst-b", "inst-a", "inst-c", "inst-d"],
                "profit": ["inst-c", "inst-a", "inst-b", "inst-d"],
                "average": ["inst-b", "inst-c", "inst-a", "inst-d"],
                "efficiency": ["inst-c", "inst-a", "inst-b", "inst-d"],
            }
            for criterion, expected in expected_by_criterion.items():
                window._set_routine_tree_display_criterion(criterion)
                self.assertEqual(expected, visible_instance_ids())

    def test_valid_routine_view_sorts_visible_child_stocks_by_selected_metric(self) -> None:
        instances = [self._instance("inst-a", "A 루틴")]
        current_stocks = {
            "inst-a": [
                {
                    "instance_id": "inst-a",
                    "stock_path": "fixture/profit-positive",
                    "stock_code": "000001",
                    "stock_name": "양수종목",
                },
                {
                    "instance_id": "inst-a",
                    "stock_path": "fixture/profit-zero",
                    "stock_code": "000002",
                    "stock_name": "중립종목",
                },
                {
                    "instance_id": "inst-a",
                    "stock_path": "fixture/profit-negative",
                    "stock_code": "000003",
                    "stock_name": "음수종목",
                },
            ]
        }
        performance_by_path = {
            "fixture/profit-positive": {
                "trade_days": 2,
                "realized_profit": 125000.0,
                "average": 62500.0,
                "profit_factor": 3.2,
            },
            "fixture/profit-zero": {
                "trade_days": 0,
                "realized_profit": 0.0,
                "average": 0.0,
                "profit_factor": 0.0,
            },
            "fixture/profit-negative": {
                "trade_days": 3,
                "realized_profit": -48000.0,
                "average": -24000.0,
                "profit_factor": 0.0,
            },
        }
        window = self._window_harness()
        window._routine_instance_operation_counts = lambda: {
            "inst-a": {"registered": 3, "running": 0, "stopped": 3, "error": 0}
        }
        window._current_stocks_by_instance = lambda: current_stocks
        window._historical_stocks_by_instance = lambda: {}
        window._routine_tree_stock_performance_source = lambda stock: {
            **performance_by_path[str(stock.get("stock_path", ""))],
            "profit_rate": 0.0,
            "average_rate": 0.0,
            "is_current": True,
        }

        def visible_stock_codes() -> list[str]:
            return [
                window.routine_table.item(row, 0).data(setting_window.Qt.UserRole)[
                    "stock_code"
                ]
                for row in range(window.routine_table.rowCount())
                if not window.routine_table.isRowHidden(row)
                and window.routine_table.item(row, 0)
                .data(setting_window.Qt.UserRole)
                .get("row_kind")
                == "stock"
            ]

        with (
            patch.object(
                setting_window,
                "load_routine_definitions",
                return_value=[self._definition()],
            ),
            patch.object(
                setting_window,
                "load_persisted_routine_instances",
                return_value=instances,
            ),
        ):
            window.load_routine_table()
            window._set_routine_tree_valid_only(True)
            window._set_routine_tree_display_level("routine")
            window._toggle_routine_instance_collapsed("inst-a")

            window._set_routine_tree_display_criterion("profit")
            self.assertEqual(["000001", "000002", "000003"], visible_stock_codes())

            window._set_routine_tree_display_criterion("period")
            self.assertEqual(["000003", "000001", "000002"], visible_stock_codes())

    def test_routine_view_sorts_instances_without_valid_filter_by_button_clicks(self) -> None:
        instances = [
            self._instance("inst-a", "A 루틴"),
            self._instance("inst-b", "B 루틴"),
        ]
        current_stocks = {
            "inst-a": [
                {
                    "instance_id": "inst-a",
                    "stock_path": "fixture/a-current",
                    "stock_code": "000001",
                    "stock_name": "A현재",
                }
            ],
            "inst-b": [
                {
                    "instance_id": "inst-b",
                    "stock_path": "fixture/b-current",
                    "stock_code": "000002",
                    "stock_name": "B현재",
                }
            ],
        }
        historical_stocks = {
            "inst-a": [
                {
                    "instance_id": "inst-a",
                    "stock_path": "fixture/a-history",
                    "stock_code": "100001",
                    "stock_name": "A과거",
                    "is_historical": True,
                }
            ],
            "inst-b": [
                {
                    "instance_id": "inst-b",
                    "stock_path": "fixture/b-history",
                    "stock_code": "100002",
                    "stock_name": "B과거",
                    "is_historical": True,
                }
            ],
        }
        performance_by_path = {
            "fixture/a-current": {"realized_profit": 10.0, "trade_days": 1},
            "fixture/a-history": {"realized_profit": 100.0, "trade_days": 2},
            "fixture/b-current": {"realized_profit": 20.0, "trade_days": 1},
            "fixture/b-history": {"realized_profit": 200.0, "trade_days": 2},
        }
        window = self._window_harness()
        window._routine_instance_operation_counts = lambda: {
            instance.instance_id: {
                "registered": 1,
                "running": 0,
                "stopped": 1,
                "error": 0,
            }
            for instance in instances
        }
        window._current_stocks_by_instance = lambda: current_stocks
        window._historical_stocks_by_instance = lambda: historical_stocks
        window._routine_tree_stock_performance_source = lambda stock: {
            **performance_by_path[str(stock.get("stock_path", ""))],
            "profit_rate": 0.0,
            "average": None,
            "average_rate": None,
            "profit_factor": 0.0,
        }

        def visible_instance_ids() -> list[str]:
            return [
                window.routine_table.item(row, 0).data(setting_window.Qt.UserRole)[
                    "instance_id"
                ]
                for row in range(window.routine_table.rowCount())
                if not window.routine_table.isRowHidden(row)
                and window.routine_table.item(row, 0)
                .data(setting_window.Qt.UserRole)
                .get("row_kind")
                == "instance"
            ]

        with (
            patch.object(
                setting_window,
                "load_routine_definitions",
                return_value=[self._definition()],
            ),
            patch.object(
                setting_window,
                "load_persisted_routine_instances",
                return_value=instances,
            ),
        ):
            window.routine_box = setting_window.QGroupBox("자동매매 루틴")
            setting_window.QVBoxLayout(window.routine_box).addWidget(
                window.routine_table
            )
            window._setup_routine_tree_display_level_badges()
            window.load_routine_table()
            self.assertFalse(window._routine_tree_valid_only)

            window._routine_tree_display_level_buttons["routine"].click()
            window._routine_tree_display_scope_buttons["all"].click()
            window._routine_tree_display_criterion_buttons["profit"].click()
            self.assertEqual(["inst-b", "inst-a"], visible_instance_ids())

            window._routine_tree_display_scope_buttons["historical"].click()
            self.assertEqual(["inst-b", "inst-a"], visible_instance_ids())

            window._routine_tree_display_scope_buttons["current"].click()
            self.assertEqual(["inst-b", "inst-a"], visible_instance_ids())

            window._routine_tree_display_scope_buttons["all"].click()
            self.assertEqual(["inst-b", "inst-a"], visible_instance_ids())

    def test_routine_view_sorts_child_stocks_without_valid_filter_by_button_clicks(self) -> None:
        instances = [self._instance("inst-a", "A 루틴")]
        current_stocks = {
            "inst-a": [
                {
                    "instance_id": "inst-a",
                    "stock_path": "fixture/current-zero",
                    "stock_code": "000001",
                    "stock_name": "현재0",
                }
            ]
        }
        historical_stocks = {
            "inst-a": [
                {
                    "instance_id": "inst-a",
                    "stock_path": "fixture/history-positive",
                    "stock_code": "000002",
                    "stock_name": "과거수익",
                    "is_historical": True,
                },
                {
                    "instance_id": "inst-a",
                    "stock_path": "fixture/history-negative",
                    "stock_code": "000003",
                    "stock_name": "과거손실",
                    "is_historical": True,
                },
            ]
        }
        performance_by_path = {
            "fixture/current-zero": {"realized_profit": 0.0, "trade_days": 0},
            "fixture/history-positive": {
                "realized_profit": 125000.0,
                "trade_days": 3,
            },
            "fixture/history-negative": {
                "realized_profit": -48000.0,
                "trade_days": 2,
            },
        }
        window = self._window_harness()
        window._routine_instance_operation_counts = lambda: {
            "inst-a": {"registered": 1, "running": 0, "stopped": 1, "error": 0}
        }
        window._current_stocks_by_instance = lambda: current_stocks
        window._historical_stocks_by_instance = lambda: historical_stocks
        window._routine_tree_stock_performance_source = lambda stock: {
            **performance_by_path[str(stock.get("stock_path", ""))],
            "profit_rate": 0.0,
            "average": None,
            "average_rate": None,
            "profit_factor": 0.0,
        }

        def visible_stock_codes() -> list[str]:
            return [
                window.routine_table.item(row, 0).data(setting_window.Qt.UserRole)[
                    "stock_code"
                ]
                for row in range(window.routine_table.rowCount())
                if not window.routine_table.isRowHidden(row)
                and window.routine_table.item(row, 0)
                .data(setting_window.Qt.UserRole)
                .get("row_kind")
                == "stock"
            ]

        with (
            patch.object(
                setting_window,
                "load_routine_definitions",
                return_value=[self._definition()],
            ),
            patch.object(
                setting_window,
                "load_persisted_routine_instances",
                return_value=instances,
            ),
        ):
            window.routine_box = setting_window.QGroupBox("자동매매 루틴")
            setting_window.QVBoxLayout(window.routine_box).addWidget(
                window.routine_table
            )
            window._setup_routine_tree_display_level_badges()
            window.load_routine_table()
            self.assertFalse(window._routine_tree_valid_only)

            window._routine_tree_display_level_buttons["routine"].click()
            window._toggle_routine_instance_collapsed("inst-a")
            window._routine_tree_display_scope_buttons["all"].click()
            window._routine_tree_display_criterion_buttons["profit"].click()
            self.assertEqual(["000002", "000001", "000003"], visible_stock_codes())

            window._routine_tree_display_scope_buttons["historical"].click()
            self.assertEqual(["000002", "000003"], visible_stock_codes())

    def test_valid_routine_view_sorts_mixed_scope_child_stocks_by_profit(self) -> None:
        instances = [self._instance("inst-a", "A 루틴")]
        current_stocks = {
            "inst-a": [
                {
                    "instance_id": "inst-a",
                    "stock_path": "fixture/current-zero-a",
                    "stock_code": "000001",
                    "stock_name": "현재0A",
                },
                {
                    "instance_id": "inst-a",
                    "stock_path": "fixture/current-zero-b",
                    "stock_code": "000002",
                    "stock_name": "현재0B",
                },
            ]
        }
        historical_stocks = {
            "inst-a": [
                {
                    "instance_id": "inst-a",
                    "stock_path": "fixture/history-plus-c",
                    "stock_code": "000003",
                    "stock_name": "과거수익C",
                    "is_historical": True,
                },
                {
                    "instance_id": "inst-a",
                    "stock_path": "fixture/history-minus-d",
                    "stock_code": "000004",
                    "stock_name": "과거손실D",
                    "is_historical": True,
                },
                {
                    "instance_id": "inst-a",
                    "stock_path": "fixture/history-plus-e",
                    "stock_code": "000005",
                    "stock_name": "과거수익E",
                    "is_historical": True,
                },
            ]
        }
        performance_by_path = {
            "fixture/current-zero-a": {"realized_profit": 0.0, "trade_days": 0},
            "fixture/current-zero-b": {"realized_profit": 0.0, "trade_days": 0},
            "fixture/history-plus-c": {"realized_profit": 125000.0, "trade_days": 3},
            "fixture/history-minus-d": {"realized_profit": -48000.0, "trade_days": 2},
            "fixture/history-plus-e": {"realized_profit": 202000.0, "trade_days": 4},
        }
        window = self._window_harness()
        window._routine_instance_operation_counts = lambda: {
            "inst-a": {"registered": 2, "running": 0, "stopped": 2, "error": 0}
        }
        window._current_stocks_by_instance = lambda: current_stocks
        window._historical_stocks_by_instance = lambda: historical_stocks
        window._routine_tree_stock_performance_source = lambda stock: {
            **performance_by_path[str(stock.get("stock_path", ""))],
            "profit_rate": 0.0,
            "average": None,
            "average_rate": None,
            "profit_factor": 0.0,
        }

        def visible_stock_codes() -> list[str]:
            return [
                window.routine_table.item(row, 0).data(setting_window.Qt.UserRole)[
                    "stock_code"
                ]
                for row in range(window.routine_table.rowCount())
                if not window.routine_table.isRowHidden(row)
                and window.routine_table.item(row, 0)
                .data(setting_window.Qt.UserRole)
                .get("row_kind")
                == "stock"
            ]

        with (
            patch.object(
                setting_window,
                "load_routine_definitions",
                return_value=[self._definition()],
            ),
            patch.object(
                setting_window,
                "load_persisted_routine_instances",
                return_value=instances,
            ),
        ):
            window.load_routine_table()
            window._set_routine_tree_valid_only(True)
            window._set_routine_tree_display_level("routine")
            window._toggle_routine_instance_collapsed("inst-a")
            window._set_routine_tree_display_scope("all")
            window._set_routine_tree_display_criterion("profit")

            self.assertEqual(
                ["000005", "000003", "000001", "000002", "000004"],
                visible_stock_codes(),
            )

            window._set_routine_tree_display_scope("historical")
            self.assertEqual(["000005", "000003", "000004"], visible_stock_codes())

    def test_valid_category_view_sorts_definition_rows_by_selected_metric(self) -> None:
        definitions = [
            replace(self._definition(), definition_id="def-a", display_name="A 그룹"),
            replace(self._definition(), definition_id="def-b", display_name="B 그룹"),
            replace(self._definition(), definition_id="def-c", display_name="C 그룹"),
            replace(self._definition(), definition_id="def-d", display_name="D 그룹"),
        ]
        instances = [
            replace(self._instance("inst-a", "A 루틴"), definition_id="def-a"),
            replace(self._instance("inst-b", "B 루틴"), definition_id="def-b"),
            replace(self._instance("inst-c", "C 루틴"), definition_id="def-c"),
            replace(self._instance("inst-d", "D 루틴"), definition_id="def-d"),
        ]
        current_stocks = {
            instance.instance_id: [
                {
                    "instance_id": instance.instance_id,
                    "stock_path": f"fixture/{instance.instance_id}",
                    "stock_code": f"00000{index}",
                    "stock_name": f"{instance.display_name} 종목",
                }
            ]
            for index, instance in enumerate(instances, start=1)
        }
        performance_by_path = {
            "fixture/inst-a": {
                "trade_days": 3,
                "realized_profit": 10.0,
                "average": 1.0,
                "profit_factor": 2.0,
            },
            "fixture/inst-b": {
                "trade_days": 8,
                "realized_profit": 5.0,
                "average": 4.0,
                "profit_factor": 1.0,
            },
            "fixture/inst-c": {
                "trade_days": 1,
                "realized_profit": 20.0,
                "average": 3.0,
                "profit_factor": 4.0,
            },
            "fixture/inst-d": {
                "trade_days": None,
                "realized_profit": None,
                "average": None,
                "profit_factor": None,
            },
        }
        window = self._window_harness()
        window._routine_instance_operation_counts = lambda: {
            instance.instance_id: {
                "registered": 1,
                "running": 0,
                "stopped": 1,
                "error": 0,
            }
            for instance in instances
        }
        window._current_stocks_by_instance = lambda: current_stocks
        window._historical_stocks_by_instance = lambda: {}
        window._routine_tree_stock_performance_source = lambda stock: {
            **performance_by_path[str(stock.get("stock_path", ""))],
            "profit_rate": 0.0,
            "average_rate": 0.0,
            "is_current": True,
        }

        def visible_definition_ids() -> list[str]:
            return [
                window.routine_table.item(row, 0).data(setting_window.Qt.UserRole)[
                    "definition_id"
                ]
                for row in range(window.routine_table.rowCount())
                if not window.routine_table.isRowHidden(row)
                and window.routine_table.item(row, 0)
                .data(setting_window.Qt.UserRole)
                .get("row_kind")
                == "definition"
            ]

        with (
            patch.object(
                setting_window,
                "load_routine_definitions",
                return_value=definitions,
            ),
            patch.object(
                setting_window,
                "load_persisted_routine_instances",
                return_value=instances,
            ),
        ):
            window.load_routine_table()
            window._set_routine_tree_valid_only(True)
            window._set_routine_tree_display_level("category")

            expected_by_criterion = {
                "period": ["def-b", "def-a", "def-c", "def-d"],
                "profit": ["def-c", "def-a", "def-b", "def-d"],
                "average": ["def-b", "def-c", "def-a", "def-d"],
                "efficiency": ["def-c", "def-a", "def-b", "def-d"],
            }
            for criterion, expected in expected_by_criterion.items():
                window._set_routine_tree_display_criterion(criterion)
                self.assertEqual(expected, visible_definition_ids())

    def test_valid_routine_sort_uses_selected_scope_before_profit_order(self) -> None:
        instances = [
            self._instance("inst-a", "A 루틴"),
            self._instance("inst-b", "B 루틴"),
        ]
        current_stocks = {
            "inst-a": [
                {
                    "instance_id": "inst-a",
                    "stock_path": "fixture/a-current",
                    "stock_code": "000001",
                    "stock_name": "A현재",
                }
            ],
            "inst-b": [
                {
                    "instance_id": "inst-b",
                    "stock_path": "fixture/b-current",
                    "stock_code": "000002",
                    "stock_name": "B현재",
                }
            ],
        }
        historical_stocks = {
            "inst-a": [
                {
                    "instance_id": "inst-a",
                    "stock_path": "fixture/a-history",
                    "stock_code": "100001",
                    "stock_name": "A과거",
                    "is_historical": True,
                }
            ],
            "inst-b": [
                {
                    "instance_id": "inst-b",
                    "stock_path": "fixture/b-history",
                    "stock_code": "100002",
                    "stock_name": "B과거",
                    "is_historical": True,
                }
            ],
        }
        performance_by_path = {
            "fixture/a-current": {
                "trade_days": 1,
                "realized_profit": 10.0,
                "average": 10.0,
                "profit_factor": 1.0,
            },
            "fixture/b-current": {
                "trade_days": 1,
                "realized_profit": 5.0,
                "average": 5.0,
                "profit_factor": 1.0,
            },
            "fixture/a-history": {
                "trade_days": 1,
                "realized_profit": -100.0,
                "average": -100.0,
                "profit_factor": 0.0,
            },
            "fixture/b-history": {
                "trade_days": 1,
                "realized_profit": 100.0,
                "average": 100.0,
                "profit_factor": 2.0,
            },
        }
        window = self._window_harness()
        window._routine_instance_operation_counts = lambda: {
            "inst-a": {"registered": 1, "running": 0, "stopped": 1, "error": 0},
            "inst-b": {"registered": 1, "running": 0, "stopped": 1, "error": 0},
        }
        window._current_stocks_by_instance = lambda: current_stocks
        window._historical_stocks_by_instance = lambda: historical_stocks
        window._routine_tree_stock_performance_source = lambda stock: {
            **performance_by_path[str(stock.get("stock_path", ""))],
            "profit_rate": 0.0,
            "average_rate": 0.0,
            "is_current": not bool(stock.get("is_historical", False)),
        }

        def visible_instance_ids() -> list[str]:
            return [
                window.routine_table.item(row, 0).data(setting_window.Qt.UserRole)[
                    "instance_id"
                ]
                for row in range(window.routine_table.rowCount())
                if not window.routine_table.isRowHidden(row)
                and window.routine_table.item(row, 0)
                .data(setting_window.Qt.UserRole)
                .get("row_kind")
                == "instance"
            ]

        with (
            patch.object(
                setting_window,
                "load_routine_definitions",
                return_value=[self._definition()],
            ),
            patch.object(
                setting_window,
                "load_persisted_routine_instances",
                return_value=instances,
            ),
        ):
            window.load_routine_table()
            window._set_routine_tree_valid_only(True)
            window._set_routine_tree_display_level("routine")
            window._set_routine_tree_display_criterion("profit")
            window._toggle_routine_instance_collapsed("inst-a")
            window._toggle_routine_instance_collapsed("inst-b")

            window._set_routine_tree_display_scope("current")
            self.assertEqual(["inst-a", "inst-b"], visible_instance_ids())

            window._set_routine_tree_display_scope("historical")
            self.assertEqual(["inst-b", "inst-a"], visible_instance_ids())

            window._set_routine_tree_display_scope("all")
            self.assertEqual(["inst-b", "inst-a"], visible_instance_ids())

    def test_valid_stock_scope_groups_current_and_historical_records_by_stock_code(
        self,
    ) -> None:
        instances = [
            self._instance("inst-a", "루틴 A"),
            self._instance("inst-b", "루틴 B"),
            self._instance("inst-c", "루틴 C"),
        ]
        current_stocks = {
            "inst-a": [
                {
                    "stock_path": "fixture/current-samsung-a",
                    "stock_code": "005930",
                    "stock_name": "삼성전자",
                },
            ],
            "inst-b": [],
            "inst-c": [],
        }
        historical_stocks = {
            "inst-a": [],
            "inst-b": [
                {
                    "instance_id": "inst-b",
                    "stock_path": "fixture/history-samsung-b",
                    "stock_code": "005930",
                    "stock_name": "삼성전자",
                    "is_historical": True,
                },
            ],
            "inst-c": [
                {
                    "instance_id": "inst-c",
                    "stock_path": "fixture/history-samsung-c",
                    "stock_code": "005930",
                    "stock_name": "삼성전자",
                    "is_historical": True,
                },
            ],
        }
        performance_by_path = {
            "fixture/current-samsung-a": {
                "trade_days": 3,
                "realized_profit": 300000.0,
                "average": 100000.0,
                "average_rate": 3.0,
                "profit_factor": 3.0,
                "is_current": True,
            },
            "fixture/history-samsung-b": {
                "trade_days": 2,
                "realized_profit": 100000.0,
                "average": 50000.0,
                "average_rate": 1.0,
                "profit_factor": 2.0,
                "is_current": False,
            },
            "fixture/history-samsung-c": {
                "trade_days": 1,
                "realized_profit": -50000.0,
                "average": -50000.0,
                "average_rate": -1.0,
                "profit_factor": 0.0,
                "is_current": False,
            },
        }
        window = self._window_harness()
        window._routine_instance_operation_counts = lambda: {
            "inst-a": {"registered": 1, "running": 0, "stopped": 1, "error": 0},
            "inst-b": {"registered": 0, "running": 0, "stopped": 0, "error": 0},
            "inst-c": {"registered": 0, "running": 0, "stopped": 0, "error": 0},
        }
        window._current_stocks_by_instance = lambda: current_stocks
        window._historical_stocks_by_instance = lambda: historical_stocks
        window._routine_tree_stock_performance_source = (
            lambda stock: dict(performance_by_path[str(stock.get("stock_path", ""))])
        )

        def visible_stock_rows() -> list[dict[str, object]]:
            return [
                window.routine_table.item(row, 0).data(setting_window.Qt.UserRole)
                for row in range(window.routine_table.rowCount())
                if not window.routine_table.isRowHidden(row)
                and window.routine_table.item(row, 0)
                .data(setting_window.Qt.UserRole)
                .get("row_kind") == "stock"
            ]

        with (
            patch.object(
                setting_window,
                "load_routine_definitions",
                return_value=[self._definition()],
            ),
            patch.object(
                setting_window,
                "load_persisted_routine_instances",
                return_value=instances,
            ),
        ):
            window.load_routine_table()
            window._set_routine_tree_valid_only(True)
            window._set_routine_tree_display_level("stock")

            window._set_routine_tree_display_scope("current")
            rows = visible_stock_rows()
            self.assertEqual(1, len(rows))
            self.assertEqual("005930", rows[0]["stock_code"])
            self.assertFalse(rows[0]["is_historical"])
            self.assertEqual("+300,000", rows[0]["performance_profit_amount"])
            self.assertEqual("3", rows[0]["performance_period_value"])

            window._set_routine_tree_display_scope("historical")
            rows = visible_stock_rows()
            self.assertEqual(1, len(rows))
            self.assertTrue(rows[0]["is_historical"])
            self.assertEqual("+50,000", rows[0]["performance_profit_amount"])
            self.assertEqual("3", rows[0]["performance_period_value"])

            window._set_routine_tree_display_scope("all")
            rows = visible_stock_rows()
            self.assertEqual(1, len(rows))
            self.assertFalse(rows[0]["is_historical"])
            self.assertEqual("+350,000", rows[0]["performance_profit_amount"])
            self.assertEqual("6", rows[0]["performance_period_value"])
            self.assertEqual("+58,333.33", rows[0]["performance_average_amount"])

    def test_valid_stock_view_final_tree_shows_only_stock_representatives(self) -> None:
        instances = [
            self._instance("inst-a", "루틴 A"),
            self._instance("inst-b", "루틴 B"),
        ]
        current_stocks = {
            "inst-a": [
                {
                    "instance_id": "inst-a",
                    "stock_path": "fixture/current-samsung",
                    "stock_code": "005930",
                    "stock_name": "삼성전자",
                },
            ],
            "inst-b": [],
        }
        historical_stocks = {
            "inst-a": [],
            "inst-b": [
                {
                    "instance_id": "inst-b",
                    "stock_path": "fixture/history-samsung",
                    "stock_code": "005930",
                    "stock_name": "삼성전자",
                    "is_historical": True,
                },
                {
                    "instance_id": "inst-b",
                    "stock_path": "fixture/history-hyundai",
                    "stock_code": "005380",
                    "stock_name": "현대차",
                    "is_historical": True,
                },
            ],
        }
        performance_by_path = {
            "fixture/current-samsung": {"realized_profit": 0.0, "trade_days": 0},
            "fixture/history-samsung": {"realized_profit": 125000.0, "trade_days": 3},
            "fixture/history-hyundai": {"realized_profit": -48000.0, "trade_days": 2},
        }
        window = self._window_harness()
        window._routine_instance_operation_counts = lambda: {
            "inst-a": {"registered": 1, "running": 0, "stopped": 1, "error": 0},
            "inst-b": {"registered": 0, "running": 0, "stopped": 0, "error": 0},
        }
        window._current_stocks_by_instance = lambda: current_stocks
        window._historical_stocks_by_instance = lambda: historical_stocks
        window._routine_tree_stock_performance_source = lambda stock: {
            **performance_by_path[str(stock.get("stock_path", ""))],
            "profit_rate": 0.0,
            "average": None,
            "average_rate": None,
            "profit_factor": 0.0,
        }

        def visible_rows() -> list[dict[str, object]]:
            return [
                window.routine_table.item(row, 0).data(setting_window.Qt.UserRole)
                for row in range(window.routine_table.rowCount())
                if not window.routine_table.isRowHidden(row)
            ]

        with (
            patch.object(
                setting_window,
                "load_routine_definitions",
                return_value=[self._definition()],
            ),
            patch.object(
                setting_window,
                "load_persisted_routine_instances",
                return_value=instances,
            ),
        ):
            window.load_routine_table()
            window._set_routine_tree_valid_only(True)
            window._set_routine_tree_display_level("stock")
            window._set_routine_tree_display_scope("all")
            window._set_routine_tree_display_criterion("profit")

            rows = visible_rows()
            self.assertTrue(rows)
            self.assertTrue(all(row.get("row_kind") == "stock" for row in rows))
            self.assertEqual(["005930", "005380"], [row["stock_code"] for row in rows])
            self.assertEqual("+125,000", rows[0]["performance_profit_amount"])
            self.assertFalse(rows[0]["is_historical"])

    def test_stock_scope_filters_stock_rows_without_hiding_registered_parents(
        self,
    ) -> None:
        instances = [
            self._instance("inst-a", "과거보유루틴"),
            self._instance("inst-b", "과거없는루틴"),
        ]
        current_stocks = {
            "inst-a": [
                {
                    "stock_path": "fixture/current-samsung",
                    "stock_code": "005930",
                    "stock_name": "삼성전자",
                },
            ],
            "inst-b": [
                {
                    "stock_path": "fixture/current-hyundai",
                    "stock_code": "005380",
                    "stock_name": "현대차",
                },
            ],
        }
        historical_stocks = {
            "inst-a": [
                {
                    "instance_id": "inst-a",
                    "stock_path": "fixture/history-naver",
                    "stock_code": "035420",
                    "stock_name": "NAVER",
                    "is_historical": True,
                },
            ],
            "inst-b": [],
        }
        window = self._window_harness()
        window._routine_instance_operation_counts = lambda: {
            "inst-a": {"registered": 1, "running": 0, "stopped": 1, "error": 0},
            "inst-b": {"registered": 1, "running": 0, "stopped": 1, "error": 0},
        }
        window._current_stocks_by_instance = lambda: current_stocks
        window._historical_stocks_by_instance = lambda: historical_stocks

        def visible_rows() -> list[dict[str, object]]:
            return [
                window.routine_table.item(row, 0).data(setting_window.Qt.UserRole)
                for row in range(window.routine_table.rowCount())
                if not window.routine_table.isRowHidden(row)
            ]

        with (
            patch.object(
                setting_window,
                "load_routine_definitions",
                return_value=[self._definition()],
            ),
            patch.object(
                setting_window,
                "load_persisted_routine_instances",
                return_value=instances,
            ),
        ):
            window.load_routine_table()
            window._set_routine_tree_display_level("stock")

            for scope in ("all", "current", "historical", "all"):
                window._set_routine_tree_display_scope(scope)
                rows = visible_rows()
                self.assertIn(
                    "지표추종매매",
                    [str(row.get("display_name", "")) for row in rows],
                )
                self.assertIn(
                    "과거보유루틴",
                    [str(row.get("display_name", "")) for row in rows],
                )
                self.assertIn(
                    "과거없는루틴",
                    [str(row.get("display_name", "")) for row in rows],
                )
            window._set_routine_tree_display_scope("historical")
            self.assertEqual(
                ["035420"],
                [
                    str(row.get("stock_code", ""))
                    for row in visible_rows()
                    if row.get("row_kind") == "stock"
                ],
            )

    def test_historical_stock_context_menu_offers_register_convert_before_display_delete(self) -> None:
        window = self._window_harness()
        window.routine_table.setRowCount(1)
        item = QTableWidgetItem()
        item.setData(
            Qt.UserRole,
            {
                "row_kind": "stock",
                "definition_id": "indicator_follow",
                "definition_name": "지표추종매매",
                "instance_id": "inst-a",
                "instance_name": "A 인스턴스",
                "stock_code": "005930",
                "display_name": "삼성전자",
                "is_historical": True,
            },
        )
        window.routine_table.setItem(0, 0, item)
        menu = MagicMock()
        actions = [MagicMock(), MagicMock()]
        callbacks = []
        for action in actions:
            action.triggered.connect.side_effect = callbacks.append
        menu.addAction.side_effect = actions
        with (
            patch.object(window.routine_table, "itemAt", return_value=item),
            patch.object(setting_window, "QMenu", return_value=menu),
            patch.object(window, "convert_historical_stock_to_registered") as convert,
            patch.object(window, "hide_historical_stock_display") as hide_display,
        ):
            window.on_routine_table_context_menu(QPoint(1, 1))
            callbacks[0](False)
            callbacks[1](False)

        self.assertEqual(
            ["등록전환", "표시삭제"],
            [call.args[0] for call in menu.addAction.call_args_list],
        )
        convert.assert_called_once_with(item.data(Qt.UserRole))
        hide_display.assert_called_once_with(item.data(Qt.UserRole))

    def test_current_stock_context_menu_does_not_offer_display_delete(self) -> None:
        window = self._window_harness()
        window.routine_table.setRowCount(1)
        item = QTableWidgetItem()
        item.setData(
            Qt.UserRole,
            {
                "row_kind": "stock",
                "instance_id": "inst-a",
                "stock_code": "005930",
                "display_name": "삼성전자",
                "is_historical": False,
            },
        )
        window.routine_table.setItem(0, 0, item)
        with (
            patch.object(window.routine_table, "itemAt", return_value=item),
            patch.object(setting_window, "QMenu") as menu_class,
        ):
            window.on_routine_table_context_menu(QPoint(1, 1))

        menu_class.assert_not_called()

    def test_historical_stock_register_convert_uses_original_instance_backend(self) -> None:
        parent = QWidget()
        self.addCleanup(parent.close)
        parent.refresh_all = MagicMock()
        metadata = {
            "row_kind": "stock",
            "definition_id": "indicator_follow",
            "definition_name": "지표추종매매",
            "instance_id": "inst-a",
            "instance_name": "A 인스턴스",
            "stock_code": "005930",
            "display_name": "삼성전자",
            "is_historical": True,
        }
        stock_dir = Path("stocks") / "005930_삼성전자"
        repository = MagicMock()
        repository.resolve_stock_dir.return_value = stock_dir
        repository.ensure_stock_folder.return_value = stock_dir

        with (
            patch.object(setting_window, "load_stock_library", return_value=[]),
            patch.object(setting_window, "find_library_stock_by_code", return_value={"code": "005930", "name": "삼성전자"}),
            patch.object(setting_window, "read_base_stocks", return_value=[]),
            patch.object(setting_window, "routine_action_reasons_for_stock", return_value=(True, {"reasons": []})) as policy,
            patch.object(setting_window.QMessageBox, "question") as question,
            patch.object(setting_window, "append_base_stock", return_value=True) as append_stock,
            patch.object(setting_window, "update_base_stock_routine_instance", return_value=True) as update_instance,
            patch.object(setting_window, "StockRepository", return_value=repository),
            patch.object(setting_window, "ensure_single_real_trade_routine_for_stock") as ensure_routine,
            patch.object(setting_window, "append_changelog"),
            patch("gui_routine_assign_window.apply_default_operation_exclusion_for_new_running_assignment"),
            patch.object(setting_window, "show_toast") as toast,
        ):
            self.assertTrue(
                setting_window.auto_trade_register_historical_stock_to_original_instance(
                    parent,
                    metadata,
                )
            )

        question.assert_not_called()
        policy.assert_called_once_with("005930", "삼성전자", allow_unassigned=True)
        append_stock.assert_called_once_with("005930", "삼성전자")
        update_instance.assert_called_once_with(
            "005930",
            "삼성전자",
            instance_id="inst-a",
            instance_name="A 인스턴스",
            definition_id="indicator_follow",
            routine_type="지표추종매매",
        )
        ensure_routine.assert_called_once_with("005930", "삼성전자", "지표추종매매")
        parent.refresh_all.assert_called_once_with()
        toast.assert_called_with(parent, "등록 1건 | 삼성전자")

    def test_historical_stock_register_convert_skips_same_routine_duplicate(self) -> None:
        parent = QWidget()
        self.addCleanup(parent.close)
        parent.refresh_all = MagicMock()
        metadata = {
            "definition_id": "indicator_follow",
            "definition_name": "지표추종매매",
            "instance_id": "inst-a",
            "instance_name": "A 인스턴스",
            "stock_code": "005930",
            "display_name": "삼성전자",
            "is_historical": True,
        }

        with (
            patch.object(setting_window, "load_stock_library", return_value=[]),
            patch.object(setting_window, "find_library_stock_by_code", return_value={"code": "005930", "name": "삼성전자"}),
            patch.object(
                setting_window,
                "read_base_stocks",
                return_value=[
                    {
                        "code": "005930",
                        "name": "삼성전자",
                        "assigned_routine_instance_id": "inst-a",
                    }
                ],
            ),
            patch.object(setting_window, "update_base_stock_routine_instance") as update_instance,
            patch.object(setting_window, "show_toast") as toast,
        ):
            self.assertFalse(
                setting_window.auto_trade_register_historical_stock_to_original_instance(
                    parent,
                    metadata,
                )
            )

        update_instance.assert_not_called()
        parent.refresh_all.assert_not_called()
        toast.assert_called_with(parent, "이미 같은 인스턴스에 지정된 종목입니다.")

    def test_historical_stock_register_convert_respects_review_required_policy(self) -> None:
        parent = QWidget()
        self.addCleanup(parent.close)
        parent.refresh_all = MagicMock()
        metadata = {
            "definition_id": "indicator_follow",
            "definition_name": "지표추종매매",
            "instance_id": "inst-a",
            "instance_name": "A 인스턴스",
            "stock_code": "005930",
            "display_name": "삼성전자",
            "is_historical": True,
        }

        with (
            patch.object(setting_window, "load_stock_library", return_value=[]),
            patch.object(setting_window, "find_library_stock_by_code", return_value={"code": "005930", "name": "삼성전자"}),
            patch.object(setting_window, "read_base_stocks", return_value=[]),
            patch.object(
                setting_window,
                "routine_action_reasons_for_stock",
                return_value=(False, {"reasons": ["처리할 수 없는 종목입니다.\n검토관리에서 확인하세요."]}),
            ),
            patch.object(setting_window, "update_base_stock_routine_instance") as update_instance,
            patch.object(setting_window, "show_toast") as toast,
        ):
            self.assertFalse(
                setting_window.auto_trade_register_historical_stock_to_original_instance(
                    parent,
                    metadata,
                )
            )

        update_instance.assert_not_called()
        parent.refresh_all.assert_not_called()
        toast.assert_called_with(parent, "처리할 수 없는 종목입니다.\n검토관리에서 확인하세요.")

    def test_development_historical_fixture_is_default_and_nonpersistent(self) -> None:
        window = self._window_harness()
        instances = [
            self._instance("inst-a", "A 인스턴스"),
            self._instance("inst-b", "B 인스턴스"),
        ]
        window._current_stocks_by_instance = lambda: {
            "inst-a": [
                {
                    "stock_code": "000660",
                    "stock_name": "SK하이닉스",
                    "stock_path": "stocks/000660_SK하이닉스",
                }
            ]
        }
        repository = MagicMock()
        repository.list_routine_assignment_history.return_value = []

        with (
            patch.object(setting_window, "StockRepository", return_value=repository),
            patch.object(
                setting_window,
                "load_persisted_routine_instances",
                return_value=instances,
            ),
            patch.dict(
                os.environ,
                {},
            ),
        ):
            os.environ.pop(setting_window.AUTO_TRADE_SETTING_APP_ENV, None)
            os.environ.pop(
                setting_window.AUTO_TRADE_SETTING_HISTORICAL_STOCK_FIXTURE_ENV,
                None,
            )
            historical = window._historical_stocks_by_instance()
            original_inst_a_count = len(historical["inst-a"])
            original_inst_b_count = len(historical["inst-b"])
            hidden_code = str(historical["inst-a"][0]["stock_code"])
            window._hidden_historical_stock_fixture_keys.add(
                ("inst-a", hidden_code)
            )
            after_display_delete = window._historical_stocks_by_instance()

        self.assertEqual({"inst-a", "inst-b"}, set(historical))
        self.assertTrue(all(len(stocks) >= 5 for stocks in historical.values()))
        self.assertNotIn(
            "000660",
            {stock["stock_code"] for stock in historical["inst-a"]},
        )
        self.assertTrue(
            all(
                stock["is_historical"] and stock["is_development_fixture"]
                for stocks in historical.values()
                for stock in stocks
            )
        )
        self.assertTrue(
            all(
                float(stock["performance_fixture"]["profit_factor"]) >= 0.0
                and "gross_profit" in stock["performance_fixture"]
                and "gross_loss_abs" in stock["performance_fixture"]
                for stocks in historical.values()
                for stock in stocks
            )
        )
        fixture_profits = {
            stock["performance_fixture"]["realized_profit"]
            for stocks in historical.values()
            for stock in stocks
        }
        self.assertTrue({125000.0, -48000.0, 0.0}.issubset(fixture_profits))
        self.assertEqual(original_inst_a_count - 1, len(after_display_delete["inst-a"]))
        self.assertNotIn(
            hidden_code,
            {stock["stock_code"] for stock in after_display_delete["inst-a"]},
        )
        self.assertEqual(original_inst_b_count, len(after_display_delete["inst-b"]))
        self.assertEqual(2, repository.list_routine_assignment_history.call_count)
        repository.hide_routine_assignment_history.assert_not_called()

    def test_manual_aggregation_fixture_keeps_stock_name_consistent_by_code(self) -> None:
        window = self._window_harness()
        instances = [
            self._instance("inst-a", "A 인스턴스"),
            self._instance("inst-b", "B 인스턴스"),
            self._instance("inst-c", "C 인스턴스"),
        ]
        window._current_stocks_by_instance = lambda: {
            "inst-a": [
                {
                    "stock_code": "000660",
                    "stock_name": "SK하이닉스",
                    "stock_path": "stocks/000660_SK하이닉스",
                }
            ]
        }
        repository = MagicMock()
        repository.list_routine_assignment_history.return_value = []

        with (
            patch.object(setting_window, "StockRepository", return_value=repository),
            patch.object(
                setting_window,
                "load_persisted_routine_instances",
                return_value=instances,
            ),
            patch.dict(os.environ, {}, clear=False),
        ):
            os.environ.pop(setting_window.AUTO_TRADE_SETTING_APP_ENV, None)
            os.environ.pop(
                setting_window.AUTO_TRADE_SETTING_HISTORICAL_STOCK_FIXTURE_ENV,
                None,
            )
            historical = window._historical_stocks_by_instance()

        names_by_code: dict[str, set[str]] = {}
        for stocks in historical.values():
            for stock in stocks:
                stock_code = str(stock["stock_code"])
                names_by_code.setdefault(stock_code, set()).add(str(stock["stock_name"]))

        self.assertTrue(
            all(len(names) == 1 for names in names_by_code.values()),
            names_by_code,
        )
        sk_hynix_records = [
            stock
            for stocks in historical.values()
            for stock in stocks
            if stock["stock_code"] == "000660"
        ]
        self.assertEqual(2, len(sk_hynix_records))
        self.assertEqual(
            {"SK하이닉스"},
            {stock["stock_name"] for stock in sk_hynix_records},
        )
        self.assertEqual(
            [48000.0, 77000.0],
            sorted(
                stock["performance_fixture"]["realized_profit"]
                for stock in sk_hynix_records
            ),
        )
        self.assertEqual(
            [2, 5],
            sorted(
                stock["performance_fixture"]["trade_days"]
                for stock in sk_hynix_records
            ),
        )
        self.assertEqual(
            {"inst-b", "inst-c"},
            {stock["instance_id"] for stock in sk_hynix_records},
        )

    def test_historical_fixture_is_always_disabled_in_production(self) -> None:
        window = self._window_harness()
        repository = MagicMock()
        repository.list_routine_assignment_history.return_value = []

        with (
            patch.object(setting_window, "StockRepository", return_value=repository),
            patch.dict(
                os.environ,
                {
                    setting_window.AUTO_TRADE_SETTING_APP_ENV: "production",
                    setting_window.AUTO_TRADE_SETTING_HISTORICAL_STOCK_FIXTURE_ENV: "1",
                },
            ),
        ):
            self.assertEqual({}, window._historical_stocks_by_instance())

    def test_development_historical_fixture_can_be_disabled_once(self) -> None:
        with patch.dict(
            os.environ,
            {
                setting_window.AUTO_TRADE_SETTING_APP_ENV: "development",
                setting_window.AUTO_TRADE_SETTING_HISTORICAL_STOCK_FIXTURE_ENV: "0",
            },
        ):
            self.assertFalse(
                setting_window.auto_trade_setting_historical_fixture_enabled()
            )

    def test_historical_stock_display_delete_only_updates_history_visibility(self) -> None:
        window = self._window_harness()
        window.load_routine_table = MagicMock()
        repository = MagicMock()
        repository.hide_routine_assignment_history.return_value = True
        metadata = {
            "row_kind": "stock",
            "instance_id": "inst-a",
            "stock_code": "005930",
            "display_name": "삼성전자",
            "is_historical": True,
        }
        with (
            patch.object(
                setting_window.QMessageBox,
                "question",
                return_value=setting_window.QMessageBox.Yes,
            ),
            patch.object(setting_window, "StockRepository", return_value=repository),
        ):
            window.hide_historical_stock_display(metadata)

        repository.hide_routine_assignment_history.assert_called_once_with(
            code="005930",
            instance_id="inst-a",
        )
        window.load_routine_table.assert_called_once_with()

    def test_development_fixture_display_delete_is_session_only(self) -> None:
        window = self._window_harness()
        window.load_routine_table = MagicMock()
        repository = MagicMock()
        metadata = {
            "row_kind": "stock",
            "instance_id": "inst-a",
            "stock_code": "005490",
            "display_name": "POSCO홀딩스",
            "is_historical": True,
            "is_development_fixture": True,
        }
        with (
            patch.object(
                setting_window.QMessageBox,
                "question",
                return_value=setting_window.QMessageBox.Yes,
            ),
            patch.object(setting_window, "StockRepository", return_value=repository),
        ):
            window.hide_historical_stock_display(metadata)

        self.assertEqual(
            {("inst-a", "005490")},
            window._hidden_historical_stock_fixture_keys,
        )
        repository.hide_routine_assignment_history.assert_not_called()
        window.load_routine_table.assert_called_once_with()

    def test_historical_stock_display_delete_cancel_is_noop(self) -> None:
        window = self._window_harness()
        window.load_routine_table = MagicMock()
        repository = MagicMock()
        metadata = {
            "row_kind": "stock",
            "instance_id": "inst-a",
            "stock_code": "005930",
            "display_name": "삼성전자",
            "is_historical": True,
        }
        with (
            patch.object(
                setting_window.QMessageBox,
                "question",
                return_value=setting_window.QMessageBox.No,
            ),
            patch.object(setting_window, "StockRepository", return_value=repository),
        ):
            window.hide_historical_stock_display(metadata)

        repository.hide_routine_assignment_history.assert_not_called()
        window.load_routine_table.assert_not_called()

    def test_instance_arrow_click_collapses_stock_rows_independently(self) -> None:
        instances = [self._instance("inst-a", "A 인스턴스"), self._instance("inst-b", "B 인스턴스")]
        stocks = [
            {
                "stock_path": "stocks/005930_A",
                "assigned_routine_instance_id": "inst-a",
                "code": "005930",
                "name": "삼성전자",
            },
            {
                "stock_path": "stocks/005380_B",
                "assigned_routine_instance_id": "inst-b",
                "code": "005380",
                "name": "현대차",
            },
        ]
        window = self._window_harness()
        window._routine_instance_operation_counts = lambda: {
            "inst-a": {"registered": 1, "running": 0, "stopped": 1, "error": 0},
            "inst-b": {"registered": 1, "running": 1, "stopped": 0, "error": 0},
        }

        with patch.object(setting_window, "load_routine_definitions", return_value=[self._definition()]), \
                patch.object(setting_window, "load_persisted_routine_instances", return_value=instances), \
                patch.object(setting_window, "read_base_stocks", return_value=stocks):
            window.load_routine_table()
            window._set_routine_tree_display_level("stock")

            self.assertEqual(
                ["definition", "instance", "stock", "instance", "stock"],
                [
                    window.routine_table.item(row, 0).data(setting_window.Qt.UserRole)["row_kind"]
                    for row in range(window.routine_table.rowCount())
                ],
            )
            first_icon = window.routine_table.cellWidget(1, 0).findChild(
                setting_window.QLabel,
                "autoTradeSettingRoutineTreeIcon",
            )
            self.assertEqual("▼", first_icon.text())
            first_instance_meta = window.routine_table.item(1, 0).data(setting_window.Qt.UserRole)
            second_instance_meta = window.routine_table.item(3, 0).data(setting_window.Qt.UserRole)
            self.assertFalse(bool(first_instance_meta.get("instance_group_top_gap")))
            self.assertTrue(bool(second_instance_meta.get("instance_group_top_gap")))
            self.assertEqual(
                setting_window.AUTO_TRADE_SETTING_INSTANCE_GROUP_TOP_GAP,
                window.routine_table.rowHeight(3) - window.routine_table.rowHeight(1),
            )
            self.assertEqual(
                window.routine_table.rowHeight(2),
                window.routine_table.rowHeight(4),
            )
            self.assertEqual(
                setting_window.AUTO_TRADE_SETTING_INSTANCE_GROUP_TOP_GAP,
                window.routine_table.rowHeight(2)
                - setting_window.AUTO_TRADE_SETTING_STOCK_ROW_HEIGHT,
            )

            window.routine_table.selectRow(1)
            selected_before_toggle = []
            window.load_selected_routine_stocks = lambda: selected_before_toggle.append(window._stock_status_filter)
            original_load_routine_table = window.load_routine_table
            window.load_routine_table = lambda: self.fail("instance collapse must not rebuild the routine table")
            window._toggle_routine_instance_collapsed("inst-a")
            window.load_routine_table = original_load_routine_table

        self.assertEqual({"inst-a"}, window._collapsed_auto_trade_instance_ids)
        self.assertEqual([], selected_before_toggle)
        row_metadata = [
            window.routine_table.item(row, 0).data(setting_window.Qt.UserRole)
            for row in range(window.routine_table.rowCount())
        ]
        self.assertEqual(
            [
                ("definition", ""),
                ("instance", "inst-a"),
                ("stock", "inst-a"),
                ("instance", "inst-b"),
                ("stock", "inst-b"),
            ],
            [(str(meta["row_kind"]), str(meta.get("instance_id", ""))) for meta in row_metadata],
        )
        self.assertFalse(window.routine_table.isRowHidden(1))
        self.assertTrue(window.routine_table.isRowHidden(2))
        self.assertFalse(window.routine_table.isRowHidden(3))
        self.assertFalse(window.routine_table.isRowHidden(4))
        collapsed_icon = window.routine_table.cellWidget(1, 0).findChild(
            setting_window.QLabel,
            "autoTradeSettingRoutineTreeIcon",
        )
        self.assertEqual("▶", collapsed_icon.text())
        self.assertEqual("inst-a", window.current_selected_instance_id())
        self.assertEqual(("inst-a",), window.current_selected_target_instance_ids())
        window.on_routine_table_item_double_clicked(window.routine_table.item(1, 0))
        self.assertEqual({"inst-a"}, window._collapsed_auto_trade_instance_ids)

        window._toggle_routine_definition_collapsed("indicator_follow")
        self.assertEqual({"indicator_follow"}, window._collapsed_auto_trade_definition_ids)
        self.assertTrue(window.routine_table.isRowHidden(1))
        self.assertTrue(window.routine_table.isRowHidden(2))
        self.assertTrue(window.routine_table.isRowHidden(3))
        self.assertTrue(window.routine_table.isRowHidden(4))
        window._toggle_routine_definition_collapsed("indicator_follow")
        self.assertEqual(set(), window._collapsed_auto_trade_definition_ids)
        self.assertFalse(window.routine_table.isRowHidden(1))
        self.assertTrue(window.routine_table.isRowHidden(2))
        self.assertFalse(window.routine_table.isRowHidden(3))
        self.assertFalse(window.routine_table.isRowHidden(4))

        with patch.object(setting_window, "load_routine_definitions", return_value=[self._definition()]), \
                patch.object(setting_window, "load_persisted_routine_instances", return_value=instances), \
                patch.object(setting_window, "read_base_stocks", return_value=stocks):
            window._set_routine_tree_display_level("stock")
            window._set_routine_tree_display_scope("all")

        self.assertEqual(set(), window._collapsed_auto_trade_instance_ids)
        stock_row = window.routine_table.item(2, 0).data(setting_window.Qt.UserRole)
        self.assertEqual("all", stock_row["display_scope"])
        self.assertFalse(window.routine_table.isRowHidden(1))
        self.assertFalse(window.routine_table.isRowHidden(2))

        with patch.object(setting_window, "load_routine_definitions", return_value=[self._definition()]), \
                patch.object(setting_window, "load_persisted_routine_instances", return_value=instances), \
                patch.object(setting_window, "read_base_stocks", return_value=stocks):
            window._toggle_routine_instance_collapsed("inst-a")

        self.assertEqual({"inst-a"}, window._collapsed_auto_trade_instance_ids)
        self.assertEqual(5, window.routine_table.rowCount())

    def test_stock_status_filter_limits_loaded_stock_rows_by_existing_status_rules(self) -> None:
        class Window:
            pass

        window = Window()
        window.stock_table = QTableWidget(0, 11)
        window.current_selected_target_instance_ids = lambda: ("inst-a",)
        window.current_selected_routine_dir = lambda: Path("routines") / "indicator_follow"
        window.current_selected_routine_name = lambda: "지표추종매매"
        window.capture_stock_table_view_state = lambda: (set(), 0)
        window.restore_stock_table_view_state = lambda _paths, _scroll: None
        window.update_selected_routine_status_bar = lambda: None
        window.update_action_buttons = lambda: None
        window._stock_visual_order = []

        stocks = [
            {"stock_path": "stocks/111111_RUN", "assigned_routine_instance_id": "inst-a", "code": "111111", "name": "정상1"},
            {"stock_path": "stocks/222222_STOP", "assigned_routine_instance_id": "inst-a", "code": "222222", "name": "제외"},
            {"stock_path": "stocks/333333_NORMAL", "assigned_routine_instance_id": "inst-a", "code": "333333", "name": "정상2"},
            {"stock_path": "stocks/444444_REVIEW", "assigned_routine_instance_id": "inst-a", "code": "444444", "name": "격리"},
        ]

        def fake_read_json(path: Path):
            text = str(path)
            if text.endswith("config.json"):
                if "222222_STOP" in text:
                    return {
                        "assigned_routine_instance_id": "inst-a",
                        "operation_mode": "SCHEDULED",
                        "operation_excluded": True,
                    }
                return {"assigned_routine_instance_id": "inst-a", "operation_mode": "SCHEDULED"}
            if "111111_RUN" in text:
                return {"status": "RUNNING", "trade_enabled": True}
            if "444444_REVIEW" in text:
                return {"status": "REVIEW_REQUIRED", "review_required": True, "trade_enabled": False}
            return {"status": "STOPPED", "trade_enabled": False}

        with patch.object(table_loader, "read_base_stocks", return_value=stocks), \
                patch.object(table_loader, "read_json_dict", side_effect=fake_read_json):
            window._stock_status_filter = "all"
            table_loader.auto_trade_load_selected_routine_stocks(window)
            self.assertEqual(4, window.stock_table.rowCount())

            window._stock_status_filter = "running"
            table_loader.auto_trade_load_selected_routine_stocks(window)
            self.assertEqual(
                ["111111", "333333"],
                [window.stock_table.item(row, 0).text() for row in range(window.stock_table.rowCount())],
            )

            window._stock_status_filter = "excluded"
            table_loader.auto_trade_load_selected_routine_stocks(window)
            self.assertEqual(
                ["222222"],
                [window.stock_table.item(row, 0).text() for row in range(window.stock_table.rowCount())],
            )

            window._stock_status_filter = "error"
            table_loader.auto_trade_load_selected_routine_stocks(window)
            self.assertEqual(["444444"], [window.stock_table.item(row, 0).text() for row in range(window.stock_table.rowCount())])

    def test_maximized_workspace_reserves_stock_table_required_width(self) -> None:
        with patch.object(AutoTradeSettingWindow, "refresh_all", lambda _self: None), \
                patch.object(AutoTradeSettingWindow, "update_startup_recovery_controls", lambda _self: None), \
                patch.object(AutoTradeSettingWindow, "current_runtime_file_signature", lambda _self: tuple()):
            window = AutoTradeSettingWindow()
        self.addCleanup(window.close)
        window.show()
        self._app.processEvents()

        header = window.stock_table.horizontalHeader()
        column_width_sum = sum(header.sectionSize(col) for col in range(window.stock_table.columnCount()))
        initial_column_width_sum = sum(
            header.sectionSize(col)
            for col in range(setting_window.AUTO_TRADE_SETTING_INITIAL_STOCK_LAST_COLUMN + 1)
        )
        initial_stock_width = window._stock_table_required_width(
            setting_window.AUTO_TRADE_SETTING_INITIAL_STOCK_LAST_COLUMN
        )
        initial_right_width = window._right_workspace_initial_width()
        stock_required_width = window._stock_table_required_width()
        right_required_width = window._right_workspace_required_width()
        self.assertGreaterEqual(initial_stock_width, initial_column_width_sum)
        self.assertLess(initial_stock_width, stock_required_width)
        self.assertGreaterEqual(initial_right_width, initial_stock_width)
        self.assertLess(initial_right_width, right_required_width)
        self.assertGreaterEqual(stock_required_width, column_width_sum)
        self.assertGreaterEqual(right_required_width, stock_required_width)
        initial_left_width, initial_splitter_right_width = window.strategy_workspace_splitter.sizes()
        self.assertGreaterEqual(initial_left_width, window.routine_box.minimumWidth())
        self.assertGreaterEqual(initial_splitter_right_width, initial_right_width)
        self.assertLess(initial_splitter_right_width, right_required_width)

        handle_width = window.strategy_workspace_splitter.handleWidth()
        available_width = window.routine_box.minimumWidth() + right_required_width + handle_width + 120
        window.strategy_workspace_splitter.resize(available_width, 420)
        window._rebalance_strategy_workspace_splitter()
        self._app.processEvents()

        left_width, right_width = window.strategy_workspace_splitter.sizes()
        self.assertGreaterEqual(left_width, window.routine_box.minimumWidth())
        self.assertGreaterEqual(right_width, right_required_width)
        self.assertEqual(setting_window.Qt.ScrollBarAlwaysOn, window.stock_table.horizontalScrollBarPolicy())
        self.assertEqual(setting_window.QHeaderView.Fixed, header.sectionResizeMode(0))

    def test_routine_tree_does_not_render_default_operation_stamp_buttons(self) -> None:
        instances = [self._instance("inst-a", "A 인스턴스"), self._instance("inst-b", "B 인스턴스")]
        window = self._window_harness()
        counts = {
            "inst-a": {"registered": 1, "running": 0, "error": 0},
            "inst-b": {"registered": 1, "running": 0, "error": 0},
        }
        window._routine_instance_operation_counts = lambda: counts
        with patch.object(setting_window, "load_routine_definitions", return_value=[self._definition()]), \
                patch.object(setting_window, "load_persisted_routine_instances", return_value=instances):
            window.load_routine_table()
            window.routine_table.selectRow(0)
            parent_stamp = window.routine_table.cellWidget(0, 0).findChild(
                setting_window.QPushButton,
                "autoTradeSettingDefaultOperationStamp",
            )
            first_stamp = window.routine_table.cellWidget(1, 0).findChild(
                setting_window.QPushButton,
                "autoTradeSettingDefaultOperationStamp",
            )
            second_stamp = window.routine_table.cellWidget(2, 0).findChild(
                setting_window.QPushButton,
                "autoTradeSettingDefaultOperationStamp",
            )

            self.assertIsNone(parent_stamp)
            self.assertIsNone(first_stamp)
            self.assertIsNone(second_stamp)
            self.assertEqual(0, window.routine_table.selectionModel().selectedRows()[0].row())


if __name__ == "__main__":
    unittest.main()
