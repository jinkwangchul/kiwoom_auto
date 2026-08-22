from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from group_pack_packing import (
    inspect_group_pack_source,
    pack_group,
    validate_group_pack_source,
)
from group_pack_registration import inspect_group_pack, register_group_pack
from logical_group_registry import LogicalGroupRepository
from routine_instance_repository import (
    RoutineInstanceCreateRequest,
    RoutineInstanceRepository,
)


class GroupPackPackingTest(unittest.TestCase):
    def _source_project(self, root: Path):
        package = root / "routines" / "지표추종매매"
        package.mkdir(parents=True)
        routine_json = {
            "schema_version": "1.0",
            "definition_id": "indicator_follow",
            "name": "지표추종매매",
            "settings_ui": "indicator_follow",
            "module_name": "indicator_follow_routine",
            "rules_file": "rules.json",
        }
        files = {
            "routines/지표추종매매/routine.json": (
                json.dumps(routine_json, ensure_ascii=False, indent=2) + "\n"
            ).encode("utf-8"),
            "routines/지표추종매매/routine.py": b"ENABLED = True\n",
            "routines/지표추종매매/rules.json": b"{}\n",
            "gui_indicator_follow_routine_settings_dialog.py": b"DIALOG = True\n",
        }
        for relative, data in files.items():
            path = root.joinpath(*Path(relative).parts)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        (package / "group_pack_spec.json").write_text(
            json.dumps(
                {
                    "definition_id": "indicator_follow",
                    "base_name": "지표추종매매",
                    "files": list(files),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        repository = LogicalGroupRepository(root)
        first = repository.create_group(
            "indicator_follow", "지표추종매매", register=True
        ).group
        second = repository.create_group(
            "indicator_follow", "지표추종매매", register=True
        ).group
        return first, second, tuple(files)

    def test_both_group_slots_produce_identical_definition_pack(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first, second, expected_files = self._source_project(root)
            output_a = root / "out" / "a.group.zip"
            output_b = root / "out" / "b.group.zip"
            with patch.dict(
                os.environ,
                {"KIWOOM_AUTO_DISTRIBUTION_PROFILE": "developer"},
            ):
                first_result = pack_group(first.group_id, output_a, project_root=root)
                second_result = pack_group(second.group_id, output_b, project_root=root)
            first_bytes = output_a.read_bytes()
            second_bytes = output_b.read_bytes()
            inspected = inspect_group_pack(output_a)
            with zipfile.ZipFile(output_a, "r") as archive:
                manifest = json.loads(archive.read("group_pack.json"))

        self.assertTrue(first_result.success)
        self.assertTrue(second_result.success)
        self.assertEqual(first_bytes, second_bytes)
        self.assertEqual("indicator_follow", inspected.definition_id)
        self.assertEqual("지표추종매매", inspected.base_name)
        self.assertEqual(sorted(expected_files), sorted(item.destination for item in inspected.files))
        self.assertNotIn("group_id", manifest)
        self.assertNotIn("slot", manifest)
        self.assertNotIn("display_name", manifest)

    def test_generated_pack_registers_and_supports_instance_creation(self) -> None:
        with tempfile.TemporaryDirectory() as source_temp, tempfile.TemporaryDirectory() as target_temp:
            source_root = Path(source_temp)
            target_root = Path(target_temp)
            group, _second, _files = self._source_project(source_root)
            output = source_root / "지표추종매매.group.zip"
            with patch.dict(
                os.environ,
                {"KIWOOM_AUTO_DISTRIBUTION_PROFILE": "developer"},
            ):
                packed = pack_group(group.group_id, output, project_root=source_root)
            registered = register_group_pack(output, project_root=target_root)
            instance_repository = RoutineInstanceRepository(target_root)
            with patch("routine_instance_repository._append_instance_lifecycle_event"):
                instance = instance_repository.create_instance(
                    RoutineInstanceCreateRequest(
                        definition_id="indicator_follow",
                        display_name="지표추종매매A",
                        group_id=registered.group.group_id,
                    ),
                    {},
                )

        self.assertTrue(packed.success)
        self.assertTrue(registered.success)
        self.assertTrue(instance.success)
        self.assertFalse(instance.instance.enabled)

    def test_distribution_profile_environment_does_not_gate_packing(self) -> None:
        for profile in ("beta", "production", "", "invalid"):
            with self.subTest(profile=profile), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                group, _second, _files = self._source_project(root)
                output = root / "packed.group.zip"
                with patch.dict(
                    os.environ,
                    {"KIWOOM_AUTO_DISTRIBUTION_PROFILE": profile},
                    clear=False,
                ):
                    result = pack_group(group.group_id, output, project_root=root)
                self.assertTrue(result.success)
                self.assertTrue(output.exists())

    def test_protected_or_missing_source_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            group, _second, _files = self._source_project(root)
            spec_path = root / "routines" / "지표추종매매" / "group_pack_spec.json"
            spec = json.loads(spec_path.read_text(encoding="utf-8"))
            spec["files"] = ["stocks/005930/config.json"]
            spec_path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")

            valid, error = validate_group_pack_source(group.group_id, project_root=root)

        self.assertFalse(valid)
        self.assertIn("보호된 저장 위치", error)

    def test_validation_failure_leaves_no_partial_zip(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            group, _second, _files = self._source_project(root)
            output = root / "failed.group.zip"
            with (
                patch.dict(
                    os.environ,
                    {"KIWOOM_AUTO_DISTRIBUTION_PROFILE": "developer"},
                ),
                patch(
                    "group_pack_packing.inspect_group_pack",
                    side_effect=ValueError("verification failed"),
                ),
            ):
                result = pack_group(group.group_id, output, project_root=root)
            partials = list(root.glob(".*.tmp.group.zip"))

        self.assertFalse(result.success)
        self.assertFalse(output.exists())
        self.assertEqual([], partials)

    def test_output_cannot_modify_project_user_data_roots(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            group, _second, _files = self._source_project(root)
            output = root / "stocks" / "forbidden.group.zip"
            with patch.dict(
                os.environ,
                {"KIWOOM_AUTO_DISTRIBUTION_PROFILE": "developer"},
            ):
                result = pack_group(group.group_id, output, project_root=root)

        self.assertFalse(result.success)
        self.assertEqual("OUTPUT_PATH_PROTECTED", result.error_code)
        self.assertFalse(output.exists())

    def test_source_inspection_uses_definition_not_group_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first, second, _files = self._source_project(root)

            first_source = inspect_group_pack_source(first.group_id, project_root=root)
            second_source = inspect_group_pack_source(second.group_id, project_root=root)

        self.assertEqual(first_source.definition_id, second_source.definition_id)
        self.assertEqual(first_source.base_name, second_source.base_name)
        self.assertEqual(first_source.files, second_source.files)


if __name__ == "__main__":
    unittest.main()
