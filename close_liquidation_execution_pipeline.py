# -*- coding: utf-8 -*-
"""Command-driven close/liquidation candidate boundary.

This module reuses the existing candidate approval and operation-policy
pipeline. It does not create ORDER_QUEUED records or call SendOrder.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

from order_approval_engine import evaluate_order_approval
from order_candidate_engine import get_real_holding_qty, read_latest_price
from order_queue import append_order_candidates
from operation_policy_gate import apply_operation_policy_gate_for_order
from runtime_io import read_json_dict
from close_liquidation_transition_service import (
    POLICY_CURRENT_PRICE,
    POLICY_MARKET,
    normalize_direct_close_policy_alias,
)


METHOD_MARKET = "MARKET"
METHOD_CURRENT_PRICE = "CURRENT_PRICE"


def normalize_direct_liquidation_method(value: object) -> str:
    normalized = normalize_direct_close_policy_alias(value)
    normalized_upper = normalized.upper().replace(" ", "_")
    if normalized == POLICY_MARKET or normalized_upper == "MARKET":
        return METHOD_MARKET
    if normalized == POLICY_CURRENT_PRICE or normalized_upper in {
        "CURRENT",
        "CURRENT_PRICE",
    }:
        return METHOD_CURRENT_PRICE
    return ""


def build_close_liquidation_candidate_preview(
    stock_dir: str | Path,
    code: str,
    name: str,
    method: object,
    *,
    command_id: str,
    requested_at: str,
    routine_instance_id: str,
    reason: str,
    latest_price_reader: Callable[[str, str], Any] = read_latest_price,
) -> dict[str, Any]:
    path = Path(stock_dir).resolve()
    clean_code = str(code or "").strip()
    clean_name = str(name or "").strip()
    clean_command_id = str(command_id or "").strip()
    clean_routine_id = str(routine_instance_id or "").strip()
    clean_requested_at = str(requested_at or "").strip()
    normalized_method = normalize_direct_liquidation_method(method)
    result: dict[str, Any] = {
        "ok": False,
        "stock_dir": str(path),
        "code": clean_code,
        "name": clean_name,
        "method": normalized_method,
        "command_id": clean_command_id,
        "routine_instance_id": clean_routine_id,
        "requested_at": clean_requested_at,
        "reason": str(reason or "").strip(),
        "blocked_reasons": [],
        "order_candidate": {},
    }
    blocked = result["blocked_reasons"]
    if not clean_code:
        blocked.append("stock code is required")
    if not clean_command_id:
        blocked.append("operation command id is required")
    if not clean_routine_id:
        blocked.append("routine instance id is required")
    if not clean_requested_at:
        blocked.append("command requested_at is required")
    if not normalized_method:
        blocked.append("direct liquidation method must be MARKET or CURRENT_PRICE")

    config = read_json_dict(path / "config.json")
    state = read_json_dict(path / "state.json")
    if not config:
        blocked.append("stock config is missing or invalid")
    if not state:
        blocked.append("stock state is missing or invalid")
    if blocked:
        return result

    assigned_routine_id = str(
        config.get("assigned_routine_instance_id") or ""
    ).strip()
    if assigned_routine_id != clean_routine_id:
        blocked.append("routine instance identity does not match stock config")

    holding_qty = get_real_holding_qty(state)
    if holding_qty is None or holding_qty <= 0:
        blocked.append("actual holding quantity is missing or zero")

    latest_price = latest_price_reader(clean_code, clean_name)
    try:
        price_value = (
            float(latest_price) if latest_price not in (None, "") else None
        )
    except (TypeError, ValueError):
        price_value = None
    if (
        normalized_method == METHOD_CURRENT_PRICE
        and (price_value is None or price_value <= 0)
    ):
        blocked.append("current-price liquidation requires a positive current price")
    if blocked:
        return result

    price: int | float = 0
    hoga = "MARKET"
    if normalized_method == METHOD_CURRENT_PRICE:
        price = (
            int(price_value)
            if float(price_value).is_integer()
            else float(price_value)
        )
        hoga = "CURRENT_PRICE"

    order_id = f"CLOSE_LIQUIDATION_{clean_command_id}"
    candidate = {
        "id": order_id,
        "created_at": clean_requested_at,
        "updated_at": clean_requested_at,
        "status": "PENDING",
        "source": "operation_command",
        "source_signal_id": clean_command_id,
        "routine": clean_routine_id,
        "code": clean_code,
        "name": clean_name,
        "side": "SELL",
        "order_type": "SELL",
        "hoga": hoga,
        "quantity": int(holding_qty),
        "quantity_estimated": int(holding_qty),
        "amount": None,
        "price": price,
        "candidate_status": "CANDIDATE_READY",
        "candidate_reason": str(reason or "청산 Command").strip(),
        "holding_source": "state",
        "price_basis": (
            "market" if normalized_method == METHOD_MARKET else "latest_price"
        ),
        "execution_enabled": False,
        "reason": str(reason or "LIQUIDATION_COMMAND").strip(),
        "order_intent": {
            "side": "SELL",
            "hoga": hoga,
            "method": normalized_method,
            "source": "OPERATION_COMMAND",
            "unresolved": False,
        },
    }
    result["holding_qty"] = int(holding_qty)
    result["price"] = price
    result["order_candidate"] = candidate
    result["ok"] = True
    return result


def commit_close_liquidation_candidate_preview(
    preview: dict[str, Any],
    *,
    candidate_appender: Callable[..., dict[str, Any]] = append_order_candidates,
    approval_evaluator: Callable[[dict[str, Any]], dict[str, Any]] = (
        evaluate_order_approval
    ),
    policy_applier: Callable[..., dict[str, Any]] = (
        apply_operation_policy_gate_for_order
    ),
) -> dict[str, Any]:
    if not isinstance(preview, dict) or preview.get("ok") is not True:
        return {
            "ok": False,
            "stage": "preview",
            "blocked_reasons": (
                list(preview.get("blocked_reasons") or ["preview is not ready"])
                if isinstance(preview, dict)
                else ["preview is required"]
            ),
        }

    candidate = deepcopy(preview["order_candidate"])
    order_id = str(candidate.get("id") or "").strip()
    approval = approval_evaluator(candidate)
    if str(approval.get("approval_status") or "").upper() != "APPROVED":
        return {
            "ok": False,
            "stage": "approval",
            "order_id": order_id,
            "approval_result": approval,
            "blocked_reasons": [
                str(
                    approval.get("approval_reason")
                    or "liquidation candidate approval blocked"
                )
            ],
        }

    candidate["approval_status"] = "APPROVED"
    candidate["approval_reason"] = str(approval.get("approval_reason") or "")
    candidate["approval_checked_at"] = str(preview.get("requested_at") or "")
    candidate["status"] = "APPROVED"
    candidate["execution_enabled"] = False
    append_result = candidate_appender(
        [candidate],
        context={
            "manual_queue_write_confirmed": True,
            "source": "OPERATION_COMMAND",
            "command_id": str(preview.get("command_id") or ""),
        },
    )
    if (
        append_result.get("ok") is not True
        or int(append_result.get("orders_created", 0) or 0) != 1
    ):
        return {
            "ok": False,
            "stage": "candidate_commit",
            "order_id": order_id,
            "approval_result": approval,
            "append_result": append_result,
            "blocked_reasons": [
                str(
                    append_result.get("reason")
                    or "liquidation candidate was not committed"
                )
            ],
        }

    policy_result = policy_applier(order_id)
    if (
        policy_result.get("ok") is not True
        or str(policy_result.get("after_status") or "").upper() != "EXECUTABLE"
    ):
        return {
            "ok": False,
            "stage": "operation_policy",
            "order_id": order_id,
            "approval_result": approval,
            "append_result": append_result,
            "policy_result": policy_result,
            "blocked_reasons": [
                str(
                    policy_result.get("reason")
                    or "liquidation operation policy blocked"
                )
            ],
        }

    return {
        "ok": True,
        "stage": "executable",
        "order_id": order_id,
        "approval_result": approval,
        "append_result": append_result,
        "policy_result": policy_result,
    }
