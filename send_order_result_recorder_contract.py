# -*- coding: utf-8 -*-
"""Build a SendOrder result recorder contract without recording side effects."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any


STATUS_READY = "RECORD_READY"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"

REVIEW_OK = "SEND_ORDER_REVIEW_OK"
REVIEW_FAILED = "SEND_ORDER_REVIEW_FAILED"
REVIEW_UNCERTAIN = "SEND_ORDER_REVIEW_UNCERTAIN"

REQUIRED_CONTRACT_FIELDS = (
    "dispatch_id",
    "order_id",
    "source_order_id",
    "source_signal_id",
    "code",
    "side",
    "quantity",
    "price",
    "hoga",
    "send_order_return_code",
    "send_order_status",
    "review_status",
    "recorded_at",
)


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _first_present(*values: Any) -> Any:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    return False


def _result(
    *,
    status: str,
    record_contract: dict[str, Any] | None = None,
    issues: list[str] | None = None,
    warnings: list[str] | None = None,
    record_ready: bool = False,
) -> dict[str, Any]:
    return {
        "status": status,
        "record_contract": deepcopy(record_contract) if isinstance(record_contract, dict) else {},
        "issues": list(issues or []),
        "warnings": list(warnings or []),
        "record_ready": bool(record_ready),
        "record_called": False,
        "runtime_write": False,
        "queue_write": False,
        "chejan_called": False,
    }


def _context_order(record_context: dict[str, Any]) -> dict[str, Any]:
    for key in ("order", "order_context", "queue_item", "dispatch_contract", "order_contract"):
        nested = record_context.get(key)
        if isinstance(nested, dict):
            return nested
    return {}


def _context_params(record_context: dict[str, Any], order_context: dict[str, Any]) -> dict[str, Any]:
    for value in (record_context.get("send_order_params"), order_context.get("send_order_params")):
        if isinstance(value, dict):
            return value
    return {}


def _build_contract(
    executor_review_result: dict[str, Any],
    record_context: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    review = _as_dict(executor_review_result.get("review"))
    order_context = _context_order(record_context)
    send_order_params = _context_params(record_context, order_context)

    contract = {
        "contract_type": "SEND_ORDER_RESULT_RECORDER_CONTRACT",
        "dispatch_id": _first_present(
            review.get("dispatch_id"),
            record_context.get("dispatch_id"),
            order_context.get("dispatch_id"),
        ),
        "order_id": _first_present(
            review.get("order_id"),
            record_context.get("order_id"),
            order_context.get("order_id"),
        ),
        "source_order_id": _first_present(
            record_context.get("source_order_id"),
            order_context.get("source_order_id"),
            record_context.get("order_id"),
            order_context.get("order_id"),
            review.get("order_id"),
        ),
        "source_signal_id": _first_present(
            record_context.get("source_signal_id"),
            order_context.get("source_signal_id"),
        ),
        "code": _first_present(
            record_context.get("code"),
            order_context.get("code"),
            send_order_params.get("code"),
        ),
        "side": _first_present(
            record_context.get("side"),
            order_context.get("side"),
            send_order_params.get("side"),
        ),
        "quantity": _first_present(
            record_context.get("quantity"),
            order_context.get("quantity"),
            send_order_params.get("quantity"),
        ),
        "price": _first_present(
            record_context.get("price"),
            order_context.get("price"),
            send_order_params.get("price"),
        ),
        "hoga": _first_present(
            record_context.get("hoga"),
            order_context.get("hoga"),
            send_order_params.get("hoga"),
        ),
        "send_order_return_code": review.get("return_code"),
        "send_order_status": _first_present(review.get("executor_status"), record_context.get("send_order_status")),
        "review_status": executor_review_result.get("status"),
        "recorded_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    missing = [field for field in REQUIRED_CONTRACT_FIELDS if _is_missing(contract.get(field))]
    contract["source_review"] = deepcopy(review)
    return contract, missing


def build_send_order_result_recorder_contract(
    executor_review_result: Any,
    record_context: Any,
) -> dict[str, Any]:
    """Convert a SendOrder executor review into a recorder contract only."""
    review_result = _as_dict(executor_review_result)
    context = _as_dict(record_context)

    if not review_result:
        return _result(status=STATUS_INVALID, issues=["executor_review_result must be a dict"])
    if not context:
        return _result(status=STATUS_INVALID, issues=["record_context must be a non-empty dict"])

    review_status = _text(review_result.get("status")).upper()
    if review_status == "INVALID":
        return _result(
            status=STATUS_INVALID,
            issues=["executor_review_result.status is INVALID"] + list(review_result.get("issues") or []),
            warnings=list(review_result.get("warnings") or []),
        )
    if review_status in {"BLOCKED", REVIEW_FAILED, REVIEW_UNCERTAIN}:
        return _result(
            status=STATUS_BLOCKED,
            issues=[f"executor_review_result.status is {review_status}"] + list(review_result.get("issues") or []),
            warnings=list(review_result.get("warnings") or []),
        )
    if review_status != REVIEW_OK:
        return _result(status=STATUS_INVALID, issues=["executor_review_result.status is not supported"])

    if review_result.get("record_ready") is not True:
        return _result(status=STATUS_BLOCKED, issues=["executor_review_result.record_ready is not true"])
    if review_result.get("recorded") is True:
        return _result(status=STATUS_BLOCKED, issues=["executor_review_result is already recorded"])
    if review_result.get("chejan_processed") is True:
        return _result(status=STATUS_BLOCKED, issues=["executor_review_result already processed Chejan"])

    review = _as_dict(review_result.get("review"))
    if not review:
        return _result(status=STATUS_INVALID, issues=["executor_review_result.review is required"])

    contract, missing = _build_contract(review_result, context)
    if missing:
        return _result(
            status=STATUS_INVALID,
            issues=["record_contract missing required fields: " + ", ".join(missing)],
        )

    return _result(
        status=STATUS_READY,
        record_contract=contract,
        issues=[],
        warnings=list(review_result.get("warnings") or []),
        record_ready=True,
    )
