# -*- coding: utf-8 -*-
"""
등록확인루틴 routine.py

루틴 패키지 자동 인식용 엔트리 파일입니다.
현재 단계에서는 실제 매매 실행 로직을 직접 수행하지 않습니다.
메인 엔진에서 정책/안전검사/주문집행을 통제하는 구조를 유지합니다.
"""

ROUTINE_NAME = "등록확인루틴"
ROUTINE_API_VERSION = "0.1"
EXECUTION_ENABLED = False


def get_routine_info():
    """루틴 메타정보 반환."""
    return {
        "name": ROUTINE_NAME,
        "api_version": ROUTINE_API_VERSION,
        "execution_enabled": EXECUTION_ENABLED,
    }


def evaluate(context):
    """
    향후 루틴 신호 평가용 인터페이스 자리입니다.

    현재는 실제 매수/매도 신호를 반환하지 않습니다.
    주문 실행은 반드시 메인 엔진 안전로직을 통과해야 합니다.
    """
    return {
        "signal": "NONE",
        "reason": "routine package scaffold only",
    }
