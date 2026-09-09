# -*- coding: utf-8 -*-
"""Mock-only context targets and menus for the Main monitoring table."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from PyQt5.QtWidgets import QDialog, QMenu, QMessageBox

from gui_auto_trade_context_menu import (
    _add_early_close_menu,
    _add_individual_liquidation_menu,
    _add_ats_settings_menu,
    _dispatch_early_close_action,
    _dispatch_ats_settings_action,
)
from gui_auto_trade_close import ProfitLossEarlyCloseDialog

from gui_main_table_loader import (
    ROUTINE_INSTANCE_ID_ROLE,
    ROUTINE_ROW_KIND_ROLE,
    ROUTINE_ROW_MOCK_INSTANCE,
    ROUTINE_ROW_MOCK_STOCK,
    ROUTINE_STOCK_CODE_ROLE,
    ROUTINE_STOCK_NAME_ROLE,
    ROUTINE_STOCK_TOOLTIP_DATA_ROLE,
)
from mock_validation_contract import normalized_stock_code
from mock_validation_contract import instance_effective_settings
from mock_validation_operation_lifecycle import (
    instance_operation_state,
    mock_validation_end_eligibility,
)
from mock_validation_quick_chart import open_mock_instance_quick_chart


@dataclass(frozen=True)
class MockContextTarget:
    row_kind: str
    stock_code: str
    stock_name: str
    validation_session_id: str
    routine_instance_id: str = ""


def mock_context_target_for_row(window: Any, row: int) -> MockContextTarget | None:
    """Resolve a row only through Mock identity and current Mock repository state."""

    table = getattr(window, "routine_table", None)
    first = table.item(row, 0) if table is not None else None
    if first is None:
        return None
    row_kind = str(first.data(ROUTINE_ROW_KIND_ROLE) or "").strip()
    if row_kind not in {ROUTINE_ROW_MOCK_STOCK, ROUTINE_ROW_MOCK_INSTANCE}:
        return None
    projection = first.data(ROUTINE_STOCK_TOOLTIP_DATA_ROLE)
    if not isinstance(projection, dict) or projection.get("mock_validation") is not True:
        return None
    try:
        stock_code = normalized_stock_code(first.data(ROUTINE_STOCK_CODE_ROLE))
    except Exception:
        return None
    stock_name = str(first.data(ROUTINE_STOCK_NAME_ROLE) or "").strip()
    session_id = str(projection.get("validation_session_id") or "").strip()
    role_instance_id = str(first.data(ROUTINE_INSTANCE_ID_ROLE) or "").strip()
    projected_instance_id = str(projection.get("routine_instance_id") or "").strip()
    if not stock_name or not session_id or role_instance_id != projected_instance_id:
        return None
    if row_kind == ROUTINE_ROW_MOCK_STOCK and role_instance_id:
        return None
    if row_kind == ROUTINE_ROW_MOCK_INSTANCE and not role_instance_id:
        return None
    host = getattr(window, "mock_validation_host", None)
    document = host.current_session(stock_code) if host is not None else None
    if not isinstance(document, dict):
        return None
    session = document.get("session")
    if not isinstance(session, dict):
        return None
    if (
        session.get("validation_session_id") != session_id
        or session.get("stock_code") != stock_code
        or str(session.get("stock_name") or "").strip() != stock_name
    ):
        return None
    if (
        row_kind == ROUTINE_ROW_MOCK_INSTANCE
        and role_instance_id not in document.get("instance_execution", {})
    ):
        return None
    return MockContextTarget(
        row_kind=row_kind,
        stock_code=stock_code,
        stock_name=stock_name,
        validation_session_id=session_id,
        routine_instance_id=role_instance_id,
    )


def select_all_visible_mock_instances(window: Any) -> None:
    table = window.routine_table
    for row in range(table.rowCount()):
        first = table.item(row, 0)
        if first is None or first.data(ROUTINE_ROW_KIND_ROLE) != ROUTINE_ROW_MOCK_INSTANCE:
            continue
        for column in range(table.columnCount()):
            item = table.item(row, column)
            if item is not None:
                item.setSelected(True)


def clear_visible_mock_instance_selection(window: Any) -> None:
    table = window.routine_table
    for row in range(table.rowCount()):
        first = table.item(row, 0)
        if first is None or first.data(ROUTINE_ROW_KIND_ROLE) != ROUTINE_ROW_MOCK_INSTANCE:
            continue
        for column in range(table.columnCount()):
            item = table.item(row, column)
            if item is not None:
                item.setSelected(False)


def _mock_instance_start_targets(
    window: Any,
    row: int,
    target: MockContextTarget,
) -> tuple[MockContextTarget, ...]:
    table = window.routine_table
    selected_rows = sorted({index.row() for index in table.selectedIndexes()})
    selected_targets = tuple(
        current
        for selected_row in selected_rows
        if (current := mock_context_target_for_row(window, selected_row)) is not None
        and current.row_kind == ROUTINE_ROW_MOCK_INSTANCE
    )
    if target in selected_targets:
        return selected_targets
    return (target,)


def _start_mock_instances(
    actions: Any,
    targets: tuple[MockContextTarget, ...],
) -> dict[str, Any]:
    started: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    for target in targets:
        state = actions.instance_context_state(
            target.stock_code, target.routine_instance_id
        )
        if (
            state.get("current") is not True
            or state.get("validation_session_id") != target.validation_session_id
            or state.get("routine_instance_id") != target.routine_instance_id
        ):
            skipped.append(
                {
                    "stock_code": target.stock_code,
                    "routine_instance_id": target.routine_instance_id,
                    "reason": "MOCK_CONTEXT_TARGET_STALE",
                }
            )
            continue
        if state.get("can_start") is not True:
            skipped.append(
                {
                    "stock_code": target.stock_code,
                    "routine_instance_id": target.routine_instance_id,
                    "reason": str(
                        state.get("state")
                        or state.get("operation_state")
                        or "BLOCKED"
                    ),
                }
            )
            continue
        actions.start_instance(target.stock_code, target.routine_instance_id)
        started.append(
            {
                "stock_code": target.stock_code,
                "routine_instance_id": target.routine_instance_id,
            }
        )
    return {
        "status": "COMPLETED" if started else "BLOCKED",
        "started": started,
        "skipped": skipped,
    }


def _run(window: Any, title: str, operation: Callable[[], dict[str, Any]]) -> None:
    reporter = getattr(window, "_mock_action_result", None)
    if callable(reporter):
        reporter(title, operation)
    else:
        operation()


def _fresh_operation(
    window: Any,
    row: int,
    expected: MockContextTarget,
    operation: Callable[[MockContextTarget], dict[str, Any]],
) -> dict[str, Any]:
    current = mock_context_target_for_row(window, row)
    if current != expected:
        return {"status": "BLOCKED", "reason": "MOCK_CONTEXT_TARGET_STALE"}
    return operation(current)


def _apply_mock_profit_loss_early_close(
    window: Any,
    row: int,
    target: MockContextTarget,
    actions: Any,
) -> None:
    dialog = ProfitLossEarlyCloseDialog(window)
    if dialog.exec_() != QDialog.Accepted:
        return
    profit_percent, loss_percent = dialog.values()
    _run(
        window,
        "모의 Instance 조기마감",
        lambda: _fresh_operation(
            window,
            row,
            target,
            lambda current: actions.early_close_instance(
                current.stock_code,
                current.routine_instance_id,
                method="손/익절",
                profit_percent=profit_percent,
                loss_percent=loss_percent,
            ),
        ),
    )


def _apply_mock_individual_liquidation(
    window: Any,
    row: int,
    target: MockContextTarget,
    actions: Any,
    *,
    method: str,
    minutes: Any,
) -> None:
    if QMessageBox.question(
        window,
        "모의 Instance 개별청산",
        f"{target.stock_name}의 선택한 Mock Routine Instance를 {method} 청산하시겠습니까?",
    ) != QMessageBox.Yes:
        return
    _run(
        window,
        "모의 Instance 개별청산",
        lambda: _fresh_operation(
            window,
            row,
            target,
            lambda current: actions.individual_liquidation_instance(
                current.stock_code,
                current.routine_instance_id,
                method=method,
                minutes_before_regular_close=minutes,
            ),
        ),
    )


def show_mock_monitoring_context_menu(
    window: Any,
    position: Any,
    *,
    expected_row_kind: str,
) -> bool:
    """Show a Mock parent/Instance menu without any Production adapter fallback."""

    item = window.routine_table.itemAt(position)
    if item is None:
        return False
    row = item.row()
    target = mock_context_target_for_row(window, row)
    if target is None or target.row_kind != expected_row_kind:
        return False
    actions = getattr(window, "mock_validation_ui_actions", None)
    if actions is None:
        return False
    menu = QMenu(window.routine_table)
    menu.setToolTipsVisible(True)

    if target.row_kind == ROUTINE_ROW_MOCK_STOCK:
        document = window.mock_validation_host.current_session(target.stock_code)
        eligibility = (
            mock_validation_end_eligibility(document)
            if isinstance(document, dict)
            else {"eligible": False}
        )
        journal_action = menu.addAction("운영일지")
        unregister_action = menu.addAction("등록해제")
        unregister_action.setEnabled(eligibility.get("eligible") is True)
        chosen = menu.exec_(window.routine_table.viewport().mapToGlobal(position))
        if chosen is journal_action:
            current = mock_context_target_for_row(window, row)
            if current == target:
                window.open_mock_validation_event_window(
                    current.stock_code,
                    expected_validation_session_id=current.validation_session_id,
                )
        elif chosen is unregister_action and unregister_action.isEnabled():
            _run(
                window,
                "모의 등록해제",
                lambda: _fresh_operation(
                    window,
                    row,
                    target,
                    lambda current: actions.unregister(current.stock_code),
                ),
            )
        return True

    state = actions.instance_context_state(
        target.stock_code, target.routine_instance_id
    )
    if (
        state.get("current") is not True
        or state.get("validation_session_id") != target.validation_session_id
        or state.get("routine_instance_id") != target.routine_instance_id
    ):
        return False

    start_targets = _mock_instance_start_targets(window, row, target)
    start_available = any(
        actions.instance_context_state(
            current.stock_code, current.routine_instance_id
        ).get("can_start")
        is True
        for current in start_targets
    )
    start_action = menu.addAction("운영시작")
    start_action.setEnabled(start_available)
    validation_stop_action = menu.addAction("검증종료")
    validation_stop_action.setEnabled(state.get("can_validation_stop") is True)
    menu.addSeparator()
    select_all_action = menu.addAction("전체선택")
    clear_selection_action = menu.addAction("선택해제")
    document = window.mock_validation_host.current_session(target.stock_code)
    operation = instance_operation_state(document, target.routine_instance_id)
    operation_policy = (
        operation.get("operation_policy_snapshot")
        if isinstance(operation, dict)
        else None
    )
    if not isinstance(operation_policy, dict):
        provider = getattr(window.mock_validation_host, "_operation_policy_provider", None)
        operation_policy = provider() if callable(provider) else {}
    operation_policy = operation_policy if isinstance(operation_policy, dict) else {}
    early_close = _add_early_close_menu(
        menu,
        has_selection=True,
        operation_policy=operation_policy,
    )
    can_early_close = state.get("can_early_close") is True
    can_early_close_cancel = state.get("can_early_close_cancel") is True
    early_close["menu"].setEnabled(can_early_close or can_early_close_cancel)
    for key in ("routine", "market", "current", "profit_loss", "carry"):
        early_close[key].setEnabled(can_early_close)
    early_close["cancel"].setEnabled(can_early_close_cancel)
    individual = _add_individual_liquidation_menu(
        menu,
        has_selection=state.get("can_immediate") is True,
        operation_policy=operation_policy,
    )
    settings = instance_effective_settings(document, target.routine_instance_id)
    settings_editable = str(state.get("state") or "").strip().upper() == "WAITING"
    time_change_action = None
    time_reset_action = None
    ats_settings = None
    if settings["operation_mode"] == "SCHEDULED":
        menu.addSeparator()
        time_change_action = menu.addAction("시간변경")
        time_reset_action = menu.addAction("변경리셋")
        time_change_action.setEnabled(settings_editable)
        time_reset_action.setEnabled(settings_editable)
    elif settings["operation_mode"] == "CONTINUOUS":
        menu.addSeparator()
        ats_settings = _add_ats_settings_menu(
            menu,
            has_selection=True,
            state_getter=lambda: window.mock_routine_instance_ats_state(row),
            toggle=lambda key, enabled, label: window.set_mock_routine_instance_ats_flag(
                row, key, enabled, label
            ),
            liquidation_available_getter=lambda: state.get("can_immediate") is True,
        )
        ats_settings["menu"].setEnabled(
            settings_editable or state.get("can_immediate") is True
        )
        for _key, _label, action in ats_settings["session_actions"]:
            action.setEnabled(settings_editable)
    chart_action = menu.addAction("간이차트")
    chart_action.setEnabled(state.get("can_chart") is True)
    reset_action = menu.addAction("검증리셋")
    reset_action.setEnabled(state.get("can_reset") is True)

    chosen = menu.exec_(window.routine_table.viewport().mapToGlobal(position))
    if chosen is start_action and start_action.isEnabled():
        _run(
            window,
            "모의 Instance 운영시작",
            lambda: _start_mock_instances(actions, start_targets),
        )
    elif chosen is validation_stop_action and validation_stop_action.isEnabled():
        _run(
            window,
            "모의검증 검증종료",
            lambda: _fresh_operation(
                window,
                row,
                target,
                lambda current: actions.validation_stop_instance(
                    current.stock_code, current.routine_instance_id
                ),
            ),
        )
    elif chosen is select_all_action:
        select_all_visible_mock_instances(window)
    elif chosen is clear_selection_action:
        clear_visible_mock_instance_selection(window)
    elif _dispatch_early_close_action(
        chosen,
        early_close,
        apply_method=lambda method: _run(
            window,
            "모의 Instance 조기마감",
            lambda: _fresh_operation(
                window,
                row,
                target,
                lambda current: actions.early_close_instance(
                    current.stock_code,
                    current.routine_instance_id,
                    method=method,
                ),
            ),
        ),
        apply_profit_loss=lambda: _apply_mock_profit_loss_early_close(
            window, row, target, actions
        ),
        cancel=lambda: _run(
            window,
            "모의 Instance 조기마감 취소",
            lambda: _fresh_operation(
                window,
                row,
                target,
                lambda current: actions.cancel_early_close_instance(
                    current.stock_code, current.routine_instance_id
                ),
            ),
        ),
    ):
        pass
    elif chosen in {individual["market"], individual["current"], individual["carry"]}:
        method = {
            individual["market"]: "시장가",
            individual["current"]: "현재가",
            individual["carry"]: "이월",
        }[chosen]
        _apply_mock_individual_liquidation(
            window,
            row,
            target,
            actions,
            method=method,
            minutes=individual["minutes"],
        )
    elif any(chosen is action for _minute, action in individual["time_actions"]):
        minutes = next(
            minute
            for minute, action in individual["time_actions"]
            if chosen is action
        )
        _apply_mock_individual_liquidation(
            window,
            row,
            target,
            actions,
            method=individual["method"],
            minutes=minutes,
        )
    elif (
        time_change_action is not None
        and chosen is time_change_action
        and time_change_action.isEnabled()
    ):
        window.open_mock_routine_instance_schedule_dialog(row)
    elif (
        time_reset_action is not None
        and chosen is time_reset_action
        and time_reset_action.isEnabled()
    ):
        window.reset_mock_routine_instance_schedule(row)
    elif ats_settings is not None and _dispatch_ats_settings_action(
        chosen,
        ats_settings,
        toggle=lambda key, enabled, label: window.set_mock_routine_instance_ats_flag(
            row, key, enabled, label
        ),
        liquidate=lambda method, *_args: _apply_mock_individual_liquidation(
            window,
            row,
            target,
            actions,
            method=method,
            minutes=individual["minutes"],
        ),
    ):
        pass
    elif chosen is chart_action and chart_action.isEnabled():
        open_mock_instance_quick_chart(window, target)
    elif chosen is reset_action and reset_action.isEnabled():
        _run(
            window,
            "모의검증 검증리셋",
            lambda: _fresh_operation(
                window,
                row,
                target,
                lambda current: actions.reset_instance(
                    current.stock_code, current.routine_instance_id
                ),
            ),
        )
    return True


__all__ = [
    "MockContextTarget",
    "clear_visible_mock_instance_selection",
    "mock_context_target_for_row",
    "select_all_visible_mock_instances",
    "show_mock_monitoring_context_menu",
]
