# -*- coding: utf-8 -*-
"""Main-monitoring stock-row context-menu adapter."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PyQt5 import sip
from PyQt5.QtCore import QItemSelectionModel
from PyQt5.QtWidgets import QDialog

from gui_auto_trade_close import (
    auto_trade_apply_selected_early_close,
    auto_trade_apply_selected_early_close_profit_loss,
    auto_trade_apply_selected_individual_liquidation_method,
    auto_trade_cancel_selected_early_close,
)
from gui_auto_trade_run_control import (
    auto_trade_stop_selected_auto_trades,
    show_auto_trade_operation_failure_dialog,
    startup_recovery_operation_block_message,
)
from gui_auto_trade_status_ops import (
    auto_trade_set_operation_mode_for_targets,
    handle_auto_trade_operation_mode_double_click,
)
from gui_auto_trade_ats_ops import (
    auto_trade_execute_selected_manual_ats_liquidation,
    auto_trade_save_selected_manual_ats_state,
    auto_trade_selected_manual_ats_liquidation_available,
    auto_trade_selected_manual_ats_state,
    auto_trade_set_selected_manual_ats_flag,
)
from gui_auto_trade_context_menu import (
    StockContextMenuCallbacks,
    show_monitor_stock_context_menu,
)
from gui_config_utils import default_config
from gui_schedule_utils import schedule_config_updates
from gui_schedule_window import ScheduleOperationDialog
from gui_auto_trade_integrity import (
    is_emergency_stopped_state,
    is_operation_excluded,
    is_review_required_stock_dir,
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
    read_global_schedule,
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
        self._runtime_file_snapshot = ()
        self._last_operation_block_reason = ""
        self._last_operation_user_message = ""
        self._last_operation_failure_dialog_shown = False

    def parent(self):
        return self._window

    def close(self) -> None:
        return

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

    def target_snapshot(self) -> list[tuple[Path, str, str]]:
        return self.selected_stock_infos()

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
        selected_paths, scroll_value = self.capture_stock_table_view_state()
        self._window.refresh_all()
        self.restore_stock_table_view_state(selected_paths, scroll_value)
        window = getattr(self._window, "auto_trade_setting_window", None)
        if window is not None and not sip.isdeleted(window):
            refresh = getattr(window, "refresh_all", None)
            if callable(refresh):
                refresh()

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

    def set_stock_operation_exclusion(
        self,
        target: tuple[Path, str, str],
        excluded: bool,
        *,
        notify: bool = True,
        refresh: bool = True,
    ) -> bool:
        from gui_auto_trade_setting_window import AutoTradeSettingWindow

        return AutoTradeSettingWindow.set_stock_operation_exclusion(
            self,
            target,
            excluded,
            notify=notify,
            refresh=refresh,
        )

    def toggle_stock_operation_exclusion(
        self,
        target: tuple[Path, str, str],
    ) -> bool:
        from gui_auto_trade_setting_window import AutoTradeSettingWindow

        return AutoTradeSettingWindow.toggle_stock_operation_exclusion(
            self,
            target,
        )

    def registered_operation_targets(self) -> list[tuple[Path, str, str]]:
        from gui_auto_trade_setting_window import AutoTradeSettingWindow

        return AutoTradeSettingWindow.registered_operation_targets(self)

    def registered_operation_start_targets(self) -> list[tuple[Path, str, str]]:
        from gui_auto_trade_setting_window import AutoTradeSettingWindow

        return AutoTradeSettingWindow.registered_operation_start_targets(self)

    def running_registered_operation_targets(self) -> list[tuple[Path, str, str]]:
        from gui_auto_trade_setting_window import AutoTradeSettingWindow

        return AutoTradeSettingWindow.running_registered_operation_targets(self)

    def update_global_operation_button_state(self) -> None:
        from gui_auto_trade_setting_window import AutoTradeSettingWindow

        AutoTradeSettingWindow.update_global_operation_button_state(self)
        window = getattr(self._window, "auto_trade_setting_window", None)
        if window is None or sip.isdeleted(window):
            return
        update = getattr(window, "update_global_operation_button_state", None)
        if callable(update):
            update()

    def set_selected_stock_operation_exclusions(self) -> None:
        from gui_auto_trade_setting_window import AutoTradeSettingWindow

        AutoTradeSettingWindow.set_selected_stock_operation_exclusions(self)

    def clear_selected_stock_operation_exclusions(self) -> None:
        from gui_auto_trade_setting_window import AutoTradeSettingWindow

        AutoTradeSettingWindow.clear_selected_stock_operation_exclusions(self)

    def emergency_stop_selected_auto_trade_stocks(self) -> dict[str, object]:
        from gui_auto_trade_setting_window import AutoTradeSettingWindow

        return AutoTradeSettingWindow.emergency_stop_selected_auto_trade_stocks(
            self
        )

    def release_selected_emergency_stopped_auto_trade_stocks(
        self,
    ) -> dict[str, object]:
        from gui_auto_trade_setting_window import AutoTradeSettingWindow

        return AutoTradeSettingWindow.release_selected_emergency_stopped_auto_trade_stocks(
            self
        )

    def unregister_selected_auto_trade_stocks(self) -> None:
        from gui_auto_trade_setting_window import AutoTradeSettingWindow

        AutoTradeSettingWindow.unregister_selected_auto_trade_stocks(self)

    def statusBarMessage(self, message: str, timeout_ms: int = 5000) -> None:
        self._last_operation_user_message = str(message or "").strip()
        self._window.statusBar().showMessage(message, timeout_ms)

    def statusBar_message(self, message: str, timeout_ms: int = 7000) -> None:
        self.statusBarMessage(message, timeout_ms)

    def operation_message_parent(self):
        return self._window

    def current_runtime_file_signature(self):
        return self._execution_host().current_runtime_file_signature()

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

    def require_startup_recovery_session(self, action_name: str) -> bool:
        checker = getattr(self._window, "startup_recovery_session_ready", None)
        if callable(checker) and checker(refresh=True):
            self._last_operation_block_reason = ""
            return True
        reason_getter = getattr(self._window, "startup_recovery_block_reason", None)
        reason = str(reason_getter() or "").strip() if callable(reason_getter) else ""
        formatter = getattr(
            self._window,
            "routine_recovery_block_message",
            None,
        )
        message = (
            formatter(self._recovery_action_label)
            if self._recovery_action_label and callable(formatter)
            else startup_recovery_operation_block_message(action_name, reason)
        )
        self._last_operation_block_reason = reason or "RECOVERY_NOT_READY"
        self.statusBarMessage(message)
        return False

    def start_target_is_review_isolated(
        self,
        stock_dir: Path,
        stock_code: str,
    ) -> bool:
        if is_review_required_stock_dir(stock_dir):
            return True
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
        if self.require_startup_recovery_session(action):
            return {
                "allowed": True,
                "reason": "RECOVERY_COMPLETED",
                "eligible": tuple(targets),
                "excluded_review": (),
            }
        return {
            "allowed": False,
            "reason": self._last_operation_block_reason or "RECOVERY_NOT_READY",
            "eligible": (),
            "excluded_review": (),
        }

    def split_start_targets(self, selected):
        return self._execution_host().split_start_targets(selected)

    def split_stop_targets(self, selected):
        return self._execution_host().split_stop_targets(selected)

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

    def stop_risk_parts(self, stock_dir: Path) -> list[str]:
        return self._execution_host().stop_risk_parts(stock_dir)

    def confirm_stop_targets_once(self, selected) -> bool:
        return self._execution_host().confirm_stop_targets_once(selected)

    def show_auto_trade_result_dialog(
        self,
        title: str,
        heading: str,
        lines: list[str],
    ) -> None:
        from gui_auto_trade_setting_window import AutoTradeSettingWindow

        AutoTradeSettingWindow.show_auto_trade_result_dialog(
            self._window,
            title,
            heading,
            lines,
        )

    def open_review_required_window(self) -> None:
        self._window.open_review_required_window()

    def start_selected_auto_trades(self) -> dict[str, object]:
        from gui_auto_trade_setting_window import AutoTradeSettingWindow

        result = AutoTradeSettingWindow.start_selected_rows_auto_trades(
            self
        )
        return result if isinstance(result, dict) else {
            "ok": False,
            "reason": "NO_STARTABLE_TARGETS",
        }

    def set_selected_individual_schedule_time(self) -> None:
        selected = self.target_snapshot()
        if not selected:
            return
        first_config = (
            read_json_dict(selected[0][0] / "config.json") or default_config()
        )
        start_time, end_buy_time, _ = effective_schedule_times(first_config)
        dialog = ScheduleOperationDialog(
            self._window,
            start_time,
            end_buy_time,
            len(selected),
        )
        dialog.setWindowTitle("종목 시간 예외 설정")
        if dialog.exec_() != QDialog.Accepted:
            return
        auto_trade_set_operation_mode_for_targets(
            self,
            selected,
            "SCHEDULED",
            schedule_config_updates(
                dialog.start_time(),
                dialog.end_buy_time(),
            ),
        )

    def reset_selected_schedule_to_global(self) -> None:
        selected = self.target_snapshot()
        if not selected:
            return
        global_schedule = read_global_schedule()
        auto_trade_set_operation_mode_for_targets(
            self,
            selected,
            "SCHEDULED",
            schedule_config_updates(
                global_schedule["start_time"],
                global_schedule["end_buy_time"],
            ),
        )

    def set_selected_continuous_operation_mode(self) -> dict[str, object]:
        selected = self.target_snapshot()
        if not selected:
            return {
                "requested": 0,
                "succeeded": 0,
                "failed": 0,
                "results": [],
            }
        return auto_trade_set_operation_mode_for_targets(
            self,
            selected,
            "CONTINUOUS",
        )

    def handle_operation_mode_double_click(self) -> dict[str, object]:
        selected = self.target_snapshot()
        if not selected:
            return {
                "requested": 0,
                "succeeded": 0,
                "failed": 0,
                "results": [],
            }
        return handle_auto_trade_operation_mode_double_click(self, selected[0])

    def selected_manual_ats_state(
        self,
        selected: list[tuple[Path, str, str]] | None = None,
    ) -> dict[str, bool]:
        return auto_trade_selected_manual_ats_state(
            self,
            selected if selected is not None else self.target_snapshot(),
        )

    def selected_manual_ats_liquidation_available(
        self,
        selected: list[tuple[Path, str, str]] | None = None,
    ) -> bool:
        return auto_trade_selected_manual_ats_liquidation_available(
            self,
            selected if selected is not None else self.target_snapshot(),
        )

    def save_selected_manual_ats_state(
        self,
        ats_state: dict[str, bool],
        selected: list[tuple[Path, str, str]] | None = None,
        editable_keys: tuple[str, ...] | None = None,
    ) -> int:
        return auto_trade_save_selected_manual_ats_state(
            self,
            ats_state,
            selected if selected is not None else self.target_snapshot(),
            editable_keys,
        )

    def set_selected_manual_ats_flag(
        self,
        flag_key: str,
        enabled: bool,
        label: str,
    ) -> None:
        auto_trade_set_selected_manual_ats_flag(
            self,
            flag_key,
            enabled,
            label,
        )

    def execute_selected_manual_ats_liquidation(
        self,
        method: str,
        ats_state: dict[str, bool],
        selected: list[tuple[Path, str, str]] | None = None,
        editable_keys: tuple[str, ...] | None = None,
        selected_sessions: tuple[str, ...] | None = None,
    ) -> None:
        auto_trade_execute_selected_manual_ats_liquidation(
            self,
            method,
            ats_state,
            selected if selected is not None else self.target_snapshot(),
            editable_keys,
            selected_sessions,
        )

    def stop_selected_auto_trades(self) -> dict[str, object]:
        return auto_trade_stop_selected_auto_trades(self)

    def show_operation_failure_dialog(
        self,
        action: str,
        result: dict[str, object] | None,
    ) -> bool:
        return show_auto_trade_operation_failure_dialog(
            self,
            action,
            result,
            self._targets,
        )

    def apply_selected_early_close_profit_loss(self) -> None:
        auto_trade_apply_selected_early_close_profit_loss(self)

    def cancel_selected_early_close(self) -> None:
        auto_trade_cancel_selected_early_close(self)

    def apply_selected_early_close(
        self,
        method: str,
        *,
        source: str = "우클릭",
        extra_policy: dict[str, object] | None = None,
        show_error_dialog: bool = True,
        show_result_toast: bool = True,
    ) -> dict[str, object]:
        return auto_trade_apply_selected_early_close(
            self,
            method,
            source=source,
            extra_policy=extra_policy,
            show_error_dialog=show_error_dialog,
            show_result_toast=show_result_toast,
        )

    def apply_selected_individual_liquidation_method(
        self,
        method: str,
        minutes: str,
        *,
        show_error_dialog: bool = True,
    ) -> dict[str, object]:
        return auto_trade_apply_selected_individual_liquidation_method(
            self,
            method,
            minutes,
            show_error_dialog=show_error_dialog,
        )


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
    emergency_states = tuple(
        is_emergency_stopped_state(
            read_json_dict(target.stock_dir / "state.json")
        )
        for target in targets
    )
    callbacks = StockContextMenuCallbacks(
        select_all=lambda: select_all_visible_main_monitoring_stocks(window),
        clear_selection=lambda: clear_main_monitoring_stock_selection(window),
        start=adapter.start_selected_auto_trades,
        emergency_stop=(
            adapter.emergency_stop_selected_auto_trade_stocks
            if any(not state for state in emergency_states)
            else None
        ),
        emergency_release=(
            adapter.release_selected_emergency_stopped_auto_trade_stocks
            if any(emergency_states)
            else None
        ),
        stock_register=lambda target_instance_id=context_target.routine_instance_id: (
            window.open_routine_instance_stock_register_from_main_table(
                target_instance_id
            )
        ),
        early_close=lambda method: adapter.apply_selected_early_close(
            method,
            source="우클릭",
        ),
        early_close_profit_loss=adapter.apply_selected_early_close_profit_loss,
        early_close_cancel=adapter.cancel_selected_early_close,
        individual_liquidation=adapter.apply_selected_individual_liquidation_method,
        time_change=adapter.set_selected_individual_schedule_time,
        time_reset=adapter.reset_selected_schedule_to_global,
        ats_state=adapter.selected_manual_ats_state,
        ats_toggle=adapter.set_selected_manual_ats_flag,
        ats_liquidation_available=(
            adapter.selected_manual_ats_liquidation_available
        ),
        ats_liquidation=(
            lambda method, state, visible_keys, selected_sessions: (
                adapter.execute_selected_manual_ats_liquidation(
                    method,
                    state,
                    adapter.target_snapshot(),
                    visible_keys,
                    selected_sessions,
                )
            )
        ),
        set_operation_exclusion=adapter.set_selected_stock_operation_exclusions,
        clear_operation_exclusion=adapter.clear_selected_stock_operation_exclusions,
        unregister=adapter.unregister_selected_auto_trade_stocks,
    )
    show_monitor_stock_context_menu(
        window,
        window.routine_table.viewport().mapToGlobal(position),
        has_selection=True,
        callbacks=callbacks,
        selected_modes=adapter.selected_operation_mode_set(),
        operation_excluded=adapter.selected_stocks_are_operation_excluded(),
    )
    return True
