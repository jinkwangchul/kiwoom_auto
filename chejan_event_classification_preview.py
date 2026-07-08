# -*- coding: utf-8 -*-
"""Preview-only classification of a raw Chejan event contract."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


STATUS_READY = "CLASSIFICATION_READY"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"

ORDER_RECEIVED = "ORDER_RECEIVED_CANDIDATE"
ORDER_REJECTED = "ORDER_REJECTED_CANDIDATE"
ORDER_CANCELLED = "ORDER_CANCELLED_CANDIDATE"
PARTIAL_FILL = "PARTIAL_FILL_CANDIDATE"
FULL_FILL = "FULL_FILL_CANDIDATE"
UNKNOWN = "UNKNOWN_CANDIDATE"

REJECT_TOKENS = ("REJECT", "ERROR", "FAILED", "REJECTED")
CANCEL_TOKENS = ("CANCEL", "CANCELED", "CANCELLED")
RECEIVED_TOKENS = ("RECEIVED", "ACCEPT", "ACCEPTED", "CONFIRM", "OPEN")
FILL_TOKENS = ("FILL", "FILLED")


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _upper(value: Any) -> str:
    return _text(value).upper()


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    text = _text(value)
    if text in {"", "-", "--"}:
        return None
    try:
        return int(float(text.replace(",", "")))
    except (TypeError, ValueError):
        return None


def _fid(raw_event: dict[str, Any], key: str) -> Any:
    return _as_dict(raw_event.get("fid_values")).get(key)


def _contains_any(value: Any, tokens: tuple[str, ...]) -> bool:
    upper = _upper(value)
    return any(token in upper for token in tokens)


def _result(
    *,
    status: str,
    classification_preview: dict[str, Any] | None = None,
    candidate_event_type: str = "",
    confidence: str = "",
    issues: list[str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "classification_preview": deepcopy(classification_preview) if isinstance(classification_preview, dict) else {},
        "candidate_event_type": candidate_event_type,
        "confidence": confidence,
        "issues": list(issues or []),
        "warnings": list(warnings or []),
        "lifecycle_created": False,
        "runtime_write": False,
        "queue_write": False,
    }


def _policy_valid(policy: Any) -> tuple[dict[str, Any], dict[str, Any] | None]:
    policy_dict = _as_dict(policy)
    if not policy_dict:
        return policy_dict, _result(status=STATUS_INVALID, issues=["classification_policy must be a non-empty dict"])
    if policy_dict.get("classification_enabled") is not True:
        return policy_dict, _result(status=STATUS_BLOCKED, issues=["classification_policy.classification_enabled is not true"])
    if "confidence_mode" in policy_dict and _text(policy_dict.get("confidence_mode")).upper() not in {"STRICT", "STANDARD", "LENIENT"}:
        return policy_dict, _result(status=STATUS_INVALID, issues=["classification_policy.confidence_mode is invalid"])
    return policy_dict, None


def _candidate_from_raw(raw_event: dict[str, Any]) -> tuple[str, str, list[str]]:
    warnings: list[str] = []
    explicit_type = _upper(raw_event.get("event_type"))
    order_status = _text(raw_event.get("order_status") or _fid(raw_event, "913"))
    filled_quantity = _int_or_none(raw_event.get("filled_quantity") or _fid(raw_event, "911"))
    remaining_quantity = _int_or_none(raw_event.get("remaining_quantity") or _fid(raw_event, "902"))

    signal = " ".join(part for part in (explicit_type, _upper(order_status)) if part)

    if explicit_type in {"ORDER_REJECTED", "ORDER_REJECTED_CANDIDATE"} or _contains_any(signal, REJECT_TOKENS):
        return ORDER_REJECTED, "HIGH", warnings
    if explicit_type in {"ORDER_CANCELED", "ORDER_CANCELLED", "ORDER_CANCELED_CANDIDATE", "ORDER_CANCELLED_CANDIDATE"} or _contains_any(signal, CANCEL_TOKENS):
        return ORDER_CANCELLED, "HIGH", warnings
    if explicit_type in {"PARTIAL_FILL", "PARTIAL_FILL_CANDIDATE"}:
        return PARTIAL_FILL, "HIGH", warnings
    if explicit_type in {"FULL_FILL", "FULL_FILL_CANDIDATE"}:
        return FULL_FILL, "HIGH", warnings
    if explicit_type in {"ORDER_UNKNOWN", "UNKNOWN_CANDIDATE"}:
        return UNKNOWN, "LOW", warnings

    filled = filled_quantity if filled_quantity is not None else 0
    remaining = remaining_quantity if remaining_quantity is not None else 0
    if filled > 0 and remaining > 0:
        return PARTIAL_FILL, "HIGH", warnings
    if filled > 0 and remaining == 0:
        return FULL_FILL, "HIGH", warnings

    if explicit_type in {"ORDER_OPEN", "ORDER_ACCEPTED", "ORDER_RECEIVED", "ORDER_RECEIVED_CANDIDATE"}:
        return ORDER_RECEIVED, "HIGH", warnings
    if _contains_any(signal, RECEIVED_TOKENS) and filled == 0:
        return ORDER_RECEIVED, "MEDIUM", warnings
    if _contains_any(signal, FILL_TOKENS):
        warnings.append("fill-like status without filled quantity remains unknown candidate")
        return UNKNOWN, "LOW", warnings

    return UNKNOWN, "LOW", warnings


def preview_chejan_event_classification(
    chejan_event_contract_result: Any,
    classification_policy: Any,
) -> dict[str, Any]:
    """Classify a Chejan event as a candidate only; never create lifecycle state."""
    event_result = _as_dict(chejan_event_contract_result)
    if not event_result:
        return _result(status=STATUS_INVALID, issues=["chejan_event_contract_result must be a dict"])

    policy, policy_blocked = _policy_valid(classification_policy)
    event_status = _text(event_result.get("status")).upper()
    warnings = list(event_result.get("warnings") or [])

    if event_status == "BLOCKED":
        return _result(
            status=STATUS_BLOCKED,
            issues=["chejan_event_contract_result.status is BLOCKED"] + list(event_result.get("issues") or []),
            warnings=warnings,
        )
    if event_status == "INVALID":
        return _result(
            status=STATUS_INVALID,
            issues=["chejan_event_contract_result.status is INVALID"] + list(event_result.get("issues") or []),
            warnings=warnings,
        )
    if event_status != "CHEJAN_EVENT_READY":
        return _result(status=STATUS_INVALID, issues=["chejan_event_contract_result.status is not supported"], warnings=warnings)

    if policy_blocked is not None:
        return policy_blocked

    contract = _as_dict(event_result.get("chejan_event_contract"))
    if not contract:
        return _result(status=STATUS_INVALID, issues=["chejan_event_contract is required"], warnings=warnings)
    raw_event = _as_dict(contract.get("raw_chejan_event"))
    if not raw_event:
        return _result(status=STATUS_INVALID, issues=["chejan_event_contract.raw_chejan_event is required"], warnings=warnings)

    candidate, confidence, candidate_warnings = _candidate_from_raw(raw_event)
    if not candidate:
        return _result(status=STATUS_INVALID, issues=["candidate_event_type could not be derived"], warnings=warnings)

    preview = {
        "preview_type": "CHEJAN_EVENT_CLASSIFICATION_PREVIEW",
        "candidate_event_type": candidate,
        "confidence": confidence,
        "classification_policy": deepcopy(policy),
        "chejan_event_contract": deepcopy(contract),
        "identity": deepcopy(_as_dict(contract.get("identity"))),
        "source_event_type": contract.get("event_type"),
        "raw_order_status": raw_event.get("order_status") or _fid(raw_event, "913"),
        "raw_filled_quantity": raw_event.get("filled_quantity") or _fid(raw_event, "911"),
        "raw_remaining_quantity": raw_event.get("remaining_quantity") or _fid(raw_event, "902"),
        "lifecycle_created": False,
        "runtime_write": False,
        "queue_write": False,
        "final_state_confirmed": False,
    }
    return _result(
        status=STATUS_READY,
        classification_preview=preview,
        candidate_event_type=candidate,
        confidence=confidence,
        issues=[],
        warnings=warnings + candidate_warnings,
    )
