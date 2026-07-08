# -*- coding: utf-8 -*-
"""Preview-only Queue Commit contract builder.

This module creates an in-memory contract and plan for a later Queue Commit.
It never writes order_queue.json or runtime files, and never calls Queue Commit,
SendOrder, Kiwoom, or GUI code.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


STATUS_READY = "READY"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"
CONTRACT_TYPE = "EXECUTION_QUEUE_COMMIT_CONTRACT_PREVIEW"
PLAN_TYPE = "EXECUTION_QUEUE_COMMIT_PLAN_PREVIEW"
VALID_SIDES = {"BUY", "SELL"}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _positive_number(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return False


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
        "runtime_write": False,
        "queue_write": False,
        "queue_commit_called": False,
        "send_order_called": False,
    }


def _matches_order(record: Any, order_contract: dict[str, Any]) -> bool:
    record_dict = _as_dict(record)
    if not record_dict:
        return False
    order_id = _clean_text(order_contract.get("order_id") or order_contract.get("id"))
    source_signal_id = _clean_text(order_contract.get("source_signal_id"))
    for key, expected in (
        ("order_id", order_id),
        ("id", order_id),
        ("source_order_id", order_id),
        ("source_signal_id", source_signal_id),
    ):
        if expected and _clean_text(record_dict.get(key)) == expected:
            return True
    return False


def _runtime_lock_exists(runtime_snapshot: Any, order_contract: dict[str, Any]) -> bool:
    snapshot = _as_dict(runtime_snapshot)
    if snapshot.get("locked") is True or snapshot.get("runtime_locked") is True:
        return True
    for key in ("locks", "active_locks", "order_locks", "runtime_locks"):
        for record in _as_list(snapshot.get(key)):
            if _matches_order(record, order_contract):
                return True
    return False


def _duplicate_order_exists(runtime_snapshot: Any, order_contract: dict[str, Any]) -> bool:
    snapshot = _as_dict(runtime_snapshot)
    if snapshot.get("duplicate") is True or snapshot.get("duplicate_order") is True:
        return True
    order_id = _clean_text(order_contract.get("order_id") or order_contract.get("id"))
    for key in ("duplicate_order_ids", "existing_order_ids"):
        if order_id and order_id in {_clean_text(value) for value in _as_list(snapshot.get(key))}:
            return True
    for key in ("orders", "existing_orders", "order_queue", "executions", "order_executions"):
        for record in _as_list(snapshot.get(key)):
            if order_id and _clean_text(_as_dict(record).get("order_id") or _as_dict(record).get("id")) == order_id:
                return True
    return False


def _validate_order_contract(order_contract: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if order_contract.get("status") != "REAL_READY":
        issues.append("order_contract.status is not REAL_READY")
    if order_contract.get("execution_enabled") is not True:
        issues.append("order_contract.execution_enabled is not true")
    if order_contract.get("preview_only") is not True:
        issues.append("order_contract.preview_only is not true")
    for field in ("order_id", "source_signal_id", "code"):
        if not _clean_text(order_contract.get(field)):
            issues.append(f"order_contract.{field} is required")
    side = _clean_text(order_contract.get("side")).upper()
    if side not in VALID_SIDES:
        issues.append("order_contract.side is invalid")
    if not _positive_number(order_contract.get("quantity")):
        issues.append("order_contract.quantity must be greater than 0")
    if not _positive_number(order_contract.get("price")):
        issues.append("order_contract.price must be greater than 0")
    return issues


def _build_contract(order_contract: dict[str, Any], approval_result: dict[str, Any], readiness_result: dict[str, Any]) -> dict[str, Any]:
    return {
        "contract_type": CONTRACT_TYPE,
        "queue_contract_version": "preview-1",
        "preview_only": True,
        "runtime_write": False,
        "queue_write": False,
        "queue_commit_called": False,
        "send_order_called": False,
        "order_id": _clean_text(order_contract.get("order_id") or order_contract.get("id")),
        "source_signal_id": _clean_text(order_contract.get("source_signal_id")),
        "code": _clean_text(order_contract.get("code")),
        "side": _clean_text(order_contract.get("side")).upper(),
        "quantity": deepcopy(order_contract.get("quantity")),
        "price": deepcopy(order_contract.get("price")),
        "order_status": order_contract.get("status"),
        "execution_enabled": order_contract.get("execution_enabled") is True,
        "approval_status": approval_result.get("status"),
        "readiness_status": readiness_result.get("status"),
        "required_next_service": "QUEUE_COMMIT_SERVICE",
    }


def _build_plan(order_contract: dict[str, Any], commit_contract: dict[str, Any], runtime_snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "plan_type": PLAN_TYPE,
        "preview_only": True,
        "runtime_write": False,
        "queue_write": False,
        "queue_commit_called": False,
        "send_order_called": False,
        "target": "runtime/order_queue.json",
        "would_create_status": "ORDER_QUEUED",
        "would_use_contract": deepcopy(commit_contract),
        "order_contract": deepcopy(order_contract),
        "runtime_snapshot_summary": {
            "lock_count": len(_as_list(runtime_snapshot.get("locks"))),
            "existing_order_count": len(_as_list(runtime_snapshot.get("existing_orders"))),
        },
        "steps": [
            "verify approval gate result",
            "verify readiness result",
            "verify order contract",
            "recheck runtime lock and duplicate state",
            "hand off to queue commit service only after explicit commit confirmation",
        ],
    }


def build_queue_commit_contract_preview(
    approval_result: Any,
    readiness_result: Any,
    order_contract: Any,
    runtime_snapshot: Any,
) -> dict[str, Any]:
    """Build an in-memory Queue Commit contract preview."""
    approval = _as_dict(approval_result)
    readiness = _as_dict(readiness_result)
    order = _as_dict(order_contract)
    runtime = _as_dict(runtime_snapshot)

    if not approval:
        return _result(status=STATUS_INVALID, issues=["approval_result must be a dict"])
    if not readiness:
        return _result(status=STATUS_INVALID, issues=["readiness_result must be a dict"])
    if not order:
        return _result(status=STATUS_INVALID, issues=["order_contract must be a non-empty dict"])
    if approval.get("status") == "INVALID":
        return _result(status=STATUS_INVALID, issues=["approval_result.status is INVALID"])
    if approval.get("status") != "APPROVED":
        return _result(status=STATUS_BLOCKED, issues=["approval_result.status is not APPROVED"], warnings=_as_list(approval.get("warnings")))
    if approval.get("preview_only") is not True:
        return _result(status=STATUS_INVALID, issues=["approval_result.preview_only is not true"])
    if readiness.get("status") == "INVALID":
        return _result(status=STATUS_INVALID, issues=["readiness_result.status is INVALID"])
    if readiness.get("status") != "READY":
        return _result(status=STATUS_BLOCKED, issues=["readiness_result.status is not READY"], warnings=_as_list(readiness.get("warnings")))
    if readiness.get("preview_only") is not True:
        return _result(status=STATUS_INVALID, issues=["readiness_result.preview_only is not true"])

    order_issues = _validate_order_contract(order)
    if order_issues:
        return _result(status=STATUS_INVALID, issues=order_issues)

    blocked_issues: list[str] = []
    if _runtime_lock_exists(runtime, order):
        blocked_issues.append("runtime lock exists for order")
    if _duplicate_order_exists(runtime, order):
        blocked_issues.append("duplicate order exists")
    if blocked_issues:
        return _result(status=STATUS_BLOCKED, issues=blocked_issues)

    commit_contract = _build_contract(order, approval, readiness)
    commit_plan = _build_plan(order, commit_contract, runtime)
    return _result(
        status=STATUS_READY,
        commit_contract=commit_contract,
        commit_plan=commit_plan,
        warnings=_as_list(approval.get("warnings")) + _as_list(readiness.get("warnings")),
    )
