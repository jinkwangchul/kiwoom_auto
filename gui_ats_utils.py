# -*- coding: utf-8 -*-
"""
gui_ats_utils.py

수동운영 ATS(시간외) 관련 유틸/설정창 분리 파일.
주의: 1차 구조분리 단계이므로 기존 동작 로직은 변경하지 않는다.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import math
from typing import Callable

from state_policy import (
    effective_schedule_times,
    normalize_operation_mode,
    normalized_hhmmss_or_empty,
    read_operation_policy,
    seconds_from_hhmmss,
)
from manual_ats_runtime import (
    INVALID_ATS_EXECUTION_METHOD,
    manual_ats_runtime_execution_method_result,
    manual_ats_runtime_selected_keys,
    normalize_manual_ats_execution_method,
)


ATS_EXECUTION_METHOD_LABELS = {
    "ROUTINE": "루틴",
    "MARKET": "시장가",
    "CURRENT_PRICE": "현재가",
}


def manual_ats_execution_method_label(value: object) -> str:
    normalized = normalize_manual_ats_execution_method(value)
    return ATS_EXECUTION_METHOD_LABELS.get(normalized or "", "")


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


def auto_trade_operation_session_phase(
    config: dict[str, object],
    state: dict[str, object],
    *,
    now_dt: datetime | None = None,
    operation_policy_reader: Callable[[], dict[str, object]] | None = None,
    ats_session_reader: Callable[[str], dict[str, object]] | None = None,
) -> dict[str, object]:
    """Classify configured regular/ATS windows for retirement and display."""

    current = now_dt or datetime.now()
    current_seconds = current.hour * 3600 + current.minute * 60 + current.second
    mode = normalize_operation_mode(config.get("operation_mode", "SCHEDULED"))
    windows: list[tuple[str, int, int]] = []
    invalid_sessions: list[str] = []

    if mode == "SCHEDULED":
        policy_reader = operation_policy_reader or read_operation_policy
        policy = policy_reader()
        policy = policy if isinstance(policy, dict) else {}
        scheduled = policy.get("scheduled_operation", {})
        scheduled = scheduled if isinstance(scheduled, dict) else {}
        global_schedule = {
            "start_time": normalized_hhmmss_or_empty(
                scheduled.get("default_start_time", "09:00:00")
            )
            or "09:00:00",
            "end_buy_time": normalized_hhmmss_or_empty(
                scheduled.get("default_end_buy_time", "13:30:00")
            )
            or "13:30:00",
        }
        start_time, end_time, _individual = effective_schedule_times(
            config,
            global_schedule,
        )
        seconds = operation_policy_time_range_seconds(
            {"start_time": start_time, "end_time": end_time},
            default_start="09:00:00",
            default_end="13:30:00",
        )
        if seconds is None:
            invalid_sessions.append("scheduled")
        else:
            windows.append(("scheduled", seconds[0], seconds[1]))
    else:
        policy_reader = operation_policy_reader or read_operation_policy
        policy = policy_reader()
        manual = policy.get("manual_operation", {}) if isinstance(policy, dict) else {}
        if not isinstance(manual, dict):
            manual = {}
        if bool(manual.get("use_regular_market", True)):
            regular = policy.get("regular_market", {}) if isinstance(policy, dict) else {}
            seconds = operation_policy_time_range_seconds(
                regular if isinstance(regular, dict) else {},
                default_start="09:00:00",
                default_end="15:20:00",
            )
            if seconds is None:
                invalid_sessions.append("regular")
            else:
                windows.append(("regular", seconds[0], seconds[1]))

        session_reader = ats_session_reader or manual_ats_session_definition
        selected_keys, _source = manual_ats_selected_keys_and_source(config, state)
        for key in selected_keys:
            session = session_reader(key)
            if not session or not bool(session.get("enabled", True)):
                invalid_sessions.append(str(key))
                continue
            seconds = operation_policy_time_range_seconds(
                session,
                default_start="00:00:00",
                default_end="00:00:00",
            )
            if seconds is None or seconds[0] == seconds[1]:
                invalid_sessions.append(str(key))
                continue
            windows.append((str(key), seconds[0], seconds[1]))

    if invalid_sessions or not windows:
        return {
            "evaluable": False,
            "phase": "SESSION_EVIDENCE_INVALID",
            "mode": mode,
            "active": False,
            "future_session_exists": False,
            "final_session_ended": False,
            "sessions": tuple(windows),
            "active_sessions": (),
            "invalid_sessions": tuple(invalid_sessions),
        }
    if any(start >= end for _name, start, end in windows):
        return {
            "evaluable": False,
            "phase": "OVERNIGHT_SESSION_UNRESOLVED",
            "mode": mode,
            "active": False,
            "future_session_exists": False,
            "final_session_ended": False,
            "sessions": tuple(windows),
            "active_sessions": (),
            "invalid_sessions": (),
        }

    ordered = tuple(sorted(windows, key=lambda item: (item[1], item[2], item[0])))
    active_sessions = tuple(
        name for name, start, end in ordered if start <= current_seconds < end
    )
    active = bool(active_sessions)
    future = any(start > current_seconds for _name, start, _end in ordered)
    if active:
        phase = "ACTIVE_SESSION"
    elif current_seconds < ordered[0][1]:
        phase = "BEFORE_FIRST_SESSION"
    elif future:
        phase = "BETWEEN_SESSIONS"
    else:
        phase = "FINAL_SESSION_ENDED"
    return {
        "evaluable": True,
        "phase": phase,
        "mode": mode,
        "active": active,
        "future_session_exists": future,
        "final_session_ended": phase == "FINAL_SESSION_ENDED",
        "sessions": ordered,
        "active_sessions": active_sessions,
        "invalid_sessions": (),
    }


def ats_execution_method_active_for_phase(session_phase: dict[str, object]) -> bool:
    if session_phase.get("evaluable") is not True:
        return False
    if str(session_phase.get("mode") or "").strip().upper() != "CONTINUOUS":
        return False
    if str(session_phase.get("phase") or "").strip().upper() != "ACTIVE_SESSION":
        return False
    active_sessions = tuple(str(value or "").strip() for value in session_phase.get("active_sessions", ()))
    return "regular" not in active_sessions and any(
        value in {"extra1", "extra2", "extra3"} for value in active_sessions
    )


def auto_trade_operation_activation_phase(
    config: dict[str, object],
    state: dict[str, object],
    *,
    now_dt: datetime | None = None,
    session_phase: dict[str, object] | None = None,
    operation_policy_reader: Callable[[], dict[str, object]] | None = None,
) -> dict[str, object]:
    """Project transient operation/trade boundaries without persisting a phase."""

    current = now_dt or datetime.now()
    current_seconds = current_time_in_seconds(current)
    mode = normalize_operation_mode(config.get("operation_mode", "SCHEDULED"))
    policy_reader = operation_policy_reader or read_operation_policy
    policy = policy_reader()
    policy = policy if isinstance(policy, dict) else {}
    scheduled = policy.get("scheduled_operation", {})
    scheduled = scheduled if isinstance(scheduled, dict) else {}
    global_schedule = {
        "start_time": normalized_hhmmss_or_empty(
            scheduled.get("default_start_time", "09:00:00")
        )
        or "09:00:00",
        "end_buy_time": normalized_hhmmss_or_empty(
            scheduled.get("default_end_buy_time", "13:30:00")
        )
        or "13:30:00",
    }
    trade_start, _trade_end, _individual = effective_schedule_times(
        config,
        global_schedule,
    )
    trade_start_seconds = seconds_from_hhmmss(
        trade_start,
        global_schedule["start_time"],
    )

    if mode == "CONTINUOUS":
        regular = policy.get("regular_market", {})
        regular = regular if isinstance(regular, dict) else {}
        boundary_text = normalized_hhmmss_or_empty(
            regular.get("start_time", "09:00:00")
        ) or "09:00:00"
    else:
        boundary_text = global_schedule["start_time"]
    boundary_seconds = seconds_from_hhmmss(boundary_text, "09:00:00")
    boundary_reached = current_seconds >= boundary_seconds
    trade_window_started = current_seconds >= trade_start_seconds

    phase = session_phase or auto_trade_operation_session_phase(
        config,
        state,
        now_dt=current,
        operation_policy_reader=policy_reader,
    )
    phase_name = str(phase.get("phase") or "").strip().upper()
    active_sessions = tuple(
        str(value or "").strip() for value in phase.get("active_sessions", ())
    )
    ats_active = ats_execution_method_active_for_phase(phase)
    regular_active = (
        "scheduled" in active_sessions
        if mode == "SCHEDULED"
        else "regular" in active_sessions
    )

    if phase.get("evaluable") is not True:
        projection_phase = "SESSION_EVIDENCE_INVALID"
    elif ats_active:
        projection_phase = "ACTIVE_SESSION"
    elif regular_active:
        if not boundary_reached:
            projection_phase = "PRE_OPERATION_BOUNDARY"
        elif not trade_window_started:
            projection_phase = "WAITING_FOR_TRADE_WINDOW_AFTER_OPERATION_BOUNDARY"
        else:
            projection_phase = "ACTIVE_SESSION"
    elif phase_name == "BEFORE_FIRST_SESSION":
        projection_phase = (
            "PRE_OPERATION_BOUNDARY"
            if not boundary_reached
            else "WAITING_FOR_TRADE_WINDOW_AFTER_OPERATION_BOUNDARY"
        )
    elif phase_name == "BETWEEN_SESSIONS":
        projection_phase = (
            "PRE_OPERATION_BOUNDARY"
            if not boundary_reached
            else "INTER_SESSION_NON_TRADING_GAP"
        )
    elif phase_name == "FINAL_SESSION_ENDED":
        projection_phase = "FINAL_END"
    else:
        projection_phase = "SESSION_EVIDENCE_INVALID"

    return {
        "evaluable": phase.get("evaluable") is True,
        "projection_phase": projection_phase,
        "mode": mode,
        "operation_boundary": boundary_text,
        "operation_boundary_reached": boundary_reached,
        "trade_window_start": trade_start,
        "trade_window_started": trade_window_started,
        "actual_trading_session_active": projection_phase == "ACTIVE_SESSION",
        "ats_session_active": ats_active,
        "regular_session_active": regular_active,
        "session_phase": phase,
    }


def project_manual_ats_execution_order(
    order: dict[str, object],
    config: dict[str, object],
    state: dict[str, object],
    *,
    now_dt: datetime | None = None,
    current_price: object = None,
    current_price_getter: Callable[[str], object] | None = None,
    session_phase: dict[str, object] | None = None,
) -> dict[str, object]:
    """Apply the persisted ATS method to one existing BUY/SELL order in memory."""
    original = deepcopy(order) if isinstance(order, dict) else {}
    phase = session_phase or auto_trade_operation_session_phase(
        config if isinstance(config, dict) else {},
        state if isinstance(state, dict) else {},
        now_dt=now_dt,
    )
    base = {
        "ok": True,
        "applied": False,
        "execution_method": "ROUTINE",
        "order": original,
        "reason_code": "ATS_EXECUTION_METHOD_NOT_ACTIVE",
        "session_phase": phase,
    }
    side = str(original.get("side") or "").strip().upper()
    if side not in {"BUY", "SELL"} or not ats_execution_method_active_for_phase(phase):
        return base

    method_result = manual_ats_runtime_execution_method_result(state)
    if method_result.get("ok") is not True:
        return {
            **base,
            "ok": False,
            "execution_method": None,
            "reason_code": INVALID_ATS_EXECUTION_METHOD,
            "blocked_reasons": [INVALID_ATS_EXECUTION_METHOD],
            "method_result": method_result,
        }

    method = str(method_result.get("execution_method") or "ROUTINE")
    if method == "ROUTINE":
        return {
            **base,
            "execution_method": method,
            "reason_code": "ATS_EXECUTION_METHOD_ROUTINE",
            "method_result": method_result,
        }

    effective = deepcopy(original)
    order_intent = effective.get("order_intent")
    intent = deepcopy(order_intent) if isinstance(order_intent, dict) else {}
    if method == "MARKET":
        effective["price"] = 0
        intent["hoga"] = "MARKET"
    else:
        if current_price is None and callable(current_price_getter):
            try:
                current_price = current_price_getter(
                    str(original.get("code") or "").strip().lstrip("A")
                )
            except Exception:
                current_price = None
        if isinstance(current_price, bool):
            valid_price = False
        else:
            try:
                numeric_price = float(current_price)
                valid_price = math.isfinite(numeric_price) and numeric_price > 0
            except (TypeError, ValueError):
                valid_price = False
        if not valid_price:
            return {
                **base,
                "ok": False,
                "execution_method": method,
                "reason_code": "ATS_CURRENT_PRICE_UNAVAILABLE",
                "blocked_reasons": ["ATS_CURRENT_PRICE_UNAVAILABLE"],
                "method_result": method_result,
                "current_price": current_price,
            }
        effective["price"] = int(numeric_price) if numeric_price.is_integer() else numeric_price
        intent["hoga"] = "CURRENT_PRICE"
    effective["order_intent"] = intent
    return {
        **base,
        "applied": True,
        "execution_method": method,
        "order": effective,
        "reason_code": f"ATS_EXECUTION_METHOD_{method}_APPLIED",
        "method_result": method_result,
        "current_price": current_price if method == "CURRENT_PRICE" else None,
    }


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
