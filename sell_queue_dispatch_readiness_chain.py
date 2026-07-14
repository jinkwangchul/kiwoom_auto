# -*- coding: utf-8 -*-
"""SELL queue-committed dispatch readiness previews.

This module starts after SELL runtime commit post-commit verification. It keeps
the chain preview-only: no queue state transition, SendOrder, broker API call,
or OrderRequest object is created.
"""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any


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
    """Review committed ORDER_QUEUED records for SELL dispatch readiness."""
    result = _base_result(QUEUE_REVIEW_TYPE, "review_type", post_commit_verifier)
    result.update(
        {
            "queue_committed_review_ready": False,
            "queue_path": None,
            "reviewed_records": [],
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
    for item in verified_records:
        if not isinstance(item, dict):
            return _finish(result, INVALID, "verified_records item must be a dict")
        record = item.get("record")
        if not isinstance(record, dict) or not record:
            return _finish(result, INVALID, "verified_records item record is required")
        queue_path = _text(item.get("order_queue_path"))
        if not queue_path:
            return _finish(result, INVALID, "verified_records item order_queue_path is required")
        queue_paths.append(queue_path)
        records.append(deepcopy(record))

    if len(set(queue_paths)) != 1:
        return _finish(result, INVALID, "all reviewed records must use the same queue path")
    result["queue_path"] = queue_paths[0]

    queue_data, queue_error = _read_queue(result["queue_path"])
    if queue_error:
        return _finish(result, INVALID, queue_error)
    orders = queue_data.get("orders")
    if not isinstance(orders, list):
        return _finish(result, INVALID, "queue orders must be a list")

    duplicate_error = _duplicate_identity_error(records)
    if duplicate_error:
        return _finish(result, INVALID, duplicate_error)

    for record in records:
        error = _validate_order_queued_record(record)
        if error:
            return _finish(result, BLOCKED, error)
        matches = [_as_dict(order) for order in orders if _identity_matches(_as_dict(order), record)]
        if len(matches) != 1:
            return _finish(result, INVALID, f"queue matching record count is {len(matches)} for {record.get('order_id')}")
        if matches[0] != record:
            return _finish(result, INVALID, f"queue record snapshot mismatch for {record.get('order_id')}")

    result["status"] = READY
    result["queue_committed_review_ready"] = True
    result["reviewed_records"] = deepcopy(records)
    result["candidate_ids"] = [_text(record.get("candidate_id")) for record in records]
    result["summary"] = {
        "reviewed_count": len(records),
        "blocked_count": 0,
        "invalid_count": 0,
        "queue_path": result["queue_path"],
    }
    return _finalize(result)


def build_sell_dispatch_eligibility(queue_review: Any, dispatch_context: Any = None) -> dict[str, Any]:
    """Validate reviewed SELL queue records before broker request preview."""
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

    records = queue_review.get("reviewed_records")
    if not isinstance(records, list) or not records:
        return _finish(result, INVALID, "reviewed_records must be a non-empty list")

    context_block = _dispatch_context_block(context)
    if context_block:
        result["blocked_candidates"] = [{"record": deepcopy(record), "reason": context_block} for record in records if isinstance(record, dict)]
        return _finish(result, BLOCKED, context_block)

    eligible: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict):
            return _finish(result, INVALID, "reviewed record must be a dict")
        candidate = _candidate_from_record(record)
        validation_error = _validate_dispatch_candidate(candidate, context)
        if validation_error:
            blocked.append({"candidate": candidate, "reason": validation_error})
        else:
            eligible.append(candidate)

    result["queue_path"] = queue_review.get("queue_path")
    result["candidate_ids"] = [_text(candidate.get("candidate_id")) for candidate in eligible + [item["candidate"] for item in blocked]]
    result["eligible_candidates"] = deepcopy(eligible)
    result["blocked_candidates"] = deepcopy(blocked)
    result["summary"] = {
        "candidate_count": len(records),
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
    """Create Kiwoom SendOrder argument previews without creating OrderRequest."""
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

    account_no = _text(context.get("account_no"))
    if not account_no:
        return _finish(result, INVALID, "broker_context.account_no is required")
    screen_no = _text(context.get("screen_no") or "0000")
    candidates = dispatch_eligibility.get("eligible_candidates")
    if not isinstance(candidates, list) or not candidates:
        return _finish(result, INVALID, "eligible_candidates must be a non-empty list")

    previews: list[dict[str, Any]] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            return _finish(result, INVALID, "eligible candidate must be a dict")
        preview = _broker_payload(candidate, account_no, screen_no)
        error = _validate_broker_payload(preview)
        if error:
            return _finish(result, INVALID, error)
        previews.append(preview)

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
    """Approve SELL dispatch preview boundaries without executing dispatch."""
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

    previews = broker_request_preview.get("broker_request_previews")
    if not isinstance(previews, list) or not previews:
        return _finish(result, INVALID, "broker_request_previews must be a non-empty list")
    expected_candidate_ids = [_text(preview.get("candidate_id")) for preview in previews if isinstance(preview, dict)]
    if context.get("approved_candidate_ids") != expected_candidate_ids:
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
    orders = queue_data.get("orders")
    if not isinstance(orders, list):
        return _finish(result, INVALID, "queue orders must be a list")

    for preview in previews:
        if not isinstance(preview, dict):
            return _finish(result, INVALID, "broker request preview item must be a dict")
        matches = [_as_dict(order) for order in orders if _identity_matches(_as_dict(order), preview)]
        if len(matches) != 1:
            return _finish(result, INVALID, f"approval queue matching record count is {len(matches)} for {preview.get('order_id')}")
        record_error = _validate_order_queued_record(matches[0])
        if record_error:
            return _finish(result, BLOCKED, record_error)

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


def _validate_order_queued_record(record: dict[str, Any]) -> str | None:
    if record.get("status") != "ORDER_QUEUED":
        return "ORDER_QUEUED record status must be ORDER_QUEUED"
    if record.get("execution_enabled") is not False:
        return "ORDER_QUEUED execution_enabled must be False"
    if record.get("send_order_called") is not False:
        return "ORDER_QUEUED send_order_called must be False"
    for field in IDENTITY_FIELDS:
        if not _text(record.get(field)):
            return f"ORDER_QUEUED {field} is required"
    execution_request = record.get("execution_request")
    if not isinstance(execution_request, dict) or not execution_request:
        return "ORDER_QUEUED execution_request is required"
    return None


def _candidate_from_record(record: dict[str, Any]) -> dict[str, Any]:
    request = _as_dict(record.get("execution_request"))
    return {
        "record": deepcopy(record),
        "source_signal_id": _text(record.get("source_signal_id")),
        "order_id": _text(record.get("order_id")),
        "candidate_id": _text(record.get("candidate_id")),
        "queue_pending_id": _text(record.get("queue_pending_id")),
        "execution_id": _text(record.get("execution_id")),
        "request_hash": _text(record.get("request_hash")),
        "lock_id": _text(record.get("lock_id")),
        "symbol": _text(request.get("symbol") or request.get("code") or record.get("symbol") or record.get("code")),
        "code": _text(request.get("code") or request.get("symbol") or record.get("code") or record.get("symbol")),
        "side": _text(request.get("side") or record.get("side")).upper(),
        "quantity": deepcopy(request.get("quantity", record.get("quantity"))),
        "price": deepcopy(request.get("price", record.get("price"))),
        "order_type": _text(request.get("order_type") or record.get("order_type")).upper(),
        "hoga": _text(request.get("hoga") or record.get("hoga")).upper(),
        "original_order_no": _text(request.get("original_order_no") or record.get("original_order_no") or "0"),
        "execution_request": deepcopy(request),
    }


def _validate_dispatch_candidate(candidate: dict[str, Any], context: dict[str, Any]) -> str | None:
    if candidate.get("side") != "SELL":
        return "candidate side must be SELL"
    if candidate.get("order_type") != "SELL":
        return "candidate order_type must be SELL"
    if not _text(candidate.get("symbol")) or not _text(candidate.get("code")):
        return "candidate symbol/code is required"
    if not _positive_number(candidate.get("quantity")):
        return "candidate quantity must be greater than 0"
    if not _text(candidate.get("hoga")):
        return "candidate hoga is required"
    if candidate.get("price") in (None, ""):
        return "candidate price is required"
    if not _number(candidate.get("price")):
        return "candidate price must be numeric"
    holding_qty = _holding_qty(context, _text(candidate.get("symbol")), _text(candidate.get("code")))
    if holding_qty is not None and _to_float(candidate.get("quantity")) > holding_qty:
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


def _broker_payload(candidate: dict[str, Any], account_no: str, screen_no: str) -> dict[str, Any]:
    return {
        "screen_no": screen_no,
        "account_no": account_no,
        "order_type": "SELL",
        "code": _text(candidate.get("code")),
        "quantity": deepcopy(candidate.get("quantity")),
        "price": deepcopy(candidate.get("price")),
        "hoga": _text(candidate.get("hoga")),
        "original_order_no": _text(candidate.get("original_order_no") or "0"),
        "source_signal_id": _text(candidate.get("source_signal_id")),
        "order_id": _text(candidate.get("order_id")),
        "candidate_id": _text(candidate.get("candidate_id")),
        "queue_pending_id": _text(candidate.get("queue_pending_id")),
        "execution_id": _text(candidate.get("execution_id")),
        "request_hash": _text(candidate.get("request_hash")),
        "lock_id": _text(candidate.get("lock_id")),
        "execution_request": deepcopy(candidate.get("execution_request")),
    }


def _validate_broker_payload(payload: dict[str, Any]) -> str | None:
    for field in (
        "screen_no",
        "account_no",
        "order_type",
        "code",
        "quantity",
        "price",
        "hoga",
        "original_order_no",
        "source_signal_id",
        "order_id",
        "candidate_id",
        "queue_pending_id",
        "execution_id",
        "request_hash",
        "lock_id",
    ):
        if payload.get(field) in (None, ""):
            return f"broker request preview {field} is required"
    if payload.get("order_type") != "SELL":
        return "broker request preview order_type must be SELL"
    return None


def _duplicate_identity_error(records: list[dict[str, Any]]) -> str | None:
    for field in IDENTITY_FIELDS:
        values = [_text(record.get(field)) for record in records]
        if len(values) != len(set(values)):
            return f"duplicate {field}"
    return None


def _identity_matches(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return all(_text(left.get(field)) == _text(right.get(field)) for field in IDENTITY_FIELDS)


def _has_safety_violation(payload: dict[str, Any]) -> bool:
    for field in SAFETY_FALSE_FIELDS:
        if payload.get(field) is True:
            return True
    return False


def _holding_qty(context: dict[str, Any], symbol: str, code: str) -> float | None:
    holdings = context.get("holdings")
    if isinstance(holdings, dict):
        for key in (symbol, code):
            if key and key in holdings:
                return _to_float(holdings[key])
    if "holding_qty" in context:
        return _to_float(context.get("holding_qty"))
    return None


def _positive_number(value: Any) -> bool:
    return _number(value) and _to_float(value) > 0


def _number(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    try:
        float(value)
    except (TypeError, ValueError):
        return False
    return True


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
