# -*- coding: utf-8 -*-
"""Manual-operation ATS liquidation request and order-candidate boundary."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from gui_ats_utils import (
    current_time_in_seconds,
    manual_ats_session_definition,
    operation_policy_time_range_seconds,
    seconds_in_range,
)
from gui_auto_trade_policy import auto_trade_setting_liquidation_completed_today
from operation_command_service import (
    COMMAND_MANUAL_ATS_LIQUIDATION,
    ManualAtsLiquidationOverride,
    OperationCommandRequest,
    OperationCommandService,
    MANUAL_ATS_LIQUIDATION_REQUEST_KEY,
    RESULT_SUCCESS,
    SCOPE_STOCK,
    STOCK_APPLIED,
    STOCK_IGNORED_DUPLICATE,
)
from order_approval_engine import evaluate_order_approval
from order_candidate_engine import get_real_holding_qty
from order_queue import append_order_candidates
from operation_policy_gate import apply_operation_policy_gate_for_order
from runtime_io import read_json_dict
from state_policy import normalize_operation_mode
from manual_ats_runtime import manual_ats_runtime_selected_keys


VALID_SESSION_KEYS = ("extra1", "extra2", "extra3")
METHOD_MARKET = "MARKET"
METHOD_CURRENT_PRICE = "CURRENT_PRICE"


def normalize_manual_ats_sell_method(value: object) -> str:
    normalized = str(value or "").strip().upper().replace(" ", "_")
    if normalized in {"시장가", "MARKET"}:
        return METHOD_MARKET
    if normalized in {"현재가", "CURRENT", "CURRENT_PRICE", "LIMIT"}:
        return METHOD_CURRENT_PRICE
    return ""


def _normalized_sessions(values: object) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple, set)):
        return ()
    selected = {str(value or "").strip() for value in values}
    return tuple(key for key in VALID_SESSION_KEYS if key in selected)


def _active_selected_sessions(
    selected_sessions: tuple[str, ...],
    now_dt: datetime,
) -> tuple[str, ...]:
    current_seconds = current_time_in_seconds(now_dt)
    active: list[str] = []
    for key in selected_sessions:
        definition = manual_ats_session_definition(key)
        seconds = operation_policy_time_range_seconds(
            definition,
            default_start="00:00:00",
            default_end="00:00:00",
        )
        if seconds is None:
            continue
        if seconds_in_range(current_seconds, seconds[0], seconds[1]):
            active.append(key)
    return tuple(active)


def build_manual_ats_liquidation_preview(
    stock_dir: str | Path,
    code: str,
    name: str,
    selected_sessions: object,
    sell_method: object,
    *,
    now_dt: datetime | None = None,
    command_id: str = "",
    current_price_reader: Callable[[str, str], Any] | None = None,
    holding_qty_override: int | None = None,
) -> dict[str, Any]:
    path = Path(stock_dir).resolve()
    clean_code = str(code or "").strip()
    clean_name = str(name or "").strip()
    method = normalize_manual_ats_sell_method(sell_method)
    sessions = _normalized_sessions(selected_sessions)
    current = now_dt or datetime.now().astimezone()
    result: dict[str, Any] = {
        "ok": False,
        "command": COMMAND_MANUAL_ATS_LIQUIDATION,
        "command_id": str(command_id or "").strip() or uuid4().hex,
        "source": "ATS_SETTINGS",
        "stock_dir": str(path),
        "code": clean_code,
        "name": clean_name,
        "selected_ats_sessions": list(sessions),
        "active_ats_sessions": [],
        "sell_method": method,
        "requested_at": current.isoformat(timespec="seconds"),
        "blocked_reasons": [],
        "order_candidate": {},
    }
    reasons = result["blocked_reasons"]
    if not clean_code:
        reasons.append("stock code is required")
    if method not in {METHOD_MARKET, METHOD_CURRENT_PRICE}:
        reasons.append("sell method must be MARKET or CURRENT_PRICE")
    if not sessions:
        reasons.append("at least one ATS session must be selected")

    config = read_json_dict(path / "config.json")
    state = read_json_dict(path / "state.json")
    if not isinstance(config, dict) or not config:
        reasons.append("stock config is missing or invalid")
    if not isinstance(state, dict) or not state:
        reasons.append("stock state is missing or invalid")
    if reasons:
        return result

    if normalize_operation_mode(config.get("operation_mode", "")) != "CONTINUOUS":
        reasons.append("manual ATS liquidation requires CONTINUOUS operation mode")
    runtime_sessions = manual_ats_runtime_selected_keys(state, now_dt=current)
    if sessions != runtime_sessions:
        reasons.append("selected ATS sessions do not match current runtime state")
    runtime_selection = state.get("manual_ats_selection")
    runtime_selection = runtime_selection if isinstance(runtime_selection, dict) else {}
    result["trade_date"] = str(runtime_selection.get("trade_date", "") or "")
    result["program_session_id"] = str(
        runtime_selection.get("program_session_id", "") or ""
    )
    if auto_trade_setting_liquidation_completed_today(state):
        reasons.append("liquidation was already completed today")

    active_sessions = _active_selected_sessions(sessions, current)
    result["active_ats_sessions"] = list(active_sessions)
    if not active_sessions:
        reasons.append("current time is outside the selected ATS sessions")

    holding_qty = (
        holding_qty_override
        if holding_qty_override is not None
        else get_real_holding_qty(state)
    )
    if holding_qty is None or holding_qty <= 0:
        reasons.append("actual holding quantity is missing or zero")

    price_value = None
    if method == METHOD_CURRENT_PRICE:
        try:
            current_price = (
                current_price_reader(clean_code, clean_name)
                if callable(current_price_reader)
                else None
            )
            price_value = (
                float(current_price) if current_price not in (None, "") else None
            )
        except Exception:
            price_value = None
        if price_value is None or price_value <= 0:
            reasons.append(
                "current-price liquidation requires an actionable current price"
            )
    if reasons:
        return result

    order_price: int | float = 0
    hoga = "MARKET"
    if method == METHOD_CURRENT_PRICE:
        order_price = int(price_value) if float(price_value).is_integer() else float(price_value)
        hoga = "CURRENT_PRICE"

    command_id_text = str(result["command_id"])
    order_id = f"ATS_LIQUIDATION_{command_id_text}"
    candidate = {
        "id": order_id,
        "created_at": result["requested_at"],
        "updated_at": result["requested_at"],
        "status": "PENDING",
        "source": "manual_ats_liquidation",
        "source_signal_id": command_id_text,
        "routine": str(config.get("assigned_routine_instance_id", "") or "MANUAL_ATS"),
        "code": clean_code,
        "name": clean_name,
        "side": "SELL",
        "order_type": "SELL",
        "hoga": hoga,
        "quantity": int(holding_qty),
        "quantity_estimated": int(holding_qty),
        "amount": None,
        "price": order_price,
        "candidate_status": "CANDIDATE_READY",
        "candidate_reason": "수동운영 ATS 청산 요청",
        "holding_source": (
            "positions_broker_reconciliation"
            if holding_qty_override is not None
            else "state"
        ),
        "price_basis": method,
        "execution_enabled": False,
        "reason": "MANUAL_ATS_LIQUIDATION",
        "order_intent": {
            "side": "SELL",
            "hoga": hoga,
            "method": method,
            "source": "ATS_SETTINGS",
            "unresolved": False,
        },
        "manual_ats_liquidation": {
            "command_id": command_id_text,
            "source": "ATS_SETTINGS",
            "selected_ats_sessions": list(sessions),
            "active_ats_sessions": list(active_sessions),
            "sell_method": method,
            "requested_at": result["requested_at"],
            "trade_date": result["trade_date"],
            "program_session_id": result["program_session_id"],
        },
    }
    result["order_candidate"] = candidate
    result["holding_qty"] = int(holding_qty)
    result["price"] = order_price
    result["ok"] = True
    return result


def commit_manual_ats_liquidation_preview(
    preview: dict[str, Any],
    *,
    project_root: str | Path,
    command_service_factory: Callable[..., OperationCommandService] = OperationCommandService,
    candidate_appender: Callable[..., dict[str, Any]] = append_order_candidates,
    approval_evaluator: Callable[[dict[str, Any]], dict[str, Any]] = evaluate_order_approval,
    policy_applier: Callable[..., dict[str, Any]] = apply_operation_policy_gate_for_order,
) -> dict[str, Any]:
    if not isinstance(preview, dict) or preview.get("ok") is not True:
        return {
            "ok": False,
            "stage": "preview",
            "blocked_reasons": list(preview.get("blocked_reasons") or ["ATS liquidation preview is not ready"])
            if isinstance(preview, dict)
            else ["ATS liquidation preview is required"],
        }

    request_result = ensure_manual_ats_liquidation_request(
        preview,
        project_root=project_root,
        command_service_factory=command_service_factory,
    )
    if request_result.get("ok") is not True:
        return request_result

    command_id = str(preview.get("command_id") or "").strip()
    stock_dir = str(preview.get("stock_dir") or "").strip()
    command_service = request_result["command_service"]
    command_result = request_result["command_result"]

    candidate = deepcopy(preview["order_candidate"])
    order_id = str(candidate.get("id") or "")
    approval = approval_evaluator(candidate)
    if str(approval.get("approval_status") or "").upper() != "APPROVED":
        command_service.record_manual_ats_liquidation_status(
            stock_dir,
            command_id,
            "ORDER_BLOCKED",
            order_id=order_id,
            detail=str(approval.get("approval_reason") or ""),
        )
        return {
            "ok": False,
            "stage": "approval",
            "command_result": command_result,
            "approval_result": approval,
            "blocked_reasons": [str(approval.get("approval_reason") or "ATS liquidation candidate approval blocked")],
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
            "source": "ATS_SETTINGS",
            "command_id": command_id,
        },
    )
    if append_result.get("ok") is not True or int(append_result.get("orders_created", 0) or 0) != 1:
        command_service.record_manual_ats_liquidation_status(
            stock_dir,
            command_id,
            "ORDER_BLOCKED",
            order_id=order_id,
            detail=str(append_result.get("reason") or ""),
        )
        return {
            "ok": False,
            "stage": "candidate_commit",
            "command_result": command_result,
            "approval_result": approval,
            "append_result": append_result,
            "blocked_reasons": [
                str(append_result.get("reason") or "ATS liquidation order candidate was not committed")
            ],
        }

    policy_result = policy_applier(order_id)
    if (
        policy_result.get("ok") is not True
        or str(policy_result.get("after_status") or "").upper() != "EXECUTABLE"
    ):
        command_service.record_manual_ats_liquidation_status(
            stock_dir,
            command_id,
            "ORDER_BLOCKED",
            order_id=order_id,
            detail=str(policy_result.get("reason") or ""),
        )
        return {
            "ok": False,
            "stage": "operation_policy",
            "order_id": order_id,
            "command_result": command_result,
            "approval_result": approval,
            "append_result": append_result,
            "policy_result": policy_result,
            "blocked_reasons": [
                str(policy_result.get("reason") or "ATS liquidation operation policy blocked")
            ],
        }

    status_result = command_service.record_manual_ats_liquidation_status(
        stock_dir,
        command_id,
        "ORDER_EXECUTABLE",
        order_id=order_id,
    )
    if status_result.status != STOCK_APPLIED:
        return {
            "ok": False,
            "stage": "runtime_status_readback",
            "order_id": order_id,
            "command_result": command_result,
            "approval_result": approval,
            "append_result": append_result,
            "policy_result": policy_result,
            "status_result": status_result,
            "blocked_reasons": [
                status_result.error
                or "manual ATS liquidation Runtime status read-back failed"
            ],
        }
    return {
        "ok": True,
        "stage": "executable",
        "command_id": command_id,
        "order_id": order_id,
        "command_result": command_result,
        "approval_result": approval,
        "append_result": append_result,
        "policy_result": policy_result,
        "status_result": status_result,
        "blocked_reasons": [],
    }


def ensure_manual_ats_liquidation_request(
    preview: dict[str, Any],
    *,
    project_root: str | Path,
    command_service_factory: Callable[..., OperationCommandService] = OperationCommandService,
) -> dict[str, Any]:
    """Persist one durable request or reuse its pre-order resume state."""
    if not isinstance(preview, dict) or preview.get("ok") is not True:
        return {
            "ok": False,
            "stage": "preview",
            "blocked_reasons": ["ATS liquidation preview is not ready"],
        }
    command_id = str(preview.get("command_id") or "").strip()
    stock_dir = str(preview.get("stock_dir") or "").strip()
    current_state = read_json_dict(Path(stock_dir) / "state.json")
    current_request = current_state.get(MANUAL_ATS_LIQUIDATION_REQUEST_KEY)
    current_request = current_request if isinstance(current_request, dict) else {}
    current_status = str(current_request.get("status") or "").strip().upper()
    if current_status == "WAITING_CANCEL_CONFIRMATION":
        return {
            "ok": False,
            "stage": "runtime_request",
            "blocked_reasons": [
                "manual ATS liquidation is already waiting for cancel confirmation"
            ],
        }
    command_service = command_service_factory(project_root)
    command_result = command_service.apply_manual_ats_liquidation(
        OperationCommandRequest(
            target_scope=SCOPE_STOCK,
            target_id=stock_dir,
            command=COMMAND_MANUAL_ATS_LIQUIDATION,
            source="MANUAL_ATS_LIQUIDATION",
            occurred_at=str(preview.get("requested_at") or ""),
            command_id=command_id,
        ),
        ManualAtsLiquidationOverride(
            sell_method=str(preview.get("sell_method") or ""),
            selected_ats_sessions=tuple(preview.get("selected_ats_sessions") or ()),
            trade_date=str(preview.get("trade_date") or ""),
            program_session_id=str(preview.get("program_session_id") or ""),
        ),
    )
    applied = (
        command_result.status == RESULT_SUCCESS
        and bool(command_result.stock_results)
        and command_result.stock_results[0].status == STOCK_APPLIED
    )
    if applied:
        return {
            "ok": True,
            "stage": "runtime_request",
            "command_service": command_service,
            "command_result": command_result,
            "request_status": "REQUESTED",
            "blocked_reasons": [],
        }

    duplicate = (
        bool(command_result.stock_results)
        and command_result.stock_results[0].status == STOCK_IGNORED_DUPLICATE
    )
    if duplicate:
        state = read_json_dict(Path(stock_dir) / "state.json")
        request = state.get(MANUAL_ATS_LIQUIDATION_REQUEST_KEY)
        request = request if isinstance(request, dict) else {}
        request_status = str(request.get("status") or "").strip().upper()
        if (
            str(request.get("command_id") or "").strip() == command_id
            and request_status in {"REQUESTED", "READY_TO_RESUME"}
        ):
            return {
                "ok": True,
                "stage": "runtime_request_reused",
                "command_service": command_service,
                "command_result": command_result,
                "request_status": request_status,
                "blocked_reasons": [],
            }

    reason = command_result.error
    if command_result.stock_results:
        reason = command_result.stock_results[0].error or reason
    return {
        "ok": False,
        "stage": "runtime_request",
        "command_result": command_result,
        "blocked_reasons": [reason or "manual ATS liquidation Runtime request failed"],
    }
