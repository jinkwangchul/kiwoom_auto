from __future__ import annotations

import builtins
import importlib.util
import os
import sys
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QObject, pyqtSignal

import gui_indicator_follow_routine_settings_dialog as dialog_module
from tests.qt_test_support import ensure_qapplication


class _RequestDialog(QObject):
    signal_validation_v4_requested = pyqtSignal(object)
    signal_validation_v5_requested = pyqtSignal(object)


def test_v5_modules_are_standalone_and_flow_uses_v5_window():
    from gui_indicator_follow_signal_validation_v4_window import (
        IndicatorFollowSignalValidationV4Window,
    )
    from gui_indicator_follow_signal_validation_v5_flow import (
        IndicatorFollowSignalValidationV5Flow,
    )
    from gui_indicator_follow_signal_validation_v5_window import (
        IndicatorFollowSignalValidationV5Window,
    )

    assert IndicatorFollowSignalValidationV5Window is not IndicatorFollowSignalValidationV4Window
    assert (
        IndicatorFollowSignalValidationV5Flow.__init__.__kwdefaults__["window_factory"]
        is IndicatorFollowSignalValidationV5Window
    )


def test_v5_and_v4_binders_keep_separate_owner_state():
    from gui_indicator_follow_signal_validation_v4_flow import (
        bind_indicator_follow_signal_validation_v4_flow,
    )
    from gui_indicator_follow_signal_validation_v5_flow import (
        bind_indicator_follow_signal_validation_v5_flow,
    )

    ensure_qapplication()
    owner = QObject()
    owner.kiwoom_api = None
    request_dialog = _RequestDialog()

    v4_flow = bind_indicator_follow_signal_validation_v4_flow(owner, request_dialog)
    v5_flow = bind_indicator_follow_signal_validation_v5_flow(owner, request_dialog)

    assert v4_flow is owner._indicator_follow_signal_validation_v4_flow
    assert v5_flow is owner._indicator_follow_signal_validation_v5_flow
    assert v5_flow is not v4_flow


def test_settings_dialog_exposes_v5_retires_v3_and_preserves_v4_seed_semantics():
    ensure_qapplication()
    project_root = Path(__file__).resolve().parents[1]
    routine_path = project_root / "routines" / "지표추종매매"

    with (
        patch.object(dialog_module.QTimer, "singleShot"),
        patch.object(dialog_module, "persistent_feature_owner", return_value=None),
    ):
        dialog = dialog_module.IndicatorFollowRoutineSettingsDialog(
            rules_path=routine_path / "rules.json",
            routine_path=routine_path,
            routine_name="지표추종매매",
            definition_id="indicator_follow",
            settings_mode="registration",
        )

    try:
        assert not hasattr(dialog, "signal_validation_v3_button")
        assert not hasattr(dialog, "signal_validation_v3_requested")
        assert "gui_indicator_follow_signal_validation_v3_window" not in sys.modules
        assert importlib.util.find_spec("gui_indicator_follow_signal_validation_v3_window") is None
        assert dialog.signal_validation_v4_button.text() == "검증차트4"
        assert dialog.signal_validation_v5_button.text() == "검증차트5"

        v4_payloads = []
        v5_payloads = []
        dialog.signal_validation_v4_requested.connect(v4_payloads.append)
        dialog.signal_validation_v5_requested.connect(v5_payloads.append)

        v4_seed = dialog._handle_signal_validation_v4_clicked()
        v5_seed = dialog._handle_signal_validation_v5_clicked()

        assert v4_seed is not None
        assert v5_seed is not None
        assert v4_payloads == [v4_seed]
        assert v5_payloads == [v5_seed]
        assert v5_seed.settings_snapshot.rules_hash == v4_seed.settings_snapshot.rules_hash
        assert v5_seed.to_ui_state() == v4_seed.to_ui_state()
    finally:
        dialog.close()


def test_missing_optional_v5_disables_only_v5_entry():
    ensure_qapplication()
    project_root = Path(__file__).resolve().parents[1]
    routine_path = project_root / "routines" / "지표추종매매"
    owner = QObject()
    owner.kiwoom_api = None
    real_import = builtins.__import__

    def import_without_v5(name, *args, **kwargs):
        if name == "gui_indicator_follow_signal_validation_v5_flow":
            raise ModuleNotFoundError(f"No module named {name!r}", name=name)
        return real_import(name, *args, **kwargs)

    with (
        patch.object(dialog_module.QTimer, "singleShot"),
        patch.object(dialog_module, "persistent_feature_owner", return_value=owner),
        patch.object(builtins, "__import__", side_effect=import_without_v5),
    ):
        dialog = dialog_module.IndicatorFollowRoutineSettingsDialog(
            rules_path=routine_path / "rules.json",
            routine_path=routine_path,
            routine_name="지표추종매매",
            parent=owner,
            definition_id="indicator_follow",
            settings_mode="registration",
        )

    try:
        assert dialog.signal_validation_v4_button.isEnabled()
        assert not dialog.signal_validation_v5_button.isEnabled()
        assert hasattr(owner, "_indicator_follow_signal_validation_v4_flow")
        assert not hasattr(owner, "_indicator_follow_signal_validation_v5_flow")
    finally:
        dialog.close()
