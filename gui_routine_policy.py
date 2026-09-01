# -*- coding: utf-8 -*-
"""
gui_routine_policy.py

루틴 지정/변경 가능 여부를 판단하는 Policy 함수.
데이터 수집은 Guard에 맡기고, 이 파일은 가능/불가 판단과 제한 사유 생성만 담당한다.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gui_common_utils import safe_int_value
from gui_auto_trade_utils import (
    PENDING_INTEGRITY_USER_REASON,
)
from gui_order_utils import pending_order_side_quantities
from gui_routine_guard import routine_action_guard_info
from gui_auto_trade_integrity import (
    is_emergency_stopped_state,
    is_review_protected_stock_dir,
    is_review_required_state,
)
from state_policy import CanonicalAutoTradeStatus, canonical_auto_trade_status
from routine_instance_registry import routine_definition_by_id, routine_instance_by_id
from stock_repository import StockRepository, is_valid_stock_code, normalize_stock_code

LOGGER = logging.getLogger(__name__)
UNEXPECTED_STATUS_REASON = "처리할 수 없는 종목입니다."
PROJECT_ROOT = Path(__file__).resolve().parent

CURRENT_STOCK_RELATION = "CURRENT"
HISTORICAL_STOCK_RELATION = "HISTORICAL"


@dataclass(frozen=True)
class RoutineUnassignRuntimeResolution:
    status: str
    stock_dir: Path | None
    config: dict[str, Any]
    state: dict[str, Any]
    error: str = ""


@dataclass(frozen=True)
class RoutineUnassignDecision:
    applicable: bool
    allowed: bool
    primary_reason_code: str
    reason_codes: tuple[str, ...]
    user_reasons: tuple[str, ...]
    status_info: CanonicalAutoTradeStatus | None
    evidence: dict[str, Any]
    diagnostic_class: str
    review_required: bool
    event_required: bool


_INTEGRITY_REASON_CODES = frozenset(
    {
        "STOCK_CODE_INVALID",
        "STOCK_RECORD_MISSING",
        "CURRENT_INSTANCE_MISMATCH",
        "ROUTINE_RELATION_MISMATCH",
        "PENDING_INTEGRITY_UNKNOWN",
        "UNKNOWN_STATUS",
        "STOCK_RUNTIME_MISSING",
        "STOCK_RUNTIME_READ_ERROR",
        "STOCK_RUNTIME_RELATION_BROKEN",
    }
)


def resolve_routine_unassign_runtime(
    code: str,
    name: str,
    *,
    project_root: Path | None = None,
) -> RoutineUnassignRuntimeResolution:
    clean_code = normalize_stock_code(code)
    if not is_valid_stock_code(clean_code):
        return RoutineUnassignRuntimeResolution("STOCK_CODE_INVALID", None, {}, {})
    root = Path(project_root or PROJECT_ROOT)
    repository = StockRepository(root)
    try:
        record = repository.find_by_code(clean_code)
    except Exception as exc:
        return RoutineUnassignRuntimeResolution(
            "READ_ERROR",
            None,
            {},
            {},
            error=str(exc),
        )
    if record is None:
        return RoutineUnassignRuntimeResolution("STOCK_NOT_FOUND", None, {}, {})
    stock_dir = root / record.stock_path
    try:
        stock_dir.resolve().relative_to(repository.stocks_dir.resolve())
    except (OSError, ValueError):
        return RoutineUnassignRuntimeResolution(
            "ASSIGNMENT_MISMATCH",
            stock_dir,
            {},
            {},
            error="stock path is outside the central stocks directory",
        )
    folder_code = stock_dir.name.split("_", 1)[0].strip()
    if folder_code != clean_code:
        return RoutineUnassignRuntimeResolution(
            "ASSIGNMENT_MISMATCH",
            stock_dir,
            {},
            {},
            error="stock directory code does not match repository identity",
        )
    if not stock_dir.is_dir():
        return RoutineUnassignRuntimeResolution("STOCK_DIR_MISSING", stock_dir, {}, {})

    config_path = stock_dir / "config.json"
    state_path = stock_dir / "state.json"
    orders_path = stock_dir / "orders.json"
    if not config_path.is_file() or not state_path.is_file() or not orders_path.is_file():
        missing = (
            "STATE_FILE_MISSING"
            if not state_path.is_file()
            else "CONFIG_FILE_MISSING"
            if not config_path.is_file()
            else "ORDERS_FILE_MISSING"
        )
        return RoutineUnassignRuntimeResolution(missing, stock_dir, {}, {})
    try:
        config_value = json.loads(config_path.read_text(encoding="utf-8"))
        state_value = json.loads(state_path.read_text(encoding="utf-8"))
        orders_value = json.loads(orders_path.read_text(encoding="utf-8"))
        if not isinstance(config_value, dict) or not isinstance(state_value, dict):
            raise ValueError("config/state root must be an object")
        if not isinstance(orders_value, dict) or not isinstance(orders_value.get("orders", []), list):
            raise ValueError("orders root must contain an orders list")
    except Exception as exc:
        return RoutineUnassignRuntimeResolution(
            "READ_ERROR",
            stock_dir,
            {},
            {},
            error=str(exc),
        )
    return RoutineUnassignRuntimeResolution(
        "FOUND",
        stock_dir,
        config_value,
        state_value,
    )


def _persisted_routine_values(config: dict[str, Any]) -> tuple[str, ...]:
    values: list[str] = []
    for key in ("routine", "routine_name", "assigned_routine", "active_routine"):
        value = str(config.get(key, "") or "").strip()
        if value:
            values.append(value)
    routines = config.get("routines", [])
    if isinstance(routines, list):
        values.extend(str(value or "").strip() for value in routines if str(value or "").strip())
    elif str(routines or "").strip():
        values.append(str(routines or "").strip())
    return tuple(values)


def _unassign_user_reason(code: str, evidence: dict[str, Any]) -> str:
    if code == "NOT_CURRENT_ROW":
        return "과거 종목은 현재 등록해제 대상이 아닙니다."
    if code == "STOCK_CODE_INVALID":
        return "종목코드가 올바르지 않습니다."
    if code == "STOCK_RECORD_MISSING":
        return "중앙 종목정보를 찾지 못했습니다."
    if code == "NO_CURRENT_ASSIGNMENT":
        return "현재 등록된 루틴이 없습니다."
    if code == "CURRENT_INSTANCE_MISMATCH":
        return "화면의 Instance와 현재 등록 Instance가 일치하지 않습니다."
    if code == "ROUTINE_RELATION_MISMATCH":
        return "저장된 루틴 관계 정보가 서로 일치하지 않습니다."
    if code == "REVIEW_REQUIRED":
        return "검토관리 대상입니다."
    if code == "EMERGENCY_STOP":
        return "긴급정지 상태입니다."
    if code == "HOLDING_QTY":
        return f"보유수량 {int(evidence.get('holding_qty', 0) or 0)}주"
    if code == "BUY_PENDING":
        return f"매수 미체결 {int(evidence.get('buy_pending_qty', 0) or 0)}주"
    if code == "SELL_PENDING":
        return f"매도 미체결 {int(evidence.get('sell_pending_qty', 0) or 0)}주"
    if code == "PENDING_INTEGRITY_UNKNOWN":
        return "미체결 데이터를 확인할 수 없습니다. 검토관리에서 확인이 필요합니다."
    if code == "UNKNOWN_STATUS":
        return f"종목 상태를 해석할 수 없습니다: {evidence.get('raw_status', '')}"
    if code == "STOCK_RUNTIME_MISSING":
        return "종목 Runtime 파일을 찾지 못했습니다."
    if code == "STOCK_RUNTIME_READ_ERROR":
        return "종목 Runtime 파일을 읽지 못했습니다."
    if code == "STOCK_RUNTIME_RELATION_BROKEN":
        return "종목 Runtime 관계가 손상되었습니다."
    return "등록해제할 수 없습니다."


def routine_unassign_decision(
    code: str,
    name: str,
    *,
    row_instance_id: str = "",
    row_relation_kind: str = CURRENT_STOCK_RELATION,
    project_root: Path | None = None,
) -> RoutineUnassignDecision:
    clean_code = normalize_stock_code(code)
    clean_row_instance_id = str(row_instance_id or "").strip()
    relation_kind = str(row_relation_kind or "").strip().upper()
    root = Path(project_root or PROJECT_ROOT)
    runtime = resolve_routine_unassign_runtime(
        clean_code,
        name,
        project_root=root,
    )
    reason_codes: list[str] = []
    status_info: CanonicalAutoTradeStatus | None = None
    config = runtime.config
    state = runtime.state
    current_instance_id = str(config.get("assigned_routine_instance_id", "") or "").strip()
    persisted_values = _persisted_routine_values(config)
    evidence: dict[str, Any] = {
        "raw_status": "",
        "canonical_status_class": "",
        "holding_qty": 0,
        "buy_pending_qty": 0,
        "sell_pending_qty": 0,
        "emergency_stopped": False,
        "review_required": False,
        "row_instance_id": clean_row_instance_id,
        "current_instance_id": current_instance_id,
        "persisted_routine_fields": persisted_values,
        "runtime_resolution": runtime.status,
        "runtime_error": runtime.error,
        "relation_issues": (),
    }

    if relation_kind != CURRENT_STOCK_RELATION:
        reason_codes.append("NOT_CURRENT_ROW")
    if runtime.status == "STOCK_CODE_INVALID":
        reason_codes.append("STOCK_CODE_INVALID")
    elif runtime.status == "STOCK_NOT_FOUND":
        reason_codes.append("STOCK_RECORD_MISSING")
    elif runtime.status in {
        "STOCK_DIR_MISSING",
        "STATE_FILE_MISSING",
        "CONFIG_FILE_MISSING",
        "ORDERS_FILE_MISSING",
    }:
        reason_codes.append("STOCK_RUNTIME_MISSING")
    elif runtime.status == "ASSIGNMENT_MISMATCH":
        reason_codes.append("STOCK_RUNTIME_RELATION_BROKEN")
    elif runtime.status == "READ_ERROR":
        reason_codes.append("STOCK_RUNTIME_READ_ERROR")

    if runtime.status == "FOUND":
        relation_issues: list[str] = []
        if not current_instance_id:
            reason_codes.append("NO_CURRENT_ASSIGNMENT")
        elif clean_row_instance_id and clean_row_instance_id != current_instance_id:
            reason_codes.append("CURRENT_INSTANCE_MISMATCH")
        if len(set(persisted_values)) > 1:
            relation_issues.append("legacy routine fields disagree")

        current_instance = None
        if current_instance_id:
            try:
                current_instance = routine_instance_by_id(
                    current_instance_id,
                    project_root=root,
                    routines_root=root / "routines",
                    instances_root=root / "routine_instances",
                )
            except Exception as exc:
                relation_issues.append(f"instance lookup failed: {exc}")
            if current_instance is None:
                reason_codes.append("STOCK_RUNTIME_RELATION_BROKEN")
                relation_issues.append("assigned instance does not exist")
            else:
                expected_definition_id = str(
                    getattr(current_instance, "definition_id", "") or ""
                ).strip()
                persisted_definition_id = str(
                    config.get("routine_definition_id", "") or ""
                ).strip()
                if (
                    persisted_definition_id
                    and expected_definition_id
                    and persisted_definition_id != expected_definition_id
                ):
                    relation_issues.append("routine definition id does not match assigned instance")

                expected_instance_name = str(
                    getattr(current_instance, "display_name", "") or ""
                ).strip()
                persisted_instance_name = str(
                    config.get("routine_instance_name", "") or ""
                ).strip()
                if (
                    persisted_instance_name
                    and expected_instance_name
                    and persisted_instance_name != expected_instance_name
                ):
                    relation_issues.append("routine instance name does not match assigned instance")

                accepted_routine_names = {
                    str(getattr(current_instance, "source_routine_name", "") or "").strip()
                }
                try:
                    definition = routine_definition_by_id(
                        expected_definition_id,
                        project_root=root,
                        routines_root=root / "routines",
                    )
                except Exception:
                    definition = None
                if definition is not None:
                    accepted_routine_names.add(
                        str(getattr(definition, "display_name", "") or "").strip()
                    )
                accepted_routine_names.discard("")
                if accepted_routine_names and any(
                    value not in accepted_routine_names for value in persisted_values
                ):
                    relation_issues.append("legacy routine value does not match assigned instance definition")

        # Assignment identity is the canonical Instance ID.  Display and
        # compatibility snapshots may legitimately lag an Instance rename;
        # retain that evidence without turning it into an unassign blocker.
        evidence["relation_issues"] = tuple(relation_issues)

        raw_status = str(state.get("status", "STOPPED") or "STOPPED").strip().upper()
        status_info = canonical_auto_trade_status(raw_status)
        holding_qty = safe_int_value(state.get("holding_qty"), 0)
        try:
            buy_pending_qty, sell_pending_qty = pending_order_side_quantities(
                runtime.stock_dir,
                state,
            )
        except Exception as exc:
            LOGGER.warning(
                "routine unassign pending-order inspection failed: code=%s name=%s",
                clean_code,
                name,
                exc_info=True,
            )
            buy_pending_qty, sell_pending_qty = "?", "?"
            evidence["pending_integrity_error"] = str(exc)
        review_required = bool(
            status_info.known
            and (
                is_review_required_state(state)
                or (
                    runtime.stock_dir is not None
                    and is_review_protected_stock_dir(runtime.stock_dir)
                )
            )
        )
        emergency_stopped = bool(is_emergency_stopped_state(state) or status_info.is_emergency)
        evidence.update(
            {
                "raw_status": raw_status,
                "canonical_status_class": status_info.status_class,
                "holding_qty": holding_qty,
                "buy_pending_qty": buy_pending_qty,
                "sell_pending_qty": sell_pending_qty,
                "emergency_stopped": emergency_stopped,
                "review_required": review_required,
            }
        )
        if review_required or status_info.is_review_required:
            reason_codes.append("REVIEW_REQUIRED")
        elif not status_info.known:
            LOGGER.error(
                "unexpected routine unassign policy status: %s code=%s name=%s",
                raw_status,
                clean_code,
                name,
            )
            reason_codes.append("UNKNOWN_STATUS")
        if emergency_stopped:
            reason_codes.append("EMERGENCY_STOP")
        if holding_qty > 0:
            reason_codes.append("HOLDING_QTY")
        if buy_pending_qty == "?" or sell_pending_qty == "?":
            reason_codes.append("PENDING_INTEGRITY_UNKNOWN")
        else:
            if isinstance(buy_pending_qty, int) and buy_pending_qty > 0:
                reason_codes.append("BUY_PENDING")
            if isinstance(sell_pending_qty, int) and sell_pending_qty > 0:
                reason_codes.append("SELL_PENDING")

    reason_codes = list(dict.fromkeys(reason_codes))
    integrity = any(code in _INTEGRITY_REASON_CODES for code in reason_codes)
    safety = any(
        code in {"REVIEW_REQUIRED", "EMERGENCY_STOP", "HOLDING_QTY", "BUY_PENDING", "SELL_PENDING"}
        for code in reason_codes
    )
    not_applicable = bool(reason_codes) and all(
        code in {"NOT_CURRENT_ROW", "NO_CURRENT_ASSIGNMENT"} for code in reason_codes
    )
    applicable = relation_kind == CURRENT_STOCK_RELATION and clean_code != ""
    if not_applicable:
        applicable = False
    diagnostic_class = (
        "INTEGRITY_BLOCK"
        if integrity
        else "SAFETY_BLOCK"
        if safety
        else "NOT_APPLICABLE"
        if reason_codes
        else "NONE"
    )
    user_reasons = tuple(_unassign_user_reason(value, evidence) for value in reason_codes)
    review_needed = any(
        code
        in {
            "UNKNOWN_STATUS",
            "CURRENT_INSTANCE_MISMATCH",
            "ROUTINE_RELATION_MISMATCH",
            "STOCK_RUNTIME_MISSING",
            "STOCK_RUNTIME_READ_ERROR",
            "STOCK_RUNTIME_RELATION_BROKEN",
            "PENDING_INTEGRITY_UNKNOWN",
        }
        for code in reason_codes
    )
    return RoutineUnassignDecision(
        applicable=applicable,
        allowed=applicable and not reason_codes,
        primary_reason_code=reason_codes[0] if reason_codes else "",
        reason_codes=tuple(reason_codes),
        user_reasons=user_reasons,
        status_info=status_info,
        evidence=evidence,
        diagnostic_class=diagnostic_class,
        review_required=review_needed,
        event_required=applicable and bool(reason_codes),
    )


def routine_action_reasons_for_stock(code: str, name: str, allow_unassigned: bool = True) -> tuple[bool, dict[str, object]]:
    """
    루틴 지정/변경 가능 여부를 삭제/등록해제 안전 규칙에 맞춰 판정한다.

    허용:
    - 미등록 종목(allow_unassigned=True)
    - 보유 0 + 현재 미체결 0
    """
    info = routine_action_guard_info(code, name)
    reasons: list[str] = []
    routine_name = str(info.get("routine_name", "")).strip()
    raw_status = str(info.get("raw_status", "")).strip().upper()
    state = info.get("state")
    if not isinstance(state, dict):
        state = {"status": raw_status}

    stock_dir = info.get("stock_dir")
    if (
        (stock_dir is not None and is_review_protected_stock_dir(Path(stock_dir)))
        or is_review_required_state(state)
    ):
        reasons.append("검토관리")
        info["reasons"] = reasons
        return False, info

    if is_emergency_stopped_state(state):
        reasons.append("긴급정지")
        info["reasons"] = reasons
        return False, info

    if not routine_name:
        if allow_unassigned:
            info["reasons"] = []
            return True, info
        reasons.append("등록 루틴이 없습니다.")
        info["reasons"] = reasons
        return False, info

    if info.get("stock_dir") is None:
        # 기존 정책상 runtime이 아직 없으면 정지에 준해 루틴명 정리는 허용한다.
        info["reasons"] = []
        return True, info

    known_statuses = {
        "STOPPED",
        "STOP",
        "MONITORING",
        "WATCHING",
        "",
        "RUNNING",
        "STARTED",
        "AUTO",
        "TRADING",
        "SELL_ONLY",
        "EMERGENCY_STOP",
        "EMERGENCY_STOPPED",
    }

    if is_review_required_state(state):
        reasons.append("검토관리")
    elif raw_status not in known_statuses:
        LOGGER.error(
            "unexpected registration policy status: %s code=%s name=%s routine=%s",
            raw_status,
            code,
            name,
            routine_name,
        )
        reasons.append(UNEXPECTED_STATUS_REASON)

    holding_qty = safe_int_value(info.get("holding_qty"), 0)
    if holding_qty > 0:
        reasons.append(f"보유 {holding_qty}")

    buy_pending_qty = info.get("buy_pending_qty", 0)
    sell_pending_qty = info.get("sell_pending_qty", 0)
    if buy_pending_qty == "?" or sell_pending_qty == "?":
        reasons.append(PENDING_INTEGRITY_USER_REASON)
    else:
        if isinstance(buy_pending_qty, int) and buy_pending_qty > 0:
            reasons.append(f"매수미결 {buy_pending_qty}")
        if isinstance(sell_pending_qty, int) and sell_pending_qty > 0:
            reasons.append(f"매도미결 {sell_pending_qty}")

    info["reasons"] = reasons
    return not reasons, info

def classify_routine_assign_targets(stocks: list[tuple[str, str]]) -> tuple[list[tuple[str, str]], list[dict[str, object]]]:
    """루틴 지정 창으로 넘길 수 있는 종목과 차단 종목을 분리한다."""
    allowed: list[tuple[str, str]] = []
    blocked: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()

    for code, name in stocks:
        code = str(code).strip()
        name = str(name).strip()
        key = (code, name)
        if not code or not name or key in seen:
            continue
        seen.add(key)

        can_process, info = routine_action_reasons_for_stock(code, name, allow_unassigned=True)
        if can_process:
            allowed.append(key)
        else:
            blocked.append(info)

    return allowed, blocked


def can_unassign_active_routine_from_stock(code: str, name: str) -> tuple[bool, str, list[str]]:
    """
    종목등록설정 우클릭 '루틴 해제' 가능 여부를 반환한다.

    루틴 해제는 종목 자체는 유지하고 기초종목.txt의 루틴명만 제거한다.
    검토관리이거나 보유/미체결이 있는 종목은 기존 안전 정책에 따라 차단한다.
    """
    decision = routine_unassign_decision(code, name)
    persisted = decision.evidence.get("persisted_routine_fields", ())
    routine_name = str(persisted[0] if persisted else "").strip()
    return decision.allowed, routine_name, list(decision.user_reasons)
