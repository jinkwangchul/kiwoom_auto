"""Read-only projection from logical Groups and explicit assignments."""

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
        return str(getattr(self.group, "group_id", "") or "").strip()

    @property
    def group_path(self) -> Path:
        return Path(getattr(self.group, "path", ""))

    @property
    def display_name(self) -> str:
        return str(
            getattr(self.group, "display_name", "")
            or getattr(self.group, "name", "")
            or ""
        ).strip()


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
            str(
                getattr(group, "display_name", "")
                or getattr(group, "name", "")
                or ""
            ).casefold(),
            str(getattr(group, "group_id", "") or "").casefold(),
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
    stocks_by_instance: dict[
        str,
        dict[tuple[str, str, str], dict[str, object]],
    ] = {}
    for stock in stocks:
        if not isinstance(stock, dict):
            continue
        instance_id = str(stock.get("assigned_routine_instance_id", "") or "").strip()
        if instance_id not in instance_by_id:
            continue
        stock_identity = _stock_identity(stock)
        stocks_by_instance.setdefault(instance_id, {})[stock_identity] = stock

    projected: list[ProjectedMainGroup] = []
    for group in ordered_groups:
        group_id = str(getattr(group, "group_id", "") or "").strip()
        projected_instances: list[ProjectedGroupInstance] = []
        for instance_id, instance in instance_by_id.items():
            explicit_group_id = explicit_group_id_by_instance.get(instance_id, "")
            if not explicit_group_id or explicit_group_id != group_id:
                continue
            related = stocks_by_instance.get(instance_id, {})
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
