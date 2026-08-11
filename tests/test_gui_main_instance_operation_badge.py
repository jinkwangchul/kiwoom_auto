import json
import os
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QItemSelectionModel, Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

import gui_auto_trade_selection
import gui_auto_trade_run_control as run_control
import gui_auto_trade_setting_window as setting_window
import gui_main_stock_context_menu as context_menu
import gui_main_table_loader as table_loader
import gui_windows


class MainInstanceOperationBadgeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_review_required_button_count_uses_review_window_collector(self) -> None:
        rows = [
            {"code": "000660", "review_reason": "긴급정지 해제 시 보유잔량 존재"},
            {"code": "323410", "review_reason": "운영 데이터 불일치"},
            {"code": "035420", "review_reason": "운영 데이터 없음"},
            {"code": "051910", "review_reason": "운영 데이터 읽기 오류"},
        ]
        window = SimpleNamespace(btn_review_required=QLabel())
        window.review_required_stock_count = (
            lambda: gui_windows.MainWindow.review_required_stock_count(window)
        )

        with patch.object(
            gui_windows,
            "collect_global_review_required_rows",
            return_value=rows,
        ) as collector:
            count = gui_windows.MainWindow.review_required_stock_count(window)
            gui_windows.MainWindow.update_review_required_button_text(window)

        collector.assert_called()
        self.assertEqual(4, count)
        self.assertEqual("검토관리(4)", window.btn_review_required.text())

    def test_review_required_button_shows_zero_when_collector_empty(self) -> None:
        window = SimpleNamespace(btn_review_required=QLabel())
        window.review_required_stock_count = (
            lambda: gui_windows.MainWindow.review_required_stock_count(window)
        )

        with patch.object(
            gui_windows,
            "collect_global_review_required_rows",
            return_value=[],
        ):
            gui_windows.MainWindow.update_review_required_button_text(window)

        self.assertEqual("검토관리(0)", window.btn_review_required.text())

    def test_monitoring_bottom_button_order_starts_with_global_start(self) -> None:
        window = SimpleNamespace(
            btn_start=QPushButton("▶ 운영시작"),
            btn_auto_trade_setting=QPushButton("자동매매설정"),
            btn_initialize=QPushButton("초기화"),
            btn_log_view=QPushButton("이벤트기록"),
            btn_review_required=QPushButton("검토관리(0)"),
            btn_exit=QPushButton("종료"),
        )

        layout = gui_windows.MainWindow._create_button_area(window)

        self.assertEqual(
            [
                "▶ 운영시작",
                "자동매매설정",
                "초기화",
                "이벤트기록",
                "검토관리(0)",
                "종료",
            ],
            [layout.itemAt(index).widget().text() for index in range(layout.count())],
        )

    def test_badge_click_selects_and_double_click_requests_toggle(self) -> None:
        on_click = MagicMock()
        on_double_click = MagicMock()
        widget = table_loader.create_routine_instance_status_widget(
            table_loader.ROUTINE_STATUS_RUNNING,
            registered=1,
            running=1,
            stopped=0,
            error=0,
            on_status_click=on_click,
            on_status_double_click=on_double_click,
        )
        widget.show()
        self.app.processEvents()
        stamp = widget.findChild(QWidget, "routineInstanceStatusStamp")
        text = widget.findChild(QLabel, "routineInstanceStatusText")

        QTest.mouseClick(stamp, Qt.LeftButton)
        self.assertEqual(1, on_click.call_count)
        on_double_click.assert_not_called()

        QTest.mouseDClick(stamp, Qt.LeftButton)
        on_double_click.assert_called_once()
        self.assertEqual("운  영", text.text())
        self.assertEqual(table_loader.ROUTINE_STATUS_STAMP_WIDTH, stamp.width())
        self.assertEqual(table_loader.ROUTINE_STATUS_STAMP_HEIGHT, stamp.height())
        self.assertTrue(stamp.testAttribute(Qt.WA_StyledBackground))
        widget.close()

    def test_missing_instance_uses_one_actionable_failure_dialog(self) -> None:
        status_bar = MagicMock()
        window = SimpleNamespace(statusBar=lambda: status_bar)

        with (
            patch.object(gui_windows, "routine_instance_by_id", return_value=None),
            patch.object(
                gui_windows,
                "show_auto_trade_operation_failure_dialog",
            ) as show_failure,
        ):
            gui_windows.MainWindow.toggle_routine_instance_operation(
                window,
                "missing-instance",
            )

        show_failure.assert_called_once()
        result = show_failure.call_args.args[2]
        self.assertIn("화면을 새로고침", result["user_message"])
        self.assertNotIn("INSTANCE_NOT_FOUND", result["user_message"])

    def test_instance_without_registered_stocks_has_actionable_message(self) -> None:
        status_bar = MagicMock()
        window = SimpleNamespace(
            _routine_instance_stock_dirs=Mock(return_value=[]),
            statusBar=lambda: status_bar,
        )
        instance = SimpleNamespace(
            instance_id="instance-a",
            display_name="지표추종매매",
        )

        with (
            patch.object(gui_windows, "routine_instance_by_id", return_value=instance),
            patch.object(
                gui_windows,
                "show_auto_trade_operation_failure_dialog",
            ) as show_failure,
        ):
            gui_windows.MainWindow.toggle_routine_instance_operation(
                window,
                "instance-a",
            )

        show_failure.assert_called_once()
        result = show_failure.call_args.args[2]
        self.assertIn("등록된 종목이 없습니다.", result["user_message"])
        self.assertIn("자동매매 설정", result["user_message"])

    def test_badge_availability_does_not_depend_on_removed_checkbox_state(self) -> None:
        self.assertTrue(
            table_loader.routine_instance_operation_badge_enabled(
                definition_enabled=True,
                registered_count=1,
            )
        )
        self.assertFalse(
            table_loader.routine_instance_operation_badge_enabled(
                definition_enabled=True,
                registered_count=0,
            )
        )

    def test_run_control_adapter_reuses_setting_start_orchestration_and_stop_backend(self) -> None:
        parent = QWidget()
        parent.routine_table = MagicMock()
        target = context_menu.MainMonitoringStockTarget(
            stock_dir=Path("stocks/005930_삼성전자"),
            code="005930",
            name="삼성전자",
            routine_instance_id="instance-a",
        )
        adapter = context_menu.MainMonitoringStockOperationAdapter(parent, [target])

        with (
            patch.object(
                gui_windows.AutoTradeSettingWindow,
                "start_selected_rows_auto_trades",
                return_value={"ok": True, "reason": "STARTED"},
            ) as start_orchestration,
            patch.object(
                context_menu,
                "auto_trade_stop_selected_auto_trades",
                return_value={"ok": True, "reason": "STOPPED"},
            ) as stop_backend,
        ):
            start_result = adapter.start_selected_auto_trades()
            stop_result = adapter.stop_selected_auto_trades()

        start_orchestration.assert_called_once_with(adapter)
        stop_backend.assert_called_once_with(adapter)
        self.assertEqual("STARTED", start_result["reason"])
        self.assertEqual("STOPPED", stop_result["reason"])
        adapter.close()
        parent.close()

    def test_monitor_start_before_running_uses_setting_selective_start_contract(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            targets = []
            for index in range(4):
                code = f"{index + 1:06d}"
                name = f"테스트{index + 1}"
                stock_dir = root / f"{code}_{name}"
                stock_dir.mkdir()
                (stock_dir / "config.json").write_text(
                    json.dumps(
                        {
                            "assigned_routine_instance_id": "instance-a",
                            "routine_instance_name": "테스트 루틴",
                            "operation_excluded": index == 0,
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                (stock_dir / "state.json").write_text(
                    json.dumps({"status": "STOPPED", "trade_enabled": False}),
                    encoding="utf-8",
                )
                targets.append(
                    context_menu.MainMonitoringStockTarget(
                        stock_dir=stock_dir,
                        code=code,
                        name=name,
                        routine_instance_id="instance-a",
                    )
                )

            parent = QWidget()
            parent.routine_table = QTableWidget()
            parent.statusBar = Mock(return_value=Mock())
            adapter = context_menu.MainMonitoringStockOperationAdapter(
                parent,
                [targets[0], targets[2]],
            )
            registered = [
                (target.stock_dir, target.code, target.name) for target in targets
            ]
            adapter.registered_operation_targets = lambda: registered
            adapter.running_registered_operation_targets = lambda: []
            adapter.refresh_all = Mock()
            adapter.update_global_operation_button_state = Mock()

            with (
                patch.object(setting_window, "read_operation_state", return_value={}),
                patch.object(
                    setting_window,
                    "auto_trade_start_selected_auto_trades",
                    return_value={"ok": True, "reason": "STARTED"},
                ) as start_backend,
            ):
                result = adapter.start_selected_auto_trades()

            self.assertTrue(result["ok"])
            self.assertEqual(
                [targets[0].code, targets[2].code],
                [item[1] for item in start_backend.call_args.kwargs["selected_targets"]],
            )
            configs = [
                json.loads((target.stock_dir / "config.json").read_text(encoding="utf-8"))
                for target in targets
            ]
            self.assertFalse(configs[0]["operation_excluded"])
            self.assertTrue(configs[1]["operation_excluded"])
            self.assertFalse(configs[2]["operation_excluded"])
            self.assertTrue(configs[3]["operation_excluded"])
            parent.close()

    def test_monitor_start_while_running_adds_only_selected_excluded_target(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            targets = []
            for index, excluded in enumerate((False, True, True)):
                code = f"{index + 1:06d}"
                name = f"테스트{index + 1}"
                stock_dir = root / f"{code}_{name}"
                stock_dir.mkdir()
                (stock_dir / "config.json").write_text(
                    json.dumps(
                        {
                            "assigned_routine_instance_id": "instance-a",
                            "routine_instance_name": "테스트 루틴",
                            "operation_excluded": excluded,
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                (stock_dir / "state.json").write_text(
                    json.dumps({"status": "STOPPED", "trade_enabled": False}),
                    encoding="utf-8",
                )
                targets.append(
                    context_menu.MainMonitoringStockTarget(
                        stock_dir=stock_dir,
                        code=code,
                        name=name,
                        routine_instance_id="instance-a",
                    )
                )

            parent = QWidget()
            parent.routine_table = QTableWidget()
            parent.statusBar = Mock(return_value=Mock())
            adapter = context_menu.MainMonitoringStockOperationAdapter(parent, [targets[1]])
            adapter.registered_operation_targets = lambda: [
                (target.stock_dir, target.code, target.name) for target in targets
            ]
            adapter.running_registered_operation_targets = lambda: [
                (targets[0].stock_dir, targets[0].code, targets[0].name)
            ]
            adapter.refresh_all = Mock()
            adapter.update_global_operation_button_state = Mock()

            with (
                patch.object(setting_window, "read_operation_state", return_value={}),
                patch.object(
                    setting_window,
                    "auto_trade_start_selected_auto_trades",
                    return_value={"ok": True, "reason": "STARTED"},
                ) as start_backend,
            ):
                result = adapter.start_selected_auto_trades()

            self.assertTrue(result["ok"])
            start_backend.assert_called_once()
            self.assertFalse(
                json.loads(
                    (targets[1].stock_dir / "config.json").read_text(encoding="utf-8")
                )["operation_excluded"]
            )
            self.assertTrue(
                json.loads(
                    (targets[2].stock_dir / "config.json").read_text(encoding="utf-8")
                )["operation_excluded"]
            )
            parent.close()

    def test_monitor_start_mixed_running_selection_uses_setting_block_contract(self) -> None:
        parent = QWidget()
        parent.routine_table = QTableWidget()
        parent.statusBar = Mock(return_value=Mock())
        running = context_menu.MainMonitoringStockTarget(
            stock_dir=Path("stocks/000001_운영중"),
            code="000001",
            name="운영중",
            routine_instance_id="instance-a",
        )
        inactive = context_menu.MainMonitoringStockTarget(
            stock_dir=Path("stocks/000002_비운영"),
            code="000002",
            name="비운영",
            routine_instance_id="instance-a",
        )
        adapter = context_menu.MainMonitoringStockOperationAdapter(
            parent,
            [running, inactive],
        )
        adapter.running_registered_operation_targets = lambda: [
            (running.stock_dir, running.code, running.name)
        ]

        with (
            patch.object(setting_window, "read_operation_state", return_value={}),
            patch.object(setting_window, "auto_trade_start_selected_auto_trades") as backend,
        ):
            result = adapter.start_selected_auto_trades()

        self.assertFalse(result["ok"])
        self.assertEqual("MIXED_RUNNING_SELECTION", result["reason"])
        backend.assert_not_called()
        parent.close()

    def test_monitor_operation_adapter_refreshes_both_open_windows(self) -> None:
        parent = QWidget()
        parent.routine_table = QTableWidget()
        parent.refresh_all = Mock()
        setting_window_widget = QWidget()
        setting_window_widget.refresh_all = Mock()
        parent.auto_trade_setting_window = setting_window_widget
        adapter = context_menu.MainMonitoringStockOperationAdapter(parent, [])

        adapter.refresh_all()

        parent.refresh_all.assert_called_once_with()
        setting_window_widget.refresh_all.assert_called_once_with()
        setting_window_widget.close()
        parent.close()

    def test_monitoring_bottom_start_reuses_setting_global_orchestration(self) -> None:
        window = SimpleNamespace()
        adapter = Mock()

        with (
            patch.object(
                gui_windows,
                "MainMonitoringStockOperationAdapter",
                return_value=adapter,
            ) as adapter_factory,
            patch.object(
                gui_windows.AutoTradeSettingWindow,
                "start_selected_auto_trades",
            ) as orchestration,
        ):
            gui_windows.MainWindow.start_global_auto_trades(window)

        adapter_factory.assert_called_once_with(window, [])
        orchestration.assert_called_once_with(adapter)

    def test_monitoring_global_start_uses_global_target_scope_and_source(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            included_dir = root / "005930_Samsung"
            excluded_dir = root / "000660_SKHynix"
            included_dir.mkdir()
            excluded_dir.mkdir()
            (included_dir / "config.json").write_text(
                json.dumps({"operation_excluded": False}),
                encoding="utf-8",
            )
            (excluded_dir / "config.json").write_text(
                json.dumps({"operation_excluded": True}),
                encoding="utf-8",
            )
            parent = QWidget()
            parent.routine_table = QTableWidget()
            parent.btn_start = QPushButton("▶ 운영시작")
            parent.auto_trade_setting_window = None
            adapter = context_menu.MainMonitoringStockOperationAdapter(parent, [])
            adapter.update_global_operation_button_state = Mock()

            with (
                patch.object(
                    setting_window,
                    "all_registered_stock_dirs",
                    return_value=[included_dir, excluded_dir],
                ),
                patch.object(setting_window, "read_operation_state", return_value={}),
                patch.object(
                    setting_window,
                    "auto_trade_start_selected_auto_trades",
                    return_value={"ok": True},
                ) as backend,
            ):
                setting_window.AutoTradeSettingWindow.start_selected_auto_trades(
                    adapter
                )

            backend.assert_called_once_with(
                adapter,
                request_scope="multiple",
                selected_targets=[(included_dir, "005930", "Samsung")],
                source="auto_trade_global_start_button",
            )
            adapter.update_global_operation_button_state.assert_called_once_with()
            adapter.close()
            parent.close()

    def test_monitoring_global_start_button_uses_setting_button_state_contract(self) -> None:
        parent = QWidget()
        parent.routine_table = QTableWidget()
        parent.btn_start = QPushButton("▶ 운영시작")
        parent.auto_trade_setting_window = None
        adapter = context_menu.MainMonitoringStockOperationAdapter(parent, [])
        adapter.registered_operation_targets = Mock(
            return_value=[(Path("stocks/005930_삼성전자"), "005930", "삼성전자")]
        )
        adapter.running_registered_operation_targets = Mock(return_value=[])
        setting_button = QPushButton("▶ 운영시작")
        setting_host = SimpleNamespace(
            btn_start=setting_button,
            registered_operation_targets=adapter.registered_operation_targets,
            running_registered_operation_targets=adapter.running_registered_operation_targets,
        )

        with patch.object(setting_window, "read_operation_state", return_value={}):
            setting_window.AutoTradeSettingWindow.update_global_operation_button_state(
                setting_host
            )
            adapter.update_global_operation_button_state()

        self.assertEqual(setting_button.text(), parent.btn_start.text())
        self.assertEqual(setting_button.isEnabled(), parent.btn_start.isEnabled())
        self.assertEqual(setting_button.styleSheet(), parent.btn_start.styleSheet())
        parent.close()

    def test_setting_global_start_refreshes_open_monitoring_parent(self) -> None:
        selected = [(Path("stocks/005930_삼성전자"), "005930", "삼성전자")]
        parent = SimpleNamespace(refresh_all=Mock())
        window = SimpleNamespace(
            parent=Mock(return_value=parent),
            running_registered_operation_targets=Mock(return_value=[]),
            registered_operation_start_targets=Mock(return_value=selected),
            update_global_operation_button_state=Mock(),
        )

        with (
            patch.object(setting_window, "read_operation_state", return_value={}),
            patch.object(
                setting_window,
                "auto_trade_start_selected_auto_trades",
                return_value={"ok": True},
            ) as backend,
        ):
            setting_window.AutoTradeSettingWindow.start_selected_auto_trades(window)

        backend.assert_called_once_with(
            window,
            request_scope="multiple",
            selected_targets=selected,
            source="auto_trade_global_start_button",
        )
        parent.refresh_all.assert_called_once_with()
        window.update_global_operation_button_state.assert_not_called()

    def test_auto_trade_setting_bottom_start_uses_global_multiple_scope(self) -> None:
        window = SimpleNamespace(
            selected_stock_infos=Mock(),
            update_global_operation_button_state=Mock(),
        )
        window.registered_operation_targets = lambda: (
            gui_windows.AutoTradeSettingWindow.registered_operation_targets(window)
        )
        window.registered_operation_start_targets = lambda: (
            gui_windows.AutoTradeSettingWindow.registered_operation_start_targets(window)
        )
        window.running_registered_operation_targets = lambda: (
            gui_windows.AutoTradeSettingWindow.running_registered_operation_targets(window)
        )
        stock_dirs = [
            Path("stocks/005930_삼성전자"),
            Path("stocks/000660_SK하이닉스"),
        ]

        with (
            patch(
                "gui_auto_trade_setting_window.all_registered_stock_dirs",
                return_value=stock_dirs,
            ),
            patch(
                "gui_auto_trade_setting_window.auto_trade_start_selected_auto_trades"
            ) as start_backend,
            patch(
                "gui_auto_trade_setting_window.auto_trade_stock_operation_excluded",
                return_value=False,
            ),
        ):
            gui_windows.AutoTradeSettingWindow.start_selected_auto_trades(window)

        window.selected_stock_infos.assert_not_called()
        call = start_backend.call_args
        self.assertEqual("multiple", call.kwargs["request_scope"])
        self.assertEqual(
            "auto_trade_global_start_button",
            call.kwargs["source"],
        )
        self.assertEqual(
            ["005930", "000660"],
            [target[1] for target in call.kwargs["selected_targets"]],
        )
        window.update_global_operation_button_state.assert_called_once_with()

    def test_auto_trade_setting_bottom_start_filters_operation_excluded_targets(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            included_dir = root / "005930_Samsung"
            excluded_dir = root / "000660_SKHynix"
            included_dir.mkdir()
            excluded_dir.mkdir()
            (included_dir / "config.json").write_text(
                json.dumps({"operation_excluded": False}),
                encoding="utf-8",
            )
            (excluded_dir / "config.json").write_text(
                json.dumps({"operation_excluded": True}),
                encoding="utf-8",
            )
            window = SimpleNamespace(
                selected_stock_infos=Mock(),
                update_global_operation_button_state=Mock(),
                statusBarMessage=Mock(),
            )
            window.registered_operation_targets = lambda: (
                gui_windows.AutoTradeSettingWindow.registered_operation_targets(window)
            )
            window.registered_operation_start_targets = lambda: (
                gui_windows.AutoTradeSettingWindow.registered_operation_start_targets(window)
            )
            window.running_registered_operation_targets = lambda: (
                gui_windows.AutoTradeSettingWindow.running_registered_operation_targets(window)
            )

            with (
                patch(
                    "gui_auto_trade_setting_window.all_registered_stock_dirs",
                    return_value=[included_dir, excluded_dir],
                ),
                patch(
                    "gui_auto_trade_setting_window.auto_trade_start_selected_auto_trades"
                ) as start_backend,
            ):
                gui_windows.AutoTradeSettingWindow.start_selected_auto_trades(window)

            start_backend.assert_called_once()
            self.assertEqual(
                ["005930"],
                [
                    target[1]
                    for target in start_backend.call_args.kwargs["selected_targets"]
                ],
            )
            window.selected_stock_infos.assert_not_called()
            window.statusBarMessage.assert_not_called()
            window.update_global_operation_button_state.assert_called_once_with()

    def test_auto_trade_setting_bottom_button_does_not_stop_running_targets(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            running_dir = root / "005930_삼성전자"
            stopped_dir = root / "000660_SK하이닉스"
            running_dir.mkdir()
            stopped_dir.mkdir()
            (running_dir / "state.json").write_text(
                json.dumps({"status": "RUNNING", "trade_enabled": True}),
                encoding="utf-8",
            )
            (stopped_dir / "state.json").write_text(
                json.dumps({"status": "STOPPED", "trade_enabled": False}),
                encoding="utf-8",
            )
            window = SimpleNamespace(
                update_global_operation_button_state=Mock(),
            )
            window.registered_operation_targets = lambda: (
                gui_windows.AutoTradeSettingWindow.registered_operation_targets(window)
            )
            window.registered_operation_start_targets = lambda: (
                gui_windows.AutoTradeSettingWindow.registered_operation_start_targets(window)
            )
            window.running_registered_operation_targets = lambda: (
                gui_windows.AutoTradeSettingWindow.running_registered_operation_targets(window)
            )

            with (
                patch(
                    "gui_auto_trade_setting_window.all_registered_stock_dirs",
                    return_value=[running_dir, stopped_dir],
                ),
                patch(
                    "gui_auto_trade_setting_window.auto_trade_stop_selected_auto_trades"
                ) as stop_backend,
                patch(
                    "gui_auto_trade_setting_window.auto_trade_start_selected_auto_trades"
                ) as start_backend,
            ):
                gui_windows.AutoTradeSettingWindow.start_selected_auto_trades(window)

            start_backend.assert_not_called()
            stop_backend.assert_not_called()
            window.update_global_operation_button_state.assert_called_once_with()

    def test_stop_backend_accepts_explicit_targets_without_current_routine(self) -> None:
        target = (Path("stocks/005930_삼성전자"), "005930", "삼성전자")
        window = SimpleNamespace(
            selected_stock_infos=Mock(),
            current_selected_routine_name=Mock(return_value=""),
            split_stop_targets=Mock(return_value=([target], [])),
            confirm_stop_targets_once=Mock(return_value=False),
            statusBarMessage=Mock(),
        )

        result = run_control.auto_trade_stop_selected_auto_trades(
            window,
            selected_targets=[target],
            source="auto_trade_global_stop_button",
        )

        self.assertEqual("CANCELLED", result["reason"])
        window.selected_stock_infos.assert_not_called()
        window.split_stop_targets.assert_called_once_with([target])
        window.confirm_stop_targets_once.assert_called_once_with([target])

    def test_global_start_button_stays_enabled_before_recovery_when_targets_exist(
        self,
    ) -> None:
        execution_buttons = [MagicMock() for _index in range(6)]
        window = SimpleNamespace(
            startup_recovery_session_ready=Mock(return_value=False),
            btn_execution_enable=execution_buttons[0],
            btn_real_ready_preflight=execution_buttons[1],
            btn_execution_preview=execution_buttons[2],
            btn_manual_send_order=execution_buttons[3],
            btn_manual_cancel_pending_order=execution_buttons[4],
            btn_manual_modify_pending_order=execution_buttons[5],
            btn_manual_queue_commit=MagicMock(),
            btn_start=MagicMock(),
            update_global_operation_button_state=MagicMock(),
        )

        with patch(
            "gui_auto_trade_setting_window.all_registered_stock_dirs",
            return_value=[Path("stocks/005930_삼성전자")],
        ):
            gui_windows.AutoTradeSettingWindow.update_startup_recovery_controls(window)

        window.update_global_operation_button_state.assert_called_once_with()
        for button in execution_buttons:
            button.setEnabled.assert_called_once_with(False)
        window.btn_manual_queue_commit.setEnabled.assert_called_once_with(False)

    def test_global_operation_button_uses_official_running_state(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            running_dir = root / "005930_삼성전자"
            stopped_dir = root / "000660_SK하이닉스"
            running_dir.mkdir()
            stopped_dir.mkdir()
            (running_dir / "state.json").write_text(
                json.dumps({"status": "RUNNING", "trade_enabled": True}),
                encoding="utf-8",
            )
            (stopped_dir / "state.json").write_text(
                json.dumps({"status": "STOPPED", "trade_enabled": False}),
                encoding="utf-8",
            )
            window = SimpleNamespace(
                btn_start=MagicMock(),
                startup_recovery_session_ready=Mock(return_value=True),
            )
            window.registered_operation_targets = lambda: (
                gui_windows.AutoTradeSettingWindow.registered_operation_targets(window)
            )
            window.running_registered_operation_targets = lambda: (
                gui_windows.AutoTradeSettingWindow.running_registered_operation_targets(window)
            )

            with (
                patch(
                    "gui_auto_trade_setting_window.all_registered_stock_dirs",
                    return_value=[running_dir, stopped_dir],
                ),
                patch(
                    "gui_auto_trade_setting_window.read_operation_state",
                    return_value={"emergency_stop": False, "operation_status": ""},
                ),
            ):
                gui_windows.AutoTradeSettingWindow.update_global_operation_button_state(
                    window
                )

            window.btn_start.setText.assert_called_once_with("운영중")
            window.btn_start.setEnabled.assert_called_once_with(False)

            (running_dir / "state.json").write_text(
                json.dumps({"status": "STOPPED", "trade_enabled": False}),
                encoding="utf-8",
            )
            window.btn_start.reset_mock()
            with (
                patch(
                    "gui_auto_trade_setting_window.all_registered_stock_dirs",
                    return_value=[running_dir, stopped_dir],
                ),
                patch(
                    "gui_auto_trade_setting_window.read_operation_state",
                    return_value={"emergency_stop": False, "operation_status": ""},
                ),
            ):
                gui_windows.AutoTradeSettingWindow.update_global_operation_button_state(
                    window
                )

            window.btn_start.setText.assert_called_once_with("▶ 운영시작")
            window.btn_start.setEnabled.assert_called_once_with(True)

    def test_stock_name_double_click_toggles_operation_exclusion(self) -> None:
        with TemporaryDirectory() as temp:
            stock_dir = Path(temp) / "000660_SK하이닉스"
            stock_dir.mkdir()
            (stock_dir / "state.json").write_text(
                json.dumps({"status": "STOPPED", "trade_enabled": False}),
                encoding="utf-8",
            )
            window = SimpleNamespace(
                stock_info_from_row=Mock(
                    return_value=(stock_dir, "000660", "SK하이닉스")
                ),
                statusBarMessage=Mock(),
            )
            target = (stock_dir, "000660", "Test")
            window.stock_info_from_row.return_value = target
            window.toggle_stock_operation_exclusion = Mock(return_value=True)
            item = SimpleNamespace(column=lambda: 1, row=lambda: 2)

            with patch(
                "gui_auto_trade_setting_window.auto_trade_start_status_indicator"
            ) as adapter:
                gui_windows.AutoTradeSettingWindow.on_stock_table_name_item_double_clicked(
                    window,
                    item,
                )

        window.toggle_stock_operation_exclusion.assert_called_once_with(target)
        adapter.assert_not_called()

    def test_running_status_indicator_does_not_call_start_backend(self) -> None:
        with TemporaryDirectory() as temp:
            stock_dir = Path(temp) / "000660_SK하이닉스"
            stock_dir.mkdir()
            (stock_dir / "state.json").write_text(
                json.dumps({"status": "RUNNING", "trade_enabled": True}),
                encoding="utf-8",
            )
            window = SimpleNamespace(
                statusBarMessage=Mock(),
            )
            target = (stock_dir, "000660", "SK하이닉스")

            with (
                patch(
                    "gui_auto_trade_run_control.auto_trade_start_selected_auto_trades"
                ) as backend,
                patch(
                    "gui_auto_trade_run_control.show_auto_trade_operation_failure_dialog"
                ) as dialog,
            ):
                result = run_control.auto_trade_start_status_indicator(
                    window,
                    target,
                )

        backend.assert_not_called()
        dialog.assert_called_once()
        self.assertEqual("ALREADY_RUNNING", result["reason"])
        self.assertIn("이미 운영 중입니다", result["user_message"])

    def test_non_name_cells_do_not_start_stock(self) -> None:
        for column in (0, 2, 3, 4):
            with self.subTest(column=column):
                window = SimpleNamespace(
                    stock_info_from_row=Mock(),
                    toggle_stock_operation_exclusion=Mock(),
                )
                item = SimpleNamespace(column=lambda: column, row=lambda: 2)

                with patch(
                    "gui_auto_trade_setting_window.auto_trade_start_status_indicator"
                ) as adapter:
                    gui_windows.AutoTradeSettingWindow.on_stock_table_name_item_double_clicked(
                        window,
                        item,
                    )

                adapter.assert_not_called()
                window.stock_info_from_row.assert_not_called()
                window.toggle_stock_operation_exclusion.assert_not_called()

    def test_status_indicator_ignores_reentry_while_start_is_inflight(self) -> None:
        with TemporaryDirectory() as temp:
            stock_dir = Path(temp) / "000660_SK하이닉스"
            stock_dir.mkdir()
            (stock_dir / "state.json").write_text(
                json.dumps({"status": "STOPPED", "trade_enabled": False}),
                encoding="utf-8",
            )
            window = SimpleNamespace(
                _operation_start_inflight_stock_codes={"000660"},
            )
            target = (stock_dir, "000660", "SK하이닉스")

            with patch(
                "gui_auto_trade_run_control.auto_trade_start_selected_auto_trades"
            ) as backend:
                result = run_control.auto_trade_start_status_indicator(
                    window,
                    target,
                )

        backend.assert_not_called()
        self.assertEqual("REQUEST_IN_PROGRESS", result["reason"])

    def test_status_indicator_reports_official_readback_mismatch(self) -> None:
        with TemporaryDirectory() as temp:
            stock_dir = Path(temp) / "000660_SK하이닉스"
            stock_dir.mkdir()
            (stock_dir / "state.json").write_text(
                json.dumps({"status": "STOPPED", "trade_enabled": False}),
                encoding="utf-8",
            )
            window = SimpleNamespace(
                statusBarMessage=Mock(),
            )
            target = (stock_dir, "000660", "SK하이닉스")

            with (
                patch(
                    "gui_auto_trade_run_control.auto_trade_start_selected_auto_trades",
                    return_value={"ok": True, "reason": "STARTED"},
                ),
                patch(
                    "gui_auto_trade_run_control.show_auto_trade_operation_failure_dialog"
                ) as dialog,
            ):
                failure = run_control.auto_trade_start_status_indicator(
                    window,
                    target,
                )

        dialog.assert_called_once()
        self.assertEqual("STATE_READBACK_FAILED", failure["reason"])
        self.assertEqual("single", failure["request_scope"])
        self.assertEqual(
            "auto_trade_status_indicator",
            failure["source"],
        )
        self.assertEqual(set(), window._operation_start_inflight_stock_codes)

    def test_selected_rows_context_start_is_always_multiple(self) -> None:
        selected = [(Path("stocks/005930_삼성전자"), "005930", "삼성전자")]
        window = SimpleNamespace(
            selected_stock_infos=Mock(return_value=selected),
            running_registered_operation_targets=Mock(return_value=[]),
            registered_operation_targets=Mock(return_value=selected),
            refresh_all=Mock(),
            update_global_operation_button_state=Mock(),
        )

        with (
            patch(
                "gui_auto_trade_setting_window.auto_trade_start_selected_auto_trades",
                return_value={"ok": False},
            ) as backend,
            patch(
                "gui_auto_trade_setting_window.read_operation_state",
                return_value={},
            ),
        ):
            gui_windows.AutoTradeSettingWindow.start_selected_rows_auto_trades(window)

        call = backend.call_args
        self.assertEqual("multiple", call.kwargs["request_scope"])
        self.assertEqual(selected, call.kwargs["selected_targets"])
        self.assertEqual(
            "auto_trade_context_menu",
            call.kwargs["source"],
        )

    def test_selected_rows_context_start_requires_running_targets_protocol(self) -> None:
        selected = [(Path("stocks/005930_삼성전자"), "005930", "삼성전자")]
        window = SimpleNamespace(selected_stock_infos=Mock(return_value=selected))

        with patch(
            "gui_auto_trade_setting_window.auto_trade_start_selected_auto_trades"
        ) as backend:
            with self.assertRaises(AttributeError):
                gui_windows.AutoTradeSettingWindow.start_selected_rows_auto_trades(window)

        backend.assert_not_called()

    def test_selected_stock_infos_excludes_hidden_selected_rows(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            visible_dir = root / "005930_삼성전자"
            hidden_dir = root / "000660_SK하이닉스"
            visible_dir.mkdir()
            hidden_dir.mkdir()
            table = QTableWidget(2, 2)
            table.setSelectionBehavior(QAbstractItemView.SelectRows)
            table.setSelectionMode(QAbstractItemView.ExtendedSelection)
            for row, (stock_dir, code, name) in enumerate(
                (
                    (visible_dir, "005930", "삼성전자"),
                    (hidden_dir, "000660", "SK하이닉스"),
                )
            ):
                code_item = QTableWidgetItem(code)
                code_item.setData(Qt.UserRole, str(stock_dir))
                table.setItem(row, 0, code_item)
                table.setItem(row, 1, QTableWidgetItem(name))
                table.selectionModel().select(
                    table.model().index(row, 0),
                    QItemSelectionModel.Select | QItemSelectionModel.Rows,
                )
            table.setRowHidden(1, True)

            infos = gui_auto_trade_selection.selected_stock_infos(
                SimpleNamespace(stock_table=table)
            )

        self.assertEqual(["005930"], [info[1] for info in infos])
        table.close()

    def test_selected_stock_infos_falls_back_to_selected_indexes(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            stock_dir = root / "005930_Samsung"
            stock_dir.mkdir()
            table = QTableWidget(1, 2)
            table.setSelectionBehavior(QAbstractItemView.SelectItems)
            table.setSelectionMode(QAbstractItemView.ExtendedSelection)
            code_item = QTableWidgetItem("005930")
            code_item.setData(Qt.UserRole, str(stock_dir))
            table.setItem(0, 0, code_item)
            table.setItem(0, 1, QTableWidgetItem("Samsung"))
            table.selectionModel().select(
                table.model().index(0, 1),
                QItemSelectionModel.Select,
            )

            infos = gui_auto_trade_selection.selected_stock_infos(
                SimpleNamespace(stock_table=table)
            )

        self.assertEqual([(stock_dir, "005930", "Samsung")], infos)
        table.close()

    def test_context_row_preserves_or_replaces_extended_row_selection(self) -> None:
        table = QTableWidget(3, 1)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        for row in range(3):
            table.setItem(row, 0, QTableWidgetItem(str(row)))
        selection_model = table.selectionModel()
        for row in (0, 2):
            selection_model.select(
                table.model().index(row, 0),
                QItemSelectionModel.Select | QItemSelectionModel.Rows,
            )
        window = SimpleNamespace(stock_table=table)

        gui_auto_trade_selection.ensure_context_row_selected(window, 2)
        self.assertEqual(
            [0, 2],
            sorted(index.row() for index in selection_model.selectedRows()),
        )

        gui_auto_trade_selection.ensure_context_row_selected(window, 1)
        self.assertEqual(
            [1],
            sorted(index.row() for index in selection_model.selectedRows()),
        )
        table.close()

    def test_recovery_block_message_is_reported_on_monitoring_window(self) -> None:
        parent = QWidget()
        parent.routine_table = MagicMock()
        parent.startup_recovery_session_ready = MagicMock(return_value=False)
        parent.startup_recovery_block_reason = MagicMock(return_value="INVALID_RUNTIME")
        parent.statusBar = MagicMock()
        status_bar = parent.statusBar.return_value
        target = context_menu.MainMonitoringStockTarget(
            stock_dir=Path("stocks/005930_삼성전자"),
            code="005930",
            name="삼성전자",
            routine_instance_id="instance-a",
        )
        adapter = context_menu.MainMonitoringStockOperationAdapter(parent, [target])

        self.assertFalse(adapter.require_startup_recovery_session("운영시작"))
        status_bar.showMessage.assert_called_once()
        message = status_bar.showMessage.call_args.args[0]
        self.assertIn("Recovery 완료 상태를 확인", message)
        self.assertNotIn("INVALID_RUNTIME", message)
        adapter.close()
        parent.close()

    def test_instance_toggle_uses_runtime_readback_to_choose_backend(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            stopped_dir = root / "005930_삼성전자"
            stopped_dir.mkdir()
            (stopped_dir / "state.json").write_text(
                json.dumps({"status": "STOPPED", "trade_enabled": False}),
                encoding="utf-8",
            )
            running_dir = root / "000660_SK하이닉스"
            running_dir.mkdir()
            (running_dir / "state.json").write_text(
                json.dumps({"status": "RUNNING", "trade_enabled": True}),
                encoding="utf-8",
            )

            adapter = MagicMock()
            status_bar = MagicMock()
            window = SimpleNamespace(
                _routine_instance_stock_dirs=lambda _instance_id: [stopped_dir],
                _reload_main_routine_table_preserving_view=MagicMock(),
                statusBar=lambda: status_bar,
            )
            instance = SimpleNamespace(
                instance_id="instance-a",
                display_name="지표추종매매",
            )
            with (
                patch.object(gui_windows, "routine_instance_by_id", return_value=instance),
                patch.object(
                    gui_windows,
                    "MainMonitoringStockOperationAdapter",
                    return_value=adapter,
                ) as adapter_factory,
            ):
                def start_instance() -> dict[str, object]:
                    (stopped_dir / "state.json").write_text(
                        json.dumps({"status": "MONITORING", "trade_enabled": True}),
                        encoding="utf-8",
                    )
                    return {"ok": True, "reason": "STARTED"}

                adapter.start_selected_auto_trades.side_effect = start_instance
                gui_windows.MainWindow.toggle_routine_instance_operation(
                    window,
                    "instance-a",
                )

            adapter.start_selected_auto_trades.assert_called_once()
            adapter.stop_selected_auto_trades.assert_not_called()
            targets = adapter_factory.call_args.args[1]
            self.assertEqual(["005930"], [target.code for target in targets])
            self.assertEqual(
                "multiple",
                adapter_factory.call_args.kwargs["request_scope"],
            )
            self.assertTrue(
                table_loader.auto_trade_setting_trade_started(
                    json.loads((stopped_dir / "state.json").read_text(encoding="utf-8"))
                )
            )
            self.assertIn("운영시작 완료", status_bar.showMessage.call_args.args[0])

            adapter.reset_mock()
            window._routine_instance_stock_dirs = lambda _instance_id: [running_dir]
            with (
                patch.object(gui_windows, "routine_instance_by_id", return_value=instance),
                patch.object(
                    gui_windows,
                    "MainMonitoringStockOperationAdapter",
                    return_value=adapter,
                ),
            ):
                def stop_instance() -> dict[str, object]:
                    (running_dir / "state.json").write_text(
                        json.dumps({"status": "STOPPED", "trade_enabled": False}),
                        encoding="utf-8",
                    )
                    return {"ok": True, "reason": "STOPPED"}

                adapter.stop_selected_auto_trades.side_effect = stop_instance
                gui_windows.MainWindow.toggle_routine_instance_operation(
                    window,
                    "instance-a",
                )

            adapter.stop_selected_auto_trades.assert_called_once()
            adapter.start_selected_auto_trades.assert_not_called()
            self.assertFalse(
                table_loader.auto_trade_setting_trade_started(
                    json.loads((running_dir / "state.json").read_text(encoding="utf-8"))
                )
            )
            self.assertIn("운영정지 완료", status_bar.showMessage.call_args.args[0])

    def test_blocked_start_keeps_official_state_and_reports_reason(self) -> None:
        with TemporaryDirectory() as temp:
            stock_dir = Path(temp) / "005930_삼성전자"
            stock_dir.mkdir()
            state_path = stock_dir / "state.json"
            state_path.write_text(
                json.dumps({"status": "STOPPED", "trade_enabled": False}),
                encoding="utf-8",
            )
            status_bar = MagicMock()
            adapter = MagicMock()
            adapter._last_operation_user_message = (
                "운영 시작 전에 Recovery가 완료되지 않았습니다."
            )
            adapter.start_selected_auto_trades.return_value = {
                "ok": False,
                "reason": "BLOCKED_RECOVERY",
            }
            window = SimpleNamespace(
                _routine_instance_stock_dirs=lambda _instance_id: [stock_dir],
                _reload_main_routine_table_preserving_view=MagicMock(),
                statusBar=lambda: status_bar,
            )
            instance = SimpleNamespace(
                instance_id="instance-a",
                display_name="지표추종매매",
            )
            with (
                patch.object(gui_windows, "routine_instance_by_id", return_value=instance),
                patch.object(
                    gui_windows,
                    "MainMonitoringStockOperationAdapter",
                    return_value=adapter,
                ),
            ):
                gui_windows.MainWindow.toggle_routine_instance_operation(
                    window,
                    "instance-a",
                )

            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertFalse(table_loader.auto_trade_setting_trade_started(state))
            status_message = status_bar.showMessage.call_args.args[0]
            self.assertIn("Recovery가 완료되지 않았습니다.", status_message)
            self.assertNotIn("BLOCKED_RECOVERY", status_message)
            adapter.show_operation_failure_dialog.assert_called_once_with(
                "운영시작",
                {
                    "ok": False,
                    "reason": "BLOCKED_RECOVERY",
                },
            )
            window._reload_main_routine_table_preserving_view.assert_called_once()

    def test_recovery_failure_uses_shared_dialog_message(self) -> None:
        parent = QWidget()
        parent.routine_table = MagicMock()
        target = context_menu.MainMonitoringStockTarget(
            stock_dir=Path("stocks/000660_SK하이닉스"),
            code="000660",
            name="SK하이닉스",
            routine_instance_id="instance-a",
        )
        adapter = context_menu.MainMonitoringStockOperationAdapter(parent, [target])
        adapter._last_operation_block_reason = (
            "INVALID_RUNTIME: 보유수량 있음 + 현재가 확인 불가"
        )

        with (
            patch("gui_auto_trade_run_control.show_toast") as toast,
            patch("gui_auto_trade_run_control.QMessageBox.warning") as warning,
        ):
            shown = adapter.show_operation_failure_dialog(
                "운영시작",
                {
                    "ok": False,
                    "reason": adapter._last_operation_block_reason,
                },
            )

        self.assertTrue(shown)
        toast.assert_called_once_with(
            parent=parent,
            message=(
                "운영시작할 수 없습니다. "
                "로그인, 계좌 선택 및 Recovery 완료 상태를 확인하십시오."
                "\n\n원인: 보유수량 있음 + 현재가 확인 불가"
            ),
            duration_ms=2500,
            position="center",
        )
        warning.assert_not_called()
        adapter.close()
        parent.close()

    def test_recovery_context_missing_displays_actual_login_cause(self) -> None:
        api = SimpleNamespace(
            is_connected=Mock(return_value=False),
            login_session_id=Mock(return_value=""),
        )
        window = SimpleNamespace(
            kiwoom_api=api,
            selected_account_no=Mock(return_value=""),
        )
        decision = SimpleNamespace(
            reason_code=gui_windows.RECOVERY_CONTEXT_MISSING,
            evidence=(),
        )

        message = gui_windows.MainWindow.production_recovery_block_user_message(
            window,
            decision,
        )

        self.assertEqual("키움 서버에 로그인되어 있지 않습니다.", message)
        self.assertNotIn("RECOVERY_", message)

    def test_recovery_context_missing_displays_account_selection_cause(self) -> None:
        api = SimpleNamespace(
            is_connected=Mock(return_value=True),
            login_session_id=Mock(return_value="login-a"),
        )
        window = SimpleNamespace(
            kiwoom_api=api,
            selected_account_no=Mock(return_value=""),
        )
        decision = SimpleNamespace(
            reason_code=gui_windows.RECOVERY_CONTEXT_MISSING,
            evidence=(),
        )

        message = gui_windows.MainWindow.production_recovery_block_user_message(
            window,
            decision,
        )

        self.assertEqual("운영할 계좌를 선택하십시오.", message)
        self.assertNotIn("RECOVERY_", message)

    def test_routine_recovery_block_message_uses_operator_language(self) -> None:
        cases = (
            (
                SimpleNamespace(is_connected=Mock(return_value=False)),
                "",
                None,
                "키움 서버에 로그인되어 있지 않습니다.",
            ),
            (
                SimpleNamespace(is_connected=Mock(return_value=True)),
                "",
                None,
                "사용할 계좌 정보가 아직 확인되지 않았습니다.",
            ),
            (
                SimpleNamespace(is_connected=Mock(return_value=True)),
                "12345678",
                SimpleNamespace(account_status="COLLECTING"),
                "기존 운영 상태를 확인하고 있습니다.",
            ),
            (
                SimpleNamespace(is_connected=Mock(return_value=True)),
                "12345678",
                SimpleNamespace(account_status="FAILED"),
                "이전 운영 상태를 확인하지 못했습니다.",
            ),
            (
                SimpleNamespace(is_connected=Mock(return_value=True)),
                "12345678",
                None,
                "운영 상태 확인이 아직 완료되지 않았습니다.",
            ),
        )
        for api, account_no, context, expected in cases:
            with self.subTest(expected=expected), patch.object(
                gui_windows.production_recovery_registry,
                "snapshot",
                return_value=context,
            ):
                window = SimpleNamespace(
                    kiwoom_api=api,
                    selected_account_no=Mock(return_value=account_no),
                )
                message = gui_windows.MainWindow.routine_recovery_block_message(
                    window,
                    "루틴 조기마감",
                )
                if expected == "키움 서버에 로그인되어 있지 않습니다.":
                    self.assertEqual(expected, message)
                else:
                    self.assertIn("루틴 조기마감 불가", message)
                self.assertIn(expected, message)
                self.assertNotIn("Recovery", message)
                self.assertNotIn("RECOVERY_", message)

    def test_routine_restart_login_block_is_single_line(self) -> None:
        window = SimpleNamespace(
            kiwoom_api=SimpleNamespace(is_connected=Mock(return_value=False)),
            selected_account_no=Mock(return_value=""),
        )

        message = gui_windows.MainWindow.routine_recovery_block_message(
            window,
            "루틴 재시작",
        )

        self.assertEqual("키움 서버에 로그인되어 있지 않습니다.", message)
        self.assertNotIn("\n", message)

    def test_routine_recovery_block_uses_toast_without_command(self) -> None:
        cases = (
            (
                gui_windows.MODE_EARLY_CLOSE,
                gui_windows.ROUTINE_STATUS_EARLY_CLOSE,
                "루틴 조기마감",
            ),
            (
                gui_windows.COMMAND_IMMEDIATE_LIQUIDATION,
                gui_windows.ROUTINE_STATUS_IMMEDIATE_LIQUIDATION,
                "루틴 즉시청산",
            ),
        )
        for command, display_status, expected_action in cases:
            with self.subTest(command=command):
                confirmation = MagicMock()
                confirmation.exec_.return_value = gui_windows.QMessageBox.Yes
                window = SimpleNamespace(
                    statusBar=Mock(),
                    _production_recovery_allows_routine_operation=Mock(
                        return_value=False
                    ),
                    load_routine_table=Mock(),
                    update_review_required_button_text=Mock(),
                    show_routine_recovery_block_toast=Mock(),
                )

                with (
                    patch.object(
                        gui_windows,
                        "_create_routine_operation_confirmation",
                        return_value=confirmation,
                    ),
                    patch.object(gui_windows.QMessageBox, "warning") as warning,
                    patch.object(gui_windows, "OperationCommandService") as service,
                ):
                    gui_windows.MainWindow.request_routine_operation(
                        window,
                        "instance-a",
                        "지표추종매매",
                        command,
                        display_status,
                    )

                window._production_recovery_allows_routine_operation.assert_called_once_with(
                    "instance-a",
                    command=gui_windows.MODE_EARLY_CLOSE,
                    caller_name=(
                        "EARLY_CLOSE_ROUTINE_INSTANCE"
                        if command == gui_windows.MODE_EARLY_CLOSE
                        else "IMMEDIATE_LIQUIDATION_ROUTINE_INSTANCE"
                    ),
                )
                window.show_routine_recovery_block_toast.assert_called_once_with(
                    expected_action
                )
                warning.assert_not_called()
                service.assert_not_called()

    def test_instance_close_actions_share_early_close_intent_with_explicit_method(self) -> None:
        cases = (
            (gui_windows.MODE_EARLY_CLOSE, "루틴"),
            (gui_windows.COMMAND_IMMEDIATE_LIQUIDATION, "시장가"),
        )
        for command, expected_method in cases:
            with self.subTest(command=command):
                confirmation = MagicMock()
                confirmation.exec_.return_value = gui_windows.QMessageBox.Yes
                command_result = SimpleNamespace(
                    status="SUCCESS",
                    stock_results=(SimpleNamespace(status="APPLIED"),),
                    error="",
                )
                window = SimpleNamespace(
                    statusBar=Mock(return_value=SimpleNamespace(showMessage=Mock())),
                    _production_recovery_allows_routine_operation=Mock(
                        return_value=True
                    ),
                    load_routine_table=Mock(),
                    update_review_required_button_text=Mock(),
                )

                with (
                    patch.object(
                        gui_windows,
                        "_create_routine_operation_confirmation",
                        return_value=confirmation,
                    ),
                    patch.object(
                        gui_windows,
                        "apply_close_intent",
                        return_value={"command_result": command_result},
                    ) as close_intent,
                    patch.object(
                        gui_windows,
                        "auto_trade_continue_pending_close_liquidations",
                    ) as continue_close,
                ):
                    gui_windows.MainWindow.request_routine_operation(
                        window,
                        "instance-a",
                        "지표추종매매",
                        command,
                        "조기마감" if expected_method == "루틴" else "즉시청산",
                    )

                window._production_recovery_allows_routine_operation.assert_called_once_with(
                    "instance-a",
                    command=gui_windows.MODE_EARLY_CLOSE,
                    caller_name=(
                        "EARLY_CLOSE_ROUTINE_INSTANCE"
                        if expected_method == "루틴"
                        else "IMMEDIATE_LIQUIDATION_ROUTINE_INSTANCE"
                    ),
                )
                close_intent.assert_called_once_with(
                    intent=gui_windows.CLOSE_INTENT_EARLY_CLOSE,
                    target_scope=gui_windows.SCOPE_ROUTINE_INSTANCE,
                    target_id="instance-a",
                    source="main_routine_context_menu",
                    requested_policy=expected_method,
                    project_root=gui_windows.PROJECT_ROOT,
                    operation_command_service_factory=gui_windows.OperationCommandService,
                )
                if expected_method == "시장가":
                    continue_close.assert_called_once_with(
                        window,
                        limit=None,
                        target_routine_instance_ids={"instance-a"},
                    )
                else:
                    continue_close.assert_not_called()

    def test_category_close_actions_apply_explicit_method_to_each_instance(self) -> None:
        cases = (
            (gui_windows.MODE_EARLY_CLOSE, "루틴"),
            (gui_windows.COMMAND_IMMEDIATE_LIQUIDATION, "시장가"),
        )
        for command, expected_method in cases:
            with self.subTest(command=command):
                confirmation = MagicMock()
                confirmation.exec_.return_value = gui_windows.QMessageBox.Yes
                command_result = SimpleNamespace(
                    status="SUCCESS",
                    stock_results=(SimpleNamespace(status="APPLIED"),),
                    error="",
                )
                window = SimpleNamespace(
                    _routine_instance_ids_by_definition={
                        "definition-a": ("instance-a", "instance-b")
                    },
                    _routine_instance_has_assigned_stocks=Mock(return_value=True),
                    _production_recovery_allows_routine_operation=Mock(
                        return_value=True
                    ),
                    load_routine_table=Mock(),
                    update_review_required_button_text=Mock(),
                    statusBar=Mock(return_value=SimpleNamespace(showMessage=Mock())),
                )

                with (
                    patch.object(gui_windows, "routine_instance_checked", return_value=True),
                    patch.object(
                        gui_windows,
                        "_create_routine_operation_confirmation",
                        return_value=confirmation,
                    ),
                    patch.object(
                        gui_windows,
                        "apply_close_intent",
                        side_effect=(
                            {"command_result": command_result},
                            {"command_result": command_result},
                        ),
                    ) as close_intent,
                    patch.object(
                        gui_windows,
                        "auto_trade_continue_pending_close_liquidations",
                    ) as continue_close,
                ):
                    gui_windows.MainWindow.request_routine_definition_operation(
                        window,
                        "definition-a",
                        "지표추종매매",
                        command,
                        "조기마감" if expected_method == "루틴" else "즉시청산",
                    )

                self.assertEqual(2, close_intent.call_count)
                self.assertEqual(
                    {"instance-a", "instance-b"},
                    {call.kwargs["target_id"] for call in close_intent.call_args_list},
                )
                self.assertTrue(
                    all(
                        call.kwargs["intent"] == gui_windows.CLOSE_INTENT_EARLY_CLOSE
                        and call.kwargs["requested_policy"] == expected_method
                        for call in close_intent.call_args_list
                    )
                )
                if expected_method == "시장가":
                    self.assertEqual(2, continue_close.call_count)
                    self.assertEqual(
                        {frozenset({"instance-a"}), frozenset({"instance-b"})},
                        {
                            frozenset(call.kwargs["target_routine_instance_ids"])
                            for call in continue_close.call_args_list
                        },
                    )
                    self.assertTrue(
                        all(call.kwargs["limit"] is None for call in continue_close.call_args_list)
                    )
                else:
                    continue_close.assert_not_called()
                self.assertTrue(
                    all(
                        call.kwargs.get("requested_policy") != ""
                        for call in close_intent.call_args_list
                    )
                )

    def test_routine_recovery_global_block_does_not_write_any_stock(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            stock_dirs = []
            before = {}
            for index in range(8):
                stock_dir = root / f"{index:06d}_stock-{index}"
                stock_dir.mkdir()
                state_path = stock_dir / "state.json"
                state_path.write_text(
                    json.dumps(
                        {
                            "status": "MONITORING",
                            "review_required": False,
                        }
                    ),
                    encoding="utf-8",
                )
                stock_dirs.append(stock_dir)
                before[state_path] = state_path.read_bytes()

            decision = SimpleNamespace(
                allowed=False,
                reason_code=gui_windows.RECOVERY_CONTEXT_MISSING,
                evidence=("caller=EARLY_CLOSE_ROUTINE_INSTANCE",),
            )
            window = SimpleNamespace(
                _routine_instance_stock_dirs=Mock(return_value=stock_dirs),
                production_recovery_gate_for_stock=Mock(return_value=decision),
                kiwoom_api=SimpleNamespace(
                    login_session_id=Mock(return_value="")
                ),
                selected_account_no=Mock(return_value=""),
                update_runtime_stock_status=Mock(),
            )

            stdout = StringIO()
            stderr = StringIO()
            with (
                patch.object(gui_windows.LOGGER, "warning") as warning,
                patch.object(gui_windows.LOGGER, "exception") as exception,
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                allowed = (
                    gui_windows.MainWindow._production_recovery_allows_routine_operation(
                        window,
                        "instance-a",
                        command=gui_windows.MODE_EARLY_CLOSE,
                        caller_name="EARLY_CLOSE_ROUTINE_INSTANCE",
                    )
                )

            self.assertFalse(allowed)
            window.production_recovery_gate_for_stock.assert_called_once_with(
                "000000",
                caller_name="EARLY_CLOSE_ROUTINE_INSTANCE",
            )
            window.update_runtime_stock_status.assert_not_called()
            warning.assert_not_called()
            exception.assert_not_called()
            self.assertEqual("", stdout.getvalue())
            self.assertEqual("", stderr.getvalue())
            for state_path, content in before.items():
                self.assertEqual(content, state_path.read_bytes())

    def test_repeated_routine_recovery_blocks_never_write_review_state(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            stock_dirs = []
            before = {}
            for index in range(8):
                stock_dir = root / f"{index:06d}_stock-{index}"
                stock_dir.mkdir()
                state_path = stock_dir / "state.json"
                state_path.write_text(
                    json.dumps(
                        {
                            "status": "MONITORING",
                            "review_required": False,
                        }
                    ),
                    encoding="utf-8",
                )
                stock_dirs.append(stock_dir)
                before[state_path] = state_path.read_bytes()

            decision = SimpleNamespace(
                allowed=False,
                reason_code=gui_windows.RECOVERY_CONTEXT_MISSING,
                evidence=(),
            )
            window = SimpleNamespace(
                _routine_instance_stock_dirs=Mock(return_value=stock_dirs),
                production_recovery_gate_for_stock=Mock(return_value=decision),
                kiwoom_api=SimpleNamespace(
                    login_session_id=Mock(return_value="")
                ),
                selected_account_no=Mock(return_value=""),
                update_runtime_stock_status=Mock(),
            )
            commands = (
                gui_windows.MODE_EARLY_CLOSE,
                gui_windows.COMMAND_IMMEDIATE_LIQUIDATION,
                gui_windows.MODE_EARLY_CLOSE,
            )

            with patch.object(gui_windows.LOGGER, "warning") as warning:
                for command in commands:
                    self.assertFalse(
                        gui_windows.MainWindow._production_recovery_allows_routine_operation(
                            window,
                            "instance-a",
                            command=command,
                            caller_name=(
                                "EARLY_CLOSE_ROUTINE_INSTANCE"
                                if command == gui_windows.MODE_EARLY_CLOSE
                                else "IMMEDIATE_LIQUIDATION_ROUTINE_INSTANCE"
                            ),
                        )
                    )

            warning.assert_not_called()
            self.assertEqual(
                len(commands),
                window.production_recovery_gate_for_stock.call_count,
            )
            window.update_runtime_stock_status.assert_not_called()
            for state_path, content in before.items():
                self.assertEqual(content, state_path.read_bytes())

    def test_unknown_recovery_reason_keeps_diagnostic_log(self) -> None:
        stock_dir = Path("000000_stock")
        decision = SimpleNamespace(
            allowed=False,
            reason_code="RECOVERY_UNKNOWN_CONTRACT_STATE",
            evidence=(),
        )
        window = SimpleNamespace(
            _routine_instance_stock_dirs=Mock(return_value=[stock_dir]),
            production_recovery_gate_for_stock=Mock(return_value=decision),
            kiwoom_api=SimpleNamespace(
                login_session_id=Mock(return_value="login-a")
            ),
            selected_account_no=Mock(return_value="12345678"),
            update_runtime_stock_status=Mock(),
        )

        with patch.object(gui_windows.LOGGER, "warning") as warning:
            allowed = (
                gui_windows.MainWindow._production_recovery_allows_routine_operation(
                    window,
                    "instance-a",
                    command=gui_windows.MODE_EARLY_CLOSE,
                    caller_name="EARLY_CLOSE_ROUTINE_INSTANCE",
                )
            )

        self.assertFalse(allowed)
        warning.assert_called_once()
        window.update_runtime_stock_status.assert_not_called()

    def test_registry_error_evidence_keeps_diagnostic_log(self) -> None:
        stock_dir = Path("000000_stock")
        decision = SimpleNamespace(
            allowed=False,
            reason_code=gui_windows.RECOVERY_CONTEXT_MISSING,
            evidence=("registry_error=RuntimeError",),
        )
        window = SimpleNamespace(
            _routine_instance_stock_dirs=Mock(return_value=[stock_dir]),
            production_recovery_gate_for_stock=Mock(return_value=decision),
            kiwoom_api=SimpleNamespace(
                login_session_id=Mock(return_value="login-a")
            ),
            selected_account_no=Mock(return_value="12345678"),
            update_runtime_stock_status=Mock(),
        )

        with patch.object(gui_windows.LOGGER, "warning") as warning:
            allowed = (
                gui_windows.MainWindow._production_recovery_allows_routine_operation(
                    window,
                    "instance-a",
                    command=gui_windows.COMMAND_IMMEDIATE_LIQUIDATION,
                    caller_name="IMMEDIATE_LIQUIDATION_ROUTINE_INSTANCE",
                )
            )

        self.assertFalse(allowed)
        warning.assert_called_once()
        window.update_runtime_stock_status.assert_not_called()

    def test_recovery_gate_exception_is_fail_closed_and_logged(self) -> None:
        window = SimpleNamespace(
            _routine_instance_stock_dirs=Mock(
                return_value=[Path("000000_stock")]
            ),
            production_recovery_gate_for_stock=Mock(
                side_effect=RuntimeError("registry failed")
            ),
            update_runtime_stock_status=Mock(),
        )

        with patch.object(gui_windows.LOGGER, "exception") as exception:
            allowed = (
                gui_windows.MainWindow._production_recovery_allows_routine_operation(
                    window,
                    "instance-a",
                    command=gui_windows.MODE_EARLY_CLOSE,
                    caller_name="EARLY_CLOSE_ROUTINE_INSTANCE",
                )
            )

        self.assertFalse(allowed)
        exception.assert_called_once()
        window.update_runtime_stock_status.assert_not_called()

    def test_routine_definition_recovery_block_shows_one_toast(self) -> None:
        confirmation = MagicMock()
        confirmation.exec_.return_value = gui_windows.QMessageBox.Yes
        window = SimpleNamespace(
            _routine_instance_ids_by_definition={
                "indicator-follow": ("instance-a", "instance-b")
            },
            _routine_instance_has_assigned_stocks=Mock(return_value=True),
            _production_recovery_allows_routine_operation=Mock(
                side_effect=(False, False)
            ),
            load_routine_table=Mock(),
            update_review_required_button_text=Mock(),
            show_routine_recovery_block_toast=Mock(),
            statusBar=Mock(
                return_value=SimpleNamespace(showMessage=Mock())
            ),
        )
        service = Mock()

        with (
            patch.object(
                gui_windows,
                "routine_instance_checked",
                return_value=True,
            ),
            patch.object(
                gui_windows,
                "_create_routine_operation_confirmation",
                return_value=confirmation,
            ),
            patch.object(
                gui_windows,
                "OperationCommandService",
                return_value=service,
            ),
            patch.object(gui_windows.QMessageBox, "warning") as warning,
        ):
            gui_windows.MainWindow.request_routine_definition_operation(
                window,
                "indicator-follow",
                "지표추종매매",
                gui_windows.MODE_EARLY_CLOSE,
                gui_windows.ROUTINE_STATUS_EARLY_CLOSE,
            )

        self.assertEqual(
            2,
            window._production_recovery_allows_routine_operation.call_count,
        )
        service.apply.assert_not_called()
        window.show_routine_recovery_block_toast.assert_called_once_with(
            "카테고리 조기마감"
        )
        warning.assert_not_called()

    def test_instance_restart_adapter_uses_shared_routine_block_formatter(self) -> None:
        expected = (
            "루틴 재시작 불가\n\n"
            "프로그램 시작 후 운영 상태 확인이 아직 완료되지 않았습니다.\n"
            "잠시 후 다시 시도해 주세요."
        )
        host = SimpleNamespace(
            filter_start_targets_by_recovery=Mock(
                return_value={
                    "allowed": False,
                    "reason": "RECOVERY_NOT_STARTED",
                    "user_message": "",
                }
            )
        )
        window = SimpleNamespace(
            routine_recovery_block_message=Mock(return_value=expected),
        )
        adapter = SimpleNamespace(
            _window=window,
            _recovery_action_label="루틴 재시작",
            _execution_host=Mock(return_value=host),
        )

        result = (
            context_menu.MainMonitoringStockOperationAdapter.filter_start_targets_by_recovery(
                adapter,
                [],
                action="운영시작",
            )
        )

        self.assertEqual(expected, result["user_message"])
        window.routine_recovery_block_message.assert_called_once_with("루틴 재시작")

    def test_recovery_states_have_actionable_messages_without_codes(self) -> None:
        api = SimpleNamespace(
            is_connected=Mock(return_value=True),
            login_session_id=Mock(return_value="login-a"),
        )
        window = SimpleNamespace(
            kiwoom_api=api,
            selected_account_no=Mock(return_value="12345678"),
        )
        cases = {
            gui_windows.RECOVERY_NOT_STARTED: "Recovery가 완료되지 않았습니다.",
            gui_windows.RECOVERY_IN_PROGRESS: "Recovery가 진행 중입니다.",
            gui_windows.RECOVERY_ACCOUNT_FAILED: "계좌 Recovery에 실패했습니다.",
            gui_windows.RECOVERY_STALE_SESSION: "현재 세션에서 사용할 수 없습니다.",
            gui_windows.RECOVERY_STOCK_PENDING: "종목의 Recovery가 아직 완료되지 않았습니다.",
            gui_windows.RECOVERY_STOCK_FAILED: "종목의 Recovery에 실패했습니다.",
        }

        for reason_code, expected in cases.items():
            with self.subTest(reason_code=reason_code):
                decision = SimpleNamespace(reason_code=reason_code, evidence=())
                message = (
                    gui_windows.MainWindow.production_recovery_block_user_message(
                        window,
                        decision,
                    )
                )
                self.assertIn(expected, message)
                self.assertNotIn(reason_code, message)

    def test_recovery_registry_read_failure_has_recovery_action(self) -> None:
        api = SimpleNamespace(
            is_connected=Mock(return_value=True),
            login_session_id=Mock(return_value="login-a"),
        )
        window = SimpleNamespace(
            kiwoom_api=api,
            selected_account_no=Mock(return_value="12345678"),
        )
        decision = SimpleNamespace(
            reason_code=gui_windows.RECOVERY_CONTEXT_MISSING,
            evidence=("registry_error=broken registry",),
        )

        message = gui_windows.MainWindow.production_recovery_block_user_message(
            window,
            decision,
        )

        self.assertIn("Recovery 데이터를 읽을 수 없습니다.", message)
        self.assertIn("복구를 다시 실행", message)
        self.assertNotIn("registry_error", message)

    def test_recovery_account_failure_uses_preserved_runtime_cause(self) -> None:
        api = SimpleNamespace(
            is_connected=Mock(return_value=True),
            login_session_id=Mock(return_value="login-a"),
        )
        window = SimpleNamespace(
            kiwoom_api=api,
            selected_account_no=Mock(return_value="12345678"),
            _production_recovery_failure_reason_code="DAMAGED_RUNTIME",
        )
        decision = SimpleNamespace(
            reason_code=gui_windows.RECOVERY_ACCOUNT_FAILED,
            evidence=(),
        )

        message = gui_windows.MainWindow.production_recovery_block_user_message(
            window,
            decision,
        )

        self.assertIn("Runtime 데이터를 읽을 수 없어", message)
        self.assertIn("검토관리", message)
        self.assertNotIn("DAMAGED_RUNTIME", message)

    def test_recovery_account_failure_uses_preserved_timer_cause(self) -> None:
        api = SimpleNamespace(
            is_connected=Mock(return_value=True),
            login_session_id=Mock(return_value="login-a"),
        )
        window = SimpleNamespace(
            kiwoom_api=api,
            selected_account_no=Mock(return_value="12345678"),
            _production_recovery_failure_reason_code="RECOVERY_TIMER_START_FAILED",
        )
        decision = SimpleNamespace(
            reason_code=gui_windows.RECOVERY_ACCOUNT_FAILED,
            evidence=(),
        )

        message = gui_windows.MainWindow.production_recovery_block_user_message(
            window,
            decision,
        )

        self.assertIn("운영 주기 실행을 시작하지 못했습니다.", message)
        self.assertIn("Recovery를 다시 실행", message)
        self.assertNotIn("RECOVERY_TIMER_START_FAILED", message)

    def test_runtime_timer_exception_hides_internal_exception(self) -> None:
        window = SimpleNamespace(
            _runtime_file_snapshot=(("state.json", 1, 1),),
            statusBarMessage=Mock(),
        )
        with (
            patch(
                "gui_auto_trade_setting_window.auto_trade_on_runtime_file_timer_tick",
                side_effect=RuntimeError("secret runtime error"),
            ),
            patch("gui_auto_trade_setting_window.LOGGER.exception"),
        ):
            gui_windows.AutoTradeSettingWindow.on_runtime_file_timer_tick(window)

        message = window.statusBarMessage.call_args.args[0]
        self.assertIn("Runtime 상태를 갱신하지 못했습니다.", message)
        self.assertNotIn("secret runtime error", message)

    def test_time_policy_timer_exception_hides_internal_exception(self) -> None:
        window = SimpleNamespace(
            _last_time_policy_minute_key="10:00",
            statusBarMessage=Mock(),
        )
        with (
            patch(
                "gui_auto_trade_setting_window.auto_trade_on_time_policy_timer_tick",
                side_effect=RuntimeError("secret timer error"),
            ),
            patch("gui_auto_trade_setting_window.LOGGER.exception"),
        ):
            gui_windows.AutoTradeSettingWindow.on_time_policy_timer_tick(window)

        message = window.statusBarMessage.call_args.args[0]
        self.assertIn("시간정책 상태를 갱신하지 못했습니다.", message)
        self.assertNotIn("secret timer error", message)

    def test_internal_reason_code_is_not_exposed_by_dialog_fallback(self) -> None:
        parent = QWidget()
        parent.routine_table = MagicMock()
        target = context_menu.MainMonitoringStockTarget(
            stock_dir=Path("stocks/005930_삼성전자"),
            code="005930",
            name="삼성전자",
            routine_instance_id="instance-a",
        )
        adapter = context_menu.MainMonitoringStockOperationAdapter(parent, [target])

        with (
            patch("gui_auto_trade_run_control.show_toast") as toast,
            patch("gui_auto_trade_run_control.QMessageBox.warning") as warning,
        ):
            shown = adapter.show_operation_failure_dialog(
                "운영시작",
                {"ok": False, "reason": "RECOVERY_CONTEXT_MISSING"},
            )

        self.assertTrue(shown)
        message = toast.call_args.kwargs["message"]
        self.assertNotIn("RECOVERY_CONTEXT_MISSING", message)
        self.assertIn("로그인, 계좌 및 운영 상태", message)
        warning.assert_not_called()
        adapter.close()
        parent.close()

    def test_review_required_failure_displays_stock_and_official_reason(self) -> None:
        with TemporaryDirectory() as temp:
            stock_dir = Path(temp) / "000660_SK하이닉스"
            stock_dir.mkdir()
            (stock_dir / "state.json").write_text(
                json.dumps(
                    {
                        "status": "REVIEW_REQUIRED",
                        "review_reason": "보유수량 있음 + 현재가 확인 불가",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            parent = QWidget()
            parent.routine_table = MagicMock()
            target = context_menu.MainMonitoringStockTarget(
                stock_dir=stock_dir,
                code="000660",
                name="SK하이닉스",
                routine_instance_id="instance-a",
            )
            adapter = context_menu.MainMonitoringStockOperationAdapter(parent, [target])

            with (
                patch("gui_auto_trade_run_control.show_toast") as toast,
                patch("gui_auto_trade_run_control.QMessageBox.warning") as warning,
            ):
                shown = adapter.show_operation_failure_dialog(
                    "운영시작",
                    {"ok": False, "reason": "REVIEW_REQUIRED"},
                )

            self.assertTrue(shown)
            message = toast.call_args.kwargs["message"]
            self.assertIn("000660 SK하이닉스", message)
            self.assertIn("보유수량 있음 + 현재가 확인 불가", message)
            self.assertIn("검토관리", message)
            warning.assert_not_called()
            adapter.close()
            parent.close()

    def test_backend_warning_is_not_duplicated_by_monitoring_presenter(self) -> None:
        parent = QWidget()
        parent.routine_table = MagicMock()
        target = context_menu.MainMonitoringStockTarget(
            stock_dir=Path("stocks/000660_SK하이닉스"),
            code="000660",
            name="SK하이닉스",
            routine_instance_id="instance-a",
        )
        adapter = context_menu.MainMonitoringStockOperationAdapter(parent, [target])
        adapter._last_operation_failure_dialog_shown = True

        with (
            patch("gui_auto_trade_run_control.show_toast") as toast,
            patch("gui_auto_trade_run_control.QMessageBox.warning") as warning,
        ):
            shown = adapter.show_operation_failure_dialog(
                "운영시작",
                {"ok": False, "reason": "START_FAILED"},
            )

        self.assertFalse(shown)
        toast.assert_not_called()
        warning.assert_not_called()
        adapter.close()
        parent.close()

    def test_backend_exception_uses_critical_dialog_and_keeps_state(self) -> None:
        with TemporaryDirectory() as temp:
            stock_dir = Path(temp) / "005930_삼성전자"
            stock_dir.mkdir()
            state_path = stock_dir / "state.json"
            state_path.write_text(
                json.dumps({"status": "STOPPED", "trade_enabled": False}),
                encoding="utf-8",
            )
            status_bar = MagicMock()
            adapter = MagicMock()
            adapter.start_selected_auto_trades.side_effect = RuntimeError("backend failed")
            window = SimpleNamespace(
                _routine_instance_stock_dirs=lambda _instance_id: [stock_dir],
                _reload_main_routine_table_preserving_view=MagicMock(),
                statusBar=lambda: status_bar,
            )
            instance = SimpleNamespace(
                instance_id="instance-a",
                display_name="지표추종매매",
            )
            with (
                patch.object(gui_windows, "routine_instance_by_id", return_value=instance),
                patch.object(
                    gui_windows,
                    "MainMonitoringStockOperationAdapter",
                    return_value=adapter,
                ),
                patch.object(gui_windows.QMessageBox, "critical") as critical,
            ):
                gui_windows.MainWindow.toggle_routine_instance_operation(
                    window,
                    "instance-a",
                )

            critical.assert_called_once()
            self.assertNotIn("backend failed", critical.call_args.args[2])
            self.assertIn("로그를 확인한 뒤 다시 시도", critical.call_args.args[2])
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertFalse(table_loader.auto_trade_setting_trade_started(state))

    def test_review_stock_stale_trade_flag_does_not_choose_stop_backend(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            normal_dir = root / "005930_삼성전자"
            review_dir = root / "000660_SK하이닉스"
            normal_dir.mkdir()
            review_dir.mkdir()
            (normal_dir / "state.json").write_text(
                json.dumps({"status": "STOPPED", "trade_enabled": False}),
                encoding="utf-8",
            )
            review_before = {
                "status": "REVIEW_REQUIRED",
                "review_required": True,
                "trade_enabled": True,
            }
            (review_dir / "state.json").write_text(
                json.dumps(review_before, ensure_ascii=False),
                encoding="utf-8",
            )
            adapter = MagicMock()
            status_bar = MagicMock()
            window = SimpleNamespace(
                _routine_instance_stock_dirs=lambda _instance_id: [
                    review_dir,
                    normal_dir,
                ],
                _reload_main_routine_table_preserving_view=MagicMock(),
                statusBar=lambda: status_bar,
            )
            instance = SimpleNamespace(
                instance_id="instance-a",
                display_name="지표추종매매",
            )

            def start_instance() -> dict[str, object]:
                (normal_dir / "state.json").write_text(
                    json.dumps({"status": "MONITORING", "trade_enabled": True}),
                    encoding="utf-8",
                )
                return {
                    "ok": True,
                    "reason": "STARTED",
                    "excluded_review": ("000660 SK하이닉스",),
                }

            adapter.start_selected_auto_trades.side_effect = start_instance
            with (
                patch.object(gui_windows, "routine_instance_by_id", return_value=instance),
                patch.object(
                    gui_windows,
                    "MainMonitoringStockOperationAdapter",
                    return_value=adapter,
                ) as adapter_factory,
            ):
                gui_windows.MainWindow.toggle_routine_instance_operation(
                    window,
                    "instance-a",
                )

            adapter.start_selected_auto_trades.assert_called_once()
            adapter.stop_selected_auto_trades.assert_not_called()
            targets = adapter_factory.call_args.args[1]
            self.assertEqual(
                {"005930"},
                {target.code for target in targets},
            )
            review_after = json.loads(
                (review_dir / "state.json").read_text(encoding="utf-8")
            )
            self.assertEqual(review_before, review_after)

    def test_all_review_stocks_do_not_enter_start_backend_or_failure_toast(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            review_dirs = [
                root / "000660_SK하이닉스",
                root / "068270_셀트리온",
            ]
            for review_dir in review_dirs:
                review_dir.mkdir()
                (review_dir / "state.json").write_text(
                    json.dumps(
                        {
                            "status": "REVIEW_REQUIRED",
                            "review_required": True,
                            "trade_enabled": False,
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )

            reload_table = MagicMock()
            status_bar = MagicMock()
            window = SimpleNamespace(
                _routine_instance_stock_dirs=lambda _instance_id: review_dirs,
                _reload_main_routine_table_preserving_view=reload_table,
                statusBar=lambda: status_bar,
            )
            instance = SimpleNamespace(
                instance_id="instance-a",
                display_name="지표추종매매",
            )

            with (
                patch.object(gui_windows, "routine_instance_by_id", return_value=instance),
                patch.object(
                    gui_windows,
                    "MainMonitoringStockOperationAdapter",
                ) as adapter_factory,
                patch.object(
                    gui_windows,
                    "show_auto_trade_operation_failure_dialog",
                ) as show_failure,
            ):
                gui_windows.MainWindow.toggle_routine_instance_operation(
                    window,
                    "instance-a",
                )

            adapter_factory.assert_not_called()
            show_failure.assert_not_called()
            reload_table.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
