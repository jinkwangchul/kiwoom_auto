from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from assignment_episode_repository import (
    ASSIGNED,
    UNASSIGNED,
    AssignmentEpisodeTarget,
    CanonicalAssignmentEpisodeRepository,
)
from assignment_episode_transition_service import transition_assignment_episode


CODE = "005930"
T0 = "2026-08-23T09:00:00+09:00"
T1 = "2026-08-23T09:10:00+09:00"
T2 = "2026-08-23T09:20:00+09:00"
T3 = "2026-08-23T09:30:00+09:00"
T4 = "2026-08-23T09:40:00+09:00"


def assigned(
    instance_id: str,
    instance_name: str,
    *,
    group_id: str = "GROUP-1",
    group_name: str = "Group One",
) -> AssignmentEpisodeTarget:
    return AssignmentEpisodeTarget.assigned(
        instance_id=instance_id,
        group_id=group_id,
        definition_id="indicator_follow",
        instance_name_snapshot=instance_name,
        group_name_snapshot=group_name,
    )


class AssignmentEpisodeRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repository = CanonicalAssignmentEpisodeRepository(self.root)

    def open_unassigned(self) -> None:
        result = self.repository.open_episode(
            CODE,
            AssignmentEpisodeTarget.unassigned(),
            started_at=T0,
            start_reason="STOCK_REGISTERED",
            source="TEST",
        )
        self.assertTrue(result.success, result.error)

    def transition(
        self,
        target: AssignmentEpisodeTarget,
        at: str,
        *,
        start_reason: str = "ASSIGNMENT_CHANGED",
        end_reason: str = "ASSIGNMENT_CHANGED",
    ):
        return transition_assignment_episode(
            self.repository,
            CODE,
            target,
            changed_at=at,
            start_reason=start_reason,
            end_reason=end_reason,
            source="TEST",
        )

    def test_unassigned_to_assigned_closes_u1_and_opens_a1(self) -> None:
        self.open_unassigned()

        result = self.transition(assigned("A", "Alpha"), T1)

        self.assertTrue(result.success, result.error)
        episodes = self.repository.list_episodes(CODE)
        self.assertEqual([UNASSIGNED, ASSIGNED], [item.ownership_kind for item in episodes])
        self.assertEqual(T1, episodes[0].ended_at)
        self.assertEqual("A", episodes[1].instance_id)
        self.assertTrue(episodes[1].is_open)
        self.assertEqual([1, 2], [item.sequence for item in episodes])

    def test_a_to_b_to_a_preserves_three_distinct_assigned_episodes(self) -> None:
        self.open_unassigned()
        self.transition(assigned("A", "Alpha"), T1)
        self.transition(assigned("B", "Beta", group_id="GROUP-2", group_name="Group Two"), T2)
        self.transition(assigned("A", "Alpha"), T3)

        episodes = self.repository.list_episodes(CODE)
        assigned_episodes = [item for item in episodes if item.ownership_kind == ASSIGNED]
        self.assertEqual(["A", "B", "A"], [item.instance_id for item in assigned_episodes])
        self.assertEqual(3, len({item.episode_id for item in assigned_episodes}))
        self.assertEqual([2, 3, 4], [item.sequence for item in assigned_episodes])
        self.assertIsNotNone(assigned_episodes[0].ended_at)
        self.assertIsNotNone(assigned_episodes[1].ended_at)
        self.assertIsNone(assigned_episodes[2].ended_at)

    def test_same_assignment_is_no_op_and_keeps_original_name_snapshot(self) -> None:
        self.open_unassigned()
        self.transition(assigned("A", "Alpha"), T1)

        result = self.transition(assigned("A", "Alpha Renamed"), T2)

        self.assertTrue(result.success)
        self.assertTrue(result.no_op)
        self.assertFalse(result.changed)
        episodes = self.repository.list_episodes(CODE)
        self.assertEqual(2, len(episodes))
        self.assertEqual("Alpha", episodes[-1].instance_name_snapshot)

    def test_unassign_and_reassign_create_u1_and_new_a2(self) -> None:
        self.open_unassigned()
        self.transition(assigned("A", "Alpha"), T1)
        self.transition(AssignmentEpisodeTarget.unassigned(), T2, start_reason="UNASSIGNED")
        self.transition(assigned("A", "Alpha Renamed"), T3)

        episodes = self.repository.list_episodes(CODE)
        self.assertEqual(
            [UNASSIGNED, ASSIGNED, UNASSIGNED, ASSIGNED],
            [item.ownership_kind for item in episodes],
        )
        self.assertIsNone(episodes[2].instance_id)
        self.assertIsNone(episodes[2].group_id)
        self.assertEqual("Alpha", episodes[1].instance_name_snapshot)
        self.assertEqual("Alpha Renamed", episodes[3].instance_name_snapshot)
        self.assertNotEqual(episodes[1].episode_id, episodes[3].episode_id)

    def test_deleted_objects_are_not_needed_to_read_identity_snapshots(self) -> None:
        self.open_unassigned()
        self.transition(assigned("A", "Alpha", group_name="Original Group"), T1)
        self.transition(AssignmentEpisodeTarget.unassigned(), T2, start_reason="INSTANCE_DELETED")

        reloaded = CanonicalAssignmentEpisodeRepository(self.root)
        historical = reloaded.list_episodes(CODE)[1]
        self.assertEqual("A", historical.instance_id)
        self.assertEqual("Alpha", historical.instance_name_snapshot)
        self.assertEqual("GROUP-1", historical.group_id)
        self.assertEqual("Original Group", historical.group_name_snapshot)

    def test_lookup_by_episode_id_works_after_restart(self) -> None:
        self.open_unassigned()
        self.transition(assigned("A", "Alpha"), T1)
        expected = self.repository.list_episodes(CODE)[1]

        restarted = CanonicalAssignmentEpisodeRepository(self.root)
        self.assertEqual(expected, restarted.get_episode(expected.episode_id))
        self.assertEqual(expected, restarted.get_open_episode(CODE))

    def test_atomic_replace_failure_leaves_original_open_episode_unchanged(self) -> None:
        self.open_unassigned()
        self.transition(assigned("A", "Alpha"), T1)
        path = self.repository.document_path(CODE)
        before = path.read_bytes()

        with patch("assignment_episode_repository.os.replace", side_effect=OSError("injected failure")):
            result = self.transition(assigned("B", "Beta"), T2)

        self.assertFalse(result.success)
        self.assertEqual("EPISODE_TRANSITION_FAILED", result.error_code)
        self.assertEqual(before, path.read_bytes())
        episodes = self.repository.list_episodes(CODE)
        self.assertEqual(1, sum(item.is_open for item in episodes))
        self.assertEqual("A", episodes[-1].instance_id)
        self.assertFalse(any(self.repository.document_path(CODE).parent.glob("*.tmp")))

    def test_close_open_episode_validates_time_and_preserves_file_on_failure(self) -> None:
        self.open_unassigned()
        path = self.repository.document_path(CODE)
        before = path.read_bytes()

        result = self.repository.close_open_episode(
            CODE,
            ended_at="2026-08-23T08:59:00+09:00",
            end_reason="INVALID",
            source="TEST",
        )

        self.assertFalse(result.success)
        self.assertEqual(before, path.read_bytes())
        self.assertTrue(self.repository.get_open_episode(CODE).is_open)

    def test_document_contains_canonical_schema_and_no_legacy_collapse(self) -> None:
        self.open_unassigned()
        self.transition(assigned("A", "Alpha"), T1)
        self.transition(assigned("B", "Beta"), T2)
        self.transition(assigned("A", "Alpha Again"), T3)

        data = json.loads(self.repository.document_path(CODE).read_text(encoding="utf-8"))
        self.assertEqual("1.0", data["schema_version"])
        self.assertEqual(CODE, data["stock_code"])
        self.assertEqual(4, len(data["episodes"]))
        self.assertEqual(["A", "B", "A"], [item["instance_id"] for item in data["episodes"][1:]])

    def test_episode_writes_do_not_modify_current_assignment_or_legacy_history(self) -> None:
        stock_dir = self.root / "stocks" / f"{CODE}_Sample"
        stock_dir.mkdir(parents=True)
        config_path = stock_dir / "config.json"
        config_path.write_text(
            json.dumps(
                {
                    "assigned_routine_instance_id": "CURRENT-A",
                    "routine_assignment_history": [
                        {
                            "instance_id": "LEGACY-A",
                            "registered_at": "2026-01-01 09:00:00",
                            "unregistered_at": "2026-01-02 09:00:00",
                        }
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        before = config_path.read_bytes()

        self.open_unassigned()
        self.transition(assigned("A", "Alpha"), T1)

        self.assertEqual(before, config_path.read_bytes())
        self.assertEqual(
            "CURRENT-A",
            json.loads(config_path.read_text(encoding="utf-8"))["assigned_routine_instance_id"],
        )

    def test_unassigned_target_rejects_fake_identity(self) -> None:
        with self.assertRaises(ValueError):
            AssignmentEpisodeTarget(
                ownership_kind=UNASSIGNED,
                instance_id="FAKE",
            ).validated()

    def test_closed_episode_cannot_be_reopened_or_overlap_successor(self) -> None:
        self.open_unassigned()
        self.transition(assigned("A", "Alpha"), T1)
        before = self.repository.document_path(CODE).read_bytes()

        result = self.transition(assigned("B", "Beta"), T0)

        self.assertFalse(result.success)
        self.assertEqual(before, self.repository.document_path(CODE).read_bytes())
        self.assertEqual("A", self.repository.get_open_episode(CODE).instance_id)

    def test_reader_rejects_duplicate_identity_sequence_and_multiple_open_episodes(self) -> None:
        self.open_unassigned()
        self.transition(assigned("A", "Alpha"), T1)
        path = self.repository.document_path(CODE)
        original = json.loads(path.read_text(encoding="utf-8"))

        invalid_documents = []
        duplicate_id = json.loads(json.dumps(original))
        duplicate_id["episodes"][1]["episode_id"] = duplicate_id["episodes"][0]["episode_id"]
        invalid_documents.append(duplicate_id)

        duplicate_sequence = json.loads(json.dumps(original))
        duplicate_sequence["episodes"][1]["sequence"] = duplicate_sequence["episodes"][0]["sequence"]
        invalid_documents.append(duplicate_sequence)

        multiple_open = json.loads(json.dumps(original))
        multiple_open["episodes"][0]["ended_at"] = None
        multiple_open["episodes"][0]["end_reason"] = None
        multiple_open["episodes"][0]["end_source"] = None
        invalid_documents.append(multiple_open)

        for invalid in invalid_documents:
            with self.subTest(invalid=invalid):
                path.write_text(json.dumps(invalid), encoding="utf-8")
                with self.assertRaises(ValueError):
                    self.repository.list_episodes(CODE)


if __name__ == "__main__":
    unittest.main()
