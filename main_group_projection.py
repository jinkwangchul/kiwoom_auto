"""Read-only Main projection from root Groups and existing stock assignments."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class ProjectedGroupInstance:
    instance: object
    stocks: tuple[dict[str, object], ...]

    @property
    def instance_id(self) -> str:
        return str(getattr(self.instance, "instance_id", "") or "").strip()


@dataclass(frozen=True)
class ProjectedMainGroup:
    group: object
    instances: tuple[ProjectedGroupInstance, ...]

    @property
    def group_id(self) -> str:
        path = Path(getattr(self.group, "path", ""))
        return str(path.resolve(strict=False))

    @property
    def group_path(self) -> Path:
        return Path(getattr(self.group, "path", ""))

    @property
    def display_name(self) -> str:
        return str(getattr(self.group, "name", "") or "").strip()


def stock_group_assignment_names(stock: dict[str, object]) -> tuple[str, ...]:
    """Read the existing central-stock Group assignment compatibility field."""
    raw = stock.get("routines", ())
    values = raw if isinstance(raw, (list, tuple, set)) else (raw,)
    names: list[str] = []
    for value in values:
        name = str(value or "").strip()
        if name and name not in names:
            names.append(name)
    return tuple(names)


def _stock_identity(stock: dict[str, object]) -> tuple[str, str, str]:
    return (
        str(stock.get("stock_path", "") or "").strip(),
        str(stock.get("code", "") or "").strip(),
        str(stock.get("name", "") or "").strip(),
    )


def build_main_group_projection(
    groups: Iterable[object],
    instances: Iterable[object],
    stocks: Iterable[dict[str, object]],
) -> tuple[ProjectedMainGroup, ...]:
    """Project Group -> RoutineInstance -> Stock without persistence or mutation."""
    ordered_groups = sorted(
        groups,
        key=lambda group: (
            str(getattr(group, "name", "") or "").casefold(),
            str(Path(getattr(group, "path", ""))).casefold(),
        ),
    )
    instance_by_id = {
        instance_id: instance
        for instance in instances
        if (instance_id := str(getattr(instance, "instance_id", "") or "").strip())
    }
    explicit_group_id_by_instance = {
        instance_id: str(getattr(instance, "group_id", "") or "").strip()
        for instance_id, instance in instance_by_id.items()
    }
    groups_by_name: dict[str, list[object]] = {}
    for group in ordered_groups:
        name = str(getattr(group, "name", "") or "").strip()
        path = Path(getattr(group, "path", ""))
        if not name or not path:
            continue
        groups_by_name.setdefault(name, []).append(group)

    stocks_by_relation: dict[tuple[str, str], dict[tuple[str, str, str], dict[str, object]]] = {}
    for stock in stocks:
        if not isinstance(stock, dict):
            continue
        instance_id = str(stock.get("assigned_routine_instance_id", "") or "").strip()
        if instance_id not in instance_by_id:
            continue
        for group_name in stock_group_assignment_names(stock):
            matching_groups = groups_by_name.get(group_name, ())
            if len(matching_groups) != 1:
                continue
            group = matching_groups[0]
            group_id = str(Path(getattr(group, "path", "")).resolve(strict=False))
            stocks_by_relation.setdefault((group_id, instance_id), {})[
                _stock_identity(stock)
            ] = stock

    projected: list[ProjectedMainGroup] = []
    for group in ordered_groups:
        group_id = str(Path(getattr(group, "path", "")).resolve(strict=False))
        projected_instances: list[ProjectedGroupInstance] = []
        for instance_id, instance in instance_by_id.items():
            related = stocks_by_relation.get((group_id, instance_id), {})
            explicit_group_id = explicit_group_id_by_instance.get(instance_id, "")
            if explicit_group_id:
                if explicit_group_id != group_id:
                    continue
            elif not related:
                continue
            projected_instances.append(
                ProjectedGroupInstance(
                    instance=instance,
                    stocks=tuple(
                        sorted(
                            related.values(),
                            key=lambda stock: (
                                str(stock.get("code", "") or ""),
                                str(stock.get("name", "") or "").casefold(),
                                str(stock.get("stock_path", "") or "").casefold(),
                            ),
                        )
                    ),
                )
            )
        projected_instances.sort(
            key=lambda item: (
                str(getattr(item.instance, "display_name", "") or "").casefold(),
                item.instance_id,
            )
        )
        projected.append(
            ProjectedMainGroup(group=group, instances=tuple(projected_instances))
        )
    return tuple(projected)
