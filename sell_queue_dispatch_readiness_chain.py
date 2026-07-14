# -*- coding: utf-8 -*-
"""SELL orchestration over existing queue review and dispatch preview layers."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

from broker_dispatch_preview import preview_broker_dispatch
from execution_order_dispatch_builder import build_order_dispatch_contract
from execution_queue_committed_review import review_execution_queue_committed
from execution_queue_review_to_send_order_preview_adapter import adapt_queue_review_to_send_order_preview


READY = "READY"
BLOCKED = "BLOCKED"
INVALID = "INVALID"

QUEUE_REVIEW_TYPE = "SELL_QUEUE_COMMITTED_REVIEW"
DISPATCH_ELIGIBILITY_TYPE = "SELL_DISPATCH_ELIGIBILITY"
BROKER_REQUEST_PREVIEW_TYPE = "SELL_BROKER_REQUEST_PREVIEW"
DISPATCH_APPROVAL_TYPE = "SELL_DISPATCH_APPROVAL_GATE"
POST_COMMIT_VERIFIER_TYPE = "SELL_RUNTIME_COMMIT_POST_COMMIT_VERIFIER"

IDENTITY_FIELDS = (
    "order_id",
    "candidate_id",
    "queue_pending_id",
    "execution_id",
    "request_hash",
    "lock_id",
)
SAFETY_FALSE_FIELDS = (
    "send_order_called",
    "broker_api_called",
    "actual_order_sent",
    "order_request_created",
    "queue_write",
    "runtime_write",
    "real_ready_state_changed",
)


def build_sell_queue_committed_review(post_commit_verifier: Any) -> dict[str, Any]:
    """Call the existing ORDER_QUEUED review once per verified SELL record."""
    result = _base_result(QUEUE_REVIEW_TYPE, "review_type", post_commit_verifier)
    result.update(
        {
            "queue_committed_review_ready": False,
            "queue_path": None,
            "candidate_reviews": [],
            "candidate_ids": [],
            "source_verifier_snapshot": deepcopy(post_commit_verifier) if isinstance(post_commit_verifier, dict) else None,
        }
    )

    if not isinstance(post_commit_verifier, dict):
        return _finish(result, INVALID, "post_commit_verifier must be a dict")
    if post_commit_verifier.get("verifier_type") != POST_COMMIT_VERIFIER_TYPE:
        return _finish(result, INVALID, "verifier_type is not SELL_RUNTIME_COMMIT_POST_COMMIT_VERIFIER")
    if _has_safety_violation(post_commit_verifier):
        return _finish(result, INVALID, "post_commit_verifier safety flag violation")

    upstream_status = _status(post_commit_verifier.get("status"))
    if upstream_status == INVALID:
        return _finish(result, INVALID, "post_commit_verifier status is INVALID")
    if upstream_status != READY:
        return _finish(result, BLOCKED, "post_commit_verifier is not READY")
    if post_commit_verifier.get("post_commit_verified") is not True:
        return _finish(result, INVALID, "post_commit_verified must be True")
    if post_commit_verifier.get("post_commit_file_verified") is not True:
        return _finish(result, INVALID, "post_commit_file_verified must be True")

    verified_records = post_commit_verifier.get("verified_records")
    if not isinstance(verified_records, list) or not verified_records:
        return _finish(result, INVALID, "verified_records must be a non-empty list")

    queue_paths: list[str] = []
    records: list[dict[str, Any]] = []
    candidate_reviews: list[dict[str, Any]] = []
    for item in verified_records:
        if not isinstance(item, dict):
            return _finish(result, INVALID, "verified_records item must be a dict")
        record = _as_dict(item.get("record"))
        queue_path = _text(item.get("order_queue_path"))
        if not record or not queue_path:
            return _finish(result, INVALID, "verified record and order_queue_path are required")
        queue_review = review_execution_queue_committed(_queue_commit_result(record))
        candidate_reviews.append(
            {
                "record": deepcopy(record),
                "queue_path": queue_path,
                "queue_review": queue_review,
            }
        )
        queue_paths.append(queue_path)
        records.append(record)

    if len(set(queue_paths)) != 1:
        return _finish(result, INVALID, "all candidates must use the same queue_path")
    duplicate_error = _duplicate_identity_error(records)
    if duplicate_error:
        return _finish(result, INVALID, duplicate_error)
    queue_data, queue_error = _read_queue(queue_paths[0])
    if queue_error:
        return _finish(result, INVALID, queue_error)
    queue_orders = _as_list(queue_data.get("orders"))
    for record in records:
        matches = [_as_dict(order) for order in queue_orders if _identity_matches(_as_dict(order), record)]
        if len(matches) != 1:
            return _finish(result, INVALID, f"queue matching record count is {len(matches)} for {record.get('order_id')}")
        if matches[0] != record:
            return _finish(result, INVALID, f"queue record snapshot mismatch for {record.get('order_id')}")

    blocked = [item for item in candidate_reviews if _status(_as_dict(item.get("queue_review")).get("status")) == "BLOCKED"]
    invalid = [item for item in candidate_reviews if _status(_as_dict(item.get("queue_review")).get("status")) == "INVALID"]
    other = [
        item
        for item in candidate_reviews
        if _status(_as_dict(item.get("queue_review")).get("status")) not in {"READY_FOR_FINAL_SEND_GATE", "BLOCKED", "INVALID"}
    ]
    result["queue_path"] = queue_paths[0]
    result["candidate_reviews"] = deepcopy(candidate_reviews)
    result["candidate_ids"] = [_text(record.get("candidate_id")) for record in records]
    result["summary"] = {
        "candidate_count": len(candidate_reviews),
        "ready_count": len(candidate_reviews) - len(blocked) - len(invalid) - len(other),
        "blocked_count": len(blocked),
        "invalid_count": len(invalid) + len(other),
        "queue_path": queue_paths[0],
    }
    if invalid or other:
        return _finish(result, INVALID, "one or more existing queue reviews are INVALID")
    if blocked:
        return _finish(result, BLOCKED, "one or more existing queue reviews are BLOCKED")

    result["status"] = READY
    result["queue_committed_review_ready"] = True
    return _finalize(result)


def build_sell_dispatch_eligibility(queue_review: Any, dispatch_context: Any = None) -> dict[str, Any]:
    """Use the existing queue-review-to-SendOrder adapter, then add SELL policy."""
    result = _base_result(DISPATCH_ELIGIBILITY_TYPE, "eligibility_type", queue_review)
    result.update(
        {
            "dispatch_eligible": False,
            "queue_path": None,
            "eligible_candidates": [],
            "blocked_candidates": [],
            "candidate_ids": [],
            "source_review_snapshot": deepcopy(queue_review) if isinstance(queue_review, dict) else None,
        }
    )
    context = _as_dict(dispatch_context)

    if not isinstance(queue_review, dict):
        return _finish(result, INVALID, "queue_review must be a dict")
    if queue_review.get("review_type") != QUEUE_REVIEW_TYPE:
        return _finish(result, INVALID, "review_type is not SELL_QUEUE_COMMITTED_REVIEW")
    if _has_safety_violation(queue_review):
        return _finish(result, INVALID, "queue_review safety flag violation")

    review_status = _status(queue_review.get("status"))
    if review_status == INVALID:
        return _finish(result, INVALID, "queue_review status is INVALID")
    if review_status != READY:
        return _finish(result, BLOCKED, "queue_review is not READY")
    if queue_review.get("queue_committed_review_ready") is not True:
        return _finish(result, INVALID, "queue_committed_review_ready must be True")

    context_block = _dispatch_context_block(context)
    if context_block:
        return _finish(result, BLOCKED, context_block)

    candidates = queue_review.get("candidate_reviews")
    if not isinstance(candidates, list) or not candidates:
        return _finish(result, INVALID, "candidate_reviews must be a non-empty list")

    eligible: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for item in candidates:
        if not isinstance(item, dict):
            return _finish(result, INVALID, "candidate review item must be a dict")
        adapter_result = adapt_queue_review_to_send_order_preview(item.get("queue_review"), context=context)
        request_preview = _as_dict(_as_dict(adapter_result.get("adapter_preview_result")).get("send_order_request_preview"))
        candidate = {
            "queue_path": item.get("queue_path"),
            "record": deepcopy(item.get("record")),
            "queue_review": deepcopy(item.get("queue_review")),
            "adapter_result": deepcopy(adapter_result),
            "send_order_request_preview": deepcopy(request_preview),
            "candidate_id": _text(_as_dict(item.get("record")).get("candidate_id")),
        }
        reason = _eligibility_block_reason(adapter_result, request_preview, context)
        if reason:
            blocked.append({"candidate": candidate, "reason": reason})
        else:
            eligible.append(candidate)

    result["queue_path"] = queue_review.get("queue_path")
    result["eligible_candidates"] = deepcopy(eligible)
    result["blocked_candidates"] = deepcopy(blocked)
    result["candidate_ids"] = [_text(item.get("candidate_id")) for item in eligible] + [
        _text(item["candidate"].get("candidate_id")) for item in blocked
    ]
    result["summary"] = {
        "candidate_count": len(candidates),
        "eligible_count": len(eligible),
        "blocked_count": len(blocked),
        "invalid_count": 0,
    }
    if blocked:
        return _finish(result, BLOCKED, "one or more SELL dispatch candidates are blocked")

    result["status"] = READY
    result["dispatch_eligible"] = True
    return _finalize(result)


def build_sell_broker_request_preview(dispatch_eligibility: Any, broker_context: Any = None) -> dict[str, Any]:
    """Compose existing dispatch builder and broker preview per candidate."""
    result = _base_result(BROKER_REQUEST_PREVIEW_TYPE, "preview_type", dispatch_eligibility)
    result.update(
        {
            "broker_request_preview_ready": False,
            "queue_path": None,
            "broker_request_previews": [],
            "candidate_ids": [],
            "source_eligibility_snapshot": deepcopy(dispatch_eligibility) if isinstance(dispatch_eligibility, dict) else None,
        }
    )
    context = _as_dict(broker_context)

    if not isinstance(dispatch_eligibility, dict):
        return _finish(result, INVALID, "dispatch_eligibility must be a dict")
    if dispatch_eligibility.get("eligibility_type") != DISPATCH_ELIGIBILITY_TYPE:
        return _finish(result, INVALID, "eligibility_type is not SELL_DISPATCH_ELIGIBILITY")
    if _has_safety_violation(dispatch_eligibility):
        return _finish(result, INVALID, "dispatch_eligibility safety flag violation")

    eligibility_status = _status(dispatch_eligibility.get("status"))
    if eligibility_status == INVALID:
        return _finish(result, INVALID, "dispatch_eligibility status is INVALID")
    if eligibility_status != READY:
        return _finish(result, BLOCKED, "dispatch_eligibility is not READY")
    if dispatch_eligibility.get("dispatch_eligible") is not True:
        return _finish(result, INVALID, "dispatch_eligible must be True")

    account_context = {"account_no": _text(context.get("account_no"))}
    broker_profile = {"broker_type": _text(context.get("broker_type") or "KIWOOM"), "default_hoga": "LIMIT"}
    broker_capabilities = _as_dict(context.get("broker_capabilities")) or {
        "supported_brokers": [broker_profile["broker_type"]],
        "supported_sides": ["SELL"],
        "supported_hogas": ["LIMIT", "MARKET"],
    }
    market_context = _as_dict(context.get("market_context")) or {"market_open": True}
    if not account_context["account_no"]:
        return _finish(result, INVALID, "broker_context.account_no is required")

    previews: list[dict[str, Any]] = []
    for candidate in _as_list(dispatch_eligibility.get("eligible_candidates")):
        request_preview = _as_dict(candidate.get("send_order_request_preview"))
        builder_result = build_order_dispatch_contract(
            _builder_review_result(request_preview),
            account_context,
            broker_profile,
        )
        broker_result = preview_broker_dispatch(builder_result, broker_capabilities, market_context)
        if _status(builder_result.get("status")) == INVALID or _status(broker_result.get("status")) == INVALID:
            return _finish(result, INVALID, "existing broker request preview returned INVALID")
        if _status(builder_result.get("status")) != "DISPATCH_READY" or _status(broker_result.get("status")) != "BROKER_DISPATCH_READY":
            return _finish(result, BLOCKED, "existing broker request preview returned BLOCKED")
        params = _as_dict(broker_result.get("send_order_params_preview"))
        previews.append(
            {
                "screen_no": _text(request_preview.get("screen_no") or "9000"),
                "account_no": _text(params.get("account_no")),
                "order_type": "SELL",
                "code": _text(params.get("code")),
                "quantity": deepcopy(params.get("quantity")),
                "price": deepcopy(params.get("price")),
                "hoga": _text(params.get("hoga")),
                "original_order_no": _text(request_preview.get("original_order_no")),
                "source_signal_id": _text(request_preview.get("source_signal_id")),
                "order_id": _text(request_preview.get("order_id")),
                "candidate_id": _text(candidate.get("candidate_id")),
                "queue_pending_id": _text(_as_dict(candidate.get("record")).get("queue_pending_id")),
                "execution_id": _text(request_preview.get("execution_id")),
                "request_hash": _text(request_preview.get("request_hash")),
                "lock_id": _text(request_preview.get("lock_id")),
                "adapter_result": deepcopy(candidate.get("adapter_result")),
                "dispatch_builder_result": deepcopy(builder_result),
                "broker_dispatch_result": deepcopy(broker_result),
            }
        )

    if not previews:
        return _finish(result, INVALID, "broker_request_previews must be non-empty")

    result["status"] = READY
    result["broker_request_preview_ready"] = True
    result["queue_path"] = dispatch_eligibility.get("queue_path")
    result["broker_request_previews"] = deepcopy(previews)
    result["candidate_ids"] = [_text(preview.get("candidate_id")) for preview in previews]
    result["summary"] = {
        "broker_request_count": len(previews),
        "blocked_count": 0,
        "invalid_count": 0,
    }
    return _finalize(result)


def build_sell_dispatch_approval_gate(broker_request_preview: Any, approval_context: Any = None) -> dict[str, Any]:
    """Approve every SELL broker request preview without dispatch execution."""
    result = _base_result(DISPATCH_APPROVAL_TYPE, "approval_type", broker_request_preview)
    result.update(
        {
            "approval_granted": False,
            "dispatch_execution_allowed": False,
            "queue_path": None,
            "approved_candidate_ids": [],
            "approved_broker_request_previews": [],
            "source_broker_request_snapshot": deepcopy(broker_request_preview) if isinstance(broker_request_preview, dict) else None,
        }
    )
    context = _as_dict(approval_context)

    if not isinstance(broker_request_preview, dict):
        return _finish(result, INVALID, "broker_request_preview must be a dict")
    if broker_request_preview.get("preview_type") != BROKER_REQUEST_PREVIEW_TYPE:
        return _finish(result, INVALID, "preview_type is not SELL_BROKER_REQUEST_PREVIEW")
    if _has_safety_violation(broker_request_preview):
        return _finish(result, INVALID, "broker_request_preview safety flag violation")

    preview_status = _status(broker_request_preview.get("status"))
    if preview_status == INVALID:
        return _finish(result, INVALID, "broker_request_preview status is INVALID")
    if preview_status != READY:
        return _finish(result, BLOCKED, "broker_request_preview is not READY")
    if broker_request_preview.get("broker_request_preview_ready") is not True:
        return _finish(result, INVALID, "broker_request_preview_ready must be True")
    if context.get("user_approved") is not True:
        return _finish(result, BLOCKED, "user approval is required")
    if not _text(context.get("approval_token")):
        return _finish(result, BLOCKED, "approval_token is required")

    previews = _as_list(broker_request_preview.get("broker_request_previews"))
    expected_candidate_ids = [_text(preview.get("candidate_id")) for preview in previews if isinstance(preview, dict)]
    if not expected_candidate_ids or context.get("approved_candidate_ids") != expected_candidate_ids:
        return _finish(result, INVALID, "approved_candidate_ids must match all candidates in order")

    account_no = _text(context.get("account_no"))
    if not account_no:
        return _finish(result, INVALID, "approval_context.account_no is required")
    if any(_text(preview.get("account_no")) != account_no for preview in previews if isinstance(preview, dict)):
        return _finish(result, INVALID, "approval account_no mismatch")

    queue_path = _text(context.get("queue_path"))
    if not queue_path or queue_path != _text(broker_request_preview.get("queue_path")):
        return _finish(result, INVALID, "approval queue_path mismatch")
    queue_data, queue_error = _read_queue(queue_path)
    if queue_error:
        return _finish(result, INVALID, queue_error)
    queue_orders = _as_list(queue_data.get("orders"))
    for preview in previews:
        matches = [_as_dict(order) for order in queue_orders if _identity_matches(_as_dict(order), preview)]
        if len(matches) != 1:
            return _finish(result, INVALID, f"approval queue matching record count is {len(matches)} for {preview.get('order_id')}")
        queue_review = review_execution_queue_committed(_queue_commit_result(matches[0]))
        if _status(queue_review.get("status")) != "READY_FOR_FINAL_SEND_GATE":
            return _finish(result, BLOCKED, "approved queue record is not ready for final send gate")

    result["status"] = READY
    result["approval_granted"] = True
    result["dispatch_execution_allowed"] = True
    result["queue_path"] = queue_path
    result["approved_candidate_ids"] = list(expected_candidate_ids)
    result["approved_broker_request_previews"] = deepcopy(previews)
    result["summary"] = {
        "approved_count": len(previews),
        "blocked_count": 0,
        "invalid_count": 0,
    }
    return _finalize(result)


def _queue_commit_result(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "COMMITTED",
        "manual_commit": True,
        "next_stage": "QUEUE_COMMITTED_REVIEW_REQUIRED",
        "commit_result": {
            "committed": True,
            "next_stage": "QUEUE_COMMITTED_REVIEW_REQUIRED",
            "order_queued_record": deepcopy(record),
            "send_order_called": False,
            "execution_enabled": False,
            "warnings": [],
            "blocked_reasons": [],
        },
        "warnings": [],
        "blocked_reasons": [],
    }


def _builder_review_result(request_preview: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "REVIEW_OK",
        "send_order_ready": True,
        "send_order_called": False,
        "warnings": [],
        "issues": [],
        "review": {
            "queue_item": {
                "order_id": _text(request_preview.get("order_id")),
                "source_signal_id": _text(request_preview.get("source_signal_id")),
                "code": _text(request_preview.get("code")),
                "side": _text(request_preview.get("side")),
                "quantity": deepcopy(request_preview.get("quantity")),
                "price": deepcopy(request_preview.get("price")),
                "hoga": _text(request_preview.get("hoga")),
                "request_hash": _text(request_preview.get("request_hash")),
            }
        },
    }


def _eligibility_block_reason(adapter_result: dict[str, Any], request_preview: dict[str, Any], context: dict[str, Any]) -> str | None:
    if _status(adapter_result.get("status")) == INVALID:
        return "existing SendOrder preview adapter returned INVALID"
    if _status(adapter_result.get("status")) != "READY_FOR_FINAL_SEND_GATE":
        return "existing SendOrder preview adapter returned BLOCKED"
    if _status(request_preview.get("side")) != "SELL":
        return "candidate side must be SELL"
    if not _positive_number(request_preview.get("quantity")):
        return "candidate quantity must be greater than 0"
    holding_qty = _holding_qty(context, _text(request_preview.get("code")))
    if holding_qty is not None and _to_float(request_preview.get("quantity")) > holding_qty:
        return "candidate quantity exceeds holding snapshot"
    return None


def _dispatch_context_block(context: dict[str, Any]) -> str | None:
    if context.get("emergency_stop") is True:
        return "emergency_stop is active"
    if context.get("trading_halted") is True:
        return "trading_halted is active"
    if context.get("market_open") is False or context.get("operating_time") is False:
        return "market is not open"
    if context.get("lock_state") in {"LOCKED", "BLOCKED"} or context.get("lock_available") is False:
        return "lock is not available"
    return None


def _duplicate_identity_error(records: list[dict[str, Any]]) -> str | None:
    for field in IDENTITY_FIELDS:
        values = [_text(record.get(field)) for record in records]
        if len(values) != len(set(values)):
            return f"duplicate {field}"
    return None


def _read_queue(path_text: str) -> tuple[dict[str, Any], str | None]:
    try:
        path = Path(path_text)
        if not path.exists():
            return {}, "queue_path does not exist"
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {}, f"queue_path is not readable JSON: {exc}"
    if not isinstance(data, dict):
        return {}, "queue JSON root must be an object"
    return data, None


def _base_result(result_type: str, type_field: str, source: Any) -> dict[str, Any]:
    result = {
        type_field: result_type,
        "ownership": "MASTER_ENGINE",
        "domain": "Execution / Queue Dispatch Readiness",
        "routine_dependency": None,
        "status": BLOCKED,
        "preview_only": True,
        "execution_connected": False,
        "runtime_write": False,
        "queue_write": False,
        "file_write": False,
        "send_order_called": False,
        "send_order": False,
        "broker_api_called": False,
        "actual_order_sent": False,
        "order_request_created": False,
        "real_ready_state_changed": False,
        "priority_selected": False,
        "auto_selected": False,
        "reasons": [],
        "warnings": [],
        "summary": {},
    }
    if isinstance(source, dict):
        result["warnings"].extend(_as_list(source.get("warnings")))
        result["reasons"].extend(_as_list(source.get("reasons")))
    return result


def _finish(result: dict[str, Any], status: str, reason: str) -> dict[str, Any]:
    result["status"] = status
    result["reasons"].append(reason)
    return _finalize(result)


def _finalize(result: dict[str, Any]) -> dict[str, Any]:
    for field in SAFETY_FALSE_FIELDS:
        result[field] = False
    result["send_order"] = False
    result["file_write"] = False
    result["execution_connected"] = False
    result["actual_order_sent"] = False
    result["order_request_created"] = False
    result["real_ready_state_changed"] = False
    result["priority_selected"] = False
    result["auto_selected"] = False
    return result


def _identity_matches(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return all(_text(left.get(field)) == _text(right.get(field)) for field in IDENTITY_FIELDS)


def _has_safety_violation(payload: dict[str, Any]) -> bool:
    return any(payload.get(field) is True for field in SAFETY_FALSE_FIELDS)


def _holding_qty(context: dict[str, Any], code: str) -> float | None:
    holdings = context.get("holdings")
    if isinstance(holdings, dict) and code in holdings:
        return _to_float(holdings[code])
    if "holding_qty" in context:
        return _to_float(context.get("holding_qty"))
    return None


def _positive_number(value: Any) -> bool:
    return not isinstance(value, bool) and _to_float(value) > 0


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _status(value: Any) -> str:
    return _text(value).upper()


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []
