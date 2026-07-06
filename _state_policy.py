# -*- coding: utf-8 -*-
"""
state_policy.py

자동매매 상태 표시명, 운영방식, 시간 운영 판정, 감시시작 상태 판정 정책.
GUI 코드에서 상태 정책을 분리해 UI 수정과 기능 수정의 충돌을 줄인다.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
GLOBAL_SCHEDULE_PATH = PROJECT_ROOT / "global_schedule.json"


def auto_trade_status_display(raw_status: object) -> str:
    status = str(raw_status or "STOPPED").strip().upper()
    mapping = {
        "STOPPED": "정지",
        "STOP": "정지",
        "WAIT": "대기",
        "WAIT_BUY": "매수대기",
        "WAIT_SELL": "매도대기",
        "RUNNING": "매수/매도",
        "MONITORING": "감시중",
        "SCHEDULED": "예약",
        "SELL_ONLY": "감시/매도",
        "BUY_SUSPENDED": "감시/매도",
        "BUY_STOPPED": "감시/매도",
        "매수중지": "감시/매도",
        "PAUSED": "일시중지",
        "REVIEW_REQUIRED": "검토필요",
        "STARTED": "매수/매도",
        "AUTO": "매수/매도",
        "TRADING": "매수/매도",
        "WATCHING": "감시중",
        "BUYING": "매수중",
        "SELLING": "매도중",
        "EMERGENCY_STOPPED": "비상정지",
    }
    return mapping.get(status, status if status else "정지")


def auto_trade_status_color(display_status: str) -> str:
    normalized = str(display_status or "").strip()
    color_map = {
        "정지": "#dc2626",
        "감시중": "#2563eb",
        "매수/매도": "#16a34a",
        "감시/매도": "#7c3aed",
        "매수중지": "#7c3aed",
        "일시중지": "#f97316",
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


def read_global_schedule() -> dict[str, str]:
    default = {"start_time": "09:00:00", "end_buy_time": "13:30:00"}
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


def write_global_schedule(start_time: str, end_buy_time: str) -> None:
    data = {
        "start_time": normalized_hhmmss_or_empty(start_time) or "09:00:00",
        "end_buy_time": normalized_hhmmss_or_empty(end_buy_time) or "13:30:00",
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    GLOBAL_SCHEDULE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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


REGULAR_SESSION_START_TIME = "09:00:00"
REGULAR_SESSION_END_TIME = "15:20:00"


def in_regular_manual_session(now_dt: datetime | None = None) -> bool:
    """정규 매매 허용 시간(09:00~15:20) 안인지 판단한다."""
    current = now_dt or datetime.now()
    current_seconds = current.hour * 3600 + current.minute * 60 + current.second
    start_seconds = seconds_from_hhmmss(REGULAR_SESSION_START_TIME, "09:00:00")
    end_seconds = seconds_from_hhmmss(REGULAR_SESSION_END_TIME, "15:20:00")
    return start_seconds <= current_seconds < end_seconds


def manual_status_for_now(now_dt: datetime | None = None) -> str:
    """
    수동 운영의 정규 매매 허용 시간 판정.

    정책:
    - 수동 운영은 사용자가 감시시작/감시종료로 직접 켜고 끄는 방식이다.
    - 단, 실제 매수/매도 주문 허용은 보수적으로 09:00:00~15:20:00 안에서만 RUNNING 이다.
    - 시간 밖에서는 감시는 유지하되 주문은 열지 않으므로 MONITORING 이다.
    - 시간 경과만으로 SELL_ONLY(감시/매도)로 자동 전환하지 않는다.
    """
    if in_regular_manual_session(now_dt):
        return "RUNNING"
    return "MONITORING"


def operation_text_and_color(config: dict[str, object]) -> tuple[str, str, str]:
    mode = normalize_operation_mode(config.get("operation_mode", "SCHEDULED"))
    if mode == "CONTINUOUS":
        return "수동", "#6A1B9A", "수동 운영: 09:00~15:20 안에서 감시시작 시 매수/매도, 시간 밖은 감시중"

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
    status = str(value or "MONITORING").strip().upper()
    if status in ("STOPPED", "STOP"):
        return "STOPPED"
    return "MONITORING"


def scheduled_status_for_now(config: dict[str, object], now_dt: datetime | None = None) -> str:
    current = now_dt or datetime.now()
    current_seconds = current.hour * 3600 + current.minute * 60 + current.second

    # 시간운영도 실제 주문 허용은 정규 운용 시간(09:00~15:20) 안으로 제한한다.
    # 정규 운용 시간 밖에서는 스케줄 종료 후라도 SELL_ONLY로 열지 않고 MONITORING으로 강제한다.
    if not in_regular_manual_session(current):
        return "MONITORING"

    start_time, end_buy_time, _ = effective_schedule_times(config)
    start_seconds = seconds_from_hhmmss(start_time, "09:00:00")
    end_seconds = seconds_from_hhmmss(end_buy_time, "13:30:00")

    if not (start_seconds < end_seconds):
        start_seconds = seconds_from_hhmmss("09:00:00", "09:00:00")
        end_seconds = seconds_from_hhmmss("13:30:00", "13:30:00")

    if current_seconds < start_seconds:
        return "MONITORING"
    if current_seconds < end_seconds:
        return "RUNNING"
    return "SELL_ONLY"


def start_status_by_operation_mode(config: dict[str, object]) -> tuple[str, str, str]:
    mode = normalize_operation_mode(config.get("operation_mode", "SCHEDULED"))

    if not real_trade_enabled(config):
        return "MONITORING", mode, "감시전용 루틴: 실주문 차단"

    if mode == "CONTINUOUS":
        status = manual_status_for_now()
        return status, mode, f"수동 운영 정규시간 기준 시작 상태 결정: {auto_trade_status_display(status)}"

    status = scheduled_status_for_now(config)
    return status, mode, f"시간 운영 시작 상태 결정: {auto_trade_status_display(status)}"


def operation_mode_recalculation_target_status(current_status: object) -> str | None:
    status = str(current_status or "STOPPED").strip().upper()
    if status in ("MONITORING", "RUNNING", "SELL_ONLY"):
        return status
    return None


def status_after_operation_mode_change(mode: str, config: dict[str, object]) -> str:
    normalized_mode = normalize_operation_mode(mode)
    if not real_trade_enabled(config):
        return "MONITORING"
    if normalized_mode == "CONTINUOUS":
        return manual_status_for_now()
    return scheduled_status_for_now(config)
