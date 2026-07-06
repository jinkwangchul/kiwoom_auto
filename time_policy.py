# -*- coding: utf-8 -*-
"""
time_policy.py

운영시간/시장시간 판정 전용 모듈.

역할 분리 원칙:
- 환경설정은 '시간표'만 가진다.
- 종목 설정은 어떤 시장/정책을 사용할지 결정한다.
- state_policy.py는 상태 표시명/색상 전용으로 남긴다.
- gui_windows.py는 버튼/화면 연결만 담당한다.

현재 v1 범위:
- 정규장 시간 판정
- 시간운영 상태 판정
- 수동운영 상태 판정
- 넥스트장/추가시장 시간표 구조 제공
- 조기마감 정책 판정 골격 제공

주의:
- 이 파일은 기존 GUI에 아직 자동 연결하지 않는다.
- 먼저 단독 테스트 후 gui_windows.py에 아주 작은 범위로 연결한다.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent
OPERATING_TIME_CONFIG_PATH = PROJECT_ROOT / "operating_time_config.json"


# 내부 상태 코드. 화면 표시는 state_policy.py에서 처리한다.
STATUS_MONITORING = "MONITORING"      # 감시/대기
STATUS_RUNNING = "RUNNING"            # 매수/매도
STATUS_SELL_ONLY = "SELL_ONLY"        # 구버전 호환: 과거 감시/매도. 신규 GUI 정책은 AUTO_CLOSE 우선.
STATUS_EARLY_CLOSE = "EARLY_CLOSE"    # 조기마감
STATUS_EMERGENCY_STOP = "EMERGENCY_STOPPED"
STATUS_REVIEW_REQUIRED = "REVIEW_REQUIRED"


DEFAULT_OPERATING_TIME_CONFIG: dict[str, Any] = {
    "regular_market": {
        "start_time": "09:00:00",
        "realtime_end_time": "15:20:00",
        "closing_auction_start_time": "15:20:00",
        "closing_auction_end_time": "15:30:00",
    },
    "global_scheduled_trade": {
        "buy_start_time": "09:00:00",
        "buy_end_time": "13:30:00",
    },
    "extra_markets": [
        {
            "name": "NEXT_MARKET",
            "display_name": "넥스트장",
            "enabled": False,
            "start_time": "08:00:00",
            "end_time": "20:00:00",
        },
        {
            "name": "EXTRA_MARKET_1",
            "display_name": "추가시장1",
            "enabled": False,
            "start_time": "00:00:00",
            "end_time": "00:00:00",
        },
        {
            "name": "EXTRA_MARKET_2",
            "display_name": "추가시장2",
            "enabled": False,
            "start_time": "00:00:00",
            "end_time": "00:00:00",
        },
    ],
    "market_close_liquidation": {
        "enabled": False,
        "minutes_before_regular_end": 10,
        "order_type": "MARKET",  # MARKET / CURRENT_PRICE
    },
}


PROTECTED_STATUSES = {
    STATUS_EMERGENCY_STOP,
    "EMERGENCY_STOP",
    "EMERGENCY",
    STATUS_REVIEW_REQUIRED,
    "REVIEW",
    "ERROR",
}


def normalized_hhmmss(value: object, default: str = "00:00:00") -> str:
    """HH:MM 또는 HH:MM:SS를 HH:MM:SS로 정규화한다."""
    text = str(value or "").strip()
    if not text:
        text = default

    parts = text.split(":")
    if len(parts) == 2:
        parts.append("00")
    if len(parts) != 3:
        return default

    try:
        hour = int(parts[0])
        minute = int(parts[1])
        second = int(parts[2])
    except Exception:
        return default

    if 0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59:
        return f"{hour:02d}:{minute:02d}:{second:02d}"
    return default


def seconds_from_hhmmss(value: object, default: str = "00:00:00") -> int:
    normalized = normalized_hhmmss(value, default)
    hour, minute, second = [int(part) for part in normalized.split(":")]
    return hour * 3600 + minute * 60 + second


def current_seconds(now_dt: datetime | None = None) -> int:
    current = now_dt or datetime.now()
    return current.hour * 3600 + current.minute * 60 + current.second


def is_time_in_range(now_seconds: int, start_time: object, end_time: object) -> bool:
    """
    시간 구간 포함 여부.

    일반 구간: start <= now < end
    자정 넘김 구간: now >= start 또는 now < end
    시작과 종료가 같으면 비활성 구간으로 본다.
    """
    start = seconds_from_hhmmss(start_time)
    end = seconds_from_hhmmss(end_time)
    if start == end:
        return False
    if start < end:
        return start <= now_seconds < end
    return now_seconds >= start or now_seconds < end


def deep_merge_defaults(config: dict[str, Any] | None) -> dict[str, Any]:
    """기본 설정과 저장 설정을 얕은/부분 병합한다."""
    result = json.loads(json.dumps(DEFAULT_OPERATING_TIME_CONFIG, ensure_ascii=False))
    if not isinstance(config, dict):
        return result

    for key, value in config.items():
        if key == "extra_markets" and isinstance(value, list):
            default_markets = result.get("extra_markets", [])
            merged_markets = []
            for index in range(max(len(default_markets), len(value))):
                base = dict(default_markets[index]) if index < len(default_markets) and isinstance(default_markets[index], dict) else {}
                incoming = value[index] if index < len(value) and isinstance(value[index], dict) else {}
                base.update(incoming)
                merged_markets.append(base)
            result[key] = merged_markets
        elif isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key].update(value)
        else:
            result[key] = value
    return result


def load_operating_time_config(path: Path | None = None) -> dict[str, Any]:
    config_path = path or OPERATING_TIME_CONFIG_PATH
    if not config_path.exists():
        return deep_merge_defaults(None)
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return deep_merge_defaults(None)
    return deep_merge_defaults(data if isinstance(data, dict) else None)


def write_default_operating_time_config(path: Path | None = None) -> Path:
    config_path = path or OPERATING_TIME_CONFIG_PATH
    if not config_path.exists():
        config_path.write_text(
            json.dumps(DEFAULT_OPERATING_TIME_CONFIG, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return config_path


def normalize_operation_mode(value: object) -> str:
    mode = str(value or "SCHEDULED").strip().upper()
    if mode in ("CONTINUOUS", "MANUAL", "수동", "수동운영"):
        return "CONTINUOUS"
    return "SCHEDULED"


def is_regular_market_realtime_open(
    operating_config: dict[str, Any] | None = None,
    now_dt: datetime | None = None,
) -> bool:
    config = deep_merge_defaults(operating_config)
    regular = config["regular_market"]
    now_s = current_seconds(now_dt)
    return is_time_in_range(now_s, regular["start_time"], regular["realtime_end_time"])


def is_closing_auction_time(
    operating_config: dict[str, Any] | None = None,
    now_dt: datetime | None = None,
) -> bool:
    config = deep_merge_defaults(operating_config)
    regular = config["regular_market"]
    now_s = current_seconds(now_dt)
    return is_time_in_range(now_s, regular["closing_auction_start_time"], regular["closing_auction_end_time"])


def active_extra_market_names(
    stock_config: dict[str, Any] | None,
    operating_config: dict[str, Any] | None = None,
    now_dt: datetime | None = None,
) -> list[str]:
    """
    현재 열린 추가시장 이름 목록.

    환경설정은 시간표만 제공한다.
    실제 사용 여부는 종목 설정의 enabled_extra_markets 목록으로 판단한다.
    """
    stock_config = stock_config or {}
    config = deep_merge_defaults(operating_config)
    enabled_names = stock_config.get("enabled_extra_markets", [])
    if not isinstance(enabled_names, list):
        enabled_names = []
    enabled_name_set = {str(name).strip() for name in enabled_names if str(name).strip()}

    now_s = current_seconds(now_dt)
    active: list[str] = []
    for market in config.get("extra_markets", []):
        if not isinstance(market, dict):
            continue
        name = str(market.get("name", "")).strip()
        if not name or name not in enabled_name_set:
            continue
        if not bool(market.get("enabled", False)):
            continue
        if is_time_in_range(now_s, market.get("start_time"), market.get("end_time")):
            active.append(name)
    return active


def is_extra_market_open_for_stock(
    stock_config: dict[str, Any] | None,
    operating_config: dict[str, Any] | None = None,
    now_dt: datetime | None = None,
) -> bool:
    return bool(active_extra_market_names(stock_config, operating_config, now_dt))


def is_trading_allowed_for_manual(
    stock_config: dict[str, Any] | None,
    operating_config: dict[str, Any] | None = None,
    now_dt: datetime | None = None,
) -> bool:
    """수동운영 매수/매도 허용 시간 판정."""
    if is_regular_market_realtime_open(operating_config, now_dt):
        return True
    return is_extra_market_open_for_stock(stock_config, operating_config, now_dt)


def effective_scheduled_trade_times(
    stock_config: dict[str, Any] | None,
    operating_config: dict[str, Any] | None = None,
) -> tuple[str, str, bool]:
    """
    시간운영 매수 시작/종료 시간.

    종목 개별 시간이 유효하면 개별 시간 우선.
    없으면 환경설정 전역 시간 사용.
    """
    stock_config = stock_config or {}
    config = deep_merge_defaults(operating_config)
    global_trade = config["global_scheduled_trade"]

    local_start = normalized_hhmmss(
        stock_config.get("start_time", stock_config.get("trade_start_time", "")),
        "",
    )
    local_end = normalized_hhmmss(
        stock_config.get("end_buy_time", stock_config.get("buy_end_time", "")),
        "",
    )
    if local_start and local_end and seconds_from_hhmmss(local_start) < seconds_from_hhmmss(local_end):
        return local_start, local_end, True

    return (
        normalized_hhmmss(global_trade.get("buy_start_time"), "09:00:00"),
        normalized_hhmmss(global_trade.get("buy_end_time"), "13:30:00"),
        False,
    )


def target_status_for_manual(
    stock_config: dict[str, Any] | None,
    operating_config: dict[str, Any] | None = None,
    now_dt: datetime | None = None,
) -> str:
    if is_trading_allowed_for_manual(stock_config, operating_config, now_dt):
        return STATUS_RUNNING
    return STATUS_MONITORING


def target_status_for_scheduled(
    stock_config: dict[str, Any] | None,
    operating_config: dict[str, Any] | None = None,
    now_dt: datetime | None = None,
) -> str:
    """시간운영 목표 상태 판정."""
    config = deep_merge_defaults(operating_config)
    now_s = current_seconds(now_dt)

    # 정규장 밖에서는 종목별 추가시장을 허용한 경우에만 별도 시간운영 가능.
    if not is_regular_market_realtime_open(config, now_dt):
        if is_extra_market_open_for_stock(stock_config, config, now_dt):
            return STATUS_RUNNING
        return STATUS_MONITORING

    start_time, end_buy_time, _ = effective_scheduled_trade_times(stock_config, config)
    start_s = seconds_from_hhmmss(start_time)
    end_s = seconds_from_hhmmss(end_buy_time)

    if start_s <= now_s < end_s:
        return STATUS_RUNNING

    regular_end_s = seconds_from_hhmmss(config["regular_market"]["realtime_end_time"], "15:20:00")
    if end_s <= now_s < regular_end_s:
        return STATUS_SELL_ONLY

    return STATUS_MONITORING


def target_status_by_operation_mode(
    stock_config: dict[str, Any] | None,
    operating_config: dict[str, Any] | None = None,
    now_dt: datetime | None = None,
) -> str:
    stock_config = stock_config or {}
    mode = normalize_operation_mode(stock_config.get("operation_mode", "SCHEDULED"))
    if mode == "CONTINUOUS":
        return target_status_for_manual(stock_config, operating_config, now_dt)
    return target_status_for_scheduled(stock_config, operating_config, now_dt)


def should_apply_market_close_liquidation(
    stock_config: dict[str, Any] | None,
    operating_config: dict[str, Any] | None = None,
    now_dt: datetime | None = None,
) -> bool:
    """
    장마감 청산정책 적용 여부.

    환경설정의 정규장 실시간 종료 시간을 기준으로 N분 전부터 True.
    종목별로 disable_market_close_liquidation=True이면 적용하지 않는다.
    """
    stock_config = stock_config or {}
    if stock_config.get("disable_market_close_liquidation", False):
        return False

    config = deep_merge_defaults(operating_config)
    policy = config.get("market_close_liquidation", {})
    if not bool(policy.get("enabled", False)):
        return False

    now_s = current_seconds(now_dt)
    regular = config["regular_market"]
    regular_start_s = seconds_from_hhmmss(regular["start_time"], "09:00:00")
    regular_end_s = seconds_from_hhmmss(regular["realtime_end_time"], "15:20:00")
    minutes_before = int(policy.get("minutes_before_regular_end", 10) or 10)
    trigger_s = max(regular_start_s, regular_end_s - minutes_before * 60)
    return trigger_s <= now_s < regular_end_s


def market_close_order_type(operating_config: dict[str, Any] | None = None) -> str:
    config = deep_merge_defaults(operating_config)
    order_type = str(config.get("market_close_liquidation", {}).get("order_type", "MARKET")).strip().upper()
    if order_type not in ("MARKET", "CURRENT_PRICE"):
        return "MARKET"
    return order_type


def is_protected_status(raw_status: object) -> bool:
    status = str(raw_status or "").strip().upper()
    return status in PROTECTED_STATUSES


def target_status_for_start_monitoring(
    stock_config: dict[str, Any] | None,
    operating_config: dict[str, Any] | None = None,
    now_dt: datetime | None = None,
) -> str:
    """감시시작 버튼을 눌렀을 때 저장할 목표 상태."""
    return target_status_by_operation_mode(stock_config, operating_config, now_dt)


if __name__ == "__main__":
    # 간단 자체 점검
    cfg = deep_merge_defaults(None)
    sample_manual = {"operation_mode": "CONTINUOUS"}
    sample_scheduled = {"operation_mode": "SCHEDULED", "start_time": "09:00:00", "end_buy_time": "13:30:00"}
    print("manual:", target_status_for_manual(sample_manual, cfg))
    print("scheduled:", target_status_for_scheduled(sample_scheduled, cfg))
