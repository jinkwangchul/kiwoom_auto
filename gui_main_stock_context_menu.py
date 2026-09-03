# -*- coding: utf-8 -*-
"""Main-monitoring stock-row context-menu adapter."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PyQt5.QtCore import QItemSelectionModel
from PyQt5.QtWidgets import QDialog

from gui_auto_trade_close import (
    auto_trade_apply_selected_early_close,
    auto_trade_apply_selected_early_close_profit_loss,
    auto_trade_apply_selected_individual_liquidation_method,
    auto_trade_cancel_selected_early_close,
)
from gui_auto_trade_run_control import (
    OperationStartCommandRequest,
    OperationStartIntent,
    auto_trade_registered_operation_start_targets,
    auto_trade_registered_operation_targets,
    auto_trade_running_registered_operation_targets,
    auto_trade_start_selected_rows_auto_trades,
    auto_trade_update_global_operation_button_state,
    execute_operation_start_command,
)
from gui_auto_trade_status_ops import (
    auto_trade_apply_schedule_times_to_targets,
    auto_trade_clear_selected_stock_operation_exclusions,
    auto_trade_finalize_operation_mode_result,
    auto_trade_reset_schedule_times_for_targets,
    auto_trade_set_selected_stock_operation_exclusions,
    execute_auto_trade_stock_operation_exclusion,
    inspect_auto_trade_operation_exclusion_availability,
)
from gui_auto_trade_unregister import unregister_selected_auto_trade_stocks
from assignment_authorization_service import (
    ASSIGNMENT_INTENT_STOCK_UNREGISTER,
    ASSIGNMENT_INTENT_UNASSIGN,
    execute_assignment_unassign,
    inspect_assignment_authorization,
    inspect_stock_unregister_availability,
)
from gui_main_emergency_ops import (
    execute_selected_emergency_stop,
)
from gui_auto_trade_ats_ops import (
    auto_trade_execute_selected_manual_ats_liquidation,
    auto_trade_selected_manual_ats_execution_method_state,
    auto_trade_selected_manual_ats_liquidation_available,
    auto_trade_selected_manual_ats_state,
    auto_trade_set_selected_manual_ats_execution_method,
    auto_trade_set_selected_manual_ats_flag,
)
from gui_auto_trade_context_menu import (
    StockContextMenuCallbacks,
    open_selected_stock_instance_charts,
    selected_emergency_context_state,
    show_monitor_stock_context_menu,
)
from gui_config_utils import default_config
from gui_schedule_window import ScheduleOperationDialog
from gui_auto_trade_integrity import (
    inspect_stock_review_state,
    is_operation_excluded,
)
from gui_auto_trade_policy import (
    auto_trade_setting_current_session_trade_started,
    auto_trade_setting_trade_started,
)
from gui_main_table_loader import (
    ROUTINE_INSTANCE_ID_ROLE,
    ROUTINE_ROW_KIND_ROLE,
    ROUTINE_ROW_STOCK,
    ROUTINE_STOCK_CODE_ROLE,
    ROUTINE_STOCK_NAME_ROLE,
    ROUTINE_STOCK_PATH_ROLE,
)
from runtime_io import read_json_dict
from state_policy import (
    effective_schedule_times,
    normalize_operation_mode,
)
from gui_routine_service import (
    execute_selected_stock_real_trade_command,
    selected_stock_real_trade_target_enabled,
    selected_stock_trade_permission_available,
    selected_stock_trade_permission_label,
)


PROJECT_ROOT = Path(__file__).resolve().parent


@dataclass(frozen=True)
class MainMonitoringStockTarget:
    stock_dir: Path
    code: str
    name: str
    routine_instance_id: str


def _stock_target_for_row(window, row: int) -> MainMonitoringStockTarget | None:
    if row < 0 or row >= window.routine_table.rowCount():
        return None
    if window.routine_table.isRowHidden(row):
        return None
    item = window.routine_table.item(row, 0)
    if item is None:
        return None
    if str(item.data(ROUTINE_ROW_KIND_ROLE) or "") != ROUTINE_ROW_STOCK:
        return None

    path_text = str(item.data(ROUTINE_STOCK_PATH_ROLE) or "").strip()
    code = str(item.data(ROUTINE_STOCK_CODE_ROLE) or "").strip()
    name = str(item.data(ROUTINE_STOCK_NAME_ROLE) or "").strip()
    instance_id = str(item.data(ROUTINE_INSTANCE_ID_ROLE) or "").strip()
    if not path_text or not code or not name or not instance_id:
        return None
    stock_dir = Path(path_text)
    if not stock_dir.is_absolute():
        stock_dir = PROJECT_ROOT / stock_dir
    if not stock_dir.exists():
        return None
    directory_name = stock_dir.name
    if "_" in directory_name:
        stored_code, stored_name = directory_name.split("_", 1)
        code = code or stored_code
        name = stored_name or name
    return MainMonitoringStockTarget(
        stock_dir=stock_dir,
        code=code,
        name=name,
        routine_instance_id=instance_id,
    )


def selected_main_monitoring_stock_targets(window) -> list[MainMonitoringStockTarget]:
    targets: list[MainMonitoringStockTarget] = []
    seen: set[str] = set()
    for index in window.routine_table.selectionModel().selectedRows():
        target = _stock_target_for_row(window, index.row())
        if target is None:
            continue
        key = str(target.stock_dir.resolve())
        if key in seen:
            continue
        seen.add(key)
        targets.append(target)
    return targets


def ensure_main_monitoring_context_stock_selected(window, row: int) -> None:
    target = _stock_target_for_row(window, row)
    if target is None:
        return
    selected_rows = {
        index.row()
        for index in window.routine_table.selectionModel().selectedRows()
        if _stock_target_for_row(window, index.row()) is not None
    }
    if row in selected_rows:
        return
    window.routine_table.clearSelection()
    window.routine_table.selectionModel().select(
        window.routine_table.model().index(row, 0),
        QItemSelectionModel.Select | QItemSelectionModel.Rows,
    )


def select_all_visible_main_monitoring_stocks(window) -> None:
    selection_model = window.routine_table.selectionModel()
    selection_model.clearSelection()
    for row in range(window.routine_table.rowCount()):
        if _stock_target_for_row(window, row) is None:
            continue
        selection_model.select(
            window.routine_table.model().index(row, 0),
            QItemSelectionModel.Select | QItemSelectionModel.Rows,
        )


def clear_main_monitoring_stock_selection(window) -> None:
    selection_model = window.routine_table.selectionModel()
    for index in tuple(selection_model.selectedRows()):
        if _stock_target_for_row(window, index.row()) is None:
            continue
        selection_model.select(
            index,
            QItemSelectionModel.Deselect | QItemSelectionModel.Rows,
        )


def clear_main_monitoring_chart_open_selection(window) -> None:
    """Clear the visible monitoring table's transient chart-launch selection."""

    selection_model = window.routine_table.selectionModel()
    selection_model.clearSelection()
    selection_model.clearCurrentIndex()


def open_selected_main_monitoring_stock_instance_charts(
    window,
    selected: list[tuple[Path, str, str]],
) -> list[object]:
    """Open the snapshotted targets, then clear only successful launch selection."""

    opened = open_selected_stock_instance_charts(window, selected)
    if opened:
        clear_main_monitoring_chart_open_selection(window)
    return opened


class MainMonitoringStockOperationAdapter:
    """Supply monitoring selection/refresh while reusing existing operations."""

    def __init__(
        self,
        window,
        targets: list[MainMonitoringStockTarget],
        *,
        request_scope: str | None = None,
        recovery_action_label: str = "",
    ) -> None:
        self._window = window
        self._targets = tuple(targets)
        self._request_scope = request_scope or (
            "single" if len(self._targets) == 1 else "multiple"
        )
        self._recovery_action_label = str(recovery_action_label or "").strip()
        self.stock_table = window.routine_table
        self._last_operation_block_reason = ""
        self._last_operation_user_message = ""
        self._last_operation_failure_dialog_shown = False

    def parent(self):
        return self._window

    def selected_stock_infos(self) -> list[tuple[Path, str, str]]:
        return [
            (target.stock_dir, target.code, target.name)
            for target in self._targets
        ]

    @property
    def btn_start(self):
        return self._window.btn_start

    def selected_operation_mode_set(
        self,
        selected: list[tuple[Path, str, str]] | None = None,
    ) -> set[str]:
        selected = selected if selected is not None else self.selected_stock_infos()
        modes: set[str] = set()
        for stock_dir, _code, _name in selected:
            config = read_json_dict(stock_dir / "config.json") or default_config()
            modes.add(
                normalize_operation_mode(
                    config.get("operation_mode", "SCHEDULED")
                )
            )
        return modes

    def current_selected_routine_name(self) -> str:
        instance_ids = {
            target.routine_instance_id
            for target in self._targets
        }
        if len(instance_ids) == 1:
            return next(iter(instance_ids))
        return "관제창 복수 루틴"

    def capture_stock_table_view_state(self) -> tuple[set[str], int]:
        return (
            {
                str(target.stock_dir.resolve())
                for target in selected_main_monitoring_stock_targets(
                    self._window
                )
            },
            self.stock_table.verticalScrollBar().value(),
        )

    def restore_stock_table_view_state(
        self,
        selected_paths: set[str],
        scroll_value: int,
    ) -> None:
        selection_model = self.stock_table.selectionModel()
        selection_model.clearSelection()
        wanted = {str(Path(value).resolve()) for value in selected_paths}
        for row in range(self.stock_table.rowCount()):
            target = _stock_target_for_row(self._window, row)
            if target is None:
                continue
            if str(target.stock_dir.resolve()) not in wanted:
                continue
            selection_model.select(
                self.stock_table.model().index(row, 0),
                QItemSelectionModel.Select | QItemSelectionModel.Rows,
            )
        self.stock_table.verticalScrollBar().setValue(scroll_value)

    def refresh_all(self) -> None:
        self.refresh_auto_trade_assignment_views()

    def refresh_auto_trade_assignment_views(self) -> None:
        selected_paths, scroll_value = self.capture_stock_table_view_state()
        refresh_views = getattr(
            self._window,
            "refresh_auto_trade_assignment_views",
            None,
        )
        if callable(refresh_views):
            refresh_views()
        else:
            self._window.refresh_all()
        self.restore_stock_table_view_state(selected_paths, scroll_value)

    def recalculate_routine_limits_for_new_operation_session(self) -> dict[str, object]:
        recalculator = getattr(
            self._window,
            "recalculate_routine_limits_for_new_operation_session",
            None,
        )
        if not callable(recalculator):
            return {"ok": False, "reason": "RECALCULATION_OWNER_UNAVAILABLE"}
        return recalculator()

    def update_action_buttons(self) -> None:
        return

    def load_selected_routine_stocks(self) -> None:
        self.refresh_all()

    def selected_stocks_are_operation_excluded(self) -> bool:
        selected = self.selected_stock_infos()
        return bool(selected) and all(
            is_operation_excluded(read_json_dict(stock_dir / "config.json"))
            for stock_dir, _code, _name in selected
        )

    def registered_operation_targets(self) -> list[tuple[Path, str, str]]:
        return auto_trade_registered_operation_targets(self._window)

    def registered_operation_start_targets(self) -> list[tuple[Path, str, str]]:
        return auto_trade_registered_operation_start_targets(self)

    def operation_start_exclusion_reason(
        self,
        target: tuple[Path, str, str],
    ) -> str | None:
        provider = getattr(
            self._window,
            "operation_start_exclusion_reason",
            None,
        )
        return provider(target) if callable(provider) else None

    def running_registered_operation_targets(self) -> list[tuple[Path, str, str]]:
        return auto_trade_running_registered_operation_targets(self)

    def update_global_operation_button_state(self) -> None:
        auto_trade_update_global_operation_button_state(self)

    def statusBarMessage(self, message: str, timeout_ms: int = 5000) -> None:
        self._last_operation_user_message = str(message or "").strip()
        self._window.statusBar().showMessage(message, timeout_ms)

    def statusBar_message(self, message: str, timeout_ms: int = 7000) -> None:
        self.statusBarMessage(message, timeout_ms)

    def operation_message_parent(self):
        return self._window

    def update_stock_status(self, *args, **kwargs):
        return self._execution_host().update_stock_status(*args, **kwargs)

    def update_stock_operation_mode(self, *args, **kwargs):
        return self._execution_host().update_stock_operation_mode(*args, **kwargs)

    def queue_pending_order_cancellations_for_stock_automatically(
        self,
        *args,
        **kwargs,
    ):
        return self._execution_host().queue_pending_order_cancellations_for_stock_automatically(
            *args,
            **kwargs,
        )

    def process_executable_order_for_auto_trade(self, *args, **kwargs):
        return self._execution_host().process_executable_order_for_auto_trade(
            *args,
            **kwargs,
        )

    def send_order_for_order_queued_automatically(self, *args, **kwargs):
        return self._execution_host().send_order_for_order_queued_automatically(
            *args,
            **kwargs,
        )

    def _execution_host(self):
        return self._window.main_monitoring_auto_trade_operation_host()

    def main_monitoring_auto_trade_operation_host(self):
        return self._execution_host()

    def startup_recovery_session_ready(self, *, refresh: bool = True) -> bool:
        checker = getattr(self._window, "startup_recovery_session_ready", None)
        if callable(checker):
            return bool(checker(refresh=refresh))
        return bool(self._execution_host().startup_recovery_session_ready(refresh=refresh))

    def global_operation_start_prerequisite(self, action_name: str) -> dict[str, object]:
        checker = getattr(self._window, "global_operation_start_prerequisite", None)
        if callable(checker):
            result = checker(action_name)
            return result if isinstance(result, dict) else {"allowed": True}
        return {
            "allowed": False,
            "reason": "GLOBAL_PREREQUISITE_OWNER_UNAVAILABLE",
        }

    def start_target_is_review_isolated(
        self,
        stock_dir: Path,
        stock_code: str,
    ) -> bool:
        checker = getattr(
            self._execution_host(),
            "start_target_is_review_isolated",
            None,
        )
        return bool(checker(stock_dir, stock_code)) if callable(checker) else False

    def filter_start_targets_by_recovery(
        self,
        targets: list[tuple[Path, str, str]],
        *,
        action: str,
    ) -> dict[str, object]:
        filter_targets = getattr(
            self._execution_host(),
            "filter_start_targets_by_recovery",
            None,
        )
        if callable(filter_targets):
            result = filter_targets(targets, action=action)
            formatter = getattr(
                self._window,
                "routine_recovery_block_message",
                None,
            )
            if (
                isinstance(result, dict)
                and result.get("allowed") is not True
                and self._recovery_action_label
                and callable(formatter)
            ):
                result = dict(result)
                result["user_message"] = formatter(self._recovery_action_label)
            return result
        return {
            "allowed": False,
            "reason": "RECOVERY_OWNER_UNAVAILABLE",
            "eligible": (),
            "excluded_review": (),
        }

    def split_start_targets(self, selected):
        return self._execution_host().split_start_targets(selected)

    def start_target_block_details(self):
        getter = getattr(self._execution_host(), "start_target_block_details", None)
        if not callable(getter):
            return ()
        return getter()

    def pre_start_review_check(self, *args, **kwargs):
        return self._execution_host().pre_start_review_check(*args, **kwargs)

    def mark_review_required(self, *args, **kwargs):
        return self._execution_host().mark_review_required(*args, **kwargs)

    def recalculate_stock_status_by_operation_policy(self, *args, **kwargs):
        return self._execution_host().recalculate_stock_status_by_operation_policy(
            *args,
            **kwargs,
        )

    def rebind_startup_recovery_after_trusted_runtime_update(self) -> None:
        self._execution_host().rebind_startup_recovery_after_trusted_runtime_update()

    def open_review_required_window(self) -> None:
        self._window.open_review_required_window()


def execute_main_monitoring_selective_start(
    adapter: MainMonitoringStockOperationAdapter,
    *,
    source: str = "auto_trade_context_menu",
) -> dict[str, object]:
    """Route a Main selection to the shared Operation Start command."""

    selected = tuple(adapter.selected_stock_infos())
    return execute_operation_start_command(
        adapter,
        OperationStartCommandRequest(
            intent=OperationStartIntent.SELECTIVE_START,
            selected_targets=selected,
            source=source,
        ),
        selective_backend=auto_trade_start_selected_rows_auto_trades,
    ).as_legacy_dict()


def toggle_main_monitoring_trade_permission(
    adapter: MainMonitoringStockOperationAdapter,
) -> dict[str, object]:
    """Apply the shared RealTrade command and keep Main-only UI feedback here."""

    selected = adapter.selected_stock_infos()
    if not selected:
        adapter.statusBarMessage("거래권한을 변경할 종목을 1개 이상 선택하세요.")
        return {"ok": False, "changed": 0, "blocked": 0, "reason": "NO_SELECTION"}
    result = execute_selected_stock_real_trade_command(
        adapter,
        selected,
        selected_stock_real_trade_target_enabled(selected),
    )
    if result.get("changed"):
        adapter.refresh_all()
    message = f"거래권한 변경: {result.get('changed', 0)}개"
    if result.get("blocked"):
        message += f" / 차단 {result.get('blocked', 0)}개"
    adapter.statusBarMessage(message)
    return result


def set_main_monitoring_individual_schedule_time(
    adapter: MainMonitoringStockOperationAdapter,
) -> None:
    """Collect Main schedule input, then delegate to the existing status owner."""

    selected = adapter.selected_stock_infos()
    if not selected:
        return
    first_config = read_json_dict(selected[0][0] / "config.json") or default_config()
    start_time, end_buy_time, _ = effective_schedule_times(first_config)
    dialog = ScheduleOperationDialog(
        adapter.parent(),
        start_time,
        end_buy_time,
        len(selected),
    )
    dialog.setWindowTitle("종목 시간 예외 설정")
    if dialog.exec_() != QDialog.Accepted:
        return
    result = auto_trade_apply_schedule_times_to_targets(
        adapter,
        selected,
        dialog.start_time(),
        dialog.end_buy_time(),
    )
    auto_trade_finalize_operation_mode_result(adapter, result)


def reset_main_monitoring_schedule_to_global(
    adapter: MainMonitoringStockOperationAdapter,
) -> None:
    selected = adapter.selected_stock_infos()
    if not selected:
        return
    result = auto_trade_reset_schedule_times_for_targets(adapter, selected)
    auto_trade_finalize_operation_mode_result(adapter, result)


def main_monitoring_unregister_available(
    adapter: MainMonitoringStockOperationAdapter,
) -> bool:
    return bool(adapter._targets) and all(
        inspect_stock_unregister_availability(
            adapter,
            PROJECT_ROOT,
            target.code,
            target.name,
        ).allowed
        for target in adapter._targets
    )


def _mock_entry_allowed(window, target: MainMonitoringStockTarget) -> tuple[bool, str]:
    state = read_json_dict(target.stock_dir / "state.json")
    if inspect_stock_review_state(target.stock_dir, loaded_state=state).review_required:
        return False, "현재 실제 운영 검토가 필요한 종목은 모의검증에 편입할 수 없습니다."
    if auto_trade_setting_current_session_trade_started(
        window,
        auto_trade_setting_trade_started(state),
        target.code,
    ):
        return False, "현재 실제 자동매매 운영 중인 종목은 모의검증에 편입할 수 없습니다."
    return True, ""


def _begin_main_mock_validation(window, target: MainMonitoringStockTarget) -> None:
    allowed, reason = _mock_entry_allowed(window, target)
    if not allowed:
        from PyQt5.QtWidgets import QMessageBox

        QMessageBox.information(window, "모의검증", reason)
        return
    window.begin_mock_validation(target)


def _main_mock_return(window, target: MainMonitoringStockTarget) -> None:
    destination = window.choose_mock_validation_return_destination(target.code)
    if not destination:
        return
    config = read_json_dict(target.stock_dir / "config.json")
    expected_instance_id = str(
        config.get("routine_instance_id")
        or config.get("assigned_routine_instance_id")
        or ""
    ).strip()
    production_target = (target.stock_dir, target.code, target.name)

    if destination in {"WAITING", "EXCLUDED"}:
        requested_excluded = destination == "EXCLUDED"

        def preflight():
            return inspect_auto_trade_operation_exclusion_availability(
                window, production_target, requested_excluded
            ).as_dict()

        def execute():
            return execute_auto_trade_stock_operation_exclusion(
                window, production_target, requested_excluded
            )
    else:
        intent = (
            ASSIGNMENT_INTENT_UNASSIGN
            if destination == "UNASSIGNED"
            else ASSIGNMENT_INTENT_STOCK_UNREGISTER
        )

        def preflight():
            return inspect_assignment_authorization(
                window,
                PROJECT_ROOT,
                target.code,
                target.name,
                intent=intent,
                expected_instance_id=expected_instance_id,
            )

        def execute():
            return execute_assignment_unassign(
                window,
                PROJECT_ROOT,
                target.code,
                target.name,
                expected_instance_id=expected_instance_id,
                intent=intent,
            )
    result = window.mock_validation_ui_actions.return_to_production(
        target.code,
        destination=destination,
        preflight=preflight,
        execute=execute,
    )
    if result.get("ok") is True:
        window.refresh_auto_trade_assignment_views()
        window.statusBar().showMessage("모의검증 종료 및 복귀가 완료되었습니다.", 5000)
    else:
        window.statusBar().showMessage(
            f"모의검증 복귀 실패: {result.get('reason') or '확인 필요'}",
            7000,
        )


def _main_mock_context(window, target: MainMonitoringStockTarget) -> dict[str, object]:
    host = getattr(window, "mock_validation_host", None)
    if host is None:
        return {"current": False, "state": ""}
    try:
        state = dict(window.mock_validation_context_state(target.code))
    except Exception:
        # A real MainWindow with unreadable Mock membership fails closed so
        # Production mutation actions do not appear for an uncertain stock.
        return {"current": True, "state": "", "context_error": True}
    if state.get("current") is not True:
        return state
    state.update(
        {
            "start": lambda: window.start_mock_validation_stock(target.code),
            "early_close": lambda: window.early_close_mock_validation_stock(target.code),
            "immediate_liquidation": lambda: window.immediate_liquidate_mock_validation_stock(target.code),
            "set_tax": lambda enabled: window.set_mock_validation_tax(target.code, enabled),
            "event": lambda: window.open_mock_validation_event_window(target.code),
            "review": lambda: window.open_mock_validation_review_window(target.code),
            "reset": lambda: window._reset_mock_validation_from_review(target.code),
            "return": lambda: _main_mock_return(window, target),
        }
    )
    return state


def show_main_monitoring_stock_context_menu(window, position) -> bool:
    item = window.routine_table.itemAt(position)
    if item is None:
        return False
    row = item.row()
    context_target = _stock_target_for_row(window, row)
    if context_target is None:
        return False

    ensure_main_monitoring_context_stock_selected(window, row)
    targets = selected_main_monitoring_stock_targets(window)
    if not targets:
        return False

    adapter = MainMonitoringStockOperationAdapter(window, targets)
    window._main_monitoring_stock_operation_adapter = adapter
    selected_modes = adapter.selected_operation_mode_set()
    operation_excluded = adapter.selected_stocks_are_operation_excluded()
    _has_selected_provenance, has_non_emergency = selected_emergency_context_state(
        (target.stock_dir, target.code, target.name) for target in targets
    )
    callbacks = StockContextMenuCallbacks(
        select_all=lambda: select_all_visible_main_monitoring_stocks(window),
        clear_selection=lambda: clear_main_monitoring_stock_selection(window),
        start=lambda: execute_main_monitoring_selective_start(adapter),
        emergency_stop=(
            lambda: execute_selected_emergency_stop(
                adapter,
                adapter.selected_stock_infos(),
            )
            if has_non_emergency
            else None
        ),
        stock_register=lambda target_instance_id=context_target.routine_instance_id: (
            window.open_routine_instance_stock_register_from_main_table(
                target_instance_id
            )
        ),
        early_close=lambda method: auto_trade_apply_selected_early_close(
            adapter,
            method,
            source="우클릭",
            selected=adapter.selected_stock_infos(),
        ),
        early_close_profit_loss=lambda: auto_trade_apply_selected_early_close_profit_loss(
            adapter
        ),
        early_close_cancel=lambda: auto_trade_cancel_selected_early_close(adapter),
        individual_liquidation=(
            lambda method, minutes: auto_trade_apply_selected_individual_liquidation_method(
                adapter,
                method,
                minutes,
            )
        ),
        open_charts=lambda: open_selected_main_monitoring_stock_instance_charts(
            window,
            adapter.selected_stock_infos(),
        ),
        time_change=lambda: set_main_monitoring_individual_schedule_time(adapter),
        time_reset=lambda: reset_main_monitoring_schedule_to_global(adapter),
        ats_state=lambda: auto_trade_selected_manual_ats_state(
            adapter,
            adapter.selected_stock_infos(),
        ),
        ats_toggle=lambda flag_key, enabled, label: auto_trade_set_selected_manual_ats_flag(
            adapter,
            flag_key,
            enabled,
            label,
        ),
        ats_execution_method_state=(
            lambda: auto_trade_selected_manual_ats_execution_method_state(
                adapter,
                adapter.selected_stock_infos(),
            )
        ),
        ats_execution_method_set=(
            lambda execution_method, label: auto_trade_set_selected_manual_ats_execution_method(
                adapter,
                execution_method,
                label,
                adapter.selected_stock_infos(),
            )
        ),
        trade_permission_label=lambda: selected_stock_trade_permission_label(
            adapter.selected_stock_infos()
        ),
        trade_permission_available=lambda: selected_stock_trade_permission_available(
            adapter,
            adapter.selected_stock_infos(),
        ),
        toggle_trade_permission=lambda: toggle_main_monitoring_trade_permission(adapter),
        ats_liquidation_available=(
            lambda: auto_trade_selected_manual_ats_liquidation_available(
                adapter,
                adapter.selected_stock_infos(),
            )
        ),
        ats_liquidation=(
            lambda method, state, visible_keys, selected_sessions: (
                auto_trade_execute_selected_manual_ats_liquidation(
                    adapter,
                    method,
                    state,
                    adapter.selected_stock_infos(),
                    visible_keys,
                    selected_sessions,
                )
            )
        ),
        set_operation_exclusion=lambda: auto_trade_set_selected_stock_operation_exclusions(
            adapter
        ),
        clear_operation_exclusion=(
            lambda: auto_trade_clear_selected_stock_operation_exclusions(adapter)
        ),
        unregister=lambda: unregister_selected_auto_trade_stocks(adapter),
        unregister_available=lambda: main_monitoring_unregister_available(adapter),
        mock_create=lambda: _begin_main_mock_validation(window, context_target),
        mock_actions=lambda: _main_mock_context(window, context_target),
    )
    show_monitor_stock_context_menu(
        window,
        window.routine_table.viewport().mapToGlobal(position),
        has_selection=True,
        callbacks=callbacks,
        selected_modes=selected_modes,
        operation_excluded=operation_excluded,
        selected_targets=adapter.selected_stock_infos(),
        scheduled_excluded_management=(
            operation_excluded and selected_modes == {"SCHEDULED"}
        ),
    )
    return True
