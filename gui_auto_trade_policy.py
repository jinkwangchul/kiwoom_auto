# -*- coding: utf-8 -*-
"""
gui_auto_trade_policy.py

자동매매설정창의 시간/운영/청산 판정 헬퍼 모듈.
- 시간정책 표시
- 조기마감/자동마감 메타데이터
- 청산정책 표시/활성 판정
- ATS 장후 차단 판정

주의:
- QTableWidget 등 화면 직접 조작은 포함하지 않는다.
"""

from __future__ import annotations

from datetime import date, datetime

from state_policy import (
    normalize_operation_mode,
    auto_trade_status_display,
    normalized_hhmmss_or_empty,
    operation_text_and_color,
    scheduled_status_for_now,
    seconds_from_hhmmss,
    status_after_operation_mode_change,
)

from gui_operation_environment import read_operation_policy
from gui_auto_trade_display import auto_trade_setting_display_status, display_status_text_for_gui
from gui_auto_trade_runtime import now_text



def auto_trade_setting_ats_after_regular_blocked(
    config: dict[str, object],
    display_status: str,
    liquidation_text: str,
    state: dict[str, object] | None,
) -> bool:
    """정규장 이후 ATS 매수/매도를 막는 실제 청산/완료 조건.

    v2.2 기준으로 조기마감/자동마감은 그 자체가 추가매수 금지 상태가 아니다.
    따라서 화면 표시가 조기마감/자동마감이거나 조기마감 메타가 있다는 이유만으로
    ATS 신호를 차단하지 않는다. 실제 청산 진행/완료/대상없음 notice 또는
    일반 청산정책이 있는 경우만 차단한다.
    """
    raw_status = str((state or {}).get("status", "")).strip().upper()
    operation_notice = str((state or {}).get("operation_notice", "")).strip().upper()

    if raw_status in {
        "AUTO_CLOSING",
        "AUTO_CLOSED",
        "EARLY_CLOSING",
        "EARLY_CLOSED",
        "LIQUIDATION",
        "LIQUIDATING",
        "LIQUIDATED",
    }:
        return True

    if operation_notice.startswith(("AUTO_CLOSE_NO_TARGET", "EARLY_CLOSE_NO_TARGET", "LIQUIDATION")):
        return True

    # 일반 청산정책이 있으면 장후 ATS 신규 매수/매도는 금지.
    # 이월과 '-'는 차단하지 않는다.
    return str(liquidation_text).strip() not in ("", "-", "이월")


def auto_trade_setting_trade_started(state: dict[str, object]) -> bool:
    """매매시작 버튼을 통해 자동 시간판정 대상에 올라간 상태인지 표시한다.

    중요:
    - 프로그램 재시작 안전초기화 이후에는 과거 status 값이 남아 있어도 현황 회색/시작 OFF로 본다.
    - 매매시작 버튼이 다시 실행되어 trade_started_at이 startup_reset_at 이후로 찍힌 경우만 ON이다.
    """
    if not isinstance(state, dict):
        return False

    reset_reason = str(state.get("startup_reset_reason", "") or "").strip().upper()
    reset_at = str(state.get("startup_reset_at", "") or "").strip()
    started_at = str(state.get("trade_started_at", "") or "").strip()
    if reset_reason == "PROGRAM_RESTART_FORCE_STOP" and reset_at:
        # 재시작 리셋 이후 다시 매매시작한 흔적이 없으면 무조건 현황 회색/시작 OFF.
        # 문자열 포맷은 now_text()의 YYYY-MM-DD HH:MM:SS라서 같은 포맷끼리 비교 가능하다.
        if not started_at or started_at <= reset_at:
            return False

    if "trade_enabled" in state:
        value = state.get("trade_enabled")
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() not in ("false", "0", "no", "n", "off", "정지", "")

    raw_status = str(state.get("status", "STOPPED")).strip().upper()
    return raw_status not in ("STOPPED", "STOP", "MANUAL_STOPPED")


def auto_trade_setting_current_session_trade_started(
    window,
    persisted_trade_started: bool,
) -> bool:
    if not persisted_trade_started:
        return False

    checker = getattr(window, "startup_recovery_session_ready", None)
    if not callable(checker):
        return True

    try:
        return bool(checker(refresh=False))
    except Exception:
        return False


def auto_trade_setting_display_status_for_current_session(
    state: dict[str, object],
    config: dict[str, object],
    *,
    holding_qty: int,
    buy_pending_qty: object = 0,
    sell_pending_qty: object = 0,
    current_session_trade_started: bool,
    persisted_trade_started: bool | None = None,
) -> str:
    """Return the production display status for the current GUI session."""
    if not isinstance(state, dict):
        state = {}
    if not isinstance(config, dict):
        config = {}

    raw_state_status = state.get("status", "STOPPED")
    raw_status_key = str(raw_state_status or "STOPPED").strip().upper() or "STOPPED"
    trade_started = (
        auto_trade_setting_trade_started(state)
        if persisted_trade_started is None
        else bool(persisted_trade_started)
    )

    if auto_trade_setting_should_preserve_raw_status(state, raw_state_status):
        raw_display_status = display_status_text_for_gui(raw_state_status)
    elif not current_session_trade_started:
        raw_display_status = display_status_text_for_gui("WAIT_BUY")
    elif auto_trade_setting_no_next_step_notice(state):
        raw_display_status = display_status_text_for_gui("WAIT_BUY")
    elif auto_trade_setting_liquidation_phase_active(config, holding_qty, state=state):
        raw_display_status = display_status_text_for_gui("WAIT_BUY")
    elif (
        not auto_trade_setting_is_after_regular_end()
        and auto_trade_setting_early_close_requested(state)
        and auto_trade_setting_has_close_progress_quantity(holding_qty, sell_pending_qty)
    ):
        raw_display_status = display_status_text_for_gui("EARLY_CLOSE")
    elif raw_status_key in {"STOPPED", "STOP", "MANUAL_STOPPED"} or not trade_started:
        raw_display_status = display_status_text_for_gui("STOPPED")
    else:
        mode = normalize_operation_mode(config.get("operation_mode", "SCHEDULED"))
        policy_status = status_after_operation_mode_change(mode, config)
        policy_display = auto_trade_setting_display_status(display_status_text_for_gui(policy_status))
        if policy_display in ("자동마감", "조기마감") and not auto_trade_setting_has_unresolved_quantity(
            holding_qty,
            buy_pending_qty,
            sell_pending_qty,
        ):
            raw_display_status = display_status_text_for_gui("WAIT_BUY")
        else:
            raw_display_status = display_status_text_for_gui(policy_status)

    return auto_trade_setting_display_status(raw_display_status)


def auto_trade_setting_should_preserve_raw_status(state: dict[str, object], status: object) -> bool:
    """시간정책 자동 재판정에서 그대로 유지할 상태인지 판단한다.

    긴급정지/검토계열은 항상 보호한다.
    조기마감은 운영자가 실제로 조기마감을 요청한 흔적이 있을 때만 보호한다.
    과거 테스트 잔류 status=EARLY_CLOSE 값만으로는 화면/시간정책을 고정하지 않는다.
    """
    raw = str(status or "STOPPED").strip().upper() or "STOPPED"
    if raw in {
        "EMERGENCY_STOPPED",
        "EMERGENCY_STOP",
        "EMERGENCY",
        "REVIEW_REQUIRED",
        "REVIEW",
        "FORCE_CLOSE",
        "FORCE_LIQUIDATION",
    }:
        return True

    if raw in {"EARLY_CLOSE", "EARLY_CLOSING", "EARLY_CLOSED"}:
        # 조기마감은 상태 고정값이 아니다.
        # 운영자가 시작시킨 자동마감성 명령이며, 화면 상태는 감시/대기 흐름을 사용한다.
        # 따라서 과거 EARLY_CLOSE 상태값은 시간정책/감시대기 표시를 막지 않는다.
        return False

    return False


def auto_trade_setting_no_next_step_notice(state: dict[str, object] | None) -> bool:
    """정상 흐름이지만 다음 절차로 진행할 대상이 없어 주황 현황으로 표시할 상태.

    확정 정책:
    - 주황은 조기/자동마감 이벤트 중 대상 없음으로 절차를 수행하지 않은 상황 표시다.
    - 프로그램 재시작/긴급정지 해제/매매시작 초기복구 같은 리셋 동작에서는 이전 주황 사유를 제거하거나 무시한다.
    - 정규장 설정 종료시간이 지나면 해당 이벤트 표시는 종료되고 정상 감시/대기 기준으로 복귀한다.
    """
    if not isinstance(state, dict):
        return False

    reset_reason = str(state.get("startup_reset_reason", "") or "").strip().upper()
    reset_at = str(state.get("startup_reset_at", "") or "").strip()
    started_at = str(state.get("trade_started_at", "") or "").strip()
    if reset_reason == "PROGRAM_RESTART_FORCE_STOP" and reset_at:
        if not started_at or started_at <= reset_at:
            return False

    notice = str(state.get("operation_notice", "")).strip().upper()
    if notice not in {
        "NO_CLOSE_TARGET",
        "AUTO_CLOSE_NO_TARGET",
        "EARLY_CLOSE_NO_TARGET",
        "LIQUIDATION_CURRENT_PRICE_CARRYOVER",
    }:
        return False
    if auto_trade_setting_is_after_regular_end():
        return False
    return True


def short_close_method_text(value: object) -> str:
    """마감/청산 방식 표시명을 짧게 통일한다."""
    text = str(value or "").strip()
    mapping = {
        "루틴매도신호": "루틴",
        "루틴매도": "루틴",
        "루틴": "루틴",
        "시장가": "시장가",
        "현재가": "현재가",
        "익절/손절": "익/손",
        "익절손절": "익/손",
        "익/손": "익/손",
        "이월": "이월",
        "즉시청산": "즉시청산",
    }
    return mapping.get(text, text or "-")


def compact_operation_time_range(text: object) -> str:
    """자동매매설정창 운영 컬럼 표시를 HH:MM~HH:MM으로 압축한다."""
    raw = str(text or "").strip()
    if not raw or raw == "-" or "~" not in raw:
        return raw
    left, right = raw.split("~", 1)

    def trim_time(value: str) -> str:
        parts = value.strip().split(":")
        if len(parts) >= 2:
            return f"{parts[0].zfill(2)}:{parts[1].zfill(2)}"
        return value.strip()

    return f"{trim_time(left)}~{trim_time(right)}"


def operation_policy_section(name: str) -> dict[str, object]:
    """operation_policy.json 상위 섹션을 안전하게 읽는다."""
    policy = read_operation_policy()
    section = policy.get(name, {})
    return section if isinstance(section, dict) else {}


def auto_trade_setting_close_timestamp_later(value: object, base: str) -> bool:
    """YYYY-MM-DD HH:MM:SS 문자열 기준으로 base 이후 시각인지 안전하게 판정한다."""
    text = str(value or "").strip()
    if not text or not base:
        return False
    try:
        return text > base
    except Exception:
        return False


def auto_trade_setting_early_close_metadata_is_stale(state: dict[str, object] | None) -> bool:
    """이미 정상 복귀/재시작/매매시작이 지난 조기마감 잔여 메타인지 판단한다.

    조기마감 메타는 해당 마감 이벤트 동안만 유효하다.
    이후 복귀, 재시작 리셋 해제, 새 매매시작 시각이 조기마감 요청시각보다 늦으면
    과거 조기마감 흔적으로 보고 화면 표시와 청산정책에서 제외한다.
    """
    if not isinstance(state, dict):
        return False

    early_at = str(state.get("early_close_requested_at", "") or "").strip()
    if not early_at:
        return False

    later_keys = [
        "review_returned_at",
        "resumed_at",
        "trade_started_at",
        "startup_reset_cleared_at",
        "startup_reset_at",
        "trade_stopped_at",
    ]
    return any(auto_trade_setting_close_timestamp_later(state.get(key), early_at) for key in later_keys)


def clear_early_close_runtime_metadata_only(state: dict[str, object]) -> dict[str, object]:
    """정상 복귀 후 남아있는 조기마감/청산정책 잔여 메타만 제거한다."""
    state["early_close_requested_at"] = ""
    state["early_close_source"] = ""
    state["early_close_method"] = ""
    state["early_close_policy"] = {}
    if str(state.get("liquidation_policy_reason", "")).strip().upper() == "EARLY_CLOSE":
        state["liquidation_policy_reason"] = ""
    state["liquidation_policy_forced"] = False
    state = clear_close_routine_final_sell_metadata(state)
    state["updated_at"] = now_text()
    return state


def auto_trade_setting_early_close_requested(state: dict[str, object] | None) -> bool:
    """조기마감 명령이 현재 적용 중인지 판단한다.

    과거 조기마감 메타가 정상 복귀/재시작/새 매매시작 이후에도 state.json에 남아 있으면
    현재 조기마감으로 보지 않는다.
    """
    if not isinstance(state, dict):
        return False
    if auto_trade_setting_early_close_metadata_is_stale(state):
        return False
    if bool(state.get("liquidation_policy_forced", False)):
        return True
    if str(state.get("liquidation_policy_reason", "")).strip().upper() == "EARLY_CLOSE":
        return True
    if str(state.get("early_close_requested_at", "")).strip():
        return True
    if str(state.get("early_close_source", "")).strip():
        return True
    raw_status = str(state.get("status", "")).strip().upper()
    return raw_status in {"EARLY_CLOSE", "EARLY_CLOSING", "EARLY_CLOSED"}


def clear_auto_close_runtime_metadata(state: dict[str, object]) -> dict[str, object]:
    """보유/미체결이 없는 마감 상태를 감시/대기로 되돌릴 때 잔여 메타를 제거한다."""
    state["status"] = "WAIT_BUY"
    state["buy_enabled"] = False
    state["early_close_requested_at"] = ""
    state["early_close_source"] = ""
    state["early_close_method"] = ""
    state["early_close_policy"] = {}
    state["auto_close_method"] = ""
    state["auto_close_policy"] = {}
    state = clear_close_routine_final_sell_metadata(state)
    state["liquidation_policy_forced"] = False
    state["liquidation_policy_reason"] = ""
    state["operation_notice"] = "NO_CLOSE_TARGET"
    state["operation_notice_reason"] = "마감 대상 없음"
    state["operation_notice_at"] = now_text()
    state["updated_at"] = now_text()
    return state


def close_method_from_state_or_policy(
    state: dict[str, object] | None,
    state_method_key: str,
    state_policy_key: str,
    policy_key: str,
    default_method: str,
) -> str:
    """종목 상태값을 우선하고 없으면 환경설정값을 사용하는 마감 방식 표시값."""
    if isinstance(state, dict):
        direct_method = str(state.get(state_method_key, "")).strip()
        if direct_method:
            return direct_method

        state_policy = state.get(state_policy_key, {})
        if isinstance(state_policy, dict):
            policy_method = str(state_policy.get("method", "")).strip()
            if policy_method:
                return policy_method

    return str(operation_policy_section(policy_key).get("method", default_method)).strip() or default_method


def auto_trade_setting_close_routine_mode_active(
    state: dict[str, object] | None,
    display_status: str = "",
) -> bool:
    """조기/자동마감이 루틴 방식으로 진행 중인지 판단한다.

    정책 기준:
    - 조기/자동마감의 루틴 방식은 1차 리셋 활동이다.
    - 첫 매도신호 전까지는 루틴 매수/매도 신호를 메인 주문판정 계층으로 넘길 수 있다.
    - 첫 매도신호가 주문 처리된 뒤에는 추가 매수/매도 주문을 차단한다.
    """
    if not isinstance(state, dict):
        return False

    if auto_trade_setting_early_close_requested(state):
        method = close_method_from_state_or_policy(
            state,
            "early_close_method",
            "early_close_policy",
            "early_close",
            "루틴",
        )
        return short_close_method_text(method) == "루틴"

    status_text = auto_trade_setting_display_status(display_status)
    has_auto_close_meta = bool(
        str(state.get("auto_close_requested_at", "") or "").strip()
        or str(state.get("auto_close_source", "") or "").strip()
        or str(state.get("auto_close_method", "") or "").strip()
        or isinstance(state.get("auto_close_policy"), dict) and bool(state.get("auto_close_policy"))
    )
    if status_text == "자동마감" or has_auto_close_meta:
        method = close_method_from_state_or_policy(
            state,
            "auto_close_method",
            "auto_close_policy",
            "auto_close",
            "루틴매도신호",
        )
        return short_close_method_text(method) == "루틴"

    return False


def auto_trade_setting_close_routine_final_sell_ordered(state: dict[str, object] | None) -> bool:
    """조기/자동마감 루틴 방식에서 마지막 매도주문이 이미 처리됐는지 확인한다."""
    if not isinstance(state, dict):
        return False
    if bool(state.get("close_routine_final_sell_ordered", False)):
        return True
    return bool(str(state.get("close_routine_final_sell_ordered_at", "") or "").strip())


def clear_close_routine_final_sell_metadata(state: dict[str, object]) -> dict[str, object]:
    """조기/자동마감 루틴 방식의 마지막 매도 처리 메타를 제거한다."""
    state["close_routine_final_sell_ordered"] = False
    state["close_routine_final_sell_ordered_at"] = ""
    state["close_routine_final_sell_source"] = ""
    state["close_routine_final_sell_reason"] = ""
    return state


def auto_trade_setting_close_routine_order_allowed(
    state: dict[str, object] | None,
    signal_type: object,
    display_status: str = "",
) -> tuple[bool, str]:
    """메인 주문판정 계층용 조기/자동마감 루틴 주문 허용 훅.

    사용 위치:
    - 루틴이 BUY/SELL 신호를 만들고, 메인프로그램이 실제 주문을 내기 직전.

    반환:
    - (True, reason): 주문판정 계속 진행 가능
    - (False, reason): 조기/자동마감 루틴의 마지막 매도 이후라 추가 주문 차단

    주의:
    - 이 함수는 주문을 실행하지 않는다.
    - 첫 SELL 신호 자체는 차단하지 않는다.
    - 첫 SELL 주문이 실제로 접수/처리된 뒤
      auto_trade_setting_mark_close_routine_final_sell_ordered()를 호출해야 한다.
    """
    if not auto_trade_setting_close_routine_mode_active(state, display_status):
        return True, "마감 루틴 아님"

    if auto_trade_setting_close_routine_final_sell_ordered(state):
        return False, "조기/자동마감 루틴 마지막 매도 이후 추가 주문 차단"

    normalized = str(signal_type or "").strip().upper()
    if normalized in {"BUY", "SELL", "매수", "매도"}:
        return True, "조기/자동마감 루틴 마지막 매도 전 주문 허용"

    return True, "비주문 신호"


def auto_trade_setting_mark_close_routine_final_sell_ordered(
    state: dict[str, object],
    source: object = "routine",
    reason: object = "루틴 매도신호",
) -> dict[str, object]:
    """첫 매도신호 주문 처리 후 해당 종목의 조기/자동마감 루틴을 잠근다.

    정책 기준:
    - 조기/자동마감 시간에 나오는 첫 매도신호가 당일 마지막 매도신호다.
    - 이 매도주문 이후에는 메인프로그램이 추가 매수/매도 주문을 차단한다.
    - 실제 보유/미수/미도 잔여 정리는 이후 청산 단계와 검토관리 단계가 담당한다.
    """
    if not isinstance(state, dict):
        return state
    state["close_routine_final_sell_ordered"] = True
    state["close_routine_final_sell_ordered_at"] = now_text()
    state["close_routine_final_sell_source"] = str(source or "routine").strip() or "routine"
    state["close_routine_final_sell_reason"] = str(reason or "루틴 매도신호").strip() or "루틴 매도신호"
    state["buy_enabled"] = False
    state["sell_enabled"] = False
    state["updated_at"] = now_text()
    return state


def auto_trade_setting_method_text(
    display_status: str,
    config: dict[str, object],
    state: dict[str, object] | None = None,
) -> str:
    """상태의 보조표시로 사용할 현재 방식 텍스트."""
    status = auto_trade_setting_display_status(display_status)
    if auto_trade_setting_early_close_requested(state):
        method = close_method_from_state_or_policy(
            state,
            "early_close_method",
            "early_close_policy",
            "early_close",
            "루틴",
        )
        return short_close_method_text(method)
    if status == "자동마감":
        method = close_method_from_state_or_policy(
            state,
            "auto_close_method",
            "auto_close_policy",
            "auto_close",
            "루틴매도신호",
        )
        return short_close_method_text(method)
    return "루틴"


def individual_liquidation_policy_from_config(config: dict[str, object] | None) -> dict[str, object]:
    """config.json의 종목별 개별 청산 설정을 안전하게 읽는다.

    반환:
    - 환경설정 사용: {}
    - 개별 청산 사용: {enabled, minutes_before_regular_close, method}
    - 청산 안함(이월): {enabled=True, method='이월', ...}
    """
    if not isinstance(config, dict):
        return {}

    raw = config.get("individual_liquidation", {})
    if not isinstance(raw, dict):
        return {}

    if not bool(raw.get("enabled", False)):
        return {}

    method = short_close_method_text(raw.get("method", "이월"))
    if not method:
        method = "이월"

    minutes_text = str(raw.get("minutes_before_regular_close", "5")).strip() or "5"
    try:
        minutes = int(minutes_text)
    except Exception:
        minutes = 5
    minutes = max(1, min(100, minutes))

    return {
        "enabled": True,
        "minutes_before_regular_close": str(minutes),
        "method": method,
    }


def effective_liquidation_policy_for_config(config: dict[str, object] | None) -> tuple[dict[str, object], bool]:
    """실제 적용할 청산정책을 반환한다.

    우선순위:
    1. 종목별 individual_liquidation
    2. 환경설정 operation_policy.json / liquidation
    """
    individual = individual_liquidation_policy_from_config(config)
    if individual:
        return individual, True

    policy = read_operation_policy()
    liquidation = policy.get("liquidation", {}) if isinstance(policy, dict) else {}
    if not isinstance(liquidation, dict):
        liquidation = {}

    method = short_close_method_text(liquidation.get("method", "이월")) or "이월"
    minutes_text = str(liquidation.get("minutes_before_regular_close", "5")).strip() or "5"
    try:
        minutes = int(minutes_text)
    except Exception:
        minutes = 5
    minutes = max(1, min(100, minutes))

    return {
        "minutes_before_regular_close": str(minutes),
        "method": method,
    }, False


def auto_trade_setting_liquidation_text(
    config: dict[str, object],
    display_status: str = "",
    state: dict[str, object] | None = None,
) -> str:
    """청산정책 표시 텍스트.

    우선순위:
    1. 종목별 개별 청산 설정(config.json / individual_liquidation)
    2. 환경설정 청산정책(operation_policy.json / liquidation)

    수동운영 평상시에는 환경설정의 수동 청산정책 적용 여부를 따른다.
    단, 종목별 개별 청산 설정이 있으면 해당 종목 예외값을 우선 표시한다.
    조기마감 상태에서는 청산정책 표시가 가능하다.
    """
    policy = read_operation_policy()
    status_text = auto_trade_setting_display_status(display_status)
    mode = normalize_operation_mode(config.get("operation_mode", "SCHEDULED"))
    early_close_forced = auto_trade_setting_early_close_requested(state)
    individual_policy = individual_liquidation_policy_from_config(config)
    has_individual = bool(individual_policy)

    manual = policy.get("manual_operation", {}) if isinstance(policy.get("manual_operation"), dict) else {}
    if (
        not has_individual
        and not early_close_forced
        and status_text != "조기마감"
        and mode == "CONTINUOUS"
        and not bool(manual.get("use_liquidation_policy", False))
    ):
        return "-"

    # 조기마감/자동마감 중에는 state에 저장된 마감 옵션이 환경설정 청산정책보다 우선한다.
    # 특히 마감 옵션이 이월이면 청산 컬럼도 이월로 표시하고, 시장가/현재가 청산으로 보이면 안 된다.
    if early_close_forced:
        method = short_close_method_text(
            close_method_from_state_or_policy(
                state,
                "early_close_method",
                "early_close_policy",
                "early_close",
                "루틴",
            )
        )
        if method == "이월":
            return "이월"

    if status_text == "자동마감":
        method = short_close_method_text(
            close_method_from_state_or_policy(
                state,
                "auto_close_method",
                "auto_close_policy",
                "auto_close",
                "루틴매도신호",
            )
        )
        if method == "이월":
            return "이월"

    liquidation, _is_individual = effective_liquidation_policy_for_config(config)
    method = short_close_method_text(liquidation.get("method", "이월"))
    minutes = str(liquidation.get("minutes_before_regular_close", "5")).strip() or "5"

    if method == "이월":
        return "이월"

    return f"{minutes}분/{method}"


def auto_trade_setting_regular_end_seconds() -> int:
    """자동매매설정창 기준 정규장/청산 종료 초 단위."""
    policy = read_operation_policy()
    regular = policy.get("regular_market", {}) if isinstance(policy.get("regular_market"), dict) else {}
    end_time = normalized_hhmmss_or_empty(regular.get("end_time", "15:20:00")) or "15:20:00"
    return seconds_from_hhmmss(end_time, "15:20:00")


def auto_trade_setting_is_after_regular_end(now_dt: datetime | None = None) -> bool:
    """정규장/청산 종료 이후인지 판단한다."""
    current = now_dt or datetime.now()
    current_seconds = current.hour * 3600 + current.minute * 60 + current.second
    return current_seconds >= auto_trade_setting_regular_end_seconds()


def auto_trade_setting_has_unresolved_quantity(
    holding_qty: int,
    buy_pending_qty: object,
    sell_pending_qty: object,
) -> bool:
    """보유/미체결 잔여 수량 여부.

    주의: 이 함수는 수량 존재 여부만 반환한다.
    반환값 True만으로 검토관리 이동을 결정하면 안 된다.
    검토관리 이동은 재시작/안정성검사/무결성검사/긴급정지해제/강제종료/청산완료후잔여 같은
    명시적 검사 컨텍스트에서만 수행한다.
    """
    if holding_qty > 0:
        return True
    if isinstance(buy_pending_qty, int) and buy_pending_qty > 0:
        return True
    if isinstance(sell_pending_qty, int) and sell_pending_qty > 0:
        return True
    if buy_pending_qty == "?" or sell_pending_qty == "?":
        return True
    return False


def auto_trade_setting_has_buy_pending_problem(buy_pending_qty: object) -> bool:
    """매수 미체결/미수성 잔여 존재 여부.

    주의:
    - 이 함수명은 기존 호출부 호환을 위해 유지한다.
    - 조기/자동마감 시작 또는 진행 중이라는 이유만으로 이 값이 True라고
      즉시 검토관리로 이동하면 안 된다.
    - 루틴 방식 조기/자동마감에서는 첫 매도신호 전까지 매수 흐름을 정상으로 본다.
    - 검토관리 이동은 청산 후에도 잔여 문제가 남거나, 명시적 안정성검사/재시작/
      긴급정지 해제 같은 검사 컨텍스트에서만 판단한다.
    """
    if isinstance(buy_pending_qty, int) and buy_pending_qty > 0:
        return True
    if buy_pending_qty == "?":
        return True
    return False


def auto_trade_setting_has_close_progress_quantity(
    holding_qty: int,
    sell_pending_qty: object,
) -> bool:
    """조기/자동마감 진행 대상 여부.

    확정 정책:
    - 조기/자동마감은 보유수량을 0으로 만드는 1차 리셋 절차다.
    - 따라서 진행 대상 기준은 보유수량이다.
    - 미수/미도/미체결은 대상 여부의 주기준이 아니라 자동매매 신뢰성 점검 지표다.
    - sell_pending_qty 인자는 기존 호출부 호환을 위해 유지한다.
    """
    return holding_qty > 0


def auto_trade_setting_today_date_text() -> str:
    """자동매매설정창 기준 금일 날짜 문자열."""
    return datetime.now().strftime("%Y-%m-%d")


def auto_trade_setting_liquidation_completed_today(state: dict[str, object] | None) -> bool:
    """금일 청산 완료 플래그 확인.

    확정 정책:
    - 청산을 거쳐 정상적으로 보유/미수/미도 0이 되면 상태 표시는 감시/대기로 둔다.
    - 단, 금일 청산완료 플래그가 있으면 수동+ATS라도 시간외 재진입은 금지한다.
    """
    if not isinstance(state, dict):
        return False

    today = auto_trade_setting_today_date_text()
    for key in [
        "liquidation_completed_at",
        "liquidation_finished_at",
        "daily_liquidation_completed_at",
        "ats_sell_completed_at",
    ]:
        value = str(state.get(key, "") or "").strip()
        if value.startswith(today):
            return True

    completed_date = str(state.get("daily_liquidation_completed_date", "") or "").strip()
    if completed_date == today:
        return True

    if bool(state.get("daily_liquidation_completed", False)):
        value = str(state.get("daily_liquidation_completed_at", "") or "").strip()
        if not value or value.startswith(today):
            return True

    return False


def auto_trade_setting_effective_liquidation_method(config: dict[str, object] | None) -> str:
    """청산 결과 판정용 실제 청산 방식."""
    liquidation, _is_individual = effective_liquidation_policy_for_config(config)
    return short_close_method_text(liquidation.get("method", "이월")) or "이월"


def auto_trade_setting_liquidation_result_policy(
    config: dict[str, object] | None,
    state: dict[str, object] | None,
    holding_qty: int,
    buy_pending_qty: object,
    sell_pending_qty: object,
) -> str:
    """청산 완료 플래그 이후 화면/안전 판정.

    반환값:
    - "NONE": 청산 완료 판정 대상 아님
    - "SUCCESS": 보유/미수/미도 없음 → 감시/대기 + ATS 재진입 금지
    - "CURRENT_CARRYOVER": 현재가 청산 후 보유/미도 잔존 → 이월과 동일 취급
    - "RED_STOP": 미수 존재 또는 시장가 청산 후 잔여 존재 → 서버/통신 불안정 적색
    """
    if not auto_trade_setting_liquidation_completed_today(state):
        return "NONE"

    # 미수/매수 미체결은 청산 완료 시점에 존재하면 안 되는 상태다.
    if isinstance(buy_pending_qty, int) and buy_pending_qty > 0:
        return "RED_STOP"
    if buy_pending_qty == "?":
        return "RED_STOP"

    method = auto_trade_setting_effective_liquidation_method(config)

    has_sell_residue = False
    if holding_qty > 0:
        has_sell_residue = True
    if isinstance(sell_pending_qty, int) and sell_pending_qty > 0:
        has_sell_residue = True
    if sell_pending_qty == "?":
        has_sell_residue = True

    if not has_sell_residue:
        return "SUCCESS"

    if method == "현재가":
        return "CURRENT_CARRYOVER"

    # 시장가 청산인데 보유/미도 잔존이면 정상 시장상황보다
    # 서버/통신/체결정보 불일치 가능성을 우선 표시한다.
    if method == "시장가":
        return "RED_STOP"

    # 기타 방식은 보수적으로 이월성 잔여로 둔다.
    return "CURRENT_CARRYOVER"


def auto_trade_setting_mark_liquidation_result_for_display(
    config: dict[str, object] | None,
    state: dict[str, object],
    holding_qty: int,
    buy_pending_qty: object,
    sell_pending_qty: object,
) -> tuple[dict[str, object], str]:
    """청산 완료 플래그 후 화면 표시용 상태값을 보정한다.

    주의:
    - 여기서는 파일을 저장하지 않는다.
    - 실제 주문/체결 확정은 키움 주문 엔진 단계에서 처리한다.
    """
    if not isinstance(state, dict):
        return state, "NONE"

    result = auto_trade_setting_liquidation_result_policy(
        config,
        state,
        holding_qty,
        buy_pending_qty,
        sell_pending_qty,
    )

    if result in {"SUCCESS", "CURRENT_CARRYOVER"}:
        state["status"] = "WAIT_BUY"
        state["buy_enabled"] = False
        state["sell_enabled"] = False
        state["liquidation_policy_forced"] = False
        state["liquidation_policy_reason"] = ""
        state["early_close_requested_at"] = ""
        state["early_close_source"] = ""
        state["early_close_method"] = ""
        state["early_close_policy"] = {}
        state["auto_close_requested_at"] = ""
        state["auto_close_source"] = ""
        state["auto_close_method"] = ""
        state["auto_close_policy"] = {}
        state = clear_close_routine_final_sell_metadata(state)
        if result == "CURRENT_CARRYOVER":
            state["operation_notice"] = "LIQUIDATION_CURRENT_PRICE_CARRYOVER"
            state["operation_notice_reason"] = "현재가 청산 잔여 - 이월 취급"
        else:
            state["operation_notice"] = ""
            state["operation_notice_reason"] = ""
        state["operation_notice_at"] = now_text() if result == "CURRENT_CARRYOVER" else ""
    elif result == "RED_STOP":
        state["server_mismatch"] = True
        state["kiwoom_sync_status"] = "MISMATCH"
        state["operation_notice"] = "LIQUIDATION_RESULT_UNRELIABLE"
        state["operation_notice_reason"] = "청산 완료 후 미수 또는 시장가 잔여 발생"
        state["operation_notice_at"] = now_text()

    return state, result


def auto_trade_setting_liquidation_active(
    config: dict[str, object],
    holding_qty: int,
    now_dt: datetime | None = None,
    display_status: str = "",
    state: dict[str, object] | None = None,
) -> bool:
    """현재 청산정책이 주도권을 가지는 시간대인지 판단한다.

    종목별 개별 청산 설정이 있으면 개별 분전값을 우선 사용한다.
    """
    if auto_trade_setting_liquidation_completed_today(state):
        return False

    if holding_qty <= 0:
        return False

    # 조기마감/자동마감 옵션의 이월은 실제 청산 실행보다 우선한다.
    # 보유가 있어도 이월이면 시장가/현재가 청산 절차를 시작하지 않는다.
    status_text = auto_trade_setting_display_status(display_status)
    if auto_trade_setting_early_close_requested(state):
        close_method = short_close_method_text(
            close_method_from_state_or_policy(
                state,
                "early_close_method",
                "early_close_policy",
                "early_close",
                "루틴",
            )
        )
        if close_method == "이월":
            return False

    if status_text == "자동마감":
        close_method = short_close_method_text(
            close_method_from_state_or_policy(
                state,
                "auto_close_method",
                "auto_close_policy",
                "auto_close",
                "루틴매도신호",
            )
        )
        if close_method == "이월":
            return False

    liquidation, _is_individual = effective_liquidation_policy_for_config(config)
    method = short_close_method_text(liquidation.get("method", "이월"))
    if method == "이월":
        return False
    try:
        minutes = int(str(liquidation.get("minutes_before_regular_close", "5")).strip() or "5")
    except Exception:
        minutes = 5
    current = now_dt or datetime.now()
    current_seconds = current.hour * 3600 + current.minute * 60 + current.second
    end_seconds = auto_trade_setting_regular_end_seconds()
    start_seconds = max(0, end_seconds - minutes * 60)
    return start_seconds <= current_seconds < end_seconds


def auto_trade_setting_liquidation_phase_active(
    config: dict[str, object],
    holding_qty: int,
    now_dt: datetime | None = None,
    state: dict[str, object] | None = None,
) -> bool:
    """청산 절차가 실제 주도권을 가진 상태인지 판단한다.

    확정 원칙:
    - 자동마감/조기마감은 마감 절차의 시작 조건이다.
    - 청산 시작시간에 도달하면 화면 상태는 자동마감/조기마감이 아니라 감시/대기다.
    - 이 상태에서는 조기마감으로 다시 변경할 수 없다.
    """
    return auto_trade_setting_liquidation_active(
        config,
        holding_qty,
        now_dt=now_dt,
        display_status="",
        state=state,
    )
