# -*- coding: utf-8 -*-
"""Preview-only persistence planning for lifecycle runtime projections.

This module converts a lifecycle runtime projection into persistence request
and write-plan dictionaries. It does not write runtime JSON files, create
backups, replace files, connect GUI flows, or call broker/Chejan code.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any
from uuid import uuid4


PERSISTENCE_TYPE = "LIFECYCLE_RUNTIME_PERSISTENCE_PREVIEW"
STATUS_READY = "PERSISTENCE_PREVIEW_READY"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"

DEFAULT_RUNTIME_TARGETS = {
    "runtime_snapshot": "runtime/runtime_snapshot.json",
    "position_view": "runtime/position_view.json",
    "balance_view": "runtime/balance_view.json",
}
DEFAULT_POSITION_TARGETS = {
    "position_view": "runtime/position_view.json",
}
DEFAULT_BALANCE_TARGETS = {
    "balance_view": "runtime/balance_view.json",
}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _now_text() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _result(
    *,
    status: str,
    persistence_plan: dict[str, Any] | None = None,
    persistence_request: dict[str, Any] | None = None,
    runtime_write_plan: dict[str, Any] | None = None,
    atomic_write_plan: dict[str, Any] | None = None,
    backup_preview: dict[str, Any] | None = None,
    rollback_preview: dict[str, Any] | None = None,
    issues: list[str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "persistence_type": PERSISTENCE_TYPE,
        "status": status,
        "preview_only": True,
        "runtime_write": False,
        "queue_write": False,
        "position_write": False,
        "balance_write": False,
        "atomic_write_called": False,
        "backup_created": False,
        "rollback_called": False,
        "rollback_executed": False,
        "gui_update_called": False,
        "send_order_called": False,
        "chejan_called": False,
        "persistence_plan": deepcopy(persistence_plan or {}),
        "persistence_request": deepcopy(persistence_request or {}),
        "runtime_write_plan": deepcopy(runtime_write_plan or {}),
        "atomic_write_plan": deepcopy(atomic_write_plan or {}),
        "backup_preview": deepcopy(backup_preview or {}),
        "rollback_preview": deepcopy(rollback_preview or {}),
        "issues": list(issues or []),
        "warnings": list(warnings or []),
    }


def _target_value(source: dict[str, Any], key: str, default: str) -> str:
    value = _text(source.get(key))
    return value or default


def _split_targets(context: dict[str, Any]) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    runtime_context = _as_dict(context.get("runtime_targets"))
    position_context = _as_dict(context.get("position_targets"))
    balance_context = _as_dict(context.get("balance_targets"))
    legacy_targets = _as_dict(context.get("targets"))
    runtime_targets = {
        "runtime_snapshot": _target_value(
            runtime_context or legacy_targets,
            "runtime_snapshot",
            DEFAULT_RUNTIME_TARGETS["runtime_snapshot"],
        ),
    }
    position_targets = {
        "position_view": _target_value(
            position_context or legacy_targets,
            "position_view",
            DEFAULT_POSITION_TARGETS["position_view"],
        ),
    }
    balance_targets = {
        "balance_view": _target_value(
            balance_context or legacy_targets,
            "balance_view",
            DEFAULT_BALANCE_TARGETS["balance_view"],
        ),
    }
    return runtime_targets, position_targets, balance_targets


def _combined_targets(runtime_targets: dict[str, str], position_targets: dict[str, str], balance_targets: dict[str, str]) -> dict[str, str]:
    return {
        "runtime_snapshot": runtime_targets["runtime_snapshot"],
        "position_view": position_targets["position_view"],
        "balance_view": balance_targets["balance_view"],
    }


def _target_map(runtime_targets: Any) -> dict[str, str]:
    provided = _as_dict(runtime_targets)
    result = dict(DEFAULT_RUNTIME_TARGETS)
    for key in result:
        value = _text(provided.get(key))
        if value:
            result[key] = value
    return result


def _required_projection_payload(projection_result: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    runtime_snapshot = _as_dict(projection_result.get("runtime_snapshot_projection"))
    position = _as_dict(projection_result.get("position_projection"))
    balance = _as_dict(projection_result.get("balance_projection"))
    runtime = _as_dict(projection_result.get("runtime_projection"))
    issues: list[str] = []
    if not runtime_snapshot:
        issues.append("runtime_snapshot_projection is required")
    if not position:
        issues.append("position_projection is required")
    if not balance:
        issues.append("balance_projection is required")
    if not runtime:
        issues.append("runtime_projection is required")
    return {
        "runtime_snapshot": runtime_snapshot,
        "position": position,
        "balance": balance,
        "runtime": runtime,
    }, issues


def _build_request(
    projection_result: dict[str, Any],
    payload: dict[str, Any],
    targets: dict[str, str],
    context: dict[str, Any],
    now: str,
) -> dict[str, Any]:
    persistence_id = _text(context.get("persistence_id")) or "LIFECYCLE_RUNTIME_PERSISTENCE_{}".format(uuid4().hex)
    return {
        "request_type": "LIFECYCLE_RUNTIME_PERSISTENCE_REQUEST",
        "request_version": "preview-1",
        "persistence_id": persistence_id,
        "preview_only": True,
        "runtime_write": False,
        "lifecycle_event": projection_result.get("lifecycle_event", ""),
        "identity": deepcopy(_as_dict(projection_result.get("identity"))),
        "targets": deepcopy(targets),
        "payloads": deepcopy(payload),
        "requested_at": now,
    }


def _build_write_plan(request: dict[str, Any], now: str) -> dict[str, Any]:
    targets = _as_dict(request.get("targets"))
    payloads = _as_dict(request.get("payloads"))
    planned_writes = [
        {
            "target_key": "runtime_snapshot",
            "target_path": targets.get("runtime_snapshot", ""),
            "payload_key": "runtime_snapshot",
            "payload": deepcopy(payloads.get("runtime_snapshot", {})),
            "write_mode": "replace_json_preview",
        },
        {
            "target_key": "position_view",
            "target_path": targets.get("position_view", ""),
            "payload_key": "position",
            "payload": deepcopy(payloads.get("position", {})),
            "write_mode": "replace_json_preview",
        },
        {
            "target_key": "balance_view",
            "target_path": targets.get("balance_view", ""),
            "payload_key": "balance",
            "payload": deepcopy(payloads.get("balance", {})),
            "write_mode": "replace_json_preview",
        },
    ]
    return {
        "plan_type": "LIFECYCLE_RUNTIME_WRITE_PLAN_PREVIEW",
        "preview_only": True,
        "runtime_write": False,
        "persistence_id": request.get("persistence_id"),
        "planned_writes": planned_writes,
        "planned_write_count": len(planned_writes),
        "planned_at": now,
    }


def _build_atomic_plan(write_plan: dict[str, Any], now: str) -> dict[str, Any]:
    operations = []
    for item in write_plan.get("planned_writes", []):
        entry = _as_dict(item)
        target_path = _text(entry.get("target_path"))
        operations.append(
            {
                "target_key": entry.get("target_key", ""),
                "target_path": target_path,
                "temp_path_preview": "{}.{}.tmp".format(target_path, uuid4().hex) if target_path else "",
                "atomic_method": "write_temp_fsync_replace_preview",
                "write_called": False,
            }
        )
    return {
        "plan_type": "LIFECYCLE_RUNTIME_ATOMIC_WRITE_PLAN_PREVIEW",
        "preview_only": True,
        "runtime_write": False,
        "atomic_write_called": False,
        "operations": operations,
        "planned_at": now,
    }


def _build_backup_preview(write_plan: dict[str, Any], now: str) -> dict[str, Any]:
    backups = []
    for item in write_plan.get("planned_writes", []):
        entry = _as_dict(item)
        target_path = _text(entry.get("target_path"))
        backups.append(
            {
                "target_key": entry.get("target_key", ""),
                "target_path": target_path,
                "backup_path_preview": "{}.bak".format(target_path) if target_path else "",
                "backup_created": False,
            }
        )
    return {
        "preview_type": "LIFECYCLE_RUNTIME_BACKUP_PREVIEW",
        "preview_only": True,
        "backup_created": False,
        "backups": backups,
        "planned_at": now,
    }


def _build_rollback_preview(backup_preview: dict[str, Any], now: str) -> dict[str, Any]:
    restore_steps = []
    for item in backup_preview.get("backups", []):
        backup = _as_dict(item)
        restore_steps.append(
            {
                "target_key": backup.get("target_key", ""),
                "target_path": backup.get("target_path", ""),
                "backup_path_preview": backup.get("backup_path_preview", ""),
                "rollback_called": False,
                "restore_method": "replace_from_backup_preview",
            }
        )
    return {
        "preview_type": "LIFECYCLE_RUNTIME_ROLLBACK_PREVIEW",
        "preview_only": True,
        "rollback_called": False,
        "restore_steps": restore_steps,
        "planned_at": now,
    }


def _backup_targets(backup_preview: dict[str, Any]) -> dict[str, str]:
    targets: dict[str, str] = {}
    for item in backup_preview.get("backups", []):
        backup = _as_dict(item)
        key = _text(backup.get("target_key"))
        value = _text(backup.get("backup_path_preview"))
        if key:
            targets[key] = value
    return targets


def _rollback_targets(rollback_preview: dict[str, Any]) -> dict[str, str]:
    targets: dict[str, str] = {}
    for item in rollback_preview.get("restore_steps", []):
        step = _as_dict(item)
        key = _text(step.get("target_key"))
        value = _text(step.get("target_path"))
        if key:
            targets[key] = value
    return targets


def _validation_result(status: str, issues: list[str], warnings: list[str]) -> dict[str, Any]:
    return {
        "valid": status == STATUS_READY,
        "status": status,
        "issues": list(issues),
        "warnings": list(warnings),
    }


def _persistence_summary(
    *,
    status: str,
    request: dict[str, Any],
    write_plan: dict[str, Any],
    projection: dict[str, Any],
) -> dict[str, Any]:
    return {
        "status": status,
        "persistence_id": request.get("persistence_id"),
        "lifecycle_event": projection.get("lifecycle_event", ""),
        "order_id": _as_dict(projection.get("identity")).get("order_id", ""),
        "planned_write_count": write_plan.get("planned_write_count", 0),
        "preview_only": True,
        "runtime_write": False,
    }


def _build_persistence_plan(
    *,
    status: str,
    projection: dict[str, Any],
    runtime_targets: dict[str, str],
    position_targets: dict[str, str],
    balance_targets: dict[str, str],
    request: dict[str, Any] | None = None,
    write_plan: dict[str, Any] | None = None,
    backup_preview: dict[str, Any] | None = None,
    rollback_preview: dict[str, Any] | None = None,
    issues: list[str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    request_payload = deepcopy(request or {})
    write_plan_payload = deepcopy(write_plan or {})
    backup_payload = deepcopy(backup_preview or {})
    rollback_payload = deepcopy(rollback_preview or {})
    issue_list = list(issues or [])
    warning_list = list(warnings or [])
    return {
        "plan_type": "LIFECYCLE_RUNTIME_PERSISTENCE_PLAN_PREVIEW",
        "preview_only": True,
        "runtime_write": False,
        "position_write": False,
        "balance_write": False,
        "backup_created": False,
        "rollback_executed": False,
        "runtime_targets": deepcopy(runtime_targets),
        "position_targets": deepcopy(position_targets),
        "balance_targets": deepcopy(balance_targets),
        "backup_targets": _backup_targets(backup_payload),
        "rollback_targets": _rollback_targets(rollback_payload),
        "persistence_summary": _persistence_summary(
            status=status,
            request=request_payload,
            write_plan=write_plan_payload,
            projection=projection,
        ),
        "validation_result": _validation_result(status, issue_list, warning_list),
    }


def build_lifecycle_runtime_persistence_preview(
    lifecycle_runtime_projection_result: Any,
    runtime_targets: Any = None,
    persistence_context: Any = None,
) -> dict[str, Any]:
    """Build preview-only runtime persistence request and write plans."""
    projection = _as_dict(lifecycle_runtime_projection_result)
    if not projection:
        return _result(status=STATUS_INVALID, issues=["lifecycle_runtime_projection_result must be a dict"])

    status = _text(projection.get("status")).upper()
    warnings = list(projection.get("warnings") or [])
    if status == "BLOCKED":
        return _result(
            status=STATUS_BLOCKED,
            issues=["lifecycle runtime projection is BLOCKED"] + list(projection.get("issues") or []),
            warnings=warnings,
        )
    if status == "INVALID":
        return _result(
            status=STATUS_INVALID,
            issues=["lifecycle runtime projection is INVALID"] + list(projection.get("issues") or []),
            warnings=warnings,
        )
    if status != "PROJECTED":
        return _result(status=STATUS_INVALID, issues=["lifecycle runtime projection status is not supported"], warnings=warnings)
    if projection.get("preview_only") is not True:
        return _result(status=STATUS_INVALID, issues=["projection preview_only must be true"], warnings=warnings)
    if projection.get("runtime_write") is not False:
        return _result(status=STATUS_INVALID, issues=["projection runtime_write must be false"], warnings=warnings)

    payload, payload_issues = _required_projection_payload(projection)
    if payload_issues:
        return _result(status=STATUS_INVALID, issues=payload_issues, warnings=warnings)

    targets = _target_map(runtime_targets)
    missing_targets = [key for key, value in targets.items() if not _text(value)]
    if missing_targets:
        return _result(status=STATUS_INVALID, issues=["runtime target missing: " + ", ".join(missing_targets)], warnings=warnings)

    context = deepcopy(_as_dict(persistence_context))
    now = _text(context.get("planned_at")) or _now_text()
    request = _build_request(projection, payload, targets, context, now)
    write_plan = _build_write_plan(request, now)
    atomic_plan = _build_atomic_plan(write_plan, now)
    backup_preview = _build_backup_preview(write_plan, now)
    rollback_preview = _build_rollback_preview(backup_preview, now)

    return _result(
        status=STATUS_READY,
        persistence_request=request,
        runtime_write_plan=write_plan,
        atomic_write_plan=atomic_plan,
        backup_preview=backup_preview,
        rollback_preview=rollback_preview,
        issues=[],
        warnings=warnings,
    )


def build_runtime_persistence_plan(
    runtime_projection_result: Any,
    persistence_context: Any = None,
) -> dict[str, Any]:
    """Build the canonical preview-only runtime persistence plan."""
    projection = _as_dict(runtime_projection_result)
    context = deepcopy(_as_dict(persistence_context))
    runtime_targets, position_targets, balance_targets = _split_targets(context)
    combined_targets = _combined_targets(runtime_targets, position_targets, balance_targets)

    preview = build_lifecycle_runtime_persistence_preview(
        projection,
        runtime_targets=combined_targets,
        persistence_context=context,
    )
    issues = list(preview.get("issues") or [])
    warnings = list(preview.get("warnings") or [])
    persistence_plan = _build_persistence_plan(
        status=_text(preview.get("status")),
        projection=projection,
        runtime_targets=runtime_targets,
        position_targets=position_targets,
        balance_targets=balance_targets,
        request=_as_dict(preview.get("persistence_request")),
        write_plan=_as_dict(preview.get("runtime_write_plan")),
        backup_preview=_as_dict(preview.get("backup_preview")),
        rollback_preview=_as_dict(preview.get("rollback_preview")),
        issues=issues,
        warnings=warnings,
    )

    result = deepcopy(preview)
    result["persistence_plan"] = persistence_plan
    result["runtime_targets"] = deepcopy(runtime_targets)
    result["position_targets"] = deepcopy(position_targets)
    result["balance_targets"] = deepcopy(balance_targets)
    result["backup_targets"] = deepcopy(persistence_plan["backup_targets"])
    result["rollback_targets"] = deepcopy(persistence_plan["rollback_targets"])
    result["persistence_summary"] = deepcopy(persistence_plan["persistence_summary"])
    result["validation_result"] = deepcopy(persistence_plan["validation_result"])
    result["rollback_executed"] = False
    return result
