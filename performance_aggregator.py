"""Read-only aggregates over canonical Performance Events and Assignment Episodes."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Iterable

from assignment_episode_repository import (
    ASSIGNED,
    UNASSIGNED,
    AssignmentEpisode,
    CanonicalAssignmentEpisodeRepository,
)
from performance_ledger_repository import (
    OWNERSHIP_UNRESOLVED,
    CanonicalStockPerformanceLedgerRepository,
    PerformanceAllocation,
    PerformanceEvent,
)
from stock_repository import is_valid_stock_code, normalize_stock_code


def _decimal(value: int | float) -> Decimal:
    return Decimal(str(value))


def _json_number(value: Decimal) -> int | float:
    integral = value.to_integral_value()
    return int(integral) if value == integral else round(float(value), 6)


def _snapshots(values: Iterable[str | None]) -> tuple[str, ...]:
    return tuple(sorted({str(value).strip() for value in values if str(value or "").strip()}, key=str.casefold))


@dataclass(frozen=True)
class PeriodPerformanceFact:
    stock_code: str
    trade_date: str
    realized_cost_basis: int | float
    net_pnl: int | float | None
    net_pnl_known_sum: int | float
    net_pnl_complete: bool


@dataclass(frozen=True)
class AggregateMetrics:
    event_count: int
    allocation_count: int
    realized_quantity: int
    realized_cost_basis: int | float
    gross_pnl: int | float
    gross_profit_sum: int | float
    gross_loss_abs_sum: int | float
    net_pnl: int | float | None
    net_pnl_known_sum: int | float
    net_pnl_complete: bool
    first_realized_at: str | None
    last_realized_at: str | None
    unique_realized_trade_dates: tuple[str, ...]
    realized_trade_date_count: int
    realized_stock_trade_date_count: int
    realized_date_complete: bool
    period_performance_facts: tuple[PeriodPerformanceFact, ...] = ()


@dataclass(frozen=True)
class OwnershipAllocationSummary:
    allocation_count: int
    realized_quantity: int
    realized_cost_basis: int | float
    gross_pnl: int | float
    net_pnl_known_sum: int | float
    net_pnl_complete: bool


@dataclass(frozen=True)
class StockLifetimeAggregate:
    stock_code: str
    performance_event_count: int
    allocation_count: int
    realized_quantity: int
    realized_cost_basis: int | float
    gross_pnl: int | float
    gross_profit_sum: int | float
    gross_loss_abs_sum: int | float
    net_pnl: int | float | None
    net_pnl_known_sum: int | float
    net_pnl_complete: bool
    first_realized_at: str | None
    last_realized_at: str | None
    unique_realized_trade_dates: tuple[str, ...]
    realized_trade_date_count: int
    realized_stock_trade_date_count: int
    realized_date_complete: bool
    resolved_allocation_count: int
    unassigned_allocation_count: int
    unresolved_allocation_count: int
    resolved: OwnershipAllocationSummary
    unassigned: OwnershipAllocationSummary
    unresolved: OwnershipAllocationSummary
    period_performance_facts: tuple[PeriodPerformanceFact, ...] = ()


@dataclass(frozen=True)
class EpisodeAggregate:
    episode_id: str
    stock_code: str
    ownership_kind: str
    instance_id: str | None
    group_id: str | None
    definition_id: str | None
    instance_name_snapshot: str | None
    group_name_snapshot: str | None
    started_at: str
    ended_at: str | None
    metrics: AggregateMetrics

    def __getattr__(self, name: str):
        return getattr(self.metrics, name)


@dataclass(frozen=True)
class InstanceAggregate:
    instance_id: str
    episode_count: int
    stock_codes: tuple[str, ...]
    definition_ids: tuple[str, ...]
    observed_instance_name_snapshots: tuple[str, ...]
    observed_group_ids: tuple[str, ...]
    observed_group_name_snapshots: tuple[str, ...]
    metrics: AggregateMetrics

    def __getattr__(self, name: str):
        return getattr(self.metrics, name)


@dataclass(frozen=True)
class GroupAggregate:
    group_id: str
    episode_count: int
    stock_codes: tuple[str, ...]
    instance_ids: tuple[str, ...]
    definition_ids: tuple[str, ...]
    observed_group_name_snapshots: tuple[str, ...]
    observed_instance_name_snapshots: tuple[str, ...]
    metrics: AggregateMetrics

    def __getattr__(self, name: str):
        return getattr(self.metrics, name)


@dataclass(frozen=True)
class PerformanceReconciliation:
    stock_lifetime_gross_pnl: int | float
    assigned_episode_gross_pnl: int | float
    unassigned_episode_gross_pnl: int | float
    unresolved_gross_pnl: int | float
    instance_gross_pnl: int | float
    group_gross_pnl: int | float
    assigned_without_group_gross_pnl: int | float
    stock_reconciled: bool
    instance_reconciled: bool
    group_reconciled: bool


@dataclass(frozen=True)
class PerformanceAggregationSnapshot:
    events_by_stock: dict[str, tuple[PerformanceEvent, ...]]
    episodes_by_stock: dict[str, tuple[AssignmentEpisode, ...]]
    episodes_by_id: dict[str, AssignmentEpisode]
    ledger_document_reads: int
    episode_document_reads: int


@dataclass(frozen=True)
class _AllocationFact:
    event: PerformanceEvent
    allocation: PerformanceAllocation
    episode: AssignmentEpisode | None


class CanonicalPerformanceAggregator:
    """One immutable read snapshot; construct a new instance for a fresh view."""

    def __init__(
        self,
        ledger_repository: CanonicalStockPerformanceLedgerRepository,
        episode_repository: CanonicalAssignmentEpisodeRepository,
    ) -> None:
        self.ledger_repository = ledger_repository
        self.episode_repository = episode_repository
        self.snapshot = self._build_snapshot()
        self._facts = self._build_allocation_facts()

    def aggregate_stock_lifetime(self, stock_code: str) -> StockLifetimeAggregate:
        code = normalize_stock_code(str(stock_code or ""))
        if not is_valid_stock_code(code):
            raise ValueError("stock_code is invalid")
        events = self.snapshot.events_by_stock.get(code, ())
        facts = tuple(fact for fact in self._facts if fact.event.stock_code == code)
        event_metrics = self._event_metrics(events)
        resolved = self._ownership_summary(fact for fact in facts if fact.episode and fact.episode.ownership_kind == ASSIGNED)
        unassigned = self._ownership_summary(fact for fact in facts if fact.episode and fact.episode.ownership_kind == UNASSIGNED)
        unresolved = self._ownership_summary(fact for fact in facts if fact.episode is None)
        return StockLifetimeAggregate(
            stock_code=code,
            performance_event_count=event_metrics.event_count,
            allocation_count=len(facts),
            realized_quantity=event_metrics.realized_quantity,
            realized_cost_basis=event_metrics.realized_cost_basis,
            gross_pnl=event_metrics.gross_pnl,
            gross_profit_sum=event_metrics.gross_profit_sum,
            gross_loss_abs_sum=event_metrics.gross_loss_abs_sum,
            net_pnl=event_metrics.net_pnl,
            net_pnl_known_sum=event_metrics.net_pnl_known_sum,
            net_pnl_complete=event_metrics.net_pnl_complete,
            first_realized_at=event_metrics.first_realized_at,
            last_realized_at=event_metrics.last_realized_at,
            unique_realized_trade_dates=event_metrics.unique_realized_trade_dates,
            realized_trade_date_count=event_metrics.realized_trade_date_count,
            realized_stock_trade_date_count=event_metrics.realized_stock_trade_date_count,
            realized_date_complete=event_metrics.realized_date_complete,
            resolved_allocation_count=resolved.allocation_count,
            unassigned_allocation_count=unassigned.allocation_count,
            unresolved_allocation_count=unresolved.allocation_count,
            resolved=resolved,
            unassigned=unassigned,
            unresolved=unresolved,
            period_performance_facts=event_metrics.period_performance_facts,
        )

    def aggregate_episode(self, episode_id: str) -> EpisodeAggregate | None:
        episode = self.snapshot.episodes_by_id.get(str(episode_id or "").strip())
        if episode is None:
            return None
        facts = tuple(fact for fact in self._facts if fact.episode and fact.episode.episode_id == episode.episode_id)
        return EpisodeAggregate(
            episode_id=episode.episode_id,
            stock_code=episode.stock_code,
            ownership_kind=episode.ownership_kind,
            instance_id=episode.instance_id,
            group_id=episode.group_id,
            definition_id=episode.definition_id,
            instance_name_snapshot=episode.instance_name_snapshot,
            group_name_snapshot=episode.group_name_snapshot,
            started_at=episode.started_at,
            ended_at=episode.ended_at,
            metrics=self._allocation_metrics(facts),
        )

    def aggregate_episode_scope(self, episode_ids: Iterable[str]) -> AggregateMetrics:
        """Aggregate allocations owned by exactly the supplied canonical Episodes."""
        selected = {str(episode_id or "").strip() for episode_id in episode_ids}
        selected.discard("")
        unknown = selected.difference(self.snapshot.episodes_by_id)
        if unknown:
            raise ValueError("aggregate scope contains an unknown episode_id")
        facts = tuple(
            fact
            for fact in self._facts
            if fact.episode is not None and fact.episode.episode_id in selected
        )
        return self._allocation_metrics(facts)

    def aggregate_instance(self, instance_id: str) -> InstanceAggregate | None:
        clean_id = str(instance_id or "").strip()
        episodes = tuple(
            episode
            for episode in self.snapshot.episodes_by_id.values()
            if episode.ownership_kind == ASSIGNED and episode.instance_id == clean_id
        )
        if not episodes:
            return None
        episode_ids = {episode.episode_id for episode in episodes}
        facts = tuple(fact for fact in self._facts if fact.episode and fact.episode.episode_id in episode_ids)
        return InstanceAggregate(
            instance_id=clean_id,
            episode_count=len(episodes),
            stock_codes=tuple(sorted({episode.stock_code for episode in episodes})),
            definition_ids=_snapshots(episode.definition_id for episode in episodes),
            observed_instance_name_snapshots=_snapshots(episode.instance_name_snapshot for episode in episodes),
            observed_group_ids=_snapshots(episode.group_id for episode in episodes),
            observed_group_name_snapshots=_snapshots(episode.group_name_snapshot for episode in episodes),
            metrics=self._allocation_metrics(facts),
        )

    def aggregate_group(self, group_id: str) -> GroupAggregate | None:
        clean_id = str(group_id or "").strip()
        episodes = tuple(
            episode
            for episode in self.snapshot.episodes_by_id.values()
            if episode.ownership_kind == ASSIGNED and episode.group_id == clean_id
        )
        if not episodes:
            return None
        episode_ids = {episode.episode_id for episode in episodes}
        facts = tuple(fact for fact in self._facts if fact.episode and fact.episode.episode_id in episode_ids)
        return GroupAggregate(
            group_id=clean_id,
            episode_count=len(episodes),
            stock_codes=tuple(sorted({episode.stock_code for episode in episodes})),
            instance_ids=_snapshots(episode.instance_id for episode in episodes),
            definition_ids=_snapshots(episode.definition_id for episode in episodes),
            observed_group_name_snapshots=_snapshots(episode.group_name_snapshot for episode in episodes),
            observed_instance_name_snapshots=_snapshots(episode.instance_name_snapshot for episode in episodes),
            metrics=self._allocation_metrics(facts),
        )

    def aggregate_all_stocks(self) -> tuple[StockLifetimeAggregate, ...]:
        return tuple(self.aggregate_stock_lifetime(code) for code in sorted(self.snapshot.events_by_stock))

    def aggregate_all_instances(self) -> tuple[InstanceAggregate, ...]:
        instance_ids = _snapshots(
            episode.instance_id
            for episode in self.snapshot.episodes_by_id.values()
            if episode.ownership_kind == ASSIGNED
        )
        return tuple(result for value in instance_ids if (result := self.aggregate_instance(value)) is not None)

    def aggregate_all_groups(self) -> tuple[GroupAggregate, ...]:
        group_ids = _snapshots(
            episode.group_id
            for episode in self.snapshot.episodes_by_id.values()
            if episode.ownership_kind == ASSIGNED
        )
        return tuple(result for value in group_ids if (result := self.aggregate_group(value)) is not None)

    def reconciliation(self) -> PerformanceReconciliation:
        stock_total = sum(
            (_decimal(result.gross_pnl) for result in self.aggregate_all_stocks()),
            Decimal(0),
        )
        assigned_facts = tuple(fact for fact in self._facts if fact.episode and fact.episode.ownership_kind == ASSIGNED)
        unassigned_facts = tuple(fact for fact in self._facts if fact.episode and fact.episode.ownership_kind == UNASSIGNED)
        unresolved_facts = tuple(fact for fact in self._facts if fact.episode is None)
        assigned_total = self._gross(assigned_facts)
        unassigned_total = self._gross(unassigned_facts)
        unresolved_total = self._gross(unresolved_facts)
        instance_total = sum(
            (_decimal(result.gross_pnl) for result in self.aggregate_all_instances()),
            Decimal(0),
        )
        group_total = sum(
            (_decimal(result.gross_pnl) for result in self.aggregate_all_groups()),
            Decimal(0),
        )
        without_group = self._gross(fact for fact in assigned_facts if not fact.episode.group_id)
        return PerformanceReconciliation(
            stock_lifetime_gross_pnl=_json_number(stock_total),
            assigned_episode_gross_pnl=_json_number(assigned_total),
            unassigned_episode_gross_pnl=_json_number(unassigned_total),
            unresolved_gross_pnl=_json_number(unresolved_total),
            instance_gross_pnl=_json_number(instance_total),
            group_gross_pnl=_json_number(group_total),
            assigned_without_group_gross_pnl=_json_number(without_group),
            stock_reconciled=stock_total == assigned_total + unassigned_total + unresolved_total,
            instance_reconciled=assigned_total == instance_total,
            group_reconciled=assigned_total == group_total + without_group,
        )

    def _build_snapshot(self) -> PerformanceAggregationSnapshot:
        episodes_by_stock: dict[str, tuple[AssignmentEpisode, ...]] = {}
        episodes_by_id: dict[str, AssignmentEpisode] = {}
        episode_reads = 0
        root = self.episode_repository.episodes_root
        if root.is_dir():
            for directory in sorted(root.iterdir(), key=lambda path: path.name):
                if not directory.is_dir() or not is_valid_stock_code(directory.name):
                    continue
                episodes = self.episode_repository.list_episodes(directory.name)
                episode_reads += 1
                episodes_by_stock[directory.name] = episodes
                for episode in episodes:
                    if episode.episode_id in episodes_by_id:
                        raise ValueError("episode_id is not globally unique")
                    episodes_by_id[episode.episode_id] = episode
        events_by_stock: dict[str, tuple[PerformanceEvent, ...]] = {}
        ledger_reads = 0
        root = self.ledger_repository.ledger_root
        if root.is_dir():
            for directory in sorted(root.iterdir(), key=lambda path: path.name):
                if not directory.is_dir() or not is_valid_stock_code(directory.name):
                    continue
                lookup = {episode.episode_id: episode for episode in episodes_by_stock.get(directory.name, ())}
                events_by_stock[directory.name] = self.ledger_repository.list_events(
                    directory.name,
                    episode_lookup=lookup,
                )
                ledger_reads += 1
        return PerformanceAggregationSnapshot(
            events_by_stock=events_by_stock,
            episodes_by_stock=episodes_by_stock,
            episodes_by_id=episodes_by_id,
            ledger_document_reads=ledger_reads,
            episode_document_reads=episode_reads,
        )

    def _build_allocation_facts(self) -> tuple[_AllocationFact, ...]:
        facts: list[_AllocationFact] = []
        for events in self.snapshot.events_by_stock.values():
            for event in events:
                for allocation in event.allocations:
                    episode = (
                        None
                        if allocation.entry_episode_id == OWNERSHIP_UNRESOLVED
                        else self.snapshot.episodes_by_id.get(allocation.entry_episode_id)
                    )
                    if allocation.entry_episode_id != OWNERSHIP_UNRESOLVED and episode is None:
                        raise ValueError("performance allocation references a missing Episode")
                    facts.append(_AllocationFact(event=event, allocation=allocation, episode=episode))
        return tuple(facts)

    @staticmethod
    def _event_metrics(events: tuple[PerformanceEvent, ...]) -> AggregateMetrics:
        event_ids = {event.performance_event_id for event in events}
        allocations = [allocation for event in events for allocation in event.allocations]
        net_values = [event.net_pnl for event in events]
        return CanonicalPerformanceAggregator._metrics(
            event_ids=event_ids,
            allocation_count=len(allocations),
            quantity=sum(event.quantity for event in events),
            cost_basis=sum((_decimal(event.realized_cost_basis) for event in events), Decimal(0)),
            gross=sum((_decimal(event.gross_pnl) for event in events), Decimal(0)),
            pnl_values=[_decimal(event.gross_pnl) for event in events],
            net_values=net_values,
            events=events,
            period_values=(
                (
                    event.stock_code,
                    event.trade_date,
                    _decimal(event.realized_cost_basis),
                    event.net_pnl,
                )
                for event in events
            ),
        )

    @staticmethod
    def _allocation_metrics(facts: Iterable[_AllocationFact]) -> AggregateMetrics:
        values = tuple(facts)
        return CanonicalPerformanceAggregator._metrics(
            event_ids={fact.event.performance_event_id for fact in values},
            allocation_count=len(values),
            quantity=sum(fact.allocation.quantity for fact in values),
            cost_basis=sum((_decimal(fact.allocation.cost_basis) for fact in values), Decimal(0)),
            gross=sum((_decimal(fact.allocation.gross_pnl) for fact in values), Decimal(0)),
            pnl_values=[_decimal(fact.allocation.gross_pnl) for fact in values],
            net_values=[fact.allocation.net_pnl for fact in values],
            events=tuple({fact.event.performance_event_id: fact.event for fact in values}.values()),
            period_values=(
                (
                    fact.event.stock_code,
                    fact.event.trade_date,
                    _decimal(fact.allocation.cost_basis),
                    fact.allocation.net_pnl,
                )
                for fact in values
            ),
        )

    @staticmethod
    def _metrics(
        *,
        event_ids: set[str],
        allocation_count: int,
        quantity: int,
        cost_basis: Decimal,
        gross: Decimal,
        pnl_values: list[Decimal],
        net_values: list[int | float | None],
        events: tuple[PerformanceEvent, ...],
        period_values: Iterable[tuple[str, str, Decimal, int | float | None]],
    ) -> AggregateMetrics:
        known_net = sum((_decimal(value) for value in net_values if value is not None), Decimal(0))
        net_complete = all(value is not None for value in net_values)
        realized_times = sorted(event.realized_at for event in events if event.realized_at)
        trade_dates = tuple(sorted({event.trade_date for event in events if event.trade_date}))
        stock_trade_dates = {
            (event.stock_code, event.trade_date)
            for event in events
            if event.stock_code and event.trade_date
        }
        date_complete = all(bool(event.realized_at and event.trade_date) for event in events)
        gross_profit = sum((value for value in pnl_values if value > 0), Decimal(0))
        gross_loss_abs = -sum((value for value in pnl_values if value < 0), Decimal(0))
        period_costs: dict[tuple[str, str], Decimal] = {}
        period_known_nets: dict[tuple[str, str], Decimal] = {}
        period_net_complete: dict[tuple[str, str], bool] = {}
        for stock_code, trade_date, period_cost_basis, period_net_pnl in period_values:
            if not stock_code or not trade_date:
                continue
            key = (stock_code, trade_date)
            period_costs[key] = period_costs.get(key, Decimal(0)) + period_cost_basis
            period_known_nets.setdefault(key, Decimal(0))
            period_net_complete.setdefault(key, True)
            if period_net_pnl is None:
                period_net_complete[key] = False
            else:
                period_known_nets[key] += _decimal(period_net_pnl)
        period_facts = tuple(
            PeriodPerformanceFact(
                stock_code=stock_code,
                trade_date=trade_date,
                realized_cost_basis=_json_number(period_costs[(stock_code, trade_date)]),
                net_pnl=(
                    _json_number(period_known_nets[(stock_code, trade_date)])
                    if period_net_complete[(stock_code, trade_date)]
                    else None
                ),
                net_pnl_known_sum=_json_number(period_known_nets[(stock_code, trade_date)]),
                net_pnl_complete=period_net_complete[(stock_code, trade_date)],
            )
            for stock_code, trade_date in sorted(period_costs)
        )
        return AggregateMetrics(
            event_count=len(event_ids),
            allocation_count=allocation_count,
            realized_quantity=quantity,
            realized_cost_basis=_json_number(cost_basis),
            gross_pnl=_json_number(gross),
            gross_profit_sum=_json_number(gross_profit),
            gross_loss_abs_sum=_json_number(gross_loss_abs),
            net_pnl=_json_number(known_net) if net_complete else None,
            net_pnl_known_sum=_json_number(known_net),
            net_pnl_complete=net_complete,
            first_realized_at=realized_times[0] if realized_times else None,
            last_realized_at=realized_times[-1] if realized_times else None,
            unique_realized_trade_dates=trade_dates,
            realized_trade_date_count=len(trade_dates),
            realized_stock_trade_date_count=len(stock_trade_dates),
            realized_date_complete=date_complete,
            period_performance_facts=period_facts,
        )

    @staticmethod
    def _ownership_summary(facts: Iterable[_AllocationFact]) -> OwnershipAllocationSummary:
        values = tuple(facts)
        nets = [fact.allocation.net_pnl for fact in values]
        known_net = sum((_decimal(value) for value in nets if value is not None), Decimal(0))
        return OwnershipAllocationSummary(
            allocation_count=len(values),
            realized_quantity=sum(fact.allocation.quantity for fact in values),
            realized_cost_basis=_json_number(sum((_decimal(fact.allocation.cost_basis) for fact in values), Decimal(0))),
            gross_pnl=_json_number(CanonicalPerformanceAggregator._gross(values)),
            net_pnl_known_sum=_json_number(known_net),
            net_pnl_complete=all(value is not None for value in nets),
        )

    @staticmethod
    def _gross(facts: Iterable[_AllocationFact]) -> Decimal:
        return sum((_decimal(fact.allocation.gross_pnl) for fact in facts), Decimal(0))
