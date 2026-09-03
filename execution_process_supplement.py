# -*- coding: utf-8 -*-
"""Read-only post-batch supplement planning for multi-child executions.

This module reads existing Production evidence and returns in-memory proposals.
It never writes Runtime, changes stock state, calls a broker, or dispatches an
order.  Callers must feed safe proposals back through the existing Candidate
and execution pipeline.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from execution_provenance_contract import (
    materialize_execution_intent_children,
    plan_generation,
    stable_hash,
    validate_child_set,
)
from krx_tick_price import move_krx_price_by_ticks


PROJECT_ROOT = Path(__file__).resolve().parent
RUNTIME_DIR = PROJECT_ROOT / "runtime"
ORDER_QUEUE_PATH = RUNTIME_DIR / "order_queue.json"
ORDER_EXECUTIONS_PATH = RUNTIME_DIR / "order_executions.json"
FILLS_PATH = RUNTIME_DIR / "fills.json"
POSITIONS_PATH = RUNTIME_DIR / "positions.json"
BROKER_HOLDINGS_PATH = RUNTIME_DIR / "broker_holdings.json"
ROUTINE_SIGNALS_PATH = RUNTIME_DIR / "routine_signals.json"

_CONFIRMED_LIVE = {"BROKER_ACCEPTED", "PARTIALLY_FILLED"}
_CONFIRMED_FILLED = {"FILLED"}
_CONFIRMED_TERMINAL_FAILURE = {
    "SEND_CALL_REJECTED",
    "BROKER_REJECTED",
    "REJECTED",
    "CANCELLED",
    "PARTIAL_CANCELLED",
}
_WAITING = {
    "PENDING",
    "APPROVED",
    "EXECUTABLE",
    "EXECUTION_ENABLED",
    "REAL_READY",
    "ORDER_QUEUED",
    "DISPATCH_CLAIMED",
    "SEND_ATTEMPTED",
    "SEND_CALL_IN_PROGRESS",
    "SEND_CALL_ACCEPTED",
}
_UNSAFE = {"SEND_UNCERTAIN"}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number < 0 or not number.is_integer():
        return None
    return int(number)


def _positive_int(value: Any) -> int | None:
    number = _nonnegative_int(value)
    return number if number is not None and number > 0 else None


def _read_list(
    path: str | Path,
    field: str,
    *,
    optional: bool = False,
) -> tuple[list[dict[str, Any]], str]:
    target = Path(path)
    try:
        root = json.loads(target.read_text(encoding="utf-8"))
    except Exception as exc:
        return [], f"{field.upper()}_READ_FAILED:{exc}"
    if optional and isinstance(root, dict) and field not in root:
        return [], ""
    if not isinstance(root, dict) or not isinstance(root.get(field), list):
        return [], f"{field.upper()}_SCHEMA_INVALID"
    values = root[field]
    if any(not isinstance(item, dict) for item in values):
        return [], f"{field.upper()}_ITEM_INVALID"
    return [deepcopy(item) for item in values], ""


def _parse_time(value: Any) -> datetime | None:
    text = _text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            return parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed
    except ValueError:
        return None


def _generation(record: dict[str, Any]) -> tuple[int | None, str]:
    request = _as_dict(record.get("execution_request"))
    intent = _as_dict(record.get("execution_intent") or request.get("execution_intent"))
    values = [
        record.get("plan_generation"),
        request.get("plan_generation"),
        intent.get("plan_generation"),
    ]
    generations: set[int] = set()
    for value in values:
        if value in (None, ""):
            continue
        try:
            generations.add(plan_generation(value))
        except ValueError:
            return None, "INVALID_PLAN_GENERATION"
    if len(generations) > 1:
        return None, "PLAN_GENERATION_EVIDENCE_MISMATCH"
    return next(iter(generations), 0), ""


def _intent(record: dict[str, Any]) -> dict[str, Any]:
    request = _as_dict(record.get("execution_request"))
    return deepcopy(_as_dict(record.get("execution_intent") or request.get("execution_intent")))


def _record_quantity(record: dict[str, Any]) -> int | None:
    request = _as_dict(record.get("execution_request"))
    preview = _as_dict(request.get("request_preview"))
    child_plan = _as_dict(record.get("child_plan") or request.get("child_plan"))
    return _positive_int(
        record.get("quantity")
        or preview.get("quantity")
        or child_plan.get("planned_quantity")
    )


def _latest_record(records: list[dict[str, Any]]) -> dict[str, Any]:
    lifecycle = [
        item
        for item in records
        if _text(item.get("source")) == "execution_queue_pending"
        or _text(item.get("status")).upper()
        in (_CONFIRMED_LIVE | _CONFIRMED_FILLED | _CONFIRMED_TERMINAL_FAILURE | _WAITING | _UNSAFE)
        and _text(item.get("id")).startswith("ORDER_QUEUED_")
    ]
    candidates = lifecycle or records
    return max(
        candidates,
        key=lambda item: (
            _parse_time(item.get("updated_at") or item.get("send_call_result_recorded_at"))
            or datetime.min,
            _text(item.get("id")),
        ),
    )


def _max_fill_quantity(fills: list[dict[str, Any]], execution_id: str) -> tuple[int, str]:
    matched = [item for item in fills if _text(item.get("execution_id")) == execution_id]
    quantities: list[int] = []
    for item in matched:
        quantity = _nonnegative_int(item.get("filled_quantity"))
        if quantity is None:
            return 0, "FILL_QUANTITY_INVALID"
        quantities.append(quantity)
    return max(quantities, default=0), ""


def _queue_filled_quantity(record: dict[str, Any]) -> int | None:
    for field in ("total_filled_quantity", "cumulative_filled_quantity"):
        if record.get(field) not in (None, ""):
            return _nonnegative_int(record.get(field))
    return None


def _split_quantity(total_quantity: int, child_count: int) -> list[int]:
    quotient, remainder = divmod(total_quantity, child_count)
    return [quotient + (1 if index < remainder else 0) for index in range(child_count)]


def _build_supplement_intents(
    *,
    template: dict[str, Any],
    source_signal_id: str,
    execution_process_id: str,
    option_snapshot_hash: str,
    quantity: int,
    generation: int,
    snapshot_hash: str,
) -> list[dict[str, Any]]:
    frozen_plan = _as_dict(template.get("multi_hoga_plan"))
    raw_offsets = frozen_plan.get("hoga_offsets")
    if not isinstance(raw_offsets, list) or not raw_offsets:
        raise ValueError("MULTI_HOGA_OFFSETS_MISSING")
    offsets: list[int] = []
    for value in raw_offsets:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("MULTI_HOGA_OFFSET_INVALID")
        offsets.append(value)
    if offsets[0] != 0 or len(offsets) != len(set(offsets)):
        raise ValueError("MULTI_HOGA_OFFSET_ORDER_INVALID")
    if quantity < len(offsets):
        offsets = [0]
    base_price = _positive_int(frozen_plan.get("base_price"))
    if base_price is None:
        raise ValueError("MULTI_HOGA_BASE_PRICE_MISSING")
    instrument_type = frozen_plan.get("instrument_type") or "STOCK"
    prices = [
        move_krx_price_by_ticks(base_price, offset, instrument_type=instrument_type)
        for offset in offsets
    ]
    quantities = _split_quantity(quantity, len(offsets))
    total = len(offsets)
    intents: list[dict[str, Any]] = []
    for index, (offset, child_quantity, price) in enumerate(
        zip(offsets, quantities, prices),
        start=1,
    ):
        intent = deepcopy(template)
        for field in ("execution_id", "provenance_approved_at", "process_record"):
            intent.pop(field, None)
        intent.update(
            {
                "source_signal_id": source_signal_id,
                "execution_process_id": execution_process_id,
                "execution_process_owner_required": False,
                "option_snapshot_hash": option_snapshot_hash,
                "plan_generation": generation,
                "quantity": child_quantity,
                "price": price,
                "hoga": "LIMIT",
                "price_basis": "ORDER_PRICE",
                "child_sequence_index": index,
                "child_sequence_total": total,
                "child_kind": "HOGA_LEVEL",
                "replan_source_snapshot_hash": snapshot_hash,
                "child_plan": {
                    "planned_quantity": child_quantity,
                    "planned_price": price,
                    "hoga_offset_ticks": offset,
                    "plan_generation": generation,
                    "replan_source_snapshot_hash": snapshot_hash,
                },
            }
        )
        intents.append(intent)
    return materialize_execution_intent_children(
        intents,
        source_signal_id=source_signal_id,
        execution_process_id=execution_process_id,
        plan_generation_value=generation,
    )


def inspect_execution_process_supplements(
    *,
    selected_account_no: str,
    allowed_stock_codes: tuple[str, ...] | list[str] | set[str] | None = None,
    blocked_execution_process_ids: tuple[str, ...] | list[str] | set[str] | None = None,
    order_queue_path: str | Path = ORDER_QUEUE_PATH,
    order_executions_path: str | Path = ORDER_EXECUTIONS_PATH,
    fills_path: str | Path = FILLS_PATH,
    positions_path: str | Path = POSITIONS_PATH,
    broker_holdings_path: str | Path = BROKER_HOLDINGS_PATH,
    routine_signals_path: str | Path = ROUTINE_SIGNALS_PATH,
) -> dict[str, Any]:
    """Return safe supplement proposals and per-process review/wait results."""
    paths = (
        (order_queue_path, "orders", False),
        (order_executions_path, "executions", False),
        (order_executions_path, "processes", True),
        (fills_path, "fills", False),
        (positions_path, "positions", False),
        (broker_holdings_path, "holdings", False),
        (routine_signals_path, "signals", False),
    )
    loaded: dict[str, list[dict[str, Any]]] = {}
    errors: list[str] = []
    for path, field, optional in paths:
        values, error = _read_list(path, field, optional=optional)
        loaded[field] = values
        if error:
            errors.append(error)
    if errors:
        return {
            "ok": False,
            "proposals": [],
            "reviews": [],
            "waiting": [],
            "errors": errors,
        }

    orders = loaded["orders"]
    executions = loaded["executions"]
    processes = loaded["processes"]
    fills = loaded["fills"]
    positions = loaded["positions"]
    holdings = loaded["holdings"]
    signals = loaded["signals"]
    allowed = (
        {_text(value) for value in allowed_stock_codes if _text(value)}
        if allowed_stock_codes is not None
        else None
    )
    blocked_processes = {
        _text(value)
        for value in (blocked_execution_process_ids or ())
        if _text(value)
    }
    account_no = _text(selected_account_no)
    if not account_no:
        return {"ok": True, "proposals": [], "reviews": [], "waiting": [], "errors": []}
    if allowed_stock_codes is not None and not allowed:
        return {"ok": True, "proposals": [], "reviews": [], "waiting": [], "errors": []}

    by_process: dict[str, list[dict[str, Any]]] = {}
    for order in orders:
        process_id = _text(order.get("execution_process_id"))
        intent = _intent(order)
        if not process_id or _text(intent.get("execution_mode")).upper() != "MULTI_HOGA":
            continue
        code = _text(order.get("code") or _as_dict(order.get("execution_request")).get("code"))
        if allowed is not None and code and code not in allowed:
            continue
        by_process.setdefault(process_id, []).append(order)

    proposals: list[dict[str, Any]] = []
    reviews: list[dict[str, Any]] = []
    waiting: list[dict[str, Any]] = []
    for process_id, process_orders in by_process.items():
        if process_id in blocked_processes:
            waiting.append(
                {
                    "execution_process_id": process_id,
                    "reason": "EXECUTION_PROCESS_RESET_IN_PROGRESS",
                }
            )
            continue
        reasons: list[str] = []
        generation_records: dict[int, dict[str, dict[str, Any]]] = {}
        source_ids = {_text(item.get("source_signal_id")) for item in process_orders if _text(item.get("source_signal_id"))}
        if len(source_ids) != 1:
            reasons.append("SOURCE_SIGNAL_ID_MISMATCH")
        source_signal_id = next(iter(source_ids), "")
        for order in process_orders:
            generation, generation_error = _generation(order)
            if generation_error or generation is None:
                reasons.append(generation_error or "PLAN_GENERATION_INVALID")
                continue
            execution_id = _text(order.get("execution_id"))
            if not execution_id:
                continue
            generation_records.setdefault(generation, {}).setdefault(execution_id, []).append(order)
        if not generation_records:
            continue
        for generation, records_by_execution in generation_records.items():
            child_issues = validate_child_set(
                [_latest_record(records) for records in records_by_execution.values()]
            )
            reasons.extend(
                f"GENERATION_{generation}_CHILD_SET_INVALID:{issue}"
                for issue in child_issues
            )
        max_generation = max(generation_records)
        latest_records = {
            execution_id: _latest_record(records)
            for execution_id, records in generation_records[max_generation].items()
        }
        all_records = {
            execution_id: _latest_record(records)
            for records_by_execution in generation_records.values()
            for execution_id, records in records_by_execution.items()
        }
        representative = next(iter(process_orders))
        template = _intent(representative)
        code = _text(representative.get("code"))
        name = _text(representative.get("name"))
        routine = _text(representative.get("routine"))
        signal_record = next(
            (item for item in signals if _text(item.get("id")) == source_signal_id),
            {},
        )
        code = code or _text(signal_record.get("code"))
        name = name or _text(signal_record.get("name"))
        routine = routine or _text(signal_record.get("routine"))
        if allowed is not None and code not in allowed:
            continue

        waiting_statuses: list[str] = []
        for execution_id, record in all_records.items():
            status = _text(record.get("status")).upper()
            if (
                status in _UNSAFE
                or record.get("send_uncertain") is True
                or record.get("call_execution_uncertain") is True
                or record.get("manual_reconciliation_required") is True
            ):
                reasons.append(f"UNSAFE_CHILD:{execution_id}:{status or 'UNKNOWN'}")
            elif status in _WAITING or status not in (
                _CONFIRMED_LIVE | _CONFIRMED_FILLED | _CONFIRMED_TERMINAL_FAILURE
            ):
                waiting_statuses.append(f"{execution_id}:{status or 'UNKNOWN'}")
        if waiting_statuses and not reasons:
            waiting.append(
                {
                    "execution_process_id": process_id,
                    "code": code,
                    "reason": "BROKER_EVIDENCE_PENDING",
                    "children": waiting_statuses,
                }
            )
            continue

        runtime_by_execution: dict[str, dict[str, Any]] = {}
        process_owners = [
            item
            for item in processes
            if _text(item.get("execution_process_id")) == process_id
        ]
        if len(process_owners) != 1:
            reasons.append("RUNTIME_PROCESS_OWNER_MISSING_OR_AMBIGUOUS")
        for execution in executions:
            execution_id = _text(execution.get("execution_id"))
            if execution_id in all_records:
                if execution_id in runtime_by_execution:
                    reasons.append(f"RUNTIME_EXECUTION_DUPLICATE:{execution_id}")
                runtime_by_execution[execution_id] = execution
        for execution_id, record in all_records.items():
            runtime = runtime_by_execution.get(execution_id)
            if runtime is None:
                reasons.append(f"RUNTIME_EXECUTION_MISSING:{execution_id}")
                continue
            if _text(runtime.get("execution_process_id")) != process_id:
                reasons.append(f"RUNTIME_PROCESS_MISMATCH:{execution_id}")
            runtime_generation, runtime_error = _generation(runtime)
            record_generation, record_error = _generation(record)
            if runtime_error or record_error or runtime_generation != record_generation:
                reasons.append(f"RUNTIME_GENERATION_MISMATCH:{execution_id}")

        confirmed_filled = 0
        confirmed_live_remaining = 0
        for execution_id, record in all_records.items():
            status = _text(record.get("status")).upper()
            fill_quantity, fill_error = _max_fill_quantity(fills, execution_id)
            if fill_error:
                reasons.append(f"{fill_error}:{execution_id}")
                continue
            for fill in fills:
                if _text(fill.get("execution_id")) == execution_id and _text(
                    fill.get("execution_process_id")
                ) not in {"", process_id}:
                    reasons.append(f"FILL_PROCESS_MISMATCH:{execution_id}")
            queue_filled = _queue_filled_quantity(record)
            if queue_filled is not None and queue_filled != fill_quantity:
                reasons.append(f"QUEUE_FILL_MISMATCH:{execution_id}")
            if status in (_CONFIRMED_FILLED | {"PARTIALLY_FILLED", "PARTIAL_CANCELLED"}):
                if fill_quantity <= 0:
                    reasons.append(f"FILL_EVIDENCE_MISSING:{execution_id}")
                confirmed_filled += fill_quantity
            if status in _CONFIRMED_LIVE:
                remaining = _nonnegative_int(record.get("remaining_quantity"))
                if remaining is None:
                    reasons.append(f"LIVE_REMAINING_QUANTITY_MISSING:{execution_id}")
                else:
                    confirmed_live_remaining += remaining

        planned_total = _positive_int(template.get("planned_total_quantity"))
        if planned_total is None:
            planned_total = _positive_int(_as_dict(template.get("multi_hoga_plan")).get("planned_total_quantity"))
        if planned_total is None:
            reasons.append("PLANNED_TOTAL_QUANTITY_MISSING")

        holding_matches = [
            item
            for item in holdings
            if _text(item.get("account_no")) == account_no and _text(item.get("code")) == code
        ]
        if len(holding_matches) != 1:
            reasons.append("BROKER_HOLDING_MATCH_INVALID")
            holding = {}
        else:
            holding = holding_matches[0]
        latest_sellable = _nonnegative_int(holding.get("available_quantity"))
        holding_quantity = _nonnegative_int(holding.get("holding_quantity"))
        if latest_sellable is None or holding_quantity is None or latest_sellable > holding_quantity:
            reasons.append("BROKER_HOLDING_QUANTITY_INVALID")
        if (
            holding.get("manual_reconciliation_required") is True
            or _text(holding.get("reconciliation_status")).upper() not in {"", "CONSISTENT"}
        ):
            reasons.append("BROKER_HOLDING_RECONCILIATION_REQUIRED")

        position_matches = [
            item
            for item in positions
            if _text(item.get("account_no")) == account_no and _text(item.get("code")) == code
        ]
        position_quantity = 0
        position: dict[str, Any] = {}
        if len(position_matches) > 1:
            reasons.append("POSITION_MATCH_AMBIGUOUS")
        elif position_matches:
            position = position_matches[0]
            position_quantity = _nonnegative_int(position.get("quantity"))
            if position_quantity is None:
                reasons.append("POSITION_QUANTITY_INVALID")
                position_quantity = 0
        if holding_quantity is not None and position_quantity != holding_quantity:
            reasons.append("POSITION_BROKER_HOLDING_MISMATCH")

        process_fills = [
            fill
            for fill in fills
            if _text(fill.get("execution_process_id")) == process_id
        ]
        latest_evidence_time = max(
            [
                _parse_time(
                    record.get("updated_at")
                    or record.get("send_call_result_recorded_at")
                    or record.get("created_at")
                )
                or datetime.min
                for record in all_records.values()
            ]
            + [
                _parse_time(
                    fill.get("recorded_at")
                    or fill.get("received_at")
                    or fill.get("broker_execution_datetime")
                    or fill.get("occurred_at")
                )
                or datetime.min
                for fill in process_fills
            ]
            + [
                _parse_time(position.get("updated_at") or position.get("last_fill_at"))
                or datetime.min
            ],
            default=datetime.min,
        )
        holding_time = _parse_time(holding.get("received_at"))
        if holding_time is None or holding_time < latest_evidence_time:
            if not reasons:
                waiting.append(
                    {
                        "execution_process_id": process_id,
                        "code": code,
                        "reason": "POST_BATCH_HOLDING_EVIDENCE_PENDING",
                    }
                )
                continue
            reasons.append("BROKER_HOLDING_EVIDENCE_STALE")

        snapshot_payload = {
            "execution_process_id": process_id,
            "orders": [
                {
                    "execution_id": execution_id,
                    "generation": _generation(record)[0],
                    "status": record.get("status"),
                    "remaining_quantity": record.get("remaining_quantity"),
                    "total_filled_quantity": record.get("total_filled_quantity"),
                    "updated_at": record.get("updated_at"),
                    "broker_order_no": record.get("broker_order_no"),
                }
                for execution_id, record in sorted(all_records.items())
            ],
            "fills": [
                {
                    "fill_id": fill.get("fill_id"),
                    "execution_id": fill.get("execution_id"),
                    "filled_quantity": fill.get("filled_quantity"),
                    "remaining_quantity": fill.get("remaining_quantity"),
                }
                for fill in process_fills
            ],
            "position_quantity": position_quantity,
            "position_updated_at": position.get("updated_at"),
            "holding_quantity": holding_quantity,
            "latest_sellable_quantity": latest_sellable,
            "holding_received_at": holding.get("received_at"),
        }
        snapshot_hash = stable_hash(snapshot_payload)
        latest_generation_hashes = {
            _text(_intent(record).get("replan_source_snapshot_hash"))
            for record in latest_records.values()
            if _text(_intent(record).get("replan_source_snapshot_hash"))
        }
        if snapshot_hash in latest_generation_hashes:
            waiting.append(
                {
                    "execution_process_id": process_id,
                    "code": code,
                    "reason": "REPLAN_SNAPSHOT_ALREADY_USED",
                }
            )
            continue

        if reasons:
            reviews.append(
                {
                    "execution_process_id": process_id,
                    "source_signal_id": source_signal_id,
                    "code": code,
                    "name": name,
                    "review_reasons": sorted(set(reasons)),
                    "review_location": "MULTI_HOGA_POST_BATCH_RECONCILIATION",
                }
            )
            continue

        assert planned_total is not None and latest_sellable is not None
        candidate_shortfall = max(
            0,
            planned_total - confirmed_filled - confirmed_live_remaining,
        )
        supplement_quantity = min(candidate_shortfall, latest_sellable)
        if supplement_quantity <= 0:
            waiting.append(
                {
                    "execution_process_id": process_id,
                    "code": code,
                    "reason": "NO_SUPPLEMENT_REQUIRED",
                    "candidate_shortfall_quantity": candidate_shortfall,
                    "latest_sellable_quantity": latest_sellable,
                }
            )
            continue
        option_hashes = {
            _text(item.get("option_snapshot_hash"))
            for item in process_orders
            if _text(item.get("option_snapshot_hash"))
        }
        if len(option_hashes) != 1:
            reviews.append(
                {
                    "execution_process_id": process_id,
                    "source_signal_id": source_signal_id,
                    "code": code,
                    "name": name,
                    "review_reasons": ["OPTION_SNAPSHOT_HASH_MISMATCH"],
                    "review_location": "MULTI_HOGA_POST_BATCH_RECONCILIATION",
                }
            )
            continue
        owner_hash = _text(process_owners[0].get("option_snapshot_hash"))
        if owner_hash != next(iter(option_hashes)):
            reviews.append(
                {
                    "execution_process_id": process_id,
                    "source_signal_id": source_signal_id,
                    "code": code,
                    "name": name,
                    "review_reasons": ["PROCESS_OPTION_SNAPSHOT_HASH_MISMATCH"],
                    "review_location": "MULTI_HOGA_POST_BATCH_RECONCILIATION",
                }
            )
            continue
        next_generation = max_generation + 1
        try:
            supplement_intents = _build_supplement_intents(
                template=template,
                source_signal_id=source_signal_id,
                execution_process_id=process_id,
                option_snapshot_hash=next(iter(option_hashes)),
                quantity=supplement_quantity,
                generation=next_generation,
                snapshot_hash=snapshot_hash,
            )
        except ValueError as exc:
            reviews.append(
                {
                    "execution_process_id": process_id,
                    "source_signal_id": source_signal_id,
                    "code": code,
                    "name": name,
                    "review_reasons": [str(exc) or "SUPPLEMENT_PLAN_INVALID"],
                    "review_location": "MULTI_HOGA_POST_BATCH_RECONCILIATION",
                }
            )
            continue
        signal = deepcopy(signal_record) if signal_record else {}
        signal.update(
            {
                "id": source_signal_id,
                "routine": routine,
                "routine_instance_id": template.get("routine_instance_id"),
                "code": code,
                "name": name,
                "signal": "SELL",
                "status": "PENDING",
                "execution_intent": supplement_intents[0],
                "execution_intents": supplement_intents,
            }
        )
        proposals.append(
            {
                "execution_process_id": process_id,
                "source_signal_id": source_signal_id,
                "code": code,
                "name": name,
                "plan_generation": next_generation,
                "planned_total_quantity": planned_total,
                "confirmed_filled_quantity": confirmed_filled,
                "confirmed_live_remaining_quantity": confirmed_live_remaining,
                "candidate_shortfall_quantity": candidate_shortfall,
                "latest_sellable_quantity": latest_sellable,
                "supplement_quantity": supplement_quantity,
                "replan_source_snapshot_hash": snapshot_hash,
                "signal": signal,
                "execution_intents": supplement_intents,
            }
        )

    return {
        "ok": True,
        "proposals": proposals,
        "reviews": reviews,
        "waiting": waiting,
        "errors": [],
    }
