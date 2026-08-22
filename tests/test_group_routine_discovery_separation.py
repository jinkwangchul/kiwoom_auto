# -*- coding: utf-8 -*-

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import gui_routine_registry as registry
from gui_routine_registry import scan_group_records, scan_routine_records


GROUP_ID = "11111111-1111-4111-8111-111111111111"


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


def _write_logical_registry(root: Path) -> Path:
    group_dir = root / "groups" / GROUP_ID
    _write_json(
        group_dir / "group.json",
        {
            "schema_version": "1.0",
            "group_id": GROUP_ID,
            "definition_id": "indicator_follow",
            "base_name": "지표추종매매",
            "display_name": "지표추종매매",
            "slot": 0,
            "created_at": "2026-08-22T09:30:00+09:00",
        },
    )
    _write_json(
        root / "groups" / "registry.json",
        {
            "schema_version": "1.0",
            "mode": "logical",
            "group_ids": [GROUP_ID],
            "cutover_at": "2026-08-22T10:00:00+09:00",
        },
    )
    return group_dir


class GroupRoutineDiscoverySeparationTests(unittest.TestCase):
    def test_logical_group_record_is_discovered_with_uuid_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            group_dir = _write_logical_registry(root)
            groups = scan_group_records(project_root=root)

        self.assertEqual(1, len(groups))
        self.assertEqual(GROUP_ID, groups[0].group_id)
        self.assertEqual("indicator_follow", groups[0].definition_id)
        self.assertEqual("지표추종매매", groups[0].display_name)
        self.assertEqual("logical_registry", groups[0].source_type)
        self.assertEqual(group_dir, groups[0].path)

    def test_root_underscore_budget_folder_is_never_a_group(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _write_json(root / "_새폴더" / "budget.json", {"budget": 1})
            self.assertEqual([], scan_group_records(project_root=root))

            _write_logical_registry(root)
            groups = scan_group_records(project_root=root)

        self.assertEqual([GROUP_ID], [group.group_id for group in groups])

    def test_absent_or_invalid_registry_discovers_no_groups(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            group_dir = root / "groups" / GROUP_ID
            _write_json(group_dir / "group.json", {"group_id": GROUP_ID})
            self.assertEqual([], scan_group_records(project_root=root))

            _write_json(root / "groups" / "registry.json", {"mode": "logical"})
            self.assertEqual([], scan_group_records(project_root=root))

    def test_routine_packages_are_discovered_independently(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _write_routine_package(root)
            _write_logical_registry(root)
            routines = scan_routine_records(project_root=root)
            groups = scan_group_records(project_root=root)

        self.assertEqual(["지표추종매매"], [routine.name for routine in routines])
        self.assertEqual(["지표추종매매"], [group.name for group in groups])

    def test_public_group_path_boundary_returns_logical_directories_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            group_dir = _write_logical_registry(root)
            with patch.object(registry, "PROJECT_ROOT", root):
                self.assertEqual([group_dir], registry.get_group_dirs())
                self.assertEqual(GROUP_ID, registry.group_record_by_id(GROUP_ID).group_id)


if __name__ == "__main__":
    unittest.main()
