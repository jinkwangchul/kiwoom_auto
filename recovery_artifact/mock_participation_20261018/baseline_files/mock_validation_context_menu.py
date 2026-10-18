# -*- coding: utf-8 -*-
"""Mock-only context targets and menus for the Main monitoring table."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable

from PyQt5.QtWidgets import (
    QDialog,
    QMenu,
    QMessageBox,
)

from gui_operation_ui_primitives import (
    PersistentContextMenu,
    ProfitLossEarlyCloseDialog,
    _add_early_close_menu,
    _add_individual_liquidation_menu,
    _add_ats_settings_menu,
    _dispatch_early_close_action,
    _dispatch_ats_settings_action,
    _individual_liquidation_action_applied,
    _refresh_individual_liquidation_menu_state,
    ats_session_ui_options,
)
from gui_auto_trade_run_control import operation_start_result_summary_toast_text

from gui_main_table_loader import (
    ROUTINE_INSTANCE_ID_ROLE,
    ROUTINE_ROW_KIND_ROLE,
    ROUTINE_ROW_MOCK_INSTANCE,
    ROUTINE_ROW_MOCK_STOCK,
    ROUTINE_STOCK_CODE_ROLE,
    ROUTINE_STOCK_NAME_ROLE,
    ROUTINE_STOCK_TOOLTIP_DATA_ROLE,
)
from mock_validation_contract import (
    MockValidationError,
    instance_effective_settings,
    instance_individual_liquidation_reservation,
    mock_instance_active_operation,
    mock_instance_pre_start_editable,
    normalized_stock_code,
)
from mock_validation_operation_lifecycle import (
    instance_operation_state,
    mock_validation_end_eligibility,
)
from mock_validation_quick_chart import open_mock_instance_quick_chart


_QT_MENU_CLASS = QMenu


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
                    "stock_name": target.stock_name,
                    "display_label": " ".join(
                        part for part in (target.stock_code, target.stock_name) if part
                    )
                    + f" / {target.routine_instance_id}",
                    "routine_instance_id": target.routine_instance_id,
                    "reason": str(
                        state.get("start_block_reason")
                        or state.get("state")
                        or state.get("operation_state")
                        or "BLOCKED"
                    ),
                }
            )
            continue
        try:
            actions.start_instance(target.stock_code, target.routine_instance_id)
        except MockValidationError as exc:
            reason = str(exc) or "BLOCKED"
            if reason != "FINAL_SESSION_ENDED":
                raise
            skipped.append(
                {
                    "stock_code": target.stock_code,
                    "stock_name": target.stock_name,
                    "display_label": " ".join(
                        part for part in (target.stock_code, target.stock_name) if part
                    )
                    + f" / {target.routine_instance_id}",
                    "routine_instance_id": target.routine_instance_id,
                    "operation_mode": str(state.get("operation_mode") or ""),
                    "reason": reason,
                }
            )
            continue
        started.append(
            {
                "stock_code": target.stock_code,
                "stock_name": target.stock_name,
                "routine_instance_id": target.routine_instance_id,
            }
        )
    result = {
        "status": "COMPLETED" if started else "BLOCKED",
        "started": started,
        "skipped": skipped,
        "requested_count": len(targets),
        "started_count": len(started),
        "blocked_count": len(skipped),
        "blocked_target_details": tuple(skipped),
    }
    result["summary_toast_message"] = operation_start_result_summary_toast_text(
        result
    )
    return result


def _run(window: Any, title: str, operation: Callable[[], dict[str, Any]]) -> None:
    reporter = getattr(window, "_mock_action_result", None)
    if callable(reporter):
        reporter(title, operation)
    else:
        operation()


def _declared_server_authentication_checker(actions: Any) -> Callable[[], Any] | None:
    checker = getattr(type(actions), "server_authenticated", None)
    if callable(checker):
        return lambda: checker(actions)
    values = getattr(actions, "__dict__", None)
    checker = values.get("server_authenticated") if isinstance(values, dict) else None
    return checker if callable(checker) else None


def _run_mock_close_edit(
    window: Any,
    title: str,
    actions: Any,
    operation: Callable[[], dict[str, Any]],
) -> bool:
    checker = _declared_server_authentication_checker(actions)
    if checker is not None:
        try:
            authenticated = checker() is True
        except Exception:
            authenticated = False
        if not authenticated:
            def blocked() -> dict[str, Any]:
                raise MockValidationError("SERVER_NOT_CONNECTED")

            _run(window, title, blocked)
            return False
    _run(window, title, operation)
    return True


def _begin_mock_stock_registration(window: Any) -> bool:
    opener = getattr(window, "open_mock_stock_search_register_dialog", None)
    if not callable(opener):
        return False
    opener()
    return True


def show_mock_registration_context_menu(window: Any, position: Any) -> bool:
    """Show Mock stock registration without requiring an existing row target."""

    menu = QMenu(window.routine_table)
    register_action = menu.addAction("종목등록")
    unregister_all_action = menu.addAction("전체해제")
    actions = getattr(window, "mock_validation_ui_actions", None)
    unregister_all_action.setEnabled(actions is not None)
    chosen = menu.exec_(window.routine_table.viewport().mapToGlobal(position))
    if chosen is register_action:
        _begin_mock_stock_registration(window)
    elif chosen is unregister_all_action and actions is not None:
        _run(window, "모의 전체해제", actions.unregister_all)
    return True


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
    checker = _declared_server_authentication_checker(actions)
    if checker is not None:
        try:
            authenticated = checker() is True
        except Exception:
            authenticated = False
        if not authenticated:
            _run_mock_close_edit(window, "모의 Instance 조기마감", actions, lambda: {})
            return
    dialog = ProfitLossEarlyCloseDialog(window)
    if dialog.exec_() != QDialog.Accepted:
        return
    profit_percent, loss_percent = dialog.values()
    _run_mock_close_edit(
        window,
        "모의 Instance 조기마감",
        actions,
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
) -> object:
    captured: dict[str, object] = {}

    def apply() -> dict[str, Any]:
        result = _fresh_operation(
            window,
            row,
            target,
            lambda current: actions.individual_liquidation_instance(
                current.stock_code,
                current.routine_instance_id,
                method=method,
                minutes_before_regular_close=minutes,
            ),
        )
        captured["result"] = result
        return result

    _run_mock_close_edit(
        window,
        "모의 Instance 개별청산",
        actions,
        apply,
    )
    return captured.get("result")


def _apply_mock_ats_liquidation(
    window: Any,
    row: int,
    target: MockContextTarget,
    actions: Any,
    *,
    method: str,
) -> None:
    if QMessageBox.question(
        window,
        f"모의검증 ATS {method}매도 확인",
        (
            f"{target.stock_name}의 선택한 모의검증을 "
            f"ATS {method} 방식으로 즉시 청산하시겠습니까?"
        ),
    ) != QMessageBox.Yes:
        return
    _run(
        window,
        f"모의검증 ATS {method}매도",
        lambda: _fresh_operation(
            window,
            row,
            target,
            lambda current: actions.manual_ats_liquidation_instance(
                current.stock_code,
                current.routine_instance_id,
                method=method,
            ),
        ),
    )


def _individual_liquidation_menu_policy(
    window: Any,
    document: dict[str, Any],
    routine_instance_id: str,
) -> dict[str, Any]:
    """Resolve only current individual-liquidation evidence for the menu."""

    host = window.mock_validation_host
    active_operation = mock_instance_active_operation(
        document, routine_instance_id
    )
    operation_policy = (
        active_operation.get("operation_policy_snapshot")
        if isinstance(active_operation, dict)
        else None
    )
    if not isinstance(operation_policy, dict):
        provider = getattr(host, "_operation_policy_provider", None)
        operation_policy = provider() if callable(provider) else {}
    operation_policy = (
        deepcopy(operation_policy) if isinstance(operation_policy, dict) else {}
    )

    individual_snapshot = (
        active_operation.get("individual_liquidation_time_snapshot")
        if isinstance(active_operation, dict)
        else None
    )
    if not isinstance(individual_snapshot, dict):
        individual_snapshot = instance_individual_liquidation_reservation(
            document,
            routine_instance_id,
            program_session_id=getattr(host, "_program_session_id", None),
        )
    if not isinstance(individual_snapshot, dict):
        return operation_policy

    method = str(individual_snapshot.get("method") or "").strip().upper()
    method = {
        "MARKET": "시장가",
        "CURRENT_PRICE": "현재가",
        "CARRYOVER": "이월",
    }.get(method, method)
    operation_policy["liquidation"] = {
        "method": method,
        "minutes_before_regular_close": str(
            individual_snapshot.get("minutes_before_regular_close") or ""
        ).strip(),
    }
    return operation_policy


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
    menu = (
        PersistentContextMenu(window.routine_table)
        if QMenu is _QT_MENU_CLASS
        else QMenu(window.routine_table)
    )
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
    individual_snapshot = (
        operation.get("individual_liquidation_time_snapshot")
        if isinstance(operation, dict)
        else None
    )
    if not isinstance(individual_snapshot, dict):
        individual_snapshot = instance_individual_liquidation_reservation(
            document, target.routine_instance_id
        )
    if isinstance(individual_snapshot, dict):
        method = str(individual_snapshot.get("method") or "").strip().upper()
        method = {
            "MARKET": "시장가",
            "CURRENT_PRICE": "현재가",
            "CARRYOVER": "이월",
        }.get(method, method)
        operation_policy = deepcopy(operation_policy)
        operation_policy["liquidation"] = {
            "method": method,
            "minutes_before_regular_close": str(
                individual_snapshot.get("minutes_before_regular_close") or ""
            ).strip(),
        }
    current_close_method = str(
        operation.get("close_method") if isinstance(operation, dict) else ""
    ).strip()
    if current_close_method:
        operation_policy = deepcopy(operation_policy)
        early_policy = operation_policy.get("early_close")
        early_policy = deepcopy(early_policy) if isinstance(early_policy, dict) else {}
        early_policy["method"] = current_close_method
        operation_policy["early_close"] = early_policy
    early_close = _add_early_close_menu(
        menu,
        has_selection=True,
        operation_policy=operation_policy,
    )
    can_early_close_cancel = state.get("can_early_close_cancel") is True
    active_close_change = bool(
        isinstance(operation, dict)
        and str(operation.get("close_source") or "").strip().upper()
        in {"EARLY", "AUTO"}
        and str(operation.get("state") or "").strip().upper()
        in {"RUNNING", "CLOSING"}
    )
    snapshot_settings = operation_policy.get("mock_instance_effective_settings")
    snapshot_settings = (
        snapshot_settings if isinstance(snapshot_settings, dict) else operation_policy
    )
    active_auto_return = bool(
        active_close_change
        and str(snapshot_settings.get("operation_mode") or "").strip().upper()
        == "SCHEDULED"
    )
    if active_close_change:
        early_close["menu"].setTitle("마감변경")
    early_close["menu"].setEnabled(True)
    for key in ("routine", "market", "current", "profit_loss", "carry"):
        early_close[key].setEnabled(True)
    early_close["cancel"].setEnabled(can_early_close_cancel)
    if early_close.get("auto") is not None:
        early_close["auto"].setEnabled(active_auto_return)
        set_visible = getattr(early_close["auto"], "setVisible", None)
        if callable(set_visible):
            set_visible(active_auto_return)
    individual_policy = _individual_liquidation_menu_policy(
        window,
        document,
        target.routine_instance_id,
    )
    individual = _add_individual_liquidation_menu(
        menu,
        has_selection=True,
        operation_policy=individual_policy,
    )
    settings = instance_effective_settings(document, target.routine_instance_id)
    settings_editable = mock_instance_pre_start_editable(
        document, target.routine_instance_id
    )
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
        visible_keys, labels = ats_session_ui_options(operation_policy)
        ats_settings = _add_ats_settings_menu(
            menu,
            has_selection=True,
            visible_keys=visible_keys,
            labels=labels,
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
    unregister_eligibility = actions.unregister_instance_eligibility(
        target.stock_code,
        target.routine_instance_id,
        expected_validation_session_id=target.validation_session_id,
    )
    unregister_action = menu.addAction("등록해제")
    unregister_action.setEnabled(unregister_eligibility.get("eligible") is True)

    registrar = getattr(menu, "register_persistent_action", None)
    if callable(registrar):
        def apply_individual_setting(method: str, minutes: str) -> None:
            current = mock_context_target_for_row(window, row)
            if current != target:
                menu.invalidate_persistent_context()
                return
            result = _apply_mock_individual_liquidation(
                window,
                row,
                target,
                actions,
                method=method,
                minutes=minutes,
            )
            if _individual_liquidation_action_applied(result):
                _refresh_individual_liquidation_menu_state(
                    individual,
                    method=method,
                    minutes=minutes,
                )

        registrar(
            individual["market"],
            lambda: apply_individual_setting("시장가", individual["minutes"]),
        )
        registrar(
            individual["current"],
            lambda: apply_individual_setting("현재가", individual["minutes"]),
        )
        registrar(
            individual["carry"],
            lambda: apply_individual_setting("이월", individual["minutes"]),
        )
        for minute, time_action in individual["time_actions"]:
            registrar(
                time_action,
                lambda value=minute: apply_individual_setting(
                    individual["method"], value
                ),
            )

    chosen = menu.exec_(window.routine_table.viewport().mapToGlobal(position))
    if callable(registrar) and (
        chosen in {individual["market"], individual["current"], individual["carry"]}
        or any(chosen is action for _minute, action in individual["time_actions"])
    ):
        return True
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
        apply_method=lambda method: _run_mock_close_edit(
            window,
            "모의 Instance 조기마감",
            actions,
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
        return_auto=lambda: _run(
            window,
            "모의 Instance 자동마감 복귀",
            lambda: _fresh_operation(
                window,
                row,
                target,
                lambda current: actions.return_early_close_to_auto_instance(
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
        liquidate=lambda method, *_args: _apply_mock_ats_liquidation(
            window,
            row,
            target,
            actions,
            method=method,
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
    elif chosen is unregister_action and unregister_action.isEnabled():
        _run(
            window,
            "모의 Routine 등록해제",
            lambda: _fresh_operation(
                window,
                row,
                target,
                lambda current: actions.unregister_instance(
                    current.stock_code,
                    current.routine_instance_id,
                    expected_validation_session_id=current.validation_session_id,
                ),
            ),
        )
    return True


__all__ = [
    "MockContextTarget",
    "clear_visible_mock_instance_selection",
    "mock_context_target_for_row",
    "select_all_visible_mock_instances",
    "show_mock_registration_context_menu",
    "show_mock_monitoring_context_menu",
]
