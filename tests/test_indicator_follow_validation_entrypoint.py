# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication

import gui_indicator_follow_routine_settings_dialog as dialog_module
from routines.지표추종매매.routine_validation_contract import (
    ValidationSettingsSnapshot,
)


class IndicatorFollowValidationEntrypointTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        cls.project_root = Path(__file__).resolve().parents[1]
        cls.routine_dir = cls.project_root / "routines" / "지표추종매매"
        cls.source_rules_path = cls.routine_dir / "rules.json"

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.rules_path = Path(self.temp_dir.name) / "rules.json"
        self.rules_path.write_bytes(self.source_rules_path.read_bytes())
        self.dialogs = []

    def tearDown(self) -> None:
        for dialog in self.dialogs:
            dialog.close()
            dialog.deleteLater()
        self.temp_dir.cleanup()

    def _dialog(self, mode: str):
        kwargs = {
            "rules_path": self.rules_path,
            "routine_path": self.routine_dir,
            "routine_name": "지표추종매매",
            "definition_id": "indicator_follow",
            "settings_mode": mode,
        }
        if mode == "edit":
            kwargs["instance_id"] = "INSTANCE-VALIDATION-1"
        with patch.object(dialog_module.QTimer, "singleShot"):
            dialog = dialog_module.IndicatorFollowRoutineSettingsDialog(**kwargs)
        self.dialogs.append(dialog)
        return dialog

    @staticmethod
    def _bottom_button_indices(dialog):
        root = dialog.layout()
        row = root.itemAt(root.count() - 1).layout()
        return {
            item.widget(): index
            for index in range(row.count())
            if (item := row.itemAt(index)).widget() is not None
        }

    def test_validation_button_has_the_same_position_in_both_modes(self):
        for mode, save_text in (("registration", "등록"), ("edit", "변경")):
            with self.subTest(mode=mode):
                dialog = self._dialog(mode)
                self.assertEqual("검증차트", dialog.validation_chart_button.text())
                self.assertEqual(save_text, dialog.save_button.text())
                indices = self._bottom_button_indices(dialog)
                self.assertEqual(
                    indices[dialog.validation_chart_button] + 1,
                    indices[dialog.save_button],
                )

    def test_current_ui_preview_is_handed_off_as_snapshot_in_both_modes(self):
        preview_rules = {
            "bar": {"bar_minutes": 7},
            "buy": {"expression": {"operator": "AND"}},
        }
        expected_hash = ValidationSettingsSnapshot(preview_rules).rules_hash

        for mode in ("registration", "edit"):
            with self.subTest(mode=mode):
                dialog = self._dialog(mode)
                stored_rules = {"bar": {"bar_minutes": 1}, "stored": True}
                ui_state = {"basic": {"basic_signal_interval_combo": "7"}}
                mapper = SimpleNamespace(
                    build_engine_rules_preview_from_ui_state=Mock(
                        return_value={"preview_rules": preview_rules}
                    )
                )
                dialog.rules = stored_rules
                dialog.collect_indicator_follow_ui_state = Mock(
                    return_value=ui_state
                )
                dialog._load_indicator_follow_rule_mapper = Mock(
                    return_value=mapper
                )
                emitted = []
                dialog.validation_chart_requested.connect(emitted.append)

                dialog.validation_chart_button.click()

                self.assertEqual(1, len(emitted))
                self.assertIsInstance(emitted[0], ValidationSettingsSnapshot)
                self.assertEqual(expected_hash, emitted[0].rules_hash)
                self.assertEqual(preview_rules, emitted[0].to_dict())
                self.assertNotEqual(stored_rules, emitted[0].to_dict())
                dialog.collect_indicator_follow_ui_state.assert_called_once_with()
                mapper.build_engine_rules_preview_from_ui_state.assert_called_once()
                call_ui_state, call_rules = (
                    mapper.build_engine_rules_preview_from_ui_state.call_args.args
                )
                self.assertIs(ui_state, call_ui_state)
                self.assertEqual(stored_rules, call_rules)
                self.assertIsNot(stored_rules, call_rules)

    def test_preview_failures_do_not_emit(self):
        dialog = self._dialog("registration")
        dialog.collect_indicator_follow_ui_state = Mock(return_value={"basic": {}})
        emitted = []
        dialog.validation_chart_requested.connect(emitted.append)

        cases = (
            Mock(side_effect=RuntimeError("mapper load failed")),
            Mock(
                return_value=SimpleNamespace(
                    build_engine_rules_preview_from_ui_state=Mock(
                        side_effect=RuntimeError("preview failed")
                    )
                )
            ),
            Mock(
                return_value=SimpleNamespace(
                    build_engine_rules_preview_from_ui_state=Mock(return_value=None)
                )
            ),
            Mock(
                return_value=SimpleNamespace(
                    build_engine_rules_preview_from_ui_state=Mock(
                        return_value={"preview_rules": []}
                    )
                )
            ),
        )
        with patch.object(dialog_module.QMessageBox, "warning") as warning:
            for loader in cases:
                dialog._load_indicator_follow_rule_mapper = loader
                dialog.validation_chart_button.click()

        self.assertEqual([], emitted)
        self.assertEqual(len(cases), warning.call_count)

    def test_validation_click_does_not_save_write_or_close(self):
        before = hashlib.sha256(self.rules_path.read_bytes()).hexdigest()
        preview_rules = {"bar": {"bar_minutes": 3}}
        for mode in ("registration", "edit"):
            with self.subTest(mode=mode):
                dialog = self._dialog(mode)
                mapper = SimpleNamespace(
                    build_engine_rules_preview_from_ui_state=Mock(
                        return_value={"preview_rules": preview_rules}
                    )
                )
                dialog.collect_indicator_follow_ui_state = Mock(
                    return_value={"basic": {}}
                )
                dialog._load_indicator_follow_rule_mapper = Mock(
                    return_value=mapper
                )
                dialog.open_registration_dialog = Mock()
                dialog.commit_current_settings = Mock()
                dialog.save_edit_settings_and_close = Mock()

                with patch.object(dialog, "close") as close, \
                     patch.object(Path, "write_text", side_effect=AssertionError("write")), \
                     patch.object(Path, "write_bytes", side_effect=AssertionError("write")):
                    dialog.validation_chart_button.click()

                dialog.open_registration_dialog.assert_not_called()
                dialog.commit_current_settings.assert_not_called()
                dialog.save_edit_settings_and_close.assert_not_called()
                close.assert_not_called()

        self.assertEqual(
            before,
            hashlib.sha256(self.rules_path.read_bytes()).hexdigest(),
        )

    def test_existing_save_buttons_keep_their_mode_specific_connections(self):
        with patch.object(
            dialog_module.IndicatorFollowRoutineSettingsDialog,
            "open_registration_dialog",
        ) as register:
            registration = self._dialog("registration")
            registration.save_button.click()
            register.assert_called_once()

        with patch.object(
            dialog_module.IndicatorFollowRoutineSettingsDialog,
            "save_edit_settings_and_close",
        ) as save_edit:
            edit = self._dialog("edit")
            edit.save_button.click()
            save_edit.assert_called_once()


if __name__ == "__main__":
    unittest.main()
