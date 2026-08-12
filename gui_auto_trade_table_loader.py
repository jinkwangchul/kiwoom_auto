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
    directional_value_color,
    format_signed_money,
    format_signed_percent,
    pending_order_side_quantities,
)
from confirmable_pnl_cycle_service import project_confirmable_cumulative_pnl
from gui_config_utils import default_config
from gui_review_utils import (
    average_price_from_state,
    build_review_required_item,
    current_price_from_state,
)
from runtime_io import read_json_dict
from gui_auto_trade_runtime import (
    parse_stock_folder_name,
    assigned_stock_dirs_in_routine,
)
from gui_base_stock_service import read_base_stocks
from state_policy import (
    status_after_operation_mode_change,  # compatibility patch point; projection delegates below
    operation_text_and_color,
    operation_mode_display,
    real_trade_enabled,
    trade_permission_display,
)
from gui_auto_trade_display import (
    apply_auto_trade_setting_activity_style,
    apply_auto_trade_setting_liquidation_style,
    apply_auto_trade_setting_protection_row_style,
    auto_trade_setting_display_status,
    auto_trade_setting_status_color,
    auto_trade_setting_status_sort_rank,
    create_auto_trade_setting_status_item,
    create_auto_trade_status_item,
    routine_status_display_text,
    SORT_ROLE,
    SortableTableWidgetItem,
    stock_position_display_values,
    yes_no_display,
)
from gui_auto_trade_situation import create_auto_trade_situation_item
from gui_stock_instance_chart_window import (
    CHART_OPEN_STOCK_CODE_COLOR,
    stock_instance_chart_is_open,
)
from gui_auto_trade_policy import (
    auto_trade_setting_ats_after_regular_blocked,
    auto_trade_setting_close_timestamp_later,
    auto_trade_setting_early_close_metadata_is_stale,
    auto_trade_setting_early_close_progress_text,
    auto_trade_setting_early_close_requested,
    auto_trade_setting_effective_liquidation_method,
    auto_trade_setting_has_close_progress_quantity,
    auto_trade_setting_is_after_regular_end,
    auto_trade_setting_liquidation_active,
    auto_trade_setting_liquidation_completed_today,
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
)
from gui_auto_trade_integrity import (
    auto_trade_setting_server_mismatch_detected,
    is_operation_excluded,
    is_review_required_state,
)


_CHART_OPEN_CODE_BASE_STYLE_ROLE = Qt.UserRole + 1001


def refresh_auto_trade_chart_open_code_styles(window) -> None:
    """Project the live chart registry onto Settings stock-code items only."""
    table = getattr(window, "stock_table", None)
    if table is None:
        return
    for row in range(table.rowCount()):
        item = table.item(row, 0)
        if item is None:
            continue
        stock_code = str(item.text() or "").strip()
        baseline = item.data(_CHART_OPEN_CODE_BASE_STYLE_ROLE)
        chart_open = stock_instance_chart_is_open(stock_code)
        if chart_open:
            if baseline is None:
                baseline = (
                    item.data(Qt.ForegroundRole),
                    item.data(Qt.FontRole),
                )
                item.setData(_CHART_OPEN_CODE_BASE_STYLE_ROLE, baseline)
            item.setForeground(QColor(CHART_OPEN_STOCK_CODE_COLOR))
        elif baseline is not None:
            foreground, font = baseline
            item.setData(Qt.ForegroundRole, foreground)
            item.setData(Qt.FontRole, font)
            item.setData(_CHART_OPEN_CODE_BASE_STYLE_ROLE, None)
    table.viewport().update()
from gui_ats_utils import (
    auto_trade_setting_regular_market_active_now,
    manual_ats_active_now,
    manual_ats_enabled_labels,
    manual_ats_session_labels,
)


OPERATION_EXCLUDED_CONFIG_KEY = "operation_excluded"


def apply_auto_trade_operation_excluded_row_style(
    item: SortableTableWidgetItem,
    excluded: bool,
) -> None:
    apply_auto_trade_setting_protection_row_style(
        item,
        operation_excluded=excluded,
    )


PROJECT_ROOT = Path(__file__).resolve().parent


def _selected_instance_stock_dirs(window) -> list[Path]:
    if bool(getattr(window, "_all_stocks_scope_active", False)):
        instance_ids_getter = getattr(window, "all_registered_instance_ids", None)
    else:
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
            operation_excluded = is_operation_excluded(config)
            review_required = is_review_required_state(state)

            buy_pending_qty, sell_pending_qty = pending_order_side_quantities(stock_dir, state)
            holding_qty = safe_int_value(state.get("holding_qty"), 0)
            avg_price = average_price_from_state(state)
            # 정규장 설정 종료 이후에는 마감/주황/조기마감 표시 원인을 화면에 남기지 않는다.
            # 이 loader는 표시 projection만 계산하며 Runtime state는 변경하지 않는다.
            if auto_trade_setting_is_after_regular_end():
                if str(state.get("operation_notice", "")).strip().upper() in {
                    "NO_CLOSE_TARGET",
                    "AUTO_CLOSE_NO_TARGET",
                    "EARLY_CLOSE_NO_TARGET",
                    "EARLY_CLOSE_WAITING",
                    "EARLY_CLOSE_ORDER_PROGRESS",
                    "EARLY_CLOSE_COMPLETED",
                }:
                    state["operation_notice"] = ""
                    state["operation_notice_reason"] = ""
                    state["operation_notice_at"] = ""

                if auto_trade_setting_early_close_requested(state):
                    state = clear_early_close_runtime_metadata_only(dict(state))
                    state["status"] = "MONITORING"
                    state["trade_set_status"] = "WAIT_BUY"

            # 정상 복귀/재시작/새 매매시작 이후 남은 조기마감 메타는
            # persistence가 아니라 현재 표시 projection에서만 제외한다.
            if auto_trade_setting_early_close_metadata_is_stale(state):
                state = clear_early_close_runtime_metadata_only(dict(state))

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
            # - 검토관리는 청산 이후에도 잔여 문제가 남거나, 재시작/
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
                state = clear_auto_close_runtime_metadata(dict(state))

            # 운영중 발생한 보유/미체결은 정상 매매 흐름이다.
            # 기존에는 아래 조건만으로 검토관리로 보냈다.
            #   trade_started + 정규장 종료 이후 + 보유/미체결 존재
            # 이 조건은 운영중 정상 보유/미수/미도까지 검토관리로 분류하는 버그를 만든다.
            #
            # 검토관리 이동은 다음처럼 별도 검사 컨텍스트에서만 수행한다.
            # - 프로그램 시작/재시작 안전초기화
            # - 운영 시작 전 안정성/무결성 검사
            # - 긴급정지 해제 복구 검사
            # - 긴급정지 해제 복구 처리
            # - 실제 청산 완료 후 잔여 확인 루틴
            # 따라서 refresh_all()/표시 갱신 경로에서는 보유/미체결만 보고 REVIEW_REQUIRED로 바꾸지 않는다.

            trade_started = auto_trade_setting_trade_started(state)
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
            if stock_status_filter == "stopped":
                stock_status_filter = "running"
            if stock_status_filter == "running" and (review_required or operation_excluded):
                continue
            if stock_status_filter == "excluded" and (
                review_required or not operation_excluded
            ):
                continue
            if stock_status_filter in {"error", "review"} and not review_required:
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

            ats_labels = [] if liquidation_completed_today else manual_ats_enabled_labels(config, state)
            if ats_labels:
                operation_display_text = "수동+ATS"
                operation_color = "#D97706"
                ats_tooltip_lines = "/".join(str(label) for label in ats_labels if str(label).strip())
                ats_source_text = "현재 운영세션 ATS"
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
                ats_active_now = manual_ats_active_now(config, state)
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
            _liquidation_policy_for_style, liquidation_is_individual = (
                effective_liquidation_policy_for_config(config, state)
            )
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
            cycle_pnl = project_confirmable_cumulative_pnl(
                code,
                current_price,
                project_root=Path(__file__).resolve().parent,
            )
            if cycle_pnl.get("available") is True:
                profit_amount = float(cycle_pnl.get("cumulative_profit") or 0)
                cycle_rate = cycle_pnl.get("cumulative_rate")
                rate_text = format_signed_percent(cycle_rate, digits=2) if cycle_rate is not None else "-"
                profit_text = f"손익 {format_signed_money(profit_amount)} / {rate_text}"
            else:
                profit_amount = 0
                profit_text = "손익 확인 필요 / -"

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
            status_rank = auto_trade_setting_status_sort_rank(display_status)

            sort_values = [
                code,
                name,
                operation_display_text,
                None,
                status_rank,
                method_text,
                liquidation_text,
                holding_qty,
                avg_price,
                profit_amount,
                safe_int_value(buy_pending_qty, 0) + safe_int_value(sell_pending_qty, 0),
            ]

            for col, value in enumerate(values):
                if col == 1:
                    item = SortableTableWidgetItem(value)
                    if trade_started:
                        item.setToolTip("현재 운영 중입니다.")
                    else:
                        item.setToolTip("더블클릭하면 이 종목의 운영을 시작합니다.")
                elif col == 3:
                    item = create_auto_trade_situation_item(
                        state,
                        current_session_trade_started,
                        display_status,
                    )
                elif col == 4:
                    item = create_auto_trade_setting_status_item(display_status)
                    early_close_progress = (
                        auto_trade_setting_early_close_progress_text(state)
                    )
                    if early_close_progress:
                        item.setToolTip(f"조기마감: {early_close_progress}")
                else:
                    item = SortableTableWidgetItem(value)
                    item.setToolTip(value)

                item.setData(Qt.UserRole, str(stock_dir))
                sort_value = item.data(SORT_ROLE) if col == 3 else sort_values[col]
                item.setData(SORT_ROLE, sort_value)

                if col == 2:
                    if liquidation_result_policy == "RED_STOP":
                        item.setToolTip("청산 결과 불안정\n\n시장가 청산 잔여 또는 미수 발생 - 긴급정지 후 무결성 확인 필요")
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
                    if display_status in ("긴급정지", "검토종목"):
                        item.setForeground(QColor(auto_trade_setting_status_color(display_status)))
                if col == 9:
                    item.setForeground(QColor(directional_value_color(profit_amount)))
                if col != 3:
                    apply_auto_trade_setting_protection_row_style(
                        item,
                        review_required=review_required,
                        operation_excluded=operation_excluded,
                    )

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


    refresh_auto_trade_chart_open_code_styles(window)
    window.update_action_buttons()
