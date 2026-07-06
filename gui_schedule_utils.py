# -*- coding: utf-8 -*-
"""
gui_schedule_utils.py

스케줄/시간 설정 관련 순수 보조 유틸리티.
UI 위젯에 직접 의존하지 않는다.
"""

from __future__ import annotations


def schedule_config_updates(start_time: str, end_buy_time: str) -> dict[str, object]:
    """
    종목별 스케줄 시간 변경에 사용할 config 업데이트 dict를 만든다.
    """
    return {
        "start_time": str(start_time).strip(),
        "end_buy_time": str(end_buy_time).strip(),
    }


def schedule_change_log_text(config_updates: dict[str, object] | None) -> str:
    """
    스케줄 시간 변경 내용을 changelog 문구로 변환한다.
    """
    if not config_updates:
        return ""

    start_time = str(config_updates.get("start_time", "")).strip()
    end_buy_time = str(config_updates.get("end_buy_time", "")).strip()
    if not start_time and not end_buy_time:
        return ""

    return f"매수시간: {start_time}~{end_buy_time}"


def schedule_status_suffix(config_updates: dict[str, object] | None) -> str:
    """
    상태바에 붙일 스케줄 시간 변경 요약 문구를 만든다.
    """
    log_text = schedule_change_log_text(config_updates)
    if not log_text:
        return ""

    return " / " + log_text
