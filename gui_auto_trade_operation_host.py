# -*- coding: utf-8 -*-
"""Widget-free operation host shared by production GUI callers."""

from __future__ import annotations

from collections import OrderedDict, deque
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
from gui_auto_trade_runtime import now_text
from gui_auto_trade_runtime import get_stock_dirs_in_routine
from gui_base_stock_service import read_base_stocks
from gui_routine_registry import get_routine_dirs
from gui_auto_trade_status_ops import (
    auto_trade_recalculate_all_status_by_operation_policy,
    auto_trade_recalculate_stock_status_by_operation_policy,
    auto_trade_update_stock_operation_mode,
    auto_trade_update_stock_status,
)
from gui_auto_trade_timer import auto_trade_current_runtime_file_signature
from candle_manager import canonical_candle_content_hash, load_candles
from execution_universe import project_execution_universe
from kiwoom_realtime_shadow import (
    NO_CANONICAL_BAR,
    RealtimeShadowBar,
    compare_shadow_bar_to_canonical,
)
from gui_review_utils import (
    build_review_required_item,
    safe_float_value,
    safe_int_value,
)
from runtime_io import read_json_dict
from state_policy import auto_trade_status_display
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

    MAX_PENDING_SHADOW_COMPARISONS = 512

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
        self._operation_candle_requests: dict[str, dict[str, object]] = {}
        self._bar_commit_trigger_queue: deque[dict[str, object]] = deque()
        self._bar_commit_drain_scheduled = False
        self._bar_commit_drain_running = False
        self._last_bar_commit_identity_by_stock: dict[str, str] = {}
        self._last_bar_commit_fast_path_result: dict[str, object] = {}
        self._bar_committed_signal_bound = False
        self._bind_bar_committed_signal_once()
        self._realtime_shadow_signal_bound = False
        self._shadow_canonical_signal_bound = False
        self._realtime_shadow_trigger_queue: deque[dict[str, object]] = deque()
        self._realtime_shadow_retry_codes: set[str] = set()
        self._realtime_shadow_drain_scheduled = False
        self._realtime_shadow_drain_running = False
        self._pending_shadow_comparisons: OrderedDict[
            tuple[str, str], RealtimeShadowBar
        ] = OrderedDict()
        self._last_realtime_shadow_comparison: dict[str, object] = {}
        self._realtime_shadow_session_identity: tuple[int, str] = (0, "")
        self._bind_realtime_shadow_signals_once()

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
        return result

    def stop_operation_timers(self) -> dict[str, object]:
        from production_recovery_timer_lifecycle import stop_recovery_bound_timers

        self.clear_realtime_shadow_registration()
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
        self._operation_candle_requests.clear()
        self._bar_commit_trigger_queue.clear()
        self._realtime_shadow_trigger_queue.clear()
        self._realtime_shadow_retry_codes.clear()
        self._pending_shadow_comparisons.clear()
        return self.stop_operation_timers()

    def operation_timer(self) -> QTimer:
        return self._operation_timer

    def _bind_bar_committed_signal_once(self) -> bool:
        if self._bar_committed_signal_bound:
            return True
        signal = getattr(self._kiwoom_api(), "bar_committed", None)
        connect = getattr(signal, "connect", None)
        if not callable(connect):
            return False
        try:
            connect(self._on_bar_committed)
        except Exception as exc:
            observe_production_exception(
                type(exc),
                exc,
                exc.__traceback__,
                component="bar_commit_fast_path",
                operation="bind_bar_committed",
                source="gui_auto_trade_operation_host.AutoTradeOperationHost._bind_bar_committed_signal_once",
                target_type="OPERATION_HOST",
                target_id="main_operation_host",
                target_name="BAR_COMMITTED fast path",
                reason_code="BAR_COMMITTED_BIND_FAILED",
                owner=self,
                failure_scope="bar_committed_signal_binding",
            )
            return False
        self._bar_committed_signal_bound = True
        return True

    def _bind_realtime_shadow_signals_once(self) -> bool:
        api = self._kiwoom_api()
        shadow_signal = getattr(api, "realtime_shadow_bar_completed", None)
        shadow_connect = getattr(shadow_signal, "connect", None)
        if not self._realtime_shadow_signal_bound and callable(shadow_connect):
            try:
                shadow_connect(self._on_realtime_shadow_bar_completed)
            except Exception as exc:
                self._observe_realtime_shadow_exception(exc, "bind_shadow_bar_signal")
            else:
                self._realtime_shadow_signal_bound = True
        self._shadow_canonical_signal_bound = self._bar_committed_signal_bound
        return bool(
            self._realtime_shadow_signal_bound
            and self._shadow_canonical_signal_bound
        )

    def sync_realtime_shadow_targets(self, snapshot=None) -> dict[str, object]:
        """Sync only current execution-ready codes; failure is diagnostic-only."""

        try:
            universe = snapshot or project_execution_universe(self)
            target_codes = tuple(universe.execution_stock_codes)
            sync = getattr(
                self._kiwoom_api(),
                "sync_realtime_shadow_registration",
                None,
            )
            if not callable(sync):
                return {
                    "ok": False,
                    "changed": False,
                    "active": False,
                    "reason_code": "REALTIME_SHADOW_API_UNAVAILABLE",
                }
            result = sync(target_codes)
            registration = result.get("snapshot") if isinstance(result, dict) else None
            identity = (
                int(getattr(registration, "connection_epoch", 0) or 0),
                str(getattr(registration, "login_session_id", "") or ""),
            )
            if identity != self._realtime_shadow_session_identity:
                self._pending_shadow_comparisons.clear()
                self._realtime_shadow_trigger_queue.clear()
                self._realtime_shadow_retry_codes.clear()
                self._realtime_shadow_session_identity = identity
            return dict(result) if isinstance(result, dict) else {
                "ok": False,
                "changed": False,
                "active": False,
                "reason_code": "REALTIME_SHADOW_RESULT_MALFORMED",
            }
        except Exception as exc:
            self._observe_realtime_shadow_exception(exc, "sync_targets")
            return {
                "ok": False,
                "changed": False,
                "active": False,
                "reason_code": "REALTIME_SHADOW_SYNC_FAILED",
                "error": str(exc),
            }

    def clear_realtime_shadow_registration(self) -> dict[str, object]:
        clear = getattr(
            self._kiwoom_api(),
            "clear_realtime_shadow_registration",
            None,
        )
        self._pending_shadow_comparisons.clear()
        self._realtime_shadow_trigger_queue.clear()
        self._realtime_shadow_retry_codes.clear()
        if not callable(clear):
            return {
                "ok": True,
                "changed": False,
                "active": False,
                "reason_code": "REALTIME_SHADOW_API_UNAVAILABLE",
            }
        try:
            return clear(reason="OPERATION_HOST_STOPPED")
        except Exception as exc:
            self._observe_realtime_shadow_exception(exc, "clear_registration")
            return {
                "ok": False,
                "changed": False,
                "active": False,
                "reason_code": "REALTIME_SHADOW_CLEAR_FAILED",
                "error": str(exc),
            }

    def _on_realtime_shadow_bar_completed(self, payload: object) -> None:
        if self._shutting_down or not isinstance(payload, dict):
            return
        required = (
            "stock_code",
            "trade_date",
            "bar_time",
            "open",
            "high",
            "low",
            "close",
            "first_tick_time",
            "last_tick_time",
            "connection_epoch",
            "login_session_id",
        )
        if any(payload.get(field) in (None, "") for field in required):
            return
        self._realtime_shadow_trigger_queue.append(dict(payload))
        self._schedule_realtime_shadow_drain()

    def _on_canonical_bar_for_shadow_comparison(self, payload: object) -> None:
        if self._shutting_down or not isinstance(payload, dict):
            return
        if (
            payload.get("event_type") != "BAR_COMMITTED"
            or payload.get("source") != "opt10080"
        ):
            return
        stock_code = str(payload.get("stock_code") or "").strip()
        if not stock_code:
            return
        if not any(key[0] == stock_code for key in self._pending_shadow_comparisons):
            return
        self._realtime_shadow_retry_codes.add(stock_code)
        self._schedule_realtime_shadow_drain()

    def _schedule_realtime_shadow_drain(self) -> None:
        if self._realtime_shadow_drain_scheduled or self._realtime_shadow_drain_running:
            return
        self._realtime_shadow_drain_scheduled = True
        QTimer.singleShot(0, self._drain_realtime_shadow_comparisons)

    def _drain_realtime_shadow_comparisons(self) -> None:
        self._realtime_shadow_drain_scheduled = False
        if self._shutting_down or self._realtime_shadow_drain_running:
            return
        self._realtime_shadow_drain_running = True
        try:
            while self._realtime_shadow_trigger_queue and not self._shutting_down:
                payload = self._realtime_shadow_trigger_queue.popleft()
                try:
                    bar = RealtimeShadowBar(**payload)
                    if not self._shadow_bar_matches_current_session(bar):
                        continue
                    self._compare_or_pend_realtime_shadow_bar(bar)
                except Exception as exc:
                    self._observe_realtime_shadow_exception(
                        exc,
                        "compare_completed_bar",
                        stock_code=str(payload.get("stock_code") or ""),
                    )

            retry_codes = tuple(self._realtime_shadow_retry_codes)
            self._realtime_shadow_retry_codes.clear()
            for stock_code in retry_codes:
                for key, bar in tuple(self._pending_shadow_comparisons.items()):
                    if key[0] != stock_code:
                        continue
                    if not self._shadow_bar_matches_current_session(bar):
                        self._pending_shadow_comparisons.pop(key, None)
                        continue
                    try:
                        self._compare_or_pend_realtime_shadow_bar(bar)
                    except Exception as exc:
                        self._observe_realtime_shadow_exception(
                            exc,
                            "retry_pending_comparison",
                            stock_code=stock_code,
                        )
        finally:
            self._realtime_shadow_drain_running = False
            if (
                self._realtime_shadow_trigger_queue
                or self._realtime_shadow_retry_codes
            ) and not self._realtime_shadow_drain_scheduled:
                self._schedule_realtime_shadow_drain()

    def _shadow_bar_matches_current_session(self, bar: RealtimeShadowBar) -> bool:
        snapshot_getter = getattr(
            self._kiwoom_api(),
            "realtime_shadow_registration_snapshot",
            None,
        )
        if not callable(snapshot_getter):
            return False
        snapshot = snapshot_getter()
        return bool(
            getattr(snapshot, "active", False)
            and bar.stock_code in getattr(snapshot, "target_stock_codes", ())
            and bar.connection_epoch == getattr(snapshot, "connection_epoch", -1)
            and bar.login_session_id == getattr(snapshot, "login_session_id", "")
        )

    def _compare_or_pend_realtime_shadow_bar(
        self,
        bar: RealtimeShadowBar,
    ) -> dict[str, object]:
        stock_dir = StockRepository().resolve_stock_dir(bar.stock_code)
        candles = load_candles(stock_dir)
        content_hash = canonical_candle_content_hash(candles) if candles else ""
        comparison = compare_shadow_bar_to_canonical(
            bar,
            candles,
            canonical_content_hash=content_hash,
        )
        result = comparison.to_payload()
        self._last_realtime_shadow_comparison = result
        key = (bar.stock_code, bar.minute_key)
        if comparison.status == NO_CANONICAL_BAR:
            self._pending_shadow_comparisons[key] = bar
            self._pending_shadow_comparisons.move_to_end(key)
            while len(self._pending_shadow_comparisons) > self.MAX_PENDING_SHADOW_COMPARISONS:
                self._pending_shadow_comparisons.popitem(last=False)
        else:
            self._pending_shadow_comparisons.pop(key, None)
            self.realtime_shadow_comparison_completed.emit(dict(result))
        return result

    def _observe_realtime_shadow_exception(
        self,
        exc: Exception,
        operation: str,
        *,
        stock_code: str = "",
    ) -> None:
        observe_production_exception(
            type(exc),
            exc,
            exc.__traceback__,
            component="realtime_shadow",
            operation=operation,
            source="gui_auto_trade_operation_host.AutoTradeOperationHost",
            target_type="MARKET_DATA",
            target_id=stock_code or "realtime_shadow",
            target_name=stock_code or "Realtime shadow",
            reason_code="REALTIME_SHADOW_DIAGNOSTIC_FAILED",
            owner=self,
            failure_scope=f"realtime_shadow:{operation}:{stock_code}",
        )

    def register_operation_candle_request(
        self,
        rqname: str,
        *,
        stock_code: str,
        stock_name: str,
        stock_dir: Path,
        operation_cycle_minute_key: str,
    ) -> bool:
        clean_rqname = str(rqname or "").strip()
        clean_code = str(stock_code or "").strip()
        minute_key = str(operation_cycle_minute_key or "").strip()
        if self._shutting_down or not clean_rqname or not clean_code or not minute_key:
            return False
        self._operation_candle_requests[clean_rqname] = {
            "stock_code": clean_code,
            "stock_name": str(stock_name or "").strip(),
            "stock_dir": Path(stock_dir),
            "operation_cycle_minute_key": minute_key,
        }
        return True

    def complete_operation_candle_request(self, rqname: str) -> bool:
        return self._operation_candle_requests.pop(str(rqname or "").strip(), None) is not None

    @staticmethod
    def _valid_bar_committed_payload(payload: object) -> bool:
        if not isinstance(payload, dict):
            return False
        if payload.get("event_type") != "BAR_COMMITTED" or payload.get("source") != "opt10080":
            return False
        timeframe = payload.get("timeframe_minutes")
        if isinstance(timeframe, bool) or timeframe != 1:
            return False
        return all(
            str(payload.get(field) or "").strip()
            for field in (
                "stock_code",
                "rqname",
                "commit_identity",
                "canonical_content_hash",
                "canonical_path",
                "bar_key",
                "bar_identity",
                "bar_time",
            )
        )

    def _on_bar_committed(self, payload: object) -> None:
        """Validate and enqueue only; broker callback work ends here."""
        self._on_canonical_bar_for_shadow_comparison(payload)
        if self._shutting_down or not self._valid_bar_committed_payload(payload):
            return
        event = dict(payload)
        rqname = str(event.get("rqname") or "").strip()
        context = self._operation_candle_requests.get(rqname)
        if not isinstance(context, dict):
            return
        stock_code = str(event.get("stock_code") or "").strip()
        if stock_code != str(context.get("stock_code") or "").strip():
            return
        expected_path = (Path(context["stock_dir"]) / "candles.json").resolve()
        try:
            event_path = Path(str(event.get("canonical_path") or "")).resolve()
        except Exception:
            return
        if event_path != expected_path:
            return

        self._operation_candle_requests.pop(rqname, None)
        commit_identity = str(event.get("commit_identity") or "").strip()
        if self._last_bar_commit_identity_by_stock.get(stock_code) == commit_identity:
            return
        self._last_bar_commit_identity_by_stock[stock_code] = commit_identity
        self._bar_commit_trigger_queue.append({"event": event, "context": context})
        if not self._bar_commit_drain_scheduled and not self._bar_commit_drain_running:
            self._bar_commit_drain_scheduled = True
            QTimer.singleShot(0, self._drain_bar_commit_triggers)

    def _drain_bar_commit_triggers(self) -> None:
        self._bar_commit_drain_scheduled = False
        if self._bar_commit_drain_running or self._shutting_down:
            return
        self._bar_commit_drain_running = True
        try:
            while self._bar_commit_trigger_queue and not self._shutting_down:
                trigger = self._bar_commit_trigger_queue.popleft()
                try:
                    result = self._process_bar_commit_trigger(trigger)
                except Exception as exc:
                    event = trigger.get("event", {}) if isinstance(trigger, dict) else {}
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
        event = trigger.get("event") if isinstance(trigger, dict) else None
        context = trigger.get("context") if isinstance(trigger, dict) else None
        if not isinstance(event, dict) or not isinstance(context, dict):
            return self._bar_commit_result(event, reason_code="MALFORMED_BAR_COMMIT_TRIGGER")

        stock_code = str(event.get("stock_code") or "").strip()
        stock_name = str(context.get("stock_name") or "").strip()
        stock_dir = StockRepository().resolve_stock_dir(stock_code, stock_name)
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
        }
        probe_result = probe_execution_stock_for_committed_bar(
            self,
            stock_dir,
            str(context.get("operation_cycle_minute_key") or ""),
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
        if self._shutting_down:
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
