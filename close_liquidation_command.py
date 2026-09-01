# -*- coding: utf-8 -*-
"""Application commands for Close/Liquidation operator intents.

The UI supplies an intent and renders the result.  This module owns the
read-only semantic availability and repeats it immediately before durable
mutation.  ``OperationCommandService`` remains the persistence boundary and
the existing GUI execution pipeline remains the order-execution boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from close_intent_service import CLOSE_INTENT_EARLY_CLOSE, apply_close_intent
from close_liquidation_transition_service import (
    DOMAIN_LIQUIDATION,
    POLICY_ROUTINE_CLOSE,
    normalize_direct_close_policy_alias,
)
from gui_auto_trade_integrity import (
    is_emergency_stopped_state,
    is_review_required_state,
)
from gui_auto_trade_policy import (
    auto_trade_setting_current_session_trade_started,
    auto_trade_setting_early_close_requested,
    auto_trade_setting_has_close_progress_quantity,
    auto_trade_setting_individual_liquidation_window_entered,
    auto_trade_setting_liquidation_phase_active,
    auto_trade_setting_trade_started,
    effective_liquidation_policy_for_config,
    individual_liquidation_policy_from_state,
    short_close_method_text,
)
from gui_ats_utils import manual_ats_active_now
from gui_common_utils import safe_int_value
from gui_window_policy import persistent_feature_owner
from manual_ats_liquidation_service import (
    ensure_manual_ats_liquidation_request,
    normalize_manual_ats_sell_method,
)
from manual_ats_runtime import manual_ats_runtime_selected_keys
from operation_command_service import (
    COMMAND_INDIVIDUAL_LIQUIDATION,
    IndividualLiquidationOverride,
    MODE_NORMAL,
    OperationCommandRequest,
    OperationCommandResult,
    OperationCommandService,
    RESULT_FAILED,
    RESULT_SUCCESS,
    SCOPE_STOCK,
    STOCK_APPLIED,
    STOCK_IGNORED_DUPLICATE,
)
from order_candidate_engine import get_real_holding_qty
from operation_policy_gate import write_global_operation_closing_state
from runtime_io import read_json_dict
from state_policy import normalize_operation_mode
from transition_evidence_reader import COMMAND_REQUEST_SCOPE, TransitionEvidenceScope
from transition_production_guard import evaluate_production_transition


EARLY_CLOSE_REQUEST = "EARLY_CLOSE_REQUEST"
EARLY_CLOSE_CANCEL = "EARLY_CLOSE_CANCEL"
INDIVIDUAL_LIQUIDATION = "INDIVIDUAL_LIQUIDATION"
MANUAL_ATS_LIQUIDATION = "MANUAL_ATS_LIQUIDATION"

PROJECT_ROOT = Path(__file__).resolve().parent
ORDER_QUEUE_PATH = PROJECT_ROOT / "runtime" / "order_queue.json"
FILLS_PATH = PROJECT_ROOT / "runtime" / "fills.json"


@dataclass(frozen=True)
class CloseLiquidationAvailability:
    allowed: bool
    reason_code: str
    intent: str
    stock_code: str
    current_session_participant: bool = False
    review_required: bool = False
    emergency_stopped: bool = False
    recovery_blocked: bool = False
    holding_qty: int = 0
    close_requested: bool = False
    command_id: str = ""
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class CloseLiquidationCommandResult:
    ok: bool
    changed: bool
    allowed: bool
    reason_code: str
    intent: str
    stock_code: str
    command_id: str = ""
    requested: bool = False
    availability: CloseLiquidationAvailability | None = None
    operation_result: Any = None


def _blocked(
    *,
    reason_code: str,
    intent: str,
    stock_code: str,
    current_session_participant: bool = False,
    review_required: bool = False,
    emergency_stopped: bool = False,
    recovery_blocked: bool = False,
    holding_qty: int = 0,
    close_requested: bool = False,
    command_id: str = "",
    evidence: tuple[str, ...] = (),
) -> CloseLiquidationAvailability:
    return CloseLiquidationAvailability(
        False,
        str(reason_code or "BLOCKED"),
        str(intent or "").strip().upper(),
        str(stock_code or "").strip(),
        current_session_participant=current_session_participant,
        review_required=review_required,
        emergency_stopped=emergency_stopped,
        recovery_blocked=recovery_blocked,
        holding_qty=holding_qty,
        close_requested=close_requested,
        command_id=command_id,
        evidence=evidence,
    )


def _recovery_decision(
    owner: object | None,
    stock_code: str,
    caller_name: str,
    inspector: Callable[[str, str], object | None] | None,
) -> object | None:
    if inspector is not None:
        try:
            return inspector(stock_code, caller_name)
        except Exception as exc:
            return type(
                "RecoveryFailure",
                (),
                {
                    "allowed": False,
                    "reason_code": "RECOVERY_BLOCKED",
                    "evidence": (f"recovery_gate_error={exc}",),
                },
            )()

    try:
        target = persistent_feature_owner(owner) if owner is not None else None
    except Exception:
        target = owner
    target = target if target is not None else owner
    checker = getattr(type(target), "production_recovery_gate_for_stock", None)
    if callable(checker):
        try:
            return checker(target, stock_code, caller_name=caller_name)
        except Exception as exc:
            return type(
                "RecoveryFailure",
                (),
                {
                    "allowed": False,
                    "reason_code": "RECOVERY_BLOCKED",
                    "evidence": (f"recovery_gate_error={exc}",),
                },
            )()
    return None


def _connection_ready(owner: object | None) -> bool | None:
    """Return broker connectivity when the owner exposes that contract."""

    try:
        target = persistent_feature_owner(owner) if owner is not None else None
    except Exception:
        target = owner
    target = target if target is not None else owner
    api = getattr(target, "kiwoom_api", None)
    checker = getattr(api, "is_connected", None)
    if not callable(checker):
        return None
    try:
        return checker() is True
    except Exception:
        return False


def inspect_close_liquidation_availability(
    owner: object | None,
    stock_dir: str | Path,
    stock_code: object,
    *,
    intent: str,
    requested_method: object = "",
    requested_minutes: object = "",
    now_dt: datetime | None = None,
    recovery_inspector: Callable[[str, str], object | None] | None = None,
    irreversible_evidence_reader: (
        Callable[[Path, str, dict[str, object]], str] | None
    ) = None,
) -> CloseLiquidationAvailability:
    """Inspect one Close/Liquidation command without mutating any state."""

    stock_path = Path(stock_dir)
    code = str(stock_code or "").strip()
    normalized_intent = str(intent or "").strip().upper()
    if normalized_intent not in {
        EARLY_CLOSE_REQUEST,
        EARLY_CLOSE_CANCEL,
        INDIVIDUAL_LIQUIDATION,
        MANUAL_ATS_LIQUIDATION,
    }:
        return _blocked(
            reason_code="INVALID_INTENT",
            intent=normalized_intent,
            stock_code=code,
        )

    state = read_json_dict(stock_path / "state.json")
    config = read_json_dict(stock_path / "config.json")
    if not isinstance(state, dict) or not state:
        return _blocked(
            reason_code="STATE_UNAVAILABLE",
            intent=normalized_intent,
            stock_code=code,
        )
    if not isinstance(config, dict):
        config = {}

    participant = auto_trade_setting_current_session_trade_started(
        owner,
        auto_trade_setting_trade_started(state),
        code,
    )
    review_required = is_review_required_state(state)
    emergency_stopped = is_emergency_stopped_state(state)
    holding_qty = (
        safe_int_value(get_real_holding_qty(state), 0)
        if normalized_intent == MANUAL_ATS_LIQUIDATION
        else safe_int_value(state.get("holding_qty"), 0)
    )
    close_requested = auto_trade_setting_early_close_requested(state)
    command_id = str(state.get("operation_command_id") or "").strip()

    common = {
        "intent": normalized_intent,
        "stock_code": code,
        "current_session_participant": participant,
        "review_required": review_required,
        "emergency_stopped": emergency_stopped,
        "holding_qty": holding_qty,
        "close_requested": close_requested,
        "command_id": command_id,
    }
    if review_required:
        return _blocked(reason_code="REVIEW_REQUIRED", **common)
    if emergency_stopped:
        return _blocked(reason_code="EMERGENCY_STOPPED", **common)
    if not participant:
        return _blocked(reason_code="NOT_CURRENT_PARTICIPANT", **common)

    if normalized_intent == EARLY_CLOSE_REQUEST and _connection_ready(owner) is False:
        return _blocked(reason_code="SERVER_NOT_CONNECTED", **common)

    recovery_caller_name = (
        "INDIVIDUAL_LIQUIDATION_REQUEST"
        if normalized_intent == INDIVIDUAL_LIQUIDATION
        else normalized_intent
    )
    recovery = _recovery_decision(
        owner,
        code,
        recovery_caller_name,
        recovery_inspector,
    )
    if recovery is not None and getattr(recovery, "allowed", False) is not True:
        recovery_reason = str(
            getattr(recovery, "reason_code", "") or "RECOVERY_BLOCKED"
        ).strip()
        return _blocked(
            reason_code=recovery_reason,
            recovery_blocked=True,
            evidence=tuple(getattr(recovery, "evidence", ()) or ()),
            **common,
        )

    if normalized_intent == EARLY_CLOSE_CANCEL:
        if not close_requested:
            return _blocked(reason_code="NOT_CANCELABLE", **common)
        requested_at = str(state.get("early_close_requested_at") or "").strip()
        if not requested_at:
            return _blocked(reason_code="COMMAND_IDENTITY_MISSING", **common)
        if irreversible_evidence_reader is not None:
            try:
                irreversible = str(
                    irreversible_evidence_reader(stock_path, code, state) or ""
                ).strip()
            except Exception as exc:
                irreversible = f"evidence_reader_error:{exc}"
            if irreversible:
                return _blocked(
                    reason_code="NOT_CANCELABLE",
                    evidence=(irreversible,),
                    **common,
                )
    elif normalized_intent == EARLY_CLOSE_REQUEST:
        if holding_qty <= 0:
            return _blocked(reason_code="NO_HOLDING", **common)
        if auto_trade_setting_liquidation_phase_active(
            config,
            holding_qty,
            now_dt=now_dt,
            state=state,
        ):
            return _blocked(reason_code="LIQUIDATION_IN_PROGRESS", **common)
    elif normalized_intent == INDIVIDUAL_LIQUIDATION:
        normalized_method = short_close_method_text(requested_method)
        if normalized_method not in {"시장가", "현재가", "이월"}:
            return _blocked(reason_code="INVALID_LIQUIDATION_METHOD", **common)
        if holding_qty <= 0:
            return _blocked(reason_code="NO_HOLDING", **common)
        current_override = individual_liquidation_policy_from_state(state)
        current_minutes = str(
            current_override.get("minutes_before_regular_close") or ""
        ).strip()
        clean_minutes = str(requested_minutes or "").strip() or "5"
        if (
            current_override
            and short_close_method_text(current_override.get("method"))
            == normalized_method
            and current_minutes == clean_minutes
        ):
            return _blocked(reason_code="ALREADY_REQUESTED", **common)
    else:
        if normalize_operation_mode(config.get("operation_mode", "SCHEDULED")) != "CONTINUOUS":
            return _blocked(reason_code="NOT_MANUAL_OPERATION", **common)
        if not manual_ats_runtime_selected_keys(state, now_dt=now_dt):
            return _blocked(reason_code="ATS_SESSION_NOT_SELECTED", **common)
        if not manual_ats_active_now(config, state, now_dt):
            return _blocked(reason_code="SESSION_NOT_ALLOWED", **common)
        if holding_qty <= 0:
            return _blocked(reason_code="NO_HOLDING", **common)
        if not normalize_manual_ats_sell_method(requested_method):
            return _blocked(reason_code="INVALID_LIQUIDATION_METHOD", **common)

    return CloseLiquidationAvailability(
        True,
        "",
        normalized_intent,
        code,
        current_session_participant=participant,
        review_required=review_required,
        emergency_stopped=emergency_stopped,
        recovery_blocked=False,
        holding_qty=holding_qty,
        close_requested=close_requested,
        command_id=command_id,
    )


def current_close_transition_policy(
    state: dict[str, object],
    requested_at: str,
) -> tuple[str, str]:
    early_method = normalize_direct_close_policy_alias(state.get("early_close_method"))
    early_policy = state.get("early_close_policy")
    if not early_method and isinstance(early_policy, dict):
        early_method = normalize_direct_close_policy_alias(early_policy.get("method"))
    early_started_at = str(state.get("early_close_requested_at") or "").strip()
    if early_method or early_started_at:
        return early_method or POLICY_ROUTINE_CLOSE, early_started_at or requested_at

    auto_method = str(state.get("auto_close_method") or "").strip()
    auto_policy = state.get("auto_close_policy")
    if not auto_method and isinstance(auto_policy, dict):
        auto_method = str(auto_policy.get("method") or "").strip()
    auto_started_at = str(state.get("auto_close_requested_at") or "").strip()
    if auto_method or auto_started_at:
        return auto_method, auto_started_at or requested_at
    return POLICY_ROUTINE_CLOSE, requested_at


def _transition_trade_date(timestamp: object, fallback: str) -> str:
    text = str(timestamp or "").strip()
    if len(text) >= 10 and text[4:5] == "-" and text[7:8] == "-":
        return text[:10]
    return str(fallback or "")[:10]


def command_transition_scope(
    *,
    code: str,
    routine_instance_id: str,
    started_at: str,
    requested_at: str,
    operation_command_id: str = "",
) -> TransitionEvidenceScope:
    return TransitionEvidenceScope(
        scope_type=COMMAND_REQUEST_SCOPE,
        stock_code=code,
        trade_date=_transition_trade_date(started_at, requested_at),
        routine_instance_id=routine_instance_id,
        transition_requested_at=started_at,
        operation_command_id=operation_command_id,
    )


def execute_individual_liquidation_command(
    owner: object | None,
    stock_dir: str | Path,
    stock_code: object,
    *,
    method: object,
    minutes_before_regular_close: object,
    source: str,
    now_dt: datetime | None = None,
    project_root: str | Path = PROJECT_ROOT,
    queue_path: str | Path = ORDER_QUEUE_PATH,
    fills_path: str | Path = FILLS_PATH,
    recovery_inspector: Callable[[str, str], object | None] | None = None,
    transition_guard: Callable[..., Any] = evaluate_production_transition,
    command_service_factory: Callable[..., OperationCommandService] | None = None,
    requested_at_factory: Callable[[], str] | None = None,
) -> CloseLiquidationCommandResult:
    stock_path = Path(stock_dir)
    code = str(stock_code or "").strip()
    normalized_method = short_close_method_text(method)
    minutes = str(minutes_before_regular_close or "").strip() or "5"
    availability = inspect_close_liquidation_availability(
        owner,
        stock_path,
        code,
        intent=INDIVIDUAL_LIQUIDATION,
        requested_method=normalized_method,
        requested_minutes=minutes,
        now_dt=now_dt,
        recovery_inspector=recovery_inspector,
    )
    if not availability.allowed:
        return CloseLiquidationCommandResult(
            False,
            False,
            False,
            availability.reason_code,
            INDIVIDUAL_LIQUIDATION,
            code,
            availability=availability,
        )

    state = read_json_dict(stock_path / "state.json")
    config = read_json_dict(stock_path / "config.json")
    state = state if isinstance(state, dict) else {}
    config = config if isinstance(config, dict) else {}
    requested_at = (
        requested_at_factory()
        if requested_at_factory is not None
        else datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )
    current_policy, _is_override = effective_liquidation_policy_for_config(
        config,
        state,
    )
    current_method = str(current_policy.get("method") or "").strip()
    current_request = state.get("individual_liquidation_request")
    current_request = current_request if isinstance(current_request, dict) else {}
    current_started_at = str(
        current_request.get("requested_at") or requested_at
    ).strip()
    current_command_id = str(
        current_request.get("command_id")
        or current_request.get("operation_command_id")
        or state.get("operation_command_id")
        or ""
    ).strip()
    routine_instance_id = str(
        config.get("assigned_routine_instance_id") or ""
    ).strip()
    transition = transition_guard(
        policy_domain=DOMAIN_LIQUIDATION,
        current_policy=current_method,
        requested_policy=normalized_method,
        queue_path=queue_path,
        fills_path=fills_path,
        runtime_state=state,
        runtime_routine_instance_id=routine_instance_id,
        scope=command_transition_scope(
            code=code,
            routine_instance_id=routine_instance_id,
            started_at=current_started_at,
            requested_at=requested_at,
            operation_command_id=current_command_id,
        ),
        liquidation_time_window_entered=(
            auto_trade_setting_individual_liquidation_window_entered(
                state,
                now_dt=now_dt,
                candidate_minutes_before_regular_close=minutes,
            )
        ),
    )
    if not getattr(transition, "allowed", False):
        reason = str(
            getattr(transition, "reason_code", "") or "TRANSITION_BLOCKED"
        )
        return CloseLiquidationCommandResult(
            False,
            False,
            False,
            reason,
            INDIVIDUAL_LIQUIDATION,
            code,
            availability=availability,
            operation_result=transition,
        )

    service_factory = command_service_factory or OperationCommandService
    service = service_factory(project_root)
    result = service.apply_individual_liquidation(
        OperationCommandRequest(
            target_scope=SCOPE_STOCK,
            target_id=str(stock_path.resolve()),
            command=COMMAND_INDIVIDUAL_LIQUIDATION,
            source=str(source or "").strip(),
        ),
        IndividualLiquidationOverride(
            method=normalized_method,
            minutes_before_regular_close=minutes,
        ),
    )
    stock_result = result.stock_results[0] if result.stock_results else None
    status = str(getattr(stock_result, "status", "") or "").strip().upper()
    changed = result.status == RESULT_SUCCESS and status == STOCK_APPLIED
    duplicate = status == STOCK_IGNORED_DUPLICATE
    reason = str(result.error or getattr(stock_result, "error", "") or "").strip()
    if not changed and not duplicate and not reason:
        reason = "COMMAND_PERSIST_FAILED"
    return CloseLiquidationCommandResult(
        changed or duplicate,
        changed,
        True,
        "DUPLICATE" if duplicate else reason,
        INDIVIDUAL_LIQUIDATION,
        code,
        command_id=result.command_id,
        requested=changed or duplicate,
        availability=availability,
        operation_result=result,
    )


def execute_manual_ats_liquidation_request_command(
    owner: object | None,
    preview: dict[str, object],
    *,
    project_root: str | Path = PROJECT_ROOT,
    now_dt: datetime | None = None,
    recovery_inspector: Callable[[str, str], object | None] | None = None,
    request_writer: Callable[..., dict[str, Any]] = ensure_manual_ats_liquidation_request,
    command_service_factory: Callable[..., OperationCommandService] | None = None,
) -> CloseLiquidationCommandResult:
    """Persist one ATS liquidation request after final semantic revalidation."""

    stock_path = Path(str(preview.get("stock_dir") or ""))
    code = str(preview.get("code") or "").strip()
    method = normalize_manual_ats_sell_method(preview.get("sell_method"))
    availability = inspect_close_liquidation_availability(
        owner,
        stock_path,
        code,
        intent=MANUAL_ATS_LIQUIDATION,
        requested_method=method,
        now_dt=now_dt,
        recovery_inspector=recovery_inspector,
    )
    if not availability.allowed:
        return CloseLiquidationCommandResult(
            False,
            False,
            False,
            availability.reason_code,
            MANUAL_ATS_LIQUIDATION,
            code,
            command_id=str(preview.get("command_id") or "").strip(),
            availability=availability,
        )

    writer_kwargs: dict[str, Any] = {"project_root": project_root}
    if command_service_factory is not None:
        writer_kwargs["command_service_factory"] = command_service_factory
    result = request_writer(preview, **writer_kwargs)
    ok = result.get("ok") is True
    stage = str(result.get("stage") or "").strip()
    request_status = str(result.get("request_status") or "").strip().upper()
    changed = ok and stage == "runtime_request" and request_status == "REQUESTED"
    reused = ok and stage == "runtime_request_reused"
    reasons = tuple(
        str(value).strip()
        for value in result.get("blocked_reasons", ())
        if str(value).strip()
    )
    reason = "DUPLICATE" if reused else (reasons[0] if reasons else "")
    if not ok and not reason:
        reason = "COMMAND_PERSIST_FAILED"
    command_result = result.get("command_result")
    command_id = str(
        getattr(command_result, "command_id", "")
        or preview.get("command_id")
        or ""
    ).strip()
    return CloseLiquidationCommandResult(
        ok,
        changed,
        True,
        reason,
        MANUAL_ATS_LIQUIDATION,
        code,
        command_id=command_id,
        requested=ok,
        availability=availability,
        operation_result=result,
    )


def execute_early_close_request_command(
    owner: object | None,
    stock_dir: str | Path,
    stock_code: object,
    *,
    method: object,
    source: str,
    extra_policy: dict[str, object] | None = None,
    now_dt: datetime | None = None,
    project_root: str | Path = PROJECT_ROOT,
    queue_path: str | Path = ORDER_QUEUE_PATH,
    fills_path: str | Path = FILLS_PATH,
    recovery_inspector: Callable[[str, str], object | None] | None = None,
    transition_guard: Callable[..., Any] = evaluate_production_transition,
    command_service_factory: Callable[..., OperationCommandService] | None = None,
    operation_state_writer: Callable[..., dict[str, Any]] | None = None,
    requested_at_factory: Callable[[], str] | None = None,
) -> CloseLiquidationCommandResult:
    stock_path = Path(stock_dir)
    code = str(stock_code or "").strip()
    method_text = normalize_direct_close_policy_alias(method) or POLICY_ROUTINE_CLOSE
    availability = inspect_close_liquidation_availability(
        owner,
        stock_path,
        code,
        intent=EARLY_CLOSE_REQUEST,
        requested_method=method_text,
        now_dt=now_dt,
        recovery_inspector=recovery_inspector,
    )
    if not availability.allowed:
        return CloseLiquidationCommandResult(
            False,
            False,
            False,
            availability.reason_code,
            EARLY_CLOSE_REQUEST,
            code,
            availability=availability,
        )

    state = read_json_dict(stock_path / "state.json")
    config = read_json_dict(stock_path / "config.json")
    state = state if isinstance(state, dict) else {}
    config = config if isinstance(config, dict) else {}
    requested_at = (
        requested_at_factory()
        if requested_at_factory is not None
        else datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )
    current_policy, current_started_at = current_close_transition_policy(
        state,
        requested_at,
    )
    current_command_id = str(state.get("operation_command_id") or "").strip()
    routine_instance_id = str(
        config.get("assigned_routine_instance_id") or ""
    ).strip()
    intent_result = apply_close_intent(
        intent=CLOSE_INTENT_EARLY_CLOSE,
        target_scope=SCOPE_STOCK,
        target_id=str(stock_path.resolve()),
        source=str(source or "").strip(),
        requested_policy=method_text,
        has_close_progress_quantity=auto_trade_setting_has_close_progress_quantity(
            safe_int_value(state.get("holding_qty"), 0),
            0,
        ),
        extra_policy=dict(extra_policy or {}),
        stock_code=code,
        runtime_state=state,
        runtime_routine_instance_id=routine_instance_id,
        current_policy=current_policy,
        current_started_at=current_started_at,
        current_command_id=current_command_id,
        requested_at=requested_at,
        project_root=project_root,
        queue_path=queue_path,
        fills_path=fills_path,
        operation_command_service_factory=(
            command_service_factory or OperationCommandService
        ),
        transition_guard=transition_guard,
        operation_state_writer=(
            operation_state_writer or write_global_operation_closing_state
        ),
    )
    command_result = intent_result.get("command_result")
    command_id = str(getattr(command_result, "command_id", "") or "")
    changed = bool(intent_result.get("durable_applied"))
    ok = bool(intent_result.get("ok"))
    reason = str(intent_result.get("reason") or "").strip()
    return CloseLiquidationCommandResult(
        ok,
        changed,
        not bool(intent_result.get("blocked")),
        reason,
        EARLY_CLOSE_REQUEST,
        code,
        command_id=command_id,
        requested=ok,
        availability=availability,
        operation_result=intent_result,
    )


def execute_early_close_cancel_command(
    owner: object | None,
    stock_dir: str | Path,
    stock_code: object,
    *,
    source: str,
    project_root: str | Path = PROJECT_ROOT,
    recovery_inspector: Callable[[str, str], object | None] | None = None,
    irreversible_evidence_reader: (
        Callable[[Path, str, dict[str, object]], str] | None
    ) = None,
    command_service_factory: Callable[..., OperationCommandService] | None = None,
) -> CloseLiquidationCommandResult:
    stock_path = Path(stock_dir)
    code = str(stock_code or "").strip()
    availability = inspect_close_liquidation_availability(
        owner,
        stock_path,
        code,
        intent=EARLY_CLOSE_CANCEL,
        recovery_inspector=recovery_inspector,
        irreversible_evidence_reader=irreversible_evidence_reader,
    )
    if not availability.allowed:
        return CloseLiquidationCommandResult(
            False,
            False,
            False,
            availability.reason_code,
            EARLY_CLOSE_CANCEL,
            code,
            availability=availability,
        )

    service_factory = command_service_factory or OperationCommandService
    service = service_factory(project_root)
    result = service.apply(
        OperationCommandRequest(
            target_scope=SCOPE_STOCK,
            target_id=str(stock_path.resolve()),
            command=MODE_NORMAL,
            source=str(source or "").strip(),
        )
    )
    stock_result = result.stock_results[0] if result.stock_results else None
    changed = bool(
        result.status == RESULT_SUCCESS
        and stock_result is not None
        and stock_result.status == STOCK_APPLIED
    )
    if changed:
        saved = read_json_dict(stock_path / "state.json")
        changed = not auto_trade_setting_early_close_requested(saved)
    reason = str(result.error or getattr(stock_result, "error", "") or "").strip()
    if not changed and not reason:
        reason = "READ_BACK_FAILED"
    return CloseLiquidationCommandResult(
        changed,
        changed,
        True,
        reason,
        EARLY_CLOSE_CANCEL,
        code,
        command_id=result.command_id,
        requested=False,
        availability=availability,
        operation_result=result,
    )
