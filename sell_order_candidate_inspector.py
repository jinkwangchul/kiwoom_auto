# -*- coding: utf-8 -*-
"""Preview-only inspector for SELL order candidates.

The inspector validates whether SELL order candidate previews are structurally
eligible for a later REAL_READY boundary. It does not promote status, create
order requests, touch queues, write runtime files, or call SendOrder.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


INSPECTION_TYPE = "SELL_ORDER_CANDIDATE_INSPECTION"
STATUS_READY = "READY"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"
STATUS_NOT_APPLICABLE = "NOT_APPLICABLE"

ACTION_METHOD = "METHOD"
ACTION_COMPLETION = "COMPLETION"
ACTION_PENDING = "PENDING"
KNOWN_ACTION_SOURCES = {ACTION_METHOD, ACTION_COMPLETION, ACTION_PENDING}

SAFETY_EXPECTED = {
    "preview_only": True,
    "execution_connected": False,
    "runtime_write": False,
    "queue_write": False,
    "send_order": False,
    "order_request_created": False,
    "candidate_created": False,
}

NORMALIZED_FIELDS = (
    "action_source",
    "symbol",
    "side",
    "signal_id",
    "method_set",
    "order_id",
    "quantity",
    "price",
    "hoga",
    "order_type",
    "source_previews",
    "preview_only",
    "execution_connected",
    "runtime_write",
    "queue_write",
    "send_order",
    "order_request_created",
    "candidate_created",
)


def inspect_sell_order_candidates(candidate_preview: Any) -> dict[str, Any]:
    """Inspect preview candidates without creating executable order requests."""

    result = _base_result()
    if not isinstance(candidate_preview, dict):
        result["status"] = STATUS_INVALID
        result["reasons"].append("candidate_preview must be a dict")
        return result

    result["warnings"].extend(_string_list(candidate_preview.get("warnings")))
    candidates = candidate_preview.get("candidates")
    if not isinstance(candidates, list):
        result["status"] = STATUS_INVALID
        result["reasons"].append("candidates must be a list")
        return result

    inspected: list[dict[str, Any]] = []
    for candidate in candidates:
        inspection = _inspect_candidate(candidate)
        if inspection is None:
            continue
        inspected.append(inspection)
        result["warnings"].extend(inspection["warnings"])

    result["inspected_candidates"] = inspected
    result["candidate_count"] = len(inspected)
    result["real_ready_eligible"] = any(item["real_ready_eligible"] for item in inspected)
    result["status"] = _overall_status(inspected)
    if not inspected:
        result["reasons"].append("no inspectable candidates")
    return result


def _base_result() -> dict[str, Any]:
    return {
        "inspection_type": INSPECTION_TYPE,
        "preview_only": True,
        "execution_connected": False,
        "runtime_write": False,
        "queue_write": False,
        "send_order": False,
        "status": STATUS_BLOCKED,
        "real_ready_eligible": False,
        "candidate_count": 0,
        "inspected_candidates": [],
        "reasons": [],
        "warnings": [],
    }


def _base_candidate_result(action_source: str | None) -> dict[str, Any]:
    return {
        "action_source": action_source,
        "status": STATUS_BLOCKED,
        "real_ready_eligible": False,
        "normalized_candidate": {},
        "reasons": [],
        "warnings": [],
    }


def _inspect_candidate(candidate: Any) -> dict[str, Any] | None:
    if not isinstance(candidate, dict):
        inspected = _base_candidate_result(None)
        inspected["status"] = STATUS_INVALID
        inspected["reasons"].append("candidate must be a dict")
        return inspected

    if _text(candidate.get("status")) == STATUS_NOT_APPLICABLE:
        return None

    action_source = _text(candidate.get("action_source")) or None
    inspected = _base_candidate_result(action_source)
    inspected["warnings"].extend(_string_list(candidate.get("warnings")))
    inspected["normalized_candidate"] = _normalize_candidate(candidate)

    invalid_reasons = _safety_violations(candidate)
    blocked_reasons: list[str] = []

    status = _text(candidate.get("status"))
    if status == STATUS_INVALID:
        invalid_reasons.append("candidate status is INVALID")
    elif status == STATUS_BLOCKED:
        blocked_reasons.append("candidate status is BLOCKED")
    elif status != STATUS_READY:
        blocked_reasons.append("candidate status is not READY")

    if action_source not in KNOWN_ACTION_SOURCES:
        invalid_reasons.append(f"unknown action_source: {action_source}")

    if action_source in KNOWN_ACTION_SOURCES:
        action_blocked, action_invalid = _inspect_by_action_source(action_source, candidate)
        blocked_reasons.extend(action_blocked)
        invalid_reasons.extend(action_invalid)

    if invalid_reasons:
        inspected["status"] = STATUS_INVALID
        inspected["reasons"].extend(invalid_reasons)
        return inspected
    if blocked_reasons:
        inspected["status"] = STATUS_BLOCKED
        inspected["reasons"].extend(blocked_reasons)
        return inspected

    inspected["status"] = STATUS_READY
    inspected["real_ready_eligible"] = True
    return inspected


def _inspect_by_action_source(action_source: str, candidate: dict[str, Any]) -> tuple[list[str], list[str]]:
    blocked: list[str] = []
    invalid: list[str] = []

    _require_text(candidate, "symbol", blocked)
    side = _text(candidate.get("side"))
    if side != "SELL":
        invalid.append("side must be SELL")

    source_previews = candidate.get("source_previews")
    if not isinstance(source_previews, dict):
        blocked.append("source_previews is required")
        source_previews = {}

    quantity, quantity_error = _positive_number(candidate.get("quantity"))
    if quantity_error == "missing":
        blocked.append("quantity is required")
    elif quantity_error == "invalid":
        invalid.append("quantity must be numeric")
    elif quantity is not None and quantity <= 0:
        blocked.append("quantity must be greater than 0")

    if action_source == ACTION_METHOD:
        _inspect_method_candidate(candidate, source_previews, blocked, invalid)
    elif action_source == ACTION_COMPLETION:
        _inspect_completion_candidate(candidate, source_previews, blocked, invalid)
    elif action_source == ACTION_PENDING:
        _inspect_pending_candidate(candidate, source_previews, blocked, invalid)

    return blocked, invalid


def _inspect_method_candidate(
    candidate: dict[str, Any],
    source_previews: dict[str, Any],
    blocked: list[str],
    invalid: list[str],
) -> None:
    if "method_preview" not in source_previews:
        blocked.append("source_previews.method_preview is required")
    if _text(candidate.get("order_type")) != "SELL":
        invalid.append("order_type must be SELL")

    hoga = _text(candidate.get("hoga"))
    if hoga not in {"MARKET", "LIMIT"}:
        invalid.append("hoga must be MARKET or LIMIT")
        return
    if hoga == "LIMIT":
        price, price_error = _positive_number(candidate.get("price"))
        if price_error == "missing":
            blocked.append("LIMIT price is required")
        elif price_error == "invalid":
            invalid.append("LIMIT price must be numeric")
        elif price is not None and price <= 0:
            blocked.append("LIMIT price must be greater than 0")


def _inspect_completion_candidate(
    candidate: dict[str, Any],
    source_previews: dict[str, Any],
    blocked: list[str],
    invalid: list[str],
) -> None:
    if "completion" not in source_previews:
        blocked.append("source_previews.completion is required")
    if _text(candidate.get("order_type")) != "SELL":
        invalid.append("order_type must be SELL")
    if _text(candidate.get("hoga")) != "MARKET":
        invalid.append("hoga must be MARKET")
    if candidate.get("price") is not None:
        invalid.append("COMPLETION price must be None")


def _inspect_pending_candidate(
    candidate: dict[str, Any],
    source_previews: dict[str, Any],
    blocked: list[str],
    invalid: list[str],
) -> None:
    if "pending" not in source_previews:
        blocked.append("source_previews.pending is required")
    _require_text(candidate, "order_id", blocked)
    if candidate.get("price") is not None:
        invalid.append("PENDING price must be None")
    if candidate.get("hoga") is not None:
        invalid.append("PENDING hoga must be None")
    if candidate.get("order_type") is not None:
        invalid.append("PENDING order_type must be None")


def _overall_status(inspected: list[dict[str, Any]]) -> str:
    if any(item["status"] == STATUS_INVALID for item in inspected):
        return STATUS_INVALID
    if any(item["real_ready_eligible"] for item in inspected):
        return STATUS_READY
    return STATUS_BLOCKED


def _normalize_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    return {field: deepcopy(candidate.get(field)) for field in NORMALIZED_FIELDS if field in candidate}


def _safety_violations(candidate: dict[str, Any]) -> list[str]:
    reasons = []
    for key, expected in SAFETY_EXPECTED.items():
        if candidate.get(key) is not expected:
            reasons.append(f"{key} must be {expected}")
    return reasons


def _positive_number(value: Any) -> tuple[float | None, str | None]:
    if value is None or value == "":
        return None, "missing"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None, "invalid"
    return number, None


def _require_text(source: dict[str, Any], key: str, blocked: list[str]) -> None:
    if not _text(source.get(key)):
        blocked.append(f"{key} is required")


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item is not None]


def _text(value: Any) -> str:
    return str(value or "").strip()
