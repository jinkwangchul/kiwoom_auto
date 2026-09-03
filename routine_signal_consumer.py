# -*- coding: utf-8 -*-
"""Dry-run consumer for routine signals.

This module consumes PENDING BUY/SELL routine signals. By default it only asks
the bridge for an OrderManager dry-run and an order payload preview. Optional
flags can update routine signal status and write order_queue.json candidates,
but it never mutates orders.json, calls an executor, or sends an order.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from routine_signal_order_bridge import (
    dry_run_order_manager_for_signal_with_payload_preview,
    load_pending_routine_signals,
)
from routine_signal_queue import (
    STATUS_BLOCKED,
    STATUS_DONE,
    STATUS_ERROR,
    STATUS_PENDING,
    STATUS_PREVIEWED,
    update_signal_status,
)
from routine_package_contract import (
    EXECUTION_ADMISSION_ROLE,
    evaluate_routine_gate,
)

try:
    from order_queue import (
        append_order_candidates,
        order_candidate_dedupe_key,
        read_order_queue,
        signal_to_order_candidate,
        signal_to_order_candidates,
    )
except Exception:  # pragma: no cover
    append_order_candidates = None
    order_candidate_dedupe_key = None
    read_order_queue = None
    signal_to_order_candidate = None
    signal_to_order_candidates = None

try:
    from order_approval_engine import evaluate_order_approval
except Exception:  # pragma: no cover
    evaluate_order_approval = None

try:
    import operation_policy_gate
except Exception:  # pragma: no cover
    operation_policy_gate = None


def _clean_limit(limit: Any) -> int | None:
    try:
        value = int(limit) if limit is not None else None
    except (TypeError, ValueError):
        return None
    if value is not None and value >= 0:
        return value
    return None


def _preview_status_for_result(result: dict[str, Any]) -> str:
    if not result.get("order_manager", {}).get("ok") or not result.get("payload_built"):
        return STATUS_ERROR
    if not bool(result.get("order_manager_allowed")):
        return STATUS_BLOCKED
    return STATUS_PREVIEWED


def _preview_metadata_for_result(result: dict[str, Any]) -> dict[str, Any]:
    order_manager = result.get("order_manager", {})
    payload = result.get("payload_preview", {})
    return {
        "preview_summary": {
            "signal_id": result.get("signal_id", ""),
            "signal_type": result.get("signal_type", ""),
            "payload_built": bool(result.get("payload_built")),
            "order_manager_allowed": bool(result.get("order_manager_allowed")),
            "payload_candidate_status": result.get("payload_candidate_status", ""),
            "send_order_called": False,
            "files_mutated": False,
        },
        "order_manager_result": {
            "ok": bool(order_manager.get("ok")),
            "allowed": bool(order_manager.get("allowed")),
            "reason": order_manager.get("reason", ""),
            "order_executor_called": bool(order_manager.get("order_executor_called", False)),
            "state_saved": bool(order_manager.get("state_saved", False)),
        },
        "payload_candidate_status": payload.get("candidate_status", ""),
    }


def _order_dedupe_key(order: dict[str, Any]) -> str:
    if callable(order_candidate_dedupe_key):
        return order_candidate_dedupe_key(order)
    return "|".join(
        [
            str(order.get("source_signal_id", "")),
            str(order.get("routine", "")),
            str(order.get("code", "")),
            str(order.get("side", "")),
        ]
    )


def _is_deferred_child_signal(signal: Any) -> bool:
    if not isinstance(signal, dict):
        return False
    intents = signal.get("execution_intents")
    return (
        isinstance(intents, list)
        and bool(intents)
        and {
            (
                str(item.get("execution_mode") or "").strip().upper(),
                str(item.get("child_kind") or "").strip().upper(),
            )
            for item in intents
            if isinstance(item, dict)
        }
        in ({("MULTI_TIME", "TIME_SLICE")}, {("MULTI_RATIO", "RATIO_SLICE")})
        and all(isinstance(item, dict) for item in intents)
    )


def routine_execution_intent_admission(
    signal: dict[str, Any],
) -> dict[str, Any]:
    """Ask the assigned routine to admit candidate generation."""
    execution_intent = signal.get("execution_intent")
    execution_intents = signal.get("execution_intents")
    if not isinstance(execution_intent, dict) and isinstance(execution_intents, list):
        execution_intent = next(
            (item for item in execution_intents if isinstance(item, dict)),
            None,
        )
    intent = execution_intent if isinstance(execution_intent, dict) else {}
    instance_id = str(
        signal.get("routine_instance_id")
        or intent.get("routine_instance_id")
        or ""
    ).strip()
    if not instance_id:
        # Legacy/reference candidates remain preview-only and unresolved; they
        # do not participate in routine-owned execution admission.
        if not intent or intent.get("unresolved") is True:
            return {
                "allowed": True,
                "reason": "LEGACY_UNRESOLVED_PREVIEW_ONLY",
                "routine_identity": {},
                "rules_identity": None,
            }
        return {"allowed": False, "reason": "ROUTINE_INSTANCE_ID_MISSING"}
    return evaluate_routine_gate(
        instance_id=instance_id,
        role=EXECUTION_ADMISSION_ROLE,
        subject=signal,
    )


def _apply_operation_policy_to_created_orders(append_result: dict[str, Any]) -> dict[str, Any]:
    created_orders = append_result.get("created_orders", [])
    if not isinstance(created_orders, list):
        created_orders = []

    policy_results: list[dict[str, Any]] = []
    policy_checked = 0
    policy_executable = 0
    policy_blocked = 0
    policy_errors = 0

    approved_orders = [
        order
        for order in created_orders
        if isinstance(order, dict)
        and str(order.get("status", "") or "").upper() == "APPROVED"
        and str(order.get("approval_status", "") or "").upper() == "APPROVED"
    ]
    if not approved_orders:
        return {
            "ok": True,
            "reason": "",
            "policy_checked": 0,
            "policy_executable": 0,
            "policy_blocked": 0,
            "policy_errors": 0,
            "policy_results": [],
        }

    if operation_policy_gate is None or not callable(getattr(operation_policy_gate, "apply_operation_policy_gate_for_order", None)):
        return {
            "ok": False,
            "reason": "operation policy gate unavailable",
            "policy_checked": 0,
            "policy_executable": 0,
            "policy_blocked": 0,
            "policy_errors": len(approved_orders),
            "policy_results": [],
        }

    queue_path = append_result.get("order_queue_path") or append_result.get("path")
    for order in approved_orders:
        order_id = str(order.get("id", "") or "").strip()
        if not order_id:
            policy_errors += 1
            policy_results.append(
                {
                    "ok": False,
                    "order_id": order_id,
                    "source_signal_id": order.get("source_signal_id", ""),
                    "status": "error",
                    "reason": "created APPROVED order has no id",
                }
            )
            continue

        policy_checked += 1
        try:
            if queue_path:
                result = operation_policy_gate.apply_operation_policy_gate_for_order(order_id, queue_path=queue_path)
            else:
                result = operation_policy_gate.apply_operation_policy_gate_for_order(order_id)
        except Exception as exc:
            policy_errors += 1
            policy_results.append(
                {
                    "ok": False,
                    "order_id": order_id,
                    "source_signal_id": order.get("source_signal_id", ""),
                    "status": "error",
                    "reason": f"operation policy gate failed: {exc}",
                }
            )
            continue

        try:
            from decision_trace_stage_observer import observe_policy_result

            observe_policy_result(
                order,
                result,
                gate_input={
                    "order_status": str(order.get("status") or ""),
                    "order_side": str(order.get("side") or ""),
                    "execution_enabled": bool(order.get("execution_enabled", False)),
                },
            )
        except Exception:
            pass

        after_status = str(result.get("after_status") or result.get("policy_status") or "").upper()
        item = {
            "ok": bool(result.get("ok")),
            "order_id": order_id,
            "source_signal_id": order.get("source_signal_id", ""),
            "status": result.get("status", ""),
            "after_status": after_status,
            "policy_status": result.get("policy_status", ""),
            "reason": result.get("reason", ""),
        }
        policy_results.append(item)
        if result.get("ok") is not True:
            policy_errors += 1
        elif after_status == "EXECUTABLE":
            policy_executable += 1
        elif after_status == "BLOCKED_POLICY":
            policy_blocked += 1
        else:
            policy_errors += 1

    ok = policy_errors == 0
    reason = "" if ok else "operation policy gate failed; signal status update skipped"
    return {
        "ok": ok,
        "reason": reason,
        "policy_checked": policy_checked,
        "policy_executable": policy_executable,
        "policy_blocked": policy_blocked,
        "policy_errors": policy_errors,
        "policy_results": policy_results,
    }


def _build_order_queue_candidates_for_signals(
    signals: list[dict[str, Any]],
    *,
    apply_approval: bool = False,
) -> dict[str, Any]:
    """Append order candidates for selected PENDING signals only."""
    if not (
        callable(read_order_queue)
        and callable(append_order_candidates)
        and callable(signal_to_order_candidate)
        and callable(signal_to_order_candidates)
    ):
        return {
            "ok": False,
            "orders_created": 0,
            "duplicates": 0,
            "ignored": len(signals),
            "approval_checked": 0,
            "approved": 0,
            "blocked": 0,
            "policy_checked": 0,
            "policy_executable": 0,
            "policy_blocked": 0,
            "policy_errors": 0,
            "policy_results": [],
            "reason": "order_queue helpers unavailable",
        }

    order_data = read_order_queue()
    orders = order_data.get("orders", [])
    if not isinstance(orders, list):
        orders = []
        order_data["orders"] = orders

    existing_keys = {
        _order_dedupe_key(order)
        for order in orders
        if isinstance(order, dict)
    }

    created_orders: list[dict[str, Any]] = []
    duplicates = 0
    ignored = 0
    execution_switch_blocked = 0

    for signal in signals:
        if not isinstance(signal, dict):
            ignored += 1
            continue

        admission = routine_execution_intent_admission(signal)
        if admission.get("allowed") is not True:
            ignored += 1
            execution_switch_blocked += 1
            continue

        signal_orders = signal_to_order_candidates(signal, len(orders) + 1)
        if not signal_orders:
            ignored += 1
            continue
        for order in signal_orders:
            order["execution_enabled"] = False
            key = _order_dedupe_key(order)
            if key in existing_keys:
                duplicates += 1
                continue
            orders.append(order)
            created_orders.append(order)
            existing_keys.add(key)

    approval_checked = 0
    approved = 0
    approval_blocked = 0
    approval_results: list[dict[str, Any]] = []

    if apply_approval and callable(evaluate_order_approval):
        provenance_approved_at = datetime.now(ZoneInfo("Asia/Seoul")).isoformat(
            timespec="milliseconds"
        )
        for order in created_orders:
            result = evaluate_order_approval(order)
            try:
                from decision_trace_stage_observer import observe_approval_result

                observe_approval_result(order, result)
            except Exception:
                pass
            approval_status = str(result.get("approval_status", "") or "").upper()
            approval_checked += 1
            order["approval_status"] = result.get("approval_status", "")
            order["approval_reason"] = result.get("approval_reason", "")
            order["execution_enabled"] = False
            execution_intent = order.get("execution_intent")
            if (
                isinstance(execution_intent, dict)
                and execution_intent.get("execution_process_owner_required") is True
            ):
                execution_intent["provenance_approved_at"] = provenance_approved_at
            if approval_status == "APPROVED":
                order["status"] = "APPROVED"
                approved += 1
            elif approval_status == "BLOCKED":
                order["status"] = "BLOCKED"
                approval_blocked += 1
            approval_results.append(
                {
                    "order_id": order.get("id", ""),
                    "source_signal_id": order.get("source_signal_id", ""),
                    "approval_status": result.get("approval_status", ""),
                    "approval_reason": result.get("approval_reason", ""),
                }
            )

    append_result: dict[str, Any] = {
        "ok": True,
        "orders_created": 0,
        "duplicates": duplicates,
        "ignored": ignored,
        "order_queue_written": False,
        "created_orders": [],
        "duplicate_orders": [],
        "policy_checked": 0,
        "policy_executable": 0,
        "policy_blocked": 0,
        "policy_errors": 0,
        "policy_results": [],
    }
    if created_orders:
        append_result = append_order_candidates(created_orders)
        if not append_result.get("ok"):
            return {
                "ok": False,
                "orders_created": 0,
                "duplicates": duplicates + int(append_result.get("duplicates", 0) or 0),
                "ignored": ignored,
                "approval_checked": approval_checked,
                "approved": approved,
                "blocked": approval_blocked,
                "order_queue_written": bool(append_result.get("order_queue_written")),
                "execution_enabled_all_false": True,
                "approval_results": approval_results,
                "policy_checked": 0,
                "policy_executable": 0,
                "policy_blocked": 0,
                "policy_errors": 0,
                "policy_results": [],
                "reason": append_result.get("reason", "order_queue append failed"),
                "append_result": append_result,
            }
        policy_result = _apply_operation_policy_to_created_orders(append_result)
        append_result["policy_checked"] = policy_result["policy_checked"]
        append_result["policy_executable"] = policy_result["policy_executable"]
        append_result["policy_blocked"] = policy_result["policy_blocked"]
        append_result["policy_errors"] = policy_result["policy_errors"]
        append_result["policy_results"] = policy_result["policy_results"]
        if policy_result["ok"] is not True:
            return {
                "ok": False,
                "orders_created": int(append_result.get("orders_created", 0) or 0),
                "duplicates": duplicates + int(append_result.get("duplicates", 0) or 0),
                "ignored": ignored,
                "approval_checked": approval_checked,
                "approved": approved,
                "blocked": approval_blocked,
                "order_queue_written": bool(append_result.get("order_queue_written")),
                "execution_enabled_all_false": True,
                "approval_results": approval_results,
                "policy_checked": policy_result["policy_checked"],
                "policy_executable": policy_result["policy_executable"],
                "policy_blocked": policy_result["policy_blocked"],
                "policy_errors": policy_result["policy_errors"],
                "policy_results": policy_result["policy_results"],
                "reason": policy_result["reason"],
                "append_result": append_result,
            }

    return {
        "ok": True,
        "orders_created": int(append_result.get("orders_created", len(created_orders)) or 0),
        "duplicates": duplicates + int(append_result.get("duplicates", 0) or 0),
        "ignored": ignored,
        "execution_switch_blocked": execution_switch_blocked,
        "approval_checked": approval_checked,
        "approved": approved,
        "blocked": approval_blocked,
        "order_queue_written": bool(append_result.get("order_queue_written")),
        "execution_enabled_all_false": all(order.get("execution_enabled") is False for order in created_orders),
        "approval_results": approval_results,
        "policy_checked": int(append_result.get("policy_checked", 0) or 0),
        "policy_executable": int(append_result.get("policy_executable", 0) or 0),
        "policy_blocked": int(append_result.get("policy_blocked", 0) or 0),
        "policy_errors": int(append_result.get("policy_errors", 0) or 0),
        "policy_results": append_result.get("policy_results", []),
        "executable_order_ids": [
            str(item.get("order_id") or "").strip()
            for item in append_result.get("policy_results", [])
            if isinstance(item, dict)
            and str(item.get("after_status") or "").upper() == "EXECUTABLE"
            and str(item.get("order_id") or "").strip()
        ],
        "append_result": append_result,
    }


def enqueue_replanned_execution_intents(
    signal: Any,
    execution_intents: Any,
    *,
    apply_approval: bool = True,
) -> dict[str, Any]:
    """Re-enter a safe in-memory replan through the existing generic pipeline."""
    signal_record = dict(signal) if isinstance(signal, dict) else {}
    intents = [dict(item) for item in execution_intents] if isinstance(execution_intents, list) else []
    if not signal_record or not intents or any(not item for item in intents):
        return {"ok": False, "reason": "REPLAN_SIGNAL_OR_INTENTS_INVALID", "executable_order_ids": []}
    source_signal_id = str(signal_record.get("id") or "").strip()
    process_ids = {str(item.get("execution_process_id") or "").strip() for item in intents}
    source_ids = {str(item.get("source_signal_id") or "").strip() for item in intents}
    if (
        not source_signal_id
        or source_ids != {source_signal_id}
        or len(process_ids) != 1
        or "" in process_ids
    ):
        return {"ok": False, "reason": "REPLAN_IDENTITY_INVALID", "executable_order_ids": []}
    prepared = dict(signal_record)
    prepared["status"] = "PENDING"
    prepared["execution_intent"] = deepcopy(intents[0])
    prepared["execution_intents"] = deepcopy(intents)
    return _build_order_queue_candidates_for_signals(
        [prepared],
        apply_approval=apply_approval,
    )


def enqueue_price_reset_generation(
    proposal: Any,
    *,
    apply_approval: bool = True,
) -> dict[str, Any]:
    """Persist one reset generation and re-enter the existing generic child pipeline."""
    item = dict(proposal) if isinstance(proposal, dict) else {}
    signal = dict(item.get("signal")) if isinstance(item.get("signal"), dict) else {}
    intents = [dict(value) for value in item.get("execution_intents", []) if isinstance(value, dict)]
    signal_id = str(signal.get("id") or "").strip()
    if not signal_id or not intents or len(intents) != len(item.get("execution_intents", [])):
        return {"ok": False, "reason": "PRICE_RESET_PROPOSAL_INVALID", "executable_order_ids": []}
    metadata = {
        "execution_intent": deepcopy(intents[0]),
        "execution_intents": deepcopy(intents),
        "price_reset_plan_generation": item.get("plan_generation"),
        "price_reset_source_snapshot_hash": item.get("trigger_snapshot_hash"),
    }
    persisted = update_signal_status(signal_id, STATUS_PENDING, metadata=metadata)
    if persisted.get("ok") is not True:
        return {
            "ok": False,
            "reason": persisted.get("reason") or "PRICE_RESET_SIGNAL_UPDATE_FAILED",
            "signal_status_update": persisted,
            "executable_order_ids": [],
        }
    mode = str(intents[0].get("execution_mode") or "SINGLE_ORDER").strip().upper()
    if mode in {"MULTI_TIME", "MULTI_RATIO"}:
        return {
            "ok": True,
            "orders_created": 0,
            "deferred": True,
            "signal_status_update": persisted,
            "executable_order_ids": [],
        }
    result = enqueue_replanned_execution_intents(
        signal,
        intents,
        apply_approval=apply_approval,
    )
    result["signal_status_update"] = persisted
    if result.get("ok") is not True:
        return result
    completed = update_signal_status(
        signal_id,
        STATUS_PREVIEWED,
        metadata={
            "price_reset_plan_generation": item.get("plan_generation"),
            "price_reset_source_snapshot_hash": item.get("trigger_snapshot_hash"),
        },
    )
    result["signal_completion_update"] = completed
    if completed.get("ok") is not True:
        result["ok"] = False
        result["reason"] = completed.get("reason") or "PRICE_RESET_SIGNAL_COMPLETION_FAILED"
    return result


def enqueue_repeat_sell_generation(
    proposal: Any,
    *,
    apply_approval: bool = True,
) -> dict[str, Any]:
    """Persist one follow-up SELL generation through the canonical signal writer."""
    item = dict(proposal) if isinstance(proposal, dict) else {}
    signal = dict(item.get("signal")) if isinstance(item.get("signal"), dict) else {}
    intents = [
        dict(value)
        for value in item.get("execution_intents", [])
        if isinstance(value, dict)
    ]
    signal_id = str(signal.get("id") or "").strip()
    if (
        not signal_id
        or not intents
        or len(intents) != len(item.get("execution_intents", []))
        or any(value.get("repeat_generation") is not True for value in intents)
    ):
        return {
            "ok": False,
            "reason": "SELL_REPEAT_PROPOSAL_INVALID",
            "executable_order_ids": [],
        }
    metadata = {
        "execution_intent": deepcopy(intents[0]),
        "execution_intents": deepcopy(intents),
        "repeat_plan_generation": item.get("plan_generation"),
        "repeat_source_snapshot_hash": item.get("repeat_source_snapshot_hash"),
    }
    persisted = update_signal_status(signal_id, STATUS_PENDING, metadata=metadata)
    if persisted.get("ok") is not True:
        return {
            "ok": False,
            "reason": persisted.get("reason") or "SELL_REPEAT_SIGNAL_UPDATE_FAILED",
            "signal_status_update": persisted,
            "executable_order_ids": [],
        }
    mode = str(intents[0].get("execution_mode") or "SINGLE_ORDER").strip().upper()
    if mode in {"MULTI_TIME", "MULTI_RATIO"}:
        return {
            "ok": True,
            "orders_created": 0,
            "deferred": True,
            "signal_status_update": persisted,
            "executable_order_ids": [],
        }
    result = enqueue_replanned_execution_intents(
        signal,
        intents,
        apply_approval=apply_approval,
    )
    result["signal_status_update"] = persisted
    if result.get("ok") is not True:
        return result
    completed = update_signal_status(
        signal_id,
        STATUS_PREVIEWED,
        metadata={
            "repeat_plan_generation": item.get("plan_generation"),
            "repeat_source_snapshot_hash": item.get("repeat_source_snapshot_hash"),
        },
    )
    result["signal_completion_update"] = completed
    if completed.get("ok") is not True:
        result["ok"] = False
        result["reason"] = completed.get("reason") or "SELL_REPEAT_SIGNAL_COMPLETION_FAILED"
    return result


def record_repeat_sell_exit(proposal: Any) -> dict[str, Any]:
    """Persist repeat-exit evidence through the existing canonical signal writer."""
    item = dict(proposal) if isinstance(proposal, dict) else {}
    signal_id = str(item.get("source_signal_id") or "").strip()
    process_id = str(item.get("execution_process_id") or "").strip()
    snapshot_hash = str(item.get("exit_source_snapshot_hash") or "").strip()
    snapshot = item.get("exit_source_snapshot")
    if (
        not signal_id
        or not process_id
        or not snapshot_hash
        or not isinstance(snapshot, dict)
        or str(snapshot.get("snapshot_hash") or "").strip() != snapshot_hash
    ):
        return {"ok": False, "reason": "SELL_REPEAT_EXIT_PROPOSAL_INVALID"}
    evidence = {
        "policy": "SELL_REPEAT_EXIT",
        "execution_process_id": process_id,
        "source_signal_id": signal_id,
        "exit_condition_type": item.get("exit_condition_type"),
        "exit_condition_types": deepcopy(item.get("exit_condition_types") or []),
        "exit_triggered_at": item.get("exit_triggered_at"),
        "exit_source_snapshot_hash": snapshot_hash,
        "exit_source_snapshot": deepcopy(snapshot),
        "evaluated_generation": item.get("evaluated_generation"),
        "reason": item.get("reason") or "SELL_REPEAT_EXIT_CONDITION_MATCHED",
    }
    status = str(item.get("signal_status") or STATUS_PREVIEWED).strip().upper()
    return update_signal_status(
        signal_id,
        status,
        metadata={"sell_repeat_exit_evidence": evidence},
    )


def record_buy_repeat_exit_completion(proposal: Any) -> dict[str, Any]:
    """Persist BUY phase-completion evidence through the canonical signal writer."""
    item = dict(proposal) if isinstance(proposal, dict) else {}
    signal_id = str(item.get("source_signal_id") or "").strip()
    process_id = str(item.get("execution_process_id") or "").strip()
    routine_instance_id = str(item.get("routine_instance_id") or "").strip()
    cycle_identity = str(item.get("cycle_identity") or "").strip()
    snapshot_hash = str(item.get("exit_source_snapshot_hash") or "").strip()
    snapshot = item.get("exit_source_snapshot")
    if not signal_id or not process_id or not routine_instance_id or not cycle_identity \
            or not snapshot_hash or not isinstance(snapshot, dict) \
            or str(snapshot.get("snapshot_hash") or "").strip() != snapshot_hash:
        return {"ok": False, "reason": "BUY_REPEAT_EXIT_PROPOSAL_INVALID"}
    evidence = {
        "policy": "BUY_REPEAT_EXIT",
        "execution_process_id": process_id,
        "source_signal_id": signal_id,
        "routine_instance_id": routine_instance_id,
        "cycle_identity": cycle_identity,
        "exit_condition_type": item.get("exit_condition_type"),
        "exit_condition_types": deepcopy(item.get("exit_condition_types") or []),
        "exit_triggered_at": item.get("exit_triggered_at"),
        "evaluated_buy_round": item.get("evaluated_generation"),
        "repeat_completed_count": item.get("repeat_completed_count"),
        "repeat_started_at": item.get("repeat_started_at"),
        "exit_source_snapshot_hash": snapshot_hash,
        "exit_source_snapshot": deepcopy(snapshot),
        "reason": item.get("reason") or "BUY_REPEAT_EXIT_CONDITION_MATCHED",
        "cancel_required": item.get("cancel_required") is True,
        "cancel_effect_confirmed": item.get("cancel_effect_confirmed") is True,
        "buy_phase_completed": item.get("buy_phase_completed") is True,
    }
    status = str(item.get("signal_status") or STATUS_PREVIEWED).strip().upper()
    return update_signal_status(signal_id, status, metadata={"buy_exit_evidence": evidence})


def enqueue_final_residual_sell_exit(
    proposal: Any,
    *,
    apply_approval: bool = True,
) -> dict[str, Any]:
    """Persist and enqueue one final residual MARKET SELL via canonical paths."""
    item = dict(proposal) if isinstance(proposal, dict) else {}
    signal = dict(item.get("signal")) if isinstance(item.get("signal"), dict) else {}
    intents = [
        dict(value)
        for value in item.get("execution_intents", [])
        if isinstance(value, dict)
    ]
    signal_id = str(signal.get("id") or "").strip()
    process_id = str(item.get("execution_process_id") or "").strip()
    action_hash = str(item.get("final_residual_exit_action_hash") or "").strip()
    exit_hash = str(item.get("repeat_exit_source_snapshot_hash") or "").strip()
    valid = (
        bool(signal_id and process_id and action_hash and exit_hash)
        and len(intents) == 1
        and len(intents) == len(item.get("execution_intents", []))
        and intents[0].get("final_residual_exit") is True
        and str(intents[0].get("side") or "").strip().upper() == "SELL"
        and str(intents[0].get("execution_mode") or "").strip().upper()
        == "SINGLE_ORDER"
        and str(intents[0].get("hoga") or "").strip().upper() == "MARKET"
        and str(intents[0].get("price_basis") or "").strip().upper() == "MARKET"
        and intents[0].get("price") is None
        and str(intents[0].get("source_signal_id") or "").strip() == signal_id
        and str(intents[0].get("execution_process_id") or "").strip() == process_id
        and str(intents[0].get("final_residual_exit_action_hash") or "").strip()
        == action_hash
        and str(intents[0].get("repeat_exit_source_snapshot_hash") or "").strip()
        == exit_hash
    )
    if not valid:
        return {
            "ok": False,
            "reason": "SELL_FINAL_RESIDUAL_EXIT_PROPOSAL_INVALID",
            "executable_order_ids": [],
        }

    requested_evidence = {
        "policy": "SELL_FINAL_RESIDUAL_MARKET_EXIT",
        "status": "REQUESTED",
        "execution_process_id": process_id,
        "source_signal_id": signal_id,
        "repeat_exit_source_snapshot_hash": exit_hash,
        "final_residual_exit_action_hash": action_hash,
        "final_residual_exit_source_snapshot_hash": item.get(
            "final_residual_exit_source_snapshot_hash"
        ),
        "plan_generation": item.get("plan_generation"),
        "requested_quantity": item.get("latest_sellable_quantity"),
        "execution_ids": [intents[0].get("execution_id")],
        "resulting_holding_zero_confirmed": False,
    }
    persisted = update_signal_status(
        signal_id,
        STATUS_PENDING,
        metadata={
            "execution_intent": deepcopy(intents[0]),
            "execution_intents": deepcopy(intents),
            "final_residual_exit_evidence": requested_evidence,
        },
    )
    if persisted.get("ok") is not True:
        return {
            "ok": False,
            "reason": persisted.get("reason")
            or "SELL_FINAL_RESIDUAL_EXIT_SIGNAL_UPDATE_FAILED",
            "signal_status_update": persisted,
            "executable_order_ids": [],
        }

    result = enqueue_replanned_execution_intents(
        signal,
        intents,
        apply_approval=apply_approval,
    )
    result["signal_status_update"] = persisted
    if result.get("ok") is not True:
        return result

    created_orders = (
        result.get("append_result", {}).get("created_orders", [])
        if isinstance(result.get("append_result"), dict)
        else []
    )
    queued_evidence = deepcopy(requested_evidence)
    queued_evidence.update(
        {
            "status": "ORDER_QUEUED",
            "order_ids": [
                str(order.get("id") or order.get("order_id") or "").strip()
                for order in created_orders
                if isinstance(order, dict)
                and str(order.get("id") or order.get("order_id") or "").strip()
            ],
        }
    )
    completed = update_signal_status(
        signal_id,
        STATUS_PREVIEWED,
        metadata={"final_residual_exit_evidence": queued_evidence},
    )
    result["signal_completion_update"] = completed
    if completed.get("ok") is not True:
        result["ok"] = False
        result["reason"] = completed.get("reason") or (
            "SELL_FINAL_RESIDUAL_EXIT_SIGNAL_COMPLETION_FAILED"
        )
    return result


def record_final_residual_sell_exit_completion(proposal: Any) -> dict[str, Any]:
    """Record holding-zero confirmation with the existing signal writer."""
    item = dict(proposal) if isinstance(proposal, dict) else {}
    signal_id = str(item.get("source_signal_id") or "").strip()
    process_id = str(item.get("execution_process_id") or "").strip()
    action_hash = str(item.get("final_residual_exit_action_hash") or "").strip()
    exit_hash = str(item.get("repeat_exit_source_snapshot_hash") or "").strip()
    if (
        not signal_id
        or not process_id
        or not action_hash
        or not exit_hash
        or item.get("resulting_holding_zero_confirmed") is not True
    ):
        return {
            "ok": False,
            "reason": "SELL_FINAL_RESIDUAL_EXIT_COMPLETION_INVALID",
        }
    evidence = {
        "policy": "SELL_FINAL_RESIDUAL_MARKET_EXIT",
        "status": "HOLDING_ZERO_CONFIRMED",
        "execution_process_id": process_id,
        "source_signal_id": signal_id,
        "repeat_exit_source_snapshot_hash": exit_hash,
        "final_residual_exit_action_hash": action_hash,
        "final_execution_id": item.get("final_execution_id"),
        "final_order_id": item.get("final_order_id"),
        "completed_at": item.get("completed_at"),
        "reason": item.get("reason"),
        "resulting_holding_zero_confirmed": True,
    }
    return update_signal_status(
        signal_id,
        STATUS_DONE,
        metadata={"final_residual_exit_evidence": evidence},
    )


def enqueue_scheduled_time_slice(
    proposal: Any,
    *,
    apply_approval: bool = True,
) -> dict[str, Any]:
    """Send one due TIME_SLICE through the existing generic candidate pipeline."""
    item = dict(proposal) if isinstance(proposal, dict) else {}
    signal = dict(item.get("signal")) if isinstance(item.get("signal"), dict) else {}
    intents = item.get("execution_intents")
    if (
        not signal
        or not isinstance(intents, list)
        or len(intents) != 1
        or not isinstance(intents[0], dict)
        or str(intents[0].get("execution_mode") or "").strip().upper() != "MULTI_TIME"
        or str(intents[0].get("child_kind") or "").strip().upper() != "TIME_SLICE"
    ):
        return {"ok": False, "reason": "TIME_SLICE_PROPOSAL_INVALID", "executable_order_ids": []}
    signal["status"] = "PENDING"
    signal["execution_intent"] = deepcopy(intents[0])
    signal["execution_intents"] = [deepcopy(intents[0])]
    result = _build_order_queue_candidates_for_signals(
        [signal],
        apply_approval=apply_approval,
    )
    if result.get("ok") is not True or item.get("complete_after_enqueue") is not True:
        return result
    if int(result.get("orders_created", 0) or 0) == 0 and int(result.get("duplicates", 0) or 0) == 0:
        # Admission may reject a due child without enqueueing anything. Keep
        # the durable plan pending so recovery cannot mistake it for complete.
        return result
    signal_id = str(signal.get("id") or "").strip()
    if not signal_id:
        result["ok"] = False
        result["reason"] = "TIME_SLICE_SIGNAL_ID_MISSING"
        return result
    status_result = update_signal_status(
        signal_id,
        STATUS_PREVIEWED,
        metadata={
            "time_slice_plan_complete": True,
            "time_slice_last_child_sequence_index": intents[0].get("child_sequence_index"),
        },
    )
    result["signal_status_update"] = status_result
    if status_result.get("ok") is not True:
        result["ok"] = False
        result["reason"] = status_result.get("reason") or "TIME_SLICE_SIGNAL_STATUS_UPDATE_FAILED"
    return result


def enqueue_eligible_ratio_slice(
    proposal: Any,
    *,
    apply_approval: bool = True,
) -> dict[str, Any]:
    """Send one eligible RATIO_SLICE through the existing generic pipeline."""
    item = dict(proposal) if isinstance(proposal, dict) else {}
    signal = dict(item.get("signal")) if isinstance(item.get("signal"), dict) else {}
    intents = item.get("execution_intents")
    if (
        not signal
        or not isinstance(intents, list)
        or len(intents) != 1
        or not isinstance(intents[0], dict)
        or str(intents[0].get("execution_mode") or "").strip().upper() != "MULTI_RATIO"
        or str(intents[0].get("child_kind") or "").strip().upper() != "RATIO_SLICE"
    ):
        return {"ok": False, "reason": "RATIO_SLICE_PROPOSAL_INVALID", "executable_order_ids": []}
    signal["status"] = "PENDING"
    signal["execution_intent"] = deepcopy(intents[0])
    signal["execution_intents"] = [deepcopy(intents[0])]
    result = _build_order_queue_candidates_for_signals(
        [signal],
        apply_approval=apply_approval,
    )
    if result.get("ok") is not True or item.get("complete_after_enqueue") is not True:
        return result
    signal_id = str(signal.get("id") or "").strip()
    if not signal_id:
        result["ok"] = False
        result["reason"] = "RATIO_SLICE_SIGNAL_ID_MISSING"
        return result
    if int(result.get("orders_created", 0) or 0) == 0 and int(result.get("duplicates", 0) or 0) == 0:
        return result
    status_result = update_signal_status(
        signal_id,
        STATUS_PREVIEWED,
        metadata={
            "ratio_slice_plan_complete": True,
            "ratio_slice_last_child_sequence_index": intents[0].get("child_sequence_index"),
        },
    )
    result["signal_status_update"] = status_result
    if status_result.get("ok") is not True:
        result["ok"] = False
        result["reason"] = status_result.get("reason") or "RATIO_SLICE_SIGNAL_STATUS_UPDATE_FAILED"
    return result


def consume_pending_routine_signals_dry_run(
    limit: int | None = None,
    mark_previewed: bool = False,
    write_order_queue: bool = False,
    apply_approval: bool = False,
    allowed_stock_codes: Iterable[Any] | None = None,
    signal_cutoff_by_stock_code: dict[Any, Any] | None = None,
) -> dict[str, Any]:
    """Consume pending routine signals in memory with OrderManager + payload preview."""
    signals = load_pending_routine_signals(
        allowed_stock_codes=allowed_stock_codes,
        signal_cutoff_by_stock_code=signal_cutoff_by_stock_code,
    )
    signals = [signal for signal in signals if not _is_deferred_child_signal(signal)]
    clean_limit = _clean_limit(limit)
    if clean_limit is not None:
        signals = signals[:clean_limit]

    results = [
        dry_run_order_manager_for_signal_with_payload_preview(signal)
        for signal in signals
    ]
    order_queue_result: dict[str, Any] = {
        "ok": True,
        "orders_created": 0,
        "duplicates": 0,
        "ignored": 0,
            "approval_checked": 0,
            "approved": 0,
            "blocked": 0,
            "policy_checked": 0,
            "policy_executable": 0,
            "policy_blocked": 0,
            "policy_errors": 0,
            "order_queue_written": False,
            "execution_enabled_all_false": True,
            "approval_results": [],
            "policy_results": [],
        }
    if write_order_queue:
        order_queue_result = _build_order_queue_candidates_for_signals(
            signals,
            apply_approval=apply_approval,
        )

    status_update_results: list[dict[str, Any]] = []
    if mark_previewed and (not write_order_queue or order_queue_result.get("ok") is True):
        for signal, result in zip(signals, results):
            signal_id = str(signal.get("id", "") or "")
            next_status = _preview_status_for_result(result)
            metadata = _preview_metadata_for_result(result)
            try:
                update_result = update_signal_status(signal_id, next_status, metadata=metadata)
            except Exception as exc:
                update_result = {
                    "ok": False,
                    "signal_id": signal_id,
                    "after_status": STATUS_ERROR,
                    "reason": f"status update failed: {exc}",
                }
            status_update_results.append(update_result)
    elif mark_previewed and write_order_queue and order_queue_result.get("ok") is not True:
        status_update_results.append(
            {
                "ok": False,
                "after_status": STATUS_ERROR,
                "reason": order_queue_result.get("reason") or "order_queue write failed; signal status update skipped",
            }
        )

    allowed = sum(1 for item in results if bool(item.get("order_manager_allowed")))
    blocked = sum(
        1
        for item in results
        if item.get("order_manager", {}).get("ok") and not bool(item.get("order_manager_allowed"))
    )
    errors = sum(
        1
        for item in results
        if not item.get("order_manager", {}).get("ok") or not item.get("payload_built")
    )
    execution_enabled_values = [
        item.get("payload_preview", {}).get("execution_enabled")
        for item in results
        if isinstance(item.get("payload_preview"), dict)
    ]
    marked_previewed = sum(
        1 for item in status_update_results
        if item.get("ok") and item.get("after_status") == STATUS_PREVIEWED
    )
    marked_blocked = sum(
        1 for item in status_update_results
        if item.get("ok") and item.get("after_status") == STATUS_BLOCKED
    )
    marked_error = sum(
        1
        for item in status_update_results
        if item.get("after_status") == STATUS_ERROR or not item.get("ok")
    )

    return {
        "summary": {
            "signals_checked": len(signals),
            "consumed_preview_count": len(results),
            "allowed": allowed,
            "blocked": blocked,
            "errors": errors,
            "send_order_called": False,
            "files_mutated": bool(order_queue_result.get("order_queue_written")),
            "queue_status_changed": bool(mark_previewed),
            "execution_enabled_all_false": all(value is False for value in execution_enabled_values),
            "status_updated_count": sum(1 for item in status_update_results if item.get("ok")),
            "marked_previewed": marked_previewed,
            "marked_blocked": marked_blocked,
            "marked_error": marked_error,
            "orders_created": int(order_queue_result.get("orders_created", 0) or 0),
            "order_queue_written": bool(order_queue_result.get("order_queue_written")),
            "approval_checked": int(order_queue_result.get("approval_checked", 0) or 0),
            "approved": int(order_queue_result.get("approved", 0) or 0),
            "approval_blocked": int(order_queue_result.get("blocked", 0) or 0),
            "policy_checked": int(order_queue_result.get("policy_checked", 0) or 0),
            "policy_executable": int(order_queue_result.get("policy_executable", 0) or 0),
            "policy_blocked": int(order_queue_result.get("policy_blocked", 0) or 0),
            "policy_errors": int(order_queue_result.get("policy_errors", 0) or 0),
            "executable_order_ids": list(order_queue_result.get("executable_order_ids") or []),
        },
        "order_queue": order_queue_result,
        "status_updates": status_update_results,
        "results": results,
    }


if __name__ == "__main__":
    print(json.dumps(consume_pending_routine_signals_dry_run(), ensure_ascii=False, indent=2))
