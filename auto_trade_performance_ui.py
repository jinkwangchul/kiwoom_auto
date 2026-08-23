"""Read-only canonical performance snapshot for the AutoTrade tree UI."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from typing import Iterable, Mapping

from assignment_episode_repository import CanonicalAssignmentEpisodeRepository
from performance_aggregator import CanonicalPerformanceAggregator
from performance_ledger_repository import CanonicalStockPerformanceLedgerRepository
from performance_metrics import CanonicalPerformanceMetricEngine, CanonicalPerformanceMetrics
from performance_scope_projection import (
    GroupPerformanceProjectionRow,
    InstancePerformanceProjectionRow,
    PerformanceScope,
    PerformanceScopeProjection,
    StockPerformanceProjectionRow,
    build_current_performance_relations,
)


_UI_SCOPE = {
    "all": PerformanceScope.ALL,
    "current": PerformanceScope.CURRENT,
    "historical": PerformanceScope.PAST,
}


def _text(value: object) -> str:
    return str(value or "").strip()


def _value(source: object, name: str, default: object = "") -> object:
    if isinstance(source, Mapping):
        return source.get(name, default)
    return getattr(source, name, default)


def _stock_relation_value(stock: object) -> SimpleNamespace:
    return SimpleNamespace(
        code=_text(_value(stock, "code")),
        name=_text(_value(stock, "name")),
        assigned_routine_instance_id=_text(
            _value(stock, "assigned_routine_instance_id")
        ),
        routine_instance_name=_text(_value(stock, "routine_instance_name")),
    )


@dataclass(frozen=True)
class CanonicalPerformanceUiSnapshot:
    """One AutoTrade refresh cycle's immutable canonical read model."""

    aggregator: CanonicalPerformanceAggregator
    projection: PerformanceScopeProjection
    metrics: CanonicalPerformanceMetricEngine
    stocks_by_scope: Mapping[str, tuple[StockPerformanceProjectionRow, ...]]
    instances_by_scope: Mapping[str, tuple[InstancePerformanceProjectionRow, ...]]
    groups_by_scope: Mapping[str, tuple[GroupPerformanceProjectionRow, ...]]
    current_stock_details: Mapping[str, dict[str, object]]
    current_instances: Mapping[str, object]
    current_groups: Mapping[str, object]

    @property
    def ledger_document_reads(self) -> int:
        return self.aggregator.snapshot.ledger_document_reads

    @property
    def episode_document_reads(self) -> int:
        return self.aggregator.snapshot.episode_document_reads

    def scope(self, ui_scope: str) -> PerformanceScope:
        return _UI_SCOPE.get(_text(ui_scope).lower(), PerformanceScope.ALL)

    def stock_rows(self, ui_scope: str) -> tuple[StockPerformanceProjectionRow, ...]:
        return self.stocks_by_scope[self.scope(ui_scope).value]

    def instance_rows(self, ui_scope: str) -> tuple[InstancePerformanceProjectionRow, ...]:
        return self.instances_by_scope[self.scope(ui_scope).value]

    def group_rows(self, ui_scope: str) -> tuple[GroupPerformanceProjectionRow, ...]:
        return self.groups_by_scope[self.scope(ui_scope).value]

    def metric_result(self, row: object) -> CanonicalPerformanceMetrics:
        aggregate = getattr(row, "lifetime", None) or getattr(row, "aggregate")
        return self.metrics.calculate(aggregate)

    def instance_stock_codes(self, row: InstancePerformanceProjectionRow) -> tuple[str, ...]:
        return self._episode_stock_codes(row.episode_ids)

    def group_stock_codes(self, row: GroupPerformanceProjectionRow) -> tuple[str, ...]:
        return self._episode_stock_codes(row.episode_ids)

    def instance_name(
        self,
        row: InstancePerformanceProjectionRow,
        *,
        prefer_episode_snapshot: bool = False,
    ) -> str:
        current = self.current_instances.get(row.instance_id)
        if not prefer_episode_snapshot and row.is_current_operational and current is not None:
            name = _text(_value(current, "display_name"))
            if name:
                return name
        return self._latest_episode_name(row.episode_ids, "instance_name_snapshot") or row.instance_id

    def group_name(
        self,
        row: GroupPerformanceProjectionRow,
        *,
        prefer_episode_snapshot: bool = False,
    ) -> str:
        current = self.current_groups.get(row.group_id)
        if not prefer_episode_snapshot and row.is_current_operational and current is not None:
            name = _text(_value(current, "display_name"))
            if name:
                return name
        return self._latest_episode_name(row.episode_ids, "group_name_snapshot") or row.group_id

    def stock_name(self, row: StockPerformanceProjectionRow) -> str:
        return row.stock_name or _text(
            self.current_stock_details.get(row.stock_code, {}).get("stock_name")
        ) or row.stock_code

    def identity_tooltip(self, row: object) -> str:
        values: list[str] = []
        for field in (
            "observed_group_name_snapshots",
            "observed_instance_name_snapshots",
        ):
            values.extend(str(value) for value in getattr(row, field, ()) if str(value))
        stable_id = _text(
            getattr(row, "group_id", "")
            or getattr(row, "instance_id", "")
            or getattr(row, "stock_code", "")
        )
        lines = [f"ID: {stable_id}"] if stable_id else []
        if values:
            lines.append("이름 이력: " + " / ".join(dict.fromkeys(values)))
        mismatch_reasons = self.current_relation_mismatch_reasons(row)
        if mismatch_reasons:
            lines.append("CURRENT 관계 불일치: " + ", ".join(mismatch_reasons))
        return "\n".join(lines)

    def current_relation_mismatch_reasons(self, row: object) -> tuple[str, ...]:
        """Return current-relation diagnostics for a stock or CURRENT parent row."""
        consistency = getattr(row, "current_relation_consistency", None)
        if consistency is not None:
            return () if consistency.consistent else tuple(consistency.reasons)

        if _text(getattr(row, "metric_scope", "")) != "OPEN_ASSIGNED_EPISODES":
            return ()
        instance_id = _text(getattr(row, "instance_id", ""))
        group_id = _text(getattr(row, "group_id", ""))
        reasons: list[str] = []
        for stock_row in self.stocks_by_scope[PerformanceScope.CURRENT.value]:
            if instance_id and stock_row.current_instance_id != instance_id:
                continue
            if group_id and stock_row.current_group_id != group_id:
                continue
            relation = stock_row.current_relation_consistency
            if relation.consistent:
                continue
            reasons.extend(relation.reasons)
        return tuple(dict.fromkeys(reasons))

    def current_stock_detail(self, stock_code: str) -> dict[str, object]:
        return dict(self.current_stock_details.get(_text(stock_code), {}))

    def _episode_stock_codes(self, episode_ids: Iterable[str]) -> tuple[str, ...]:
        values = {
            episode.stock_code
            for episode_id in episode_ids
            if (episode := self.aggregator.snapshot.episodes_by_id.get(episode_id)) is not None
        }
        return tuple(sorted(values))

    def _latest_episode_name(self, episode_ids: Iterable[str], field: str) -> str:
        episodes = [
            episode
            for episode_id in episode_ids
            if (episode := self.aggregator.snapshot.episodes_by_id.get(episode_id)) is not None
            and _text(getattr(episode, field, ""))
        ]
        if not episodes:
            return ""
        return _text(getattr(max(episodes, key=lambda item: item.started_at), field))


def build_canonical_performance_ui_snapshot(
    project_root: Path | str,
    *,
    stocks: Iterable[object],
    instances: Iterable[object],
    groups: Iterable[object],
) -> CanonicalPerformanceUiSnapshot:
    """Build exactly one repository/aggregation/projection snapshot."""
    root = Path(project_root)
    stock_values = tuple(stocks)
    instance_values = tuple(instances)
    group_values = tuple(groups)
    episode_repository = CanonicalAssignmentEpisodeRepository(root)
    ledger_repository = CanonicalStockPerformanceLedgerRepository(
        root,
        episode_repository=episode_repository,
    )
    aggregator = CanonicalPerformanceAggregator(ledger_repository, episode_repository)
    current_relations = build_current_performance_relations(
        (_stock_relation_value(stock) for stock in stock_values),
        instance_values,
        group_values,
    )
    projection = PerformanceScopeProjection(aggregator, current_relations)
    stocks_by_scope = {
        scope.value: projection.project_stocks(scope) for scope in PerformanceScope
    }
    instances_by_scope = {
        scope.value: projection.project_instances(scope) for scope in PerformanceScope
    }
    groups_by_scope = {
        scope.value: projection.project_groups(scope) for scope in PerformanceScope
    }
    stock_details = {
        _text(_value(stock, "code")): {
            "stock_code": _text(_value(stock, "code")),
            "stock_name": _text(_value(stock, "name")),
            "stock_path": _text(_value(stock, "stock_path")),
            "instance_id": _text(_value(stock, "assigned_routine_instance_id")),
        }
        for stock in stock_values
        if _text(_value(stock, "code"))
    }
    return CanonicalPerformanceUiSnapshot(
        aggregator=aggregator,
        projection=projection,
        metrics=CanonicalPerformanceMetricEngine(),
        stocks_by_scope=MappingProxyType(stocks_by_scope),
        instances_by_scope=MappingProxyType(instances_by_scope),
        groups_by_scope=MappingProxyType(groups_by_scope),
        current_stock_details=MappingProxyType(stock_details),
        current_instances=MappingProxyType(
            {
                _text(_value(instance, "instance_id")): instance
                for instance in instance_values
                if _text(_value(instance, "instance_id"))
            }
        ),
        current_groups=MappingProxyType(
            {
                _text(_value(group, "group_id")): group
                for group in group_values
                if _text(_value(group, "group_id"))
            }
        ),
    )
