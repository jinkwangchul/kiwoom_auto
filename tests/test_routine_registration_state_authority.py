from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch
from uuid import UUID

from PyQt5.QtWidgets import QDialog

from gui_indicator_follow_buy_controls import (
    normalize_legacy_buy_bollinger_sign_ui_state,
)
from gui_indicator_follow_routine_settings_dialog import (
    IndicatorFollowRoutineSettingsDialog,
    STATE_AUTHORITY_CANONICAL_DEFAULT,
    STATE_AUTHORITY_GROUP_REMEMBERED,
    STATE_AUTHORITY_INSTANCE_BASELINE,
    STATE_AUTHORITY_LEGACY_TEMPLATE_FALLBACK,
    _remember_successful_registration_state,
    register_routine_instance_snapshot,
)
from logical_group_registry import LogicalGroupRepository
from routine_instance_repository import (
    REGISTRATION_BASELINE_FILE,
    RoutineInstanceCreateRequest,
    RoutineInstanceRepository,
    RoutineInstanceCreateResult,
)


GROUP_1 = "dbf3790f-c00f-427f-90dc-15d8283f4e1a"
GROUP_2 = "0cb21141-cadc-4701-acce-7c915a9c0259"
INSTANCE_1 = UUID("8898a834-e994-4fc6-bcdd-df118622df88")


class RoutineRegistrationStateAuthorityTest(unittest.TestCase):
    def _write_definition(self, root: Path, definition_id: str = "indicator_follow") -> Path:
        routine_dir = root / "routines" / definition_id
        routine_dir.mkdir(parents=True, exist_ok=True)
        (routine_dir / "routine.json").write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "definition_id": definition_id,
                    "name": definition_id,
                    "settings_ui": "indicator_follow",
                    "module_name": f"{definition_id}_routine",
                    "rules_file": "rules.json",
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        template = routine_dir / "rules.json"
        template.write_text(
            json.dumps(
                {"indicator_follow_ui_state": {"state": {"source": "template"}}},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return template

    def _write_group(
        self,
        root: Path,
        group_id: str,
        *,
        definition_id: str = "indicator_follow",
        slot: int = 0,
    ) -> None:
        group_dir = root / "groups" / group_id
        group_dir.mkdir(parents=True, exist_ok=True)
        (group_dir / "group.json").write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "group_id": group_id,
                    "definition_id": definition_id,
                    "base_name": "그룹",
                    "display_name": "그룹" if slot == 0 else f"그룹_{slot}",
                    "slot": slot,
                    "created_at": "2026-09-16T09:00:00+09:00",
                },
                ensure_ascii=False,
                indent=2,
            ) + "\n",
            encoding="utf-8",
        )

    def _repository(self, root: Path) -> RoutineInstanceRepository:
        self._write_definition(root)
        return RoutineInstanceRepository(
            root,
            id_factory=lambda: INSTANCE_1,
            now_factory=lambda: datetime(2026, 9, 16, 10, 0, tzinfo=timezone.utc),
        )

    @staticmethod
    def _rules(state: dict) -> dict:
        return {
            "routine_name": "indicator_follow",
            "indicator_follow_ui_state": {"schema_version": "1.0", "state": state},
        }

    def test_group_memory_is_scoped_by_group_and_definition(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._write_group(root, GROUP_1)
            self._write_group(root, GROUP_2, slot=1)
            repository = LogicalGroupRepository(root)
            state = {"basic": {"value": "B"}}

            saved = repository.remember_registration_state(
                GROUP_1,
                "indicator_follow",
                state,
                source_instance_id=str(INSTANCE_1),
                registered_at="2026-09-16T10:00:00+09:00",
            )

            self.assertEqual(state, saved["indicator_follow_ui_state"])
            self.assertEqual(
                state,
                repository.remembered_registration_state(
                    GROUP_1, "indicator_follow"
                )["indicator_follow_ui_state"],
            )
            self.assertIsNone(
                repository.remembered_registration_state(
                    GROUP_2, "indicator_follow"
                )
            )
            self.assertIsNone(
                repository.remembered_registration_state(GROUP_1, "other")
            )

    @patch("routine_instance_repository._append_instance_lifecycle_event")
    def test_create_writes_atomic_immutable_registration_baseline(self, _event) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository = self._repository(root)
            template_path = root / "routines" / "indicator_follow" / "rules.json"
            template_before = template_path.read_bytes()
            initial_state = {"basic": {"value": "B"}, "buy_ui": {"value": 1}}
            result = repository.create_instance(
                RoutineInstanceCreateRequest(
                    definition_id="indicator_follow",
                    display_name="instance",
                ),
                self._rules(initial_state),
            )

            self.assertTrue(result.success, result.error)
            instance_dir = root / "routine_instances" / str(INSTANCE_1)
            baseline_path = instance_dir / REGISTRATION_BASELINE_FILE
            baseline_before = baseline_path.read_bytes()
            baseline = repository.load_registration_baseline(str(INSTANCE_1))
            self.assertEqual(initial_state, baseline["indicator_follow_ui_state"])

            rules_path = instance_dir / "rules.json"
            for current in ("C", "D", "V2_APPLY"):
                rules_path.write_text(
                    json.dumps(self._rules({"basic": {"value": current}})),
                    encoding="utf-8",
                )
                self.assertEqual(baseline_before, baseline_path.read_bytes())
                self.assertEqual(
                    initial_state,
                    repository.load_registration_baseline(str(INSTANCE_1))[
                        "indicator_follow_ui_state"
                    ],
                )
            self.assertEqual(template_before, template_path.read_bytes())

    @patch("routine_instance_repository._append_instance_lifecycle_event")
    def test_legacy_instance_without_baseline_is_not_backfilled(self, _event) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository = self._repository(root)
            result = repository.create_instance(
                RoutineInstanceCreateRequest(
                    definition_id="indicator_follow",
                    display_name="legacy",
                ),
                self._rules({"basic": {"value": "CURRENT"}}),
            )
            baseline_path = (
                root / "routine_instances" / result.instance.instance_id
                / REGISTRATION_BASELINE_FILE
            )
            baseline_path.unlink()
            current_before = Path(result.instance.rules_path).read_bytes()

            self.assertIsNone(
                repository.load_registration_baseline(result.instance.instance_id)
            )
            self.assertFalse(baseline_path.exists())
            self.assertEqual(current_before, Path(result.instance.rules_path).read_bytes())

    @patch("routine_instance_repository._append_instance_lifecycle_event")
    def test_successful_readback_updates_group_memory_from_saved_rules(self, _event) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository = self._repository(root)
            self._write_group(root, GROUP_1)
            state = {"basic": {"value": "B"}}
            rules = self._rules(state)
            result = repository.create_instance(
                RoutineInstanceCreateRequest(
                    definition_id="indicator_follow",
                    display_name="instance",
                    group_id=GROUP_1,
                ),
                rules,
            )

            payload = _remember_successful_registration_state(
                project_root=root,
                group_id=GROUP_1,
                definition_id="indicator_follow",
                instance=result.instance,
                expected_rules=rules,
            )

            self.assertEqual(state, payload["indicator_follow_ui_state"])
            self.assertEqual(
                state,
                LogicalGroupRepository(root).remembered_registration_state(
                    GROUP_1, "indicator_follow"
                )["indicator_follow_ui_state"],
            )

    @patch("routine_instance_repository._append_instance_lifecycle_event")
    def test_readback_mismatch_does_not_update_group_memory(self, _event) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository = self._repository(root)
            self._write_group(root, GROUP_1)
            result = repository.create_instance(
                RoutineInstanceCreateRequest(
                    definition_id="indicator_follow",
                    display_name="instance",
                    group_id=GROUP_1,
                ),
                self._rules({"basic": {"value": "SAVED"}}),
            )
            with self.assertRaisesRegex(ValueError, "read-back mismatch"):
                _remember_successful_registration_state(
                    project_root=root,
                    group_id=GROUP_1,
                    definition_id="indicator_follow",
                    instance=result.instance,
                    expected_rules=self._rules({"basic": {"value": "WORKING"}}),
                )
            self.assertIsNone(
                LogicalGroupRepository(root).remembered_registration_state(
                    GROUP_1, "indicator_follow"
                )
            )

    def test_registration_initial_authority_prefers_group_memory_and_keeps_explicit_sign(self) -> None:
        remembered_state = {
            "buy_ui": {
                "signal_filter": {"buy_bollinger_sign_combo": "+"}
            }
        }
        captured = {}
        dialog = SimpleNamespace(
            settings_mode="registration",
            group_id=GROUP_1,
            definition_id="indicator_follow",
        )

        def apply_state(state):
            captured["state"] = normalize_legacy_buy_bollinger_sign_ui_state(state)
            return {"sync_errors": []}

        dialog.apply_indicator_follow_ui_state = apply_state
        with patch(
            "gui_indicator_follow_routine_settings_dialog.LogicalGroupRepository"
        ) as repository_type:
            repository_type.return_value.remembered_registration_state.return_value = {
                "indicator_follow_ui_state": remembered_state
            }
            IndicatorFollowRoutineSettingsDialog._initialize_settings_state_authority(
                dialog
            )

        self.assertEqual(STATE_AUTHORITY_GROUP_REMEMBERED, dialog._registration_initial_state_source)
        self.assertEqual(
            "+",
            captured["state"]["buy_ui"]["signal_filter"][
                "buy_bollinger_sign_combo"
            ],
        )
        self.assertIsNone(dialog._initial_settings_undo_snapshot)
        self.assertEqual(STATE_AUTHORITY_CANONICAL_DEFAULT, dialog._settings_undo_source)

    def test_registration_without_group_memory_is_explicit_legacy_fallback(self) -> None:
        dialog = SimpleNamespace(
            settings_mode="registration",
            group_id=GROUP_1,
            definition_id="indicator_follow",
            apply_indicator_follow_ui_state=Mock(),
        )
        with patch(
            "gui_indicator_follow_routine_settings_dialog.LogicalGroupRepository"
        ) as repository_type:
            repository_type.return_value.remembered_registration_state.return_value = None
            IndicatorFollowRoutineSettingsDialog._initialize_settings_state_authority(dialog)
        self.assertEqual(
            STATE_AUTHORITY_LEGACY_TEMPLATE_FALLBACK,
            dialog._registration_initial_state_source,
        )
        dialog.apply_indicator_follow_ui_state.assert_not_called()

    def test_undo_providers_do_not_silently_fallback(self) -> None:
        registration = SimpleNamespace(
            _initial_settings_undo_snapshot=None,
            _settings_undo_source=STATE_AUTHORITY_CANONICAL_DEFAULT,
            _settings_undo_unavailable_reason="Canonical unavailable",
            _settings_ui_state_from_snapshot=lambda value: json.loads(value),
        )
        registration.settings_undo_target = lambda: (
            IndicatorFollowRoutineSettingsDialog.settings_undo_target(registration)
        )
        registration.apply_indicator_follow_ui_state = Mock()
        result = IndicatorFollowRoutineSettingsDialog.restore_settings_undo_snapshot(
            registration
        )
        self.assertFalse(result["available"])
        registration.apply_indicator_follow_ui_state.assert_not_called()

        baseline_state = {"basic": {"value": "B"}}
        edit = SimpleNamespace(
            _initial_settings_undo_snapshot=json.dumps(baseline_state),
            _settings_undo_source=STATE_AUTHORITY_INSTANCE_BASELINE,
            _settings_undo_unavailable_reason="",
            _settings_ui_state_from_snapshot=lambda value: json.loads(value),
            apply_indicator_follow_ui_state=Mock(return_value={"applied": ["basic"]}),
        )
        edit.settings_undo_target = lambda: (
            IndicatorFollowRoutineSettingsDialog.settings_undo_target(edit)
        )
        applied = IndicatorFollowRoutineSettingsDialog.restore_settings_undo_snapshot(edit)
        edit.apply_indicator_follow_ui_state.assert_called_once_with(baseline_state)
        self.assertTrue(applied["available"])
        self.assertEqual(STATE_AUTHORITY_INSTANCE_BASELINE, applied["source"])

    @patch("gui_indicator_follow_routine_settings_dialog.load_persisted_routine_instances", return_value=[])
    @patch("gui_indicator_follow_routine_settings_dialog.QMessageBox.warning")
    @patch("gui_indicator_follow_routine_settings_dialog.QMessageBox.critical")
    def test_cancel_validation_failure_and_create_failure_do_not_touch_group_memory(
        self,
        _critical,
        _warning,
        _instances,
    ) -> None:
        owner = SimpleNamespace()
        registration_request = RoutineInstanceCreateRequest(
            definition_id="indicator_follow",
            display_name="instance",
            group_id=GROUP_1,
        )
        fake_dialog = Mock()
        fake_dialog.registration_request = registration_request

        with patch("gui_routine_registration_dialog.RoutineRegistrationDialog", return_value=fake_dialog), patch(
            "gui_indicator_follow_routine_settings_dialog._remember_successful_registration_state"
        ) as remember:
            fake_dialog.exec_.return_value = QDialog.Rejected
            self.assertIsNone(
                register_routine_instance_snapshot(
                    owner,
                    definition_id="indicator_follow",
                    definition_display_name="지표추종매매",
                    group_id=GROUP_1,
                    rules_provider=Mock(),
                )
            )
            remember.assert_not_called()

            fake_dialog.exec_.return_value = QDialog.Accepted
            self.assertIsNone(
                register_routine_instance_snapshot(
                    owner,
                    definition_id="indicator_follow",
                    definition_display_name="지표추종매매",
                    group_id=GROUP_1,
                    rules_provider=lambda: {"success": False, "user_messages": []},
                )
            )
            remember.assert_not_called()

            with patch.object(
                RoutineInstanceRepository,
                "create_instance",
                return_value=RoutineInstanceCreateResult(
                    False,
                    error_code="INSTANCE_CREATE_FAILED",
                    error="failed",
                ),
            ):
                self.assertIsNone(
                    register_routine_instance_snapshot(
                        owner,
                        definition_id="indicator_follow",
                        definition_display_name="지표추종매매",
                        group_id=GROUP_1,
                        rules_provider=lambda: {
                            "success": True,
                            "rules": self._rules({"value": "B"}),
                        },
                    )
                )
            remember.assert_not_called()


if __name__ == "__main__":
    unittest.main()
