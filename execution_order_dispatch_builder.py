# -*- coding: utf-8 -*-
"""Build final broker dispatch contracts from queue commit review results."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any
from uuid import uuid4


STATUS_READY = "DISPATCH_READY"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"
BUILDER_TYPE = "EXECUTION_ORDER_DISPATCH_BUILDER"
VALID_SIDES = {"BUY", "SELL"}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _result(
    *,
    status: str,
    dispatch_contract: dict[str, Any] | None = None,
    issues: list[str] | None = None,
    warnings: list[str] | None = None,
    send_order_ready: bool = False,
) -> dict[str, Any]:
    return {
        "builder_type": BUILDER_TYPE,
        "status": status,
        "dispatch_contract": deepcopy(dispatch_contract) if isinstance(dispatch_contract, dict) else {},
        "issues": list(issues or []),
        "warnings": list(warnings or []),
        "send_order_ready": send_order_ready,
        "send_order_called": False,
        "broker_called": False,
    }


def _extract_queue_item(review: dict[str, Any]) -> dict[str, Any]:
    queue_item = _as_dict(review.get("queue_item"))
    if queue_item:
        return queue_item
    return _as_dict(_as_dict(review.get("commit_report")).get("committed_record"))


def _value(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return None


def _build_contract(queue_item: dict[str, Any], account_context: dict[str, Any], broker_profile: dict[str, Any]) -> dict[str, Any]:
    broker_type = _text(broker_profile.get("broker_type") or broker_profile.get("type"))
    hoga = _text(
        _value(
            queue_item.get("hoga"),
            _as_dict(queue_item.get("order_intent")).get("hoga"),
            broker_profile.get("default_hoga"),
            account_context.get("default_hoga"),
        )
    )
    return {
        "dispatch_id": f"DISPATCH_{uuid4().hex}",
        "created_at": _now_text(),
        "account_no": _text(account_context.get("account_no")),
        "broker_type": broker_type,
        "order_id": _text(queue_item.get("order_id")),
        "source_order_id": _text(queue_item.get("source_order_id") or queue_item.get("order_id")),
        "source_signal_id": _text(queue_item.get("source_signal_id")),
        "code": _text(queue_item.get("code")),
        "side": _text(queue_item.get("side")).upper(),
        "quantity": deepcopy(queue_item.get("quantity")),
        "price": deepcopy(queue_item.get("price")),
        "hoga": hoga,
        "request_hash": _text(queue_item.get("request_hash")),
        "queue_item": deepcopy(queue_item),
    }


def _contract_issues(contract: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    for field in (
        "account_no",
        "broker_type",
        "order_id",
        "source_order_id",
        "source_signal_id",
        "code",
        "hoga",
        "request_hash",
        "dispatch_id",
        "created_at",
    ):
        if not _text(contract.get(field)):
            issues.append(f"dispatch_contract.{field} is required")
    if _text(contract.get("side")).upper() not in VALID_SIDES:
        issues.append("dispatch_contract.side is invalid")
    if contract.get("quantity") in (None, ""):
        issues.append("dispatch_contract.quantity is required")
    if contract.get("price") in (None, ""):
        issues.append("dispatch_contract.price is required")
    return issues


def build_order_dispatch_contract(
    review_result: Any,
    account_context: Any,
    broker_profile: Any,
) -> dict[str, Any]:
    """Build a dispatch contract without calling SendOrder or broker code."""
    review_result_dict = _as_dict(review_result)
    account = _as_dict(account_context)
    broker = _as_dict(broker_profile)

    if not review_result_dict:
        return _result(status=STATUS_INVALID, issues=["review_result must be a dict"])
    if not account:
        return _result(status=STATUS_INVALID, issues=["account_context must be a non-empty dict"])
    if not broker:
        return _result(status=STATUS_INVALID, issues=["broker_profile must be a non-empty dict"])

    review_status = _text(review_result_dict.get("status")).upper()
    if review_status == "INVALID":
        return _result(status=STATUS_INVALID, issues=["review_result.status is INVALID"] + _as_list(review_result_dict.get("issues")))
    if review_status != "REVIEW_OK":
        return _result(status=STATUS_BLOCKED, issues=["review_result.status is not REVIEW_OK"] + _as_list(review_result_dict.get("issues")))
    if review_result_dict.get("send_order_ready") is not True:
        return _result(status=STATUS_BLOCKED, issues=["review_result.send_order_ready is not true"])
    if review_result_dict.get("send_order_called") is not False:
        return _result(status=STATUS_BLOCKED, issues=["review_result.send_order_called is not false"])

    if not _text(account.get("account_no")):
        return _result(status=STATUS_INVALID, issues=["account_context.account_no is required"])
    if not _text(broker.get("broker_type") or broker.get("type")):
        return _result(status=STATUS_INVALID, issues=["broker_profile.broker_type is required"])

    review = _as_dict(review_result_dict.get("review"))
    queue_item = _extract_queue_item(review)
    if not queue_item:
        return _result(status=STATUS_INVALID, issues=["review.queue_item is required"])

    contract = _build_contract(queue_item, account, broker)
    issues = _contract_issues(contract)
    if issues:
        return _result(status=STATUS_INVALID, dispatch_contract=contract, issues=issues)

    return _result(
        status=STATUS_READY,
        dispatch_contract=contract,
        issues=[],
        warnings=_as_list(review_result_dict.get("warnings")),
        send_order_ready=True,
    )
