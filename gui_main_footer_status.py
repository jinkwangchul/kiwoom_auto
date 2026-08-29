# -*- coding: utf-8 -*-

"""Operator-facing projection for the main-window footer status message."""

from __future__ import annotations

import re
from dataclasses import dataclass


OPERATOR_FOOTER_SUCCESS_COLOR = "#16A34A"
OPERATOR_FOOTER_FAILURE_COLOR = "#DC2626"
OPERATOR_FOOTER_PROGRESS_COLOR = "#D97706"
OPERATOR_FOOTER_STATE_COLOR = "#4B5563"
OPERATOR_FOOTER_WARNING_COLOR = "#D97706"
OPERATOR_FOOTER_PRIORITY_HOLD_MS = 2500


@dataclass(frozen=True)
class OperatorFooterProjection:
    text: str
    category: str
    color: str
    priority: int


_CATEGORY_STYLE = {
    "success": (OPERATOR_FOOTER_SUCCESS_COLOR, 2),
    "failure": (OPERATOR_FOOTER_FAILURE_COLOR, 5),
    "progress": (OPERATOR_FOOTER_PROGRESS_COLOR, 3),
    "state": (OPERATOR_FOOTER_STATE_COLOR, 1),
    "warning": (OPERATOR_FOOTER_WARNING_COLOR, 4),
}

_CANONICAL_CATEGORIES = {
    "▷ 로그인 중": "progress",
    "✓ 서버 연결 완료": "success",
    "✕ 서버 연결 실패": "failure",
    "✕ 서버 연결 끊김": "failure",
    "▷ 서버 재연결 중": "progress",
    "▷ 서버 인증 중": "progress",
    "✓ 서버 인증 완료": "success",
    "✕ 서버 인증 실패": "failure",
    "✕ 사용자 정보 확인 실패": "failure",
    "✕ 버전 확인 실패": "failure",
    "▷ 준비 중": "progress",
    "✓ 준비 완료": "success",
    "● 운영 대기": "state",
    "● 비운영 상태": "state",
    "※ 운영 대상 없음": "warning",
    "▷ 종료 처리 중": "progress",
    "▷ 시장데이터 수신 대기": "progress",
    "✓ 시장데이터 수신 정상": "success",
    "✕ 시장데이터 수신 중단": "failure",
    "▷ 운영 시작 대기": "progress",
    "✓ 운영 시작": "success",
    "✓ 운영 정지": "success",
    "✕ 운영 시작 실패": "failure",
    "✕ 운영 정지 실패": "failure",
    "● 감시전용 운영": "state",
    "✕ 긴급정지": "failure",
    "✓ 긴급정지 해제": "success",
    "✓ 설정 저장 완료": "success",
    "✕ 작업 처리 실패": "failure",
}

_INTERNAL_FOOTER_MARKERS = (
    "real_ready",
    "executable",
    "approval",
    "policy gate",
    "execution preview",
    "queue candidate",
    "order_queued",
    "runtime commit",
    "dispatch claim",
    "dispatch",
    "final send gate",
    "manual queue",
    "manual sendorder",
    "sendorder preview",
    "sendorder",
    "broker_order_no",
    "execution_id",
    "order_id",
    "request_hash",
    "revision",
    "주문후보검증",
    "실자동매매 주문처리",
    "루틴 신호 로그",
    "command 처리",
    "시간정책 자동반영",
    "주문 후보",
    "분봉조회",
    "candles.json",
    "수동 실주문 후보",
    "수동 queue",
    "manual cancel",
    "manual modify",
    "runtime 상태를 갱신",
    "시간정책 상태를 갱신",
)


def operator_footer_canonical_messages() -> tuple[str, ...]:
    return (
        "▷ 로그인 중",
        "✓ 서버 연결 완료",
        "✕ 로그인 실패 ({code})",
        "✕ 서버 연결 실패",
        "✕ 서버 연결 끊김",
        "▷ 서버 재연결 중",
        "▷ 서버 인증 중",
        "✓ 서버 인증 완료",
        "✕ 서버 인증 실패",
        "✕ 사용자 정보 확인 실패",
        "✕ 버전 확인 실패",
        *tuple(_CANONICAL_CATEGORIES.keys())[10:],
    )


def _projection(text: str, category: str) -> OperatorFooterProjection:
    color, priority = _CATEGORY_STYLE[category]
    return OperatorFooterProjection(text, category, color, priority)


def _canonical_projection(message: str) -> OperatorFooterProjection | None:
    category = _CANONICAL_CATEGORIES.get(message)
    if category is not None:
        return _projection(message, category)
    match = re.fullmatch(r"✕ 로그인 실패 \(([^)]+)\)", message)
    if match:
        return _projection(message, "failure")
    return None


def _contains_any(message: str, values: tuple[str, ...]) -> bool:
    return any(value in message for value in values)


def _message_count(message: str, label: str) -> int | None:
    match = re.search(rf"{re.escape(label)}\s*(\d+)", message)
    return int(match.group(1)) if match else None


def project_operator_footer_message(
    raw_message: object,
) -> OperatorFooterProjection | None:
    """Map a raw status-bar message to the small operator footer vocabulary.

    ``None`` means that the message remains available to its existing Event/Log
    owners but must not replace the operator-facing footer text.
    """

    message = str(raw_message or "").strip()
    if not message:
        return _projection("✓ 준비 완료", "success")

    canonical = _canonical_projection(message)
    if canonical is not None:
        return canonical

    lowered = message.casefold()
    if _contains_any(lowered, _INTERNAL_FOOTER_MARKERS):
        return None

    login_failure = re.fullmatch(r"login failed:\s*([^\s]+)", lowered)
    if login_failure:
        return _projection(
            f"✕ 로그인 실패 ({login_failure.group(1)})",
            "failure",
        )
    if lowered == "user info exchange failed":
        return _projection("✕ 사용자 정보 확인 실패", "failure")
    if lowered == "version processing failed":
        return _projection("✕ 버전 확인 실패", "failure")
    if lowered == "server connection failed":
        return _projection("✕ 서버 연결 실패", "failure")
    if lowered == "login succeeded" or "로그인 상태: 연결됨" in message:
        return _projection("✓ 서버 연결 완료", "success")
    if lowered == "kiwoom api disconnected" or message in {
        "미연결 상태",
        "로그인 상태: 실패",
    }:
        return _projection("✕ 서버 연결 끊김", "failure")
    if "재연결" in message:
        return _projection("▷ 서버 재연결 중", "progress")
    if "로그인 요청" in message and not _contains_any(
        message,
        ("오류", "못했습니다", "실패"),
    ):
        return _projection("▷ 로그인 중", "progress")
    if _contains_any(
        lowered,
        (
            "openapi를 사용할 수 없습니다",
            "openapi 상태",
            "api가 초기화되지",
            "api 사용불가",
        ),
    ):
        return _projection("✕ 서버 연결 실패", "failure")
    if _contains_any(
        message,
        ("키움 서버에 로그인되어 있지", "서버 연결 및 계좌 복구"),
    ):
        return _projection("✕ 서버 연결 실패", "failure")

    if "긴급정지 상태" in message:
        return _projection("✕ 긴급정지", "failure")
    if "정지해제" in message:
        failed_count = _message_count(message, "실패")
        remaining_count = _message_count(message, "긴급정지 잔존")
        if (
            "차단" in message
            or "미완료" in message
            or (failed_count is not None and failed_count > 0)
            or (remaining_count is not None and remaining_count > 0)
        ):
            return _projection("✕ 작업 처리 실패", "failure")
        if _contains_any(message, ("완료", "정상", "해제")):
            return _projection("✓ 긴급정지 해제", "success")
    if "긴급정지" in message:
        return _projection("✕ 긴급정지", "failure")

    if "서버 인증" in message:
        if _contains_any(message, ("중", "대기", "진행")):
            return _projection("▷ 서버 인증 중", "progress")
        if _contains_any(message, ("완료", "성공")):
            return _projection("✓ 서버 인증 완료", "success")
        if _contains_any(message, ("실패", "오류", "중단", "불가")):
            return _projection("✕ 서버 인증 실패", "failure")
    if "운영 재개 승인 완료" in message:
        return _projection("✓ 서버 인증 완료", "success")
    if _contains_any(
        message,
        (
            "Recovery에 실패",
            "Recovery 데이터를 읽을 수 없습니다",
            "Recovery 정보가 일치하지",
            "이전 Recovery 정보",
            "계좌비밀번호 입력 기능",
            "계좌비밀번호 입력창",
        ),
    ):
        return _projection("✕ 서버 인증 실패", "failure")
    if "복구 상태를 확인" in message and _contains_any(
        message,
        ("오류", "실패", "못했습니다"),
    ):
        return _projection("✕ 서버 인증 실패", "failure")
    if _contains_any(message, ("Recovery가 진행 중", "Recovery가 아직 완료", "복구 상태를 확인")):
        return _projection("▷ 서버 인증 중", "progress")
    if _contains_any(
        message,
        (
            "운영할 계좌를 선택",
            "계좌 복구 확인 후",
            "복구가 필요한 종목",
            "복구 검토 대상",
        ),
    ):
        return _projection("✕ 서버 인증 실패", "failure")

    if _contains_any(message, ("준비 중", "초기화 중")):
        return _projection("▷ 준비 중", "progress")
    if message == "준비 완료":
        return _projection("✓ 준비 완료", "success")
    if _contains_any(message, ("종료 처리 중", "종료 중")):
        return _projection("▷ 종료 처리 중", "progress")

    if "시장데이터" in message:
        if _contains_any(message, ("중단", "실패", "오류")):
            return _projection("✕ 시장데이터 수신 중단", "failure")
        if _contains_any(message, ("대기", "수신 전", "준비 중")):
            return _projection("▷ 시장데이터 수신 대기", "progress")
        if _contains_any(message, ("정상", "완료", "수신 중")):
            return _projection("✓ 시장데이터 수신 정상", "success")

    if "감시전용" in message or "신호평가 전용" in message:
        return _projection("● 감시전용 운영", "state")
    if _contains_any(
        message,
        (
            "운영시작 대상이 없",
            "운영 시작 대상이 없",
            "등록된 종목이 없습니다",
            "운영 대상 종목이 없습니다",
            "청산 대상 없음",
            "대상 0",
            "적용: 0개",
        ),
    ):
        return _projection("※ 운영 대상 없음", "warning")
    if "대상종목" in message and "운영시작" in message:
        started_count = _message_count(message, "운영시작")
        already_count = _message_count(message, "기운영중")
        unavailable_count = _message_count(message, "운영불가")
        if started_count is not None and started_count > 0:
            return _projection("✓ 운영 시작", "success")
        if (
            already_count is not None
            and already_count > 0
            and unavailable_count == 0
        ):
            return _projection("✓ 운영 시작", "success")
        return _projection("✕ 운영 시작 실패", "failure")
    start_count = _message_count(message, "운영 시작")
    if start_count is not None:
        return _projection(
            "✓ 운영 시작" if start_count > 0 else "✕ 운영 시작 실패",
            "success" if start_count > 0 else "failure",
        )
    if "운영을 시작했습니다" in message or "이미 운영 중" in message:
        return _projection("✓ 운영 시작", "success")
    if "운영 시작" in message or "운영시작" in message:
        if _contains_any(message, ("대기", "확인 중")):
            return _projection("▷ 운영 시작 대기", "progress")
        if _contains_any(
            message,
            ("실패", "불가", "오류", "못했습니다", "할 수 없", "완료되지"),
        ):
            return _projection("✕ 운영 시작 실패", "failure")
        if _contains_any(message, ("완료", "시작했습니다", "기운영중", "이미 운영 중")):
            return _projection("✓ 운영 시작", "success")
    if _contains_any(
        message,
        (
            "운영을 시작",
            "운영을 다시 시작",
            "운영 시작에 필요한",
            "운영시작 가능",
            "정상 운영이 이미 종료",
            "매매 운영 시간이 아닙니다",
            "검토관리 대상입니다",
            "검토 대상으로 분리",
            "필수 운영 설정이 완료되지",
            "필수 설정이 완료되지",
            "초회 매수 주수가 설정되지",
            "시작금액을 확정할 수 없",
            "운영 상태 데이터를 읽을 수 없",
            "마감 또는 청산 절차가 진행 중",
        ),
    ):
        return _projection("✕ 운영 시작 실패", "failure")
    if "운영 정지" in message or "운영정지" in message:
        if _contains_any(message, ("실패", "불가", "오류", "못했습니다")):
            return _projection("✕ 운영 정지 실패", "failure")
        return _projection("✓ 운영 정지", "success")
    if "운영이 종료" in message:
        return _projection("✓ 운영 정지", "success")
    if "운영 대기" in message:
        return _projection("● 운영 대기", "state")
    if "비운영" in message:
        return _projection("● 비운영 상태", "state")
    if message.startswith("운영중 유지:") and "\n" not in message:
        return _projection("✓ 운영 시작", "success")
    if _contains_any(
        message,
        (
            "수동운영 최종 세션 종료:",
            "시간운영 종료:",
            "검토관리 필요:",
            "복구 준비 미완료:",
            "복구 실패:",
            "복구 검토 필요:",
            "마감/청산 진행:",
            "운영 시작 불가:",
        ),
    ):
        return _projection("✕ 운영 시작 실패", "failure")

    if "검토정지" in message:
        changed_count = _message_count(message, "변경")
        failed_count = _message_count(message, "실패")
        if changed_count is not None and changed_count > 0:
            return _projection("✓ 운영 정지", "success")
        if failed_count is not None and failed_count > 0:
            return _projection("✕ 운영 정지 실패", "failure")
    if "거래권한을 변경할 종목" in message:
        return _projection("✕ 작업 처리 실패", "failure")
    if "마감정책이 취소되었습니다" in message:
        return _projection("✓ 설정 저장 완료", "success")
    if "마감정책 취소 대상이 아닙니다" in message:
        return _projection("✕ 작업 처리 실패", "failure")

    changed_count = _message_count(message, "변경")
    blocked_count = _message_count(message, "차단")
    if changed_count is not None and blocked_count is not None:
        if changed_count > 0:
            return _projection("✓ 설정 저장 완료", "success")
        if blocked_count > 0:
            return _projection("✕ 작업 처리 실패", "failure")
    applied_count = _message_count(message, "성공")
    if applied_count is not None and blocked_count is not None:
        if applied_count > 0:
            return _projection("✓ 설정 저장 완료", "success")
        if blocked_count > 0:
            return _projection("✕ 작업 처리 실패", "failure")

    if _contains_any(message, ("취소", "선택 해제", "전체 종목 선택")):
        return None
    if _contains_any(
        message,
        (
            "설정 완료",
            "설정 변경 완료",
            "변경 완료",
            "저장 완료",
            "등록해제 완료",
            "전환 완료",
            "리셋 완료",
            "운영 제외",
            "조기마감 적용",
        ),
    ):
        return _projection("✓ 설정 저장 완료", "success")
    if _contains_any(message, ("설정 오류", "저장 실패")):
        return _projection("✕ 작업 처리 실패", "failure")

    if _contains_any(
        message,
        (
            "오류",
            "실패",
            "불가",
            "차단",
            "못했습니다",
            "할 수 없습니다",
            "읽을 수 없습니다",
            "확인하지 못",
        ),
    ):
        return _projection("✕ 작업 처리 실패", "failure")
    return None


def should_defer_operator_footer_message(
    *,
    current_priority: int,
    incoming_priority: int,
    hold_until: float,
    now: float,
) -> bool:
    return now < hold_until and incoming_priority < current_priority
