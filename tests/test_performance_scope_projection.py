from __future__ import annotations

from datetime import datetime, timezone, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from assignment_episode_repository import AssignmentEpisodeTarget, CanonicalAssignmentEpisodeRepository
from performance_aggregator import CanonicalPerformanceAggregator
from performance_ledger_repository import (
    CANONICAL_OWNER_POLICY,
    OWNERSHIP_UNRESOLVED,
    CanonicalStockPerformanceLedgerRepository,
)
from performance_scope_projection import (
    PerformanceLevel,
    PerformanceScope,
    PerformanceScopeProjection,
    build_current_performance_relations,
)


CODE = "005930"
T0 = "2026-08-23T09:00:00+09:00"
T1 = "2026-08-23T09:10:00+09:00"
T2 = "2026-08-23T09:20:00+09:00"
T3 = "2026-08-23T09:30:00+09:00"


class PerformanceScopeProjectionTests(unittest.TestCase):
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
        self.sequence = 0

    @staticmethod
    def target(instance_id: str, group_id: str, instance_name: str, group_name: str):
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

    def append(self, episode_id: str, gross: int, *, entry_episode_id: str | None = None) -> None:
        self.sequence += 1
        owner_id = entry_episode_id or episode_id
        result = self.ledger.append_event(
            {
                "stock_code": CODE,
                "broker": "KIWOOM",
                "account_number": "12345678",
                "trade_date": "2026-08-23",
                "broker_order_no": f"ORDER-{self.sequence}",
                "execution_identity": f"EXEC-{self.sequence}",
                "fill_id": f"FILL-{self.sequence}",
                "realization_id": f"REALIZATION-{self.sequence}",
                "realized_at": f"2026-08-23T10:{self.sequence:02d}:00+09:00",
                "quantity": 1,
                "realized_cost_basis": 100,
                "gross_pnl": gross,
                "fee": None,
                "tax": None,
                "net_pnl": None,
                "exit_episode_id": episode_id,
                "canonical_owner_policy": CANONICAL_OWNER_POLICY,
                "allocations": [
                    {
                        "entry_lot_id": f"LOT-{self.sequence}",
                        "entry_episode_id": owner_id,
                        "quantity": 1,
                        "cost_basis": 100,
                        "gross_pnl": gross,
                        "net_pnl": None,
                    }
                ],
            }
        )
        self.assertTrue(result.success, result.error)

    @staticmethod
    def stock(instance_id: str = "A"):
        return SimpleNamespace(
            code=CODE,
            name="Samsung",
            assigned_routine_instance_id=instance_id,
            routine_instance_name=instance_id,
        )

    @staticmethod
    def instance(instance_id: str, group_id: str, name: str | None = None):
        return SimpleNamespace(instance_id=instance_id, group_id=group_id, display_name=name or instance_id)

    @staticmethod
    def group(group_id: str, name: str | None = None):
        return SimpleNamespace(group_id=group_id, display_name=name or group_id)

    def projection(self, stocks, instances, groups=()):
        aggregator = CanonicalPerformanceAggregator(self.ledger, self.episodes)
        relations = build_current_performance_relations(stocks, instances, groups)
        return PerformanceScopeProjection(aggregator, relations)

    def a_to_b_to_a_fixture(self):
        a1 = self.transition(self.target("A", "G1", "Alpha Old", "Group One"), T1)
        b1 = self.transition(self.target("B", "G1", "Beta", "Group One"), T2)
        a2 = self.transition(self.target("A", "G2", "Alpha New", "Group Two"), T3)
        self.append(a1.episode_id, 100)
        self.append(b1.episode_id, 50)
        self.append(a2.episode_id, 30)
        projection = self.projection(
            [self.stock("A")],
            [self.instance("A", "G2", "Alpha Current"), self.instance("B", "G1", "Beta")],
            [self.group("G1", "Group One"), self.group("G2", "Group Two")],
        )
        return a1, b1, a2, projection

    def test_stock_visibility_is_scoped_but_numeric_value_is_always_lifetime(self) -> None:
        _, _, _, projection = self.a_to_b_to_a_fixture()

        all_row = projection.project_stocks(PerformanceScope.ALL)[0]
        current_row = projection.project_stocks(PerformanceScope.CURRENT)[0]

        self.assertEqual(180, all_row.lifetime.gross_pnl)
        self.assertEqual(all_row.lifetime, current_row.lifetime)
        self.assertEqual((), projection.project_stocks(PerformanceScope.PAST))
        self.assertEqual("STOCK_LIFETIME", current_row.metric_scope)
        self.assertTrue(current_row.current_relation_consistency.consistent)

    def test_a_to_b_to_a_parent_scope_uses_open_and_closed_episodes(self) -> None:
        _, _, _, projection = self.a_to_b_to_a_fixture()

        current = {row.instance_id: row.aggregate.gross_pnl for row in projection.project_instances("CURRENT")}
        past = {row.instance_id: row.aggregate.gross_pnl for row in projection.project_instances("PAST")}
        all_rows = {row.instance_id: row.aggregate.gross_pnl for row in projection.project_instances("ALL")}

        self.assertEqual({"A": 30}, current)
        self.assertEqual({"A": 100, "B": 50}, past)
        self.assertEqual({"A": 130, "B": 50}, all_rows)
        self.assertEqual("OPEN_ASSIGNED_EPISODES", projection.project_instances("CURRENT")[0].metric_scope)

    def test_group_move_keeps_historical_pnl_in_original_group(self) -> None:
        _, _, _, projection = self.a_to_b_to_a_fixture()

        current = {row.group_id: row.aggregate.gross_pnl for row in projection.project_groups("CURRENT")}
        past = {row.group_id: row.aggregate.gross_pnl for row in projection.project_groups("PAST")}
        all_rows = {row.group_id: row.aggregate.gross_pnl for row in projection.project_groups("ALL")}

        self.assertEqual({"G2": 30}, current)
        self.assertEqual({"G1": 150}, past)
        self.assertEqual({"G1": 150, "G2": 30}, all_rows)

    def test_currently_unassigned_stock_is_hidden_current_but_kept_past_and_all(self) -> None:
        a1 = self.transition(self.target("A", "G1", "Alpha", "Group One"), T1)
        unassigned = self.transition(AssignmentEpisodeTarget.unassigned(), T2)
        self.append(unassigned.episode_id, 100, entry_episode_id=a1.episode_id)
        projection = self.projection(
            [self.stock("")],
            [self.instance("A", "G1")],
            [self.group("G1")],
        )

        self.assertEqual((), projection.project_stocks("CURRENT"))
        self.assertEqual(100, projection.project_stocks("PAST")[0].lifetime.gross_pnl)
        self.assertEqual(100, projection.project_stocks("ALL")[0].lifetime.gross_pnl)
        self.assertEqual({"A": 100}, {row.instance_id: row.aggregate.gross_pnl for row in projection.project_instances("PAST")})

    def test_deleted_parent_files_are_historical_rows_not_current_objects(self) -> None:
        a1 = self.transition(self.target("DELETED-A", "DELETED-G", "Old A", "Old Group"), T1)
        self.transition(AssignmentEpisodeTarget.unassigned(), T2)
        self.append(self.u1.episode_id, 100, entry_episode_id=a1.episode_id)
        projection = self.projection([self.stock("")], [], [])

        self.assertEqual((), projection.project_instances("CURRENT"))
        past_instance = projection.project_instances("PAST")[0]
        past_group = projection.project_groups("PAST")[0]
        self.assertEqual("DELETED-A", past_instance.instance_id)
        self.assertEqual(("Old A",), past_instance.observed_instance_name_snapshots)
        self.assertEqual("DELETED-G", past_group.group_id)
        self.assertEqual(100, past_group.aggregate.gross_pnl)

    def test_unassigned_and_unresolved_affect_stock_only_and_diagnostic(self) -> None:
        a1 = self.transition(self.target("A", "G1", "Alpha", "Group One"), T1)
        self.append(a1.episode_id, 100)
        self.append(a1.episode_id, 20, entry_episode_id=self.u1.episode_id)
        self.append(a1.episode_id, 30, entry_episode_id=OWNERSHIP_UNRESOLVED)
        projection = self.projection(
            [self.stock("A")],
            [self.instance("A", "G1")],
            [self.group("G1")],
        )

        self.assertEqual(150, projection.project_stocks("ALL")[0].lifetime.gross_pnl)
        self.assertEqual(100, projection.project_instances("ALL")[0].aggregate.gross_pnl)
        self.assertEqual(100, projection.project_groups("ALL")[0].aggregate.gross_pnl)
        self.assertEqual((1, 30), projection.unresolved_summary("ALL"))

    def test_current_relation_mismatch_is_diagnostic_and_never_reassigned(self) -> None:
        a1 = self.transition(self.target("A", "G1", "Alpha", "Group One"), T1)
        self.append(a1.episode_id, 100)
        projection = self.projection(
            [self.stock("B")],
            [self.instance("A", "G1"), self.instance("B", "G2")],
            [self.group("G1"), self.group("G2")],
        )

        stock = projection.project_stocks("CURRENT")[0]
        current_instance = projection.project_instances("CURRENT")[0]
        current_group = projection.project_groups("CURRENT")[0]
        reconciliation = projection.reconcile_scope("CURRENT")

        self.assertFalse(stock.current_relation_consistency.consistent)
        self.assertIn("INSTANCE_ID_MISMATCH", stock.current_relation_consistency.reasons)
        self.assertIn("GROUP_ID_MISMATCH", stock.current_relation_consistency.reasons)
        self.assertEqual("B", current_instance.instance_id)
        self.assertEqual(0, current_instance.aggregate.gross_pnl)
        self.assertEqual("G2", current_group.group_id)
        self.assertEqual(0, current_group.aggregate.gross_pnl)
        self.assertFalse(reconciliation.instance_reconciled)
        self.assertFalse(reconciliation.group_reconciled)

    def test_zero_event_current_rows_remain_but_assignment_only_history_is_hidden(self) -> None:
        a1 = self.transition(self.target("A", "G1", "Alpha", "Group One"), T1)
        self.transition(self.target("B", "G2", "Beta", "Group Two"), T2)
        projection = self.projection(
            [self.stock("B")],
            [self.instance("A", "G1"), self.instance("B", "G2")],
            [self.group("G1"), self.group("G2")],
        )

        self.assertEqual(0, projection.project_stocks("CURRENT")[0].lifetime.gross_pnl)
        self.assertEqual({"B": 0}, {row.instance_id: row.aggregate.gross_pnl for row in projection.project_instances("CURRENT")})
        self.assertEqual((), projection.project_instances("PAST"))
        self.assertEqual((), projection.project_groups("PAST"))
        self.assertEqual({"B"}, {row.instance_id for row in projection.project_instances("ALL")})
        self.assertEqual({"G2"}, {row.group_id for row in projection.project_groups("ALL")})
        self.assertEqual(
            "NO_CANONICAL_PERFORMANCE_EVENTS",
            projection.project_instances("CURRENT")[0].performance_absence_reason,
        )
        self.assertEqual(0, projection.aggregator.aggregate_episode(a1.episode_id).gross_pnl)

    def test_unassigned_closed_episode_without_performance_is_not_a_stock_row(self) -> None:
        self.transition(self.target("A", "G1", "Alpha", "Group One"), T1)
        self.transition(AssignmentEpisodeTarget.unassigned(), T2)
        projection = self.projection(
            [self.stock("")],
            [self.instance("A", "G1")],
            [self.group("G1")],
        )

        self.assertEqual((), projection.project_stocks("CURRENT"))
        self.assertEqual((), projection.project_stocks("PAST"))
        self.assertEqual((), projection.project_stocks("ALL"))

    def test_ledger_only_stock_is_known_in_all_without_fake_parent_or_current_visibility(self) -> None:
        other_code = "000660"
        result = self.ledger.append_event(
            {
                "stock_code": other_code,
                "broker": "KIWOOM",
                "account_number": "12345678",
                "trade_date": "2026-08-23",
                "broker_order_no": "ORDER-LEDGER-ONLY",
                "execution_identity": "EXEC-LEDGER-ONLY",
                "fill_id": None,
                "realization_id": "LEGACY-ONLY",
                "realized_at": "2026-08-23T11:00:00+09:00",
                "quantity": 1,
                "realized_cost_basis": 100,
                "gross_pnl": 40,
                "fee": None,
                "tax": None,
                "net_pnl": None,
                "exit_episode_id": OWNERSHIP_UNRESOLVED,
                "canonical_owner_policy": CANONICAL_OWNER_POLICY,
                "allocations": [
                    {
                        "entry_lot_id": "LEGACY-ONLY-LOT",
                        "entry_episode_id": OWNERSHIP_UNRESOLVED,
                        "quantity": 1,
                        "cost_basis": 100,
                        "gross_pnl": 40,
                        "net_pnl": None,
                    }
                ],
            }
        )
        self.assertTrue(result.success, result.error)
        projection = self.projection([], [], [])

        all_rows = {row.stock_code: row for row in projection.project_stocks("ALL")}

        self.assertIn(other_code, all_rows)
        self.assertEqual(40, all_rows[other_code].lifetime.gross_pnl)
        self.assertEqual(("PERFORMANCE_LEDGER",), all_rows[other_code].visibility_reasons)
        self.assertEqual((), projection.project_stocks("CURRENT"))
        self.assertEqual((other_code,), tuple(row.stock_code for row in projection.project_stocks("PAST")))
        self.assertEqual((), projection.project_instances("ALL"))
        self.assertEqual((), projection.project_groups("ALL"))

    def test_unresolved_event_is_stock_evidence_but_not_parent_evidence(self) -> None:
        a1 = self.transition(self.target("A", "G1", "Alpha", "Group One"), T1)
        self.append(a1.episode_id, 0, entry_episode_id=OWNERSHIP_UNRESOLVED)
        projection = self.projection(
            [self.stock("A")],
            [self.instance("A", "G1")],
            [self.group("G1")],
        )

        self.assertEqual((CODE,), tuple(row.stock_code for row in projection.project_stocks("ALL")))
        instance = projection.project_instances("ALL")[0]
        group = projection.project_groups("ALL")[0]
        self.assertEqual(0, instance.aggregate.event_count)
        self.assertEqual("NO_RESOLVED_PERFORMANCE_OWNERSHIP", instance.performance_absence_reason)
        self.assertEqual("NO_RESOLVED_PERFORMANCE_OWNERSHIP", group.performance_absence_reason)

    def test_current_parent_reports_performance_outside_current_scope(self) -> None:
        a1 = self.transition(self.target("A", "G1", "Alpha", "Group One"), T1)
        self.append(a1.episode_id, 100)
        self.transition(AssignmentEpisodeTarget.unassigned(), T2)
        self.transition(self.target("A", "G1", "Alpha", "Group One"), T3)
        projection = self.projection(
            [self.stock("A")],
            [self.instance("A", "G1")],
            [self.group("G1")],
        )

        current = projection.project_instances("CURRENT")[0]
        self.assertEqual(0, current.aggregate.event_count)
        self.assertEqual("NO_PERFORMANCE_IN_SCOPE", current.performance_absence_reason)
        self.assertIsNone(projection.project_instances("ALL")[0].performance_absence_reason)

    def test_scope_reconciliation_and_generic_project_api(self) -> None:
        _, _, _, projection = self.a_to_b_to_a_fixture()

        reconciliations = {scope: projection.reconcile_scope(scope) for scope in PerformanceScope}
        all_gross = reconciliations[PerformanceScope.ALL].expected_assigned_episode_gross_pnl
        current_gross = reconciliations[PerformanceScope.CURRENT].expected_assigned_episode_gross_pnl
        past_gross = reconciliations[PerformanceScope.PAST].expected_assigned_episode_gross_pnl

        self.assertEqual(180, all_gross)
        self.assertEqual(30, current_gross)
        self.assertEqual(150, past_gross)
        self.assertEqual(all_gross, current_gross + past_gross)
        self.assertTrue(all(result.instance_reconciled and result.group_reconciled for result in reconciliations.values()))
        self.assertEqual(projection.project_stocks("ALL"), projection.project("ALL", PerformanceLevel.STOCK))
        self.assertEqual(projection.project_instances("ALL"), projection.project("ALL", "INSTANCE"))
        self.assertEqual(projection.project_groups("ALL"), projection.project("ALL", "GROUP"))


if __name__ == "__main__":
    unittest.main()
