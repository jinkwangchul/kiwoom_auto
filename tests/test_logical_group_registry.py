from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import UUID

import logical_group_registry
from logical_group_registry import LogicalGroupRepository


GROUP_IDS = tuple(
    UUID(value)
    for value in (
        "11111111-1111-4111-8111-111111111111",
        "22222222-2222-4222-8222-222222222222",
        "33333333-3333-4333-8333-333333333333",
        "44444444-4444-4444-8444-444444444444",
        "55555555-5555-4555-8555-555555555555",
    )
)


class LogicalGroupRepositoryTest(unittest.TestCase):
    def _repository(self, root: Path) -> LogicalGroupRepository:
        routine_dir = root / "routines" / "indicator_follow"
        routine_dir.mkdir(parents=True)
        (routine_dir / "routine.json").write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "definition_id": "indicator_follow",
                    "name": "지표추종매매",
                    "settings_ui": "indicator_follow",
                    "module_name": "indicator_follow_routine",
                    "rules_file": "rules.json",
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        ids = iter(GROUP_IDS)
        return LogicalGroupRepository(
            root,
            id_factory=lambda: next(ids),
            now_factory=lambda: datetime(2026, 8, 22, 9, 30, tzinfo=timezone.utc),
        )

    def test_first_and_repeated_create_use_lowest_slots_and_unique_uuids(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository = self._repository(root)

            results = [
                repository.create_group("indicator_follow", "지표추종매매")
                for _index in range(4)
            ]
            groups = repository.list_groups()

        self.assertTrue(all(result.success for result in results))
        self.assertEqual([0, 1, 2, 3], [group.slot for group in groups])
        self.assertEqual(
            ["지표추종매매", "지표추종매매_1", "지표추종매매_2", "지표추종매매_3"],
            [group.display_name for group in groups],
        )
        self.assertEqual(4, len({group.group_id for group in groups}))
        self.assertTrue(all(group.definition_id == "indicator_follow" for group in groups))

    def test_lowest_missing_slot_is_reused_instead_of_max_plus_one(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository = self._repository(root)
            created = [
                repository.create_group("indicator_follow", "지표추종매매").group
                for _index in range(4)
            ]
            removed = created[2]
            self.assertIsNotNone(removed)
            for path in removed.group_dir.iterdir():
                path.unlink()
            removed.group_dir.rmdir()

            self.assertEqual(2, repository.next_available_slot("지표추종매매"))
            replacement = repository.create_group("indicator_follow", "지표추종매매")

        self.assertTrue(replacement.success)
        self.assertEqual(2, replacement.group.slot)
        self.assertEqual("지표추종매매_2", replacement.group.display_name)

    def test_group_json_schema_and_read_back_match(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository = self._repository(root)
            result = repository.create_group("indicator_follow", "지표추종매매")
            metadata = json.loads(
                (result.group.group_dir / "group.json").read_text(encoding="utf-8")
            )
            reloaded = repository.get_group(result.group.group_id)

        self.assertEqual(
            {
                "schema_version": "1.0",
                "group_id": str(GROUP_IDS[0]),
                "definition_id": "indicator_follow",
                "base_name": "지표추종매매",
                "display_name": "지표추종매매",
                "slot": 0,
                "created_at": "2026-08-22T09:30:00+00:00",
            },
            metadata,
        )
        self.assertEqual(result.group, reloaded)

    def test_unknown_definition_is_rejected_without_groups_storage(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository = self._repository(root)

            result = repository.create_group("missing", "지표추종매매")

        self.assertFalse(result.success)
        self.assertEqual("DEFINITION_UNKNOWN", result.error_code)
        self.assertFalse((root / "groups").exists())

    def test_same_definition_can_be_registered_more_than_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repository = self._repository(Path(temp))

            first = repository.create_group("indicator_follow", "지표추종매매")
            second = repository.create_group("indicator_follow", "지표추종매매")

        self.assertTrue(first.success)
        self.assertTrue(second.success)
        self.assertEqual(first.group.definition_id, second.group.definition_id)
        self.assertNotEqual(first.group.group_id, second.group.group_id)

    def test_registered_create_atomically_appends_to_logical_registry(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository = self._repository(root)

            first = repository.create_group(
                "indicator_follow", "지표추종매매", register=True
            )
            second = repository.create_group(
                "indicator_follow", "지표추종매매", register=True
            )
            state = repository.registry_state()

        self.assertTrue(first.success)
        self.assertTrue(second.success)
        self.assertTrue(state.valid)
        self.assertEqual(
            (first.group.group_id, second.group.group_id),
            state.group_ids,
        )

    def test_registered_create_registry_failure_leaves_existing_registry_valid(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository = self._repository(root)
            first = repository.create_group(
                "indicator_follow", "지표추종매매", register=True
            )
            original_registry = repository.registry_path.read_bytes()
            with patch.object(
                repository,
                "_replace_registry",
                side_effect=OSError("registry blocked"),
            ):
                second = repository.create_group(
                    "indicator_follow", "지표추종매매", register=True
                )
            remaining = repository.list_groups()
            saved_registry = repository.registry_path.read_bytes()
            state = repository.registry_state()

        self.assertTrue(first.success)
        self.assertFalse(second.success)
        self.assertEqual([first.group.group_id], [group.group_id for group in remaining])
        self.assertEqual(original_registry, saved_registry)
        self.assertTrue(state.valid)

    def test_global_display_name_collision_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository = self._repository(root)
            first = repository.create_group("indicator_follow", "지표추종매매")
            self.assertTrue(first.success)
            collision_dir = root / "groups" / str(GROUP_IDS[4])
            collision_dir.mkdir(parents=True)
            (collision_dir / "group.json").write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "group_id": str(GROUP_IDS[4]),
                        "definition_id": "indicator_follow",
                        "base_name": "지표추종매매_1",
                        "display_name": "지표추종매매_1",
                        "slot": 0,
                        "created_at": "2026-08-22T09:00:00+00:00",
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            result = repository.create_group("indicator_follow", "지표추종매매")

        self.assertTrue(result.success)
        self.assertEqual(2, result.group.slot)
        self.assertEqual("지표추종매매_2", result.group.display_name)

    def test_promote_failure_leaves_no_partial_group(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository = self._repository(root)
            with patch.object(logical_group_registry.os, "replace", side_effect=OSError("blocked")):
                result = repository.create_group("indicator_follow", "지표추종매매")

            remaining = [
                path
                for path in (root / "groups").iterdir()
                if path.is_dir()
            ]

        self.assertFalse(result.success)
        self.assertEqual("GROUP_CREATE_FAILED", result.error_code)
        self.assertEqual([], remaining)

    def test_get_group_rejects_non_uuid_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repository = self._repository(Path(temp))

            self.assertIsNone(repository.get_group("C:/legacy/_지표추종매매"))

    def test_registry_cutover_is_valid_only_when_storage_matches(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository = self._repository(root)
            first = repository.create_group("indicator_follow", "지표추종매매").group
            second = repository.create_group("indicator_follow", "지표추종매매").group

            state = repository.promote_logical_cutover(
                (first.group_id, second.group_id),
                cutover_at="2026-08-22T10:00:00+09:00",
            )
            payload = json.loads(repository.registry_path.read_text(encoding="utf-8"))

            self.assertTrue(state.valid)
            self.assertTrue(repository.logical_cutover_active())
            self.assertEqual("logical", payload["mode"])
            self.assertEqual([first.group_id, second.group_id], payload["group_ids"])

            repository.registry_path.write_text(
                json.dumps({**payload, "group_ids": [first.group_id]}),
                encoding="utf-8",
            )
            self.assertFalse(repository.registry_state().valid)
            self.assertFalse(repository.logical_cutover_active())

    def test_registry_can_remain_logical_after_last_group_is_removed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository = self._repository(root)
            group = repository.create_group("indicator_follow", "지표추종매매").group
            repository.promote_logical_cutover(
                (group.group_id,),
                cutover_at="2026-08-22T10:00:00+09:00",
            )
            for path in group.group_dir.iterdir():
                path.unlink()
            group.group_dir.rmdir()

            state = repository.remove_group_from_registry(group.group_id)

            self.assertTrue(state.valid)
            self.assertEqual((), state.group_ids)
            self.assertTrue(repository.logical_cutover_active())


if __name__ == "__main__":
    unittest.main()
