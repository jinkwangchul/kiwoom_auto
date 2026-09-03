# -*- coding: utf-8 -*-
"""Durable BUY repeat-exit inspection.

This module is intentionally read-only.  It proposes generic CANCEL actions
and BUY phase-completion evidence; the existing queue, approval and broker
boundary remain the only mutation/execution paths.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
import json
from pathlib import Path
from typing import Any

from execution_price_comparison import evaluate_percent_comparison, resolve_price_source
from execution_provenance_contract import stable_hash, plan_generation, validate_child_set

PROJECT_ROOT = Path(__file__).resolve().parent
RUNTIME_DIR = PROJECT_ROOT / "runtime"
ORDER_QUEUE_PATH = RUNTIME_DIR / "order_queue.json"
ORDER_EXECUTIONS_PATH = RUNTIME_DIR / "order_executions.json"
FILLS_PATH = RUNTIME_DIR / "fills.json"
POSITIONS_PATH = RUNTIME_DIR / "positions.json"
HOLDINGS_PATH = RUNTIME_DIR / "broker_holdings.json"
SIGNALS_PATH = RUNTIME_DIR / "routine_signals.json"

_OPEN = {"BROKER_ACCEPTED", "PARTIALLY_FILLED"}
_PRE_DISPATCH = {
    "ORDER_QUEUED", "APPROVED", "EXECUTABLE", "DISPATCH_CLAIMED",
    "SEND_ATTEMPTED", "SEND_CALL_IN_PROGRESS", "SEND_CALL_ACCEPTED",
}
_ACTIVE = _OPEN | _PRE_DISPATCH
_SUCCESS_TERMINAL = {"FILLED", "CANCELLED", "CANCELED", "PARTIAL_CANCELLED"}
_FAILURE_TERMINAL = {"BROKER_REJECTED", "SEND_CALL_REJECTED", "REJECTED", "FAILED"}
_ACTIVE_CANCEL = _ACTIVE | {"SEND_UNCERTAIN"}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return int(number) if number >= 0 and number.is_integer() else None


def _price(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _time(value: Any) -> datetime | None:
    text = _text(value)
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _latest(records: list[dict[str, Any]]) -> dict[str, Any]:
    return max(
        records,
        key=lambda item: (
            _time(item.get("updated_at") or item.get("created_at")) or datetime.min,
            _text(item.get("id") or item.get("execution_id")),
        ),
    )


def _read(path: str | Path, field: str, optional: bool = False) -> tuple[list[dict[str, Any]], str]:
    target = Path(path)
    if optional and not target.exists():
        return [], ""
    try:
        root = json.loads(target.read_text(encoding="utf-8"))
    except Exception as exc:
        return [], f"{field.upper()}_READ_FAILED:{exc}"
    values = root.get(field) if isinstance(root, dict) else None
    if not isinstance(values, list) or any(not isinstance(item, dict) for item in values):
        return [], f"{field.upper()}_SCHEMA_INVALID"
    return values, ""


def _intent(record: dict[str, Any]) -> dict[str, Any]:
    direct = _dict(record.get("execution_intent"))
    return direct or _dict(_dict(record.get("execution_request")).get("execution_intent"))


def _preview(record: dict[str, Any]) -> dict[str, Any]:
    return _dict(_dict(record.get("execution_request")).get("request_preview"))


def _action(record: dict[str, Any]) -> str:
    return _text(record.get("order_action") or _preview(record).get("order_action") or "NEW").upper()


def _remaining(record: dict[str, Any]) -> int:
    for key in ("remaining_quantity", "unfilled_quantity", "quantity"):
        value = _int(record.get(key))
        if value is not None:
            return value
    return 0


def _original_no(record: dict[str, Any]) -> str:
    return _text(record.get("original_order_no") or _preview(record).get("original_order_no"))


def _field_value(record: dict[str, Any], field: str) -> str:
    return _text(record.get(field) or _intent(record).get(field) or _preview(record).get(field))


def _review(*, process_id: str, signal_id: str, code: str, name: str,
            reasons: list[str]) -> dict[str, Any]:
    return {
        "execution_process_id": process_id,
        "source_signal_id": signal_id,
        "code": code,
        "name": name,
        "review_reasons": list(dict.fromkeys(reasons)),
        "review_location": "BUY_EXIT_RECONCILIATION",
    }


def _signal_intents(signal: dict[str, Any], process_id: str) -> list[dict[str, Any]]:
    values = [item for item in signal.get("execution_intents", []) if isinstance(item, dict)]
    direct = _dict(signal.get("execution_intent"))
    if direct and direct not in values:
        values.append(direct)
    return [item for item in values if _text(item.get("execution_process_id")) == process_id]


def _buy_exit_policy(intent: dict[str, Any]) -> dict[str, Any]:
    policy = _dict(intent.get("buy_exit_policy"))
    if policy.get("policy") == "BUY_REPEAT_EXIT" and policy.get("enabled") is True \
            and str(policy.get("logic") or "").upper() == "OR" \
            and isinstance(policy.get("conditions"), list) and policy.get("conditions"):
        return policy
    return {}


def _anchor(orders: list[dict[str, Any]], signal: dict[str, Any], process_id: str) -> datetime | None:
    """Recover the first repeat-round anchor; reset generation is irrelevant."""
    repeat_records: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for item in orders:
        intent = _intent(item)
        round_value = _int(intent.get("buy_round"))
        if round_value is not None and round_value >= 2:
            repeat_records.append((item, intent))
    for intent in _signal_intents(signal, process_id):
        round_value = _int(intent.get("buy_round"))
        if round_value is not None and round_value >= 2:
            repeat_records.append((intent, intent))

    explicit = [
        parsed
        for item, intent in repeat_records
        for parsed in [_time(intent.get("buy_repeat_started_at") or intent.get("repeat_started_at"))]
        if parsed is not None
    ]
    if explicit:
        return min(explicit)

    created = [
        parsed
        for item, intent in repeat_records
        for parsed in [_time(
            intent.get("created_at") or item.get("created_at")
            or item.get("send_call_result_recorded_at") or item.get("updated_at")
        )]
        if parsed is not None
    ]
    return min(created) if created else None


def _completed_repeat_rounds(
    orders: list[dict[str, Any]],
    fills: list[dict[str, Any]],
    process_id: str,
) -> set[int]:
    """Count filled repeat rounds, never child count, reset generation, or rejects."""
    latest_by_execution: dict[str, dict[str, Any]] = {}
    for item in orders:
        execution_id = _field_value(item, "execution_id")
        if execution_id:
            latest_by_execution[execution_id] = (
                _latest([latest_by_execution[execution_id], item])
                if execution_id in latest_by_execution else item
            )
    fills_by_execution: dict[str, list[dict[str, Any]]] = {}
    for fill in fills:
        if _text(fill.get("execution_process_id")) != process_id:
            continue
        execution_id = _text(fill.get("execution_id"))
        if execution_id:
            fills_by_execution.setdefault(execution_id, []).append(fill)

    grouped: dict[int, list[tuple[str, dict[str, Any]]]] = {}
    for execution_id, order in latest_by_execution.items():
        round_value = _int(_intent(order).get("buy_round"))
        if round_value is not None and round_value >= 2:
            grouped.setdefault(round_value, []).append((execution_id, order))

    completed: set[int] = set()
    for round_value, children in grouped.items():
        if not all(
            _text(order.get("status")).upper() in _SUCCESS_TERMINAL
            and _remaining(order) == 0
            for _, order in children
        ):
            continue
        has_fill = any(
            any((_int(fill.get("filled_quantity")) or 0) > 0 for fill in fills_by_execution.get(execution_id, []))
            for execution_id, _ in children
        )
        if has_fill:
            completed.add(round_value)
    return completed


def evaluate_buy_exit_policy(
    *, policy: dict[str, Any], completed_repeat_count: int,
    repeat_started_at: datetime | None, order_price: float | None,
    current_price: float | None, average_price: float | None,
    now: datetime, timeframe_minutes: int | None = None,
) -> dict[str, Any]:
    """Evaluate enabled BUY exit conditions using OR semantics."""
    evaluated: list[dict[str, Any]] = []
    matched: list[str] = []
    waiting: list[str] = []
    for raw in policy.get("conditions") or []:
        condition = deepcopy(raw) if isinstance(raw, dict) else {}
        kind = _text(condition.get("condition_type")).upper()
        result: dict[str, Any] = {"condition_type": kind, "matched": False, "status": "READY"}
        if kind == "COUNT":
            target = _int(condition.get("target_repeat_generations"))
            if target is None or target <= 0:
                result.update(status="INVALID", reason="BUY_REPEAT_EXIT_COUNT_INVALID")
                waiting.append("BUY_REPEAT_EXIT_COUNT_INVALID")
            else:
                result.update(target_repeat_generations=target, completed_repeat_generations=completed_repeat_count,
                              matched=completed_repeat_count >= target,
                              reason="MATCHED" if completed_repeat_count >= target else "NOT_MATCHED")
        elif kind == "TIME":
            value = _price(condition.get("configured_value"))
            unit = _text(condition.get("configured_unit")).upper()
            duration_ms = _int(condition.get("duration_ms"))
            if unit == "BAR":
                if timeframe_minutes is None or timeframe_minutes <= 0 or value is None:
                    result.update(status="WAITING", reason="BUY_REPEAT_EXIT_TIMEFRAME_UNAVAILABLE")
                    waiting.append("BUY_REPEAT_EXIT_TIMEFRAME_UNAVAILABLE")
                    evaluated.append(result)
                    continue
                else:
                    duration_ms = int(value * timeframe_minutes * 60_000)
            if duration_ms is None or duration_ms <= 0:
                result.update(status="INVALID", reason="BUY_REPEAT_EXIT_TIME_INVALID")
                waiting.append("BUY_REPEAT_EXIT_TIME_INVALID")
            elif repeat_started_at is None:
                result.update(status="NOT_STARTED", duration_ms=duration_ms, reason="FIRST_REPEAT_GENERATION_NOT_STARTED")
                waiting.append("FIRST_REPEAT_GENERATION_NOT_STARTED")
            else:
                due = repeat_started_at + timedelta(milliseconds=duration_ms)
                hit = now >= due
                result.update(duration_ms=duration_ms, anchor_at=repeat_started_at.isoformat(timespec="milliseconds"),
                              due_at=due.isoformat(timespec="milliseconds"), evaluated_at=now.isoformat(timespec="milliseconds"),
                              matched=hit, reason="MATCHED" if hit else "NOT_MATCHED")
        elif kind == "PRICE":
            left_source = _text(condition.get("left_source")).upper()
            right_source = _text(condition.get("right_source")).upper()
            if "CURRENT_PRICE" in {left_source, right_source} and current_price is None:
                result.update(status="WAITING", reason="BUY_REPEAT_EXIT_CURRENT_PRICE_UNAVAILABLE")
                waiting.append("BUY_REPEAT_EXIT_CURRENT_PRICE_UNAVAILABLE")
            else:
                left = resolve_price_source(left_source, order_price=order_price, current_price=current_price, average_price=average_price)
                right = resolve_price_source(right_source, order_price=order_price, current_price=current_price, average_price=average_price)
                hit, observed = evaluate_percent_comparison(left=right, right=left,
                    direction=_text(condition.get("direction")).upper(), compare=_text(condition.get("compare")).upper(),
                    threshold=condition.get("threshold_percent")) if left is not None and right is not None else (None, None)
                if hit is None or observed is None:
                    result.update(status="WAITING", reason="BUY_REPEAT_EXIT_PRICE_SOURCE_UNAVAILABLE")
                    waiting.append("BUY_REPEAT_EXIT_PRICE_SOURCE_UNAVAILABLE")
                else:
                    result.update(left_source=left_source, right_source=right_source, left_value=left, right_value=right,
                                  observed_percent=observed, matched=hit, reason="MATCHED" if hit else "NOT_MATCHED")
        else:
            result.update(status="INVALID", reason="BUY_REPEAT_EXIT_CONDITION_TYPE_INVALID")
            waiting.append("BUY_REPEAT_EXIT_CONDITION_TYPE_INVALID")
        if result.get("matched") is True:
            matched.append(kind)
        evaluated.append(result)
    payload = {"logic": "OR", "completed_repeat_generations": completed_repeat_count,
               "repeat_started_at": repeat_started_at.isoformat(timespec="milliseconds") if repeat_started_at else None,
               "order_price": order_price, "current_price": current_price, "average_price": average_price,
               "conditions": evaluated, "matched_condition_types": matched}
    return {**payload, "active": bool(evaluated), "triggered": bool(matched),
            "waiting_reasons": sorted(set(waiting)), "snapshot_hash": stable_hash(payload)}


def inspect_buy_repeat_exits(*, selected_account_no: str, actionable_prices_by_code: dict[str, Any] | None = None,
                             allowed_stock_codes: tuple[str, ...] | list[str] | set[str] | None = None,
                             blocked_execution_process_ids: tuple[str, ...] | list[str] | set[str] | None = None,
                             now: datetime | None = None, proposal_limit: int = 5,
                             order_queue_path: str | Path = ORDER_QUEUE_PATH,
                             order_executions_path: str | Path = ORDER_EXECUTIONS_PATH,
                             fills_path: str | Path = FILLS_PATH, positions_path: str | Path = POSITIONS_PATH,
                             holdings_path: str | Path = HOLDINGS_PATH, signals_path: str | Path = SIGNALS_PATH) -> dict[str, Any]:
    fields = [(order_queue_path, "orders", False), (order_executions_path, "executions", False),
              (order_executions_path, "processes", False), (fills_path, "fills", False),
              (positions_path, "positions", False), (holdings_path, "holdings", False),
              (signals_path, "signals", False)]
    loaded: dict[str, list[dict[str, Any]]] = {}; errors: list[str] = []
    for path, field, optional in fields:
        loaded[field], error = _read(path, field, optional)
        if error: errors.append(error)
    result = {"ok": not errors, "cancel_proposals": [], "completion_proposals": [], "reviews": [], "waiting": [], "errors": errors, "blocked_execution_process_ids": []}
    if errors:
        # If Queue remains readable, preserve process-level blocking even when
        # another authoritative ledger cannot be inspected.  Lower-priority
        # reset/slice/repeat paths must not advance those BUY processes.
        result["blocked_execution_process_ids"] = sorted({
            _text(order.get("execution_process_id") or _intent(order).get("execution_process_id"))
            for order in loaded.get("orders", [])
            if _action(order) in {"NEW", "MODIFY"}
            and _text(_intent(order).get("side") or order.get("side")).upper() == "BUY"
            and _buy_exit_policy(_intent(order))
            and _text(order.get("execution_process_id") or _intent(order).get("execution_process_id"))
        })
        return result
    if not _text(selected_account_no):
        return result
    allowed = {_text(v) for v in allowed_stock_codes or [] if _text(v)} if allowed_stock_codes is not None else None
    externally_blocked = {_text(v) for v in blocked_execution_process_ids or [] if _text(v)}
    blocked: set[str] = set()
    prices = {_text(k): _price(v) for k, v in (actionable_prices_by_code or {}).items()}
    groups: dict[str, list[dict[str, Any]]] = {}
    for order in loaded["orders"]:
        if _action(order) not in {"NEW", "MODIFY"}:
            continue
        intent = _intent(order)
        if _text(intent.get("side") or order.get("side")).upper() != "BUY" or not _buy_exit_policy(intent):
            continue
        process_id = _text(order.get("execution_process_id") or intent.get("execution_process_id"))
        if process_id:
            groups.setdefault(process_id, []).append(order)
    current_at = now or datetime.now()
    max_proposals = max(0, int(proposal_limit or 0))
    for process_id, orders in groups.items():
        if process_id in externally_blocked:
            continue
        latest_by_id: dict[str, dict[str, Any]] = {}
        for item in orders:
            key = _text(item.get("execution_id") or _intent(item).get("execution_id") or item.get("id"))
            if key:
                latest_by_id[key] = _latest([latest_by_id[key], item]) if key in latest_by_id else item
        orders = list(latest_by_id.values()) or orders
        representative = orders[0]
        intent = _intent(representative)
        account_values = {_field_value(item, "account_no") for item in orders}
        account_values.discard("")
        code_values = {_field_value(item, "code") for item in orders}
        code_values.discard("")
        source_ids = {_field_value(item, "source_signal_id") for item in orders}
        source_ids.discard("")
        routine_ids = {_field_value(item, "routine_instance_id") for item in orders}
        routine_ids.discard("")
        cycle_ids = {_field_value(item, "cycle_identity") for item in orders}
        cycle_ids.discard("")
        account = next(iter(account_values), "")
        code = next(iter(code_values), "")
        signal_id = next(iter(source_ids), "")
        routine_instance_id = next(iter(routine_ids), "")
        cycle_identity = next(iter(cycle_ids), "")
        name = _text(representative.get("name"))
        if account != _text(selected_account_no) or (allowed is not None and code not in allowed):
            continue

        reasons: list[str] = []
        if len(account_values) != 1:
            reasons.append("BUY_EXIT_ACCOUNT_IDENTITY_MISMATCH")
        if len(code_values) != 1:
            reasons.append("BUY_EXIT_CODE_IDENTITY_MISMATCH")
        if len(source_ids) != 1:
            reasons.append("BUY_EXIT_SOURCE_SIGNAL_ID_MISMATCH")
        if len(routine_ids) != 1:
            reasons.append("BUY_EXIT_ROUTINE_INSTANCE_ID_MISMATCH")
        if len(cycle_ids) != 1:
            reasons.append("BUY_EXIT_CYCLE_IDENTITY_MISMATCH")

        matching_signals = [s for s in loaded["signals"] if _text(s.get("id")) == signal_id]
        signal = matching_signals[0] if len(matching_signals) == 1 else {}
        if len(matching_signals) != 1:
            reasons.append("BUY_EXIT_SOURCE_SIGNAL_MISSING_OR_AMBIGUOUS")
        signal_intents = _signal_intents(signal, process_id)
        signal_routine_ids = {
            _text(signal.get("routine_instance_id")),
            *(_text(item.get("routine_instance_id")) for item in signal_intents),
        }
        signal_routine_ids.discard("")
        signal_cycle_ids = {
            _text(signal.get("cycle_identity")),
            *(_text(item.get("cycle_identity")) for item in signal_intents),
        }
        signal_cycle_ids.discard("")
        if signal and (
            _text(signal.get("code")) != code
            or (
                _text(signal.get("execution_process_id"))
                and _text(signal.get("execution_process_id")) != process_id
            )
            or signal_routine_ids != {routine_instance_id}
            or signal_cycle_ids != {cycle_identity}
        ):
            reasons.append("BUY_EXIT_SIGNAL_IDENTITY_MISMATCH")
        name = name or _text(signal.get("name"))

        existing = _dict(signal.get("buy_exit_evidence"))
        if existing:
            if (
                _text(existing.get("execution_process_id")) == process_id
                and _text(existing.get("source_signal_id")) == signal_id
                and _text(existing.get("routine_instance_id")) == routine_instance_id
                and _text(existing.get("cycle_identity")) == cycle_identity
                and existing.get("buy_phase_completed") is True
            ):
                blocked.add(process_id)
                result["waiting"].append({"execution_process_id": process_id, "code": code,
                                          "reason": "BUY_REPEAT_EXIT_ALREADY_RECORDED"})
            else:
                blocked.add(process_id)
                result["reviews"].append(_review(
                    process_id=process_id, signal_id=signal_id, code=code, name=name,
                    reasons=["BUY_EXIT_EVIDENCE_IDENTITY_INVALID"],
                ))
            continue

        cancel_orders = [
            item for item in loaded["orders"]
            if _action(item) == "CANCEL" and _field_value(item, "execution_process_id") == process_id
        ]
        process_records = orders + cancel_orders
        if any(
            _text(item.get("status")).upper() == "SEND_UNCERTAIN"
            or item.get("manual_reconciliation_required") is True
            for item in process_records
        ):
            reasons.append("BUY_EXIT_SEND_UNCERTAIN_OR_RECONCILIATION_REQUIRED")

        latest_generation = max(
            plan_generation(item.get("plan_generation", _intent(item).get("plan_generation")))
            for item in orders
        )
        latest_signal_intents = [
            item for item in signal_intents
            if plan_generation(item.get("plan_generation")) == latest_generation
        ]
        if latest_signal_intents:
            reasons.extend(f"BUY_EXIT_CHILD_SET_INVALID:{issue}" for issue in validate_child_set(latest_signal_intents))

        runtime_by_execution: dict[str, list[dict[str, Any]]] = {}
        execution_ids: set[str] = set()
        for order in orders:
            execution_id = _field_value(order, "execution_id")
            if not execution_id:
                reasons.append("BUY_EXIT_EXECUTION_ID_MISSING")
                continue
            execution_ids.add(execution_id)
        for runtime in loaded["executions"]:
            execution_id = _text(runtime.get("execution_id"))
            if execution_id:
                runtime_by_execution.setdefault(execution_id, []).append(runtime)
        for execution_id in execution_ids:
            runtime_matches = runtime_by_execution.get(execution_id, [])
            if len(runtime_matches) != 1:
                reasons.append(f"BUY_EXIT_RUNTIME_EXECUTION_MISSING_OR_AMBIGUOUS:{execution_id}")
                continue
            runtime = runtime_matches[0]
            if _text(runtime.get("execution_process_id")) != process_id:
                reasons.append(f"BUY_EXIT_RUNTIME_PROCESS_MISMATCH:{execution_id}")
            for field, expected in (
                ("source_signal_id", signal_id), ("routine_instance_id", routine_instance_id),
                ("cycle_identity", cycle_identity), ("account_no", account), ("code", code),
            ):
                actual = _text(runtime.get(field))
                if actual and actual != expected:
                    reasons.append(f"BUY_EXIT_RUNTIME_{field.upper()}_MISMATCH:{execution_id}")

        owners = [item for item in loaded["processes"] if _text(item.get("execution_process_id")) == process_id]
        if len(owners) != 1:
            reasons.append("BUY_EXIT_PROCESS_OWNER_MISSING_OR_AMBIGUOUS")
        option_hashes = {
            _field_value(item, "option_snapshot_hash") for item in orders
            if _field_value(item, "option_snapshot_hash")
        }
        if len(option_hashes) != 1:
            reasons.append("BUY_EXIT_OPTION_SNAPSHOT_HASH_MISMATCH")
        elif owners:
            owner = owners[0]
            if _text(owner.get("option_snapshot_hash")) != next(iter(option_hashes)):
                reasons.append("BUY_EXIT_PROCESS_OPTION_SNAPSHOT_HASH_MISMATCH")
            for field, expected in (
                ("source_signal_id", signal_id), ("routine_instance_id", routine_instance_id),
                ("cycle_identity", cycle_identity), ("account_no", account), ("code", code),
            ):
                actual = _text(owner.get(field))
                if actual and actual != expected:
                    reasons.append(f"BUY_EXIT_PROCESS_{field.upper()}_MISMATCH")

        fills_for_process = [item for item in loaded["fills"] if _text(item.get("execution_process_id")) == process_id]
        for fill in loaded["fills"]:
            execution_id = _text(fill.get("execution_id"))
            if execution_id in execution_ids and _text(fill.get("execution_process_id")) != process_id:
                reasons.append(f"BUY_EXIT_FILL_PROCESS_MISMATCH:{execution_id}")
        for fill in fills_for_process:
            execution_id = _text(fill.get("execution_id"))
            quantity = _int(fill.get("filled_quantity"))
            if execution_id not in execution_ids:
                reasons.append(f"BUY_EXIT_FILL_EXECUTION_MISMATCH:{execution_id or 'MISSING'}")
            if quantity is None or quantity <= 0:
                reasons.append(f"BUY_EXIT_FILL_QUANTITY_INVALID:{execution_id or 'MISSING'}")
            for field, expected in (
                ("source_signal_id", signal_id), ("routine_instance_id", routine_instance_id),
                ("cycle_identity", cycle_identity), ("account_no", account), ("code", code),
            ):
                actual = _text(fill.get(field))
                if actual and actual != expected:
                    reasons.append(f"BUY_EXIT_FILL_{field.upper()}_MISMATCH:{execution_id or 'MISSING'}")

        position_matches = [
            item for item in loaded["positions"]
            if _text(item.get("account_no")) == account and _text(item.get("code")) == code
        ]
        holding_matches = [
            item for item in loaded["holdings"]
            if _text(item.get("account_no")) == account and _text(item.get("code")) == code
        ]
        position = position_matches[0] if len(position_matches) == 1 else {}
        holding = holding_matches[0] if len(holding_matches) == 1 else {}
        if len(position_matches) != 1:
            reasons.append("BUY_EXIT_POSITION_MATCH_INVALID")
        if len(holding_matches) != 1:
            reasons.append("BUY_EXIT_BROKER_HOLDING_MATCH_INVALID")
        position_quantity = _int(position.get("quantity"))
        holding_quantity = _int(holding.get("holding_quantity"))
        available_quantity = _int(holding.get("available_quantity"))
        if (
            position_quantity is None or holding_quantity is None or available_quantity is None
            or available_quantity > holding_quantity
        ):
            reasons.append("BUY_EXIT_HOLDING_QUANTITY_INVALID")
        elif position_quantity != holding_quantity:
            reasons.append("BUY_EXIT_POSITION_BROKER_MISMATCH")
        if holding.get("manual_reconciliation_required") is True \
                or _text(holding.get("reconciliation_status")).upper() not in {"", "CONSISTENT"}:
            reasons.append("BUY_EXIT_HOLDING_RECONCILIATION_REQUIRED")

        latest_position_evidence = max(
            [_time(position.get("updated_at") or position.get("last_fill_at")) or datetime.min]
            + [
                _time(item.get("recorded_at") or item.get("received_at") or item.get("occurred_at")) or datetime.min
                for item in fills_for_process
            ],
            default=datetime.min,
        )
        holding_time = _time(holding.get("received_at"))
        if holding_time is None or holding_time < latest_position_evidence:
            reasons.append("BUY_EXIT_HOLDING_EVIDENCE_STALE")

        latest_status_by_execution = {
            execution_id: _latest([item for item in orders if _field_value(item, "execution_id") == execution_id])
            for execution_id in execution_ids
        }
        for execution_id, order in latest_status_by_execution.items():
            status = _text(order.get("status")).upper()
            if status in _FAILURE_TERMINAL:
                reasons.append(f"BUY_EXIT_FAILURE_TERMINAL_UNRESOLVED:{execution_id}:{status}")
            elif status not in _ACTIVE | _SUCCESS_TERMINAL | {"SEND_UNCERTAIN"}:
                reasons.append(f"BUY_EXIT_STATUS_UNRESOLVED:{execution_id}:{status or 'UNKNOWN'}")

        if reasons:
            blocked.add(process_id)
            result["reviews"].append(_review(
                process_id=process_id, signal_id=signal_id, code=code, name=name, reasons=reasons,
            ))
            continue

        policy = _buy_exit_policy(intent)
        round_values = [_int(_intent(item).get("buy_round")) for item in orders]
        latest_round = max((value for value in round_values if value is not None), default=1)
        completed_repeat_rounds = _completed_repeat_rounds(orders, loaded["fills"], process_id)
        repeat_count = len(completed_repeat_rounds)
        anchor = _anchor(orders, signal, process_id)
        order_price = next((_price(_intent(o).get("price") or o.get("price")) for o in reversed(orders) if _price(_intent(o).get("price") or o.get("price"))), None)
        evaluation = evaluate_buy_exit_policy(policy=policy, completed_repeat_count=repeat_count, repeat_started_at=anchor,
            order_price=order_price, current_price=prices.get(code), average_price=_price(position.get("average_price")), now=current_at)
        if not evaluation.get("triggered"):
            if evaluation.get("waiting_reasons"): result["waiting"].append({"execution_process_id": process_id, "code": code, "reason": "BUY_REPEAT_EXIT_EVIDENCE_PENDING", "waiting_reasons": evaluation["waiting_reasons"]})
            continue
        blocked.add(process_id)
        active = [o for o in cancel_orders if _text(o.get("status")).upper() in _ACTIVE_CANCEL and o.get("original_order_effect_confirmed") is not True]
        if any(_text(o.get("status")).upper() == "SEND_UNCERTAIN" or o.get("manual_reconciliation_required") is True for o in active):
            result["reviews"].append(_review(process_id=process_id, signal_id=signal_id, code=code, name=name,
                                             reasons=["BUY_EXIT_CANCEL_SEND_UNCERTAIN"])); continue
        cancel_generation_failed = False
        for order in latest_status_by_execution.values():
            status = _text(order.get("status")).upper()
            if status in _ACTIVE and _remaining(order) > 0 and not active:
                if len(result["cancel_proposals"]) >= max_proposals:
                    result["waiting"].append({
                        "execution_process_id": process_id,
                        "code": code,
                        "reason": "BUY_EXIT_CANCEL_PROPOSAL_LIMIT_REACHED",
                    })
                    cancel_generation_failed = True
                    continue
                broker_no = _text(order.get("broker_order_no"))
                if not broker_no:
                    result["reviews"].append(_review(process_id=process_id, signal_id=signal_id, code=code,
                                                     name=name, reasons=["BUY_EXIT_BROKER_ORDER_NO_MISSING"]))
                    cancel_generation_failed = True
                    continue
                result["cancel_proposals"].append({"execution_process_id": process_id, "source_signal_id": signal_id, "account_no": account, "code": code, "side": "BUY", "order_queued_id": order.get("id") or order.get("order_queued_id"), "broker_order_no": broker_no, "remaining_quantity": _remaining(order), "trigger_snapshot": evaluation, "reason": "BUY_REPEAT_EXIT_CONDITION_MATCHED"})
        if cancel_generation_failed:
            continue
        if result["cancel_proposals"] and any(p.get("execution_process_id") == process_id for p in result["cancel_proposals"]):
            result["waiting"].append({"execution_process_id": process_id, "code": code, "reason": "BUY_EXIT_CANCEL_FIRST"}); continue
        if active:
            result["waiting"].append({"execution_process_id": process_id, "code": code, "reason": "BUY_EXIT_ACTIVE_CANCEL"}); continue
        cancel_effect_pending = False
        for order in latest_status_by_execution.values():
            status = _text(order.get("status")).upper()
            if status not in {"CANCELLED", "CANCELED", "PARTIAL_CANCELLED"}:
                continue
            broker_no = _text(order.get("broker_order_no"))
            matching = [c for c in cancel_orders if _original_no(c) == broker_no]
            if not any(
                c.get("original_order_effect_confirmed") is True
                and _text(c.get("status")).upper() in _SUCCESS_TERMINAL
                for c in matching
            ):
                cancel_effect_pending = True
        if cancel_effect_pending:
            result["waiting"].append({"execution_process_id": process_id, "code": code, "reason": "BUY_EXIT_CANCEL_EFFECT_PENDING"})
            continue
        terminal = all(
            _text(item.get("status")).upper() in _SUCCESS_TERMINAL and _remaining(item) == 0
            for item in latest_status_by_execution.values()
        )
        if terminal:
            result["completion_proposals"].append({"execution_process_id": process_id, "source_signal_id": signal_id,
                "routine_instance_id": routine_instance_id, "cycle_identity": cycle_identity,
                "code": code, "name": name,
                "exit_condition_type": evaluation.get("matched_condition_types", ["OR"])[0],
                "exit_condition_types": evaluation.get("matched_condition_types", []),
                "exit_triggered_at": current_at.isoformat(timespec="milliseconds"),
                "evaluated_generation": latest_round, "repeat_completed_count": repeat_count,
                "repeat_started_at": anchor.isoformat(timespec="milliseconds") if anchor else None,
                "completed_repeat_rounds": sorted(completed_repeat_rounds),
                "exit_source_snapshot_hash": evaluation.get("snapshot_hash"),
                "exit_source_snapshot": evaluation, "reason": "BUY_REPEAT_EXIT_CONDITION_MATCHED",
                "cancel_required": bool(cancel_orders), "cancel_effect_confirmed": True,
                "buy_phase_completed": True})
    result["blocked_execution_process_ids"] = sorted(blocked)
    return result


__all__ = ["evaluate_buy_exit_policy", "inspect_buy_repeat_exits"]
