# -*- coding: utf-8 -*-
"""Read-only review of Queue Commit Executor results."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any


REVIEW_TYPE = "EXECUTION_QUEUE_COMMIT_RESULT_REVIEW"
STATUS_OK = "REVIEW_OK"
STATUS_BLOCKED = "REVIEW_BLOCKED"
STATUS_INVALID = "INVALID"
STATUS_ERROR = "ERROR"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _result(
    *,
    status: str,
    review: dict[str, Any] | None = None,
    issues: list[str] | None = None,
    warnings: list[str] | None = None,
    send_order_ready: bool = False,
) -> dict[str, Any]:
    return {
        "review_type": REVIEW_TYPE,
        "status": status,
        "review": deepcopy(review) if isinstance(review, dict) else {},
        "issues": list(issues or []),
        "warnings": list(warnings or []),
        "send_order_ready": send_order_ready,
        "send_order_called": False,
        "runtime_write": False,
        "queue_write": False,
    }


def _read_queue(path: Path) -> tuple[dict[str, Any], str | None]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {}, f"failed to read order_queue json: {exc}"
    if not isinstance(data, dict):
        return {}, "order_queue root must be an object"
    if not isinstance(data.get("orders"), list):
        return {}, "order_queue orders must be a list"
    if any(not isinstance(item, dict) for item in data.get("orders", [])):
        return {}, "order_queue orders must contain only objects"
    return data, None


def _find_order(data: dict[str, Any], expected_order_id: str, commit_id: str) -> dict[str, Any] | None:
    for item in data.get("orders", []):
        order = _as_dict(item)
        if _text(order.get("order_id")) != expected_order_id:
            continue
        if commit_id and _text(order.get("commit_id")) != commit_id:
            continue
        return deepcopy(order)
    return None


def review_queue_commit_result(
    commit_result: Any,
    queue_path: Any,
    expected_order_id: Any,
) -> dict[str, Any]:
    """Review Queue Commit Executor output without side effects."""
    if not isinstance(commit_result, dict):
        return _result(status=STATUS_INVALID, issues=["commit_result must be a dict"])
    expected = _text(expected_order_id)
    if not expected:
        return _result(status=STATUS_INVALID, issues=["expected_order_id is required"])

    status = _text(commit_result.get("status")).upper()
    if status == "INVALID":
        return _result(status=STATUS_INVALID, issues=["commit_result.status is INVALID"] + _as_list(commit_result.get("issues")))
    if status == "ERROR":
        return _result(status=STATUS_ERROR, issues=["commit_result.status is ERROR"] + _as_list(commit_result.get("issues")))
    if status != "COMMITTED":
        return _result(status=STATUS_BLOCKED, issues=["commit_result.status is not COMMITTED"] + _as_list(commit_result.get("issues")))

    commit_id = _text(commit_result.get("commit_id"))
    report = _as_dict(commit_result.get("commit_report"))
    if not commit_id:
        return _result(status=STATUS_INVALID, issues=["commit_id is required"])
    if not report:
        return _result(status=STATUS_INVALID, issues=["commit_report is required"])
    required_report_fields = ("before_hash", "after_hash", "committed_record")
    missing = [field for field in required_report_fields if not report.get(field)]
    if missing:
        return _result(status=STATUS_INVALID, issues=[f"commit_report.{field} is required" for field in missing])
    if commit_result.get("queue_write") is not True:
        return _result(status=STATUS_INVALID, issues=["commit_result.queue_write is not true"])
    if commit_result.get("queue_commit_called") is not True:
        return _result(status=STATUS_INVALID, issues=["commit_result.queue_commit_called is not true"])
    if commit_result.get("send_order_called") is not False:
        return _result(status=STATUS_BLOCKED, issues=["commit_result.send_order_called is not false"])
    if report.get("rollback_attempted") is True or report.get("rollback_succeeded") is True or report.get("restored_from_backup") is True:
        return _result(status=STATUS_BLOCKED, issues=["commit_result is rollback state"])

    path = Path(queue_path)
    if not path.exists():
        return _result(status=STATUS_ERROR, issues=["queue file does not exist"])
    data, read_issue = _read_queue(path)
    if read_issue is not None:
        return _result(status=STATUS_ERROR, issues=[read_issue])

    queue_item = _find_order(data, expected, commit_id)
    if queue_item is None:
        return _result(status=STATUS_BLOCKED, issues=["queue item not found"])
    if queue_item.get("send_order_called") is not False:
        return _result(status=STATUS_BLOCKED, review={"queue_item": queue_item}, issues=["queue item send_order_called is not false"])
    if queue_item.get("status") != "ORDER_QUEUED":
        return _result(status=STATUS_BLOCKED, review={"queue_item": queue_item}, issues=["queue item status is not ORDER_QUEUED"])

    review = {
        "commit_id": commit_id,
        "order_id": expected,
        "queue_path": str(path),
        "before_hash": report.get("before_hash"),
        "after_hash": report.get("after_hash"),
        "queue_item": queue_item,
        "commit_report": deepcopy(report),
        "ready_for_send_order_review": True,
    }
    return _result(status=STATUS_OK, review=review, issues=[], warnings=_as_list(commit_result.get("warnings")), send_order_ready=True)
