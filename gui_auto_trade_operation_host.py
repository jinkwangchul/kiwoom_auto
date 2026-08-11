# -*- coding: utf-8 -*-
"""Widget-free operation host shared by production GUI callers."""

from __future__ import annotations

import logging
from pathlib import Path

from PyQt5.QtCore import QObject, QTimer, pyqtSignal

from auto_trade_order_execution_boundary import (
    AutoTradeOrderExecutionBoundary,
    AutoTradeOrderExecutionContext,
    ORDER_EXECUTIONS_PATH,
    ORDER_LOCKS_PATH,
    ORDER_QUEUE_PATH,
)

from gui_auto_trade_integrity import (
    auto_trade_setting_data_inconsistency_reasons,
    is_review_required_stock_dir,
    unique_review_reasons,
)
from gui_auto_trade_runtime import now_text
from gui_auto_trade_runtime import get_stock_dirs_in_routine
from gui_base_stock_service import read_base_stocks
from gui_routine_registry import get_routine_dirs
from gui_auto_trade_status_ops import (
    auto_trade_recalculate_all_status_by_operation_policy,
    auto_trade_recalculate_stock_status_by_operation_policy,
    auto_trade_resume_status_after_pause,
    auto_trade_update_stock_operation_mode,
    auto_trade_update_stock_status,
)
from gui_auto_trade_timer import auto_trade_current_runtime_file_signature
from gui_review_utils import (
    build_review_required_item,
    pending_order_summary,
    safe_float_value,
    safe_int_value,
)
from runtime_io import read_json_dict
from state_policy import auto_trade_status_display
from event_journal_production import append_production_event


LOGGER = logging.getLogger(__name__)


class AutoTradeOperationHost(QObject):
    """Expose explicit production operations without constructing a GUI window."""

    operation_cycle_completed = pyqtSignal(dict)

    def __init__(self, owner) -> None:
        super().__init__(owner if isinstance(owner, QObject) else None)
        self._owner = owner
        self._operation_timer = QTimer(self)
        self._operation_timer.setInterval(10_000)
        self._operation_timer.timeout.connect(self.run_operation_cycle)
        self._last_time_policy_minute_key = ""
        self._operation_cycle_running = False
        self._shutting_down = False
        self._order_execution_boundary = AutoTradeOrderExecutionBoundary(
            AutoTradeOrderExecutionContext(
                kiwoom_connected=self._kiwoom_connected,
                account_numbers=self._kiwoom_account_numbers,
                selected_account_no=self._selected_account_no,
                send_order_callable=self._send_order_callable,
                selected_stock_info=lambda: None,
                selected_routine_metadata=lambda: None,
                selected_target_instance_ids=lambda: (),
                selected_routine_dir=lambda: None,
                routine_dirs=lambda: get_routine_dirs(),
                stock_dirs_in_routine=lambda routine_dir: get_stock_dirs_in_routine(routine_dir),
                base_stocks=lambda: read_base_stocks(),
                order_queue_path=lambda: ORDER_QUEUE_PATH,
                order_executions_path=lambda: ORDER_EXECUTIONS_PATH,
                order_locks_path=lambda: ORDER_LOCKS_PATH,
                confirm_runtime_file_init=None,
            )
        )

    def parent(self):
        return self._owner

    def statusBarMessage(self, message: str, timeout_ms: int = 5000) -> None:
        status_bar_getter = getattr(self._owner, "statusBar", None)
        if not callable(status_bar_getter):
            return
        try:
            status_bar_getter().showMessage(message, timeout_ms)
        except Exception:
            pass

    def start_after_recovery(self, identity) -> dict[str, object]:
        """Start the single durable operation loop after approved Recovery."""
        if self._shutting_down:
            return {
                "started": False,
                "reason_code": "OPERATION_HOST_SHUTTING_DOWN",
                "started_count": 0,
            }
        from production_recovery_timer_lifecycle import start_recovery_bound_timers

        result = start_recovery_bound_timers(
            identity=identity,
            timers=(self._operation_timer,),
        )
        if result.get("started") is True and int(result.get("started_count") or 0) > 0:
            append_production_event(
                "OPERATION_HOST_STARTED",
                result="SUCCESS",
                source="AutoTradeOperationHost.start_after_recovery",
                target_type="OPERATION_HOST",
                target_id="main_operation_host",
                reason_code=str(result.get("reason_code") or ""),
            )
        return result

    def stop_operation_timers(self) -> dict[str, object]:
        from production_recovery_timer_lifecycle import stop_recovery_bound_timers

        result = stop_recovery_bound_timers((self._operation_timer,))
        if result.get("stopped") is True and int(result.get("stopped_count") or 0) > 0:
            append_production_event(
                "OPERATION_HOST_STOPPED",
                result="COMPLETED",
                source="AutoTradeOperationHost.stop_operation_timers",
                target_type="OPERATION_HOST",
                target_id="main_operation_host",
                reason_code=str(result.get("reason_code") or ""),
            )
        return result

    def shutdown(self) -> dict[str, object]:
        self._shutting_down = True
        return self.stop_operation_timers()

    def operation_timer(self) -> QTimer:
        return self._operation_timer

    def run_operation_cycle(self) -> dict[str, object]:
        """Run one operation cycle without depending on any window visibility."""
        if self._shutting_down:
            return {"processed": False, "reason_code": "OPERATION_HOST_SHUTTING_DOWN"}
        if self._operation_cycle_running:
            return {"processed": False, "reason_code": "OPERATION_CYCLE_REENTRY"}

        from gui_auto_trade_timer import auto_trade_run_operation_cycle

        self._operation_cycle_running = True
        try:
            try:
                result = auto_trade_run_operation_cycle(self)
            except Exception as exc:
                LOGGER.exception("Main operation host cycle failed")
                self.statusBarMessage(
                    "자동매매 운영 주기를 처리하지 못했습니다. 로그를 확인하십시오."
                )
                result = {
                    "processed": False,
                    "reason_code": "OPERATION_CYCLE_FAILED",
                    "error": str(exc),
                }
        finally:
            self._operation_cycle_running = False
        if isinstance(result, dict):
            signal_result = result.get("signal_result", {})
            deferred = (
                isinstance(signal_result, dict)
                and signal_result.get("deferred_for_candle_refresh") is True
            )
            if not deferred:
                self.operation_cycle_completed.emit(result)
            return result
        return {"processed": False, "reason_code": "INVALID_OPERATION_CYCLE_RESULT"}

    def complete_deferred_operation_cycle(self, result: dict[str, object]) -> None:
        """Emit the cycle boundary after asynchronous candle/signal work ends."""
        if self._shutting_down or not isinstance(result, dict):
            return
        self.operation_cycle_completed.emit(dict(result))

    def _kiwoom_api(self):
        return getattr(self._owner, "kiwoom_api", None)

    def _kiwoom_connected(self) -> bool:
        checker = getattr(self._kiwoom_api(), "is_connected", None)
        try:
            return bool(checker()) if callable(checker) else False
        except Exception:
            return False

    def _kiwoom_account_numbers(self) -> list[str]:
        getter = getattr(self._owner, "kiwoom_account_numbers", None)
        if not callable(getter):
            getter = getattr(self._kiwoom_api(), "account_numbers", None)
        try:
            values = getter() if callable(getter) else []
        except Exception:
            values = []
        return list(values) if isinstance(values, list) else []

    def _selected_account_no(self) -> str:
        getter = getattr(self._owner, "selected_account_no", None)
        try:
            return str(getter() or "").strip() if callable(getter) else ""
        except Exception:
            return ""

    def _send_order_callable(self):
        return getattr(self._kiwoom_api(), "send_order", None)

    def startup_recovery_session_ready(self, *, refresh: bool = True) -> bool:
        checker = getattr(self._owner, "startup_recovery_session_ready", None)
        try:
            return bool(checker(refresh=refresh)) if callable(checker) else False
        except Exception:
            return False

    def current_runtime_file_signature(self):
        return auto_trade_current_runtime_file_signature(self)

    def recalculate_all_status_by_operation_policy(
        self,
        reason: str,
        silent_unchanged: bool = False,
        write_changelog_when_unchanged: bool = True,
    ) -> dict[str, int]:
        return auto_trade_recalculate_all_status_by_operation_policy(
            self,
            reason,
            silent_unchanged,
            write_changelog_when_unchanged,
        )

    def update_stock_operation_mode(self, *args, **kwargs):
        return auto_trade_update_stock_operation_mode(self, *args, **kwargs)

    def resume_status_after_pause(self, state: dict[str, object]):
        return auto_trade_resume_status_after_pause(self, state)

    @staticmethod
    def int_state_value(state: dict[str, object], key: str) -> int:
        try:
            return int(state.get(key, 0) or 0)
        except (TypeError, ValueError):
            return 0

    def filter_start_targets_by_recovery(
        self,
        targets: list[tuple[Path, str, str]],
        *,
        action: str,
    ) -> dict[str, object]:
        filter_targets = getattr(
            self._owner,
            "filter_start_targets_by_production_recovery",
            None,
        )
        if callable(filter_targets):
            return filter_targets(targets, caller_name=action)
        if self.startup_recovery_session_ready(refresh=True):
            return {
                "allowed": True,
                "reason": "RECOVERY_COMPLETED",
                "eligible": tuple(targets),
                "excluded_review": (),
            }
        return {
            "allowed": False,
            "reason": "RECOVERY_NOT_READY",
            "eligible": (),
            "excluded_review": (),
        }

    def queue_pending_order_cancellations_for_stock_automatically(
        self,
        *args,
        **kwargs,
    ):
        return self._order_execution_boundary.queue_pending_order_cancellations_for_stock_automatically(
            *args,
            **kwargs,
        )

    def process_executable_order_for_auto_trade(self, *args, **kwargs):
        return self._order_execution_boundary.process_executable_order_for_auto_trade(
            *args,
            **kwargs,
        )

    def send_order_for_order_queued_automatically(self, *args, **kwargs):
        return self._order_execution_boundary.send_order_for_order_queued_automatically(
            *args,
            **kwargs,
        )

    def auto_process_executable_orders_for_real_trade(self, *args, **kwargs):
        return self._order_execution_boundary.auto_process_executable_orders_for_real_trade(
            *args,
            **kwargs,
        )

    def start_target_is_review_isolated(
        self,
        stock_dir: Path,
        _stock_code: str,
    ) -> bool:
        return is_review_required_stock_dir(stock_dir)

    def split_start_targets(self, selected):
        allowed = {
            "STOPPED",
            "STOP",
            "WAIT",
            "WAIT_BUY",
            "WAIT_SELL",
            "MONITORING",
            "WATCHING",
            "WATCH",
            "WATCH_BUY",
        }
        targets = []
        skipped = []
        for stock_dir, code, name in selected:
            if self.start_target_is_review_isolated(stock_dir, code):
                skipped.append(f"{code} {name}(검토종목)")
                continue
            state = read_json_dict(Path(stock_dir) / "state.json")
            status = str(state.get("status", "STOPPED")).strip().upper() or "STOPPED"
            if status in allowed:
                targets.append((stock_dir, code, name))
            else:
                skipped.append(f"{code} {name}({auto_trade_status_display(status)})")
        return targets, skipped

    def split_stop_targets(self, selected):
        targets = []
        skipped = []
        for stock_dir, code, name in selected:
            state = read_json_dict(Path(stock_dir) / "state.json")
            status = str(state.get("status", "STOPPED")).strip().upper() or "STOPPED"
            if status in {"STOPPED", "STOP"}:
                skipped.append(f"{code} {name}(이미 중지됨)")
            else:
                targets.append((stock_dir, code, name))
        return targets, skipped

    def pre_start_review_check(
        self,
        routine_name: str,
        stock_dir: Path,
        code: str,
        name: str,
    ) -> dict[str, object]:
        item = build_review_required_item(routine_name, stock_dir, code, name)
        state = read_json_dict(stock_dir / "state.json")
        reasons = auto_trade_setting_data_inconsistency_reasons(state)
        if reasons:
            return build_review_required_item(
                routine_name,
                stock_dir,
                code,
                name,
                reasons,
            )
        before_status = (
            str(state.get("status", "STOPPED")).strip().upper() or "STOPPED"
        )
        if before_status == "PAUSED":
            new_status, metadata, reason = self.resume_status_after_pause(state)
            if new_status == "REVIEW_REQUIRED":
                item = build_review_required_item(
                    routine_name,
                    stock_dir,
                    code,
                    name,
                    [reason],
                )
                item["resume_metadata"] = metadata
        return item

    def mark_review_required(
        self,
        stock_dir: Path,
        code: str,
        name: str,
        item: dict[str, object],
        source: str = "",
    ) -> bool:
        reasons = unique_review_reasons(list(item.get("review_reasons", [])))
        reason_text = " / ".join(reasons) if reasons else "수동 검토 필요"
        metadata = {
            "review_required": True,
            "review_status": "PENDING",
            "review_location": str(
                source
                or item.get("review_location", "")
                or item.get("review_source", "")
                or item.get("detected_by", "")
                or "-"
            ).strip()
            or "-",
            "review_reason": reason_text,
            "review_checked_at": now_text(),
            "missed_buy_signal_count": safe_int_value(
                item.get("missed_buy_signal_count"),
                0,
            ),
            "missed_sell_signal_count": safe_int_value(
                item.get("missed_sell_signal_count"),
                0,
            ),
            "last_checked_price": safe_float_value(
                item.get("current_price"),
                0.0,
            ),
            "last_checked_pnl_rate": str(item.get("pnl_rate_text", "-")),
        }
        resume_metadata = item.get("resume_metadata")
        if isinstance(resume_metadata, dict):
            metadata.update(resume_metadata)
        return self.update_stock_status(
            stock_dir,
            code,
            name,
            "REVIEW_REQUIRED",
            metadata,
            reason_text,
        )

    def update_stock_status(self, *args, **kwargs):
        return auto_trade_update_stock_status(self, *args, **kwargs)

    def recalculate_stock_status_by_operation_policy(self, *args, **kwargs):
        return auto_trade_recalculate_stock_status_by_operation_policy(
            self,
            *args,
            **kwargs,
        )

    def rebind_startup_recovery_after_trusted_runtime_update(self):
        callback = getattr(
            self._owner,
            "rebind_startup_recovery_after_trusted_runtime_update",
            None,
        )
        return callback() if callable(callback) else False

    def stop_risk_parts(self, stock_dir: Path) -> list[str]:
        state = read_json_dict(stock_dir / "state.json")
        try:
            holding_qty = int(state.get("holding_qty", 0) or 0)
        except (TypeError, ValueError):
            holding_qty = 0
        pending_exists, pending_qty = pending_order_summary(stock_dir, state)
        parts = []
        if holding_qty > 0:
            parts.append(f"보유 {holding_qty:,}주")
        if pending_exists:
            parts.append(f"미체결 {pending_qty:,}주")
        return parts
