# -*- coding: utf-8 -*-

import json
from pathlib import Path
import tempfile
import unittest

from unittest.mock import patch

import gui_routine_registry as registry
from gui_routine_registry import scan_group_records, scan_routine_records


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _write_routine_package(root: Path, name: str = "지표추종매매") -> None:
    package = root / "routines" / name
    _write_json(
        package / "routine.json",
        {"name": name, "entry_file": "routine.py", "enabled": True},
    )
    (package / "routine.py").write_text("ROUTINE = True\n", encoding="utf-8")


def _write_group(root: Path, name: str) -> Path:
    group = root / f"_{name}"
    _write_json(group / "budget.json", {"total_budget": 1000000})
    return group


class GroupRoutineDiscoverySeparationTests(unittest.TestCase):
    def test_group_discovery_normalizes_prefix_and_rejects_invalid_folders(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _write_group(root, "사용자가추가한그룹")
            _write_json(root / "접두사없는그룹" / "budget.json", {})
            (root / "_예산없는그룹").mkdir()

            groups = scan_group_records(project_root=root)

        self.assertEqual(["사용자가추가한그룹"], [group.name for group in groups])

    def test_routine_package_and_group_are_discovered_independently(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _write_routine_package(root)
            _write_group(root, "사용자가추가한그룹")

            routines = scan_routine_records(project_root=root)
            groups = scan_group_records(project_root=root)

        self.assertEqual(["지표추종매매"], [routine.name for routine in routines])
        self.assertEqual(["사용자가추가한그룹"], [group.name for group in groups])

    def test_same_name_group_and_routine_keep_separate_records(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _write_routine_package(root, "지표추종매매")
            _write_group(root, "지표추종매매")

            routines = scan_routine_records(project_root=root)
            groups = scan_group_records(project_root=root)

        self.assertEqual("package", routines[0].source_type)
        self.assertEqual(root / "routines" / "지표추종매매", routines[0].path)
        self.assertEqual(root / "_지표추종매매", groups[0].path)
        self.assertIsNot(routines[0], groups[0])

    def test_group_add_and_remove_are_reflected_without_registration(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.assertEqual([], scan_group_records(project_root=root))

            group = _write_group(root, "테스트그룹")
            self.assertEqual(
                ["테스트그룹"],
                [item.name for item in scan_group_records(project_root=root)],
            )

            (group / "budget.json").unlink()
            group.rmdir()
            self.assertEqual([], scan_group_records(project_root=root))

    def test_public_group_path_boundary_uses_root_groups_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package_name = "루틴정의"
            group = _write_group(root, "운영그룹")
            _write_routine_package(root, package_name)

            with (
                patch.object(registry, "PROJECT_ROOT", root),
                patch.object(registry, "ROUTINES_ROOT", root / "routines"),
            ):
                self.assertEqual([group], registry.get_group_dirs())
                self.assertEqual([package_name], registry.routine_names())


if __name__ == "__main__":
    unittest.main()
