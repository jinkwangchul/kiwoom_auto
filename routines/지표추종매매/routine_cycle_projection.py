# -*- coding: utf-8 -*-
"""Read-only fill projection for the indicator-follow trading cycle."""

from __future__ import annotations

from typing import Any


ROUTINE_TYPE = "INDICATOR_FOLLOW"
TERMINAL_ORDER_STATUSES = {
    "FILLED",
    "CANCELLED",
    "CANCELED",
    "BROKER_REJECTED",
    "SEND_CALL_REJECTED",
    "REJECTED",
    "BLOCKED",
    "INVALID",
    "PARTIAL_CANCELLED",
}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _upper(value: Any) -> str:
    return _clean(value).upper()


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _integer(value: Any) -> int | None:
    number = _number(value)
    if number is None or not number.is_integer():
        return None
    return int(number)


def _unresolved(reason: str, *, holding_qty: int = 0, avg_price: float = 0.0) -> dict[str, Any]:
    return {
        "status": "unresolved",
        "active": False,
        "confirmed_buy_round": None,
        "cumulative_filled_buy_amount": None,
        "holding_qty": holding_qty,
        "avg_price": avg_price,
        "last_buy_order_identity": None,
        "cycle_identity": None,
        "filled_buy_amount_by_round": {},
        "base_filled_buy_amount": 0.0,
        "last_filled_buy_amount": 0.0,
        "approved_buy_budget_by_round": {},
        "last_normal_round_approved_budget": None,
        "last_confirmed_buy_order_price": None,
        "last_plus_one_pending": False,
        "last_plus_one_completed": False,
        "pending_buy_rounds": [],
        "pending_buy_order_identities": [],
        "partial_sell": False,
        "cycle_ended": False,
        "unresolved_reason": reason,
    }


def _resolved(
    *,
    active: bool,
    confirmed_round: int,
    cumulative_buy_amount: float,
    holding_qty: int,
    avg_price: float,
    last_buy_order_identity: str | None,
    cycle_identity: str | None,
    filled_buy_amount_by_round: dict[int, float],
    approved_buy_budget_by_round: dict[int, float],
    confirmed_order_price_by_round: dict[int, float],
    last_plus_one_pending: bool,
    last_plus_one_completed: bool,
    pending_buy_rounds: list[int],
    pending_buy_order_identities: list[str],
    partial_sell: bool,
    cycle_ended: bool,
) -> dict[str, Any]:
    return {
        "status": "resolved",
        "active": active,
        "confirmed_buy_round": confirmed_round,
        "cumulative_filled_buy_amount": cumulative_buy_amount,
        "holding_qty": holding_qty,
        "avg_price": avg_price,
        "last_buy_order_identity": last_buy_order_identity,
        "cycle_identity": cycle_identity,
        "filled_buy_amount_by_round": dict(filled_buy_amount_by_round),
        "base_filled_buy_amount": filled_buy_amount_by_round.get(1, 0.0),
        "last_filled_buy_amount": filled_buy_amount_by_round.get(confirmed_round, 0.0),
        "approved_buy_budget_by_round": dict(approved_buy_budget_by_round),
        "last_normal_round_approved_budget": approved_buy_budget_by_round.get(confirmed_round),
        "last_confirmed_buy_order_price": confirmed_order_price_by_round.get(confirmed_round),
        "last_plus_one_pending": last_plus_one_pending,
        "last_plus_one_completed": last_plus_one_completed,
        "pending_buy_rounds": list(pending_buy_rounds),
        "pending_buy_order_identities": list(pending_buy_order_identities),
        "partial_sell": partial_sell,
        "cycle_ended": cycle_ended,
        "unresolved_reason": "",
    }


def _order_aliases(order: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for field in ("id", "order_queued_id", "order_id", "execution_id", "broker_order_no"):
        value = _clean(order.get(field))
        if value and value not in values:
            values.append(value)
    return values


def _fill_order_aliases(fill: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for field in ("order_queued_id", "order_id", "execution_id", "broker_order_no"):
        value = _clean(fill.get(field))
        if value and value not in values:
            values.append(value)
    return values


def _order_identity(order: dict[str, Any]) -> str:
    aliases = _order_aliases(order)
    return aliases[0] if aliases else ""


def _order_intent(order: dict[str, Any]) -> dict[str, Any]:
    direct = order.get("execution_intent")
    if isinstance(direct, dict):
        return direct
    request = _as_dict(order.get("execution_request"))
    nested = request.get("execution_intent")
    return nested if isinstance(nested, dict) else {}


def _routine_provenance(order: dict[str, Any]) -> dict[str, Any]:
    direct = order.get("routine_provenance")
    if isinstance(direct, dict):
        return direct
    request = _as_dict(order.get("execution_request"))
    nested = request.get("routine_provenance")
    if isinstance(nested, dict):
        return nested
    legacy = order.get("order_provenance")
    return legacy if isinstance(legacy, dict) else {}


def _provenance_value(order: dict[str, Any], field: str) -> str:
    intent = _order_intent(order)
    value = _clean(intent.get(field))
    if value:
        return value
    return _clean(_routine_provenance(order).get(field))


def _is_target_order(order: dict[str, Any], routine_instance_id: str) -> bool:
    return (
        _upper(_provenance_value(order, "routine_type")) == ROUTINE_TYPE
        and _provenance_value(order, "routine_instance_id") == routine_instance_id
    )


def _record_code(record: dict[str, Any]) -> str:
    for source in (
        record,
        _as_dict(_as_dict(record.get("execution_request")).get("request_preview")),
    ):
        for field in ("code", "stock_code", "symbol", "ticker"):
            value = _clean(source.get(field))
            if value:
                return value
    return ""


def _position_snapshot(positions: list[Any], code: str) -> tuple[int, float, str | None]:
    matches = [item for item in positions if isinstance(item, dict) and _record_code(item) == code]
    open_matches = [item for item in matches if (_integer(item.get("quantity")) or 0) > 0]
    if len(open_matches) > 1:
        return 0, 0.0, "MULTIPLE_OPEN_POSITIONS"
    if not open_matches:
        return 0, 0.0, None
    quantity = _integer(open_matches[0].get("quantity"))
    average = _number(open_matches[0].get("average_price"))
    if quantity is None or quantity < 0 or average is None or average < 0:
        return 0, 0.0, "POSITION_VALUE_INVALID"
    return quantity, average, None


def _fill_sort_key(fill: dict[str, Any]) -> tuple[str, str, str]:
    return (
        _clean(fill.get("received_at")),
        _clean(fill.get("recorded_at")),
        _clean(fill.get("fill_id")),
    )


def _approved_round_budget(intent: dict[str, Any]) -> float | None:
    """Return the immutable approved budget for one normal BUY process."""
    for plan_key in ("multi_hoga_plan", "multi_time_plan", "multi_ratio_plan"):
        plan = _as_dict(intent.get(plan_key))
        value = _number(plan.get("approved_round_budget"))
        if value is not None and value > 0:
            return value
    value = _number(intent.get("approved_round_budget"))
    if value is None:
        value = _number(intent.get("budget"))
    return value if value is not None and value > 0 else None


def _canonical_order_basis_price(intent: dict[str, Any]) -> float | None:
    """Return the approved/order basis, not a later tick or Position average."""
    for plan_key, field in (
        ("multi_hoga_plan", "base_price"),
        ("multi_time_plan", "order_price"),
        ("multi_ratio_plan", "order_price"),
    ):
        value = _number(_as_dict(intent.get(plan_key)).get(field))
        if value is not None and value > 0:
            return value
    value = _number(intent.get("actionable_order_price"))
    if value is None:
        value = _number(intent.get("price"))
    return value if value is not None and value > 0 else None


def project_indicator_follow_cycle(
    *,
    code: str,
    routine_instance_id: str,
    order_queue: Any,
    fills: Any,
    positions: Any,
) -> dict[str, Any]:
    """Rebuild one routine instance's current cycle without writing state."""
    clean_code = _clean(code)
    clean_instance = _clean(routine_instance_id)
    if not clean_code:
        return _unresolved("STOCK_CODE_MISSING")
    if not clean_instance:
        return _unresolved("ROUTINE_INSTANCE_ID_MISSING")

    queue_root = _as_dict(order_queue)
    fills_root = _as_dict(fills)
    positions_root = _as_dict(positions)
    orders = queue_root.get("orders")
    fill_records = fills_root.get("fills")
    position_records = positions_root.get("positions")
    if not isinstance(orders, list):
        return _unresolved("ORDER_QUEUE_INVALID")
    if not isinstance(fill_records, list):
        return _unresolved("FILLS_LEDGER_INVALID")
    if not isinstance(position_records, list):
        return _unresolved("POSITIONS_LEDGER_INVALID")

    holding_qty, avg_price, position_issue = _position_snapshot(position_records, clean_code)
    if position_issue:
        return _unresolved(position_issue)

    # The canonical queue retains the source candidate beside its dispatch
    # projection. Collapse only an explicit order_id -> source id link with
    # identical execution/provenance; unrelated alias collisions still block.
    dispatch_by_source: dict[str, list[dict[str, Any]]] = {}
    for item in orders:
        if isinstance(item, dict) and isinstance(item.get("execution_request"), dict):
            dispatch_by_source.setdefault(_clean(item.get("order_id")), []).append(item)
    projected_orders: list[dict[str, Any]] = []
    aliases_by_projection: dict[int, list[str]] = {}
    for item in orders:
        if not isinstance(item, dict):
            return _unresolved("ORDER_QUEUE_ENTRY_INVALID", holding_qty=holding_qty, avg_price=avg_price)
        projected = item
        matches = dispatch_by_source.get(_clean(item.get("id")), [])
        if len(matches) == 1:
            dispatch = matches[0]
            identity_fields = (
                "routine_type", "routine_instance_id", "side", "cycle_identity",
                "source_signal_id", "execution_process_id", "execution_id",
                "buy_phase", "buy_round", "child_kind", "child_sequence_index",
                "child_sequence_total", "plan_generation", "quantity",
            )
            if (_clean(item.get("execution_id"))
                    and item.get("execution_id") == dispatch.get("execution_id")
                    and all(_order_intent(item).get(key) == _order_intent(dispatch).get(key)
                            for key in identity_fields)):
                projected = dispatch
        aliases_by_projection.setdefault(id(projected), []).extend(_order_aliases(item))
        if not any(projected is previous for previous in projected_orders):
            projected_orders.append(projected)

    alias_map: dict[str, dict[str, Any]] = {}
    ambiguous_aliases: set[str] = set()
    target_orders: list[dict[str, Any]] = []
    for raw_order in projected_orders:
        if not isinstance(raw_order, dict):
            return _unresolved("ORDER_QUEUE_ENTRY_INVALID", holding_qty=holding_qty, avg_price=avg_price)
        order = raw_order
        for alias in aliases_by_projection[id(order)]:
            if alias in alias_map and alias_map[alias] is not order:
                ambiguous_aliases.add(alias)
            else:
                alias_map[alias] = order
        if _is_target_order(order, clean_instance):
            target_orders.append(order)

    pending_target = any(
        _upper(order.get("status")) not in TERMINAL_ORDER_STATUSES
        for order in target_orders
    )
    pending_buy_rounds: list[int] = []
    pending_buy_order_identities: list[str] = []
    last_plus_one_pending = False
    for order in target_orders:
        if _upper(order.get("status")) in TERMINAL_ORDER_STATUSES:
            continue
        intent = _order_intent(order)
        side = _upper(intent.get("side") or order.get("side"))
        if side != "BUY":
            continue
        if _upper(intent.get("generation_kind")) == "LAST_PLUS_ONE":
            last_plus_one_pending = True
        pending_round = _integer(intent.get("buy_round"))
        if pending_round is None or pending_round <= 0:
            return _unresolved(
                "PENDING_BUY_PROVENANCE_INCOMPLETE",
                holding_qty=holding_qty,
                avg_price=avg_price,
            )
        if pending_round not in pending_buy_rounds:
            pending_buy_rounds.append(pending_round)
        pending_identity = _order_identity(order)
        if pending_identity and pending_identity not in pending_buy_order_identities:
            pending_buy_order_identities.append(pending_identity)
    cumulative_by_order: dict[str, int] = {}
    round_processes: dict[int, tuple[str, str, str]] = {}
    ledger_qty = 0
    active = False
    confirmed_round = 0
    cumulative_buy_amount = 0.0
    filled_buy_amount_by_round: dict[int, float] = {}
    approved_buy_budget_by_round: dict[int, float] = {}
    confirmed_order_price_by_round: dict[int, float] = {}
    last_buy_identity: str | None = None
    cycle_identity: str | None = None
    partial_sell = False
    cycle_ended = False
    foreign_fill_during_active_cycle = False
    filled_order_identities: set[str] = set()

    code_fills = [
        item for item in fill_records
        if isinstance(item, dict) and _record_code(item) == clean_code
    ]
    if any(not isinstance(item, dict) for item in fill_records):
        return _unresolved("FILL_ENTRY_INVALID", holding_qty=holding_qty, avg_price=avg_price)

    for fill in sorted(code_fills, key=_fill_sort_key):
        aliases = _fill_order_aliases(fill)
        if not aliases:
            return _unresolved("FILL_ORDER_IDENTITY_MISSING", holding_qty=holding_qty, avg_price=avg_price)
        if any(alias in ambiguous_aliases for alias in aliases):
            return _unresolved("FILL_ORDER_IDENTITY_AMBIGUOUS", holding_qty=holding_qty, avg_price=avg_price)
        matches = {id(alias_map[alias]): alias_map[alias] for alias in aliases if alias in alias_map}
        order = next(iter(matches.values())) if len(matches) == 1 else None
        if len(matches) > 1:
            return _unresolved("FILL_ORDER_LINK_AMBIGUOUS", holding_qty=holding_qty, avg_price=avg_price)

        order_key = _order_identity(order) if order else aliases[0]
        cumulative = _integer(fill.get("filled_quantity"))
        price = _number(fill.get("filled_price"))
        side = _upper(fill.get("side"))
        if cumulative is None or cumulative <= 0 or price is None or price <= 0:
            return _unresolved("FILL_VALUE_INVALID", holding_qty=holding_qty, avg_price=avg_price)
        previous = cumulative_by_order.get(order_key, 0)
        delta = cumulative - previous
        if delta < 0:
            return _unresolved("FILL_CUMULATIVE_QUANTITY_REVERSED", holding_qty=holding_qty, avg_price=avg_price)
        cumulative_by_order[order_key] = max(previous, cumulative)
        if delta == 0:
            continue

        target_order = order is not None and _is_target_order(order, clean_instance)
        if side == "BUY":
            if active and not target_order:
                foreign_fill_during_active_cycle = True
            if target_order:
                intent = _order_intent(order)
                phase = _upper(intent.get("buy_phase"))
                planned_round = _integer(intent.get("buy_round"))
                if phase not in {"BASE", "REPEAT"} or planned_round is None or planned_round <= 0:
                    return _unresolved("BUY_PROVENANCE_INCOMPLETE", holding_qty=holding_qty, avg_price=avg_price)
                generation_kind = _upper(intent.get("generation_kind"))
                if generation_kind == "LAST_PLUS_ONE":
                    if not active or confirmed_round <= 0:
                        return _unresolved(
                            "LAST_PLUS_ONE_CYCLE_BOUNDARY_UNRESOLVED",
                            holding_qty=holding_qty,
                            avg_price=avg_price,
                        )
                    cumulative_buy_amount += delta * price
                    filled_order_identities.add(_order_identity(order))
                    ledger_qty += delta
                    continue
                first_fill_for_order = previous == 0
                if first_fill_for_order:
                    if not active:
                        if phase != "BASE" or planned_round != 1 or ledger_qty != 0:
                            return _unresolved("BASE_CYCLE_BOUNDARY_UNRESOLVED", holding_qty=holding_qty, avg_price=avg_price)
                        active = True
                        confirmed_round = 0
                        round_processes = {}
                        cumulative_buy_amount = 0.0
                        filled_buy_amount_by_round = {}
                        approved_buy_budget_by_round = {}
                        confirmed_order_price_by_round = {}
                        last_buy_identity = None
                        cycle_identity = _clean(intent.get("cycle_identity")) or order_key
                        partial_sell = False
                        cycle_ended = False
                    expected_round = confirmed_round + 1
                    process_identity = (
                        _clean(intent.get("execution_process_id")),
                        _clean(intent.get("source_signal_id")),
                        _clean(intent.get("cycle_identity")),
                    )
                    same_round_child = (
                        planned_round == confirmed_round
                        and all(process_identity)
                        and round_processes.get(planned_round) == process_identity
                        and intent.get("child_kind") in {"HOGA_LEVEL", "TIME_SLICE", "RATIO_SLICE"}
                    )
                    if planned_round != expected_round and not same_round_child:
                        return _unresolved("BUY_ROUND_SEQUENCE_MISMATCH", holding_qty=holding_qty, avg_price=avg_price)
                    if phase != ("BASE" if planned_round == 1 else "REPEAT"):
                        return _unresolved("BUY_PHASE_ROUND_MISMATCH", holding_qty=holding_qty, avg_price=avg_price)
                    confirmed_round = planned_round
                    round_processes[planned_round] = process_identity
                    last_buy_identity = order_key
                    approved_budget = _approved_round_budget(intent)
                    confirmed_price = _canonical_order_basis_price(intent)
                    if approved_budget is not None:
                        approved_buy_budget_by_round[planned_round] = approved_budget
                    if confirmed_price is not None:
                        confirmed_order_price_by_round[planned_round] = confirmed_price
                    if cycle_identity is None:
                        cycle_identity = _clean(intent.get("cycle_identity")) or order_key
                filled_amount = delta * price
                cumulative_buy_amount += filled_amount
                filled_buy_amount_by_round[planned_round] = (
                    filled_buy_amount_by_round.get(planned_round, 0.0) + filled_amount
                )
            ledger_qty += delta
        elif side == "SELL":
            ledger_qty -= delta
            if ledger_qty < 0:
                return _unresolved("SELL_EXCEEDS_RECONSTRUCTED_HOLDING", holding_qty=holding_qty, avg_price=avg_price)
            if active:
                if not target_order:
                    foreign_fill_during_active_cycle = True
                partial_sell = ledger_qty > 0
                if ledger_qty == 0:
                    active = False
                    confirmed_round = 0
                    cumulative_buy_amount = 0.0
                    filled_buy_amount_by_round = {}
                    approved_buy_budget_by_round = {}
                    confirmed_order_price_by_round = {}
                    last_buy_identity = None
                    cycle_identity = None
                    partial_sell = False
                    cycle_ended = True
        else:
            return _unresolved("FILL_SIDE_INVALID", holding_qty=holding_qty, avg_price=avg_price)

    if foreign_fill_during_active_cycle:
        return _unresolved("FOREIGN_ORDER_MIXED_IN_ACTIVE_CYCLE", holding_qty=holding_qty, avg_price=avg_price)
    if ledger_qty != holding_qty:
        return _unresolved("FILL_POSITION_QUANTITY_MISMATCH", holding_qty=holding_qty, avg_price=avg_price)
    if holding_qty > 0 and not active:
        return _unresolved("EXISTING_HOLDING_WITHOUT_ROUTINE_CYCLE", holding_qty=holding_qty, avg_price=avg_price)
    if holding_qty == 0 and active:
        return _unresolved("ACTIVE_CYCLE_WITH_ZERO_HOLDING", holding_qty=holding_qty, avg_price=avg_price)
    if holding_qty == 0 and pending_target:
        return _unresolved("ZERO_HOLDING_WITH_PENDING_ROUTINE_ORDER", holding_qty=holding_qty, avg_price=avg_price)

    last_plus_one_groups: dict[str, list[dict[str, Any]]] = {}
    for order in target_orders:
        intent = _order_intent(order)
        if _upper(intent.get("generation_kind")) != "LAST_PLUS_ONE":
            continue
        if cycle_identity and _clean(intent.get("cycle_identity")) != cycle_identity:
            continue
        group_key = (
            _clean(intent.get("execution_process_id"))
            or _clean(intent.get("source_signal_id"))
            or _order_identity(order)
        )
        last_plus_one_groups.setdefault(group_key, []).append(order)
    last_plus_one_completed = any(
        bool(group)
        and all(_upper(order.get("status")) == "FILLED" for order in group)
        and any(_order_identity(order) in filled_order_identities for order in group)
        for group in last_plus_one_groups.values()
    )

    return _resolved(
        active=active,
        confirmed_round=confirmed_round,
        cumulative_buy_amount=cumulative_buy_amount,
        holding_qty=holding_qty,
        avg_price=avg_price,
        last_buy_order_identity=last_buy_identity,
        cycle_identity=cycle_identity,
        filled_buy_amount_by_round=filled_buy_amount_by_round,
        approved_buy_budget_by_round=approved_buy_budget_by_round,
        confirmed_order_price_by_round=confirmed_order_price_by_round,
        last_plus_one_pending=last_plus_one_pending,
        last_plus_one_completed=last_plus_one_completed,
        pending_buy_rounds=sorted(pending_buy_rounds),
        pending_buy_order_identities=pending_buy_order_identities,
        partial_sell=partial_sell,
        cycle_ended=cycle_ended,
    )
