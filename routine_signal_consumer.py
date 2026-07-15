# -*- coding: utf-8 -*-
"""Dry-run consumer for routine signals.

This module consumes PENDING BUY/SELL routine signals. By default it only asks
the bridge for an OrderManager dry-run and an order payload preview. Optional
flags can update routine signal status and write order_queue.json candidates,
but it never mutates orders.json, calls an executor, or sends an order.
"""

from __future__ import annotations

import json
from typing import Any

from routine_signal_order_bridge import (
    dry_run_order_manager_for_signal_with_payload_preview,
    load_pending_routine_signals,
)
from routine_signal_queue import (
    STATUS_BLOCKED,
    STATUS_ERROR,
    STATUS_PREVIEWED,
    update_signal_status,
)

try:
    from order_queue import append_order_candidates, read_order_queue, signal_to_order_candidate
except Exception:  # pragma: no cover
    append_order_candidates = None
    read_order_queue = None
    signal_to_order_candidate = None

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
    return "|".join(
        [
            str(order.get("source_signal_id", "")),
            str(order.get("routine", "")),
            str(order.get("code", "")),
            str(order.get("side", "")),
        ]
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
    if not callable(read_order_queue) or not callable(append_order_candidates) or not callable(signal_to_order_candidate):
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

    for signal in signals:
        if not isinstance(signal, dict):
            ignored += 1
            continue

        order = signal_to_order_candidate(signal, len(orders) + 1)
        if order is None:
            ignored += 1
            continue

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
        for order in created_orders:
            result = evaluate_order_approval(order)
            approval_status = str(result.get("approval_status", "") or "").upper()
            approval_checked += 1
            order["approval_status"] = result.get("approval_status", "")
            order["approval_reason"] = result.get("approval_reason", "")
            order["execution_enabled"] = False
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
        "append_result": append_result,
    }


def consume_pending_routine_signals_dry_run(
    limit: int | None = None,
    mark_previewed: bool = False,
    write_order_queue: bool = False,
    apply_approval: bool = False,
) -> dict[str, Any]:
    """Consume pending routine signals in memory with OrderManager + payload preview."""
    signals = load_pending_routine_signals()
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
        },
        "order_queue": order_queue_result,
        "status_updates": status_update_results,
        "results": results,
    }


if __name__ == "__main__":
    print(json.dumps(consume_pending_routine_signals_dry_run(), ensure_ascii=False, indent=2))
