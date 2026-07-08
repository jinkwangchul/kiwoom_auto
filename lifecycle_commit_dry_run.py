# -*- coding: utf-8 -*-
"""Preview-only dry-run validation for lifecycle commit contracts."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


STATUS_READY = "LIFECYCLE_DRY_RUN_READY"
STATUS_BLOCKED = "LIFECYCLE_DRY_RUN_BLOCKED"
STATUS_INVALID = "INVALID"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
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
        "lifecycle_write": False,
        "runtime_write": False,
        "queue_write": False,
    }


def _store_snapshot_issues(snapshot: Any) -> tuple[dict[str, Any], list[str]]:
    payload = _as_dict(snapshot)
    issues: list[str] = []
    if not payload:
        return payload, ["lifecycle_store_snapshot must be a non-empty dict"]
    if payload.get("snapshot_valid") is not True:
        issues.append("lifecycle_store_snapshot.snapshot_valid is not true")
    if not _text(payload.get("lifecycle_store")):
        issues.append("lifecycle_store_snapshot.lifecycle_store is required")
    if not isinstance(payload.get("existing_transitions", []), list):
        issues.append("lifecycle_store_snapshot.existing_transitions must be a list")
    return payload, issues


def _runtime_context_issues(context: Any) -> tuple[dict[str, Any], list[str], list[str]]:
    payload = _as_dict(context)
    invalid: list[str] = []
    blocked: list[str] = []
    if not payload:
        return payload, ["lifecycle_runtime_context must be a non-empty dict"], blocked
    if "lifecycle_runtime_enabled" not in payload:
        invalid.append("lifecycle_runtime_context.lifecycle_runtime_enabled is required")
    elif payload.get("lifecycle_runtime_enabled") is not True:
        blocked.append("lifecycle runtime disabled")
    if payload.get("emergency_stop") is True:
        blocked.append("emergency stop is active")
    return payload, invalid, blocked


def _transition_matches(record: Any, order_id: str, event: str, evidence_id: str) -> bool:
    payload = _as_dict(record)
    if not payload:
        return False
    if evidence_id and _text(payload.get("evidence_id")) == evidence_id:
        return True
    return _text(payload.get("order_id")) == order_id and _text(payload.get("candidate_lifecycle_event") or payload.get("event")) == event


def _duplicate_transition_exists(store_snapshot: dict[str, Any], order_id: str, event: str, evidence_id: str) -> bool:
    if store_snapshot.get("duplicate_transition") is True:
        return True
    for record in _as_list(store_snapshot.get("existing_transitions")):
        if _transition_matches(record, order_id, event, evidence_id):
            return True
    for record in _as_list(store_snapshot.get("existing_events")):
        if _transition_matches(record, order_id, event, evidence_id):
            return True
    return False


def _validate_preview(preview: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    issues: list[str] = []
    commit_contract = _as_dict(preview.get("commit_contract"))
    commit_plan = _as_dict(preview.get("commit_plan"))
    if not commit_contract:
        issues.append("commit_contract is required")
    if not commit_plan:
        issues.append("commit_plan is required")
    if preview.get("preview_only") is not True:
        issues.append("lifecycle_commit_contract_preview.preview_only is not true")
    if preview.get("lifecycle_write") is not False:
        issues.append("lifecycle_commit_contract_preview.lifecycle_write must be false")
    if commit_contract:
        for field in ("order_id", "candidate_lifecycle_event", "evidence_id", "lifecycle_store"):
            if not _text(commit_contract.get(field)):
                issues.append(f"commit_contract.{field} is required")
        if commit_contract.get("preview_only") is not True:
            issues.append("commit_contract.preview_only is not true")
    if commit_plan and commit_plan.get("preview_only") is not True:
        issues.append("commit_plan.preview_only is not true")
    return commit_contract, commit_plan, issues


def dry_run_lifecycle_commit(
    lifecycle_commit_contract_preview: Any,
    lifecycle_store_snapshot: Any,
    lifecycle_runtime_context: Any,
) -> dict[str, Any]:
    """Dry-run a lifecycle commit contract without creating or modifying lifecycle state."""
    preview = _as_dict(lifecycle_commit_contract_preview)
    if not preview:
        return _result(status=STATUS_INVALID, issues=["lifecycle_commit_contract_preview must be a dict"])

    status = _text(preview.get("status")).upper()
    warnings = list(preview.get("warnings") or [])
    if status == "BLOCKED":
        return _result(
            status=STATUS_BLOCKED,
            issues=["lifecycle_commit_contract_preview.status is BLOCKED"] + list(preview.get("issues") or []),
            warnings=warnings,
        )
    if status == "INVALID":
        return _result(
            status=STATUS_INVALID,
            issues=["lifecycle_commit_contract_preview.status is INVALID"] + list(preview.get("issues") or []),
            warnings=warnings,
        )
    if status != "LIFECYCLE_COMMIT_READY":
        return _result(status=STATUS_INVALID, issues=["lifecycle_commit_contract_preview.status is not supported"], warnings=warnings)

    commit_contract, commit_plan, preview_issues = _validate_preview(preview)
    if preview_issues:
        return _result(status=STATUS_INVALID, issues=preview_issues, warnings=warnings)

    store_snapshot, store_issues = _store_snapshot_issues(lifecycle_store_snapshot)
    if store_issues:
        return _result(status=STATUS_INVALID, issues=store_issues, warnings=warnings)

    runtime_context, runtime_invalid, runtime_blocked = _runtime_context_issues(lifecycle_runtime_context)
    if runtime_invalid:
        return _result(status=STATUS_INVALID, issues=runtime_invalid, warnings=warnings)

    order_id = _text(commit_contract.get("order_id"))
    event = _text(commit_contract.get("candidate_lifecycle_event"))
    evidence_id = _text(commit_contract.get("evidence_id"))
    duplicate = _duplicate_transition_exists(store_snapshot, order_id, event, evidence_id)

    blocked_issues = list(runtime_blocked)
    if duplicate:
        blocked_issues.append("duplicate lifecycle transition exists")

    dry_run = {
        "lifecycle_commit_dry_run": True,
        "dry_run_ready": not blocked_issues,
        "commit_contract": deepcopy(commit_contract),
        "commit_plan": deepcopy(commit_plan),
        "lifecycle_store_snapshot": deepcopy(store_snapshot),
        "lifecycle_runtime_context": deepcopy(runtime_context),
        "order_id": order_id,
        "candidate_lifecycle_event": event,
        "evidence_id": evidence_id,
        "duplicate_check_passed": not duplicate,
        "runtime_enabled_check_passed": "lifecycle runtime disabled" not in blocked_issues,
        "would_write_lifecycle": False,
        "would_write_runtime": False,
        "would_write_queue": False,
    }
    if blocked_issues:
        return _result(status=STATUS_BLOCKED, dry_run=dry_run, issues=blocked_issues, warnings=warnings)

    return _result(status=STATUS_READY, dry_run=dry_run, issues=[], warnings=warnings)
