# -*- coding: utf-8 -*-
"""Read-only final residual MARKET SELL inspection.

The inspector turns durable SELL repeat-exit evidence into at most one final
MARKET child in the same execution process.  It never writes Runtime files or
calls a broker; callers must use the existing routine signal/candidate path.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
from typing import Any

from execution_provenance_contract import (
    materialize_execution_intent_children,
    plan_generation,
    stable_hash,
)


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
_ACTIVE_CANCEL = _PRE_DISPATCH | _OPEN | {"SEND_UNCERTAIN"}


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


def _time(value: Any) -> datetime | None:
    value_text = _text(value)
    if not value_text:
        return None
    try:
        return datetime.fromisoformat(value_text.replace("Z", "+00:00")).replace(
            tzinfo=None
        )
    except ValueError:
        return None


def _read(
    path: str | Path,
    field: str,
    *,
    optional: bool = False,
) -> tuple[list[dict[str, Any]], str]:
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
    return _text(
        record.get("order_action") or _preview(record).get("order_action") or "NEW"
    ).upper()


def _latest(records: list[dict[str, Any]]) -> dict[str, Any]:
    return max(
        records,
        key=lambda item: (
            _time(
                item.get("updated_at")
                or item.get("send_call_result_recorded_at")
                or item.get("created_at")
            )
            or datetime.min,
            _text(item.get("id")),
        ),
    )


def _generation(record: dict[str, Any]) -> int:
    intent = _intent(record)
    return plan_generation(record.get("plan_generation", intent.get("plan_generation")))


def _plan_generation_or_none(value: Any) -> int | None:
    try:
        return plan_generation(value)
    except ValueError:
        return None


def _is_final_intent(intent: dict[str, Any]) -> bool:
    return intent.get("final_residual_exit") is True and bool(
        _text(intent.get("final_residual_exit_action_hash"))
    )


def _review(
    *,
    process_id: str,
    signal_id: str,
    code: str,
    name: str,
    reasons: list[str],
) -> dict[str, Any]:
    return {
        "execution_process_id": process_id,
        "source_signal_id": signal_id,
        "code": code,
        "name": name,
        "review_reasons": sorted(set(reasons)),
        "review_location": "SELL_FINAL_RESIDUAL_EXIT_RECONCILIATION",
    }


def _clean_final_template(template: dict[str, Any]) -> dict[str, Any]:
    intent = deepcopy(template)
    for field in (
        "execution_id",
        "provenance_approved_at",
        "process_record",
        "child_sequence_index",
        "child_sequence_total",
        "child_kind",
        "child_plan",
        "multi_hoga_plan",
        "multi_time_plan",
        "multi_ratio_plan",
        "schedule_anchor_at",
        "scheduled_at",
        "repeat_generation",
        "repeat_started_at",
        "repeat_plan_snapshot_hash",
        "repeat_parent_generation",
        "repeat_source_snapshot_hash",
        "price_reset_source_snapshot_hash",
        "sell_repeat_policy",
        "sell_price_reset_policy",
        "unfilled_timeout_policy",
    ):
        intent.pop(field, None)
    return intent


def _build_final_intent(
    *,
    template: dict[str, Any],
    source_signal_id: str,
    process_id: str,
    option_snapshot_hash: str,
    generation: int,
    quantity: int,
    exit_evidence: dict[str, Any],
    eligibility_snapshot_hash: str,
    action_hash: str,
    generated_at: datetime,
) -> dict[str, Any]:
    intent = _clean_final_template(template)
    intent.update(
        {
            "side": "SELL",
            "budget": None,
            "source_signal_id": source_signal_id,
            "execution_process_id": process_id,
            "execution_process_owner_required": False,
            "option_snapshot_hash": option_snapshot_hash,
            "plan_generation": generation,
            "planned_total_quantity": quantity,
            "execution_mode": "SINGLE_ORDER",
            "quantity": quantity,
            "price": None,
            "hoga": "MARKET",
            "price_basis": "MARKET",
            "child_sequence_index": 1,
            "child_sequence_total": 1,
            "child_kind": "SINGLE_ORDER",
            "final_residual_exit": True,
            "final_residual_exit_action_hash": action_hash,
            "final_residual_exit_source_snapshot_hash": eligibility_snapshot_hash,
            "repeat_exit_source_snapshot_hash": exit_evidence.get(
                "exit_source_snapshot_hash"
            ),
            "final_residual_exit_parent_generation": generation - 1,
            "final_residual_exit_triggered_at": generated_at.isoformat(
                timespec="milliseconds"
            ),
            "child_plan": {
                "planned_quantity": quantity,
                "planned_price": None,
                "plan_generation": generation,
                "final_residual_exit_action_hash": action_hash,
                "final_residual_exit_source_snapshot_hash": eligibility_snapshot_hash,
            },
        }
    )
    return materialize_execution_intent_children(
        [intent],
        source_signal_id=source_signal_id,
        execution_process_id=process_id,
        plan_generation_value=generation,
    )[0]


def _holding_matches(
    *,
    positions: list[dict[str, Any]],
    holdings: list[dict[str, Any]],
    account_no: str,
    code: str,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    reasons: list[str] = []
    position_matches = [
        item
        for item in positions
        if _text(item.get("account_no")) == account_no and _text(item.get("code")) == code
    ]
    holding_matches = [
        item
        for item in holdings
        if _text(item.get("account_no")) == account_no and _text(item.get("code")) == code
    ]
    position = position_matches[0] if len(position_matches) == 1 else {}
    holding = holding_matches[0] if len(holding_matches) == 1 else {}
    if len(position_matches) != 1:
        reasons.append("SELL_FINAL_EXIT_POSITION_MATCH_INVALID")
    if len(holding_matches) != 1:
        reasons.append("SELL_FINAL_EXIT_BROKER_HOLDING_MATCH_INVALID")
    position_quantity = _nonnegative_int(position.get("quantity"))
    holding_quantity = _nonnegative_int(holding.get("holding_quantity"))
    available_quantity = _nonnegative_int(holding.get("available_quantity"))
    if (
        position_quantity is None
        or holding_quantity is None
        or available_quantity is None
        or available_quantity > holding_quantity
    ):
        reasons.append("SELL_FINAL_EXIT_HOLDING_QUANTITY_INVALID")
    elif position_quantity != holding_quantity:
        reasons.append("SELL_FINAL_EXIT_POSITION_BROKER_MISMATCH")
    if (
        holding.get("manual_reconciliation_required") is True
        or _text(holding.get("reconciliation_status")).upper()
        not in {"", "CONSISTENT"}
    ):
        reasons.append("SELL_FINAL_EXIT_HOLDING_RECONCILIATION_REQUIRED")
    return position, holding, reasons


def _completion_proposal(
    *,
    process_id: str,
    signal_id: str,
    code: str,
    name: str,
    exit_hash: str,
    action_hash: str,
    completed_at: datetime,
    order: dict[str, Any] | None,
    reason: str,
) -> dict[str, Any]:
    intent = _intent(order or {})
    return {
        "execution_process_id": process_id,
        "source_signal_id": signal_id,
        "code": code,
        "name": name,
        "status": "HOLDING_ZERO_CONFIRMED",
        "reason": reason,
        "completed_at": completed_at.isoformat(timespec="milliseconds"),
        "repeat_exit_source_snapshot_hash": exit_hash,
        "final_residual_exit_action_hash": action_hash,
        "final_execution_id": intent.get("execution_id"),
        "final_order_id": (order or {}).get("id") or (order or {}).get("order_id"),
        "resulting_holding_zero_confirmed": True,
    }


def inspect_sell_final_residual_exits(
    *,
    selected_account_no: str,
    allowed_stock_codes: tuple[str, ...] | list[str] | set[str] | None = None,
    now: datetime | None = None,
    proposal_limit: int = 5,
    order_queue_path: str | Path = ORDER_QUEUE_PATH,
    order_executions_path: str | Path = ORDER_EXECUTIONS_PATH,
    fills_path: str | Path = FILLS_PATH,
    positions_path: str | Path = POSITIONS_PATH,
    holdings_path: str | Path = HOLDINGS_PATH,
    signals_path: str | Path = SIGNALS_PATH,
) -> dict[str, Any]:
    """Propose one final MARKET child per durable repeat-exit evidence."""
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
    result: dict[str, Any] = {
        "ok": not errors,
        "proposals": [],
        "completion_proposals": [],
        "reviews": [],
        "waiting": [],
        "errors": errors,
    }
    if errors:
        return result

    account_no = _text(selected_account_no)
    allowed = (
        {_text(value) for value in allowed_stock_codes if _text(value)}
        if allowed_stock_codes is not None
        else None
    )
    if not account_no or (allowed_stock_codes is not None and not allowed):
        return result
    current_at = now or datetime.now()
    max_proposals = max(0, int(proposal_limit or 0))
    runtime_executions = {
        _text(item.get("execution_id")): item
        for item in loaded["executions"]
        if _text(item.get("execution_id"))
    }

    for signal in loaded["signals"]:
        exit_evidence = _as_dict(signal.get("sell_repeat_exit_evidence"))
        if not exit_evidence:
            continue
        signal_id = _text(signal.get("id"))
        process_id = _text(exit_evidence.get("execution_process_id"))
        code = _text(signal.get("code"))
        name = _text(signal.get("name"))
        if allowed is not None and code not in allowed:
            continue
        exit_hash = _text(exit_evidence.get("exit_source_snapshot_hash"))
        exit_snapshot = _as_dict(exit_evidence.get("exit_source_snapshot"))
        reasons: list[str] = []
        if (
            not signal_id
            or not process_id
            or _text(exit_evidence.get("source_signal_id")) not in {"", signal_id}
            or not exit_hash
            or _text(exit_snapshot.get("snapshot_hash")) != exit_hash
        ):
            reasons.append("SELL_FINAL_EXIT_REPEAT_EXIT_EVIDENCE_INVALID")
        evaluated_generation = _nonnegative_int(
            exit_evidence.get("evaluated_generation")
        )
        if evaluated_generation is None:
            reasons.append("SELL_FINAL_EXIT_EVALUATED_GENERATION_INVALID")
        signal_process_intents = [
            item
            for item in signal.get("execution_intents", [])
            if isinstance(item, dict)
            and _text(item.get("execution_process_id")) == process_id
        ]
        for item in signal_process_intents:
            signal_generation = _plan_generation_or_none(item.get("plan_generation"))
            if signal_generation is None:
                reasons.append("SELL_FINAL_EXIT_SIGNAL_GENERATION_INVALID")
            elif (
                evaluated_generation is not None
                and _text(item.get("repeat_source_snapshot_hash"))
                and signal_generation > evaluated_generation
            ):
                reasons.append("SELL_FINAL_EXIT_NEWER_REPEAT_SIGNAL_INTENT_EXISTS")
        action_hash = stable_hash(
            {
                "policy": "SELL_FINAL_RESIDUAL_MARKET_EXIT",
                "execution_process_id": process_id,
                "source_signal_id": signal_id,
                "repeat_exit_source_snapshot_hash": exit_hash,
            }
        )

        process_orders = [
            item
            for item in loaded["orders"]
            if _text(item.get("execution_process_id") or _intent(item).get("execution_process_id"))
            == process_id
        ]
        original_orders = [
            item for item in process_orders if _action(item) in {"NEW", "MODIFY"}
        ]
        cancel_orders = [item for item in process_orders if _action(item) == "CANCEL"]
        source_ids = {
            _text(item.get("source_signal_id") or _intent(item).get("source_signal_id"))
            for item in original_orders
        }
        source_ids.discard("")
        if source_ids != {signal_id}:
            reasons.append("SELL_FINAL_EXIT_SOURCE_SIGNAL_ID_MISMATCH")
        account_values = {
            _text(item.get("account_no") or _preview(item).get("account_no"))
            for item in original_orders
        }
        if account_values != {account_no}:
            reasons.append("SELL_FINAL_EXIT_ACCOUNT_IDENTITY_MISMATCH")
        if not original_orders:
            reasons.append("SELL_FINAL_EXIT_PROCESS_ORDERS_MISSING")

        latest_by_execution: dict[str, dict[str, Any]] = {}
        generation_groups: dict[int, list[dict[str, Any]]] = {}
        for order in original_orders:
            execution_id = _text(order.get("execution_id") or _intent(order).get("execution_id"))
            if not execution_id:
                reasons.append("SELL_FINAL_EXIT_EXECUTION_ID_MISSING")
                continue
            try:
                generation = _generation(order)
            except ValueError:
                reasons.append(f"SELL_FINAL_EXIT_GENERATION_INVALID:{execution_id}")
                continue
            generation_groups.setdefault(generation, []).append(order)
            previous = latest_by_execution.get(execution_id)
            latest_by_execution[execution_id] = (
                order if previous is None else _latest([previous, order])
            )
        if not generation_groups:
            if reasons:
                result["reviews"].append(
                    _review(
                        process_id=process_id,
                        signal_id=signal_id,
                        code=code,
                        name=name,
                        reasons=reasons,
                    )
                )
            continue

        final_orders = [
            item for item in latest_by_execution.values() if _is_final_intent(_intent(item))
        ]
        final_signal_intents = [
            item
            for item in signal.get("execution_intents", [])
            if isinstance(item, dict) and _is_final_intent(item)
        ]
        final_evidence = _as_dict(signal.get("final_residual_exit_evidence"))

        if len(final_orders) > 1:
            reasons.append("SELL_FINAL_EXIT_MULTIPLE_FINAL_ORDERS")
        if any(
            _text(_intent(item).get("final_residual_exit_action_hash")) != action_hash
            or _text(_intent(item).get("repeat_exit_source_snapshot_hash")) != exit_hash
            for item in final_orders
        ):
            reasons.append("SELL_FINAL_EXIT_ORDER_EVIDENCE_IDENTITY_MISMATCH")
        if final_evidence and _text(
            final_evidence.get("final_residual_exit_action_hash")
        ) not in {"", action_hash}:
            reasons.append("SELL_FINAL_EXIT_SIGNAL_EVIDENCE_IDENTITY_MISMATCH")

        if (
            not final_orders
            and _text(final_evidence.get("status")).upper()
            == "HOLDING_ZERO_CONFIRMED"
            and final_evidence.get("resulting_holding_zero_confirmed") is True
        ):
            if reasons:
                result["reviews"].append(
                    _review(
                        process_id=process_id,
                        signal_id=signal_id,
                        code=code,
                        name=name,
                        reasons=reasons,
                    )
                )
            else:
                result["waiting"].append(
                    {
                        "execution_process_id": process_id,
                        "code": code,
                        "reason": "SELL_FINAL_EXIT_COMPLETE",
                    }
                )
            continue

        position, holding, holding_reasons = _holding_matches(
            positions=loaded["positions"],
            holdings=loaded["holdings"],
            account_no=account_no,
            code=code,
        )
        reasons.extend(holding_reasons)
        position_quantity = _nonnegative_int(position.get("quantity"))
        holding_quantity = _nonnegative_int(holding.get("holding_quantity"))
        available_quantity = _nonnegative_int(holding.get("available_quantity"))

        if reasons:
            result["reviews"].append(
                _review(
                    process_id=process_id,
                    signal_id=signal_id,
                    code=code,
                    name=name,
                    reasons=reasons,
                )
            )
            continue

        if final_orders:
            final_order = final_orders[0]
            status = _text(final_order.get("status")).upper()
            if status == "SEND_UNCERTAIN" or final_order.get(
                "manual_reconciliation_required"
            ) is True:
                result["reviews"].append(
                    _review(
                        process_id=process_id,
                        signal_id=signal_id,
                        code=code,
                        name=name,
                        reasons=["SELL_FINAL_EXIT_SEND_UNCERTAIN"],
                    )
                )
                continue
            if status in _OPEN or status in _PRE_DISPATCH:
                result["waiting"].append(
                    {
                        "execution_process_id": process_id,
                        "code": code,
                        "reason": "SELL_FINAL_EXIT_ORDER_ACTIVE",
                        "status": status,
                    }
                )
                continue
            if status in _FAILURE_TERMINAL:
                result["reviews"].append(
                    _review(
                        process_id=process_id,
                        signal_id=signal_id,
                        code=code,
                        name=name,
                        reasons=[f"SELL_FINAL_EXIT_MARKET_ORDER_REJECTED:{status}"],
                    )
                )
                continue
            if status != "FILLED":
                result["reviews"].append(
                    _review(
                        process_id=process_id,
                        signal_id=signal_id,
                        code=code,
                        name=name,
                        reasons=[f"SELL_FINAL_EXIT_TERMINAL_UNRESOLVED:{status or 'UNKNOWN'}"],
                    )
                )
                continue
            intent = _intent(final_order)
            execution_id = _text(
                final_order.get("execution_id") or intent.get("execution_id")
            )
            final_consistency: list[str] = []
            runtime = runtime_executions.get(execution_id)
            if runtime is None:
                final_consistency.append(
                    f"SELL_FINAL_EXIT_RUNTIME_EXECUTION_MISSING:{execution_id}"
                )
            else:
                if _text(runtime.get("execution_process_id")) != process_id:
                    final_consistency.append(
                        f"SELL_FINAL_EXIT_RUNTIME_PROCESS_MISMATCH:{execution_id}"
                    )
                if plan_generation(runtime.get("plan_generation")) != _generation(
                    final_order
                ):
                    final_consistency.append(
                        f"SELL_FINAL_EXIT_RUNTIME_GENERATION_MISMATCH:{execution_id}"
                    )
            final_fills = [
                item
                for item in loaded["fills"]
                if _text(item.get("execution_id")) == execution_id
            ]
            fill_quantities = [
                _nonnegative_int(item.get("filled_quantity")) for item in final_fills
            ]
            if any(value is None for value in fill_quantities):
                final_consistency.append(
                    f"SELL_FINAL_EXIT_FILL_QUANTITY_INVALID:{execution_id}"
                )
            if any(
                _text(item.get("execution_process_id")) not in {"", process_id}
                for item in final_fills
            ):
                final_consistency.append(
                    f"SELL_FINAL_EXIT_FILL_PROCESS_MISMATCH:{execution_id}"
                )
            queue_filled = next(
                (
                    _nonnegative_int(final_order.get(field))
                    for field in (
                        "total_filled_quantity",
                        "cumulative_filled_quantity",
                    )
                    if final_order.get(field) not in (None, "")
                ),
                None,
            )
            evidence_filled = max(
                (value for value in fill_quantities if value is not None), default=0
            )
            if queue_filled is not None and queue_filled != evidence_filled:
                final_consistency.append(
                    f"SELL_FINAL_EXIT_QUEUE_FILL_MISMATCH:{execution_id}"
                )
            owners = [
                item
                for item in loaded["processes"]
                if _text(item.get("execution_process_id")) == process_id
            ]
            if len(owners) != 1:
                final_consistency.append(
                    "SELL_FINAL_EXIT_PROCESS_OWNER_MISSING_OR_AMBIGUOUS"
                )
            elif _text(owners[0].get("option_snapshot_hash")) not in {
                "",
                _text(
                    final_order.get("option_snapshot_hash")
                    or intent.get("option_snapshot_hash")
                ),
            }:
                final_consistency.append(
                    "SELL_FINAL_EXIT_PROCESS_OPTION_SNAPSHOT_HASH_MISMATCH"
                )
            if final_consistency:
                result["reviews"].append(
                    _review(
                        process_id=process_id,
                        signal_id=signal_id,
                        code=code,
                        name=name,
                        reasons=final_consistency,
                    )
                )
                continue
            final_time = _time(
                final_order.get("updated_at") or final_order.get("created_at")
            ) or datetime.min
            for fill in final_fills:
                final_time = max(
                    final_time,
                    _time(
                        fill.get("recorded_at")
                        or fill.get("received_at")
                        or fill.get("occurred_at")
                    )
                    or datetime.min,
                )
            final_time = max(
                final_time,
                _time(position.get("updated_at") or position.get("last_fill_at"))
                or datetime.min,
            )
            holding_time = _time(holding.get("received_at"))
            if holding_time is None or holding_time < final_time:
                result["waiting"].append(
                    {
                        "execution_process_id": process_id,
                        "code": code,
                        "reason": "SELL_FINAL_EXIT_HOLDING_ZERO_CONFIRMATION_PENDING",
                    }
                )
                continue
            if position_quantity == 0 and holding_quantity == 0 and available_quantity == 0:
                if _text(final_evidence.get("status")).upper() != "HOLDING_ZERO_CONFIRMED":
                    result["completion_proposals"].append(
                        _completion_proposal(
                            process_id=process_id,
                            signal_id=signal_id,
                            code=code,
                            name=name,
                            exit_hash=exit_hash,
                            action_hash=action_hash,
                            completed_at=current_at,
                            order=final_order,
                            reason="SELL_FINAL_EXIT_HOLDING_ZERO_CONFIRMED",
                        )
                    )
                else:
                    result["waiting"].append(
                        {
                            "execution_process_id": process_id,
                            "code": code,
                            "reason": "SELL_FINAL_EXIT_COMPLETE",
                        }
                    )
                continue
            result["reviews"].append(
                _review(
                    process_id=process_id,
                    signal_id=signal_id,
                    code=code,
                    name=name,
                    reasons=["SELL_FINAL_EXIT_FILLED_WITH_RESIDUAL_HOLDING"],
                )
            )
            continue

        if final_signal_intents:
            if any(
                _text(item.get("final_residual_exit_action_hash")) != action_hash
                for item in final_signal_intents
            ):
                result["reviews"].append(
                    _review(
                        process_id=process_id,
                        signal_id=signal_id,
                        code=code,
                        name=name,
                        reasons=["SELL_FINAL_EXIT_PENDING_INTENT_IDENTITY_MISMATCH"],
                    )
                )
            elif _text(signal.get("status")).upper() == "PENDING":
                result["waiting"].append(
                    {
                        "execution_process_id": process_id,
                        "code": code,
                        "reason": "SELL_FINAL_EXIT_GENERATION_PENDING_EXECUTION",
                    }
                )
            else:
                result["reviews"].append(
                    _review(
                        process_id=process_id,
                        signal_id=signal_id,
                        code=code,
                        name=name,
                        reasons=["SELL_FINAL_EXIT_PENDING_INTENT_QUEUE_MISSING"],
                    )
                )
            continue
        if final_evidence:
            result["reviews"].append(
                _review(
                    process_id=process_id,
                    signal_id=signal_id,
                    code=code,
                    name=name,
                    reasons=["SELL_FINAL_EXIT_REQUEST_EVIDENCE_WITHOUT_ORDER_OR_INTENT"],
                )
            )
            continue

        assert evaluated_generation is not None
        repeat_after_exit = [
            item
            for item in original_orders
            if _text(_intent(item).get("repeat_source_snapshot_hash"))
            and _generation(item) > evaluated_generation
        ]
        if repeat_after_exit:
            result["reviews"].append(
                _review(
                    process_id=process_id,
                    signal_id=signal_id,
                    code=code,
                    name=name,
                    reasons=["SELL_FINAL_EXIT_NEWER_REPEAT_GENERATION_EXISTS"],
                )
            )
            continue
        generation = max(generation_groups)
        if generation != evaluated_generation:
            result["reviews"].append(
                _review(
                    process_id=process_id,
                    signal_id=signal_id,
                    code=code,
                    name=name,
                    reasons=["SELL_FINAL_EXIT_GENERATION_EVIDENCE_MISMATCH"],
                )
            )
            continue

        active_cancels: list[dict[str, Any]] = []
        for cancel in cancel_orders:
            status = _text(cancel.get("status")).upper()
            if status in _ACTIVE_CANCEL and cancel.get("original_order_effect_confirmed") is not True:
                active_cancels.append(cancel)
        if any(
            _text(item.get("status")).upper() == "SEND_UNCERTAIN"
            or item.get("manual_reconciliation_required") is True
            for item in active_cancels
        ):
            result["reviews"].append(
                _review(
                    process_id=process_id,
                    signal_id=signal_id,
                    code=code,
                    name=name,
                    reasons=["SELL_FINAL_EXIT_CANCEL_SEND_UNCERTAIN"],
                )
            )
            continue
        if active_cancels:
            result["waiting"].append(
                {
                    "execution_process_id": process_id,
                    "code": code,
                    "reason": "SELL_FINAL_EXIT_ACTIVE_CANCEL",
                }
            )
            continue

        unsafe_reasons: list[str] = []
        active = False
        for execution_id, order in latest_by_execution.items():
            status = _text(order.get("status")).upper()
            if status == "SEND_UNCERTAIN" or order.get(
                "manual_reconciliation_required"
            ) is True:
                unsafe_reasons.append(
                    f"SELL_FINAL_EXIT_UNSAFE_ORDER:{execution_id}:{status or 'UNKNOWN'}"
                )
            elif status in _OPEN or status in _PRE_DISPATCH:
                active = True
            elif status in _FAILURE_TERMINAL:
                unsafe_reasons.append(
                    f"SELL_FINAL_EXIT_PREVIOUS_GENERATION_FAILURE:{execution_id}:{status}"
                )
            elif status not in _SUCCESS_TERMINAL:
                unsafe_reasons.append(
                    f"SELL_FINAL_EXIT_STATUS_UNRESOLVED:{execution_id}:{status or 'UNKNOWN'}"
                )
        if unsafe_reasons:
            result["reviews"].append(
                _review(
                    process_id=process_id,
                    signal_id=signal_id,
                    code=code,
                    name=name,
                    reasons=unsafe_reasons,
                )
            )
            continue
        if active:
            result["waiting"].append(
                {
                    "execution_process_id": process_id,
                    "code": code,
                    "reason": "SELL_FINAL_EXIT_OPEN_SELL_ORDER",
                }
            )
            continue

        consistency_reasons: list[str] = []
        for execution_id, order in latest_by_execution.items():
            runtime = runtime_executions.get(execution_id)
            if runtime is None:
                consistency_reasons.append(
                    f"SELL_FINAL_EXIT_RUNTIME_EXECUTION_MISSING:{execution_id}"
                )
                continue
            if _text(runtime.get("execution_process_id")) != process_id:
                consistency_reasons.append(
                    f"SELL_FINAL_EXIT_RUNTIME_PROCESS_MISMATCH:{execution_id}"
                )
            if plan_generation(runtime.get("plan_generation")) != _generation(order):
                consistency_reasons.append(
                    f"SELL_FINAL_EXIT_RUNTIME_GENERATION_MISMATCH:{execution_id}"
                )
            execution_fills = [
                item
                for item in loaded["fills"]
                if _text(item.get("execution_id")) == execution_id
            ]
            fill_quantities = [
                _nonnegative_int(item.get("filled_quantity")) for item in execution_fills
            ]
            if any(value is None for value in fill_quantities):
                consistency_reasons.append(
                    f"SELL_FINAL_EXIT_FILL_QUANTITY_INVALID:{execution_id}"
                )
            queue_filled = next(
                (
                    _nonnegative_int(order.get(field))
                    for field in (
                        "total_filled_quantity",
                        "cumulative_filled_quantity",
                    )
                    if order.get(field) not in (None, "")
                ),
                None,
            )
            evidence_filled = max(
                (value for value in fill_quantities if value is not None), default=0
            )
            if queue_filled is not None and queue_filled != evidence_filled:
                consistency_reasons.append(
                    f"SELL_FINAL_EXIT_QUEUE_FILL_MISMATCH:{execution_id}"
                )
            if any(
                _text(item.get("execution_process_id")) not in {"", process_id}
                for item in execution_fills
            ):
                consistency_reasons.append(
                    f"SELL_FINAL_EXIT_FILL_PROCESS_MISMATCH:{execution_id}"
                )
        owners = [
            item
            for item in loaded["processes"]
            if _text(item.get("execution_process_id")) == process_id
        ]
        option_hashes = {
            _text(item.get("option_snapshot_hash") or _intent(item).get("option_snapshot_hash"))
            for item in original_orders
            if _text(item.get("option_snapshot_hash") or _intent(item).get("option_snapshot_hash"))
        }
        if len(owners) != 1:
            consistency_reasons.append(
                "SELL_FINAL_EXIT_PROCESS_OWNER_MISSING_OR_AMBIGUOUS"
            )
        elif len(option_hashes) == 1 and _text(
            owners[0].get("option_snapshot_hash")
        ) not in {"", next(iter(option_hashes))}:
            consistency_reasons.append(
                "SELL_FINAL_EXIT_PROCESS_OPTION_SNAPSHOT_HASH_MISMATCH"
            )
        if len(option_hashes) != 1:
            consistency_reasons.append("SELL_FINAL_EXIT_OPTION_SNAPSHOT_HASH_MISMATCH")
        if consistency_reasons:
            result["reviews"].append(
                _review(
                    process_id=process_id,
                    signal_id=signal_id,
                    code=code,
                    name=name,
                    reasons=consistency_reasons,
                )
            )
            continue

        process_fills = [
            item
            for item in loaded["fills"]
            if _text(item.get("execution_process_id")) == process_id
        ]
        evidence_times = [
            _time(
                item.get("updated_at")
                or item.get("send_call_result_recorded_at")
                or item.get("created_at")
            )
            or datetime.min
            for item in list(latest_by_execution.values()) + cancel_orders
        ]
        evidence_times.extend(
            _time(item.get("recorded_at") or item.get("received_at") or item.get("occurred_at"))
            or datetime.min
            for item in process_fills
        )
        evidence_times.extend(
            [
                _time(position.get("updated_at") or position.get("last_fill_at"))
                or datetime.min,
                _time(exit_evidence.get("exit_triggered_at")) or datetime.min,
            ]
        )
        latest_evidence = max(evidence_times, default=datetime.min)
        holding_time = _time(holding.get("received_at"))
        if holding_time is None or holding_time < latest_evidence:
            result["waiting"].append(
                {
                    "execution_process_id": process_id,
                    "code": code,
                    "reason": "SELL_FINAL_EXIT_HOLDING_EVIDENCE_PENDING",
                }
            )
            continue
        assert position_quantity is not None
        assert holding_quantity is not None
        assert available_quantity is not None
        if holding_quantity == 0 and position_quantity == 0 and available_quantity == 0:
            result["completion_proposals"].append(
                _completion_proposal(
                    process_id=process_id,
                    signal_id=signal_id,
                    code=code,
                    name=name,
                    exit_hash=exit_hash,
                    action_hash=action_hash,
                    completed_at=current_at,
                    order=None,
                    reason="SELL_FINAL_EXIT_NOT_REQUIRED_HOLDING_ZERO",
                )
            )
            continue
        if available_quantity <= 0:
            result["waiting"].append(
                {
                    "execution_process_id": process_id,
                    "code": code,
                    "reason": "SELL_FINAL_EXIT_SELLABLE_QUANTITY_ZERO",
                }
            )
            continue
        if len(result["proposals"]) >= max_proposals:
            continue

        latest_generation_orders = generation_groups[generation]
        template = _intent(_latest(latest_generation_orders))
        snapshot_payload = {
            "policy": "SELL_FINAL_RESIDUAL_MARKET_EXIT",
            "execution_process_id": process_id,
            "source_signal_id": signal_id,
            "repeat_exit_source_snapshot_hash": exit_hash,
            "source_plan_generation": generation,
            "position": {
                "quantity": position.get("quantity"),
                "updated_at": position.get("updated_at"),
            },
            "broker_holding": {
                "holding_quantity": holding.get("holding_quantity"),
                "available_quantity": holding.get("available_quantity"),
                "received_at": holding.get("received_at"),
            },
            "orders": [
                {
                    "execution_id": execution_id,
                    "status": order.get("status"),
                    "remaining_quantity": order.get("remaining_quantity"),
                    "updated_at": order.get("updated_at"),
                }
                for execution_id, order in sorted(latest_by_execution.items())
            ],
        }
        eligibility_snapshot_hash = stable_hash(snapshot_payload)
        final_intent = _build_final_intent(
            template=template,
            source_signal_id=signal_id,
            process_id=process_id,
            option_snapshot_hash=next(iter(option_hashes)),
            generation=generation + 1,
            quantity=available_quantity,
            exit_evidence=exit_evidence,
            eligibility_snapshot_hash=eligibility_snapshot_hash,
            action_hash=action_hash,
            generated_at=current_at,
        )
        signal_proposal = deepcopy(signal)
        signal_proposal.update(
            {
                "id": signal_id,
                "code": code,
                "name": name,
                "signal": "SELL",
                "status": "PENDING",
                "execution_intent": final_intent,
                "execution_intents": [final_intent],
            }
        )
        result["proposals"].append(
            {
                "execution_process_id": process_id,
                "source_signal_id": signal_id,
                "code": code,
                "name": name,
                "plan_generation": generation + 1,
                "latest_sellable_quantity": available_quantity,
                "repeat_exit_source_snapshot_hash": exit_hash,
                "final_residual_exit_action_hash": action_hash,
                "final_residual_exit_source_snapshot_hash": eligibility_snapshot_hash,
                "eligibility_snapshot": snapshot_payload,
                "signal": signal_proposal,
                "execution_intents": [final_intent],
            }
        )

    return result


__all__ = ["inspect_sell_final_residual_exits"]
