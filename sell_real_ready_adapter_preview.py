"""Preview-only adapter from SELL execution contracts to REAL_READY candidates."""

from __future__ import annotations

from copy import deepcopy
from numbers import Number
from typing import Any


READY = "READY"
BLOCKED = "BLOCKED"
INVALID = "INVALID"

PREVIEW_TYPE = "SELL_REAL_READY_ADAPTER_PREVIEW"
SOURCE_PREVIEW_TYPE = "SELL_EXECUTION_CONTRACT_PREVIEW"
ORDER_CANDIDATE_TYPE = "SELL_REAL_READY_ORDER_CANDIDATE_PREVIEW"
SUPPORTED_ACTION_SOURCES = {"METHOD", "COMPLETION"}
EXCLUDED_ACTION_SOURCES = {"PENDING", "CANCEL_PENDING_ORDER"}


def build_sell_real_ready_adapter_preview(
    execution_contract_preview: dict[str, Any],
    market_context: dict[str, Any] | None = None,
    runtime_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Normalize READY SELL execution contracts into preview-only candidates."""
    result = _base_result(market_context, runtime_context)

    if not isinstance(execution_contract_preview, dict):
        result["status"] = INVALID
        result["reasons"].append("execution_contract_preview must be a dict")
        return result

    result["execution_contract_snapshot"] = deepcopy(execution_contract_preview)

    if execution_contract_preview.get("preview_type") != SOURCE_PREVIEW_TYPE:
        result["status"] = INVALID
        result["reasons"].append("execution_contract_preview preview_type is invalid")
        return result

    if _has_forbidden_safety_flag(execution_contract_preview):
        result["status"] = INVALID
        result["reasons"].append("execution_contract_preview safety flag violation")
        return result

    contracts = execution_contract_preview.get("contracts")
    if not isinstance(contracts, list):
        result["status"] = INVALID
        result["reasons"].append("contracts must be a list")
        return result

    if isinstance(execution_contract_preview.get("warnings"), list):
        result["warnings"].extend(deepcopy(execution_contract_preview["warnings"]))

    summary = result["summary"]
    summary["contract_count"] = len(contracts)

    has_invalid = False
    for index, contract in enumerate(contracts):
        if not isinstance(contract, dict):
            result["blocked_candidates"].append(
                _blocked_candidate(index, "UNKNOWN", "contract must be a dict", invalid=True)
            )
            has_invalid = True
            continue

        action_source = _clean_text(contract.get("action_source")).upper()
        contract_status = _clean_text(contract.get("contract_status")).upper()

        if action_source in EXCLUDED_ACTION_SOURCES:
            summary["excluded_count"] += 1
            result["blocked_candidates"].append(
                _excluded_candidate(
                    contract,
                    index,
                    "PENDING cancel action requires a separate cancel execution path",
                )
            )
            continue

        if action_source not in SUPPORTED_ACTION_SOURCES:
            result["blocked_candidates"].append(
                _blocked_candidate(index, action_source or "UNKNOWN", "unsupported action_source", invalid=True)
            )
            has_invalid = True
            continue

        if contract_status == INVALID:
            summary["excluded_count"] += 1
            continue

        if contract_status != READY:
            summary["excluded_count"] += 1
            continue

        summary["ready_contract_count"] += 1
        candidate = _candidate_from_contract(contract, index, action_source)
        if candidate["candidate_status"] == READY:
            result["order_candidates"].append(candidate)
            summary["ready_candidate_count"] += 1
        elif candidate["candidate_status"] == INVALID:
            result["blocked_candidates"].append(candidate)
            summary["invalid_candidate_count"] += 1
            has_invalid = True
        else:
            result["blocked_candidates"].append(candidate)
            summary["blocked_candidate_count"] += 1

    summary["candidate_count"] = len(result["order_candidates"])
    summary["blocked_candidate_count"] = len(result["blocked_candidates"]) - summary["invalid_candidate_count"]
    summary["priority_selected"] = False
    summary["auto_selected"] = False
    summary["real_ready_state_changed"] = False

    if has_invalid:
        result["status"] = INVALID
    elif result["order_candidates"]:
        result["status"] = READY
    else:
        result["status"] = BLOCKED
        if not result["reasons"]:
            result["reasons"].append("no READY SELL REAL_READY preview candidates")

    return result


def _base_result(
    market_context: dict[str, Any] | None,
    runtime_context: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "preview_type": PREVIEW_TYPE,
        "preview_only": True,
        "execution_connected": False,
        "pipeline_called": False,
        "runtime_write": False,
        "queue_write": False,
        "order_request_created": False,
        "send_order": False,
        "real_ready_state_changed": False,
        "status": BLOCKED,
        "order_candidates": [],
        "blocked_candidates": [],
        "summary": {
            "contract_count": 0,
            "ready_contract_count": 0,
            "candidate_count": 0,
            "ready_candidate_count": 0,
            "blocked_candidate_count": 0,
            "invalid_candidate_count": 0,
            "excluded_count": 0,
            "priority_selected": False,
            "auto_selected": False,
            "real_ready_state_changed": False,
        },
        "warnings": [],
        "reasons": [],
        "execution_contract_snapshot": {},
        "market_context_snapshot": deepcopy(market_context or {}),
        "runtime_context_snapshot": deepcopy(runtime_context or {}),
    }


def _candidate_from_contract(
    contract: dict[str, Any],
    index: int,
    action_source: str,
) -> dict[str, Any]:
    candidate = _candidate_shell(contract, index, action_source)
    reasons: list[str] = []
    invalid_reasons: list[str] = []

    order_id = contract.get("order_id") or contract.get("id")
    source_signal_id = contract.get("source_signal_id")
    code = contract.get("code")
    side = _clean_text(contract.get("side")).upper()
    quantity = contract.get("quantity")
    hoga = _clean_text(contract.get("hoga")).upper()
    order_type = _clean_text(contract.get("order_type")).upper()
    order_intent = contract.get("order_intent")

    if _has_forbidden_safety_flag(contract):
        invalid_reasons.append("contract safety flag violation")
    if not order_id:
        reasons.append("id/order_id is required")
    if not source_signal_id:
        reasons.append("source_signal_id is required")
    if not code:
        reasons.append("code is required")
    if side != "SELL":
        invalid_reasons.append("side must be SELL")
    if not _positive_number(quantity):
        reasons.append("quantity must be positive")
    if action_source not in SUPPORTED_ACTION_SOURCES:
        invalid_reasons.append("unsupported action_source")
    if not isinstance(order_intent, dict):
        reasons.append("order_intent is required")
    if order_type != "SELL":
        invalid_reasons.append("order_type must be SELL")
    if hoga not in {"MARKET", "LIMIT"}:
        invalid_reasons.append("hoga must be MARKET or LIMIT")

    price = contract.get("price")
    price_required = bool(contract.get("price_required"))
    if hoga == "MARKET":
        candidate["price"] = None
        candidate["price_required"] = False
        candidate["warnings"].append(
            "MARKET price=None conflicts with current execution readiness validator price requirement"
        )
        reasons.append("MARKET price=None is not convertible to common readiness input in this phase")
    elif hoga == "LIMIT":
        candidate["price_required"] = True
        if not _positive_number(price):
            reasons.append("LIMIT price must be positive")

    candidate.update(
        {
            "id": order_id,
            "order_id": order_id,
            "source_signal_id": source_signal_id,
            "code": code,
            "symbol": contract.get("symbol"),
            "side": "SELL",
            "quantity": quantity,
            "hoga": hoga or None,
            "order_type": order_type or None,
            "order_intent": deepcopy(order_intent) if isinstance(order_intent, dict) else None,
            "action_source": action_source,
            "status": "REAL_READY",
            "execution_enabled": True,
            "source_contract": deepcopy(contract),
        }
    )

    if invalid_reasons:
        candidate["candidate_status"] = INVALID
        candidate["reasons"].extend(invalid_reasons)
    elif reasons:
        candidate["candidate_status"] = BLOCKED
        candidate["reasons"].extend(reasons)
    return candidate


def _candidate_shell(contract: dict[str, Any], index: int, action_source: str) -> dict[str, Any]:
    warnings = deepcopy(contract.get("warnings")) if isinstance(contract.get("warnings"), list) else []
    reasons = deepcopy(contract.get("reasons")) if isinstance(contract.get("reasons"), list) else []
    return {
        "candidate_type": ORDER_CANDIDATE_TYPE,
        "candidate_status": READY,
        "action_source": action_source,
        "contract_index": index,
        "preview_only": True,
        "execution_connected": False,
        "pipeline_called": False,
        "runtime_write": False,
        "queue_write": False,
        "order_request_created": False,
        "send_order": False,
        "real_ready_state_changed": False,
        "priority_selected": False,
        "auto_selected": False,
        "price": contract.get("price"),
        "price_required": bool(contract.get("price_required")),
        "warnings": warnings,
        "reasons": reasons,
    }


def _excluded_candidate(contract: dict[str, Any], index: int, reason: str) -> dict[str, Any]:
    candidate = _candidate_shell(contract, index, _clean_text(contract.get("action_source")).upper())
    candidate.update(
        {
            "candidate_status": BLOCKED,
            "status": "NOT_APPLICABLE",
            "execution_enabled": False,
            "id": contract.get("id"),
            "order_id": contract.get("order_id"),
            "source_signal_id": contract.get("source_signal_id"),
            "code": contract.get("code"),
            "symbol": contract.get("symbol"),
            "side": contract.get("side"),
            "quantity": contract.get("quantity"),
            "hoga": contract.get("hoga"),
            "order_type": contract.get("order_type"),
            "order_intent": deepcopy(contract.get("order_intent")) if isinstance(contract.get("order_intent"), dict) else None,
            "source_contract": deepcopy(contract),
        }
    )
    candidate["reasons"].append(reason)
    return candidate


def _blocked_candidate(
    index: int,
    action_source: str,
    reason: str,
    *,
    invalid: bool = False,
) -> dict[str, Any]:
    return {
        "candidate_type": ORDER_CANDIDATE_TYPE,
        "candidate_status": INVALID if invalid else BLOCKED,
        "action_source": action_source,
        "contract_index": index,
        "preview_only": True,
        "execution_connected": False,
        "pipeline_called": False,
        "runtime_write": False,
        "queue_write": False,
        "order_request_created": False,
        "send_order": False,
        "real_ready_state_changed": False,
        "priority_selected": False,
        "auto_selected": False,
        "reasons": [reason],
        "warnings": [],
    }


def _positive_number(value: Any) -> bool:
    return isinstance(value, Number) and not isinstance(value, bool) and value > 0


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _has_forbidden_safety_flag(payload: dict[str, Any]) -> bool:
    return any(
        payload.get(flag) is True
        for flag in (
            "execution_connected",
            "pipeline_called",
            "runtime_write",
            "queue_write",
            "order_request_created",
            "send_order",
            "real_ready_state_changed",
        )
    )
