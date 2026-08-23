from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from assignment_episode_repository import (
    AssignmentEpisodeTarget,
    CanonicalAssignmentEpisodeRepository,
)
from performance_aggregator import CanonicalPerformanceAggregator
from performance_ledger_repository import (
    CANONICAL_OWNER_POLICY,
    OWNERSHIP_UNRESOLVED,
    CanonicalStockPerformanceLedgerRepository,
)


CODE = "005930"
T0 = "2026-08-23T09:00:00+09:00"
T1 = "2026-08-23T09:10:00+09:00"
T2 = "2026-08-23T09:20:00+09:00"
T3 = "2026-08-23T09:30:00+09:00"


class PerformanceAggregatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.episodes = CanonicalAssignmentEpisodeRepository(self.root)
        self.ledger = CanonicalStockPerformanceLedgerRepository(
            self.root,
            episode_repository=self.episodes,
            now_factory=lambda: datetime(2026, 8, 23, 12, 0, tzinfo=timezone(timedelta(hours=9))),
        )
        opened = self.episodes.open_episode(
            CODE,
            AssignmentEpisodeTarget.unassigned(),
            started_at=T0,
            start_reason="STOCK_REGISTERED",
            source="TEST",
        )
        self.assertTrue(opened.success, opened.error)
        self.u1 = opened.opened_episode
        self.event_sequence = 0

    @staticmethod
    def target(instance_id: str, group_id: str | None, instance_name: str, group_name: str | None):
        return AssignmentEpisodeTarget.assigned(
            instance_id=instance_id,
            group_id=group_id,
            definition_id="indicator_follow",
            instance_name_snapshot=instance_name,
            group_name_snapshot=group_name,
        )

    def transition(self, target: AssignmentEpisodeTarget, at: str):
        result = self.episodes.transition_episode(
            CODE,
            target,
            changed_at=at,
            start_reason="ASSIGNMENT_CHANGED",
            end_reason="ASSIGNMENT_CHANGED",
            source="TEST",
        )
        self.assertTrue(result.success, result.error)
        return result.opened_episode

    def append(
        self,
        allocations: list[dict[str, object]],
        *,
        exit_episode_id: str,
        gross: int,
        quantity: int = 1,
        cost_basis: int = 100,
        net: int | None = None,
        trade_date: str = "2026-08-23",
    ):
        self.event_sequence += 1
        minute = self.event_sequence
        result = self.ledger.append_event(
            {
                "stock_code": CODE,
                "broker": "KIWOOM",
                "account_number": "12345678",
                "trade_date": trade_date,
                "broker_order_no": f"ORDER-{self.event_sequence}",
                "execution_identity": f"EXEC-{self.event_sequence}",
                "fill_id": f"FILL-{self.event_sequence}",
                "realization_id": f"REALIZATION-{self.event_sequence}",
                "realized_at": f"{trade_date}T10:{minute:02d}:00+09:00",
                "quantity": quantity,
                "realized_cost_basis": cost_basis,
                "gross_pnl": gross,
                "fee": None,
                "tax": None,
                "net_pnl": net,
                "exit_episode_id": exit_episode_id,
                "canonical_owner_policy": CANONICAL_OWNER_POLICY,
                "allocations": allocations,
            }
        )
        self.assertTrue(result.success, result.error)
        return result.event

    @staticmethod
    def allocation(
        lot_id: str,
        episode_id: str,
        gross: int,
        *,
        quantity: int = 1,
        cost_basis: int = 100,
        net: int | None = None,
    ) -> dict[str, object]:
        return {
            "entry_lot_id": lot_id,
            "entry_episode_id": episode_id,
            "quantity": quantity,
            "cost_basis": cost_basis,
            "gross_pnl": gross,
            "net_pnl": net,
        }

    def comprehensive_fixture(self):
        a1 = self.transition(self.target("A", "G1", "Old", "Group One"), T1)
        b1 = self.transition(self.target("B", "G1", "Beta", "Group One"), T2)
        a2 = self.transition(self.target("A", "G2", "New", "Group Two"), T3)
        self.append([self.allocation("A1-LOT", a1.episode_id, 100, net=80)], exit_episode_id=a1.episode_id, gross=100, net=80)
        self.append([self.allocation("B1-LOT", b1.episode_id, 50)], exit_episode_id=b1.episode_id, gross=50)
        self.append([self.allocation("A2-LOT", a2.episode_id, 30, net=25)], exit_episode_id=a2.episode_id, gross=30, net=25)
        self.append([self.allocation("U1-LOT", self.u1.episode_id, 20)], exit_episode_id=a2.episode_id, gross=20)
        self.append([self.allocation("LEGACY-LOT", OWNERSHIP_UNRESOLVED, 30)], exit_episode_id=a2.episode_id, gross=30)
        return a1, b1, a2

    def test_a_to_b_to_a_stock_episode_instance_and_group_totals(self) -> None:
        a1, b1, a2 = self.comprehensive_fixture()
        aggregator = CanonicalPerformanceAggregator(self.ledger, self.episodes)

        stock = aggregator.aggregate_stock_lifetime(CODE)
        episode_values = [aggregator.aggregate_episode(item.episode_id).gross_pnl for item in (a1, b1, a2)]
        instance_a = aggregator.aggregate_instance("A")
        instance_b = aggregator.aggregate_instance("B")
        group_g1 = aggregator.aggregate_group("G1")
        group_g2 = aggregator.aggregate_group("G2")

        self.assertEqual(230, stock.gross_pnl)
        self.assertEqual([100, 50, 30], episode_values)
        self.assertEqual(130, instance_a.gross_pnl)
        self.assertEqual(50, instance_b.gross_pnl)
        self.assertEqual(150, group_g1.gross_pnl)
        self.assertEqual(30, group_g2.gross_pnl)

    def test_stock_lifetime_breakdown_keeps_unassigned_and_unresolved(self) -> None:
        self.comprehensive_fixture()
        stock = CanonicalPerformanceAggregator(self.ledger, self.episodes).aggregate_stock_lifetime(CODE)

        self.assertEqual(5, stock.performance_event_count)
        self.assertEqual(5, stock.allocation_count)
        self.assertEqual(3, stock.resolved_allocation_count)
        self.assertEqual(1, stock.unassigned_allocation_count)
        self.assertEqual(1, stock.unresolved_allocation_count)
        self.assertEqual(180, stock.resolved.gross_pnl)
        self.assertEqual(20, stock.unassigned.gross_pnl)
        self.assertEqual(30, stock.unresolved.gross_pnl)

    def test_unassigned_episode_can_be_queried_but_is_excluded_from_parents(self) -> None:
        self.comprehensive_fixture()
        aggregator = CanonicalPerformanceAggregator(self.ledger, self.episodes)

        unassigned = aggregator.aggregate_episode(self.u1.episode_id)
        parent_total = sum(result.gross_pnl for result in aggregator.aggregate_all_instances())

        self.assertEqual("UNASSIGNED", unassigned.ownership_kind)
        self.assertEqual(20, unassigned.gross_pnl)
        self.assertEqual(180, parent_total)

    def test_multi_episode_sell_counts_event_once_and_allocates_parent_pnl(self) -> None:
        a1 = self.transition(self.target("A", "G1", "Alpha", "Group One"), T1)
        b1 = self.transition(self.target("B", "G1", "Beta", "Group One"), T2)
        self.append(
            [
                self.allocation("A1-LOT", a1.episode_id, 60, quantity=3, cost_basis=300),
                self.allocation("B1-LOT", b1.episode_id, 40, quantity=2, cost_basis=200),
            ],
            exit_episode_id=b1.episode_id,
            gross=100,
            quantity=5,
            cost_basis=500,
        )
        aggregator = CanonicalPerformanceAggregator(self.ledger, self.episodes)

        stock = aggregator.aggregate_stock_lifetime(CODE)

        self.assertEqual(1, stock.performance_event_count)
        self.assertEqual(2, stock.allocation_count)
        self.assertEqual(100, stock.gross_pnl)
        self.assertEqual(60, aggregator.aggregate_episode(a1.episode_id).gross_pnl)
        self.assertEqual(40, aggregator.aggregate_episode(b1.episode_id).gross_pnl)
        self.assertEqual(100, aggregator.aggregate_group("G1").gross_pnl)

    def test_net_completeness_never_exposes_partial_sum_as_complete_net(self) -> None:
        a1 = self.transition(self.target("A", "G1", "Alpha", "Group One"), T1)
        self.append([self.allocation("KNOWN", a1.episode_id, 100, net=80)], exit_episode_id=a1.episode_id, gross=100, net=80)
        self.append([self.allocation("UNKNOWN", a1.episode_id, 50)], exit_episode_id=a1.episode_id, gross=50)
        aggregator = CanonicalPerformanceAggregator(self.ledger, self.episodes)

        stock = aggregator.aggregate_stock_lifetime(CODE)
        instance = aggregator.aggregate_instance("A")

        self.assertEqual(80, stock.net_pnl_known_sum)
        self.assertIsNone(stock.net_pnl)
        self.assertFalse(stock.net_pnl_complete)
        self.assertEqual(80, instance.net_pnl_known_sum)
        self.assertIsNone(instance.net_pnl)
        self.assertFalse(instance.net_pnl_complete)

    def test_snapshot_names_group_move_and_deleted_objects_preserve_history(self) -> None:
        self.comprehensive_fixture()
        instance_path = self.root / "routine_instances" / "A"
        group_path = self.root / "groups" / "G1"
        instance_path.mkdir(parents=True)
        group_path.mkdir(parents=True)
        instance_path.rmdir()
        group_path.rmdir()
        aggregator = CanonicalPerformanceAggregator(self.ledger, self.episodes)

        instance = aggregator.aggregate_instance("A")
        g1 = aggregator.aggregate_group("G1")
        g2 = aggregator.aggregate_group("G2")

        self.assertEqual(("New", "Old"), instance.observed_instance_name_snapshots)
        self.assertEqual(("G1", "G2"), instance.observed_group_ids)
        self.assertEqual(("Group One",), g1.observed_group_name_snapshots)
        self.assertEqual(("Group Two",), g2.observed_group_name_snapshots)
        self.assertEqual(100, g1.metrics.gross_pnl - aggregator.aggregate_instance("B").gross_pnl)
        self.assertEqual(30, g2.gross_pnl)

    def test_current_assignment_and_orders_or_realized_files_are_not_sources(self) -> None:
        a1 = self.transition(self.target("A", "G1", "Alpha", "Group One"), T1)
        self.append([self.allocation("A1", a1.episode_id, 100)], exit_episode_id=a1.episode_id, gross=100)
        stock_dir = self.root / "stocks" / "005930_Sample"
        stock_dir.mkdir(parents=True)
        (stock_dir / "config.json").write_text(json.dumps({"assigned_routine_instance_id": "B"}), encoding="utf-8")
        (stock_dir / "orders.json").write_text(json.dumps({"profit": 999999}), encoding="utf-8")
        runtime = self.root / "runtime"
        runtime.mkdir()
        (runtime / "realized_pnl.json").write_text(json.dumps({"gross_pnl": 888888}), encoding="utf-8")

        aggregator = CanonicalPerformanceAggregator(self.ledger, self.episodes)

        self.assertEqual(100, aggregator.aggregate_stock_lifetime(CODE).gross_pnl)
        self.assertEqual(100, aggregator.aggregate_instance("A").gross_pnl)
        self.assertIsNone(aggregator.aggregate_instance("B"))

    def test_reconciliation_exposes_unassigned_unresolved_and_missing_group(self) -> None:
        a1 = self.transition(self.target("A", None, "Alpha", None), T1)
        self.append([self.allocation("A1", a1.episode_id, 100)], exit_episode_id=a1.episode_id, gross=100)
        self.append([self.allocation("U1", self.u1.episode_id, 20)], exit_episode_id=a1.episode_id, gross=20)
        self.append([self.allocation("X1", OWNERSHIP_UNRESOLVED, 30)], exit_episode_id=a1.episode_id, gross=30)

        result = CanonicalPerformanceAggregator(self.ledger, self.episodes).reconciliation()

        self.assertEqual(150, result.stock_lifetime_gross_pnl)
        self.assertEqual(100, result.assigned_episode_gross_pnl)
        self.assertEqual(20, result.unassigned_episode_gross_pnl)
        self.assertEqual(30, result.unresolved_gross_pnl)
        self.assertEqual(100, result.instance_gross_pnl)
        self.assertEqual(0, result.group_gross_pnl)
        self.assertEqual(100, result.assigned_without_group_gross_pnl)
        self.assertTrue(result.stock_reconciled)
        self.assertTrue(result.instance_reconciled)
        self.assertTrue(result.group_reconciled)

    def test_date_facts_are_raw_and_stable_after_restart(self) -> None:
        a1 = self.transition(self.target("A", "G1", "Alpha", "Group One"), T1)
        self.append([self.allocation("DAY1", a1.episode_id, 100)], exit_episode_id=a1.episode_id, gross=100)
        self.append([self.allocation("DAY2", a1.episode_id, 50)], exit_episode_id=a1.episode_id, gross=50, trade_date="2026-08-24")
        first = CanonicalPerformanceAggregator(self.ledger, self.episodes).aggregate_instance("A")
        restarted = CanonicalPerformanceAggregator(
            CanonicalStockPerformanceLedgerRepository(self.root),
            CanonicalAssignmentEpisodeRepository(self.root),
        ).aggregate_instance("A")

        self.assertEqual(first, restarted)
        self.assertEqual(("2026-08-23", "2026-08-24"), first.unique_realized_trade_dates)
        self.assertEqual(2, first.realized_trade_date_count)
        self.assertTrue(first.realized_date_complete)
        self.assertLess(first.first_realized_at, first.last_realized_at)

    def test_profit_loss_raw_facts_are_ledger_reproducible(self) -> None:
        a1 = self.transition(self.target("A", "G1", "Alpha", "Group One"), T1)
        self.append([self.allocation("WIN", a1.episode_id, 100)], exit_episode_id=a1.episode_id, gross=100)
        self.append([self.allocation("LOSS", a1.episode_id, -40)], exit_episode_id=a1.episode_id, gross=-40)
        self.append([self.allocation("EVEN", a1.episode_id, 0)], exit_episode_id=a1.episode_id, gross=0)

        result = CanonicalPerformanceAggregator(self.ledger, self.episodes).aggregate_instance("A")

        self.assertEqual(60, result.gross_pnl)
        self.assertEqual(100, result.gross_profit_sum)
        self.assertEqual(40, result.gross_loss_abs_sum)

    def test_period_facts_group_net_and_cost_by_stock_and_trade_date(self) -> None:
        a1 = self.transition(self.target("A", "G1", "Alpha", "Group One"), T1)
        self.append(
            [self.allocation("DAY1-A", a1.episode_id, 10, cost_basis=100, net=10)],
            exit_episode_id=a1.episode_id,
            gross=10,
            cost_basis=100,
            net=10,
        )
        self.append(
            [self.allocation("DAY1-B", a1.episode_id, 0, cost_basis=10000, net=0)],
            exit_episode_id=a1.episode_id,
            gross=0,
            cost_basis=10000,
            net=0,
        )

        aggregator = CanonicalPerformanceAggregator(self.ledger, self.episodes)
        stock_fact = aggregator.aggregate_stock_lifetime(CODE).period_performance_facts[0]
        parent_fact = aggregator.aggregate_instance("A").period_performance_facts[0]

        self.assertEqual(1, aggregator.aggregate_instance("A").realized_stock_trade_date_count)
        self.assertEqual(10100, stock_fact.realized_cost_basis)
        self.assertEqual(10, stock_fact.net_pnl)
        self.assertEqual(stock_fact, parent_fact)

    def test_one_snapshot_reads_each_document_once_and_reuses_it_for_all_aggregates(self) -> None:
        self.comprehensive_fixture()
        with patch.object(self.episodes, "get_episode", side_effect=AssertionError("repeated Episode lookup")):
            aggregator = CanonicalPerformanceAggregator(self.ledger, self.episodes)
        self.assertEqual(1, aggregator.snapshot.episode_document_reads)
        self.assertEqual(1, aggregator.snapshot.ledger_document_reads)

        before = (
            aggregator.snapshot.episode_document_reads,
            aggregator.snapshot.ledger_document_reads,
        )
        aggregator.aggregate_stock_lifetime(CODE)
        aggregator.aggregate_episode(self.u1.episode_id)
        aggregator.aggregate_instance("A")
        aggregator.aggregate_group("G1")
        aggregator.reconciliation()

        self.assertEqual(before, (1, 1))


if __name__ == "__main__":
    unittest.main()
