# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QPoint, QTimer
from PyQt5.QtWidgets import QApplication, QDialogButtonBox, QWidget

from gui_operation_environment import (
    OperationEnvironmentSettingsDialog,
    ProgramFactoryResetConfirmDialog,
    default_operation_policy,
)
import gui_operation_environment as operation_environment
from program_factory_reset import (
    build_factory_reset_preview,
    execute_program_factory_reset,
    factory_reset_manifest,
    validate_factory_reset_safety,
)
import program_factory_reset as factory_reset
from logical_group_registry import LogicalGroupRepository
from routine_instance_repository import RoutineInstanceRepository
from stock_repository import StockRepository
from startup_runtime_initializer import initialize_pristine_startup_runtime


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class ProgramFactoryResetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _project_fixture(self, root: Path) -> None:
        for name in (
            "stocks",
            "routine_instances",
            "runtime",
            "archived_stocks",
            "assignment_episodes",
            "performance_ledger",
            "migration_manifests",
            "groups",
            "artifacts",
            "reports",
            "logs",
            "routines",
            "_지표추종매매",
        ):
            (root / name).mkdir(parents=True, exist_ok=True)

        stock_dir = root / "stocks" / "000001_테스트"
        _write_json(
            stock_dir / "config.json",
            {
                "code": "000001",
                "name": "테스트",
                "assigned_routine_instance_id": "instance-1",
            },
        )
        _write_json(
            stock_dir / "state.json",
            {
                "status": "RUNNING",
                "holding_qty": 3,
                "trade_enabled": True,
                "review_required": True,
            },
        )
        _write_json(stock_dir / "orders.json", {"orders": [{"status": "PENDING"}]})
        unassigned_dir = root / "stocks" / "000002_미지정"
        _write_json(unassigned_dir / "config.json", {"code": "000002", "name": "미지정"})
        _write_json(unassigned_dir / "state.json", {"status": "REVIEW_REQUIRED", "holding_qty": 0})
        _write_json(unassigned_dir / "orders.json", {"orders": []})

        group_id = "11111111-1111-4111-8111-111111111111"
        _write_json(
            root / "groups" / group_id / "group.json",
            {
                "schema_version": "1.0",
                "group_id": group_id,
                "definition_id": "parent",
                "base_name": "부모루틴",
                "display_name": "부모루틴",
                "slot": 0,
                "created_at": "2026-08-23T00:00:00+09:00",
            },
        )
        _write_json(
            root / "groups" / "registry.json",
            {
                "schema_version": "1.0",
                "mode": "logical",
                "group_ids": [group_id],
                "cutover_at": "2026-08-23T00:00:00+09:00",
            },
        )
        _write_json(
            root / "routine_instances" / "instance-1" / "instance.json",
            {"instance_id": "instance-1", "group_id": group_id},
        )
        _write_json(
            root / "assignment_episodes" / "000001" / "episodes.json",
            {"episodes": [{"episode_id": "episode-1"}]},
        )
        _write_json(
            root / "performance_ledger" / "000001" / "events.json",
            {"events": [{"performance_event_id": "event-1"}]},
        )
        _write_json(root / "performance_ledger" / "000001" / "entry_lots.json", {"lots": [{}]})
        _write_json(root / "migration_manifests" / "migration.json", {"applied": True})
        _write_json(root / "archived_stocks" / "old" / "state.json", {"status": "STOPPED"})
        (root / "artifacts" / "result.txt").write_text("generated", encoding="utf-8")
        (root / "reports" / "report.txt").write_text("generated", encoding="utf-8")
        (root / "invalid_items.log").write_text("generated", encoding="utf-8")

        routine_dir = root / "routines" / "부모루틴"
        routine_dir.mkdir(parents=True, exist_ok=True)
        (routine_dir / "routine.py").write_text("PARENT = True\n", encoding="utf-8")
        _write_json(routine_dir / "rules.json", {"default": True})
        _write_json(routine_dir / "approval_session.json", {"approved": True})
        (routine_dir / "reports").mkdir()
        (routine_dir / "reports" / "user-report.txt").write_text("generated", encoding="utf-8")
        _write_json(root / "_지표추종매매" / "budget.json", {"default": True})
        (root / "stock_library.json").write_text("{}\n", encoding="utf-8")
        (root / "screen_registry.json").write_text("{}\n", encoding="utf-8")
        (root / "기초종목.txt").write_text("sample\n", encoding="utf-8")
        (root / "PROJECT_CHANGELOG.txt").write_text("history\n", encoding="utf-8")
        _write_json(root / "operation_policy.json", {"regular_market": {"start_time": "10:00:00"}})
        _write_json(root / "global_schedule.json", {"start_time": "10:00:00", "end_buy_time": "12:00:00"})

        initialized = initialize_pristine_startup_runtime(root / "runtime")
        self.assertTrue(initialized["initialized"])
        _write_json(
            root / "runtime" / "operation_state.json",
            {"operation_status": "RUNNING", "emergency_stop": False},
        )
        _write_json(root / "runtime" / "order_queue.json", {"orders": [{"status": "PENDING"}]})
        _write_json(root / "runtime" / "order_locks.json", {"locks": [{"code": "000001"}]})
        _write_json(root / "runtime" / "order_executions.json", {"executions": [{"status": "DISPATCHING"}]})
        (root / "runtime" / "routine_signal_probe.log").write_text("generated", encoding="utf-8")
        _write_json(
            root / "runtime" / "realized_pnl.json",
            {"version": 1, "realizations": [{"realized_pnl": 1200}]},
        )
        (root / "runtime" / "stock_library.json").write_bytes(b'{"stocks":["000001"]}\n')
        (root / "runtime" / "stock_library_meta.json").write_bytes(b'{"state":"READY"}\n')

    def test_confirmation_requires_exact_text(self) -> None:
        dialog = ProgramFactoryResetConfirmDialog()
        self.assertEqual(
            "프로그램을 완전초기화 합니다.\n"
            "치명적인 손실을 초래할수 있습니다.\n"
            '아래 입력창에 "전체초기화"를 입력하세요.',
            dialog.warning_label.text(),
        )
        warning_width = max(
            dialog.warning_label.fontMetrics().horizontalAdvance(line)
            for line in dialog.WARNING_TEXT.splitlines()
        )
        horizontal_margin = dialog.warning_label.fontMetrics().horizontalAdvance("가") * 2
        warning_icon_gap = dialog.warning_label.fontMetrics().horizontalAdvance("가")
        warning_icon_width = dialog.warning_icon_label.width()
        self.assertEqual(
            warning_width + warning_icon_gap + warning_icon_width + (horizontal_margin * 2),
            dialog.minimumWidth(),
        )
        self.assertEqual(warning_width, dialog.warning_label.width())
        self.assertFalse(dialog.warning_icon_label.pixmap().isNull())
        dialog.show()
        self.app.processEvents()
        self.assertLess(
            dialog.warning_icon_label.geometry().left(),
            dialog.warning_label.geometry().left(),
        )
        warning_text_x = dialog.warning_label.mapTo(dialog, QPoint(0, 0)).x()
        confirmation_input_x = dialog.confirmation_input.mapTo(dialog, QPoint(0, 0)).x()
        self.assertEqual(warning_text_x, confirmation_input_x)
        self.assertGreaterEqual(dialog.minimumHeight(), 210)
        self.assertGreaterEqual(dialog.confirmation_input.minimumHeight(), 32)
        expected_input_width = dialog.confirmation_input.fontMetrics().horizontalAdvance("가" * 10) + 16
        self.assertEqual(expected_input_width, dialog.confirmation_input.width())
        self.assertIn("border: none", dialog.confirmation_input.styleSheet())
        self.assertGreaterEqual(dialog.reset_button.minimumWidth(), 110)
        self.assertGreaterEqual(dialog.cancel_button.minimumWidth(), 110)
        self.assertFalse(dialog.reset_button.isEnabled())
        for text in ("전체 초기화", "초기화", "전체초기화1", " 전체초기화", "전체초기화 "):
            dialog.confirmation_input.setText(text)
            self.assertFalse(dialog.reset_button.isEnabled(), text)
        dialog.confirmation_input.setText("전체초기화")
        self.assertTrue(dialog.reset_button.isEnabled())
        dialog.confirmation_input.clear()
        self.assertFalse(dialog.reset_button.isEnabled())
        dialog.close()

    def test_environment_dialog_exposes_factory_reset_button(self) -> None:
        dialog = OperationEnvironmentSettingsDialog()
        dialog.show()
        self.app.processEvents()
        self.assertEqual("프로그램 초기화", dialog.program_factory_reset_button.text())
        self.assertEqual(
            "operationEnvironmentProgramResetButton",
            dialog.program_factory_reset_button.objectName(),
        )
        self.assertEqual("설정 초기화", dialog.settings_reset_button.text())
        self.assertEqual(
            "operationEnvironmentSettingsResetButton",
            dialog.settings_reset_button.objectName(),
        )
        reset_y = dialog.program_factory_reset_button.mapTo(dialog, QPoint(0, 0)).y()
        settings_reset_y = dialog.settings_reset_button.mapTo(dialog, QPoint(0, 0)).y()
        save_y = dialog.settings_button_box.button(QDialogButtonBox.Save).mapTo(dialog, QPoint(0, 0)).y()
        cancel_y = dialog.settings_button_box.button(QDialogButtonBox.Cancel).mapTo(dialog, QPoint(0, 0)).y()
        self.assertEqual(reset_y, settings_reset_y)
        self.assertEqual(reset_y, save_y)
        self.assertEqual(save_y, cancel_y)
        reset_height = dialog.program_factory_reset_button.height()
        settings_reset_height = dialog.settings_reset_button.height()
        save_height = dialog.settings_button_box.button(QDialogButtonBox.Save).height()
        cancel_height = dialog.settings_button_box.button(QDialogButtonBox.Cancel).height()
        self.assertEqual(32, reset_height)
        self.assertEqual(reset_height, settings_reset_height)
        self.assertEqual(reset_height, save_height)
        self.assertEqual(save_height, cancel_height)
        self.assertTrue(dialog.program_factory_reset_button.styleSheet())
        self.assertEqual(
            dialog.program_factory_reset_button.styleSheet(),
            dialog.settings_reset_button.styleSheet(),
        )
        self.assertEqual(
            dialog.settings_reset_button.styleSheet(),
            dialog.settings_button_box.button(QDialogButtonBox.Save).styleSheet(),
        )
        self.assertEqual(
            dialog.settings_button_box.button(QDialogButtonBox.Save).styleSheet(),
            dialog.settings_button_box.button(QDialogButtonBox.Cancel).styleSheet(),
        )
        dialog.close()

    def test_settings_reset_changes_widgets_without_writing_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            policy_path = Path(temp) / "operation_policy.json"
            custom_policy = default_operation_policy()
            custom_policy["regular_market"] = {
                "start_time": "10:10:00",
                "end_time": "14:40:00",
            }
            custom_policy["starting_budget_defaults"] = {
                "quantity": 9,
                "amount_multiplier": 3.5,
                "limit_recommended_multiplier": 150.0,
                "limit_minimum_multiplier": 50.0,
            }
            _write_json(policy_path, custom_policy)
            before = policy_path.read_bytes()

            with patch.object(operation_environment, "OPERATION_POLICY_PATH", policy_path):
                dialog = OperationEnvironmentSettingsDialog()
                dialog.settings_reset_button.click()

                self.assertEqual("09:00:00", dialog.regular_start.time_text())
                self.assertEqual("15:20:00", dialog.regular_end.time_text())
                self.assertEqual(
                    ["장전프리", "장마감NTX", "추가시간3"],
                    [edit.text() for edit in dialog.extra_name],
                )
                self.assertEqual([False, False, False], [check.isChecked() for check in dialog.extra_enabled])
                self.assertEqual(
                    ["08:00:00", "15:40:00", ""],
                    [edit.time_text() for edit in dialog.extra_start],
                )
                self.assertEqual(
                    ["08:50:00", "19:50:00", ""],
                    [edit.time_text() for edit in dialog.extra_end],
                )
                self.assertEqual("09:00:00", dialog.scheduled_start.time_text())
                self.assertEqual("13:30:00", dialog.scheduled_end_buy.time_text())
                self.assertTrue(dialog.manual_use_regular.isChecked())
                self.assertFalse(dialog.manual_liquidation.isChecked())
                self.assertEqual(
                    [True, False, False, False, False],
                    [check.isChecked() for check in dialog.auto_close_checks],
                )
                self.assertEqual(
                    [True, False, False, False, False],
                    [check.isChecked() for check in dialog.early_close_checks],
                )
                self.assertFalse(dialog.auto_profit.isEnabled())
                self.assertFalse(dialog.auto_loss.isEnabled())
                self.assertFalse(dialog.early_profit.isEnabled())
                self.assertFalse(dialog.early_loss.isEnabled())
                self.assertEqual("5", dialog.liquidation_minutes.currentText())
                self.assertTrue(dialog.liquidation_checks["시장가"].isChecked())
                self.assertFalse(dialog.liquidation_checks["현재가"].isChecked())
                self.assertFalse(dialog.liquidation_checks["이월"].isChecked())
                self.assertEqual("1", dialog.starting_quantity.text())
                self.assertEqual("1.5", dialog.starting_amount_multiplier.text())
                self.assertEqual("100", dialog.limit_recommended_multiplier.text())
                self.assertEqual("25", dialog.limit_minimum_multiplier.text())
                reset_policy = dialog.build_policy_from_widgets()
                official_policy = default_operation_policy()
                for key, value in official_policy.items():
                    if key != "updated_at":
                        self.assertEqual(value, reset_policy[key], key)
                self.assertEqual(before, policy_path.read_bytes())
                dialog.close()

    def test_settings_reset_is_saved_only_by_save_button(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            policy_path = Path(temp) / "operation_policy.json"
            changelog_path = Path(temp) / "PROJECT_CHANGELOG.txt"
            custom_policy = default_operation_policy()
            custom_policy["starting_budget_defaults"] = {
                "quantity": 7,
                "amount_multiplier": 4.0,
                "limit_recommended_multiplier": 180.0,
                "limit_minimum_multiplier": 60.0,
            }
            _write_json(policy_path, custom_policy)
            changelog_path.write_text("", encoding="utf-8")

            with (
                patch.object(operation_environment, "OPERATION_POLICY_PATH", policy_path),
                patch.object(operation_environment, "CHANGELOG_PATH", changelog_path),
                patch.object(operation_environment, "show_toast"),
            ):
                dialog = OperationEnvironmentSettingsDialog()
                dialog.settings_reset_button.click()
                dialog.settings_button_box.button(QDialogButtonBox.Save).click()

            saved = json.loads(policy_path.read_text(encoding="utf-8"))
            self.assertEqual(default_operation_policy()["starting_budget_defaults"], saved["starting_budget_defaults"])
            self.assertEqual(default_operation_policy()["regular_market"], saved["regular_market"])

    def test_settings_reset_allows_edit_before_save(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            policy_path = Path(temp) / "operation_policy.json"
            changelog_path = Path(temp) / "PROJECT_CHANGELOG.txt"
            _write_json(policy_path, default_operation_policy())
            changelog_path.write_text("", encoding="utf-8")

            with (
                patch.object(operation_environment, "OPERATION_POLICY_PATH", policy_path),
                patch.object(operation_environment, "CHANGELOG_PATH", changelog_path),
                patch.object(operation_environment, "show_toast"),
            ):
                dialog = OperationEnvironmentSettingsDialog()
                dialog.settings_reset_button.click()
                dialog.starting_quantity.setText("3")
                dialog.settings_button_box.button(QDialogButtonBox.Save).click()

            saved = json.loads(policy_path.read_text(encoding="utf-8"))
            self.assertEqual(3, saved["starting_budget_defaults"]["quantity"])
            self.assertEqual(1.5, saved["starting_budget_defaults"]["amount_multiplier"])

    def test_settings_reset_then_cancel_keeps_saved_policy(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            policy_path = Path(temp) / "operation_policy.json"
            custom_policy = default_operation_policy()
            custom_policy["regular_market"] = {
                "start_time": "10:10:00",
                "end_time": "14:40:00",
            }
            _write_json(policy_path, custom_policy)
            before = policy_path.read_bytes()

            with patch.object(operation_environment, "OPERATION_POLICY_PATH", policy_path):
                dialog = OperationEnvironmentSettingsDialog()
                dialog.settings_reset_button.click()
                dialog.settings_button_box.button(QDialogButtonBox.Cancel).click()

            self.assertEqual(before, policy_path.read_bytes())

    def test_reset_clears_all_user_data_and_preserves_product_resources(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._project_fixture(root)
            parent_code = (root / "routines" / "부모루틴" / "routine.py").read_bytes()
            parent_rules = (root / "routines" / "부모루틴" / "rules.json").read_bytes()
            library = (root / "runtime" / "stock_library.json").read_bytes()
            library_meta = (root / "runtime" / "stock_library_meta.json").read_bytes()

            result = execute_program_factory_reset(root, broker_connected=True)

            self.assertTrue(result["success"], result["issues"])
            self.assertEqual(1, result["removed_groups"])
            self.assertEqual(1, result["removed_instances"])
            self.assertEqual(2, result["removed_stocks"])
            self.assertEqual(1, result["removed_assignment_episodes"])
            self.assertEqual(1, result["removed_performance_events"])
            self.assertEqual(0, result["broker_orders_called"])
            self.assertTrue(result["initialized_at"])
            self.assertEqual([], list((root / "groups").iterdir()))
            self.assertEqual([], list((root / "stocks").iterdir()))
            self.assertEqual([], list((root / "routine_instances").iterdir()))
            self.assertEqual([], list((root / "archived_stocks").iterdir()))
            self.assertEqual([], list((root / "assignment_episodes").iterdir()))
            self.assertEqual([], list((root / "performance_ledger").iterdir()))
            self.assertEqual([], list((root / "migration_manifests").iterdir()))
            self.assertEqual([], LogicalGroupRepository(root).list_groups())
            self.assertEqual([], RoutineInstanceRepository(root).list_instances())
            self.assertEqual([], StockRepository(root).list_stocks())
            self.assertEqual([], list((root / "artifacts").iterdir()))
            self.assertEqual([], list((root / "reports").iterdir()))
            self.assertFalse((root / "invalid_items.log").exists())

            routine_dir = root / "routines" / "부모루틴"
            self.assertEqual(parent_code, (routine_dir / "routine.py").read_bytes())
            self.assertEqual(parent_rules, (routine_dir / "rules.json").read_bytes())
            self.assertFalse((routine_dir / "approval_session.json").exists())
            self.assertFalse((routine_dir / "reports").exists())
            self.assertTrue((root / "_지표추종매매" / "budget.json").exists())
            self.assertFalse((root / "runtime" / "realized_pnl.json").exists())
            self.assertEqual(library, (root / "runtime" / "stock_library.json").read_bytes())
            self.assertEqual(library_meta, (root / "runtime" / "stock_library_meta.json").read_bytes())
            self.assertEqual("history\n", (root / "PROJECT_CHANGELOG.txt").read_text(encoding="utf-8"))

            policy = json.loads((root / "operation_policy.json").read_text(encoding="utf-8"))
            defaults = default_operation_policy()
            for key in defaults:
                if key != "updated_at":
                    self.assertEqual(defaults[key], policy[key], key)
            self.assertEqual(
                {
                    "quantity": 1,
                    "amount_multiplier": 1.5,
                    "limit_recommended_multiplier": 100.0,
                    "limit_minimum_multiplier": 25.0,
                },
                policy["starting_budget_defaults"],
            )
            runtime_files = {path.name for path in (root / "runtime").iterdir()}
            self.assertEqual(
                {
                    "order_queue.json",
                    "fills.json",
                    "positions.json",
                    "broker_holdings.json",
                    "order_executions.json",
                    "order_locks.json",
                    "routine_signals.json",
                    "stock_library.json",
                    "stock_library_meta.json",
                },
                runtime_files,
            )

    def test_running_holding_review_and_pending_state_do_not_block_reset(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._project_fixture(root)
            result = execute_program_factory_reset(root, broker_connected=False)

            self.assertTrue(result["success"], result["issues"])
            self.assertEqual(0, result["broker_orders_called"])
            self.assertEqual([], list((root / "stocks").iterdir()))

    def test_connected_broker_is_not_a_reset_guard(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._project_fixture(root)
            result = validate_factory_reset_safety(root, broker_connected=True)
            self.assertTrue(result["success"], result["issues"])

    def test_failure_rolls_back_every_original_byte_and_resumes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._project_fixture(root)
            before = {
                path.relative_to(root).as_posix(): path.read_bytes()
                for path in root.rglob("*")
                if path.is_file()
            }
            events: list[object] = []

            def quiesce() -> str:
                events.append("quiesced")
                return "token"

            def resume(token: object) -> None:
                events.append(("resumed", token))

            with patch.object(factory_reset, "_initialize_empty_state", side_effect=OSError("injected")):
                result = execute_program_factory_reset(
                    root,
                    quiesce=quiesce,
                    resume_after_failure=resume,
                )

            after = {
                path.relative_to(root).as_posix(): path.read_bytes()
                for path in root.rglob("*")
                if path.is_file()
            }
            self.assertFalse(result["success"])
            self.assertTrue(result["rollback_complete"])
            self.assertEqual(before, after)
            self.assertEqual(["quiesced", ("resumed", "token")], events)

    def test_partial_staging_failure_restores_only_moved_targets(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._project_fixture(root)
            before = {
                path.relative_to(root).as_posix(): path.read_bytes()
                for path in root.rglob("*")
                if path.is_file()
            }
            real_replace = factory_reset.os.replace
            injected = False

            def fail_second_target(source: object, destination: object) -> None:
                nonlocal injected
                if Path(source) == root / "routine_instances" and not injected:
                    injected = True
                    raise PermissionError("injected staging lock")
                real_replace(source, destination)

            with patch.object(factory_reset.os, "replace", side_effect=fail_second_target):
                result = execute_program_factory_reset(root)

            after = {
                path.relative_to(root).as_posix(): path.read_bytes()
                for path in root.rglob("*")
                if path.is_file()
            }
            self.assertFalse(result["success"])
            self.assertTrue(result["rollback_complete"])
            self.assertEqual(before, after)
            self.assertTrue((root / "groups" / "registry.json").exists())
            self.assertTrue((root / "routine_instances" / "instance-1").exists())

    def test_confirmation_cancel_does_not_call_reset_service(self) -> None:
        with (
            patch.object(ProgramFactoryResetConfirmDialog, "exec_", return_value=ProgramFactoryResetConfirmDialog.Rejected),
            patch("program_factory_reset.execute_program_factory_reset") as execute_reset,
            patch.object(operation_environment, "append_production_event") as append_event,
        ):
            dialog = OperationEnvironmentSettingsDialog(
                factory_reset_validator=validate_factory_reset_safety,
                factory_reset_executor=execute_reset,
            )
            dialog._request_program_factory_reset()
        execute_reset.assert_not_called()
        append_event.assert_not_called()
        dialog.close()

    def test_quiesce_stops_owner_timers_and_failure_resume_restores_them(self) -> None:
        owner = QWidget()
        owner_timer = QTimer(owner)
        owner_timer.start(1000)

        class _MarketDataHost:
            def __init__(self) -> None:
                self.cleared = 0

            def clear(self) -> None:
                self.cleared += 1

        class _OperationHost:
            def __init__(self) -> None:
                self._factory_reset_quiesced = False
                self._bar_commit_trigger_queue = [{"pending": True}]
                self._market_data_host = _MarketDataHost()

        host = _OperationHost()
        owner._main_monitoring_auto_trade_operation_host = host
        dialog = OperationEnvironmentSettingsDialog(owner)

        token = dialog._quiesce_for_program_factory_reset()
        self.assertFalse(owner_timer.isActive())
        self.assertTrue(owner._factory_reset_in_progress)
        self.assertTrue(host._factory_reset_quiesced)
        self.assertEqual([], host._bar_commit_trigger_queue)
        self.assertEqual(1, host._market_data_host.cleared)

        dialog._resume_after_program_factory_reset_failure(token)
        self.assertTrue(owner_timer.isActive())
        self.assertFalse(owner._factory_reset_in_progress)
        self.assertFalse(host._factory_reset_quiesced)
        owner_timer.stop()
        dialog.close()
        owner.close()

    def test_preview_is_read_only_and_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._project_fixture(root)
            before = (root / "runtime" / "order_queue.json").read_bytes()
            preview = build_factory_reset_preview(root)
            self.assertEqual(1, preview["removed_groups"])
            self.assertEqual(1, preview["removed_instances"])
            self.assertEqual(2, preview["removed_stocks"])
            self.assertEqual(before, (root / "runtime" / "order_queue.json").read_bytes())

    def test_manifest_is_explicit(self) -> None:
        manifest = factory_reset_manifest()
        self.assertIn("stocks", manifest["DELETE_CONTENTS"])
        self.assertIn("groups", manifest["DELETE_CONTENTS"])
        self.assertIn("assignment_episodes", manifest["DELETE_CONTENTS"])
        self.assertIn("performance_ledger", manifest["DELETE_CONTENTS"])
        self.assertIn("operation_policy.json", manifest["RESET"])
        self.assertIn("routines", manifest["PRESERVE"])
        self.assertIn("runtime/stock_library.json", manifest["PRESERVE"])
        self.assertNotIn("_등록확인폴더", manifest["PRESERVE"])


if __name__ == "__main__":
    unittest.main()
