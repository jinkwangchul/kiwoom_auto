# -*- coding: utf-8 -*-
"""Preview-only dry-run validation for Queue Commit contracts."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


STATUS_READY = "DRY_RUN_READY"
STATUS_BLOCKED = "DRY_RUN_BLOCKED"
STATUS_INVALID = "INVALID"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _result(
    *,
    status: str,
    dry_run: dict[str, Any] | None = None,
    issues: list[str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "dry_run": deepcopy(dry_run) if isinstance(dry_run, dict) else {},
        "issues": list(issues or []),
        "warnings": list(warnings or []),
        "preview_only": True,
        "runtime_write": False,
        "queue_write": False,
        "queue_commit_called": False,
        "send_order_called": False,
    }


def _matches_order(record: Any, order_id: str, source_signal_id: str) -> bool:
    record_dict = _as_dict(record)
    if not record_dict:
        return False
    for key, expected in (
        ("order_id", order_id),
        ("id", order_id),
        ("source_order_id", order_id),
        ("source_signal_id", source_signal_id),
    ):
        if expected and _clean_text(record_dict.get(key)) == expected:
            return True
    return False


def _runtime_lock_exists(runtime_snapshot: dict[str, Any], order_id: str, source_signal_id: str) -> bool:
    if runtime_snapshot.get("locked") is True or runtime_snapshot.get("runtime_locked") is True:
        return True
    for key in ("locks", "active_locks", "order_locks", "runtime_locks"):
        for record in _as_list(runtime_snapshot.get(key)):
            if _matches_order(record, order_id, source_signal_id):
                return True
    return False


def _duplicate_order_exists(queue_snapshot: dict[str, Any], runtime_snapshot: dict[str, Any], order_id: str, source_signal_id: str) -> bool:
    if queue_snapshot.get("duplicate") is True or runtime_snapshot.get("duplicate") is True:
        return True
    for snapshot in (queue_snapshot, runtime_snapshot):
        for key in ("duplicate_order_ids", "existing_order_ids"):
            if order_id and order_id in {_clean_text(value) for value in _as_list(snapshot.get(key))}:
                return True
        for key in ("orders", "existing_orders", "order_queue", "executions", "order_executions"):
            for record in _as_list(snapshot.get(key)):
                if _matches_order(record, order_id, source_signal_id):
                    return True
    return False


def _queue_snapshot_malformed(queue_snapshot: Any) -> bool:
    if not isinstance(queue_snapshot, dict):
        return True
    for key in ("orders", "existing_orders", "order_queue"):
        value = queue_snapshot.get(key)
        if value is not None and not isinstance(value, list):
            return True
    return False


def dry_run_queue_commit(
    commit_contract_preview: Any,
    runtime_snapshot: Any,
    queue_snapshot: Any,
) -> dict[str, Any]:
    """Dry-run a Queue Commit contract without writing anything."""
    preview = _as_dict(commit_contract_preview)
    runtime = _as_dict(runtime_snapshot)
    queue = _as_dict(queue_snapshot)

    if not preview:
        return _result(status=STATUS_INVALID, issues=["commit_contract_preview must be a dict"])
    if preview.get("status") == "INVALID":
        return _result(status=STATUS_INVALID, issues=["commit_contract_preview.status is INVALID"])
    if preview.get("status") != "READY":
        return _result(status=STATUS_BLOCKED, issues=["commit_contract_preview.status is not READY"], warnings=_as_list(preview.get("warnings")))
    if preview.get("preview_only") is not True:
        return _result(status=STATUS_INVALID, issues=["commit_contract_preview.preview_only is not true"])

    commit_contract = _as_dict(preview.get("commit_contract"))
    commit_plan = _as_dict(preview.get("commit_plan"))
    if not commit_contract:
        return _result(status=STATUS_INVALID, issues=["commit_contract is required"])
    if not commit_plan:
        return _result(status=STATUS_INVALID, issues=["commit_plan is required"])
    target = _clean_text(commit_plan.get("target") or commit_plan.get("queue_target") or commit_contract.get("queue_target"))
    if not target:
        return _result(status=STATUS_INVALID, issues=["queue target is required"])
    if _queue_snapshot_malformed(queue_snapshot):
        return _result(status=STATUS_INVALID, issues=["queue_snapshot is malformed"])

    order_id = _clean_text(commit_contract.get("order_id"))
    source_signal_id = _clean_text(commit_contract.get("source_signal_id"))
    if not order_id:
        return _result(status=STATUS_INVALID, issues=["commit_contract.order_id is required"])

    blocked_issues: list[str] = []
    if _duplicate_order_exists(queue, runtime, order_id, source_signal_id):
        blocked_issues.append("duplicate order exists")
    if _runtime_lock_exists(runtime, order_id, source_signal_id):
        blocked_issues.append("runtime lock exists")
    dry_run = {
        "queue_commit_dry_run": True,
        "commit_contract": deepcopy(commit_contract),
        "commit_plan": deepcopy(commit_plan),
        "target": target,
        "order_id": order_id,
        "source_signal_id": source_signal_id,
        "duplicate_check_passed": "duplicate order exists" not in blocked_issues,
        "runtime_lock_check_passed": "runtime lock exists" not in blocked_issues,
        "would_write_queue": False,
        "would_call_queue_commit": False,
    }
    if blocked_issues:
        return _result(status=STATUS_BLOCKED, dry_run=dry_run, issues=blocked_issues, warnings=_as_list(preview.get("warnings")))

    dry_run["dry_run_ready"] = True
    return _result(status=STATUS_READY, dry_run=dry_run, issues=[], warnings=_as_list(preview.get("warnings")))
