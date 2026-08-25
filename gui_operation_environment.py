# -*- coding: utf-8 -*-
"""
gui_operation_environment.py

운영환경설정 전용 모듈.
- operation_policy.json 기본값
- 운영환경설정 읽기/쓰기
- 시간 콤보 위젯
- 운영환경설정 다이얼로그

주의:
- 자동매매 상태판정/청산/ATS 실행 로직은 포함하지 않는다.
"""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR
from pathlib import Path
from typing import Mapping

from PyQt5.QtCore import QSettings, Qt, QTime, QTimer
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QStyle,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
    QMessageBox,
    QFrame,
)


PROJECT_ROOT = Path(__file__).resolve().parent
from state_policy import normalized_hhmmss_or_empty
from gui_toast import show_toast
from event_journal_production import append_production_event
from gui_window_policy import (
    configure_persistent_feature_window,
    persistent_feature_owner,
)


OPERATION_POLICY_PATH = PROJECT_ROOT / "operation_policy.json"
CHANGELOG_PATH = PROJECT_ROOT / "PROJECT_CHANGELOG.txt"

STARTING_BUDGET_DEFAULTS = {
    "quantity": 1,
    "amount_multiplier": 1.5,
    "limit_recommended_multiplier": 100.0,
    "limit_minimum_multiplier": 25.0,
}
STOCK_LIMIT_DIGIT_ALIGNMENT_SETTINGS_KEY = "ui/stock_limit_digit_alignment"
SYSTEM_BUDGET_MAX_AMOUNT = 9_999_999_999
SYSTEM_BUDGET_DEFAULTS = {
    "total_budget": 2_000_000,
    "available_budget_percent": 100,
}
REVIEW_POLICY_DEFAULTS = {
    "long_term_holding_enabled": False,
}
STOCK_REGISTRATION_LOCATIONS = ("WAITING", "EXCLUDED")
STOCK_REGISTRATION_DEFAULTS = {"default_location": "WAITING"}
BUFFER_RESPONSE_APPLICATION_MODES = ("UNIFIED", "SEGMENTED")
BUFFER_RESPONSE_EVALUATION_FACTORS = ("손익비율", "손익금액", "투입금액")
BUFFER_RESPONSE_SORT_DIRECTIONS = ("높은순", "낮은순")
BUFFER_RESPONSE_ACTION_MODES = ("조기마감", "즉시청산", "구간마감")
BUFFER_RESPONSE_THRESHOLDS = tuple(range(10, 100, 10))
BUFFER_RESPONSE_STRATEGY_KEYS = ("unified", "profit", "loss")


def _journal_changes(
    before: Mapping[str, object],
    after: Mapping[str, object],
    *,
    prefix: str = "",
) -> list[dict[str, object]]:
    """Return changed leaf settings in the Event Journal v1 shape."""

    changes: list[dict[str, object]] = []
    for key in sorted(set(before) | set(after)):
        if key == "updated_at":
            continue
        field_key = f"{prefix}.{key}" if prefix else str(key)
        before_value = before.get(key)
        after_value = after.get(key)
        if isinstance(before_value, Mapping) and isinstance(after_value, Mapping):
            changes.extend(
                _journal_changes(before_value, after_value, prefix=field_key)
            )
        elif before_value != after_value:
            changes.append(
                {
                    "field_key": field_key,
                    "before": before_value,
                    "after": after_value,
                }
            )
    return changes


def _append_setting_change(
    event_type: str,
    *,
    source: str,
    target: str,
    target_id: str,
    changes: list[dict[str, object]],
) -> None:
    if not changes:
        return
    template_args = {"target": target} if event_type == "SETTING_CHANGED" else {}
    append_production_event(
        event_type,
        result="SUCCESS",
        source=source,
        template_args=template_args,
        target_type="SYSTEM_SETTING",
        target_id=target_id,
        target_name=target,
        changes=changes,
    )


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")



def append_changelog(change_type: str, filename: str, message: str) -> None:
    """환경설정 저장 변경사항을 PROJECT_CHANGELOG.txt에 기록한다."""
    block = (
        f"\n[{now_text()}]\n"
        f"버전: v1.1\n"
        f"구분: {change_type}\n"
        f"파일: {filename}\n"
        f"내용: {message}\n"
        f"작성자: admin\n"
    )
    with CHANGELOG_PATH.open("a", encoding="utf-8") as file:
        file.write(block)


def default_operation_policy() -> dict[str, object]:
    """운영환경설정 기본값.

    현재 단계에서는 UI/저장 구조를 먼저 확정한다.
    실제 자동판정 엔진 연결은 후속 패치에서 단계적으로 반영한다.
    """
    return {
        "regular_market": {
            "start_time": "09:00:00",
            "end_time": "15:20:00",
        },
        "extra_sessions": [
            {"enabled": False, "name": "장전프리", "start_time": "08:00:00", "end_time": "08:50:00"},
            {"enabled": False, "name": "장마감NTX", "start_time": "15:40:00", "end_time": "19:50:00"},
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
            "method": "루틴매도신호",
            "profit_percent": "",
            "loss_percent": "",
        },
        "liquidation": {
            "minutes_before_regular_close": "5",
            "method": "시장가",
        },
        "review_policy": dict(REVIEW_POLICY_DEFAULTS),
        "stock_registration": dict(STOCK_REGISTRATION_DEFAULTS),
        "system_budget": dict(SYSTEM_BUDGET_DEFAULTS),
        "starting_budget_defaults": dict(STARTING_BUDGET_DEFAULTS),
        "updated_at": "",
    }


def read_operation_policy(*, path: Path | None = None) -> dict[str, object]:
    default = default_operation_policy()
    target_path = Path(path) if path is not None else OPERATION_POLICY_PATH
    if not target_path.exists():
        return default
    try:
        data = json.loads(target_path.read_text(encoding="utf-8"))
    except Exception:
        return default
    if not isinstance(data, dict):
        return default

    # 얕은 병합: 누락된 상위 항목은 기본값으로 보완한다.
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


def write_operation_policy(
    policy: dict[str, object],
    *,
    path: Path | None = None,
    preserve_buffer_response: bool = True,
) -> None:
    policy = dict(policy)
    target_path = Path(path) if path is not None else OPERATION_POLICY_PATH
    existing: dict[str, object] | None = None
    if (
        "system_budget" not in policy
        or "review_policy" not in policy
        or "stock_registration" not in policy
        or (preserve_buffer_response and "buffer_response" not in policy)
    ):
        existing = read_operation_policy(path=target_path)
    if "system_budget" not in policy:
        assert existing is not None
        policy["system_budget"] = system_budget_policy(existing)
    else:
        policy["system_budget"] = system_budget_policy(policy)
    if "review_policy" not in policy:
        assert existing is not None
        policy["review_policy"] = review_policy(existing)
    else:
        policy["review_policy"] = review_policy(policy)
    if "stock_registration" not in policy:
        assert existing is not None
        policy["stock_registration"] = stock_registration_policy(existing)
    else:
        policy["stock_registration"] = stock_registration_policy(policy)
    if (
        preserve_buffer_response
        and "buffer_response" not in policy
        and isinstance(existing, dict)
        and "buffer_response" in existing
    ):
        policy["buffer_response"] = existing["buffer_response"]
    scheduled = policy.get("scheduled_operation")
    if isinstance(scheduled, dict):
        policy["scheduled_operation"] = {
            key: value
            for key, value in scheduled.items()
            if key != "after_buy_end_status"
        }
    policy["updated_at"] = now_text()
    target_path.write_text(
        json.dumps(policy, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def stock_registration_policy(
    policy: dict[str, object] | None = None,
) -> dict[str, str]:
    source = policy if isinstance(policy, dict) else {}
    section = source.get("stock_registration")
    raw_location = (
        section.get("default_location") if isinstance(section, dict) else None
    )
    location = str(raw_location or "").strip().upper()
    if location not in STOCK_REGISTRATION_LOCATIONS:
        location = "WAITING"
    return {"default_location": location}


def _strict_integer(value: object) -> int:
    if isinstance(value, bool):
        raise ValueError("boolean is not an integer setting")
    if isinstance(value, int):
        return value
    text = str(value).replace(",", "").strip()
    if not text or not text.isdigit():
        raise ValueError("integer setting required")
    return int(text)


def validate_system_total_budget(value: object) -> int:
    amount = _strict_integer(value)
    if amount < 0 or amount > SYSTEM_BUDGET_MAX_AMOUNT:
        raise ValueError("total_budget is outside the supported range")
    return amount


def validate_available_budget_percent(value: object) -> int:
    percent = _strict_integer(value)
    if percent < 1 or percent > 100:
        raise ValueError("available_budget_percent must be between 1 and 100")
    return percent


def review_policy(policy: dict[str, object] | None = None) -> dict[str, bool]:
    """Return the normalized global Review-classification policy."""
    source = policy if isinstance(policy, dict) else {}
    raw_section = source.get("review_policy")
    section = raw_section if isinstance(raw_section, dict) else {}
    raw_enabled = section.get(
        "long_term_holding_enabled",
        REVIEW_POLICY_DEFAULTS["long_term_holding_enabled"],
    )
    if isinstance(raw_enabled, bool):
        enabled = raw_enabled
    elif isinstance(raw_enabled, str):
        normalized = raw_enabled.strip().upper()
        enabled = normalized in {"TRUE", "1", "YES", "Y", "ON"}
    else:
        enabled = False
    return {"long_term_holding_enabled": enabled}


def read_review_policy(*, path: Path | None = None) -> dict[str, bool]:
    return review_policy(read_operation_policy(path=path))


def write_long_term_holding_policy(
    enabled: bool,
    *,
    path: Path | None = None,
) -> dict[str, bool]:
    """Persist the single global long-hold Review policy with read-back."""
    if not isinstance(enabled, bool):
        raise ValueError("long_term_holding_enabled must be boolean")
    before = read_review_policy(path=path)
    normalized = {"long_term_holding_enabled": enabled}
    policy = read_operation_policy(path=path)
    policy["review_policy"] = dict(normalized)
    write_operation_policy(policy, path=path)
    saved = read_review_policy(path=path)
    if saved != normalized:
        raise RuntimeError("review_policy read-back verification failed")
    try:
        _append_setting_change(
            "SETTING_CHANGED",
            source="LONG_TERM_HOLDING_POLICY_WRITER",
            target="장기보유",
            target_id="GLOBAL_REVIEW_POLICY",
            changes=_journal_changes(before, saved),
        )
    except Exception:
        pass
    return saved


def system_budget_policy(policy: dict[str, object] | None = None) -> dict[str, int]:
    """Return the normalized system-wide budget policy.

    A missing field receives the confirmed initial default. An explicitly
    malformed amount fails closed to zero; an invalid ratio disables the
    buffer by falling back to 100 percent available.
    """
    source = policy if isinstance(policy, dict) else {}
    raw = source.get("system_budget")
    section_present = "system_budget" in source
    section = raw if isinstance(raw, dict) else {}

    if "total_budget" not in section:
        total_budget = (
            int(SYSTEM_BUDGET_DEFAULTS["total_budget"])
            if not section_present
            else 0
        )
    else:
        try:
            total_budget = validate_system_total_budget(section.get("total_budget"))
        except ValueError:
            total_budget = 0

    if "available_budget_percent" not in section:
        available_percent = int(SYSTEM_BUDGET_DEFAULTS["available_budget_percent"])
    else:
        try:
            available_percent = validate_available_budget_percent(
                section.get("available_budget_percent")
            )
        except ValueError:
            available_percent = 100

    return {
        "total_budget": total_budget,
        "available_budget_percent": available_percent,
    }


def read_system_budget_policy(*, path: Path | None = None) -> dict[str, int]:
    return system_budget_policy(read_operation_policy(path=path))


def read_system_total_budget_for_recalculation(
    *,
    path: Path | None = None,
) -> int | None:
    """Read total_budget without masking malformed persisted evidence."""
    target_path = Path(path) if path is not None else OPERATION_POLICY_PATH
    if not target_path.exists():
        return int(SYSTEM_BUDGET_DEFAULTS["total_budget"])
    try:
        persisted = json.loads(target_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(persisted, dict):
        return None
    section = persisted.get("system_budget")
    if section is None:
        return int(SYSTEM_BUDGET_DEFAULTS["total_budget"])
    if not isinstance(section, dict):
        return None
    if "total_budget" not in section:
        return 0
    try:
        return validate_system_total_budget(section["total_budget"])
    except ValueError:
        return None


def write_system_budget_policy(
    *,
    total_budget: object,
    available_budget_percent: object,
    path: Path | None = None,
) -> dict[str, int]:
    """Persist the two canonical system-budget values through the policy writer."""
    before = read_system_budget_policy(path=path)
    normalized = {
        "total_budget": validate_system_total_budget(total_budget),
        "available_budget_percent": validate_available_budget_percent(
            available_budget_percent
        ),
    }
    policy = read_operation_policy(path=path)
    policy["system_budget"] = dict(normalized)
    write_operation_policy(policy, path=path)
    saved = read_system_budget_policy(path=path)
    if saved != normalized:
        raise RuntimeError("system_budget read-back verification failed")
    _append_setting_change(
        "SETTING_CHANGED",
        source="SYSTEM_BUDGET_WRITER",
        target="예산설정",
        target_id="SYSTEM_BUDGET",
        changes=_journal_changes(before, saved),
    )
    return saved


def default_buffer_response_policy() -> dict[str, object]:
    """Return editor defaults without making them a configured policy."""

    return {
        "application_mode": "UNIFIED",
        "threshold_percent": 80,
        "strategies": {
            "unified": {
                "evaluation_factor": "손익금액",
                "direction": "낮은순",
                "response_mode": "조기마감",
            },
            "profit": {
                "evaluation_factor": "손익금액",
                "direction": "높은순",
                "response_mode": "조기마감",
            },
            "loss": {
                "evaluation_factor": "손익금액",
                "direction": "낮은순",
                "response_mode": "즉시청산",
            },
        },
    }


def validate_buffer_response_policy(
    value: Mapping[str, object] | object,
) -> dict[str, object]:
    """Validate and normalize the complete persisted buffer-response section."""

    if not isinstance(value, Mapping):
        raise ValueError("buffer_response policy must be an object")
    application_mode = str(value.get("application_mode") or "").strip().upper()
    if application_mode not in BUFFER_RESPONSE_APPLICATION_MODES:
        raise ValueError("buffer_response application_mode is invalid")

    threshold_value = value.get(
        "threshold_percent",
        value.get("configured_threshold"),
    )
    threshold_percent = _strict_integer(threshold_value)
    if threshold_percent not in BUFFER_RESPONSE_THRESHOLDS:
        raise ValueError("buffer_response threshold_percent is invalid")

    raw_strategies = value.get("strategies")
    if not isinstance(raw_strategies, Mapping):
        raise ValueError("buffer_response strategies are invalid")
    strategies: dict[str, dict[str, str]] = {}
    for key in BUFFER_RESPONSE_STRATEGY_KEYS:
        raw_strategy = raw_strategies.get(key)
        if not isinstance(raw_strategy, Mapping):
            raise ValueError(f"buffer_response strategy {key} is invalid")
        evaluation_factor = str(
            raw_strategy.get("evaluation_factor") or ""
        ).strip()
        direction = str(raw_strategy.get("direction") or "").strip()
        response_mode = str(raw_strategy.get("response_mode") or "").strip()
        if evaluation_factor not in BUFFER_RESPONSE_EVALUATION_FACTORS:
            raise ValueError(f"buffer_response strategy {key} factor is invalid")
        if direction not in BUFFER_RESPONSE_SORT_DIRECTIONS:
            raise ValueError(f"buffer_response strategy {key} direction is invalid")
        if response_mode not in BUFFER_RESPONSE_ACTION_MODES:
            raise ValueError(f"buffer_response strategy {key} response is invalid")
        strategies[key] = {
            "evaluation_factor": evaluation_factor,
            "direction": direction,
            "response_mode": response_mode,
        }
    return {
        "application_mode": application_mode,
        "threshold_percent": threshold_percent,
        "strategies": strategies,
    }


def _buffer_response_unavailable(reason: str) -> dict[str, object]:
    return {
        "available": False,
        "configured": False,
        "application_mode": "",
        "threshold_percent": None,
        "configured_threshold": None,
        "strategies": {},
        "reason": reason,
    }


def read_buffer_response_policy(*, path: Path | None = None) -> dict[str, object]:
    """Read only an explicitly persisted, fully valid buffer-response policy."""

    policy = read_operation_policy(path=path)
    if "buffer_response" not in policy:
        return _buffer_response_unavailable("BUFFER_RESPONSE_POLICY_NOT_CONFIGURED")
    try:
        normalized = validate_buffer_response_policy(policy.get("buffer_response"))
    except ValueError:
        return _buffer_response_unavailable("BUFFER_RESPONSE_POLICY_MALFORMED")
    return {
        "available": True,
        "configured": True,
        **normalized,
        "configured_threshold": normalized["threshold_percent"],
        "reason": "",
    }


def write_buffer_response_policy(
    policy: Mapping[str, object] | object,
    *,
    path: Path | None = None,
) -> dict[str, object]:
    """Persist one validated section while preserving all other policy keys."""

    current_policy = read_operation_policy(path=path)
    current_section = current_policy.get("buffer_response")
    try:
        before = validate_buffer_response_policy(current_section)
    except ValueError:
        before = {}
    normalized = validate_buffer_response_policy(policy)
    operation_policy = current_policy
    operation_policy["buffer_response"] = normalized
    write_operation_policy(operation_policy, path=path)
    saved_policy = read_operation_policy(path=path)
    saved = validate_buffer_response_policy(saved_policy.get("buffer_response"))
    if saved != normalized:
        raise RuntimeError("buffer_response read-back verification failed")
    _append_setting_change(
        "SETTING_CHANGED",
        source="BUFFER_RESPONSE_POLICY_WRITER",
        target="완충대응 설정",
        target_id="BUFFER_RESPONSE",
        changes=_journal_changes(before, saved),
    )
    return saved


def _positive_decimal(value: object, fallback: float) -> float:
    try:
        parsed = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return fallback
    if not parsed.is_finite() or parsed <= 0:
        return fallback
    return float(parsed)


def _plain_number_text(value: object) -> str:
    try:
        text = format(Decimal(str(value)), "f")
    except InvalidOperation:
        return str(value)
    return text.rstrip("0").rstrip(".") if "." in text else text


def starting_budget_defaults(policy: dict[str, object] | None = None) -> dict[str, float | int]:
    source = policy if isinstance(policy, dict) else read_operation_policy()
    raw = source.get("starting_budget_defaults", {})
    if not isinstance(raw, dict):
        raw = {}
    quantity_value = _positive_decimal(raw.get("quantity"), 1.0)
    return {
        "quantity": max(1, int(quantity_value)),
        "amount_multiplier": _positive_decimal(raw.get("amount_multiplier"), 1.5),
        "limit_recommended_multiplier": _positive_decimal(
            raw.get("limit_recommended_multiplier"), 100.0
        ),
        "limit_minimum_multiplier": _positive_decimal(
            raw.get("limit_minimum_multiplier"), 25.0
        ),
    }


def _ui_settings() -> QSettings:
    return QSettings(
        QSettings.IniFormat,
        QSettings.UserScope,
        "jinkwangchul",
        "kiwoom_auto",
    )


def stock_limit_digit_alignment_enabled() -> bool:
    raw_value = _ui_settings().value(
        STOCK_LIMIT_DIGIT_ALIGNMENT_SETTINGS_KEY,
        True,
    )
    if isinstance(raw_value, bool):
        return raw_value
    if isinstance(raw_value, (int, float)):
        return bool(raw_value)
    return str(raw_value).strip().lower() not in {"0", "false", "no", "off"}


def set_stock_limit_digit_alignment_enabled(enabled: bool) -> None:
    _ui_settings().setValue(
        STOCK_LIMIT_DIGIT_ALIGNMENT_SETTINGS_KEY,
        bool(enabled),
    )


def effective_amount_starting_budget(
    current_price: object,
    amount_multiplier: object,
) -> int | None:
    try:
        price = Decimal(str(current_price).replace(",", "").strip())
        multiplier = Decimal(str(amount_multiplier).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None
    if not price.is_finite() or not multiplier.is_finite() or price <= 0 or multiplier <= 0:
        return None
    return floor_money_to_won(price * multiplier)


def floor_money_to_won(value: object) -> int | None:
    """Apply the existing budget-money FLOOR rule at the one-won boundary."""
    try:
        amount = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None
    if not amount.is_finite() or amount < 0:
        return None
    return int(amount.to_integral_value(rounding=ROUND_FLOOR))


def round_up_to_leading_place(amount: object) -> int | None:
    try:
        value = Decimal(str(amount).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None
    if not value.is_finite() or value <= 0:
        return None
    integer_value = int(value.to_integral_value(rounding=ROUND_CEILING))
    place = 10 ** (len(str(integer_value)) - 1)
    return ((integer_value + place - 1) // place) * place


def suggested_buy_limit(
    starting_budget: object,
    multiplier: object,
    *,
    align_digits: bool = False,
) -> int | None:
    try:
        amount = Decimal(str(starting_budget).replace(",", "").strip()) * Decimal(
            str(multiplier).replace(",", "").strip()
        )
    except (InvalidOperation, ValueError):
        return None
    raw_amount = floor_money_to_won(amount)
    if raw_amount is None:
        return None
    if align_digits:
        from gui_main_budget_panel import round_money_to_two_significant_digits

        return round_money_to_two_significant_digits(raw_amount)
    return raw_amount


class TimeComboWidget(QWidget):
    """시/분 콤보박스로 시간을 선택하는 작은 위젯."""

    def __init__(
        self,
        default_time: str = "09:00:00",
        parent: QWidget | None = None,
        *,
        allow_empty: bool = False,
    ) -> None:
        super().__init__(parent)
        self.allow_empty = allow_empty
        self.hour_combo = QComboBox()
        self.minute_combo = QComboBox()
        if allow_empty:
            self.hour_combo.addItem("")
            self.minute_combo.addItem("")
        self.hour_combo.addItems([f"{hour:02d}" for hour in range(24)])
        self.minute_combo.addItems([f"{minute:02d}" for minute in range(0, 60, 5)])
        self.hour_combo.setFixedWidth(68)
        self.minute_combo.setFixedWidth(68)
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self.hour_combo)
        layout.addWidget(QLabel("시"))
        layout.addWidget(self.minute_combo)
        layout.addWidget(QLabel("분"))
        self.setLayout(layout)
        self.set_time(default_time, default_time)

    def set_time(self, value: object, default_time: str = "09:00:00") -> None:
        if self.allow_empty and str(value or "").strip() == "":
            self.hour_combo.setCurrentText("")
            self.minute_combo.setCurrentText("")
            return
        normalized = normalized_hhmmss_or_empty(value) or normalized_hhmmss_or_empty(default_time) or "09:00:00"
        try:
            hour, minute, _second = [int(part) for part in normalized.split(":")]
        except Exception:
            hour, minute = 9, 0
        rounded_minute = int(minute / 5) * 5
        self.hour_combo.setCurrentText(f"{hour:02d}")
        self.minute_combo.setCurrentText(f"{rounded_minute:02d}")

    def time_text(self) -> str:
        if self.allow_empty and (
            not self.hour_combo.currentText().strip()
            or not self.minute_combo.currentText().strip()
        ):
            return ""
        return f"{int(self.hour_combo.currentText()):02d}:{int(self.minute_combo.currentText()):02d}:00"


class ProgramFactoryResetConfirmDialog(QDialog):
    CONFIRMATION_TEXT = "전체초기화"
    WARNING_TEXT = (
        "프로그램을 완전초기화 합니다.\n"
        "치명적인 손실을 초래할수 있습니다.\n"
        '아래 입력창에 "전체초기화"를 입력하세요.'
    )

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("프로그램 초기화 확인")
        self.setModal(True)

        self.warning_label = QLabel(self.WARNING_TEXT)
        self.warning_label.setWordWrap(True)
        warning_metrics = self.warning_label.fontMetrics()
        warning_width = max(
            warning_metrics.horizontalAdvance(line)
            for line in self.WARNING_TEXT.splitlines()
        )
        horizontal_margin = warning_metrics.horizontalAdvance("가") * 2
        warning_icon_gap = warning_metrics.horizontalAdvance("가")
        warning_icon_size = QApplication.style().pixelMetric(QStyle.PM_MessageBoxIconSize)
        dialog_width = (
            warning_width
            + warning_icon_gap
            + warning_icon_size
            + (horizontal_margin * 2)
        )
        self.setMinimumSize(dialog_width, 210)
        self.resize(dialog_width, 220)
        self.warning_label.setFixedWidth(warning_width)
        self.warning_icon_label = QLabel()
        self.warning_icon_label.setObjectName("programFactoryResetWarningIcon")
        self.warning_icon_label.setFixedSize(warning_icon_size, warning_icon_size)
        self.warning_icon_label.setPixmap(
            QApplication.style()
            .standardIcon(QStyle.SP_MessageBoxWarning)
            .pixmap(warning_icon_size, warning_icon_size)
        )
        self.confirmation_input = QLineEdit()
        self.confirmation_input.setObjectName("programFactoryResetConfirmationInput")
        self.confirmation_input.setMinimumHeight(32)
        input_width = self.confirmation_input.fontMetrics().horizontalAdvance("가" * 10) + 16
        self.confirmation_input.setFixedWidth(input_width)
        self.confirmation_input.setStyleSheet(
            "QLineEdit, QLineEdit:focus {"
            " border: none;"
            " background-color: #FFFFFF;"
            " padding: 0 8px;"
            "}"
        )

        buttons = QDialogButtonBox()
        self.reset_button = buttons.addButton("초기화", QDialogButtonBox.AcceptRole)
        self.cancel_button = buttons.addButton("취소", QDialogButtonBox.RejectRole)
        self.reset_button.setObjectName("programFactoryResetConfirmButton")
        self.reset_button.setEnabled(False)
        self.reset_button.setMinimumWidth(110)
        self.cancel_button.setMinimumWidth(110)
        buttons.layout().setSpacing(18)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.confirmation_input.textChanged.connect(
            lambda text: self.reset_button.setEnabled(text == self.CONFIRMATION_TEXT)
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(horizontal_margin, 20, horizontal_margin, 22)
        layout.setSpacing(14)
        warning_row = QHBoxLayout()
        warning_row.setContentsMargins(0, 0, 0, 0)
        warning_row.setSpacing(0)
        warning_row.addWidget(self.warning_icon_label, 0, Qt.AlignVCenter)
        warning_row.addSpacing(warning_icon_gap)
        warning_row.addWidget(self.warning_label)
        layout.addLayout(warning_row)
        input_row = QHBoxLayout()
        input_row.setContentsMargins(0, 0, 0, 0)
        input_row.setSpacing(0)
        input_row.addSpacing(warning_icon_size + warning_icon_gap)
        input_row.addWidget(self.confirmation_input)
        input_row.addStretch(1)
        layout.addLayout(input_row)
        layout.addSpacing(6)
        layout.addWidget(buttons)
        self.confirmation_input.setFocus()


class OperationEnvironmentSettingsDialog(QDialog):
    """스케줄매매관리 대체용 운영환경설정 UI.

    환경설정은 전체 기본값이며, 개별 종목 예외는 종목 우클릭 설정에서 처리한다.
    """

    CLOSE_METHODS = ["루틴매도신호", "시장가", "현재가", "익절/손절", "이월"]
    LIQUIDATION_METHODS = ["이월", "시장가", "현재가"]

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(None)
        configure_persistent_feature_window(self, parent)
        self.setWindowTitle("환경설정")
        self.setStyleSheet("""
            QDialog, QWidget, QLabel, QCheckBox, QComboBox, QLineEdit, QPushButton {
                font-family: '맑은 고딕';
                font-size: 9pt;
            }
            QGroupBox {
                font-family: '맑은 고딕';
                font-size: 10pt;
                font-weight: bold;
            }
            QComboBox {
                min-height: 24px;
            }
            QLineEdit {
                min-height: 24px;
            }
            QPushButton {
                min-height: 28px;
                min-width: 82px;
            }
        """)
        self.resize(1080, 700)
        self.policy = read_operation_policy()
        self.setStyleSheet(
            "QGroupBox { font-size: 9pt; font-weight: bold; margin-top: 10px; }"
            "QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 6px; }"
            "QLabel, QCheckBox, QComboBox, QLineEdit, QPushButton { font-size: 9pt; }"
            "QComboBox, QLineEdit { min-height: 30px; }"
        )

        self.regular_start = self._make_time_edit("09:00:00")
        self.regular_end = self._make_time_edit("15:20:00")
        self.scheduled_start = self._make_time_edit("09:00:00")
        self.scheduled_end_buy = self._make_time_edit("13:30:00")

        self.extra_name: list[QLineEdit] = []
        self.extra_enabled: list[QCheckBox] = []
        self.extra_start: list[TimeComboWidget] = []
        self.extra_end: list[TimeComboWidget] = []

        self.manual_use_regular = QCheckBox("정규장 사용")
        self.manual_extra_checks = [QCheckBox(f"추가{i}") for i in range(1, 4)]
        self.manual_liquidation = QCheckBox("청산정책 적용")

        
        self.auto_close_method = QComboBox()
        # 체크박스 UI와 저장용 숨김 콤보의 항목은 반드시 1회만 등록한다.
        self.auto_close_method.setVisible(False)
        self.auto_close_signal = QCheckBox("루틴마감")
        self.auto_close_market = QCheckBox("시장가")
        self.auto_close_current = QCheckBox("현재가")
        self.auto_close_profit_loss = QCheckBox("익절/손절")
        self.auto_close_signal.setChecked(True)
        self.auto_close_options = [
            self.auto_close_signal,
            self.auto_close_market,
            self.auto_close_current,
            self.auto_close_profit_loss,
        ]
        for _cb in self.auto_close_options:
            _cb.setMinimumWidth(92)

        self.auto_close_method.setMinimumWidth(150)
        self.auto_close_method.addItems(self.CLOSE_METHODS)
        self.auto_close_method.setMinimumWidth(145)
        self.auto_profit = self._make_short_line()
        self.auto_loss = self._make_short_line()

        
        self.early_close_method = QComboBox()
        # 체크박스 UI와 저장용 숨김 콤보의 항목은 반드시 1회만 등록한다.
        self.early_close_method.setVisible(False)
        self.early_close_signal = QCheckBox("루틴마감")
        self.early_close_market = QCheckBox("시장가")
        self.early_close_current = QCheckBox("현재가")
        self.early_close_profit_loss = QCheckBox("익절/손절")
        self.early_close_signal.setChecked(True)
        self.early_close_options = [
            self.early_close_signal,
            self.early_close_market,
            self.early_close_current,
            self.early_close_profit_loss,
        ]
        for _cb in self.early_close_options:
            _cb.setMinimumWidth(92)

        self.early_close_method.setMinimumWidth(150)
        self.early_close_method.addItems(self.CLOSE_METHODS)
        self.early_close_method.setMinimumWidth(145)
        self.early_profit = self._make_short_line()
        self.early_loss = self._make_short_line()

        self.liquidation_minutes = self._make_short_line("5")
        self.liquidation_checks: dict[str, QCheckBox] = {
            name: QCheckBox(name) for name in self.LIQUIDATION_METHODS
        }
        for checkbox in self.liquidation_checks.values():
            checkbox.clicked.connect(lambda _checked=False, cb=checkbox: self._select_liquidation_method(cb))

        self.liquidation_minutes = QComboBox()
        self.liquidation_minutes.addItems([str(value) for value in range(5, 101, 5)])
        self.liquidation_minutes.setFixedWidth(70)
        self.starting_quantity = self._make_short_line("1")
        self.starting_amount_multiplier = self._make_short_line("1.5")
        self.limit_recommended_multiplier = self._make_short_line("100")
        self.limit_minimum_multiplier = self._make_short_line("25")
        self.limit_digit_alignment_toggle = QPushButton()
        self.limit_digit_alignment_toggle.setObjectName("stockLimitDigitAlignmentToggle")
        self.limit_digit_alignment_toggle.setCheckable(True)
        self.limit_digit_alignment_toggle.setToolTip("한도금액의 상위 두 자릿수를 맞춥니다")
        self.limit_digit_alignment_toggle.setStyleSheet(
            """
            QPushButton#stockLimitDigitAlignmentToggle {
                background-color: transparent;
                color: #9a9a9a;
                border: 1px solid #b8b8b8;
                border-radius: 2px;
                padding: 4px 4px;
            }
            QPushButton#stockLimitDigitAlignmentToggle:hover,
            QPushButton#stockLimitDigitAlignmentToggle:pressed {
                background-color: transparent;
            }
            QPushButton#stockLimitDigitAlignmentToggle:checked,
            QPushButton#stockLimitDigitAlignmentToggle:checked:hover,
            QPushButton#stockLimitDigitAlignmentToggle:checked:pressed {
                background-color: transparent;
                color: #000000;
                border-color: #000000;
            }
            """
        )
        self.limit_digit_alignment_toggle.setText("자리맞춤 OFF")
        self.limit_digit_alignment_toggle.ensurePolished()
        self.limit_digit_alignment_toggle.setFixedWidth(
            self.limit_digit_alignment_toggle.sizeHint().width()
        )
        self.limit_digit_alignment_toggle.toggled.connect(
            self._stock_limit_digit_alignment_toggled
        )
        self.registration_waiting = QCheckBox("대기")
        self.registration_excluded = QCheckBox("제외")
        self.registration_waiting.setObjectName("stockRegistrationWaitingCheck")
        self.registration_excluded.setObjectName("stockRegistrationExcludedCheck")
        self.registration_waiting.setChecked(True)
        self.registration_waiting.clicked.connect(
            lambda _checked=False: self._select_registration_location("WAITING")
        )
        self.registration_excluded.clicked.connect(
            lambda _checked=False: self._select_registration_location("EXCLUDED")
        )
        self._setup_ui()
        self._connect_close_option_checks()
        self.manual_liquidation.clicked.connect(lambda _checked=False: self._update_manual_liquidation_mode())
        self.load_policy_to_widgets()

    def _make_short_line(self, default: str = "") -> QLineEdit:
        line = QLineEdit(default)
        line.setMinimumWidth(70)
        return line

    def _update_stock_limit_digit_alignment_toggle_text(self) -> None:
        state = "ON" if self.limit_digit_alignment_toggle.isChecked() else "OFF"
        self.limit_digit_alignment_toggle.setText(f"자리맞춤 {state}")

    def _stock_limit_digit_alignment_toggled(self, checked: bool) -> None:
        self._update_stock_limit_digit_alignment_toggle_text()
        set_stock_limit_digit_alignment_enabled(checked)


    def _make_time_edit(
        self,
        default_time: str,
        *,
        allow_empty: bool = False,
    ) -> TimeComboWidget:
        return TimeComboWidget(default_time, allow_empty=allow_empty)

    def _set_time_edit(self, edit: TimeComboWidget, value: object, default_time: str) -> None:
        edit.set_time(value, default_time)

    def _time_edit_text(self, edit: TimeComboWidget) -> str:
        return edit.time_text()

    def _select_liquidation_method(self, selected: QCheckBox) -> None:
        for checkbox in self.liquidation_checks.values():
            checkbox.setChecked(checkbox is selected)

    def _current_liquidation_method(self) -> str:
        for name, checkbox in self.liquidation_checks.items():
            if checkbox.isChecked():
                return name
        return "시장가"




    def _exclusive_close_check(self, current: QCheckBox, checks: list[QCheckBox]) -> None:
        """체크박스형 표시지만 마감방식은 1개만 선택한다."""
        for cb in checks:
            cb.setChecked(cb is current)
        current.setChecked(True)
        self._update_profit_loss_input_enabled()

    def _update_profit_loss_input_enabled(self) -> None:
        """익절/손절 옵션 선택 여부에 따라 입력칸 활성/비활성을 맞춘다."""
        auto_enabled = (
            hasattr(self, "auto_close_checks")
            and len(self.auto_close_checks) > 3
            and self.auto_close_checks[3].isChecked()
        )
        early_enabled = (
            hasattr(self, "early_close_checks")
            and len(self.early_close_checks) > 3
            and self.early_close_checks[3].isChecked()
        )

        if hasattr(self, "auto_profit"):
            self.auto_profit.setEnabled(auto_enabled)
        if hasattr(self, "auto_loss"):
            self.auto_loss.setEnabled(auto_enabled)
        if hasattr(self, "early_profit"):
            self.early_profit.setEnabled(early_enabled)
        if hasattr(self, "early_loss"):
            self.early_loss.setEnabled(early_enabled)

    def _connect_close_option_checks(self) -> None:
        for checks in [getattr(self, "auto_close_checks", []), getattr(self, "early_close_checks", [])]:
            for cb in checks:
                cb.clicked.connect(
                    lambda checked, current=cb, group=checks: self._exclusive_close_check(current, group)
                )
        self._update_profit_loss_input_enabled()

    def _sync_close_checkboxes_to_combo(self) -> None:
        def sync(checks: list[QCheckBox], combo: QComboBox, default_index: int) -> None:
            if not checks:
                return
            selected = default_index
            for idx, cb in enumerate(checks):
                if cb.isChecked():
                    selected = idx
                    break
            for idx, cb in enumerate(checks):
                cb.setChecked(idx == selected)
            combo.setCurrentIndex(selected)

        if hasattr(self, "auto_close_method") and hasattr(self, "auto_close_checks"):
            sync(self.auto_close_checks, self.auto_close_method, 0)
        if hasattr(self, "early_close_method") and hasattr(self, "early_close_checks"):
            sync(self.early_close_checks, self.early_close_method, 1)
        self._update_profit_loss_input_enabled()

    def _sync_combo_to_close_checkboxes(self) -> None:
        def sync(combo: QComboBox, checks: list[QCheckBox]) -> None:
            if not checks:
                return
            idx = combo.currentIndex()
            if idx < 0 or idx >= len(checks):
                idx = 0
            for i, cb in enumerate(checks):
                cb.setChecked(i == idx)

        if hasattr(self, "auto_close_method") and hasattr(self, "auto_close_checks"):
            sync(self.auto_close_method, self.auto_close_checks)
        if hasattr(self, "early_close_method") and hasattr(self, "early_close_checks"):
            sync(self.early_close_method, self.early_close_checks)
        self._update_profit_loss_input_enabled()


    def update_manual_extra_labels(self) -> None:
        """추가시간 구간명을 수동운영 옵션 표시명에 반영한다."""
        for index, checkbox in enumerate(self.manual_extra_checks):
            name = self.extra_name[index].text().strip() if index < len(self.extra_name) else ""
            checkbox.setText(name or f"추가{index + 1}")
            checkbox.setMinimumWidth(max(82, min(150, len(checkbox.text()) * 12 + 34)))

    def _update_manual_liquidation_mode(self) -> None:
        """청산정책 적용 시 수동운영은 정규장만 허용한다.

        청산정책은 정규장 종료 기준으로 동작하므로 추가시간과 함께 선택되면
        의미가 충돌한다. 따라서 청산정책 적용 ON 상태에서는 정규장만 유지하고
        추가시간 체크박스는 자동 해제 후 비활성화한다.
        """
        liquidation_enabled = self.manual_liquidation.isChecked()

        if liquidation_enabled:
            self.manual_use_regular.setChecked(True)

        self.manual_use_regular.setEnabled(not liquidation_enabled)

        for checkbox in self.manual_extra_checks:
            if liquidation_enabled:
                checkbox.setChecked(False)
            checkbox.setEnabled(not liquidation_enabled)

    def save_extra_sessions_only(self) -> None:
        """추가시간 이름/시간만 저장하고 수동운영 옵션 표시를 즉시 갱신한다."""
        self.update_manual_extra_labels()
        # 추가시간 저장 버튼을 눌러도 화면에 선택된 마감/조기마감 체크 상태가
        # 숨김 콤보 저장값과 어긋나지 않도록 먼저 동기화한다.
        self._sync_close_checkboxes_to_combo()
        policy = self.build_policy_from_widgets()
        try:
            write_operation_policy(policy)
            self.policy = policy
            append_changelog("UPDATE", "operation_policy.json", "추가시간 설정 저장")
        except Exception as exc:
            QMessageBox.critical(self, "저장 오류", f"추가시간 저장 중 오류가 발생했습니다.\n\n{exc}")
            return
        logical_owner = persistent_feature_owner(self)
        toast_parent = logical_owner if logical_owner is not None else self
        show_toast(
            parent=toast_parent,
            message="환경설정을 저장했습니다.",
            duration_ms=2000,
            position="center",
        )

    def _setup_ui(self) -> None:
        layout = QVBoxLayout()
        layout.setSpacing(10)
        layout.setContentsMargins(18, 16, 18, 16)

        # 1. 운영시간 설정
        operation_time_box = QGroupBox("")
        operation_time_layout = QGridLayout()
        operation_time_layout.setContentsMargins(16, 12, 16, 12)
        operation_time_layout.setHorizontalSpacing(8)
        operation_time_layout.setVerticalSpacing(8)

        # 정규장 행: 제목 / 정규장 / 시작 / 종료를 같은 높이로 정렬
        op_title = QLabel("1. 운영시간 설정")
        op_title.setStyleSheet("font-weight: bold;")
        operation_time_layout.addWidget(op_title, 0, 0, Qt.AlignLeft | Qt.AlignVCenter)

        regular_label = QLabel("정규장")
        regular_label.setStyleSheet("font-weight: bold;")
        operation_time_layout.addWidget(regular_label, 0, 1, Qt.AlignCenter)

        operation_time_layout.addWidget(QLabel("시작"), 0, 3, Qt.AlignRight | Qt.AlignVCenter)
        operation_time_layout.addWidget(self.regular_start, 0, 4, 1, 2, Qt.AlignLeft | Qt.AlignVCenter)

        operation_time_layout.addWidget(QLabel("종료"), 0, 7, Qt.AlignRight | Qt.AlignVCenter)
        operation_time_layout.addWidget(self.regular_end, 0, 8, 1, 2, Qt.AlignLeft | Qt.AlignVCenter)

        separator = QFrame()
        separator.setFrameShape(QFrame.HLine)
        separator.setFrameShadow(QFrame.Sunken)
        operation_time_layout.addWidget(separator, 1, 0, 1, 10)

        # ATS 위치는 유지. 시작/종료 열만 정규장 시작/종료 열과 동일하게 사용.
        ats_label = QLabel("ATS")
        ats_label.setStyleSheet("font-weight: bold; font-size: 11pt;")
        ats_label.setAlignment(Qt.AlignCenter)
        operation_time_layout.addWidget(ats_label, 4, 0, Qt.AlignCenter)

        for index in range(3):
            enabled = QCheckBox()
            enabled.setToolTip("ATS설정 팝업에 이 항목을 표시합니다.")
            name = QLineEdit()
            name.setFixedWidth(120)
            start_time = self._make_time_edit("09:00:00", allow_empty=True)
            end_time = self._make_time_edit("15:20:00", allow_empty=True)
            self.extra_enabled.append(enabled)
            self.extra_name.append(name)
            self.extra_start.append(start_time)
            self.extra_end.append(end_time)

            row = index + 3
            name_wrap = QWidget()
            name_layout = QHBoxLayout()
            name_layout.setContentsMargins(0, 0, 0, 0)
            name_layout.setSpacing(6)
            name_layout.addWidget(enabled)
            name_layout.addWidget(name)
            name_wrap.setLayout(name_layout)
            operation_time_layout.addWidget(name_wrap, row, 1, 1, 1, Qt.AlignLeft | Qt.AlignVCenter)

            if index == 1:
                operation_time_layout.addWidget(QLabel("시작"), row, 3, Qt.AlignRight | Qt.AlignVCenter)
                operation_time_layout.addWidget(QLabel("종료"), row, 7, Qt.AlignRight | Qt.AlignVCenter)

            operation_time_layout.addWidget(start_time, row, 4, 1, 2, Qt.AlignLeft | Qt.AlignVCenter)
            operation_time_layout.addWidget(end_time, row, 8, 1, 2, Qt.AlignLeft | Qt.AlignVCenter)

        operation_time_layout.setColumnMinimumWidth(0, 130)
        operation_time_layout.setColumnMinimumWidth(1, 124)
        operation_time_layout.setColumnMinimumWidth(2, 50)
        operation_time_layout.setColumnMinimumWidth(3, 54)
        operation_time_layout.setColumnMinimumWidth(4, 92)
        operation_time_layout.setColumnMinimumWidth(5, 52)
        operation_time_layout.setColumnMinimumWidth(6, 70)
        operation_time_layout.setColumnMinimumWidth(7, 54)
        operation_time_layout.setColumnMinimumWidth(8, 92)
        operation_time_layout.setColumnMinimumWidth(9, 52)
        operation_time_layout.setColumnStretch(10, 1)

        operation_time_box.setLayout(operation_time_layout)
        layout.addWidget(operation_time_box)

        # 2. 시간운영 기본설정
        scheduled_box = QGroupBox("")
        scheduled_layout = QHBoxLayout()
        scheduled_layout.setContentsMargins(12, 8, 12, 8)
        scheduled_layout.setSpacing(18)
        scheduled_title = QLabel("2. 시간운영 기본설정")
        scheduled_title.setStyleSheet("font-weight: bold;")
        scheduled_title.setMinimumWidth(205)
        scheduled_layout.addWidget(scheduled_title)
        scheduled_layout.addWidget(QLabel("시작"))
        scheduled_layout.addWidget(self.scheduled_start)
        scheduled_layout.addSpacing(22)
        scheduled_layout.addWidget(QLabel("매수종료"))
        scheduled_layout.addWidget(self.scheduled_end_buy)
        scheduled_layout.addStretch(1)
        scheduled_box.setLayout(scheduled_layout)
        layout.addWidget(scheduled_box)

        # 3~6. 옵션 열 정렬 영역
        # 기준:
        # - 1, 2번 영역은 수정하지 않는다.
        # - 체크박스 사각형의 x축을 기준으로 정렬한다.
        # - 3번 청산정책 적용과 4/5번 이월은 같은 후방 열.
        # - 6번 시장가 = 5번 현재가 열, 6번 현재가 = 5번 익절/손절 열.
        # - 6번 이월은 6번 시장가↔현재가 간격만큼 오른쪽.
        title_width = 205
        option_col_width = 128
        late_col_width = 78

        def make_row_box(title_text: str) -> tuple[QGroupBox, QGridLayout]:
            box = QGroupBox("")
            row_layout = QGridLayout()
            row_layout.setContentsMargins(12, 8, 12, 8)
            row_layout.setHorizontalSpacing(0)
            row_layout.setVerticalSpacing(0)

            title = QLabel(title_text)
            title.setStyleSheet("font-weight: bold;")
            title.setMinimumWidth(title_width)
            row_layout.addWidget(title, 0, 0, Qt.AlignLeft | Qt.AlignVCenter)

            # 1~4번 기본 옵션열은 동일 간격.
            for col in range(1, 5):
                row_layout.setColumnMinimumWidth(col, option_col_width)
                row_layout.setColumnStretch(col, 0)

            # 5번은 익절/손절 입력칸 소속 영역.
            row_layout.setColumnMinimumWidth(5, 178)
            row_layout.setColumnStretch(5, 0)

            # 6번은 후방 체크박스열. 기존보다 약 40% 정도 뒤쪽으로 밀린 위치.
            row_layout.setColumnMinimumWidth(6, late_col_width)
            row_layout.setColumnStretch(7, 1)

            box.setLayout(row_layout)
            return box, row_layout

        # 3. 수동운영 기본설정
        manual_box, manual_layout = make_row_box("3. 수동운영 기본설정")
        self.manual_use_regular.setText("정규장")
        manual_layout.addWidget(self.manual_use_regular, 0, 1, Qt.AlignLeft | Qt.AlignVCenter)

        self.manual_liquidation.setText("청산정책 적용")
        self.manual_liquidation.setMinimumWidth(
            max(130, self.manual_liquidation.sizeHint().width())
        )

        separator_label = QLabel("|")
        separator_label.setFixedWidth(18)
        manual_layout.addWidget(separator_label, 0, 5, Qt.AlignRight | Qt.AlignVCenter)
        manual_layout.addWidget(
            self.manual_liquidation,
            0,
            6,
            1,
            2,
            Qt.AlignLeft | Qt.AlignVCenter,
        )
        layout.addWidget(manual_box)

        # 4. 자동마감 설정
        auto_box, auto_layout = make_row_box("4. 자동마감 설정")
        self.auto_close_checks = [
            QCheckBox("루틴마감"),
            QCheckBox("시장가"),
            QCheckBox("현재가"),
            QCheckBox("익절/손절"),
            QCheckBox("이월"),
        ]
        self.auto_close_checks[0].setChecked(True)

        auto_layout.addWidget(self.auto_close_checks[0], 0, 1, Qt.AlignLeft | Qt.AlignVCenter)
        auto_layout.addWidget(self.auto_close_checks[1], 0, 2, Qt.AlignLeft | Qt.AlignVCenter)
        auto_layout.addWidget(self.auto_close_checks[2], 0, 3, Qt.AlignLeft | Qt.AlignVCenter)

        profit_auto_wrap = QWidget()
        profit_auto_layout = QHBoxLayout()
        profit_auto_layout.setContentsMargins(0, 0, 0, 0)
        profit_auto_layout.setSpacing(4)
        profit_auto_layout.addWidget(self.auto_close_checks[3])
        profit_auto_layout.addWidget(QLabel("+"))
        self.auto_profit.setFixedWidth(54)
        self.auto_profit.setPlaceholderText("입력")
        profit_auto_layout.addWidget(self.auto_profit)
        profit_auto_layout.addWidget(QLabel("/ -"))
        self.auto_loss.setFixedWidth(54)
        self.auto_loss.setPlaceholderText("입력")
        profit_auto_layout.addWidget(self.auto_loss)
        profit_auto_wrap.setLayout(profit_auto_layout)
        auto_layout.addWidget(profit_auto_wrap, 0, 4, 1, 2, Qt.AlignLeft | Qt.AlignVCenter)

        auto_layout.addWidget(self.auto_close_checks[4], 0, 6, Qt.AlignLeft | Qt.AlignVCenter)
        layout.addWidget(auto_box)

        # 5. 조기마감 설정
        early_box, early_layout = make_row_box("5. 조기마감 설정")
        self.early_close_checks = [
            QCheckBox("루틴마감"),
            QCheckBox("시장가"),
            QCheckBox("현재가"),
            QCheckBox("익절/손절"),
            QCheckBox("이월"),
        ]
        self.early_close_checks[1].setChecked(True)

        early_layout.addWidget(self.early_close_checks[0], 0, 1, Qt.AlignLeft | Qt.AlignVCenter)
        early_layout.addWidget(self.early_close_checks[1], 0, 2, Qt.AlignLeft | Qt.AlignVCenter)
        early_layout.addWidget(self.early_close_checks[2], 0, 3, Qt.AlignLeft | Qt.AlignVCenter)

        profit_early_wrap = QWidget()
        profit_early_layout = QHBoxLayout()
        profit_early_layout.setContentsMargins(0, 0, 0, 0)
        profit_early_layout.setSpacing(4)
        profit_early_layout.addWidget(self.early_close_checks[3])
        profit_early_layout.addWidget(QLabel("+"))
        self.early_profit.setFixedWidth(54)
        self.early_profit.setPlaceholderText("입력")
        profit_early_layout.addWidget(self.early_profit)
        profit_early_layout.addWidget(QLabel("/ -"))
        self.early_loss.setFixedWidth(54)
        self.early_loss.setPlaceholderText("입력")
        profit_early_layout.addWidget(self.early_loss)
        profit_early_wrap.setLayout(profit_early_layout)
        early_layout.addWidget(profit_early_wrap, 0, 4, 1, 2, Qt.AlignLeft | Qt.AlignVCenter)

        early_layout.addWidget(self.early_close_checks[4], 0, 6, Qt.AlignLeft | Qt.AlignVCenter)
        layout.addWidget(early_box)

        # 6. 청산설정
        liquidation_box, liquidation_layout = make_row_box("6. 청산설정")

        liquidation_start_wrap = QWidget()
        liquidation_start_layout = QHBoxLayout()
        liquidation_start_layout.setContentsMargins(0, 0, 0, 0)
        liquidation_start_layout.setSpacing(8)
        liquidation_start_layout.addWidget(QLabel("■ 정규장 종료"))
        self.liquidation_minutes.setFixedWidth(64)
        liquidation_start_layout.addWidget(self.liquidation_minutes)
        liquidation_start_layout.addWidget(QLabel("분전"))
        liquidation_start_wrap.setLayout(liquidation_start_layout)
        liquidation_layout.addWidget(liquidation_start_wrap, 0, 1, 1, 2, Qt.AlignLeft | Qt.AlignVCenter)

        # 확정 기준:
        # 5번 현재가 체크박스 열 == 6번 시장가 체크박스 열
        # 5번 익절/손절 체크박스 열 == 6번 현재가 체크박스 열
        # 6번 이월은 시장가↔현재가와 동일한 한 칸 거리만큼 오른쪽.
        if "시장가" in self.liquidation_checks:
            liquidation_layout.addWidget(self.liquidation_checks["시장가"], 0, 3, Qt.AlignLeft | Qt.AlignVCenter)
        if "현재가" in self.liquidation_checks:
            liquidation_layout.addWidget(self.liquidation_checks["현재가"], 0, 4, Qt.AlignLeft | Qt.AlignVCenter)
        if "이월" in self.liquidation_checks:
            liquidation_layout.addWidget(self.liquidation_checks["이월"], 0, 5, Qt.AlignLeft | Qt.AlignVCenter)

        layout.addWidget(liquidation_box)

        # 7. 시작 예산 설정
        budget_box, budget_layout = make_row_box("7. 시작 예산 설정")
        budget_content_wrap = QWidget()
        budget_content_layout = QHBoxLayout()
        budget_content_layout.setContentsMargins(0, 0, 0, 0)
        budget_content_layout.setSpacing(10)

        quantity_wrap = QWidget()
        quantity_layout = QHBoxLayout()
        quantity_layout.setContentsMargins(0, 0, 0, 0)
        quantity_layout.setSpacing(6)
        quantity_layout.addWidget(QLabel("■ 주수 :"))
        self.starting_quantity.setFixedWidth(48)
        quantity_layout.addWidget(self.starting_quantity)
        quantity_wrap.setLayout(quantity_layout)
        budget_content_layout.addWidget(quantity_wrap)

        amount_wrap = QWidget()
        amount_layout = QHBoxLayout()
        amount_layout.setContentsMargins(0, 0, 0, 0)
        amount_layout.setSpacing(6)
        amount_layout.addWidget(QLabel("■ 금액 : 현재가 ×"))
        self.starting_amount_multiplier.setFixedWidth(48)
        amount_layout.addWidget(self.starting_amount_multiplier)
        amount_wrap.setLayout(amount_layout)
        budget_content_layout.addWidget(amount_wrap)

        limit_wrap = QWidget()
        limit_layout = QHBoxLayout()
        limit_layout.setContentsMargins(0, 0, 0, 0)
        limit_layout.setSpacing(6)
        limit_layout.addWidget(QLabel("■ 한도금액 : 시작예산 × 권장"))
        self.limit_recommended_multiplier.setFixedWidth(48)
        limit_layout.addWidget(self.limit_recommended_multiplier)
        limit_layout.addWidget(QLabel("| 최소"))
        self.limit_minimum_multiplier.setFixedWidth(48)
        limit_layout.addWidget(self.limit_minimum_multiplier)
        limit_layout.addSpacing(8)
        self._update_stock_limit_digit_alignment_toggle_text()
        limit_layout.addWidget(self.limit_digit_alignment_toggle)
        limit_wrap.setLayout(limit_layout)
        budget_content_layout.addWidget(limit_wrap)
        budget_content_layout.addStretch(1)
        budget_content_wrap.setLayout(budget_content_layout)
        budget_layout.addWidget(
            budget_content_wrap,
            0,
            1,
            1,
            7,
            Qt.AlignLeft | Qt.AlignVCenter,
        )
        layout.addWidget(budget_box)

        # 8. 종목등록 설정
        registration_box, registration_layout = make_row_box("8. 종목등록 설정")
        registration_layout.addWidget(QLabel("등록위치 :"), 0, 1, Qt.AlignLeft | Qt.AlignVCenter)
        registration_layout.addWidget(self.registration_waiting, 0, 2, Qt.AlignLeft | Qt.AlignVCenter)
        registration_layout.addWidget(self.registration_excluded, 0, 3, Qt.AlignLeft | Qt.AlignVCenter)
        layout.addWidget(registration_box)

        self.program_factory_reset_button = QPushButton("프로그램 초기화")
        self.program_factory_reset_button.setObjectName("operationEnvironmentProgramResetButton")
        self.program_factory_reset_button.clicked.connect(self._request_program_factory_reset)
        self.settings_reset_button = QPushButton("설정 초기화")
        self.settings_reset_button.setObjectName("operationEnvironmentSettingsResetButton")
        self.settings_reset_button.clicked.connect(self._load_official_settings_defaults)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("저장")
        buttons.button(QDialogButtonBox.Cancel).setText("취소")
        buttons.button(QDialogButtonBox.Save).setMinimumWidth(110)
        buttons.button(QDialogButtonBox.Cancel).setMinimumWidth(110)
        bottom_button_style = (
            "QPushButton {"
            " border: 1px solid #A6A6A6;"
            " border-radius: 2px;"
            " background-color: #F5F5F5;"
            " color: #202020;"
            " padding: 0 10px;"
            "}"
            "QPushButton:hover { background-color: #EAF2FA; border-color: #7A9FC2; }"
            "QPushButton:pressed { background-color: #DCE8F3; border-color: #5F87AD; }"
            "QPushButton:focus { border-color: #0078D4; }"
            "QPushButton:disabled { background-color: #F0F0F0; color: #9A9A9A; border-color: #C8C8C8; }"
        )
        for button in (
            self.program_factory_reset_button,
            self.settings_reset_button,
            buttons.button(QDialogButtonBox.Save),
            buttons.button(QDialogButtonBox.Cancel),
        ):
            button.setFixedHeight(32)
            button.setStyleSheet(bottom_button_style)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.settings_button_box = buttons

        bottom_buttons = QHBoxLayout()
        bottom_buttons.addWidget(self.program_factory_reset_button)
        bottom_buttons.addWidget(self.settings_reset_button)
        bottom_buttons.addStretch(1)
        bottom_buttons.addWidget(buttons)
        layout.addLayout(bottom_buttons)
        self.setLayout(layout)

    def _load_official_settings_defaults(self) -> None:
        self.policy = default_operation_policy()
        set_stock_limit_digit_alignment_enabled(True)
        self.load_policy_to_widgets()

    def _select_registration_location(self, location: str) -> None:
        excluded = str(location or "").strip().upper() == "EXCLUDED"
        self.registration_waiting.setChecked(not excluded)
        self.registration_excluded.setChecked(excluded)

    def _current_registration_location(self) -> str:
        return "EXCLUDED" if self.registration_excluded.isChecked() else "WAITING"

    def _main_window_kiwoom_api(self) -> object | None:
        current: QWidget | None = persistent_feature_owner(self)
        while current is not None:
            api = getattr(current, "kiwoom_api", None)
            if api is not None:
                return api
            current = persistent_feature_owner(current)
        return None

    def _main_window_owner(self) -> QWidget | None:
        current: QWidget | None = persistent_feature_owner(self)
        last: QWidget | None = current
        while current is not None:
            last = current
            current = persistent_feature_owner(current)
        return last

    def _quiesce_for_program_factory_reset(self) -> dict[str, object]:
        owner = self._main_window_owner()
        token: dict[str, object] = {"owner": owner, "timers": []}
        if owner is None:
            return token
        setattr(owner, "_factory_reset_in_progress", True)
        timers: list[tuple[QTimer, bool]] = []
        timer_owners: list[QWidget] = [owner]
        application = QApplication.instance()
        if application is not None:
            for window in application.topLevelWidgets():
                current: QWidget | None = window
                while current is not None and current is not owner:
                    current = persistent_feature_owner(current)
                if current is owner and window not in timer_owners:
                    timer_owners.append(window)
        all_timers = {
            timer
            for timer_owner in timer_owners
            for timer in timer_owner.findChildren(QTimer)
        }
        for timer in all_timers:
            active = timer.isActive()
            timers.append((timer, active))
            if active:
                timer.stop()
        token["timers"] = timers
        host = getattr(owner, "_main_monitoring_auto_trade_operation_host", None)
        if host is not None:
            setattr(host, "_factory_reset_quiesced", True)
            trigger_queue = getattr(host, "_bar_commit_trigger_queue", None)
            clear = getattr(trigger_queue, "clear", None)
            if callable(clear):
                clear()
            market_data_host = getattr(host, "_market_data_host", None)
            clear_market_data = getattr(market_data_host, "clear", None)
            if callable(clear_market_data):
                clear_market_data()
        return token

    @staticmethod
    def _resume_after_program_factory_reset_failure(token: object) -> None:
        state = token if isinstance(token, dict) else {}
        owner = state.get("owner")
        if owner is not None:
            setattr(owner, "_factory_reset_in_progress", False)
            host = getattr(owner, "_main_monitoring_auto_trade_operation_host", None)
            if host is not None:
                setattr(host, "_factory_reset_quiesced", False)
        for timer, was_active in state.get("timers", []):
            if was_active and not timer.isActive():
                timer.start()

    def _request_program_factory_reset(self) -> None:
        from program_factory_reset import (
            execute_program_factory_reset,
            validate_factory_reset_safety,
        )

        preview = validate_factory_reset_safety(PROJECT_ROOT, broker_connected=False)
        self._last_factory_reset_preview = preview
        if not preview.get("success"):
            issues = [str(item) for item in preview.get("issues", []) if str(item).strip()]
            QMessageBox.critical(
                self,
                "프로그램 초기화 불가",
                "프로그램 초기화 대상을 확인하지 못했습니다.\n\n"
                + "\n".join(f"- {item}" for item in issues[:8]),
            )
            return

        confirmation = ProgramFactoryResetConfirmDialog(self)
        dialog_result = confirmation.exec_()
        accepted = dialog_result == QDialog.Accepted
        confirmation_matched = (
            confirmation.confirmation_input.text() == confirmation.CONFIRMATION_TEXT
        )
        if not accepted or not confirmation_matched:
            return

        append_production_event(
            "OPERATOR_SETTING_DECISION",
            result="ACCEPTED",
            source="gui_operation_environment.OperationEnvironmentSettingsDialog._request_program_factory_reset",
            target_type="APPLICATION_SETTINGS",
            target_name="프로그램 전체 초기화",
            details={
                "interaction_type": "INPUT",
                "prompt_key": "PROGRAM_FACTORY_RESET",
                "prompt_title": "프로그램 초기화 확인",
                "prompt_summary": "프로그램 종목·운영 데이터·사용자 설정 초기화",
                "offered_options": ["초기화", "취소"],
                "selected_option": "초기화",
                "confirmation_matched": True,
            },
        )

        result = execute_program_factory_reset(
            PROJECT_ROOT,
            broker_connected=False,
            quiesce=self._quiesce_for_program_factory_reset,
            resume_after_failure=self._resume_after_program_factory_reset_failure,
        )
        if not result.get("success"):
            issues = [str(item) for item in result.get("issues", []) if str(item).strip()]
            detail = "\n".join(f"- {item}" for item in issues[:8])
            QMessageBox.critical(
                self,
                "프로그램 초기화 실패",
                "프로그램 초기화를 완료하지 못했습니다.\n\n"
                + (detail or "초기화 대상을 확인해 주세요."),
            )
            return

        QMessageBox.information(
            self,
            "프로그램 초기화 완료",
            "프로그램 초기화가 완료되었습니다.\n"
            "프로그램을 종료합니다. 다시 실행해 주세요.",
        )
        application = QApplication.instance()
        if application is not None:
            application.quit()

    def load_policy_to_widgets(self) -> None:
        regular = self.policy.get("regular_market", {}) if isinstance(self.policy.get("regular_market"), dict) else {}
        self._set_time_edit(self.regular_start, regular.get("start_time", "09:00:00"), "09:00:00")
        self._set_time_edit(self.regular_end, regular.get("end_time", "15:20:00"), "15:20:00")

        extra_sessions = self.policy.get("extra_sessions", [])
        if not isinstance(extra_sessions, list):
            extra_sessions = []
        for index in range(3):
            item = extra_sessions[index] if index < len(extra_sessions) and isinstance(extra_sessions[index], dict) else {}
            self.extra_enabled[index].setChecked(bool(item.get("enabled", True)))
            self.extra_name[index].setText(str(item.get("name", f"추가시간{index + 1}")))
            self._set_time_edit(self.extra_start[index], item.get("start_time", "09:00:00"), "09:00:00")
            self._set_time_edit(self.extra_end[index], item.get("end_time", "15:20:00"), "15:20:00")

        scheduled = self.policy.get("scheduled_operation", {}) if isinstance(self.policy.get("scheduled_operation"), dict) else {}
        self._set_time_edit(self.scheduled_start, scheduled.get("default_start_time", "09:00:00"), "09:00:00")
        self._set_time_edit(self.scheduled_end_buy, scheduled.get("default_end_buy_time", "13:30:00"), "13:30:00")

        manual = self.policy.get("manual_operation", {}) if isinstance(self.policy.get("manual_operation"), dict) else {}
        self.manual_use_regular.setChecked(bool(manual.get("use_regular_market", True)))
        for checkbox in self.manual_extra_checks:
            checkbox.setChecked(False)
        self.manual_liquidation.setChecked(bool(manual.get("use_liquidation_policy", False)))

        auto = self.policy.get("auto_close", {}) if isinstance(self.policy.get("auto_close"), dict) else {}
        self.auto_close_method.setCurrentText(str(auto.get("method", "루틴매도신호")))
        self.auto_profit.setText(str(auto.get("profit_percent", "")))
        self.auto_loss.setText(str(auto.get("loss_percent", "")))

        early = self.policy.get("early_close", {}) if isinstance(self.policy.get("early_close"), dict) else {}
        self.early_close_method.setCurrentText(str(early.get("method", "루틴매도신호")))
        self.early_profit.setText(str(early.get("profit_percent", "")))
        self.early_loss.setText(str(early.get("loss_percent", "")))

        liquidation = self.policy.get("liquidation", {}) if isinstance(self.policy.get("liquidation"), dict) else {}
        liq_minutes = str(liquidation.get("minutes_before_regular_close", "5")).strip() or "5"
        if liq_minutes not in [str(value) for value in range(5, 101, 5)]:
            liq_minutes = "5"
        self.liquidation_minutes.setCurrentText(liq_minutes)
        method = str(liquidation.get("method", "시장가"))
        if method not in self.liquidation_checks:
            method = "시장가"
        self._select_liquidation_method(self.liquidation_checks[method])

        budget_defaults = starting_budget_defaults(self.policy)
        self.starting_quantity.setText(str(budget_defaults["quantity"]))
        self.starting_amount_multiplier.setText(_plain_number_text(budget_defaults["amount_multiplier"]))
        self.limit_recommended_multiplier.setText(
            _plain_number_text(budget_defaults["limit_recommended_multiplier"])
        )
        self.limit_minimum_multiplier.setText(
            _plain_number_text(budget_defaults["limit_minimum_multiplier"])
        )
        self.limit_digit_alignment_toggle.blockSignals(True)
        self.limit_digit_alignment_toggle.setChecked(
            stock_limit_digit_alignment_enabled()
        )
        self.limit_digit_alignment_toggle.blockSignals(False)
        self._update_stock_limit_digit_alignment_toggle_text()

        registration = stock_registration_policy(self.policy)
        self._select_registration_location(registration["default_location"])

        self._sync_combo_to_close_checkboxes()
        self.update_manual_extra_labels()
        self._update_manual_liquidation_mode()

    def _validate_profit_loss_inputs(self) -> bool:
        """익절/손절 체크 시 최소 한쪽 값 입력을 강제한다."""
        checks = [
            ("자동마감", getattr(self, "auto_close_checks", []), self.auto_profit, self.auto_loss),
            ("조기마감", getattr(self, "early_close_checks", []), self.early_profit, self.early_loss),
        ]
        for title, close_checks, profit_edit, loss_edit in checks:
            if len(close_checks) <= 3 or not close_checks[3].isChecked():
                continue
            profit_value = profit_edit.text().strip()
            loss_value = loss_edit.text().strip()
            if profit_value or loss_value:
                continue
            QMessageBox.warning(
                self,
                "입력 필요",
                f"{title} 설정에서 익절/손절을 선택했습니다.\n\n+ 입력 또는 - 입력 중 최소 1개 값을 입력하세요.",
            )
            profit_edit.setFocus()
            return False
        return True

    def _validated_starting_budget_defaults(self) -> dict[str, float | int] | None:
        fields = (
            ("주수", self.starting_quantity, True),
            ("금액 배수", self.starting_amount_multiplier, False),
            ("한도금액 권장 배수", self.limit_recommended_multiplier, False),
            ("한도금액 최소 배수", self.limit_minimum_multiplier, False),
        )
        parsed: dict[str, float | int] = {}
        keys = tuple(STARTING_BUDGET_DEFAULTS)
        for key, (label, editor, integer_only) in zip(keys, fields):
            text = editor.text().replace(",", "").strip()
            try:
                value = Decimal(text)
            except InvalidOperation:
                value = Decimal(0)
            valid = value.is_finite() and value > 0
            if integer_only:
                valid = valid and value == value.to_integral_value()
            if not valid:
                QMessageBox.warning(self, "입력 확인", f"{label}은(는) 0보다 큰 {'정수' if integer_only else '숫자'}로 입력하세요.")
                editor.setFocus()
                return None
            parsed[key] = int(value) if integer_only else float(value)
        if parsed["limit_minimum_multiplier"] > parsed["limit_recommended_multiplier"]:
            QMessageBox.warning(
                self,
                "입력 확인",
                "한도금액 최소 배수는 권장 배수보다 클 수 없습니다.",
            )
            self.limit_minimum_multiplier.setFocus()
            return None
        return parsed

    def _starting_budget_values_or_existing(self) -> dict[str, float | int]:
        try:
            return {
                "quantity": int(Decimal(self.starting_quantity.text().strip())),
                "amount_multiplier": float(Decimal(self.starting_amount_multiplier.text().strip())),
                "limit_recommended_multiplier": float(
                    Decimal(self.limit_recommended_multiplier.text().strip())
                ),
                "limit_minimum_multiplier": float(
                    Decimal(self.limit_minimum_multiplier.text().strip())
                ),
            }
        except (InvalidOperation, ValueError):
            return starting_budget_defaults(self.policy)


    def build_policy_from_widgets(
        self,
        budget_defaults: dict[str, float | int] | None = None,
    ) -> dict[str, object]:
        # 저장 직전에도 체크박스 선택값을 저장용 콤보값에 맞춘다.
        # accept() 외 경로에서 호출되어도 저장값이 흔들리지 않게 하기 위함이다.
        self._sync_close_checkboxes_to_combo()
        self._update_manual_liquidation_mode()
        return {
            "regular_market": {
                "start_time": self._time_edit_text(self.regular_start),
                "end_time": self._time_edit_text(self.regular_end),
            },
            "extra_sessions": [
                {
                    "enabled": self.extra_enabled[index].isChecked(),
                    "name": self.extra_name[index].text().strip() or f"추가시간{index + 1}",
                    "start_time": self._time_edit_text(self.extra_start[index]),
                    "end_time": self._time_edit_text(self.extra_end[index]),
                }
                for index in range(3)
            ],
            "scheduled_operation": {
                "default_start_time": self._time_edit_text(self.scheduled_start),
                "default_end_buy_time": self._time_edit_text(self.scheduled_end_buy),
            },
            "manual_operation": {
                "use_regular_market": self.manual_use_regular.isChecked(),
                "use_extra_session_1": False,
                "use_extra_session_2": False,
                "use_extra_session_3": False,
                "enabled_status": "매수/매도",
                "disabled_status": "감시/대기",
                "use_liquidation_policy": self.manual_liquidation.isChecked(),
            },
            "auto_close": {
                "method": self.auto_close_method.currentText(),
                "profit_percent": self.auto_profit.text().strip(),
                "loss_percent": self.auto_loss.text().strip(),
            },
            "early_close": {
                "method": self.early_close_method.currentText(),
                "profit_percent": self.early_profit.text().strip(),
                "loss_percent": self.early_loss.text().strip(),
            },
            "liquidation": {
                "minutes_before_regular_close": self.liquidation_minutes.currentText(),
                "method": self._current_liquidation_method(),
            },
            "review_policy": review_policy(self.policy),
            "stock_registration": {
                "default_location": self._current_registration_location(),
            },
            "system_budget": system_budget_policy(self.policy),
            "starting_budget_defaults": (
                dict(budget_defaults)
                if budget_defaults is not None
                else self._starting_budget_values_or_existing()
            ),
        }




    def accept(self) -> None:
        self._sync_close_checkboxes_to_combo()
        if not self._validate_profit_loss_inputs():
            return
        budget_defaults = self._validated_starting_budget_defaults()
        if budget_defaults is None:
            return
        policy = self.build_policy_from_widgets(budget_defaults)
        before_policy = read_operation_policy()
        self._starting_budget_defaults_changed = (
            starting_budget_defaults(before_policy) != budget_defaults
        )
        try:
            write_operation_policy(policy)
            saved_policy = read_operation_policy()
            expected_policy = dict(policy)
            expected_policy["system_budget"] = system_budget_policy(policy)
            if (
                "buffer_response" not in expected_policy
                and "buffer_response" in before_policy
            ):
                expected_policy["buffer_response"] = before_policy["buffer_response"]
            expected_scheduled = expected_policy.get("scheduled_operation")
            if isinstance(expected_scheduled, dict):
                expected_policy["scheduled_operation"] = {
                    key: value
                    for key, value in expected_scheduled.items()
                    if key != "after_buy_end_status"
                }
            expected_policy.pop("updated_at", None)
            comparable_saved = dict(saved_policy)
            comparable_saved.pop("updated_at", None)
            if comparable_saved != expected_policy:
                raise RuntimeError("operation policy read-back verification failed")
            append_changelog("UPDATE", "operation_policy.json", "환경설정 저장")
        except Exception as exc:
            QMessageBox.critical(self, "저장 오류", f"환경설정 저장 중 오류가 발생했습니다.\n\n{exc}")
            return
        trading_keys = ("regular_market", "extra_sessions", "scheduled_operation")
        trading_before = {
            key: before_policy.get(key) for key in trading_keys
        }
        trading_after = {
            key: saved_policy.get(key) for key in trading_keys
        }
        _append_setting_change(
            "TRADING_TIME_CHANGED",
            source="OPERATION_ENVIRONMENT_DIALOG",
            target="운영시간",
            target_id="OPERATION_TIME_POLICY",
            changes=_journal_changes(trading_before, trading_after),
        )
        excluded_keys = {*trading_keys, "system_budget", "buffer_response", "updated_at"}
        settings_before = {
            key: value for key, value in before_policy.items() if key not in excluded_keys
        }
        settings_after = {
            key: value for key, value in saved_policy.items() if key not in excluded_keys
        }
        _append_setting_change(
            "SETTING_CHANGED",
            source="OPERATION_ENVIRONMENT_DIALOG",
            target="환경설정",
            target_id="OPERATION_POLICY",
            changes=_journal_changes(settings_before, settings_after),
        )
        logical_owner = persistent_feature_owner(self)
        toast_parent = logical_owner if logical_owner is not None else self
        show_toast(
            parent=toast_parent,
            message="환경설정을 저장했습니다.",
            duration_ms=2000,
            position="center",
        )
        super().accept()
