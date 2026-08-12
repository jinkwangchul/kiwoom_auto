# -*- coding: utf-8 -*-
"""
gui_auto_trade_situation.py

자동매매설정/관제 현황 표시등 생성 전용 모듈.

주의:
- 현황색 표시 아이템만 만든다.
- 상태 저장/변경, 청산 실행, 검토관리 이동은 하지 않는다.
"""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import QTableWidgetItem

from gui_auto_trade_display import SORT_ROLE, SortableTableWidgetItem
from gui_auto_trade_integrity import (
    auto_trade_setting_data_inconsistency_reasons,
    auto_trade_setting_server_mismatch_detected,
    is_emergency_stopped_state,
    is_review_required_state,
)
from gui_auto_trade_policy import (
    auto_trade_setting_early_close_progress_text,
)


def create_auto_trade_situation_item(
    state: dict[str, object] | None,
    trade_started: bool,
    display_status: str = "",
) -> QTableWidgetItem:
    """자동매매설정/관제 현황 표시등.

    최종 정책:
    - 빨강: 데이터 신뢰 불가. 자동 검토관리 이동 금지, 무결성 확인 후 운영자 판단.
    - 회색: 매매시작 OFF / 정지 / 비활성. 운영자가 매매시작을 눌러야 하는 대기 상태.
    - 녹색: 매매시작 ON + 운영방식/시간정책에 따라 정상 운영 중.

    중요:
    - 조기마감/자동마감 상태 자체는 주황 사유가 아니다.
    - 보유 또는 미도(매도 미체결)가 있으면 처리 대상이 있으므로 녹색이다.
    """
    item = SortableTableWidgetItem("●")
    dot_font = item.font()
    dot_font.setPointSize(13)
    dot_font.setBold(True)
    item.setFont(dot_font)
    item.setTextAlignment(Qt.AlignCenter)

    # 1. 검토관리는 최상위 보호 상태이므로 다른 현황 조건보다 먼저 빨강으로 표시한다.
    if is_review_required_state(state):
        item.setForeground(QColor("#DC2626"))
        item.setToolTip("현황: 검토관리 - 운영자 확인 필요")
        item.setData(SORT_ROLE, 3)
        return item

    if is_emergency_stopped_state(state):
        item.setForeground(QColor("#DC2626"))
        item.setToolTip("현황: 긴급정지 - 운영자 확인 필요")
        item.setData(SORT_ROLE, 3)
        return item

    mismatch_reasons = auto_trade_setting_data_inconsistency_reasons(state)

    # 2. 데이터 신뢰 불가는 시작 OFF여도 빨강을 유지한다.
    if auto_trade_setting_server_mismatch_detected(state):
        item.setForeground(QColor("#DC2626"))
        if mismatch_reasons:
            item.setToolTip("현황: 내부 데이터 불일치 - " + ", ".join(mismatch_reasons))
        else:
            item.setToolTip("현황: 서버/프로그램 정보 불일치 또는 서버 불안 - 긴급정지 후 무결성 확인 필요")
        item.setData(SORT_ROLE, 3)
        return item

    # 3. 매매시작 OFF는 회색이다.
    # 주황은 리셋/복구에서 복원하지 않으며, 운영자가 매매시작을 누르기 전에는 표시하지 않는다.
    if not trade_started:
        item.setForeground(QColor("#9CA3AF"))
        item.setToolTip("현황: 정지/비활성 - 운영자 매매시작 대기")
        item.setData(SORT_ROLE, 0)
        return item

    # 4. 그 외 매매시작 ON 상태는 운영방식/시간정책에 따른 정상 운영 상태다.
    early_close_progress = auto_trade_setting_early_close_progress_text(state)
    item.setForeground(QColor("#16A34A"))
    item.setToolTip(
        f"조기마감: {early_close_progress}"
        if early_close_progress
        else "현황: 정상 운영 중"
    )
    item.setData(SORT_ROLE, 1)
    return item
