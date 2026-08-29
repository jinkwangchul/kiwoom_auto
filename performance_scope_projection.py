"""Read-only ALL/CURRENT/PAST projections over canonical performance history."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from types import MappingProxyType
from typing import Iterable, Mapping

from assignment_episode_repository import ASSIGNED, UNASSIGNED, AssignmentEpisode
from performance_aggregator import (
    AggregateMetrics,
    CanonicalPerformanceAggregator,
    StockLifetimeAggregate,
)
from stock_repository import is_valid_stock_code, normalize_stock_code


class PerformanceScope(str, Enum):
    ALL = "ALL"
    CURRENT = "CURRENT"
    PAST = "PAST"


class PerformanceLevel(str, Enum):
    STOCK = "STOCK"
    INSTANCE = "INSTANCE"
    GROUP = "GROUP"


@dataclass(frozen=True)
class CurrentStockRelation:
    stock_code: str
    stock_name: str
    current_instance_id: str | None
    current_group_id: str | None
    current_instance_name: str | None
    current_group_name: str | None
    instance_exists: bool

    @property
    def is_currently_assigned(self) -> bool:
        return bool(self.current_instance_id)


@dataclass(frozen=True)
class CurrentPerformanceRelationSnapshot:
    stocks_by_code: Mapping[str, CurrentStockRelation]


@dataclass(frozen=True)
class CurrentRelationConsistency:
    consistent: bool
    reasons: tuple[str, ...]
    current_instance_id: str | None
    current_group_id: str | None
    open_episode_id: str | None
    open_episode_ownership_kind: str | None
    open_episode_instance_id: str | None
    open_episode_group_id: str | None


@dataclass(frozen=True)
class StockPerformanceProjectionRow:
    stock_code: str
    stock_name: str
    identity: str
    visibility_reasons: tuple[str, ...]
    metric_scope: str
    is_currently_assigned: bool
    has_closed_episode: bool
    current_instance_id: str | None
    current_group_id: str | None
    lifetime: StockLifetimeAggregate
    current_relation_consistency: CurrentRelationConsistency


@dataclass(frozen=True)
class InstancePerformanceProjectionRow:
    instance_id: str
    identity: str
    visibility_reasons: tuple[str, ...]
    metric_scope: str
    episode_ids: tuple[str, ...]
    is_current_operational: bool
    is_historical: bool
    observed_instance_name_snapshots: tuple[str, ...]
    observed_group_ids: tuple[str, ...]
    observed_group_name_snapshots: tuple[str, ...]
    aggregate: AggregateMetrics
    performance_absence_reason: str | None


@dataclass(frozen=True)
class GroupPerformanceProjectionRow:
    group_id: str
    identity: str
    visibility_reasons: tuple[str, ...]
    metric_scope: str
    episode_ids: tuple[str, ...]
    is_current_operational: bool
    is_historical: bool
    observed_group_name_snapshots: tuple[str, ...]
    observed_instance_ids: tuple[str, ...]
    observed_instance_name_snapshots: tuple[str, ...]
    aggregate: AggregateMetrics
    performance_absence_reason: str | None


@dataclass(frozen=True)
class PerformanceScopeReconciliation:
    scope: PerformanceScope
    expected_assigned_episode_gross_pnl: int | float
    projected_instance_gross_pnl: int | float
    projected_group_gross_pnl: int | float
    assigned_without_group_gross_pnl: int | float
    instance_reconciled: bool
    group_reconciled: bool


def _text(value: object) -> str:
    return str(value or "").strip()


def _nullable(value: object) -> str | None:
    return _text(value) or None


def _snapshot_names(values: Iterable[str | None]) -> tuple[str, ...]:
    return tuple(sorted({_text(value) for value in values if _text(value)}, key=str.casefold))


def _number(value: int | float) -> Decimal:
    return Decimal(str(value))


def _json_number(value: Decimal) -> int | float:
    integral = value.to_integral_value()
    return int(integral) if value == integral else round(float(value), 6)


def build_current_performance_relations(
    stocks: Iterable[object],
    instances: Iterable[object],
    groups: Iterable[object] = (),
) -> CurrentPerformanceRelationSnapshot:
    """Build the current relation snapshot from the existing assignment SoT."""
    instances_by_id = {
        _text(getattr(instance, "instance_id", "")): instance
        for instance in instances
        if _text(getattr(instance, "instance_id", ""))
    }
    groups_by_id = {
        _text(getattr(group, "group_id", "")): group
        for group in groups
        if _text(getattr(group, "group_id", ""))
    }
    relations: dict[str, CurrentStockRelation] = {}
    for stock in stocks:
        code = normalize_stock_code(_text(getattr(stock, "code", "")))
        if not is_valid_stock_code(code):
            raise ValueError("current relation contains an invalid stock_code")
        if code in relations:
            raise ValueError("current relation contains duplicate stock_code")
        instance_id = _nullable(getattr(stock, "assigned_routine_instance_id", ""))
        instance = instances_by_id.get(instance_id or "")
        group_id = _nullable(getattr(instance, "group_id", "")) if instance is not None else None
        group = groups_by_id.get(group_id or "")
        relations[code] = CurrentStockRelation(
            stock_code=code,
            stock_name=_text(getattr(stock, "name", "")),
            current_instance_id=instance_id,
            current_group_id=group_id,
            current_instance_name=(
                _nullable(getattr(instance, "display_name", ""))
                if instance is not None
                else _nullable(getattr(stock, "routine_instance_name", ""))
            ),
            current_group_name=(
                _nullable(getattr(group, "display_name", ""))
                if group is not None
                else None
            ),
            instance_exists=instance is not None if instance_id else True,
        )
    return CurrentPerformanceRelationSnapshot(stocks_by_code=MappingProxyType(relations))


class PerformanceScopeProjection:
    def __init__(
        self,
        aggregator: CanonicalPerformanceAggregator,
        current_relations: CurrentPerformanceRelationSnapshot,
    ) -> None:
        self.aggregator = aggregator
        self.current_relations = current_relations
        self._resolved_performance_episode_ids = (
            aggregator.resolved_performance_episode_ids
        )

    def project_stocks(self, scope: PerformanceScope | str) -> tuple[StockPerformanceProjectionRow, ...]:
        selected_scope = self._scope(scope)
        rows: list[StockPerformanceProjectionRow] = []
        for stock_code in sorted(self._known_stock_codes()):
            relation = self.current_relations.stocks_by_code.get(stock_code)
            episodes = self.aggregator.snapshot.episodes_by_stock.get(stock_code, ())
            has_closed = any(not episode.is_open for episode in episodes)
            currently_assigned = bool(relation and relation.is_currently_assigned)
            has_performance = bool(
                self.aggregator.snapshot.events_by_stock.get(stock_code)
            )
            if selected_scope == PerformanceScope.CURRENT and not currently_assigned:
                continue
            if selected_scope == PerformanceScope.PAST and (
                currently_assigned or not has_performance
            ):
                continue
            if selected_scope == PerformanceScope.ALL and not (
                currently_assigned or has_performance
            ):
                continue
            reasons = self._stock_visibility_reasons(
                selected_scope,
                currently_assigned=currently_assigned,
                has_ledger=has_performance,
            )
            rows.append(
                StockPerformanceProjectionRow(
                    stock_code=stock_code,
                    stock_name=relation.stock_name if relation else "",
                    identity=stock_code,
                    visibility_reasons=reasons,
                    metric_scope="STOCK_LIFETIME",
                    is_currently_assigned=currently_assigned,
                    has_closed_episode=has_closed,
                    current_instance_id=relation.current_instance_id if relation else None,
                    current_group_id=relation.current_group_id if relation else None,
                    lifetime=self.aggregator.aggregate_stock_lifetime(stock_code),
                    current_relation_consistency=self._current_consistency(stock_code, relation, episodes),
                )
            )
        return tuple(rows)

    def project_instances(self, scope: PerformanceScope | str) -> tuple[InstancePerformanceProjectionRow, ...]:
        selected_scope = self._scope(scope)
        current_ids = {
            relation.current_instance_id
            for relation in self.current_relations.stocks_by_code.values()
            if relation.current_instance_id and relation.instance_exists
        }
        assigned_episodes = tuple(
            episode
            for episode in self.aggregator.snapshot.episodes_by_id.values()
            if episode.ownership_kind == ASSIGNED and episode.instance_id
        )
        historical_ids = {
            episode.instance_id
            for episode in assigned_episodes
            if not episode.is_open
            and episode.episode_id in self._resolved_performance_episode_ids
        }
        ids = self._parent_ids(selected_scope, current_ids, historical_ids)
        rows = [
            self._instance_row(
                instance_id,
                selected_scope,
                current_ids,
                assigned_episodes,
            )
            for instance_id in ids
        ]
        return tuple(sorted(rows, key=lambda row: (self._row_name(row.observed_instance_name_snapshots), row.instance_id)))

    def project_groups(self, scope: PerformanceScope | str) -> tuple[GroupPerformanceProjectionRow, ...]:
        selected_scope = self._scope(scope)
        current_ids = {
            relation.current_group_id
            for relation in self.current_relations.stocks_by_code.values()
            if relation.current_instance_id and relation.instance_exists and relation.current_group_id
        }
        assigned_episodes = tuple(
            episode
            for episode in self.aggregator.snapshot.episodes_by_id.values()
            if episode.ownership_kind == ASSIGNED and episode.group_id
        )
        historical_ids = {
            episode.group_id
            for episode in assigned_episodes
            if not episode.is_open
            and episode.episode_id in self._resolved_performance_episode_ids
        }
        ids = self._parent_ids(selected_scope, current_ids, historical_ids)
        rows = [
            self._group_row(group_id, selected_scope, current_ids, assigned_episodes)
            for group_id in ids
        ]
        return tuple(sorted(rows, key=lambda row: (self._row_name(row.observed_group_name_snapshots), row.group_id)))

    def project(self, scope: PerformanceScope | str, level: PerformanceLevel | str):
        selected_level = level if isinstance(level, PerformanceLevel) else PerformanceLevel(_text(level).upper())
        if selected_level == PerformanceLevel.STOCK:
            return self.project_stocks(scope)
        if selected_level == PerformanceLevel.INSTANCE:
            return self.project_instances(scope)
        return self.project_groups(scope)

    def reconcile_scope(self, scope: PerformanceScope | str) -> PerformanceScopeReconciliation:
        selected_scope = self._scope(scope)
        expected_episodes = self._episodes_for_metric_scope(selected_scope)
        expected_metrics = self.aggregator.aggregate_episode_scope(episode.episode_id for episode in expected_episodes)
        instance_total = sum(
            (_number(row.aggregate.gross_pnl) for row in self.project_instances(selected_scope)),
            Decimal(0),
        )
        group_total = sum(
            (_number(row.aggregate.gross_pnl) for row in self.project_groups(selected_scope)),
            Decimal(0),
        )
        without_group = self.aggregator.aggregate_episode_scope(
            episode.episode_id for episode in expected_episodes if not episode.group_id
        ).gross_pnl
        expected = _number(expected_metrics.gross_pnl)
        missing_group = _number(without_group)
        return PerformanceScopeReconciliation(
            scope=selected_scope,
            expected_assigned_episode_gross_pnl=_json_number(expected),
            projected_instance_gross_pnl=_json_number(instance_total),
            projected_group_gross_pnl=_json_number(group_total),
            assigned_without_group_gross_pnl=_json_number(missing_group),
            instance_reconciled=expected == instance_total,
            group_reconciled=expected == group_total + missing_group,
        )

    def unresolved_summary(self, scope: PerformanceScope | str) -> tuple[int, int | float]:
        rows = self.project_stocks(scope)
        return (
            sum(row.lifetime.unresolved_allocation_count for row in rows),
            _json_number(sum((_number(row.lifetime.unresolved.gross_pnl) for row in rows), Decimal(0))),
        )

    def _instance_row(
        self,
        instance_id: str,
        scope: PerformanceScope,
        current_ids: set[str],
        episodes: tuple[AssignmentEpisode, ...],
    ) -> InstancePerformanceProjectionRow:
        identity_episodes = tuple(episode for episode in episodes if episode.instance_id == instance_id)
        metric_episodes = self._parent_scope_episodes(identity_episodes, scope)
        current_names = (
            relation.current_instance_name
            for relation in self.current_relations.stocks_by_code.values()
            if relation.current_instance_id == instance_id
        )
        aggregate = self.aggregator.aggregate_episode_scope(
            episode.episode_id for episode in metric_episodes
        )
        current_stock_codes = {
            relation.stock_code
            for relation in self.current_relations.stocks_by_code.values()
            if relation.current_instance_id == instance_id
        }
        return InstancePerformanceProjectionRow(
            instance_id=instance_id,
            identity=instance_id,
            visibility_reasons=self._parent_visibility_reasons(scope, instance_id in current_ids, identity_episodes),
            metric_scope=self._metric_scope_text(scope),
            episode_ids=tuple(sorted(episode.episode_id for episode in metric_episodes)),
            is_current_operational=instance_id in current_ids,
            is_historical=any(not episode.is_open for episode in identity_episodes),
            observed_instance_name_snapshots=_snapshot_names(
                [episode.instance_name_snapshot for episode in identity_episodes] + list(current_names)
            ),
            observed_group_ids=_snapshot_names(episode.group_id for episode in identity_episodes),
            observed_group_name_snapshots=_snapshot_names(episode.group_name_snapshot for episode in identity_episodes),
            aggregate=aggregate,
            performance_absence_reason=self._parent_performance_absence_reason(
                identity_episodes,
                aggregate,
                current_stock_codes,
            ),
        )

    def _group_row(
        self,
        group_id: str,
        scope: PerformanceScope,
        current_ids: set[str],
        episodes: tuple[AssignmentEpisode, ...],
    ) -> GroupPerformanceProjectionRow:
        identity_episodes = tuple(episode for episode in episodes if episode.group_id == group_id)
        metric_episodes = self._parent_scope_episodes(identity_episodes, scope)
        current_names = (
            relation.current_group_name
            for relation in self.current_relations.stocks_by_code.values()
            if relation.current_group_id == group_id
        )
        aggregate = self.aggregator.aggregate_episode_scope(
            episode.episode_id for episode in metric_episodes
        )
        current_stock_codes = {
            relation.stock_code
            for relation in self.current_relations.stocks_by_code.values()
            if relation.current_group_id == group_id
        }
        return GroupPerformanceProjectionRow(
            group_id=group_id,
            identity=group_id,
            visibility_reasons=self._parent_visibility_reasons(scope, group_id in current_ids, identity_episodes),
            metric_scope=self._metric_scope_text(scope),
            episode_ids=tuple(sorted(episode.episode_id for episode in metric_episodes)),
            is_current_operational=group_id in current_ids,
            is_historical=any(not episode.is_open for episode in identity_episodes),
            observed_group_name_snapshots=_snapshot_names(
                [episode.group_name_snapshot for episode in identity_episodes] + list(current_names)
            ),
            observed_instance_ids=_snapshot_names(episode.instance_id for episode in identity_episodes),
            observed_instance_name_snapshots=_snapshot_names(episode.instance_name_snapshot for episode in identity_episodes),
            aggregate=aggregate,
            performance_absence_reason=self._parent_performance_absence_reason(
                identity_episodes,
                aggregate,
                current_stock_codes,
            ),
        )

    def _current_consistency(
        self,
        stock_code: str,
        relation: CurrentStockRelation | None,
        episodes: tuple[AssignmentEpisode, ...],
    ) -> CurrentRelationConsistency:
        current_instance_id = relation.current_instance_id if relation else None
        current_group_id = relation.current_group_id if relation else None
        open_episode = next((episode for episode in episodes if episode.is_open), None)
        reasons: list[str] = []
        if current_instance_id and relation and not relation.instance_exists:
            reasons.append("CURRENT_INSTANCE_MISSING")
        if current_instance_id:
            if open_episode is None:
                reasons.append("OPEN_EPISODE_MISSING")
            elif open_episode.ownership_kind != ASSIGNED:
                reasons.append("OPEN_EPISODE_UNASSIGNED")
            else:
                if open_episode.instance_id != current_instance_id:
                    reasons.append("INSTANCE_ID_MISMATCH")
                if open_episode.group_id != current_group_id:
                    reasons.append("GROUP_ID_MISMATCH")
        elif open_episode is not None and open_episode.ownership_kind != UNASSIGNED:
            reasons.append("OPEN_ASSIGNED_EPISODE_WITHOUT_CURRENT_ASSIGNMENT")
        return CurrentRelationConsistency(
            consistent=not reasons,
            reasons=tuple(reasons),
            current_instance_id=current_instance_id,
            current_group_id=current_group_id,
            open_episode_id=open_episode.episode_id if open_episode else None,
            open_episode_ownership_kind=open_episode.ownership_kind if open_episode else None,
            open_episode_instance_id=open_episode.instance_id if open_episode else None,
            open_episode_group_id=open_episode.group_id if open_episode else None,
        )

    def _known_stock_codes(self) -> set[str]:
        return (
            set(self.current_relations.stocks_by_code)
            | set(self.aggregator.snapshot.episodes_by_stock)
            | set(self.aggregator.snapshot.events_by_stock)
        )

    def _episodes_for_metric_scope(self, scope: PerformanceScope) -> tuple[AssignmentEpisode, ...]:
        episodes = tuple(
            episode
            for episode in self.aggregator.snapshot.episodes_by_id.values()
            if episode.ownership_kind == ASSIGNED
        )
        return self._filter_metric_episodes(episodes, scope)

    @staticmethod
    def _filter_metric_episodes(
        episodes: tuple[AssignmentEpisode, ...],
        scope: PerformanceScope,
    ) -> tuple[AssignmentEpisode, ...]:
        if scope == PerformanceScope.CURRENT:
            return tuple(episode for episode in episodes if episode.is_open)
        if scope == PerformanceScope.PAST:
            return tuple(episode for episode in episodes if not episode.is_open)
        return episodes

    def _parent_scope_episodes(
        self,
        episodes: tuple[AssignmentEpisode, ...],
        scope: PerformanceScope,
    ) -> tuple[AssignmentEpisode, ...]:
        if scope == PerformanceScope.CURRENT:
            return tuple(episode for episode in episodes if episode.is_open)
        historical = tuple(
            episode
            for episode in episodes
            if not episode.is_open
            and episode.episode_id in self._resolved_performance_episode_ids
        )
        if scope == PerformanceScope.PAST:
            return historical
        return tuple(episode for episode in episodes if episode.is_open) + historical

    def _parent_performance_absence_reason(
        self,
        identity_episodes: tuple[AssignmentEpisode, ...],
        aggregate: AggregateMetrics,
        current_stock_codes: set[str],
    ) -> str | None:
        if aggregate.event_count:
            return None
        all_identity = self.aggregator.aggregate_episode_scope(
            episode.episode_id for episode in identity_episodes
        )
        if all_identity.event_count:
            return "NO_PERFORMANCE_IN_SCOPE"
        stock_codes = {
            episode.stock_code for episode in identity_episodes
        } | current_stock_codes
        if any(
            self.aggregator.snapshot.events_by_stock.get(stock_code)
            for stock_code in stock_codes
        ):
            return "NO_RESOLVED_PERFORMANCE_OWNERSHIP"
        return "NO_CANONICAL_PERFORMANCE_EVENTS"

    @staticmethod
    def _parent_ids(
        scope: PerformanceScope,
        current_ids: set[str],
        historical_ids: set[str],
    ) -> tuple[str, ...]:
        if scope == PerformanceScope.CURRENT:
            values = current_ids
        elif scope == PerformanceScope.PAST:
            values = historical_ids
        else:
            values = current_ids | historical_ids
        return tuple(sorted(values))

    @staticmethod
    def _stock_visibility_reasons(
        scope: PerformanceScope,
        *,
        currently_assigned: bool,
        has_ledger: bool,
    ) -> tuple[str, ...]:
        if scope == PerformanceScope.CURRENT:
            return ("CURRENT_ASSIGNMENT",)
        if scope == PerformanceScope.PAST:
            return ("PERFORMANCE_LEDGER",)
        reasons: list[str] = []
        if currently_assigned:
            reasons.append("CURRENT_ASSIGNMENT")
        if has_ledger:
            reasons.append("PERFORMANCE_LEDGER")
        return tuple(reasons or ("KNOWN_STOCK",))

    def _parent_visibility_reasons(
        self,
        scope: PerformanceScope,
        is_current: bool,
        episodes: tuple[AssignmentEpisode, ...],
    ) -> tuple[str, ...]:
        if scope == PerformanceScope.CURRENT:
            return ("CURRENT_RELATION",)
        if scope == PerformanceScope.PAST:
            return ("HISTORICAL_PERFORMANCE",)
        reasons = []
        if is_current:
            reasons.append("CURRENT_RELATION")
        if any(
            not episode.is_open
            and episode.episode_id in self._resolved_performance_episode_ids
            for episode in episodes
        ):
            reasons.append("HISTORICAL_PERFORMANCE")
        return tuple(reasons)

    @staticmethod
    def _metric_scope_text(scope: PerformanceScope) -> str:
        if scope == PerformanceScope.CURRENT:
            return "OPEN_ASSIGNED_EPISODES"
        if scope == PerformanceScope.PAST:
            return "CLOSED_ASSIGNED_EPISODES"
        return "ALL_ASSIGNED_EPISODES"

    @staticmethod
    def _row_name(names: tuple[str, ...]) -> str:
        return names[0].casefold() if names else ""

    @staticmethod
    def _scope(value: PerformanceScope | str) -> PerformanceScope:
        return value if isinstance(value, PerformanceScope) else PerformanceScope(_text(value).upper())
