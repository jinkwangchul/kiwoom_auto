"""Read-only Group -> RoutineInstance -> Stock scope projection."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Iterable, Mapping

from gui_routine_registry import scan_group_records
from main_group_projection import build_main_group_projection
from routine_instance_registry import load_persisted_routine_instances
from stock_repository import StockRecord, StockRepository


@dataclass(frozen=True)
class GroupScopeSnapshot:
    project_root: Path
    groups_by_id: Mapping[str, object]
    group_instance_ids_by_id: Mapping[str, tuple[str, ...]]
    stocks_by_group_id: Mapping[str, tuple[StockRecord, ...]]
    stocks_by_instance_id: Mapping[str, tuple[StockRecord, ...]]

    def group_instance_ids(self, group_id: str) -> tuple[str, ...]:
        return self.group_instance_ids_by_id.get(str(group_id or "").strip(), ())

    def group_stocks(self, group_id: str) -> tuple[StockRecord, ...]:
        return self.stocks_by_group_id.get(str(group_id or "").strip(), ())

    def instance_stocks(self, instance_id: str) -> tuple[StockRecord, ...]:
        return self.stocks_by_instance_id.get(str(instance_id or "").strip(), ())

    def stock_path(self, stock: StockRecord) -> Path:
        path = Path(stock.stock_path)
        return path if path.is_absolute() else self.project_root / path

    def group_stock_dirs(self, group_id: str) -> tuple[Path, ...]:
        return tuple(self.stock_path(stock) for stock in self.group_stocks(group_id))

    def instance_stock_dirs(self, instance_id: str) -> tuple[Path, ...]:
        return tuple(self.stock_path(stock) for stock in self.instance_stocks(instance_id))

    def all_group_stock_dirs(self) -> tuple[Path, ...]:
        unique: dict[str, Path] = {}
        for group_id in self.groups_by_id:
            for path in self.group_stock_dirs(group_id):
                unique.setdefault(str(path.resolve(strict=False)), path)
        return tuple(sorted(unique.values(), key=lambda path: path.name))


def build_group_scope(
    project_root: Path | str,
    groups: Iterable[object],
    instances: Iterable[object],
    stocks: Iterable[StockRecord],
) -> GroupScopeSnapshot:
    root = Path(project_root).resolve(strict=False)
    instance_records = tuple(instances)
    instance_ids = {
        str(getattr(instance, "instance_id", "") or "").strip()
        for instance in instance_records
        if str(getattr(instance, "instance_id", "") or "").strip()
    }
    stock_records = tuple(stocks)
    records_by_path = {
        str(stock.stock_path or "").strip(): stock
        for stock in stock_records
        if str(stock.stock_path or "").strip()
    }
    projection = build_main_group_projection(
        groups,
        instance_records,
        (stock.to_base_stock_dict() for stock in stock_records),
    )
    groups_by_id: dict[str, object] = {}
    instance_ids_by_group: dict[str, tuple[str, ...]] = {}
    stocks_by_group: dict[str, tuple[StockRecord, ...]] = {}
    direct_stocks_by_instance: dict[str, list[StockRecord]] = {
        instance_id: [] for instance_id in instance_ids
    }
    for stock in stock_records:
        instance_id = str(stock.assigned_routine_instance_id or "").strip()
        if instance_id in direct_stocks_by_instance:
            direct_stocks_by_instance[instance_id].append(stock)
    stocks_by_instance: dict[str, tuple[StockRecord, ...]] = {
        instance_id: tuple(sorted(values, key=lambda item: (item.code, item.name)))
        for instance_id, values in direct_stocks_by_instance.items()
    }
    for projected_group in projection:
        group_id = projected_group.group_id
        groups_by_id[group_id] = projected_group.group
        instance_ids_by_group[group_id] = tuple(
            item.instance_id for item in projected_group.instances
        )
        group_records: dict[str, StockRecord] = {}
        for projected_instance in projected_group.instances:
            for stock in projected_instance.stocks:
                stock_path = str(stock.get("stock_path", "") or "").strip()
                record = records_by_path.get(stock_path)
                if record is None:
                    continue
                group_records[stock_path] = record
        stocks_by_group[group_id] = tuple(
            sorted(group_records.values(), key=lambda item: (item.code, item.name))
        )

    return GroupScopeSnapshot(
        project_root=root,
        groups_by_id=MappingProxyType(groups_by_id),
        group_instance_ids_by_id=MappingProxyType(instance_ids_by_group),
        stocks_by_group_id=MappingProxyType(stocks_by_group),
        stocks_by_instance_id=MappingProxyType(stocks_by_instance),
    )


def load_group_scope(project_root: Path | str | None = None) -> GroupScopeSnapshot:
    root = Path(project_root or Path(__file__).resolve().parent).resolve(strict=False)
    return build_group_scope(
        root,
        scan_group_records(project_root=root),
        load_persisted_routine_instances(project_root=root),
        StockRepository(root).list_stocks(),
    )
