# -*- coding: utf-8 -*-
"""
gui_auto_trade_utils.py

자동매매설정 창에서 사용하는 자동매매 관련 판정 유틸리티.
UI에 직접 의존하지 않는다.
"""

from __future__ import annotations

import logging
from pathlib import Path

from gui_auto_trade_runtime import now_text, write_state_json
from gui_auto_trade_status_ops import append_stock_log
from gui_auto_trade_integrity import (
    is_emergency_stopped_state,
    is_review_required_state,
    operator_review_location,
    REVIEW_REASON_PENDING_ORDER_DATA_ERROR,
)
from gui_order_utils import pending_order_integrity_issue_codes, pending_order_side_quantities
from gui_review_utils import merge_existing_review_metadata, normalized_review_reasons
from runtime_io import read_json_dict

LOGGER = logging.getLogger(__name__)
UNEXPECTED_STATUS_REASON = "처리할 수 없는 종목입니다."
PENDING_INTEGRITY_USER_REASON = "처리할 수 없는 종목입니다.\n검토관리에서 확인하세요."


def mark_pending_order_integrity_review_required(
    routine_name: str,
    stock_dir: Path,
    code: str,
    name: str,
    issue_codes: list[str],
    *,
    source: str,
) -> bool:
    state = read_json_dict(stock_dir / "state.json")
    if not isinstance(state, dict):
        state = {}
    before_status = str(state.get("status", "STOPPED")).strip().upper() or "STOPPED"
    unique_issues = list(dict.fromkeys(str(issue).strip() for issue in issue_codes if str(issue).strip()))
    evidence_reason = "PENDING_ORDER_DATA_INTEGRITY"
    if unique_issues:
        evidence_reason += ": " + " / ".join(unique_issues)
    reason = REVIEW_REASON_PENDING_ORDER_DATA_ERROR
    review_location = operator_review_location(source)

    if before_status in {"REVIEW_REQUIRED", "REVIEW"} and reason in normalized_review_reasons(
        [state.get("review_reason", "")]
    ):
        return True

    timestamp = now_text()
    metadata = {
            "status": "REVIEW_REQUIRED",
            "review_required": True,
            "review_status": "PENDING",
            "review_location": review_location,
            "review_reason": reason,
            "review_detail": f"{code} {name} / {reason} / evidence={evidence_reason}",
            "review_checked_at": timestamp,
            "updated_at": timestamp,
            "trade_enabled": False,
        }
    metadata = merge_existing_review_metadata(state, metadata)
    state.update(metadata)
    if before_status not in {"REVIEW_REQUIRED", "REVIEW"}:
        state["review_entered_at"] = timestamp

    ok = write_state_json(stock_dir, state)
    log_message = (
        f"미체결 데이터 무결성 오류: {before_status} -> REVIEW_REQUIRED / "
        f"routine={routine_name} / issues={', '.join(unique_issues) or '-'}"
    )
    append_stock_log(stock_dir, "ERROR" if ok else "CRITICAL", log_message)
    if not ok:
        LOGGER.error(
            "pending integrity review-required write failed: code=%s name=%s routine=%s stock_dir=%s issues=%s",
            code,
            name,
            routine_name,
            stock_dir,
            unique_issues,
        )
        return False

    saved_state = read_json_dict(stock_dir / "state.json")
    saved_ok = (
        isinstance(saved_state, dict)
        and str(saved_state.get("status", "") or "").strip().upper() == "REVIEW_REQUIRED"
        and saved_state.get("review_required") is True
        and str(saved_state.get("review_reason", "") or "").strip()
        == str(state.get("review_reason", "") or "").strip()
    )
    if not saved_ok:
        append_stock_log(
            stock_dir,
            "CRITICAL",
            f"미체결 데이터 무결성 검토관리 저장 검증 실패: routine={routine_name} / issues={', '.join(unique_issues) or '-'}",
        )
        LOGGER.error(
            "pending integrity review-required read-back failed: code=%s name=%s routine=%s stock_dir=%s reason=%s",
            code,
            name,
            routine_name,
            stock_dir,
            reason,
        )
        return False
    return True


def auto_trade_unregister_category(
    routine_name: str,
    stock_dir: Path,
    code: str,
    name: str,
) -> dict[str, object]:
    """
    자동매매설정 창의 루틴 등록해제 정책에 따라 선택 종목을 분류한다.

    반환 category:
    - immediate: 보유 0 + 현재 미체결 0
    - blocked: 보유·미체결, 운영 중, 보호 상태, 무결성 오류 등
    """
    state = read_json_dict(stock_dir / "state.json")
    raw_status = str(state.get("status", "STOPPED")).strip().upper()
    try:
        holding_qty = int(state.get("holding_qty", 0) or 0)
    except Exception:
        holding_qty = 0

    buy_pending_qty, sell_pending_qty = pending_order_side_quantities(stock_dir, state)
    item: dict[str, object] = {
        "code": code,
        "name": name,
        "title": f"{code} {name}",
        "runtime_dirs": [(routine_name, stock_dir)],
    }

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
        item["category"] = "blocked"
        item["reasons"] = [f"{routine_name}: 검토관리 종목입니다."]
        return item

    if is_emergency_stopped_state(state):
        item["category"] = "blocked"
        item["reasons"] = [f"{routine_name}: 긴급정지 종목입니다."]
        return item

    operation_active_statuses = {"RUNNING", "STARTED", "AUTO", "TRADING", "SELL_ONLY"}
    if raw_status in operation_active_statuses:
        item["category"] = "blocked"
        item["reasons"] = [f"{routine_name}: 운영 중 종목입니다."]
        return item

    if raw_status not in known_statuses:
        item["category"] = "blocked"
        LOGGER.error(
            "unexpected unregister policy status: %s code=%s name=%s routine=%s stock_dir=%s",
            raw_status,
            code,
            name,
            routine_name,
            stock_dir,
        )
        item["reasons"] = [f"{routine_name}: {UNEXPECTED_STATUS_REASON}"]
        return item

    if buy_pending_qty == "?" or sell_pending_qty == "?":
        item["category"] = "blocked"
        issue_codes = pending_order_integrity_issue_codes(stock_dir, state)
        mark_pending_order_integrity_review_required(
            routine_name,
            stock_dir,
            code,
            name,
            issue_codes,
            source="등록해제 미체결 데이터 무결성 오류",
        )
        item["reasons"] = [f"{routine_name}: {PENDING_INTEGRITY_USER_REASON}"]
        return item

    pending_parts: list[str] = []
    if isinstance(buy_pending_qty, int) and buy_pending_qty > 0:
        pending_parts.append(f"매수미결 {buy_pending_qty}")
    if isinstance(sell_pending_qty, int) and sell_pending_qty > 0:
        pending_parts.append(f"매도미결 {sell_pending_qty}")

    if holding_qty > 0 or pending_parts:
        details: list[str] = []
        if holding_qty > 0:
            details.append(f"보유 {holding_qty}")
        details.extend(pending_parts)
        reason = f"{routine_name}: {', '.join(details)}"
        item["category"] = "blocked"
        item["reasons"] = [reason]
        return item

    item["category"] = "immediate"
    item["reasons"] = [f"{routine_name}: 보유·미체결 없음"]
    return item
