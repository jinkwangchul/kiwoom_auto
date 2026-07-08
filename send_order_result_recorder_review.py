# -*- coding: utf-8 -*-
"""Review a SendOrder result recorder output without side effects."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any


REVIEW_TYPE = "SEND_ORDER_RESULT_RECORDER_REVIEW"
STATUS_OK = "RECORD_REVIEW_OK"
STATUS_BLOCKED = "RECORD_REVIEW_BLOCKED"
STATUS_INVALID = "INVALID"
STATUS_ERROR = "ERROR"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


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
    record_verified: bool = False,
) -> dict[str, Any]:
    return {
        "status": status,
        "review": deepcopy(review) if isinstance(review, dict) else {},
        "issues": list(issues or []),
        "warnings": list(warnings or []),
        "record_verified": bool(record_verified),
        "chejan_ready": False,
        "chejan_called": False,
        "runtime_write": False,
        "queue_write": False,
    }


def _read_executions(path: Path) -> tuple[dict[str, Any], str | None]:
    if not path.exists():
        return {}, "record file not found"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {}, f"failed to read order_executions json: {exc}"
    if not isinstance(data, dict):
        return {}, "order_executions root must be an object"
    if not isinstance(data.get("executions"), list):
        return {}, "order_executions executions must be a list"
    if any(not isinstance(item, dict) for item in data.get("executions", [])):
        return {}, "order_executions executions must contain only objects"
    return data, None


def _find_record(data: dict[str, Any], record_id: str) -> dict[str, Any]:
    for item in data.get("executions", []):
        current = _as_dict(item)
        if _text(current.get("record_id")) == record_id:
            return current
    return {}


def review_send_order_result_record(
    recorder_result: Any,
    expected_record_id: Any,
    record_path: Any,
) -> dict[str, Any]:
    """Verify a recorder result and its persisted temp record without mutation."""
    result = _as_dict(recorder_result)
    if not result:
        return _result(status=STATUS_INVALID, issues=["recorder_result must be a dict"])

    expected_id = _text(expected_record_id)
    if not expected_id:
        return _result(status=STATUS_INVALID, issues=["expected_record_id is required"])

    status = _text(result.get("status")).upper()
    warnings = list(result.get("warnings") or [])
    if status == "INVALID":
        return _result(
            status=STATUS_INVALID,
            issues=["recorder_result.status is INVALID"] + list(result.get("issues") or []),
            warnings=warnings,
        )
    if status == "ERROR":
        return _result(
            status=STATUS_ERROR,
            issues=["recorder_result.status is ERROR"] + list(result.get("issues") or []),
            warnings=warnings,
        )
    if status == "BLOCKED":
        return _result(
            status=STATUS_BLOCKED,
            issues=["recorder_result.status is BLOCKED"] + list(result.get("issues") or []),
            warnings=warnings,
        )
    if status != "RECORDED":
        return _result(status=STATUS_INVALID, issues=["recorder_result.status is not supported"], warnings=warnings)

    report = _as_dict(result.get("record_report"))
    if not report:
        return _result(status=STATUS_INVALID, issues=["record_report is required"], warnings=warnings)

    report_record_id = _text(report.get("record_id"))
    if not report_record_id:
        return _result(status=STATUS_INVALID, issues=["record_report.record_id is required"], warnings=warnings)
    if report_record_id != expected_id:
        return _result(status=STATUS_INVALID, issues=["expected_record_id does not match record_report.record_id"], warnings=warnings)
    if result.get("record_called") is not True:
        return _result(status=STATUS_INVALID, issues=["recorder_result.record_called is not true"], warnings=warnings)
    if result.get("queue_write") is not False:
        return _result(status=STATUS_INVALID, issues=["recorder_result.queue_write must be false"], warnings=warnings)
    if result.get("chejan_called") is not False:
        return _result(status=STATUS_INVALID, issues=["recorder_result.chejan_called must be false"], warnings=warnings)

    try:
        path = Path(record_path)
    except Exception as exc:
        return _result(status=STATUS_ERROR, issues=[f"record_path is invalid: {exc}"], warnings=warnings)

    data, read_issue = _read_executions(path)
    if read_issue is not None:
        return _result(status=STATUS_ERROR, issues=[read_issue], warnings=warnings)

    record = _find_record(data, expected_id)
    if not record:
        return _result(status=STATUS_ERROR, issues=["expected record not found"], warnings=warnings)

    if _text(record.get("dispatch_id")) != _text(report.get("dispatch_id")):
        return _result(status=STATUS_INVALID, issues=["record dispatch_id does not match report"], warnings=warnings)
    if _text(record.get("order_id")) != _text(report.get("order_id")):
        return _result(status=STATUS_INVALID, issues=["record order_id does not match report"], warnings=warnings)
    if record.get("status") != "SEND_ORDER_RESULT_RECORDED":
        return _result(status=STATUS_INVALID, issues=["record status is not SEND_ORDER_RESULT_RECORDED"], warnings=warnings)

    review = {
        "review_type": REVIEW_TYPE,
        "record_id": expected_id,
        "record_path": str(path),
        "dispatch_id": record.get("dispatch_id"),
        "order_id": record.get("order_id"),
        "source_order_id": record.get("source_order_id"),
        "source_signal_id": record.get("source_signal_id"),
        "code": record.get("code"),
        "side": record.get("side"),
        "quantity": deepcopy(record.get("quantity")),
        "price": deepcopy(record.get("price")),
        "hoga": record.get("hoga"),
        "send_order_return_code": deepcopy(record.get("send_order_return_code")),
        "send_order_status": record.get("send_order_status"),
        "review_status": record.get("review_status"),
        "record_status": record.get("status"),
        "record_called": True,
        "chejan_deferred": True,
    }
    return _result(status=STATUS_OK, review=review, issues=[], warnings=warnings, record_verified=True)
