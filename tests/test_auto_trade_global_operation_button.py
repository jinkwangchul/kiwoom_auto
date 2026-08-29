from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QCoreApplication, QEvent
from PyQt5.QtWidgets import QApplication, QPushButton, QTableWidget, QTableWidgetItem, QWidget

import gui_auto_trade_run_control as run_control
import gui_auto_trade_policy as auto_trade_policy
import gui_main_emergency_ops as emergency_ops
import gui_main_stock_context_menu as monitoring_context_menu
import gui_auto_trade_setting_window as setting_window
import gui_auto_trade_status_ops as status_ops
import operation_policy_gate
from runtime_io import read_json_dict


class _OperationButtonHarness(QWidget):
    registered_operation_targets = (
        setting_window.AutoTradeSettingWindow.registered_operation_targets
    )
    registered_operation_start_targets = (
        setting_window.AutoTradeSettingWindow.registered_operation_start_targets
    )
    running_registered_operation_targets = (
        setting_window.AutoTradeSettingWindow.running_registered_operation_targets
    )
    update_global_operation_button_state = (
        setting_window.AutoTradeSettingWindow.update_global_operation_button_state
    )
    start_selected_auto_trades = (
        setting_window.AutoTradeSettingWindow.start_selected_auto_trades
    )
    start_selected_rows_auto_trades = (
        setting_window.AutoTradeSettingWindow.start_selected_rows_auto_trades
    )
    on_stock_table_name_item_double_clicked = (
        setting_window.AutoTradeSettingWindow.on_stock_table_name_item_double_clicked
    )
    toggle_stock_operation_exclusion = (
        setting_window.AutoTradeSettingWindow.toggle_stock_operation_exclusion
    )
    emergency_stop_selected_auto_trade_stocks = (
        setting_window.AutoTradeSettingWindow.emergency_stop_selected_auto_trade_stocks
    )

    def release_selected_emergency_stopped_auto_trade_stocks(self):
        # Backend-only regression harness. Production UI no longer exposes this action.
        return emergency_ops.execute_selected_emergency_release(
            self, self.selected_stock_infos()
        )

    def __init__(self) -> None:
        super().__init__()
        self.btn_start = QPushButton("■ 운영시작", self)
        self.stock_table = QTableWidget(0, 2, self)
        self.status_messages: list[str] = []
        self._selected_stock_infos: list[tuple[Path, str, str]] = []
        self.login_ready = True
        self.account_ready = True
        self.recovery_ready = True
        self.btn_start.clicked.connect(self.start_selected_auto_trades)

    def startup_recovery_session_ready(self, *, refresh: bool = True) -> bool:
        return self.recovery_ready

    def filter_start_targets_by_recovery(self, targets, *, action: str):
        if not self.login_ready:
            return {
                "allowed": False,
                "reason": "LOGIN_REQUIRED",
                "user_message": "키움 서버에 로그인되어 있지 않습니다.",
                "eligible": (),
                "excluded_review": (),
            }
        if not self.account_ready:
            return {
                "allowed": False,
                "reason": "ACCOUNT_REQUIRED",
                "user_message": "운영할 계좌가 선택되지 않았습니다.",
                "eligible": (),
                "excluded_review": (),
            }
        if not self.recovery_ready:
            return {
                "allowed": False,
                "reason": "RECOVERY_NOT_READY",
                "user_message": "오늘의 복구 확인이 완료되지 않았습니다.",
                "eligible": (),
                "excluded_review": (),
            }
        return {
            "allowed": True,
            "reason": "READY",
            "eligible": tuple(targets),
            "excluded_review": (),
        }

    def selected_stock_infos(self):
        return list(self._selected_stock_infos)

    def current_selected_routine_name(self) -> str:
        return ""

    def production_recovery_stock_is_review_required(self, _code: str) -> bool:
        return False

    def split_start_targets(self, selected):
        return setting_window.AutoTradeSettingWindow.split_start_targets(
            self,
            selected,
        )

    def start_target_is_review_isolated(
        self,
        _stock_dir: Path,
        _code: str,
    ) -> bool:
        return False

    def pre_start_review_check(self, routine_name, stock_dir, code, name):
        return {"routine_name": routine_name, "review_reasons": []}

    def mark_review_required(self, *_args, **_kwargs) -> bool:
        return True

    def update_stock_status(
        self,
        stock_dir: Path,
        code: str,
        name: str,
        new_status: str,
        extra_state=None,
        log_suffix: str = "",
    ) -> bool:
        return status_ops.auto_trade_update_stock_status(
            self,
            stock_dir,
            code,
            name,
            new_status,
            extra_state,
            log_suffix,
        )

    def recalculate_stock_status_by_operation_policy(
        self,
        stock_dir: Path,
        code: str,
        name: str,
        reason: str,
        extra_state=None,
        silent_unchanged: bool = False,
    ):
        return status_ops.auto_trade_recalculate_stock_status_by_operation_policy(
            self,
            stock_dir,
            code,
            name,
            reason,
            extra_state,
            silent_unchanged,
        )

    def refresh_all(self) -> None:
        self.update_global_operation_button_state()

    def statusBarMessage(self, message: str, timeout_ms: int = 5000) -> None:
        self.status_messages.append(message)

    def show_auto_trade_result_dialog(self, *_args, **_kwargs) -> None:
        return None

    def open_review_required_window(self) -> None:
        return None

    def rebind_startup_recovery_after_trusted_runtime_update(self) -> bool:
        return True

    def operation_message_parent(self):
        return self

    def stock_info_from_row(self, row: int):
        if row < 0 or row >= self.stock_table.rowCount():
            return None
        code_item = self.stock_table.item(row, 0)
        name_item = self.stock_table.item(row, 1)
        if code_item is None or name_item is None:
            return None
        for target in self.registered_operation_targets():
            if target[1] == code_item.text():
                return target
        return None


class AutoTradeGlobalOperationButtonTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.start_clock_patcher = patch.object(
            run_control,
            "current_datetime",
            return_value=datetime(2026, 8, 10, 10, 0, 0),
        )
        self.start_clock_patcher.start()
        self.addCleanup(self.start_clock_patcher.stop)
        self.start_session_phase_patcher = patch(
            "gui_auto_trade_policy.auto_trade_operation_session_phase",
            return_value={
                "evaluable": True,
                "phase": "ACTIVE_SESSION",
                "mode": "CONTINUOUS",
                "active": True,
                "future_session_exists": False,
            },
        )
        self.start_session_phase_patcher.start()
        self.addCleanup(self.start_session_phase_patcher.stop)
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.operation_state_path = self.root / "runtime" / "operation_state.json"
        self.operation_state_path.parent.mkdir()
        self.operation_state_path.write_text("{}", encoding="utf-8")
        self.targets = [
            self._create_stock(index)
            for index in range(1, 13)
        ]
        self.window = _OperationButtonHarness()
        self.window.registered_operation_targets = lambda: list(self.targets)
        self.addCleanup(self._dispose_window)
        self.registered_patcher = patch.object(
            run_control,
            "all_registered_stock_dirs",
            return_value=[target[0] for target in self.targets],
        )
        self.registered_patcher.start()
        self.addCleanup(self.registered_patcher.stop)
        self.changelog_patcher = patch.object(run_control, "append_changelog")
        self.changelog_patcher.start()
        self.addCleanup(self.changelog_patcher.stop)
        self.operation_state_patcher = patch.object(
            operation_policy_gate,
            "OPERATION_STATE_PATH",
            self.operation_state_path,
        )
        self.operation_state_patcher.start()
        self.addCleanup(self.operation_state_patcher.stop)
        self.window.update_global_operation_button_state()

    def _dispose_window(self) -> None:
        self.window.close()
        self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.processEvents()

    def _create_stock(self, index: int):
        code = f"{index:06d}"
        name = f"테스트{index}"
        stock_dir = self.root / f"{code}_{name}"
        stock_dir.mkdir()
        (stock_dir / "config.json").write_text(
            json.dumps(
                {
                    "operation_mode": "CONTINUOUS",
                    "trade_amount_type": "QUANTITY",
                    "buy_qty": 1,
                    "assigned_routine_instance_id": "instance-test",
                    "routine_instance_name": "테스트 루틴",
                    "real_trade_enabled": True,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self._write_state(stock_dir, status="STOPPED", trade_enabled=False)
        return stock_dir, code, name

    @staticmethod
    def _write_state(
        stock_dir: Path,
        *,
        status: str,
        trade_enabled: bool,
        **extra,
    ) -> None:
        state = {
            "status": status,
            "trade_enabled": trade_enabled,
            "holding_qty": 0,
        }
        if status == "EMERGENCY_STOPPED" and "emergency_scope" not in extra:
            state["emergency_scope"] = "SELECTED"
        state.update(extra)
        (stock_dir / "state.json").write_text(
            json.dumps(
                state,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    @staticmethod
    def _write_operation_excluded(stock_dir: Path, excluded: bool) -> None:
        config_path = stock_dir / "config.json"
        config = read_json_dict(config_path)
        config["operation_excluded"] = excluded
        config_path.write_text(
            json.dumps(config, ensure_ascii=False),
            encoding="utf-8",
        )

    def _write_operation_state(self, value: dict) -> None:
        self.operation_state_path.write_text(
            json.dumps(value, ensure_ascii=False),
            encoding="utf-8",
        )

    def test_operation_start_summary_uses_existing_toast_without_modal(self) -> None:
        self.window.show_auto_trade_result_dialog = Mock()
        result = {
            "started_count": 1,
            "blocked_count": 2,
            "failed_count": 0,
            "blocked_target_details": (
                {
                    "stock_code": "111111",
                    "reason": "FINAL_SESSION_ENDED",
                    "operation_mode": "SCHEDULED",
                },
                {
                    "stock_code": "222222",
                    "reason": "FINAL_SESSION_ENDED",
                    "operation_mode": "SCHEDULED",
                },
            ),
        }

        with patch.object(run_control, "show_toast") as toast:
            run_control._show_operation_start_summary_toast(self.window, result)

        toast.assert_called_once_with(
            parent=self.window,
            message="대상종목 3  |  기운영중 0  |  운영시작 1  |  운영불가 2\n시간운영 종료 2",
            duration_ms=3200,
            position="center",
        )
        self.window.show_auto_trade_result_dialog.assert_not_called()

    def test_all_running_operation_start_shows_fixed_summary_without_backend(self) -> None:
        for target in self.targets[:5]:
            self._write_state(target[0], status="RUNNING", trade_enabled=True)
        self.window._selected_stock_infos = list(self.targets[:5])
        before = {
            target[0]: (target[0] / "state.json").read_bytes()
            for target in self.targets[:5]
        }

        with (
            patch.object(
                self.window,
                "running_registered_operation_targets",
                return_value=list(self.targets[:5]),
            ),
            patch.object(run_control, "auto_trade_start_selected_auto_trades") as backend,
            patch.object(run_control, "show_toast") as toast,
        ):
            result = run_control.auto_trade_start_selected_rows_auto_trades(self.window)

        backend.assert_not_called()
        toast.assert_called_once()
        self.assertEqual(
            "대상종목 5  |  기운영중 5  |  운영시작 0  |  운영불가 0",
            toast.call_args.kwargs["message"],
        )
        self.assertEqual(before, {
            target[0]: (target[0] / "state.json").read_bytes()
            for target in self.targets[:5]
        })
        self.assertEqual(5, result["already_running_count"])
        self.assertEqual(0, result["started_count"])

    def test_global_all_running_operation_start_shows_fixed_summary_without_backend(self) -> None:
        for target in self.targets[:5]:
            self._write_state(target[0], status="RUNNING", trade_enabled=True)
        before = {
            target[0]: (target[0] / "state.json").read_bytes()
            for target in self.targets[:5]
        }

        with (
            patch.object(
                self.window,
                "running_registered_operation_targets",
                return_value=list(self.targets[:5]),
            ),
            patch.object(
                self.window,
                "registered_operation_start_targets",
                return_value=list(self.targets[:5]),
            ),
            patch.object(setting_window, "auto_trade_start_selected_auto_trades") as backend,
            patch.object(run_control, "show_toast") as toast,
        ):
            self.window.start_selected_auto_trades()

        backend.assert_not_called()
        toast.assert_called_once()
        self.assertEqual(
            "대상종목 5  |  기운영중 5  |  운영시작 0  |  운영불가 0",
            toast.call_args.kwargs["message"],
        )
        self.assertEqual(before, {
            target[0]: (target[0] / "state.json").read_bytes()
            for target in self.targets[:5]
        })

    def test_bottom_button_shows_today_normal_ended_disabled(self) -> None:
        self._write_operation_state(
            {
                "operation_date": setting_window.date.today().isoformat(),
                "operation_status": "NORMAL_ENDED",
                "operation_participant_stock_codes": [self.targets[0][1]],
            }
        )

        self.window.update_global_operation_button_state()

        self.assertEqual("\u25cf \uc6b4\uc601\uc815\uc9c0", self.window.btn_start.text())
        self.assertIn("color: #111827", self.window.btn_start.styleSheet())
        self.assertIn(
            "QPushButton:disabled {color: #111827;border-color: #D1D5DB;"
            "background-color: #FFFFFF;",
            self.window.btn_start.styleSheet(),
        )
        self.assertFalse(self.window.btn_start.isEnabled())

    def test_bottom_button_today_normal_ended_has_priority_over_emergency(self) -> None:
        self._write_operation_state(
            {
                "operation_date": setting_window.date.today().isoformat(),
                "operation_status": "NORMAL_ENDED",
                "operation_participant_stock_codes": [self.targets[0][1]],
                "emergency_stop": True,
            }
        )

        self.window.update_global_operation_button_state()

        self.assertEqual("\u25cf \uc6b4\uc601\uc815\uc9c0", self.window.btn_start.text())
        self.assertFalse(self.window.btn_start.isEnabled())

    def test_bottom_button_past_normal_ended_uses_existing_start_contract(self) -> None:
        self._write_operation_state(
            {
                "operation_date": "2000-01-01",
                "operation_status": "NORMAL_ENDED",
                "operation_participant_stock_codes": [self.targets[0][1]],
            }
        )

        self.window.update_global_operation_button_state()

        self.assertEqual("\u25a0 \uc6b4\uc601\uc2dc\uc791", self.window.btn_start.text())
        self.assertIn("color: #1D4ED8", self.window.btn_start.styleSheet())
        self.assertIn(
            "QPushButton {color: #1D4ED8;border: 1px solid #1D4ED8;"
            "background-color: #FFFFFF;",
            self.window.btn_start.styleSheet(),
        )
        self.assertIn(
            "QPushButton:hover {color: #1D4ED8;border-color: #1D4ED8;"
            "background-color: #F8FAFC;",
            self.window.btn_start.styleSheet(),
        )
        self.assertIn(
            "QPushButton:pressed {color: #1D4ED8;border-color: #1D4ED8;"
            "background-color: #F8FAFC;",
            self.window.btn_start.styleSheet(),
        )
        self.assertIn(
            "QPushButton:disabled {color: #1D4ED8;border-color: #D1D5DB;"
            "background-color: #FFFFFF;",
            self.window.btn_start.styleSheet(),
        )
        self.assertTrue(self.window.btn_start.isEnabled())

    def test_bottom_button_today_running_and_closing_without_current_participant_uses_stopped(self) -> None:
        for status in ("RUNNING", "CLOSING"):
            with self.subTest(status=status):
                self._write_operation_state(
                    {
                        "operation_date": setting_window.date.today().isoformat(),
                        "operation_status": status,
                        "operation_participant_stock_codes": [self.targets[0][1]],
                    }
                )

                self.window.update_global_operation_button_state()

                self.assertEqual("\u25cf \uc6b4\uc601\uc815\uc9c0", self.window.btn_start.text())
                self.assertFalse(self.window.btn_start.isEnabled())

    def test_bottom_button_current_participant_uses_running_even_when_global_state_is_empty(self) -> None:
        self._write_state(
            self.targets[0][0],
            status="RUNNING",
            trade_enabled=True,
            trade_started_at="2026-08-26 09:01:00",
        )
        run_control.auto_trade_register_current_session_operation_participants(
            self.window,
            (self.targets[0][1],),
        )

        with (
            patch.object(self.window, "registered_operation_targets", return_value=[self.targets[0]]),
            patch.object(run_control, "read_operation_state", return_value={}),
        ):
            self.window.update_global_operation_button_state()

        self.assertEqual("\u25b6 \uc6b4\uc601\uc911", self.window.btn_start.text())
        self.assertIn("color: #15803D", self.window.btn_start.styleSheet())
        self.assertIn("background-color: #F8FAFC", self.window.btn_start.styleSheet())
        self.assertFalse(self.window.btn_start.isEnabled())

    def test_bottom_button_missing_or_corrupt_operation_state_uses_start_contract(self) -> None:
        self.operation_state_path.unlink()
        self.window.update_global_operation_button_state()
        self.assertEqual("\u25a0 \uc6b4\uc601\uc2dc\uc791", self.window.btn_start.text())
        self.assertTrue(self.window.btn_start.isEnabled())

        self.operation_state_path.write_text("{", encoding="utf-8")
        self.window.update_global_operation_button_state()
        self.assertEqual("\u25a0 \uc6b4\uc601\uc2dc\uc791", self.window.btn_start.text())
        self.assertTrue(self.window.btn_start.isEnabled())

    def test_global_start_is_blocked_when_today_normal_ended(self) -> None:
        self._write_operation_state(
            {
                "operation_date": setting_window.date.today().isoformat(),
                "operation_status": "NORMAL_ENDED",
                "operation_ended_at": f"{setting_window.date.today().isoformat()} 15:30:00",
                "operation_end_reason": "ALL_PARTICIPANTS_COMPLETE",
                "operation_participant_stock_codes": [self.targets[0][1]],
            }
        )
        before = self.operation_state_path.read_text(encoding="utf-8")

        with patch.object(
            setting_window,
            "auto_trade_start_selected_auto_trades",
        ) as start_backend:
            self.window.start_selected_auto_trades()

        start_backend.assert_not_called()
        self.assertEqual(before, self.operation_state_path.read_text(encoding="utf-8"))
        self.assertEqual("\u25cf \uc6b4\uc601\uc815\uc9c0", self.window.btn_start.text())
        self.assertFalse(self.window.btn_start.isEnabled())
        self.assertEqual(["오늘 운영이 종료되었습니다."], self.window.status_messages)

    def test_bottom_button_shows_global_emergency_disabled_when_global_latch_true(self) -> None:
        with patch.object(
            run_control,
            "read_operation_state",
            return_value={"emergency_stop": True},
        ):
            self.window.update_global_operation_button_state()

        self.assertEqual("긴급정지", self.window.btn_start.text())
        self.assertFalse(self.window.btn_start.isEnabled())

    def test_bottom_button_keeps_existing_start_contract_when_global_latch_false(self) -> None:
        with (
            patch.object(self.window, "registered_operation_targets", return_value=[self.targets[0]]),
            patch.object(
                run_control,
                "read_operation_state",
                return_value={"emergency_stop": False},
            ),
        ):
            self.window.update_global_operation_button_state()

        self.assertEqual("■ 운영시작", self.window.btn_start.text())
        self.assertTrue(self.window.btn_start.isEnabled())

    def test_bottom_button_keeps_existing_running_contract_when_global_latch_false(self) -> None:
        self._write_state(
            self.targets[0][0],
            status="RUNNING",
            trade_enabled=True,
            trade_started_at="2026-08-26 09:01:00",
        )
        run_control.auto_trade_register_current_session_operation_participants(
            self.window,
            (self.targets[0][1],),
        )

        with (
            patch.object(self.window, "registered_operation_targets", return_value=[self.targets[0]]),
            patch.object(
                run_control,
                "read_operation_state",
                return_value={"emergency_stop": False},
            ),
        ):
            self.window.update_global_operation_button_state()

        self.assertEqual("▶ 운영중", self.window.btn_start.text())
        self.assertFalse(self.window.btn_start.isEnabled())

    def test_stale_early_close_starts_then_rejects_current_session_rerun(self) -> None:
        targets = list(self.targets[:2])
        for target in targets:
            self._write_state(
                target[0],
                status="EARLY_CLOSE",
                trade_enabled=True,
                trade_started_at="2026-08-09 10:00:00",
                early_close_requested_at="2026-08-09 13:00:00",
                early_close_source="main_routine_context_menu",
                early_close_method="루틴",
                early_close_policy={"method": "루틴"},
                operation_command_mode="EARLY_CLOSE",
            )
        queue_path = self.root / "runtime" / "order_queue.json"
        queue_path.write_text('{"orders": []}', encoding="utf-8")

        target = targets[0]
        before_state = read_json_dict(target[0] / "state.json")
        self.assertTrue(auto_trade_policy.auto_trade_setting_trade_started(before_state))
        self.assertFalse(
            auto_trade_policy.auto_trade_setting_current_session_trade_started(
                self.window,
                True,
                target[1],
            )
        )
        self.assertEqual(
            "waiting",
            auto_trade_policy.auto_trade_stock_operation_category(
                self.window,
                stock_code=target[1],
                persisted_trade_started=True,
                operation_excluded=False,
                review_required=False,
            ),
        )
        start_targets, skipped = self.window.split_start_targets(targets)
        self.assertEqual(targets, start_targets)
        self.assertEqual([], skipped)

        with patch.object(run_control, "ORDER_QUEUE_PATH", queue_path):
            first = run_control.auto_trade_start_selected_auto_trades(
                self.window,
                selected_targets=targets,
            )

        self.assertTrue(first["ok"], first)
        saved = read_json_dict(target[0] / "state.json")
        self.assertEqual("RUNNING", saved["status"])
        self.assertIs(saved["trade_enabled"], True)
        self.assertTrue(str(saved.get("trade_started_at") or ""))
        self.assertEqual("", saved.get("early_close_requested_at"))
        self.assertEqual("", saved.get("early_close_source"))
        self.assertEqual("", saved.get("early_close_method"))
        self.assertEqual({}, saved.get("early_close_policy"))
        self.assertTrue(
            auto_trade_policy.auto_trade_setting_current_session_trade_started(
                self.window,
                auto_trade_policy.auto_trade_setting_trade_started(saved),
                target[1],
            )
        )
        self.assertEqual(
            "operation",
            auto_trade_policy.auto_trade_stock_operation_category(
                self.window,
                stock_code=target[1],
                persisted_trade_started=True,
                operation_excluded=False,
                review_required=False,
            ),
        )

        with (
            patch.object(run_control, "ORDER_QUEUE_PATH", queue_path),
            patch.object(run_control, "show_toast") as toast,
        ):
            second = run_control.auto_trade_start_selected_auto_trades(
                self.window,
                selected_targets=targets,
            )

        self.assertFalse(second["ok"])
        self.assertEqual("NO_STARTABLE_TARGETS", second["reason"])
        self.assertEqual(
            "선택한 루틴은 이미 운영 중입니다.",
            second["user_message"],
        )
        toast.assert_called_once()
        self.assertEqual(
            "선택한 루틴은 이미 운영 중입니다.",
            toast.call_args.kwargs["message"],
        )

    def test_monitoring_and_setting_buttons_share_owner_recovery_context(self) -> None:
        self._write_state(
            self.targets[0][0],
            status="RUNNING",
            trade_enabled=True,
        )
        owner = QWidget()
        self.addCleanup(owner.close)
        owner.routine_table = QTableWidget(0, 1, owner)
        owner.btn_start = QPushButton("▶ 운영시작", owner)
        owner.startup_recovery_session_ready = Mock(return_value=False)
        owner._current_session_operation_participant_stock_codes = {
            self.targets[0][1]
        }
        adapter = monitoring_context_menu.MainMonitoringStockOperationAdapter(owner, [])
        adapter.registered_operation_targets = lambda: list(self.targets)
        setting_button = QPushButton("▶ 운영시작", owner)
        setting_host = Mock()
        setting_host.btn_start = setting_button
        setting_host.registered_operation_targets.return_value = self.targets
        setting_host.running_registered_operation_targets = lambda: (
            run_control.auto_trade_running_registered_operation_targets(setting_host)
        )
        setting_host.startup_recovery_session_ready.return_value = False
        setting_host._current_session_operation_participant_stock_codes = {
            self.targets[0][1]
        }

        with patch.object(run_control, "read_operation_state", return_value={}):
            adapter.update_global_operation_button_state()
            run_control.auto_trade_update_global_operation_button_state(setting_host)

        self.assertEqual("■ 운영시작", owner.btn_start.text())
        self.assertTrue(owner.btn_start.isEnabled())
        self.assertEqual(setting_button.text(), owner.btn_start.text())
        self.assertEqual(setting_button.isEnabled(), owner.btn_start.isEnabled())
        owner.startup_recovery_session_ready.assert_called_with(refresh=False)
        setting_host.startup_recovery_session_ready.assert_called_with(refresh=False)

    def test_running_targets_exclude_operation_excluded_stock(self) -> None:
        stock_dir = self.targets[0][0]
        self._write_state(stock_dir, status="RUNNING", trade_enabled=True)
        config = read_json_dict(stock_dir / "config.json")
        config["operation_excluded"] = True
        (stock_dir / "config.json").write_text(
            json.dumps(config, ensure_ascii=False),
            encoding="utf-8",
        )

        running = run_control.auto_trade_running_registered_operation_targets(self.window)

        self.assertNotIn(self.targets[0], running)

    def test_bottom_button_startup_restore_from_global_operation_state(self) -> None:
        with patch.object(
            run_control,
            "read_operation_state",
            side_effect=[
                {"emergency_stop": True},
                {"emergency_stop": False},
            ],
        ):
            self.window.update_global_operation_button_state()
            self.assertEqual("긴급정지", self.window.btn_start.text())
            self.assertFalse(self.window.btn_start.isEnabled())

            self.window.update_global_operation_button_state()
            self.assertEqual("■ 운영시작", self.window.btn_start.text())
            self.assertTrue(self.window.btn_start.isEnabled())

    def test_bottom_button_returns_to_existing_contract_after_global_release(self) -> None:
        with patch.object(
            run_control,
            "read_operation_state",
            side_effect=[
                {"emergency_stop": True},
                {"emergency_stop": False},
            ],
        ):
            self.window.update_global_operation_button_state()
            self.assertEqual("긴급정지", self.window.btn_start.text())

            self.window.update_global_operation_button_state()

        self.assertEqual("■ 운영시작", self.window.btn_start.text())
        self.assertTrue(self.window.btn_start.isEnabled())

    def test_bottom_button_today_started_without_current_participant_shows_stopped(self) -> None:
        with patch.object(
            run_control,
            "read_operation_state",
            return_value={
                "operation_date": setting_window.date.today().isoformat(),
                "operation_status": "RUNNING",
                "operation_participant_stock_codes": [self.targets[0][1]],
            },
        ):
            self.window.update_global_operation_button_state()

        self.assertEqual("● 운영정지", self.window.btn_start.text())
        self.assertIn("background-color: #F8FAFC", self.window.btn_start.styleSheet())
        self.assertFalse(self.window.btn_start.isEnabled())

    def test_bottom_button_previous_day_stopped_returns_to_start(self) -> None:
        with patch.object(
            run_control,
            "read_operation_state",
            return_value={
                "operation_date": "2000-01-01",
                "operation_status": "RUNNING",
                "operation_started_at": "2000-01-01 09:00:00",
                "operation_participant_stock_codes": [self.targets[0][1]],
            },
        ):
            self.window.update_global_operation_button_state()

        self.assertEqual("■ 운영시작", self.window.btn_start.text())
        self.assertTrue(self.window.btn_start.isEnabled())

    def test_bottom_button_ignores_per_stock_emergency_when_global_latch_false(self) -> None:
        for target in self.targets:
            self._write_state(target[0], status="EMERGENCY_STOPPED", trade_enabled=False)

        with patch.object(
            run_control,
            "read_operation_state",
            return_value={"emergency_stop": False},
        ):
            self.window.update_global_operation_button_state()

        self.assertEqual("■ 운영시작", self.window.btn_start.text())
        self.assertTrue(self.window.btn_start.isEnabled())

    def test_selected_emergency_stop_does_not_change_bottom_button_without_global_latch(self) -> None:
        self.window._selected_stock_infos = list(self.targets)

        with (
            patch.object(
                run_control,
                "read_operation_state",
                return_value={"emergency_stop": False},
            ),
            patch("gui_main_emergency_ops.append_changelog"),
            patch("gui_main_emergency_ops.append_stock_log"),
            patch("gui_main_emergency_ops.show_toast"),
        ):
            self.window.emergency_stop_selected_auto_trade_stocks()
            self.window.update_global_operation_button_state()

        self.assertTrue(
            all(
                read_json_dict(target[0] / "state.json").get("status")
                == "REVIEW_REQUIRED"
                for target in self.targets
            )
        )
        self.assertEqual("■ 운영시작", self.window.btn_start.text())
        self.assertTrue(self.window.btn_start.isEnabled())

    def test_global_start_updates_button_to_running_disabled(self) -> None:
        self.assertTrue(self.window.btn_start.isEnabled())

        with patch.object(
            operation_policy_gate,
            "now_text",
            return_value="2026-07-29 09:05:00",
        ):
            self.window.btn_start.click()

        self.assertEqual("\u25b6 \uc6b4\uc601\uc911", self.window.btn_start.text())
        self.assertFalse(self.window.btn_start.isEnabled())
        operation_state = read_json_dict(self.operation_state_path)
        self.assertEqual("2026-07-29", operation_state["operation_date"])
        self.assertEqual("RUNNING", operation_state["operation_status"])
        self.assertEqual("2026-07-29 09:05:00", operation_state["operation_started_at"])
        self.assertEqual("2026-07-29 09:05:00", operation_state["operation_updated_at"])
        self.assertEqual(
            [target[1] for target in self.targets],
            operation_state["operation_participant_stock_codes"],
        )
        self.assertTrue(
            all(
                read_json_dict(target[0] / "state.json").get("trade_enabled")
                is True
                for target in self.targets
            )
        )

    def test_backend_global_start_is_blocked_by_global_emergency_without_mutation(self) -> None:
        self._write_operation_state(
            {
                "operation_date": "2026-07-29",
                "operation_status": "RUNNING",
                "operation_started_at": "2026-07-29 09:05:00",
                "operation_participant_stock_codes": [self.targets[1][1]],
                "emergency_stop": True,
                "emergency_stopped_at": "2026-07-29 10:00:00",
                "emergency_reason": "USER_EMERGENCY_STOP",
            }
        )
        before_operation_state = self.operation_state_path.read_text(encoding="utf-8")
        before_stock_state = (self.targets[0][0] / "state.json").read_text(
            encoding="utf-8"
        )

        with (
            patch.object(
                self.window,
                "recalculate_stock_status_by_operation_policy",
                wraps=self.window.recalculate_stock_status_by_operation_policy,
            ) as stock_start,
            patch.object(run_control, "write_global_operation_running_state") as writer,
        ):
            result = run_control.auto_trade_start_selected_auto_trades(
                self.window,
                selected_targets=[self.targets[0]],
            )

        self.assertFalse(result["ok"])
        self.assertTrue(result["blocked"])
        self.assertEqual("GLOBAL_EMERGENCY_STOP", result["reason"])
        self.assertEqual(0, result["started_count"])
        stock_start.assert_not_called()
        writer.assert_not_called()
        self.assertEqual(
            before_operation_state,
            self.operation_state_path.read_text(encoding="utf-8"),
        )
        self.assertEqual(
            before_stock_state,
            (self.targets[0][0] / "state.json").read_text(encoding="utf-8"),
        )

    def test_backend_global_start_blocks_past_emergency_latch_regardless_of_status(self) -> None:
        cases = [
            {
                "operation_date": "2000-01-01",
                "operation_status": "RUNNING",
                "operation_participant_stock_codes": [self.targets[1][1]],
                "emergency_stop": True,
            },
            {
                "operation_date": setting_window.date.today().isoformat(),
                "operation_status": "NORMAL_ENDED",
                "operation_participant_stock_codes": [self.targets[1][1]],
                "emergency_stop": True,
            },
            {
                "operation_date": "2000-01-01",
                "operation_status": "NORMAL_ENDED",
                "operation_participant_stock_codes": [self.targets[1][1]],
                "emergency_stop": True,
            },
            {
                "operation_date": "2000-01-01",
                "operation_status": "CLOSING",
                "operation_participant_stock_codes": [self.targets[1][1]],
                "emergency_stop": True,
            },
        ]
        for operation_state in cases:
            with self.subTest(operation_state=operation_state):
                self._write_state(
                    self.targets[0][0],
                    status="STOPPED",
                    trade_enabled=False,
                )
                self._write_operation_state(operation_state)
                before_operation_state = self.operation_state_path.read_text(
                    encoding="utf-8"
                )
                before_stock_state = (self.targets[0][0] / "state.json").read_text(
                    encoding="utf-8"
                )

                with (
                    patch.object(
                        self.window,
                        "recalculate_stock_status_by_operation_policy",
                        wraps=self.window.recalculate_stock_status_by_operation_policy,
                    ) as stock_start,
                    patch.object(
                        run_control,
                        "write_global_operation_running_state",
                    ) as writer,
                ):
                    result = run_control.auto_trade_start_selected_auto_trades(
                        self.window,
                        selected_targets=[self.targets[0]],
                    )

                self.assertFalse(result["ok"])
                self.assertEqual("GLOBAL_EMERGENCY_STOP", result["reason"])
                stock_start.assert_not_called()
                writer.assert_not_called()
                self.assertEqual(
                    before_operation_state,
                    self.operation_state_path.read_text(encoding="utf-8"),
                )
                self.assertEqual(
                    before_stock_state,
                    (self.targets[0][0] / "state.json").read_text(encoding="utf-8"),
                )

    def test_backend_global_start_allows_existing_flow_when_global_emergency_false_or_missing(self) -> None:
        cases = [
            {"operation_date": "2000-01-01", "emergency_stop": False},
            {"operation_date": "2000-01-01"},
            {
                "operation_date": "2000-01-01",
                "emergency_stop": False,
                "emergency_released_at": "2026-07-29 08:59:00",
            },
        ]
        for index, operation_state in enumerate(cases):
            with self.subTest(operation_state=operation_state):
                target = self.targets[index]
                self._write_state(target[0], status="STOPPED", trade_enabled=False)
                self._write_operation_state(operation_state)

                result = run_control.auto_trade_start_selected_auto_trades(
                    self.window,
                    selected_targets=[target],
                )

                state = read_json_dict(target[0] / "state.json")
                operation_state_after = read_json_dict(self.operation_state_path)
                self.assertTrue(result["ok"])
                self.assertEqual(1, result["started_count"])
                self.assertTrue(state["trade_enabled"])
                self.assertEqual("RUNNING", operation_state_after["operation_status"])
                self.assertIn(
                    target[1],
                    operation_state_after["operation_participant_stock_codes"],
                )

    def test_global_start_partial_success_records_running_once(self) -> None:
        failed_target = self.targets[0]
        config_path = failed_target[0] / "config.json"
        config = read_json_dict(config_path)
        config.pop("assigned_routine_instance_id", None)
        config_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")

        with patch.object(
            operation_policy_gate,
            "now_text",
            return_value="2026-07-29 09:06:00",
        ):
            result = run_control.auto_trade_start_selected_auto_trades(
                self.window,
                selected_targets=list(self.targets[:3]),
            )

        operation_state = read_json_dict(self.operation_state_path)
        self.assertTrue(result["ok"])
        self.assertEqual(2, result["started_count"])
        self.assertEqual("RUNNING", operation_state["operation_status"])
        self.assertEqual("2026-07-29 09:06:00", operation_state["operation_started_at"])
        self.assertEqual(
            [self.targets[1][1], self.targets[2][1]],
            operation_state["operation_participant_stock_codes"],
        )
        self.assertNotIn(
            failed_target[1],
            operation_state["operation_participant_stock_codes"],
        )

    def test_global_start_zero_targets_does_not_change_operation_state(self) -> None:
        for target in self.targets:
            self._write_operation_excluded(target[0], True)
        before = read_json_dict(self.operation_state_path)

        with patch.object(
            setting_window,
            "auto_trade_start_selected_auto_trades",
        ) as start_backend:
            self.window.start_selected_auto_trades()

        self.assertEqual(before, read_json_dict(self.operation_state_path))
        start_backend.assert_not_called()

    def test_global_start_all_fail_does_not_change_operation_state(self) -> None:
        target = self.targets[0]
        config_path = target[0] / "config.json"
        config = read_json_dict(config_path)
        config.pop("assigned_routine_instance_id", None)
        config_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
        before = read_json_dict(self.operation_state_path)

        result = run_control.auto_trade_start_selected_auto_trades(
            self.window,
            selected_targets=[target],
        )

        self.assertFalse(result["ok"])
        self.assertEqual(before, read_json_dict(self.operation_state_path))

    def test_global_start_preserves_first_started_at_when_already_running_today(self) -> None:
        self.operation_state_path.write_text(
            json.dumps(
                {
                    "operation_date": "2026-07-29",
                    "operation_status": "RUNNING",
                    "operation_started_at": "2026-07-29 09:05:00",
                    "operation_updated_at": "2026-07-29 09:05:00",
                    "operation_participant_stock_codes": [self.targets[1][1]],
                    "emergency_stop": False,
                    "existing_key": "preserve",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        with patch.object(
            operation_policy_gate,
            "now_text",
            return_value="2026-07-29 10:15:00",
        ):
            result = run_control.auto_trade_start_selected_auto_trades(
                self.window,
                selected_targets=[self.targets[0]],
            )

        operation_state = read_json_dict(self.operation_state_path)
        self.assertTrue(result["ok"])
        self.assertEqual("2026-07-29 09:05:00", operation_state["operation_started_at"])
        self.assertEqual("2026-07-29 10:15:00", operation_state["operation_updated_at"])
        self.assertEqual(
            [self.targets[0][1], self.targets[1][1]],
            operation_state["operation_participant_stock_codes"],
        )
        self.assertFalse(operation_state["emergency_stop"])
        self.assertEqual("preserve", operation_state["existing_key"])

    def test_global_start_next_day_replaces_running_start_date(self) -> None:
        self.operation_state_path.write_text(
            json.dumps(
                {
                    "operation_date": "2026-07-28",
                    "operation_status": "RUNNING",
                    "operation_started_at": "2026-07-28 09:05:00",
                    "operation_updated_at": "2026-07-28 09:05:00",
                    "operation_participant_stock_codes": [self.targets[1][1]],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        with patch.object(
            operation_policy_gate,
            "now_text",
            return_value="2026-07-29 09:10:00",
        ):
            result = run_control.auto_trade_start_selected_auto_trades(
                self.window,
                selected_targets=[self.targets[0]],
            )

        operation_state = read_json_dict(self.operation_state_path)
        self.assertTrue(result["ok"])
        self.assertEqual("2026-07-29", operation_state["operation_date"])
        self.assertEqual("2026-07-29 09:10:00", operation_state["operation_started_at"])
        self.assertEqual(
            [self.targets[0][1]],
            operation_state["operation_participant_stock_codes"],
        )

    def test_global_start_writer_failure_is_reported_without_stock_rollback(self) -> None:
        with patch.object(
            run_control,
            "write_global_operation_running_state",
            return_value={"ok": False, "error": "write failed"},
        ) as writer:
            result = run_control.auto_trade_start_selected_auto_trades(
                self.window,
                selected_targets=[self.targets[0]],
            )

        state = read_json_dict(self.targets[0][0] / "state.json")
        self.assertTrue(result["ok"])
        self.assertTrue(result["operation_state_write_failed"])
        self.assertTrue(state["trade_enabled"])
        writer.assert_called_once()
        self.assertEqual(
            [self.targets[0][1]],
            writer.call_args.kwargs["participant_stock_codes"],
        )

    def test_global_start_passes_only_non_excluded_targets(self) -> None:
        excluded_target = self.targets[0]
        included_targets = self.targets[1:]
        self._write_operation_excluded(excluded_target[0], True)

        with patch.object(
            setting_window,
            "auto_trade_start_selected_auto_trades",
        ) as start_backend:
            self.window.start_selected_auto_trades()

        start_backend.assert_called_once()
        passed_targets = start_backend.call_args.kwargs["selected_targets"]
        self.assertEqual(
            [target[1] for target in included_targets],
            [target[1] for target in passed_targets],
        )
        self.assertNotIn(excluded_target[1], [target[1] for target in passed_targets])

    def test_global_start_keeps_excluded_stock_config_and_state_unchanged(self) -> None:
        excluded_target = self.targets[0]
        self._write_operation_excluded(excluded_target[0], True)
        before_config = read_json_dict(excluded_target[0] / "config.json")
        before_state = read_json_dict(excluded_target[0] / "state.json")

        self.window.start_selected_auto_trades()

        self.assertEqual(
            before_config,
            read_json_dict(excluded_target[0] / "config.json"),
        )
        self.assertEqual(
            before_state,
            read_json_dict(excluded_target[0] / "state.json"),
        )

    def test_global_start_with_all_targets_excluded_does_not_call_backend(self) -> None:
        for target in self.targets:
            self._write_operation_excluded(target[0], True)
        with patch.object(
            self.window,
            "registered_operation_targets",
            return_value=list(self.targets[:3]),
        ):
            self.window.update_global_operation_button_state()

        with patch.object(
            setting_window,
            "auto_trade_start_selected_auto_trades",
        ) as start_backend:
            self.window.btn_start.click()

        start_backend.assert_not_called()
        self.assertEqual("\u25a0 \uc6b4\uc601\uc2dc\uc791", self.window.btn_start.text())
        self.assertTrue(self.window.btn_start.isEnabled())
        self.assertEqual(1, len(self.window.status_messages))

    def test_global_start_missing_operation_excluded_key_is_included(self) -> None:
        config = read_json_dict(self.targets[0][0] / "config.json")
        self.assertNotIn("operation_excluded", config)

        with patch.object(
            setting_window,
            "auto_trade_start_selected_auto_trades",
        ) as start_backend:
            self.window.start_selected_auto_trades()

        passed_codes = [
            target[1]
            for target in start_backend.call_args.kwargs["selected_targets"]
        ]
        self.assertIn(self.targets[0][1], passed_codes)

    def test_context_start_before_running_starts_selected_targets_only(self) -> None:
        selected_targets = [self.targets[0], self.targets[2]]
        self.window._selected_stock_infos = selected_targets

        def start_side_effect(*_args, **kwargs):
            for stock_dir, _code, _name in kwargs["selected_targets"]:
                self._write_state(stock_dir, status="RUNNING", trade_enabled=True)
            run_control.auto_trade_register_current_session_operation_participants(
                self.window,
                (code for _stock_dir, code, _name in kwargs["selected_targets"]),
            )
            return {"ok": True}

        with patch.object(
            run_control,
            "auto_trade_start_selected_auto_trades",
            side_effect=start_side_effect,
        ) as start_backend:
            self.window.start_selected_rows_auto_trades()

        start_backend.assert_called_once()
        self.assertEqual(
            [target[1] for target in selected_targets],
            [
                target[1]
                for target in start_backend.call_args.kwargs["selected_targets"]
            ],
        )
        self.assertTrue(
            read_json_dict(self.targets[1][0] / "config.json").get(
                "operation_excluded"
            )
        )
        self.assertTrue(
            read_json_dict(self.targets[3][0] / "config.json").get(
                "operation_excluded"
            )
        )
        self.assertEqual("▶ 운영중", self.window.btn_start.text())
        self.assertFalse(self.window.btn_start.isEnabled())

    def test_context_start_before_running_excludes_unselected_registered_targets(self) -> None:
        selected_targets = [self.targets[0], self.targets[2]]
        self.window._selected_stock_infos = selected_targets

        with patch.object(
            run_control,
            "auto_trade_start_selected_auto_trades",
            return_value={"ok": True},
        ) as start_backend:
            self.window.start_selected_rows_auto_trades()

        start_backend.assert_called_once()
        self.assertTrue(
            read_json_dict(self.targets[1][0] / "config.json").get(
                "operation_excluded"
            )
        )

    def test_context_start_before_running_clears_selected_operation_exclusion(self) -> None:
        selected_target = self.targets[0]
        self._write_operation_excluded(selected_target[0], True)
        self.window._selected_stock_infos = [selected_target]

        with patch.object(
            run_control,
            "auto_trade_start_selected_auto_trades",
            return_value={"ok": True},
        ) as start_backend:
            self.window.start_selected_rows_auto_trades()

        start_backend.assert_called_once()
        self.assertFalse(
            read_json_dict(selected_target[0] / "config.json").get(
                "operation_excluded"
            )
        )

    def test_context_start_before_running_all_failed_keeps_unselected_unchanged(self) -> None:
        selected_target = self.targets[0]
        untouched_target = self.targets[1]
        before_untouched = read_json_dict(untouched_target[0] / "config.json")
        self.window._selected_stock_infos = [selected_target]

        with patch.object(
            run_control,
            "auto_trade_start_selected_auto_trades",
            return_value={"ok": False, "reason": "START_FAILED"},
        ) as start_backend:
            self.window.start_selected_rows_auto_trades()

        start_backend.assert_called_once()
        self.assertEqual(
            before_untouched,
            read_json_dict(untouched_target[0] / "config.json"),
        )

    def test_context_start_before_running_partial_success_confirms_operation_set(self) -> None:
        selected_targets = [self.targets[0], self.targets[2]]
        untouched_target = self.targets[1]
        self.window._selected_stock_infos = selected_targets

        with patch.object(
            run_control,
            "auto_trade_start_selected_auto_trades",
            return_value={
                "ok": True,
                "completed": (f"{selected_targets[0][1]} {selected_targets[0][2]}",),
                "failed": (f"{selected_targets[1][1]} {selected_targets[1][2]}",),
            },
        ):
            self.window.start_selected_rows_auto_trades()

        self.assertTrue(
            read_json_dict(untouched_target[0] / "config.json").get(
                "operation_excluded"
            )
        )

    def test_context_start_before_running_does_not_exclude_review_or_unassigned(self) -> None:
        selected_target = self.targets[0]
        review_target = self.targets[1]
        unassigned_target = self.targets[2]
        self._write_state(
            review_target[0],
            status="REVIEW_REQUIRED",
            trade_enabled=False,
            review_required=True,
        )
        unassigned_config_path = unassigned_target[0] / "config.json"
        unassigned_config = read_json_dict(unassigned_config_path)
        unassigned_config.pop("assigned_routine_instance_id", None)
        unassigned_config_path.write_text(
            json.dumps(unassigned_config, ensure_ascii=False),
            encoding="utf-8",
        )
        self.window._selected_stock_infos = [selected_target]

        with patch.object(
            run_control,
            "auto_trade_start_selected_auto_trades",
            return_value={"ok": True},
        ):
            self.window.start_selected_rows_auto_trades()

        self.assertNotIn(
            "operation_excluded",
            read_json_dict(review_target[0] / "config.json"),
        )
        self.assertNotIn(
            "operation_excluded",
            read_json_dict(unassigned_target[0] / "config.json"),
        )

    def test_context_start_while_running_includes_selected_excluded_target(self) -> None:
        running_target = self.targets[0]
        add_target = self.targets[1]
        self._write_state(
            running_target[0],
            status="RUNNING",
            trade_enabled=True,
        )
        self._write_operation_excluded(add_target[0], True)
        self.window._selected_stock_infos = [add_target]

        with patch.object(
            run_control,
            "auto_trade_start_selected_auto_trades",
            return_value={"ok": True},
        ) as start_backend:
            self.window.start_selected_rows_auto_trades()

        start_backend.assert_called_once()
        self.assertFalse(
            read_json_dict(add_target[0] / "config.json").get("operation_excluded")
        )
        self.assertEqual(
            [add_target[1]],
            [
                target[1]
                for target in start_backend.call_args.kwargs["selected_targets"]
            ],
        )

    def test_context_start_while_running_ignores_already_running_included_target(self) -> None:
        running_target = self.targets[0]
        untouched_target = self.targets[1]
        self._write_state(
            running_target[0],
            status="RUNNING",
            trade_enabled=True,
        )
        self._write_operation_excluded(running_target[0], False)
        run_control.auto_trade_register_current_session_operation_participants(
            self.window,
            (running_target[1],),
        )
        self._write_operation_excluded(untouched_target[0], True)
        before_untouched = read_json_dict(untouched_target[0] / "config.json")
        self.window._selected_stock_infos = [running_target]

        with patch.object(
            setting_window,
            "auto_trade_start_selected_auto_trades",
        ) as start_backend:
            self.window.start_selected_rows_auto_trades()

        start_backend.assert_not_called()
        self.assertFalse(
            read_json_dict(running_target[0] / "config.json").get(
                "operation_excluded"
            )
        )
        self.assertEqual(
            before_untouched,
            read_json_dict(untouched_target[0] / "config.json"),
        )
        self.assertIn(
            "선택한 종목이 모두 이미 운영 중입니다.",
            self.window.status_messages,
        )

    def test_context_start_while_running_starts_only_additional_target(self) -> None:
        running_target = self.targets[0]
        add_target = self.targets[1]
        untouched_target = self.targets[2]
        self._write_state(
            running_target[0],
            status="RUNNING",
            trade_enabled=True,
        )
        self._write_operation_excluded(running_target[0], False)
        run_control.auto_trade_register_current_session_operation_participants(
            self.window,
            (running_target[1],),
        )
        self._write_operation_excluded(add_target[0], True)
        before_running = read_json_dict(running_target[0] / "config.json")
        before_add = read_json_dict(add_target[0] / "config.json")
        before_untouched = read_json_dict(untouched_target[0] / "config.json")
        self.window._selected_stock_infos = [running_target, add_target]

        with patch.object(
            run_control,
            "auto_trade_start_selected_auto_trades",
            return_value={"ok": True},
        ) as start_backend:
            self.window.start_selected_rows_auto_trades()

        start_backend.assert_called_once()
        self.assertEqual(
            [add_target[1]],
            [
                target[1]
                for target in start_backend.call_args.kwargs["selected_targets"]
            ],
        )
        self.assertEqual(
            before_running,
            read_json_dict(running_target[0] / "config.json"),
        )
        self.assertTrue(before_add.get("operation_excluded"))
        self.assertFalse(
            read_json_dict(add_target[0] / "config.json").get(
                "operation_excluded"
            )
        )
        self.assertEqual(
            before_untouched,
            read_json_dict(untouched_target[0] / "config.json"),
        )

    def test_direct_start_call_while_running_does_not_stop(self) -> None:
        for target in self.targets[:3]:
            self._write_state(
                target[0],
                status="RUNNING",
                trade_enabled=True,
            )

        self.window.start_selected_auto_trades()
        self.assertTrue(
            all(
                read_json_dict(target[0] / "state.json").get("trade_enabled")
                is True
                for target in self.targets[:3]
            )
        )

    def test_partial_running_state_shows_running_disabled(self) -> None:
        for target in self.targets[:3]:
            self._write_state(
                target[0],
                status="RUNNING",
                trade_enabled=True,
                trade_started_at="2026-08-26 09:01:00",
            )
        run_control.auto_trade_register_current_session_operation_participants(
            self.window,
            (target[1] for target in self.targets[:3]),
        )
        with patch.object(
            self.window,
            "registered_operation_targets",
            return_value=list(self.targets[:3]),
        ):
            self.window.update_global_operation_button_state()
        self.assertEqual("\u25b6 \uc6b4\uc601\uc911", self.window.btn_start.text())
        self.assertFalse(self.window.btn_start.isEnabled())

    def test_login_failure_shows_toast_and_keeps_start_button(self) -> None:
        self.window.login_ready = False

        with patch.object(run_control, "show_toast") as toast:
            self.window.btn_start.click()

        toast.assert_called_once()
        self.assertIn(
            "로그인되어 있지 않습니다",
            toast.call_args.kwargs["message"],
        )
        self.assertEqual("■ 운영시작", self.window.btn_start.text())

    def test_all_emergency_stopped_shows_contract_toast(self) -> None:
        for target in self.targets:
            self._write_state(
                target[0],
                status="EMERGENCY_STOPPED",
                trade_enabled=False,
            )
        self.window.update_global_operation_button_state()

        with patch.object(run_control, "show_toast") as toast:
            self.window.btn_start.click()

        toast.assert_called_once()
        self.assertEqual(
            "모든 종목이 긴급정지 상태입니다.",
            toast.call_args.kwargs["message"],
        )
        self.assertEqual("■ 운영시작", self.window.btn_start.text())

    def test_context_emergency_stop_single_selected_stock_only(self) -> None:
        target = self.targets[0]
        untouched = self.targets[1]
        before_untouched = read_json_dict(untouched[0] / "state.json")
        self.window._selected_stock_infos = [target]

        with (
            patch("gui_main_emergency_ops.append_changelog"),
            patch("gui_main_emergency_ops.append_stock_log"),
            patch("gui_main_emergency_ops.show_toast"),
            patch("gui_main_emergency_ops.now_text", return_value="2026-07-29 10:00:00"),
        ):
            result = self.window.emergency_stop_selected_auto_trade_stocks()

        changed_state = read_json_dict(target[0] / "state.json")
        self.assertEqual(("000001 테스트1",), result["changed"])
        self.assertEqual("REVIEW_REQUIRED", changed_state["status"])
        self.assertEqual("", changed_state["emergency_stopped_at"])
        self.assertEqual("", changed_state["emergency_reason"])
        self.assertEqual("SELECTED", changed_state["emergency_scope"])
        self.assertFalse(changed_state["trade_enabled"])
        self.assertNotIn("buy_enabled", changed_state)
        self.assertNotIn("sell_enabled", changed_state)
        self.assertEqual(before_untouched, read_json_dict(untouched[0] / "state.json"))

    def test_context_emergency_stop_multi_selected_stocks_only(self) -> None:
        selected_targets = [self.targets[0], self.targets[2]]
        untouched = self.targets[1]
        before_untouched = read_json_dict(untouched[0] / "state.json")
        self.window._selected_stock_infos = selected_targets

        with (
            patch("gui_main_emergency_ops.append_changelog"),
            patch("gui_main_emergency_ops.append_stock_log"),
            patch("gui_main_emergency_ops.show_toast"),
            patch("gui_main_emergency_ops.now_text", return_value="2026-07-29 10:01:00"),
        ):
            result = self.window.emergency_stop_selected_auto_trade_stocks()

        self.assertEqual(("000001 테스트1", "000003 테스트3"), result["changed"])
        for target in selected_targets:
            state = read_json_dict(target[0] / "state.json")
            self.assertEqual("REVIEW_REQUIRED", state["status"])
            self.assertEqual("SELECTED", state["emergency_scope"])
            self.assertFalse(state["trade_enabled"])
            self.assertNotIn("buy_enabled", state)
            self.assertNotIn("sell_enabled", state)
        self.assertEqual(before_untouched, read_json_dict(untouched[0] / "state.json"))

    def test_context_emergency_stop_skips_existing_emergency_without_timestamp_overwrite(self) -> None:
        emergency_target = self.targets[0]
        self._write_state(
            emergency_target[0],
            status="EMERGENCY_STOPPED",
            trade_enabled=False,
            emergency_stopped_at="2026-07-29 09:00:00",
            emergency_reason="USER_EMERGENCY_STOP",
        )
        before_state = read_json_dict(emergency_target[0] / "state.json")
        self.window._selected_stock_infos = [emergency_target]

        with (
            patch("gui_main_emergency_ops.append_changelog") as changelog,
            patch("gui_main_emergency_ops.append_stock_log"),
            patch("gui_main_emergency_ops.show_toast") as toast,
            patch("gui_main_emergency_ops.now_text", return_value="2026-07-29 10:02:00"),
        ):
            result = self.window.emergency_stop_selected_auto_trade_stocks()

        self.assertEqual((), result["changed"])
        self.assertEqual(("000001 테스트1",), result["skipped"])
        self.assertEqual(before_state, read_json_dict(emergency_target[0] / "state.json"))
        changelog.assert_not_called()
        toast.assert_not_called()

    def test_context_emergency_release_single_stock_passes_to_stopped_without_restart(self) -> None:
        target = self.targets[0]
        self._write_state(
            target[0],
            status="EMERGENCY_STOPPED",
            trade_enabled=False,
        )
        self.window._selected_stock_infos = [target]

        with (
            patch("gui_main_emergency_ops.emergency_review_reason_for_stock", return_value=(False, "정상")),
            patch("gui_main_emergency_ops.append_changelog"),
            patch("gui_main_emergency_ops.append_stock_log"),
            patch("gui_main_emergency_ops.show_toast"),
            patch("gui_main_emergency_ops.now_text", return_value="2026-07-29 10:03:00"),
            patch("gui_auto_trade_setting_window.auto_trade_start_selected_auto_trades") as start_backend,
        ):
            result = self.window.release_selected_emergency_stopped_auto_trade_stocks()

        state = read_json_dict(target[0] / "state.json")
        self.assertEqual(("000001 테스트1",), result["normal"])
        self.assertEqual("STOPPED", state["status"])
        self.assertFalse(state["trade_enabled"])
        self.assertEqual("PASSED", state["emergency_release_check"])
        self.assertEqual("", state["emergency_stopped_at"])
        self.assertEqual("", state["emergency_reason"])
        self.assertFalse(state["review_required"])
        self.assertEqual("", state["review_status"])
        self.assertNotIn("buy_enabled", state)
        self.assertNotIn("sell_enabled", state)
        start_backend.assert_not_called()

    def test_context_emergency_release_failure_moves_to_review_required_without_restart(self) -> None:
        target = self.targets[0]
        self._write_state(
            target[0],
            status="EMERGENCY_STOPPED",
            trade_enabled=False,
            holding_qty=1,
            emergency_stopped_at="2026-07-29 09:00:00",
            emergency_reason="USER_EMERGENCY_STOP",
        )
        (target[0] / "orders.json").write_text(
            json.dumps({"orders": []}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        expected_reason = emergency_ops.emergency_review_reason_for_stock(target[0])[1]
        self.window._selected_stock_infos = [target]

        with (
            patch("gui_main_emergency_ops.append_changelog"),
            patch("gui_main_emergency_ops.append_stock_log"),
            patch("gui_main_emergency_ops.show_toast"),
            patch("gui_main_emergency_ops.now_text", return_value="2026-07-29 10:04:00"),
            patch("gui_auto_trade_setting_window.auto_trade_start_selected_auto_trades") as start_backend,
        ):
            result = self.window.release_selected_emergency_stopped_auto_trade_stocks()

        state = read_json_dict(target[0] / "state.json")
        self.assertEqual(("000001 테스트1",), result["blocked"])
        self.assertEqual("EMERGENCY_STOPPED", state["status"])
        self.assertFalse(state["trade_enabled"])
        self.assertTrue(state["review_required"])
        self.assertEqual("PENDING", state["review_status"])
        self.assertEqual(expected_reason, state["review_reason"])
        self.assertEqual("2026-07-29 09:00:00", state["emergency_stopped_at"])
        self.assertEqual("USER_EMERGENCY_STOP", state["emergency_reason"])
        self.assertNotIn("emergency_release_check", state)
        self.assertNotIn("buy_enabled", state)
        self.assertNotIn("sell_enabled", state)
        start_backend.assert_not_called()

    def test_context_emergency_release_multi_selection_only_releases_selected_emergency(self) -> None:
        release_target = self.targets[0]
        normal_selected = self.targets[1]
        unselected_emergency = self.targets[2]
        self._write_state(release_target[0], status="EMERGENCY_STOPPED", trade_enabled=False)
        self._write_state(normal_selected[0], status="RUNNING", trade_enabled=True)
        self._write_state(unselected_emergency[0], status="EMERGENCY_STOPPED", trade_enabled=False)
        before_normal = read_json_dict(normal_selected[0] / "state.json")
        before_unselected = read_json_dict(unselected_emergency[0] / "state.json")
        self.window._selected_stock_infos = [release_target, normal_selected]

        with (
            patch("gui_main_emergency_ops.emergency_review_reason_for_stock", return_value=(False, "정상")),
            patch("gui_main_emergency_ops.append_changelog"),
            patch("gui_main_emergency_ops.append_stock_log"),
            patch("gui_main_emergency_ops.show_toast"),
            patch("gui_main_emergency_ops.now_text", return_value="2026-07-29 10:05:00"),
        ):
            result = self.window.release_selected_emergency_stopped_auto_trade_stocks()

        self.assertEqual(("000001 테스트1",), result["normal"])
        self.assertEqual(("000002 테스트2",), result["skipped"])
        released_state = read_json_dict(release_target[0] / "state.json")
        self.assertEqual("STOPPED", released_state["status"])
        self.assertFalse(released_state["trade_enabled"])
        self.assertFalse(released_state["review_required"])
        self.assertEqual("", released_state["review_status"])
        self.assertEqual(before_normal, read_json_dict(normal_selected[0] / "state.json"))
        self.assertEqual(before_unselected, read_json_dict(unselected_emergency[0] / "state.json"))

    def test_release_emergency_stop_target_does_not_require_window_routine_name_method(self) -> None:
        target = self.targets[0]
        self._write_state(
            target[0],
            status="EMERGENCY_STOPPED",
            trade_enabled=False,
        )
        window = type(
            "WindowWithoutRoutineName",
            (),
             {
                 "production_recovery_stock_is_review_required": lambda _self, _code: False,
                 "startup_recovery_session_ready": lambda _self, refresh=False: True,
             },
        )()

        with (
            patch("gui_main_emergency_ops.emergency_review_reason_for_stock", return_value=(False, "정상")),
            patch("gui_main_emergency_ops.append_stock_log"),
            patch("gui_main_emergency_ops.now_text", return_value="2026-07-29 10:06:00"),
            patch("gui_auto_trade_setting_window.auto_trade_start_selected_auto_trades") as start_backend,
        ):
            result = emergency_ops.release_emergency_stop_target(
                window,
                target[0],
                target[1],
                target[2],
            )

        state = read_json_dict(target[0] / "state.json")
        self.assertEqual(emergency_ops.RELEASED_NORMAL, result)
        self.assertEqual("STOPPED", state["status"])
        self.assertFalse(state["trade_enabled"])
        self.assertEqual("PASSED", state["emergency_release_check"])
        self.assertEqual("", state["emergency_stopped_at"])
        self.assertEqual("", state["emergency_reason"])
        self.assertFalse(state["review_required"])
        self.assertEqual("", state["review_status"])
        self.assertNotIn("buy_enabled", state)
        self.assertNotIn("sell_enabled", state)
        start_backend.assert_not_called()

    def test_release_emergency_stop_target_uses_empty_routine_when_metadata_missing(self) -> None:
        target = self.targets[0]
        config_path = target[0] / "config.json"
        config = read_json_dict(config_path)
        for key in (
            "routine_instance_name",
            "routine",
            "routine_name",
            "assigned_routine_instance_id",
        ):
            config.pop(key, None)
        config_path.write_text(
            json.dumps(config, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        self._write_state(
            target[0],
            status="EMERGENCY_STOPPED",
            trade_enabled=False,
            holding_qty=1,
            emergency_stopped_at="2026-07-29 09:00:00",
            emergency_reason="USER_EMERGENCY_STOP",
        )
        (target[0] / "orders.json").write_text(
            json.dumps({"orders": []}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        expected_reason = emergency_ops.emergency_review_reason_for_stock(target[0])[1]
        window = type(
            "WindowWithoutRoutineMetadata",
            (),
            {"startup_recovery_session_ready": lambda _self, refresh=False: True},
        )()

        with (
            patch("gui_main_emergency_ops.append_stock_log"),
            patch("gui_main_emergency_ops.now_text", return_value="2026-07-29 10:07:00"),
            patch("gui_auto_trade_setting_window.auto_trade_start_selected_auto_trades") as start_backend,
        ):
            result = emergency_ops.release_emergency_stop_target(
                window,
                target[0],
                target[1],
                target[2],
            )

        state = read_json_dict(target[0] / "state.json")
        self.assertEqual(emergency_ops.BLOCKED_IN_EMERGENCY, result)
        self.assertEqual("EMERGENCY_STOPPED", state["status"])
        self.assertEqual("", state["review_routine"])
        self.assertFalse(state["trade_enabled"])
        self.assertTrue(state["review_required"])
        self.assertEqual("PENDING", state["review_status"])
        self.assertEqual(expected_reason, state["review_reason"])
        self.assertEqual("2026-07-29 09:00:00", state["emergency_stopped_at"])
        self.assertEqual("USER_EMERGENCY_STOP", state["emergency_reason"])
        self.assertNotIn("emergency_release_check", state)
        self.assertNotIn("buy_enabled", state)
        self.assertNotIn("sell_enabled", state)
        start_backend.assert_not_called()

    def test_release_emergency_stop_target_blocks_pending_integrity_error(self) -> None:
        target = self.targets[0]
        self._write_state(
            target[0],
            status="EMERGENCY_STOPPED",
            trade_enabled=False,
            holding_qty=0,
            emergency_stopped_at="2026-07-29 09:00:00",
            emergency_reason="USER_EMERGENCY_STOP",
        )
        (target[0] / "orders.json").write_text(
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
            )
            + "\n",
            encoding="utf-8",
        )
        window = type(
            "WindowWithoutRoutineName",
            (),
             {
                 "production_recovery_stock_is_review_required": lambda _self, _code: False,
                 "startup_recovery_session_ready": lambda _self, refresh=False: True,
             },
        )()

        with (
            patch("gui_main_emergency_ops.append_stock_log"),
            patch("gui_main_emergency_ops.now_text", return_value="2026-07-29 10:08:00"),
            patch("gui_auto_trade_setting_window.auto_trade_start_selected_auto_trades") as start_backend,
        ):
            result = emergency_ops.release_emergency_stop_target(
                window,
                target[0],
                target[1],
                target[2],
            )

        state = read_json_dict(target[0] / "state.json")
        self.assertEqual(emergency_ops.BLOCKED_IN_EMERGENCY, result)
        self.assertEqual("EMERGENCY_STOPPED", state["status"])
        self.assertTrue(state["review_required"])
        self.assertEqual("PENDING", state["review_status"])
        self.assertEqual("미체결 데이터 오류", state["review_reason"])
        self.assertIn("PENDING_ORDER_QTY_MISSING", state["review_detail"])
        self.assertFalse(state["trade_enabled"])
        self.assertEqual("2026-07-29 09:00:00", state["emergency_stopped_at"])
        self.assertEqual("USER_EMERGENCY_STOP", state["emergency_reason"])
        self.assertNotIn("emergency_release_check", state)
        self.assertNotIn("buy_enabled", state)
        self.assertNotIn("sell_enabled", state)
        start_backend.assert_not_called()

    def test_release_emergency_stop_target_blocks_operation_data_mismatch(self) -> None:
        target = self.targets[0]
        self._write_state(
            target[0],
            status="EMERGENCY_STOPPED",
            trade_enabled=False,
            holding_qty=0,
            avg_price=1000,
            emergency_stopped_at="2026-07-29 09:00:00",
            emergency_reason="USER_EMERGENCY_STOP",
        )
        (target[0] / "orders.json").write_text(
            json.dumps({"orders": []}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        window = type(
            "WindowWithoutRoutineName",
            (),
             {
                 "production_recovery_stock_is_review_required": lambda _self, _code: False,
                 "startup_recovery_session_ready": lambda _self, refresh=False: True,
             },
        )()

        with (
            patch("gui_main_emergency_ops.append_stock_log"),
            patch("gui_main_emergency_ops.now_text", return_value="2026-07-29 10:09:00"),
            patch("gui_auto_trade_setting_window.auto_trade_start_selected_auto_trades") as start_backend,
        ):
            result = emergency_ops.release_emergency_stop_target(
                window,
                target[0],
                target[1],
                target[2],
            )

        state = read_json_dict(target[0] / "state.json")
        self.assertEqual(emergency_ops.BLOCKED_IN_EMERGENCY, result)
        self.assertEqual("EMERGENCY_STOPPED", state["status"])
        self.assertTrue(state["review_required"])
        self.assertEqual("PENDING", state["review_status"])
        self.assertEqual("운영 데이터 불일치", state["review_reason"])
        self.assertIn("보유 0인데 평단 존재", state["review_detail"])
        self.assertNotEqual("RESOLVED", state["review_status"])
        self.assertFalse(state["trade_enabled"])
        self.assertEqual("2026-07-29 09:00:00", state["emergency_stopped_at"])
        self.assertEqual("USER_EMERGENCY_STOP", state["emergency_reason"])
        self.assertNotIn("emergency_release_check", state)
        self.assertNotIn("buy_enabled", state)
        self.assertNotIn("sell_enabled", state)
        start_backend.assert_not_called()

    def test_stock_name_double_click_toggles_operation_exclusion(self) -> None:
        target = self.targets[0]
        self.window.stock_table.insertRow(0)
        self.window.stock_table.setItem(0, 0, QTableWidgetItem(target[1]))
        name_item = QTableWidgetItem(target[2])
        self.window.stock_table.setItem(0, 1, name_item)

        with (
            patch("gui_auto_trade_status_ops.append_stock_log"),
            patch("gui_auto_trade_status_ops.append_changelog"),
            patch("gui_auto_trade_status_ops.show_toast"),
        ):
            self.window.on_stock_table_name_item_double_clicked(name_item)

        config = read_json_dict(target[0] / "config.json")
        state = read_json_dict(target[0] / "state.json")
        self.assertTrue(config.get("operation_excluded"))
        self.assertFalse(state.get("trade_enabled"))


if __name__ == "__main__":
    unittest.main()
