from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from assignment_episode_repository import AssignmentEpisodeTarget, CanonicalAssignmentEpisodeRepository
from performance_aggregator import (
    AggregateMetrics,
    CanonicalPerformanceAggregator,
    PeriodPerformanceFact,
)
from performance_ledger_repository import CANONICAL_OWNER_POLICY, CanonicalStockPerformanceLedgerRepository
from performance_metrics import (
    CanonicalPerformanceMetricEngine,
    MetricStatus,
    canonical_metric_sort_key,
)
from performance_scope_projection import (
    PerformanceScope,
    PerformanceScopeProjection,
    build_current_performance_relations,
)


class CanonicalPerformanceMetricTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = CanonicalPerformanceMetricEngine()

    @staticmethod
    def aggregate(**overrides: object) -> AggregateMetrics:
        values = {
            "event_count": 1,
            "allocation_count": 1,
            "realized_quantity": 1,
            "realized_cost_basis": 1000,
            "gross_pnl": 100,
            "gross_profit_sum": 100,
            "gross_loss_abs_sum": 0,
            "net_pnl": 90,
            "net_pnl_known_sum": 90,
            "net_pnl_complete": True,
            "first_realized_at": "2026-08-23T10:00:00+09:00",
            "last_realized_at": "2026-08-23T10:00:00+09:00",
            "unique_realized_trade_dates": ("2026-08-23",),
            "realized_trade_date_count": 1,
            "realized_stock_trade_date_count": 1,
            "realized_date_complete": True,
            "period_performance_facts": (
                PeriodPerformanceFact(
                    stock_code="005930",
                    trade_date="2026-08-23",
                    realized_cost_basis=1000,
                    net_pnl=90,
                    net_pnl_known_sum=90,
                    net_pnl_complete=True,
                ),
            ),
        }
        values.update(overrides)
        return AggregateMetrics(**values)

    def test_confirmed_positive_metrics_use_complete_net_and_trade_dates(self) -> None:
        result = self.engine.calculate(self.aggregate())

        self.assertEqual(1, result.period.value)
        self.assertEqual(100, result.gross_profit_amount.value)
        self.assertEqual(90, result.profit_amount.value)
        self.assertEqual(9, result.profit_rate.value)
        self.assertEqual(90, result.average_amount.value)
        self.assertEqual(9, result.average_rate.value)
        self.assertEqual(MetricStatus.UNDEFINED, result.efficiency.status)

    def test_loss_and_real_zero_are_not_unavailable(self) -> None:
        loss = self.engine.calculate(
            self.aggregate(
                gross_pnl=-100,
                gross_profit_sum=0,
                gross_loss_abs_sum=100,
                net_pnl=-110,
                net_pnl_known_sum=-110,
                period_performance_facts=(
                    PeriodPerformanceFact("005930", "2026-08-23", 1000, -110, -110, True),
                ),
            )
        )
        zero = self.engine.calculate(
            self.aggregate(
                gross_pnl=0,
                gross_profit_sum=0,
                gross_loss_abs_sum=0,
                net_pnl=0,
                net_pnl_known_sum=0,
                period_performance_facts=(
                    PeriodPerformanceFact("005930", "2026-08-23", 1000, 0, 0, True),
                ),
            )
        )

        self.assertEqual(-110, loss.profit_amount.value)
        self.assertEqual(-11, loss.profit_rate.value)
        self.assertEqual(MetricStatus.VALID_ZERO, loss.efficiency.status)
        self.assertEqual(MetricStatus.VALID_ZERO, zero.profit_amount.status)
        self.assertEqual(MetricStatus.VALID_ZERO, zero.profit_rate.status)
        self.assertEqual(MetricStatus.VALID_ZERO, zero.average_amount.status)

    def test_no_event_and_zero_denominators_are_explicit(self) -> None:
        result = self.engine.calculate(
            self.aggregate(
                event_count=0,
                allocation_count=0,
                realized_quantity=0,
                realized_cost_basis=0,
                gross_pnl=0,
                gross_profit_sum=0,
                net_pnl=0,
                net_pnl_known_sum=0,
                first_realized_at=None,
                last_realized_at=None,
                unique_realized_trade_dates=(),
                realized_trade_date_count=0,
                realized_stock_trade_date_count=0,
                period_performance_facts=(),
            )
        )

        self.assertEqual(MetricStatus.VALID_ZERO, result.period.status)
        self.assertEqual(MetricStatus.VALID_ZERO, result.profit_amount.status)
        self.assertEqual(MetricStatus.UNDEFINED, result.profit_rate.status)
        self.assertEqual(MetricStatus.UNDEFINED, result.average_amount.status)
        self.assertEqual(MetricStatus.UNAVAILABLE, result.efficiency.status)
        self.assertEqual(MetricStatus.UNAVAILABLE, result.average_rate.status)

    def test_incomplete_net_and_date_are_not_partial_metrics(self) -> None:
        result = self.engine.calculate(
            self.aggregate(
                net_pnl=None,
                net_pnl_known_sum=40,
                net_pnl_complete=False,
                realized_date_complete=False,
                period_performance_facts=(
                    PeriodPerformanceFact("005930", "2026-08-23", 1000, None, 40, False),
                ),
            )
        )

        self.assertEqual(100, result.gross_profit_amount.value)
        self.assertEqual(MetricStatus.INCOMPLETE, result.net_profit_amount.status)
        self.assertEqual(MetricStatus.INCOMPLETE, result.profit_amount.status)
        self.assertEqual(MetricStatus.INCOMPLETE, result.profit_rate.status)
        self.assertEqual(MetricStatus.INCOMPLETE, result.average_amount.status)
        self.assertEqual(MetricStatus.INCOMPLETE, result.average_rate.status)
        self.assertIn("NET_PNL_INCOMPLETE", result.unavailable_reasons)
        self.assertIn("REALIZED_DATE_INCOMPLETE", result.unavailable_reasons)

    def test_profit_factor_and_sort_value_share_one_numeric_result(self) -> None:
        result = self.engine.calculate(
            self.aggregate(
                event_count=3,
                allocation_count=3,
                gross_pnl=100,
                gross_profit_sum=150,
                gross_loss_abs_sum=50,
            )
        )

        self.assertEqual(3, result.efficiency.value)
        self.assertEqual(result.efficiency.value, result.efficiency.sort_value)
        self.assertLess(
            canonical_metric_sort_key(result.efficiency, descending=True),
            canonical_metric_sort_key(
                self.engine.calculate(self.aggregate(event_count=0, period_performance_facts=())).average_rate,
                descending=True,
            ),
        )

    def test_average_rate_is_arithmetic_mean_of_canonical_period_rates(self) -> None:
        result = self.engine.calculate(
            self.aggregate(
                event_count=2,
                allocation_count=2,
                realized_cost_basis=10100,
                net_pnl=10,
                net_pnl_known_sum=10,
                realized_stock_trade_date_count=2,
                period_performance_facts=(
                    PeriodPerformanceFact("005930", "2026-08-23", 100, 10, 10, True),
                    PeriodPerformanceFact("005930", "2026-08-24", 10000, 0, 0, True),
                ),
            )
        )

        self.assertEqual(5, result.average_rate.value)
        self.assertEqual(result.average_rate.value, result.average_rate.sort_value)
        self.assertEqual(2, result.average_rate_diagnostics.valid_rate_period_count)

    def test_average_rate_handles_negative_zero_and_two_stocks_on_same_day(self) -> None:
        result = self.engine.calculate(
            self.aggregate(
                event_count=3,
                allocation_count=3,
                realized_stock_trade_date_count=3,
                period_performance_facts=(
                    PeriodPerformanceFact("005930", "2026-08-23", 100, 10, 10, True),
                    PeriodPerformanceFact("005930", "2026-08-24", 100, -4, -4, True),
                    PeriodPerformanceFact("005930", "2026-08-25", 100, 0, 0, True),
                ),
            )
        )
        same_day = self.engine.calculate(
            self.aggregate(
                event_count=2,
                allocation_count=2,
                realized_stock_trade_date_count=2,
                period_performance_facts=(
                    PeriodPerformanceFact("005930", "2026-08-23", 100, 10, 10, True),
                    PeriodPerformanceFact("000660", "2026-08-23", 100, -2, -2, True),
                ),
            )
        )

        self.assertEqual(2, result.average_rate.value)
        self.assertEqual(4, same_day.average_rate.value)

    def test_average_rate_reports_incomplete_and_undefined_periods(self) -> None:
        incomplete = self.engine.calculate(
            self.aggregate(
                event_count=2,
                allocation_count=2,
                realized_stock_trade_date_count=2,
                period_performance_facts=(
                    PeriodPerformanceFact("005930", "2026-08-23", 100, 10, 10, True),
                    PeriodPerformanceFact("005930", "2026-08-24", 100, None, 0, False),
                ),
            )
        )
        undefined = self.engine.calculate(
            self.aggregate(
                realized_cost_basis=0,
                period_performance_facts=(
                    PeriodPerformanceFact("005930", "2026-08-23", 0, 10, 10, True),
                ),
            )
        )

        self.assertEqual(MetricStatus.INCOMPLETE, incomplete.average_rate.status)
        self.assertEqual(10, incomplete.average_rate.value)
        self.assertEqual(1, incomplete.average_rate_diagnostics.valid_rate_period_count)
        self.assertEqual(1, incomplete.average_rate_diagnostics.incomplete_rate_period_count)
        self.assertEqual(MetricStatus.UNDEFINED, undefined.average_rate.status)
        self.assertEqual(1, undefined.average_rate_diagnostics.undefined_rate_period_count)


class CanonicalPerformanceMetricIntegrationTests(unittest.TestCase):
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
        self.sequence = 0
        self.engine = CanonicalPerformanceMetricEngine()

    @staticmethod
    def target(instance_id: str, group_id: str):
        return AssignmentEpisodeTarget.assigned(
            instance_id=instance_id,
            group_id=group_id,
            definition_id="indicator_follow",
            instance_name_snapshot=instance_id,
            group_name_snapshot=group_id,
        )

    def open_unassigned(self, stock_code: str, at: str = "2026-08-23T09:00:00+09:00") -> None:
        result = self.episodes.open_episode(
            stock_code,
            AssignmentEpisodeTarget.unassigned(),
            started_at=at,
            start_reason="REGISTERED",
            source="TEST",
        )
        self.assertTrue(result.success, result.error)

    def transition(self, stock_code: str, target: AssignmentEpisodeTarget, at: str):
        result = self.episodes.transition_episode(
            stock_code,
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
        stock_code: str,
        episode_id: str,
        *,
        gross: int,
        net: int | None,
        cost_basis: int,
        trade_date: str,
        entry_episode_id: str | None = None,
    ) -> None:
        self.sequence += 1
        result = self.ledger.append_event(
            {
                "stock_code": stock_code,
                "broker": "KIWOOM",
                "account_number": "12345678",
                "trade_date": trade_date,
                "broker_order_no": f"ORDER-{self.sequence}",
                "execution_identity": f"EXEC-{self.sequence}",
                "fill_id": f"FILL-{self.sequence}",
                "realization_id": f"REALIZATION-{self.sequence}",
                "realized_at": f"{trade_date}T10:00:00+09:00",
                "quantity": 1,
                "realized_cost_basis": cost_basis,
                "gross_pnl": gross,
                "fee": None,
                "tax": None,
                "net_pnl": net,
                "exit_episode_id": episode_id,
                "canonical_owner_policy": CANONICAL_OWNER_POLICY,
                "allocations": [
                    {
                        "entry_lot_id": f"LOT-{self.sequence}",
                        "entry_episode_id": entry_episode_id or episode_id,
                        "quantity": 1,
                        "cost_basis": cost_basis,
                        "gross_pnl": gross,
                        "net_pnl": net,
                    }
                ],
            }
        )
        self.assertTrue(result.success, result.error)

    def test_a_to_b_to_a_stock_lifetime_and_parent_scope_metrics(self) -> None:
        code = "005930"
        self.open_unassigned(code)
        a1 = self.transition(code, self.target("A", "G1"), "2026-08-23T09:10:00+09:00")
        b1 = self.transition(code, self.target("B", "G1"), "2026-08-23T09:20:00+09:00")
        a2 = self.transition(code, self.target("A", "G2"), "2026-08-23T09:30:00+09:00")
        self.append(code, a1.episode_id, gross=100, net=90, cost_basis=1000, trade_date="2026-08-23")
        self.append(code, b1.episode_id, gross=50, net=45, cost_basis=500, trade_date="2026-08-24")
        self.append(code, a2.episode_id, gross=30, net=27, cost_basis=300, trade_date="2026-08-25")

        aggregator = CanonicalPerformanceAggregator(self.ledger, self.episodes)
        relations = build_current_performance_relations(
            [SimpleNamespace(code=code, name="Samsung", assigned_routine_instance_id="A")],
            [SimpleNamespace(instance_id="A", group_id="G2", display_name="A")],
            [SimpleNamespace(group_id="G2", display_name="G2")],
        )
        projection = PerformanceScopeProjection(aggregator, relations)
        stock_values = [
            self.engine.calculate(projection.project_stocks(scope)[0].lifetime).profit_amount.value
            for scope in (PerformanceScope.ALL, PerformanceScope.CURRENT)
        ]
        self.assertEqual([162, 162], stock_values)
        stock_average_rates = [
            self.engine.calculate(projection.project_stocks(scope)[0].lifetime).average_rate.value
            for scope in (PerformanceScope.ALL, PerformanceScope.CURRENT)
        ]
        self.assertEqual([9, 9], stock_average_rates)
        self.assertEqual((), projection.project_stocks(PerformanceScope.PAST))

        current_a = self.engine.calculate(projection.project_instances(PerformanceScope.CURRENT)[0].aggregate)
        past = {row.instance_id: self.engine.calculate(row.aggregate) for row in projection.project_instances(PerformanceScope.PAST)}
        all_rows = {row.instance_id: self.engine.calculate(row.aggregate) for row in projection.project_instances(PerformanceScope.ALL)}
        self.assertEqual(27, current_a.profit_amount.value)
        self.assertEqual(9, current_a.average_rate.value)
        self.assertEqual(90, past["A"].profit_amount.value)
        self.assertEqual(9, past["A"].average_rate.value)
        self.assertEqual(45, past["B"].profit_amount.value)
        self.assertEqual(117, all_rows["A"].profit_amount.value)
        self.assertEqual(45, all_rows["B"].profit_amount.value)
        self.assertEqual(90, self.engine.calculate(aggregator.aggregate_episode(a1.episode_id).metrics).profit_amount.value)

        current_groups = {
            row.group_id: self.engine.calculate(row.aggregate)
            for row in projection.project_groups(PerformanceScope.CURRENT)
        }
        past_groups = {
            row.group_id: self.engine.calculate(row.aggregate)
            for row in projection.project_groups(PerformanceScope.PAST)
        }
        self.assertEqual(27, current_groups["G2"].profit_amount.value)
        self.assertEqual(9, current_groups["G2"].average_rate.value)
        self.assertEqual(135, past_groups["G1"].profit_amount.value)
        self.assertEqual(9, past_groups["G1"].average_rate.value)

    def test_multi_stock_parent_recalculates_average_from_parent_raw_totals(self) -> None:
        for code in ("005930", "000660"):
            self.open_unassigned(code)
        first = self.transition("005930", self.target("A", "G1"), "2026-08-23T09:10:00+09:00")
        second = self.transition("000660", self.target("A", "G1"), "2026-08-23T09:10:00+09:00")
        self.append("005930", first.episode_id, gross=50, net=45, cost_basis=500, trade_date="2026-08-23")
        self.append("005930", first.episode_id, gross=50, net=45, cost_basis=500, trade_date="2026-08-24")
        self.append("000660", second.episode_id, gross=-40, net=-40, cost_basis=100, trade_date="2026-08-23")

        aggregator = CanonicalPerformanceAggregator(self.ledger, self.episodes)
        parent = self.engine.calculate(aggregator.aggregate_instance("A").metrics)
        children = [
            self.engine.calculate(aggregator.aggregate_stock_lifetime(code)).average_amount.value
            for code in ("005930", "000660")
        ]

        self.assertEqual(50, parent.profit_amount.value)
        self.assertEqual(3, parent.period.value)
        self.assertAlmostEqual(16.666667, parent.average_amount.value)
        self.assertAlmostEqual(-7.333333, parent.average_rate.value)
        self.assertNotEqual(sum(children) / len(children), parent.average_amount.value)
        child_rates = [
            self.engine.calculate(aggregator.aggregate_stock_lifetime(code)).average_rate.value
            for code in ("005930", "000660")
        ]
        self.assertNotEqual(sum(child_rates) / len(child_rates), parent.average_rate.value)

    def test_unresolved_and_incomplete_net_are_not_promoted_to_parent_or_profit(self) -> None:
        from performance_ledger_repository import OWNERSHIP_UNRESOLVED

        code = "005930"
        self.open_unassigned(code)
        a1 = self.transition(code, self.target("A", "G1"), "2026-08-23T09:10:00+09:00")
        self.append(code, a1.episode_id, gross=100, net=90, cost_basis=1000, trade_date="2026-08-23")
        self.append(
            code,
            a1.episode_id,
            gross=30,
            net=None,
            cost_basis=300,
            trade_date="2026-08-24",
            entry_episode_id=OWNERSHIP_UNRESOLVED,
        )

        aggregator = CanonicalPerformanceAggregator(self.ledger, self.episodes)
        stock = self.engine.calculate(aggregator.aggregate_stock_lifetime(code))
        parent = self.engine.calculate(aggregator.aggregate_instance("A").metrics)

        self.assertEqual(130, stock.gross_profit_amount.value)
        self.assertEqual(MetricStatus.INCOMPLETE, stock.profit_amount.status)
        self.assertEqual(100, parent.gross_profit_amount.value)
        self.assertEqual(90, parent.profit_amount.value)


if __name__ == "__main__":
    unittest.main()
