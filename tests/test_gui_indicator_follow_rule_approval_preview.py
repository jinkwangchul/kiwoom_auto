# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import hashlib
import json
import sys
import tempfile
import types
import unittest

from tests.test_gui_execution_preview_button import _install_pyqt5_import_stubs


_install_pyqt5_import_stubs()
sys.modules["PyQt5"].sip = types.ModuleType("PyQt5.sip")
sys.modules["PyQt5.sip"] = sys.modules["PyQt5"].sip

import gui_indicator_follow_routine_settings_dialog as dialog_module
from gui_indicator_follow_routine_settings_dialog import IndicatorFollowRoutineSettingsDialog


class _FakeLine:
    def __init__(self, value: str) -> None:
        self.value = value

    def text(self) -> str:
        return self.value


class _FakeText:
    def __init__(self) -> None:
        self.value = ""
        self.visible = False

    def setPlainText(self, value: str) -> None:
        self.value = value

    def toPlainText(self) -> str:
        return self.value

    def setVisible(self, value: bool) -> None:
        self.visible = value


class _FakeSignal:
    def __init__(self) -> None:
        self.callback = None

    def connect(self, callback) -> None:
        self.callback = callback

    def emit(self, index: int = 0) -> None:
        if self.callback is not None:
            self.callback(index)


class _FakeComboBox:
    def __init__(self) -> None:
        self.items = []
        self.current = ""
        self.tooltip = ""
        self.minimum_width = None
        self.currentIndexChanged = _FakeSignal()

    def addItems(self, items) -> None:
        self.items.extend(items)

    def setCurrentText(self, value: str) -> None:
        self.current = value

    def currentText(self) -> str:
        return self.current

    def setToolTip(self, value: str) -> None:
        self.tooltip = value

    def setMinimumWidth(self, value: int) -> None:
        self.minimum_width = value

    def change_to(self, value: str) -> None:
        self.current = value
        self.currentIndexChanged.emit(0)


class _FakeButton:
    def __init__(self, text: str = "") -> None:
        self.text = text
        self.tooltip = ""
        self.clicked = _FakeSignal()

    def setToolTip(self, value: str) -> None:
        self.tooltip = value

    def click(self) -> None:
        if self.clicked.callback is not None:
            self.clicked.callback()

    def setParent(self, parent) -> None:
        self.parent = parent


class _FakeLabel:
    def __init__(self, value: str = "") -> None:
        self.value = value
        self.tooltip = ""
        self.word_wrap = False
        self.minimum_width = None

    def setToolTip(self, value: str) -> None:
        self.tooltip = value

    def setWordWrap(self, value: bool) -> None:
        self.word_wrap = value

    def setMinimumWidth(self, value: int) -> None:
        self.minimum_width = value

    def setParent(self, parent) -> None:
        self.parent = parent


class _FakeGroupBox:
    def __init__(self, title: str = "") -> None:
        self.title = title
        self.visible = False

    def setVisible(self, value: bool) -> None:
        self.visible = value


class _FakeLayoutItem:
    def __init__(self, widget) -> None:
        self._widget = widget

    def widget(self):
        return self._widget


class _FakeGridLayout:
    def __init__(self) -> None:
        self.widgets = []
        self.column_stretches = {}
        self.column_minimum_widths = {}

    def addWidget(self, widget, row: int, column: int, *span) -> None:
        self.widgets.append((widget, row, column, span))

    def setColumnStretch(self, column: int, stretch: int) -> None:
        self.column_stretches[column] = stretch

    def setColumnMinimumWidth(self, column: int, width: int) -> None:
        self.column_minimum_widths[column] = width

    def count(self) -> int:
        return len(self.widgets)

    def takeAt(self, index: int):
        widget, _row, _column, _span = self.widgets.pop(index)
        return _FakeLayoutItem(widget)


class GuiIndicatorFollowRuleApprovalPreviewTest(unittest.TestCase):
    def setUp(self) -> None:
        self.current_rules = {
            "buy": {
                "groups": [
                    {
                        "conditions": [
                            {
                                "target": "OSC",
                                "operator": "TURN_UP",
                            }
                        ]
                    }
                ]
            },
            "sell": {
                "signals": {
                    "macd_sell": {
                        "enabled": True,
                    }
                }
            },
        }
        self.preview_result = {
            "preview_rules": {
                "indicator_follow_rule_preview": {
                    "mode": "merge_add_candidate",
                    "candidates": {
                        "buy": {
                            "merge_into": "buy.groups[0].conditions",
                            "skip_existing": [
                                {
                                    "target": "OSC",
                                    "operator": "TURN_UP",
                                }
                            ],
                            "add_conditions": [
                                {
                                    "target": "OSC",
                                    "operator": "<=",
                                    "value": -91.0,
                                }
                            ],
                        },
                        "sell": {
                            "add_signal_candidate": {
                                "path": "sell.signals.ui_preview_condition_c",
                                "enabled": False,
                                "preview_candidate": True,
                                "groups_logic": "OR",
                                "groups": [
                                    {
                                        "enabled": True,
                                        "conditions_logic": "AND",
                                        "conditions": [
                                            {
                                                "target": "MACD",
                                                "operator": "<=",
                                                "value": -1.0,
                                            }
                                        ],
                                    }
                                ],
                            }
                        },
                    },
                }
            },
            "mapped_paths": [
                "buy.groups[0].conditions",
                "sell.signals.ui_preview_condition_c",
            ],
            "warnings": [],
        }
        self.dialog = IndicatorFollowRoutineSettingsDialog.__new__(IndicatorFollowRoutineSettingsDialog)
        self.dialog.rules = self.current_rules
        self.dialog.rules_data = self.current_rules
        self.dialog.preview_text = _FakeText()
        self.dialog._rule_approval_session = {}
        self.dialog._rule_approval_decision_widgets = {}
        self.dialog._last_rule_engine_preview = {}
        self.dialog._last_rule_pipeline_preview = {}
        self.dialog._last_rule_validation_context = {}
        self.dialog._rule_approval_session_dirty = False
        self.dialog._last_saved_rule_approval_session_decisions = None
        self.dialog._last_rule_approval_session_save_result = {}
        self.dialog._rule_approval_save_button = None
        self.dialog._rule_approval_controls_box = _FakeGroupBox("Rule Candidate Approval Preview Controls")
        self.dialog._rule_approval_controls_layout = _FakeGridLayout()
        self._temp_dir = tempfile.TemporaryDirectory()
        self.dialog._approval_session_path = (
            Path(self._temp_dir.name) / "runtime" / "routines" / "indicator_follow" / "approval_session.json"
        )

        self._original_combo = dialog_module.QComboBox
        self._original_label = dialog_module.QLabel
        self._original_button = dialog_module.QPushButton
        dialog_module.QComboBox = _FakeComboBox
        dialog_module.QLabel = _FakeLabel
        dialog_module.QPushButton = _FakeButton

    def tearDown(self) -> None:
        dialog_module.QComboBox = self._original_combo
        dialog_module.QLabel = self._original_label
        dialog_module.QPushButton = self._original_button
        self._temp_dir.cleanup()

    def _rules_json_hash(self) -> str:
        project_root = Path(__file__).resolve().parents[1]
        rules_path = next((project_root / "routines").glob("*/rules.json"))
        return hashlib.sha256(rules_path.read_bytes()).hexdigest().upper()

    def _mapper_hash(self) -> str:
        project_root = Path(__file__).resolve().parents[1]
        mapper_path = project_root / "routines" / "지표추종매매" / "routine_rule_mapper.py"
        return hashlib.sha256(mapper_path.read_bytes()).hexdigest().upper()

    def _build(
        self,
        decisions: dict[str, str] | None = None,
        saved_session: dict[str, object] | None = None,
    ) -> dict[str, object]:
        return IndicatorFollowRoutineSettingsDialog.build_rule_candidate_approval_and_patch_preview(
            self.dialog,
            self.preview_result,
            decisions,
            saved_session,
        )

    def _prepare_validation_context(self) -> dict[str, object]:
        result = self._build({})
        self.dialog._last_rule_validation_context = {
            "summary_lines": ["signal: signal", "execution: execution", "sell: sell", "buy: buy"],
            "state": {"basic": {}},
            "rules_preview_view": {},
            "engine_rules_preview_view": {},
            "engine_rules_pending_view": {},
            "saved_engine_rules_pending_view": {},
            "engine_rules_approval_simulation": {},
            "engine_rules_diff_view": {},
        }
        self.dialog._refresh_rule_approval_controls(result["session"])
        return result

    def test_default_decisions_keep_all_candidates_pending_and_no_patches(self) -> None:
        result = self._build({})
        session = result["session"]
        approval = result["approval_result"]
        patch = result["patch_preview"]

        self.assertEqual(session["mode"], "approval_session")
        self.assertEqual(session["decisions"]["buy.groups[0].conditions"], "PENDING")
        self.assertEqual(session["decisions"]["sell.signals.ui_preview_condition_c"], "PENDING")
        self.assertEqual(
            approval["candidate_decisions"]["buy.groups[0].conditions"]["decision"],
            "PENDING",
        )
        self.assertEqual(
            approval["candidate_decisions"]["sell.signals.ui_preview_condition_c"]["decision"],
            "PENDING",
        )
        self.assertEqual(patch["patches"], [])
        self.assertEqual(result["apply_preview"]["applied_patches"], [])
        self.assertEqual(result["apply_preview"]["summary"]["applied"], 0)
        self.assertFalse(result["commit_preview"]["commit_allowed"])
        self.assertIn(
            "approval session has no approved patches",
            result["commit_preview"]["blocked_reasons"],
        )
        self.assertNotIn("applied_rules_preview", result["apply_preview"])

    def test_build_preview_adds_session_fingerprint_and_valid_validation(self) -> None:
        result = self._build({})

        self.assertIn("fingerprint", self.dialog._rule_approval_session)
        self.assertIn("fingerprint_detail", self.dialog._rule_approval_session)
        self.assertEqual(result["approval_session_validation"]["status"], "VALID")
        self.assertTrue(result["approval_session_validation"]["path_match"])
        self.assertTrue(result["approval_session_validation"]["type_match"])
        self.assertTrue(result["approval_session_validation"]["fingerprint_match"])
        self.assertEqual(result["approval_session_validation"]["restore_status"], "NEW")
        self.assertEqual(result["approval_session_file"]["status"], "NOT_FOUND")
        self.assertEqual(result["approval_session_file"]["restore_status"], "NEW")
        self.assertFalse(result["approval_session_file"]["dirty"])
        self.assertNotIn("fingerprint_detail", result["session"])

    def test_buy_approval_creates_merge_conditions_patch(self) -> None:
        result = self._build({"buy.groups[0].conditions": "APPROVED"})
        session = result["session"]
        approval = result["approval_result"]
        patch = result["patch_preview"]

        self.assertEqual(session["decisions"]["buy.groups[0].conditions"], "APPROVED")
        self.assertEqual(approval["approved_paths"], ["buy.groups[0].conditions"])
        self.assertEqual(
            approval["candidate_decisions"]["sell.signals.ui_preview_condition_c"]["decision"],
            "PENDING",
        )
        self.assertEqual(len(patch["patches"]), 1)
        self.assertEqual(patch["patches"][0]["operation"], "merge_conditions")
        self.assertEqual(len(result["apply_preview"]["applied_patches"]), 1)
        self.assertEqual(result["apply_preview"]["applied_patches"][0]["operation"], "merge_conditions")
        self.assertEqual(result["apply_preview"]["applied_patches"][0]["added_count"], 1)
        self.assertTrue(result["commit_preview"]["commit_allowed"])
        self.assertEqual(result["commit_preview"]["final_diff"][0]["operation"], "merge_conditions")

    def test_sell_approval_creates_add_signal_patch(self) -> None:
        result = self._build({"sell.signals.ui_preview_condition_c": "APPROVED"})
        session = result["session"]
        approval = result["approval_result"]
        patch = result["patch_preview"]

        self.assertEqual(session["decisions"]["sell.signals.ui_preview_condition_c"], "APPROVED")
        self.assertEqual(approval["approved_paths"], ["sell.signals.ui_preview_condition_c"])
        self.assertEqual(
            approval["candidate_decisions"]["buy.groups[0].conditions"]["decision"],
            "PENDING",
        )
        self.assertEqual(len(patch["patches"]), 1)
        self.assertEqual(patch["patches"][0]["operation"], "add_signal")
        self.assertEqual(len(result["apply_preview"]["applied_patches"]), 1)
        self.assertEqual(result["apply_preview"]["applied_patches"][0]["operation"], "add_signal")
        self.assertEqual(
            result["apply_preview"]["applied_patches"][0]["target_path"],
            "sell.signals.ui_condition_c",
        )
        self.assertTrue(result["commit_preview"]["commit_allowed"])
        self.assertEqual(result["commit_preview"]["final_diff"][0]["operation"], "add_signal")

    def test_buy_and_sell_approval_create_two_patches(self) -> None:
        result = self._build(
            {
                "buy.groups[0].conditions": "APPROVED",
                "sell.signals.ui_preview_condition_c": "APPROVED",
            }
        )
        operations = [patch["operation"] for patch in result["patch_preview"]["patches"]]

        self.assertEqual(len(operations), 2)
        self.assertIn("merge_conditions", operations)
        self.assertIn("add_signal", operations)
        self.assertEqual(len(result["apply_preview"]["applied_patches"]), 2)
        self.assertTrue(result["commit_preview"]["commit_allowed"])
        self.assertEqual(len(result["commit_preview"]["final_diff"]), 2)

    def test_reject_and_defer_do_not_create_patches(self) -> None:
        result = self._build(
            {
                "buy.groups[0].conditions": "REJECTED",
                "sell.signals.ui_preview_condition_c": "DEFERRED",
            }
        )

        self.assertEqual(result["approval_result"]["rejected_paths"], ["buy.groups[0].conditions"])
        self.assertEqual(
            result["approval_result"]["deferred_paths"],
            ["sell.signals.ui_preview_condition_c"],
        )
        self.assertEqual(result["patch_preview"]["patches"], [])
        self.assertEqual(result["apply_preview"]["applied_patches"], [])

    def test_applied_preview_only_records_decision_without_patch(self) -> None:
        result = self._build({"buy.groups[0].conditions": "APPLIED_PREVIEW_ONLY"})

        self.assertEqual(
            result["approval_result"]["candidate_decisions"]["buy.groups[0].conditions"]["decision"],
            "APPLIED_PREVIEW_ONLY",
        )
        self.assertEqual(result["patch_preview"]["patches"], [])
        self.assertEqual(result["apply_preview"]["applied_patches"], [])

    def test_validate_preview_default_ui_output_remains_pending(self) -> None:
        self.dialog.validation_signal_line = _FakeLine("signal")
        self.dialog.validation_execution_line = _FakeLine("execution")
        self.dialog.validation_sell_line = _FakeLine("sell")
        self.dialog.validation_buy_line = _FakeLine("buy")
        self.dialog.preview_text = _FakeText()
        self.dialog.collect_indicator_follow_ui_state = lambda: {"basic": {}}
        self.dialog.build_rules_with_indicator_follow_ui_state = lambda: {"indicator_follow_ui_state": {}}
        self.dialog.build_engine_rules_preview_from_current_ui_state = lambda: self.preview_result
        self.dialog.build_engine_rules_pending_from_current_ui_state = lambda: {"pending_rules": {}, "warnings": []}
        self.dialog.build_engine_rules_approval_simulation_from_current_ui_state = lambda preview: {}
        self.dialog.build_engine_rules_diff_from_preview = lambda preview: {"summary": {}, "changes": [], "warnings": []}
        self.dialog._build_saved_rule_mapper_pending_view = lambda pending: {}

        IndicatorFollowRoutineSettingsDialog._handle_validate_clicked(self.dialog)
        text = self.dialog.preview_text.toPlainText()

        self.assertTrue(self.dialog._rule_approval_controls_box.visible)
        self.assertEqual(len(self.dialog._rule_approval_decision_widgets), 2)
        self.assertIn("[Rule Candidate Approval]", text)
        self.assertIn("[Rule Approval Session]", text)
        self.assertIn("[Approval Session File]", text)
        self.assertIn("[Approval Session Validation]", text)
        self.assertIn("[Approved Rule Patch Preview]", text)
        self.assertIn("[Approved Rule Apply Preview]", text)
        self.assertIn("[Rule Commit Preview]", text)
        self.assertIn('"status": "NOT_FOUND"', text)
        self.assertIn('"status": "VALID"', text)
        self.assertIn('"restore_status": "NEW"', text)
        self.assertIn('"session_status": "ACTIVE"', text)
        self.assertIn('"decision": "PENDING"', text)
        self.assertIn('"patches": []', text)
        self.assertIn('"applied_patches": []', text)
        self.assertIn('"applied": 0', text)
        self.assertIn('"commit_allowed": false', text)
        self.assertIn("approval session has no approved patches", text)

    def _write_saved_session_file(self, session: dict[str, object]) -> None:
        self.dialog._approval_session_path.parent.mkdir(parents=True, exist_ok=True)
        payload = deepcopy(session)
        payload.pop("fingerprint_detail", None)
        payload.pop("validation", None)
        self.dialog._approval_session_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def test_validate_auto_load_restore_success_from_session_file(self) -> None:
        self._build({"buy.groups[0].conditions": "APPROVED"})
        self._write_saved_session_file(self.dialog._rule_approval_session)

        result = self._build({})
        self.dialog._refresh_rule_approval_controls(result["session"])

        self.assertEqual(result["approval_session_file"]["status"], "LOADED")
        self.assertEqual(result["approval_session_file"]["restore_status"], "RESTORED")
        self.assertFalse(result["approval_session_file"]["dirty"])
        self.assertEqual(result["session"]["decisions"]["buy.groups[0].conditions"], "APPROVED")
        self.assertEqual(
            self.dialog._rule_approval_decision_widgets["buy.groups[0].conditions"].currentText(),
            "APPROVED",
        )

    def test_dirty_state_tracks_changes_after_restored_session(self) -> None:
        self._build({"buy.groups[0].conditions": "APPROVED"})
        self._write_saved_session_file(self.dialog._rule_approval_session)
        result = self._build({})
        self.dialog._last_rule_validation_context = {
            "summary_lines": [],
            "state": {},
            "rules_preview_view": {},
            "engine_rules_preview_view": {},
            "engine_rules_pending_view": {},
            "saved_engine_rules_pending_view": {},
            "engine_rules_approval_simulation": {},
            "engine_rules_diff_view": {},
        }
        self.dialog._refresh_rule_approval_controls(result["session"])

        self.dialog._rule_approval_decision_widgets["buy.groups[0].conditions"].change_to("PENDING")
        dirty_text = self.dialog.preview_text.toPlainText()

        self.assertTrue(self.dialog._rule_approval_session_dirty)
        self.assertIn('"dirty": true', dirty_text)
        self.assertIn('"commit_allowed": false', dirty_text)
        self.assertIn("decision changed after last session restore/save", dirty_text)
        self.assertIn(
            "approval session has unsaved decision changes; save approval session before commit preview",
            dirty_text,
        )

        self.dialog._rule_approval_decision_widgets["buy.groups[0].conditions"].change_to("APPROVED")
        clean_text = self.dialog.preview_text.toPlainText()

        self.assertFalse(self.dialog._rule_approval_session_dirty)
        self.assertIn('"dirty": false', clean_text)

    def test_validate_auto_load_stale_session_resets_to_pending(self) -> None:
        self._build({"buy.groups[0].conditions": "APPROVED"})
        stale = deepcopy(self.dialog._rule_approval_session)
        stale["fingerprint"] = "stale"
        self._write_saved_session_file(stale)

        result = self._build({})
        self.dialog._last_rule_validation_context = {
            "summary_lines": [],
            "state": {},
            "rules_preview_view": {},
            "engine_rules_preview_view": {},
            "engine_rules_pending_view": {},
            "saved_engine_rules_pending_view": {},
            "engine_rules_approval_simulation": {},
            "engine_rules_diff_view": {},
        }
        self.dialog._refresh_rule_approval_controls(result["session"])
        self.dialog._render_rule_validation_preview(result)
        text = self.dialog.preview_text.toPlainText()

        self.assertEqual(result["approval_session_file"]["status"], "LOADED")
        self.assertEqual(result["approval_session_file"]["restore_status"], "RESET_TO_PENDING")
        self.assertFalse(result["approval_session_file"]["dirty"])
        self.assertEqual(set(result["session"]["decisions"].values()), {"PENDING"})
        self.assertEqual(
            self.dialog._rule_approval_decision_widgets["buy.groups[0].conditions"].currentText(),
            "PENDING",
        )
        self.assertIn("approval session fingerprint mismatch; decisions reset to PENDING", text)

    def test_validate_auto_load_corrupted_session_falls_back_to_pending(self) -> None:
        self.dialog._approval_session_path.parent.mkdir(parents=True, exist_ok=True)
        self.dialog._approval_session_path.write_text("{bad", encoding="utf-8")

        result = self._build({})
        self.dialog._last_rule_validation_context = {
            "summary_lines": [],
            "state": {},
            "rules_preview_view": {},
            "engine_rules_preview_view": {},
            "engine_rules_pending_view": {},
            "saved_engine_rules_pending_view": {},
            "engine_rules_approval_simulation": {},
            "engine_rules_diff_view": {},
        }
        self.dialog._refresh_rule_approval_controls(result["session"])
        self.dialog._render_rule_validation_preview(result)
        text = self.dialog.preview_text.toPlainText()

        self.assertEqual(result["approval_session_file"]["status"], "CORRUPTED")
        self.assertEqual(result["approval_session_file"]["restore_status"], "NEW")
        self.assertEqual(set(result["session"]["decisions"].values()), {"PENDING"})
        self.assertIn("failed to read approval session JSON", text)

    def test_validate_auto_load_does_not_create_session_file_or_call_save(self) -> None:
        original_save = dialog_module.rule_approval_session_file_service.save_rule_approval_session
        calls = {"save": 0}

        def fake_save(*args, **kwargs):
            calls["save"] += 1
            raise AssertionError("save_rule_approval_session must not be called by GUI preview")

        dialog_module.rule_approval_session_file_service.save_rule_approval_session = fake_save
        try:
            self._prepare_validation_context()
            self.dialog._rule_approval_decision_widgets["buy.groups[0].conditions"].change_to("APPROVED")
        finally:
            dialog_module.rule_approval_session_file_service.save_rule_approval_session = original_save

        self.assertEqual(calls["save"], 0)
        self.assertFalse(self.dialog._approval_session_path.exists())

    def test_approval_session_save_button_saves_valid_session_and_resets_dirty(self) -> None:
        rules_before = self._rules_json_hash()
        self._prepare_validation_context()
        self.dialog._rule_approval_decision_widgets["buy.groups[0].conditions"].change_to("APPROVED")

        self.dialog._rule_approval_save_button.click()
        text = self.dialog.preview_text.toPlainText()

        self.assertTrue(self.dialog._approval_session_path.exists())
        self.assertFalse(self.dialog._rule_approval_session_dirty)
        self.assertIn('"status": "SAVED"', text)
        self.assertIn('"saved": true', text)
        self.assertIn('"dirty": false', text)
        self.assertIn('"saved_at"', text)
        self.assertEqual(rules_before, self._rules_json_hash())

        restored = self._build({})
        self.assertEqual(restored["approval_session_file"]["status"], "LOADED")
        self.assertEqual(restored["approval_session_file"]["restore_status"], "RESTORED")
        self.assertEqual(restored["session"]["decisions"]["buy.groups[0].conditions"], "APPROVED")

    def test_approval_session_save_dirty_true_after_change_and_false_after_resave(self) -> None:
        self._prepare_validation_context()
        self.dialog._rule_approval_decision_widgets["buy.groups[0].conditions"].change_to("APPROVED")
        self.dialog._rule_approval_save_button.click()

        self.dialog._rule_approval_decision_widgets["buy.groups[0].conditions"].change_to("REJECTED")
        dirty_text = self.dialog.preview_text.toPlainText()

        self.assertTrue(self.dialog._rule_approval_session_dirty)
        self.assertIn('"dirty": true', dirty_text)

        self.dialog._rule_approval_save_button.click()
        clean_text = self.dialog.preview_text.toPlainText()

        self.assertFalse(self.dialog._rule_approval_session_dirty)
        self.assertIn('"dirty": false', clean_text)

    def test_approval_session_save_blocks_fingerprint_mismatch(self) -> None:
        self._prepare_validation_context()
        self.dialog._rule_approval_session["fingerprint"] = "stale"

        self.dialog._rule_approval_save_button.click()
        text = self.dialog.preview_text.toPlainText()

        self.assertFalse(self.dialog._approval_session_path.exists())
        self.assertIn('"saved": false', text)
        self.assertIn("approval session validation must be VALID", text)

    def test_approval_session_save_uses_file_service_once(self) -> None:
        original_save = dialog_module.rule_approval_session_file_service.save_rule_approval_session
        calls = {"save": 0}

        def wrapped_save(*args, **kwargs):
            calls["save"] += 1
            return original_save(*args, **kwargs)

        dialog_module.rule_approval_session_file_service.save_rule_approval_session = wrapped_save
        try:
            self._prepare_validation_context()
            self.dialog._rule_approval_save_button.click()
        finally:
            dialog_module.rule_approval_session_file_service.save_rule_approval_session = original_save

        self.assertEqual(calls["save"], 1)
        self.assertTrue(self.dialog._approval_session_path.exists())

    def test_approval_controls_are_created_with_pending_combos(self) -> None:
        self._prepare_validation_context()

        widgets = self.dialog._rule_approval_decision_widgets

        self.assertTrue(self.dialog._rule_approval_controls_box.visible)
        self.assertEqual(self.dialog._rule_approval_controls_box.title, "Rule Candidate Approval Preview Controls")
        self.assertEqual(set(widgets.keys()), set(self.preview_result["mapped_paths"]))
        self.assertEqual(widgets["buy.groups[0].conditions"].currentText(), "PENDING")
        self.assertEqual(widgets["sell.signals.ui_preview_condition_c"].currentText(), "PENDING")
        self.assertEqual(
            widgets["buy.groups[0].conditions"].items,
            ["PENDING", "APPROVED", "REJECTED", "DEFERRED", "APPLIED_PREVIEW_ONLY"],
        )
        self.assertEqual(self.dialog._rule_approval_save_button.text, "승인 검토 상태 저장")
        self.assertEqual(
            self.dialog._rule_approval_save_button.tooltip,
            "현재 승인 검토 상태(decision)만 저장합니다.\nrules.json은 변경되지 않습니다.",
        )
        button_texts = [
            widget.text
            for widget, _row, _column, _span in self.dialog._rule_approval_controls_layout.widgets
            if isinstance(widget, _FakeButton)
        ]
        self.assertEqual(button_texts, ["승인 검토 상태 저장"])
        self.assertFalse(any("Commit" in text or "커밋" in text for text in button_texts))

    def test_approval_controls_show_preview_only_notice(self) -> None:
        self._prepare_validation_context()

        labels = [
            widget.value
            for widget, _row, _column, _span in self.dialog._rule_approval_controls_layout.widgets
            if isinstance(widget, _FakeLabel)
        ]

        self.assertIn(
            "미리보기 전용: 선택한 decision은 저장/적용되지 않으며 rules.json을 변경하지 않습니다.",
            labels,
        )

    def test_approval_controls_show_short_path_labels_with_full_path_tooltips(self) -> None:
        self._prepare_validation_context()

        label_by_value = {
            widget.value: widget
            for widget, _row, _column, _span in self.dialog._rule_approval_controls_layout.widgets
            if isinstance(widget, _FakeLabel)
        }

        self.assertIn("buy merge conditions", label_by_value)
        self.assertEqual(
            label_by_value["buy merge conditions"].tooltip,
            "buy.groups[0].conditions",
        )
        self.assertIn("sell.signals.ui_preview_condition_c", label_by_value)
        self.assertEqual(
            label_by_value["sell.signals.ui_preview_condition_c"].tooltip,
            "sell.signals.ui_preview_condition_c",
        )

    def test_approval_controls_keep_decision_columns_readable(self) -> None:
        self._prepare_validation_context()

        layout = self.dialog._rule_approval_controls_layout

        self.assertEqual(layout.column_stretches.get(0), 1)
        self.assertGreaterEqual(layout.column_minimum_widths.get(1, 0), 130)
        self.assertGreaterEqual(layout.column_minimum_widths.get(2, 0), 70)
        self.assertGreaterEqual(layout.column_minimum_widths.get(3, 0), 180)
        self.assertEqual(
            self.dialog._rule_approval_decision_widgets["buy.groups[0].conditions"].minimum_width,
            180,
        )

    def test_buy_combo_approved_refreshes_patch_and_apply_preview(self) -> None:
        self._prepare_validation_context()

        self.dialog._rule_approval_decision_widgets["buy.groups[0].conditions"].change_to("APPROVED")
        text = self.dialog.preview_text.toPlainText()

        self.assertEqual(
            self.dialog._rule_approval_session["decisions"]["buy.groups[0].conditions"],
            "APPROVED",
        )
        self.assertEqual(self.dialog._rule_approval_session_validation["valid"], True)
        self.assertIn('"status": "VALID"', text)
        self.assertIn('"operation": "merge_conditions"', text)
        self.assertIn('"added_count": 1', text)
        self.assertIn('"commit_allowed": true', text)

    def test_sell_combo_approved_refreshes_patch_and_apply_preview(self) -> None:
        self._prepare_validation_context()

        self.dialog._rule_approval_decision_widgets[
            "sell.signals.ui_preview_condition_c"
        ].change_to("APPROVED")
        text = self.dialog.preview_text.toPlainText()

        self.assertEqual(
            self.dialog._rule_approval_session["decisions"]["sell.signals.ui_preview_condition_c"],
            "APPROVED",
        )
        self.assertIn('"operation": "add_signal"', text)
        self.assertIn('"target_path": "sell.signals.ui_condition_c"', text)
        self.assertIn('"commit_allowed": true', text)

    def test_non_approved_combo_values_do_not_create_patch_or_apply_preview(self) -> None:
        for decision in ["REJECTED", "DEFERRED", "APPLIED_PREVIEW_ONLY"]:
            with self.subTest(decision=decision):
                self._prepare_validation_context()

                self.dialog._rule_approval_decision_widgets["buy.groups[0].conditions"].change_to(decision)
                text = self.dialog.preview_text.toPlainText()

                self.assertEqual(
                    self.dialog._rule_approval_session["decisions"]["buy.groups[0].conditions"],
                    decision,
                )
        self.assertIn('"patches": []', text)
        self.assertIn('"applied_patches": []', text)

    def test_saved_session_restore_restores_matching_decisions(self) -> None:
        self._build({"buy.groups[0].conditions": "APPROVED"})
        saved_session = deepcopy(self.dialog._rule_approval_session)

        result = self._build(saved_session=saved_session)

        self.assertEqual(result["approval_session_validation"]["status"], "VALID")
        self.assertEqual(result["approval_session_validation"]["restore_status"], "RESTORED")
        self.assertEqual(result["session"]["decisions"]["buy.groups[0].conditions"], "APPROVED")
        self.assertEqual(
            self.dialog._rule_approval_session["decisions"]["buy.groups[0].conditions"],
            "APPROVED",
        )

    def test_stale_saved_session_restore_resets_to_pending_and_warns(self) -> None:
        self._build({"buy.groups[0].conditions": "APPROVED"})
        saved_session = deepcopy(self.dialog._rule_approval_session)
        saved_session["fingerprint"] = "stale"

        result = self._build(saved_session=saved_session)
        self.dialog._last_rule_validation_context = {
            "summary_lines": [],
            "state": {},
            "rules_preview_view": {},
            "engine_rules_preview_view": {},
            "engine_rules_pending_view": {},
            "saved_engine_rules_pending_view": {},
            "engine_rules_approval_simulation": {},
            "engine_rules_diff_view": {},
        }
        self.dialog._refresh_rule_approval_controls(result["session"])
        self.dialog._render_rule_validation_preview(result)
        text = self.dialog.preview_text.toPlainText()

        self.assertEqual(result["approval_session_validation"]["status"], "VALID")
        self.assertEqual(result["approval_session_validation"]["restore_status"], "RESET_TO_PENDING")
        self.assertEqual(set(result["session"]["decisions"].values()), {"PENDING"})
        self.assertEqual(
            self.dialog._rule_approval_decision_widgets["buy.groups[0].conditions"].currentText(),
            "PENDING",
        )
        self.assertIn(
            "approval session fingerprint mismatch; decisions reset to PENDING",
            text,
        )

    def test_rules_json_is_not_written(self) -> None:
        before = self._rules_json_hash()

        self._build(
            {
                "buy.groups[0].conditions": "APPROVED",
                "sell.signals.ui_preview_condition_c": "APPROVED",
            }
        )

        self.assertEqual(before, self._rules_json_hash())

    def test_combo_changes_do_not_write_rules_or_mapper(self) -> None:
        rules_before = self._rules_json_hash()
        mapper_before = self._mapper_hash()
        self._prepare_validation_context()

        self.dialog._rule_approval_decision_widgets["buy.groups[0].conditions"].change_to("APPROVED")
        self.dialog._rule_approval_decision_widgets[
            "sell.signals.ui_preview_condition_c"
        ].change_to("APPROVED")

        self.assertEqual(rules_before, self._rules_json_hash())
        self.assertEqual(mapper_before, self._mapper_hash())


if __name__ == "__main__":
    unittest.main()

