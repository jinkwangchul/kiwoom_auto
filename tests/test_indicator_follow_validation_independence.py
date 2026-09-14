# -*- coding: utf-8 -*-
from __future__ import annotations

import json
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

    def test_main_and_other_routine_work_without_indicator_follow_package(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            package = root / "routines" / "independent_test"
            package.mkdir(parents=True)
            (package / "routine.json").write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "definition_id": "independent_test",
                        "name": "Independent Test",
                        "locators": {
                            "evaluation": {
                                "file": "entry.py",
                                "callable": "evaluate",
                            },
                            "execution_admission": {
                                "file": "entry.py",
                                "callable": "admit",
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )
            (package / "entry.py").write_text(
                "def evaluate(context):\n"
                "    return {'signal': 'BUY', 'source': 'independent_test'}\n\n"
                "def admit(subject, rules, routine_identity, rules_identity):\n"
                "    return {\n"
                "        'allowed': True,\n"
                "        'routine_identity': routine_identity,\n"
                "        'rules_identity': rules_identity,\n"
                "    }\n",
                encoding="utf-8",
            )
            instance_id = "33333333-3333-4333-8333-333333333333"
            instance = root / "routine_instances" / instance_id
            instance.mkdir(parents=True)
            (instance / "instance.json").write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "instance_id": instance_id,
                        "definition_id": "independent_test",
                        "display_name": "Independent Instance",
                        "enabled": True,
                        "buy_limit_enabled": False,
                        "buy_limit_amount": None,
                        "rules_file": "rules.json",
                    }
                ),
                encoding="utf-8",
            )
            (instance / "rules.json").write_text("{}\n", encoding="utf-8")
            script = textwrap.dedent(
                f"""
                import gui_auto_trade_setting_window
                import gui_windows
                from routine_instance_registry import load_routine_definitions
                from routine_package_contract import (
                    EVALUATION_ROLE,
                    EXECUTION_ADMISSION_ROLE,
                    evaluate_routine_gate,
                    load_routine_callable,
                )

                root = {str(root)!r}
                definitions = load_routine_definitions(project_root=root)
                assert [item.definition_id for item in definitions] == ["independent_test"]
                evaluate = load_routine_callable(definitions[0], EVALUATION_ROLE)
                assert evaluate({{}}) == {{
                    "signal": "BUY",
                    "source": "independent_test",
                }}
                admitted = evaluate_routine_gate(
                    instance_id={instance_id!r},
                    role=EXECUTION_ADMISSION_ROLE,
                    subject={{"stock_code": "005930"}},
                    project_root=root,
                )
                assert admitted["allowed"] is True, admitted
                missing = evaluate_routine_gate(
                    instance_id="44444444-4444-4444-8444-444444444444",
                    role=EXECUTION_ADMISSION_ROLE,
                    subject={{}},
                    project_root=root,
                )
                assert missing["allowed"] is False, missing
                assert missing["reason"] == "ROUTINE_GATE_UNAVAILABLE", missing
                """
            )
            result = subprocess.run(
                [sys.executable, "-c", script],
                cwd=self.project_root,
                env=dict(os.environ),
                capture_output=True,
                text=True,
                timeout=60,
            )
        self.assertEqual(
            0,
            result.returncode,
            msg=f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )

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
