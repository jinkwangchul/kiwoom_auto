from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from group_pack_registration import (
    inspect_group_pack,
    register_group_pack,
    validate_group_pack,
)
from logical_group_registry import LogicalGroupCreateResult, LogicalGroupRepository
from routine_instance_repository import (
    RoutineInstanceCreateRequest,
    RoutineInstanceRepository,
)


class GroupPackRegistrationTest(unittest.TestCase):
    def _payload(self) -> dict[str, bytes]:
        routine_json = json.dumps(
            {
                "schema_version": "1.0",
                "definition_id": "indicator_follow",
                "name": "지표추종매매",
                "settings_ui": "indicator_follow",
                "module_name": "indicator_follow_routine",
                "rules_file": "rules.json",
                "locators": {
                    "evaluation": {"file": "routine.py", "callable": "evaluate"},
                    "settings": {"file": "settings.py", "callable": "Dialog"},
                    "rule_mapper": {"file": "routine_rule_mapper.py"},
                    "execution_admission": {
                        "file": "routine.py",
                        "callable": "evaluate_execution_admission",
                    },
                    "final_safety": {
                        "file": "routine.py",
                        "callable": "evaluate_final_real_order_safety",
                    },
                },
            },
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8")
        routine_code = b"""\
def evaluate(context):
    return None

def _allow(subject, rules, routine_identity, rules_identity):
    return {
        'allowed': True,
        'routine_identity': routine_identity,
        'rules_identity': rules_identity,
    }

evaluate_execution_admission = _allow
evaluate_final_real_order_safety = _allow
"""
        return {
            "routines/indicator_follow/routine.json": routine_json,
            "routines/indicator_follow/routine.py": routine_code,
            "routines/indicator_follow/settings.py": b"class Dialog:\n    pass\n",
            "routines/indicator_follow/routine_rule_mapper.py": b"MAPPER = True\n",
            "routines/indicator_follow/rules.json": b"{}\n",
        }

    def _write_pack(
        self,
        root: Path,
        *,
        payload: dict[str, bytes] | None = None,
        manifest_override: dict[str, object] | None = None,
        symlink: bool = False,
    ) -> Path:
        payload = payload or self._payload()
        files = []
        for destination, data in payload.items():
            files.append(
                {
                    "source": f"payload/{destination}",
                    "destination": destination,
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
            )
        manifest = {
            "schema_version": "1.0",
            "definition_id": "indicator_follow",
            "base_name": "지표추종매매",
            "files": files,
        }
        if manifest_override:
            manifest.update(manifest_override)
        pack_path = root / "indicator-follow.group.zip"
        with zipfile.ZipFile(pack_path, "w") as archive:
            archive.writestr(
                "group_pack.json",
                json.dumps(manifest, ensure_ascii=False),
            )
            for destination, data in payload.items():
                archive.writestr(f"payload/{destination}", data)
            if symlink:
                info = zipfile.ZipInfo("payload/link")
                info.create_system = 3
                info.external_attr = 0o120777 << 16
                archive.writestr(info, "target")
        return pack_path

    def test_initial_and_identical_registration_install_once_and_allocate_groups(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pack = self._write_pack(root)
            first = register_group_pack(pack, project_root=root)
            installed = root / "routines" / "indicator_follow" / "routine.py"
            first_mtime = installed.stat().st_mtime_ns
            second = register_group_pack(pack, project_root=root)
            second_mtime = installed.stat().st_mtime_ns
            repository = LogicalGroupRepository(root)
            groups = repository.list_groups()
            registry_valid = repository.registry_state().valid

        self.assertTrue(first.success)
        self.assertTrue(second.success)
        self.assertEqual(["지표추종매매", "지표추종매매_1"], [g.display_name for g in groups])
        self.assertEqual(first_mtime, second_mtime)
        self.assertEqual((), second.installed_files)
        self.assertEqual(5, len(second.reused_files))
        self.assertTrue(registry_valid)

    def test_lowest_deleted_slot_is_reused(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pack = self._write_pack(root)
            results = [register_group_pack(pack, project_root=root) for _ in range(3)]
            repository = LogicalGroupRepository(root)
            repository.rollback_created_group(results[2].group.group_id)

            replacement = register_group_pack(pack, project_root=root)

        self.assertTrue(replacement.success)
        self.assertEqual(2, replacement.group.slot)
        self.assertEqual("지표추종매매_2", replacement.group.display_name)

    def test_existing_different_file_blocks_without_group_or_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pack = self._write_pack(root)
            destination = root / "routines" / "indicator_follow" / "routine.py"
            destination.parent.mkdir(parents=True)
            destination.write_bytes(b"LOCAL = True\n")

            result = register_group_pack(pack, project_root=root)
            saved_data = destination.read_bytes()
            groups = LogicalGroupRepository(root).list_groups()

        self.assertFalse(result.success)
        self.assertEqual(b"LOCAL = True\n", saved_data)
        self.assertEqual([], groups)

    def test_invalid_hash_traversal_and_symlink_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bad_hash = self._write_pack(root, manifest_override={"files": [{
                "source": "payload/routines/indicator_follow/routine.py",
                "destination": "routines/indicator_follow/routine.py",
                "sha256": "0" * 64,
            }]})
            self.assertFalse(validate_group_pack(bad_hash)[0])

            traversal = self._write_pack(root)
            with zipfile.ZipFile(traversal, "a") as archive:
                archive.writestr("../escape", b"bad")
            self.assertFalse(validate_group_pack(traversal)[0])

            symlink_pack = self._write_pack(root, symlink=True)
            self.assertFalse(validate_group_pack(symlink_pack)[0])
            escaped = (root / "escape").exists()

        self.assertFalse(escaped)

    def test_protected_destination_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data = b"{}\n"
            pack = self._write_pack(
                root,
                payload={"stocks/005930/config.json": data},
            )

            with self.assertRaises(ValueError):
                inspect_group_pack(pack)

    def test_manifest_rejects_update_metadata_and_definition_name_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            versioned = self._write_pack(root, manifest_override={"version": "2.0"})
            self.assertFalse(validate_group_pack(versioned)[0])

            mismatched = self._write_pack(
                root,
                manifest_override={"base_name": "임의그룹명"},
            )
            result = register_group_pack(mismatched, project_root=root)
            installed_exists = (root / "routines").exists()
            groups_exists = (root / "groups").exists()

        self.assertFalse(result.success)
        self.assertFalse(installed_exists)
        self.assertFalse(groups_exists)

    def test_group_create_failure_rolls_back_only_new_install(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pack = self._write_pack(root)
            repository = LogicalGroupRepository(root)
            with patch.object(
                repository,
                "create_group",
                return_value=LogicalGroupCreateResult(False, error="blocked"),
            ):
                result = register_group_pack(
                    pack,
                    project_root=root,
                    repository=repository,
                )
            routines_exists = (root / "routines").exists()
            groups_exists = (root / "groups").exists()

        self.assertFalse(result.success)
        self.assertFalse(routines_exists)
        self.assertFalse(groups_exists)

    def test_missing_locator_target_fails_closed_and_rolls_back_install(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            payload = self._payload()
            del payload["routines/indicator_follow/routine_rule_mapper.py"]
            pack = self._write_pack(root, payload=payload)

            result = register_group_pack(pack, project_root=root)

        self.assertFalse(result.success)
        self.assertEqual("PACK_REGISTRATION_FAILED", result.error_code)
        self.assertIn("routine locator file does not exist", result.error)
        self.assertFalse((root / "routines").exists())
        self.assertFalse((root / "groups").exists())

    def test_registered_definition_can_create_disabled_instance(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pack = self._write_pack(root)
            registered = register_group_pack(pack, project_root=root)
            repository = RoutineInstanceRepository(root)
            with patch("routine_instance_repository._append_instance_lifecycle_event"):
                instance = repository.create_instance(
                    RoutineInstanceCreateRequest(
                        definition_id="indicator_follow",
                        display_name="지표추종매매A",
                        group_id=registered.group.group_id,
                    ),
                    {},
                )

        self.assertTrue(instance.success)
        self.assertFalse(instance.instance.enabled)
        self.assertEqual(registered.group.group_id, instance.instance.group_id)


if __name__ == "__main__":
    unittest.main()
