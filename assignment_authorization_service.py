"""Read-only authorization and guarded Assignment application commands."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from assignment_episode_linkage import assign_stock_routine, unassign_stock_routine
from gui_auto_trade_integrity import (
    inspect_review_state_path,
    is_emergency_stopped_state,
)
from gui_auto_trade_policy import (
    auto_trade_current_session_operation_participant_codes,
)
from gui_common_utils import safe_int_value
from gui_order_utils import pending_order_side_quantities
from gui_routine_registry import scan_group_records
from gui_window_policy import persistent_feature_owner
from production_recovery_state_registry import (
    RECOVERY_CONTEXT_MISSING,
    RECOVERY_NOT_STARTED,
    RECOVERY_STOCK_PENDING,
    recovery_stock_is_review_required,
)
from routine_instance_repository import RoutineInstanceRepository
from runtime_io import read_json_dict
from stock_repository import StockRepository, normalize_stock_code


ASSIGNMENT_INTENT_ASSIGN = "ASSIGN"
ASSIGNMENT_INTENT_REASSIGN = "REASSIGN"
ASSIGNMENT_INTENT_UNASSIGN = "UNASSIGN"
ASSIGNMENT_INTENT_REVIEW_RESOLUTION_UNASSIGN = "REVIEW_RESOLUTION_UNASSIGN"
ASSIGNMENT_INTENT_STOCK_UNREGISTER = "STOCK_UNREGISTER"

_ASSIGN_INTENTS = frozenset(
    {ASSIGNMENT_INTENT_ASSIGN, ASSIGNMENT_INTENT_REASSIGN}
)
_UNASSIGN_INTENTS = frozenset(
    {
        ASSIGNMENT_INTENT_UNASSIGN,
        ASSIGNMENT_INTENT_REVIEW_RESOLUTION_UNASSIGN,
        ASSIGNMENT_INTENT_STOCK_UNREGISTER,
    }
)
_FIRST_ASSIGNMENT_RECOVERY_DEFERRED_REASONS = frozenset(
    {
        RECOVERY_CONTEXT_MISSING,
        RECOVERY_NOT_STARTED,
        RECOVERY_STOCK_PENDING,
    }
)


@dataclass(frozen=True)
class AssignmentAuthorizationResult:
    allowed: bool
    reason_code: str
    stock_code: str
    intent: str
    current_session_participant: bool = False
    review_required: bool = False
    recovery_blocked: bool = False
    registration_valid: bool = False
    assignment_valid: bool = False
    current_instance_id: str = ""
    target_instance_id: str = ""
    group_id: str = ""
    holding_qty: int = 0
    buy_pending_qty: int | str = 0
    sell_pending_qty: int | str = 0
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class AssignmentCommandResult:
    ok: bool
    changed: bool = False
    reason_code: str = ""
    transaction_id: str = ""
    assignment_before: str = ""
    assignment_after: str = ""
    reconciliation_required: bool = False
    error: str = ""
    authorization: AssignmentAuthorizationResult | None = None


def _owner_candidates(owner: object | None) -> tuple[object, ...]:
    candidates: list[object] = []
    pending = [owner]
    seen: set[int] = set()
    while pending and len(candidates) < 10:
        current = pending.pop(0)
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        candidates.append(current)
        try:
            logical_owner = persistent_feature_owner(current)  # type: ignore[arg-type]
        except Exception:
            logical_owner = None
        if logical_owner is not None and logical_owner is not current:
            pending.append(logical_owner)
        for attribute in (
            "_window",
            "_owner",
            "auto_trade_setting_window",
            "_main_monitoring_auto_trade_operation_host",
        ):
            try:
                linked = getattr(current, attribute, None)
            except Exception:
                linked = None
            if linked is not None:
                pending.append(linked)
    return tuple(candidates)


def _recovery_block(owner: object | None, stock_code: str) -> tuple[bool, str, tuple[str, ...]]:
    candidates = _owner_candidates(owner)
    for candidate in candidates:
        checker = getattr(candidate, "production_recovery_gate_for_stock", None)
        if not callable(checker):
            continue
        try:
            decision = checker(
                stock_code,
                caller_name="ASSIGNMENT_AUTHORIZATION",
            )
        except Exception as exc:
            return True, "RECOVERY_BLOCKED", (f"recovery_gate_error={exc}",)
        if bool(getattr(decision, "allowed", False)):
            return False, "", ()
        reason = str(getattr(decision, "reason_code", "") or "RECOVERY_BLOCKED")
        return True, reason, tuple(getattr(decision, "evidence", ()) or ())

    try:
        if recovery_stock_is_review_required(stock_code):
            return True, "RECOVERY_BLOCKED", ("recovery_stock_review_required",)
    except Exception as exc:
        return True, "RECOVERY_BLOCKED", (f"recovery_review_error={exc}",)

    for candidate in candidates:
        checker = getattr(candidate, "startup_recovery_session_ready", None)
        if not callable(checker):
            continue
        try:
            if checker(refresh=False):
                return False, "", ()
            return True, "RECOVERY_BLOCKED", ("startup_recovery_not_ready",)
        except Exception as exc:
            return True, "RECOVERY_BLOCKED", (f"startup_recovery_error={exc}",)
    return False, "", ()


def _blocked(
    *,
    reason_code: str,
    stock_code: str,
    intent: str,
    current_session_participant: bool = False,
    review_required: bool = False,
    recovery_blocked: bool = False,
    registration_valid: bool = False,
    assignment_valid: bool = False,
    current_instance_id: str = "",
    target_instance_id: str = "",
    group_id: str = "",
    holding_qty: int = 0,
    buy_pending_qty: int | str = 0,
    sell_pending_qty: int | str = 0,
    evidence: tuple[str, ...] = (),
) -> AssignmentAuthorizationResult:
    return AssignmentAuthorizationResult(
        False,
        reason_code,
        stock_code,
        intent,
        current_session_participant=current_session_participant,
        review_required=review_required,
        recovery_blocked=recovery_blocked,
        registration_valid=registration_valid,
        assignment_valid=assignment_valid,
        current_instance_id=current_instance_id,
        target_instance_id=target_instance_id,
        group_id=group_id,
        holding_qty=holding_qty,
        buy_pending_qty=buy_pending_qty,
        sell_pending_qty=sell_pending_qty,
        evidence=evidence,
    )


def inspect_assignment_authorization(
    owner: object | None,
    project_root: Path | str,
    code: object,
    name: str,
    *,
    intent: str,
    target_instance_id: str = "",
    expected_instance_id: str | None = None,
) -> AssignmentAuthorizationResult:
    """Inspect Assignment authority without writing config, Runtime, or Review state."""

    root = Path(project_root).resolve(strict=False)
    stock_code = normalize_stock_code(code)
    clean_intent = str(intent or "").strip().upper()
    target_id = str(target_instance_id or "").strip()
    if clean_intent not in _ASSIGN_INTENTS | _UNASSIGN_INTENTS:
        return _blocked(
            reason_code="INVALID_INTENT",
            stock_code=stock_code,
            intent=clean_intent,
            target_instance_id=target_id,
        )

    participant = bool(
        stock_code
        and stock_code
        in auto_trade_current_session_operation_participant_codes(owner)
    )
    if participant:
        return _blocked(
            reason_code="CURRENTLY_RUNNING",
            stock_code=stock_code,
            intent=clean_intent,
            current_session_participant=True,
            target_instance_id=target_id,
        )

    repository = StockRepository(root)
    try:
        record = repository.find_by_code(stock_code)
    except Exception as exc:
        return _blocked(
            reason_code="REGISTRATION_INVALID",
            stock_code=stock_code,
            intent=clean_intent,
            target_instance_id=target_id,
            evidence=(f"repository_error={exc}",),
        )
    if record is None:
        return _blocked(
            reason_code="NOT_REGISTERED",
            stock_code=stock_code,
            intent=clean_intent,
            target_instance_id=target_id,
        )

    stock_dir = repository.resolve_stock_dir(stock_code, name)
    config_path = stock_dir / "config.json"
    state_path = stock_dir / "state.json"
    orders_path = stock_dir / "orders.json"
    review_inspection = inspect_review_state_path(state_path)
    if not config_path.is_file() or not state_path.is_file() or not orders_path.is_file():
        return _blocked(
            reason_code="REGISTRATION_INVALID",
            stock_code=stock_code,
            intent=clean_intent,
            review_required=review_inspection.review_required,
            registration_valid=False,
            target_instance_id=target_id,
            evidence=(
                f"config_exists={config_path.is_file()}",
                f"state_exists={state_path.is_file()}",
                f"orders_exists={orders_path.is_file()}",
            ),
        )

    config = read_json_dict(config_path)
    state = review_inspection.state
    orders = read_json_dict(orders_path)
    if not isinstance(config, dict) or not isinstance(state, dict):
        return _blocked(
            reason_code="REGISTRATION_INVALID",
            stock_code=stock_code,
            intent=clean_intent,
            target_instance_id=target_id,
            evidence=("config_or_state_not_object",),
        )
    orders_integrity_valid = bool(
        isinstance(orders, dict)
        and isinstance(orders.get("orders", []), list)
    )

    current_id = str(config.get("assigned_routine_instance_id", "") or "").strip()
    review_required = review_inspection.review_required
    review_resolution = clean_intent == ASSIGNMENT_INTENT_REVIEW_RESOLUTION_UNASSIGN
    if review_required and not review_resolution:
        return _blocked(
            reason_code="REVIEW_REQUIRED",
            stock_code=stock_code,
            intent=clean_intent,
            review_required=True,
            registration_valid=True,
            current_instance_id=current_id,
            target_instance_id=target_id,
            evidence=(
                f"review_reason={review_inspection.reason_code}",
                f"review_source={review_inspection.source}",
            ),
        )
    if is_emergency_stopped_state(state) and not review_resolution:
        return _blocked(
            reason_code="EMERGENCY_STOP",
            stock_code=stock_code,
            intent=clean_intent,
            review_required=review_required,
            registration_valid=True,
            current_instance_id=current_id,
            target_instance_id=target_id,
        )

    # Recovery gates execution-enabling Assignment changes.  Removing an
    # Assignment is configuration-only and remains governed by the participant,
    # Review/Emergency, identity, holding, pending-order, and integrity guards.
    if clean_intent in _ASSIGN_INTENTS:
        recovery_blocked, recovery_reason, recovery_evidence = _recovery_block(
            owner,
            stock_code,
        )
        state_status = str(state.get("status") or "").strip().upper()
        # A first, stopped Assignment is configuration-only.  Missing/not-yet-
        # admitted Recovery authority is deferred to Operation Start; explicit
        # Recovery failure/review/integrity decisions remain blocking here.
        deferred_first_assignment = bool(
            recovery_blocked
            and recovery_reason in _FIRST_ASSIGNMENT_RECOVERY_DEFERRED_REASONS
            and clean_intent == ASSIGNMENT_INTENT_ASSIGN
            and not current_id
            and state_status == "STOPPED"
        )
        if recovery_blocked and not deferred_first_assignment:
            return _blocked(
                reason_code=recovery_reason or "RECOVERY_BLOCKED",
                stock_code=stock_code,
                intent=clean_intent,
                review_required=review_required,
                recovery_blocked=True,
                registration_valid=True,
                current_instance_id=current_id,
                target_instance_id=target_id,
                evidence=recovery_evidence,
            )

    if expected_instance_id is not None and current_id != str(expected_instance_id or "").strip():
        return _blocked(
            reason_code="ASSIGNMENT_CHANGED",
            stock_code=stock_code,
            intent=clean_intent,
            review_required=review_required,
            registration_valid=True,
            current_instance_id=current_id,
            target_instance_id=target_id,
        )

    instance_repository = RoutineInstanceRepository(root)
    groups = {record.group_id for record in scan_group_records(project_root=root)}
    current_group_id = ""
    if current_id:
        current_instance = instance_repository.get_instance(current_id)
        if current_instance is None:
            return _blocked(
                reason_code="ASSIGNMENT_INVALID",
                stock_code=stock_code,
                intent=clean_intent,
                review_required=review_required,
                registration_valid=True,
                current_instance_id=current_id,
                target_instance_id=target_id,
                evidence=("current_instance_missing",),
            )
        current_group_id = str(current_instance.group_id or "").strip()
        if not current_group_id or current_group_id not in groups:
            return _blocked(
                reason_code="ASSIGNMENT_INVALID",
                stock_code=stock_code,
                intent=clean_intent,
                review_required=review_required,
                registration_valid=True,
                current_instance_id=current_id,
                target_instance_id=target_id,
                group_id=current_group_id,
                evidence=("current_group_missing",),
            )

    target_group_id = ""
    if clean_intent in _ASSIGN_INTENTS:
        if not target_id:
            return _blocked(
                reason_code="TARGET_INSTANCE_MISSING",
                stock_code=stock_code,
                intent=clean_intent,
                registration_valid=True,
                assignment_valid=not current_id or bool(current_group_id),
                current_instance_id=current_id,
            )
        target_instance = instance_repository.get_instance(target_id)
        if target_instance is None:
            return _blocked(
                reason_code="TARGET_INSTANCE_MISSING",
                stock_code=stock_code,
                intent=clean_intent,
                registration_valid=True,
                assignment_valid=not current_id or bool(current_group_id),
                current_instance_id=current_id,
                target_instance_id=target_id,
            )
        target_group_id = str(target_instance.group_id or "").strip()
        if not target_group_id or target_group_id not in groups:
            return _blocked(
                reason_code="GROUP_MISSING",
                stock_code=stock_code,
                intent=clean_intent,
                registration_valid=True,
                assignment_valid=not current_id or bool(current_group_id),
                current_instance_id=current_id,
                target_instance_id=target_id,
                group_id=target_group_id,
            )
        if clean_intent == ASSIGNMENT_INTENT_ASSIGN and current_id and current_id != target_id:
            return _blocked(
                reason_code="ASSIGNMENT_EXISTS",
                stock_code=stock_code,
                intent=clean_intent,
                registration_valid=True,
                assignment_valid=True,
                current_instance_id=current_id,
                target_instance_id=target_id,
                group_id=target_group_id,
            )
        if clean_intent == ASSIGNMENT_INTENT_REASSIGN and not current_id:
            return _blocked(
                reason_code="ALREADY_UNASSIGNED",
                stock_code=stock_code,
                intent=clean_intent,
                registration_valid=True,
                assignment_valid=True,
                target_instance_id=target_id,
                group_id=target_group_id,
            )
    elif not current_id:
        return AssignmentAuthorizationResult(
            True,
            "ALREADY_UNASSIGNED",
            stock_code,
            clean_intent,
            review_required=review_required,
            registration_valid=True,
            assignment_valid=True,
        )

    holding_qty = safe_int_value(state.get("holding_qty"), 0)
    try:
        buy_pending_qty, sell_pending_qty = pending_order_side_quantities(
            stock_dir,
            state,
        )
    except Exception as exc:
        buy_pending_qty, sell_pending_qty = "?", "?"
        pending_error = (f"pending_inspection_error={exc}",)
    else:
        pending_error = ()
    common = {
        "stock_code": stock_code,
        "intent": clean_intent,
        "review_required": review_required,
        "registration_valid": True,
        "assignment_valid": True,
        "current_instance_id": current_id,
        "target_instance_id": target_id,
        "group_id": target_group_id or current_group_id,
        "holding_qty": holding_qty,
        "buy_pending_qty": buy_pending_qty,
        "sell_pending_qty": sell_pending_qty,
    }
    if holding_qty > 0:
        return _blocked(reason_code="HAS_HOLDING", **common)
    if not orders_integrity_valid:
        return _blocked(
            reason_code="PENDING_ORDER_INTEGRITY_UNKNOWN",
            evidence=("orders_root_invalid",),
            **common,
        )
    if buy_pending_qty == "?" or sell_pending_qty == "?":
        return _blocked(
            reason_code="PENDING_ORDER_INTEGRITY_UNKNOWN",
            evidence=pending_error,
            **common,
        )
    if safe_int_value(buy_pending_qty, 0) > 0 or safe_int_value(sell_pending_qty, 0) > 0:
        return _blocked(reason_code="HAS_PENDING_ORDER", **common)

    return AssignmentAuthorizationResult(
        True,
        "ALLOWED",
        stock_code,
        clean_intent,
        review_required=review_required,
        registration_valid=True,
        assignment_valid=True,
        current_instance_id=current_id,
        target_instance_id=target_id,
        group_id=target_group_id or current_group_id,
        holding_qty=holding_qty,
        buy_pending_qty=buy_pending_qty,
        sell_pending_qty=sell_pending_qty,
    )


def inspect_stock_unregister_availability(
    owner: object | None,
    project_root: Path | str,
    code: object,
    name: str,
    *,
    expected_instance_id: str | None = None,
) -> AssignmentAuthorizationResult:
    return inspect_assignment_authorization(
        owner,
        project_root,
        code,
        name,
        intent=ASSIGNMENT_INTENT_STOCK_UNREGISTER,
        expected_instance_id=expected_instance_id,
    )


def execute_assignment_change(
    owner: object | None,
    project_root: Path | str,
    code: object,
    name: str,
    *,
    instance_id: str,
    instance_name: str,
    definition_id: str,
    routine_type: str,
    expected_instance_id: str | None = None,
    intent: str = ASSIGNMENT_INTENT_ASSIGN,
) -> AssignmentCommandResult:
    authorization = inspect_assignment_authorization(
        owner,
        project_root,
        code,
        name,
        intent=intent,
        target_instance_id=instance_id,
        expected_instance_id=expected_instance_id,
    )
    if not authorization.allowed:
        return AssignmentCommandResult(
            False,
            reason_code=authorization.reason_code,
            assignment_before=authorization.current_instance_id,
            assignment_after=str(instance_id or "").strip(),
            authorization=authorization,
        )
    repository = StockRepository(Path(project_root))
    result = assign_stock_routine(
        repository.project_root,
        authorization.stock_code,
        name,
        instance_id=str(instance_id or "").strip(),
        instance_name=instance_name,
        definition_id=definition_id,
        routine_type=routine_type,
        expected_instance_id=(
            authorization.current_instance_id
            if expected_instance_id is None
            else expected_instance_id
        ),
        stock_repository=repository,
    )
    return AssignmentCommandResult(
        result.ok,
        changed=result.changed,
        reason_code=result.reason_code,
        transaction_id=result.transaction_id,
        assignment_before=result.assignment_before,
        assignment_after=result.assignment_after,
        reconciliation_required=result.reconciliation_required,
        error=result.error,
        authorization=authorization,
    )


def execute_assignment_unassign(
    owner: object | None,
    project_root: Path | str,
    code: object,
    name: str,
    *,
    expected_instance_id: str | None = None,
    intent: str = ASSIGNMENT_INTENT_UNASSIGN,
) -> AssignmentCommandResult:
    authorization = inspect_assignment_authorization(
        owner,
        project_root,
        code,
        name,
        intent=intent,
        expected_instance_id=expected_instance_id,
    )
    if not authorization.allowed:
        return AssignmentCommandResult(
            False,
            reason_code=authorization.reason_code,
            assignment_before=authorization.current_instance_id,
            authorization=authorization,
        )
    if authorization.reason_code == "ALREADY_UNASSIGNED":
        return AssignmentCommandResult(
            True,
            changed=False,
            reason_code="ALREADY_UNASSIGNED",
            authorization=authorization,
        )
    repository = StockRepository(Path(project_root))
    result = unassign_stock_routine(
        repository.project_root,
        authorization.stock_code,
        name,
        [],
        expected_instance_id=(
            authorization.current_instance_id
            if expected_instance_id is None
            else expected_instance_id
        ),
        stock_repository=repository,
    )
    return AssignmentCommandResult(
        result.ok,
        changed=result.changed,
        reason_code=result.reason_code,
        transaction_id=result.transaction_id,
        assignment_before=result.assignment_before,
        assignment_after=result.assignment_after,
        reconciliation_required=result.reconciliation_required,
        error=result.error,
        authorization=authorization,
    )
