# -*- coding: utf-8 -*-
"""Fail-open Event Journal summaries for the Production trade pipeline."""

from __future__ import annotations

from collections import OrderedDict
from functools import wraps
import json
import logging
from pathlib import Path
from typing import Any

from event_journal_production import append_production_event


LOGGER = logging.getLogger(__name__)
_MAX_DEDUPE_KEYS = 4096
_SEEN_EVENT_KEYS: OrderedDict[tuple[str, ...], None] = OrderedDict()


def _fail_open_observer(function):
    @wraps(function)
    def wrapped(*args: Any, **kwargs: Any):
        try:
            return function(*args, **kwargs)
        except Exception as exc:
            LOGGER.warning("Event Journal observer failed in %s: %s", function.__name__, exc)
            return {"appended": False, "write_failed": True, "error": str(exc)}
    return wrapped


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _nested(value: dict[str, Any], key: str) -> dict[str, Any]:
    return _dict(value.get(key))


def _first(*values: Any) -> str:
    for value in values:
        text = _text(value)
        if text:
            return text
    return ""


def _identity(order: Any, result: Any) -> dict[str, str]:
    source = _dict(order)
    outcome = _dict(result)
    execution_request = _nested(source, "execution_request")
    request_preview = _nested(execution_request, "request_preview")
    routine_provenance = _nested(source, "routine_provenance")
    return {
        "stock_code": _first(
            source.get("code"), source.get("stock_code"),
            outcome.get("code"), outcome.get("stock_code"),
            execution_request.get("code"), execution_request.get("stock_code"),
            request_preview.get("code"), request_preview.get("stock_code"),
        ),
        "stock_name": _first(
            source.get("name"), source.get("stock_name"),
            outcome.get("name"), outcome.get("stock_name"),
            execution_request.get("name"), execution_request.get("stock_name"),
            request_preview.get("name"), request_preview.get("stock_name"),
        ),
        "routine": _first(
            source.get("routine_name"), source.get("routine"),
            source.get("routine_instance_id"),
            outcome.get("routine_name"), outcome.get("routine"),
            outcome.get("routine_instance_id"),
            routine_provenance.get("routine_name"),
            routine_provenance.get("routine_instance_id"),
        ),
        "signal_id": _first(
            outcome.get("source_signal_id"), outcome.get("signal_id"),
            source.get("source_signal_id"), source.get("signal_id"),
            execution_request.get("source_signal_id"),
        ),
        "order_id": _first(
            outcome.get("order_id"), source.get("order_id"), source.get("id"),
            execution_request.get("order_id"),
        ),
        "execution_id": _first(
            outcome.get("execution_id"), source.get("execution_id"),
            execution_request.get("execution_id"),
        ),
    }


def _remember(key: tuple[str, ...]) -> bool:
    if key in _SEEN_EVENT_KEYS:
        _SEEN_EVENT_KEYS.move_to_end(key)
        return False
    _SEEN_EVENT_KEYS[key] = None
    while len(_SEEN_EVENT_KEYS) > _MAX_DEDUPE_KEYS:
        _SEEN_EVENT_KEYS.popitem(last=False)
    return True


def _append_once(key: tuple[str, ...], event_type: str, **fields: Any) -> dict[str, Any]:
    if not _remember(key):
        return {"appended": False, "duplicate": True, "event_type": event_type}
    return append_production_event(event_type, **fields)


def _target_fields(identity: dict[str, str]) -> dict[str, Any]:
    code = identity["stock_code"]
    name = identity["stock_name"] or code
    return {
        "target_type": "STOCK",
        "target_id": code,
        "target_name": name,
        "stock_code": code,
        "stock_name": name,
        "routine": identity["routine"] or None,
        "signal_id": identity["signal_id"] or None,
        "order_id": identity["order_id"] or None,
        "execution_id": identity["execution_id"] or None,
    }


@_fail_open_observer
def observe_signal_created(
    signal_result: Any,
    queue_result: Any,
    *,
    routine_name: str,
    stock_code: str,
    stock_name: str,
    routine_instance_id: str = "",
) -> dict[str, Any]:
    """Record only a newly committed and verified BUY/SELL Routine Signal."""

    signal = _text(_dict(signal_result).get("signal")).upper()
    queued = _dict(queue_result)
    signal_id = _text(queued.get("id"))
    if (
        signal not in {"BUY", "SELL"}
        or queued.get("status") != "queued"
        or queued.get("signal_committed") is not True
        or queued.get("post_write_verified") is not True
        or not signal_id
    ):
        return {"appended": False, "skipped": True, "reason": "SIGNAL_NOT_NEWLY_COMMITTED"}

    event_type = f"{signal}_SIGNAL_DETECTED"
    details = {"routine_instance_id": _text(routine_instance_id)} if _text(routine_instance_id) else None
    return _append_once(
        (event_type, signal_id),
        event_type,
        severity="NOTICE",
        source="routine_signal_probe.probe_routine_for_stock",
        template_args={"stock_name": _text(stock_name) or _text(stock_code)},
        target_type="STOCK",
        target_id=_text(stock_code),
        target_name=_text(stock_name) or _text(stock_code),
        stock_code=_text(stock_code),
        stock_name=_text(stock_name) or _text(stock_code),
        routine=_text(routine_name) or None,
        signal_id=signal_id,
        details=details,
    )


@_fail_open_observer
def observe_approval_blocked(order: Any, result: Any) -> dict[str, Any]:
    value = _dict(result)
    if _text(value.get("approval_status")).upper() != "BLOCKED":
        return {"appended": False, "skipped": True, "reason": "APPROVAL_NOT_BLOCKED"}
    identity = _identity(order, value)
    reason = _first(value.get("reason_code"), value.get("approval_reason"))
    stable_id = identity["order_id"] or identity["signal_id"]
    if not stable_id:
        return {"appended": False, "skipped": True, "reason": "APPROVAL_IDENTITY_MISSING"}
    details = {"approval_reason": _text(value.get("approval_reason"))} if _text(value.get("approval_reason")) else None
    return _append_once(
        ("APPROVAL_BLOCKED", stable_id, reason),
        "APPROVAL_BLOCKED",
        severity="NOTICE",
        result="BLOCKED",
        source="routine_signal_consumer.approval",
        template_args={"stock_name": identity["stock_name"] or identity["stock_code"]},
        reason_code=reason or None,
        details=details,
        **_target_fields(identity),
    )


@_fail_open_observer
def observe_policy_blocked(order: Any, result: Any) -> dict[str, Any]:
    value = _dict(result)
    status = _first(value.get("policy_status"), value.get("after_status")).upper()
    if status != "BLOCKED_POLICY":
        return {"appended": False, "skipped": True, "reason": "POLICY_NOT_BLOCKED"}
    identity = _identity(order, value)
    reason = _first(value.get("reason_code"), value.get("policy_reason"), value.get("reason"), status)
    stable_id = identity["order_id"] or identity["signal_id"]
    if not stable_id:
        return {"appended": False, "skipped": True, "reason": "POLICY_IDENTITY_MISSING"}
    return _append_once(
        ("POLICY_BLOCKED", stable_id, reason),
        "POLICY_BLOCKED",
        severity="NOTICE",
        result="BLOCKED",
        source="routine_signal_consumer.operation_policy",
        template_args={"stock_name": identity["stock_name"] or identity["stock_code"]},
        reason_code=reason or None,
        **_target_fields(identity),
    )


@_fail_open_observer
def observe_execution_blocked(
    order: Any,
    result: Any,
    *,
    execution_step: str,
    passed: bool,
) -> dict[str, Any]:
    step = _text(execution_step).upper()
    if passed or step not in {"EXECUTION_ENABLE", "REAL_READY", "FINAL_GUARD"}:
        return {"appended": False, "skipped": True, "reason": "EXECUTION_NOT_BLOCKED"}
    value = _dict(result)
    identity = _identity(order, value)
    blocked = value.get("blocked_reasons") or value.get("issues") or []
    if not isinstance(blocked, list):
        blocked = [blocked]
    reasons = [_text(item) for item in blocked if _text(item)]
    reason = _first(value.get("reason_code"), reasons[0] if reasons else "", step)
    reason_signature = "\x1f".join(reasons) or reason
    stable_id = identity["order_id"] or identity["signal_id"]
    if not stable_id:
        return {"appended": False, "skipped": True, "reason": "EXECUTION_IDENTITY_MISSING"}
    return _append_once(
        ("EXECUTION_BLOCKED", stable_id, step, reason_signature),
        "EXECUTION_BLOCKED",
        severity="WARNING",
        result="BLOCKED",
        source="AutoTradeSettingWindow.process_executable_order_for_auto_trade",
        template_args={"stock_name": identity["stock_name"] or identity["stock_code"]},
        reason_code=reason or None,
        details={"execution_step": step},
        **_target_fields(identity),
    )


@_fail_open_observer
def observe_order_queued(
    order: Any,
    queue_record: Any,
    *,
    queue_commit_result: Any,
    read_back_result: Any,
) -> dict[str, Any]:
    commit = _dict(queue_commit_result)
    canonical_commit = _dict(commit.get("commit_result")) or commit
    read_back = _dict(read_back_result)
    actual = _dict(read_back.get("record"))
    record = actual or _dict(queue_record)
    if (
        commit.get("manual_commit") is not True
        or canonical_commit.get("committed") is not True
        or canonical_commit.get("post_write_verified") is not True
        or read_back.get("verified") is not True
        or record.get("status") != "ORDER_QUEUED"
    ):
        return {"appended": False, "skipped": True, "reason": "QUEUE_NOT_COMMITTED_AND_VERIFIED"}
    combined = dict(record)
    combined.update({key: value for key, value in _dict(order).items() if value not in (None, "")})
    identity = _identity(combined, record)
    queued_id = _text(record.get("id"))
    stable_id = queued_id or identity["order_id"]
    if not stable_id:
        return {"appended": False, "skipped": True, "reason": "ORDER_QUEUED_IDENTITY_MISSING"}
    return _append_once(
        ("ORDER_QUEUED", stable_id),
        "ORDER_QUEUED",
        severity="INFO",
        result="COMPLETED",
        source="execution_queue_commit_service.commit_execution_queue_manually",
        template_args={"stock_name": identity["stock_name"] or identity["stock_code"]},
        **_target_fields(identity),
    )


@_fail_open_observer
def observe_send_order_result(order: Any, result: Any) -> dict[str, Any]:
    """Summarize a durable SendOrder call result, not Broker acceptance."""

    value = _dict(result)
    if value.get("queue_result_recorded") is not True:
        return {"appended": False, "skipped": True, "reason": "SEND_RESULT_NOT_RECORDED"}
    status = _text(value.get("status")).upper()
    event_contract = {
        "SEND_CALL_ACCEPTED": ("SEND_ORDER_REQUEST_ACCEPTED", "NOTICE", "ACCEPTED"),
        "SEND_CALL_REJECTED": ("SEND_ORDER_REQUEST_REJECTED", "WARNING", "REJECTED"),
        "SEND_CALL_UNCERTAIN": ("SEND_ORDER_RESULT_UNCERTAIN", "WARNING", "UNCERTAIN"),
        "SEND_UNCERTAIN": ("SEND_ORDER_RESULT_UNCERTAIN", "WARNING", "UNCERTAIN"),
    }.get(status)
    if event_contract is None:
        return {"appended": False, "skipped": True, "reason": "SEND_RESULT_NOT_FINAL"}
    identity = _identity(order, value)
    record_result = _dict(value.get("record_result"))
    stable_id = _first(
        value.get("send_order_attempt_id"), record_result.get("send_order_attempt_id"),
        identity["execution_id"], identity["order_id"],
    )
    if not stable_id:
        return {"appended": False, "skipped": True, "reason": "SEND_RESULT_IDENTITY_MISSING"}
    event_type, severity, journal_result = event_contract
    return _append_once(
        (event_type, stable_id),
        event_type,
        severity=severity,
        result=journal_result,
        source="kiwoom_send_order_executor.execute_claimed_send_order",
        template_args={"stock_name": identity["stock_name"] or identity["stock_code"]},
        details={"broker_return_code": value.get("return_code")},
        **_target_fields(identity),
    )


@_fail_open_observer
def observe_broker_chejan_result(record_result: Any, normalized_event: Any) -> dict[str, Any]:
    """Summarize only a newly committed normalized Broker lifecycle event."""

    recorded = _dict(record_result)
    event = _dict(normalized_event)
    if recorded.get("recorded") is not True or recorded.get("post_write_verified") is not True:
        return {"appended": False, "skipped": True, "reason": "CHEJAN_NOT_RECORDED"}
    normalized_type = _text(event.get("event_type")).upper()
    event_contract = {
        "ORDER_ACCEPTED": ("BROKER_ORDER_ACCEPTED", "NOTICE", "ACCEPTED"),
        "ORDER_OPEN": ("BROKER_ORDER_ACCEPTED", "NOTICE", "ACCEPTED"),
        "ORDER_REJECTED": ("BROKER_ORDER_REJECTED", "WARNING", "REJECTED"),
        "ORDER_CANCELED": ("ORDER_CANCELLED", "NOTICE", "CANCELLED"),
    }.get(normalized_type)
    if event_contract is None:
        return {"appended": False, "skipped": True, "reason": "CHEJAN_NOT_BROKER_SUMMARY"}
    combined = dict(event)
    combined.update(recorded)
    identity = _identity(combined, recorded)
    broker_order_no = _first(recorded.get("broker_order_no"), event.get("broker_order_no"))
    event_identity = _text(recorded.get("event_identity"))
    stable_id = event_identity or broker_order_no or identity["order_id"]
    if not stable_id:
        return {"appended": False, "skipped": True, "reason": "BROKER_EVENT_IDENTITY_MISSING"}
    event_type, severity, journal_result = event_contract
    dedupe_state = "ACCEPTED" if event_type == "BROKER_ORDER_ACCEPTED" else normalized_type
    return _append_once(
        (event_type, broker_order_no or stable_id, dedupe_state),
        event_type,
        severity=severity,
        result=journal_result,
        source="chejan_event_recorder.record_chejan_event",
        template_args={"stock_name": identity["stock_name"] or identity["stock_code"]},
        broker_order_no=broker_order_no or None,
        details={"normalized_event_type": normalized_type, "order_status": _text(event.get("order_status"))},
        **_target_fields(identity),
    )


@_fail_open_observer
def observe_execution_fill(fill_result: Any) -> dict[str, Any]:
    """Summarize a fill only after the canonical fills append is verified."""

    value = _dict(fill_result)
    fill = _dict(value.get("fill_record"))
    if value.get("fill_recorded") is not True or value.get("post_write_verified") is not True:
        return {"appended": False, "skipped": True, "reason": "FILL_NOT_RECORDED"}
    event_type = _text(value.get("event_type")).upper()
    if event_type not in {"PARTIAL_FILL", "FULL_FILL"}:
        return {"appended": False, "skipped": True, "reason": "FILL_EVENT_UNSUPPORTED"}
    identity = _identity(fill, value)
    fill_id = _text(value.get("fill_id"))
    if not fill_id:
        return {"appended": False, "skipped": True, "reason": "FILL_IDENTITY_MISSING"}
    filled_qty = fill.get("filled_quantity")
    order_qty = fill.get("order_quantity")
    template_args = {"stock_name": identity["stock_name"] or identity["stock_code"]}
    if event_type == "PARTIAL_FILL":
        template_args["filled_qty"] = filled_qty
        if order_qty not in (None, ""):
            template_args["order_qty"] = order_qty
    return _append_once(
        (event_type, fill_id),
        event_type,
        severity="NOTICE",
        source="execution_fill_recorder.record_execution_fill",
        template_args=template_args,
        broker_order_no=_text(fill.get("broker_order_no")) or None,
        details={
            "side": _text(fill.get("side")) or None,
            "filled_quantity": filled_qty,
            "order_quantity": order_qty,
            "remaining_quantity": fill.get("remaining_quantity"),
            "quantity_semantics": "CUMULATIVE",
        },
        **_target_fields(identity),
    )


@_fail_open_observer
def observe_close_started(
    close_result: Any,
    *,
    stock_name: str = "",
    command_id: str = "",
    requested_at: str = "",
) -> dict[str, Any]:
    """Record one durable AUTO_CLOSE or EARLY_CLOSE transition."""

    value = _dict(close_result)
    intent = _text(value.get("intent")).upper()
    if value.get("durable_applied") is not True or value.get("read_back_verified") is not True:
        return {"appended": False, "skipped": True, "reason": "CLOSE_NOT_DURABLY_APPLIED"}
    event_type = {"AUTO_CLOSE": "AUTO_CLOSE_STARTED", "EARLY_CLOSE": "EARLY_CLOSE_STARTED"}.get(intent)
    if not event_type:
        return {"appended": False, "skipped": True, "reason": "CLOSE_INTENT_UNSUPPORTED"}
    code = _text(value.get("stock_code"))
    command_result = getattr(value.get("command_result"), "command_id", "")
    stable_id = _first(command_id, command_result, requested_at, code)
    target = _text(stock_name) or code or "운영 대상"
    return _append_once(
        (event_type, code, stable_id),
        event_type,
        severity="INFO",
        source="close_intent_service.apply_close_intent",
        template_args={"target": target},
        target_type="STOCK" if code else "OPERATION",
        target_id=code or None,
        target_name=target,
        stock_code=code or None,
        stock_name=_text(stock_name) or None,
        command_id=_first(command_id, command_result) or None,
    )


def _stock_metadata(stock_path: str) -> tuple[str, str]:
    try:
        data = json.loads((Path(stock_path) / "config.json").read_text(encoding="utf-8"))
    except Exception:
        return "", ""
    if not isinstance(data, dict):
        return "", ""
    return _first(data.get("name"), data.get("stock_name")), _first(
        data.get("routine_name"), data.get("assigned_routine_instance_id")
    )


@_fail_open_observer
def observe_liquidation_requested(request: Any, command_result: Any) -> list[dict[str, Any]]:
    """Record only newly persisted one-shot liquidation REQUESTED commands."""

    command = _text(getattr(request, "command", "")).upper()
    if command not in {"IMMEDIATE_LIQUIDATION", "INDIVIDUAL_LIQUIDATION", "MANUAL_ATS_LIQUIDATION"}:
        return []
    command_id = _text(getattr(command_result, "command_id", ""))
    outputs = []
    for item in tuple(getattr(command_result, "applied", ()) or ()):
        code = _text(getattr(item, "stock_id", ""))
        stock_path = _text(getattr(item, "stock_path", ""))
        name, routine = _stock_metadata(stock_path)
        display = name or code
        outputs.append(
            _append_once(
                ("LIQUIDATION_REQUESTED", command_id, code),
                "LIQUIDATION_REQUESTED",
                severity="NOTICE",
                result="REQUESTED",
                source="operation_command_service.OperationCommandService.apply",
                template_args={"stock_name": display},
                target_type="STOCK",
                target_id=code,
                target_name=display,
                stock_code=code,
                stock_name=name or None,
                routine=routine or None,
                command_id=command_id or None,
                details={"command": command},
            )
        )
    return outputs


@_fail_open_observer
def observe_manual_ats_liquidation_outcome(
    *,
    command_id: str,
    stock_code: str,
    stock_name: str,
    result: str,
    details: dict[str, Any],
    reason_code: str = "",
    occurred_at: str | None = None,
) -> dict[str, Any]:
    """Record one durable final ATS liquidation outcome per command id."""

    clean_command_id = _text(command_id)
    clean_code = _text(stock_code)
    clean_name = _text(stock_name) or clean_code
    if not clean_command_id or not clean_code:
        return {
            "appended": False,
            "skipped": True,
            "reason": "ATS_LIQUIDATION_IDENTITY_MISSING",
        }
    normalized_result = _text(result).upper()
    severity = "ERROR" if normalized_result == "FAILED" else "WARNING" if normalized_result == "BLOCKED" else "NOTICE"
    return append_production_event(
        "MANUAL_ATS_LIQUIDATION",
        event_id=f"manual-ats-liquidation:{clean_command_id}",
        severity=severity,
        result=normalized_result,
        source="gui_auto_trade_ats_ops",
        occurred_at=occurred_at,
        template_args={"stock_name": clean_name},
        target_type="STOCK",
        target_id=clean_code,
        target_name=clean_name,
        stock_code=clean_code,
        stock_name=clean_name,
        command_id=clean_command_id,
        reason_code=_text(reason_code) or None,
        details=dict(details),
    )


@_fail_open_observer
def observe_liquidation_completed(completion_result: Any) -> list[dict[str, Any]]:
    """Record completion only after the canonical NORMAL_ENDED write succeeds."""

    value = _dict(completion_result)
    evaluated = _dict(value.get("evaluator_result"))
    if value.get("normal_ended_applied") is not True or evaluated.get("global_complete") is not True:
        return []
    operation_date = _text(evaluated.get("operation_date"))
    outputs = []
    for item in evaluated.get("stock_results") or []:
        stock = _dict(item)
        if _text(stock.get("status")).upper() != "DONE":
            continue
        code = _text(stock.get("stock_code"))
        name, routine = _stock_metadata(_text(_dict(stock.get("evidence")).get("stock_dir")))
        display = name or code
        outputs.append(
            _append_once(
                ("LIQUIDATION_COMPLETED", operation_date, code),
                "LIQUIDATION_COMPLETED",
                severity="NOTICE",
                result="COMPLETED",
                source="operation_close_completion_check_service.check_global_close_completion_after_durable_update",
                template_args={"stock_name": display},
                target_type="STOCK",
                target_id=code,
                target_name=display,
                stock_code=code,
                stock_name=name or None,
                routine=routine or None,
                details={"close_mode": _text(stock.get("close_mode")), "completion_status": _text(stock.get("status"))},
            )
        )
    return outputs


@_fail_open_observer
def observe_pnl_cycle_boundaries(boundary_results: Any) -> list[dict[str, Any]]:
    """Record newly durable stock PnL-cycle boundaries."""
    outputs = []
    for result in boundary_results if isinstance(boundary_results, list) else []:
        value = _dict(result)
        boundary = _dict(value.get("boundary"))
        if value.get("written") is not True or not _text(boundary.get("boundary_id")):
            continue
        code = _text(boundary.get("stock_code"))
        boundary_id = _text(boundary.get("boundary_id"))
        outputs.append(_append_once(
            ("PNL_CYCLE_BOUNDARY_CREATED", boundary_id),
            "PNL_CYCLE_BOUNDARY_CREATED",
            severity="NOTICE",
            result="COMPLETED",
            source="confirmable_pnl_cycle_service.record_completion_boundaries",
            target_type="STOCK",
            target_id=code,
            target_name=code,
            stock_code=code,
            details={
                "boundary_id": boundary_id,
                "reason": _text(boundary.get("boundary_reason")),
                "completion_evidence_id": _text(boundary.get("completion_evidence_id")),
                "boundary_at": _text(boundary.get("boundary_at")),
            },
        ))
    return outputs


def reset_trade_event_dedupe_for_tests() -> None:
    _SEEN_EVENT_KEYS.clear()
