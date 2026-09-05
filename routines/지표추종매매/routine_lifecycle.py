"""Pure Indicator Follow lifecycle coordinator.

This module owns strategy ordering.  It consumes one immutable Main facts
snapshot and emits only generic lifecycle commands; it never writes Queue,
Runtime, stock state, Review, or broker state.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from execution_active_buy import inspect_active_buy_lifecycle
from execution_buy_exit import inspect_buy_repeat_exits
from execution_buy_recovery import inspect_buy_recovery_generations
from execution_price_reset import inspect_buy_price_resets, inspect_sell_price_resets
from execution_process_supplement import inspect_execution_process_supplements
from execution_ratio_slice_eligibility import inspect_eligible_ratio_slices
from execution_sell_final_exit import inspect_sell_final_residual_exits
from execution_sell_repeat import inspect_sell_repeat_exits, inspect_sell_repeat_generations
from execution_time_slice_due import inspect_due_time_slices
from execution_unfilled_cancel_eligibility import inspect_unfilled_cancel_eligibility
from execution_unfilled_cancel_eligibility import cancel_effect_state
from routine_lifecycle_decision import build_routine_lifecycle_decision
from routine_main_facts import validate_routine_main_facts


_TERMINAL_SIGNALS = {"DONE", "CANCELLED", "EXPIRED", "ERROR", "BLOCKED"}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _stock_code(value: dict[str, Any]) -> str:
    return _text(value.get("code")).lstrip("A")


def _request_preview(value: dict[str, Any]) -> dict[str, Any]:
    request = value.get("execution_request")
    request = request if isinstance(request, dict) else {}
    preview = request.get("request_preview")
    return preview if isinstance(preview, dict) else {}


def _holding_reconciliation_state(main_facts: dict[str, Any], code: str) -> str:
    positions = [
        row for row in main_facts.get("positions", [])
        if isinstance(row, dict) and _stock_code(row) == code
    ]
    holdings = [
        row for row in main_facts.get("holdings", [])
        if isinstance(row, dict) and _stock_code(row) == code
    ]
    if len(positions) > 1 or len(holdings) > 1:
        return "INVALID"
    if holdings and (
        holdings[0].get("manual_reconciliation_required") is True
        or _text(holdings[0].get("reconciliation_status") or "CONSISTENT").upper() != "CONSISTENT"
    ):
        return "PENDING"
    try:
        position_qty = int(positions[0].get("quantity") or 0) if positions else 0
        holding_qty = int(holdings[0].get("holding_quantity") or 0) if holdings else 0
    except (TypeError, ValueError):
        return "INVALID"
    return "CONFIRMED" if position_qty == holding_qty else "PENDING"


def _owned_codes(main_facts: dict[str, Any], instance_id: str) -> tuple[str, ...]:
    configs = main_facts.get("stock_configs")
    configs = configs if isinstance(configs, dict) else {}
    states = main_facts.get("stock_states")
    states = states if isinstance(states, dict) else {}
    allowed = {_text(code).lstrip("A") for code in main_facts.get("allowed_stock_codes", [])}
    return tuple(
        code for code, config in configs.items()
        if _text(code).lstrip("A") in allowed
        and isinstance(config, dict)
        and _text(config.get("assigned_routine_instance_id")) == instance_id
        and not (
            isinstance(states.get(code), dict)
            and (
                states[code].get("review_required") is True
                or _text(states[code].get("status")).upper() in {"REVIEW", "REVIEW_REQUIRED"}
            )
        )
    )


def _decision(
    command: str,
    *,
    main_facts: dict[str, Any],
    routine_identity: dict[str, Any],
    item: dict[str, Any],
    reason: str,
    phase: str,
) -> dict[str, Any]:
    code = _stock_code(item)
    if not code:
        signal = item.get("signal")
        code = _stock_code(signal) if isinstance(signal, dict) else ""
    signal_id = _text(item.get("source_signal_id") or item.get("id"))
    signal = item.get("signal")
    if isinstance(signal, dict):
        signal_id = signal_id or _text(signal.get("id"))
    write_contract: dict[str, Any] | None = None
    if command == "COMPLETE_BUY_ROUND" and phase == "BUY_COMPLETION":
        write_contract = {
            "signal_id": signal_id,
            "status": "DONE",
            "metadata": {"buy_completion_evidence": {
                "policy": "BUY_NORMAL_FLOW_COMPLETION",
                "execution_process_ids": sorted({
                    _text(order.get("execution_process_id"))
                    for order in main_facts.get("orders", [])
                    if isinstance(order, dict)
                    and _text(order.get("source_signal_id")) == signal_id
                    and _text(order.get("execution_process_id"))
                }),
                "result": "FILLED_RESIDUAL_ZERO",
            }},
        }
    elif command == "COMPLETE_BUY_ROUND" and phase == "BUY_EXIT":
        write_contract = {
            "signal_id": signal_id, "status": _text(item.get("signal_status")) or "DONE",
            "metadata": {"buy_exit_evidence": {
                **deepcopy(item), "policy": "BUY_RECOVERY_EXIT", "buy_phase_completed": True,
            }},
        }
    elif command == "COMPLETE_BUY_ROUND" and phase == "BUY_RECOVERY":
        write_contract = {
            "signal_id": signal_id, "status": "DONE",
            "metadata": {"buy_completion_evidence": {
                **deepcopy(item), "policy": "BUY_RECOVERY_TERMINAL_COMPLETION", "buy_phase_completed": True,
            }},
        }
    elif command == "REQUEST_FINAL_LIQUIDATION" and phase == "SELL_EXIT":
        write_contract = {
            "signal_id": signal_id, "status": _text(item.get("signal_status")) or "PREVIEWED",
            "metadata": {"sell_repeat_exit_evidence": {
                "policy": "SELL_REPEAT_EXIT",
                "execution_process_id": item.get("execution_process_id"),
                "source_signal_id": signal_id,
                "exit_condition_type": item.get("exit_condition_type"),
                "exit_condition_types": deepcopy(item.get("exit_condition_types") or []),
                "exit_triggered_at": item.get("exit_triggered_at"),
                "exit_source_snapshot_hash": item.get("exit_source_snapshot_hash"),
                "exit_source_snapshot": deepcopy(item.get("exit_source_snapshot")),
                "evaluated_generation": item.get("evaluated_generation"),
                "reason": item.get("reason") or "SELL_REPEAT_EXIT_CONDITION_MATCHED",
            }},
        }
    elif command == "CARRY_POSITION":
        write_contract = {
            "signal_id": signal_id, "status": "DONE",
            "metadata": {"sell_carryover_evidence": {
                **deepcopy(item), "policy": "CARRY_TO_NEXT_SIGNAL",
                "position_preserved": True, "process_terminal": True,
            }},
        }
    elif command == "COMPLETE_PROCESS" and phase == "SELL_FINAL":
        write_contract = {
            "signal_id": signal_id, "status": "DONE",
            "metadata": {"final_residual_exit_evidence": {
                **deepcopy(item), "policy": "SELL_FINAL_RESIDUAL_MARKET_EXIT",
                "resulting_holding_zero_confirmed": True,
            }},
        }
    elif command == "COMPLETE_PROCESS" and phase == "SIGNAL_PRIORITY":
        write_contract = {
            "signal_id": signal_id, "status": "CANCELLED",
            "metadata": {
                "duplicate_priority": "TRAILING",
                "reason_code": "DUPLICATE_SIGNAL_REPLACED_AFTER_CANCEL_EFFECT",
            },
        }
    payload = {"phase": phase, "proposal": deepcopy(item)}
    if write_contract is not None:
        payload["write_contract"] = write_contract
    return build_routine_lifecycle_decision(
        command,
        main_facts=main_facts,
        routine_identity=routine_identity,
        stock_code=code,
        reason=reason,
        evidence={"phase": phase},
        payload=payload,
    )


def _append_inspection(
    decisions: list[dict[str, Any]],
    inspection: dict[str, Any],
    *,
    main_facts: dict[str, Any],
    routine_identity: dict[str, Any],
    phase: str,
    proposal_commands: tuple[tuple[str, str], ...],
) -> None:
    for review in inspection.get("reviews") or []:
        if isinstance(review, dict):
            decisions.append(_decision(
                "STOP_AND_REVIEW", main_facts=main_facts,
                routine_identity=routine_identity, item=review,
                reason=_text((review.get("review_reasons") or ["LIFECYCLE_CONTRACT_INVALID"])[0]),
                phase=phase,
            ))
    for waiting in inspection.get("waiting") or []:
        if isinstance(waiting, dict):
            decisions.append(_decision(
                "WAIT", main_facts=main_facts, routine_identity=routine_identity,
                item=waiting, reason=_text(waiting.get("reason")) or "LIFECYCLE_FACT_PENDING",
                phase=phase,
            ))
    for field, command in proposal_commands:
        for proposal in inspection.get(field) or []:
            if isinstance(proposal, dict):
                decisions.append(_decision(
                    command, main_facts=main_facts, routine_identity=routine_identity,
                    item=proposal, reason=_text(proposal.get("reason")) or phase,
                    phase=phase,
                ))


def _signal_ownership_decisions(
    *,
    main_facts: dict[str, Any],
    routine_identity: dict[str, Any],
    owned_codes: tuple[str, ...],
) -> list[dict[str, Any]]:
    instance_id = _text(routine_identity.get("routine_instance_id"))
    signals = [
        row for row in main_facts.get("signals", [])
        if isinstance(row, dict)
        and _stock_code(row) in owned_codes
        and _text(row.get("routine_instance_id")) == instance_id
        and _text(row.get("status")).upper() not in _TERMINAL_SIGNALS
    ]
    orders = [row for row in main_facts.get("orders", []) if isinstance(row, dict)]
    decisions: list[dict[str, Any]] = []
    for code in owned_codes:
        active = sorted(
            (row for row in signals if _stock_code(row) == code),
            key=lambda row: (_text(row.get("created_at")), _text(row.get("id"))),
        )
        if len(active) < 2:
            continue
        successor = active[-1]
        policy = successor.get("signal_runtime_policy")
        priority = _text(policy.get("duplicate_priority") if isinstance(policy, dict) else "").upper()
        if priority == "LEADING":
            decisions.append(_decision(
                "IGNORE_SIGNAL", main_facts=main_facts, routine_identity=routine_identity,
                item=successor, reason="DUPLICATE_SIGNAL_LEADING_PRIORITY", phase="SIGNAL_PRIORITY",
            ))
            continue
        if priority != "TRAILING":
            continue
        predecessor = active[0]
        predecessor_id = _text(predecessor.get("id"))
        open_orders = [
            row for row in orders
            if _text(row.get("source_signal_id")) == predecessor_id
            and _text(row.get("status")).upper() not in {
                "DONE", "FILLED", "CANCELED", "CANCELLED", "REJECTED",
                "BROKER_REJECTED", "SEND_CALL_REJECTED", "BLOCKED", "BLOCKED_POLICY", "FAILED",
            }
        ]
        predispatch = [row for row in open_orders if _text(row.get("status")).upper() in {"APPROVED", "EXECUTABLE", "ORDER_QUEUED"}]
        if predispatch:
            decisions.append(_decision(
                "BLOCK_PROCESS_DISPATCH", main_facts=main_facts,
                routine_identity=routine_identity, item={**predecessor, "orders": predispatch},
                reason="DUPLICATE_SIGNAL_SUPERSEDE_PREDISPATCH", phase="SIGNAL_PRIORITY",
            ))
        broker_open = [row for row in open_orders if row not in predispatch]
        if broker_open:
            for order in broker_open:
                if _text(order.get("status")).upper() == "SEND_UNCERTAIN" or order.get("manual_reconciliation_required") is True:
                    decisions.append(_decision(
                        "STOP_AND_REVIEW", main_facts=main_facts,
                        routine_identity=routine_identity, item=order,
                        reason="SUPERSEDED_ORDER_SEND_UNCERTAIN", phase="SIGNAL_PRIORITY",
                    ))
                    continue
                broker_no = _text(order.get("broker_order_no"))
                related_cancels = [
                    row for row in orders
                    if _text(row.get("order_action") or _request_preview(row).get("order_action")).upper() == "CANCEL"
                    and _text(_request_preview(row).get("original_order_no") or row.get("original_order_no")) == broker_no
                ]
                effects = [cancel_effect_state(row) for row in related_cancels]
                if "CONFIRMED" in effects:
                    continue
                if any(effect in {"UNCERTAIN", "REJECTED"} for effect in effects):
                    decisions.append(_decision(
                        "STOP_AND_REVIEW", main_facts=main_facts,
                        routine_identity=routine_identity, item=order,
                        reason="CANCEL_EFFECT_CONTRACT_INVALID", phase="SIGNAL_PRIORITY",
                    ))
                    continue
                if related_cancels:
                    decisions.append(_decision(
                        "WAIT", main_facts=main_facts, routine_identity=routine_identity,
                        item=order, reason="CANCEL_EFFECT_PENDING", phase="SIGNAL_PRIORITY",
                    ))
                    continue
                decisions.append(_decision(
                    "REQUEST_CANCEL", main_facts=main_facts,
                    routine_identity=routine_identity, item=order,
                    reason="DUPLICATE_SIGNAL_TRAILING_PRIORITY", phase="SIGNAL_PRIORITY",
                ))
        unresolved_broker_orders = [
            order for order in broker_open
            if not any(
                cancel_effect_state(row) == "CONFIRMED"
                for row in orders
                if _text(row.get("order_action") or _request_preview(row).get("order_action")).upper() == "CANCEL"
                and _text(_request_preview(row).get("original_order_no") or row.get("original_order_no"))
                == _text(order.get("broker_order_no"))
            )
        ]
        if not predispatch and not unresolved_broker_orders:
            reconciliation = _holding_reconciliation_state(main_facts, code)
            if reconciliation == "INVALID":
                decisions.append(_decision(
                    "STOP_AND_REVIEW", main_facts=main_facts,
                    routine_identity=routine_identity, item=predecessor,
                    reason="HOLDING_RECONCILIATION_INVALID", phase="SIGNAL_PRIORITY",
                ))
            elif reconciliation == "PENDING":
                decisions.append(_decision(
                    "WAIT", main_facts=main_facts, routine_identity=routine_identity,
                    item=predecessor, reason="HOLDING_RECONCILIATION_PENDING", phase="SIGNAL_PRIORITY",
                ))
            else:
                decisions.append(_decision(
                    "COMPLETE_PROCESS", main_facts=main_facts,
                    routine_identity=routine_identity, item=predecessor,
                    reason="DUPLICATE_SIGNAL_REPLACED_AFTER_CANCEL_EFFECT", phase="SIGNAL_PRIORITY",
                ))
        decisions.append(_decision(
            "WAIT", main_facts=main_facts, routine_identity=routine_identity,
            item=successor, reason="PREDECESSOR_TERMINAL_CONFIRMATION_PENDING", phase="SIGNAL_PRIORITY",
        ))
    return decisions


def _buy_completion_decisions(
    *,
    main_facts: dict[str, Any],
    routine_identity: dict[str, Any],
    owned_codes: tuple[str, ...],
) -> list[dict[str, Any]]:
    instance_id = _text(routine_identity.get("routine_instance_id"))
    orders = [row for row in main_facts.get("orders", []) if isinstance(row, dict)]
    decisions: list[dict[str, Any]] = []
    for signal in main_facts.get("signals", []):
        if (
            not isinstance(signal, dict)
            or _stock_code(signal) not in owned_codes
            or _text(signal.get("routine_instance_id")) != instance_id
            or _text(signal.get("signal")).upper() != "BUY"
            or _text(signal.get("status")).upper() in _TERMINAL_SIGNALS
        ):
            continue
        intent = signal.get("execution_intent")
        intent = intent if isinstance(intent, dict) else {}
        completion_key = _text(intent.get("deferred_plan_status_key"))
        if intent.get("deferred_dispatch") is True and (
            not completion_key or signal.get(completion_key) is not True
        ):
            continue
        signal_id = _text(signal.get("id"))
        source_orders = [
            row for row in orders
            if _text(row.get("source_signal_id")) == signal_id
            and _text(row.get("order_action") or "NEW").upper() == "NEW"
        ]
        if not source_orders:
            continue
        latest: dict[str, dict[str, Any]] = {}
        for order in source_orders:
            execution_id = _text(order.get("execution_id") or order.get("id"))
            if execution_id:
                latest[execution_id] = order
        try:
            complete = bool(latest) and all(
                _text(order.get("status")).upper() == "FILLED"
                and int(order.get("remaining_quantity") or 0) == 0
                for order in latest.values()
            )
        except (TypeError, ValueError):
            complete = False
        if complete:
            decisions.append(_decision(
                "COMPLETE_BUY_ROUND", main_facts=main_facts,
                routine_identity=routine_identity,
                item={**signal, "completed_execution_ids": sorted(latest)},
                reason="BUY_NORMAL_FLOW_FILLED_RESIDUAL_ZERO",
                phase="BUY_COMPLETION",
            ))
    return decisions


def evaluate_lifecycle(
    *,
    main_facts: dict[str, Any],
    rules: dict[str, Any],
    routine_identity: dict[str, Any],
    rules_identity: str,
) -> dict[str, Any]:
    del rules, rules_identity
    valid, reason = validate_routine_main_facts(main_facts)
    if not valid:
        return {"decisions": []}
    instance_id = _text(routine_identity.get("routine_instance_id"))
    owned_codes = _owned_codes(main_facts, instance_id)
    if not owned_codes:
        return {"decisions": []}
    account = _text(main_facts.get("selected_account_no"))
    prices = main_facts.get("actionable_prices_by_code")
    prices = prices if isinstance(prices, dict) else {}
    common = {
        "selected_account_no": account,
        "allowed_stock_codes": owned_codes,
        "actionable_prices_by_code": prices,
        "main_facts": main_facts,
    }
    decisions = _signal_ownership_decisions(
        main_facts=main_facts, routine_identity=routine_identity, owned_codes=owned_codes,
    )
    blocked_codes = {d["stock_code"] for d in decisions}
    lifecycle_codes = tuple(code for code in owned_codes if code not in blocked_codes)
    if not lifecycle_codes:
        return {"decisions": decisions}
    decisions.extend(_buy_completion_decisions(
        main_facts=main_facts,
        routine_identity=routine_identity,
        owned_codes=lifecycle_codes,
    ))
    completed_codes = {
        d["stock_code"] for d in decisions
        if d.get("payload", {}).get("phase") == "BUY_COMPLETION"
    }
    lifecycle_codes = tuple(code for code in lifecycle_codes if code not in completed_codes)
    if not lifecycle_codes:
        return {"decisions": decisions}
    common["allowed_stock_codes"] = lifecycle_codes

    inspections = (
        ("BUY_EXIT", inspect_buy_repeat_exits(**common),
         (("cancel_proposals", "REQUEST_CANCEL"), ("completion_proposals", "COMPLETE_BUY_ROUND"))),
        ("SELL_EXIT", inspect_sell_repeat_exits(**common),
         (("exit_proposals", "REQUEST_FINAL_LIQUIDATION"), ("carryover_proposals", "CARRY_POSITION"))),
        ("ACTIVE_BUY", inspect_active_buy_lifecycle(**common),
         (("supersede_proposals", "BLOCK_PROCESS_DISPATCH"), ("cancel_proposals", "REQUEST_CANCEL"), ("replan_proposals", "CREATE_GENERATION"))),
        ("SELL_RESET", inspect_sell_price_resets(**common),
         (("cancel_proposals", "REQUEST_CANCEL"), ("replan_proposals", "CREATE_GENERATION"))),
        ("BUY_RESET", inspect_buy_price_resets(
            **common, current_orderable_cash=main_facts.get("current_orderable_cash")),
         (("cancel_proposals", "REQUEST_CANCEL"), ("replan_proposals", "CREATE_GENERATION"))),
        ("UNFILLED", inspect_unfilled_cancel_eligibility(
            selected_account_no=account, allowed_stock_codes=lifecycle_codes, main_facts=main_facts),
         (("proposals", "REQUEST_CANCEL"),)),
        ("BUY_RECOVERY", inspect_buy_recovery_generations(**common),
         (("proposals", "CREATE_GENERATION"), ("completion_proposals", "COMPLETE_BUY_ROUND"))),
        ("TIME_SLICE", inspect_due_time_slices(
            **common, current_orderable_cash=main_facts.get("current_orderable_cash")),
         (("proposals", "CREATE_GENERATION"),)),
        ("RATIO_SLICE", inspect_eligible_ratio_slices(
            **common, current_orderable_cash=main_facts.get("current_orderable_cash")),
         (("proposals", "CREATE_GENERATION"),)),
        ("MULTI_HOGA_SUPPLEMENT", inspect_execution_process_supplements(
            selected_account_no=account, allowed_stock_codes=lifecycle_codes, main_facts=main_facts),
         (("proposals", "CREATE_GENERATION"),)),
        ("SELL_REPEAT", inspect_sell_repeat_generations(**common),
         (("proposals", "CREATE_GENERATION"), ("exit_proposals", "COMPLETE_PROCESS"), ("carryover_proposals", "CARRY_POSITION"))),
        ("SELL_FINAL", inspect_sell_final_residual_exits(
            selected_account_no=account, allowed_stock_codes=lifecycle_codes, main_facts=main_facts),
         (("proposals", "REQUEST_FINAL_LIQUIDATION"), ("completion_proposals", "COMPLETE_PROCESS"))),
    )
    for phase, inspection, commands in inspections:
        _append_inspection(
            decisions, inspection, main_facts=main_facts,
            routine_identity=routine_identity, phase=phase, proposal_commands=commands,
        )
    # One stock receives at most its highest-priority command from this facts
    # revision.  Main must execute it and recapture before asking again.
    selected: list[dict[str, Any]] = []
    selected_codes: set[str] = set()
    for decision in decisions:
        code = _text(decision.get("stock_code"))
        if not code or code in selected_codes:
            continue
        same_stock = [row for row in decisions if _text(row.get("stock_code")) == code]
        chosen = next(
            (row for row in same_stock if _text(row.get("command")).upper() == "STOP_AND_REVIEW"),
            None,
        ) or next(
            (row for row in same_stock if _text(row.get("command")).upper() != "WAIT"),
            decision,
        )
        selected.append(chosen)
        selected_codes.add(code)
    return {"decisions": selected}
