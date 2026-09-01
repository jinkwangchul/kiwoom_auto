# -*- coding: utf-8 -*-
"""Preview-only execution runtime write candidates.

This module builds in-memory record previews for future order_executions.json
and order_locks.json writes. It never creates runtime files, writes files,
creates directories, performs atomic replacement, commits queues, or calls
execution/order components.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from execution_runtime_catalog_preview import RUNTIME_TARGETS
from execution_provenance_contract import same_payload, validate_process_record


WRITE_PREVIEW_TYPE = "EXECUTION_RUNTIME_WRITE_PREVIEW"
STATUS_READY = "READY"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _catalog_from_input(value: Any) -> tuple[dict[str, Any], bool]:
    if not isinstance(value, dict):
        return {}, False
    if isinstance(value.get("catalog_preview"), dict):
        return deepcopy(value["catalog_preview"]), True
    return deepcopy(value), True


def _base_result(
    *,
    status: str,
    catalog: dict[str, Any] | None = None,
    execution_record_preview: dict[str, Any] | None = None,
    process_record_preview: dict[str, Any] | None = None,
    lock_record_preview: dict[str, Any] | None = None,
    append_requirements: dict[str, bool] | None = None,
    idempotent_existing: bool = False,
    issues: list[str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    source = _as_dict(catalog)
    duplicate_checks = {
        "execution_id": source.get("execution_id"),
        "request_hash": source.get("request_hash"),
        "order_id": source.get("order_id"),
        "lock_id": source.get("lock_id"),
    }
    runtime_targets = source.get("runtime_targets")
    return {
        "status": status,
        "preview_only": True,
        "runtime_write": False,
        "write_preview_type": WRITE_PREVIEW_TYPE,
        "execution_record_preview": deepcopy(execution_record_preview),
        "process_record_preview": deepcopy(process_record_preview),
        "lock_record_preview": deepcopy(lock_record_preview),
        "append_requirements": deepcopy(append_requirements or {}),
        "idempotent_existing": idempotent_existing,
        "duplicate_checks": deepcopy(duplicate_checks),
        "would_write_targets": deepcopy(runtime_targets if isinstance(runtime_targets, dict) else RUNTIME_TARGETS),
        "issues": list(issues or []),
        "warnings": list(warnings or []),
    }


def _existing_items(data: Any, field: str) -> tuple[list[dict[str, Any]], str | None]:
    if not isinstance(data, dict):
        return [], "MALFORMED_EXISTING_DATA"
    items = data.get(field)
    if not isinstance(items, list):
        return [], f"MALFORMED_{field.upper()}_FIELD"
    return [_as_dict(item) for item in items if isinstance(item, dict)], None


def _optional_existing_items(data: Any, field: str) -> tuple[list[dict[str, Any]], str | None]:
    if not isinstance(data, dict):
        return [], "MALFORMED_EXISTING_DATA"
    if field not in data:
        return [], None
    return _existing_items(data, field)


def _duplicate_issue(
    items: list[dict[str, Any]],
    *,
    fields: tuple[str, ...],
    source: dict[str, Any],
) -> str | None:
    for field in fields:
        target = _clean_text(source.get(field))
        if not target:
            continue
        for item in items:
            if _clean_text(item.get(field)) == target:
                return f"DUPLICATE_{field.upper()}"
    return None


def _matching_identity_records(
    items: list[dict[str, Any]],
    *,
    fields: tuple[str, ...],
    source: dict[str, Any],
) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for item in items:
        if any(
            _clean_text(source.get(field))
            and _clean_text(item.get(field)) == _clean_text(source.get(field))
            for field in fields
        ):
            matches.append(item)
    return matches


def _execution_record(catalog: dict[str, Any]) -> dict[str, Any]:
    record = {
        "execution_id": catalog.get("execution_id"),
        "order_id": catalog.get("order_id"),
        "request_hash": catalog.get("request_hash"),
        "lock_id": catalog.get("lock_id"),
        "status": "RUNTIME_WRITE_PREVIEW",
        "source": "execution_runtime_catalog_preview",
        "preview_only": True,
        "runtime_write": False,
    }
    provenance = _as_dict(catalog.get("provenance"))
    for field in (
        "execution_process_id",
        "child_sequence_index",
        "child_sequence_total",
        "child_kind",
        "child_plan",
        "option_snapshot_hash",
        "source_kind",
        "source_command_id",
        "source_signal_id",
        "execution_trade_date",
    ):
        value = provenance.get(field)
        if value not in (None, "", {}):
            record[field] = deepcopy(value)
    return record


def _process_record(catalog: dict[str, Any]) -> dict[str, Any]:
    return deepcopy(_as_dict(_as_dict(catalog.get("provenance")).get("process_record")))


def _lock_record(catalog: dict[str, Any]) -> dict[str, Any]:
    return {
        "lock_id": catalog.get("lock_id"),
        "order_id": catalog.get("order_id"),
        "request_hash": catalog.get("request_hash"),
        "execution_id": catalog.get("execution_id"),
        "status": "RUNTIME_WRITE_PREVIEW",
        "source": "execution_runtime_catalog_preview",
        "preview_only": True,
        "runtime_write": False,
    }


def build_execution_runtime_write_preview(
    catalog_preview: Any = None,
    *,
    catalog_orchestrator_result: Any = None,
    existing_order_executions_data: Any = None,
    existing_order_locks_data: Any = None,
) -> dict[str, Any]:
    """Build order_executions/order_locks write candidates in memory only."""
    catalog_source = catalog_orchestrator_result if catalog_orchestrator_result is not None else catalog_preview
    catalog, catalog_ok = _catalog_from_input(catalog_source)
    if not catalog_ok:
        return _base_result(
            status=STATUS_INVALID,
            catalog={},
            issues=["MALFORMED_CATALOG_INPUT"],
        )

    catalog_status = catalog.get("status")
    if catalog_status == STATUS_INVALID:
        return _base_result(
            status=STATUS_INVALID,
            catalog=catalog,
            issues=list(catalog.get("issues") or ["CATALOG_INVALID"]),
            warnings=list(catalog.get("warnings") or []),
        )
    if catalog_status != STATUS_READY:
        issue = "CATALOG_NOT_READY"
        if catalog_status == STATUS_BLOCKED:
            issue = "CATALOG_BLOCKED"
        return _base_result(
            status=STATUS_BLOCKED,
            catalog=catalog,
            issues=list(catalog.get("issues") or [issue]),
            warnings=list(catalog.get("warnings") or []),
        )

    required_fields = {
        "execution_id": "MISSING_EXECUTION_ID",
        "order_id": "MISSING_ORDER_ID",
        "request_hash": "MISSING_REQUEST_HASH",
        "lock_id": "MISSING_LOCK_ID",
    }
    missing = [
        issue
        for field, issue in required_fields.items()
        if not _clean_text(catalog.get(field))
    ]
    if missing:
        return _base_result(
            status=STATUS_INVALID,
            catalog=catalog,
            issues=missing,
            warnings=list(catalog.get("warnings") or []),
        )

    executions, executions_error = _existing_items(existing_order_executions_data, "executions")
    processes, processes_error = _optional_existing_items(existing_order_executions_data, "processes")
    locks, locks_error = _existing_items(existing_order_locks_data, "locks")
    malformed_existing = [
        issue for issue in (executions_error, processes_error, locks_error) if issue
    ]
    if malformed_existing:
        return _base_result(
            status=STATUS_INVALID,
            catalog=catalog,
            issues=malformed_existing,
            warnings=list(catalog.get("warnings") or []),
        )

    execution_record = _execution_record(catalog)
    process_record = _process_record(catalog)
    process_append_required = False
    provenance = _as_dict(catalog.get("provenance"))
    process_id = _clean_text(provenance.get("execution_process_id"))
    if process_id:
        if not process_id:
            return _base_result(
                status=STATUS_INVALID,
                catalog=catalog,
                issues=["MISSING_EXECUTION_PROCESS_ID"],
            )
        matching_processes = [
            item for item in processes
            if _clean_text(item.get("execution_process_id")) == process_id
        ]
        if len(matching_processes) > 1:
            return _base_result(
                status=STATUS_INVALID,
                catalog=catalog,
                issues=["DUPLICATE_EXECUTION_PROCESS_ID"],
            )
        if process_record:
            process_issues = validate_process_record(process_record)
            if process_issues:
                return _base_result(
                    status=STATUS_INVALID,
                    catalog=catalog,
                    issues=process_issues,
                )
            if matching_processes and not same_payload(matching_processes[0], process_record):
                return _base_result(
                    status=STATUS_INVALID,
                    catalog=catalog,
                    issues=["EXECUTION_PROCESS_PAYLOAD_CONFLICT"],
                )
            process_append_required = not matching_processes
        elif not matching_processes:
            return _base_result(
                status=STATUS_INVALID,
                catalog=catalog,
                issues=["EXECUTION_PROCESS_OWNER_MISSING"],
            )

    execution_append_required = True
    execution_matches = _matching_identity_records(
        executions,
        fields=("execution_id", "request_hash", "order_id"),
        source=execution_record,
    )
    if execution_matches:
        if process_id and len(execution_matches) == 1 and same_payload(
            execution_matches[0], execution_record
        ):
            execution_append_required = False
        else:
            execution_duplicate = _duplicate_issue(
                executions,
                fields=("execution_id", "request_hash", "order_id"),
                source=execution_record,
            )
            return _base_result(
                status=STATUS_INVALID if process_id else STATUS_BLOCKED,
                catalog=catalog,
                issues=[execution_duplicate or "EXECUTION_IDENTITY_CONFLICT"],
                warnings=list(catalog.get("warnings") or []),
            )

    lock_record = _lock_record(catalog)
    lock_append_required = True
    lock_matches = _matching_identity_records(
        locks,
        fields=("lock_id", "request_hash", "order_id"),
        source=lock_record,
    )
    if lock_matches:
        if process_id and len(lock_matches) == 1 and same_payload(lock_matches[0], lock_record):
            lock_append_required = False
        else:
            lock_duplicate = _duplicate_issue(
                locks,
                fields=("lock_id", "request_hash", "order_id"),
                source=lock_record,
            )
            return _base_result(
                status=STATUS_INVALID if process_id else STATUS_BLOCKED,
                catalog=catalog,
                issues=[lock_duplicate or "LOCK_IDENTITY_CONFLICT"],
                warnings=list(catalog.get("warnings") or []),
            )

    if execution_append_required != lock_append_required:
        return _base_result(
            status=STATUS_INVALID,
            catalog=catalog,
            issues=["EXECUTION_LOCK_IDEMPOTENCY_SPLIT_BRAIN"],
            warnings=list(catalog.get("warnings") or []),
        )

    return _base_result(
        status=STATUS_READY,
        catalog=catalog,
        execution_record_preview=execution_record,
        process_record_preview=process_record if process_append_required else None,
        lock_record_preview=lock_record,
        append_requirements={
            "process": process_append_required,
            "execution": execution_append_required,
            "lock": lock_append_required,
        },
        idempotent_existing=(
            not process_append_required
            and not execution_append_required
            and not lock_append_required
        ),
        issues=[],
        warnings=list(catalog.get("warnings") or []),
    )
