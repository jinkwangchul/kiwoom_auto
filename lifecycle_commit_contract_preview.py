# -*- coding: utf-8 -*-
"""Preview-only lifecycle commit contract builder."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


STATUS_READY = "LIFECYCLE_COMMIT_READY"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"
CONTRACT_TYPE = "ORDER_LIFECYCLE_COMMIT_CONTRACT_PREVIEW"
PLAN_TYPE = "ORDER_LIFECYCLE_COMMIT_PLAN_PREVIEW"


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
    commit_contract: dict[str, Any] | None = None,
    commit_plan: dict[str, Any] | None = None,
    issues: list[str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "commit_contract": deepcopy(commit_contract) if isinstance(commit_contract, dict) else {},
        "commit_plan": deepcopy(commit_plan) if isinstance(commit_plan, dict) else {},
        "issues": list(issues or []),
        "warnings": list(warnings or []),
        "preview_only": True,
        "lifecycle_write": False,
        "runtime_write": False,
        "queue_write": False,
    }


def _validate_target_context(context: Any) -> tuple[dict[str, Any], list[str]]:
    target = _as_dict(context)
    issues: list[str] = []
    if not target:
        return target, ["lifecycle_target_context must be a non-empty dict"]
    if target.get("target_valid") is not True:
        issues.append("lifecycle_target_context.target_valid is not true")
    if not _text(target.get("target_name")):
        issues.append("lifecycle_target_context.target_name is required")
    if not _text(target.get("lifecycle_store")):
        issues.append("lifecycle_target_context.lifecycle_store is required")
    if target.get("lifecycle_write_enabled") is not True:
        issues.append("lifecycle_target_context.lifecycle_write_enabled is not true")
    return target, issues


def _validate_snapshot(snapshot: Any) -> tuple[dict[str, Any], list[str]]:
    payload = _as_dict(snapshot)
    issues: list[str] = []
    if not payload:
        return payload, ["current_lifecycle_snapshot must be a non-empty dict"]
    if payload.get("snapshot_valid") is not True:
        issues.append("current_lifecycle_snapshot.snapshot_valid is not true")
    if not _text(payload.get("order_id")):
        issues.append("current_lifecycle_snapshot.order_id is required")
    if not isinstance(payload.get("existing_events", []), list):
        issues.append("current_lifecycle_snapshot.existing_events must be a list")
    return payload, issues


def _validate_approval_payload(approval_result: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    approval = _as_dict(approval_result.get("approval"))
    issues: list[str] = []
    if not approval:
        return approval, ["approval payload is required"]
    if approval.get("approved") is not True:
        issues.append("approval.approved is not true")
    if approval.get("lifecycle_write_allowed") is not True:
        issues.append("approval.lifecycle_write_allowed is not true")
    if _text(approval.get("next_stage")) != "ORDER_LIFECYCLE_WRITE_REQUIRED":
        issues.append("approval.next_stage is not ORDER_LIFECYCLE_WRITE_REQUIRED")
    if not _text(approval.get("candidate_lifecycle_event")):
        issues.append("approval.candidate_lifecycle_event is required")
    identity = _as_dict(approval.get("identity"))
    missing_identity = [
        field
        for field in ("record_id", "order_id", "dispatch_id", "source_signal_id", "order_queued_id")
        if not _text(identity.get(field))
    ]
    if missing_identity:
        issues.append("approval.identity missing fields: " + ", ".join(missing_identity))
    return approval, issues


def _build_contract(
    approval_result: dict[str, Any],
    approval: dict[str, Any],
    target_context: dict[str, Any],
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    identity = _as_dict(approval.get("identity"))
    transition_preview = _as_dict(approval.get("transition_preview"))
    return {
        "contract_type": CONTRACT_TYPE,
        "contract_version": "preview-1",
        "preview_only": True,
        "lifecycle_write": False,
        "runtime_write": False,
        "queue_write": False,
        "approval_status": approval_result.get("status"),
        "candidate_lifecycle_event": _text(approval.get("candidate_lifecycle_event")),
        "evidence_id": _text(transition_preview.get("evidence_id")),
        "record_id": _text(identity.get("record_id")),
        "order_id": _text(identity.get("order_id")),
        "dispatch_id": _text(identity.get("dispatch_id")),
        "source_signal_id": _text(identity.get("source_signal_id")),
        "order_queued_id": _text(identity.get("order_queued_id")),
        "target_name": _text(target_context.get("target_name")),
        "lifecycle_store": _text(target_context.get("lifecycle_store")),
        "current_lifecycle_status": snapshot.get("current_status"),
        "required_next_service": "ORDER_LIFECYCLE_COMMIT_SERVICE",
    }


def _build_plan(
    commit_contract: dict[str, Any],
    approval: dict[str, Any],
    target_context: dict[str, Any],
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    return {
        "plan_type": PLAN_TYPE,
        "preview_only": True,
        "lifecycle_write": False,
        "runtime_write": False,
        "queue_write": False,
        "would_append_event": commit_contract["candidate_lifecycle_event"],
        "would_use_contract": deepcopy(commit_contract),
        "transition_preview": deepcopy(_as_dict(approval.get("transition_preview"))),
        "target_context": deepcopy(target_context),
        "current_lifecycle_snapshot": deepcopy(snapshot),
        "existing_event_count": len(_as_list(snapshot.get("existing_events"))),
        "steps": [
            "verify lifecycle approval",
            "verify target context",
            "verify current lifecycle snapshot",
            "build lifecycle commit contract",
            "hand off only after explicit lifecycle commit confirmation",
        ],
    }


def build_lifecycle_commit_contract_preview(
    lifecycle_approval_result: Any,
    lifecycle_target_context: Any,
    current_lifecycle_snapshot: Any,
) -> dict[str, Any]:
    """Build an in-memory lifecycle commit contract and plan only."""
    approval_result = _as_dict(lifecycle_approval_result)
    if not approval_result:
        return _result(status=STATUS_INVALID, issues=["lifecycle_approval_result must be a dict"])

    status = _text(approval_result.get("status")).upper()
    warnings = list(approval_result.get("warnings") or [])
    if status == "DENIED":
        return _result(
            status=STATUS_BLOCKED,
            issues=["lifecycle_approval_result.status is DENIED"] + list(approval_result.get("issues") or []),
            warnings=warnings,
        )
    if status == "INVALID":
        return _result(
            status=STATUS_INVALID,
            issues=["lifecycle_approval_result.status is INVALID"] + list(approval_result.get("issues") or []),
            warnings=warnings,
        )
    if status != "LIFECYCLE_APPROVED":
        return _result(status=STATUS_INVALID, issues=["lifecycle_approval_result.status is not supported"], warnings=warnings)
    if approval_result.get("lifecycle_write_allowed") is not True:
        return _result(status=STATUS_BLOCKED, issues=["lifecycle_approval_result.lifecycle_write_allowed is not true"], warnings=warnings)

    approval, approval_issues = _validate_approval_payload(approval_result)
    if approval_issues:
        return _result(status=STATUS_INVALID, issues=approval_issues, warnings=warnings)

    target_context, target_issues = _validate_target_context(lifecycle_target_context)
    if target_issues:
        return _result(status=STATUS_INVALID, issues=target_issues, warnings=warnings)

    snapshot, snapshot_issues = _validate_snapshot(current_lifecycle_snapshot)
    if snapshot_issues:
        return _result(status=STATUS_INVALID, issues=snapshot_issues, warnings=warnings)

    identity = _as_dict(approval.get("identity"))
    snapshot_order_id = _text(snapshot.get("order_id"))
    if snapshot_order_id != _text(identity.get("order_id")):
        return _result(status=STATUS_INVALID, issues=["snapshot order_id does not match approval identity"], warnings=warnings)

    commit_contract = _build_contract(approval_result, approval, target_context, snapshot)
    missing_contract_fields = [
        field
        for field in (
            "candidate_lifecycle_event",
            "order_id",
            "dispatch_id",
            "source_signal_id",
            "target_name",
            "lifecycle_store",
        )
        if not _text(commit_contract.get(field))
    ]
    if missing_contract_fields:
        return _result(status=STATUS_INVALID, issues=["commit_contract missing fields: " + ", ".join(missing_contract_fields)], warnings=warnings)

    commit_plan = _build_plan(commit_contract, approval, target_context, snapshot)
    return _result(
        status=STATUS_READY,
        commit_contract=commit_contract,
        commit_plan=commit_plan,
        issues=[],
        warnings=warnings,
    )
