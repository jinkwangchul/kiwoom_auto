# -*- coding: utf-8 -*-
"""SELL dispatch final guard, SendOrder call preview, and audit preview chain."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Any

from execution_queue_committed_review import review_execution_queue_committed
from final_execution_guard import evaluate_final_execution_guard
from kiwoom_send_order_adapter_contract import build_kiwoom_send_order_adapter_contract
from kiwoom_send_order_call_preview import preview_kiwoom_send_order_call


READY = "READY"
BLOCKED = "BLOCKED"
INVALID = "INVALID"

APPROVAL_TYPE = "SELL_DISPATCH_APPROVAL_GATE"
FINAL_GUARD_TYPE = "SELL_DISPATCH_FINAL_EXECUTION_GUARD"
CALL_PREVIEW_TYPE = "SELL_SEND_ORDER_CALL_PREVIEW"
AUDIT_PREVIEW_TYPE = "SELL_DISPATCH_EXECUTION_AUDIT_PREVIEW"

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
    "queue_status_changed",
    "real_ready_state_changed",
)


def build_sell_dispatch_final_execution_guard(approval_gate: Any, guard_context: Any = None) -> dict[str, Any]:
    """Recheck approved SELL dispatch candidates immediately before SendOrder preview."""
    result = _base_result(FINAL_GUARD_TYPE, "guard_type", approval_gate)
    result.update(
        {
            "final_guard_ready": False,
            "queue_path": None,
            "guarded_candidates": [],
            "blocked_candidates": [],
            "candidate_ids": [],
            "approval_token_present": False,
            "approval_token_hash": "",
            "source_approval_snapshot": _sanitize_token_snapshot(approval_gate) if isinstance(approval_gate, dict) else None,
        }
    )
    context = _as_dict(guard_context)

    if not isinstance(approval_gate, dict):
        return _finish(result, INVALID, "approval_gate must be a dict")
    if approval_gate.get("approval_type") != APPROVAL_TYPE:
        return _finish(result, INVALID, "approval_type is not SELL_DISPATCH_APPROVAL_GATE")
    if _has_safety_violation(approval_gate):
        return _finish(result, INVALID, "approval_gate safety flag violation")

    approval_status = _status(approval_gate.get("status"))
    if approval_status == INVALID:
        return _finish(result, INVALID, "approval_gate status is INVALID")
    if approval_status != READY:
        return _finish(result, BLOCKED, "approval_gate is not READY")
    if approval_gate.get("approval_granted") is not True:
        return _finish(result, BLOCKED, "approval_granted must be True")
    if approval_gate.get("dispatch_execution_allowed") is not True:
        return _finish(result, BLOCKED, "dispatch_execution_allowed must be True")

    approval_token = _text(approval_gate.get("approval_token"))
    context_token = _text(context.get("approval_token"))
    if not approval_token:
        return _finish(result, BLOCKED, "approval_token is required")
    if context_token and context_token != approval_token:
        return _finish(result, INVALID, "guard_context approval_token mismatch")

    previews = approval_gate.get("approved_broker_request_previews")
    if not isinstance(previews, list) or not previews:
        return _finish(result, INVALID, "approved_broker_request_previews must be a non-empty list")
    expected_ids = [_text(preview.get("candidate_id")) for preview in previews if isinstance(preview, dict)]
    if approval_gate.get("approved_candidate_ids") != expected_ids:
        return _finish(result, INVALID, "approved candidate order does not match previews")

    queue_path = _text(context.get("queue_path") or approval_gate.get("queue_path"))
    if not queue_path or queue_path != _text(approval_gate.get("queue_path")):
        return _finish(result, INVALID, "queue_path mismatch")
    account_no = _text(context.get("account_no") or _first_preview_value(previews, "account_no"))
    if not account_no:
        return _finish(result, INVALID, "account_no is required")
    if any(_text(preview.get("account_no")) != account_no for preview in previews if isinstance(preview, dict)):
        return _finish(result, INVALID, "account_no mismatch")

    context_block = _guard_context_block(context)
    if context_block:
        result["blocked_candidates"] = [{"candidate": deepcopy(preview), "reason": context_block} for preview in previews]
        return _finish(result, BLOCKED, context_block)

    queue_data, queue_error = _read_queue(queue_path)
    if queue_error:
        return _finish(result, INVALID, queue_error)
    queue_orders = _as_list(queue_data.get("orders"))

    guarded: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for preview in previews:
        if not isinstance(preview, dict):
            return _finish(result, INVALID, "approved preview item must be a dict")
        matches = [_as_dict(order) for order in queue_orders if _identity_matches(_as_dict(order), preview)]
        if len(matches) != 1:
            return _finish(result, BLOCKED, f"queue matching record count is {len(matches)} for {preview.get('order_id')}")

        queue_review = review_execution_queue_committed(_queue_commit_result(matches[0]))
        if _status(queue_review.get("status")) != "READY_FOR_FINAL_SEND_GATE":
            blocked.append({"candidate": deepcopy(preview), "reason": "queue record is not ORDER_QUEUED-ready", "queue_review": queue_review})
            continue

        guard = evaluate_final_execution_guard(
            {"status": "REAL_READY", "execution_enabled": True},
            {"operator_confirmed": True, "real_trade_enabled": True},
            {"unresolved": False, "hoga_preview": {"unresolved": False}, "order_type_preview": {"unresolved": False}},
        )
        sell_reason = _sell_guard_reason(preview, context)
        if not guard.get("ok") or sell_reason:
            blocked.append({"candidate": deepcopy(preview), "reason": sell_reason or "final_execution_guard blocked", "final_guard": guard})
        else:
            guarded.append({"candidate": deepcopy(preview), "queue_record": deepcopy(matches[0]), "queue_review": queue_review, "final_guard": guard})

    result["queue_path"] = queue_path
    result["candidate_ids"] = list(expected_ids)
    result["guarded_candidates"] = deepcopy(guarded)
    result["blocked_candidates"] = deepcopy(blocked)
    result["approval_token_present"] = True
    result["approval_token_hash"] = _sha256_text(approval_token)
    result["summary"] = {
        "candidate_count": len(previews),
        "guard_ready_count": len(guarded),
        "guard_blocked_count": len(blocked),
        "guard_invalid_count": 0,
    }
    if blocked:
        return _finish(result, BLOCKED, "one or more SELL dispatch final guard candidates are blocked")

    result["status"] = READY
    result["final_guard_ready"] = True
    return _finalize(result)


def build_sell_send_order_call_preview(final_guard_result: Any, call_context: Any = None) -> dict[str, Any]:
    """Build final Kiwoom SendOrder argument previews without calling Kiwoom."""
    result = _base_result(CALL_PREVIEW_TYPE, "preview_type", final_guard_result)
    result.update(
        {
            "send_order_call_preview_ready": False,
            "function_boundary": "kiwoom.SendOrder",
            "queue_path": None,
            "call_previews": [],
            "candidate_ids": [],
            "source_final_guard_snapshot": deepcopy(final_guard_result) if isinstance(final_guard_result, dict) else None,
        }
    )
    context = _as_dict(call_context)

    if not isinstance(final_guard_result, dict):
        return _finish(result, INVALID, "final_guard_result must be a dict")
    if final_guard_result.get("guard_type") != FINAL_GUARD_TYPE:
        return _finish(result, INVALID, "guard_type is not SELL_DISPATCH_FINAL_EXECUTION_GUARD")
    if _has_safety_violation(final_guard_result):
        return _finish(result, INVALID, "final_guard_result safety flag violation")
    guard_status = _status(final_guard_result.get("status"))
    if guard_status == INVALID:
        return _finish(result, INVALID, "final_guard_result status is INVALID")
    if guard_status != READY:
        return _finish(result, BLOCKED, "final_guard_result is not READY")
    if final_guard_result.get("final_guard_ready") is not True:
        return _finish(result, INVALID, "final_guard_ready must be True")

    call_token = _text(context.get("final_call_token") or context.get("approval_token") or "FINAL_CALL_TOKEN_PREVIEW")
    guarded_candidates = final_guard_result.get("guarded_candidates")
    if not isinstance(guarded_candidates, list) or not guarded_candidates:
        return _finish(result, INVALID, "guarded_candidates must be a non-empty list")

    calls: list[dict[str, Any]] = []
    for item in guarded_candidates:
        candidate = _as_dict(item.get("candidate")) if isinstance(item, dict) else {}
        broker_dispatch_result = _as_dict(candidate.get("broker_dispatch_result"))
        if not broker_dispatch_result:
            return _finish(result, INVALID, "candidate broker_dispatch_result is required")
        adapter_contract = build_kiwoom_send_order_adapter_contract(
            broker_dispatch_result,
            {"account_no": candidate.get("account_no")},
            {"screen_no": candidate.get("screen_no")},
        )
        safety = {
            "status": "SEND_ORDER_SAFE",
            "send_order_allowed": True,
            "send_order_called": False,
            "broker_called": False,
            "runtime_write": False,
            "queue_write": False,
            "issues": [],
            "warnings": [],
        }
        call_result = preview_kiwoom_send_order_call(safety, adapter_contract, {"final_call_token": call_token})
        if _status(adapter_contract.get("status")) == INVALID or _status(call_result.get("status")) == INVALID:
            return _finish(result, INVALID, "existing SendOrder call preview returned INVALID")
        if _status(adapter_contract.get("status")) != "SEND_ORDER_CONTRACT_READY" or _status(call_result.get("status")) != "SEND_ORDER_CALL_READY":
            return _finish(result, BLOCKED, "existing SendOrder call preview returned BLOCKED")
        params = _as_dict(call_result.get("send_order_call_preview")).get("send_order_params", {})
        calls.append(
            {
                "candidate_id": _text(candidate.get("candidate_id")),
                "source_signal_id": _text(candidate.get("source_signal_id")),
                "order_id": _text(candidate.get("order_id")),
                "queue_pending_id": _text(candidate.get("queue_pending_id")),
                "execution_id": _text(candidate.get("execution_id")),
                "request_hash": _text(candidate.get("request_hash")),
                "lock_id": _text(candidate.get("lock_id")),
                "function_boundary": "kiwoom.SendOrder",
                "rq_name": _text(params.get("order_name")),
                "screen_no": _text(params.get("screen_no")),
                "account_no": _text(params.get("account_no")),
                "order_type": deepcopy(params.get("order_type")),
                "code": _text(params.get("code")),
                "quantity": deepcopy(params.get("quantity")),
                "price": deepcopy(params.get("price")),
                "hoga": _text(params.get("hoga")),
                "original_order_no": _text(params.get("original_order_no")),
                "send_order_args": deepcopy(call_result.get("send_order_args")),
                "send_order_call_result": deepcopy(call_result),
                "adapter_contract_result": deepcopy(adapter_contract),
            }
        )

    result["status"] = READY
    result["send_order_call_preview_ready"] = True
    result["queue_path"] = final_guard_result.get("queue_path")
    result["call_previews"] = deepcopy(calls)
    result["candidate_ids"] = [_text(call.get("candidate_id")) for call in calls]
    result["summary"] = {
        "call_preview_count": len(calls),
        "blocked_count": 0,
        "invalid_count": 0,
    }
    return _finalize(result)


def build_sell_dispatch_execution_audit_preview(send_order_call_preview: Any, audit_context: Any = None) -> dict[str, Any]:
    """Create an in-memory audit preview for dispatch execution readiness."""
    result = _base_result(AUDIT_PREVIEW_TYPE, "audit_type", send_order_call_preview)
    result.update(
        {
            "audit_preview_ready": False,
            "execution_not_started": True,
            "approval_token_hash": "",
            "masked_account_no": "",
            "queue_path": None,
            "candidate_identities": [],
            "send_order_call_preview_hash": "",
            "expected_dispatch_count": 0,
            "source_call_preview_snapshot": deepcopy(send_order_call_preview) if isinstance(send_order_call_preview, dict) else None,
        }
    )
    context = _as_dict(audit_context)

    if not isinstance(send_order_call_preview, dict):
        return _finish(result, INVALID, "send_order_call_preview must be a dict")
    if send_order_call_preview.get("preview_type") != CALL_PREVIEW_TYPE:
        return _finish(result, INVALID, "preview_type is not SELL_SEND_ORDER_CALL_PREVIEW")
    if _has_safety_violation(send_order_call_preview):
        return _finish(result, INVALID, "send_order_call_preview safety flag violation")
    if _status(send_order_call_preview.get("status")) != READY:
        return _finish(result, BLOCKED if _status(send_order_call_preview.get("status")) == BLOCKED else INVALID, "send_order_call_preview is not READY")
    if send_order_call_preview.get("send_order_call_preview_ready") is not True:
        return _finish(result, INVALID, "send_order_call_preview_ready must be True")

    calls = _as_list(send_order_call_preview.get("call_previews"))
    if not calls:
        return _finish(result, INVALID, "call_previews must be non-empty")
    token = _text(context.get("approval_token") or context.get("final_call_token"))
    account_no = _text(context.get("account_no") or _first_preview_value(calls, "account_no"))

    identities = [_identity_from_preview(call) for call in calls]
    result["status"] = READY
    result["audit_preview_ready"] = True
    result["approval_token_hash"] = _sha256_text(token) if token else ""
    result["masked_account_no"] = _mask_account(account_no)
    result["queue_path"] = send_order_call_preview.get("queue_path")
    result["candidate_identities"] = identities
    result["send_order_call_preview_hash"] = _stable_hash(send_order_call_preview)
    result["expected_dispatch_count"] = len(calls)
    result["summary"] = {
        "expected_dispatch_count": len(calls),
        "identity_count": len(identities),
        "execution_not_started": True,
        "actual_order_sent": False,
        "broker_api_called": False,
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
        },
    }


def _sanitize_token_snapshot(value: dict[str, Any]) -> dict[str, Any]:
    snapshot = deepcopy(value)
    token = _text(snapshot.pop("approval_token", ""))
    snapshot["approval_token_present"] = bool(token)
    snapshot["approval_token_hash"] = _sha256_text(token) if token else ""
    return snapshot


def _sell_guard_reason(preview: dict[str, Any], context: dict[str, Any]) -> str | None:
    if _text(preview.get("order_type")).upper() != "SELL":
        return "order_type must be SELL"
    if not _positive_number(preview.get("quantity")):
        return "quantity must be greater than 0"
    lock_ids = context.get("order_locks")
    if isinstance(lock_ids, dict) and lock_ids.get(_text(preview.get("lock_id"))) is False:
        return "order lock mismatch"
    if context.get("order_lock_valid") is False:
        return "order lock mismatch"
    account_snapshot = _as_dict(context.get("account_snapshot"))
    if account_snapshot:
        if account_snapshot.get("valid") is False:
            return "account snapshot invalid"
        if _text(account_snapshot.get("account_no")) and _text(account_snapshot.get("account_no")) != _text(preview.get("account_no")):
            return "account snapshot account mismatch"
    holding_qty = _holding_qty(context, _text(preview.get("code")))
    if holding_qty is not None and _to_float(preview.get("quantity")) > holding_qty:
        return "quantity exceeds holding snapshot"
    return None


def _guard_context_block(context: dict[str, Any]) -> str | None:
    if context.get("emergency_stop") is True:
        return "emergency_stop is active"
    if context.get("trading_halted") is True:
        return "trading_halted is active"
    if context.get("market_open") is not True:
        return "market_open must be True"
    return None


def _identity_from_preview(preview: dict[str, Any]) -> dict[str, str]:
    return {field: _text(preview.get(field)) for field in IDENTITY_FIELDS}


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
        "domain": "Execution / Dispatch Final Guard",
        "routine_dependency": None,
        "status": BLOCKED,
        "preview_only": True,
        "execution_connected": False,
        "runtime_write": False,
        "queue_write": False,
        "queue_status_changed": False,
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
    result["queue_status_changed"] = False
    result["priority_selected"] = False
    result["auto_selected"] = False
    return result


def _has_safety_violation(payload: dict[str, Any]) -> bool:
    return any(payload.get(field) is True for field in SAFETY_FALSE_FIELDS)


def _identity_matches(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return all(_text(left.get(field)) == _text(right.get(field)) for field in IDENTITY_FIELDS)


def _first_preview_value(previews: list[Any], field: str) -> Any:
    for preview in previews:
        if isinstance(preview, dict) and preview.get(field) not in (None, ""):
            return preview.get(field)
    return None


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


def _mask_account(account_no: str) -> str:
    text = _text(account_no)
    if len(text) <= 4:
        return "*" * len(text)
    return text[:2] + ("*" * (len(text) - 4)) + text[-2:]


def _stable_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(_text(value).encode("utf-8")).hexdigest()


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
