# -*- coding: utf-8 -*-
"""Review a Kiwoom SendOrder executor result without side effects."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


STATUS_OK = "SEND_ORDER_REVIEW_OK"
STATUS_FAILED = "SEND_ORDER_REVIEW_FAILED"
STATUS_UNCERTAIN = "SEND_ORDER_REVIEW_UNCERTAIN"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _result(
    *,
    status: str,
    review: dict[str, Any] | None = None,
    issues: list[str] | None = None,
    warnings: list[str] | None = None,
    record_ready: bool = False,
    chejan_wait_required: bool = False,
    send_order_called: bool = False,
) -> dict[str, Any]:
    return {
        "status": status,
        "review": deepcopy(review) if isinstance(review, dict) else {},
        "issues": list(issues or []),
        "warnings": list(warnings or []),
        "record_ready": bool(record_ready),
        "chejan_wait_required": bool(chejan_wait_required),
        "send_order_called": bool(send_order_called),
        "runtime_write": False,
        "queue_write": False,
        "recorded": False,
        "chejan_processed": False,
    }


def _call_preview_identity(call_preview_result: dict[str, Any]) -> dict[str, str]:
    preview = _as_dict(call_preview_result.get("send_order_call_preview"))
    return {
        "dispatch_id": _text(preview.get("dispatch_id")),
        "order_id": _text(preview.get("order_id")),
        "final_call_token": _text(preview.get("final_call_token")),
    }


def _executor_identity(executor_result: dict[str, Any], send_order_result: dict[str, Any]) -> dict[str, str]:
    return {
        "dispatch_id": _text(executor_result.get("dispatch_id") or send_order_result.get("dispatch_id")),
        "order_id": _text(executor_result.get("order_id") or send_order_result.get("order_id")),
        "final_call_token": _text(
            executor_result.get("final_call_token") or send_order_result.get("final_call_token")
        ),
    }


def _adapter_call_count(executor_result: dict[str, Any], send_order_result: dict[str, Any]) -> tuple[int | None, bool]:
    for value in (
        executor_result.get("adapter_call_count"),
        executor_result.get("send_order_call_count"),
        send_order_result.get("adapter_call_count"),
        send_order_result.get("send_order_call_count"),
    ):
        count = _int_or_none(value)
        if count is not None:
            return count, False
    if executor_result.get("send_order_called") is True and executor_result.get("broker_called") is True:
        return 1, True
    return None, False


def _validate_common(
    executor_result: dict[str, Any],
    call_preview_result: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, str], int | None, bool, dict[str, Any] | None]:
    send_order_result = _as_dict(executor_result.get("send_order_result"))
    if not send_order_result:
        return send_order_result, {}, None, False, _result(
            status=STATUS_INVALID,
            issues=["send_order_result is required"],
            send_order_called=executor_result.get("send_order_called") is True,
        )

    call_identity = _call_preview_identity(call_preview_result)
    if not call_identity["dispatch_id"]:
        return send_order_result, call_identity, None, False, _result(
            status=STATUS_INVALID,
            issues=["call_preview_result.dispatch_id is required"],
            send_order_called=executor_result.get("send_order_called") is True,
        )
    if not call_identity["order_id"]:
        return send_order_result, call_identity, None, False, _result(
            status=STATUS_INVALID,
            issues=["call_preview_result.order_id is required"],
            send_order_called=executor_result.get("send_order_called") is True,
        )

    executor_identity = _executor_identity(executor_result, send_order_result)
    if executor_identity["dispatch_id"] and executor_identity["dispatch_id"] != call_identity["dispatch_id"]:
        return send_order_result, call_identity, None, False, _result(
            status=STATUS_INVALID,
            issues=["executor dispatch_id does not match call preview"],
            send_order_called=executor_result.get("send_order_called") is True,
        )
    if executor_identity["order_id"] and executor_identity["order_id"] != call_identity["order_id"]:
        return send_order_result, call_identity, None, False, _result(
            status=STATUS_INVALID,
            issues=["executor order_id does not match call preview"],
            send_order_called=executor_result.get("send_order_called") is True,
        )

    call_count, inferred = _adapter_call_count(executor_result, send_order_result)
    if call_count != 1:
        return send_order_result, call_identity, call_count, inferred, _result(
            status=STATUS_INVALID,
            issues=["adapter_call_count must be 1"],
            send_order_called=executor_result.get("send_order_called") is True,
        )

    return send_order_result, call_identity, call_count, inferred, None


def review_kiwoom_send_order_executor_result(
    send_order_executor_result: Any,
    call_preview_result: Any,
    review_context: Any,
) -> dict[str, Any]:
    """Classify a SendOrder executor result without recording or Chejan work."""
    executor_result = _as_dict(send_order_executor_result)
    call_preview = _as_dict(call_preview_result)

    if not executor_result:
        return _result(status=STATUS_INVALID, issues=["send_order_executor_result must be a dict"])
    if not call_preview:
        return _result(status=STATUS_INVALID, issues=["call_preview_result must be a dict"])
    if not isinstance(review_context, dict):
        return _result(status=STATUS_INVALID, issues=["review_context must be a dict"])

    executor_status = _text(executor_result.get("status")).upper()
    if executor_status == STATUS_INVALID:
        return _result(
            status=STATUS_INVALID,
            issues=["send_order_executor_result.status is INVALID"] + list(executor_result.get("issues") or []),
            send_order_called=executor_result.get("send_order_called") is True,
        )
    if executor_status == STATUS_BLOCKED:
        return _result(
            status=STATUS_BLOCKED,
            issues=["send_order_executor_result.status is BLOCKED"] + list(executor_result.get("issues") or []),
            send_order_called=executor_result.get("send_order_called") is True,
        )
    if executor_status not in {"SEND_ORDER_SENT", "SEND_ORDER_FAILED", "ERROR"}:
        return _result(
            status=STATUS_INVALID,
            issues=["send_order_executor_result.status is not supported"],
            send_order_called=executor_result.get("send_order_called") is True,
        )

    send_order_result, identity, call_count, inferred_count, blocked = _validate_common(executor_result, call_preview)
    if blocked is not None:
        return blocked

    return_code = _int_or_none(send_order_result.get("return_code"))
    warnings = list(executor_result.get("warnings") or [])
    if inferred_count:
        warnings.append("adapter_call_count inferred from send_order_called and broker_called flags")

    review = {
        "review_type": "KIWOOM_SEND_ORDER_EXECUTOR_RESULT_REVIEW",
        "executor_status": executor_status,
        "return_code": return_code,
        "adapter_call_count": call_count,
        "dispatch_id": identity["dispatch_id"],
        "order_id": identity["order_id"],
        "final_call_token": identity["final_call_token"],
        "recording_deferred": True,
        "chejan_deferred": True,
    }

    if executor_status == "SEND_ORDER_SENT" and return_code == 0:
        return _result(
            status=STATUS_OK,
            review=review,
            issues=[],
            warnings=warnings,
            record_ready=True,
            chejan_wait_required=True,
            send_order_called=True,
        )

    if executor_status == "SEND_ORDER_FAILED" and return_code not in (None, 0):
        return _result(
            status=STATUS_FAILED,
            review=review,
            issues=list(executor_result.get("issues") or []),
            warnings=warnings,
            record_ready=False,
            chejan_wait_required=False,
            send_order_called=True,
        )

    return _result(
        status=STATUS_UNCERTAIN,
        review=review,
        issues=list(executor_result.get("issues") or []) or ["send order executor result is uncertain"],
        warnings=warnings,
        record_ready=False,
        chejan_wait_required=False,
        send_order_called=executor_result.get("send_order_called") is True,
    )
