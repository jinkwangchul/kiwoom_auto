# -*- coding: utf-8 -*-
"""Preview-only readiness validator before queue commit.

This module performs only in-memory checks. It does not write runtime files,
order_queue.json, rules.json, call Queue Commit, SendOrder, Kiwoom, or GUI code.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


STATUS_READY = "READY"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"
VALID_SIDES = {"BUY", "SELL"}
ALLOWED_OPERATION_STATES = {"READY", "RUNNING", "OPEN", "NORMAL", "IDLE", "ENABLED", "TRADING_ENABLED"}
BLOCKED_OPERATION_STATES = {"BLOCKED", "HALTED", "STOPPED", "DISABLED", "PAUSED", "CLOSED"}


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
    readiness: dict[str, Any],
    issues: list[str],
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    summary = {
        "ready": status == STATUS_READY,
        "blocked": status == STATUS_BLOCKED,
        "invalid": status == STATUS_INVALID,
        "issue_count": len(issues),
        "warning_count": len(warnings or []),
    }
    return {
        "status": status,
        "readiness": deepcopy(readiness),
        "issues": list(issues),
        "warnings": list(warnings or []),
        "validation_summary": summary,
        "preview_only": True,
        "runtime_write": False,
        "queue_write": False,
        "send_order_called": False,
        "queue_commit_called": False,
    }


def _matches_order(record: Any, order_contract: dict[str, Any]) -> bool:
    record_dict = _as_dict(record)
    if not record_dict:
        return False
    order_id = _clean_text(order_contract.get("order_id") or order_contract.get("id"))
    source_signal_id = _clean_text(order_contract.get("source_signal_id"))
    code = _clean_text(order_contract.get("code"))
    for key, expected in (
        ("order_id", order_id),
        ("id", order_id),
        ("source_order_id", order_id),
        ("source_signal_id", source_signal_id),
        ("code", code),
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
    order_id = _clean_text(order_contract.get("order_id") or order_contract.get("id"))
    source_signal_id = _clean_text(order_contract.get("source_signal_id"))
    for key in ("locked_order_ids", "locked_source_signal_ids"):
        locked_values = {_clean_text(value) for value in _as_list(snapshot.get(key))}
        if order_id in locked_values or source_signal_id in locked_values:
            return True
    return False


def _duplicate_order_exists(runtime_snapshot: Any, order_contract: dict[str, Any]) -> bool:
    snapshot = _as_dict(runtime_snapshot)
    order_id = _clean_text(order_contract.get("order_id") or order_contract.get("id"))
    for key in ("duplicate_order_ids", "existing_order_ids"):
        if order_id and order_id in {_clean_text(value) for value in _as_list(snapshot.get(key))}:
            return True
    for key in ("orders", "existing_orders", "order_queue", "executions", "order_executions"):
        for record in _as_list(snapshot.get(key)):
            if order_id and _clean_text(_as_dict(record).get("order_id") or _as_dict(record).get("id")) == order_id:
                return True
    return False


def _operation_allowed(operation_state: Any) -> bool:
    state = _as_dict(operation_state)
    if state.get("emergency_stop") is True:
        return False
    if state.get("operation_allowed") is False or state.get("execution_allowed") is False:
        return False
    status = _clean_text(state.get("status") or state.get("state") or state.get("mode")).upper()
    if status in BLOCKED_OPERATION_STATES:
        return False
    if status and status not in ALLOWED_OPERATION_STATES:
        return False
    return True


def validate_execution_readiness(
    preview_controller_result: Any,
    guard: Any,
    runtime_snapshot: Any,
    operation_state: Any,
) -> dict[str, Any]:
    """Validate whether a preview controller result may proceed to queue commit."""
    preview = _as_dict(preview_controller_result)
    guard_dict = _as_dict(guard)
    runtime_dict = _as_dict(runtime_snapshot)
    operation_dict = _as_dict(operation_state)
    readiness: dict[str, Any] = {
        "preview_controller_ready": False,
        "order_contract_ready": False,
        "guard_ready": False,
        "runtime_ready": False,
        "operation_ready": False,
    }

    if not preview:
        return _result(status=STATUS_INVALID, readiness=readiness, issues=["preview_controller_result must be a dict"])

    issues: list[str] = []
    warnings: list[str] = []
    if preview.get("status") != STATUS_READY:
        issues.append("preview_controller_result.status is not READY")
        return _result(status=STATUS_BLOCKED, readiness=readiness, issues=issues, warnings=warnings)
    readiness["preview_controller_ready"] = True

    order_contract = _as_dict(preview.get("order_contract"))
    if not order_contract:
        issues.append("order_contract is required")
        return _result(status=STATUS_INVALID, readiness=readiness, issues=issues, warnings=warnings)

    invalid_contract_issues: list[str] = []
    if order_contract.get("status") != "REAL_READY":
        invalid_contract_issues.append("order_contract.status is not REAL_READY")
    if order_contract.get("execution_enabled") is not True:
        invalid_contract_issues.append("order_contract.execution_enabled is not true")
    if order_contract.get("preview_only") is not True:
        invalid_contract_issues.append("order_contract.preview_only is not true")
    if not _clean_text(order_contract.get("code")):
        invalid_contract_issues.append("order_contract.code is required")
    side = _clean_text(order_contract.get("side")).upper()
    if side not in VALID_SIDES:
        invalid_contract_issues.append("order_contract.side is invalid")
    if not _positive_number(order_contract.get("quantity")):
        invalid_contract_issues.append("order_contract.quantity must be greater than 0")
    if not _positive_number(order_contract.get("price")):
        invalid_contract_issues.append("order_contract.price must be greater than 0")
    if not _clean_text(order_contract.get("source_signal_id")):
        invalid_contract_issues.append("order_contract.source_signal_id is required")
    if invalid_contract_issues:
        return _result(status=STATUS_INVALID, readiness=readiness, issues=invalid_contract_issues, warnings=warnings)
    readiness["order_contract_ready"] = True

    blocked_issues: list[str] = []
    if preview.get("preview_only") is not True:
        blocked_issues.append("preview_controller_result.preview_only is not true")
    if guard_dict.get("operator_confirmed") is not True:
        blocked_issues.append("guard.operator_confirmed is not true")
    if guard_dict.get("real_trade_enabled") is not True:
        blocked_issues.append("guard.real_trade_enabled is not true")
    if guard_dict.get("real_trade_guard_ok") is not True:
        blocked_issues.append("guard.real_trade_guard_ok is not true")
    if not _clean_text(guard_dict.get("account_no")):
        blocked_issues.append("guard.account_no is required")
    readiness["guard_ready"] = not blocked_issues

    if _runtime_lock_exists(runtime_dict, order_contract):
        blocked_issues.append("runtime lock exists for order")
    if _duplicate_order_exists(runtime_dict, order_contract):
        blocked_issues.append("duplicate order exists")
    readiness["runtime_ready"] = not any(
        issue in {"runtime lock exists for order", "duplicate order exists"} for issue in blocked_issues
    )

    if operation_dict.get("emergency_stop") is True:
        blocked_issues.append("operation_state.emergency_stop is true")
    if not _operation_allowed(operation_dict):
        if "operation_state.emergency_stop is true" not in blocked_issues:
            blocked_issues.append("operation_state is not allowed")
    readiness["operation_ready"] = not any(issue.startswith("operation_state") for issue in blocked_issues)

    if blocked_issues:
        return _result(status=STATUS_BLOCKED, readiness=readiness, issues=blocked_issues, warnings=warnings)

    readiness.update(
        {
            "guard_ready": True,
            "runtime_ready": True,
            "operation_ready": True,
            "queue_commit_ready": True,
        }
    )
    return _result(status=STATUS_READY, readiness=readiness, issues=[], warnings=warnings)
