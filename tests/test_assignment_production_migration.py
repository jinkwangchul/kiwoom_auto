from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import patch
from uuid import uuid4

from assignment_episode_linkage import (
    ASSIGNMENT_TRANSACTION_COMMITTED,
    ASSIGNMENT_TRANSACTION_FIELD_CONFLICT,
    AssignmentTransactionJournalRepository,
    assign_stock_routine,
    unassign_stock_routine,
)
from assignment_episode_repository import CanonicalAssignmentEpisodeRepository
from stock_repository import StockRepository


CODE = "005930"


class AssignmentProductionMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.group_ids = [str(uuid4()) for _ in range(3)]
        self.instance_ids = [str(uuid4()) for _ in range(3)]
        self._write_foundation()
        self.repository = StockRepository(self.root)
        event_patch = patch("stock_repository._append_routine_changed")
        event_patch.start()
        self.addCleanup(event_patch.stop)

    @staticmethod
    def _json(path: Path, payload: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _write_foundation(self) -> None:
        routine = self.root / "routines" / "Sample"
        self._json(
            routine / "routine.json",
            {
                "schema_version": "1.0",
                "definition_id": "sample",
                "name": "Sample",
                "entry_file": "routine.py",
                "rules_file": "rules.json",
                "enabled": True,
            },
        )
        (routine / "routine.py").write_text("", encoding="utf-8")
        for index, group_id in enumerate(self.group_ids):
            name = f"Group{index + 1}"
            self._json(
                self.root / "groups" / group_id / "group.json",
                {
                    "schema_version": "1.0",
                    "group_id": group_id,
                    "definition_id": "sample",
                    "base_name": name,
                    "display_name": name,
                    "slot": 0,
                    "created_at": "2026-08-29T09:00:00+09:00",
                },
            )
        self._json(
            self.root / "groups" / "registry.json",
            {
                "schema_version": "1.0",
                "mode": "logical",
                "group_ids": self.group_ids,
                "cutover_at": "2026-08-29T09:00:00+09:00",
            },
        )
        for index, (instance_id, group_id) in enumerate(
            zip(self.instance_ids, self.group_ids),
            start=1,
        ):
            self._json(
                self.root / "routine_instances" / instance_id / "instance.json",
                {
                    "schema_version": "1.0",
                    "instance_id": instance_id,
                    "definition_id": "sample",
                    "display_name": f"Instance {index}",
                    "enabled": False,
                    "buy_limit_enabled": False,
                    "buy_limit_amount": None,
                    "rules_file": "rules.json",
                    "created_at": "2026-08-29T09:00:00+09:00",
                    "updated_at": "2026-08-29T09:00:00+09:00",
                    "group_id": group_id,
                },
            )
            self._json(
                self.root / "routine_instances" / instance_id / "rules.json",
                {},
            )
        stock = self.root / "stocks" / f"{CODE}_Sample"
        self._json(
            stock / "config.json",
            {
                "assigned_routine_instance_id": "",
                "routines": [],
                "buy_amount": 333_000,
                "buy_limit_amount": 777_000,
                "operation_mode": "SCHEDULED",
                "operation_excluded": True,
                "policy_overrides": {"entry": False, "exit": True},
            },
        )
        self._json(stock / "state.json", {"status": "STOPPED"})
        self._json(stock / "orders.json", {"orders": []})

    def _config(self) -> dict[str, object]:
        path = self.root / "stocks" / f"{CODE}_Sample" / "config.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def _journals(self) -> list[dict[str, object]]:
        root = AssignmentTransactionJournalRepository(self.root).transactions_root / CODE
        if not root.is_dir():
            return []
        return [json.loads(path.read_text(encoding="utf-8")) for path in root.glob("*.json")]

    def _assign(self, target: str, expected: str):
        return assign_stock_routine(
            self.root,
            CODE,
            "Sample",
            instance_id=target,
            instance_name="stale caller display",
            definition_id="stale-caller-definition",
            routine_type="stale caller type",
            expected_instance_id=expected,
            stock_repository=self.repository,
        )

    def test_assign_reassign_unassign_use_journal_and_preserve_unrelated_fields(self) -> None:
        instance_a, instance_b, _ = self.instance_ids
        assigned = self._assign(instance_a, "")
        self.assertTrue(assigned.ok, assigned)
        self.assertTrue(assigned.changed)
        self.assertEqual("", assigned.assignment_before)
        self.assertEqual(instance_a, assigned.assignment_after)
        self.assertTrue(assigned.transaction_id)

        reassigned = self._assign(instance_b, instance_a)
        self.assertTrue(reassigned.ok, reassigned)
        unassigned = unassign_stock_routine(
            self.root,
            CODE,
            "Sample",
            [],
            expected_instance_id=instance_b,
            stock_repository=self.repository,
        )
        self.assertTrue(unassigned.ok, unassigned)

        config = self._config()
        self.assertEqual("", config["assigned_routine_instance_id"])
        for key, expected in {
            "buy_amount": 333_000,
            "buy_limit_amount": 777_000,
            "operation_mode": "SCHEDULED",
            "operation_excluded": True,
            "policy_overrides": {"entry": False, "exit": True},
        }.items():
            self.assertEqual(expected, config[key])
        self.assertEqual("", config["routine"])
        self.assertEqual([], config["routines"])
        history = config["routine_assignment_history"]
        self.assertEqual([instance_a, instance_b], [item["instance_id"] for item in history])
        self.assertTrue(all(item["unregistered_at"] for item in history))

        episodes = CanonicalAssignmentEpisodeRepository(self.root).list_episodes(CODE)
        self.assertEqual(
            ["UNASSIGNED", "ASSIGNED", "ASSIGNED", "UNASSIGNED"],
            [episode.ownership_kind for episode in episodes],
        )
        self.assertEqual(instance_a, episodes[1].instance_id)
        self.assertEqual(instance_b, episodes[2].instance_id)
        self.assertTrue(all(episode.ended_at for episode in episodes[:-1]))
        self.assertIsNone(episodes[-1].ended_at)
        journals = self._journals()
        self.assertEqual(3, len(journals))
        self.assertTrue(
            all(item["state"] == ASSIGNMENT_TRANSACTION_COMMITTED for item in journals)
        )

    def test_same_id_rename_refreshes_display_without_changing_episode_identity(self) -> None:
        instance_a = self.instance_ids[0]
        self.assertTrue(self._assign(instance_a, "").ok)
        episode_repository = CanonicalAssignmentEpisodeRepository(self.root)
        before_episode = episode_repository.get_open_episode(CODE)
        self.assertIsNotNone(before_episode)
        metadata_path = self.root / "routine_instances" / instance_a / "instance.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["display_name"] = "Renamed Instance"
        self._json(metadata_path, metadata)

        refreshed = self._assign(instance_a, instance_a)
        self.assertTrue(refreshed.ok, refreshed)
        self.assertTrue(refreshed.changed)
        self.assertEqual(instance_a, self._config()["assigned_routine_instance_id"])
        self.assertEqual("Renamed Instance", self._config()["routine_instance_name"])
        self.assertEqual("sample", self._config()["routine_definition_id"])
        self.assertEqual("Sample", self._config()["routine_type"])
        after_episode = episode_repository.get_open_episode(CODE)
        self.assertEqual(before_episode.episode_id, after_episode.episode_id)
        self.assertEqual(2, len(episode_repository.list_episodes(CODE)))

    def test_stale_expected_identity_conflicts_before_journal_or_mutation(self) -> None:
        instance_a, instance_b, instance_c = self.instance_ids
        self.assertTrue(self._assign(instance_a, "").ok)
        self.assertTrue(self._assign(instance_b, instance_a).ok)
        journals_before = len(self._journals())
        episodes_before = CanonicalAssignmentEpisodeRepository(self.root).document_path(
            CODE
        ).read_bytes()

        stale = self._assign(instance_c, instance_a)

        self.assertFalse(stale.ok)
        self.assertEqual(ASSIGNMENT_TRANSACTION_FIELD_CONFLICT, stale.reason_code)
        self.assertEqual("", stale.transaction_id)
        self.assertEqual(instance_b, self._config()["assigned_routine_instance_id"])
        self.assertEqual(journals_before, len(self._journals()))
        self.assertEqual(
            episodes_before,
            CanonicalAssignmentEpisodeRepository(self.root).document_path(CODE).read_bytes(),
        )

    def test_concurrent_same_expected_identity_allows_only_one_winner(self) -> None:
        instance_a, instance_b, instance_c = self.instance_ids
        self.assertTrue(self._assign(instance_a, "").ok)
        barrier = threading.Barrier(3)
        results = []

        def worker(target: str) -> None:
            repository = StockRepository(self.root)
            barrier.wait()
            results.append(
                assign_stock_routine(
                    self.root,
                    CODE,
                    "Sample",
                    instance_id=target,
                    instance_name="stale",
                    definition_id="sample",
                    routine_type="Sample",
                    expected_instance_id=instance_a,
                    stock_repository=repository,
                )
            )

        threads = [
            threading.Thread(target=worker, args=(instance_b,)),
            threading.Thread(target=worker, args=(instance_c,)),
        ]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(5)

        self.assertEqual(2, len(results))
        self.assertEqual(1, sum(result.ok for result in results))
        self.assertEqual(
            1,
            sum(
                result.reason_code == ASSIGNMENT_TRANSACTION_FIELD_CONFLICT
                for result in results
            ),
        )
        self.assertIn(
            self._config()["assigned_routine_instance_id"],
            {instance_b, instance_c},
        )

    def test_missing_instance_or_group_fails_before_journal(self) -> None:
        journal_count = len(self._journals())
        missing_instance = self._assign(str(uuid4()), "")
        self.assertFalse(missing_instance.ok)
        self.assertEqual("PREPARE_FAILED", missing_instance.reason_code)
        self.assertEqual(journal_count, len(self._journals()))

        instance_a = self.instance_ids[0]
        group_dir = self.root / "groups" / self.group_ids[0]
        renamed_group = group_dir.with_name(f".{group_dir.name}.missing")
        group_dir.rename(renamed_group)
        missing_group = self._assign(instance_a, "")
        self.assertFalse(missing_group.ok)
        self.assertEqual("PREPARE_FAILED", missing_group.reason_code)
        self.assertEqual(journal_count, len(self._journals()))


if __name__ == "__main__":
    unittest.main()
