# -*- coding: utf-8 -*-
"""Preview-only final approval gate after execution readiness validation."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


STATUS_APPROVED = "APPROVED"
STATUS_DENIED = "DENIED"
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
    approval: dict[str, Any],
    issues: list[str],
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    approval_summary = {
        "approved": status == STATUS_APPROVED,
        "denied": status == STATUS_DENIED,
        "invalid": status == STATUS_INVALID,
        "issue_count": len(issues),
        "warning_count": len(warnings or []),
    }
    return {
        "status": status,
        "approval": deepcopy(approval),
        "issues": list(issues),
        "warnings": list(warnings or []),
        "approval_summary": approval_summary,
        "preview_only": True,
        "runtime_write": False,
        "queue_write": False,
        "queue_commit_called": False,
        "send_order_called": False,
    }


def _approval_policy_allows(policy: dict[str, Any]) -> tuple[bool | None, str | None]:
    if not policy:
        return None, "approval_policy must be a non-empty dict"
    if policy.get("approved") is True or policy.get("approval_allowed") is True or policy.get("allow") is True:
        return True, None
    if policy.get("approved") is False or policy.get("approval_allowed") is False or policy.get("allow") is False:
        return False, "approval policy rejected"
    status = _clean_text(policy.get("status") or policy.get("decision") or policy.get("approval_status")).upper()
    if status in {"APPROVED", "ALLOW", "ALLOWED", "PASS", "PASSED"}:
        return True, None
    if status in {"DENIED", "REJECTED", "REJECT", "BLOCKED", "INVALID"}:
        return False, "approval policy rejected"
    return None, "approval_policy approval decision is missing"


def _matches_order(record: Any, approval: dict[str, Any]) -> bool:
    record_dict = _as_dict(record)
    if not record_dict:
        return False
    identity = _as_dict(approval.get("identity"))
    order_id = _clean_text(approval.get("order_id") or identity.get("order_id"))
    source_signal_id = _clean_text(approval.get("source_signal_id") or identity.get("source_signal_id"))
    for key, expected in (
        ("order_id", order_id),
        ("id", order_id),
        ("source_order_id", order_id),
        ("source_signal_id", source_signal_id),
    ):
        if expected and _clean_text(record_dict.get(key)) == expected:
            return True
    return False


def _runtime_has_lock(runtime_snapshot: Any, approval: dict[str, Any]) -> bool:
    snapshot = _as_dict(runtime_snapshot)
    if snapshot.get("locked") is True or snapshot.get("runtime_locked") is True:
        return True
    for key in ("locks", "active_locks", "order_locks", "runtime_locks"):
        for record in _as_list(snapshot.get(key)):
            if _matches_order(record, approval):
                return True
    order_id = _clean_text(approval.get("order_id") or _as_dict(approval.get("identity")).get("order_id"))
    if order_id and order_id in {_clean_text(value) for value in _as_list(snapshot.get("locked_order_ids"))}:
        return True
    return False


def _runtime_has_duplicate(runtime_snapshot: Any, approval: dict[str, Any]) -> bool:
    snapshot = _as_dict(runtime_snapshot)
    if snapshot.get("duplicate") is True or snapshot.get("duplicate_order") is True:
        return True
    order_id = _clean_text(approval.get("order_id") or _as_dict(approval.get("identity")).get("order_id"))
    for key in ("duplicate_order_ids", "existing_order_ids"):
        if order_id and order_id in {_clean_text(value) for value in _as_list(snapshot.get(key))}:
            return True
    for key in ("orders", "existing_orders", "order_queue", "executions", "order_executions"):
        for record in _as_list(snapshot.get(key)):
            if order_id and _clean_text(_as_dict(record).get("order_id") or _as_dict(record).get("id")) == order_id:
                return True
    return False


def evaluate_execution_approval(
    readiness_result: Any,
    operator_context: Any,
    approval_policy: Any,
    runtime_snapshot: Any,
) -> dict[str, Any]:
    """Evaluate final approval eligibility without writes or external calls."""
    readiness = _as_dict(readiness_result)
    operator = _as_dict(operator_context)
    policy = _as_dict(approval_policy)
    runtime = _as_dict(runtime_snapshot)
    approval: dict[str, Any] = {
        "readiness_status": readiness.get("status"),
        "approved": False,
        "order_id": _clean_text(policy.get("order_id") or _as_dict(policy.get("identity")).get("order_id")),
        "source_signal_id": _clean_text(
            policy.get("source_signal_id") or _as_dict(policy.get("identity")).get("source_signal_id")
        ),
    }

    if not readiness:
        return _result(status=STATUS_INVALID, approval=approval, issues=["readiness_result must be a dict"])
    if not policy:
        return _result(status=STATUS_INVALID, approval=approval, issues=["approval_policy must be a non-empty dict"])
    if readiness.get("status") == "INVALID":
        return _result(status=STATUS_INVALID, approval=approval, issues=["readiness_result.status is INVALID"])
    if readiness.get("status") != "READY":
        return _result(status=STATUS_DENIED, approval=approval, issues=["readiness_result.status is not READY"])
    if readiness.get("preview_only") is not True:
        return _result(status=STATUS_INVALID, approval=approval, issues=["readiness_result.preview_only is not true"])

    policy_allowed, policy_issue = _approval_policy_allows(policy)
    if policy_allowed is None:
        return _result(status=STATUS_INVALID, approval=approval, issues=[policy_issue or "approval_policy is malformed"])

    issues: list[str] = []
    if operator.get("operator_confirmed") is not True:
        issues.append("operator_context.operator_confirmed is not true")
    if operator.get("real_trade_enabled") is not True:
        issues.append("operator_context.real_trade_enabled is not true")
    if operator.get("real_trade_guard_ok") is not True:
        issues.append("operator_context.real_trade_guard_ok is not true")
    if policy_allowed is not True:
        issues.append(policy_issue or "approval policy rejected")
    if operator.get("emergency_stop") is True or runtime.get("emergency_stop") is True:
        issues.append("emergency stop is active")

    readiness_approval = _as_dict(readiness.get("readiness"))
    approval.update(
        {
            "readiness": deepcopy(readiness_approval),
            "policy": deepcopy(policy),
            "operator_confirmed": operator.get("operator_confirmed") is True,
            "real_trade_enabled": operator.get("real_trade_enabled") is True,
            "real_trade_guard_ok": operator.get("real_trade_guard_ok") is True,
        }
    )

    if _runtime_has_lock(runtime, approval):
        issues.append("runtime lock exists")
    if _runtime_has_duplicate(runtime, approval):
        issues.append("duplicate order exists")

    if issues:
        return _result(status=STATUS_DENIED, approval=approval, issues=issues, warnings=_as_list(readiness.get("warnings")))

    approval["approved"] = True
    approval["approval_policy_passed"] = True
    return _result(status=STATUS_APPROVED, approval=approval, issues=[], warnings=_as_list(readiness.get("warnings")))
