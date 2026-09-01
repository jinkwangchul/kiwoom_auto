# -*- coding: utf-8 -*-
"""User-facing explanations for internal command reason codes.

The codes remain available to logs and diagnostics.  General UI surfaces use
this module so implementation vocabulary does not leak into toast, dialog,
tooltip, or status-bar text.
"""

from __future__ import annotations


_USER_REASON_MESSAGES = {
    "SELECT_STOCK_REQUIRED": "대상 종목을 선택하세요.",
    "CURRENTLY_RUNNING": "운영 중에는 변경할 수 없습니다.",
    "ALREADY_RUNNING": "이미 운영 중인 종목입니다.",
    "REVIEW_REQUIRED": "검토가 필요한 종목입니다.",
    "EMERGENCY_STOP": "긴급정지 상태에서는 변경할 수 없습니다.",
    "EMERGENCY_STOPPED": "긴급정지 상태에서는 처리할 수 없습니다.",
    "HAS_HOLDING": "보유 수량이 있어 해제할 수 없습니다.",
    "HOLDING_QTY": "보유 수량이 있어 해제할 수 없습니다.",
    "HAS_PENDING_ORDER": "미체결 주문이 있어 해제할 수 없습니다.",
    "BUY_PENDING": "매수 미체결 주문이 있어 해제할 수 없습니다.",
    "SELL_PENDING": "매도 미체결 주문이 있어 해제할 수 없습니다.",
    "PENDING_ORDER_INTEGRITY_UNKNOWN": "미체결 주문 상태를 확인할 수 없습니다.",
    "PENDING_INTEGRITY_UNKNOWN": "미체결 주문 상태를 확인할 수 없습니다.",
    "CURRENT_PRICE_UNAVAILABLE": "현재 가격 정보를 확인할 수 없습니다.",
    "NOT_REGISTERED": "종목 등록 정보를 찾을 수 없습니다.",
    "STOCK_NOT_REGISTERED": "종목 등록 정보를 찾을 수 없습니다.",
    "STOCK_RECORD_MISSING": "종목 등록 정보를 찾을 수 없습니다.",
    "NO_CURRENT_ASSIGNMENT": "현재 등록된 루틴이 없습니다.",
    "ALREADY_UNASSIGNED": "현재 등록된 루틴이 없습니다.",
    "ASSIGNMENT_EXISTS": "이미 다른 루틴에 추가된 종목입니다.",
    "ASSIGNMENT_CHANGED": "등록된 루틴이 변경되었습니다. 화면을 새로 확인하세요.",
    "ASSIGNMENT_INVALID": "종목의 루틴 등록 정보를 확인할 수 없습니다.",
    "TARGET_INSTANCE_MISSING": "추가할 루틴 정보를 찾을 수 없습니다.",
    "GROUP_MISSING": "추가할 루틴 그룹 정보를 찾을 수 없습니다.",
    "REGISTRATION_INVALID": "종목 등록 정보가 완전하지 않습니다.",
    "REGISTRATION_IN_FLIGHT": "같은 종목의 등록 처리가 진행 중입니다.",
    "RECOVERY_CONTEXT_MISSING": "현재 로그인 상태 확인이 완료되지 않았습니다.",
    "RECOVERY_NOT_STARTED": "현재 로그인 상태 확인이 완료되지 않았습니다.",
    "RECOVERY_IN_PROGRESS": "기존 운영 상태를 확인하고 있습니다. 잠시 후 다시 시도하세요.",
    "RECOVERY_BLOCKED": "현재 로그인 상태 확인이 완료되지 않아 처리할 수 없습니다.",
    "RECOVERY_STOCK_PENDING": "이 종목의 현재 로그인 상태 확인이 완료되지 않았습니다.",
    "RECOVERY_STOCK_REVIEW_REQUIRED": "이 종목은 운영 상태 검토가 필요합니다.",
    "RECOVERY_STOCK_FAILED": "이 종목의 운영 상태를 확인하지 못했습니다.",
    "RECOVERY_ACCOUNT_REVIEW_REQUIRED": "확인이 필요한 운영 항목이 남아 있습니다.",
    "RECOVERY_ACCOUNT_FAILED": "계좌의 운영 상태를 확인하지 못했습니다.",
    "RECOVERY_IDENTITY_MISMATCH": "현재 로그인 또는 계좌 정보가 확인된 운영 상태와 일치하지 않습니다.",
    "RECOVERY_STALE_SESSION": "이전 로그인에서 확인한 운영 상태는 사용할 수 없습니다.",
    "UNREGISTER_UNAVAILABLE": "현재 선택한 종목은 루틴에서 해제할 수 없습니다.",
    "STOCK_REGISTER_UNAVAILABLE": "현재 선택한 종목은 등록할 수 없습니다.",
    "TRADE_PERMISSION_UNAVAILABLE": "현재 선택한 종목의 주문 권한을 변경할 수 없습니다.",
    "EXCLUSION_UNAVAILABLE": "현재 선택한 종목의 운영 제외 상태를 변경할 수 없습니다.",
    "EXCLUDED_MANAGEMENT_RESTRICTED": "운영 제외 상태에서는 이 작업을 할 수 없습니다.",
    "EARLY_CLOSE_UNAVAILABLE": "현재 선택한 종목은 조기마감할 수 없습니다.",
    "EARLY_CLOSE_CANCEL_UNAVAILABLE": "현재 선택한 종목은 조기마감을 취소할 수 없습니다.",
    "INDIVIDUAL_LIQUIDATION_UNAVAILABLE": "현재 선택한 종목은 개별청산할 수 없습니다.",
    "SERVER_NOT_CONNECTED": "키움 서버에 로그인되어 있지 않습니다.",
    "STATE_UNAVAILABLE": "종목의 운영 상태를 확인할 수 없습니다.",
    "NO_HOLDING": "보유 수량이 없습니다.",
    "LIQUIDATION_IN_PROGRESS": "청산 절차가 이미 진행 중입니다.",
    "ALREADY_REQUESTED": "이미 요청한 작업입니다.",
    "NOT_CANCELABLE": "현재 상태에서는 취소할 수 없습니다.",
    "NOT_CURRENT_PARTICIPANT": "현재 운영 대상 종목이 아닙니다.",
    "ATS_SESSION_NOT_SELECTED": "청산할 거래 세션을 선택하세요.",
    "SESSION_NOT_ALLOWED": "현재 거래 세션에서는 처리할 수 없습니다.",
}

_INTERNAL_UI_TERMS = (
    "registry",
    "canonical",
    "recovery",
    "instance",
    "repository",
    "writer",
    "mutation",
    "participant",
    "current-session",
    "authority",
    "sot",
    "runtime",
)


def user_reason_message(reason_code: object, *, fallback: str = "요청을 처리할 수 없습니다.") -> str:
    """Translate one internal reason code without echoing the code itself."""

    raw = str(reason_code or "").strip()
    code = raw.upper()
    mapped = _USER_REASON_MESSAGES.get(code)
    if mapped:
        return mapped
    if raw and not raw.isascii() and not any(term in raw.lower() for term in _INTERNAL_UI_TERMS):
        return raw
    return str(fallback or "요청을 처리할 수 없습니다.")


def user_reason_messages(
    reason_codes: object,
    *,
    fallback: str = "요청을 처리할 수 없습니다.",
) -> tuple[str, ...]:
    """Translate a diagnostic reason list while suppressing raw code/detail text."""

    values = reason_codes if isinstance(reason_codes, (list, tuple, set)) else (reason_codes,)
    messages: list[str] = []
    for value in values:
        code = str(value or "").partition(":")[0].strip()
        message = user_reason_message(code, fallback=fallback)
        if message and message not in messages:
            messages.append(message)
    return tuple(messages) or (str(fallback or "요청을 처리할 수 없습니다."),)
