# -*- coding: utf-8 -*-
"""Immutable read-only Stock/Routine references for Mock Validation sessions."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from mock_validation_contract import (
    MockValidationError,
    clean_text,
    normalized_stock_code,
    now_text,
    payload_hash,
    validate_reference_snapshot,
)


def _field(source: Any, name: str) -> Any:
    if isinstance(source, Mapping):
        return source.get(name)
    return getattr(source, name, None)


def build_mock_reference_snapshot(
    *,
    stock: Mapping[str, Any],
    routine_instances: list[Any] | tuple[Any, ...],
    rules_by_instance_id: Mapping[str, Mapping[str, Any]],
    created_at: str | None = None,
) -> dict[str, Any]:
    """Copy Production read models into an immutable Mock-owned snapshot.

    No repository object is retained.  Callers must perform Production reads
    before invoking this function and supply plain mappings/records.
    """

    code = normalized_stock_code(stock.get("code") or stock.get("stock_code"))
    name = clean_text(stock.get("name") or stock.get("stock_name"))
    if not name:
        raise MockValidationError("MOCK_STOCK_NAME_MISSING")
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for instance in routine_instances:
        instance_id = clean_text(_field(instance, "instance_id") or _field(instance, "routine_instance_id"))
        if not instance_id or instance_id in seen:
            raise MockValidationError("MOCK_ROUTINE_INSTANCE_ID_INVALID")
        seen.add(instance_id)
        rules = rules_by_instance_id.get(instance_id)
        if not isinstance(rules, Mapping):
            raise MockValidationError("MOCK_ROUTINE_RULES_SNAPSHOT_MISSING")
        rules_snapshot = deepcopy(dict(rules))
        records.append(
            {
                "routine_instance_id": instance_id,
                "routine_definition_id": clean_text(_field(instance, "definition_id") or _field(instance, "routine_definition_id")),
                "routine_type": clean_text(_field(instance, "routine_type")),
                "routine_instance_name": clean_text(_field(instance, "display_name") or _field(instance, "routine_instance_name")),
                "group_id": clean_text(_field(instance, "group_id")),
                "rules_hash": payload_hash(rules_snapshot),
                "rules_snapshot": rules_snapshot,
            }
        )
    snapshot = {
        "stock_code": code,
        "stock_name": name,
        "stock_identity_reference": {
            "stock_path": clean_text(stock.get("stock_path")),
            "identity_hash": payload_hash(
                {
                    "stock_code": code,
                    "stock_name": name,
                    "stock_path": clean_text(stock.get("stock_path")),
                }
            ),
        },
        "routine_instances": records,
        "snapshot_created_at": clean_text(created_at) or now_text(),
    }
    snapshot["snapshot_hash"] = payload_hash(snapshot)
    return validate_reference_snapshot(snapshot)


__all__ = ["build_mock_reference_snapshot"]
