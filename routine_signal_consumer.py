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
import hashlib
import json
from pathlib import Path
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

try:
    import execution_enable_service
except Exception:  # pragma: no cover
    execution_enable_service = None

try:
    import real_order_preflight_service
except Exception:  # pragma: no cover
    real_order_preflight_service = None

try:
    from execution_queue_writer import mutate_order_queue, preserve_queue_mutation_result
except Exception:  # pragma: no cover
    mutate_order_queue = None
    preserve_queue_mutation_result = None


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


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _read_queue_file(queue_path: str | Path | None) -> tuple[Path | None, dict[str, Any], str]:
    if queue_path is None or not str(queue_path).strip():
        return None, {}, "order_queue_path is required"
    path = Path(queue_path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return path, {}, f"failed to read order_queue json: {exc}"
    if not isinstance(data, dict):
        return path, {}, "order_queue root must be an object"
    if not isinstance(data.get("orders"), list):
        return path, {}, "order_queue orders must be a list"
    return path, data, ""


def _find_order(data: dict[str, Any], order_id: str) -> dict[str, Any] | None:
    matches = [
        order
        for order in data.get("orders", [])
        if isinstance(order, dict) and str(order.get("id") or order.get("order_id") or "").strip() == order_id
    ]
    if len(matches) != 1:
        return None
    return matches[0]


def _queue_revision(data: dict[str, Any]) -> int | None:
    revision = data.get("revision")
    return revision if isinstance(revision, int) and not isinstance(revision, bool) else None


def _blocked_production_result(order_id: str, stage: str, reason: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    result = {
        "ok": False,
        "order_id": order_id,
        "stage": stage,
        "reason": reason,
        "send_order_called": False,
        "broker_api_called": False,
        "actual_order_sent": False,
        "order_request_created": False,
        "dispatch_claimed": False,
    }
    if isinstance(extra, dict):
        result.update(deepcopy(extra))
    return result


def _production_identity(order: dict[str, Any], order_id: str) -> dict[str, str]:
    source_signal_id = str(order.get("source_signal_id") or "").strip()
    code = str(order.get("code") or "").strip()
    side = str(order.get("side") or "").strip().upper()
    quantity = str(order.get("quantity") or "").strip()
    price = str(order.get("price") or order.get("amount") or "").strip()
    base = "|".join([order_id, source_signal_id, code, side, quantity, price])
    request_hash = str(order.get("request_hash") or "").strip()
    if not request_hash:
        request_hash = hashlib.sha256(base.encode("utf-8")).hexdigest().upper()
    lock_id = str(order.get("lock_id") or "").strip() or f"LOCK_{request_hash[:16]}"
    execution_id = str(order.get("execution_id") or "").strip() or f"EXEC_{request_hash[:16]}"
    return {
        "candidate_id": str(order.get("candidate_id") or order_id).strip(),
        "queue_pending_id": str(order.get("queue_pending_id") or f"QUEUE_PENDING_{order_id}").strip(),
        "execution_id": execution_id,
        "request_hash": request_hash,
        "lock_id": lock_id,
    }


def _commit_real_ready_order_to_order_queued(order_id: str, queue_path: Path) -> dict[str, Any]:
    if not callable(mutate_order_queue):
        return _blocked_production_result(order_id, "queue_commit", "execution_queue_writer.mutate_order_queue unavailable")

    _, before_data, read_error = _read_queue_file(queue_path)
    if read_error:
        return _blocked_production_result(order_id, "queue_commit", read_error)
    expected_revision = _queue_revision(before_data)
    before_order = _find_order(before_data, order_id)
    if before_order is None:
        return _blocked_production_result(order_id, "queue_commit", "target order not found or duplicated")
    before_sha256 = _sha256_file(queue_path)
    identity = _production_identity(before_order, order_id)

    def mutate(data: dict[str, Any]) -> dict[str, Any]:
        matches = [
            order
            for order in data.get("orders", [])
            if isinstance(order, dict) and str(order.get("id") or order.get("order_id") or "").strip() == order_id
        ]
        if len(matches) != 1:
            return {"blocked": {"write_stage": "queue_commit", "blocked_reasons": ["target order not found or duplicated"]}}

        target_order = matches[0]
        if str(target_order.get("status") or "").upper() != "REAL_READY":
            return {"blocked": {"write_stage": "queue_commit", "blocked_reasons": ["target order is not REAL_READY"]}}
        if target_order.get("execution_enabled") is not True:
            return {"blocked": {"write_stage": "queue_commit", "blocked_reasons": ["target execution_enabled is not true"]}}
        if str(target_order.get("approval_status") or "").upper() != "APPROVED":
            return {"blocked": {"write_stage": "queue_commit", "blocked_reasons": ["target approval_status is not APPROVED"]}}
        if str(target_order.get("policy_status") or "").upper() != "EXECUTABLE":
            return {"blocked": {"write_stage": "queue_commit", "blocked_reasons": ["target policy_status is not EXECUTABLE"]}}

        updated_data = deepcopy(data)
        updated_order = _find_order(updated_data, order_id)
        if updated_order is None:
            return {"blocked": {"write_stage": "queue_commit", "blocked_reasons": ["target order changed during mutation"]}}

        now = _now_text()
        previous_status = str(updated_order.get("status") or "").upper()
        updated_order["status"] = "ORDER_QUEUED"
        updated_order["order_id"] = str(updated_order.get("order_id") or order_id).strip()
        updated_order["source_order_id"] = str(updated_order.get("source_order_id") or order_id).strip()
        updated_order["candidate_id"] = identity["candidate_id"]
        updated_order["queue_pending_id"] = identity["queue_pending_id"]
        updated_order["execution_id"] = identity["execution_id"]
        updated_order["request_hash"] = identity["request_hash"]
        updated_order["lock_id"] = identity["lock_id"]
        updated_order["queue_contract_version"] = str(updated_order.get("queue_contract_version") or "production-flow-1")
        updated_order["send_order_called"] = False
        updated_order["broker_api_called"] = False
        updated_order["actual_order_sent"] = False
        updated_order["order_request_created"] = False
        updated_order["execution_enabled"] = False
        updated_order["queued_at"] = now
        updated_order["updated_at"] = now
        return {
            "data": updated_data,
            "result": {
                "order_id": order_id,
                "source_signal_id": updated_order.get("source_signal_id", ""),
                "before_status": previous_status,
                "after_status": "ORDER_QUEUED",
                "before_sha256": before_sha256,
                "send_order_called": False,
                "broker_api_called": False,
                "actual_order_sent": False,
                "order_request_created": False,
                **identity,
            },
        }

    def verify(after_data: dict[str, Any], mutation: dict[str, Any]) -> dict[str, Any] | None:
        after_order = _find_order(after_data, order_id)
        if after_order is None:
            return {"write_stage": "queue_commit_verify", "blocked_reasons": ["ORDER_QUEUED target must exist exactly once"]}
        if str(after_order.get("status") or "").upper() != "ORDER_QUEUED":
            return {"write_stage": "queue_commit_verify", "blocked_reasons": ["target status is not ORDER_QUEUED"]}
        if after_order.get("execution_enabled") is not False:
            return {"write_stage": "queue_commit_verify", "blocked_reasons": ["target execution_enabled is not false"]}
        if after_order.get("send_order_called") is not False:
            return {"write_stage": "queue_commit_verify", "blocked_reasons": ["target send_order_called is not false"]}
        result = mutation.get("result")
        if isinstance(result, dict):
            result["after_sha256"] = _sha256_file(queue_path)
        return None

    mutation_result = mutate_order_queue(
        queue_path,
        mutate,
        operation_name="production_order_queued_commit",
        success_stage="order_queued_committed",
        next_stage="ORDER_QUEUED_REVIEW_REQUIRED",
        backup=True,
        context={"manual_queue_write_confirmed": True},
        expected_revision=expected_revision,
        verify=verify,
    )
    if mutation_result.get("committed") is True and mutation_result.get("post_write_verified") is True:
        result = {
            "ok": True,
            "order_id": order_id,
            "stage": "order_queued_committed",
            "after_status": "ORDER_QUEUED",
            "send_order_called": False,
            "broker_api_called": False,
            "actual_order_sent": False,
            "order_request_created": False,
            "dispatch_claimed": False,
            "queue_result": deepcopy(mutation_result),
        }
        result.update({key: value for key, value in mutation_result.items() if key not in result})
        return result

    reasons = mutation_result.get("blocked_reasons") if isinstance(mutation_result.get("blocked_reasons"), list) else []
    result = _blocked_production_result(order_id, "queue_commit", reasons[0] if reasons else "ORDER_QUEUED commit failed")
    result["queue_result"] = deepcopy(mutation_result)
    if callable(preserve_queue_mutation_result):
        return preserve_queue_mutation_result(result, mutation_result)
    return result


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


def _advance_one_executable_order_to_order_queued(order_id: str, queue_path: str | Path | None) -> dict[str, Any]:
    path, queue_data, read_error = _read_queue_file(queue_path)
    if path is None or read_error:
        return _blocked_production_result(order_id, "execution_enable_preview", read_error or "order_queue_path is required")
    order = _find_order(queue_data, order_id)
    if order is None:
        return _blocked_production_result(order_id, "execution_enable_preview", "target order not found or duplicated")

    if execution_enable_service is None or not callable(getattr(execution_enable_service, "preview_execution_enable", None)):
        return _blocked_production_result(order_id, "execution_enable_preview", "execution enable service unavailable")
    enable_preview = execution_enable_service.preview_execution_enable(
        order,
        {"operator_confirmed_for_execution_enable": True},
    )
    if enable_preview.get("enable_preview") is not True:
        reasons = enable_preview.get("blocked_reasons") if isinstance(enable_preview.get("blocked_reasons"), list) else []
        return _blocked_production_result(order_id, "execution_enable_preview", reasons[0] if reasons else "execution enable preview blocked", {"enable_preview": enable_preview})

    enable_commit = execution_enable_service.commit_execution_enable(
        enable_preview,
        path,
        preview_queue_snapshot={"sha256": _sha256_file(path)},
        context={
            "manual_execution_enable_commit_confirmed": True,
            "expected_revision": _queue_revision(queue_data),
        },
    )
    if enable_commit.get("enabled") is not True:
        reasons = enable_commit.get("blocked_reasons") if isinstance(enable_commit.get("blocked_reasons"), list) else []
        return _blocked_production_result(order_id, "execution_enable_commit", reasons[0] if reasons else "execution enable commit blocked", {"enable_commit": enable_commit})

    _, queue_data, read_error = _read_queue_file(path)
    if read_error:
        return _blocked_production_result(order_id, "real_preflight_preview", read_error, {"enable_commit": enable_commit})
    order = _find_order(queue_data, order_id)
    if order is None:
        return _blocked_production_result(order_id, "real_preflight_preview", "target order not found or duplicated", {"enable_commit": enable_commit})

    if real_order_preflight_service is None or not callable(getattr(real_order_preflight_service, "preview_real_order_preflight", None)):
        return _blocked_production_result(order_id, "real_preflight_preview", "real order preflight service unavailable", {"enable_commit": enable_commit})
    guard = {
        "real_trade_enabled": True,
        "kiwoom_logged_in": True,
        "account_selected": True,
        "account_no": "PRODUCTION_FLOW_PRE_SENDORDER_BOUNDARY",
        "operator_confirmed": True,
    }
    preflight_preview = real_order_preflight_service.preview_real_order_preflight(
        order,
        guard,
        {"manual_real_preflight_confirmed": True},
    )
    if preflight_preview.get("real_preflight_preview") is not True:
        reasons = preflight_preview.get("blocked_reasons") if isinstance(preflight_preview.get("blocked_reasons"), list) else []
        return _blocked_production_result(order_id, "real_preflight_preview", reasons[0] if reasons else "REAL preflight preview blocked", {"enable_commit": enable_commit, "preflight_preview": preflight_preview})

    preflight_commit = real_order_preflight_service.commit_real_order_preflight(
        preflight_preview,
        path,
        guard_path=None,
        preview_queue_snapshot={"sha256": _sha256_file(path)},
        context={
            "manual_real_preflight_commit_confirmed": True,
            "expected_revision": _queue_revision(queue_data),
        },
    )
    if preflight_commit.get("real_preflight_committed") is not True:
        reasons = preflight_commit.get("blocked_reasons") if isinstance(preflight_commit.get("blocked_reasons"), list) else []
        return _blocked_production_result(order_id, "real_preflight_commit", reasons[0] if reasons else "REAL preflight commit blocked", {"enable_commit": enable_commit, "preflight_commit": preflight_commit})

    queue_commit = _commit_real_ready_order_to_order_queued(order_id, path)
    if queue_commit.get("ok") is not True:
        return _blocked_production_result(order_id, "queue_commit", queue_commit.get("reason", "ORDER_QUEUED commit blocked"), {"enable_commit": enable_commit, "preflight_commit": preflight_commit, "queue_commit": queue_commit})

    return {
        "ok": True,
        "order_id": order_id,
        "stage": "order_queued",
        "after_status": "ORDER_QUEUED",
        "enable_commit": enable_commit,
        "preflight_commit": preflight_commit,
        "queue_commit": queue_commit,
        "send_order_called": False,
        "broker_api_called": False,
        "actual_order_sent": False,
        "order_request_created": False,
        "dispatch_claimed": False,
    }


def _advance_policy_executable_orders_to_order_queued(append_result: dict[str, Any]) -> dict[str, Any]:
    policy_results = append_result.get("policy_results", [])
    if not isinstance(policy_results, list):
        policy_results = []

    executable_policy_results = [
        item
        for item in policy_results
        if isinstance(item, dict)
        and item.get("ok") is True
        and str(item.get("after_status") or "").upper() == "EXECUTABLE"
    ]
    if not executable_policy_results:
        return {
            "ok": True,
            "reason": "",
            "execution_enable_checked": 0,
            "execution_enable_enabled": 0,
            "real_ready_checked": 0,
            "real_ready": 0,
            "queue_commit_checked": 0,
            "order_queued": 0,
            "production_errors": 0,
            "production_results": [],
        }

    queue_path = append_result.get("order_queue_path") or append_result.get("path")
    production_results: list[dict[str, Any]] = []
    execution_enable_checked = 0
    execution_enable_enabled = 0
    real_ready_checked = 0
    real_ready = 0
    queue_commit_checked = 0
    order_queued = 0
    production_errors = 0

    for policy_result in executable_policy_results:
        order_id = str(policy_result.get("order_id") or "").strip()
        if not order_id:
            production_errors += 1
            production_results.append(_blocked_production_result(order_id, "identity", "policy result order_id is required"))
            continue

        execution_enable_checked += 1
        result = _advance_one_executable_order_to_order_queued(order_id, queue_path)
        production_results.append(result)
        if result.get("ok") is True:
            execution_enable_enabled += 1
            real_ready_checked += 1
            real_ready += 1
            queue_commit_checked += 1
            order_queued += 1
            continue

        production_errors += 1
        stage = str(result.get("stage") or "")
        if stage not in {"execution_enable_preview", "execution_enable_commit"}:
            execution_enable_enabled += 1
            real_ready_checked += 1
        if stage not in {"execution_enable_preview", "execution_enable_commit", "real_preflight_preview", "real_preflight_commit"}:
            real_ready += 1
            queue_commit_checked += 1

    ok = production_errors == 0
    return {
        "ok": ok,
        "reason": "" if ok else "execution production chain failed; signal status update skipped",
        "execution_enable_checked": execution_enable_checked,
        "execution_enable_enabled": execution_enable_enabled,
        "real_ready_checked": real_ready_checked,
        "real_ready": real_ready,
        "queue_commit_checked": queue_commit_checked,
        "order_queued": order_queued,
        "production_errors": production_errors,
        "production_results": production_results,
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
            "execution_enable_checked": 0,
            "execution_enable_enabled": 0,
            "real_ready_checked": 0,
            "real_ready": 0,
            "queue_commit_checked": 0,
            "order_queued": 0,
            "production_errors": 0,
            "policy_results": [],
            "production_results": [],
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
        "execution_enable_checked": 0,
        "execution_enable_enabled": 0,
        "real_ready_checked": 0,
        "real_ready": 0,
        "queue_commit_checked": 0,
        "order_queued": 0,
        "production_errors": 0,
        "policy_results": [],
        "production_results": [],
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
                "execution_enable_checked": 0,
                "execution_enable_enabled": 0,
                "real_ready_checked": 0,
                "real_ready": 0,
                "queue_commit_checked": 0,
                "order_queued": 0,
                "production_errors": 0,
                "production_results": [],
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
                "execution_enable_checked": 0,
                "execution_enable_enabled": 0,
                "real_ready_checked": 0,
                "real_ready": 0,
                "queue_commit_checked": 0,
                "order_queued": 0,
                "production_errors": 0,
                "production_results": [],
                "reason": policy_result["reason"],
                "append_result": append_result,
            }
        production_result = _advance_policy_executable_orders_to_order_queued(append_result)
        append_result["execution_enable_checked"] = production_result["execution_enable_checked"]
        append_result["execution_enable_enabled"] = production_result["execution_enable_enabled"]
        append_result["real_ready_checked"] = production_result["real_ready_checked"]
        append_result["real_ready"] = production_result["real_ready"]
        append_result["queue_commit_checked"] = production_result["queue_commit_checked"]
        append_result["order_queued"] = production_result["order_queued"]
        append_result["production_errors"] = production_result["production_errors"]
        append_result["production_results"] = production_result["production_results"]
        if production_result["ok"] is not True:
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
                "execution_enable_checked": production_result["execution_enable_checked"],
                "execution_enable_enabled": production_result["execution_enable_enabled"],
                "real_ready_checked": production_result["real_ready_checked"],
                "real_ready": production_result["real_ready"],
                "queue_commit_checked": production_result["queue_commit_checked"],
                "order_queued": production_result["order_queued"],
                "production_errors": production_result["production_errors"],
                "production_results": production_result["production_results"],
                "reason": production_result["reason"],
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
        "execution_enable_checked": int(append_result.get("execution_enable_checked", 0) or 0),
        "execution_enable_enabled": int(append_result.get("execution_enable_enabled", 0) or 0),
        "real_ready_checked": int(append_result.get("real_ready_checked", 0) or 0),
        "real_ready": int(append_result.get("real_ready", 0) or 0),
        "queue_commit_checked": int(append_result.get("queue_commit_checked", 0) or 0),
        "order_queued": int(append_result.get("order_queued", 0) or 0),
        "production_errors": int(append_result.get("production_errors", 0) or 0),
        "production_results": append_result.get("production_results", []),
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
            "execution_enable_checked": 0,
            "execution_enable_enabled": 0,
            "real_ready_checked": 0,
            "real_ready": 0,
            "queue_commit_checked": 0,
            "order_queued": 0,
            "production_errors": 0,
            "order_queue_written": False,
            "execution_enabled_all_false": True,
            "approval_results": [],
            "policy_results": [],
            "production_results": [],
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
            "execution_enable_checked": int(order_queue_result.get("execution_enable_checked", 0) or 0),
            "execution_enable_enabled": int(order_queue_result.get("execution_enable_enabled", 0) or 0),
            "real_ready_checked": int(order_queue_result.get("real_ready_checked", 0) or 0),
            "real_ready": int(order_queue_result.get("real_ready", 0) or 0),
            "queue_commit_checked": int(order_queue_result.get("queue_commit_checked", 0) or 0),
            "order_queued": int(order_queue_result.get("order_queued", 0) or 0),
            "production_errors": int(order_queue_result.get("production_errors", 0) or 0),
        },
        "order_queue": order_queue_result,
        "status_updates": status_update_results,
        "results": results,
    }


if __name__ == "__main__":
    print(json.dumps(consume_pending_routine_signals_dry_run(), ensure_ascii=False, indent=2))
