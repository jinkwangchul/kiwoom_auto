# -*- coding: utf-8 -*-
"""Preview-only SELL REAL_READY authorization boundary.

This module consumes ``SELL_ORDER_CANDIDATE_INSPECTION`` results and calculates
which candidates are eligible for a later REAL_READY boundary. It never creates
REAL_READY status, order requests, queue entries, runtime writes, or SendOrder
calls.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


PREVIEW_TYPE = "SELL_REAL_READY_AUTHORIZATION_PREVIEW"
INSPECTION_TYPE = "SELL_ORDER_CANDIDATE_INSPECTION"
STATUS_READY = "READY"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"
STATUS_NOT_APPLICABLE = "NOT_APPLICABLE"

ACTION_METHOD = "METHOD"
ACTION_COMPLETION = "COMPLETION"
ACTION_PENDING = "PENDING"
SUPPORTED_ACTION_SOURCES = {ACTION_METHOD, ACTION_COMPLETION, ACTION_PENDING}

SAFETY_EXPECTED = {
    "preview_only": True,
    "execution_connected": False,
    "runtime_write": False,
    "queue_write": False,
    "send_order": False,
    "order_request_created": False,
    "candidate_created": False,
}


def build_sell_real_ready_authorization_preview(
    inspection_result: Any,
    market_context: Any = None,
    runtime_context: Any = None,
) -> dict[str, Any]:
    """Build a read-only authorization preview from SELL candidate inspection."""

    result = _base_result(market_context, runtime_context)
    if not isinstance(inspection_result, dict):
        result["status"] = STATUS_INVALID
        result["reasons"].append("inspection_result must be a dict")
        return result

    result["inspection_snapshot"] = deepcopy(inspection_result)
    result["warnings"].extend(_string_list(inspection_result.get("warnings")))

    if inspection_result.get("inspection_type") != INSPECTION_TYPE:
        result["status"] = STATUS_INVALID
        result["reasons"].append("inspection_type must be SELL_ORDER_CANDIDATE_INSPECTION")
        return result

    inspected_candidates = inspection_result.get("inspected_candidates")
    if not isinstance(inspected_candidates, list):
        result["status"] = STATUS_INVALID
        result["reasons"].append("inspected_candidates must be a list")
        return result

    authorized_candidates = []
    for inspected in inspected_candidates:
        authorization = _authorize_candidate(inspected)
        authorized_candidates.append(authorization)
        result["warnings"].extend(authorization["warnings"])

    result["authorized_candidates"] = authorized_candidates
    result["authorization_summary"] = _authorization_summary(authorized_candidates)
    result["status"] = _overall_status(authorized_candidates)
    if not authorized_candidates:
        result["reasons"].append("no inspected candidates")
    if result["authorization_summary"]["authorized_count"] > 1:
        result["warnings"].append("multiple_authorized_candidates_priority_not_selected")
    return result


def _base_result(market_context: Any, runtime_context: Any) -> dict[str, Any]:
    return {
        "preview_type": PREVIEW_TYPE,
        "preview_only": True,
        "execution_connected": False,
        "runtime_write": False,
        "queue_write": False,
        "send_order": False,
        "status": STATUS_BLOCKED,
        "authorized_candidates": [],
        "authorization_summary": {
            "candidate_count": 0,
            "authorized_count": 0,
            "blocked_count": 0,
            "invalid_count": 0,
            "action_sources": [],
            "priority_selected": False,
            "real_ready_status_created": False,
            "order_request_created": False,
        },
        "inspection_snapshot": {},
        "market_context_snapshot": deepcopy(market_context) if isinstance(market_context, dict) else {},
        "runtime_context_snapshot": deepcopy(runtime_context) if isinstance(runtime_context, dict) else {},
        "warnings": [],
        "reasons": [],
    }


def _base_candidate(action_source: str | None) -> dict[str, Any]:
    return {
        "action_source": action_source,
        "status": STATUS_BLOCKED,
        "authorized": False,
        "real_ready_eligible": False,
        "normalized_candidate": {},
        "warnings": [],
        "reasons": [],
    }


def _authorize_candidate(inspected: Any) -> dict[str, Any]:
    if not isinstance(inspected, dict):
        candidate = _base_candidate(None)
        candidate["status"] = STATUS_INVALID
        candidate["reasons"].append("inspected candidate must be a dict")
        return candidate

    action_source = _text(inspected.get("action_source")) or None
    candidate = _base_candidate(action_source)
    candidate["warnings"].extend(_string_list(inspected.get("warnings")))
    candidate["real_ready_eligible"] = bool(inspected.get("real_ready_eligible"))
    candidate["normalized_candidate"] = deepcopy(_as_dict(inspected.get("normalized_candidate")))

    reasons = _string_list(inspected.get("reasons"))
    invalid_reasons: list[str] = []
    blocked_reasons: list[str] = []

    status = _text(inspected.get("status"))
    if status == STATUS_NOT_APPLICABLE:
        blocked_reasons.append("inspected candidate is NOT_APPLICABLE")
    elif status == STATUS_INVALID:
        invalid_reasons.append("inspected candidate status is INVALID")
    elif status == STATUS_BLOCKED:
        blocked_reasons.append("inspected candidate status is BLOCKED")
    elif status != STATUS_READY:
        blocked_reasons.append("inspected candidate status is not READY")

    if action_source not in SUPPORTED_ACTION_SOURCES:
        invalid_reasons.append(f"unsupported action_source: {action_source}")

    invalid_reasons.extend(_safety_violations(candidate["normalized_candidate"]))

    if status == STATUS_READY and not candidate["real_ready_eligible"]:
        blocked_reasons.append("real_ready_eligible is False")

    if invalid_reasons:
        candidate["status"] = STATUS_INVALID
        candidate["reasons"].extend(invalid_reasons)
        candidate["reasons"].extend(reasons)
        return candidate
    if blocked_reasons:
        candidate["status"] = STATUS_BLOCKED
        candidate["reasons"].extend(blocked_reasons)
        candidate["reasons"].extend(reasons)
        return candidate

    candidate["status"] = STATUS_READY
    candidate["authorized"] = True
    return candidate


def _authorization_summary(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    authorized_count = sum(1 for candidate in candidates if candidate["authorized"])
    blocked_count = sum(1 for candidate in candidates if candidate["status"] == STATUS_BLOCKED)
    invalid_count = sum(1 for candidate in candidates if candidate["status"] == STATUS_INVALID)
    return {
        "candidate_count": len(candidates),
        "authorized_count": authorized_count,
        "blocked_count": blocked_count,
        "invalid_count": invalid_count,
        "action_sources": [candidate.get("action_source") for candidate in candidates],
        "priority_selected": False,
        "real_ready_status_created": False,
        "order_request_created": False,
    }


def _overall_status(candidates: list[dict[str, Any]]) -> str:
    if any(candidate["status"] == STATUS_INVALID for candidate in candidates):
        return STATUS_INVALID
    if any(candidate["authorized"] for candidate in candidates):
        return STATUS_READY
    return STATUS_BLOCKED


def _safety_violations(candidate: dict[str, Any]) -> list[str]:
    reasons = []
    for key, expected in SAFETY_EXPECTED.items():
        if candidate.get(key) is not expected:
            reasons.append(f"{key} must be {expected}")
    return reasons


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item is not None]


def _text(value: Any) -> str:
    return str(value or "").strip()
