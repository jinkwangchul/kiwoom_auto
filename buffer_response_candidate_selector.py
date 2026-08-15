# -*- coding: utf-8 -*-
"""Read-only selector for one new buffer-response close candidate.

The selector consumes the stage-2 policy projection.  It does not read GUI
widgets, recalculate thresholds/PnL, claim ownership, persist state, cancel an
order, or invoke any close/liquidation execution path.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Iterable, Mapping

from gui_auto_trade_integrity import (
    is_emergency_stopped_state,
    is_operation_excluded,
    is_review_required_state,
)
from gui_auto_trade_policy import auto_trade_setting_early_close_requested
from gui_order_utils import order_current_pending_qty
from close_liquidation_transition_service import (
    POLICY_ROUTINE_CLOSE,
    normalize_direct_close_policy_alias,
)
from operation_close_completion_evaluator import (
    ACTIVE_QUEUE_STATUSES,
    CLOSED_QUEUE_STATUSES,
    CLOSE_STARTED_STATUSES,
)


_SUPPORTED_FACTORS = frozenset({"손익금액", "손익비율", "투입금액"})
_SUPPORTED_DIRECTIONS = frozenset({"높은순", "낮은순"})
_ACTIVE_LIQUIDATION_REQUEST_KEYS = (
    "individual_liquidation_request",
    "manual_ats_liquidation_request",
)
_TERMINAL_LIQUIDATION_REQUEST_STATUSES = frozenset(
    {"COMPLETED", "FAILED", "ORDER_BLOCKED", "CANCELED", "CANCELLED"}
)
_CLOSE_COMMAND_MODES = frozenset(
    {
        "EARLY_CLOSE",
        "AUTO_CLOSE",
        "CARRY_OVER",
        "INDIVIDUAL_LIQUIDATION",
        "MANUAL_ATS_LIQUIDATION",
    }
)


def _text(value: object) -> str:
    return str(value or "").strip()


def _stock_code(value: object) -> str:
    return _text(value).lstrip("A")


def _decimal(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def _positive_integer(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        text = str(value).replace(",", "").strip()
        number = int(text)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _result_unselectable(
    reason: str,
    *,
    policy_projection: Mapping[str, object] | None = None,
    candidate_count: int = 0,
    excluded: Mapping[str, str] | None = None,
) -> dict[str, object]:
    policy = policy_projection if isinstance(policy_projection, Mapping) else {}
    exclusions = dict(excluded or {})
    return {
        "selectable": False,
        "selected_stock": None,
        "selected_stock_code": "",
        "evaluation_factor": _text(policy.get("evaluation_factor")),
        "direction": _text(policy.get("direction")),
        "selected_evaluation_value": None,
        "effective_response": _text(policy.get("effective_response")),
        "candidate_count": candidate_count,
        "excluded_count": len(exclusions),
        "excluded": exclusions,
        "reason": _text(reason) or "BUFFER_RESPONSE_CANDIDATE_UNAVAILABLE",
    }


def _active_liquidation_request(state: Mapping[str, object]) -> bool:
    for key in _ACTIVE_LIQUIDATION_REQUEST_KEYS:
        request = state.get(key)
        if not isinstance(request, Mapping) or not request:
            continue
        status = _text(request.get("status")).upper() or "REQUESTED"
        if status not in _TERMINAL_LIQUIDATION_REQUEST_STATUSES:
            return True
    return False


def _auto_close_evidence(state: Mapping[str, object]) -> bool:
    return bool(
        _text(state.get("auto_close_requested_at"))
        or _text(state.get("auto_close_source"))
        or _text(state.get("auto_close_method"))
        or (
            isinstance(state.get("auto_close_policy"), Mapping)
            and bool(state.get("auto_close_policy"))
        )
    )


def _active_sell_order_reason(
    stock_code: str,
    orders: object,
) -> str:
    if not isinstance(orders, (list, tuple)):
        return "ORDER_EVIDENCE_UNAVAILABLE"
    for order in orders:
        if not isinstance(order, Mapping):
            return "ORDER_EVIDENCE_INVALID"
        order_code = _stock_code(order.get("code") or order.get("stock_code"))
        if order_code and order_code != stock_code:
            continue
        status = _text(order.get("status") or order.get("order_status")).upper()
        try:
            pending_qty, pending_unknown = order_current_pending_qty(dict(order))
        except Exception:
            return "ORDER_EVIDENCE_INVALID"
        remaining = order.get("remaining_quantity")
        if remaining not in (None, ""):
            try:
                pending_qty = max(pending_qty, int(str(remaining).replace(",", "")))
            except (TypeError, ValueError):
                pending_unknown = True
        active = (
            status in ACTIVE_QUEUE_STATUSES
            or pending_unknown
            or pending_qty > 0
            or (
                status not in CLOSED_QUEUE_STATUSES
                and bool(
                    order.get("actual_order_sent") is True
                    or order.get("send_order_called") is True
                    or _text(order.get("broker_order_no"))
                )
            )
        )
        if not active:
            continue
        side = _text(order.get("side") or order.get("order_side")).upper()
        if side in {"SELL", "S", "매도"}:
            return "ACTIVE_SELL_ORDER"
        if side not in {"BUY", "B", "매수"}:
            return "ACTIVE_ORDER_SIDE_UNCERTAIN"
    return ""


def existing_close_exclusion_reason(candidate: Mapping[str, object]) -> str:
    """Classify existing Production close/sell evidence without mutation."""
    code = _stock_code(candidate.get("stock_code"))
    if not code:
        return "STOCK_IDENTITY_UNAVAILABLE"
    if candidate.get("is_auto_trade_target") is not True:
        return "NOT_CURRENT_AUTO_TRADE_TARGET"

    position = candidate.get("position")
    if not isinstance(position, Mapping):
        return "POSITION_EVIDENCE_UNAVAILABLE"
    position_code = _stock_code(position.get("code") or position.get("stock_code"))
    if position_code != code:
        return "POSITION_IDENTITY_MISMATCH"
    if _text(position.get("position_status")).upper() != "OPEN":
        return "POSITION_NOT_OPEN"
    if _positive_integer(position.get("quantity")) is None:
        return "NO_OPEN_HOLDING"

    state = candidate.get("state")
    config = candidate.get("config")
    if not isinstance(state, Mapping) or not isinstance(config, Mapping):
        return "STOCK_EVIDENCE_UNAVAILABLE"
    return existing_close_or_sell_exclusion_reason(
        stock_code=code,
        state=state,
        config=config,
        orders=candidate.get("orders"),
    )


def existing_close_or_sell_exclusion_reason(
    *,
    stock_code: object,
    state: Mapping[str, object] | object,
    config: Mapping[str, object] | object,
    orders: object,
) -> str:
    """Classify close/sell conflicts without requiring position projection."""

    code = _stock_code(stock_code)
    if not code:
        return "STOCK_IDENTITY_UNAVAILABLE"
    if not isinstance(state, Mapping) or not isinstance(config, Mapping):
        return "STOCK_EVIDENCE_UNAVAILABLE"
    state_dict = dict(state)
    config_dict = dict(config)
    if is_operation_excluded(config_dict):
        return "OPERATION_EXCLUDED"
    status = _text(state.get("status")).upper()
    if status in CLOSE_STARTED_STATUSES:
        return "CLOSE_OR_LIQUIDATION_IN_PROGRESS"
    if is_review_required_state(state_dict):
        return "REVIEW_REQUIRED"
    if is_emergency_stopped_state(state_dict):
        return "EMERGENCY_STOPPED"
    if auto_trade_setting_early_close_requested(state_dict):
        return "EARLY_CLOSE_ALREADY_REQUESTED"

    if _auto_close_evidence(state):
        return "AUTO_CLOSE_ALREADY_REQUESTED"
    if _text(state.get("operation_command_mode")).upper() in _CLOSE_COMMAND_MODES:
        return "CLOSE_COMMAND_ALREADY_APPLIED"
    if bool(state.get("liquidation_policy_forced")):
        return "LIQUIDATION_POLICY_ACTIVE"
    if _active_liquidation_request(state):
        return "LIQUIDATION_REQUEST_ACTIVE"
    if bool(state.get("close_routine_final_sell_ordered")) or _text(
        state.get("close_routine_final_sell_ordered_at")
    ):
        return "CLOSE_ROUTINE_FINAL_SELL_ORDERED"

    operation_notice = _text(state.get("operation_notice")).upper()
    if operation_notice.startswith(("AUTO_CLOSE", "EARLY_CLOSE", "LIQUIDATION")):
        return "CLOSE_OPERATION_NOTICE_ACTIVE"
    return _active_sell_order_reason(code, orders)


def buffer_owned_early_close_escalation_exclusion_reason(
    *,
    stock_code: object,
    state: Mapping[str, object] | object,
    config: Mapping[str, object] | object,
    orders: object,
    expected_source: object,
    expected_command_id: object,
) -> str:
    """Allow only the exact buffer-owned routine early-close being promoted."""

    code = _stock_code(stock_code)
    source = _text(expected_source)
    command_id = _text(expected_command_id)
    if not code or not source or not command_id:
        return "BUFFER_OWNERSHIP_COMMAND_IDENTITY_UNAVAILABLE"
    if not isinstance(state, Mapping) or not isinstance(config, Mapping):
        return "STOCK_EVIDENCE_UNAVAILABLE"
    state_dict = dict(state)
    config_dict = dict(config)
    if is_operation_excluded(config_dict):
        return "OPERATION_EXCLUDED"
    if is_review_required_state(state_dict):
        return "REVIEW_REQUIRED"
    if is_emergency_stopped_state(state_dict):
        return "EMERGENCY_STOPPED"
    if _auto_close_evidence(state):
        return "AUTO_CLOSE_ALREADY_REQUESTED"
    if _active_liquidation_request(state):
        return "LIQUIDATION_REQUEST_ACTIVE"
    if bool(state.get("close_routine_final_sell_ordered")) or _text(
        state.get("close_routine_final_sell_ordered_at")
    ):
        return "CLOSE_ROUTINE_FINAL_SELL_ORDERED"
    if _text(state.get("operation_command_mode")).upper() != "EARLY_CLOSE":
        return "BUFFER_EARLY_CLOSE_MODE_MISMATCH"
    if _text(state.get("operation_command_id")) != command_id:
        return "BUFFER_EARLY_CLOSE_COMMAND_ID_MISMATCH"
    if _text(state.get("operation_command_source")) != source:
        return "BUFFER_EARLY_CLOSE_SOURCE_MISMATCH"
    if _text(state.get("early_close_source")) != source:
        return "BUFFER_EARLY_CLOSE_SOURCE_MISMATCH"
    if (
        normalize_direct_close_policy_alias(state.get("early_close_method"))
        != POLICY_ROUTINE_CLOSE
    ):
        return "BUFFER_EARLY_CLOSE_METHOD_MISMATCH"
    status = _text(state.get("status")).upper()
    if status not in {"EARLY_CLOSE", "EARLY_CLOSING", "EARLY_CLOSED"}:
        return "BUFFER_EARLY_CLOSE_STATUS_MISMATCH"
    return _active_sell_order_reason(code, orders)


def select_buffer_owned_early_close_escalation_candidate(
    *,
    policy_projection: Mapping[str, object] | object,
    candidates: Iterable[Mapping[str, object]] | object,
    owned_command_evidence: Mapping[str, Mapping[str, object]] | object,
) -> dict[str, object]:
    """Select one existing buffer-owned EARLY_CLOSE event, never an ordinary holding."""

    if not isinstance(policy_projection, Mapping):
        return _result_unselectable("POLICY_PROJECTION_UNAVAILABLE")
    if (
        policy_projection.get("available") is not True
        or policy_projection.get("applicable") is not True
        or _text(policy_projection.get("effective_response"))
        != "IMMEDIATE_LIQUIDATION_REQUIRED"
    ):
        return _result_unselectable(
            "POLICY_PROJECTION_NOT_IMMEDIATE",
            policy_projection=policy_projection,
        )
    if not isinstance(owned_command_evidence, Mapping) or not owned_command_evidence:
        return _result_unselectable(
            "NO_PENDING_BUFFER_OWNED_EARLY_CLOSE",
            policy_projection=policy_projection,
        )

    factor = _text(policy_projection.get("evaluation_factor"))
    direction = _text(policy_projection.get("direction"))
    factor_values = policy_projection.get("candidate_factor_values")
    if (
        factor not in _SUPPORTED_FACTORS
        or direction not in _SUPPORTED_DIRECTIONS
        or not isinstance(factor_values, Mapping)
    ):
        return _result_unselectable(
            "POLICY_FILTER_UNSUPPORTED",
            policy_projection=policy_projection,
        )
    normalized_factor_values = {
        _stock_code(code): _decimal(value)
        for code, value in factor_values.items()
        if _stock_code(code)
    }
    try:
        candidate_items = list(candidates)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return _result_unselectable(
            "CANDIDATE_INPUT_UNAVAILABLE",
            policy_projection=policy_projection,
        )
    candidates_by_code: dict[str, Mapping[str, object]] = {}
    for candidate in candidate_items:
        if not isinstance(candidate, Mapping):
            continue
        code = _stock_code(candidate.get("stock_code"))
        if code in candidates_by_code:
            return _result_unselectable(
                "DUPLICATE_STOCK_IDENTITY",
                policy_projection=policy_projection,
            )
        if code:
            candidates_by_code[code] = candidate

    eligible: list[tuple[str, Decimal, str, Mapping[str, object]]] = []
    excluded: dict[str, str] = {}
    for raw_event_id, raw_evidence in sorted(owned_command_evidence.items()):
        event_id = _text(raw_event_id)
        if not event_id or not isinstance(raw_evidence, Mapping):
            excluded[event_id or "#event"] = "BUFFER_OWNERSHIP_EVIDENCE_INVALID"
            continue
        code = _stock_code(raw_evidence.get("stock_code"))
        candidate = candidates_by_code.get(code)
        if candidate is None:
            excluded[code or event_id] = "BUFFER_OWNED_CANDIDATE_SNAPSHOT_MISSING"
            continue
        position = candidate.get("position")
        if not isinstance(position, Mapping):
            excluded[code] = "POSITION_EVIDENCE_UNAVAILABLE"
            continue
        if _text(position.get("position_status")).upper() != "OPEN":
            excluded[code] = "POSITION_NOT_OPEN"
            continue
        if _positive_integer(position.get("quantity")) is None:
            excluded[code] = "NO_OPEN_HOLDING"
            continue
        reason = buffer_owned_early_close_escalation_exclusion_reason(
            stock_code=code,
            state=candidate.get("state"),
            config=candidate.get("config"),
            orders=candidate.get("orders"),
            expected_source=raw_evidence.get("source"),
            expected_command_id=raw_evidence.get("command_id"),
        )
        if reason:
            excluded[code] = reason
            continue
        value = normalized_factor_values.get(code)
        if value is None:
            excluded[code] = "EVALUATION_VALUE_UNAVAILABLE"
            continue
        eligible.append((code, value, event_id, candidate))

    if not eligible:
        return _result_unselectable(
            "NO_ELIGIBLE_BUFFER_OWNED_EARLY_CLOSE",
            policy_projection=policy_projection,
            excluded=excluded,
        )
    if direction == "높은순":
        eligible.sort(key=lambda item: (-item[1], item[0], item[2]))
    else:
        eligible.sort(key=lambda item: (item[1], item[0], item[2]))
    code, selected_value, event_id, selected = eligible[0]
    return {
        "selectable": True,
        "selected_event_id": event_id,
        "selected_stock": {
            "stock_code": code,
            "stock_dir": _text(selected.get("stock_dir")),
            "routine_instance_id": _text(selected.get("routine_instance_id")),
        },
        "selected_stock_code": code,
        "evaluation_factor": factor,
        "direction": direction,
        "selected_evaluation_value": selected_value,
        "effective_response": _text(policy_projection.get("effective_response")),
        "candidate_count": len(eligible),
        "excluded_count": len(excluded),
        "excluded": excluded,
        "reason": "",
    }


def select_buffer_response_candidate(
    *,
    policy_projection: Mapping[str, object] | object,
    candidates: Iterable[Mapping[str, object]] | object,
    already_buffer_selected: Iterable[object] = (),
) -> dict[str, object]:
    """Select zero or one stock using one stage-2 factor and direction."""
    if not isinstance(policy_projection, Mapping):
        return _result_unselectable("POLICY_PROJECTION_UNAVAILABLE")
    if (
        policy_projection.get("available") is not True
        or policy_projection.get("applicable") is not True
        or not _text(policy_projection.get("effective_response"))
    ):
        return _result_unselectable(
            "POLICY_PROJECTION_NOT_APPLICABLE",
            policy_projection=policy_projection,
        )

    factor = _text(policy_projection.get("evaluation_factor"))
    direction = _text(policy_projection.get("direction"))
    if factor not in _SUPPORTED_FACTORS or direction not in _SUPPORTED_DIRECTIONS:
        return _result_unselectable(
            "POLICY_FILTER_UNSUPPORTED",
            policy_projection=policy_projection,
        )
    factor_values = policy_projection.get("candidate_factor_values")
    if not isinstance(factor_values, Mapping):
        return _result_unselectable(
            "POLICY_FACTOR_SNAPSHOT_UNAVAILABLE",
            policy_projection=policy_projection,
        )
    normalized_factor_values = {
        _stock_code(code): _decimal(value)
        for code, value in factor_values.items()
        if _stock_code(code)
    }

    try:
        candidate_items = list(candidates)  # type: ignore[arg-type]
        already_selected = {
            code
            for code in (_stock_code(value) for value in already_buffer_selected)
            if code
        }
    except (TypeError, ValueError):
        return _result_unselectable(
            "CANDIDATE_INPUT_UNAVAILABLE",
            policy_projection=policy_projection,
        )

    seen: set[str] = set()
    eligible: list[tuple[str, Decimal, Mapping[str, object]]] = []
    excluded: dict[str, str] = {}
    for index, candidate in enumerate(candidate_items):
        if not isinstance(candidate, Mapping):
            excluded[f"#{index}"] = "CANDIDATE_EVIDENCE_INVALID"
            continue
        code = _stock_code(candidate.get("stock_code"))
        identity = code or f"#{index}"
        if code in seen:
            return _result_unselectable(
                "DUPLICATE_STOCK_IDENTITY",
                policy_projection=policy_projection,
                excluded=excluded,
            )
        if code:
            seen.add(code)
        if code in already_selected:
            excluded[identity] = "ALREADY_BUFFER_SELECTED"
            continue
        reason = existing_close_exclusion_reason(candidate)
        if reason:
            excluded[identity] = reason
            continue
        value = normalized_factor_values.get(code)
        if value is None:
            excluded[identity] = "EVALUATION_VALUE_UNAVAILABLE"
            continue
        eligible.append((code, value, candidate))

    if not eligible:
        return _result_unselectable(
            "NO_ELIGIBLE_CANDIDATE",
            policy_projection=policy_projection,
            candidate_count=0,
            excluded=excluded,
        )

    if direction == "높은순":
        eligible.sort(key=lambda item: (-item[1], item[0]))
    else:
        eligible.sort(key=lambda item: (item[1], item[0]))
    code, selected_value, selected = eligible[0]
    selected_identity = {
        "stock_code": code,
        "stock_dir": _text(selected.get("stock_dir")),
        "routine_instance_id": _text(selected.get("routine_instance_id")),
    }
    return {
        "selectable": True,
        "selected_stock": selected_identity,
        "selected_stock_code": code,
        "evaluation_factor": factor,
        "direction": direction,
        "selected_evaluation_value": selected_value,
        "effective_response": _text(policy_projection.get("effective_response")),
        "candidate_count": len(eligible),
        "excluded_count": len(excluded),
        "excluded": excluded,
        "reason": "",
    }
