# -*- coding: utf-8 -*-
from __future__ import annotations

from io import BytesIO
import json
import os
from pathlib import Path
from pathlib import PurePosixPath
import shutil
import subprocess
import sys
import tarfile
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

    def _package_absent_execution_copy(self, destination: Path) -> None:
        archive_result = subprocess.run(
            ["git", "archive", "--format=tar", "HEAD"],
            cwd=self.project_root,
            capture_output=True,
            check=True,
        )
        excluded_package = ("routines", "지표추종매매")
        excluded_data_roots = {"routine_instances", "runtime", "stocks"}
        with tarfile.open(fileobj=BytesIO(archive_result.stdout), mode="r:") as archive:
            members = []
            for member in archive.getmembers():
                relative = PurePosixPath(member.name)
                if relative.is_absolute() or ".." in relative.parts:
                    raise AssertionError(f"unsafe git archive member: {member.name}")
                if relative.parts and relative.parts[0] in excluded_data_roots:
                    continue
                if relative.parts[:2] == excluded_package:
                    continue
                if "__pycache__" in relative.parts or relative.suffix in {".pyc", ".log"}:
                    continue
                members.append(member)
            archive.extractall(destination, members=members)

        self.assertFalse((destination / ".git").exists())
        self.assertFalse((destination / "routine_instances").exists())
        self.assertFalse((destination / "runtime").exists())
        self.assertFalse((destination / "stocks").exists())
        self.assertFalse((destination / "routines" / "지표추종매매").exists())
        self.assertEqual(
            destination.resolve(),
            (destination / "routines").resolve().parent,
        )

    @staticmethod
    def _write_json(path: Path, payload: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def _install_independent_routine_fixture(self, root: Path) -> tuple[str, str]:
        package = root / "routines" / "independent_test"
        self._write_json(
            package / "routine.json",
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
            },
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
        self._write_json(
            root / "routine_instances" / instance_id / "instance.json",
            {
                "schema_version": "1.0",
                "instance_id": instance_id,
                "definition_id": "independent_test",
                "display_name": "Independent Instance",
                "enabled": True,
                "buy_limit_enabled": False,
                "buy_limit_amount": None,
                "rules_file": "rules.json",
            },
        )
        self._write_json(
            root / "routine_instances" / instance_id / "rules.json",
            {},
        )

        orphan_instance_id = "55555555-5555-4555-8555-555555555555"
        self._write_json(
            root / "routine_instances" / orphan_instance_id / "instance.json",
            {
                "schema_version": "1.0",
                "instance_id": orphan_instance_id,
                "definition_id": "indicator_follow",
                "display_name": "Missing Package Instance",
                "enabled": True,
                "buy_limit_enabled": False,
                "buy_limit_amount": None,
                "rules_file": "rules.json",
            },
        )
        self._write_json(
            root / "routine_instances" / orphan_instance_id / "rules.json",
            {},
        )
        return instance_id, orphan_instance_id

    def _run_package_absent_copy(
        self,
        root: Path,
        *,
        instance_id: str,
        orphan_instance_id: str,
    ) -> subprocess.CompletedProcess[str]:
        script = textwrap.dedent(
            f"""
            import importlib.util
            from pathlib import Path
            import sys

            root = Path({str(root)!r}).resolve()
            original_root = Path({str(self.project_root)!r}).resolve()
            assert Path.cwd().resolve() == root
            assert not (root / ".git").exists()
            assert not (root / "routines" / "지표추종매매").exists()
            assert (root / "routines").resolve().parent == root
            for search_entry in sys.path:
                if not search_entry:
                    continue
                resolved = Path(search_entry).resolve()
                assert resolved != original_root
                assert original_root not in resolved.parents
            sys.path.insert(0, str(root))

            import gui_auto_trade_setting_window
            import gui_windows
            import routine_instance_registry
            import routine_package_contract
            from routine_instance_registry import load_routine_definitions
            from routine_package_contract import (
                EVALUATION_ROLE,
                EXECUTION_ADMISSION_ROLE,
                evaluate_routine_gate,
                load_routine_callable,
            )

            def assert_loaded_from_copy(module):
                origin = Path(module.__file__).resolve()
                assert origin == root or root in origin.parents, (module.__name__, origin)
                assert original_root != origin and original_root not in origin.parents

            for module in (
                gui_auto_trade_setting_window,
                gui_windows,
                routine_instance_registry,
                routine_package_contract,
            ):
                assert_loaded_from_copy(module)
            assert importlib.util.find_spec("routines.지표추종매매") is None
            assert not any(
                name == "routines.지표추종매매"
                or name.startswith("routines.지표추종매매.")
                for name in sys.modules
            )

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

            orphan = evaluate_routine_gate(
                instance_id={orphan_instance_id!r},
                role=EXECUTION_ADMISSION_ROLE,
                subject={{}},
                project_root=root,
            )
            assert orphan["allowed"] is False, orphan
            assert orphan["reason"] == "ROUTINE_GATE_UNAVAILABLE", orphan
            assert not any(
                name == "routines.지표추종매매"
                or name.startswith("routines.지표추종매매.")
                for name in sys.modules
            )

            print("MAIN_ORIGIN=" + gui_windows.__file__)
            print("SETTINGS_ORIGIN=" + gui_auto_trade_setting_window.__file__)
            print("REGISTRY_ORIGIN=" + routine_instance_registry.__file__)
            print("CONTRACT_ORIGIN=" + routine_package_contract.__file__)
            print("INDEPENDENT_ROUTINE=BUY/ALLOW")
            print("MISSING_INSTANCE=ROUTINE_GATE_UNAVAILABLE")
            print("MISSING_PACKAGE=ROUTINE_GATE_UNAVAILABLE")
            """
        )
        environment = dict(os.environ)
        environment.pop("PYTHONPATH", None)
        environment.pop("PYTHONHOME", None)
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        environment["QT_QPA_PLATFORM"] = "offscreen"
        return subprocess.run(
            [sys.executable, "-I", "-c", script],
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
            timeout=60,
        )

    def test_main_and_other_routine_work_without_indicator_follow_package(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._package_absent_execution_copy(root)
            instance_id, orphan_instance_id = self._install_independent_routine_fixture(
                root
            )
            result = self._run_package_absent_copy(
                root,
                instance_id=instance_id,
                orphan_instance_id=orphan_instance_id,
            )
        self.assertEqual(
            0,
            result.returncode,
            msg=f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )
        self.assertIn("INDEPENDENT_ROUTINE=BUY/ALLOW", result.stdout)
        self.assertIn("MISSING_INSTANCE=ROUTINE_GATE_UNAVAILABLE", result.stdout)
        self.assertIn("MISSING_PACKAGE=ROUTINE_GATE_UNAVAILABLE", result.stdout)

    def test_package_absence_isolation_detects_injected_reverse_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._package_absent_execution_copy(root)
            instance_id, orphan_instance_id = self._install_independent_routine_fixture(
                root
            )
            main_module = root / "gui_auto_trade_setting_window.py"
            source = main_module.read_text(encoding="utf-8")
            future_import = "from __future__ import annotations\n"
            self.assertIn(future_import, source)
            injected_dependency = (
                "import sys\n"
                "try:\n"
                "    import routines.지표추종매매\n"
                "except ModuleNotFoundError as exc:\n"
                "    print('EXPECTED_REVERSE_DEPENDENCY_DETECTED:' + str(exc.name), "
                "file=sys.stderr)\n"
                "    raise\n"
            )
            main_module.write_text(
                source.replace(
                    future_import,
                    future_import + injected_dependency,
                    1,
                ),
                encoding="utf-8",
            )
            result = self._run_package_absent_copy(
                root,
                instance_id=instance_id,
                orphan_instance_id=orphan_instance_id,
            )

        self.assertNotEqual(0, result.returncode)
        self.assertIn("EXPECTED_REVERSE_DEPENDENCY_DETECTED", result.stderr)
        self.assertIn("routines.지표추종매매", result.stderr)
        self.assertNotIn("INDEPENDENT_ROUTINE=BUY/ALLOW", result.stdout)

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
