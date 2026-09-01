# -*- coding: utf-8 -*-
"""Widget-free operation host shared by production GUI callers."""

from __future__ import annotations

from collections import deque
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
    operator_review_location,
    operator_review_reason,
)
from gui_auto_trade_runtime import all_group_stock_dirs, now_text
from gui_base_stock_service import read_base_stocks
from gui_auto_trade_status_ops import (
    auto_trade_recalculate_all_status_by_operation_policy,
    auto_trade_recalculate_stock_status_by_operation_policy,
    auto_trade_update_stock_operation_mode,
    auto_trade_update_stock_status,
)
from candle_manager import canonical_candle_content_hash, load_candles
from execution_universe import project_execution_universe
from gui_market_data_host import MarketDataHost
from gui_review_utils import (
    build_review_required_item,
    safe_float_value,
    safe_int_value,
)
from gui_auto_trade_policy import auto_trade_setting_start_target_decision
from runtime_io import read_json_dict
from state_policy import auto_trade_status_display
from stock_code_contract import normalize_stock_code
from stock_repository import StockRepository
from event_journal_production import (
    append_production_event,
    observe_owner_failure_transition,
    observe_production_exception,
)


LOGGER = logging.getLogger(__name__)


class AutoTradeOperationHost(QObject):
    """Expose explicit production operations without constructing a GUI window."""

    operation_cycle_completed = pyqtSignal(dict)
    realtime_shadow_comparison_completed = pyqtSignal(object)

    def __init__(self, owner) -> None:
        super().__init__(owner if isinstance(owner, QObject) else None)
        self._owner = owner
        self._operation_timer = QTimer(self)
        self._operation_timer.setInterval(10_000)
        self._operation_timer.timeout.connect(self.run_operation_cycle)
        self._last_time_policy_minute_key = ""
        self._operation_cycle_running = False
        self._shutting_down = False
        self._factory_reset_quiesced = False
        self._last_start_target_block_details = []
        self._current_session_operation_participant_stock_codes: set[str] = set()
        recovery_gate = getattr(self._owner, "production_recovery_gate_for_stock", None)
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
                routine_dirs=lambda: [],
                stock_dirs_in_routine=lambda _routine_dir: [],
                base_stocks=lambda: read_base_stocks(),
                order_queue_path=lambda: ORDER_QUEUE_PATH,
                order_executions_path=lambda: ORDER_EXECUTIONS_PATH,
                order_locks_path=lambda: ORDER_LOCKS_PATH,
                confirm_runtime_file_init=None,
                all_group_stock_dirs=all_group_stock_dirs,
                current_orderable_cash=lambda: (
                    self._owner.current_orderable_cash_for_budget()
                    if callable(getattr(self._owner, "current_orderable_cash_for_budget", None))
                    else None
                ),
                fresh_current_price=lambda stock_code: (
                    getattr(
                        self.fresh_monitoring_market_information_state(stock_code),
                        "last_price",
                        None,
                    )
                ),
                production_recovery_gate_for_stock=(
                    (
                        lambda stock_code, caller_name: recovery_gate(
                            stock_code,
                            caller_name=caller_name,
                        )
                    )
                    if callable(recovery_gate)
                    else None
                ),
            )
        )
        self._bar_commit_trigger_queue: deque[dict[str, object]] = deque()
        self._bar_commit_drain_scheduled = False
        self._bar_commit_drain_running = False
        self._last_bar_commit_fast_path_result: dict[str, object] = {}
        self._market_data_host = MarketDataHost(
            self,
            self._kiwoom_api(),
            self._market_data_execution_entry,
        )
        self._market_data_ready_signal_bound = False
        self._market_data_comparison_signal_bound = False
        self._bind_market_data_host_signals_once()

    def parent(self):
        return self._owner

    def current_session_operation_participant_stock_codes(self) -> tuple[str, ...]:
        """Return an immutable snapshot of this process's explicit participants."""

        return tuple(sorted(self._current_session_operation_participant_stock_codes))

    def is_current_session_operation_participant(self, stock_code: object) -> bool:
        code = normalize_stock_code(stock_code)
        return bool(
            code and code in self._current_session_operation_participant_stock_codes
        )

    def register_current_session_operation_participants(
        self,
        stock_codes,
    ) -> tuple[str, ...]:
        registered = {
            normalize_stock_code(code)
            for code in stock_codes
            if normalize_stock_code(code)
        }
        if registered:
            self._current_session_operation_participant_stock_codes.update(registered)
        return tuple(sorted(registered))

    def retire_current_session_operation_participants(
        self,
        stock_codes,
    ) -> dict[str, tuple[str, ...]]:
        requested = tuple(
            sorted(
                {
                    normalize_stock_code(code)
                    for code in stock_codes
                    if normalize_stock_code(code)
                }
            )
        )
        before = self.current_session_operation_participant_stock_codes()
        requested_set = set(requested)
        removed = tuple(code for code in before if code in requested_set)
        if removed:
            self._current_session_operation_participant_stock_codes.difference_update(
                removed
            )
        return {
            "before": before,
            "requested": requested,
            "removed": removed,
            "remaining": self.current_session_operation_participant_stock_codes(),
        }

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
        if self._shutting_down or self._factory_reset_quiesced:
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
            recovery_session_id = str(
                getattr(identity, "recovery_session_id", "") or ""
            ).strip()
            append_production_event(
                "OPERATION_HOST_STARTED",
                result="SUCCESS",
                source="AutoTradeOperationHost.start_after_recovery",
                target_type="OPERATION_HOST",
                target_id="main_operation_host",
                reason_code=str(result.get("reason_code") or ""),
                **(
                    {"correlation_id": recovery_session_id}
                    if recovery_session_id
                    else {}
                ),
            )
        if result.get("started") is True:
            try:
                snapshot = project_execution_universe(self)
                result["immediate_realtime_shadow_result"] = (
                    self.sync_realtime_shadow_targets(snapshot)
                )
            except Exception as exc:
                LOGGER.exception("Immediate realtime shadow sync failed")
                result["immediate_realtime_shadow_result"] = {
                    "ok": False,
                    "changed": False,
                    "active": False,
                    "reason_code": "REALTIME_SHADOW_SYNC_FAILED",
                    "error": str(exc),
                }
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
        self._market_data_host.shutdown()
        self._bar_commit_trigger_queue.clear()
        return self.stop_operation_timers()

    def operation_timer(self) -> QTimer:
        return self._operation_timer

    def market_data_host(self) -> MarketDataHost:
        return self._market_data_host

    def _market_data_execution_entry(self, stock_code: str):
        stock_dir = StockRepository().resolve_stock_dir(str(stock_code or "").strip())
        snapshot = project_execution_universe(self, stock_dirs=[stock_dir])
        return snapshot.entries[0] if snapshot.entries else None

    def _bind_market_data_host_signals_once(self) -> bool:
        if not self._market_data_ready_signal_bound:
            self._market_data_host.canonical_bar_ready_for_operation.connect(
                self._on_canonical_bar_ready_for_operation
            )
            self._market_data_ready_signal_bound = True
        if not self._market_data_comparison_signal_bound:
            self._market_data_host.realtime_shadow_comparison_completed.connect(
                self._on_market_data_comparison_completed
            )
            self._market_data_comparison_signal_bound = True
        return True

    def _on_market_data_comparison_completed(self, payload: object) -> None:
        if not self._shutting_down and not self._factory_reset_quiesced and isinstance(payload, dict):
            event = dict(payload)
            QTimer.singleShot(0, lambda: self._emit_market_data_comparison(event))

    def _emit_market_data_comparison(self, payload: dict[str, object]) -> None:
        if self._shutting_down or self._factory_reset_quiesced:
            return
        try:
            self.realtime_shadow_comparison_completed.emit(dict(payload))
        except Exception:
            LOGGER.exception("Market-data comparison notification failed")

    def _on_canonical_bar_ready_for_operation(self, payload: object) -> None:
        """Queue a normalized market-data event for the trading boundary."""
        if self._shutting_down or self._factory_reset_quiesced or not isinstance(payload, dict):
            return
        required = (
            "stock_code",
            "stock_dir",
            "source",
            "evaluation_tick_key",
            "commit_identity",
            "canonical_content_hash",
            "canonical_path",
            "bar_key",
            "bar_identity",
            "bar_time",
        )
        if any(not str(payload.get(field) or "").strip() for field in required):
            return
        self._bar_commit_trigger_queue.append(dict(payload))
        if not self._bar_commit_drain_scheduled and not self._bar_commit_drain_running:
            self._bar_commit_drain_scheduled = True
            QTimer.singleShot(0, self._drain_bar_commit_triggers)

    # Compatibility forwarding only. Production orchestration resolves MarketDataHost directly.
    def _bind_bar_committed_signal_once(self) -> bool:
        return self._bind_market_data_host_signals_once()

    def _bind_realtime_shadow_signals_once(self) -> bool:
        return self._bind_market_data_host_signals_once()

    def sync_realtime_shadow_targets(self, snapshot=None) -> dict[str, object]:
        universe = snapshot or project_execution_universe(self)
        return self._market_data_host.sync_targets(universe)

    def retire_time_ended_current_session_participants(
        self,
        *,
        now_dt=None,
    ) -> dict[str, object]:
        """Own final-time participant retirement and execution-shadow cleanup."""

        from gui_auto_trade_run_control import (
            auto_trade_retire_time_ended_current_session_participants,
        )

        result = auto_trade_retire_time_ended_current_session_participants(
            self,
            now_dt=now_dt,
            order_queue_path=ORDER_QUEUE_PATH,
            order_executions_path=ORDER_EXECUTIONS_PATH,
            order_locks_path=ORDER_LOCKS_PATH,
        )
        removed = tuple(result.get("removed", ()))
        before = tuple(result.get("before", ()))
        if not removed and before:
            return result

        snapshot = project_execution_universe(self)
        result["execution_universe_snapshot"] = snapshot
        try:
            sync_result = self.sync_realtime_shadow_targets(snapshot)
        except Exception as exc:
            LOGGER.exception("Time-end execution shadow sync failed")
            sync_result = {
                "ok": False,
                "changed": False,
                "active": False,
                "reason_code": "REALTIME_SHADOW_SYNC_FAILED",
                "error": str(exc),
            }
        result["execution_shadow_sync_result"] = sync_result
        if (
            isinstance(sync_result, dict)
            and sync_result.get("ok") is True
            and not tuple(result.get("remaining", ()))
        ):
            result["operation_timer_stop_result"] = self.stop_operation_timers()
        return result

    def sync_monitoring_universe_for_current_session(self) -> dict[str, object]:
        """Sync all current registered Stocks without evaluating operation readiness."""

        try:
            projection = StockRepository().realtime_monitoring_universe()
            result = self._market_data_host.sync_monitoring_targets(
                projection.target_stock_codes
            )
            response = dict(result) if isinstance(result, dict) else {}
            response.update(
                monitoring_target_stock_codes=projection.target_stock_codes,
                unsupported_stock_codes=projection.unsupported_stock_codes,
                source_record_count=projection.source_record_count,
            )
            return response
        except Exception as exc:
            LOGGER.exception("Realtime monitoring universe sync failed")
            return {
                "ok": False,
                "changed": False,
                "active": False,
                "reason_code": "REALTIME_MONITORING_UNIVERSE_SYNC_FAILED",
                "error": str(exc),
            }

    def clear_realtime_shadow_registration(self) -> dict[str, object]:
        return self._market_data_host.clear()

    def market_data_mode_snapshot(self, stock_code: str):
        return self._market_data_host.mode_snapshot(stock_code)

    def price_signal_observation_enabled(self) -> bool:
        return self._market_data_host.price_signal_observation_enabled()

    def set_price_signal_observation_enabled(self, enabled: object) -> bool:
        return self._market_data_host.set_price_signal_observation_enabled(enabled)

    def high_resolution_market_state(self, stock_code: str):
        return self._market_data_host.high_resolution_market_state(stock_code)

    def monitoring_market_information_state(self, stock_code: str):
        return self._market_data_host.monitoring_market_information_state(stock_code)

    def fresh_monitoring_market_information_state(self, stock_code: str):
        return self._market_data_host.fresh_monitoring_market_information_state(stock_code)

    def configuration_market_information_state(self, stock_code: str):
        return self._market_data_host.configuration_market_information_state(stock_code)

    def high_resolution_market_data_snapshot(self):
        return self._market_data_host.high_resolution_market_data_snapshot()

    def tr_governor_metrics_snapshot(self):
        getter = getattr(self.kiwoom_api, "tr_governor_metrics_snapshot", None)
        return getter() if callable(getter) else None

    def prepare_market_data_operation_cycle(self, snapshot, minute_key: str) -> dict[str, object]:
        return self._market_data_host.prepare_operation_cycle(snapshot, minute_key)

    def market_data_refresh_decision(self, stock_code: str, minute_key: str) -> dict[str, object]:
        return self._market_data_host.market_data_refresh_decision(stock_code, minute_key)

    def complete_realtime_reconciliation(
        self,
        stock_code: str,
        minute_key: str,
        stock_dir: Path,
        result: object,
    ) -> bool:
        return self._market_data_host.complete_reconciliation(
            stock_code,
            minute_key,
            stock_dir,
            result,
        )

    def register_operation_candle_request(self, rqname: str, **kwargs) -> bool:
        return self._market_data_host.register_operation_candle_request(rqname, **kwargs)

    def complete_operation_candle_request(self, rqname: str) -> bool:
        return self._market_data_host.complete_operation_candle_request(rqname)

    def _on_bar_committed(self, payload: object) -> None:
        self._market_data_host._on_bar_committed(payload)

    def _drain_bar_commit_triggers(self) -> None:
        self._bar_commit_drain_scheduled = False
        if self._bar_commit_drain_running or self._shutting_down or self._factory_reset_quiesced:
            return
        self._bar_commit_drain_running = True
        try:
            while self._bar_commit_trigger_queue and not self._shutting_down and not self._factory_reset_quiesced:
                trigger = self._bar_commit_trigger_queue.popleft()
                try:
                    result = self._process_bar_commit_trigger(trigger)
                except Exception as exc:
                    event = trigger if isinstance(trigger, dict) else {}
                    observe_production_exception(
                        type(exc),
                        exc,
                        exc.__traceback__,
                        component="bar_commit_fast_path",
                        operation="drain_bar_commit_trigger",
                        source="gui_auto_trade_operation_host.AutoTradeOperationHost._drain_bar_commit_triggers",
                        target_type="STOCK",
                        target_id=str(event.get("stock_code") or ""),
                        target_name=str(event.get("stock_name") or event.get("stock_code") or ""),
                        reason_code="BAR_COMMIT_FAST_PATH_FAILED",
                        owner=self,
                        failure_scope=f"bar_commit_fast_path:{event.get('stock_code', '')}",
                    )
                    result = {
                        "accepted": True,
                        "evaluated": False,
                        "stock_code": str(event.get("stock_code") or ""),
                        "commit_identity": str(event.get("commit_identity") or ""),
                        "signal": "ERROR",
                        "queue_status": "",
                        "reason_code": "BAR_COMMIT_FAST_PATH_FAILED",
                    }
                self._last_bar_commit_fast_path_result = result
        finally:
            self._bar_commit_drain_running = False
            if self._bar_commit_trigger_queue and not self._bar_commit_drain_scheduled:
                self._bar_commit_drain_scheduled = True
                QTimer.singleShot(0, self._drain_bar_commit_triggers)

    def _process_bar_commit_trigger(self, trigger: dict[str, object]) -> dict[str, object]:
        event = trigger if isinstance(trigger, dict) else None
        if not isinstance(event, dict):
            return self._bar_commit_result(event, reason_code="MALFORMED_BAR_COMMIT_TRIGGER")
        stock_code = str(event.get("stock_code") or "").strip()
        stock_name = str(event.get("stock_name") or "").strip()
        stock_dir = Path(str(event.get("stock_dir") or ""))
        expected_path = (stock_dir / "candles.json").resolve()
        if expected_path != Path(str(event.get("canonical_path") or "")).resolve():
            return self._bar_commit_result(event, reason_code="CANONICAL_CANDLE_PATH_MISMATCH")

        candles = load_candles(stock_dir)
        current_hash = canonical_candle_content_hash(candles)
        if not candles or current_hash != str(event.get("canonical_content_hash") or ""):
            return self._bar_commit_result(event, reason_code="SUPERSEDED_BAR_COMMIT")

        snapshot = project_execution_universe(self, stock_dirs=[stock_dir])
        entry = snapshot.entries[0] if snapshot.entries else None
        if entry is None or entry.execution_ready is not True:
            return self._bar_commit_result(event, reason_code="EXECUTION_NOT_READY")

        from gui_auto_trade_timer import _process_pending_signal_pipeline
        from routine_signal_probe import probe_execution_stock_for_committed_bar

        provenance = {
            "trigger_commit_identity": event["commit_identity"],
            "trigger_bar_key": event["bar_key"],
            "trigger_bar_identity": event["bar_identity"],
            "trigger_canonical_content_hash": event["canonical_content_hash"],
            "trigger_market_data_source": str(event.get("source") or ""),
        }
        probe_result = probe_execution_stock_for_committed_bar(
            self,
            stock_dir,
            str(event.get("evaluation_tick_key") or ""),
            trigger_provenance=provenance,
            execution_universe_snapshot=snapshot,
        )
        pipeline_result = _process_pending_signal_pipeline(self, snapshot)
        result = self._bar_commit_result(
            event,
            evaluated=True,
            signal=str(probe_result.get("signal") or ""),
            queue_status=str(probe_result.get("queue_status") or ""),
            reason_code="BAR_COMMIT_FAST_PATH_EVALUATED",
        )
        result["probe_result"] = probe_result
        result["pipeline_result"] = pipeline_result
        return result

    @staticmethod
    def _bar_commit_result(
        event: object,
        *,
        evaluated: bool = False,
        signal: str = "",
        queue_status: str = "",
        reason_code: str,
    ) -> dict[str, object]:
        payload = event if isinstance(event, dict) else {}
        return {
            "accepted": True,
            "evaluated": evaluated,
            "stock_code": str(payload.get("stock_code") or ""),
            "commit_identity": str(payload.get("commit_identity") or ""),
            "signal": signal,
            "queue_status": queue_status,
            "reason_code": reason_code,
        }

    def run_operation_cycle(self) -> dict[str, object]:
        """Run one operation cycle without depending on any window visibility."""
        if self._shutting_down or self._factory_reset_quiesced:
            return {"processed": False, "reason_code": "OPERATION_HOST_SHUTTING_DOWN"}
        bind_bar_committed = getattr(self, "_bind_bar_committed_signal_once", None)
        if callable(bind_bar_committed):
            bind_bar_committed()
        bind_realtime = getattr(self, "_bind_realtime_shadow_signals_once", None)
        if callable(bind_realtime):
            bind_realtime()
        if self._operation_cycle_running:
            return {"processed": False, "reason_code": "OPERATION_CYCLE_REENTRY"}

        from gui_auto_trade_timer import auto_trade_run_operation_cycle

        self._operation_cycle_running = True
        cycle_exception = None
        try:
            try:
                result = auto_trade_run_operation_cycle(self)
            except Exception as exc:
                cycle_exception = exc
                observe_production_exception(
                    type(exc),
                    exc,
                    exc.__traceback__,
                    component="operation_host",
                    operation="run_operation_cycle",
                    source="gui_auto_trade_operation_host.AutoTradeOperationHost.run_operation_cycle",
                    target_type="OPERATION_HOST",
                    target_id="main_operation_host",
                    target_name="자동매매 운영 주기",
                    reason_code="OPERATION_CYCLE_FAILED",
                    owner=self,
                    failure_scope="operation_cycle",
                )
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
            if cycle_exception is None:
                observe_owner_failure_transition(
                    self,
                    "operation_cycle",
                    active=False,
                )
            observe_owner_failure_transition(
                self,
                "operation_cycle_result",
                active=False,
            )
            signal_result = result.get("signal_result", {})
            deferred = (
                isinstance(signal_result, dict)
                and signal_result.get("deferred_for_candle_refresh") is True
            )
            if not deferred:
                self.operation_cycle_completed.emit(result)
            return result
        observe_owner_failure_transition(
            self,
            "operation_cycle_result",
            active=True,
            signature=type(result).__name__,
            event_type="INTEGRITY_WARNING",
            severity="ERROR",
            result="FAILED",
            source="gui_auto_trade_operation_host.AutoTradeOperationHost.run_operation_cycle",
            template_args={"target": "자동매매 운영 주기"},
            target_type="OPERATION_HOST",
            target_id="main_operation_host",
            target_name="자동매매 운영 주기",
            reason_code="INVALID_OPERATION_CYCLE_RESULT",
            component="operation_host",
            operation="run_operation_cycle",
            details={"result_type": type(result).__name__},
        )
        return {"processed": False, "reason_code": "INVALID_OPERATION_CYCLE_RESULT"}

    def complete_deferred_operation_cycle(self, result: dict[str, object]) -> None:
        """Emit the cycle boundary after asynchronous candle/signal work ends."""
        if self._shutting_down or self._factory_reset_quiesced or not isinstance(result, dict):
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
        targets = []
        skipped = []
        block_details = []
        for stock_dir, code, name in selected:
            if self.start_target_is_review_isolated(stock_dir, code):
                skipped.append(f"{code} {name}(검토종목)")
                block_details.append(
                    {
                        "stock_code": str(code),
                        "stock_name": str(name),
                        "reason": "REVIEW_REQUIRED",
                        "display_label": f"{code} {name}".strip(),
                    }
                )
                continue
            state = read_json_dict(Path(stock_dir) / "state.json")
            status = str(state.get("status", "STOPPED")).strip().upper() or "STOPPED"
            config = read_json_dict(Path(stock_dir) / "config.json")
            decision = auto_trade_setting_start_target_decision(
                self,
                state,
                code,
                config=config,
            )
            if decision.get("allowed") is True:
                targets.append((stock_dir, code, name))
            else:
                skipped.append(f"{code} {name}({auto_trade_status_display(status)})")
                block_details.append(
                    {
                        "stock_code": str(code),
                        "stock_name": str(name),
                        "reason": str(decision.get("reason") or "NOT_STARTABLE"),
                        "status": status,
                        "operation_mode": str(
                            decision.get("operation_mode") or ""
                        ),
                        "session_phase": dict(
                            decision.get("session_phase") or {}
                        ),
                        "display_label": f"{code} {name}".strip(),
                    }
                )
        self._last_start_target_block_details = block_details
        return targets, skipped

    def start_target_block_details(self):
        return tuple(
            dict(item)
            for item in self._last_start_target_block_details
            if isinstance(item, dict)
        )

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
        operator_reasons = unique_review_reasons(
            [operator_review_reason(reason) for reason in reasons]
        )
        reason_text = " / ".join(operator_reasons) or "수동 검토 필요"
        metadata = {
            "review_required": True,
            "review_status": "PENDING",
            "review_location": operator_review_location(str(
                source
                or item.get("review_location", "")
                or item.get("review_source", "")
                or item.get("detected_by", "")
                or "-"
            ).strip()
            or "-"),
            "review_reason": reason_text,
            "review_checked_at": now_text(),
            "last_checked_price": safe_float_value(
                item.get("current_price"),
                0.0,
            ),
            "last_checked_pnl_rate": str(item.get("pnl_rate_text", "-")),
        }
        raw_reason_text = " / ".join(reasons)
        if raw_reason_text and raw_reason_text != reason_text:
            metadata["review_detail"] = raw_reason_text
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
