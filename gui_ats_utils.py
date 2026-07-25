# -*- coding: utf-8 -*-
"""
gui_ats_utils.py

수동운영 ATS(시간외) 관련 유틸/설정창 분리 파일.
주의: 1차 구조분리 단계이므로 기존 동작 로직은 변경하지 않는다.
"""

from __future__ import annotations

from datetime import datetime

from PyQt5.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

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



def manual_ats_selected_keys_and_source(
    config: dict[str, object] | None,
    state: dict[str, object] | None = None,
) -> tuple[list[str], str]:
    """Current-day/current-process manual ATS selection for a stock."""
    if not isinstance(config, dict):
        return [], "none"

    if normalize_operation_mode(config.get("operation_mode", "SCHEDULED")) != "CONTINUOUS":
        return [], "none"

    runtime_keys = list(manual_ats_runtime_selected_keys(state))
    return (runtime_keys, "runtime") if runtime_keys else ([], "none")


def manual_ats_source(
    config: dict[str, object] | None,
    state: dict[str, object] | None = None,
) -> str:
    """수동+ATS 표시 출처: runtime/none."""
    _keys, source = manual_ats_selected_keys_and_source(config, state)
    return source


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


def manual_ats_market_day_closed(now_dt: datetime | None = None) -> bool:
    """Return True after the latest configured regular/ATS end time."""
    policy = read_operation_policy()
    if not isinstance(policy, dict):
        return False
    end_seconds: list[int] = []
    regular = policy.get("regular_market", {})
    regular_range = operation_policy_time_range_seconds(
        regular if isinstance(regular, dict) else {},
        default_start="09:00:00",
        default_end="15:20:00",
    )
    if regular_range is not None and regular_range[0] < regular_range[1]:
        end_seconds.append(regular_range[1])
    sessions = policy.get("extra_sessions", [])
    if isinstance(sessions, list):
        for session in sessions:
            if not isinstance(session, dict):
                continue
            session_range = operation_policy_time_range_seconds(
                session,
                default_start="00:00:00",
                default_end="00:00:00",
            )
            if session_range is not None and session_range[0] < session_range[1]:
                end_seconds.append(session_range[1])
    return bool(end_seconds) and current_time_in_seconds(now_dt) >= max(end_seconds)


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
    """현재 시간이 해당 종목의 수동+ATS 선택 시간 안인지 판단한다.

    - 선택 여부는 현재 거래일/프로그램 세션의 Runtime 상태만 본다.
    - extra_sessions는 이름/시작/종료 시간 정의로만 사용한다.
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


class ManualAtsSettingsDialog(QDialog):
    """수동운영 ATS설정 전용 소형 창.

    우클릭 하위메뉴의 체크 표시가 환경/스타일에 따라 직관적으로 보이지 않는 문제를 피하기 위해
    체크박스를 명확히 보여주는 별도 창으로 관리한다.
    """

    def __init__(
        self,
        initial_state: dict[str, bool] | None = None,
        labels: dict[str, str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("ATS설정")
        self.resize(300, 190)
        self.requested_sell_method = ""

        state = initial_state if isinstance(initial_state, dict) else {}
        label_map = labels if isinstance(labels, dict) else manual_ats_session_labels()

        layout = QVBoxLayout()
        guide = QLabel("사용할 ATS 구간을 선택하세요.")
        layout.addWidget(guide)

        self.check_extra1 = QCheckBox(str(label_map.get("extra1", "추가1")))
        self.check_extra2 = QCheckBox(str(label_map.get("extra2", "추가2")))
        self.check_extra3 = QCheckBox(str(label_map.get("extra3", "추가3")))

        self.check_extra1.setChecked(bool(state.get("extra1", False)))
        self.check_extra2.setChecked(bool(state.get("extra2", False)))
        self.check_extra3.setChecked(bool(state.get("extra3", False)))

        layout.addWidget(self.check_extra1)
        layout.addWidget(self.check_extra2)
        layout.addWidget(self.check_extra3)

        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Sunken)
        layout.addWidget(line)

        sell_layout = QHBoxLayout()
        self.btn_market_sell = QPushButton("시장가매도")
        self.btn_current_sell = QPushButton("현재가매도")
        self.btn_market_sell.clicked.connect(lambda: self.accept_with_sell_method("시장가"))
        self.btn_current_sell.clicked.connect(lambda: self.accept_with_sell_method("현재가"))
        sell_layout.addWidget(self.btn_market_sell)
        sell_layout.addWidget(self.btn_current_sell)
        layout.addLayout(sell_layout)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("저장")
        buttons.button(QDialogButtonBox.Cancel).setText("취소")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.check_extra1.stateChanged.connect(self.update_sell_button_state)
        self.check_extra2.stateChanged.connect(self.update_sell_button_state)
        self.check_extra3.stateChanged.connect(self.update_sell_button_state)
        self.setLayout(layout)
        self.update_sell_button_state()

    def values(self) -> dict[str, bool]:
        return {
            "extra1": self.check_extra1.isChecked(),
            "extra2": self.check_extra2.isChecked(),
            "extra3": self.check_extra3.isChecked(),
        }

    def has_any_ats(self) -> bool:
        values = self.values()
        return any(bool(values.get(key, False)) for key in ["extra1", "extra2", "extra3"])

    def update_sell_button_state(self) -> None:
        enabled = self.has_any_ats()
        self.btn_market_sell.setEnabled(enabled)
        self.btn_current_sell.setEnabled(enabled)

    def accept_with_sell_method(self, method: str) -> None:
        if not self.has_any_ats():
            return
        self.requested_sell_method = method
        self.accept()
