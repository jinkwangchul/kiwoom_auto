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
    _text,
    build_current_performance_relations,
)
from gui_auto_trade_display import profit_loss_value_color
from gui_common_utils import safe_int_value
from gui_order_utils import (
    directional_value_color,
    format_signed_money,
    format_signed_percent,
    numeric_order_value,
    order_datetime,
    parse_order_datetime_value,
    summarize_orders,
)
from runtime_io import read_orders_data


_UI_SCOPE = {
    "all": PerformanceScope.ALL,
    "current": PerformanceScope.CURRENT,
    "historical": PerformanceScope.PAST,
}


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


def normalize_profit_factor(value: object) -> float:
    """PF display input is non-negative; unavailable values normalize to zero."""
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return 0.0


def routine_tree_stock_performance_source(
    context: object,
    stock: dict[str, object],
) -> dict[str, object]:
    if bool(stock.get("is_development_fixture", False)):
        fixture = stock.get("performance_fixture")
        if isinstance(fixture, dict):
            profit_factor = normalize_profit_factor(
                fixture.get("profit_factor", fixture.get("efficiency"))
            )
            return {
                **fixture,
                "gross_profit": fixture.get("gross_profit"),
                "gross_loss_abs": fixture.get("gross_loss_abs"),
                "profit_factor": profit_factor,
                "is_current": False,
            }
    stock_path = Path(str(stock.get("stock_path", "") or "").strip())
    if not stock_path.is_absolute():
        stock_path = Path(__file__).resolve().parent / stock_path
    snapshot = getattr(context, "_auto_trade_initial_read_snapshot", None)
    snapshot = snapshot if isinstance(snapshot, dict) else None
    snapshot_data = (
        snapshot.get("stock_data_by_dir", {}).get(str(stock_path), {})
        if snapshot is not None
        else {}
    )
    orders = (
        list(snapshot_data.get("orders", ()))
        if snapshot_data
        else read_orders_data(stock_path / "orders.json")
    )
    is_historical = bool(stock.get("is_historical", False))
    if is_historical:
        registered_at = parse_order_datetime_value(stock.get("registered_at"))
        unregistered_at = parse_order_datetime_value(stock.get("unregistered_at"))
        orders = [
            order
            for order in orders
            if (parsed := order_datetime(order)) is not None
            and (registered_at is None or parsed >= registered_at)
            and (unregistered_at is None or parsed <= unregistered_at)
        ]
    filled_orders = [
        order
        for order in orders
        if numeric_order_value(
            order,
            ["filled_qty", "filled", "executed_qty"],
            0.0,
        )
        > 0
    ]
    trade_dates = {
        parsed.date()
        for order in filled_orders
        if (parsed := order_datetime(order)) is not None
    }
    realized_profit: float | None = None
    if filled_orders:
        realized_profit = float(
            summarize_orders(orders).get("realized_pnl", 0.0) or 0.0
        )
    return {
        "trade_days": len(trade_dates) if trade_dates else None,
        "realized_profit": realized_profit,
        "profit_rate": None,
        "average": None,
        "average_rate": None,
        "gross_profit": None,
        "gross_loss_abs": None,
        "profit_factor": 0.0,
        "is_current": bool(stock.get("is_current", not is_historical)),
    }


def routine_tree_performance_texts(
    context: object,
    stocks: list[dict[str, object]],
    source_cache: dict[str, dict[str, object]] | None = None,
) -> dict[str, object]:
    cache = source_cache if source_cache is not None else {}
    source_rows: list[dict[str, object]] = []
    for stock in stocks:
        stock_path_key = str(stock.get("stock_path", "") or "").strip()
        is_historical = bool(stock.get("is_historical", False))
        cache_key = stock_path_key
        if is_historical or not cache_key:
            cache_key = "|".join(
                (
                    str(stock.get("instance_id", "") or "").strip(),
                    str(stock.get("stock_code", "") or "").strip(),
                    stock_path_key,
                    "historical" if is_historical else "current",
                )
            )
        if cache_key not in cache:
            cache[cache_key] = routine_tree_stock_performance_source(context, stock)
        source_rows.append(cache[cache_key])
    trade_days = [
        int(source.get("trade_days", 0) or 0)
        for source in source_rows
        if int(source.get("trade_days", 0) or 0) > 0
    ]
    period_text = "0"
    if len(stocks) == 1 and trade_days:
        period_text = str(trade_days[0])
    elif len(stocks) > 1 and trade_days:
        period_text = str(safe_int_value(sum(trade_days) / len(trade_days), 0))

    realized_values = [
        float(source["realized_profit"])
        for source in source_rows
        if source.get("realized_profit") is not None
    ]
    profit_value = sum(realized_values) if realized_values else 0.0
    profit_amount_text = format_signed_money(profit_value)
    profit_rate_value = (
        source_rows[0].get("profit_rate") if len(source_rows) == 1 else None
    )
    profit_rate_text = format_signed_percent(
        profit_rate_value if profit_rate_value is not None else 0.0,
        digits=2,
    )
    average_values = [
        float(source["average"])
        for source in source_rows
        if source.get("average") is not None
    ]
    average_value = (
        sum(average_values) / len(average_values) if average_values else 0.0
    )
    average_rate_values = [
        float(source["average_rate"])
        for source in source_rows
        if source.get("average_rate") is not None
    ]
    average_rate_value = (
        sum(average_rate_values) / len(average_rate_values)
        if average_rate_values
        else 0.0
    )
    average_amount_text = format_signed_money(average_value)
    average_rate_text = format_signed_percent(average_rate_value, digits=2)
    profit_factor_value = (
        source_rows[0].get(
            "profit_factor",
            source_rows[0].get("efficiency"),
        )
        if len(source_rows) == 1
        else 0.0
    )
    profit_factor_value = normalize_profit_factor(profit_factor_value)
    efficiency_text = f"{profit_factor_value:.1f}"

    return {
        "performance_period_text": f"기간({period_text})",
        "performance_profit_text": (
            f"수익({profit_amount_text} / {profit_rate_text})"
        ),
        "performance_average_text": (
            f"평균({average_amount_text} / {average_rate_text})"
        ),
        "performance_efficiency_text": f"효율({efficiency_text})",
        "performance_period_value": period_text,
        "performance_profit_amount": profit_amount_text,
        "performance_profit_rate": profit_rate_text,
        "performance_profit_color": profit_loss_value_color(profit_value),
        "performance_average_amount": average_amount_text,
        "performance_average_rate": average_rate_text,
        "performance_average_color": profit_loss_value_color(
            average_value if average_values else None
        ),
        "performance_efficiency_value": efficiency_text,
        "performance_efficiency_color": directional_value_color(
            profit_factor_value
        ),
        "performance_period_sort_value": float(sum(trade_days)),
        "performance_profit_sort_value": float(profit_value),
        "performance_average_sort_value": float(average_value),
        "performance_efficiency_sort_value": float(profit_factor_value),
    }
