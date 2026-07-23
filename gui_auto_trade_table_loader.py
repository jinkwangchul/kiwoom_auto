# -*- coding: utf-8 -*-
"""
gui_auto_trade_table_loader.py

자동매매설정창 하단 종목표 로딩/표시 처리 헬퍼.
"""

from __future__ import annotations

import json
from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import QMessageBox

from gui_common_utils import safe_int_value
from gui_order_utils import (
    pending_order_side_quantities,
)
from gui_config_utils import default_config
from gui_review_utils import (
    average_price_from_state,
    build_review_required_item,
    current_price_from_state,
)
from runtime_io import read_json_dict
from gui_auto_trade_runtime import (
    now_text,
    parse_stock_folder_name,
    assigned_stock_dirs_in_routine,
    write_state_json,
)
from gui_base_stock_service import read_base_stocks
from state_policy import (
    status_after_operation_mode_change,
    operation_text_and_color,
    normalize_operation_mode,
    operation_mode_display,
    real_trade_enabled,
    trade_permission_display,
)
from gui_auto_trade_display import (
    apply_auto_trade_setting_activity_style,
    apply_auto_trade_setting_liquidation_style,
    auto_trade_setting_display_status,
    create_auto_trade_setting_status_item,
    create_auto_trade_status_item,
    display_status_text_for_gui,
    routine_status_display_text,
    SORT_ROLE,
    SortableTableWidgetItem,
    stock_position_display_values,
    yes_no_display,
)
from gui_auto_trade_situation import create_auto_trade_situation_item
from gui_auto_trade_policy import (
    auto_trade_setting_should_preserve_raw_status,
    auto_trade_setting_ats_after_regular_blocked,
    auto_trade_setting_close_timestamp_later,
    auto_trade_setting_early_close_metadata_is_stale,
    auto_trade_setting_early_close_requested,
    auto_trade_setting_effective_liquidation_method,
    auto_trade_setting_has_close_progress_quantity,
    auto_trade_setting_has_unresolved_quantity,
    auto_trade_setting_is_after_regular_end,
    auto_trade_setting_liquidation_active,
    auto_trade_setting_liquidation_completed_today,
    auto_trade_setting_liquidation_phase_active,
    auto_trade_setting_liquidation_result_policy,
    auto_trade_setting_liquidation_text,
    auto_trade_setting_mark_liquidation_result_for_display,
    auto_trade_setting_method_text,
    auto_trade_setting_no_next_step_notice,
    auto_trade_setting_regular_end_seconds,
    auto_trade_setting_today_date_text,
    auto_trade_setting_trade_started,
    auto_trade_setting_current_session_trade_started,
    auto_trade_setting_display_status_for_current_session,
    clear_early_close_runtime_metadata_only,
    clear_auto_close_runtime_metadata,
    close_method_from_state_or_policy,
    compact_operation_time_range,
    effective_liquidation_policy_for_config,
    individual_liquidation_policy_from_config,
)
from gui_auto_trade_integrity import (
    auto_trade_setting_server_mismatch_detected,
)
from gui_ats_utils import (
    auto_trade_setting_regular_market_active_now,
    manual_ats_active_now,
    manual_ats_enabled_labels,
    manual_ats_session_labels,
    manual_ats_source,
)


PROJECT_ROOT = Path(__file__).resolve().parent


def _selected_instance_stock_dirs(window) -> list[Path]:
    instance_ids_getter = getattr(window, "current_selected_target_instance_ids", None)
    if not callable(instance_ids_getter):
        return []
    target_instance_ids = {
        str(instance_id or "").strip()
        for instance_id in instance_ids_getter()
        if str(instance_id or "").strip()
    }
    if not target_instance_ids:
        return []

    result: list[Path] = []
    seen: set[str] = set()
    for stock in read_base_stocks():
        stock_path = str(stock.get("stock_path", "") or "").strip()
        if not stock_path:
            continue
        stock_dir = PROJECT_ROOT / stock_path
        assigned_instance_id = str(
            stock.get("assigned_routine_instance_id", "") or ""
        ).strip()
        if not assigned_instance_id:
            config = read_json_dict(stock_dir / "config.json")
            assigned_instance_id = str(
                config.get("assigned_routine_instance_id", "") or ""
            ).strip()
        if assigned_instance_id not in target_instance_ids:
            continue
        stock_dir_text = str(stock_dir)
        if stock_dir_text in seen:
            continue
        seen.add(stock_dir_text)
        result.append(stock_dir)
    return sorted(result, key=lambda path: path.name)


def auto_trade_load_selected_routine_stocks(window) -> None:
    routine_dir = window.current_selected_routine_dir()
    routine_name = window.current_selected_routine_name()

    selected_stock_paths, stock_scroll_value = window.capture_stock_table_view_state()

    # 헤더 정렬 상태에서 종목 설정을 변경하면 refresh/load 과정에서 sortItems()가 다시 실행되어
    # 작업 중인 종목이 화면상 다른 위치로 이동하는 문제가 있었다.
    # 정렬 규칙은 헤더 클릭 순간에만 적용하고, 이후 갱신에서는 그때 저장한 화면 순서를 우선 복원한다.
    previous_stock_path_order: list[str] = []
    stored_visual_order = getattr(window, "_stock_visual_order", [])
    if isinstance(stored_visual_order, list) and stored_visual_order:
        previous_stock_path_order = [str(path) for path in stored_visual_order if str(path).strip()]
    else:
        previous_stock_path_seen: set[str] = set()
        for row_index in range(window.stock_table.rowCount()):
            path_text = ""
            for col_index in range(window.stock_table.columnCount()):
                item = window.stock_table.item(row_index, col_index)
                if item is None:
                    continue
                value = item.data(Qt.UserRole)
                if value:
                    path_text = str(value)
                    break
            if path_text and path_text not in previous_stock_path_seen:
                previous_stock_path_order.append(path_text)
                previous_stock_path_seen.add(path_text)
    previous_stock_order_index = {path: index for index, path in enumerate(previous_stock_path_order)}
    preserve_visual_order = False

    status_bar_updater = getattr(window, "update_selected_routine_status_bar", None)
    if callable(status_bar_updater):
        status_bar_updater()

    window.stock_table.blockSignals(True)
    window.stock_table.setUpdatesEnabled(False)
    window.stock_table.setSortingEnabled(False)
    try:
        # v20.8.2: 상태 컬럼은 더 이상 셀 위젯을 사용하지 않는다.
        # 그래도 이전 버전에서 남은 셀 위젯이 있을 수 있으므로 먼저 제거한다.
        for row in range(window.stock_table.rowCount()):
            for col in range(window.stock_table.columnCount()):
                window.stock_table.removeCellWidget(row, col)
        window.stock_table.clearContents()

        stock_dirs = _selected_instance_stock_dirs(window)
        if not stock_dirs:
            window.stock_table.setRowCount(0)
            return
        if previous_stock_order_index:
            matched_previous_paths = {str(path) for path in stock_dirs} & set(previous_stock_order_index)
            if matched_previous_paths:
                preserve_visual_order = True
                fallback_start = len(previous_stock_order_index)
                stock_dirs.sort(
                    key=lambda path: (
                        previous_stock_order_index.get(str(path), fallback_start),
                        path.name,
                    )
                )
        window.stock_table.setRowCount(0)
        row = 0

        for stock_dir in stock_dirs:
            code, name = parse_stock_folder_name(stock_dir.name)
            state = read_json_dict(stock_dir / "state.json")

            # 검토종목은 자동매매설정 창에서 완전 제외한다.
            config = read_json_dict(stock_dir / "config.json")
            if not config:
                config = default_config()

            buy_pending_qty, sell_pending_qty = pending_order_side_quantities(stock_dir, state)
            holding_qty = safe_int_value(state.get("holding_qty"), 0)
            avg_price = average_price_from_state(state)
            has_unresolved_qty = auto_trade_setting_has_unresolved_quantity(
                holding_qty,
                buy_pending_qty,
                sell_pending_qty,
            )

            # 정규장 설정 종료 이후에는 마감/주황/조기마감 표시 원인을 화면/상태에 남기지 않는다.
            # 마감 이벤트는 정규장 안에서만 유효하며, 장 종료 후에는 감시/대기 기준으로 복귀한다.
            if auto_trade_setting_is_after_regular_end():
                state_changed_after_regular_end = False

                if str(state.get("operation_notice", "")).strip().upper() in {
                    "NO_CLOSE_TARGET",
                    "AUTO_CLOSE_NO_TARGET",
                    "EARLY_CLOSE_NO_TARGET",
                }:
                    state["operation_notice"] = ""
                    state["operation_notice_reason"] = ""
                    state["operation_notice_at"] = ""
                    state_changed_after_regular_end = True

                if auto_trade_setting_early_close_requested(state):
                    state = clear_early_close_runtime_metadata_only(dict(state))
                    state["status"] = "MONITORING"
                    state["trade_set_status"] = "WAIT_BUY"
                    state["buy_enabled"] = False
                    state["sell_enabled"] = False
                    state_changed_after_regular_end = True

                if state_changed_after_regular_end:
                    state["updated_at"] = now_text()
                    write_state_json(stock_dir, state)

            # 정상 복귀/재시작/새 매매시작 이후 남은 조기마감 메타는 화면 표시 전에 정리한다.
            if auto_trade_setting_early_close_metadata_is_stale(state):
                clean_state = clear_early_close_runtime_metadata_only(dict(state))
                if write_state_json(stock_dir, clean_state):
                    state = clean_state

            raw_status_for_cleanup = str(state.get("status", "")).strip().upper()
            close_runtime_active = auto_trade_setting_early_close_requested(state) or raw_status_for_cleanup in {
                "AUTO_CLOSE",
                "AUTO_CLOSING",
                "AUTO_CLOSED",
                "EARLY_CLOSE",
                "EARLY_CLOSING",
                "EARLY_CLOSED",
            }

            # 조기/자동마감 진행 중 매수 미체결만으로 검토관리 이동하지 않는다.
            # 정책 기준:
            # - 조기/자동마감은 1차 리셋 활동이다.
            # - 첫 매도신호 전까지 매수 흐름은 정상 루틴 마무리 과정으로 본다.
            # - 검토관리는 청산 이후에도 잔여 문제가 남거나, 명시적 안정성검사/재시작/
            #   긴급정지 해제 같은 검사 컨텍스트에서 판단한다.

            # 조기마감/자동마감은 v2.2 기준 추가매수 금지 상태가 아니다.
            # 다만 실제 보유/미도가 없고, 이미 대상 없음 notice가 찍힌 경우에는
            # 과거 마감 메타를 정리해 화면 상태가 계속 고정되지 않게 한다.
            has_close_progress_qty = auto_trade_setting_has_close_progress_quantity(
                holding_qty,
                sell_pending_qty,
            )
            if (
                close_runtime_active
                and not has_close_progress_qty
                and auto_trade_setting_no_next_step_notice(state)
            ):
                clean_state = clear_auto_close_runtime_metadata(dict(state))
                if write_state_json(stock_dir, clean_state):
                    state = clean_state

            # 운영중 발생한 보유/미체결은 정상 매매 흐름이다.
            # 기존에는 아래 조건만으로 검토관리로 보냈다.
            #   trade_started + 정규장 종료 이후 + 보유/미체결 존재
            # 이 조건은 운영중 정상 보유/미수/미도까지 검토관리로 분류하는 버그를 만든다.
            #
            # 검토관리 이동은 다음처럼 별도 검사 컨텍스트에서만 수행한다.
            # - 프로그램 시작/재시작 안전초기화
            # - 운영 시작 전 안정성/무결성 검사
            # - 긴급정지 해제 복구 검사
            # - 강제종료 처리
            # - 실제 청산 완료 후 잔여 확인 루틴
            # 따라서 refresh_all()/표시 갱신 경로에서는 보유/미체결만 보고 REVIEW_REQUIRED로 바꾸지 않는다.

            # 화면 표시 상태는 state.json의 과거 status를 그대로 쓰지 않는다.
            # 단, 매매시작 대상에서 제외된 종목(trade_enabled=False/STOPPED)은
            # 현재 시간정책으로 다시 실행상태처럼 보이면 안 된다.
            raw_state_status = state.get("status", "STOPPED")
            trade_started = auto_trade_setting_trade_started(state)
            raw_status_key = str(raw_state_status or "STOPPED").strip().upper() or "STOPPED"

            liquidation_phase_active = (
                trade_started
                and auto_trade_setting_liquidation_phase_active(config, holding_qty, state=state)
            )

            # 조기/자동마감 요청 후 진행 대상이 없으면 상태를 매수/매도로 재판정하지 않는다.
            # 현황은 주황, 상태는 감시/대기, 방식/청산은 비활성으로 고정한다.
            # 청산 절차에 들어가면 자동마감/조기마감 표시는 종료하고 감시/대기로 표시한다.
            if auto_trade_setting_no_next_step_notice(state):
                raw_display_status = display_status_text_for_gui("WAIT_BUY")
            elif liquidation_phase_active:
                raw_display_status = display_status_text_for_gui("WAIT_BUY")
            elif (
                not auto_trade_setting_is_after_regular_end()
                and auto_trade_setting_early_close_requested(state)
                and auto_trade_setting_has_close_progress_quantity(holding_qty, sell_pending_qty)
            ):
                raw_display_status = "조기마감"
            elif auto_trade_setting_should_preserve_raw_status(state, raw_state_status):
                raw_display_status = display_status_text_for_gui(raw_state_status)
            elif raw_status_key in {"STOPPED", "STOP", "MANUAL_STOPPED"} or not trade_started:
                raw_display_status = display_status_text_for_gui("STOPPED")
            else:
                mode = normalize_operation_mode(config.get("operation_mode", "SCHEDULED"))
                policy_status = status_after_operation_mode_change(mode, config)
                policy_display = auto_trade_setting_display_status(
                    display_status_text_for_gui(policy_status)
                )

                # 시간정책 표시 원칙:
                # - 시간운영: 개별/전역 매매 가능 시간 안에서만 매수/매도.
                # - 수동운영: 정규장 또는 수동운영 추가시간 체크 구간 안에서만 매수/매도.
                # - 시간 밖이면 현황 녹색/주황/시작 ON이어도 감시/대기로 표시한다.
                # - 보유/미수/미도 유무만으로 감시/대기/매수매도를 바꾸지 않는다.
                # - 자동마감/조기마감 상태 자체를 매수차단 사유로 보지 않는다.
                # - 단, 실제 잔여 수량이 없으면 기존처럼 감시/대기로 표시한다.
                if policy_display in ("자동마감", "조기마감") and not has_unresolved_qty:
                    raw_display_status = display_status_text_for_gui("WAIT_BUY")
                else:
                    raw_display_status = display_status_text_for_gui(policy_status)

            display_status = auto_trade_setting_display_status(raw_display_status)
            current_session_trade_started = auto_trade_setting_current_session_trade_started(
                window,
                trade_started,
            )
            display_status = auto_trade_setting_display_status_for_current_session(
                state,
                config,
                holding_qty=holding_qty,
                buy_pending_qty=buy_pending_qty,
                sell_pending_qty=sell_pending_qty,
                current_session_trade_started=current_session_trade_started,
                persisted_trade_started=trade_started,
            )

            stock_status_filter = str(getattr(window, "_stock_status_filter", "all") or "all").strip().lower()
            if stock_status_filter == "running" and not auto_trade_setting_trade_started(state):
                continue
            if stock_status_filter == "stopped" and auto_trade_setting_trade_started(state):
                continue
            if stock_status_filter == "error" and raw_status_key != "ERROR":
                continue

            window.stock_table.insertRow(row)

            operation_text, operation_color, operation_tooltip = operation_text_and_color(config)
            operation_display_text = compact_operation_time_range(operation_text)
            operation_display_tooltip = str(operation_tooltip or "").strip()
            if operation_display_text == "수동":
                operation_display_tooltip = ""

            # ATS는 수동운영 종목에만 해당한다.
            # 활성 ATS가 있으면 운영 컬럼에 반드시 표시해 운영자가 놓치지 않도록 한다.
            liquidation_completed_today = auto_trade_setting_liquidation_completed_today(state)
            if liquidation_completed_today:
                state, liquidation_result_policy = auto_trade_setting_mark_liquidation_result_for_display(
                    config,
                    state,
                    holding_qty,
                    buy_pending_qty,
                    sell_pending_qty,
                )
                display_status = auto_trade_setting_display_status(state.get("status", display_status))
            else:
                liquidation_result_policy = "NONE"

            ats_labels = [] if liquidation_completed_today else manual_ats_enabled_labels(config)
            if ats_labels:
                ats_source = manual_ats_source(config)
                operation_display_text = "수동+ATS"
                operation_color = "#D97706" if ats_source == "individual" else "#8A2BE2"
                ats_tooltip_lines = "/".join(str(label) for label in ats_labels if str(label).strip())
                ats_source_text = "개별설정 ATS" if ats_source == "individual" else "환경설정 ATS"
                operation_tooltip = f"{ats_source_text} 적용 | {ats_tooltip_lines}\n\n※주의:정규장외 시장 거래중"
                operation_display_tooltip = operation_tooltip

            trade_started = auto_trade_setting_trade_started(state)
            current_session_trade_started = auto_trade_setting_current_session_trade_started(
                window,
                trade_started,
            )
            method_text = auto_trade_setting_method_text(display_status, config, state)
            liquidation_text = auto_trade_setting_liquidation_text(config, display_status, state)

            if ats_labels:
                regular_active_now = auto_trade_setting_regular_market_active_now()
                ats_active_now = manual_ats_active_now(config)
                after_regular_end = auto_trade_setting_is_after_regular_end()

                # ATS는 정규장 외 거래가능시간 확장이다.
                # - 정규장 안이면 기존 수동운영 판정을 유지한다.
                # - 정규장 밖 + 선택 ATS 시간 밖이면 감시/대기.
                # - 정규장 이후 ATS 시간 안이라도 조기마감/자동마감/일반 청산정책이 있으면 감시/대기.
                # - 정규장 이후 ATS 시간 안이고 차단 조건이 없으면 매수/매도.
                if not regular_active_now:
                    if not ats_active_now:
                        display_status = "감시/대기"
                    elif after_regular_end:
                        if auto_trade_setting_ats_after_regular_blocked(
                            config,
                            display_status,
                            liquidation_text,
                            state,
                        ):
                            display_status = "감시/대기"
                        else:
                            display_status = "매수/매도"
                    else:
                        # 장전 ATS는 수동운영 기본틀과 동일하게 거래 가능 시간이다.
                        display_status = "매수/매도"

                    method_text = auto_trade_setting_method_text(display_status, config, state)
                    liquidation_text = auto_trade_setting_liquidation_text(config, display_status, state)

            liquidation_active = auto_trade_setting_liquidation_active(config, holding_qty, display_status=display_status, state=state)
            has_holding = holding_qty > 0
            # 상태/방식/청산의 화면 활성 기준을 분리한다.
            # - 현황 회색/시작 OFF: 상태/방식/청산 모두 비활성
            # - 현황 녹색/주황/시작 ON + 감시/대기: 상태는 운용 상태로 보되, 방식은 아직 매매방식 미적용 상태이므로 비활성
            # - 현황 녹색/주황/시작 ON + 매수/매도/자동마감/조기마감: 방식 활성
            # - 청산은 기존 청산 규칙 + 운영중 + 보유수량 조건을 모두 만족할 때만 활성
            status_cell_active = (
                current_session_trade_started
                and display_status not in ("긴급정지", "검토종목")
            )
            method_cell_active = (
                status_cell_active
                and display_status not in ("감시/대기", "-", "")
            )
            liquidation_has_policy = str(liquidation_text).strip() not in ("", "-")
            _liquidation_policy_for_style, liquidation_is_individual = effective_liquidation_policy_for_config(config)
            liquidation_cell_active = (
                current_session_trade_started
                and has_holding
                and liquidation_active
                and liquidation_has_policy
            )
            current_price = current_price_from_state(state)
            holding_text, price_text, profit_text, pending_text, profit_amount, _profit_rate = (
                stock_position_display_values(
                    holding_qty=holding_qty,
                    avg_price=avg_price,
                    current_price=current_price,
                    buy_pending_qty=buy_pending_qty,
                    sell_pending_qty=sell_pending_qty,
                )
            )

            values = [
                code,
                name,
                operation_display_text,
                "●",
                display_status,
                method_text,
                liquidation_text,
                holding_text,
                price_text,
                profit_text,
                pending_text,
            ]
            status_rank = {
                "감시/대기": 0,
                "매수/매도": 1,
                "자동마감": 2,
                "조기마감": 3,
            }.get(display_status, 99)
            if auto_trade_setting_server_mismatch_detected(state):
                situation_rank = 3
            elif auto_trade_setting_no_next_step_notice(state):
                situation_rank = 2
            elif current_session_trade_started:
                situation_rank = 1
            else:
                situation_rank = 0

            sort_values = [
                code,
                name,
                operation_display_text,
                situation_rank,
                status_rank,
                method_text,
                liquidation_text,
                holding_qty,
                avg_price,
                profit_amount,
                safe_int_value(buy_pending_qty, 0) + safe_int_value(sell_pending_qty, 0),
            ]

            for col, value in enumerate(values):
                if col == 3:
                    item = create_auto_trade_situation_item(
                        state,
                        current_session_trade_started,
                        display_status,
                    )
                elif col == 4:
                    item = create_auto_trade_setting_status_item(display_status)
                else:
                    item = SortableTableWidgetItem(value)
                    item.setToolTip(value)

                item.setData(Qt.UserRole, str(stock_dir))
                item.setData(SORT_ROLE, sort_values[col])

                if col == 2:
                    if liquidation_result_policy == "RED_STOP":
                        item.setToolTip("청산 결과 불안정\n\n시장가 청산 잔여 또는 미수 발생 - 운영정지 후 안정성검사 필요")
                    elif liquidation_result_policy == "CURRENT_CARRYOVER":
                        item.setToolTip("현재가 청산 잔여\n\n이월 취급 / 시간외·ATS 재진입 금지")
                    elif liquidation_completed_today:
                        item.setToolTip("금일 청산 완료\n\n시간외/ATS 재진입 금지")
                    elif str(value) == "수동+ATS":
                        item.setToolTip(operation_display_tooltip)
                    elif str(value) == "수동":
                        item.setToolTip("")
                    else:
                        item.setToolTip(operation_display_tooltip + "\n\n주의: 정규장외 거래 적용중")
                    item.setForeground(QColor(operation_color))
                elif col == 5:
                    item.setToolTip(f"현재 상태 적용 방식: {method_text}")
                    apply_auto_trade_setting_activity_style(item, method_cell_active)
                elif col == 6:
                    tooltip_prefix = "개별 청산" if liquidation_is_individual else "청산정책"
                    item.setToolTip(f"{tooltip_prefix}: {liquidation_text}")
                    apply_auto_trade_setting_liquidation_style(
                        item,
                        liquidation_cell_active,
                        liquidation_has_policy,
                        liquidation_is_individual,
                    )
                if col == 4:
                    apply_auto_trade_setting_activity_style(item, status_cell_active)

                if col in (0, 2, 3, 5, 6, 7, 8, 9, 10):
                    item.setTextAlignment(Qt.AlignCenter)
                elif col == 4:
                    item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
                else:
                    item.setTextAlignment(Qt.AlignVCenter | Qt.AlignLeft)

                window.stock_table.setItem(row, col, item)

            row += 1

        window.stock_table.clearSelection()
    finally:
        # 하단 종목표는 refresh/load 중 자동 재정렬하지 않는다.
        # _stock_visual_order는 헤더 클릭 직후에만 갱신한다.
        # loader는 저장된 화면 순서를 읽어서 복원만 한다.

        window.stock_table.setUpdatesEnabled(True)
        window.stock_table.blockSignals(False)

        window.restore_stock_table_view_state(selected_stock_paths, stock_scroll_value)


    window.update_action_buttons()
