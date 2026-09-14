# -*- coding: utf-8 -*-
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest


class IndicatorFollowValidationIndependenceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.project_root = Path(__file__).resolve().parents[1]

    def test_legacy_v1_entrypoints_are_removed_and_v2_owns_its_canvas(self) -> None:
        for relative in (
            "gui_indicator_follow_validation_flow.py",
            "gui_indicator_follow_validation_chart_window.py",
        ):
            self.assertFalse((self.project_root / relative).exists(), relative)
        source = (
            self.project_root / "gui_indicator_follow_signal_validation_window.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("gui_indicator_follow_validation_chart_window", source)
        self.assertIn("_IndicatorFollowSignalValidationChartCanvasBase", source)

    def test_main_callers_have_no_indicator_validation_binder_dependency(self) -> None:
        for relative in ("gui_auto_trade_setting_window.py", "gui_windows.py"):
            source = (self.project_root / relative).read_text(encoding="utf-8")
            self.assertNotIn("bind_indicator_follow_validation_flow", source)
            self.assertNotIn("bind_indicator_follow_signal_validation_flow", source)

    def test_general_settings_load_validate_and_save_when_v2_is_unavailable(self) -> None:
        source_rules = (
            self.project_root / "routines" / "지표추종매매" / "rules.json"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            rules_path = Path(temp_dir) / "rules.json"
            shutil.copy2(source_rules, rules_path)
            script = textwrap.dedent(
                f"""
                import importlib.abc
                import os
                import sys
                os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

                blocked = {{
                    "gui_indicator_follow_signal_validation_flow",
                    "gui_indicator_follow_signal_validation_window",
                    "indicator_follow_signal_validation_projection",
                    "indicator_follow_signal_validation_presentation",
                    "indicator_follow_signal_validation_recent_stocks",
                }}

                class V2Blocker(importlib.abc.MetaPathFinder):
                    def find_spec(self, fullname, path=None, target=None):
                        if fullname in blocked:
                            raise ModuleNotFoundError(
                                f"blocked optional V2 module: {{fullname}}",
                                name=fullname,
                            )
                        return None

                sys.meta_path.insert(0, V2Blocker())

                import gui_auto_trade_setting_window
                import gui_windows
                from PyQt5.QtWidgets import QApplication, QWidget
                from gui_indicator_follow_routine_settings_dialog import (
                    IndicatorFollowRoutineSettingsDialog,
                )

                app = QApplication.instance() or QApplication([])
                owner = QWidget()
                owner.kiwoom_api = None
                dialog = IndicatorFollowRoutineSettingsDialog(
                    rules_path={str(rules_path)!r},
                    routine_path={str(source_rules.parent)!r},
                    routine_name="지표추종매매",
                    parent=owner,
                    definition_id="indicator_follow",
                    settings_mode="registration",
                )
                assert dialog.signal_validation_button.isEnabled() is False
                assert isinstance(dialog.collect_indicator_follow_ui_state(), dict)
                preview = dialog.build_engine_rules_preview_from_current_ui_state()
                assert isinstance(preview, dict)
                saved = dialog.save_indicator_follow_ui_state_to_rules()
                assert saved.get("success") is True, saved
                dialog.close()
                owner.close()
                """
            )
            environment = dict(os.environ)
            environment["QT_QPA_PLATFORM"] = "offscreen"
            result = subprocess.run(
                [sys.executable, "-c", script],
                cwd=self.project_root,
                env=environment,
                capture_output=True,
                text=True,
                timeout=60,
            )
        self.assertEqual(
            0,
            result.returncode,
            msg=f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )


if __name__ == "__main__":
    unittest.main()
