# -*- coding: utf-8 -*-
"""
gui_ats_utils.py

수동운영 ATS(시간외) 관련 유틸/설정창 분리 파일.
주의: 1차 구조분리 단계이므로 기존 동작 로직은 변경하지 않는다.
"""

from __future__ import annotations

from datetime import datetime

from state_policy import (
    normalize_operation_mode,
    normalized_hhmmss_or_empty,
    read_operation_policy,
    seconds_from_hhmmss,
)
from manual_ats_runtime import manual_ats_runtime_selected_keys


def manual_ats_session_labels() -> dict[str, str]:
    """환경설정(operation_policy.json)의 추가시간 이름을 ATS 표시명으로 사용한다."""
    fallback = {"extra1": "추가1", "extra2": "추가2", "extra3": "추가3"}
    try:
        policy = read_operation_policy()
        sessions = policy.get("extra_sessions", []) if isinstance(policy, dict) else []
        if not isinstance(sessions, list):
            return fallback
        labels = dict(fallback)
        for index, key in enumerate(["extra1", "extra2", "extra3"]):
            if index >= len(sessions) or not isinstance(sessions[index], dict):
                continue
            name = str(sessions[index].get("name", "")).strip()
            if name:
                labels[key] = name
        return labels
    except Exception:
        return fallback


def manual_ats_visible_session_keys() -> tuple[str, ...]:
    """ATS settings popup entries enabled by operation policy.

    Existing policies without ``enabled`` predate visibility controls and remain
    visible for compatibility. Newly created default policies keep their
    explicit disabled values.
    """
    keys = ("extra1", "extra2", "extra3")
    try:
        policy = read_operation_policy()
        sessions = policy.get("extra_sessions", []) if isinstance(policy, dict) else []
        if not isinstance(sessions, list):
            return keys
        return tuple(
            key
            for index, key in enumerate(keys)
            if index >= len(sessions)
            or not isinstance(sessions[index], dict)
            or bool(sessions[index].get("enabled", True))
        )
    except Exception:
        return keys



def manual_ats_selected_keys_and_source(
    config: dict[str, object] | None,
    state: dict[str, object] | None = None,
) -> tuple[list[str], str]:
    """Return the persistent manual ATS selection for a continuous-mode stock."""
    if not isinstance(config, dict):
        return [], "none"

    if normalize_operation_mode(config.get("operation_mode", "SCHEDULED")) != "CONTINUOUS":
        return [], "none"

    runtime_keys = list(manual_ats_runtime_selected_keys(state))
    return (runtime_keys, "runtime") if runtime_keys else ([], "none")


def manual_ats_enabled_labels(
    config: dict[str, object] | None,
    state: dict[str, object] | None = None,
) -> list[str]:
    """수동운영 종목의 활성 ATS 구간 표시명 목록.

    1차에서는 상태판정 없이 운영 컬럼 표시만 담당한다.
    """
    selected_keys, _source = manual_ats_selected_keys_and_source(config, state)
    if not selected_keys:
        return []

    label_map = manual_ats_session_labels()
    fallback = {"extra1": "추가1", "extra2": "추가2", "extra3": "추가3"}
    return [str(label_map.get(key, fallback.get(key, key))) for key in selected_keys]


def operation_policy_time_range_seconds(
    section: dict[str, object],
    default_start: str = "09:00:00",
    default_end: str = "15:20:00",
) -> tuple[int, int] | None:
    """operation_policy 시간 섹션을 초 단위 시작/종료로 변환한다."""
    if not isinstance(section, dict):
        return None

    start_text = normalized_hhmmss_or_empty(section.get("start_time", default_start)) or default_start
    end_text = normalized_hhmmss_or_empty(section.get("end_time", default_end)) or default_end
    try:
        return seconds_from_hhmmss(start_text, default_start), seconds_from_hhmmss(end_text, default_end)
    except Exception:
        return None


def current_time_in_seconds(now_dt: datetime | None = None) -> int:
    current = now_dt or datetime.now()
    return current.hour * 3600 + current.minute * 60 + current.second


def seconds_in_range(current_seconds: int, start_seconds: int, end_seconds: int) -> bool:
    """자정 넘김 구간까지 포함한 시간 범위 판정."""
    if start_seconds == end_seconds:
        return False
    if start_seconds < end_seconds:
        return start_seconds <= current_seconds < end_seconds
    return current_seconds >= start_seconds or current_seconds < end_seconds


def auto_trade_setting_regular_market_active_now(now_dt: datetime | None = None) -> bool:
    """수동운영 기본 정규장 거래 가능 시간인지 판단한다."""
    policy = read_operation_policy()
    regular = policy.get("regular_market", {}) if isinstance(policy, dict) else {}
    seconds = operation_policy_time_range_seconds(
        regular,
        default_start="09:00:00",
        default_end="15:20:00",
    )
    if seconds is None:
        return False
    start_seconds, end_seconds = seconds
    return seconds_in_range(current_time_in_seconds(now_dt), start_seconds, end_seconds)


def manual_ats_session_definition(key: str) -> dict[str, object]:
    """extra1~3 키에 해당하는 시간외 구간 정의를 읽는다."""
    key_to_index = {"extra1": 0, "extra2": 1, "extra3": 2}
    index = key_to_index.get(key)
    if index is None:
        return {}

    policy = read_operation_policy()
    sessions = policy.get("extra_sessions", []) if isinstance(policy, dict) else []
    if not isinstance(sessions, list):
        return {}
    if index >= len(sessions) or not isinstance(sessions[index], dict):
        return {}
    return dict(sessions[index])


def manual_ats_active_now(
    config: dict[str, object] | None,
    state: dict[str, object] | None = None,
    now_dt: datetime | None = None,
) -> bool:
    """현재 시간이 해당 종목의 지속 ATS 선택 시간 안인지 판단한다.

    선택은 운영자가 해제할 때까지 유지되며 저장 날짜와 프로그램 세션은
    마지막 설정 시점의 메타데이터일 뿐 만료 조건이 아니다.
    """
    selected_keys, _source = manual_ats_selected_keys_and_source(config, state)
    if not selected_keys:
        return False

    current_seconds = current_time_in_seconds(now_dt)

    for key in selected_keys:
        session = manual_ats_session_definition(key)
        if not session:
            continue

        seconds = operation_policy_time_range_seconds(
            session,
            default_start="00:00:00",
            default_end="00:00:00",
        )
        if seconds is None:
            continue

        start_seconds, end_seconds = seconds
        if seconds_in_range(current_seconds, start_seconds, end_seconds):
            return True

    return False
