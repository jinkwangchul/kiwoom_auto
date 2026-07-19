from __future__ import annotations

import os
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication, QDialog, QPushButton

from gui_indicator_follow_routine_settings_dialog import IndicatorFollowRoutineSettingsDialog
from gui_routine_registration_dialog import (
    RoutineRegistrationDialog,
    suggest_routine_instance_display_name,
)
from routine_instance_repository import RoutineInstanceCreateRequest, RoutineInstanceRepository


@unittest.skipIf(
    getattr(QApplication, "__name__", "") == "_QtImportStub",
    "requires real PyQt widgets; the legacy GUI test module installed global stubs",
)
class RoutineRegistrationDialogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_dialog_returns_only_registration_metadata(self) -> None:
        dialog = RoutineRegistrationDialog(
            definition_id="indicator_follow",
            definition_display_name="지표추종매매",
        )
        dialog.name_edit.setText("대형주 추세형")
        dialog.description_edit.setText("대형주 중심")
        dialog.buy_limit_enabled_check.setChecked(True)
        dialog.buy_limit_amount_edit.setText("12,000,000")

        screenshot_path = os.environ.get("ROUTINE_REGISTRATION_SCREENSHOT_PATH", "").strip()
        if screenshot_path:
            dialog.show()
            self.app.processEvents()
            self.assertTrue(dialog.grab().save(screenshot_path))

        dialog._accept_validated()

        self.assertEqual(QDialog.Accepted, dialog.result())
        request = dialog.registration_request
        self.assertIsNotNone(request)
        assert request is not None
        self.assertEqual("indicator_follow", request.definition_id)
        self.assertEqual("대형주 추세형", request.display_name)
        self.assertEqual(12_000_000, request.buy_limit_amount)
        self.assertEqual("비활성", dialog.new_status_label.text())

    def test_default_name_uses_persisted_instance_count_and_description_label_is_memo(self) -> None:
        self.assertEqual(
            "지표추종매매A",
            suggest_routine_instance_display_name("지표추종매매", 0),
        )
        self.assertEqual(
            "지표추종매매B",
            suggest_routine_instance_display_name("지표추종매매", 1),
        )
        suggested = suggest_routine_instance_display_name(
            "지표추종매매",
            2,
        )
        dialog = RoutineRegistrationDialog(
            definition_id="indicator_follow",
            definition_display_name="지표추종매매",
            initial_display_name=suggested,
        )

        self.assertEqual("지표추종매매C", dialog.name_edit.text())
        self.assertEqual("메모", dialog.form_layout.labelForField(dialog.description_edit).text())

    def test_default_name_stops_at_z_without_inventing_an_overflow_rule(self) -> None:
        self.assertEqual(
            "지표추종매매Z",
            suggest_routine_instance_display_name("지표추종매매", 25),
        )
        self.assertEqual(
            "",
            suggest_routine_instance_display_name("지표추종매매", 26),
        )

    def test_cancel_stops_before_repository_or_rules_conversion(self) -> None:
        fake_self = SimpleNamespace(
            definition_id="indicator_follow",
            definition_display_name="지표추종매매",
            routine_name="지표추종매매",
            build_registration_rules_from_current_ui_state=Mock(),
        )
        fake_dialog = Mock()
        fake_dialog.exec_.return_value = QDialog.Rejected
        fake_dialog.registration_request = None

        with (
            patch("gui_routine_registration_dialog.RoutineRegistrationDialog", return_value=fake_dialog),
            patch("gui_indicator_follow_routine_settings_dialog.RoutineInstanceRepository") as repository,
        ):
            result = IndicatorFollowRoutineSettingsDialog.open_registration_dialog(fake_self)

        self.assertIsNone(result)
        fake_self.build_registration_rules_from_current_ui_state.assert_not_called()
        repository.assert_not_called()

    def test_success_refreshes_parent_once_after_repository_success(self) -> None:
        parent = SimpleNamespace(refresh_all=Mock())
        request = RoutineInstanceCreateRequest(
            definition_id="indicator_follow",
            display_name="대형주 추세형",
        )
        fake_self = SimpleNamespace(
            definition_id="indicator_follow",
            definition_display_name="지표추종매매",
            routine_name="지표추종매매",
            build_registration_rules_from_current_ui_state=Mock(
                return_value={"success": True, "rules": {"buy": {}}, "error": ""}
            ),
            parent=lambda: parent,
        )
        fake_dialog = Mock()
        fake_dialog.exec_.return_value = QDialog.Accepted
        fake_dialog.registration_request = request
        instance = SimpleNamespace(instance_id="instance-id", display_name="대형주 추세형")
        repository = Mock()
        repository.create_instance.return_value = SimpleNamespace(
            success=True,
            instance=instance,
            error="",
        )

        with (
            patch("gui_routine_registration_dialog.RoutineRegistrationDialog", return_value=fake_dialog),
            patch("gui_indicator_follow_routine_settings_dialog.RoutineInstanceRepository", return_value=repository),
            patch("gui_indicator_follow_routine_settings_dialog.QMessageBox.information"),
        ):
            result = IndicatorFollowRoutineSettingsDialog.open_registration_dialog(fake_self)

        self.assertIs(instance, result)
        repository.create_instance.assert_called_once_with(request, {"buy": {}})
        parent.refresh_all.assert_called_once_with()

    def test_storage_failure_does_not_refresh_parent(self) -> None:
        parent = SimpleNamespace(refresh_all=Mock())
        request = RoutineInstanceCreateRequest(
            definition_id="indicator_follow",
            display_name="대형주 추세형",
        )
        fake_self = SimpleNamespace(
            definition_id="indicator_follow",
            definition_display_name="지표추종매매",
            routine_name="지표추종매매",
            build_registration_rules_from_current_ui_state=Mock(
                return_value={"success": True, "rules": {}, "error": ""}
            ),
            parent=lambda: parent,
        )
        fake_dialog = Mock()
        fake_dialog.exec_.return_value = QDialog.Accepted
        fake_dialog.registration_request = request
        repository = Mock()
        repository.create_instance.return_value = SimpleNamespace(
            success=False,
            instance=None,
            error="write failed",
        )

        with (
            patch("gui_routine_registration_dialog.RoutineRegistrationDialog", return_value=fake_dialog),
            patch("gui_indicator_follow_routine_settings_dialog.RoutineInstanceRepository", return_value=repository),
            patch("gui_indicator_follow_routine_settings_dialog.QMessageBox.critical"),
        ):
            result = IndicatorFollowRoutineSettingsDialog.open_registration_dialog(fake_self)

        self.assertIsNone(result)
        parent.refresh_all.assert_not_called()

    def test_registration_rules_use_existing_ui_mapper_without_writing(self) -> None:
        base_rules = {"indicator_follow_ui_state": {"state": {"basic": {}}}}
        pending_rules = {
            **base_rules,
            "indicator_follow_rule_pending": {"mode": "merge_add_candidate"},
        }
        mapper = Mock()
        mapper.build_engine_rules_pending_from_ui_state.return_value = {
            "pending_rules": pending_rules,
            "validation_warnings": [],
            "postponed": ["existing postponed mapping"],
        }
        fake_self = SimpleNamespace(
            build_rules_with_indicator_follow_ui_state=Mock(return_value=base_rules),
            collect_indicator_follow_ui_state=Mock(return_value={"basic": {}}),
            _load_indicator_follow_rule_mapper=Mock(return_value=mapper),
        )

        result = IndicatorFollowRoutineSettingsDialog.build_registration_rules_from_current_ui_state(
            fake_self
        )

        self.assertTrue(result["success"])
        self.assertEqual(pending_rules, result["rules"])
        mapper.build_engine_rules_pending_from_ui_state.assert_called_once_with(
            {"basic": {}},
            base_rules,
        )

    def test_actual_settings_ui_builds_registration_snapshot_without_source_write(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        routine_dir = project_root / "routines" / "지표추종매매"
        rules_path = routine_dir / "rules.json"
        before = hashlib.sha256(rules_path.read_bytes()).hexdigest()

        with patch("gui_indicator_follow_routine_settings_dialog.QTimer.singleShot"):
            dialog = IndicatorFollowRoutineSettingsDialog(
                rules_path=rules_path,
                routine_path=routine_dir,
                routine_name="지표추종매매",
                definition_id="indicator_follow",
            )
        try:
            self.assertEqual("지표추종매매 신규 등록설정", dialog.windowTitle())
            self.assertTrue(dialog.registration_mode_label.isVisibleTo(dialog.control_tab))
            self.assertEqual("신규 등록", dialog.registration_mode_label.text())
            self.assertEqual(312, dialog.registration_mode_label.width())
            self.assertEqual(52, dialog.registration_mode_label.height())
            self.assertEqual(18, dialog.registration_mode_label.font().pointSize())
            self.assertEqual("registration", dialog.settings_mode)
            self.assertEqual("등록", dialog.save_button.text())
            self.assertIs(dialog.register_button, dialog.save_button)
            self.assertFalse(hasattr(dialog, "control_full_view_button"))
            self.assertFalse(
                {
                    "전체보기",
                    "전체접기",
                }
                & {
                    button.text()
                    for button in dialog.control_tab.findChildren(QPushButton)
                }
            )
            dialog._apply_control_section_mode("summary", force=True)
            dialog._toggle_control_section_mode("buy")
            self.assertTrue(dialog.buy_detail_expanded)
            self.assertFalse(dialog.sell_detail_expanded)
            dialog._apply_control_section_mode("summary", force=True)
            screenshot_path = os.environ.get("ROUTINE_SETTINGS_SCREENSHOT_PATH", "").strip()
            if screenshot_path:
                dialog.show()
                self.app.processEvents()
                self.assertTrue(dialog.grab().save(screenshot_path))
            result = dialog.build_registration_rules_from_current_ui_state()
        finally:
            dialog.close()

        after = hashlib.sha256(rules_path.read_bytes()).hexdigest()
        self.assertTrue(result["success"], result.get("error"))
        self.assertIn("indicator_follow_ui_state", result["rules"])
        self.assertIn("indicator_follow_rule_pending", result["rules"])
        self.assertEqual(before, after)

    def test_existing_instance_uses_instance_name_in_window_title(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        routine_dir = project_root / "routines" / "지표추종매매"
        with patch("gui_indicator_follow_routine_settings_dialog.QTimer.singleShot"):
            dialog = IndicatorFollowRoutineSettingsDialog(
                rules_path=routine_dir / "rules.json",
                routine_path=routine_dir,
                routine_name="동전주 매매",
                definition_id="indicator_follow",
                definition_display_name="지표추종매매",
                instance_id="a52f539d-4f18-4ef6-b0cf-f471567982a1",
            )
        try:
            self.assertEqual("동전주 매매 설정", dialog.windowTitle())
            self.assertTrue(dialog.registration_mode_label.isVisibleTo(dialog.control_tab))
            self.assertEqual("동전주 매매 설정", dialog.registration_mode_label.text())
            self.assertEqual(312, dialog.registration_mode_label.width())
            self.assertEqual(52, dialog.registration_mode_label.height())
            self.assertEqual("edit", dialog.settings_mode)
            self.assertEqual("다른 이름으로 등록", dialog.register_button.text())
            self.assertEqual("저장", dialog.save_button.text())
            screenshot_path = os.environ.get(
                "ROUTINE_EXISTING_SETTINGS_SCREENSHOT_PATH",
                "",
            ).strip()
            if screenshot_path:
                dialog.show()
                self.app.processEvents()
                self.assertTrue(dialog.grab().save(screenshot_path))
        finally:
            dialog.close()

    def test_edit_mode_loads_and_updates_current_instance_rules_only(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        routine_dir = project_root / "routines" / "지표추종매매"
        source_rules = json.loads(
            (routine_dir / "rules.json").read_text(encoding="utf-8")
        )
        source_rules["indicator_follow_ui_state"]["state"]["basic"][
            "basic_error_policy_combo"
        ] = "매매지속"

        with tempfile.TemporaryDirectory() as temp:
            instance_rules_path = Path(temp) / "rules.json"
            instance_rules_path.write_text(
                json.dumps(source_rules, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            with patch("gui_indicator_follow_routine_settings_dialog.QTimer.singleShot"):
                dialog = IndicatorFollowRoutineSettingsDialog(
                    rules_path=instance_rules_path,
                    routine_path=routine_dir,
                    routine_name="동전주 매매",
                    definition_id="indicator_follow",
                    definition_display_name="지표추종매매",
                    instance_id="edit-instance-id",
                    settings_mode="edit",
                )
            try:
                self.assertEqual(
                    "매매지속",
                    dialog.basic_error_policy_combo.currentText(),
                )
                dialog.basic_error_policy_combo.setCurrentText("매매중지")
                with patch.object(
                    RoutineInstanceRepository,
                    "create_instance",
                ) as create_instance:
                    result = dialog.save_indicator_follow_ui_state_to_rules()
                create_instance.assert_not_called()
                self.assertTrue(result["success"], result.get("error"))
            finally:
                dialog.close()

            saved_rules = json.loads(instance_rules_path.read_text(encoding="utf-8"))
            self.assertEqual(
                "매매중지",
                saved_rules["indicator_follow_ui_state"]["state"]["basic"][
                    "basic_error_policy_combo"
                ],
            )

    def test_long_instance_name_shrinks_then_uses_ellipsis_inside_fixed_stamp(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        routine_dir = project_root / "routines" / "지표추종매매"
        long_name = "아주 긴 이름의 지표추종 자동매매 등록 인스턴스 설정"
        with patch("gui_indicator_follow_routine_settings_dialog.QTimer.singleShot"):
            dialog = IndicatorFollowRoutineSettingsDialog(
                rules_path=routine_dir / "rules.json",
                routine_path=routine_dir,
                routine_name=long_name,
                definition_id="indicator_follow",
                definition_display_name="지표추종매매",
                instance_id="long-name-instance",
            )
        try:
            self.assertEqual(312, dialog.registration_mode_label.width())
            self.assertLessEqual(dialog.registration_mode_label.font().pointSize(), 18)
            self.assertTrue(dialog.registration_mode_label.text().endswith("…"))
            self.assertEqual(f"{long_name} 설정", dialog.registration_mode_label.toolTip())
        finally:
            dialog.close()


if __name__ == "__main__":
    unittest.main()
