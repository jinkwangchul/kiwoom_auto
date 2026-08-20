# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication, QLabel

import gui_operation_environment as environment
import gui_stock_data
from stock_repository import StockRepository


class StockRegistrationRosterPolicyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_default_legacy_and_malformed_policy_are_waiting(self) -> None:
        self.assertEqual({"default_location": "WAITING"}, environment.stock_registration_policy())
        self.assertEqual(
            {"default_location": "WAITING"},
            environment.stock_registration_policy({"regular_market": {}}),
        )
        self.assertEqual(
            {"default_location": "WAITING"},
            environment.stock_registration_policy(
                {"stock_registration": {"default_location": "RUNNING"}}
            ),
        )

    def test_dialog_load_select_build_and_reset(self) -> None:
        policy = environment.default_operation_policy()
        policy["stock_registration"] = {"default_location": "EXCLUDED"}
        with patch.object(environment, "read_operation_policy", return_value=policy):
            dialog = environment.OperationEnvironmentSettingsDialog()
        self.addCleanup(dialog.deleteLater)
        self.assertFalse(dialog.registration_waiting.isChecked())
        self.assertTrue(dialog.registration_excluded.isChecked())
        self.assertIn("8. 종목등록 설정", [label.text() for label in dialog.findChildren(QLabel)])
        dialog.registration_waiting.click()
        self.assertTrue(dialog.registration_waiting.isChecked())
        self.assertFalse(dialog.registration_excluded.isChecked())
        self.assertEqual(
            "WAITING",
            dialog.build_policy_from_widgets()["stock_registration"]["default_location"],
        )
        dialog._load_official_settings_defaults()
        self.assertTrue(dialog.registration_waiting.isChecked())
        self.assertFalse(dialog.registration_excluded.isChecked())

    def test_save_reload_and_partial_writer_preserve_location(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "operation_policy.json"
            policy = environment.default_operation_policy()
            policy["stock_registration"] = {"default_location": "EXCLUDED"}
            environment.write_operation_policy(policy, path=path)
            environment.write_operation_policy(
                {"regular_market": {"start_time": "09:10:00"}},
                path=path,
            )
            loaded = environment.read_operation_policy(path=path)
        self.assertEqual("EXCLUDED", loaded["stock_registration"]["default_location"])

    def test_dialog_save_reloads_excluded_from_temp_policy(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "operation_policy.json"
            with patch.object(environment, "OPERATION_POLICY_PATH", path), patch.object(
                environment, "append_changelog"
            ), patch.object(environment, "show_toast"), patch.object(
                environment, "append_production_event"
            ):
                dialog = environment.OperationEnvironmentSettingsDialog()
                self.addCleanup(dialog.deleteLater)
                dialog.registration_excluded.click()
                dialog.accept()
                reloaded = environment.OperationEnvironmentSettingsDialog()
                self.addCleanup(reloaded.deleteLater)
        self.assertFalse(reloaded.registration_waiting.isChecked())
        self.assertTrue(reloaded.registration_excluded.isChecked())

    def test_true_new_waiting_and_excluded_apply_once_without_state_mutation(self) -> None:
        for location, excluded in (("WAITING", False), ("EXCLUDED", True)):
            with self.subTest(location=location), tempfile.TemporaryDirectory() as temp:
                repo = StockRepository(Path(temp))
                policy_reader = patch.object(
                    environment,
                    "read_operation_policy",
                    return_value={"stock_registration": {"default_location": location}},
                )
                with patch.object(gui_stock_data, "stock_repository_factory", return_value=repo), policy_reader as reader:
                    self.assertTrue(gui_stock_data.append_base_stock("005930", "Test"))
                    stock_dir = repo.resolve_stock_dir("005930", "Test")
                    first_config = json.loads((stock_dir / "config.json").read_text(encoding="utf-8"))
                    first_state = (stock_dir / "state.json").read_bytes()
                    reader.return_value = {
                        "stock_registration": {
                            "default_location": "WAITING" if excluded else "EXCLUDED"
                        }
                    }
                    self.assertTrue(gui_stock_data.append_base_stock("005930", "Test"))
                    second_config = json.loads((stock_dir / "config.json").read_text(encoding="utf-8"))
                self.assertIs(first_config["operation_excluded"], excluded)
                self.assertEqual(first_config, second_config)
                self.assertEqual(first_state, (stock_dir / "state.json").read_bytes())
                self.assertEqual("STOPPED", json.loads(first_state.decode("utf-8"))["status"])

    def test_existing_first_assignment_move_and_unassign_preserve_category(self) -> None:
        for excluded in (False, True):
            with self.subTest(excluded=excluded), tempfile.TemporaryDirectory() as temp:
                repo = StockRepository(Path(temp))
                stock_dir = repo.ensure_stock_folder("005930", "Test")
                config_path = stock_dir / "config.json"
                config = json.loads(config_path.read_text(encoding="utf-8"))
                config["operation_excluded"] = excluded
                config_path.write_text(json.dumps(config), encoding="utf-8")
                self.assertTrue(repo.update_stock_routine_instance(
                    "005930", "Test", instance_id="A", instance_name="A",
                    definition_id="D1", routine_type="RoutineA",
                ))
                self.assertTrue(repo.update_stock_routine_instance(
                    "005930", "Test", instance_id="B", instance_name="B",
                    definition_id="D2", routine_type="RoutineB",
                ))
                self.assertTrue(repo.update_stock_routine("005930", "Test", []))
                saved = json.loads(config_path.read_text(encoding="utf-8"))
                self.assertIs(saved["operation_excluded"], excluded)

    def test_registration_modules_do_not_register_session_participants(self) -> None:
        sources = (
            Path(gui_stock_data.__file__).read_text(encoding="utf-8"),
            Path(__file__).parents[1].joinpath("gui_auto_trade_setting_window.py").read_text(encoding="utf-8"),
        )
        self.assertTrue(all(
            "auto_trade_register_current_session_operation_participants" not in source
            for source in sources
        ))


if __name__ == "__main__":
    unittest.main()
