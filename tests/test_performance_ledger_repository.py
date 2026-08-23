from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from assignment_episode_repository import (
    AssignmentEpisodeTarget,
    CanonicalAssignmentEpisodeRepository,
)
from performance_ledger_repository import (
    CANONICAL_OWNER_POLICY,
    OWNERSHIP_UNRESOLVED,
    CanonicalStockPerformanceLedgerRepository,
    canonical_performance_event_key,
)


CODE = "005930"
T0 = "2026-08-23T09:00:00+09:00"
T1 = "2026-08-23T09:10:00+09:00"
T2 = "2026-08-23T09:20:00+09:00"
T3 = "2026-08-23T09:30:00+09:00"


class PerformanceLedgerRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.episodes = CanonicalAssignmentEpisodeRepository(self.root)
        self.ledger = CanonicalStockPerformanceLedgerRepository(
            self.root,
            episode_repository=self.episodes,
            now_factory=lambda: datetime.fromisoformat("2026-08-23T10:01:00+09:00"),
        )
        opened = self.episodes.open_episode(
            CODE,
            AssignmentEpisodeTarget.unassigned(),
            started_at=T0,
            start_reason="STOCK_REGISTERED",
            source="TEST",
        )
        self.assertTrue(opened.success, opened.error)
        self.unassigned_episode = opened.opened_episode

    @staticmethod
    def assigned(instance_id: str, group_id: str) -> AssignmentEpisodeTarget:
        return AssignmentEpisodeTarget.assigned(
            instance_id=instance_id,
            group_id=group_id,
            definition_id="indicator_follow",
            instance_name_snapshot=instance_id,
            group_name_snapshot=group_id,
        )

    def transition(self, target: AssignmentEpisodeTarget, changed_at: str):
        result = self.episodes.transition_episode(
            CODE,
            target,
            changed_at=changed_at,
            start_reason="ASSIGNMENT_CHANGED",
            end_reason="ASSIGNMENT_CHANGED",
            source="TEST",
        )
        self.assertTrue(result.success, result.error)
        return result.opened_episode

    def event(
        self,
        allocations: list[dict[str, object]],
        *,
        exit_episode_id: str,
        quantity: int = 5,
        cost_basis: int = 500,
        gross_pnl: int = 100,
        net_pnl: int | None = None,
        execution_identity: str = "EXEC-1",
        fill_id: str | None = "FILL-1",
        realization_id: str | None = "REALIZATION-1",
    ) -> dict[str, object]:
        return {
            "stock_code": CODE,
            "broker": "KIWOOM",
            "account_number": "1234-5678",
            "trade_date": "2026-08-23",
            "broker_order_no": "ORDER-1",
            "execution_identity": execution_identity,
            "fill_id": fill_id,
            "realization_id": realization_id,
            "realized_at": "2026-08-23T10:00:00+09:00",
            "quantity": quantity,
            "realized_cost_basis": cost_basis,
            "gross_pnl": gross_pnl,
            "fee": None,
            "tax": None,
            "net_pnl": net_pnl,
            "exit_episode_id": exit_episode_id,
            "canonical_owner_policy": CANONICAL_OWNER_POLICY,
            "allocations": allocations,
        }

    @staticmethod
    def allocation(
        lot_id: str,
        episode_id: str,
        quantity: int,
        cost_basis: int,
        gross_pnl: int,
        *,
        net_pnl: int | None = None,
    ) -> dict[str, object]:
        return {
            "entry_lot_id": lot_id,
            "entry_episode_id": episode_id,
            "quantity": quantity,
            "cost_basis": cost_basis,
            "gross_pnl": gross_pnl,
            "net_pnl": net_pnl,
        }

    def test_single_episode_realization_is_recorded_once(self) -> None:
        a1 = self.transition(self.assigned("INSTANCE-A", "GROUP-A"), T1)
        result = self.ledger.append_event(
            self.event(
                [self.allocation("LOT-A1", a1.episode_id, 5, 500, 100)],
                exit_episode_id=a1.episode_id,
            )
        )

        self.assertTrue(result.success, result.error)
        self.assertTrue(result.changed)
        self.assertEqual(1, len(self.ledger.list_events(CODE)))
        self.assertEqual(a1.episode_id, result.event.allocations[0].entry_episode_id)
        self.assertEqual(100, result.event.allocations[0].gross_pnl)

    def test_reassignment_does_not_reassign_entry_ownership_to_exit_episode(self) -> None:
        a1 = self.transition(self.assigned("INSTANCE-A", "GROUP-A"), T1)
        b1 = self.transition(self.assigned("INSTANCE-B", "GROUP-B"), T2)

        result = self.ledger.append_event(
            self.event(
                [self.allocation("LOT-A1", a1.episode_id, 5, 500, 100)],
                exit_episode_id=b1.episode_id,
            )
        )

        self.assertTrue(result.success, result.error)
        self.assertEqual(a1.episode_id, result.event.allocations[0].entry_episode_id)
        self.assertEqual(b1.episode_id, result.event.exit_episode_id)

    def test_one_sell_can_reconcile_lots_from_multiple_episodes(self) -> None:
        a1 = self.transition(self.assigned("INSTANCE-A", "GROUP-A"), T1)
        b1 = self.transition(self.assigned("INSTANCE-B", "GROUP-B"), T2)

        result = self.ledger.append_event(
            self.event(
                [
                    self.allocation("LOT-A1", a1.episode_id, 3, 300, 60),
                    self.allocation("LOT-B1", b1.episode_id, 2, 200, 40),
                ],
                exit_episode_id=b1.episode_id,
            )
        )

        self.assertTrue(result.success, result.error)
        self.assertEqual(1, len(self.ledger.list_events(CODE)))
        self.assertEqual(2, len(self.ledger.list_allocations(CODE)))
        self.assertEqual(5, sum(item.quantity for item in result.event.allocations))
        self.assertEqual(100, sum(item.gross_pnl for item in result.event.allocations))

    def test_duplicate_key_same_payload_is_idempotent_across_restart(self) -> None:
        a1 = self.transition(self.assigned("INSTANCE-A", "GROUP-A"), T1)
        payload = self.event(
            [self.allocation("LOT-A1", a1.episode_id, 5, 500, 100)],
            exit_episode_id=a1.episode_id,
        )
        first = self.ledger.append_event(payload)
        restarted = CanonicalStockPerformanceLedgerRepository(
            self.root,
            episode_repository=CanonicalAssignmentEpisodeRepository(self.root),
        )
        second = restarted.append_event(payload)

        self.assertTrue(first.changed)
        self.assertTrue(second.success, second.error)
        self.assertTrue(second.no_op)
        self.assertFalse(second.changed)
        self.assertEqual(first.event.performance_event_id, second.event.performance_event_id)
        self.assertEqual(1, len(restarted.list_events(CODE)))

    def test_duplicate_key_with_different_economic_payload_is_hard_conflict(self) -> None:
        a1 = self.transition(self.assigned("INSTANCE-A", "GROUP-A"), T1)
        original = self.event(
            [self.allocation("LOT-A1", a1.episode_id, 5, 500, 100)],
            exit_episode_id=a1.episode_id,
        )
        first = self.ledger.append_event(original)
        before = self.ledger.document_path(CODE).read_bytes()
        conflict_payload = self.event(
            [self.allocation("LOT-A1", a1.episode_id, 5, 500, 101)],
            exit_episode_id=a1.episode_id,
            gross_pnl=101,
        )
        conflict = self.ledger.append_event(conflict_payload)

        self.assertTrue(first.success)
        self.assertFalse(conflict.success)
        self.assertTrue(conflict.conflict)
        self.assertEqual("PERFORMANCE_EVENT_CONFLICT", conflict.error_code)
        self.assertEqual(before, self.ledger.document_path(CODE).read_bytes())

    def test_unassigned_episode_is_preserved_without_parent_owner(self) -> None:
        result = self.ledger.append_event(
            self.event(
                [self.allocation("LOT-U1", self.unassigned_episode.episode_id, 5, 500, 30)],
                exit_episode_id=self.unassigned_episode.episode_id,
                gross_pnl=30,
            )
        )

        self.assertTrue(result.success, result.error)
        allocation = result.event.allocations[0]
        episode = self.episodes.get_episode(allocation.entry_episode_id, stock_code=CODE)
        self.assertEqual("UNASSIGNED", episode.ownership_kind)
        self.assertIsNone(episode.instance_id)

    def test_ownership_unresolved_is_explicit_and_preserved_in_stock_lifetime(self) -> None:
        result = self.ledger.append_event(
            self.event(
                [self.allocation("LEGACY-LOT", OWNERSHIP_UNRESOLVED, 5, 500, 20)],
                exit_episode_id=OWNERSHIP_UNRESOLVED,
                gross_pnl=20,
                fill_id=None,
                realization_id="LEGACY-REALIZATION",
            )
        )

        self.assertTrue(result.success, result.error)
        self.assertEqual(OWNERSHIP_UNRESOLVED, result.event.exit_episode_id)
        self.assertEqual(OWNERSHIP_UNRESOLVED, result.event.allocations[0].entry_episode_id)
        self.assertEqual(20, self.ledger.list_allocations(CODE)[0].gross_pnl)

    def test_reconciliation_mismatch_rejects_event_without_file(self) -> None:
        a1 = self.transition(self.assigned("INSTANCE-A", "GROUP-A"), T1)
        invalid_payloads = [
            self.event([self.allocation("LOT-A1", a1.episode_id, 4, 500, 100)], exit_episode_id=a1.episode_id),
            self.event([self.allocation("LOT-A1", a1.episode_id, 5, 499, 100)], exit_episode_id=a1.episode_id),
            self.event([self.allocation("LOT-A1", a1.episode_id, 5, 500, 99)], exit_episode_id=a1.episode_id),
            self.event(
                [self.allocation("LOT-A1", a1.episode_id, 5, 500, 100, net_pnl=90)],
                exit_episode_id=a1.episode_id,
                net_pnl=91,
            ),
        ]

        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                result = self.ledger.append_event(payload)
                self.assertFalse(result.success)
                self.assertFalse(self.ledger.document_path(CODE).exists())

    def test_missing_or_wrong_stock_episode_reference_is_rejected(self) -> None:
        other_code = "000660"
        other_repository = CanonicalAssignmentEpisodeRepository(self.root)
        other = other_repository.open_episode(
            other_code,
            AssignmentEpisodeTarget.unassigned(),
            started_at=T0,
            start_reason="STOCK_REGISTERED",
            source="TEST",
        ).opened_episode
        payload = self.event(
            [self.allocation("LOT-OTHER", other.episode_id, 5, 500, 100)],
            exit_episode_id=other.episode_id,
        )

        result = self.ledger.append_event(payload)

        self.assertFalse(result.success)
        self.assertIn("existing canonical episode", result.error)
        self.assertFalse(self.ledger.document_path(CODE).exists())

    def test_current_relation_changes_and_deleted_objects_do_not_mutate_event(self) -> None:
        a1 = self.transition(self.assigned("INSTANCE-A", "GROUP-A"), T1)
        b1 = self.transition(self.assigned("INSTANCE-B", "GROUP-B"), T2)
        created = self.ledger.append_event(
            self.event(
                [self.allocation("LOT-A1", a1.episode_id, 5, 500, 100)],
                exit_episode_id=b1.episode_id,
            )
        )
        before = self.ledger.document_path(CODE).read_bytes()
        self.transition(AssignmentEpisodeTarget.unassigned(), T3)
        instance_dir = self.root / "routine_instances" / "INSTANCE-A"
        group_dir = self.root / "groups" / "GROUP-A"
        instance_dir.mkdir(parents=True)
        group_dir.mkdir(parents=True)
        instance_dir.rmdir()
        group_dir.rmdir()

        reloaded = CanonicalStockPerformanceLedgerRepository(self.root)
        persisted = reloaded.get_event(created.event.performance_event_id)

        self.assertEqual(before, self.ledger.document_path(CODE).read_bytes())
        self.assertEqual(a1.episode_id, persisted.allocations[0].entry_episode_id)
        self.assertEqual(b1.episode_id, persisted.exit_episode_id)

    def test_atomic_replace_failure_preserves_existing_event_and_removes_temp(self) -> None:
        a1 = self.transition(self.assigned("INSTANCE-A", "GROUP-A"), T1)
        first = self.ledger.append_event(
            self.event(
                [self.allocation("LOT-A1", a1.episode_id, 5, 500, 100)],
                exit_episode_id=a1.episode_id,
            )
        )
        before = self.ledger.document_path(CODE).read_bytes()
        second_payload = self.event(
            [self.allocation("LOT-A2", a1.episode_id, 5, 500, 50)],
            exit_episode_id=a1.episode_id,
            gross_pnl=50,
            execution_identity="EXEC-2",
        )

        with patch("performance_ledger_repository.os.replace", side_effect=OSError("injected failure")):
            result = self.ledger.append_event(second_payload)

        self.assertFalse(result.success)
        self.assertEqual(before, self.ledger.document_path(CODE).read_bytes())
        self.assertEqual(first.event, self.ledger.list_events(CODE)[0])
        self.assertFalse(any(self.ledger.document_path(CODE).parent.glob("*.tmp")))

    def test_document_schema_and_reader_reject_duplicate_key(self) -> None:
        a1 = self.transition(self.assigned("INSTANCE-A", "GROUP-A"), T1)
        result = self.ledger.append_event(
            self.event(
                [self.allocation("LOT-A1", a1.episode_id, 5, 500, 100)],
                exit_episode_id=a1.episode_id,
            )
        )
        path = self.ledger.document_path(CODE)
        document = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual("1.0", document["schema_version"])
        self.assertEqual(CODE, document["stock_code"])
        self.assertEqual("ENTRY_EPISODE", document["events"][0]["canonical_owner_policy"])
        document["events"].append(json.loads(json.dumps(document["events"][0])))
        document["events"][1]["performance_event_id"] = "a9b93714-6957-4708-83db-438266288fed"
        path.write_text(json.dumps(document), encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "duplicate performance_event_key"):
            self.ledger.list_events(CODE)
        self.assertIsNotNone(result.event.performance_event_key)

    def test_stable_key_normalizes_account_and_identity_case(self) -> None:
        first = canonical_performance_event_key(
            broker="kiwoom",
            account_number="1234-5678",
            trade_date="2026-08-23",
            broker_order_no=" order-1 ",
            execution_identity="exec-1",
        )
        second = canonical_performance_event_key(
            broker="KIWOOM",
            account_number="12345678",
            trade_date="2026-08-23",
            broker_order_no="ORDER-1",
            execution_identity="EXEC-1",
        )

        self.assertEqual(first, second)
        with self.assertRaisesRegex(ValueError, "execution_identity"):
            canonical_performance_event_key(
                broker="KIWOOM",
                account_number="12345678",
                trade_date="2026-08-23",
                broker_order_no="ORDER-1",
                execution_identity="",
            )

    def test_append_does_not_touch_existing_runtime_stock_or_episode_files(self) -> None:
        a1 = self.transition(self.assigned("INSTANCE-A", "GROUP-A"), T1)
        stock_file = self.root / "stocks" / "005930_Sample" / "orders.json"
        runtime_file = self.root / "runtime" / "realized_pnl.json"
        stock_file.parent.mkdir(parents=True)
        runtime_file.parent.mkdir(parents=True)
        stock_file.write_text('{"orders": []}\n', encoding="utf-8")
        runtime_file.write_text('{"realizations": []}\n', encoding="utf-8")
        episode_file = self.episodes.document_path(CODE)
        before = (stock_file.read_bytes(), runtime_file.read_bytes(), episode_file.read_bytes())

        result = self.ledger.append_event(
            self.event(
                [self.allocation("LOT-A1", a1.episode_id, 5, 500, 100)],
                exit_episode_id=a1.episode_id,
            )
        )

        self.assertTrue(result.success, result.error)
        self.assertEqual(before, (stock_file.read_bytes(), runtime_file.read_bytes(), episode_file.read_bytes()))
        self.assertTrue(self.ledger.document_path(CODE).exists())


if __name__ == "__main__":
    unittest.main()
