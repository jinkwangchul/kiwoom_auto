# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QPoint
from PyQt5.QtWidgets import QApplication, QDialogButtonBox

from gui_operation_environment import (
    OperationEnvironmentSettingsDialog,
    ProgramFactoryResetConfirmDialog,
    default_operation_policy,
)
import gui_operation_environment as operation_environment
from program_factory_reset import (
    execute_program_factory_reset,
    factory_reset_manifest,
    validate_factory_reset_safety,
)
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
            "artifacts",
            "reports",
            "routines",
            "_등록확인폴더",
            "_지표추종매매",
        ):
            (root / name).mkdir(parents=True, exist_ok=True)

        stock_dir = root / "stocks" / "000001_테스트"
        _write_json(stock_dir / "config.json", {"code": "000001", "name": "테스트"})
        _write_json(stock_dir / "state.json", {"status": "STOPPED", "holding_qty": 0})
        _write_json(stock_dir / "orders.json", {"orders": []})
        _write_json(root / "routine_instances" / "instance-1" / "instance.json", {"id": "instance-1"})
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
        _write_json(root / "_등록확인폴더" / "budget.json", {"default": True})
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
            {"operation_status": "NORMAL_ENDED", "emergency_stop": False},
        )
        (root / "runtime" / "routine_signal_probe.log").write_text("generated", encoding="utf-8")

    def test_confirmation_requires_exact_text(self) -> None:
        dialog = ProgramFactoryResetConfirmDialog()
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

    def test_reset_clears_user_data_and_preserves_parent_resources(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._project_fixture(root)
            parent_code = (root / "routines" / "부모루틴" / "routine.py").read_bytes()
            parent_rules = (root / "routines" / "부모루틴" / "rules.json").read_bytes()

            result = execute_program_factory_reset(root, broker_connected=False)

            self.assertTrue(result["success"], result["issues"])
            self.assertEqual([], list((root / "stocks").iterdir()))
            self.assertEqual([], list((root / "routine_instances").iterdir()))
            self.assertEqual([], list((root / "archived_stocks").iterdir()))
            self.assertEqual([], list((root / "artifacts").iterdir()))
            self.assertEqual([], list((root / "reports").iterdir()))
            self.assertFalse((root / "invalid_items.log").exists())

            routine_dir = root / "routines" / "부모루틴"
            self.assertEqual(parent_code, (routine_dir / "routine.py").read_bytes())
            self.assertEqual(parent_rules, (routine_dir / "rules.json").read_bytes())
            self.assertFalse((routine_dir / "approval_session.json").exists())
            self.assertFalse((routine_dir / "reports").exists())
            self.assertTrue((root / "_등록확인폴더" / "budget.json").exists())
            self.assertTrue((root / "_지표추종매매" / "budget.json").exists())
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
                },
                runtime_files,
            )

    def test_reset_is_blocked_before_any_change_when_unsafe(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._project_fixture(root)
            stock_state = root / "stocks" / "000001_테스트" / "state.json"
            _write_json(stock_state, {"status": "RUNNING", "holding_qty": 1})

            before = stock_state.read_bytes()
            result = execute_program_factory_reset(root, broker_connected=False)

            self.assertFalse(result["success"])
            self.assertEqual(before, stock_state.read_bytes())
            self.assertTrue((root / "routine_instances" / "instance-1").exists())

    def test_connected_broker_blocks_reset(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._project_fixture(root)
            result = validate_factory_reset_safety(root, broker_connected=True)
            self.assertFalse(result["success"])
            self.assertIn("키움 서버 연결을 먼저 종료해 주세요.", result["issues"])

    def test_manifest_is_explicit(self) -> None:
        manifest = factory_reset_manifest()
        self.assertIn("stocks", manifest["DELETE_CONTENTS"])
        self.assertIn("operation_policy.json", manifest["RESET"])
        self.assertIn("routines", manifest["PRESERVE"])


if __name__ == "__main__":
    unittest.main()
