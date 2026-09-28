# -*- coding: utf-8 -*-
"""Mock-only projection from internal action reasons to user-visible messages."""

from __future__ import annotations

from typing import Any

from gui_user_reason import user_reason_message


_MOCK_REASON_MESSAGES = {
    "FINAL_SESSION_ENDED": "운영 가능한 거래 시간이 종료되어 운영을 시작할 수 없습니다.",
    "ALREADY_RUNNING": "이미 운영 중인 모의검증 대상입니다.",
    "INSTANCE_DISABLED": "이 루틴은 모의검증 실행이 비활성화되어 있습니다.",
    "ROUTINE_INSTANCE_DISABLED": "이 루틴은 모의검증 실행이 비활성화되어 있습니다.",
    "MOCK_ALLOWED_DISABLED": "이 루틴은 모의검증 실행이 비활성화되어 있습니다.",
    "MOCK_EXECUTION_NOT_ALLOWED": "이 루틴은 모의검증 실행이 비활성화되어 있습니다.",
    "MOCK_ORDERBOOK_STALE": "가상 실행에 필요한 호가 정보가 오래되어 주문을 진행할 수 없습니다.",
    "MOCK_ORDERBOOK_UNAVAILABLE": "가상 실행에 필요한 호가 정보를 확인할 수 없습니다.",
    "MOCK_MARKET_UNAVAILABLE": "가상 실행에 필요한 시장정보를 확인할 수 없습니다.",
    "INVALID_RULES": "루틴 설정을 확인할 수 없어 요청을 처리할 수 없습니다.",
    "MOCK_ROUTINE_RULES_SNAPSHOT_MISSING": "루틴 설정을 확인할 수 없어 요청을 처리할 수 없습니다.",
    "INVALID_IDENTITY": "선택한 모의검증 대상을 확인할 수 없습니다.",
    "MOCK_CURRENT_SESSION_NOT_FOUND": "선택한 모의검증 대상을 확인할 수 없습니다.",
    "MOCK_CONTEXT_TARGET_STALE": "선택한 모의검증 상태가 변경되었습니다. 화면을 새로 확인하세요.",
    "MOCK_VALIDATION_SESSION_IDENTITY_MISMATCH": "선택한 모의검증 상태가 변경되었습니다. 화면을 새로 확인하세요.",
    "REVISION_CONFLICT": "모의검증 상태가 변경되었습니다. 화면을 새로 확인한 뒤 다시 시도하세요.",
    "MOCK_SESSION_REVISION_CONFLICT": "모의검증 상태가 변경되었습니다. 화면을 새로 확인한 뒤 다시 시도하세요.",
    "MOCK_INSTANCE_SETTINGS_REVISION_CONFLICT": "모의검증 상태가 변경되었습니다. 화면을 새로 확인한 뒤 다시 시도하세요.",
    "OPERATION_NOT_RUNNING": "현재 운영 중인 모의검증이 없습니다.",
    "MOCK_OPERATION_NOT_STARTED": "현재 운영 중인 모의검증이 없습니다.",
    "MOCK_INSTANCE_OPERATION_NOT_STARTED": "현재 운영 중인 모의검증이 없습니다.",
    "MOCK_INSTANCE_OPERATION_CLOSE_STATE_INVALID": "현재 운영 대상 종목이 아닙니다.",
    "MOCK_ROUTINE_INSTANCE_NOT_IN_SESSION": "현재 운영 대상 종목이 아닙니다.",
    "CLOSE_ALREADY_PENDING": "마감 또는 청산이 이미 진행 중입니다.",
    "MOCK_CLOSE_ALREADY_REQUESTED": "마감 또는 청산이 이미 진행 중입니다.",
    "LIQUIDATION_NOT_ALLOWED": "현재 상태에서는 청산을 진행할 수 없습니다.",
    "MOCK_LIQUIDATION_BLOCKED": "현재 상태에서는 청산을 진행할 수 없습니다.",
    "LIQUIDATION_TIME_WINDOW_ENTERED": "청산설정변경이 불가능합니다.",
    "NO_HOLDING": "보유수량이 없습니다.",
    "OUTSIDE_REGULAR_MARKET": "현재 조기마감 가능한 시간이 아닙니다.",
    "SCHEDULED_OPERATION_WINDOW_ENDED": "현재 조기마감 가능한 시간이 아닙니다.",
    "RETURN_TO_ROUTINE_CLOSE_NOT_ALLOWED": "현재 마감방식으로 변경할 수 없습니다.",
    "EXECUTION_PROGRESS_BLOCKED": "현재 마감방식으로 변경할 수 없습니다.",
    "MOCK_EARLY_AUTO_RETURN_BLOCKED": "현재 마감방식으로 변경할 수 없습니다.",
    "MOCK_EARLY_AUTO_RETURN_FILLED_BLOCKED": "현재 마감방식으로 변경할 수 없습니다.",
    "MOCK_INSTANCE_EARLY_CLOSE_CANCEL_BLOCKED": "현재 상태에서는 실행할 수 없습니다.",
    "MOCK_ATS_SESSION_INACTIVE": "현재 ATS 거래시간에는 청산을 진행할 수 없습니다.",
    "VALIDATION_STOPPED": "모의검증이 종료된 상태입니다.",
    "MOCK_RESET_REQUIRES_REVIEW_STOPPED": "운영이 종료된 상태에서만 검증리셋할 수 있습니다.",
    "MOCK_INSTANCE_SETTINGS_REQUIRE_WAITING": "모의검증 진행 중에는 설정을 변경할 수 없습니다.",
    "MOCK_REVIEW_UNRESOLVED": "검토가 필요한 상태에서는 등록을 해제할 수 없습니다.",
    "MOCK_ACTIVE_EXECUTION": "모의검증 진행 중에는 등록을 해제할 수 없습니다.",
    "MOCK_OPERATION_ACTIVE": "모의검증 진행 중에는 등록을 해제할 수 없습니다.",
    "MOCK_INSTANCE_OPERATION_ACTIVE": "모의검증 진행 중에는 등록을 해제할 수 없습니다.",
    "MOCK_POSITION_REMAINS": "보유 수량이 있어 등록을 해제할 수 없습니다.",
    "MOCK_LAST_ROUTINE_REQUIRES_STOCK_UNREGISTER": "마지막 루틴은 종목 등록해제로 정리하세요.",
}


def _reason_code(value: object) -> str:
    return str(value or "").partition(":")[0].strip().upper()


def mock_user_message(
    reason: object,
    *,
    fallback: str = "요청을 처리하지 못했습니다.",
) -> str:
    """Return Mock presentation text without exposing an internal reason token."""

    code = _reason_code(reason)
    mapped = _MOCK_REASON_MESSAGES.get(code)
    if mapped:
        return mapped
    return user_reason_message(reason, fallback=fallback)


def mock_action_result_message(result: Any) -> str:
    """Return one user message only for a blocked/failed Mock action result."""

    if not isinstance(result, dict):
        return ""
    if str(result.get("summary_toast_message") or "").strip():
        return ""
    status = str(result.get("status") or "").strip().upper()
    blocked = result.get("ok") is False or status in {"BLOCKED", "ERROR", "FAILED"}
    if not blocked:
        return ""
    reason = (
        result.get("reason_code")
        or result.get("reason")
        or result.get("error")
        or status
    )
    return mock_user_message(reason)


def mock_action_status_text(result: Any) -> str:
    """Project an internal action status into compact status-bar text."""

    if not isinstance(result, dict):
        return "완료"
    status = str(result.get("status") or "").strip().upper()
    if result.get("ok") is False or status in {"BLOCKED", "WAIT", "NOOP"}:
        return "처리 불가"
    if status in {"ERROR", "FAILED", "INSTANCE_ERROR"}:
        return "오류"
    return "완료"


__all__ = [
    "mock_action_result_message",
    "mock_action_status_text",
    "mock_user_message",
]
