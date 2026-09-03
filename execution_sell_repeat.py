# -*- coding: utf-8 -*-
"""Read-only follow-up SELL generation inspection over durable evidence."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
import json
from pathlib import Path
from typing import Any

from execution_price_comparison import evaluate_percent_comparison, resolve_price_source
from execution_price_reset import build_sell_generation_intents
from execution_provenance_contract import plan_generation, stable_hash, validate_child_set


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
_SUCCESS_TERMINAL = {"FILLED", "CANCELLED", "CANCELED", "PARTIAL_CANCELLED"}
_FAILURE_TERMINAL = {"BROKER_REJECTED", "SEND_CALL_REJECTED", "REJECTED"}
_ACTIVE_CANCEL = _PRE_DISPATCH | {"BROKER_ACCEPTED", "SEND_UNCERTAIN"}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


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


def _positive_price(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _time(value: Any) -> datetime | None:
    value_text = _text(value)
    if not value_text:
        return None
    try:
        return datetime.fromisoformat(value_text.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _latest(records: list[dict[str, Any]]) -> dict[str, Any]:
    return max(
        records,
        key=lambda item: (
            _time(item.get("updated_at") or item.get("send_call_result_recorded_at") or item.get("created_at"))
            or datetime.min,
            _text(item.get("id")),
        ),
    )


def _read(path: str | Path, field: str, *, optional: bool = False) -> tuple[list[dict[str, Any]], str]:
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
    direct = _as_dict(record.get("execution_intent"))
    return direct or _as_dict(_as_dict(record.get("execution_request")).get("execution_intent"))


def _preview(record: dict[str, Any]) -> dict[str, Any]:
    return _as_dict(_as_dict(record.get("execution_request")).get("request_preview"))


def _action(record: dict[str, Any]) -> str:
    return _text(record.get("order_action") or _preview(record).get("order_action") or "NEW").upper()


def _original_no(record: dict[str, Any]) -> str:
    return _text(record.get("original_order_no") or _preview(record).get("original_order_no"))


def _cancel_evidence(record: dict[str, Any]) -> dict[str, Any]:
    direct = _as_dict(record.get("cancel_evidence"))
    if direct:
        return direct
    child = _as_dict(record.get("child_plan"))
    if not child:
        child = _as_dict(_as_dict(record.get("execution_request")).get("child_plan"))
    return _as_dict(child.get("cancel_evidence"))


def _policy(intent: dict[str, Any]) -> dict[str, Any]:
    policy = _as_dict(intent.get("sell_repeat_policy"))
    if (
        _text(policy.get("policy")).upper() == "SELL_FOLLOW_UP_REPEAT"
        and policy.get("enabled") is True
        and isinstance(policy.get("execution_template"), dict)
    ):
        return policy
    return {}


def _exit_policy(repeat_policy: dict[str, Any]) -> dict[str, Any]:
    policy = _as_dict(repeat_policy.get("exit_policy"))
    conditions = policy.get("conditions")
    if (
        _text(policy.get("policy")).upper() == "SELL_REPEAT_EXIT"
        and _text(policy.get("logic")).upper() == "OR"
        and isinstance(conditions, list)
        and all(isinstance(item, dict) for item in conditions)
    ):
        return policy
    return {}


def _review(
    *, process_id: str, signal_id: str, code: str, name: str, reasons: list[str]
) -> dict[str, Any]:
    return {
        "execution_process_id": process_id,
        "source_signal_id": signal_id,
        "code": code,
        "name": name,
        "review_reasons": sorted(set(reasons)),
        "review_location": "SELL_REPEAT_RECONCILIATION",
    }


def _generation_identity_present(intent: dict[str, Any]) -> bool:
    return bool(
        _text(intent.get("repeat_source_snapshot_hash"))
        or _text(intent.get("price_reset_source_snapshot_hash"))
        or _text(intent.get("final_residual_exit_action_hash"))
    )


def _repeat_started_at(
    process_orders: list[dict[str, Any]],
    signal_intents: list[dict[str, Any]],
) -> datetime | None:
    values: list[datetime] = []
    for order in process_orders:
        intent = _intent(order)
        if not _text(intent.get("repeat_source_snapshot_hash")):
            continue
        timestamp = _time(
            intent.get("repeat_started_at")
            or order.get("created_at")
            or order.get("updated_at")
        )
        if timestamp is not None:
            values.append(timestamp)
    for intent in signal_intents:
        if not _text(intent.get("repeat_source_snapshot_hash")):
            continue
        timestamp = _time(intent.get("repeat_started_at"))
        if timestamp is not None:
            values.append(timestamp)
    return min(values) if values else None


def _completed_repeat_count(process_orders: list[dict[str, Any]]) -> int:
    return len({
        _text(_intent(item).get("repeat_source_snapshot_hash"))
        for item in process_orders
        if _text(_intent(item).get("repeat_source_snapshot_hash"))
    })


def _generation_order_price(
    template: dict[str, Any],
    latest_orders: dict[str, dict[str, Any]],
) -> float | None:
    candidates = (
        _as_dict(template.get("multi_hoga_plan")).get("base_price"),
        _as_dict(template.get("multi_ratio_plan")).get("order_price"),
        template.get("price"),
    )
    for value in candidates:
        price = _positive_price(value)
        if price is not None:
            return price
    for order in latest_orders.values():
        intent = _intent(order)
        for value in (
            _as_dict(intent.get("multi_hoga_plan")).get("base_price"),
            _as_dict(intent.get("multi_ratio_plan")).get("order_price"),
            intent.get("price"),
            order.get("price"),
        ):
            price = _positive_price(value)
            if price is not None:
                return price
    return None


def _evaluate_exit_policy(
    *,
    policy: dict[str, Any],
    process_id: str,
    generation: int,
    repeat_count: int,
    repeat_started_at: datetime | None,
    order_price: float | None,
    current_price: float | None,
    average_price: float | None,
    now: datetime,
) -> dict[str, Any]:
    conditions = policy.get("conditions")
    conditions = conditions if isinstance(conditions, list) else []
    evaluated: list[dict[str, Any]] = []
    waiting_reasons: list[str] = []
    matched_types: list[str] = []
    for raw in conditions:
        condition = deepcopy(raw) if isinstance(raw, dict) else {}
        condition_type = _text(condition.get("condition_type")).upper()
        result = {"condition_type": condition_type, "matched": False, "status": "READY"}
        if condition_type == "COUNT":
            target = _positive_int(condition.get("target_repeat_generations"))
            if target is None:
                result.update({"status": "INVALID", "reason": "SELL_REPEAT_EXIT_COUNT_INVALID"})
                waiting_reasons.append("SELL_REPEAT_EXIT_COUNT_INVALID")
            else:
                matched = repeat_count >= target
                result.update({
                    "target_repeat_generations": target,
                    "completed_repeat_generations": repeat_count,
                    "matched": matched,
                    "reason": "MATCHED" if matched else "NOT_MATCHED",
                })
        elif condition_type == "TIME":
            duration_ms = _positive_int(condition.get("duration_ms"))
            if duration_ms is None:
                result.update({"status": "INVALID", "reason": "SELL_REPEAT_EXIT_TIME_INVALID"})
                waiting_reasons.append("SELL_REPEAT_EXIT_TIME_INVALID")
            elif repeat_started_at is None:
                result.update({
                    "status": "NOT_STARTED",
                    "duration_ms": duration_ms,
                    "reason": "FIRST_REPEAT_GENERATION_NOT_STARTED",
                })
            else:
                due_at = repeat_started_at + timedelta(milliseconds=duration_ms)
                matched = now >= due_at
                result.update({
                    "duration_ms": duration_ms,
                    "anchor_at": repeat_started_at.isoformat(timespec="milliseconds"),
                    "due_at": due_at.isoformat(timespec="milliseconds"),
                    "evaluated_at": now.isoformat(timespec="milliseconds"),
                    "matched": matched,
                    "reason": "MATCHED" if matched else "NOT_MATCHED",
                })
        elif condition_type == "PRICE":
            left_source = _text(condition.get("left_source")).upper()
            right_source = _text(condition.get("right_source")).upper()
            if "CURRENT_PRICE" in {left_source, right_source} and current_price is None:
                result.update({"status": "WAITING", "reason": "SELL_REPEAT_EXIT_CURRENT_PRICE_UNAVAILABLE"})
                waiting_reasons.append("SELL_REPEAT_EXIT_CURRENT_PRICE_UNAVAILABLE")
            else:
                left_value = resolve_price_source(
                    left_source,
                    order_price=order_price,
                    current_price=current_price,
                    average_price=average_price,
                )
                right_value = resolve_price_source(
                    right_source,
                    order_price=order_price,
                    current_price=current_price,
                    average_price=average_price,
                )
                if left_value is None or right_value is None:
                    reason = "SELL_REPEAT_EXIT_PRICE_SOURCE_UNAVAILABLE"
                    result.update({"status": "WAITING", "reason": reason})
                    waiting_reasons.append(reason)
                else:
                    matched, observed = evaluate_percent_comparison(
                        left=right_value,
                        right=left_value,
                        direction=_text(condition.get("direction")).upper(),
                        compare=_text(condition.get("compare")).upper(),
                        threshold=condition.get("threshold_percent"),
                    )
                    if matched is None or observed is None:
                        reason = "SELL_REPEAT_EXIT_PRICE_POLICY_INVALID"
                        result.update({"status": "INVALID", "reason": reason})
                        waiting_reasons.append(reason)
                    else:
                        result.update({
                            "left_source": left_source,
                            "right_source": right_source,
                            "left_value": left_value,
                            "right_value": right_value,
                            "observed_percent": observed,
                            "threshold_percent": condition.get("threshold_percent"),
                            "direction": condition.get("direction"),
                            "compare": condition.get("compare"),
                            "matched": matched,
                            "reason": "MATCHED" if matched else "NOT_MATCHED",
                        })
        else:
            reason = "SELL_REPEAT_EXIT_CONDITION_TYPE_INVALID"
            result.update({"status": "INVALID", "reason": reason})
            waiting_reasons.append(reason)
        if result.get("matched") is True:
            matched_types.append(condition_type)
        evaluated.append(result)
    snapshot_payload = {
        "execution_process_id": process_id,
        "evaluated_generation": generation,
        "logic": "OR",
        "policy_snapshot_hash": policy.get("snapshot_hash"),
        "completed_repeat_generations": repeat_count,
        "repeat_started_at": (
            repeat_started_at.isoformat(timespec="milliseconds")
            if repeat_started_at is not None else None
        ),
        "order_price": order_price,
        "current_price": current_price,
        "average_price": average_price,
        "conditions": evaluated,
        "matched_condition_types": matched_types,
    }
    return {
        **snapshot_payload,
        "active": bool(conditions),
        "triggered": bool(matched_types),
        "waiting_reasons": sorted(set(waiting_reasons)),
        "snapshot_hash": stable_hash(snapshot_payload),
    }


def _time_offsets(*, count: int, value: int, unit_ms: int, range_mode: str) -> list[int]:
    duration = value * unit_ms
    if range_mode in {"WITHIN", "이내"}:
        if count == 1:
            return [0]
        return [round(index * duration / (count - 1)) for index in range(count)]
    if range_mode in {"INTERVAL", "간격"}:
        return [index * duration for index in range(count)]
    raise ValueError("SELL_REPEAT_TIME_RANGE_INVALID")


def _template_for_generation(
    *,
    policy: dict[str, Any],
    base_intent: dict[str, Any],
    quantity: int,
    current_price: float | None,
) -> dict[str, Any]:
    configured = deepcopy(_as_dict(policy.get("execution_template")))
    mode = _text(configured.get("execution_mode")).upper()
    template = {
        key: deepcopy(value)
        for key, value in base_intent.items()
        if key not in {
            "execution_id", "execution_mode", "quantity", "price", "hoga",
            "price_basis", "child_sequence_index", "child_sequence_total",
            "child_kind", "child_plan", "multi_hoga_plan", "multi_time_plan",
            "multi_ratio_plan", "schedule_anchor_at", "scheduled_at",
            "plan_generation", "planned_total_quantity",
            "repeat_source_snapshot_hash", "price_reset_source_snapshot_hash",
        }
    }
    template.update(deepcopy(configured))
    template["sell_repeat_policy"] = deepcopy(policy)
    timeout_policy = _as_dict(policy.get("unfilled_timeout_policy"))
    reset_policy = _as_dict(policy.get("sell_price_reset_policy"))
    if timeout_policy:
        template["unfilled_timeout_policy"] = deepcopy(timeout_policy)
    else:
        template.pop("unfilled_timeout_policy", None)
    if reset_policy:
        reset_copy = deepcopy(reset_policy)
        if current_price is not None:
            reset_copy["order_price"] = current_price
        template["sell_price_reset_policy"] = reset_copy
    else:
        template.pop("sell_price_reset_policy", None)

    if mode == "MULTI_HOGA":
        offsets = configured.get("hoga_offsets")
        if not isinstance(offsets, list) or not offsets or any(
            isinstance(value, bool) or not isinstance(value, int) for value in offsets
        ):
            raise ValueError("SELL_REPEAT_HOGA_OFFSETS_INVALID")
        template["multi_hoga_plan"] = {
            "base_price": current_price,
            "hoga_offsets": list(offsets),
            "planned_child_count": min(len(offsets), quantity),
            "planned_total_quantity": quantity,
            "instrument_type": configured.get("instrument_type") or "STOCK",
        }
    elif mode == "MULTI_TIME":
        configured_count = _positive_int(configured.get("configured_child_count"))
        value = _positive_int(configured.get("time_value"))
        unit_ms = _positive_int(configured.get("time_unit_milliseconds"))
        if configured_count is None or value is None or unit_ms is None:
            raise ValueError("SELL_REPEAT_TIME_PLAN_INVALID")
        child_count = min(configured_count, quantity)
        template["multi_time_plan"] = {
            "configured_child_count": configured_count,
            "planned_child_count": child_count,
            "planned_total_quantity": quantity,
            "scheduled_offsets_ms": _time_offsets(
                count=child_count,
                value=value,
                unit_ms=unit_ms,
                range_mode=_text(configured.get("time_range")).upper(),
            ),
            "price_basis": configured.get("price_basis") or "ORDER_PRICE",
        }
    elif mode == "MULTI_RATIO":
        configured_count = _positive_int(configured.get("configured_child_count"))
        if configured_count is None:
            raise ValueError("SELL_REPEAT_RATIO_PLAN_INVALID")
        template["multi_ratio_plan"] = {
            "configured_child_count": configured_count,
            "planned_child_count": min(configured_count, quantity),
            "planned_total_quantity": quantity,
            "ratio_left": configured.get("ratio_left"),
            "ratio_right": configured.get("ratio_right"),
            "ratio_direction": configured.get("ratio_direction"),
            "ratio_value": configured.get("ratio_value"),
            "ratio_compare": configured.get("ratio_compare"),
            "ratio_unit": "PERCENT",
            "order_price": current_price,
        }
    elif mode not in {"SINGLE_ORDER", "SINGLE"}:
        raise ValueError(f"SELL_REPEAT_EXECUTION_MODE_UNSUPPORTED:{mode or 'EMPTY'}")
    return template


def _repeat_snapshot(
    *,
    process_id: str,
    generation: int,
    policy: dict[str, Any],
    latest_orders: dict[str, dict[str, Any]],
    position: dict[str, Any],
    holding: dict[str, Any],
) -> dict[str, Any]:
    payload = {
        "execution_process_id": process_id,
        "source_plan_generation": generation,
        "repeat_plan_snapshot_hash": policy.get("plan_snapshot_hash"),
        "orders": [
            {
                "execution_id": execution_id,
                "status": order.get("status"),
                "remaining_quantity": order.get("remaining_quantity"),
                "broker_order_no": order.get("broker_order_no"),
                "updated_at": order.get("updated_at"),
            }
            for execution_id, order in sorted(latest_orders.items())
        ],
        "position": {
            "quantity": position.get("quantity"),
            "average_price": position.get("average_price"),
            "updated_at": position.get("updated_at"),
        },
        "broker_holding": {
            "holding_quantity": holding.get("holding_quantity"),
            "available_quantity": holding.get("available_quantity"),
            "received_at": holding.get("received_at"),
        },
    }
    return {**payload, "snapshot_hash": stable_hash(payload)}


def inspect_sell_repeat_generations(
    *,
    selected_account_no: str,
    actionable_prices_by_code: dict[str, Any] | None,
    allowed_stock_codes: tuple[str, ...] | list[str] | set[str] | None = None,
    blocked_execution_process_ids: tuple[str, ...] | list[str] | set[str] | None = None,
    exit_only: bool = False,
    now: datetime | None = None,
    proposal_limit: int = 5,
    order_queue_path: str | Path = ORDER_QUEUE_PATH,
    order_executions_path: str | Path = ORDER_EXECUTIONS_PATH,
    fills_path: str | Path = FILLS_PATH,
    positions_path: str | Path = POSITIONS_PATH,
    holdings_path: str | Path = HOLDINGS_PATH,
    signals_path: str | Path = SIGNALS_PATH,
) -> dict[str, Any]:
    """Propose at most one safe next generation per completed SELL process."""
    paths = (
        (order_queue_path, "orders", False),
        (order_executions_path, "executions", False),
        (order_executions_path, "processes", True),
        (fills_path, "fills", False),
        (positions_path, "positions", False),
        (holdings_path, "holdings", False),
        (signals_path, "signals", False),
    )
    loaded: dict[str, list[dict[str, Any]]] = {}
    errors: list[str] = []
    for path, field, optional in paths:
        values, error = _read(path, field, optional=optional)
        loaded[field] = values
        if error:
            errors.append(error)
    empty = {
        "ok": not errors,
        "proposals": [],
        "exit_proposals": [],
        "blocked_execution_process_ids": [],
        "reviews": [],
        "waiting": [],
        "errors": errors,
    }
    if errors:
        return empty
    account_no = _text(selected_account_no)
    allowed = (
        {_text(value) for value in allowed_stock_codes if _text(value)}
        if allowed_stock_codes is not None else None
    )
    blocked = {_text(value) for value in (blocked_execution_process_ids or []) if _text(value)}
    if not account_no or (allowed_stock_codes is not None and not allowed):
        return empty
    current_at = now or datetime.now()
    prices = {
        _text(code): _positive_price(value)
        for code, value in (actionable_prices_by_code or {}).items()
        if _text(code)
    }
    original_orders = [item for item in loaded["orders"] if _action(item) in {"NEW", "MODIFY"}]
    cancel_orders = [item for item in loaded["orders"] if _action(item) == "CANCEL"]
    by_process: dict[str, list[dict[str, Any]]] = {}
    for order in original_orders:
        intent = _intent(order)
        process_id = _text(order.get("execution_process_id") or intent.get("execution_process_id"))
        if process_id and _policy(intent):
            by_process.setdefault(process_id, []).append(order)

    proposals: list[dict[str, Any]] = []
    exit_proposals: list[dict[str, Any]] = []
    exit_blocked_processes: set[str] = set()
    reviews: list[dict[str, Any]] = []
    waiting: list[dict[str, Any]] = []
    max_proposals = max(0, int(proposal_limit or 0))
    runtime_executions = {
        _text(item.get("execution_id")): item
        for item in loaded["executions"]
        if _text(item.get("execution_id"))
    }

    for process_id, process_orders in by_process.items():
        if process_id in blocked or len(proposals) >= max_proposals:
            continue
        source_ids = {
            _text(item.get("source_signal_id") or _intent(item).get("source_signal_id"))
            for item in process_orders
        }
        source_ids.discard("")
        signal_id = next(iter(source_ids), "")
        signal = next((item for item in loaded["signals"] if _text(item.get("id")) == signal_id), {})
        representative = process_orders[0]
        code = _text(representative.get("code") or _preview(representative).get("code") or signal.get("code"))
        name = _text(representative.get("name") or signal.get("name"))
        if allowed is not None and code not in allowed:
            continue
        existing_exit = _as_dict(signal.get("sell_repeat_exit_evidence"))
        if existing_exit:
            if (
                _text(existing_exit.get("execution_process_id")) == process_id
                and _text(existing_exit.get("source_signal_id")) in {"", signal_id}
                and _text(existing_exit.get("exit_source_snapshot_hash"))
            ):
                waiting.append({
                    "execution_process_id": process_id,
                    "code": code,
                    "reason": "SELL_REPEAT_EXIT_ALREADY_RECORDED",
                    "exit_source_snapshot_hash": existing_exit.get("exit_source_snapshot_hash"),
                })
                exit_blocked_processes.add(process_id)
            else:
                reviews.append(_review(
                    process_id=process_id,
                    signal_id=signal_id,
                    code=code,
                    name=name,
                    reasons=["SELL_REPEAT_EXIT_EVIDENCE_IDENTITY_INVALID"],
                ))
                if exit_only:
                    exit_blocked_processes.add(process_id)
            continue
        reasons: list[str] = []
        if len(source_ids) != 1:
            reasons.append("SELL_REPEAT_SOURCE_SIGNAL_ID_MISMATCH")
        if not signal:
            reasons.append("SELL_REPEAT_SOURCE_SIGNAL_MISSING")
        if {_text(item.get("account_no") or _preview(item).get("account_no")) for item in process_orders} != {account_no}:
            reasons.append("SELL_REPEAT_ACCOUNT_IDENTITY_MISMATCH")

        generation_groups: dict[int, dict[str, list[dict[str, Any]]]] = {}
        for order in process_orders:
            intent = _intent(order)
            generation = plan_generation(order.get("plan_generation", intent.get("plan_generation")))
            execution_id = _text(order.get("execution_id") or intent.get("execution_id"))
            if not execution_id:
                reasons.append("SELL_REPEAT_EXECUTION_ID_MISSING")
                continue
            generation_groups.setdefault(generation, {}).setdefault(execution_id, []).append(order)
        if not generation_groups:
            continue
        generation = max(generation_groups)
        all_signal_intents = [
            deepcopy(item)
            for item in signal.get("execution_intents", [])
            if isinstance(item, dict) and _text(item.get("execution_process_id")) == process_id
        ]
        signal_generations = {plan_generation(item.get("plan_generation")) for item in all_signal_intents}
        if signal_generations and max(signal_generations) > generation:
            pending_generation = max(signal_generations)
            pending = [item for item in all_signal_intents if plan_generation(item.get("plan_generation")) == pending_generation]
            if pending and all(_generation_identity_present(item) for item in pending):
                waiting.append({
                    "execution_process_id": process_id,
                    "code": code,
                    "reason": "SELL_REPEAT_GENERATION_PENDING_EXECUTION",
                    "plan_generation": pending_generation,
                })
                continue
            reviews.append(_review(
                process_id=process_id, signal_id=signal_id, code=code, name=name,
                reasons=["SELL_REPEAT_SIGNAL_GENERATION_AHEAD_WITHOUT_IDENTITY"],
            ))
            continue

        latest_orders = {
            execution_id: _latest(records)
            for execution_id, records in generation_groups[generation].items()
        }
        signal_intents = [
            item for item in all_signal_intents
            if plan_generation(item.get("plan_generation")) == generation
        ]
        template = signal_intents[0] if signal_intents else _intent(next(iter(latest_orders.values())))
        policy = _policy(template)
        if not policy:
            reasons.append("SELL_REPEAT_POLICY_MISSING")
        policy_hashes = {stable_hash(_policy(_intent(order))) for order in latest_orders.values()}
        if len(policy_hashes) != 1:
            reasons.append("SELL_REPEAT_POLICY_MISMATCH")
        if signal_intents:
            reasons.extend(f"SELL_REPEAT_CHILD_SET_INVALID:{issue}" for issue in validate_child_set(signal_intents))
            expected_ids = {_text(item.get("execution_id")) for item in signal_intents}
            if expected_ids != set(latest_orders):
                waiting.append({
                    "execution_process_id": process_id,
                    "code": code,
                    "reason": "SELL_REPEAT_GENERATION_CHILDREN_INCOMPLETE",
                })
                continue

        process_cancels = [
            item for item in cancel_orders
            if _text(item.get("execution_process_id") or _as_dict(item.get("execution_request")).get("execution_process_id")) == process_id
        ]
        active_cancels = [
            item for item in process_cancels
            if _text(item.get("status")).upper() in _ACTIVE_CANCEL
            and item.get("original_order_effect_confirmed") is not True
        ]
        if any(
            _text(item.get("status")).upper() == "SEND_UNCERTAIN"
            or item.get("manual_reconciliation_required") is True
            for item in active_cancels
        ):
            reasons.append("SELL_REPEAT_CANCEL_SEND_UNCERTAIN")
        elif active_cancels:
            waiting.append({"execution_process_id": process_id, "code": code, "reason": "SELL_REPEAT_ACTIVE_CANCEL"})
            continue

        for execution_id, order in latest_orders.items():
            status = _text(order.get("status")).upper()
            if status == "SEND_UNCERTAIN" or order.get("manual_reconciliation_required") is True:
                reasons.append(f"SELL_REPEAT_UNSAFE_ORDER:{execution_id}:{status or 'UNKNOWN'}")
            elif status in _OPEN or status in _PRE_DISPATCH:
                waiting.append({
                    "execution_process_id": process_id,
                    "code": code,
                    "reason": "SELL_REPEAT_PREVIOUS_GENERATION_ACTIVE",
                    "execution_id": execution_id,
                    "status": status,
                })
            elif status in _FAILURE_TERMINAL:
                reasons.append(f"SELL_REPEAT_PREVIOUS_GENERATION_FAILURE_UNRESOLVED:{execution_id}:{status}")
            elif status not in _SUCCESS_TERMINAL:
                reasons.append(f"SELL_REPEAT_STATUS_UNRESOLVED:{execution_id}:{status or 'UNKNOWN'}")
            elif status in {"CANCELLED", "CANCELED", "PARTIAL_CANCELLED"}:
                broker_no = _text(order.get("broker_order_no"))
                matching = [item for item in process_cancels if _original_no(item) == broker_no]
                confirmed = any(
                    _text(_cancel_evidence(item).get("trigger")).upper() == "UNFILLED_TIMEOUT"
                    and _text(item.get("status")).upper() in _SUCCESS_TERMINAL
                    and (
                        item.get("original_order_effect_confirmed") is True
                        or status in {"CANCELLED", "CANCELED", "PARTIAL_CANCELLED"}
                    )
                    for item in matching
                )
                if not confirmed:
                    reasons.append(f"SELL_REPEAT_TERMINAL_CANCEL_NOT_POLICY_CONFIRMED:{execution_id}")
        if any(item.get("reason") == "SELL_REPEAT_PREVIOUS_GENERATION_ACTIVE" and item.get("execution_process_id") == process_id for item in waiting):
            continue

        position_matches = [
            item for item in loaded["positions"]
            if _text(item.get("account_no")) == account_no and _text(item.get("code")) == code
        ]
        holding_matches = [
            item for item in loaded["holdings"]
            if _text(item.get("account_no")) == account_no and _text(item.get("code")) == code
        ]
        position = position_matches[0] if len(position_matches) == 1 else {}
        holding = holding_matches[0] if len(holding_matches) == 1 else {}
        if len(position_matches) != 1:
            reasons.append("SELL_REPEAT_POSITION_MATCH_INVALID")
        if len(holding_matches) != 1:
            reasons.append("SELL_REPEAT_BROKER_HOLDING_MATCH_INVALID")
        position_quantity = _nonnegative_int(position.get("quantity"))
        holding_quantity = _nonnegative_int(holding.get("holding_quantity"))
        available_quantity = _nonnegative_int(holding.get("available_quantity"))
        if position_quantity is None or holding_quantity is None or available_quantity is None or available_quantity > holding_quantity:
            reasons.append("SELL_REPEAT_HOLDING_QUANTITY_INVALID")
        elif position_quantity != holding_quantity:
            reasons.append("SELL_REPEAT_POSITION_BROKER_MISMATCH")
        if holding.get("manual_reconciliation_required") is True or _text(holding.get("reconciliation_status")).upper() not in {"", "CONSISTENT"}:
            reasons.append("SELL_REPEAT_HOLDING_RECONCILIATION_REQUIRED")

        for execution_id, order in latest_orders.items():
            runtime = runtime_executions.get(execution_id)
            if runtime is None:
                reasons.append(f"SELL_REPEAT_RUNTIME_EXECUTION_MISSING:{execution_id}")
                continue
            if _text(runtime.get("execution_process_id")) != process_id:
                reasons.append(f"SELL_REPEAT_RUNTIME_PROCESS_MISMATCH:{execution_id}")
            if plan_generation(runtime.get("plan_generation")) != generation:
                reasons.append(f"SELL_REPEAT_RUNTIME_GENERATION_MISMATCH:{execution_id}")
            execution_fills = [item for item in loaded["fills"] if _text(item.get("execution_id")) == execution_id]
            fill_quantities = [_nonnegative_int(item.get("filled_quantity")) for item in execution_fills]
            if any(value is None for value in fill_quantities):
                reasons.append(f"SELL_REPEAT_FILL_QUANTITY_INVALID:{execution_id}")
                continue
            queue_filled = next((
                _nonnegative_int(order.get(field))
                for field in ("total_filled_quantity", "cumulative_filled_quantity")
                if order.get(field) not in (None, "")
            ), None)
            evidence_filled = max((value for value in fill_quantities if value is not None), default=0)
            if queue_filled is not None and queue_filled != evidence_filled:
                reasons.append(f"SELL_REPEAT_QUEUE_FILL_MISMATCH:{execution_id}")
            if any(_text(item.get("execution_process_id")) not in {"", process_id} for item in execution_fills):
                reasons.append(f"SELL_REPEAT_FILL_PROCESS_MISMATCH:{execution_id}")

        owners = [item for item in loaded["processes"] if _text(item.get("execution_process_id")) == process_id]
        option_hashes = {_text(item.get("option_snapshot_hash")) for item in process_orders if _text(item.get("option_snapshot_hash"))}
        if len(owners) != 1:
            reasons.append("SELL_REPEAT_PROCESS_OWNER_MISSING_OR_AMBIGUOUS")
        elif len(option_hashes) == 1 and _text(owners[0].get("option_snapshot_hash")) not in {"", next(iter(option_hashes))}:
            reasons.append("SELL_REPEAT_PROCESS_OPTION_SNAPSHOT_HASH_MISMATCH")
        if len(option_hashes) != 1:
            reasons.append("SELL_REPEAT_OPTION_SNAPSHOT_HASH_MISMATCH")

        if reasons:
            reviews.append(_review(process_id=process_id, signal_id=signal_id, code=code, name=name, reasons=reasons))
            if exit_only:
                exit_blocked_processes.add(process_id)
            continue

        process_fills = [item for item in loaded["fills"] if _text(item.get("execution_process_id")) == process_id]
        latest_evidence = max(
            [
                _time(item.get("updated_at") or item.get("send_call_result_recorded_at") or item.get("created_at")) or datetime.min
                for item in list(latest_orders.values()) + process_cancels
            ]
            + [_time(item.get("recorded_at") or item.get("received_at") or item.get("occurred_at")) or datetime.min for item in process_fills]
            + [_time(position.get("updated_at") or position.get("last_fill_at")) or datetime.min],
            default=datetime.min,
        )
        holding_time = _time(holding.get("received_at"))
        if holding_time is None or holding_time < latest_evidence:
            waiting.append({"execution_process_id": process_id, "code": code, "reason": "SELL_REPEAT_HOLDING_EVIDENCE_PENDING"})
            continue
        assert available_quantity is not None
        if available_quantity <= 0:
            waiting.append({"execution_process_id": process_id, "code": code, "reason": "SELL_REPEAT_POSITION_CLOSED"})
            continue

        repeat_exit_policy = _exit_policy(policy)
        if not repeat_exit_policy:
            reviews.append(_review(
                process_id=process_id,
                signal_id=signal_id,
                code=code,
                name=name,
                reasons=["SELL_REPEAT_EXIT_POLICY_INVALID"],
            ))
            if exit_only:
                exit_blocked_processes.add(process_id)
            continue
        repeat_started_at = _repeat_started_at(process_orders, all_signal_intents)
        repeat_count = _completed_repeat_count(process_orders)
        exit_evaluation = _evaluate_exit_policy(
            policy=repeat_exit_policy,
            process_id=process_id,
            generation=generation,
            repeat_count=repeat_count,
            repeat_started_at=repeat_started_at,
            order_price=_generation_order_price(template, latest_orders),
            current_price=prices.get(code),
            average_price=_positive_price(position.get("average_price")),
            now=current_at,
        )
        if exit_evaluation.get("triggered") is True:
            exit_blocked_processes.add(process_id)
            matched_types = [
                _text(value).upper()
                for value in exit_evaluation.get("matched_condition_types", [])
                if _text(value)
            ]
            exit_proposals.append({
                "execution_process_id": process_id,
                "source_signal_id": signal_id,
                "code": code,
                "name": name,
                "signal_status": _text(signal.get("status")).upper() or "PREVIEWED",
                "exit_condition_type": matched_types[0] if len(matched_types) == 1 else "OR",
                "exit_condition_types": matched_types,
                "exit_triggered_at": current_at.isoformat(timespec="milliseconds"),
                "exit_source_snapshot_hash": exit_evaluation.get("snapshot_hash"),
                "evaluated_generation": generation,
                "reason": "SELL_REPEAT_EXIT_CONDITION_MATCHED",
                "exit_source_snapshot": exit_evaluation,
            })
            waiting.append({
                "execution_process_id": process_id,
                "code": code,
                "reason": "SELL_REPEAT_EXIT_TRIGGERED",
                "matched_condition_types": matched_types,
            })
            continue
        if (
            exit_evaluation.get("active") is True
            and exit_evaluation.get("waiting_reasons")
        ):
            exit_blocked_processes.add(process_id)
            waiting.append({
                "execution_process_id": process_id,
                "code": code,
                "reason": "SELL_REPEAT_EXIT_EVIDENCE_PENDING",
                "waiting_reasons": exit_evaluation.get("waiting_reasons"),
            })
            continue
        if exit_only:
            continue

        snapshot = _repeat_snapshot(
            process_id=process_id,
            generation=generation,
            policy=policy,
            latest_orders=latest_orders,
            position=position,
            holding=holding,
        )
        snapshot_hash = _text(snapshot.get("snapshot_hash"))
        used_hashes = {
            _text(_intent(item).get("repeat_source_snapshot_hash"))
            for item in process_orders
            if _text(_intent(item).get("repeat_source_snapshot_hash"))
        }
        used_hashes.update(
            _text(item.get("repeat_source_snapshot_hash"))
            for item in all_signal_intents
            if _text(item.get("repeat_source_snapshot_hash"))
        )
        if snapshot_hash in used_hashes:
            waiting.append({"execution_process_id": process_id, "code": code, "reason": "SELL_REPEAT_SNAPSHOT_ALREADY_USED"})
            continue

        current_price = prices.get(code)
        execution_template = _as_dict(policy.get("execution_template"))
        hoga = _text(execution_template.get("hoga")).upper()
        mode = _text(execution_template.get("execution_mode")).upper()
        ratio_sources = {
            _text(execution_template.get("ratio_left")).upper(),
            _text(execution_template.get("ratio_right")).upper(),
        }
        reset_sources = {
            _text(_as_dict(policy.get("sell_price_reset_policy")).get("left_source")).upper(),
            _text(_as_dict(policy.get("sell_price_reset_policy")).get("right_source")).upper(),
        }
        needs_current = (
            hoga != "MARKET"
            or mode == "MULTI_HOGA"
            or "CURRENT_PRICE" in ratio_sources
            or bool({"ORDER_PRICE", "CURRENT_PRICE"} & reset_sources)
        )
        if needs_current and current_price is None:
            waiting.append({"execution_process_id": process_id, "code": code, "reason": "SELL_REPEAT_CURRENT_PRICE_UNAVAILABLE"})
            continue
        try:
            generation_template = _template_for_generation(
                policy=policy,
                base_intent=template,
                quantity=available_quantity,
                current_price=current_price,
            )
            generation_template.update({
                "repeat_plan_snapshot_hash": policy.get("plan_snapshot_hash"),
                "repeat_parent_generation": generation,
                "repeat_generation": True,
                "repeat_started_at": (
                    repeat_started_at or current_at
                ).isoformat(timespec="milliseconds"),
            })
            intents = build_sell_generation_intents(
                template=generation_template,
                source_signal_id=signal_id,
                process_id=process_id,
                option_snapshot_hash=next(iter(option_hashes)),
                generation=generation + 1,
                quantity=available_quantity,
                current_price=current_price,
                source_snapshot_hash=snapshot_hash,
                generated_at=current_at,
                source_snapshot_field="repeat_source_snapshot_hash",
            )
        except ValueError as exc:
            reviews.append(_review(
                process_id=process_id, signal_id=signal_id, code=code, name=name,
                reasons=[str(exc) or "SELL_REPEAT_PLAN_INVALID"],
            ))
            continue
        signal_proposal = deepcopy(signal)
        signal_proposal.update({
            "id": signal_id,
            "code": code,
            "name": name,
            "signal": "SELL",
            "status": "PENDING",
            "execution_intent": intents[0],
            "execution_intents": intents,
        })
        proposals.append({
            "execution_process_id": process_id,
            "source_signal_id": signal_id,
            "code": code,
            "plan_generation": generation + 1,
            "latest_sellable_quantity": available_quantity,
            "repeat_source_snapshot_hash": snapshot_hash,
            "repeat_snapshot": snapshot,
            "signal": signal_proposal,
            "execution_intents": intents,
        })

    return {
        "ok": not bool(errors),
        "proposals": proposals,
        "exit_proposals": exit_proposals,
        "blocked_execution_process_ids": sorted(exit_blocked_processes),
        "reviews": reviews,
        "waiting": waiting,
        "errors": errors,
    }


def inspect_sell_repeat_exits(**kwargs: Any) -> dict[str, Any]:
    """Inspect only repeat-exit precedence without proposing a repeat generation."""
    return inspect_sell_repeat_generations(exit_only=True, **kwargs)


__all__ = ["inspect_sell_repeat_exits", "inspect_sell_repeat_generations"]
