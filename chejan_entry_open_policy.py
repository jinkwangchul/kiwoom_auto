# -*- coding: utf-8 -*-
"""Policy-only gate for opening Chejan event entry."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


POLICY_TYPE = "CHEJAN_ENTRY_OPEN_POLICY"
STATUS_OPEN = "CHEJAN_ENTRY_OPEN"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"

ALLOWED_OPERATION_STATES = {"READY", "RUNNING", "OPEN", "NORMAL", "IDLE", "ENABLED", "TRADING_ENABLED"}
BLOCKED_OPERATION_STATES = {"BLOCKED", "HALTED", "STOPPED", "DISABLED", "PAUSED", "CLOSED"}
REQUIRED_IDENTITY_FIELDS = ("record_id", "order_id", "dispatch_id", "source_signal_id", "order_queued_id")


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
    policy: dict[str, Any] | None = None,
    issues: list[str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "policy_type": POLICY_TYPE,
        "status": status,
        "policy": deepcopy(policy) if isinstance(policy, dict) else {},
        "issues": list(issues or []),
        "warnings": list(warnings or []),
        "chejan_live_connected": False,
        "runtime_write": False,
        "queue_write": False,
        "lifecycle_created": False,
    }


def _operation_allowed(operation_state: dict[str, Any]) -> tuple[bool, str | None]:
    if operation_state.get("emergency_stop") is True:
        return False, "operation_state.emergency_stop is true"
    if operation_state.get("operation_allowed") is False or operation_state.get("chejan_allowed") is False:
        return False, "operation_state is not allowed"
    status = _text(operation_state.get("status")).upper()
    if status in BLOCKED_OPERATION_STATES:
        return False, "operation_state.status is blocked"
    if status and status not in ALLOWED_OPERATION_STATES:
        return False, "operation_state.status is not allowed"
    return True, None


def _runtime_enabled(runtime_context: dict[str, Any]) -> tuple[bool, str | None]:
    if runtime_context.get("emergency_stop") is True:
        return False, "chejan_runtime_context.emergency_stop is true"
    if runtime_context.get("chejan_runtime_enabled") is not True:
        return False, "chejan_runtime_context.chejan_runtime_enabled is not true"
    if runtime_context.get("chejan_entry_enabled") is False:
        return False, "chejan_runtime_context.chejan_entry_enabled is false"
    return True, None


def _duplicate_entry_exists(runtime_context: dict[str, Any], identity: dict[str, Any]) -> bool:
    candidates = {
        _text(identity.get("record_id")),
        _text(identity.get("order_id")),
        _text(identity.get("dispatch_id")),
        _text(identity.get("order_queued_id")),
    }
    candidates.discard("")

    for key in (
        "duplicate_entry_ids",
        "existing_entry_ids",
        "active_chejan_entry_ids",
        "existing_chejan_entries",
        "active_chejan_entries",
    ):
        value = runtime_context.get(key)
        if isinstance(value, dict):
            iterable = list(value.keys()) + list(value.values())
        else:
            iterable = _as_list(value)
        for item in iterable:
            if isinstance(item, dict):
                item_values = {
                    _text(item.get("record_id")),
                    _text(item.get("order_id")),
                    _text(item.get("dispatch_id")),
                    _text(item.get("order_queued_id")),
                }
                item_values.discard("")
                if candidates.intersection(item_values):
                    return True
            elif _text(item) in candidates:
                return True
    return runtime_context.get("duplicate_entry") is True or runtime_context.get("duplicate_chejan_entry") is True


def evaluate_chejan_entry_open_policy(
    chejan_entry_contract_result: Any,
    chejan_runtime_context: Any,
    operation_state: Any,
) -> dict[str, Any]:
    """Evaluate whether Chejan entry may be opened, without connecting handlers."""
    entry_result = _as_dict(chejan_entry_contract_result)
    runtime_context = _as_dict(chejan_runtime_context)
    operation = _as_dict(operation_state)

    if not entry_result:
        return _result(status=STATUS_INVALID, issues=["chejan_entry_contract_result must be a dict"])
    if not runtime_context:
        return _result(status=STATUS_INVALID, issues=["chejan_runtime_context must be a non-empty dict"])
    if not operation:
        return _result(status=STATUS_INVALID, issues=["operation_state must be a non-empty dict"])

    warnings = list(entry_result.get("warnings") or [])
    entry_status = _text(entry_result.get("status")).upper()
    if entry_status == "BLOCKED":
        return _result(
            status=STATUS_BLOCKED,
            issues=["chejan_entry_contract_result.status is BLOCKED"] + list(entry_result.get("issues") or []),
            warnings=warnings,
        )
    if entry_status == "INVALID":
        return _result(
            status=STATUS_INVALID,
            issues=["chejan_entry_contract_result.status is INVALID"] + list(entry_result.get("issues") or []),
            warnings=warnings,
        )
    if entry_status != "CHEJAN_ENTRY_READY":
        return _result(status=STATUS_INVALID, issues=["chejan_entry_contract_result.status is not supported"], warnings=warnings)

    if entry_result.get("preview_only") is not True:
        return _result(status=STATUS_INVALID, issues=["chejan_entry_contract_result.preview_only is not true"], warnings=warnings)
    if entry_result.get("chejan_called") is not False:
        return _result(status=STATUS_INVALID, issues=["chejan_entry_contract_result.chejan_called must be false"], warnings=warnings)
    if entry_result.get("runtime_write") is not False or entry_result.get("queue_write") is not False:
        return _result(status=STATUS_INVALID, issues=["chejan_entry_contract_result write flags must be false"], warnings=warnings)

    contract = _as_dict(entry_result.get("chejan_entry_contract"))
    if not contract:
        return _result(status=STATUS_INVALID, issues=["chejan_entry_contract is required"], warnings=warnings)
    identity = _as_dict(contract.get("identity"))
    if not identity:
        return _result(status=STATUS_INVALID, issues=["chejan_entry_contract.identity is required"], warnings=warnings)
    missing = [field for field in REQUIRED_IDENTITY_FIELDS if not _text(identity.get(field))]
    if missing:
        return _result(status=STATUS_INVALID, issues=["identity missing fields: " + ", ".join(missing)], warnings=warnings)
    if contract.get("chejan_live_connected") is not False:
        return _result(status=STATUS_INVALID, issues=["chejan_entry_contract.chejan_live_connected must be false"], warnings=warnings)

    runtime_ok, runtime_issue = _runtime_enabled(runtime_context)
    if not runtime_ok:
        return _result(status=STATUS_BLOCKED, issues=[runtime_issue or "chejan runtime is not enabled"], warnings=warnings)

    operation_ok, operation_issue = _operation_allowed(operation)
    if not operation_ok:
        return _result(status=STATUS_BLOCKED, issues=[operation_issue or "operation_state is not allowed"], warnings=warnings)

    if _duplicate_entry_exists(runtime_context, identity):
        return _result(status=STATUS_BLOCKED, issues=["duplicate Chejan entry exists"], warnings=warnings)

    policy = {
        "policy_stage": "chejan_entry_open_policy_evaluated",
        "chejan_entry_open_allowed": True,
        "identity": deepcopy(identity),
        "operation_checks": {
            "operation_allowed": True,
            "emergency_stop_absent": True,
            "status": operation.get("status"),
        },
        "runtime_checks": {
            "chejan_runtime_enabled": True,
            "duplicate_entry_absent": True,
            "chejan_live_connected": False,
        },
        "next_stage": "CHEJAN_EVENT_RECEIVE_REQUIRED",
        "chejan_live_connected": False,
        "runtime_write": False,
        "queue_write": False,
        "lifecycle_created": False,
    }
    return _result(status=STATUS_OPEN, policy=policy, issues=[], warnings=warnings)
