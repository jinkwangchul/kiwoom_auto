# -*- coding: utf-8 -*-
"""Pure read model for actual fills and persisted execution processes.

The helpers in this module do not read or write files.  They only join ledger
records supplied by the chart projection so historical provenance is never
reconstructed from current rules or GUI state.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from execution_provenance_contract import option_snapshot_hash, validate_process_record


PROVENANCE_COMPLETE = "COMPLETE"
PROVENANCE_PARTIAL = "PARTIAL"
PROVENANCE_LEGACY_MISSING = "LEGACY_MISSING"
PROVENANCE_AMBIGUOUS = "AMBIGUOUS"
PROVENANCE_INVALID = "INVALID"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _decimal(value: Any) -> Decimal | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def _positive_int(value: Any) -> int | None:
    number = _decimal(value)
    if number is None or number <= 0 or number != number.to_integral_value():
        return None
    return int(number)


def _positive_number(value: Any) -> int | float | None:
    number = _decimal(value)
    if number is None or number <= 0:
        return None
    return int(number) if number == number.to_integral_value() else float(number)


def _datetime(value: Any) -> datetime | None:
    text = _text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _record_code(record: Any) -> str:
    item = _as_dict(record)
    request = _as_dict(item.get("execution_request"))
    request_preview = _as_dict(request.get("request_preview"))
    intent = _as_dict(item.get("execution_intent") or request.get("execution_intent"))
    return _text(
        item.get("code")
        or item.get("stock_code")
        or request.get("code")
        or request.get("stock_code")
        or request_preview.get("code")
        or request_preview.get("stock_code")
        or intent.get("code")
        or intent.get("stock_code")
    ).lstrip("A")


def _record_trade_date(record: Any) -> str:
    item = _as_dict(record)
    request = _as_dict(item.get("execution_request"))
    return _text(item.get("execution_trade_date") or request.get("execution_trade_date"))


def _queue_quantity(record: Any) -> int | None:
    item = _as_dict(record)
    request = _as_dict(item.get("execution_request"))
    request_preview = _as_dict(request.get("request_preview"))
    child_plan = _as_dict(item.get("child_plan") or request.get("child_plan"))
    return _positive_int(
        request_preview.get("quantity")
        or child_plan.get("planned_quantity")
        or item.get("order_quantity")
    )


def _queue_price(record: Any) -> int | float | None:
    item = _as_dict(record)
    request = _as_dict(item.get("execution_request"))
    request_preview = _as_dict(request.get("request_preview"))
    child_plan = _as_dict(item.get("child_plan") or request.get("child_plan"))
    return _positive_number(
        request_preview.get("price")
        or child_plan.get("planned_price")
        or item.get("order_price")
    )


def _unit_text(value: Any) -> str:
    return {
        "MINUTE": "분",
        "MINUTES": "분",
        "SECOND": "초",
        "SECONDS": "초",
        "TICK": "틱",
        "TICKS": "틱",
        "PERCENT": "%",
    }.get(_text(value).upper(), _text(value))


def option_snapshot_summary(option_snapshot: Any) -> str:
    """Return a compact summary using only persisted snapshot fields."""
    snapshot = _as_dict(option_snapshot)
    if not snapshot:
        return ""
    mode = _text(snapshot.get("execution_mode")).upper()
    point = _as_dict(snapshot.get("point"))
    hoga = _as_dict(snapshot.get("hoga"))
    ratio = _as_dict(snapshot.get("ratio"))

    point_count = _positive_int(point.get("point_count"))
    if mode == "MULTI_TIME":
        value = point.get("point_value", point.get("point_range"))
        unit = _unit_text(point.get("point_unit"))
        count_text = f" {point_count}회" if point_count is not None else ""
        range_text = ""
        if value not in (None, ""):
            range_text = f" {value}{unit} 이내"
        return f"다중시간{range_text}{count_text}".strip()

    hoga_mode = _text(hoga.get("hoga_mode")).upper()
    if mode == "MULTI_HOGA" or "MULTI" in hoga_mode:
        up = hoga.get("hoga_up")
        down = hoga.get("hoga_down")
        if up not in (None, "") or down not in (None, ""):
            return f"다중호가 ↑{up if up not in (None, '') else 0} / ↓{down if down not in (None, '') else 0}"
        return "다중호가"

    ratio_count = _positive_int(ratio.get("ratio_count"))
    if ratio and mode == "MULTI_RATIO":
        left = ratio.get("ratio_left")
        right = ratio.get("ratio_right")
        relation = (
            f" {left}:{right}"
            if left not in (None, "") and right not in (None, "")
            else ""
        )
        count_text = f" {ratio_count}회" if ratio_count is not None else ""
        return f"다중비율{relation}{count_text}".strip()

    return "단일 주문"


def _fill_time_evidence(fill: dict[str, Any]) -> tuple[str, str, str]:
    """Return occurred_at, source, quality without using recorded_at."""
    source = _text(fill.get("execution_time_source")).upper()
    quality = _text(fill.get("execution_time_quality")).upper()
    broker_text = _text(fill.get("broker_execution_datetime"))
    received_text = _text(fill.get("received_at"))
    broker_time = _datetime(broker_text)
    received_time = _datetime(received_text)

    if source == "BROKER_FID_908":
        if broker_time is None:
            return "", source, quality or "UNAVAILABLE"
        return broker_time.isoformat(), source, quality or "EXACT"
    if source == "LOCAL_RECEIVED_AT":
        if received_time is None:
            return "", source, quality or "UNAVAILABLE"
        return received_time.isoformat(), source, quality or "APPROXIMATE"
    if source in {"NONE", "UNAVAILABLE"}:
        return "", source or "NONE", quality or "UNAVAILABLE"

    # Legacy records had no named time contract.  A persisted broker datetime is
    # still exact evidence; otherwise received_at is explicitly approximate.
    if broker_time is not None:
        return broker_time.isoformat(), "BROKER_FID_908", quality or "EXACT"
    if received_time is not None:
        return received_time.isoformat(), "LOCAL_RECEIVED_AT", "APPROXIMATE"
    return "", "NONE", "UNAVAILABLE"


def _fill_order_key(fill: dict[str, Any]) -> str:
    identity = _text(
        fill.get("broker_order_no")
        or fill.get("order_id")
        or fill.get("order_queued_id")
        or fill.get("execution_id")
    )
    return "|".join(
        (
            _text(fill.get("account_no")),
            _text(fill.get("code")).lstrip("A"),
            _text(fill.get("side")).upper(),
            identity,
        )
    )


def _group_by(records: list[dict[str, Any]], field: str) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        key = _text(record.get(field))
        if key:
            grouped.setdefault(key, []).append(record)
    return grouped


def _matching_queue(
    queue_by_execution: dict[str, list[dict[str, Any]]],
    execution_id: str,
) -> tuple[dict[str, Any] | None, bool]:
    matches = queue_by_execution.get(execution_id, [])
    return (deepcopy(matches[0]), False) if len(matches) == 1 else (None, len(matches) > 1)


def _provenance_join(
    fill: dict[str, Any],
    executions_by_id: dict[str, list[dict[str, Any]]],
    processes_by_id: dict[str, list[dict[str, Any]]],
    queue_by_execution: dict[str, list[dict[str, Any]]],
) -> tuple[str, dict[str, Any], dict[str, Any], dict[str, Any]]:
    execution_id = _text(fill.get("execution_id"))
    execution_matches = executions_by_id.get(execution_id, []) if execution_id else []
    if len(execution_matches) > 1:
        return PROVENANCE_AMBIGUOUS, {}, {}, {}
    execution = deepcopy(execution_matches[0]) if execution_matches else {}
    queue_record, queue_ambiguous = _matching_queue(queue_by_execution, execution_id)
    if queue_ambiguous:
        return PROVENANCE_AMBIGUOUS, execution, {}, {}
    queue_record = queue_record or {}

    fill_process_id = _text(fill.get("execution_process_id"))
    execution_process_id = _text(execution.get("execution_process_id"))
    queue_process_id = _text(queue_record.get("execution_process_id"))
    process_ids = {
        value for value in (fill_process_id, execution_process_id, queue_process_id) if value
    }
    if len(process_ids) > 1:
        return PROVENANCE_INVALID, execution, {}, queue_record
    process_id = next(iter(process_ids), "")
    if not process_id:
        return PROVENANCE_LEGACY_MISSING, execution, {}, queue_record

    process_matches = processes_by_id.get(process_id, [])
    if len(process_matches) > 1:
        return PROVENANCE_AMBIGUOUS, execution, {}, queue_record
    if len(process_matches) != 1:
        return PROVENANCE_INVALID, execution, {}, queue_record
    process = deepcopy(process_matches[0])
    if validate_process_record(process):
        return PROVENANCE_INVALID, execution, process, queue_record
    expected_hash = _text(process.get("option_snapshot_hash"))
    for reference in (execution, queue_record):
        reference_hash = _text(reference.get("option_snapshot_hash"))
        if reference_hash and reference_hash != expected_hash:
            return PROVENANCE_INVALID, execution, process, queue_record
    if not execution:
        return PROVENANCE_PARTIAL, execution, process, queue_record
    return PROVENANCE_COMPLETE, execution, process, queue_record


def _child_status(
    execution: dict[str, Any],
    queue_record: dict[str, Any],
    filled_quantity: int,
    ordered_quantity: int | None,
) -> str:
    kind = _text(execution.get("child_kind") or queue_record.get("child_kind")).upper()
    queue_status = _text(queue_record.get("status")).upper()
    if kind == "CANCEL":
        return "CANCELLED" if "CANCEL" in queue_status else (queue_status or "CANCEL")
    if ordered_quantity is not None and filled_quantity >= ordered_quantity:
        return "COMPLETED"
    if filled_quantity > 0:
        return "PARTIAL"
    if queue_record.get("send_order_called") is True:
        return "ORDERED"
    return queue_status or _text(execution.get("status")).upper() or "PLANNED"


def project_execution_chart_read_model(
    *,
    fills: Any,
    order_executions: Any,
    queue_records: Any,
    stock_code: str,
    trade_date: str,
) -> dict[str, Any]:
    """Join persisted execution evidence into marker and rail projections."""
    fill_rows = [deepcopy(item) for item in fills if isinstance(item, dict)] if isinstance(fills, list) else []
    execution_root = _as_dict(order_executions)
    execution_rows = execution_root.get("executions", [])
    execution_rows = [deepcopy(item) for item in execution_rows if isinstance(item, dict)] if isinstance(execution_rows, list) else []
    processes_present = "processes" in execution_root
    process_rows = execution_root.get("processes", [])
    process_rows = [deepcopy(item) for item in process_rows if isinstance(item, dict)] if isinstance(process_rows, list) else []
    queues = [deepcopy(item) for item in queue_records if isinstance(item, dict)] if isinstance(queue_records, list) else []

    executions_by_id = _group_by(execution_rows, "execution_id")
    processes_by_id = _group_by(process_rows, "execution_process_id")
    queue_by_execution = _group_by(queues, "execution_id")
    diagnostics: list[dict[str, str]] = []

    selected_fills: list[tuple[int, dict[str, Any], str, str, str]] = []
    normalized_code = _text(stock_code).lstrip("A")
    for ledger_index, fill in enumerate(fill_rows):
        if _text(fill.get("code")).lstrip("A") != normalized_code:
            continue
        occurred_at, time_source, time_quality = _fill_time_evidence(fill)
        broker_time = _datetime(fill.get("broker_execution_datetime"))
        received_time = _datetime(fill.get("received_at"))
        evidence_date = _datetime(occurred_at) or received_time or broker_time
        if evidence_date is not None and evidence_date.date().isoformat() != _text(trade_date):
            continue
        if evidence_date is None:
            diagnostics.append({"fill_id": _text(fill.get("fill_id")), "reason": "FILL_TRADE_DATE_UNAVAILABLE"})
            continue
        selected_fills.append((ledger_index, fill, occurred_at, time_source, time_quality))

    selected_fills.sort(
        key=lambda item: (
            item[2] or _text(item[1].get("received_at")),
            _text(item[1].get("recorded_at")),
            item[0],
        )
    )
    cumulative_by_order: dict[str, int] = {}
    seen_fill_ids: set[str] = set()
    markers: list[dict[str, Any]] = []
    fills_by_execution: dict[str, list[dict[str, Any]]] = {}
    relevant_process_ids: set[str] = set()

    for _ledger_index, fill, occurred_at, time_source, time_quality in selected_fills:
        fill_id = _text(fill.get("fill_id"))
        if not fill_id or fill_id in seen_fill_ids:
            diagnostics.append({"fill_id": fill_id, "reason": "FILL_ID_MISSING_OR_DUPLICATE"})
            continue
        seen_fill_ids.add(fill_id)
        execution_id = _text(fill.get("execution_id"))
        if execution_id:
            fills_by_execution.setdefault(execution_id, []).append(fill)
        quantity = _positive_int(fill.get("filled_quantity"))
        price = _positive_number(fill.get("filled_price"))
        side = _text(fill.get("side")).upper()
        order_key = _fill_order_key(fill)
        if quantity is None or price is None or side not in {"BUY", "SELL"} or not order_key.rsplit("|", 1)[-1]:
            diagnostics.append({"fill_id": fill_id, "reason": "FILL_MARKER_EVIDENCE_INVALID"})
            continue
        previous = cumulative_by_order.get(order_key, 0)
        if quantity < previous:
            diagnostics.append({"fill_id": fill_id, "reason": "FILL_CUMULATIVE_QUANTITY_OUT_OF_ORDER"})
            continue
        cumulative_by_order[order_key] = quantity
        delta = quantity - previous
        if delta <= 0:
            continue

        provenance_status, execution, process, queue_record = _provenance_join(
            fill,
            executions_by_id,
            processes_by_id,
            queue_by_execution,
        )
        process_id = _text(
            fill.get("execution_process_id")
            or execution.get("execution_process_id")
            or queue_record.get("execution_process_id")
        )
        if process_id:
            relevant_process_ids.add(process_id)
        if provenance_status in {PROVENANCE_INVALID, PROVENANCE_AMBIGUOUS}:
            diagnostics.append({"fill_id": fill_id, "reason": f"PROVENANCE_{provenance_status}"})
        if not occurred_at:
            diagnostics.append({"fill_id": fill_id, "reason": "FILL_TIME_UNAVAILABLE"})
            continue

        snapshot = _as_dict(process.get("option_snapshot"))
        source_kind = _text(process.get("source_kind") or snapshot.get("source_kind") or execution.get("source_kind"))
        marker = {
            "marker_id": f"ACTUAL_FILL:{fill_id}",
            "fill_id": fill_id,
            "side": side,
            "filled_quantity_delta": delta,
            "filled_price": price,
            "occurred_at": occurred_at,
            "execution_time_source": time_source,
            "execution_time_quality": time_quality,
            "provenance_status": provenance_status,
            "broker_order_no": _text(fill.get("broker_order_no") or queue_record.get("broker_order_no")),
            "order_id": _text(fill.get("order_id") or execution.get("order_id") or queue_record.get("order_id")),
            "order_queued_id": _text(fill.get("order_queued_id") or queue_record.get("id")),
            "execution_id": execution_id,
            "execution_process_id": process_id,
            "execution_identity_source": _text(fill.get("execution_identity_source")),
            "execution_identity": _text(fill.get("execution_identity")),
            "source_kind": source_kind,
            "source_signal_id": _text(process.get("source_signal_id") or snapshot.get("source_signal_id") or execution.get("source_signal_id")),
            "source_command_id": _text(process.get("source_command_id") or snapshot.get("source_command_id") or execution.get("source_command_id")),
            "child_sequence_index": execution.get("child_sequence_index", queue_record.get("child_sequence_index")),
            "child_sequence_total": execution.get("child_sequence_total", queue_record.get("child_sequence_total")),
            "child_kind": _text(execution.get("child_kind") or queue_record.get("child_kind")),
            "child_plan": deepcopy(_as_dict(execution.get("child_plan") or queue_record.get("child_plan"))),
            "option_snapshot_hash": _text(process.get("option_snapshot_hash") or execution.get("option_snapshot_hash")),
            "option_summary": option_snapshot_summary(snapshot) if provenance_status == PROVENANCE_COMPLETE else "",
            "broker_execution_time_raw": _text(fill.get("broker_execution_time_raw")),
            "received_at": _text(fill.get("received_at")),
            "recorded_at": _text(fill.get("recorded_at")),
        }
        markers.append(marker)

    # A queued child can make a process relevant before any fill exists.  This is
    # rail-only evidence and never creates a price marker.
    for queue_record in queues:
        if _record_code(queue_record) != normalized_code:
            continue
        queue_date = _record_trade_date(queue_record)
        if queue_date and queue_date != _text(trade_date):
            continue
        process_id = _text(queue_record.get("execution_process_id"))
        if process_id:
            relevant_process_ids.add(process_id)

    rails: list[dict[str, Any]] = []
    for process_id in relevant_process_ids:
        process_matches = processes_by_id.get(process_id, [])
        if len(process_matches) != 1:
            diagnostics.append({"execution_process_id": process_id, "reason": "PROCESS_OWNER_AMBIGUOUS_OR_MISSING"})
            continue
        process = process_matches[0]
        if validate_process_record(process):
            diagnostics.append({"execution_process_id": process_id, "reason": "PROCESS_OWNER_INVALID"})
            continue
        snapshot = _as_dict(process.get("option_snapshot"))
        snapshot_hash = _text(process.get("option_snapshot_hash"))
        if snapshot_hash != option_snapshot_hash(snapshot):
            diagnostics.append({"execution_process_id": process_id, "reason": "PROCESS_SNAPSHOT_HASH_MISMATCH"})
            continue
        child_rows = [row for row in execution_rows if _text(row.get("execution_process_id")) == process_id]
        if not child_rows:
            diagnostics.append({"execution_process_id": process_id, "reason": "PROCESS_CHILD_MISSING"})
            continue
        child_rows.sort(
            key=lambda row: (
                _positive_int(row.get("child_sequence_index")) or 0,
                _text(row.get("execution_id")),
            )
        )
        children: list[dict[str, Any]] = []
        invalid_child = False
        started_candidates: list[str] = []
        for execution in child_rows:
            execution_id = _text(execution.get("execution_id"))
            if not execution_id or len(executions_by_id.get(execution_id, [])) != 1:
                invalid_child = True
                break
            if _text(execution.get("option_snapshot_hash")) not in {"", snapshot_hash}:
                invalid_child = True
                break
            queue_record, queue_ambiguous = _matching_queue(queue_by_execution, execution_id)
            if queue_ambiguous:
                invalid_child = True
                break
            queue_record = queue_record or {}
            if queue_record and _text(queue_record.get("execution_process_id")) not in {"", process_id}:
                invalid_child = True
                break
            child_fills = fills_by_execution.get(execution_id, [])
            fill_ids = [_text(fill.get("fill_id")) for fill in child_fills if _text(fill.get("fill_id"))]
            child_markers = [marker for marker in markers if marker.get("execution_id") == execution_id]
            filled_quantity = sum(int(marker["filled_quantity_delta"]) for marker in child_markers)
            ordered_quantity = _queue_quantity(queue_record)
            if ordered_quantity is None:
                ordered_quantity = _positive_int(_as_dict(execution.get("child_plan")).get("planned_quantity"))
            child_plan = deepcopy(_as_dict(execution.get("child_plan") or queue_record.get("child_plan")))
            if not child_plan and queue_record:
                quantity = _queue_quantity(queue_record)
                price = _queue_price(queue_record)
                child_plan = {
                    key: value
                    for key, value in (("planned_quantity", quantity), ("planned_price", price))
                    if value is not None
                }
            latest_marker = child_markers[-1] if child_markers else {}
            planned_at = _text(
                queue_record.get("queued_at")
                or queue_record.get("created_at")
                or queue_record.get("updated_at")
            )
            if planned_at:
                started_candidates.append(planned_at)
            children.append(
                {
                    "execution_id": execution_id,
                    "child_sequence_index": execution.get("child_sequence_index", queue_record.get("child_sequence_index")),
                    "child_sequence_total": execution.get("child_sequence_total", queue_record.get("child_sequence_total")),
                    "child_kind": _text(execution.get("child_kind") or queue_record.get("child_kind")),
                    "child_plan": child_plan,
                    "order_id": _text(execution.get("order_id") or queue_record.get("order_id")),
                    "order_queued_id": _text(queue_record.get("id")),
                    "broker_order_no": _text(queue_record.get("broker_order_no") or latest_marker.get("broker_order_no")),
                    "fill_ids": fill_ids,
                    "ordered_quantity": ordered_quantity,
                    "filled_quantity": filled_quantity,
                    "planned_at": planned_at,
                    "broker_execution_datetime": _text(latest_marker.get("occurred_at")),
                    "execution_time_source": _text(latest_marker.get("execution_time_source")),
                    "status": _child_status(execution, queue_record, filled_quantity, ordered_quantity),
                }
            )
        if invalid_child:
            diagnostics.append({"execution_process_id": process_id, "reason": "PROCESS_CHILD_REFERENCE_INVALID"})
            continue

        completed = sum(1 for child in children if child["status"] in {"COMPLETED", "CANCELLED"})
        if children and completed == len(children):
            status = "COMPLETED"
        elif any(child["status"] == "PARTIAL" for child in children):
            status = "PARTIAL"
        else:
            status = _text(process.get("status")).upper() or "APPROVED"
        rail = {
            "execution_process_id": process_id,
            "source_kind": _text(process.get("source_kind") or snapshot.get("source_kind")),
            "source_signal_id": _text(process.get("source_signal_id") or snapshot.get("source_signal_id")),
            "source_command_id": _text(process.get("source_command_id") or snapshot.get("source_command_id")),
            "routine_id": _text(snapshot.get("routine_id")),
            "routine_instance_id": _text(snapshot.get("routine_instance_id")),
            "side": _text(snapshot.get("side")).upper(),
            "execution_mode": _text(snapshot.get("execution_mode")).upper(),
            "buy_phase": snapshot.get("buy_phase"),
            "buy_round": snapshot.get("buy_round"),
            "option_snapshot_hash": snapshot_hash,
            "option_summary": option_snapshot_summary(snapshot),
            "approved_at": _text(process.get("approved_at") or snapshot.get("approved_at")),
            "started_at": min(started_candidates) if started_candidates else "",
            "status": status,
            "child_total": len(children),
            "child_completed": completed,
            "provenance_status": PROVENANCE_COMPLETE,
            "children": children,
        }
        rails.append(rail)

    rails.sort(key=lambda item: (_text(item.get("started_at") or item.get("approved_at")), _text(item.get("execution_process_id"))))
    markers.sort(key=lambda item: (_text(item.get("occurred_at")), _text(item.get("fill_id"))))
    return {
        "actual_fill_markers": markers,
        "actual_buy_fill_markers": [marker for marker in markers if marker.get("side") == "BUY"],
        "actual_sell_fill_markers": [marker for marker in markers if marker.get("side") == "SELL"],
        "execution_process_rails": rails,
        "diagnostics": diagnostics,
        "processes_present": processes_present,
    }
