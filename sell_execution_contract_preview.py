"""Preview-only SELL execution contract builder.

This module normalizes authorized SELL candidates into deterministic execution
contract previews. It intentionally avoids order request creation, runtime or
queue writes, dispatch calls, and REAL_READY state mutation.
"""

from __future__ import annotations

from copy import deepcopy
from numbers import Number
from typing import Any


READY = "READY"
BLOCKED = "BLOCKED"
INVALID = "INVALID"

PREVIEW_TYPE = "SELL_EXECUTION_CONTRACT_PREVIEW"
SOURCE_AUTHORIZATION_PREVIEW_TYPE = "SELL_REAL_READY_AUTHORIZATION_PREVIEW"

SELL_ORDER_SOURCES = {"METHOD", "COMPLETION"}
SUPPORTED_ACTION_SOURCES = SELL_ORDER_SOURCES | {"PENDING"}
SUPPORTED_HOGA = {"MARKET", "LIMIT"}


def build_sell_execution_contract_preview(
    authorization_preview: dict[str, Any],
    market_context: dict[str, Any] | None = None,
    runtime_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build preview-only SELL execution contracts from authorization output."""
    result = _base_result(market_context, runtime_context)

    if not isinstance(authorization_preview, dict):
        result["status"] = INVALID
        result["reasons"].append("authorization_preview must be a dict")
        return result

    result["authorization_snapshot"] = deepcopy(authorization_preview)

    if authorization_preview.get("preview_type") != SOURCE_AUTHORIZATION_PREVIEW_TYPE:
        result["status"] = INVALID
        result["reasons"].append("authorization_preview preview_type is invalid")
        return result

    if _has_forbidden_safety_flag(authorization_preview):
        result["status"] = INVALID
        result["reasons"].append("authorization_preview safety flag violation")
        return result

    candidates = authorization_preview.get("authorized_candidates")
    if not isinstance(candidates, list):
        result["status"] = INVALID
        result["reasons"].append("authorized_candidates must be a list")
        return result

    input_warnings = authorization_preview.get("warnings")
    if isinstance(input_warnings, list):
        result["warnings"].extend(deepcopy(input_warnings))

    summary = result["summary"]
    summary["candidate_count"] = len(candidates)

    has_invalid = False
    ready_count = 0
    blocked_count = 0

    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            result["contracts"].append(_invalid_contract(index, "candidate must be a dict"))
            has_invalid = True
            continue

        if candidate.get("authorized") is not True:
            summary["unauthorized_skipped_count"] += 1
            continue

        summary["authorized_input_count"] += 1
        action_source = _candidate_action_source(candidate)

        if action_source not in SUPPORTED_ACTION_SOURCES:
            result["contracts"].append(
                _invalid_contract(index, "unsupported action_source", action_source)
            )
            has_invalid = True
            continue

        if action_source == "PENDING":
            contract = _pending_contract(candidate, index)
        else:
            contract = _sell_order_contract(candidate, index, action_source)

        result["contracts"].append(contract)
        if contract["contract_status"] == READY:
            ready_count += 1
        elif contract["contract_status"] == INVALID:
            has_invalid = True
        else:
            blocked_count += 1

    summary["contract_count"] = len(result["contracts"])
    summary["ready_contract_count"] = ready_count
    summary["blocked_contract_count"] = blocked_count
    summary["invalid_contract_count"] = sum(
        1 for contract in result["contracts"] if contract.get("contract_status") == INVALID
    )
    summary["priority_selected"] = False
    summary["auto_selected"] = False
    summary["real_ready_state_changed"] = False

    if has_invalid:
        result["status"] = INVALID
    elif ready_count > 0:
        result["status"] = READY
    else:
        result["status"] = BLOCKED
        if not result["contracts"]:
            result["reasons"].append("no authorized SELL execution contract candidates")

    return result


def _base_result(
    market_context: dict[str, Any] | None,
    runtime_context: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "preview_type": PREVIEW_TYPE,
        "preview_only": True,
        "execution_connected": False,
        "runtime_write": False,
        "queue_write": False,
        "order_request_created": False,
        "send_order": False,
        "real_ready_state_changed": False,
        "status": BLOCKED,
        "contracts": [],
        "summary": {
            "candidate_count": 0,
            "authorized_input_count": 0,
            "unauthorized_skipped_count": 0,
            "contract_count": 0,
            "ready_contract_count": 0,
            "blocked_contract_count": 0,
            "invalid_contract_count": 0,
            "pending_not_applicable_count": 0,
            "market_price_none_count": 0,
            "priority_selected": False,
            "auto_selected": False,
            "real_ready_state_changed": False,
        },
        "warnings": [],
        "reasons": [],
        "authorization_snapshot": {},
        "market_context_snapshot": deepcopy(market_context or {}),
        "runtime_context_snapshot": deepcopy(runtime_context or {}),
    }


def _sell_order_contract(candidate: dict[str, Any], index: int, action_source: str) -> dict[str, Any]:
    normalized = deepcopy(candidate.get("normalized_candidate"))
    if not isinstance(normalized, dict):
        return _invalid_contract(index, "normalized_candidate must be a dict", action_source)

    source_signal_id = (
        normalized.get("source_signal_id")
        or normalized.get("signal_id")
        or candidate.get("source_signal_id")
        or candidate.get("signal_id")
    )
    symbol = normalized.get("symbol") or normalized.get("code")
    code = normalized.get("code") or normalized.get("symbol")
    method_set = normalized.get("method_set")
    candidate_id = normalized.get("id") or normalized.get("order_id")
    contract_id = candidate_id or _deterministic_contract_id(
        action_source,
        source_signal_id,
        method_set,
        index,
    )

    contract = _contract_shell(index, action_source, normalized)
    contract.update(
        {
            "id": contract_id,
            "order_id": normalized.get("order_id") or contract_id,
            "source_signal_id": source_signal_id,
            "code": code,
            "symbol": symbol,
            "side": "SELL",
            "quantity": normalized.get("quantity"),
            "price": normalized.get("price"),
            "price_required": normalized.get("hoga") == "LIMIT",
            "hoga": normalized.get("hoga"),
            "order_type": normalized.get("order_type"),
            "target_status": "REAL_READY",
            "intended_status": "REAL_READY",
            "status": "REAL_READY",
            "execution_enabled": True,
            "order_intent": {
                "side": "SELL",
                "hoga": normalized.get("hoga"),
                "action_source": action_source,
                "price_required": normalized.get("hoga") == "LIMIT",
            },
        }
    )

    if _has_forbidden_safety_flag(normalized) or _has_forbidden_safety_flag(candidate):
        contract["contract_status"] = INVALID
        contract["reasons"].append("candidate safety flag violation")
        return contract

    if normalized.get("side") != "SELL":
        contract["contract_status"] = INVALID
        contract["reasons"].append("side must be SELL")
        return contract

    if normalized.get("order_type") != "SELL":
        contract["contract_status"] = INVALID
        contract["reasons"].append("order_type must be SELL")
        return contract

    if not symbol:
        contract["contract_status"] = BLOCKED
        contract["reasons"].append("symbol is required")

    if not source_signal_id:
        contract["contract_status"] = BLOCKED
        contract["reasons"].append("source_signal_id is required")

    if not _positive_number(normalized.get("quantity")):
        contract["contract_status"] = BLOCKED
        contract["reasons"].append("quantity must be positive")

    hoga = normalized.get("hoga")
    if hoga not in SUPPORTED_HOGA:
        contract["contract_status"] = BLOCKED
        contract["reasons"].append("hoga must be MARKET or LIMIT")
    elif hoga == "MARKET":
        contract["price"] = None
        contract["price_required"] = False
        contract["order_intent"]["price_required"] = False
        contract["warnings"].append(
            "MARKET price=None may be incompatible with current execution readiness validators requiring price > 0"
        )
    elif not _positive_number(normalized.get("price")):
        contract["contract_status"] = BLOCKED
        contract["reasons"].append("LIMIT price must be positive")

    return contract


def _pending_contract(candidate: dict[str, Any], index: int) -> dict[str, Any]:
    normalized = deepcopy(candidate.get("normalized_candidate"))
    if not isinstance(normalized, dict):
        normalized = {}
    contract = _contract_shell(index, "PENDING", normalized)
    contract.update(
        {
            "id": normalized.get("id") or normalized.get("order_id") or _deterministic_contract_id(
                "PENDING",
                normalized.get("signal_id") or candidate.get("signal_id"),
                normalized.get("method_set"),
                index,
            ),
            "order_id": normalized.get("order_id"),
            "source_signal_id": normalized.get("source_signal_id") or normalized.get("signal_id"),
            "code": normalized.get("code") or normalized.get("symbol"),
            "symbol": normalized.get("symbol") or normalized.get("code"),
            "side": normalized.get("side") or "SELL",
            "quantity": normalized.get("quantity"),
            "price": None,
            "price_required": False,
            "hoga": None,
            "order_type": None,
            "target_status": None,
            "intended_status": None,
            "status": "NOT_APPLICABLE",
            "execution_enabled": False,
            "normal_sell_order_contract": False,
            "not_applicable": True,
            "order_intent": {
                "side": "SELL",
                "hoga": None,
                "action_source": "PENDING",
                "price_required": False,
            },
        }
    )
    contract["contract_status"] = BLOCKED
    contract["reasons"].append(
        "PENDING cancel action requires a separate cancel execution path"
    )
    return contract


def _contract_shell(index: int, action_source: str, normalized: dict[str, Any]) -> dict[str, Any]:
    return {
        "contract_type": "SELL_EXECUTION_CONTRACT",
        "contract_status": READY,
        "authorized": True,
        "action_source": action_source,
        "candidate_index": index,
        "preview_only": True,
        "execution_connected": False,
        "runtime_write": False,
        "queue_write": False,
        "order_request_created": False,
        "send_order": False,
        "real_ready_state_changed": False,
        "source_candidate": deepcopy(normalized),
        "warnings": [],
        "reasons": [],
    }


def _invalid_contract(index: int, reason: str, action_source: str | None = None) -> dict[str, Any]:
    contract = _contract_shell(index, action_source or "UNKNOWN", {})
    contract["contract_status"] = INVALID
    contract["authorized"] = False
    contract["reasons"].append(reason)
    return contract


def _candidate_action_source(candidate: dict[str, Any]) -> str | None:
    normalized = candidate.get("normalized_candidate")
    if isinstance(normalized, dict):
        return candidate.get("action_source") or normalized.get("action_source")
    return candidate.get("action_source")


def _deterministic_contract_id(
    action_source: str,
    source_signal_id: Any,
    method_set: Any,
    index: int,
) -> str:
    parts = [
        "SELL_EXEC_CONTRACT",
        str(index),
        str(action_source or "UNKNOWN"),
        str(source_signal_id or "NO_SIGNAL"),
        str(method_set or "NO_METHOD"),
    ]
    return "_".join(parts)


def _positive_number(value: Any) -> bool:
    return isinstance(value, Number) and not isinstance(value, bool) and value > 0


def _has_forbidden_safety_flag(payload: dict[str, Any]) -> bool:
    return any(
        payload.get(flag) is True
        for flag in (
            "execution_connected",
            "runtime_write",
            "queue_write",
            "order_request_created",
            "send_order",
            "real_ready_state_changed",
        )
    )
