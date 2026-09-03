# -*- coding: utf-8 -*-
"""Read-only BUY/SELL price-reset orchestration over durable execution evidence."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
import json
from pathlib import Path
from typing import Any

from execution_price_comparison import (
    evaluate_percent_comparison,
    positive_price,
    resolve_price_source,
)
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
HOLDINGS_PATH = RUNTIME_DIR / "broker_holdings.json"
SIGNALS_PATH = RUNTIME_DIR / "routine_signals.json"

_OPEN = {"BROKER_ACCEPTED", "PARTIALLY_FILLED"}
_PRE_DISPATCH = {
    "ORDER_QUEUED", "APPROVED", "EXECUTABLE", "DISPATCH_CLAIMED",
    "SEND_ATTEMPTED", "SEND_CALL_IN_PROGRESS", "SEND_CALL_ACCEPTED",
}
_TERMINAL = {
    "FILLED", "CANCELLED", "CANCELED", "PARTIAL_CANCELLED",
    "BROKER_REJECTED", "SEND_CALL_REJECTED", "REJECTED",
}
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
    policy = _as_dict(intent.get("sell_price_reset_policy"))
    if (
        _text(policy.get("policy")).upper() == "SELL_PRICE_CHANGE_RESET"
        and _text(policy.get("action")).upper() == "RESET"
    ):
        return policy
    return {}


def _split(total: int, count: int) -> list[int]:
    quotient, remainder = divmod(total, count)
    return [quotient + (1 if index < remainder else 0) for index in range(count)]


def _review(
    *, process_id: str, signal_id: str, code: str, name: str, reasons: list[str]
) -> dict[str, Any]:
    return {
        "execution_process_id": process_id,
        "source_signal_id": signal_id,
        "code": code,
        "name": name,
        "review_reasons": sorted(set(reasons)),
        "review_location": "SELL_PRICE_RESET_RECONCILIATION",
    }


def _reset_snapshot(
    *,
    process_id: str,
    generation: int,
    policy: dict[str, Any],
    left_price: float,
    right_price: float,
    observed_percent: float,
    current_price: float | None,
    average_price: float | None,
    latest_orders: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    payload = {
        "execution_process_id": process_id,
        "source_plan_generation": generation,
        "policy": policy,
        "left_price": left_price,
        "right_price": right_price,
        "observed_percent": observed_percent,
        "current_price": current_price,
        "average_price": average_price,
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
    }
    return {**payload, "snapshot_hash": stable_hash(payload)}


def _clean_template(template: dict[str, Any]) -> dict[str, Any]:
    intent = deepcopy(template)
    for field in (
        "execution_id", "provenance_approved_at", "process_record",
        "schedule_anchor_at", "scheduled_at",
    ):
        intent.pop(field, None)
    return intent


def build_sell_generation_intents(
    *,
    template: dict[str, Any],
    source_signal_id: str,
    process_id: str,
    option_snapshot_hash: str,
    generation: int,
    quantity: int,
    current_price: float | None,
    source_snapshot_hash: str,
    generated_at: datetime,
    source_snapshot_field: str = "price_reset_source_snapshot_hash",
) -> list[dict[str, Any]]:
    """Materialize one later SELL generation with caller-owned evidence identity."""
    if source_snapshot_field not in {
        "price_reset_source_snapshot_hash",
        "repeat_source_snapshot_hash",
    }:
        raise ValueError("SELL_GENERATION_SOURCE_SNAPSHOT_FIELD_INVALID")
    mode = _text(template.get("execution_mode")).upper() or "SINGLE_ORDER"
    common = _clean_template(template)
    common.update(
        {
            "source_signal_id": source_signal_id,
            "execution_process_id": process_id,
            "execution_process_owner_required": False,
            "option_snapshot_hash": option_snapshot_hash,
            "plan_generation": generation,
            "planned_total_quantity": quantity,
            source_snapshot_field: source_snapshot_hash,
        }
    )
    reset_policy = deepcopy(_policy(common))
    if reset_policy:
        if current_price is not None:
            reset_policy["order_price"] = current_price
        common["sell_price_reset_policy"] = reset_policy
    intents: list[dict[str, Any]] = []

    if mode == "MULTI_HOGA":
        plan = deepcopy(_as_dict(template.get("multi_hoga_plan")))
        offsets = plan.get("hoga_offsets")
        if not isinstance(offsets, list) or not offsets or any(
            isinstance(value, bool) or not isinstance(value, int) for value in offsets
        ):
            raise ValueError("SELL_PRICE_RESET_HOGA_OFFSETS_INVALID")
        if offsets[0] != 0 or len(offsets) != len(set(offsets)):
            raise ValueError("SELL_PRICE_RESET_HOGA_OFFSETS_INVALID")
        if current_price is None:
            raise ValueError("SELL_PRICE_RESET_CURRENT_PRICE_UNAVAILABLE")
        if quantity < len(offsets):
            offsets = [0]
        instrument = plan.get("instrument_type") or "STOCK"
        prices = [
            move_krx_price_by_ticks(current_price, offset, instrument_type=instrument)
            for offset in offsets
        ]
        quantities = _split(quantity, len(offsets))
        plan.update(
            {
                "base_price": current_price,
                "hoga_offsets": list(offsets),
                "planned_child_count": len(offsets),
                "planned_total_quantity": quantity,
            }
        )
        for index, (offset, child_quantity, price) in enumerate(
            zip(offsets, quantities, prices), start=1
        ):
            intents.append(
                {
                    **deepcopy(common),
                    "quantity": child_quantity,
                    "price": price,
                    "hoga": "LIMIT",
                    "price_basis": "ORDER_PRICE",
                    "child_sequence_index": index,
                    "child_sequence_total": len(offsets),
                    "child_kind": "HOGA_LEVEL",
                    "multi_hoga_plan": deepcopy(plan),
                    "child_plan": {
                        "planned_quantity": child_quantity,
                        "planned_price": price,
                        "hoga_offset_ticks": offset,
                        "plan_generation": generation,
                        source_snapshot_field: source_snapshot_hash,
                    },
                }
            )
    elif mode == "MULTI_TIME":
        plan = deepcopy(_as_dict(template.get("multi_time_plan")))
        offsets = plan.get("scheduled_offsets_ms")
        configured_count = _positive_int(plan.get("configured_child_count"))
        if not isinstance(offsets, list) or not offsets or configured_count is None:
            raise ValueError("SELL_PRICE_RESET_TIME_PLAN_INVALID")
        child_count = min(configured_count, quantity)
        offsets = offsets[:child_count]
        if len(offsets) != child_count or any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in offsets
        ):
            raise ValueError("SELL_PRICE_RESET_TIME_PLAN_INVALID")
        if current_price is None:
            raise ValueError("SELL_PRICE_RESET_CURRENT_PRICE_UNAVAILABLE")
        quantities = _split(quantity, child_count)
        anchor = generated_at.isoformat(timespec="milliseconds")
        plan.update(
            {
                "planned_child_count": child_count,
                "planned_total_quantity": quantity,
                "scheduled_offsets_ms": list(offsets),
            }
        )
        for index, (offset, child_quantity) in enumerate(zip(offsets, quantities), start=1):
            scheduled_at = (generated_at + timedelta(milliseconds=offset)).isoformat(timespec="milliseconds")
            intents.append(
                {
                    **deepcopy(common),
                    "quantity": child_quantity,
                    "price": current_price,
                    "hoga": "LIMIT",
                    "child_sequence_index": index,
                    "child_sequence_total": child_count,
                    "child_kind": "TIME_SLICE",
                    "multi_time_plan": deepcopy(plan),
                    "schedule_anchor_at": anchor,
                    "scheduled_at": scheduled_at,
                    "child_plan": {
                        "planned_quantity": child_quantity,
                        "planned_price": current_price,
                        "scheduled_offset_ms": offset,
                        "schedule_anchor_at": anchor,
                        "scheduled_at": scheduled_at,
                        "plan_generation": generation,
                        source_snapshot_field: source_snapshot_hash,
                    },
                }
            )
    elif mode == "MULTI_RATIO":
        plan = deepcopy(_as_dict(template.get("multi_ratio_plan")))
        configured_count = _positive_int(plan.get("configured_child_count"))
        if configured_count is None:
            raise ValueError("SELL_PRICE_RESET_RATIO_PLAN_INVALID")
        child_count = min(configured_count, quantity)
        quantities = _split(quantity, child_count)
        if "ORDER_PRICE" in {
            _text(plan.get("ratio_left")).upper(),
            _text(plan.get("ratio_right")).upper(),
        } and current_price is None:
            raise ValueError("SELL_PRICE_RESET_CURRENT_PRICE_UNAVAILABLE")
        plan.update(
            {
                "planned_child_count": child_count,
                "planned_total_quantity": quantity,
                "order_price": current_price,
            }
        )
        hoga = _text(template.get("hoga")).upper() or "LIMIT"
        price = None if hoga == "MARKET" else current_price
        if hoga != "MARKET" and price is None:
            raise ValueError("SELL_PRICE_RESET_CURRENT_PRICE_UNAVAILABLE")
        for index, child_quantity in enumerate(quantities, start=1):
            intents.append(
                {
                    **deepcopy(common),
                    "quantity": child_quantity,
                    "price": price,
                    "hoga": hoga,
                    "child_sequence_index": index,
                    "child_sequence_total": child_count,
                    "child_kind": "RATIO_SLICE",
                    "multi_ratio_plan": deepcopy(plan),
                    "child_plan": {
                        "planned_quantity": child_quantity,
                        "planned_price": price,
                        "ratio_step_index": index,
                        "plan_generation": generation,
                        source_snapshot_field: source_snapshot_hash,
                    },
                }
            )
    elif mode in {"SINGLE_ORDER", "SINGLE"}:
        hoga = _text(template.get("hoga")).upper() or "LIMIT"
        price = None if hoga == "MARKET" else current_price
        if hoga != "MARKET" and price is None:
            raise ValueError("SELL_PRICE_RESET_CURRENT_PRICE_UNAVAILABLE")
        intents.append(
            {
                **deepcopy(common),
                "execution_mode": "SINGLE_ORDER",
                "quantity": quantity,
                "price": price,
                "hoga": hoga,
                "child_sequence_index": 1,
                "child_sequence_total": 1,
                "child_kind": "SINGLE_ORDER",
                "child_plan": {
                    "planned_quantity": quantity,
                    "planned_price": price,
                    "plan_generation": generation,
                    source_snapshot_field: source_snapshot_hash,
                },
            }
        )
    else:
        raise ValueError(f"SELL_PRICE_RESET_EXECUTION_MODE_UNSUPPORTED:{mode}")

    return materialize_execution_intent_children(
        intents,
        source_signal_id=source_signal_id,
        execution_process_id=process_id,
        plan_generation_value=generation,
    )


def _buy_price_reset_policy(intent: dict[str, Any]) -> dict[str, Any]:
    policy = _as_dict(intent.get("buy_price_reset_policy"))
    if (
        policy.get("enabled") is True
        and _text(policy.get("policy")).upper() == "BUY_PRICE_CHANGE_RESET"
        and _text(policy.get("action")).upper() == "RESET"
    ):
        return policy
    return {}


def _record_order_price(record: dict[str, Any]) -> float | None:
    intent = _intent(record)
    for source in (record, intent, _as_dict(record.get("child_plan")), _as_dict(intent.get("child_plan"))):
        value = positive_price(source.get("price") or source.get("planned_price") or source.get("order_price"))
        if value is not None:
            return value
    return None


def _clean_buy_template(template: dict[str, Any]) -> dict[str, Any]:
    value = deepcopy(template)
    for field in ("execution_id", "provenance_approved_at", "process_record", "schedule_anchor_at", "scheduled_at"):
        value.pop(field, None)
    return value


def build_buy_generation_intents(
    *,
    template: dict[str, Any],
    source_signal_id: str,
    process_id: str,
    option_snapshot_hash: str,
    generation: int,
    buy_round: int,
    remaining_budget: float,
    current_price: float | None,
    source_snapshot_hash: str,
    generated_at: datetime,
) -> list[dict[str, Any]]:
    """Materialize a same-round BUY reset generation from remaining budget."""
    mode = _text(template.get("execution_mode")).upper() or "SINGLE_ORDER"
    price_basis = _text(template.get("price_basis") or template.get("order_price_basis")).upper()
    frozen_price = _record_order_price(template)
    base_price = current_price if price_basis == "CURRENT_PRICE" or frozen_price is None else frozen_price
    if base_price is None or base_price <= 0:
        raise ValueError("BUY_PRICE_RESET_CURRENT_PRICE_UNAVAILABLE")
    budget = float(remaining_budget)
    quantity = int(budget // base_price)
    if quantity <= 0:
        raise ValueError("BUY_PRICE_RESET_REMAINING_BUDGET_BELOW_ONE_SHARE")
    common = _clean_buy_template(template)
    common.update({
        "side": "BUY",
        "source_signal_id": source_signal_id,
        "execution_process_id": process_id,
        "option_snapshot_hash": option_snapshot_hash,
        "plan_generation": generation,
        "buy_round": buy_round,
        "buy_phase": "BASE" if buy_round == 1 else "REPEAT",
        "planned_total_quantity": quantity,
        "price": base_price,
        "budget": quantity * base_price,
        "buy_price_reset_source_snapshot_hash": source_snapshot_hash,
    })
    intents: list[dict[str, Any]] = []
    if mode == "MULTI_HOGA":
        plan = deepcopy(_as_dict(template.get("multi_hoga_plan")))
        offsets = plan.get("hoga_offsets")
        if not isinstance(offsets, list) or not offsets or any(isinstance(v, bool) or not isinstance(v, int) for v in offsets):
            raise ValueError("BUY_PRICE_RESET_HOGA_PLAN_INVALID")
        if offsets[0] != 0 or len(offsets) != len(set(offsets)):
            raise ValueError("BUY_PRICE_RESET_HOGA_PLAN_INVALID")
        if quantity < len(offsets):
            raise ValueError("BUY_PRICE_RESET_QUANTITY_BELOW_CHILD_COUNT")
        instrument = plan.get("instrument_type") or "STOCK"
        prices = [move_krx_price_by_ticks(base_price, offset, instrument_type=instrument) for offset in offsets]
        quantities = _split(quantity, len(offsets))
        plan.update({"base_price": base_price, "planned_child_count": len(offsets), "planned_total_quantity": quantity})
        for index, (offset, child_quantity, price) in enumerate(zip(offsets, quantities, prices), 1):
            intents.append({
                **deepcopy(common), "price": price, "budget": child_quantity * price,
                "quantity": child_quantity, "hoga": "LIMIT", "price_basis": "ORDER_PRICE",
                "hoga_mode": "MULTI", "execution_mode": "MULTI_HOGA",
                "child_sequence_index": index, "child_sequence_total": len(offsets), "child_kind": "HOGA_LEVEL",
                "multi_hoga_plan": deepcopy(plan),
                "child_plan": {"planned_quantity": child_quantity, "planned_price": price, "hoga_offset_ticks": offset,
                                "plan_generation": generation, "buy_price_reset_source_snapshot_hash": source_snapshot_hash},
            })
    elif mode == "MULTI_TIME":
        plan = deepcopy(_as_dict(template.get("multi_time_plan")))
        offsets = plan.get("scheduled_offsets_ms")
        count = _positive_int(plan.get("configured_child_count"))
        if not isinstance(offsets, list) or not offsets or count is None or len(offsets) < count:
            raise ValueError("BUY_PRICE_RESET_TIME_PLAN_INVALID")
        if quantity < count:
            raise ValueError("BUY_PRICE_RESET_QUANTITY_BELOW_CHILD_COUNT")
        offsets = offsets[:count]
        quantities = _split(quantity, count)
        anchor = generated_at.isoformat(timespec="milliseconds")
        plan.update({"planned_child_count": count, "planned_total_quantity": quantity})
        for index, (offset, child_quantity) in enumerate(zip(offsets, quantities), 1):
            scheduled_at = (generated_at + timedelta(milliseconds=offset)).isoformat(timespec="milliseconds")
            intents.append({
                **deepcopy(common), "price": base_price, "budget": child_quantity * base_price,
                "quantity": child_quantity, "hoga": "LIMIT", "price_basis": price_basis,
                "execution_mode": "MULTI_TIME", "child_sequence_index": index, "child_sequence_total": count,
                "child_kind": "TIME_SLICE", "multi_time_plan": deepcopy(plan),
                "schedule_anchor_at": anchor, "scheduled_at": scheduled_at,
                "child_plan": {"planned_quantity": child_quantity, "planned_price": base_price,
                                "scheduled_offset_ms": offset, "schedule_anchor_at": anchor, "scheduled_at": scheduled_at,
                                "plan_generation": generation, "buy_price_reset_source_snapshot_hash": source_snapshot_hash},
            })
    elif mode == "MULTI_RATIO":
        plan = deepcopy(_as_dict(template.get("multi_ratio_plan")))
        count = _positive_int(plan.get("configured_child_count"))
        if count is None:
            raise ValueError("BUY_PRICE_RESET_RATIO_PLAN_INVALID")
        if quantity < count:
            raise ValueError("BUY_PRICE_RESET_QUANTITY_BELOW_CHILD_COUNT")
        quantities = _split(quantity, count)
        plan.update({"planned_child_count": count, "planned_total_quantity": quantity, "order_price": base_price})
        for index, child_quantity in enumerate(quantities, 1):
            intents.append({
                **deepcopy(common), "price": base_price, "budget": child_quantity * base_price,
                "quantity": child_quantity, "hoga": "LIMIT", "price_basis": price_basis,
                "execution_mode": "MULTI_RATIO", "child_sequence_index": index, "child_sequence_total": count,
                "child_kind": "RATIO_SLICE", "multi_ratio_plan": deepcopy(plan),
                "child_plan": {"planned_quantity": child_quantity, "planned_price": base_price,
                                "ratio_step_index": index, "plan_generation": generation,
                                "buy_price_reset_source_snapshot_hash": source_snapshot_hash},
            })
    elif mode in {"SINGLE", "SINGLE_ORDER"}:
        hoga = _text(template.get("hoga")).upper() or "LIMIT"
        intents.append({
            **deepcopy(common), "execution_mode": "SINGLE_ORDER", "quantity": quantity,
            "hoga": hoga, "price": None if hoga == "MARKET" else base_price,
            "price_basis": "MARKET" if hoga == "MARKET" else price_basis,
            "child_sequence_index": 1, "child_sequence_total": 1, "child_kind": "SINGLE_ORDER",
            "child_plan": {"planned_quantity": quantity, "planned_price": None if hoga == "MARKET" else base_price,
                            "plan_generation": generation, "buy_price_reset_source_snapshot_hash": source_snapshot_hash},
        })
    else:
        raise ValueError(f"BUY_PRICE_RESET_EXECUTION_MODE_UNSUPPORTED:{mode}")
    return materialize_execution_intent_children(
        intents, source_signal_id=source_signal_id, execution_process_id=process_id,
        plan_generation_value=generation,
    )


def _buy_review(*, process_id: str, signal_id: str, code: str, name: str, reasons: list[str]) -> dict[str, Any]:
    return {
        "execution_process_id": process_id,
        "source_signal_id": signal_id,
        "code": code,
        "name": name,
        "review_reasons": sorted(set(reasons)),
        "review_location": "BUY_PRICE_RESET_RECONCILIATION",
    }


def inspect_buy_price_resets(
    *,
    selected_account_no: str,
    actionable_prices_by_code: dict[str, Any] | None,
    allowed_stock_codes: tuple[str, ...] | list[str] | set[str] | None = None,
    blocked_execution_process_ids: tuple[str, ...] | list[str] | set[str] | None = None,
    current_orderable_cash: float | None = None,
    now: datetime | None = None,
    cancel_limit: int = 5,
    order_queue_path: str | Path = ORDER_QUEUE_PATH,
    order_executions_path: str | Path = ORDER_EXECUTIONS_PATH,
    fills_path: str | Path = FILLS_PATH,
    positions_path: str | Path = POSITIONS_PATH,
    holdings_path: str | Path = HOLDINGS_PATH,
    signals_path: str | Path = SIGNALS_PATH,
) -> dict[str, Any]:
    """Inspect BUY price-reset evidence without mutating durable state."""
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
        "ok": not errors, "cancel_proposals": [], "replan_proposals": [],
        "reviews": [], "waiting": [], "errors": errors,
        "blocked_execution_process_ids": [],
    }
    if errors:
        return empty
    account_no = _text(selected_account_no)
    allowed = ({_text(value) for value in allowed_stock_codes if _text(value)}
               if allowed_stock_codes is not None else None)
    if not account_no or (allowed_stock_codes is not None and not allowed):
        return empty
    current_at = now or datetime.now()
    prices = {_text(code): positive_price(value)
              for code, value in (actionable_prices_by_code or {}).items() if _text(code)}
    originals = [item for item in loaded["orders"] if _action(item) in {"NEW", "MODIFY"}
                 and _text(item.get("side") or _intent(item).get("side")).upper() == "BUY"]
    cancels = [item for item in loaded["orders"] if _action(item) == "CANCEL"]
    by_process: dict[str, list[dict[str, Any]]] = {}
    for order in originals:
        intent = _intent(order)
        process_id = _text(order.get("execution_process_id") or intent.get("execution_process_id"))
        if process_id and _buy_price_reset_policy(intent):
            by_process.setdefault(process_id, []).append(order)
    result = {**empty, "ok": True}
    max_cancels = max(0, int(cancel_limit or 0))
    externally_blocked = {
        _text(value) for value in (blocked_execution_process_ids or []) if _text(value)
    }
    blocked: set[str] = set(externally_blocked)

    for process_id, process_orders in by_process.items():
        if process_id in externally_blocked:
            result["waiting"].append({
                "execution_process_id": process_id,
                "reason": "BUY_PRICE_RESET_BLOCKED_BY_HIGHER_PRIORITY_POLICY",
            })
            continue
        source_ids = {_text(item.get("source_signal_id") or _intent(item).get("source_signal_id"))
                      for item in process_orders}
        source_ids.discard("")
        signal_id = next(iter(source_ids), "")
        signal = next((item for item in loaded["signals"] if _text(item.get("id")) == signal_id), {})
        representative = process_orders[0]
        code = _text(representative.get("code") or _preview(representative).get("code") or signal.get("code"))
        name = _text(representative.get("name") or signal.get("name"))
        if allowed is not None and code not in allowed:
            continue
        existing_exit = _as_dict(signal.get("buy_exit_evidence"))
        if existing_exit:
            blocked.add(process_id)
            if (
                existing_exit.get("buy_phase_completed") is True
                and _text(existing_exit.get("execution_process_id")) == process_id
                and _text(existing_exit.get("source_signal_id")) == signal_id
            ):
                result["waiting"].append({
                    "execution_process_id": process_id,
                    "code": code,
                    "reason": "BUY_PRICE_RESET_BLOCKED_BY_COMPLETED_EXIT",
                })
            else:
                result["reviews"].append(_review(
                    process_id=process_id, signal_id=signal_id, code=code, name=name,
                    reasons=["BUY_PRICE_RESET_EXIT_EVIDENCE_IDENTITY_INVALID"],
                ))
            continue
        reasons: list[str] = []
        if len(source_ids) != 1:
            reasons.append("BUY_PRICE_RESET_SOURCE_SIGNAL_ID_MISMATCH")
        if not signal:
            reasons.append("BUY_PRICE_RESET_SOURCE_SIGNAL_MISSING")
        account_values = {_text(item.get("account_no") or _preview(item).get("account_no"))
                         for item in process_orders}
        if account_values != {account_no}:
            reasons.append("BUY_PRICE_RESET_ACCOUNT_IDENTITY_MISMATCH")
        code_values = {_text(item.get("code") or _preview(item).get("code"))
                       for item in process_orders if _text(item.get("code") or _preview(item).get("code"))}
        if code_values and code_values != {code}:
            reasons.append("BUY_PRICE_RESET_CODE_IDENTITY_MISMATCH")
        generations: dict[int, dict[str, list[dict[str, Any]]]] = {}
        for order in process_orders:
            intent = _intent(order)
            generation = plan_generation(order.get("plan_generation", intent.get("plan_generation")))
            execution_id = _text(order.get("execution_id") or intent.get("execution_id"))
            if not execution_id:
                reasons.append("BUY_PRICE_RESET_EXECUTION_ID_MISSING")
                continue
            generations.setdefault(generation, {}).setdefault(execution_id, []).append(order)
        if not generations:
            continue
        generation = max(generations)
        latest_orders = {execution_id: _latest(records)
                         for execution_id, records in generations[generation].items()}
        signal_intents = [deepcopy(item) for item in signal.get("execution_intents", [])
                          if isinstance(item, dict)
                          and _text(item.get("execution_process_id")) == process_id
                          and plan_generation(item.get("plan_generation")) == generation]
        template = signal_intents[0] if signal_intents else _intent(next(iter(latest_orders.values())))
        policy = _buy_price_reset_policy(template)
        if not policy:
            reasons.append("BUY_PRICE_RESET_POLICY_MISMATCH")
        option_hashes = {_text(item.get("option_snapshot_hash") or _intent(item).get("option_snapshot_hash"))
                        for item in process_orders}
        option_hashes.discard("")
        if len(option_hashes) != 1:
            reasons.append("BUY_PRICE_RESET_OPTION_SNAPSHOT_HASH_MISMATCH")
        round_values = {_nonnegative_int(item.get("buy_round") or _intent(item).get("buy_round"))
                       for item in process_orders}
        round_values.discard(None)
        if len(round_values) > 1:
            reasons.append("BUY_PRICE_RESET_BUY_ROUND_MISMATCH")
        cycle_values = {_text(item.get("cycle_identity") or _intent(item).get("cycle_identity"))
                        for item in process_orders}
        cycle_values.discard("")
        if len(cycle_values) > 1:
            reasons.append("BUY_PRICE_RESET_CYCLE_IDENTITY_MISMATCH")
        runtime_by_execution = {
            _text(item.get("execution_id")): item for item in loaded["executions"]
            if _text(item.get("execution_id"))
        }
        for execution_id in latest_orders:
            runtime = runtime_by_execution.get(execution_id)
            if runtime is None:
                reasons.append(f"BUY_PRICE_RESET_RUNTIME_EXECUTION_MISSING:{execution_id}")
            elif (_text(runtime.get("execution_process_id")) != process_id
                  or plan_generation(runtime.get("plan_generation")) != generation):
                reasons.append(f"BUY_PRICE_RESET_RUNTIME_IDENTITY_MISMATCH:{execution_id}")
        left_source = _text(policy.get("left_source")).upper()
        right_source = _text(policy.get("right_source")).upper()
        current_price = prices.get(code)
        order_price = positive_price(policy.get("order_price")) or _record_order_price(representative)
        position_matches = [item for item in loaded["positions"]
                            if _text(item.get("account_no")) == account_no and _text(item.get("code")) == code]
        average_price = positive_price(position_matches[0].get("average_price")) if len(position_matches) == 1 else None
        holding_matches = [item for item in loaded["holdings"]
                           if _text(item.get("account_no")) == account_no and _text(item.get("code")) == code]
        if len(position_matches) == 1 and len(holding_matches) == 1:
            position_qty = _nonnegative_int(position_matches[0].get("quantity"))
            holding_qty = _nonnegative_int(holding_matches[0].get("holding_quantity"))
            if position_qty is not None and holding_qty is not None and position_qty != holding_qty:
                reasons.append("BUY_PRICE_RESET_POSITION_BROKER_MISMATCH")
        if "CURRENT_PRICE" in {left_source, right_source} and current_price is None:
            result["waiting"].append({"execution_process_id": process_id, "code": code,
                                       "reason": "BUY_PRICE_RESET_CURRENT_PRICE_UNAVAILABLE"})
            blocked.add(process_id)
            continue
        left_price = resolve_price_source(left_source, order_price=order_price,
                                          current_price=current_price, average_price=average_price)
        right_price = resolve_price_source(right_source, order_price=order_price,
                                           current_price=current_price, average_price=average_price)
        threshold = positive_price(policy.get("threshold_percent"))
        triggered = False
        observed: float | None = None
        if not reasons:
            if left_price is None or right_price is None or threshold is None:
                reasons.append("BUY_PRICE_RESET_TRIGGER_SOURCE_INVALID")
            else:
                triggered, observed = evaluate_percent_comparison(
                    left=left_price, right=right_price,
                    direction=_text(policy.get("direction")).upper(),
                    compare=_text(policy.get("compare")).upper(), threshold=threshold,
                )
                if observed is None:
                    reasons.append("BUY_PRICE_RESET_TRIGGER_POLICY_INVALID")
        if reasons:
            result["reviews"].append(_buy_review(process_id=process_id, signal_id=signal_id,
                                                  code=code, name=name, reasons=reasons))
            blocked.add(process_id)
            continue
        if not triggered:
            result["waiting"].append({"execution_process_id": process_id, "code": code,
                                       "reason": "BUY_PRICE_RESET_THRESHOLD_NOT_MET",
                                       "observed_percent": observed, "threshold_percent": threshold})
            continue
        blocked.add(process_id)
        trigger_snapshot = _reset_snapshot(
            process_id=process_id, generation=generation, policy=policy,
            left_price=left_price or 0, right_price=right_price or 0,
            observed_percent=observed or 0, current_price=current_price,
            average_price=average_price, latest_orders=latest_orders,
        )
        snapshot_hash = _text(trigger_snapshot.get("snapshot_hash"))
        used_hashes = {_text(_intent(item).get("buy_price_reset_source_snapshot_hash"))
                       for item in process_orders}
        used_hashes.update(_text(item.get("buy_price_reset_source_snapshot_hash"))
                           for item in signal.get("execution_intents", []) if isinstance(item, dict))
        if snapshot_hash in used_hashes:
            result["waiting"].append({"execution_process_id": process_id, "code": code,
                                       "reason": "BUY_PRICE_RESET_SNAPSHOT_ALREADY_USED"})
            blocked.discard(process_id)
            continue
        process_cancels = [item for item in cancels
                           if _text(item.get("execution_process_id") or _as_dict(item.get("execution_request")).get("execution_process_id")) == process_id]
        active_cancels = [item for item in process_cancels
                          if _text(item.get("status")).upper() in _ACTIVE_CANCEL
                          and item.get("original_order_effect_confirmed") is not True]
        if any(_text(item.get("status")).upper() == "SEND_UNCERTAIN"
               or item.get("manual_reconciliation_required") is True for item in active_cancels):
            result["reviews"].append(_buy_review(process_id=process_id, signal_id=signal_id,
                                                  code=code, name=name,
                                                  reasons=["BUY_PRICE_RESET_CANCEL_SEND_UNCERTAIN"]))
            continue
        open_orders: list[dict[str, Any]] = []
        pre_dispatch: list[str] = []
        unsafe: list[str] = []
        for execution_id, order in latest_orders.items():
            status = _text(order.get("status")).upper()
            if status == "SEND_UNCERTAIN" or order.get("manual_reconciliation_required") is True:
                unsafe.append(f"BUY_PRICE_RESET_UNSAFE_ORDER:{execution_id}:{status or 'UNKNOWN'}")
            elif status in _PRE_DISPATCH:
                pre_dispatch.append(f"{execution_id}:{status}")
            elif status in _OPEN:
                open_orders.append(order)
            elif status not in _TERMINAL:
                unsafe.append(f"BUY_PRICE_RESET_STATUS_UNRESOLVED:{execution_id}:{status or 'UNKNOWN'}")
        if unsafe:
            result["reviews"].append(_buy_review(process_id=process_id, signal_id=signal_id,
                                                  code=code, name=name, reasons=unsafe))
            continue
        if pre_dispatch:
            result["waiting"].append({"execution_process_id": process_id, "code": code,
                                       "reason": "BUY_PRICE_RESET_ORDER_EVIDENCE_PENDING",
                                       "children": pre_dispatch})
            continue
        active_originals = {_original_no(item) for item in active_cancels if _original_no(item)}
        if open_orders:
            for order in open_orders:
                broker_no = _text(order.get("broker_order_no"))
                remaining = _positive_int(order.get("remaining_quantity"))
                if not broker_no or remaining is None:
                    result["reviews"].append(_buy_review(process_id=process_id, signal_id=signal_id,
                                                          code=code, name=name,
                                                          reasons=["BUY_PRICE_RESET_OPEN_ORDER_IDENTITY_INVALID"]))
                    continue
                if broker_no in active_originals:
                    continue
                result["cancel_proposals"].append({
                    "order_queued_id": _text(order.get("id")), "account_no": account_no,
                    "code": code, "side": "BUY", "broker_order_no": broker_no,
                    "remaining_quantity": remaining, "execution_process_id": process_id,
                    "source_signal_id": signal_id, "source_plan_generation": generation,
                    "trigger_snapshot": deepcopy(trigger_snapshot),
                })
                if len(result["cancel_proposals"]) >= max_cancels:
                    break
            result["waiting"].append({"execution_process_id": process_id, "code": code,
                                       "reason": "BUY_PRICE_RESET_CANCEL_REQUIRED"})
            continue
        if active_cancels:
            result["waiting"].append({"execution_process_id": process_id, "code": code,
                                       "reason": "BUY_PRICE_RESET_CANCEL_EFFECT_PENDING"})
            continue
        reset_cancels = [item for item in process_cancels
                         if _text(_cancel_evidence(item).get("trigger")).upper() == "BUY_PRICE_CHANGE_RESET"
                         and plan_generation(_cancel_evidence(item).get("source_plan_generation")) == generation]
        for cancel in reset_cancels:
            if _text(cancel.get("status")).upper() not in {"CANCELLED", "CANCELED", "PARTIAL_CANCELLED"}:
                result["waiting"].append({"execution_process_id": process_id, "code": code,
                                           "reason": "BUY_PRICE_RESET_CANCEL_EFFECT_PENDING"})
                break
        else:
            planned_budget = 0.0
            filled_cost = 0.0
            for execution_id, order in latest_orders.items():
                intent = _intent(order)
                qty = _positive_int(order.get("quantity") or intent.get("quantity")) or 0
                price = _record_order_price(order)
                budget_value = positive_price(order.get("budget")) or positive_price(intent.get("budget"))
                planned_budget += budget_value if budget_value is not None else ((qty * price) if price else 0)
                execution_fill_count = 0
                for fill in loaded["fills"]:
                    if _text(fill.get("execution_id")) != execution_id:
                        continue
                    fill_qty = _positive_int(fill.get("filled_quantity")) or 0
                    fill_price = positive_price(fill.get("filled_price") or fill.get("price")) or price or 0
                    filled_cost += fill_qty * fill_price
                    execution_fill_count += fill_qty
                    if _text(fill.get("execution_process_id")) not in {"", process_id}:
                        reasons.append(f"BUY_PRICE_RESET_FILL_PROCESS_MISMATCH:{execution_id}")
                if execution_fill_count == 0:
                    cumulative = _nonnegative_int(
                        order.get("total_filled_quantity")
                        or order.get("cumulative_filled_quantity")
                        or intent.get("total_filled_quantity")
                    )
                    if cumulative:
                        filled_cost += cumulative * (price or 0)
                    elif _text(order.get("status")).upper() == "FILLED":
                        reasons.append(f"BUY_PRICE_RESET_FILL_EVIDENCE_MISSING:{execution_id}")
            remaining_budget = round(max(0.0, planned_budget - filled_cost), 8)
            if current_orderable_cash is not None:
                cash = positive_price(current_orderable_cash)
                if cash is not None and remaining_budget > cash:
                    result["waiting"].append({"execution_process_id": process_id, "code": code,
                                               "reason": "BUY_PRICE_RESET_CASH_INSUFFICIENT",
                                               "remaining_round_budget": remaining_budget,
                                               "current_orderable_cash": cash})
                    continue
            if remaining_budget <= 0:
                result["waiting"].append({"execution_process_id": process_id, "code": code,
                                           "reason": "BUY_PRICE_RESET_REMAINING_BUDGET_EXHAUSTED"})
                continue
            if reasons:
                result["reviews"].append(_buy_review(process_id=process_id, signal_id=signal_id,
                                                      code=code, name=name, reasons=reasons))
                continue
            if len(option_hashes) != 1:
                result["reviews"].append(_buy_review(process_id=process_id, signal_id=signal_id,
                                                      code=code, name=name,
                                                      reasons=["BUY_PRICE_RESET_OPTION_SNAPSHOT_HASH_MISMATCH"]))
                continue
            buy_round_value = template.get("buy_round")
            if not isinstance(buy_round_value, int) or isinstance(buy_round_value, bool):
                buy_round_value = next(
                    (_nonnegative_int(item.get("buy_round") or _intent(item).get("buy_round"))
                     for item in process_orders
                     if _nonnegative_int(item.get("buy_round") or _intent(item).get("buy_round")) is not None),
                    0,
                )
            buy_round = int(buy_round_value or 0)
            try:
                intents = build_buy_generation_intents(
                    template=template, source_signal_id=signal_id,
                    process_id=process_id, option_snapshot_hash=next(iter(option_hashes)),
                    generation=generation + 1, buy_round=buy_round,
                    remaining_budget=remaining_budget, current_price=current_price,
                    source_snapshot_hash=snapshot_hash, generated_at=current_at,
                )
            except ValueError as exc:
                result["reviews"].append(_buy_review(process_id=process_id, signal_id=signal_id,
                                                      code=code, name=name, reasons=[str(exc)]))
                continue
            proposal_signal = deepcopy(signal)
            proposal_signal.update({"id": signal_id, "code": code, "name": name, "signal": "BUY",
                                     "status": "PENDING", "execution_intent": intents[0],
                                     "execution_intents": intents})
            result["replan_proposals"].append({
                "execution_process_id": process_id, "source_signal_id": signal_id,
                "code": code, "buy_round": buy_round, "plan_generation": generation + 1,
                "remaining_round_budget": remaining_budget, "trigger_snapshot_hash": snapshot_hash,
                "signal": proposal_signal, "execution_intents": intents,
            })
    result["blocked_execution_process_ids"] = sorted(blocked)
    result["cancel_proposals"] = result["cancel_proposals"][:max_cancels]
    return result


def inspect_sell_price_resets(
    *,
    selected_account_no: str,
    actionable_prices_by_code: dict[str, Any] | None,
    allowed_stock_codes: tuple[str, ...] | list[str] | set[str] | None = None,
    blocked_execution_process_ids: tuple[str, ...] | list[str] | set[str] | None = None,
    now: datetime | None = None,
    cancel_limit: int = 5,
    order_queue_path: str | Path = ORDER_QUEUE_PATH,
    order_executions_path: str | Path = ORDER_EXECUTIONS_PATH,
    fills_path: str | Path = FILLS_PATH,
    positions_path: str | Path = POSITIONS_PATH,
    holdings_path: str | Path = HOLDINGS_PATH,
    signals_path: str | Path = SIGNALS_PATH,
) -> dict[str, Any]:
    """Inspect reset triggers and return cancel-first or replan proposals without writes."""
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
        "ok": not errors, "cancel_proposals": [], "replan_proposals": [],
        "reviews": [], "waiting": [], "errors": errors,
        "blocked_execution_process_ids": [],
    }
    if errors:
        return empty
    account_no = _text(selected_account_no)
    allowed = (
        {_text(value) for value in allowed_stock_codes if _text(value)}
        if allowed_stock_codes is not None else None
    )
    if not account_no or (allowed_stock_codes is not None and not allowed):
        return empty
    current_at = now or datetime.now()
    current_cycle_blocked = {
        _text(value)
        for value in (blocked_execution_process_ids or [])
        if _text(value)
    }
    prices = {
        _text(code): positive_price(value)
        for code, value in (actionable_prices_by_code or {}).items()
        if _text(code)
    }
    orders = loaded["orders"]
    original_orders = [item for item in orders if _action(item) in {"NEW", "MODIFY"}]
    cancel_orders = [item for item in orders if _action(item) == "CANCEL"]
    by_process: dict[str, list[dict[str, Any]]] = {}
    for order in original_orders:
        intent = _intent(order)
        process_id = _text(order.get("execution_process_id") or intent.get("execution_process_id"))
        if process_id and _policy(intent):
            by_process.setdefault(process_id, []).append(order)

    cancel_proposals: list[dict[str, Any]] = []
    replan_proposals: list[dict[str, Any]] = []
    reviews: list[dict[str, Any]] = []
    waiting: list[dict[str, Any]] = []
    blocked_processes: set[str] = set()
    max_cancels = max(0, int(cancel_limit or 0))

    for process_id, process_orders in by_process.items():
        if process_id in current_cycle_blocked:
            waiting.append(
                {
                    "execution_process_id": process_id,
                    "reason": "SELL_PRICE_RESET_REPEAT_EXIT_PRECEDENCE",
                }
            )
            blocked_processes.add(process_id)
            continue
        source_ids = {_text(item.get("source_signal_id") or _intent(item).get("source_signal_id")) for item in process_orders}
        source_ids.discard("")
        signal_id = next(iter(source_ids), "")
        signal = next((item for item in loaded["signals"] if _text(item.get("id")) == signal_id), {})
        representative = process_orders[0]
        code = _text(representative.get("code") or _preview(representative).get("code") or signal.get("code"))
        name = _text(representative.get("name") or signal.get("name"))
        if allowed is not None and code not in allowed:
            continue
        existing_repeat_exit = _as_dict(signal.get("sell_repeat_exit_evidence"))
        if existing_repeat_exit:
            exit_process_id = _text(existing_repeat_exit.get("execution_process_id"))
            exit_signal_id = _text(existing_repeat_exit.get("source_signal_id"))
            if exit_process_id not in {"", process_id} or exit_signal_id not in {"", signal_id}:
                reviews.append(
                    _review(
                        process_id=process_id,
                        signal_id=signal_id,
                        code=code,
                        name=name,
                        reasons=["SELL_PRICE_RESET_REPEAT_EXIT_IDENTITY_MISMATCH"],
                    )
                )
                blocked_processes.add(process_id)
            else:
                waiting.append(
                    {
                        "execution_process_id": process_id,
                        "code": code,
                        "reason": "SELL_PRICE_RESET_REPEAT_EXITED",
                    }
                )
            continue
        reasons: list[str] = []
        if len(source_ids) != 1:
            reasons.append("SELL_PRICE_RESET_SOURCE_SIGNAL_ID_MISMATCH")
        if not signal:
            reasons.append("SELL_PRICE_RESET_SOURCE_SIGNAL_MISSING")
        account_values = {_text(item.get("account_no") or _preview(item).get("account_no")) for item in process_orders}
        if account_values != {account_no}:
            reasons.append("SELL_PRICE_RESET_ACCOUNT_IDENTITY_MISMATCH")
        generation_groups: dict[int, dict[str, list[dict[str, Any]]]] = {}
        for order in process_orders:
            intent = _intent(order)
            generation = plan_generation(order.get("plan_generation", intent.get("plan_generation")))
            execution_id = _text(order.get("execution_id") or intent.get("execution_id"))
            if not execution_id:
                reasons.append("SELL_PRICE_RESET_EXECUTION_ID_MISSING")
                continue
            generation_groups.setdefault(generation, {}).setdefault(execution_id, []).append(order)
        if not generation_groups:
            continue
        generation = max(generation_groups)
        all_signal_intents = [
            deepcopy(item) for item in signal.get("execution_intents", [])
            if isinstance(item, dict)
            and _text(item.get("execution_process_id")) == process_id
        ]
        signal_generations = {
            plan_generation(item.get("plan_generation")) for item in all_signal_intents
        }
        if signal_generations and max(signal_generations) > generation:
            pending_generation = max(signal_generations)
            pending_intents = [
                item for item in all_signal_intents
                if plan_generation(item.get("plan_generation")) == pending_generation
            ]
            if pending_intents and all(
                _text(item.get("price_reset_source_snapshot_hash"))
                or _text(item.get("repeat_source_snapshot_hash"))
                or _text(item.get("final_residual_exit_action_hash"))
                for item in pending_intents
            ):
                waiting.append(
                    {
                        "execution_process_id": process_id,
                        "code": code,
                        "reason": "SELL_PRICE_RESET_GENERATION_PENDING_EXECUTION",
                        "plan_generation": pending_generation,
                    }
                )
                continue
            reviews.append(
                _review(
                    process_id=process_id,
                    signal_id=signal_id,
                    code=code,
                    name=name,
                    reasons=["SELL_PRICE_RESET_SIGNAL_GENERATION_AHEAD_WITHOUT_IDENTITY"],
                )
            )
            blocked_processes.add(process_id)
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
        if signal_intents:
            reasons.extend(
                f"SELL_PRICE_RESET_CHILD_SET_INVALID:{issue}"
                for issue in validate_child_set(signal_intents)
            )
        policies = {_text(stable_hash(_policy(_intent(order)))) for order in latest_orders.values()}
        if len(policies) != 1 or not _policy(template):
            reasons.append("SELL_PRICE_RESET_POLICY_MISMATCH")
        policy = _policy(template)

        process_cancels = [
            item for item in cancel_orders
            if _text(item.get("execution_process_id") or _as_dict(item.get("execution_request")).get("execution_process_id")) == process_id
        ]
        reset_cancels = [
            item for item in process_cancels
            if _text(_cancel_evidence(item).get("trigger")).upper() == "SELL_PRICE_CHANGE_RESET"
            and plan_generation(_cancel_evidence(item).get("source_plan_generation")) == generation
        ]
        frozen_hashes = {
            _text(_cancel_evidence(item).get("trigger_snapshot_hash"))
            for item in reset_cancels if _text(_cancel_evidence(item).get("trigger_snapshot_hash"))
        }
        if len(frozen_hashes) > 1:
            reasons.append("SELL_PRICE_RESET_TRIGGER_SNAPSHOT_CONFLICT")

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
        average_price = positive_price(position.get("average_price"))
        current_price = prices.get(code)
        left_source = _text(policy.get("left_source")).upper()
        right_source = _text(policy.get("right_source")).upper()
        if "CURRENT_PRICE" in {left_source, right_source} and current_price is None and not frozen_hashes:
            waiting.append({"execution_process_id": process_id, "code": code, "reason": "SELL_PRICE_RESET_CURRENT_PRICE_UNAVAILABLE"})
            continue
        if "AVG_PRICE" in {left_source, right_source} and average_price is None and not frozen_hashes:
            reasons.append("SELL_PRICE_RESET_AVERAGE_PRICE_UNAVAILABLE")
        left_price = resolve_price_source(
            left_source,
            order_price=positive_price(policy.get("order_price")),
            current_price=current_price,
            average_price=average_price,
        )
        right_price = resolve_price_source(
            right_source,
            order_price=positive_price(policy.get("order_price")),
            current_price=current_price,
            average_price=average_price,
        )
        threshold = positive_price(policy.get("threshold_percent"))
        triggered: bool | None = None
        observed: float | None = None
        if not frozen_hashes and not reasons:
            if left_price is None or right_price is None or threshold is None:
                reasons.append("SELL_PRICE_RESET_TRIGGER_SOURCE_INVALID")
            else:
                triggered, observed = evaluate_percent_comparison(
                    left=left_price,
                    right=right_price,
                    direction=_text(policy.get("direction")).upper(),
                    compare=_text(policy.get("compare")).upper(),
                    threshold=threshold,
                )
                if triggered is None or observed is None:
                    reasons.append("SELL_PRICE_RESET_TRIGGER_POLICY_INVALID")
        if reasons:
            reviews.append(_review(process_id=process_id, signal_id=signal_id, code=code, name=name, reasons=reasons))
            blocked_processes.add(process_id)
            continue
        if not frozen_hashes and not triggered:
            waiting.append({
                "execution_process_id": process_id, "code": code,
                "reason": "SELL_PRICE_RESET_THRESHOLD_NOT_MET",
                "observed_percent": observed, "threshold_percent": threshold,
            })
            continue
        blocked_processes.add(process_id)

        unsafe = []
        pre_dispatch = []
        open_orders: list[dict[str, Any]] = []
        for execution_id, order in latest_orders.items():
            status = _text(order.get("status")).upper()
            if status == "SEND_UNCERTAIN" or order.get("manual_reconciliation_required") is True:
                unsafe.append(f"SELL_PRICE_RESET_UNSAFE_ORDER:{execution_id}:{status or 'UNKNOWN'}")
            elif status in _PRE_DISPATCH:
                pre_dispatch.append(f"{execution_id}:{status}")
            elif status in _OPEN:
                open_orders.append(order)
            elif status not in _TERMINAL:
                unsafe.append(f"SELL_PRICE_RESET_STATUS_UNRESOLVED:{execution_id}:{status or 'UNKNOWN'}")
        active_cancels = [
            item for item in process_cancels
            if _text(item.get("status")).upper() in _ACTIVE_CANCEL
            and item.get("original_order_effect_confirmed") is not True
        ]
        uncertain_cancels = [
            item for item in active_cancels
            if _text(item.get("status")).upper() == "SEND_UNCERTAIN"
            or item.get("manual_reconciliation_required") is True
        ]
        if unsafe or uncertain_cancels:
            reasons = unsafe or ["SELL_PRICE_RESET_CANCEL_SEND_UNCERTAIN"]
            reviews.append(_review(process_id=process_id, signal_id=signal_id, code=code, name=name, reasons=reasons))
            continue
        if pre_dispatch:
            waiting.append({"execution_process_id": process_id, "code": code, "reason": "SELL_PRICE_RESET_ORDER_EVIDENCE_PENDING", "children": pre_dispatch})
            continue

        if frozen_hashes:
            snapshot_hash = next(iter(frozen_hashes))
            trigger_snapshot = {"snapshot_hash": snapshot_hash}
        else:
            assert left_price is not None and right_price is not None and observed is not None
            trigger_snapshot = _reset_snapshot(
                process_id=process_id,
                generation=generation,
                policy=policy,
                left_price=left_price,
                right_price=right_price,
                observed_percent=observed,
                current_price=current_price,
                average_price=average_price,
                latest_orders=latest_orders,
            )
            snapshot_hash = _text(trigger_snapshot.get("snapshot_hash"))

        active_originals = {_original_no(item) for item in active_cancels if _original_no(item)}
        if open_orders:
            for order in open_orders:
                broker_no = _text(order.get("broker_order_no"))
                remaining = _positive_int(order.get("remaining_quantity"))
                if not broker_no or remaining is None:
                    reasons.append("SELL_PRICE_RESET_OPEN_ORDER_IDENTITY_INVALID")
                    continue
                if broker_no in active_originals:
                    continue
                completed_without_effect = any(
                    _original_no(cancel) == broker_no
                    and _text(cancel.get("status")).upper()
                    in {"CANCELLED", "CANCELED", "PARTIAL_CANCELLED"}
                    for cancel in reset_cancels
                )
                if completed_without_effect:
                    reasons.append("SELL_PRICE_RESET_CANCEL_EFFECT_UNCONFIRMED")
                    continue
                cancel_proposals.append(
                    {
                        "order_queued_id": _text(order.get("id")),
                        "account_no": account_no,
                        "code": code,
                        "side": "SELL",
                        "broker_order_no": broker_no,
                        "remaining_quantity": remaining,
                        "execution_process_id": process_id,
                        "source_signal_id": signal_id,
                        "source_plan_generation": generation,
                        "trigger_snapshot": deepcopy(trigger_snapshot),
                    }
                )
                if len(cancel_proposals) >= max_cancels:
                    break
            if reasons:
                reviews.append(_review(process_id=process_id, signal_id=signal_id, code=code, name=name, reasons=reasons))
            else:
                waiting.append({"execution_process_id": process_id, "code": code, "reason": "SELL_PRICE_RESET_CANCEL_REQUIRED"})
            continue
        if active_cancels:
            waiting.append({"execution_process_id": process_id, "code": code, "reason": "SELL_PRICE_RESET_CANCEL_EFFECT_PENDING"})
            continue

        for cancel in reset_cancels:
            status = _text(cancel.get("status")).upper()
            original = next(
                (item for item in process_orders if _text(item.get("broker_order_no")) == _original_no(cancel)),
                {},
            )
            if status not in {"CANCELLED", "CANCELED", "PARTIAL_CANCELLED"} or _text(original.get("status")).upper() not in {"CANCELLED", "CANCELED", "PARTIAL_CANCELLED", "FILLED"}:
                waiting.append({"execution_process_id": process_id, "code": code, "reason": "SELL_PRICE_RESET_CANCEL_EFFECT_PENDING"})
                reasons.append("WAIT")
                break
        if reasons:
            continue

        if len(position_matches) != 1:
            reasons.append("SELL_PRICE_RESET_POSITION_MATCH_INVALID")
        if len(holding_matches) != 1:
            reasons.append("SELL_PRICE_RESET_BROKER_HOLDING_MATCH_INVALID")
        position_quantity = _nonnegative_int(position.get("quantity"))
        holding_quantity = _nonnegative_int(holding.get("holding_quantity"))
        available_quantity = _nonnegative_int(holding.get("available_quantity"))
        if position_quantity is None or holding_quantity is None or available_quantity is None or available_quantity > holding_quantity:
            reasons.append("SELL_PRICE_RESET_HOLDING_QUANTITY_INVALID")
        elif position_quantity != holding_quantity:
            reasons.append("SELL_PRICE_RESET_POSITION_BROKER_MISMATCH")
        if holding.get("manual_reconciliation_required") is True or _text(holding.get("reconciliation_status")).upper() not in {"", "CONSISTENT"}:
            reasons.append("SELL_PRICE_RESET_HOLDING_RECONCILIATION_REQUIRED")

        runtime_executions = {
            _text(item.get("execution_id")): item for item in loaded["executions"] if _text(item.get("execution_id"))
        }
        for execution_id in latest_orders:
            runtime = runtime_executions.get(execution_id)
            if runtime is None:
                reasons.append(f"SELL_PRICE_RESET_RUNTIME_EXECUTION_MISSING:{execution_id}")
                continue
            if _text(runtime.get("execution_process_id")) != process_id:
                reasons.append(f"SELL_PRICE_RESET_RUNTIME_PROCESS_MISMATCH:{execution_id}")
            if plan_generation(runtime.get("plan_generation")) != generation:
                reasons.append(f"SELL_PRICE_RESET_RUNTIME_GENERATION_MISMATCH:{execution_id}")
        owners = [item for item in loaded["processes"] if _text(item.get("execution_process_id")) == process_id]
        process_option_hashes = {
            _text(item.get("option_snapshot_hash"))
            for item in process_orders
            if _text(item.get("option_snapshot_hash"))
        }
        if len(owners) != 1:
            reasons.append("SELL_PRICE_RESET_PROCESS_OWNER_MISSING_OR_AMBIGUOUS")
        elif (
            len(process_option_hashes) == 1
            and _text(owners[0].get("option_snapshot_hash"))
            not in {"", next(iter(process_option_hashes))}
        ):
            reasons.append("SELL_PRICE_RESET_PROCESS_OPTION_SNAPSHOT_HASH_MISMATCH")

        process_fills = [item for item in loaded["fills"] if _text(item.get("execution_process_id")) == process_id]
        for execution_id, order in latest_orders.items():
            execution_fills = [
                item for item in loaded["fills"]
                if _text(item.get("execution_id")) == execution_id
            ]
            fill_quantities = [_nonnegative_int(item.get("filled_quantity")) for item in execution_fills]
            if any(value is None for value in fill_quantities):
                reasons.append(f"SELL_PRICE_RESET_FILL_QUANTITY_INVALID:{execution_id}")
                continue
            queue_filled = next(
                (
                    _nonnegative_int(order.get(field))
                    for field in ("total_filled_quantity", "cumulative_filled_quantity")
                    if order.get(field) not in (None, "")
                ),
                None,
            )
            evidence_filled = max((value for value in fill_quantities if value is not None), default=0)
            if queue_filled is not None and queue_filled != evidence_filled:
                reasons.append(f"SELL_PRICE_RESET_QUEUE_FILL_MISMATCH:{execution_id}")
            if any(
                _text(item.get("execution_process_id")) not in {"", process_id}
                for item in execution_fills
            ):
                reasons.append(f"SELL_PRICE_RESET_FILL_PROCESS_MISMATCH:{execution_id}")
        latest_evidence = max(
            [
                _time(item.get("updated_at") or item.get("send_call_result_recorded_at") or item.get("created_at")) or datetime.min
                for item in list(latest_orders.values()) + reset_cancels
            ]
            + [_time(item.get("recorded_at") or item.get("received_at") or item.get("occurred_at")) or datetime.min for item in process_fills]
            + [_time(position.get("updated_at") or position.get("last_fill_at")) or datetime.min],
            default=datetime.min,
        )
        holding_time = _time(holding.get("received_at"))
        if holding_time is None or holding_time < latest_evidence:
            if reasons:
                reasons.append("SELL_PRICE_RESET_HOLDING_EVIDENCE_STALE")
            else:
                waiting.append({"execution_process_id": process_id, "code": code, "reason": "SELL_PRICE_RESET_HOLDING_EVIDENCE_PENDING"})
                continue
        if reasons:
            reviews.append(_review(process_id=process_id, signal_id=signal_id, code=code, name=name, reasons=reasons))
            continue
        assert available_quantity is not None
        if available_quantity <= 0:
            waiting.append({"execution_process_id": process_id, "code": code, "reason": "SELL_PRICE_RESET_NO_SELLABLE_QUANTITY"})
            continue

        used_hashes = {
            _text(_intent(item).get("price_reset_source_snapshot_hash"))
            for item in process_orders
            if _text(_intent(item).get("price_reset_source_snapshot_hash"))
        }
        used_hashes.update(
            _text(item.get("price_reset_source_snapshot_hash"))
            for item in signal.get("execution_intents", []) if isinstance(item, dict)
            and _text(item.get("price_reset_source_snapshot_hash"))
        )
        if snapshot_hash in used_hashes:
            waiting.append({"execution_process_id": process_id, "code": code, "reason": "SELL_PRICE_RESET_SNAPSHOT_ALREADY_USED"})
            continue
        option_hashes = {_text(item.get("option_snapshot_hash")) for item in process_orders if _text(item.get("option_snapshot_hash"))}
        if len(option_hashes) != 1:
            reviews.append(_review(process_id=process_id, signal_id=signal_id, code=code, name=name, reasons=["SELL_PRICE_RESET_OPTION_SNAPSHOT_HASH_MISMATCH"]))
            continue
        next_generation = generation + 1
        try:
            intents = build_sell_generation_intents(
                template=template,
                source_signal_id=signal_id,
                process_id=process_id,
                option_snapshot_hash=next(iter(option_hashes)),
                generation=next_generation,
                quantity=available_quantity,
                current_price=current_price,
                source_snapshot_hash=snapshot_hash,
                generated_at=current_at,
            )
        except ValueError as exc:
            reviews.append(_review(process_id=process_id, signal_id=signal_id, code=code, name=name, reasons=[str(exc) or "SELL_PRICE_RESET_REPLAN_INVALID"]))
            continue
        signal_proposal = deepcopy(signal)
        signal_proposal.update(
            {
                "id": signal_id,
                "code": code,
                "name": name,
                "signal": "SELL",
                "status": "PENDING",
                "execution_intent": intents[0],
                "execution_intents": intents,
            }
        )
        replan_proposals.append(
            {
                "execution_process_id": process_id,
                "source_signal_id": signal_id,
                "code": code,
                "plan_generation": next_generation,
                "latest_sellable_quantity": available_quantity,
                "trigger_snapshot_hash": snapshot_hash,
                "signal": signal_proposal,
                "execution_intents": intents,
            }
        )

    return {
        "ok": not bool(errors),
        "cancel_proposals": cancel_proposals[:max_cancels],
        "replan_proposals": replan_proposals,
        "reviews": reviews,
        "waiting": waiting,
        "errors": errors,
        "blocked_execution_process_ids": sorted(blocked_processes),
    }


__all__ = ["build_sell_generation_intents", "build_buy_generation_intents", "inspect_buy_price_resets", "inspect_sell_price_resets"]
