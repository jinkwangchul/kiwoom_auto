# -*- coding: utf-8 -*-
"""
gui_review_required_window.py

검토관리창 및 검토관리 관련 공통 헬퍼.
- 검토관리 대상 수집
- 검토관리창 UI
- 복구/삭제/새로고침
- 검토관리 관련 변경 로그

주의:
- 자동매매설정창 본체와 ATS/환경설정 로직은 포함하지 않는다.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from tempfile import gettempdir

from PyQt5.QtCore import QItemSelectionModel, Qt
from PyQt5.QtGui import QBrush, QColor, QFontMetrics, QPalette
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from gui_common_utils import safe_int_value
from gui_order_utils import (
    DIRECTIONAL_NEUTRAL_COLOR,
    DIRECTIONAL_POSITIVE_COLOR,
    pending_order_side_quantities,
)
from gui_auto_trade_display import auto_trade_setting_badge_stylesheet
from gui_review_utils import normalized_review_reasons, safe_float_value
from gui_styles import (
    PLAIN_HEADER_GRID_COLOR_PROPERTY,
    PLAIN_HEADER_USE_TABLE_BODY_BACKGROUND_PROPERTY,
    REGISTERED_STOCK_STATUS_GRID_COLOR,
    apply_plain_table_header,
    registered_stock_status_table_stylesheet,
)
from gui_table_utils import next_sort_order
from gui_toast import show_toast
from gui_window_policy import (
    configure_persistent_feature_window,
    persistent_feature_owner,
)
from gui_operation_environment import (
    read_review_policy,
    write_long_term_holding_policy,
)
from account_funds_foundation import READY as ACCOUNT_FUNDS_READY
from event_journal_production import append_production_event
from runtime_io import read_json_dict
from stock_repository import repository as stock_repository_factory
from stock_long_hold_policy import long_hold_excludes_holding_review
from gui_auto_trade_runtime import write_state_json
from gui_auto_trade_integrity import (
    is_emergency_stopped_state as _common_is_emergency_stopped_state,
    is_review_required_state as _common_is_review_required_state,
    read_review_state_with_issue,
    operator_review_location,
    operator_review_reason,
)
from gui_auto_trade_utils import PENDING_INTEGRITY_USER_REASON
from stock_position_reconciliation_service import (
    STATUS_APPLIED as POSITION_STATUS_APPLIED,
    STATUS_BLOCKED_EVIDENCE as POSITION_STATUS_BLOCKED_EVIDENCE,
    STATUS_FAILED as POSITION_STATUS_FAILED,
    STATUS_NO_CHANGE as POSITION_STATUS_NO_CHANGE,
    reconcile_review_stock_position,
    state_file_sha256,
)
from legacy_close_reconciliation_service import (
    STATUS_BLOCKED_EVIDENCE as LEGACY_CLOSE_STATUS_BLOCKED_EVIDENCE,
    STATUS_COMPLETED as LEGACY_CLOSE_STATUS_COMPLETED,
    STATUS_FAILED as LEGACY_CLOSE_STATUS_FAILED,
    STATUS_NO_CHANGE as LEGACY_CLOSE_STATUS_NO_CHANGE,
    _active_close_evidence,
    reconcile_legacy_early_close_no_target,
)
from production_recovery_contract import (
    ACCOUNT_COLLECTING,
    ACCOUNT_COMPLETED,
    ACCOUNT_RECONCILING,
    BrokerAccountSnapshot,
    RecoverySessionIdentity,
)
from production_recovery_state_registry import production_recovery_registry
PROJECT_ROOT = Path(__file__).resolve().parent
CHANGELOG_PATH = PROJECT_ROOT / "PROJECT_CHANGELOG.txt"
ORDER_QUEUE_PATH = PROJECT_ROOT / "runtime" / "order_queue.json"
REVIEW_UNKNOWN_TEXT = "-"
REVIEW_TIME_UNRECORDED = "\ubbf8\uae30\ub85d"
REVIEW_DISPLAY_STATUS_UNRESOLVED = "\ubbf8\ud574\uacb0"
REVIEW_DISPLAY_STATUS_EMERGENCY_STOPPED = "\uae34\uae09\uc815\uc9c0"
REVIEW_RETURN_ALLOWED = "ALLOWED"
REVIEW_RETURN_BLOCKED = "BLOCKED"
REVIEW_REASON_OPERATION_DATA_MISSING = "\uc6b4\uc601 \ub370\uc774\ud130 \uc5c6\uc74c"
REVIEW_REASON_OPERATION_DATA_READ_ERROR = "\uc6b4\uc601 \ub370\uc774\ud130 \uc77d\uae30 \uc624\ub958"
REVIEW_DETECTION_EVENT_UNRECORDED = "\ubbf8\uae30\ub85d"
REVIEW_DETECTION_EVENT_STOCK_MANAGEMENT = "\uc885\ubaa9\uad00\ub9ac"
REVIEW_SOURCE_EMERGENCY_RELEASE = "\uae34\uae09\uc815\uc9c0\ud574\uc81c"
REVIEW_REPRODUCTION_MANIFEST_PREFIX = "review_required_library_cases_"
LONG_HOLD_BADGE_ACTIVE_COLOR = DIRECTIONAL_POSITIVE_COLOR
LONG_HOLD_BADGE_IDLE_COLOR = DIRECTIONAL_NEUTRAL_COLOR


def review_operator_readiness_evidence(owner) -> dict[str, str]:
    """Classify current account readiness for operator guidance without mutation."""
    api = getattr(owner, "kiwoom_api", None)
    connection_reader = getattr(api, "is_connected", None)
    if callable(connection_reader):
        try:
            if connection_reader() is not True:
                return {"cause": "SERVER_DISCONNECTED"}
        except Exception:
            return {"cause": "SERVER_DISCONNECTED"}

    account_reader = getattr(owner, "selected_account_no", None)
    account_no = ""
    if callable(account_reader):
        try:
            account_no = str(account_reader() or "").strip()
        except Exception:
            account_no = ""
        if not account_no:
            return {"cause": "ACCOUNT_NOT_SELECTED"}

    authentication_states = getattr(owner, "_account_authentication_states", None)
    query_states = getattr(owner, "_account_query_states", None)
    if account_no and isinstance(authentication_states, dict):
        if str(authentication_states.get(account_no, "") or "") != ACCOUNT_FUNDS_READY:
            return {"cause": "ACCOUNT_CHECK_INCOMPLETE"}
    if account_no and isinstance(query_states, dict):
        if str(query_states.get(account_no, "") or "") != ACCOUNT_FUNDS_READY:
            return {"cause": "ACCOUNT_CHECK_INCOMPLETE"}

    context = production_recovery_registry.snapshot()
    if context is None:
        return {"cause": "ACCOUNT_OPERATION_CHECK_INCOMPLETE"}

    login_reader = getattr(api, "login_session_id", None)
    login_session_id = ""
    if callable(login_reader):
        try:
            login_session_id = str(login_reader() or "").strip()
        except Exception:
            login_session_id = ""
    identity = getattr(context, "identity", None)
    if identity is not None and (
        (account_no and str(getattr(identity, "account_no", "") or "").strip() != account_no)
        or (
            login_session_id
            and str(getattr(identity, "login_session_id", "") or "").strip()
            != login_session_id
        )
        or str(getattr(identity, "trading_day", "") or "").strip()
        != datetime.now().date().isoformat()
    ):
        return {"cause": "RECOVERY_IDENTITY_MISMATCH"}

    status = str(getattr(context, "account_status", "") or "").strip().upper()
    if status in {ACCOUNT_COLLECTING, ACCOUNT_RECONCILING}:
        return {"cause": "ACCOUNT_OPERATION_CHECK_IN_PROGRESS"}
    return {"cause": "ACCOUNT_OPERATION_CHECK_INCOMPLETE"}


def _operator_readiness_guidance(evidence: dict[str, str] | None) -> tuple[str, str, str]:
    cause = str((evidence or {}).get("cause", "") or "").strip().upper()
    if cause == "SERVER_DISCONNECTED":
        return (
            "현재 서버에 연결되어 있지 않습니다.",
            "서버 연결 및 계좌 확인 후 상태재판정하십시오.",
            "서버 연결과 현재 계좌 상태 확인이 완료되어야 합니다.",
        )
    if cause == "ACCOUNT_NOT_SELECTED":
        return (
            "계좌가 선택되지 않았습니다.",
            "계좌를 선택한 뒤 상태를 다시 확인하십시오.",
            "현재 계좌가 선택되고 계좌 상태 확인이 완료되어야 합니다.",
        )
    if cause == "ACCOUNT_CHECK_INCOMPLETE":
        return (
            "계좌 확인이 완료되지 않았습니다.",
            "계좌 인증과 보유·주문 확인이 완료된 뒤 상태재판정하십시오.",
            "계좌 인증과 보유·주문 상태 확인이 완료되어야 합니다.",
        )
    if cause == "ACCOUNT_OPERATION_CHECK_IN_PROGRESS":
        return (
            "계좌 및 보유·주문 상태를 확인 중입니다.",
            "확인이 완료된 뒤 상태재판정하십시오.",
            "현재 계좌와 보유·주문 상태 확인이 완료되어야 합니다.",
        )
    if cause == "RECOVERY_IDENTITY_MISMATCH":
        return (
            "현재 로그인 또는 계좌 정보가 이전 확인 상태와 일치하지 않습니다.",
            "현재 계좌 상태를 다시 확인하십시오.",
            "현재 로그인·계좌 정보로 계좌 상태 확인이 완료되어야 합니다.",
        )
    return (
        "현재 계좌 및 운영 상태 확인이 완료되지 않았습니다.",
        "현재 계좌와 운영 상태를 확인한 뒤 상태재판정하십시오.",
        "현재 계좌 및 운영 상태 확인이 완료되어야 합니다.",
    )


def build_review_operator_guidance(
    row: dict[str, object] | None,
    availability_result: dict[str, object] | None = None,
    readiness_evidence: dict[str, str] | None = None,
) -> dict[str, str]:
    """Build read-only operator guidance from already collected Review evidence."""
    current = row if isinstance(row, dict) else {}
    availability = (
        availability_result if isinstance(availability_result, dict) else {}
    )
    availability_value = str(
        availability.get("availability", current.get("return_availability", ""))
        or ""
    ).strip().upper()
    raw_block_reason = str(
        availability.get("reason", current.get("return_block_reason", "")) or ""
    ).strip()
    reason_values = normalized_review_reasons([current.get("review_reason", "")])
    operator_reason_by_internal = {
        "EMERGENCY_STOP_ACTIVE": "긴급정지 활성",
        "ACTIVE_CLOSE_OR_LIQUIDATION": "청산 처리 중",
        "SERVER_MISMATCH": "운영 데이터 불일치",
        "RECOVERY_NOT_READY": "계좌 상태 확인 필요",
    }
    reason_values = [
        "계좌 상태 확인 필요"
        if reason == "복구 상태 오류"
        else operator_reason_by_internal.get(reason.upper(), reason)
        if not (reason.isupper() and "_" in reason)
        else operator_reason_by_internal.get(reason.upper(), "운영 상태 확인 필요")
        for reason in reason_values
    ]
    reason_text = " / ".join(reason_values) or REVIEW_UNKNOWN_TEXT
    location = str(current.get("review_location", "") or "").strip()
    location_text = location or REVIEW_DETECTION_EVENT_UNRECORDED
    display_status = str(current.get("display_status", "") or "").strip()
    detail_text = str(current.get("review_detail", "") or "").strip()
    evidence_text = " ".join(
        value
        for value in (raw_block_reason, detail_text, reason_text)
        if value
    )
    explicit_block_reason_codes = {
        "EMERGENCY_STOP_ACTIVE",
        "PENDING_ORDER_DATA_INTEGRITY",
        "ACTIVE_CLOSE_OR_LIQUIDATION",
        "RECOVERY_NOT_READY",
        "SERVER_MISMATCH",
    }
    classification_text = (
        raw_block_reason
        if raw_block_reason.upper() in explicit_block_reason_codes
        else evidence_text
    )
    classification_upper = classification_text.upper()
    explicit_emergency_active = (
        display_status == REVIEW_DISPLAY_STATUS_EMERGENCY_STOPPED
        or raw_block_reason.upper() == "EMERGENCY_STOP_ACTIVE"
    )
    holding_qty = safe_int_value(current.get("holding_qty"), 0)
    avg_price = safe_float_value(current.get("avg_price"), 0.0)

    if availability_value == REVIEW_RETURN_ALLOWED:
        return {
            "summary": "복귀 가능",
            "reason": reason_text,
            "review_location": location_text,
            "block_reason": REVIEW_UNKNOWN_TEXT,
            "operator_action": "복귀하면 STOPPED 상태가 되며 자동 운영은 시작되지 않습니다.",
            "resolution_condition": "현재 복귀 안전조건을 충족했습니다.",
        }

    if explicit_emergency_active:
        block_reason = "긴급정지가 활성 상태입니다."
        action = "먼저 긴급정지를 해제한 뒤 상태재판정하세요."
        condition = "긴급정지가 해제되고 복귀 가능으로 확인되어야 합니다."
    elif (
        REVIEW_REASON_OPERATION_DATA_MISSING in classification_text
        or "STATE.JSON 누락" in classification_upper
    ):
        block_reason = "운영 데이터가 없습니다."
        action = "종목 운영 데이터 확인이 필요합니다."
        condition = "운영 데이터가 정상적으로 확인되고 복귀 안전조건을 충족해야 합니다."
    elif (
        REVIEW_REASON_OPERATION_DATA_READ_ERROR in classification_text
        or "STATE.JSON 이상" in classification_upper
        or "읽기 오류" in classification_text
    ):
        block_reason = "운영 데이터를 읽을 수 없습니다."
        action = "state 데이터를 정상화한 뒤 상태재판정하세요."
        condition = "운영 데이터가 정상적으로 읽히고 복귀 안전조건을 충족해야 합니다."
    elif (
        "PENDING_ORDER_DATA_INTEGRITY" in classification_upper
        or "미체결 데이터 오류" in classification_text
        or "미체결 존재" in classification_text
        or "처리할 수 없는 종목" in classification_text
    ):
        block_reason = "미체결 상태를 확인할 수 없거나 주문이 진행 중입니다."
        action = "주문·체결 상태를 확인하고 데이터가 정상화된 뒤 상태재판정하세요."
        condition = "미체결 무결성이 정상이고 복귀 가능으로 확인되어야 합니다."
    elif "ACTIVE_CLOSE_OR_LIQUIDATION" in classification_upper or "청산 처리 중" in classification_text:
        block_reason = "청산 처리가 진행 중입니다."
        action = "청산 진행 및 주문 상태를 확인한 뒤 상태재판정하세요."
        condition = "청산 처리가 끝나고 복귀 가능으로 확인되어야 합니다."
    elif "RECOVERY" in classification_upper or "복구 상태 오류" in classification_text:
        block_reason, action, condition = _operator_readiness_guidance(
            readiness_evidence
        )
    elif "SERVER_MISMATCH" in classification_upper or "서버" in classification_text:
        block_reason = "서버와 Runtime 정보가 일치하지 않습니다."
        action = "서버와 Runtime 상태의 일치를 확인한 뒤 상태재판정하세요."
        condition = "서버와 Runtime이 일치하고 복귀 가능으로 확인되어야 합니다."
    elif (
        holding_qty > 0
        or avg_price > 0
        or "HOLDING" in classification_upper
        or "보유수량" in classification_text
        or "보유잔량" in classification_text
        or "평단" in classification_text
    ):
        block_reason = "보유잔량 또는 보유정보 확인이 필요합니다."
        action = "현재 보유 상태와 이후 처리 방침을 확인한 뒤 상태재판정하세요."
        condition = "보유 관련 복귀 안전조건을 충족해야 합니다."
    else:
        block_reason = "현재 복귀 안전조건을 충족하지 못했습니다."
        action = "표시된 검토 사유와 관련 운영 상태를 확인한 뒤 상태재판정하세요."
        condition = "복귀 가능으로 확인되어야 합니다."

    return {
        "summary": "정상화 확인 필요",
        "reason": reason_text,
        "review_location": location_text,
        "block_reason": block_reason,
        "operator_action": action,
        "resolution_condition": condition,
    }


def review_entered_at_display(state: dict[str, object]) -> str:
    """Read the persisted review transition time without inferring one."""
    entered_at = str(state.get("review_entered_at", "") or "").strip()
    return entered_at or REVIEW_TIME_UNRECORDED


def review_detection_event_display(source: object) -> str:
    """Return the operator-facing production event for the stored review source."""
    return operator_review_location(source, default=REVIEW_DETECTION_EVENT_UNRECORDED)


def _review_manifest_entered_at_display(raw: object) -> str:
    timestamp = str(raw or "").strip()
    if len(timestamp) == 15 and timestamp[8] == "_":
        date_part = timestamp[:8]
        time_part = timestamp[9:]
        if date_part.isdigit() and time_part.isdigit():
            return (
                f"{date_part[:4]}-{date_part[4:6]}-{date_part[6:8]} "
                f"{time_part[:2]}:{time_part[2:4]}:{time_part[4:6]}"
            )
    return timestamp or REVIEW_TIME_UNRECORDED


def _state_issue_review_record_from_manifest(
    code: str,
    name: str,
) -> dict[str, str]:
    """Read persisted visible-GUI reproduction metadata without mutating stock data."""

    temp_root = Path(gettempdir())
    candidates = sorted(
        temp_root.glob(f"{REVIEW_REPRODUCTION_MANIFEST_PREFIX}*.json"),
        reverse=True,
    )
    project_root_text = str(PROJECT_ROOT)
    for manifest_path in candidates:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if str(manifest.get("root", "") or "").strip() != project_root_text:
            continue
        cases = manifest.get("cases", [])
        if not isinstance(cases, list):
            continue
        for case in cases:
            if not isinstance(case, dict):
                continue
            if str(case.get("code", "") or "").strip() != code:
                continue
            case_name = str(case.get("name", "") or "").strip()
            if case_name and case_name != name:
                stock_dir_text = str(case.get("stock_dir", "") or "")
                if f"{code}_" not in stock_dir_text:
                    continue
            entered_at = (
                case.get("review_entered_at")
                or case.get("created_at")
                or manifest.get("created_at")
            )
            return {
                "review_location": REVIEW_DETECTION_EVENT_STOCK_MANAGEMENT,
                "review_entered_at": _review_manifest_entered_at_display(entered_at),
            }
    return {
        "review_location": REVIEW_DETECTION_EVENT_STOCK_MANAGEMENT,
        "review_entered_at": REVIEW_TIME_UNRECORDED,
    }


def get_routine_dirs() -> list[Path]:
    """
    호환용 루틴 path 조회.

    신규 기준은 routines/*/routine.json이며, 기존 _루틴폴더/budget.json은
    gui_routine_registry의 fallback 정책에만 맡긴다.
    이 함수는 더 이상 루틴폴더 내부 종목폴더를 전제로 하지 않는다.
    """
    try:
        from gui_routine_registry import get_routine_dirs as registry_get_routine_dirs
        return registry_get_routine_dirs()
    except Exception:
        return []


def routine_display_name(routine_dir: Path) -> str:
    """호환용 루틴 표시명 반환. 신규 루틴 패키지 routine.json을 우선한다."""
    try:
        from gui_routine_registry import routine_display_name as registry_routine_display_name
        return registry_routine_display_name(Path(routine_dir))
    except Exception:
        return Path(routine_dir).name.lstrip("_").strip()

def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def unique_review_reasons(reasons) -> list[str]:
    """검토 사유 목록에서 빈값/중복을 제거하고 입력 순서를 유지한다."""
    result: list[str] = []
    seen: set[str] = set()

    for reason in reasons:
        text = str(reason).strip()
        if not text:
            continue
        if text in seen:
            continue
        seen.add(text)
        result.append(text)

    return result


def is_review_required_state(state: dict[str, object] | None) -> bool:
    """검토관리 전용 분리 판정.

    자동매매설정 창에서는 이 조건에 걸린 종목을 절대 표시하지 않는다.
    검토관리 창에서는 이 조건에 걸린 종목만 표시한다.
    """
    return _common_is_review_required_state(state)


def is_review_required_stock_dir(stock_dir: Path) -> bool:
    """runtime 폴더 기준 검토관리 전용 종목 여부."""
    try:
        state = read_json_dict(stock_dir / "state.json")
    except Exception:
        return False
    return is_review_required_state(state)


def _read_central_review_state(state_path: Path) -> tuple[dict[str, object], str]:
    return read_review_state_with_issue(state_path)


def _review_display_status_for_collected_row(
    state: dict[str, object] | None,
    *,
    state_issue_reason: str = "",
    review_location_source: object = "",
    holding_qty: int = 0,
    avg_price: float = 0.0,
    buy_pending_qty: object = 0,
    sell_pending_qty: object = 0,
    return_availability: object = "",
) -> str:
    """Return the review-management status display for a collected row."""
    if state_issue_reason:
        return REVIEW_DISPLAY_STATUS_UNRESOLVED
    if not isinstance(state, dict) or not state:
        return REVIEW_DISPLAY_STATUS_UNRESOLVED

    emergency_reason = str(state.get("emergency_reason", "") or "").strip()
    emergency_stopped_at = str(state.get("emergency_stopped_at", "") or "").strip()
    source = str(review_location_source or state.get("review_location", "") or "").strip()
    emergency_scope = str(state.get("emergency_scope", "") or "").strip().upper()
    if _common_is_emergency_stopped_state(state):
        if emergency_scope != "SELECTED":
            return REVIEW_DISPLAY_STATUS_EMERGENCY_STOPPED
    if (
        (emergency_reason or emergency_stopped_at)
        and emergency_scope != "SELECTED"
        and source != REVIEW_SOURCE_EMERGENCY_RELEASE
    ):
        return REVIEW_DISPLAY_STATUS_EMERGENCY_STOPPED

    availability = str(return_availability or "").strip().upper()
    return "\ud574\uacb0" if availability == REVIEW_RETURN_ALLOWED else REVIEW_DISPLAY_STATUS_UNRESOLVED


def append_changelog(change_type: str, filename: str, message: str) -> None:
    """
    GUI 조작으로 발생한 변경사항을 PROJECT_CHANGELOG.txt 에 기록한다.
    """
    block = (
        f"\n[{now_text()}]\n"
        f"버전: v1.1\n"
        f"구분: {change_type}\n"
        f"파일: {filename}\n"
        f"내용: {message}\n"
        f"작성자: admin\n"
    )

    with CHANGELOG_PATH.open("a", encoding="utf-8") as file:
        file.write(block)


def append_stock_log(stock_dir: Path, event_type: str, message: str) -> Path | None:
    """
    종목별 logs/YYYYMMDD.log 에 GUI 조작 및 상태 변경 내역을 기록한다.

    주의:
    - 실제 키움 주문/체결 로그가 아니라 관리자 GUI 조작 로그이다.
    - logs 폴더가 없으면 생성한다.
    - 기록 실패는 GUI 흐름을 막지 않도록 조용히 무시한다.
    """
    try:
        logs_dir = stock_dir / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        log_path = logs_dir / f"{datetime.now().strftime('%Y%m%d')}.log"
        line = f"[{now_text()}] [{event_type}] {message}"
        with log_path.open("a", encoding="utf-8") as file:
            file.write(line + "\n")
        return log_path
    except Exception:
        return None


def append_review_normalization_event(
    *,
    event_type: str,
    source: str,
    stock_dir: Path,
    code: str,
    name: str,
    destination: str,
    state_before: dict[str, object],
    result: dict[str, object],
) -> dict[str, object]:
    """Record one finalized review action without participating in its mutation."""
    status = str(result.get("status", "") or "")
    journal_result = {
        "NORMALIZED": "COMPLETED",
        "BLOCKED": "BLOCKED",
    }.get(status, "FAILED")
    severity = {
        "COMPLETED": "INFO",
        "BLOCKED": "WARNING",
        "FAILED": "ERROR",
    }[journal_result]
    state_after = read_json_dict(Path(stock_dir) / "state.json")
    details: dict[str, object] = {"destination": destination}
    before_status = str(state_before.get("status", "") or "").strip()
    after_status = str(state_after.get("status", "") or "").strip()
    previous_routine = str(
        state_before.get("active_routine")
        or state_before.get("routine_name")
        or state_before.get("review_routine")
        or ""
    ).strip()
    review_reason = str(state_before.get("review_reason", "") or "").strip()
    result_reason = str(result.get("reason", "") or "").strip()
    restored_routine = str(result.get("routine_name", "") or "").strip()
    if before_status:
        details["before_status"] = before_status
    if after_status:
        details["after_status"] = after_status
    if previous_routine:
        details["previous_routine"] = previous_routine
    if review_reason:
        details["review_reason"] = review_reason
    if result_reason:
        details["reason"] = result_reason
    if destination == "RESTORE" and restored_routine:
        details["restored_routine"] = restored_routine
    if destination == "UNASSIGNED" and journal_result == "COMPLETED":
        details["routine_link_released"] = True

    return append_production_event(
        event_type,
        severity=severity,
        result=journal_result,
        source=source,
        template_args={"stock_name": name},
        target_type="STOCK",
        target_id=code,
        target_name=name,
        stock_code=code,
        stock_name=name,
        details=details,
    )


def append_review_force_reset_event(
    *,
    stock_dir: Path,
    code: str,
    name: str,
    state_before: dict[str, object],
    result: str,
    reason: str = "",
    delete_target_verified: bool,
) -> dict[str, object]:
    """Record the one finalized local-project disposal outcome for a stock."""
    severity = {
        "COMPLETED": "INFO",
        "BLOCKED": "WARNING",
        "FAILED": "ERROR",
    }[result]
    details: dict[str, object] = {
        "delete_target_verified": delete_target_verified,
        "post_delete_verified": result == "COMPLETED",
        "local_holding_qty": safe_int_value(state_before.get("holding_qty"), 0),
    }
    for key, value in (
        ("before_status", state_before.get("status")),
        ("review_status", state_before.get("review_status")),
        ("review_reason", state_before.get("review_reason")),
        (
            "previous_routine",
            state_before.get("active_routine")
            or state_before.get("routine_name")
            or state_before.get("review_routine"),
        ),
    ):
        clean_value = str(value or "").strip()
        if clean_value:
            details[key] = clean_value
    if reason:
        details["reason"] = reason
    if result == "COMPLETED":
        details["final_state"] = "UNREGISTERED"

    return append_production_event(
        "REVIEW_FORCE_RESET",
        severity=severity,
        result=result,
        source="GlobalReviewRequiredWindow.delete_selected_review_items",
        template_args={"stock_name": name},
        target_type="STOCK",
        target_id=code,
        target_name=name,
        stock_code=code,
        stock_name=name,
        details=details,
    )


def update_base_stock_routines(code: str, name: str, routines: list[str]) -> bool:
    """
    중앙 stocks/config.json 기준으로 종목의 루틴 연결을 갱신한다.

    과거 기초종목.txt 갱신 방식은 루틴 패키지 전환 이후 사용하지 않는다.
    """
    try:
        repo = stock_repository_factory()
        return repo.update_stock_routine(code, name, routines)
    except Exception:
        return False

def parse_stock_folder_name(folder_name: str) -> tuple[str, str]:
    """
    종목 폴더명에서 종목코드와 종목명을 분리한다.
    예: 005930_삼성전자 -> ("005930", "삼성전자")
    """
    parts = folder_name.split("_", 1)
    if len(parts) != 2:
        return "", folder_name.strip()
    return parts[0].strip(), parts[1].strip()


def get_stock_dirs_in_routine(routine_dir: Path) -> list[Path]:
    """
    호환용 종목 조회.

    과거에는 루틴폴더 아래 종목폴더를 조회했지만, 현재 기준 종목 원본은
    중앙 stocks/이며 루틴 연결은 각 종목 config.json의 routine 값으로 판단한다.
    """
    routine_name = routine_display_name(routine_dir)
    try:
        repo = stock_repository_factory()
        result: list[Path] = []
        for record in repo.list_stocks():
            if str(record.routine or "").strip() != routine_name:
                continue
            stock_dir = repo.resolve_stock_dir(record.code, record.name)
            if stock_dir.exists() and stock_dir.is_dir():
                result.append(stock_dir)
        return sorted(result, key=lambda path: path.name)
    except Exception:
        return []

def auto_trade_setting_data_inconsistency_reasons(state: dict[str, object] | None) -> list[str]:
    """운영 중/재시작/자동 로컬 무결성검사 공통 내부 데이터 불일치 판정.

    주의:
    - holding_qty/current_qty/qty 계열은 수량으로 본다.
    - holding_amount 계열은 수량이 아니라 보유금액/평가금액 계열로 본다.
    - 보유수량 0인데 평단 또는 보유금액이 남아 있으면 비정상으로 본다.
    """
    if not isinstance(state, dict):
        return ["state.json 형식 이상"]

    reasons: list[str] = []

    def present(key: str) -> bool:
        return key in state and state.get(key) not in (None, "")

    def number_value(key: str, default: float = 0.0) -> tuple[float, bool]:
        if not present(key):
            return default, False
        value = state.get(key)
        try:
            if isinstance(value, str):
                value = value.replace(",", "").strip()
            return float(value), True
        except Exception:
            reasons.append(f"{key} 숫자 형식 오류")
            return default, True

    qty_keys = [
        "holding_qty",
        "current_qty",
        "current_quantity",
        "qty",
        "balance_qty",
        "position_qty",
    ]
    amount_keys = [
        "holding_amount",
        "holding_value",
        "holding_eval_amount",
        "position_amount",
        "stock_value",
    ]
    avg_keys = [
        "avg_price",
        "average_price",
        "avg_buy_price",
        "buy_avg_price",
        "average_buy_price",
    ]

    qty_values: dict[str, float] = {}
    amount_values: dict[str, float] = {}
    avg_values: dict[str, float] = {}

    for key in qty_keys:
        value, exists = number_value(key)
        if exists:
            qty_values[key] = value
            if value < 0:
                reasons.append(f"{key} 음수")

    for key in amount_keys:
        value, exists = number_value(key)
        if exists:
            amount_values[key] = value
            if value < 0:
                reasons.append(f"{key} 음수")

    for key in avg_keys:
        value, exists = number_value(key)
        if exists:
            avg_values[key] = value
            if value < 0:
                reasons.append(f"{key} 음수")

    primary_qty = qty_values.get("holding_qty", 0.0)
    if primary_qty == 0:
        positive_qtys = [value for value in qty_values.values() if value > 0]
        if positive_qtys:
            primary_qty = max(positive_qtys)

    primary_avg = avg_values.get("avg_price", 0.0)
    if primary_avg == 0:
        positive_avgs = [value for value in avg_values.values() if value > 0]
        if positive_avgs:
            primary_avg = max(positive_avgs)

    primary_amount = amount_values.get("holding_amount", 0.0)
    if primary_amount == 0:
        positive_amounts = [value for value in amount_values.values() if value > 0]
        if positive_amounts:
            primary_amount = max(positive_amounts)

    positive_qty_pairs = {key: value for key, value in qty_values.items() if value > 0}
    if len(set(positive_qty_pairs.values())) > 1:
        reasons.append("보유수량 필드 불일치")

    if primary_qty <= 0 and primary_avg > 0:
        reasons.append("보유 0인데 평단 존재")
    if primary_qty <= 0 and primary_amount > 0:
        reasons.append("보유 0인데 보유금액 존재")
    if primary_qty > 0 and primary_avg <= 0:
        reasons.append("보유 존재인데 평단 없음")

    return unique_review_reasons(reasons)


def auto_trade_setting_server_mismatch_detected(state: dict[str, object] | None) -> bool:
    """키움 서버 정보와 프로그램 내부 정보 불일치/서버 불안 표시 여부.

    실제 키움 연동 단계에서 아래 플래그 중 하나가 저장되면 현황을 빨강으로 표시한다.
    빨강은 자동 검토관리 이동이 아니라 긴급정지/무결성 확인 대상이라는 뜻이다.
    """
    if not isinstance(state, dict):
        return False

    if auto_trade_setting_data_inconsistency_reasons(state):
        return True

    bool_keys = {
        "server_mismatch",
        "kiwoom_mismatch",
        "server_data_mismatch",
        "kiwoom_data_mismatch",
        "data_mismatch",
        "server_unstable",
        "kiwoom_server_unstable",
    }
    for key in bool_keys:
        value = state.get(key)
        if isinstance(value, bool) and value:
            return True
        if str(value or "").strip().lower() in {"true", "1", "yes", "y", "on"}:
            return True

    status_keys = {
        "kiwoom_sync_status",
        "server_sync_status",
        "reconciliation_status",
        "server_status",
    }
    danger_values = {"MISMATCH", "UNSTABLE", "ERROR", "FAILED", "FAIL", "UNKNOWN"}
    for key in status_keys:
        if str(state.get(key, "")).strip().upper() in danger_values:
            return True

    return False


def collect_global_review_required_rows(availability_window=None) -> list[dict[str, object]]:
    """
    프로그램 전체 단위 검토관리 대상 목록을 중앙 stocks/ 기준으로 수집한다.

    정책:
    - 검토관리의 진실 원본은 stocks/<종목>/state.json 이다.
    - 루틴 패키지 폴더나 구형 _루틴폴더 내부 종목폴더는 조회하지 않는다.
    - 루틴명은 stocks/<종목>/config.json의 연결값을 우선하고, 없으면 state의 review_routine을 보조로 표시한다.
    """
    rows: list[dict[str, object]] = []
    seen_keys: set[tuple[str, str, str]] = set()
    global_long_hold_enabled = bool(
        read_review_policy().get("long_term_holding_enabled", False)
    )

    try:
        repo = stock_repository_factory()
        records = repo.list_stocks()
    except Exception:
        records = []

    for record in records:
        code = str(record.code or "").strip()
        name = str(record.name or "").strip()
        stock_dir = repo.resolve_stock_dir(code, name)
        state, state_issue_reason = _read_central_review_state(stock_dir / "state.json")
        routine_name = str(record.routine or state.get("review_routine", "") or "-").strip() or "-"

        if state_issue_reason:
            if not str(record.routine or "").strip():
                continue
            state_issue_record = _state_issue_review_record_from_manifest(code, name)
            holding_qty = 0
            avg_price = 0.0
            buy_pending_qty = 0
            sell_pending_qty = 0
            review_location = state_issue_record["review_location"]
            review_entered_at = state_issue_record["review_entered_at"]
            return_availability = REVIEW_RETURN_BLOCKED
            availability_reason = state_issue_reason
            display_status = _review_display_status_for_collected_row(
                None,
                state_issue_reason=state_issue_reason,
                return_availability=return_availability,
            )
        elif not is_review_required_state(state):
            continue
        else:
            holding_qty = safe_int_value(state.get("holding_qty"), 0)
            avg_price = safe_float_value(state.get("avg_price"), 0.0)
            buy_pending_qty, sell_pending_qty = pending_order_side_quantities(stock_dir, state)
            safety_issue = bool(
                auto_trade_setting_data_inconsistency_reasons(state)
                or auto_trade_setting_server_mismatch_detected(state)
            )
            if long_hold_excludes_holding_review(
                global_long_hold_enabled,
                state,
                holding_qty=holding_qty,
                buy_pending_qty=buy_pending_qty,
                sell_pending_qty=sell_pending_qty,
                safety_issue=safety_issue,
            ):
                continue
            review_location_source = state.get("review_location", "") or REVIEW_UNKNOWN_TEXT
            review_location = review_detection_event_display(review_location_source)
            review_entered_at = review_entered_at_display(state)

            from gui_main_emergency_ops import review_return_availability

            availability = review_return_availability(
                availability_window,
                stock_dir,
                code,
                state=state,
            )
            return_availability = str(
                availability.get("availability", REVIEW_RETURN_BLOCKED)
                or REVIEW_RETURN_BLOCKED
            )
            availability_reason = str(availability.get("reason", "") or "")
            display_status = _review_display_status_for_collected_row(
                state,
                review_location_source=review_location_source,
                holding_qty=holding_qty,
                avg_price=avg_price,
                buy_pending_qty=buy_pending_qty,
                sell_pending_qty=sell_pending_qty,
                return_availability=return_availability,
            )

        key = (routine_name, code, name)
        if key in seen_keys:
            continue
        seen_keys.add(key)

        review_reasons = normalized_review_reasons(
            [
                state_issue_reason,
                state.get("review_reason", ""),
            ]
        )
        rows.append({
            "routine_name": routine_name,
            "stock_dir": stock_dir,
            "code": code,
            "name": name,
            "review_location": review_location,
            "review_reason": " / ".join(review_reasons) or REVIEW_UNKNOWN_TEXT,
            "review_entered_at": review_entered_at,
            "last_checked_at": str(state.get("review_checked_at", "") or state.get("updated_at", "") or "-").strip() or "-",
            "holding_qty": holding_qty,
            "avg_price": avg_price,
            "buy_pending_qty": buy_pending_qty,
            "sell_pending_qty": sell_pending_qty,
            "display_status": display_status,
            "return_availability": return_availability,
            "return_block_reason": availability_reason,
            "emergency_scope": str(state.get("emergency_scope", "") or "").strip().upper(),
        })

    rows.sort(key=lambda row: (str(row.get("review_entered_at", "")), str(row.get("routine_name", "")), str(row.get("code", ""))))
    return rows

class GlobalReviewRequiredWindow(QDialog):
    """프로그램 전체 단위 검토종목 통합 관리창."""

    def __init__(
        self,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(None)
        configure_persistent_feature_window(self, parent)
        self.setWindowTitle("검토종목 관리")
        self.resize(1320, 620)

        self.summary_label = QLabel("검토종목: 0개")
        self.table = QTableWidget()
        self.btn_return = QPushButton("복귀")
        self.btn_unassign = QPushButton("미지정")
        self.btn_delete = QPushButton("강제초기화")
        self.btn_refresh = QPushButton("상태재판정")
        self.btn_close = QPushButton("닫기")
        self.long_hold_toggle_button = QPushButton("장기보유 OFF")
        self.long_hold_toggle_button.setObjectName("reviewLongHoldToggle")
        self.long_hold_toggle_button.setCursor(Qt.PointingHandCursor)
        self.long_hold_toggle_button.setToolTip("전체 검토 판정의 장기보유 ON/OFF 전환")
        self.operator_guidance_label = QLabel("종목을 선택하세요.")
        self.operator_guidance_label.setObjectName("reviewOperatorGuidance")
        self.operator_guidance_label.setWordWrap(True)
        self.operator_guidance_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.operator_guidance_label.setMinimumHeight(82)
        self._review_rows_by_stock_dir: dict[str, dict[str, object]] = {}
        self.last_position_reconciliation_result: dict[str, object] = {}
        self.last_legacy_close_reconciliation_result: dict[str, object] = {}
        self._review_sort_column = -1
        self._review_sort_order = Qt.AscendingOrder

        self._setup_ui()
        self._connect_events()
        self.refresh_review_items()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout()
        top_layout = QHBoxLayout()
        top_layout.addWidget(self.summary_label)
        top_layout.addStretch(1)
        self.btn_refresh.setMinimumWidth(100)
        top_layout.addWidget(self.btn_refresh)
        layout.addLayout(top_layout)

        headers = [
            "코드",
            "종목",
            "위치",
            "상태",
            "시간",
            "사유",
            "검출",
        ]
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        detection_header_item = self.table.horizontalHeaderItem(6)
        if detection_header_item is not None:
            detection_header_item.setTextAlignment(Qt.AlignCenter)
        apply_plain_table_header(self.table)
        header = self.table.horizontalHeader()
        self.table.setObjectName("reviewRequiredTable")
        header.setProperty(PLAIN_HEADER_USE_TABLE_BODY_BACKGROUND_PROPERTY, True)
        header.setProperty(
            PLAIN_HEADER_GRID_COLOR_PROPERTY,
            REGISTERED_STOCK_STATUS_GRID_COLOR,
        )
        # Keep the six predictable fields stable and let the variable-length
        # reason consume only the remaining viewport.  Row reloads must not
        # recalculate geometry or push the final detection column off-screen.
        for column in (0, 1, 2, 3, 4, 6):
            header.setSectionResizeMode(column, QHeaderView.Interactive)
        header.setSectionResizeMode(5, QHeaderView.Stretch)
        header.setStretchLastSection(False)
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(True)
        self.table.setColumnWidth(0, 75)    # 코드
        self.table.setColumnWidth(1, 160)   # 종목
        self.table.setColumnWidth(2, 140)   # 위치
        self.table.setColumnWidth(3, 90)    # 상태
        self.table.setColumnWidth(4, 360)   # 시간
        self.table.setColumnWidth(6, 140)   # 검출
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.verticalHeader().hide()
        self.table.setShowGrid(True)
        self.table.setGridStyle(Qt.SolidLine)
        self.table.setAlternatingRowColors(False)
        body_background = self.table.viewport().palette().color(QPalette.Base).name()
        self.table.setStyleSheet(
            registered_stock_status_table_stylesheet(
                self.table.objectName(),
                body_background,
            )
        )
        layout.addWidget(self.table)
        self.operator_guidance_label.setStyleSheet(
            "QLabel#reviewOperatorGuidance {"
            " border: 1px solid #d6dbe3;"
            " padding: 5px 7px;"
            " background: #ffffff;"
            " color: #000000;"
            " }"
        )
        layout.addWidget(self.operator_guidance_label)

        buttons = QHBoxLayout()
        badge_metrics = QFontMetrics(self.long_hold_toggle_button.font())
        badge_width = max(
            badge_metrics.horizontalAdvance("장기보유 ON"),
            badge_metrics.horizontalAdvance("장기보유 OFF"),
        ) + 16
        self.long_hold_toggle_button.setFixedWidth(badge_width)
        self.long_hold_toggle_button.setFixedHeight(
            self.btn_return.sizeHint().height()
        )
        buttons.addWidget(self.long_hold_toggle_button)
        buttons.addStretch(1)
        self.btn_return.setMinimumWidth(90)
        self.btn_unassign.setMinimumWidth(90)
        self.btn_delete.setMinimumWidth(90)
        self.btn_close.setMinimumWidth(100)
        buttons.addWidget(self.btn_return)
        buttons.addWidget(self.btn_unassign)
        buttons.addWidget(self.btn_delete)
        buttons.addWidget(self.btn_close)
        layout.addLayout(buttons)
        self.setLayout(layout)

    def _connect_events(self) -> None:
        self.btn_return.clicked.connect(self.return_selected_items_to_auto_list)
        self.btn_unassign.clicked.connect(self.unassign_selected_review_items)
        self.btn_delete.clicked.connect(self.delete_selected_review_items)
        self.btn_refresh.clicked.connect(self.refresh_review_items)
        self.btn_close.clicked.connect(self.close)
        self.long_hold_toggle_button.clicked.connect(
            self.toggle_long_term_holding_policy
        )
        self.table.horizontalHeader().sectionClicked.connect(self.sort_review_table_by_column)
        self.table.customContextMenuRequested.connect(self.show_review_table_context_menu)
        self.table.itemSelectionChanged.connect(self.refresh_operator_guidance)

    def sort_review_table_by_column(self, column: int) -> None:
        """검토관리 표 헤더 클릭 정렬."""
        self._review_sort_order = next_sort_order(
            self._review_sort_column,
            column,
            self._review_sort_order,
        )
        self._review_sort_column = column
        self.table.sortItems(column, self._review_sort_order)
        self.table.horizontalHeader().setSortIndicator(column, self._review_sort_order)

    def _apply_saved_review_sort(self) -> None:
        if 0 <= self._review_sort_column < self.table.columnCount():
            self.table.sortItems(self._review_sort_column, self._review_sort_order)
            self.table.horizontalHeader().setSortIndicator(
                self._review_sort_column,
                self._review_sort_order,
            )

    def show_review_table_context_menu(self, position) -> None:
        """검토관리 표 우클릭 메뉴."""
        menu = QMenu(self)
        action_select_all = menu.addAction("전체 선택")
        action_clear_all = menu.addAction("전체 해제")
        selected_action = menu.exec_(self.table.viewport().mapToGlobal(position))

        if selected_action == action_select_all:
            self.table.selectAll()
        elif selected_action == action_clear_all:
            self.table.clearSelection()

    def _set_item(
        self,
        row: int,
        col: int,
        text: object,
        align=Qt.AlignCenter,
        tooltip: str = "",
    ) -> None:
        item = QTableWidgetItem(str(text if text is not None else "-"))
        item.setTextAlignment(align)
        if tooltip:
            item.setToolTip(tooltip)
        item.setBackground(QBrush(QColor("#ffffff")))
        item.setForeground(QBrush(QColor("#000000")))
        self.table.setItem(row, col, item)

    def _review_row_tooltip(self, row: dict[str, object]) -> str:
        """검토관리 종목 행에 표시할 상세 툴팁."""
        code = str(row.get("code", "-") or "-").strip() or "-"
        name = str(row.get("name", "-") or "-").strip() or "-"
        routine = str(row.get("routine_name", "-") or "-").strip() or "-"
        location = str(row.get("review_location", "-") or "-").strip() or "-"
        display_status = str(
            row.get("display_status", row.get("return_availability", "-")) or "-"
        ).strip() or "-"
        reason = str(row.get("review_reason", "-") or "-").strip() or "-"
        entered_at = str(row.get("review_entered_at", "-") or "-").strip() or "-"
        return (
            f"코드: {code}\n"
            f"종목명: {name}\n"
            f"현재위치: {routine}\n"
            f"검토위치: {location}\n"
            f"상태: {display_status}\n"
            f"사유: {reason}\n"
            f"검토 전환 시각: {entered_at}"
        )


    def _central_review_rows(self) -> list[dict[str, object]]:
        return collect_global_review_required_rows(self)

    def refresh_operator_guidance(self) -> None:
        """Refresh the read-only guidance for the first selected Review row."""
        selected_rows = self.table.selectionModel().selectedRows()
        if not selected_rows:
            self.btn_return.setEnabled(False)
            self.btn_unassign.setEnabled(False)
            self.operator_guidance_label.setText("종목을 선택하세요.")
            return
        selected_projection_rows: list[dict[str, object]] = []
        for selected_index in selected_rows:
            selected_item = self.table.item(selected_index.row(), 0)
            selected_path = (
                str(selected_item.data(Qt.UserRole) or "") if selected_item else ""
            )
            selected_row = self._review_rows_by_stock_dir.get(selected_path)
            if isinstance(selected_row, dict):
                selected_projection_rows.append(selected_row)
        has_allowed_selection = any(
            str(row.get("return_availability", "") or "").strip().upper()
            == REVIEW_RETURN_ALLOWED
            for row in selected_projection_rows
        )
        self.btn_return.setEnabled(has_allowed_selection)
        self.btn_unassign.setEnabled(has_allowed_selection)
        first_item = self.table.item(selected_rows[0].row(), 0)
        stock_dir_text = str(first_item.data(Qt.UserRole) or "") if first_item else ""
        row = self._review_rows_by_stock_dir.get(stock_dir_text)
        if not isinstance(row, dict):
            self.operator_guidance_label.setText("선택한 종목의 안내 정보를 확인할 수 없습니다.")
            return
        guidance = build_review_operator_guidance(
            row,
            readiness_evidence=review_operator_readiness_evidence(
                persistent_feature_owner(self)
            ),
        )
        guidance_lines = [guidance["summary"]]
        if guidance["block_reason"] != REVIEW_UNKNOWN_TEXT:
            guidance_lines.append(f"복귀 차단: {guidance['block_reason']}")
        guidance_lines.extend(
            [
                f"운영자 조치: {guidance['operator_action']}",
                f"해결 조건: {guidance['resolution_condition']}",
            ]
        )
        self.operator_guidance_label.setText("\n".join(guidance_lines))

    def _auto_reconciliation_targets(
        self,
        rows: list[dict[str, object]],
    ) -> list[tuple[Path, str]]:
        """Return persisted Review rows eligible for proven-safe preparation.

        This preparation boundary is deliberately separate from the Collector:
        the Collector stays a read-only projection. GLOBAL/unknown emergency
        ownership remains excluded. SELECTED is only Review provenance and is
        therefore eligible for the same safe preparation as other Review rows.
        """
        stocks_root = (PROJECT_ROOT / "stocks").resolve()
        targets: list[tuple[Path, str]] = []
        for row in rows:
            stock_dir_value = row.get("stock_dir")
            code = str(row.get("code", "") or "").strip()
            if not code or not stock_dir_value:
                continue
            try:
                stock_dir = Path(stock_dir_value).resolve(strict=True)
                stock_dir.relative_to(stocks_root)
                state = json.loads((stock_dir / "state.json").read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(state, dict) or not _common_is_review_required_state(state):
                continue
            scope = str(state.get("emergency_scope", "") or "").strip().upper()
            selected_provenance = scope == "SELECTED"
            if (
                (_common_is_emergency_stopped_state(state) and not selected_provenance)
                or (scope and not selected_provenance)
                or (
                    (
                        str(state.get("emergency_reason", "") or "").strip()
                        or str(state.get("emergency_stopped_at", "") or "").strip()
                    )
                    and not selected_provenance
                )
                or _active_close_evidence(state)
            ):
                continue
            targets.append((stock_dir, code))
        return targets

    def _prepare_safe_review_reconciliation(self) -> None:
        """Use already-completed evidence only; never return a Review row.

        LOCAL_SAFE currently has no writer-backed inference path: a terminal
        ``EARLY_CLOSE_NO_TARGET`` residue is already canonical and is therefore
        intentionally a no-write outcome even when offline.  BROKER_SAFE uses
        the existing completed Recovery handoff and canonical services only.
        """
        rows = self._central_review_rows()
        targets = self._auto_reconciliation_targets(rows)
        self.last_position_reconciliation_result = {
            "status": POSITION_STATUS_NO_CHANGE,
            "reason": "LOCAL_SAFE_NO_WRITER_REQUIRED",
            "service_calls": 0,
        }
        self.last_legacy_close_reconciliation_result = {
            "status": LEGACY_CLOSE_STATUS_NO_CHANGE,
            "reason": "LOCAL_SAFE_NO_WRITER_REQUIRED",
            "service_calls": 0,
        }
        handoff = self._completed_recovery_handoff()
        if handoff is None or not targets:
            return

        identity = handoff.get("identity")
        snapshot = handoff.get("snapshot")
        if not isinstance(identity, RecoverySessionIdentity) or not isinstance(
            snapshot, BrokerAccountSnapshot
        ):
            return

        position_results: list[dict[str, object]] = []
        for stock_dir, code in targets:
            try:
                state_sha = state_file_sha256(stock_dir / "state.json")
                result = reconcile_review_stock_position(
                    stock_dir=stock_dir,
                    stock_code=code,
                    recovery_identity=identity,
                    completed_recovery_identity=identity,
                    broker_snapshot=snapshot,
                    expected_account_no=identity.account_no,
                    expected_trading_day=identity.trading_day,
                    expected_login_session_id=identity.login_session_id,
                    expected_recovery_session_id=identity.recovery_session_id,
                    completed_recovery_status=str(handoff.get("recovery_status", "") or ""),
                    holdings_complete=handoff.get("holdings_complete") is True,
                    open_orders_complete=handoff.get("open_orders_complete") is True,
                    expected_state_sha256=state_sha,
                    order_queue_path=ORDER_QUEUE_PATH,
                )
            except Exception as exc:
                result = {
                    "status": POSITION_STATUS_FAILED,
                    "reason": f"SERVICE_CALL_FAILED:{type(exc).__name__}",
                    "stock_code": code,
                }
            position_results.append(dict(result))
        self.last_position_reconciliation_result = {
            "status": "COMPLETED",
            "service_calls": len(position_results),
            "results": position_results,
        }

        legacy_handoff = self._legacy_close_recovery_handoff()
        if legacy_handoff is None:
            return
        legacy_identity = legacy_handoff.get("identity")
        legacy_snapshot = legacy_handoff.get("snapshot")
        if not isinstance(legacy_identity, RecoverySessionIdentity) or not isinstance(
            legacy_snapshot, BrokerAccountSnapshot
        ):
            return
        legacy_results: list[dict[str, object]] = []
        for stock_dir, code in targets:
            try:
                state = json.loads((stock_dir / "state.json").read_text(encoding="utf-8"))
                if (
                    not isinstance(state, dict)
                    or str(state.get("operation_command_mode", "") or "").strip().upper()
                    != "EARLY_CLOSE"
                    or str(state.get("operation_notice", "") or "").strip().upper()
                    == "EARLY_CLOSE_NO_TARGET"
                ):
                    continue
                result = reconcile_legacy_early_close_no_target(
                    stock_dir=stock_dir,
                    stock_code=code,
                    recovery_identity=legacy_identity,
                    completed_recovery_identity=legacy_identity,
                    broker_snapshot=legacy_snapshot,
                    expected_account_no=legacy_identity.account_no,
                    expected_trading_day=legacy_identity.trading_day,
                    expected_login_session_id=legacy_identity.login_session_id,
                    expected_recovery_session_id=legacy_identity.recovery_session_id,
                    completed_recovery_status=str(
                        legacy_handoff.get("recovery_status", "") or ""
                    ),
                    holdings_complete=legacy_handoff.get("holdings_complete") is True,
                    open_orders_complete=legacy_handoff.get("open_orders_complete") is True,
                    expected_state_sha256=state_file_sha256(stock_dir / "state.json"),
                    order_queue_path=ORDER_QUEUE_PATH,
                )
            except Exception as exc:
                result = {
                    "status": LEGACY_CLOSE_STATUS_FAILED,
                    "reason": f"SERVICE_CALL_FAILED:{type(exc).__name__}",
                    "stock_code": code,
                }
            legacy_results.append(dict(result))
        self.last_legacy_close_reconciliation_result = {
            "status": "COMPLETED",
            "service_calls": len(legacy_results),
            "results": legacy_results,
        }

    def refresh_review_items(self) -> None:
        """Prepare only proven-safe evidence, then render the read-only view."""
        self._prepare_safe_review_reconciliation()
        self.load_review_items()

    def load_review_items(self) -> None:
        selected_stock_dirs = {
            str(item.data(Qt.UserRole) or "")
            for index in self.table.selectionModel().selectedRows()
            for item in [self.table.item(index.row(), 0)]
            if item is not None and str(item.data(Qt.UserRole) or "")
        }
        scroll_value = self.table.verticalScrollBar().value()
        rows = self._central_review_rows()
        self._review_rows_by_stock_dir = {
            str(row.get("stock_dir", "") or ""): row for row in rows
        }
        self.table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            tooltip = self._review_row_tooltip(row)
            self._set_item(row_index, 0, row.get("code", "-"), tooltip=tooltip)
            self._set_item(row_index, 1, row.get("name", "-"), Qt.AlignLeft | Qt.AlignVCenter, tooltip)
            self._set_item(row_index, 2, row.get("routine_name", "-"), tooltip=tooltip)
            self._set_item(
                row_index,
                3,
                row.get("display_status", row.get("return_availability", "-")),
                tooltip=tooltip,
            )
            self._set_item(
                row_index,
                4,
                row.get("review_entered_at", "미기록"),
                Qt.AlignCenter,
                tooltip,
            )
            self._set_item(
                row_index,
                5,
                row.get("review_reason", "-"),
                Qt.AlignCenter,
                tooltip,
            )
            self._set_item(row_index, 6, row.get("review_location", "-"), tooltip=tooltip)

            first_item = self.table.item(row_index, 0)
            if first_item is not None:
                first_item.setData(Qt.UserRole, str(row.get("stock_dir", "")))
                first_item.setData(Qt.UserRole + 1, str(row.get("code", "")))
                first_item.setData(Qt.UserRole + 2, str(row.get("name", "")))

        self._apply_saved_review_sort()
        if selected_stock_dirs:
            selection_model = self.table.selectionModel()
            for row_index in range(self.table.rowCount()):
                item = self.table.item(row_index, 0)
                if item is None or str(item.data(Qt.UserRole) or "") not in selected_stock_dirs:
                    continue
                selection_model.select(
                    self.table.model().index(row_index, 0),
                    QItemSelectionModel.Select | QItemSelectionModel.Rows,
                )
        self.table.verticalScrollBar().setValue(scroll_value)
        self.summary_label.setText(f"검토종목: {len(rows)}개")
        self.refresh_long_hold_policy_badge()
        self.refresh_operator_guidance()

    def refresh_long_hold_policy_badge(self) -> None:
        enabled = bool(
            read_review_policy().get("long_term_holding_enabled", False)
        )
        self._apply_long_hold_badge_state(enabled)

    def _apply_long_hold_badge_state(self, enabled: bool) -> None:
        self.long_hold_toggle_button.setText(
            "장기보유 ON" if enabled else "장기보유 OFF"
        )
        color = (
            LONG_HOLD_BADGE_ACTIVE_COLOR
            if enabled
            else LONG_HOLD_BADGE_IDLE_COLOR
        )
        self.long_hold_toggle_button.setEnabled(True)
        self.long_hold_toggle_button.setStyleSheet(
            auto_trade_setting_badge_stylesheet(
                "QPushButton#reviewLongHoldToggle",
                text_color=color,
                border_color=color,
            )
            + "QPushButton#reviewLongHoldToggle { margin: 1px 0; }"
            + "QPushButton#reviewLongHoldToggle:focus { outline: none; }"
        )

    def toggle_long_term_holding_policy(self) -> None:
        before = bool(
            read_review_policy().get("long_term_holding_enabled", False)
        )
        try:
            saved = write_long_term_holding_policy(not before)
        except Exception:
            self.refresh_long_hold_policy_badge()
            show_toast(self, "장기보유 설정 저장에 실패했습니다.")
            return
        self._apply_long_hold_badge_state(
            bool(saved.get("long_term_holding_enabled", False))
        )
        self._refresh_after_review_action()
        show_toast(
            self,
            "장기보유 ON" if saved["long_term_holding_enabled"] else "장기보유 OFF",
        )

    def selected_stock_dirs(self) -> list[tuple[Path, str, str]]:
        """검토관리창에서 선택된 종목의 runtime 폴더를 반환한다."""
        result: list[tuple[Path, str, str]] = []
        seen: set[str] = set()

        for index in self.table.selectionModel().selectedRows():
            item = self.table.item(index.row(), 0)
            if item is None:
                continue

            stock_dir_text = str(item.data(Qt.UserRole) or "").strip()
            code = str(item.data(Qt.UserRole + 1) or item.text() or "").strip()
            name = str(item.data(Qt.UserRole + 2) or "").strip()
            if not stock_dir_text:
                continue

            stock_dir = Path(stock_dir_text)
            key = str(stock_dir.resolve()) if stock_dir.exists() else stock_dir_text
            if key in seen:
                continue
            seen.add(key)
            result.append((stock_dir, code, name))

        return result

    def _completed_recovery_handoff(self) -> dict[str, object] | None:
        owner = persistent_feature_owner(self)
        getter = getattr(owner, "latest_completed_recovery_handoff", None)
        if not callable(getter):
            return None
        try:
            handoff = getter()
        except Exception:
            return None
        if not isinstance(handoff, dict):
            return None
        identity = handoff.get("identity")
        snapshot = handoff.get("snapshot")
        if (
            not isinstance(identity, RecoverySessionIdentity)
            or not isinstance(snapshot, BrokerAccountSnapshot)
            or handoff.get("recovery_status") != ACCOUNT_COMPLETED
            or handoff.get("holdings_complete") is not True
            or handoff.get("open_orders_complete") is not True
            or not snapshot.is_complete
            or bool(snapshot.errors)
            or snapshot.account_no != identity.account_no
            or snapshot.trading_day != identity.trading_day
            or snapshot.recovery_session_id != identity.recovery_session_id
            or snapshot.requested_at != identity.requested_at
        ):
            return None
        return handoff

    def _position_reconciliation_targets(self) -> list[tuple[Path, str, str]]:
        """Return only persisted, readable Review targets from the selection."""
        stocks_root = (PROJECT_ROOT / "stocks").resolve()
        targets: list[tuple[Path, str, str]] = []
        for stock_dir, code, name in self.selected_stock_dirs():
            try:
                resolved_dir = stock_dir.resolve(strict=True)
                resolved_dir.relative_to(stocks_root)
                state_path = resolved_dir / "state.json"
                state = json.loads(state_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(state, dict) or not _common_is_review_required_state(state):
                continue
            targets.append((resolved_dir, code, name))
        return targets

    def _legacy_close_recovery_handoff(self) -> dict[str, object] | None:
        """Return the existing handoff only while current GUI identities match."""
        handoff = self._completed_recovery_handoff()
        if handoff is None:
            return None
        identity = handoff.get("identity")
        if not isinstance(identity, RecoverySessionIdentity):
            return None
        owner = persistent_feature_owner(self)
        account_getter = getattr(owner, "selected_account_no", None)
        api = getattr(owner, "kiwoom_api", None)
        login_getter = getattr(api, "login_session_id", None)
        if not callable(account_getter) or not callable(login_getter):
            return None
        try:
            account_no = str(account_getter() or "").strip()
            login_session_id = str(login_getter() or "").strip()
        except Exception:
            return None
        if (
            not account_no
            or not login_session_id
            or identity.account_no != account_no
            or identity.login_session_id != login_session_id
            or identity.trading_day != datetime.now().date().isoformat()
        ):
            return None
        return handoff

    def _legacy_close_reconciliation_targets(self) -> list[tuple[Path, str, str]]:
        """Return selected persisted Review rows eligible for the explicit action."""
        stocks_root = (PROJECT_ROOT / "stocks").resolve()
        targets: list[tuple[Path, str, str]] = []
        for stock_dir, code, name in self.selected_stock_dirs():
            try:
                resolved_dir = stock_dir.resolve(strict=True)
                resolved_dir.relative_to(stocks_root)
                state = json.loads((resolved_dir / "state.json").read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(state, dict) or not _common_is_review_required_state(state):
                continue
            if str(state.get("operation_command_mode", "") or "").strip().upper() != "EARLY_CLOSE":
                continue
            if str(state.get("operation_notice", "") or "").strip().upper() == "EARLY_CLOSE_NO_TARGET":
                continue
            targets.append((resolved_dir, code, name))
        return targets

    def reconcile_selected_legacy_early_close(self) -> None:
        """Explicitly reconcile selected legacy EARLY_CLOSE Review residues."""
        handoff = self._legacy_close_recovery_handoff()
        selected_count = len(self.selected_stock_dirs())
        targets = self._legacy_close_reconciliation_targets()
        skipped = max(selected_count - len(targets), 0)
        if handoff is None:
            self.last_legacy_close_reconciliation_result = {
                "status": LEGACY_CLOSE_STATUS_BLOCKED_EVIDENCE,
                "reason": "COMPLETED_RECOVERY_HANDOFF_UNAVAILABLE_OR_MISMATCH",
                "service_calls": 0,
                "skipped": skipped,
            }
            show_toast(self, "현재 계좌와 일치하는 완료 Recovery가 없어 정합할 수 없습니다.")
            return
        if not targets:
            self.last_legacy_close_reconciliation_result = {
                "status": LEGACY_CLOSE_STATUS_BLOCKED_EVIDENCE,
                "reason": "ELIGIBLE_LEGACY_EARLY_CLOSE_REVIEW_REQUIRED",
                "service_calls": 0,
                "skipped": skipped,
            }
            show_toast(self, "조기마감 상태를 정합할 검토종목을 선택하세요.")
            return

        prepared: list[tuple[Path, str, str, str]] = []
        preflight_failed = 0
        for stock_dir, code, name in targets:
            try:
                state_sha = state_file_sha256(stock_dir / "state.json")
            except Exception:
                preflight_failed += 1
                continue
            prepared.append((stock_dir, code, name, state_sha))
        if not prepared:
            self.last_legacy_close_reconciliation_result = {
                "status": LEGACY_CLOSE_STATUS_FAILED,
                "reason": "STATE_EVIDENCE_CAPTURE_FAILED",
                "service_calls": 0,
                "failed": preflight_failed,
                "skipped": skipped,
            }
            show_toast(self, f"조기마감정합 실패 | 실패 {preflight_failed}개")
            return

        identity = handoff.get("identity")
        snapshot = handoff.get("snapshot")
        target_lines = "\n".join(
            f"- {code} {name}".rstrip() for _stock_dir, code, name, _sha in prepared
        )
        confirmation = QMessageBox.question(
            self,
            "조기마감정합",
            "현재 완료된 Recovery의 보유·미체결 정보를 기준으로 선택 종목의 "
            "과거 조기마감 상태를 '조기마감 대상 없음'으로 정합합니다.\n\n"
            "검토상태, 긴급정지, 보유정보, 루틴, 운영상태는 변경하지 않습니다.\n\n"
            f"대상: {len(prepared)}개\n{target_lines}\n\n"
            f"Recovery session: {getattr(identity, 'recovery_session_id', '')}\n"
            f"계좌: {str(handoff.get('account_display', '') or '')}",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirmation != QMessageBox.Yes:
            self.last_legacy_close_reconciliation_result = {
                "status": "CANCELED",
                "reason": "OPERATOR_CANCELED",
                "service_calls": 0,
                "skipped": skipped,
            }
            return

        current_handoff = self._legacy_close_recovery_handoff()
        if (
            current_handoff is None
            or current_handoff.get("identity") != identity
            or current_handoff.get("snapshot") is not snapshot
        ):
            self.last_legacy_close_reconciliation_result = {
                "status": LEGACY_CLOSE_STATUS_BLOCKED_EVIDENCE,
                "reason": "RECOVERY_HANDOFF_CHANGED_DURING_CONFIRMATION",
                "service_calls": 0,
                "skipped": skipped,
            }
            show_toast(self, "확인 중 Recovery 정보가 변경되어 조기마감정합을 차단했습니다.")
            return

        counts = {
            LEGACY_CLOSE_STATUS_COMPLETED: 0,
            LEGACY_CLOSE_STATUS_NO_CHANGE: 0,
            LEGACY_CLOSE_STATUS_BLOCKED_EVIDENCE: 0,
            LEGACY_CLOSE_STATUS_FAILED: preflight_failed,
        }
        results: list[dict[str, object]] = []
        for stock_dir, code, _name, state_sha in prepared:
            try:
                result = reconcile_legacy_early_close_no_target(
                    stock_dir=stock_dir,
                    stock_code=code,
                    recovery_identity=identity,
                    completed_recovery_identity=identity,
                    broker_snapshot=snapshot,
                    expected_account_no=getattr(identity, "account_no", ""),
                    expected_trading_day=getattr(identity, "trading_day", ""),
                    expected_login_session_id=getattr(identity, "login_session_id", ""),
                    expected_recovery_session_id=getattr(identity, "recovery_session_id", ""),
                    completed_recovery_status=str(handoff.get("recovery_status", "") or ""),
                    holdings_complete=handoff.get("holdings_complete") is True,
                    open_orders_complete=handoff.get("open_orders_complete") is True,
                    expected_state_sha256=state_sha,
                    order_queue_path=ORDER_QUEUE_PATH,
                )
            except Exception as exc:
                result = {
                    "status": LEGACY_CLOSE_STATUS_FAILED,
                    "reason": f"SERVICE_CALL_FAILED:{type(exc).__name__}",
                    "stock_code": code,
                }
            status = str(result.get("status", "") or "")
            counts[status if status in counts else LEGACY_CLOSE_STATUS_FAILED] += 1
            results.append(dict(result))

        self.last_legacy_close_reconciliation_result = {
            "status": "COMPLETED",
            "service_calls": len(prepared),
            "completed": counts[LEGACY_CLOSE_STATUS_COMPLETED],
            "no_change": counts[LEGACY_CLOSE_STATUS_NO_CHANGE],
            "blocked": counts[LEGACY_CLOSE_STATUS_BLOCKED_EVIDENCE],
            "failed": counts[LEGACY_CLOSE_STATUS_FAILED],
            "skipped": skipped,
            "results": results,
        }
        self.load_review_items()
        show_toast(
            self,
            "조기마감정합 완료 | "
            f"변경 {counts[LEGACY_CLOSE_STATUS_COMPLETED]} | "
            f"변경없음 {counts[LEGACY_CLOSE_STATUS_NO_CHANGE]} | "
            f"차단 {counts[LEGACY_CLOSE_STATUS_BLOCKED_EVIDENCE]} | "
            f"대상아님 {skipped} | "
            f"실패 {counts[LEGACY_CLOSE_STATUS_FAILED]}",
        )

    def reconcile_selected_position_information(self) -> None:
        """Explicitly apply one completed Recovery snapshot through Phase A."""
        handoff = self._completed_recovery_handoff()
        targets = self._position_reconciliation_targets()
        if handoff is None:
            self.last_position_reconciliation_result = {
                "status": POSITION_STATUS_BLOCKED_EVIDENCE,
                "reason": "COMPLETED_RECOVERY_HANDOFF_UNAVAILABLE",
                "service_calls": 0,
            }
            show_toast(self, "완료된 Recovery 보유정보가 없어 정합할 수 없습니다.")
            return
        if not targets:
            self.last_position_reconciliation_result = {
                "status": POSITION_STATUS_BLOCKED_EVIDENCE,
                "reason": "VALID_REVIEW_SELECTION_REQUIRED",
                "service_calls": 0,
            }
            show_toast(self, "보유정보를 정합할 검토종목을 선택하세요.")
            return

        prepared: list[tuple[Path, str, str, str]] = []
        preflight_failed = 0
        for stock_dir, code, name in targets:
            try:
                state_sha = state_file_sha256(stock_dir / "state.json")
            except Exception:
                preflight_failed += 1
                continue
            prepared.append((stock_dir, code, name, state_sha))
        if not prepared:
            self.last_position_reconciliation_result = {
                "status": POSITION_STATUS_FAILED,
                "reason": "STATE_EVIDENCE_CAPTURE_FAILED",
                "service_calls": 0,
                "failed": preflight_failed,
            }
            show_toast(self, f"보유정보정합 실패 | 실패 {preflight_failed}개")
            return

        identity = handoff.get("identity")
        snapshot = handoff.get("snapshot")
        target_lines = "\n".join(
            f"- {code} {name}".rstrip() for _stock_dir, code, name, _sha in prepared
        )
        confirmation = QMessageBox.question(
            self,
            "보유정보정합",
            "Broker Recovery 완료 보유정보를 기준으로 선택 종목의 "
            "보유수량/평단만 정합합니다.\n"
            "검토상태, 긴급정지, 마감 상태는 변경하지 않습니다.\n\n"
            f"대상: {len(prepared)}개\n{target_lines}\n\n"
            f"Recovery session: {getattr(identity, 'recovery_session_id', '')}\n"
            f"계좌: {str(handoff.get('account_display', '') or '')}",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirmation != QMessageBox.Yes:
            self.last_position_reconciliation_result = {
                "status": "CANCELED",
                "reason": "OPERATOR_CANCELED",
                "service_calls": 0,
            }
            return

        current_handoff = self._completed_recovery_handoff()
        if (
            current_handoff is None
            or current_handoff.get("identity") != identity
            or current_handoff.get("snapshot") is not snapshot
        ):
            self.last_position_reconciliation_result = {
                "status": POSITION_STATUS_BLOCKED_EVIDENCE,
                "reason": "RECOVERY_HANDOFF_CHANGED_DURING_CONFIRMATION",
                "service_calls": 0,
            }
            show_toast(self, "확인 중 Recovery 보유정보가 변경되어 정합을 차단했습니다.")
            return

        counts = {
            POSITION_STATUS_APPLIED: 0,
            POSITION_STATUS_NO_CHANGE: 0,
            POSITION_STATUS_BLOCKED_EVIDENCE: 0,
            POSITION_STATUS_FAILED: preflight_failed,
        }
        results: list[dict[str, object]] = []
        for stock_dir, code, _name, state_sha in prepared:
            try:
                result = reconcile_review_stock_position(
                    stock_dir=stock_dir,
                    stock_code=code,
                    recovery_identity=identity,
                    completed_recovery_identity=identity,
                    broker_snapshot=snapshot,
                    expected_account_no=getattr(identity, "account_no", ""),
                    expected_trading_day=getattr(identity, "trading_day", ""),
                    expected_login_session_id=getattr(identity, "login_session_id", ""),
                    expected_recovery_session_id=getattr(
                        identity, "recovery_session_id", ""
                    ),
                    completed_recovery_status=str(
                        handoff.get("recovery_status", "") or ""
                    ),
                    holdings_complete=handoff.get("holdings_complete") is True,
                    open_orders_complete=handoff.get("open_orders_complete") is True,
                    expected_state_sha256=state_sha,
                    order_queue_path=ORDER_QUEUE_PATH,
                )
            except Exception as exc:
                result = {
                    "status": POSITION_STATUS_FAILED,
                    "reason": f"SERVICE_CALL_FAILED:{type(exc).__name__}",
                    "stock_code": code,
                }
            status = str(result.get("status", "") or "")
            counts[status if status in counts else POSITION_STATUS_FAILED] += 1
            results.append(dict(result))

        self.last_position_reconciliation_result = {
            "status": "COMPLETED",
            "service_calls": len(prepared),
            "applied": counts[POSITION_STATUS_APPLIED],
            "no_change": counts[POSITION_STATUS_NO_CHANGE],
            "blocked": counts[POSITION_STATUS_BLOCKED_EVIDENCE],
            "failed": counts[POSITION_STATUS_FAILED],
            "results": results,
        }
        self.load_review_items()
        show_toast(
            self,
            "보유정보정합 완료 | "
            f"변경 {counts[POSITION_STATUS_APPLIED]} | "
            f"변경없음 {counts[POSITION_STATUS_NO_CHANGE]} | "
            f"차단 {counts[POSITION_STATUS_BLOCKED_EVIDENCE]} | "
            f"실패 {counts[POSITION_STATUS_FAILED]}",
        )

    def _review_exit_block_reason(self, stock_dir: Path, state: dict[str, object]) -> str:
        """복귀/미지정 전 필요한 최소 무결성 조건을 확인한다."""
        holding_qty = safe_int_value(state.get("holding_qty"), 0)
        avg_price = safe_float_value(state.get("avg_price"), 0.0)
        if holding_qty > 0:
            return "보유수량 존재"
        if avg_price > 0 and holding_qty <= 0:
            return "보유 0인데 평단 존재"

        buy_pending_qty, sell_pending_qty = pending_order_side_quantities(stock_dir, state)
        if buy_pending_qty == "?" or sell_pending_qty == "?":
            return PENDING_INTEGRITY_USER_REASON
        if safe_int_value(buy_pending_qty, 0) > 0:
            return "미수/매수 미체결 존재"
        if safe_int_value(sell_pending_qty, 0) > 0:
            return "미도/매도 미체결 존재"

        if auto_trade_setting_server_mismatch_detected(state):
            return "서버/프로그램 정보 불일치"

        return ""

    def _clear_review_state(self, state: dict[str, object]) -> None:
        """검토관리 해제 공통 메타 정리."""
        state["review_required"] = False
        state["review_status"] = ""
        state["review_location"] = ""
        state["review_reason"] = ""
        state["review_detail"] = ""
        state["review_entered_at"] = ""
        state["review_checked_at"] = ""
        state["review_routine"] = ""
        state["updated_at"] = now_text()

    def _refresh_after_review_action(self) -> None:
        self.load_review_items()
        parent = persistent_feature_owner(self)
        if hasattr(parent, "refresh_all"):
            try:
                parent.refresh_all()
            except Exception:
                pass
        elif hasattr(parent, "refresh_auto_trade_assignment_views"):
            try:
                parent.refresh_auto_trade_assignment_views()
            except Exception:
                pass

    def return_selected_items_to_auto_list(self) -> None:
        """검토관리 종목을 원래 루틴에 남긴 채 감시/대기 상태로 복귀한다."""
        targets = self.selected_stock_dirs()
        if not targets:
            show_toast(self, "복귀할 검토종목을 선택하세요.")
            return

        changed = 0
        blocked: list[str] = []
        failed = 0
        from gui_main_emergency_ops import normalize_review_emergency_target

        for stock_dir, code, name in targets:
            state_before = read_json_dict(stock_dir / "state.json")
            result = normalize_review_emergency_target(
                self,
                stock_dir,
                code,
                name,
                destination="RESTORE",
            )
            append_review_normalization_event(
                event_type="REVIEW_RETURNED",
                source="GlobalReviewRequiredWindow.return_selected_items_to_auto_list",
                stock_dir=stock_dir,
                code=code,
                name=name,
                destination="RESTORE",
                state_before=state_before,
                result=result,
            )
            status = str(result.get("status", "") or "")
            if status == "NORMALIZED":
                append_stock_log(stock_dir, "GUI", "검토관리 복귀: 정상화 완료")
                changed += 1
            elif status == "BLOCKED":
                blocked.append(
                    f"{code} {name}: {result.get('reason') or '무결성 문제'}"
                )
            else:
                failed += 1

        self._refresh_after_review_action()
        unavailable = len(blocked) + failed
        result_parts: list[str] = []
        if changed:
            result_parts.append(f"복귀 완료 {changed}개")
        if unavailable:
            result_parts.append(f"복귀 불가 {unavailable}개")
        show_toast(self, " | ".join(result_parts))

    def unassign_selected_review_items(self) -> None:
        """무결성 문제가 해소된 검토관리 종목을 미지정으로 전환한다."""
        targets = self.selected_stock_dirs()
        if not targets:
            show_toast(self, "미지정으로 전환할 검토종목을 선택하세요.")
            return

        changed = 0
        blocked: list[str] = []
        failed = 0
        from gui_main_emergency_ops import normalize_review_emergency_target

        for stock_dir, code, name in targets:
            state_before = read_json_dict(stock_dir / "state.json")
            result = normalize_review_emergency_target(
                self,
                stock_dir,
                code,
                name,
                destination="UNASSIGNED",
            )
            append_review_normalization_event(
                event_type="REVIEW_UNASSIGNED",
                source="GlobalReviewRequiredWindow.unassign_selected_review_items",
                stock_dir=stock_dir,
                code=code,
                name=name,
                destination="UNASSIGNED",
                state_before=state_before,
                result=result,
            )
            status = str(result.get("status", "") or "")
            if status == "NORMALIZED":
                append_stock_log(stock_dir, "GUI", "검토관리 미지정 전환: 정상화 완료")
                changed += 1
            elif status == "BLOCKED":
                blocked.append(
                    f"{code} {name}: {result.get('reason') or '무결성 문제'}"
                )
            else:
                failed += 1

        if changed:
            append_changelog("UPDATE", "기초종목.txt/state.json", f"검토관리 미지정 전환: {changed}개")
        self._refresh_after_review_action()

        unavailable = len(blocked) + failed
        result_parts: list[str] = []
        if changed:
            result_parts.append(f"미지정 완료 {changed}개")
        if unavailable:
            result_parts.append(f"미지정 불가 {unavailable}개")
        show_toast(self, " | ".join(result_parts))

    def delete_selected_review_items(self) -> None:
        """정상화할 수 없는 검토종목의 로컬 프로젝트 데이터를 폐기한다."""
        targets = self.selected_stock_dirs()
        if not targets:
            show_toast(self, "강제초기화할 검토종목을 선택하세요.")
            return

        from gui_stock_register_window import (
            STOCK_RESET_INITIALIZABLE,
            confirm_force_stock_reset,
            delete_stock_project_data,
            force_stock_reset_preflight,
        )

        prepared_targets: list[tuple[Path, str, str, dict[str, object]]] = []
        blocked: list[str] = []
        seen_identities: set[tuple[str, str]] = set()
        for selected_dir, code, name in targets:
            state_before = read_json_dict(selected_dir / "state.json")
            identity = (code, name)
            if identity in seen_identities:
                reason = "대상 identity 중복"
                blocked.append(f"{code} {name}: {reason}")
                append_review_force_reset_event(
                    stock_dir=selected_dir,
                    code=code,
                    name=name,
                    state_before=state_before,
                    result="BLOCKED",
                    reason=reason,
                    delete_target_verified=False,
                )
                continue
            seen_identities.add(identity)
            preflight = force_stock_reset_preflight(code, name, selected_dir)
            if preflight.get("status") != STOCK_RESET_INITIALIZABLE:
                reason = str(preflight.get("reason") or "대상 확인 실패")
                blocked.append(f"{code} {name}: {reason}")
                append_review_force_reset_event(
                    stock_dir=selected_dir,
                    code=code,
                    name=name,
                    state_before=state_before,
                    result="BLOCKED",
                    reason=reason,
                    delete_target_verified=False,
                )
                continue
            stock_dir = preflight.get("stock_dir")
            if not isinstance(stock_dir, Path):
                reason = "대상 경로 확인 실패"
                blocked.append(f"{code} {name}: {reason}")
                append_review_force_reset_event(
                    stock_dir=selected_dir,
                    code=code,
                    name=name,
                    state_before=state_before,
                    result="BLOCKED",
                    reason=reason,
                    delete_target_verified=False,
                )
                continue
            prepared_targets.append((stock_dir, code, name, state_before))

        if blocked:
            QMessageBox.information(
                self,
                "강제초기화 불가",
                "강제초기화 대상을 확인할 수 없습니다.\n\n" + "\n".join(blocked[:8]),
            )
            return

        if not confirm_force_stock_reset(
            self,
            [(code, name) for _, code, name, _ in prepared_targets],
        ):
            return

        deleted = 0
        failed = 0
        for stock_dir, code, name, state_before in prepared_targets:
            verified = force_stock_reset_preflight(code, name, stock_dir)
            verified_dir = verified.get("stock_dir")
            if verified.get("status") != STOCK_RESET_INITIALIZABLE or not isinstance(verified_dir, Path):
                reason = str(verified.get("reason") or "대상 재확인 실패")
                append_review_force_reset_event(
                    stock_dir=stock_dir,
                    code=code,
                    name=name,
                    state_before=state_before,
                    result="BLOCKED",
                    reason=reason,
                    delete_target_verified=False,
                )
                failed += 1
                continue
            result = delete_stock_project_data(code, name, verified_dir)
            if result.get("status") == "DELETED":
                append_review_force_reset_event(
                    stock_dir=stock_dir,
                    code=code,
                    name=name,
                    state_before=state_before,
                    result="COMPLETED",
                    delete_target_verified=True,
                )
                deleted += 1
            else:
                append_review_force_reset_event(
                    stock_dir=stock_dir,
                    code=code,
                    name=name,
                    state_before=state_before,
                    result="FAILED",
                    reason=str(result.get("reason") or "강제초기화 처리 실패"),
                    delete_target_verified=True,
                )
                failed += 1

        self._refresh_after_review_action()

        message = f"강제초기화 완료: {deleted}개"
        if failed:
            message += f" / 실패 {failed}개"
        show_toast(self, message)
