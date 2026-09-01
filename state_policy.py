# -*- coding: utf-8 -*-
"""
state_policy.py

자동매매 상태 표시명, 운영방식, 시간 운영 판정, 감시시작 상태 판정 정책.
GUI 코드에서 상태 정책을 분리해 UI 수정과 기능 수정의 충돌을 줄인다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
GLOBAL_SCHEDULE_PATH = PROJECT_ROOT / "global_schedule.json"
OPERATION_POLICY_PATH = PROJECT_ROOT / "operation_policy.json"


FINAL_AUTO_TRADE_DISPLAY_STATUSES = (
    "감시/대기",
    "매수/매도",
    "자동마감",
    "감시/매도",  # 구버전 표시명 호환
    "조기마감",
    "긴급정지",
    "검토종목",
)


@dataclass(frozen=True)
class CanonicalAutoTradeStatus:
    raw_status: str
    normalized_status: str
    known: bool
    display_status: str
    status_class: str
    is_emergency: bool
    is_review_required: bool


_AUTO_TRADE_STATUS_DISPLAY_MAP = {
    "MONITORING": "감시/대기",
    "WATCHING": "감시/대기",
    "WATCH": "감시/대기",
    "WATCH_BUY": "감시/대기",
    "RUNNING": "매수/매도",
    "BUY_SELL": "매수/매도",
    "BUY": "매수/매도",
    "SELL": "매수/매도",
    "SELL_ONLY": "자동마감",
    "WATCH_SELL": "자동마감",
    "BUY_SUSPENDED": "자동마감",
    "BUY_STOPPED": "자동마감",
    "AUTO_CLOSE": "자동마감",
    "AUTO_CLOSING": "자동마감",
    "AUTO_CLOSED": "자동마감",
    "EARLY_CLOSE": "조기마감",
    "EARLY_CLOSING": "조기마감",
    "EARLY_CLOSED": "조기마감",
    "CLOSE_EARLY": "조기마감",
    "FORCE_CLOSE": "조기마감",
    "FORCE_LIQUIDATION": "조기마감",
    "STOPPED": "감시/대기",
    "STOP": "감시/대기",
    "WAIT": "감시/대기",
    "WAIT_BUY": "감시/대기",
    "WAIT_SELL": "감시/대기",
    "SCHEDULED": "감시/대기",
    "STARTED": "매수/매도",
    "AUTO": "매수/매도",
    "TRADING": "매수/매도",
    "BUYING": "매수/매도",
    "SELLING": "매수/매도",
    "EMERGENCY_STOPPED": "긴급정지",
    "EMERGENCY_STOP": "긴급정지",
    "EMERGENCY": "긴급정지",
    "REVIEW_REQUIRED": "검토종목",
    "REVIEW": "검토종목",
    "PAUSED": "검토종목",
    "ERROR": "검토종목",
}

_AUTO_TRADE_STATUS_CLASS = {
    **{
        status: "NORMAL_OPERATION"
        for status in (
            "MONITORING", "WATCHING", "WATCH", "WATCH_BUY", "RUNNING",
            "BUY_SELL", "BUY", "SELL", "STOPPED", "STOP", "WAIT",
            "WAIT_BUY", "WAIT_SELL", "SCHEDULED", "STARTED", "AUTO",
            "TRADING", "BUYING", "SELLING",
        )
    },
    **{
        status: "CLOSE_OPERATION"
        for status in (
            "SELL_ONLY", "WATCH_SELL", "BUY_SUSPENDED", "BUY_STOPPED",
            "AUTO_CLOSE", "AUTO_CLOSING", "AUTO_CLOSED", "EARLY_CLOSE",
            "EARLY_CLOSING", "EARLY_CLOSED", "CLOSE_EARLY", "FORCE_CLOSE",
            "FORCE_LIQUIDATION",
        )
    },
    **{
        status: "EMERGENCY"
        for status in ("EMERGENCY_STOPPED", "EMERGENCY_STOP", "EMERGENCY")
    },
    **{
        status: "REVIEW"
        for status in ("REVIEW_REQUIRED", "REVIEW", "PAUSED", "ERROR")
    },
}


def canonical_auto_trade_status(raw_status: object) -> CanonicalAutoTradeStatus:
    raw_text = str(raw_status or "STOPPED").strip() or "STOPPED"
    if raw_text == "감시/매도":
        return CanonicalAutoTradeStatus(
            raw_status=raw_text,
            normalized_status="AUTO_CLOSE",
            known=True,
            display_status="자동마감",
            status_class="CLOSE_OPERATION",
            is_emergency=False,
            is_review_required=False,
        )
    display_to_class = {
        "감시/대기": "NORMAL_OPERATION",
        "매수/매도": "NORMAL_OPERATION",
        "자동마감": "CLOSE_OPERATION",
        "조기마감": "CLOSE_OPERATION",
        "긴급정지": "EMERGENCY",
        "검토종목": "REVIEW",
    }
    if raw_text in display_to_class:
        status_class = display_to_class[raw_text]
        return CanonicalAutoTradeStatus(
            raw_status=raw_text,
            normalized_status=raw_text,
            known=True,
            display_status=raw_text,
            status_class=status_class,
            is_emergency=status_class == "EMERGENCY",
            is_review_required=status_class == "REVIEW",
        )

    normalized = raw_text.upper()
    known = normalized in _AUTO_TRADE_STATUS_DISPLAY_MAP
    status_class = _AUTO_TRADE_STATUS_CLASS.get(normalized, "UNKNOWN")
    return CanonicalAutoTradeStatus(
        raw_status=raw_text,
        normalized_status=normalized,
        known=known,
        display_status=_AUTO_TRADE_STATUS_DISPLAY_MAP.get(normalized, "검토종목"),
        status_class=status_class,
        is_emergency=status_class == "EMERGENCY",
        is_review_required=status_class == "REVIEW",
    )


def auto_trade_status_display(raw_status: object) -> str:
    """
    내부 상태값을 화면 표시명으로 변환한다.

    v1.2 기준 화면 표시 상태는 아래 6개로 고정한다.
    - 감시/대기
    - 매수/매도
    - 자동마감
    - 조기마감
    - 긴급정지
    - 검토종목

    주의:
    - 과거 표시명 "감시/매도"는 내부 호환용으로만 인정하고 "자동마감"으로 변환한다.
    - "자동마감"이 raw status로 들어와도 검토종목으로 떨어지면 안 된다.

    STOPPED/REVIEW_REQUIRED 같은 내부 코드는 화면에 직접 노출하지 않는다.
    """
    raw_text = str(raw_status or "STOPPED").strip()
    if not raw_text:
        raw_text = "STOPPED"

    # 이미 최종 표시명으로 들어온 값은 그대로 인정한다.
    # 단, 구버전 표시명 "감시/매도"는 신표준 "자동마감"으로 통일한다.
    if raw_text == "감시/매도":
        return "자동마감"
    if raw_text in FINAL_AUTO_TRADE_DISPLAY_STATUSES:
        return raw_text

    return canonical_auto_trade_status(raw_text).display_status


def display_status_text_for_gui(raw_status: object) -> str:
    """Normalize raw state to the canonical operator-facing status text."""
    status = str(raw_status or "").strip()
    if not status or status == "-":
        return "-"
    try:
        return auto_trade_status_display(status)
    except Exception:
        return "검토종목"


def auto_trade_setting_display_status(
    display_status: str,
    *,
    emergency_scope: str | None = None,
) -> str:
    """Project canonical status text for the AutoTrade settings view."""
    normalized = display_status_text_for_gui(display_status)
    if normalized == "감시/매도":
        return "자동마감"
    if (
        normalized == "긴급정지"
        and str(emergency_scope or "").strip().upper() == "SELECTED"
    ):
        return "검토종목"
    return normalized


def auto_trade_status_color(display_status: str) -> str:
    normalized = str(display_status or "").strip()
    color_map = {
        # 최신 표시명
        "감시/대기": "#2563eb",
        "매수/매도": "#16a34a",
        "자동마감": "#7c3aed",
        "감시/매도": "#7c3aed",  # 구버전 표시명 호환
        "조기마감": "#ea580c",
        "긴급정지": "#7f1d1d",
        "검토종목": "#ca8a04",

        # 호환 표시명
        "정지": "#dc2626",
        "감시중": "#2563eb",
        "매수중지": "#7c3aed",
        "검토필요": "#ca8a04",
        "비상정지": "#7f1d1d",
        "등록대기": "#9ca3af",
        "대기": "#9ca3af",
        "예약": "#9ca3af",
        "매수대기": "#0ea5e9",
        "매도대기": "#8b5cf6",
        "매수중": "#0891b2",
        "매도중": "#9333ea",
        "오류": "#b91c1c",
    }
    return color_map.get(normalized, "#6b7280")


def auto_trade_status_dot(display_status: str) -> str:
    return "●"


def normalize_operation_mode(value: object) -> str:
    """
    종목별 자동매매 운영방식을 표준값으로 변환한다.
    저장값:
    - SCHEDULED: 시간 운영, 기본값
    - CONTINUOUS: 수동 운영
    """
    mode = str(value or "SCHEDULED").strip().upper()
    if mode in ("CONTINUOUS", "MANUAL", "지속", "지속운영", "수동", "수동운영", "상시", "상시운영", "즉시", "즉시운영"):
        return "CONTINUOUS"
    return "SCHEDULED"


def operation_mode_display(value: object) -> str:
    mode = normalize_operation_mode(value)
    if mode == "CONTINUOUS":
        return "수동"
    return "시간"


def operation_mode_check_text(current_mode: object, target_mode: str) -> str:
    return "✓" if normalize_operation_mode(current_mode) == target_mode else ""


def real_trade_enabled(config: dict[str, object] | None) -> bool:
    """
    실주문 권한 여부를 반환한다.

    대안 B 정책:
    - 동일 종목은 여러 루틴에 배정 가능하다.
    - 실제 주문 가능 루틴은 종목당 1개만 허용한다.
    - False 인 루틴은 감시/신호 확인 전용이다.
    """
    if not isinstance(config, dict):
        return True
    value = config.get("real_trade_enabled", True)
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    return text not in ("false", "0", "no", "n", "off", "감시전용")


def trade_permission_display(config: dict[str, object] | None) -> tuple[str, str, str]:
    if real_trade_enabled(config):
        return "실주문", "#0F766E", "실주문 루틴: 이 종목의 실제 매수/매도 주문 가능"
    return "감시전용", "#6B7280", "감시전용 루틴: 신호/상태 확인만 수행하며 실제 주문은 차단"


def normalized_hhmmss_or_empty(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""

    parts = text.split(":")
    if len(parts) == 2:
        parts.append("00")
    if len(parts) != 3:
        return ""

    try:
        hour = int(parts[0])
        minute = int(parts[1])
        second = int(parts[2])
    except Exception:
        return ""

    if 0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59:
        return f"{hour:02d}:{minute:02d}:{second:02d}"
    return ""


def normalized_hhmm_or_empty(value: object) -> str:
    normalized = normalized_hhmmss_or_empty(value)
    if not normalized:
        return ""
    return normalized[:5]


def seconds_from_hhmmss(value: object, default_value: str) -> int:
    normalized = normalized_hhmmss_or_empty(value) or normalized_hhmmss_or_empty(default_value)
    if not normalized:
        return 0
    hour, minute, second = [int(part) for part in normalized.split(":")]
    return hour * 3600 + minute * 60 + second


def minutes_from_hhmm(value: object, default_value: str) -> int:
    return seconds_from_hhmmss(value, default_value) // 60




def default_operation_policy() -> dict[str, object]:
    """운영환경설정 기본값. gui_windows.py의 기본 구조와 동일하게 유지한다."""
    return {
        "regular_market": {
            "start_time": "09:00:00",
            "end_time": "15:20:00",
        },
        "extra_sessions": [
            {"enabled": False, "name": "추가시간1", "start_time": "08:00:00", "end_time": "08:50:00"},
            {"enabled": False, "name": "추가시간2", "start_time": "15:40:00", "end_time": "19:50:00"},
            {"enabled": False, "name": "추가시간3", "start_time": "", "end_time": ""},
        ],
        "scheduled_operation": {
            "default_start_time": "09:00:00",
            "default_end_buy_time": "13:30:00",
        },
        "manual_operation": {
            "use_regular_market": True,
            "use_extra_session_1": False,
            "use_extra_session_2": False,
            "use_extra_session_3": False,
            "enabled_status": "매수/매도",
            "disabled_status": "감시/대기",
            "use_liquidation_policy": False,
        },
        "auto_close": {
            "method": "루틴매도신호",
            "profit_percent": "",
            "loss_percent": "",
        },
        "early_close": {
            "method": "시장가",
            "profit_percent": "",
            "loss_percent": "",
        },
        "liquidation": {
            "minutes_before_regular_close": "5",
            "method": "이월",
        },
        "updated_at": "",
    }


def read_operation_policy() -> dict[str, object]:
    """operation_policy.json을 읽고 누락된 기본 섹션을 보완한다."""
    default = default_operation_policy()
    if not OPERATION_POLICY_PATH.exists():
        return default
    try:
        data = json.loads(OPERATION_POLICY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return default
    if not isinstance(data, dict):
        return default

    merged = default_operation_policy()
    for key, value in data.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key].update(value)  # type: ignore[index]
        else:
            merged[key] = value
    scheduled = merged.get("scheduled_operation")
    if isinstance(scheduled, dict):
        scheduled.pop("after_buy_end_status", None)
    return merged


def read_global_schedule() -> dict[str, str]:
    """전역 시간운영 기본값을 읽는다.

    최신 기준은 operation_policy.json 의 scheduled_operation 이다.
    global_schedule.json 은 과거 호환용 백업 기준으로만 사용한다.
    """
    default = {"start_time": "09:00:00", "end_buy_time": "13:30:00"}

    if OPERATION_POLICY_PATH.exists():
        try:
            policy = json.loads(OPERATION_POLICY_PATH.read_text(encoding="utf-8"))
        except Exception:
            policy = {}
        if isinstance(policy, dict):
            scheduled = policy.get("scheduled_operation", {})
            if isinstance(scheduled, dict):
                start_time = normalized_hhmmss_or_empty(scheduled.get("default_start_time", ""))
                end_buy_time = normalized_hhmmss_or_empty(scheduled.get("default_end_buy_time", ""))
                if (
                    start_time
                    and end_buy_time
                    and seconds_from_hhmmss(start_time, start_time) < seconds_from_hhmmss(end_buy_time, end_buy_time)
                ):
                    return {"start_time": start_time, "end_buy_time": end_buy_time}

    # 구버전 호환: operation_policy.json 이 없거나 손상된 경우에만 사용한다.
    if not GLOBAL_SCHEDULE_PATH.exists():
        return default

    try:
        data = json.loads(GLOBAL_SCHEDULE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return default

    if not isinstance(data, dict):
        return default

    start_time = normalized_hhmmss_or_empty(data.get("start_time", data.get("buy_start_time", ""))) or default["start_time"]
    end_buy_time = normalized_hhmmss_or_empty(data.get("end_buy_time", data.get("buy_end_time", ""))) or default["end_buy_time"]
    if seconds_from_hhmmss(start_time, start_time) >= seconds_from_hhmmss(end_buy_time, end_buy_time):
        return default
    return {"start_time": start_time, "end_buy_time": end_buy_time}


def write_global_schedule(
    start_time: str,
    end_buy_time: str,
    *,
    path: Path | None = None,
) -> None:
    data = {
        "start_time": normalized_hhmmss_or_empty(start_time) or "09:00:00",
        "end_buy_time": normalized_hhmmss_or_empty(end_buy_time) or "13:30:00",
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    target_path = Path(path) if path is not None else GLOBAL_SCHEDULE_PATH
    target_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def schedule_override_enabled(config: dict[str, object], global_schedule: dict[str, str] | None = None) -> bool:
    global_schedule = global_schedule or read_global_schedule()
    local_start = normalized_hhmmss_or_empty(config.get("start_time", config.get("trade_start_time", "")))
    local_end = normalized_hhmmss_or_empty(config.get("end_buy_time", config.get("buy_end_time", "")))

    if not local_start or not local_end:
        return False

    if seconds_from_hhmmss(local_start, local_start) >= seconds_from_hhmmss(local_end, local_end):
        return True

    return local_start != global_schedule["start_time"] or local_end != global_schedule["end_buy_time"]


def effective_schedule_times(config: dict[str, object], global_schedule: dict[str, str] | None = None) -> tuple[str, str, bool]:
    global_schedule = global_schedule or read_global_schedule()
    local_start = normalized_hhmmss_or_empty(config.get("start_time", config.get("trade_start_time", "")))
    local_end = normalized_hhmmss_or_empty(config.get("end_buy_time", config.get("buy_end_time", "")))

    if local_start and local_end and seconds_from_hhmmss(local_start, local_start) < seconds_from_hhmmss(local_end, local_end):
        is_custom = local_start != global_schedule["start_time"] or local_end != global_schedule["end_buy_time"]
        return local_start, local_end, is_custom

    return global_schedule["start_time"], global_schedule["end_buy_time"], False


def _strict_global_schedule_source() -> tuple[dict[str, object] | None, str]:
    if OPERATION_POLICY_PATH.exists():
        try:
            policy = json.loads(OPERATION_POLICY_PATH.read_text(encoding="utf-8"))
        except Exception:
            return None, "INVALID"
        if not isinstance(policy, dict):
            return None, "INVALID"
        scheduled = policy.get("scheduled_operation")
        if not isinstance(scheduled, dict):
            return None, "MISSING"
        return {
            "start_time": scheduled.get("default_start_time"),
            "end_buy_time": scheduled.get("default_end_buy_time"),
        }, "GLOBAL"

    if not GLOBAL_SCHEDULE_PATH.exists():
        return None, "MISSING"
    try:
        schedule = json.loads(GLOBAL_SCHEDULE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None, "INVALID"
    if not isinstance(schedule, dict):
        return None, "INVALID"
    return {
        "start_time": schedule.get("start_time", schedule.get("buy_start_time")),
        "end_buy_time": schedule.get("end_buy_time", schedule.get("buy_end_time")),
    }, "GLOBAL_LEGACY"


def operation_mode_change_decision(
    config: dict[str, object],
    requested_mode: object,
    current_datetime: datetime,
    global_schedule: dict[str, object] | None = None,
    ats_runtime_active: bool = False,
    runtime_status: object = "STOPPED",
    pending_order_active: bool = False,
    close_or_liquidation_active: bool = False,
    runtime_state_available: bool = True,
) -> dict[str, object]:
    current_mode = normalize_operation_mode(config.get("operation_mode", "SCHEDULED"))
    target_mode = normalize_operation_mode(requested_mode)
    current_time = current_datetime.strftime("%H:%M:%S")
    if ats_runtime_active:
        return {
            "allowed": False,
            "reason": "BLOCKED_ATS_RUNTIME_ACTIVE",
            "scheduled_end_time": "",
            "current_time": current_time,
            "schedule_source": "",
        }
    if not runtime_state_available:
        return {
            "allowed": False,
            "reason": "BLOCKED_RUNTIME_STATE_UNAVAILABLE",
            "scheduled_end_time": "",
            "current_time": current_time,
            "schedule_source": "",
        }
    status = str(runtime_status or "STOPPED").strip().upper() or "STOPPED"
    if status in {"RUNNING", "STARTED", "AUTO", "TRADING", "SELL_ONLY"}:
        return {
            "allowed": False,
            "reason": "BLOCKED_TRADING_ACTIVE",
            "scheduled_end_time": "",
            "current_time": current_time,
            "schedule_source": "",
        }
    if pending_order_active:
        return {
            "allowed": False,
            "reason": "BLOCKED_ORDER_ACTIVE",
            "scheduled_end_time": "",
            "current_time": current_time,
            "schedule_source": "",
        }
    if close_or_liquidation_active or status in {
        "AUTO_CLOSE",
        "AUTO_CLOSING",
        "EARLY_CLOSE",
        "EARLY_CLOSING",
        "FORCE_CLOSE",
        "FORCE_LIQUIDATION",
        "LIQUIDATION",
        "LIQUIDATING",
    }:
        return {
            "allowed": False,
            "reason": "BLOCKED_CLOSE_OR_LIQUIDATION_ACTIVE",
            "scheduled_end_time": "",
            "current_time": current_time,
            "schedule_source": "",
        }
    if current_mode == target_mode:
        return {
            "allowed": True,
            "reason": "ALLOWED_NO_MODE_CHANGE",
            "scheduled_end_time": "",
            "current_time": current_time,
            "schedule_source": "",
        }
    if target_mode != "SCHEDULED":
        return {
            "allowed": True,
            "reason": "ALLOWED_RUNTIME_STATE_IDLE",
            "scheduled_end_time": "",
            "current_time": current_time,
            "schedule_source": "",
        }

    local_start_raw = config.get("start_time", config.get("trade_start_time", ""))
    local_end_raw = config.get("end_buy_time", config.get("buy_end_time", ""))
    has_local_schedule = bool(str(local_start_raw or "").strip() or str(local_end_raw or "").strip())

    if has_local_schedule:
        schedule = {"start_time": local_start_raw, "end_buy_time": local_end_raw}
        schedule_source = "INDIVIDUAL"
    elif global_schedule is not None:
        schedule = global_schedule
        schedule_source = "GLOBAL"
    else:
        schedule, schedule_source = _strict_global_schedule_source()

    if schedule is None:
        return {
            "allowed": False,
            "reason": (
                "BLOCKED_TIME_POLICY_INVALID"
                if schedule_source == "INVALID"
                else "BLOCKED_TIME_POLICY_MISSING"
            ),
            "scheduled_end_time": "",
            "current_time": current_time,
            "schedule_source": schedule_source,
        }

    start_time = normalized_hhmmss_or_empty(schedule.get("start_time", ""))
    end_time = normalized_hhmmss_or_empty(schedule.get("end_buy_time", ""))
    if not start_time or not end_time:
        raw_start = str(schedule.get("start_time") or "").strip()
        raw_end = str(schedule.get("end_buy_time") or "").strip()
        return {
            "allowed": False,
            "reason": (
                "BLOCKED_TIME_POLICY_INVALID"
                if raw_start or raw_end
                else "BLOCKED_TIME_POLICY_MISSING"
            ),
            "scheduled_end_time": end_time,
            "current_time": current_time,
            "schedule_source": schedule_source,
        }

    if seconds_from_hhmmss(start_time, start_time) >= seconds_from_hhmmss(end_time, end_time):
        return {
            "allowed": False,
            "reason": "BLOCKED_TIME_POLICY_INVALID",
            "scheduled_end_time": end_time,
            "current_time": current_time,
            "schedule_source": schedule_source,
        }

    return {
        "allowed": True,
        "reason": "ALLOWED_RUNTIME_STATE_IDLE",
        "scheduled_end_time": end_time,
        "current_time": current_time,
        "schedule_source": schedule_source,
    }


REGULAR_SESSION_START_TIME = "09:00:00"
REGULAR_SESSION_END_TIME = "15:20:00"


def in_regular_manual_session(now_dt: datetime | None = None) -> bool:
    """정규 매매 허용 시간 안인지 판단한다. operation_policy.json 값이 있으면 우선 사용한다."""
    current = now_dt or datetime.now()
    current_seconds = current.hour * 3600 + current.minute * 60 + current.second

    try:
        policy = read_operation_policy()
        regular = policy.get("regular_market", {}) if isinstance(policy, dict) else {}
        if not isinstance(regular, dict):
            regular = {}
        start_value = regular.get("start_time", REGULAR_SESSION_START_TIME)
        end_value = regular.get("end_time", REGULAR_SESSION_END_TIME)
    except Exception:
        start_value = REGULAR_SESSION_START_TIME
        end_value = REGULAR_SESSION_END_TIME

    start_seconds = seconds_from_hhmmss(start_value, "09:00:00")
    end_seconds = seconds_from_hhmmss(end_value, "15:20:00")
    return start_seconds <= current_seconds < end_seconds


def _seconds_for_now(now_dt: datetime | None = None) -> int:
    current = now_dt or datetime.now()
    return current.hour * 3600 + current.minute * 60 + current.second


def _time_window_contains(now_seconds: int, start_time: object, end_time: object) -> bool:
    start_text = normalized_hhmmss_or_empty(start_time)
    end_text = normalized_hhmmss_or_empty(end_time)
    if not start_text or not end_text:
        return False
    start_seconds = seconds_from_hhmmss(start_text, start_text)
    end_seconds = seconds_from_hhmmss(end_text, end_text)
    if start_seconds == end_seconds:
        return False
    if start_seconds < end_seconds:
        return start_seconds <= now_seconds < end_seconds
    # 자정을 넘기는 추가시간도 허용한다. 예: 23:00~02:00
    return now_seconds >= start_seconds or now_seconds < end_seconds


def manual_extra_session_enabled_now(
    now_dt: datetime | None = None,
    policy: dict[str, object] | None = None,
) -> bool:
    """Environment ATS rows define time ranges, not stock application state."""
    return False


def in_manual_trading_session(
    now_dt: datetime | None = None,
    config: dict[str, object] | None = None,
) -> bool:
    """수동운영 매수/매도 가능 여부.

    환경설정 기준의 정규장만 판정한다.
    종목별 ATS 확장은 현재 Runtime 선택을 읽는 실행 경계에서 별도 판정한다.
    """
    policy = read_operation_policy()
    manual = policy.get("manual_operation", {}) if isinstance(policy, dict) else {}
    if not isinstance(manual, dict):
        manual = {}

    use_regular = bool(manual.get("use_regular_market", True))
    if use_regular and in_regular_manual_session(now_dt):
        return True

    return manual_extra_session_enabled_now(now_dt=now_dt, policy=policy)


def status_for_schedule_window(
    config: dict[str, object] | None = None,
    now_dt: datetime | None = None,
    after_end_status: str = "AUTO_CLOSE",
) -> str:
    """전역/개별 시간 + 정규장 상한(15:20)을 반영한 현재 상태."""
    current = now_dt or datetime.now()
    current_seconds = current.hour * 3600 + current.minute * 60 + current.second

    # 정규장 밖은 무조건 감시/대기
    if not in_regular_manual_session(current):
        return "MONITORING"

    start_time, end_buy_time, _ = effective_schedule_times(config or {})
    start_seconds = seconds_from_hhmmss(start_time, "09:00:00")
    end_seconds = seconds_from_hhmmss(end_buy_time, "13:30:00")

    if not (start_seconds < end_seconds):
        start_seconds = seconds_from_hhmmss("09:00:00", "09:00:00")
        end_seconds = seconds_from_hhmmss("13:30:00", "13:30:00")

    if current_seconds < start_seconds:
        return "MONITORING"
    if current_seconds < end_seconds:
        return "RUNNING"
    return after_end_status


def manual_status_for_now(
    now_dt: datetime | None = None,
    config: dict[str, object] | None = None,
) -> str:
    """
    수동 운영 상태 판정.

    정책:
    - 수동운영은 시간운영의 매수시작/매수종료 시간을 따르지 않는다.
    - 전역 운영환경설정의 정규장 사용 시간이면 RUNNING.
    - 전역 운영환경설정의 추가시간 1~3 중 체크된 구간이면 RUNNING.
    - 그 외 시간은 MONITORING.
    - 시간운영에는 추가시간을 적용하지 않는다.
    """
    if in_manual_trading_session(now_dt=now_dt, config=config):
        return "RUNNING"
    return "MONITORING"


def operation_text_and_color(config: dict[str, object]) -> tuple[str, str, str]:
    mode = normalize_operation_mode(config.get("operation_mode", "SCHEDULED"))
    if mode == "CONTINUOUS":
        return "수동", "#6A1B9A", "수동 운영: 정규장 또는 선택된 추가시간 안에서 매수/매도, 시간 밖은 감시/대기"

    start_time, end_buy_time, is_individual = effective_schedule_times(config)
    text = f"{start_time}~{end_buy_time}"
    color = "#1565C0" if is_individual else "#000000"
    tooltip_prefix = "개별 수정 시간" if is_individual else "기본 시간"
    return text, color, f"{tooltip_prefix}: {text}"


def validate_buy_time_range(start_time: object, end_time: object) -> tuple[bool, str]:
    start_text = normalized_hhmmss_or_empty(start_time)
    end_text = normalized_hhmmss_or_empty(end_time)
    if not start_text or not end_text:
        return False, "시간은 HH:MM 또는 HH:MM:SS 형식으로 입력해야 합니다. 예: 09:00:00"
    if seconds_from_hhmmss(start_text, start_text) >= seconds_from_hhmmss(end_text, end_text):
        return False, "매수 시작 시간은 매수 종료 시간보다 빨라야 합니다."
    return True, ""


def normalize_after_trade_end_status(value: object) -> str:
    status_text = str(value or "MONITORING").strip()
    status = status_text.upper()
    if status_text in ("자동마감", "감시/매도") or status in ("AUTO_CLOSE", "AUTO_CLOSING", "AUTO_CLOSED"):
        return "AUTO_CLOSE"
    # 구버전 매수차단 계열은 표시 호환용으로만 자동마감에 편입한다.
    if status in ("SELL_ONLY", "WATCH_SELL", "BUY_SUSPENDED", "BUY_STOPPED"):
        return "AUTO_CLOSE"
    if status in ("STOPPED", "STOP"):
        return "STOPPED"
    return "MONITORING"


def scheduled_status_for_now(config: dict[str, object], now_dt: datetime | None = None) -> str:
    """시간 운영: 설정 시간 안 RUNNING, 종료 후 정규장 종료 전 AUTO_CLOSE, 이후 MONITORING.

    v2.2 기준 AUTO_CLOSE는 신규매수 금지/매도만 의미가 아니라
    마감상태 진입 후 루틴 매수·매도 신호를 정책상 수용하는 상태이다.
    """
    return status_for_schedule_window(config, now_dt, after_end_status="AUTO_CLOSE")


def start_status_by_operation_mode(config: dict[str, object]) -> tuple[str, str, str]:
    mode = normalize_operation_mode(config.get("operation_mode", "SCHEDULED"))

    if not real_trade_enabled(config):
        return "MONITORING", mode, "감시전용 루틴: 실주문 차단"

    if mode == "CONTINUOUS":
        status = manual_status_for_now(config=config)
        return status, mode, f"수동 운영 시간정책 기준 시작 상태 결정: {auto_trade_status_display(status)}"

    status = scheduled_status_for_now(config)
    return status, mode, f"시간 운영 시작 상태 결정: {auto_trade_status_display(status)}"


def operation_mode_recalculation_target_status(current_status: object) -> str | None:
    status = str(current_status or "STOPPED").strip().upper()
    if status in ("MONITORING", "RUNNING", "SELL_ONLY", "AUTO_CLOSE", "AUTO_CLOSING"):
        return "AUTO_CLOSE" if status in ("SELL_ONLY", "AUTO_CLOSING") else status
    return None


def status_after_operation_mode_change(mode: str, config: dict[str, object]) -> str:
    normalized_mode = normalize_operation_mode(mode)
    if not real_trade_enabled(config):
        return "MONITORING"
    if normalized_mode == "CONTINUOUS":
        return manual_status_for_now(config=config)
    return scheduled_status_for_now(config)
