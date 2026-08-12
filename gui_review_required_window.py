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

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QBrush, QColor
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
from gui_order_utils import pending_order_side_quantities
from gui_review_utils import safe_float_value
from gui_styles import TABLE_LIGHT_SELECTION_STYLE, apply_plain_table_header
from gui_table_utils import next_sort_order
from gui_toast import show_toast
from gui_window_policy import (
    configure_persistent_feature_window,
    persistent_feature_owner,
)
from event_journal_production import append_production_event
from runtime_io import read_json_dict
from stock_repository import repository as stock_repository_factory
from gui_auto_trade_runtime import write_state_json
from gui_auto_trade_integrity import (
    is_review_required_state as _common_is_review_required_state,
    read_review_state_with_issue,
)
from gui_auto_trade_utils import PENDING_INTEGRITY_USER_REASON
PROJECT_ROOT = Path(__file__).resolve().parent
CHANGELOG_PATH = PROJECT_ROOT / "PROJECT_CHANGELOG.txt"

REVIEW_UNKNOWN_TEXT = "-"
REVIEW_TIME_UNRECORDED = "\ubbf8\uae30\ub85d"
REVIEW_DISPLAY_STATUS_UNRESOLVED = "\ubbf8\ud574\uacb0"
REVIEW_DISPLAY_STATUS_EMERGENCY_STOPPED = "\uae34\uae09\uc815\uc9c0"
REVIEW_REASON_OPERATION_DATA_MISSING = "\uc6b4\uc601 \ub370\uc774\ud130 \uc5c6\uc74c"
REVIEW_REASON_OPERATION_DATA_READ_ERROR = "\uc6b4\uc601 \ub370\uc774\ud130 \uc77d\uae30 \uc624\ub958"
REVIEW_DETECTION_EVENT_UNRECORDED = "\ubbf8\uae30\ub85d"
REVIEW_DETECTION_EVENT_STOCK_MANAGEMENT = "\uc885\ubaa9\uad00\ub9ac"
REVIEW_SOURCE_EMERGENCY_RELEASE = "\uae34\uae09\uc815\uc9c0\ud574\uc81c"
REVIEW_REPRODUCTION_MANIFEST_PREFIX = "review_required_library_cases_"
REVIEW_DETECTION_EVENT_DISPLAY_BY_SOURCE = {
    "\uc6b4\uc601\uc2dc\uc791": "\uc6b4\uc601 \uc2dc\uc791",
    "\uc6b4\uc601\uc911": "\uc6b4\uc601 \uc911",
    "\uc548\uc815\uc131\uac80\uc0ac": "\uc548\uc815\uc131 \uac80\uc0ac",
    "\uae34\uae09\uc815\uc9c0\ud574\uc81c": "\uae34\uae09\uc815\uc9c0 \ud574\uc81c",
    "\uac15\uc81c\uc885\ub8cc": "\uc6b4\uc601 \uc885\ub8cc",
    "\uc885\ubaa9\ub4f1\ub85d \ucc3d \ubbf8\uccb4\uacb0 \ub370\uc774\ud130 \ubb34\uacb0\uc131 \uc624\ub958": "\uc885\ubaa9 \ub4f1\ub85d",
    "\ub4f1\ub85d\ud574\uc81c \ubbf8\uccb4\uacb0 \ub370\uc774\ud130 \ubb34\uacb0\uc131 \uc624\ub958": "\uc885\ubaa9 \ud574\uc81c",
    "\ub8e8\ud2f4 \uc774\ub3d9 \ubbf8\uccb4\uacb0 \ub370\uc774\ud130 \ubb34\uacb0\uc131 \uc624\ub958": "\ub8e8\ud2f4 \ub4f1\ub85d",
    "\ub8e8\ud2f4 \ud574\uc81c \ubbf8\uccb4\uacb0 \ub370\uc774\ud130 \ubb34\uacb0\uc131 \uc624\ub958": "\ub8e8\ud2f4 \ud574\uc81c",
    "PRODUCTION_RECOVERY": "\ud504\ub85c\uadf8\ub7a8 \uc2dc\uc791",
}


def review_entered_at_display(state: dict[str, object]) -> str:
    """Read the persisted review transition time without inferring one."""
    entered_at = str(state.get("review_entered_at", "") or "").strip()
    return entered_at or REVIEW_TIME_UNRECORDED


def review_detection_event_display(source: object) -> str:
    """Return the operator-facing production event for the stored review source."""
    raw = str(source or "").strip()
    if not raw or raw == REVIEW_UNKNOWN_TEXT:
        return REVIEW_DETECTION_EVENT_UNRECORDED
    return REVIEW_DETECTION_EVENT_DISPLAY_BY_SOURCE.get(raw, raw)


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
            location = (
                case.get("review_location")
                or REVIEW_DETECTION_EVENT_STOCK_MANAGEMENT
            )
            entered_at = (
                case.get("review_entered_at")
                or case.get("created_at")
                or manifest.get("created_at")
            )
            return {
                "review_location": review_detection_event_display(location),
                "review_entered_at": _review_manifest_entered_at_display(entered_at),
            }
    return {
        "review_location": REVIEW_DETECTION_EVENT_UNRECORDED,
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
) -> str:
    """Return the review-management status display for a collected row."""
    if state_issue_reason:
        return REVIEW_DISPLAY_STATUS_UNRESOLVED
    if not isinstance(state, dict) or not state:
        return REVIEW_DISPLAY_STATUS_UNRESOLVED

    review_status = str(state.get("review_status", "") or "").strip().upper()
    if review_status == "RESOLVED":
        return "\ud574\uacb0"

    emergency_reason = str(state.get("emergency_reason", "") or "").strip()
    emergency_stopped_at = str(state.get("emergency_stopped_at", "") or "").strip()
    source = str(review_location_source or state.get("review_location", "") or "").strip()
    if (
        (emergency_reason or emergency_stopped_at)
        and source != REVIEW_SOURCE_EMERGENCY_RELEASE
    ):
        return REVIEW_DISPLAY_STATUS_EMERGENCY_STOPPED

    if review_status in {"PENDING", "REVIEW_REQUIRED", "NEEDS_REVIEW"}:
        return REVIEW_DISPLAY_STATUS_UNRESOLVED

    if buy_pending_qty == "?" or sell_pending_qty == "?":
        return REVIEW_DISPLAY_STATUS_UNRESOLVED
    if holding_qty > 0:
        return REVIEW_DISPLAY_STATUS_UNRESOLVED
    if safe_int_value(buy_pending_qty, 0) > 0:
        return REVIEW_DISPLAY_STATUS_UNRESOLVED
    if safe_int_value(sell_pending_qty, 0) > 0:
        return REVIEW_DISPLAY_STATUS_UNRESOLVED
    if avg_price > 0 and holding_qty <= 0:
        return REVIEW_DISPLAY_STATUS_UNRESOLVED
    if auto_trade_setting_server_mismatch_detected(state):
        return REVIEW_DISPLAY_STATUS_UNRESOLVED

    if is_review_required_state(state):
        return "\ud574\uacb0"
    return REVIEW_DISPLAY_STATUS_UNRESOLVED


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


def collect_global_review_required_rows() -> list[dict[str, object]]:
    """
    프로그램 전체 단위 검토관리 대상 목록을 중앙 stocks/ 기준으로 수집한다.

    정책:
    - 검토관리의 진실 원본은 stocks/<종목>/state.json 이다.
    - 루틴 패키지 폴더나 구형 _루틴폴더 내부 종목폴더는 조회하지 않는다.
    - 루틴명은 stocks/<종목>/config.json의 연결값을 우선하고, 없으면 state의 review_routine을 보조로 표시한다.
    """
    rows: list[dict[str, object]] = []
    seen_keys: set[tuple[str, str, str]] = set()

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
            display_status = _review_display_status_for_collected_row(
                None,
                state_issue_reason=state_issue_reason,
            )
        elif not is_review_required_state(state):
            continue
        else:
            holding_qty = safe_int_value(state.get("holding_qty"), 0)
            avg_price = safe_float_value(state.get("avg_price"), 0.0)
            buy_pending_qty, sell_pending_qty = pending_order_side_quantities(stock_dir, state)
            review_location_source = state.get("review_location", "") or REVIEW_UNKNOWN_TEXT
            review_location = review_detection_event_display(review_location_source)
            review_entered_at = review_entered_at_display(state)

            display_status = _review_display_status_for_collected_row(
                state,
                review_location_source=review_location_source,
                holding_qty=holding_qty,
                avg_price=avg_price,
                buy_pending_qty=buy_pending_qty,
                sell_pending_qty=sell_pending_qty,
            )

        key = (routine_name, code, name)
        if key in seen_keys:
            continue
        seen_keys.add(key)

        rows.append({
            "routine_name": routine_name,
            "stock_dir": stock_dir,
            "code": code,
            "name": name,
            "review_location": review_location,
            "review_reason": state_issue_reason or str(state.get("review_reason", "") or state.get("review_detail", "") or REVIEW_UNKNOWN_TEXT).strip() or REVIEW_UNKNOWN_TEXT,
            "review_entered_at": review_entered_at,
            "last_checked_at": str(state.get("review_checked_at", "") or state.get("updated_at", "") or "-").strip() or "-",
            "holding_qty": holding_qty,
            "avg_price": avg_price,
            "buy_pending_qty": buy_pending_qty,
            "sell_pending_qty": sell_pending_qty,
            "display_status": display_status,
            "return_availability": display_status,
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
        self.resize(1100, 620)

        self.summary_label = QLabel("검토종목: 0개")
        self.table = QTableWidget()
        self.btn_return = QPushButton("복귀")
        self.btn_unassign = QPushButton("미지정")
        self.btn_delete = QPushButton("강제초기화")
        self.btn_refresh = QPushButton("새로고침")
        self.btn_close = QPushButton("닫기")
        self._review_sort_column = -1
        self._review_sort_order = Qt.AscendingOrder

        self._setup_ui()
        self._connect_events()
        self.load_review_items()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout()
        layout.addWidget(self.summary_label)

        headers = [
            "코드",
            "종목",
            "위치",
            "상태",
            "검토 전환 시각",
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
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.Stretch)
        header.setStretchLastSection(False)
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(True)
        self.table.setColumnWidth(0, 75)    # 코드
        self.table.setColumnWidth(1, 180)   # 종목
        self.table.setColumnWidth(2, 140)   # 위치
        self.table.setColumnWidth(3, 75)    # 상태
        self.table.setColumnWidth(4, 180)   # 검토 전환 시각
        self.table.setColumnWidth(5, 350)   # 사유
        self.table.setColumnWidth(6, 130)   # 검출
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.setStyleSheet(TABLE_LIGHT_SELECTION_STYLE)
        layout.addWidget(self.table)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.btn_return.setMinimumWidth(90)
        self.btn_unassign.setMinimumWidth(90)
        self.btn_delete.setMinimumWidth(90)
        self.btn_refresh.setMinimumWidth(100)
        self.btn_close.setMinimumWidth(100)
        buttons.addWidget(self.btn_return)
        buttons.addWidget(self.btn_unassign)
        buttons.addWidget(self.btn_delete)
        buttons.addWidget(self.btn_refresh)
        buttons.addWidget(self.btn_close)
        layout.addLayout(buttons)
        self.setLayout(layout)

    def _connect_events(self) -> None:
        self.btn_return.clicked.connect(self.return_selected_items_to_auto_list)
        self.btn_unassign.clicked.connect(self.unassign_selected_review_items)
        self.btn_delete.clicked.connect(self.delete_selected_review_items)
        self.btn_refresh.clicked.connect(self.load_review_items)
        self.btn_close.clicked.connect(self.close)
        self.table.horizontalHeader().sectionClicked.connect(self.sort_review_table_by_column)
        self.table.customContextMenuRequested.connect(self.show_review_table_context_menu)

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
        return collect_global_review_required_rows()

    def load_review_items(self) -> None:
        rows = self._central_review_rows()
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
            self._set_item(row_index, 4, row.get("review_entered_at", "미기록"), tooltip=tooltip)
            self._set_item(row_index, 5, row.get("review_reason", "-"), Qt.AlignLeft | Qt.AlignVCenter, tooltip)
            self._set_item(row_index, 6, row.get("review_location", "-"), tooltip=tooltip)

            first_item = self.table.item(row_index, 0)
            if first_item is not None:
                first_item.setData(Qt.UserRole, str(row.get("stock_dir", "")))
                first_item.setData(Qt.UserRole + 1, str(row.get("code", "")))
                first_item.setData(Qt.UserRole + 2, str(row.get("name", "")))

        self._apply_saved_review_sort()
        self.summary_label.setText(f"검토종목: {len(rows)}개")

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
            QMessageBox.information(
                self,
                "강제초기화",
                "강제초기화할 검토종목을 선택하세요.",
            )
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
