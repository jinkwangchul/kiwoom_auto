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

from gui_common_utils import safe_int_value
from gui_auto_trade_display import SORT_ROLE, SortableTableWidgetItem
from gui_auto_trade_integrity import (
    auto_trade_setting_data_inconsistency_reasons,
    auto_trade_setting_server_mismatch_detected,
)
from gui_auto_trade_policy import auto_trade_setting_no_next_step_notice


def create_auto_trade_situation_item(
    state: dict[str, object] | None,
    trade_started: bool,
    display_status: str = "",
) -> QTableWidgetItem:
    """자동매매설정/관제 현황 표시등.

    최종 정책:
    - 빨강: 데이터 신뢰 불가. 자동 검토관리 이동 금지, 안정성검사 후 운영자 판단.
    - 회색: 매매시작 OFF / 정지 / 비활성. 운영자가 매매시작을 눌러야 하는 대기 상태.
    - 녹색: 매매시작 ON + 운영방식/시간정책에 따라 정상 운영 중.
    - 주황: 매매시작 ON + 정상이나 조기/자동마감/청산에서 처리할 다음 대상이 없음.

    중요:
    - 조기마감/자동마감 상태 자체는 주황 사유가 아니다.
    - 보유 또는 미도(매도 미체결)가 있으면 처리 대상이 있으므로 녹색이다.
    - 주황은 operation_notice 계열의 '대상 없음' 사유가 있고,
      실제 처리 대상도 없을 때만 표시한다.
    """
    item = SortableTableWidgetItem("●")
    dot_font = item.font()
    dot_font.setPointSize(13)
    dot_font.setBold(True)
    item.setFont(dot_font)
    item.setTextAlignment(Qt.AlignCenter)

    mismatch_reasons = auto_trade_setting_data_inconsistency_reasons(state)

    # 1. 데이터 신뢰 불가는 최우선이다. 시작 OFF여도 빨강을 유지한다.
    if auto_trade_setting_server_mismatch_detected(state):
        item.setForeground(QColor("#DC2626"))
        if mismatch_reasons:
            item.setToolTip("현황: 내부 데이터 불일치 - " + ", ".join(mismatch_reasons))
        else:
            item.setToolTip("현황: 서버/프로그램 정보 불일치 또는 서버 불안 - 운영정지 후 안정성검사 필요")
        item.setData(SORT_ROLE, 3)
        return item

    # 2. 매매시작 OFF는 회색이다.
    # 주황은 리셋/복구에서 복원하지 않으며, 운영자가 매매시작을 누르기 전에는 표시하지 않는다.
    if not trade_started:
        item.setForeground(QColor("#9CA3AF"))
        item.setToolTip("현황: 정지/비활성 - 운영자 매매시작 대기")
        item.setData(SORT_ROLE, 0)
        return item

    # 3. 주황은 '다음 절차 진행 대상 없음'일 때만 표시한다.
    # 조기마감/자동마감/청산 상태 자체가 주황 사유는 아니다.
    holding_qty = safe_int_value(state.get("holding_qty") if isinstance(state, dict) else 0, 0)
    sell_pending_qty = safe_int_value(state.get("sell_pending_qty") if isinstance(state, dict) else 0, 0)
    pending_sell_qty = safe_int_value(state.get("pending_sell_qty") if isinstance(state, dict) else 0, 0)
    sell_order_qty = safe_int_value(state.get("sell_order_qty") if isinstance(state, dict) else 0, 0)
    has_next_target = any(qty > 0 for qty in (holding_qty, sell_pending_qty, pending_sell_qty, sell_order_qty))

    if auto_trade_setting_no_next_step_notice(state) and not has_next_target:
        item.setForeground(QColor("#F59E0B"))
        item.setToolTip("현황: 정상이나 다음 절차 진행 대상 없음")
        item.setData(SORT_ROLE, 2)
        return item

    # 4. 그 외 매매시작 ON 상태는 운영방식/시간정책에 따른 정상 운영 상태다.
    item.setForeground(QColor("#16A34A"))
    item.setToolTip("현황: 정상 운영 중")
    item.setData(SORT_ROLE, 1)
    return item
