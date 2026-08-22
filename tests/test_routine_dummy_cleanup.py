# -*- coding: utf-8 -*-

import json
from pathlib import Path
import tempfile
import unittest

from gui_routine_registry import (
    normalize_routine_name,
    scan_group_records,
    scan_routine_records,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class RoutineDummyCleanupTests(unittest.TestCase):
    def test_registration_check_dummy_paths_are_absent(self) -> None:
        self.assertFalse((PROJECT_ROOT / "_등록확인폴더").exists())
        self.assertFalse((PROJECT_ROOT / "routines" / "등록확인루틴").exists())

    def test_registration_check_alias_is_absent(self) -> None:
        self.assertEqual("등록확인폴더", normalize_routine_name("등록확인폴더"))
        self.assertEqual("등록확인폴더", normalize_routine_name("_등록확인폴더"))

    def test_underscore_budget_folder_is_not_a_group(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            group_dir = Path(temp) / "_사용자가추가한그룹"
            group_dir.mkdir()
            (group_dir / "budget.json").write_text(
                json.dumps({"total_budget": 1000000}), encoding="utf-8"
            )

            records = scan_group_records(project_root=Path(temp))

        self.assertEqual([], records)

    def test_production_routine_discovery_remains_available(self) -> None:
        names = {
            record.name
            for record in scan_routine_records()
        }
        self.assertIn("지표추종매매", names)
        self.assertNotIn("등록확인루틴", names)


if __name__ == "__main__":
    unittest.main()
