# -*- coding: utf-8 -*-
"""Independent entry and replay flow for indicator signal Validation V2."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import pickle
import tempfile
import time
from threading import Event, Thread
import weakref

from PyQt5 import sip
from PyQt5.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt5.QtWidgets import QDialog, QWidget

from candle_timeframe_aggregation import SEOUL_TIMEZONE, candle_market_datetime
from gui_toast import show_toast
from gui_stock_data import STOCK_LIBRARY_READY, load_stock_library_snapshot
from gui_indicator_follow_signal_validation_window import (
    IndicatorFollowSignalValidationWindow,
    _calculation_bounds_for_display_range,
    _calculation_indexes_for_display,
    _calculation_indexes_for_mixed_display,
    _krx_regular_session_candles,
    prepare_signal_validation_presentation,
)
from indicator_follow_signal_validation_execution import ValidationVirtualPositionTracker
from indicator_follow_backtest_parity import (
    candle_projection_parity_report,
    compare_signal_replay_entries,
    emitted_signal_entries,
    execution_model_report,
    filter_signal_backtest_authorization_report,
    market_session_parity_report,
    production_decision_scope_report,
    require_signal_replay_parity,
    require_signal_rule_support,
    signal_replay_entries_cache_signature,
)
from indicator_follow_validation_production_session import (
    production_minute_source_retry_plan,
    project_production_calculation_candles,
    production_session_contract_for_stock,
    unavailable_production_session_contract,
)
from indicator_follow_signal_validation_historical_cache import (
    IndicatorFollowSignalValidationHistoricalCache,
    ValidationHistoricalCacheEntry,
    merge_validation_candles,
)
from indicator_follow_validation_timeframe import (
    normalize_validation_timeframe,
    validation_timeframe_from_rules,
)
from indicator_follow_validation_history_contract import (
    required_validation_history_context_bars,
)
from candle_manager import DEFAULT_CANDLES_MAX_COUNT
from gui_indicator_follow_validation_host import IndicatorFollowValidationHost
from indicator_follow_signal_validation_recent_stocks import (
    IndicatorFollowSignalValidationRecentStockStore,
)
from indicator_follow_signal_validation_preferences import (
    IndicatorFollowSignalValidationPreferenceStore,
)
from indicator_follow_signal_validation_runtime_trace import (
    signal_validation_trace_enabled,
    trace_candle_signature,
    trace_pool_summary,
    trace_production_contract_summary,
    trace_signal_validation_event,
)
from indicator_follow_signal_validation_projection import (
    IndicatorFollowSignalValidationApplyPayload,
    IndicatorFollowSignalValidationRunRequest,
    IndicatorFollowSignalValidationSeed,
    build_validation_average_price_context,
)
from routines.지표추종매매.routine_validation_contract import (
    ValidationRequest,
    ValidationSettingsSnapshot,
    ValidationStockRef,
)
from routines.지표추종매매.routine_validation_historical import (
    ValidationHistoricalProvider,
    ValidationHistoricalResult,
    ValidationHistoricalSnapshot,
)
from routines.지표추종매매.routine_validation_replay import project_validation_candles
from routines.지표추종매매.routine_validation_replay import (
    ValidationHistoricalReplay,
    ValidationReplayEntry,
    ValidationReplayResult,
    ValidationReplaySnapshot,
)
from routines.지표추종매매.routine_validation_session import ValidationSession
from stock_code_contract import normalize_broker_stock_code
from stock_code_contract import market_source_for_identity


DEFAULT_SIGNAL_VALIDATION_HISTORICAL_COUNT = 500
MAX_SIGNAL_VALIDATION_HISTORICAL_COUNT = 5_000
_HISTORY_EXTENSION_STEP = 500
_PERSISTENT_REFRESH_PROBE_COUNT = 300
_SHARED_SIGNAL_VALIDATION_HISTORICAL_POOLS: dict[
    tuple[int, str, str, str], dict[str, object]
] = {}
_OWNER_FLOW_ATTRIBUTE = "_indicator_follow_signal_validation_flow"
_HISTORY_CACHE_UNSET = object()
_CACHE_LEASE_UNSET = object()


def _trace_approved_krx_history(event: str, stock_code: str, timeframe: str, **details: object) -> None:
    """Optional bounded diagnostic for the approved 005930 M120/Y1 paths."""
    if (
        stock_code == "005930"
        and timeframe in {"M120", "Y1"}
        and signal_validation_trace_enabled()
    ):
        trace_signal_validation_event(
            "flow.validation.history." + event,
            stock_code=stock_code,
            timeframe=timeframe,
            **details,
        )


_MARKET_SNAPSHOT_FIELDS = (
    "current_price",
    "open_price",
    "high_price",
    "low_price",
    "change_rate",
    "execution_strength",
    "previous_day_volume_rate",
    "cumulative_trading_value",
    "cumulative_volume",
    "market_capitalization",
)


class IndicatorFollowSignalValidationFlow(QObject):
    """Own V2 picker/window lifetime and fresh read-only replay requests."""

    validation_failed = pyqtSignal(str)
    signal_scan_completed = pyqtSignal(object)
    range_replay_completed = pyqtSignal(object)
    history_browse_completed = pyqtSignal(object)
    history_cache_completed = pyqtSignal(object)
    history_fetch_completed = pyqtSignal(object)

    def __init__(
        self,
        broker: object,
        parent: QObject | None = None,
        *,
        host: IndicatorFollowValidationHost | None = None,
        operation_active_reader: Callable[[], bool] | None = None,
        historical_count: int = DEFAULT_SIGNAL_VALIDATION_HISTORICAL_COUNT,
        historical_provider_factory: Callable[..., object] = ValidationHistoricalProvider,
        replay_factory: Callable[..., object] = ValidationHistoricalReplay,
        window_factory: Callable[..., object] = IndicatorFollowSignalValidationWindow,
        recent_stock_store: object | None = None,
        preference_store: object | None = None,
        historical_cache: object | None = None,
        production_session_contract_reader: Callable[..., dict[str, object]] = (
            production_session_contract_for_stock
        ),
        full_parity_workers: int = 0,
    ) -> None:
        super().__init__(parent)
        if (
            isinstance(historical_count, bool)
            or not isinstance(historical_count, int)
            or historical_count <= 0
        ):
            raise ValueError("historical_count must be a positive integer")
        for name, factory in (
            ("historical_provider_factory", historical_provider_factory),
            ("replay_factory", replay_factory),
            ("window_factory", window_factory),
            ("production_session_contract_reader", production_session_contract_reader),
        ):
            if not callable(factory):
                raise TypeError(f"{name} must be callable")
        self._broker = broker
        self._operation_active_reader = operation_active_reader
        self._historical_count = historical_count
        self._historical_provider_factory = historical_provider_factory
        self._replay_factory = replay_factory
        self._window_factory = window_factory
        if (
            isinstance(full_parity_workers, bool)
            or not isinstance(full_parity_workers, int)
            or full_parity_workers < 0
            or full_parity_workers > 8
        ):
            raise ValueError("full_parity_workers must be 0..8")
        self._production_session_contract_reader = production_session_contract_reader
        self._full_parity_workers = full_parity_workers
        self._recent_stock_store = (
            recent_stock_store
            if recent_stock_store is not None
            else IndicatorFollowSignalValidationRecentStockStore()
        )
        self._preference_store = (
            preference_store
            if preference_store is not None
            else IndicatorFollowSignalValidationPreferenceStore()
        )
        self._historical_cache = (
            historical_cache
            if historical_cache is not None
            else IndicatorFollowSignalValidationHistoricalCache()
        )
        self._host = host if host is not None else IndicatorFollowValidationHost(
            parent,
            operation_active_reader=operation_active_reader,
        )
        restored = self._read_recent_stocks()
        self._last_selected_stock = restored[0] if restored else None
        self._bound_dialog_ids: set[int] = set()
        self._open_windows: dict[int, object] = {}
        self._request_generation: dict[int, int] = {}
        self._prepare_active: dict[int, object] = {}
        self._prepare_pending: dict[int, dict[str, object]] = {}
        self._prepare_deferred: dict[int, tuple[int, dict[str, object]]] = {}
        self._range_replay_generation: dict[int, int] = {}
        self._range_replay_active: dict[int, object] = {}
        self._range_replay_pending: dict[int, dict[str, object]] = {}
        self._range_replay_deferred: dict[int, tuple[int, dict[str, object]]] = {}
        self._active_run_identity: dict[int, tuple[object, ...]] = {}
        self._history_browse_active: dict[int, object] = {}
        self._history_browse_deferred: dict[int, tuple[int, dict[str, object]]] = {}
        self._market_snapshot_generation: dict[int, int] = {}
        self._market_snapshot_metadata: dict[int, tuple[str, dict[str, object]]] = {}
        self._active_providers: dict[tuple[int, int], object] = {}
        self._historical_pools: dict[int, dict[str, object]] = {}
        self._validation_sessions: dict[int, ValidationSession] = {}
        self._history_targets: dict[int, int] = {}
        self._history_extension_inflight: dict[int, dict[str, object]] = {}
        self._history_exhaustion: dict[int, tuple[object, ...]] = {}
        self._integrated_display_sidecars: dict[
            tuple[str, str, int], dict[str, object]
        ] = {}
        self._integrated_display_inflight: dict[
            tuple[str, str, int], int
        ] = {}
        self._last_run_requests: dict[int, IndicatorFollowSignalValidationRunRequest] = {}
        self._now_factory = lambda: datetime.now(SEOUL_TIMEZONE)
        take_evicted = getattr(self._recent_stock_store, "take_startup_evicted_codes", None)
        startup_evicted = (
            take_evicted() if callable(take_evicted)
            else getattr(self._recent_stock_store, "startup_evicted_codes", ())
        )
        for code in tuple(startup_evicted):
            self._purge_validation_history_for_stock(code)
        self.signal_scan_completed.connect(self._on_signal_scan_completed)
        self.range_replay_completed.connect(self._on_range_replay_completed)
        self.history_browse_completed.connect(self._on_history_browse_completed)
        self.history_cache_completed.connect(self._on_history_cache_completed)
        self.history_fetch_completed.connect(self._on_history_fetch_completed)
        self._host.validation_blocked.connect(self._forward_host_failure)

    @property
    def host(self) -> IndicatorFollowValidationHost:
        return self._host

    @property
    def open_windows(self) -> tuple[object, ...]:
        return tuple(self._open_windows.values())

    @property
    def last_selected_stock(self) -> ValidationStockRef | None:
        return self._last_selected_stock

    @property
    def recent_stocks(self) -> tuple[ValidationStockRef, ...]:
        return self._read_recent_stocks()

    def _read_recent_stocks(self) -> tuple[ValidationStockRef, ...]:
        try:
            stocks = self._recent_stock_store.recent_stocks
        except Exception:
            return ()
        return tuple(
            ValidationStockRef(stock.code, stock.name)
            for stock in stocks
            if isinstance(stock, ValidationStockRef) and stock.code and stock.name
        )

    def _metadata_for(self, stock: object) -> dict[str, object] | None:
        resolver = getattr(self._recent_stock_store, "metadata_for", None)
        if not callable(resolver):
            return None
        try:
            metadata = resolver(stock)
        except Exception:
            return None
        return dict(metadata) if isinstance(metadata, dict) else None

    def bind_dialog(self, dialog: object) -> bool:
        signal = getattr(dialog, "signal_validation_requested", None)
        connect = getattr(signal, "connect", None)
        if not callable(connect):
            return False
        key = id(dialog)
        if key in self._bound_dialog_ids:
            return True
        try:
            dialog_ref = weakref.ref(dialog)
        except TypeError:
            return False

        def start_request(
            seed: object,
            requester_ref: weakref.ReferenceType[object] = dialog_ref,
        ) -> object | None:
            return self._start_entry(seed, requester_ref())

        connect(start_request)
        self._bound_dialog_ids.add(key)
        destroyed = getattr(dialog, "destroyed", None)
        if callable(getattr(destroyed, "connect", None)):
            destroyed.connect(
                lambda _obj=None, dialog_key=key: self._bound_dialog_ids.discard(dialog_key)
            )
        return True

    def _server_authenticated(self) -> bool:
        checker = getattr(self._broker, "is_connected", None)
        if not callable(checker):
            return False
        try:
            return checker() is True
        except Exception:
            return False

    @staticmethod
    def _valid_requester(requester: object) -> bool:
        if not isinstance(requester, QWidget):
            return False
        try:
            requester.window()
        except RuntimeError:
            return False
        return True

    def _start_entry(self, seed: object, requester: object) -> object | None:
        if not isinstance(seed, IndicatorFollowSignalValidationSeed):
            self.validation_failed.emit("INVALID_SIGNAL_VALIDATION_SEED")
            return None
        if not self._valid_requester(requester):
            self.validation_failed.emit("INVALID_VALIDATION_REQUESTER")
            return None
        if not self._server_authenticated():
            try:
                show_toast(
                    requester,
                    "키움 서버에 로그인되어 있지 않습니다.",
                    duration_ms=2500,
                )
            except RuntimeError:
                pass
            self.validation_failed.emit("SERVER_NOT_CONNECTED")
            return None
        try:
            requester_ref = weakref.ref(requester)
        except TypeError:
            self.validation_failed.emit("INVALID_VALIDATION_REQUESTER")
            return None
        preflight = getattr(self._host, "preflight_block_reason", None)
        if not callable(preflight):
            self.validation_failed.emit("VALIDATION_PREFLIGHT_UNAVAILABLE")
            return
        block_reason = preflight(seed.settings_snapshot)
        if block_reason is not None:
            self.validation_failed.emit(str(block_reason or "VALIDATION_BLOCKED"))
            return None
        return self._open_signal_window(seed, requester_ref)

    def _open_signal_window(
        self,
        seed: IndicatorFollowSignalValidationSeed,
        source_ref: weakref.ReferenceType[object],
    ) -> object | None:
        try:
            window = self._window_factory(
                self._last_selected_stock,
                seed,
                parent=None,
            )
            self._sync_window_stock_projection(window)
            set_preference_store = getattr(
                window,
                "set_validation_preferences_store",
                None,
            )
            if callable(set_preference_store):
                set_preference_store(self._preference_store)
            set_historical_candle_count = getattr(
                window,
                "set_historical_candle_count",
                None,
            )
            if callable(set_historical_candle_count):
                set_historical_candle_count(self._historical_count)
            commit_entry_state = getattr(window, "commit_entry_state", None)
            if callable(commit_entry_state):
                commit_entry_state()
            if not callable(getattr(window, "show", None)):
                raise TypeError("signal validation window show is unavailable")
            run_signal = getattr(window, "validation_run_requested", None)
            if not callable(getattr(run_signal, "connect", None)):
                raise TypeError("signal validation run signal is unavailable")
            window_ref = weakref.ref(window)
            run_signal.connect(
                lambda snapshot, ref=window_ref: self._run_validation(ref(), snapshot)
            )
            range_signal = getattr(
                window,
                "validation_range_evaluation_requested",
                None,
            )
            if callable(getattr(range_signal, "connect", None)):
                range_signal.connect(
                    lambda start, end, ref=window_ref: (
                        self._run_cached_range_replay(ref(), start, end)
                    )
                )
            extension_signal = getattr(
                window,
                "historical_extension_requested",
                None,
            )
            if callable(getattr(extension_signal, "connect", None)):
                extension_signal.connect(
                    lambda ref=window_ref: self._request_history_extension(ref())
                )
            interaction_ended = getattr(window, "chart_interaction_ended", None)
            if callable(getattr(interaction_ended, "connect", None)):
                interaction_ended.connect(
                    lambda epoch, cancelled, ref=window_ref: (
                        self._on_chart_interaction_ended(
                            ref(), epoch, cancelled
                        )
                    )
                )
            input_invalidated = getattr(window, "chart_input_invalidated", None)
            if callable(getattr(input_invalidated, "connect", None)):
                input_invalidated.connect(
                    lambda ref=window_ref: self._invalidate_window_input(ref())
                )
            range_changed = getattr(window, "validation_range_changed", None)
            if callable(getattr(range_changed, "connect", None)):
                range_changed.connect(
                    lambda value, ref=window_ref: (
                        self._invalidate_window_range(ref(), value)
                    )
                )
            apply_signal = getattr(window, "settings_apply_requested", None)
            if not callable(getattr(apply_signal, "connect", None)):
                raise TypeError("signal validation apply signal is unavailable")
            apply_signal.connect(
                lambda payload, ref=window_ref, source=source_ref: self._apply_to_source(
                    ref(),
                    source,
                    payload,
                )
            )
            stock_signal = getattr(window, "stock_selection_requested", None)
            if not callable(getattr(stock_signal, "connect", None)):
                raise TypeError("stock selection request signal is unavailable")
            stock_signal.connect(
                lambda ref=window_ref: self._select_stock_for_window(ref())
            )
            recent_signal = getattr(window, "recent_stock_selected", None)
            if not callable(getattr(recent_signal, "connect", None)):
                raise TypeError("recent stock selection signal is unavailable")
            recent_signal.connect(
                lambda stock, ref=window_ref: self._activate_stock_for_window(
                    ref(),
                    stock,
                )
            )
            remove_signal = getattr(window, "recent_stock_remove_requested", None)
            if callable(getattr(remove_signal, "connect", None)):
                remove_signal.connect(
                    lambda stock, ref=window_ref: self._remove_recent_stock_for_window(
                        ref(),
                        stock,
                    )
                )
            if callable(getattr(window, "setAttribute", None)):
                window.setAttribute(Qt.WA_DeleteOnClose, True)
            key = id(window)
            self._open_windows[key] = window
            self._request_generation[key] = 0
            self._market_snapshot_generation[key] = 0
            self._history_targets[key] = self._historical_count
            destroyed = getattr(window, "destroyed", None)
            if callable(getattr(destroyed, "connect", None)):
                destroyed.connect(
                    lambda _obj=None, window_key=key: self._release_window(window_key)
                )
            window.show()
            if self._last_selected_stock is not None:
                self._request_market_snapshot_for_window(window)
                initial_request = getattr(window, "request_initial_validation", None)
                if not callable(initial_request):
                    raise TypeError("initial signal validation request is unavailable")
                QTimer.singleShot(
                    0,
                    lambda ref=window_ref: self._request_initial_validation(ref()),
                )
            return window
        except Exception as exc:
            self.validation_failed.emit(f"SIGNAL_WINDOW_ERROR: {exc}")
            return None

    def _select_stock_for_window(self, window: object) -> None:
        window_key = id(window)
        if window_key not in self._open_windows:
            return
        create_picker = getattr(self._host, "create_stock_picker", None)
        if not callable(create_picker):
            self._fail_window(window, "STOCK_PICKER_UNAVAILABLE")
            return
        try:
            picker = create_picker(window)
            if picker.exec_() != QDialog.Accepted:
                return
            selected = picker.selected_stock
        except Exception as exc:
            self._fail_window(window, f"STOCK_PICKER_ERROR: {exc}")
            return
        self._activate_stock_for_window(window, selected)

    def _activate_stock_for_window(self, window: object, selected: object) -> None:
        window_key = id(window)
        if window_key not in self._open_windows:
            return
        if (
            not isinstance(selected, ValidationStockRef)
            or not selected.code
            or not selected.name
        ):
            self._fail_window(window, "INVALID_SELECTED_STOCK")
            return
        selected = ValidationStockRef(selected.code, selected.name)
        current_stock = getattr(window, "stock", None)
        if (
            isinstance(current_stock, ValidationStockRef)
            and current_stock.code == selected.code
        ):
            if self._remember_stock(selected):
                self._refresh_open_window_stock_projections()
            return
        set_stock = getattr(window, "set_validation_stock", None)
        request_validation = getattr(window, "request_validation", None)
        if not callable(set_stock) or not callable(request_validation):
            self._fail_window(window, "SIGNAL_WINDOW_STOCK_API_UNAVAILABLE")
            return
        self._invalidate_window_requests(window_key)
        self._historical_pools.pop(window_key, None)
        self._validation_sessions.pop(window_key, None)
        self._history_targets[window_key] = self._historical_count
        self._history_extension_inflight.pop(window_key, None)
        self._last_run_requests.pop(window_key, None)
        if set_stock(selected) is not True:
            return
        self._last_selected_stock = ValidationStockRef(selected.code, selected.name)
        self._remember_stock(selected)
        self._refresh_open_window_stock_projections()
        self._request_market_snapshot_for_window(window)
        request_validation()

    def _remember_stock(self, stock: ValidationStockRef) -> bool:
        activate = getattr(self._recent_stock_store, "activate", None)
        if not callable(activate):
            self._last_selected_stock = ValidationStockRef(stock.code, stock.name)
            return False
        previous = self._read_recent_stocks()
        try:
            changed = activate(stock) is True
        except Exception:
            changed = False
        recent = self._read_recent_stocks()
        retained_codes = {candidate.code for candidate in recent}
        for candidate in previous:
            if candidate.code not in retained_codes:
                self._purge_validation_history_for_stock(candidate.code)
        self._last_selected_stock = (
            recent[0] if recent else ValidationStockRef(stock.code, stock.name)
        )
        return changed

    def _sync_window_stock_projection(self, window: object) -> None:
        set_recent = getattr(window, "set_recent_stocks", None)
        if callable(set_recent):
            current = getattr(window, "stock", None)
            current_code = current.code if isinstance(current, ValidationStockRef) else ""
            set_recent(tuple(
                stock for stock in self._read_recent_stocks()
                if stock.code != current_code
            ))
        set_metadata = getattr(window, "set_stock_metadata", None)
        if callable(set_metadata):
            stock = getattr(window, "stock", None)
            metadata = self._metadata_for(stock) or {}
            snapshot_projection = self._market_snapshot_metadata.get(id(window))
            if (
                isinstance(stock, ValidationStockRef)
                and snapshot_projection is not None
                and snapshot_projection[0] == stock.code
            ):
                metadata.update(snapshot_projection[1])
            set_metadata(metadata or None)

    def _request_market_snapshot_for_window(self, window: object) -> None:
        window_key = id(window)
        stock = getattr(window, "stock", None)
        if (
            window_key not in self._open_windows
            or not isinstance(stock, ValidationStockRef)
            or not stock.code
        ):
            return
        request = getattr(self._broker, "request_initial_market_snapshot", None)
        if not callable(request):
            return
        generation = self._market_snapshot_generation.get(window_key, 0) + 1
        self._market_snapshot_generation[window_key] = generation
        expected_code = stock.code
        window_ref = weakref.ref(window)

        def completed(result: object) -> None:
            target = window_ref()
            if (
                target is None
                or self._market_snapshot_generation.get(window_key) != generation
            ):
                return
            current_stock = getattr(target, "stock", None)
            if (
                not isinstance(current_stock, ValidationStockRef)
                or current_stock.code != expected_code
            ):
                return
            payload = dict(result) if isinstance(result, dict) else {}
            rows = payload.get("rows")
            if payload.get("ok") is not True or not isinstance(rows, list):
                return
            for row in rows:
                if not isinstance(row, dict):
                    continue
                row_code = normalize_broker_stock_code(row.get("stock_code"))
                if row_code != expected_code:
                    continue
                projection = {
                    field: row.get(field)
                    for field in _MARKET_SNAPSHOT_FIELDS
                    if field in row
                }
                self._market_snapshot_metadata[window_key] = (
                    expected_code,
                    projection,
                )
                self._sync_window_stock_projection(target)
                return

        try:
            request((expected_code,), callback=completed)
        except Exception:
            return

    def _refresh_open_window_stock_projections(self) -> None:
        for window in tuple(self._open_windows.values()):
            self._sync_window_stock_projection(window)

    def _purge_validation_history_for_stock(self, stock_code: object) -> None:
        code = str(stock_code or "").strip().upper()
        if not code:
            return
        delete_stock = getattr(self._historical_cache, "delete_stock", None)
        if callable(delete_stock):
            try:
                delete_stock(code)
            except Exception:
                pass
        self._evict_validation_history_for_stock(code)

    def _remove_recent_stock_for_window(self, window: object, stock: object) -> None:
        if id(window) not in self._open_windows:
            return
        if not isinstance(stock, ValidationStockRef) or not stock.code:
            return
        remove = getattr(self._recent_stock_store, "remove", None)
        if not callable(remove):
            return
        try:
            changed = remove(stock) is True
        except Exception:
            changed = False
        if not changed:
            return
        self._purge_validation_history_for_stock(stock.code)
        recent = self._read_recent_stocks()
        self._last_selected_stock = recent[0] if recent else None
        QTimer.singleShot(0, self._refresh_open_window_stock_projections)

    def _evict_validation_history_for_stock(self, stock_code: object) -> None:
        code = str(stock_code or "").strip()
        if not code:
            return
        for key in tuple(_SHARED_SIGNAL_VALIDATION_HISTORICAL_POOLS):
            if len(key) > 1 and str(key[1]) == code:
                _SHARED_SIGNAL_VALIDATION_HISTORICAL_POOLS.pop(key, None)
        affected_window_keys: set[int] = set()
        for window_key, pool in tuple(self._historical_pools.items()):
            stock = pool.get("stock") if isinstance(pool, dict) else None
            pool_code = (
                stock.code
                if isinstance(stock, ValidationStockRef)
                else str(getattr(stock, "code", "") or "").strip()
            )
            if pool_code == code:
                self._historical_pools.pop(window_key, None)
                affected_window_keys.add(window_key)
        for window_key, window in tuple(self._open_windows.items()):
            stock = getattr(window, "stock", None)
            if isinstance(stock, ValidationStockRef) and stock.code == code:
                affected_window_keys.add(window_key)
                discard = getattr(window, "discard_validation_stock_if_matches", None)
                if callable(discard):
                    try:
                        discard(code)
                    except Exception:
                        pass
        for window_key in affected_window_keys:
            self._validation_sessions.pop(window_key, None)
            self._complete_history_extension(window_key, success=False)
            self._last_run_requests.pop(window_key, None)
            self._invalidate_window_requests(window_key)
            for request_key in tuple(self._active_providers):
                if request_key[0] == window_key:
                    self._active_providers.pop(request_key, None)

    def _invalidate_window_requests(self, window_key: int) -> None:
        self._clear_window_candle_preview(window_key, self._prepare_active.get(window_key))
        self._drop_deferred_prepare(window_key)
        self._drop_deferred_range(window_key)
        self._history_browse_active.pop(window_key, None)
        self._history_browse_deferred.pop(window_key, None)
        self._complete_history_extension(window_key, success=False)
        if window_key in self._request_generation:
            self._request_generation[window_key] += 1
        self._prepare_pending.pop(window_key, None)
        self._range_replay_pending.pop(window_key, None)
        self._active_run_identity.pop(window_key, None)
        if window_key in self._range_replay_generation:
            self._range_replay_generation[window_key] += 1
        if window_key in self._market_snapshot_generation:
            self._market_snapshot_generation[window_key] += 1
        self._market_snapshot_metadata.pop(window_key, None)
        for request_key in tuple(self._active_providers):
            if request_key[0] == window_key:
                self._active_providers.pop(request_key, None)

    def _clear_window_candle_preview(self, window_key: int, token: object) -> None:
        window = self._open_windows.get(window_key)
        clear = getattr(window, "clear_candle_preview", None)
        if token is not None and callable(clear):
            try:
                clear(token)
            except RuntimeError:
                pass  # A deleted Qt window cannot retain a visible preview.

    def _invalidate_window_input(self, window: object) -> None:
        window_key = id(window)
        if self._open_windows.get(window_key) is window:
            self._invalidate_window_requests(window_key)

    def _invalidate_window_range(self, window: object, range_value: object) -> None:
        window_key = id(window)
        if self._open_windows.get(window_key) is not window:
            return
        current_range = getattr(window, "validation_range", None)
        if range_value is None:
            if current_range is not None:
                return
        elif (
            not isinstance(range_value, tuple)
            or tuple(range_value) != current_range
        ):
            return
        self._drop_deferred_range(window_key)
        self._range_replay_pending.pop(window_key, None)
        self._range_replay_generation[window_key] = (
            self._range_replay_generation.get(window_key, 0) + 1
        )

    def _drop_deferred_prepare(self, window_key: int) -> None:
        deferred = self._prepare_deferred.pop(window_key, None)
        if (
            deferred is not None
            and self._prepare_active.get(window_key)
            is deferred[1].get("prepare_token")
        ):
            self._prepare_active.pop(window_key, None)
            generation = deferred[1].get("generation")
            if (
                isinstance(generation, int)
                and not isinstance(generation, bool)
                and self._request_generation.get(window_key) == generation
            ):
                self._complete_history_extension(window_key, success=False)

    def _drop_deferred_range(self, window_key: int) -> None:
        deferred = self._range_replay_deferred.pop(window_key, None)
        if (
            deferred is not None
            and self._range_replay_active.get(window_key)
            is deferred[1].get("range_token")
        ):
            self._range_replay_active.pop(window_key, None)

    @staticmethod
    def _window_busy_epoch(window: object) -> int | None:
        busy = getattr(window, "is_chart_view_update_busy", None)
        if not callable(busy):
            busy = getattr(window, "is_chart_interaction_busy", None)
        if not callable(busy):
            return None
        try:
            if not busy():
                return None
            epoch = getattr(window, "chart_interaction_epoch")
        except (AttributeError, RuntimeError):
            return None
        return epoch if isinstance(epoch, int) and not isinstance(epoch, bool) else None

    @staticmethod
    def _window_accepts_results(window: object) -> bool:
        if not callable(getattr(window, "is_chart_interaction_busy", None)):
            return True
        suspended = getattr(window, "_chart_results_suspended", None)
        if isinstance(suspended, bool):
            # Never-shown direct callers remain supported; hide/close suspends.
            return not suspended
        visible = getattr(window, "isVisible", None)
        if not callable(visible):
            return True
        try:
            return bool(visible())
        except RuntimeError:
            return False

    def _on_chart_interaction_ended(
        self,
        window: object,
        epoch: object,
        cancelled: object,
    ) -> None:
        window_key = id(window)
        if (
            self._open_windows.get(window_key) is not window
            or isinstance(epoch, bool)
            or not isinstance(epoch, int)
        ):
            return
        deferred_prepare = self._prepare_deferred.get(window_key)
        deferred_range = self._range_replay_deferred.get(window_key)
        deferred_browse = self._history_browse_deferred.get(window_key)
        if bool(cancelled):
            if deferred_prepare is not None and epoch >= deferred_prepare[0]:
                self._drop_deferred_prepare(window_key)
            if deferred_range is not None and epoch >= deferred_range[0]:
                self._drop_deferred_range(window_key)
            if deferred_browse is not None and epoch >= deferred_browse[0]:
                self._history_browse_deferred.pop(window_key, None)
                self._history_browse_active.pop(window_key, None)
                self._complete_history_extension(window_key, success=False)
            self._start_pending_window_work(window_key)
            return
        if self._window_busy_epoch(window) is not None:
            return
        if deferred_prepare is not None:
            if deferred_prepare[0] == epoch:
                self._prepare_deferred.pop(window_key, None)
                self._on_signal_scan_completed(deferred_prepare[1])
            elif epoch > deferred_prepare[0]:
                self._drop_deferred_prepare(window_key)
        if deferred_range is not None:
            if deferred_range[0] == epoch:
                self._range_replay_deferred.pop(window_key, None)
                self._on_range_replay_completed(deferred_range[1])
            elif epoch > deferred_range[0]:
                self._drop_deferred_range(window_key)
        if deferred_browse is not None:
            if deferred_browse[0] == epoch:
                self._history_browse_deferred.pop(window_key, None)
                self._on_history_browse_completed(deferred_browse[1])
            elif epoch > deferred_browse[0]:
                self._history_browse_deferred.pop(window_key, None)
                self._history_browse_active.pop(window_key, None)
                self._complete_history_extension(window_key, success=False)
        self._start_pending_window_work(window_key)

    def _start_pending_window_work(self, window_key: int) -> None:
        if (
            window_key in self._prepare_active
            or window_key in self._range_replay_active
        ):
            return
        pending_prepare = self._prepare_pending.pop(window_key, None)
        if self._prepare_request_is_current(pending_prepare):
            self._range_replay_pending.pop(window_key, None)
            self._start_pool_prepare(pending_prepare)
            return
        pending_range = self._range_replay_pending.pop(window_key, None)
        if pending_range is not None:
            window = pending_range.get("window")
            requested_range = pending_range.get("range")
            if (
                window is not None
                and isinstance(requested_range, tuple)
                and len(requested_range) == 2
            ):
                timestamp_capable, requested_times = (
                    self._capture_window_range_times(window, requested_range)
                )
                pending_range["range_uses_timestamps"] = timestamp_capable
                pending_range["range_times"] = requested_times
            self._start_range_replay(pending_range)

    @staticmethod
    def _request_initial_validation(window: object) -> None:
        request = getattr(window, "request_initial_validation", None)
        if callable(request):
            request()

    def _apply_to_source(
        self,
        window: object,
        source_ref: weakref.ReferenceType[object],
        payload: object,
    ) -> None:
        show_result = getattr(window, "show_settings_apply_result", None)
        if not isinstance(payload, IndicatorFollowSignalValidationApplyPayload):
            if callable(show_result):
                show_result("설정적용 데이터가 올바르지 않습니다.", success=False)
            return
        source = source_ref()
        if not self._valid_requester(source):
            if callable(show_result):
                show_result(
                    "원본 설정창이 닫혀 있어 검증값을 적용할 수 없습니다.",
                    success=False,
                )
            return
        apply_candidate = getattr(
            source,
            "apply_signal_validation_candidate_ui_state",
            None,
        )
        apply_state = getattr(source, "apply_signal_validation_ui_state", None)
        if not callable(apply_state):
            if callable(show_result):
                show_result("원본 설정창에 검증값을 적용할 수 없습니다.", success=False)
            return
        try:
            if callable(apply_candidate):
                result = apply_candidate(payload.to_ui_state())
            else:
                result = apply_state(payload.to_ui_state())
        except Exception:
            if callable(show_result):
                show_result("검증값 적용 중 오류가 발생했습니다.", success=False)
            return
        skipped = result.get("skipped", []) if isinstance(result, dict) else ["invalid_result"]
        if skipped:
            if callable(show_result):
                show_result("일부 검증값을 적용할 수 없습니다.", success=False)
            return
        if callable(show_result):
            show_result("", success=True)

    def _release_window(self, window_key: int) -> None:
        self._clear_window_candle_preview(window_key, self._prepare_active.get(window_key))
        self._open_windows.pop(window_key, None)
        self._request_generation.pop(window_key, None)
        self._prepare_active.pop(window_key, None)
        self._prepare_pending.pop(window_key, None)
        self._prepare_deferred.pop(window_key, None)
        self._range_replay_generation.pop(window_key, None)
        self._range_replay_active.pop(window_key, None)
        self._range_replay_pending.pop(window_key, None)
        self._range_replay_deferred.pop(window_key, None)
        self._active_run_identity.pop(window_key, None)
        self._history_browse_active.pop(window_key, None)
        self._history_browse_deferred.pop(window_key, None)
        self._market_snapshot_generation.pop(window_key, None)
        self._market_snapshot_metadata.pop(window_key, None)
        self._historical_pools.pop(window_key, None)
        self._validation_sessions.pop(window_key, None)
        self._history_targets.pop(window_key, None)
        inflight = self._history_extension_inflight.pop(window_key, None)
        cancel_event = inflight.get("cancel_event") if isinstance(inflight, dict) else None
        if isinstance(cancel_event, Event):
            cancel_event.set()
        self._history_exhaustion.pop(window_key, None)
        self._last_run_requests.pop(window_key, None)
        for request_key in list(self._active_providers):
            if request_key[0] == window_key:
                self._active_providers.pop(request_key, None)

    def _request_history_extension(self, window: object) -> None:
        window_key = id(window)
        if window_key not in self._open_windows:
            return
        run_request = self._last_run_requests.get(window_key)
        if not isinstance(run_request, IndicatorFollowSignalValidationRunRequest):
            return
        if window_key in self._history_extension_inflight:
            return
        if (
            window_key in self._prepare_active
            or window_key in self._range_replay_active
            or any(key[0] == window_key for key in self._active_providers)
        ):
            return
        try:
            warmup_bars = required_validation_history_context_bars(
                run_request.settings_snapshot.to_dict()
            )
        except Exception:
            return
        maximum_target = max(
            1,
            min(
                MAX_SIGNAL_VALIDATION_HISTORICAL_COUNT,
                DEFAULT_CANDLES_MAX_COUNT - warmup_bars,
            ),
        )
        current_target = self._history_targets.get(
            window_key,
            self._historical_count,
        )
        next_target = min(maximum_target, current_target + _HISTORY_EXTENSION_STEP)
        if next_target <= current_target:
            return
        source_pool = self._historical_pools.get(window_key)
        stock = getattr(window, "stock", None)
        exhaustion_identity = (
            getattr(stock, "code", ""),
            run_request.settings_snapshot.rules_hash,
            next_target,
            self._pool_computation_source_identity(source_pool),
        )
        if self._history_exhaustion.get(window_key) == exhaustion_identity:
            return
        trace_signal_validation_event(
            "flow.history_extension.request",
            window_key=window_key,
            request_generation=self._request_generation.get(window_key, 0),
            previous_target=current_target,
            requested_target=next_target,
            window_range=getattr(window, "validation_range", None),
            window_range_times=getattr(window, "_validation_range_times", None),
            window_display=trace_candle_signature(getattr(window, "_candles", None)),
            window_calculation=trace_candle_signature(
                getattr(window, "_calculation_candles", None)
            ),
            source_pool_timeframe=(
                str(source_pool.get("timeframe_key") or "")
                if isinstance(source_pool, dict) else ""
            ),
            source_pool_requested_count=(
                int(source_pool.get("requested_count") or 0)
                if isinstance(source_pool, dict) else 0
            ),
            source_pool_candles=trace_candle_signature(
                source_pool.get("candles") if isinstance(source_pool, dict) else None
            ),
        )
        self._history_targets[window_key] = next_target
        self._history_extension_inflight[window_key] = {
            "previous_target": current_target,
            "requested_target": next_target,
            "cancel_event": Event(),
            "exhaustion_identity": exhaustion_identity,
        }
        extension_request = IndicatorFollowSignalValidationRunRequest(
            run_request.settings_snapshot,
            run_request.candle_count,
            force_historical_refresh=False,
        )
        self._run_validation(window, extension_request, history_extension=True)

    def _complete_history_extension(
        self,
        window_key: int,
        *,
        success: bool,
    ) -> None:
        state = self._history_extension_inflight.pop(window_key, None)
        if not isinstance(state, dict):
            return
        cancel_event = state.get("cancel_event")
        if isinstance(cancel_event, Event):
            cancel_event.set()
        for request_key in tuple(self._active_providers):
            if request_key[0] == window_key:
                self._active_providers.pop(request_key, None)
        if not success:
            self._history_targets[window_key] = int(
                state.get("previous_target") or self._historical_count
            )
        else:
            self._history_exhaustion.pop(window_key, None)

    @staticmethod
    def _historical_rows_from_candles(
        candles: list[dict[str, object]],
    ) -> list[dict[str, str]]:
        def text(value: object) -> str:
            return "" if value is None else str(value)

        return [
            {
                "체결시간": text(candle.get("time")),
                "시가": text(candle.get("open")),
                "고가": text(candle.get("high")),
                "저가": text(candle.get("low")),
                "현재가": text(candle.get("close")),
                "거래량": text(candle.get("volume")),
            }
            for candle in candles
        ]

    def _shared_pool_key(
        self,
        stock: ValidationStockRef,
        timeframe_key: str,
    ) -> tuple[int, str, str, str]:
        return (
            id(self._broker),
            stock.code,
            str(timeframe_key),
            self._validation_market_data_identity(stock),
        )

    def _validation_market_data_identity(self, stock: ValidationStockRef) -> str:
        """Validation Historical remains canonical KRX regardless of ATS eligibility."""
        return stock.code

    def _historical_result_matches_validation_source(
        self,
        result: object,
        stock: ValidationStockRef,
    ) -> bool:
        if (
            not isinstance(result, ValidationHistoricalResult)
            or result.ok is not True
            or result.snapshot is None
        ):
            return False
        expected_identity = self._validation_market_data_identity(stock)
        response_identity = str(
            result.snapshot.market_data_identity or expected_identity
        ).strip()
        response_source = str(
            result.snapshot.market_source
            or market_source_for_identity(response_identity)
        ).strip().upper()
        return (
            response_identity == expected_identity
            and response_source == "KRX"
        )

    @staticmethod
    def _integrated_display_sidecar_key(
        session: ValidationSession,
    ) -> tuple[str, str, int]:
        request = session.request
        return (
            request.stock.code,
            request.timeframe_key,
            request.timeframe_minutes,
        )

    @staticmethod
    def _integrated_display_time_supported(candle: dict[str, object]) -> bool:
        stamp = str(candle.get("time") or "")
        if len(stamp) != 14 or not stamp.isdigit():
            return False
        minute = stamp[8:12]
        return (
            "0800" <= minute < "0850"
            or "0900" <= minute < "1530"
            or "1540" <= minute < "2000"
        )

    @staticmethod
    def _verified_nxt_display_stock(stock: ValidationStockRef) -> bool:
        try:
            snapshot = load_stock_library_snapshot()
        except Exception:
            return False
        if getattr(snapshot, "state", None) != STOCK_LIBRARY_READY:
            return False
        matches = [
            record
            for record in tuple(getattr(snapshot, "records", ()) or ())
            if isinstance(record, dict)
            and str(record.get("code") or "") == stock.code
        ]
        return len(matches) == 1 and matches[0].get("nxt_available") is True

    def _integrated_display_candles_for_session(
        self,
        session: ValidationSession,
    ) -> list[dict[str, object]] | None:
        sidecar = self._integrated_display_sidecars.get(
            self._integrated_display_sidecar_key(session)
        )
        candles = sidecar.get("candles") if isinstance(sidecar, dict) else None
        if not isinstance(candles, list) or not candles:
            return None
        return [dict(candle) for candle in candles if isinstance(candle, dict)]

    def _apply_integrated_display_sidecar(
        self,
        window: object,
        session: ValidationSession,
    ) -> None:
        candles = self._integrated_display_candles_for_session(session)
        apply_display = getattr(
            window,
            "set_display_only_integrated_candles",
            None,
        )
        if candles and callable(apply_display):
            apply_display(candles)

    def _ensure_integrated_display_sidecar(
        self,
        window: object,
        session: ValidationSession,
        target_count: int,
    ) -> None:
        timeframe = normalize_validation_timeframe(
            session.request.timeframe_key,
            fallback_minutes=session.request.timeframe_minutes,
        )
        if (
            timeframe["kind"] != "MINUTE"
            or not self._verified_nxt_display_stock(session.request.stock)
        ):
            return
        requester = getattr(
            self._broker,
            "request_minute_candles_display_only_integrated",
            None,
        )
        if not callable(requester):
            return
        interval = session.request.timeframe_minutes
        full_day_count = sum(
            (session_minutes + interval - 1) // interval
            for session_minutes in (50, 390, 260)
        )
        request_count = min(
            DEFAULT_CANDLES_MAX_COUNT,
            max(1, int(target_count), full_day_count),
        )
        key = self._integrated_display_sidecar_key(session)
        cached = self._integrated_display_sidecars.get(key)
        if (
            isinstance(cached, dict)
            and int(cached.get("coverage_count") or 0) >= request_count
        ):
            self._apply_integrated_display_sidecar(window, session)
            return
        if self._integrated_display_inflight.get(key, 0) >= request_count:
            return
        self._integrated_display_inflight[key] = request_count
        stock = session.request.stock
        window_key = id(window)

        def completed(payload: object) -> None:
            if self._integrated_display_inflight.get(key) == request_count:
                self._integrated_display_inflight.pop(key, None)
            if not isinstance(payload, dict) or (
                payload.get("ok") is not True
                or str(payload.get("code") or "") != stock.code
                or str(payload.get("market_data_identity") or "")
                != f"{stock.code}_AL"
                or str(payload.get("market_source") or "").upper()
                != "INTEGRATED"
                or payload.get("display_only") is not True
                or payload.get("interval") != session.request.timeframe_minutes
                or not isinstance(payload.get("rows"), list)
                or any(not isinstance(row, dict) for row in payload["rows"])
            ):
                return
            try:
                snapshot = ValidationHistoricalSnapshot(
                    stock=stock,
                    timeframe_minutes=session.request.timeframe_minutes,
                    timeframe_key=session.request.timeframe_key,
                    requested_count=request_count,
                    request_id=str(
                        payload.get("request_id")
                        or payload.get("rqname")
                        or f"{stock.code}_AL_DISPLAY"
                    ),
                    rows=payload["rows"],
                    market_data_identity=f"{stock.code}_AL",
                    market_source="INTEGRATED",
                )
                candles, _dropped = project_validation_candles(
                    snapshot,
                    as_of=datetime.max.replace(tzinfo=SEOUL_TIMEZONE),
                )
            except Exception:
                return
            candles = [
                candle
                for candle in candles
                if self._integrated_display_time_supported(candle)
            ]
            if not candles:
                return
            coverage_count = len(candles)
            existing = self._integrated_display_sidecars.get(key)
            existing_coverage = (
                int(existing.get("coverage_count") or 0)
                if isinstance(existing, dict)
                else 0
            )
            if coverage_count >= existing_coverage:
                self._integrated_display_sidecars[key] = {
                    "stock_code": stock.code,
                    "timeframe_key": session.request.timeframe_key,
                    "timeframe_minutes": session.request.timeframe_minutes,
                    "requested_count": request_count,
                    "coverage_count": coverage_count,
                    "market_data_identity": f"{stock.code}_AL",
                    "market_source": "INTEGRATED",
                    "display_only": True,
                    "candles": candles,
                }
            if (
                self._open_windows.get(window_key) is window
                and self._validation_sessions.get(window_key) is session
            ):
                self._apply_integrated_display_sidecar(window, session)

        try:
            started = requester(
                stock.code,
                stock.name,
                interval=session.request.timeframe_minutes,
                count=request_count,
                max_count=DEFAULT_CANDLES_MAX_COUNT,
                callback=completed,
                nxt_available=True,
            )
        except Exception:
            self._integrated_display_inflight.pop(key, None)
            return
        if (
            isinstance(started, dict)
            and started.get("ok") is False
            and self._integrated_display_inflight.get(key) == request_count
        ):
            self._integrated_display_inflight.pop(key, None)

    @staticmethod
    def _context_provider_for_session(
        session: ValidationSession,
    ) -> object:
        rules = session.request.settings_snapshot.to_dict()
        execution_policy = rules.get("validation_execution")
        return (
            ValidationVirtualPositionTracker(execution_policy)
            if isinstance(execution_policy, dict)
            else build_validation_average_price_context
        )

    @staticmethod
    def _signal_view_signature(pool: object) -> tuple[object, ...] | None:
        if not isinstance(pool, dict):
            return None
        candles = pool.get("candles")
        if not isinstance(candles, list) or not candles:
            return None
        return (
            str(pool.get("timeframe_key") or ""),
            len(candles),
            str(candles[0].get("time") or ""),
            str(candles[-1].get("time") or ""),
            pool.get("production_calculation_cache_identity"),
        )

    def _pool_matches(
        self,
        pool: object,
        stock: ValidationStockRef,
        timeframe_key: str,
        required_count: int,
    ) -> bool:
        return (
            isinstance(pool, dict)
            and pool.get("stock") == stock
            and pool.get("timeframe_key") == str(timeframe_key)
            and pool.get("market_data_identity")
            == self._validation_market_data_identity(stock)
            and isinstance(pool.get("requested_count"), int)
            and int(pool["requested_count"]) >= required_count
            and isinstance(pool.get("candles"), list)
            and bool(pool["candles"])
        )

    def _pool_match_failure_reason(
        self,
        pool: object,
        stock: ValidationStockRef,
        timeframe_key: str,
        required_count: int,
    ) -> str | None:
        """Explain the existing predicate for diagnostics; never route by this."""
        if pool is None:
            return "missing"
        if not isinstance(pool, dict):
            return "not_dict"
        if pool.get("stock") != stock:
            return "stock"
        if pool.get("timeframe_key") != str(timeframe_key):
            return "timeframe"
        if pool.get("market_data_identity") != self._validation_market_data_identity(stock):
            return "market_identity"
        requested_count = pool.get("requested_count")
        if not isinstance(requested_count, int) or requested_count < required_count:
            return "requested_count"
        candles = pool.get("candles")
        if not isinstance(candles, list) or not candles:
            return "candles"
        return None

    @staticmethod
    def _trace_history_browse_decision(
        *,
        window_key: int,
        generation: int,
        overlap_ok: bool,
        marker_coverage: object,
        display_candles: object,
        coverage_ok: bool,
    ) -> None:
        if not signal_validation_trace_enabled():
            return
        coverage_present = marker_coverage is not None
        coverage_range = (
            [str(marker_coverage[0]), str(marker_coverage[1])]
            if isinstance(marker_coverage, tuple) and len(marker_coverage) == 2
            else None
        )
        display_range = None
        display_ok = False
        if isinstance(display_candles, list) and display_candles:
            first = display_candles[0]
            last = display_candles[-1]
            if isinstance(first, dict) and isinstance(last, dict):
                display_ok = True
                display_range = [
                    str(first.get("time") or ""),
                    str(last.get("time") or ""),
                ]
        trace_signal_validation_event(
            "flow.history_browse.decision",
            window_key=window_key,
            generation=generation,
            overlap_ok=bool(overlap_ok),
            marker_coverage_present=coverage_present,
            marker_coverage_range=coverage_range,
            display_ok=display_ok,
            display_range=display_range,
            coverage_ok=bool(coverage_ok),
            requires_full_revalidation=bool(
                overlap_ok and display_ok and not coverage_ok
            ),
        )

    @staticmethod
    def _trace_pool_replaced(
        *,
        reason: str,
        window_key: int,
        old_pool: object,
        new_pool: object,
    ) -> None:
        if not signal_validation_trace_enabled():
            return
        trace_signal_validation_event(
            "flow.pool.replaced",
            reason=str(reason),
            window_key=window_key,
            old_pool_present=isinstance(old_pool, dict),
            new_pool_present=isinstance(new_pool, dict),
            same_object=old_pool is new_pool,
            old_pool=trace_pool_summary(old_pool),
            new_pool=trace_pool_summary(new_pool),
        )

    def _persistent_cache_requires_broker_probe(
        self,
        entry: ValidationHistoricalCacheEntry,
    ) -> bool:
        try:
            verified_at = datetime.fromisoformat(str(entry.updated_at or ""))
        except (TypeError, ValueError):
            return True
        if verified_at.tzinfo is None:
            return True
        verified_at = verified_at.astimezone(SEOUL_TIMEZONE)

        try:
            current = self._now_factory()
        except Exception:
            current = datetime.now(SEOUL_TIMEZONE)
        if not isinstance(current, datetime):
            return True
        current = (
            current.replace(tzinfo=SEOUL_TIMEZONE)
            if current.tzinfo is None
            else current.astimezone(SEOUL_TIMEZONE)
        )
        if current < verified_at:
            return True
        if current == verified_at:
            return current.date().weekday() < 5

        # Only skip the broker probe when the cache was already broker-verified
        # and every calendar date since that verification is a weekend.
        # This intentionally does not guess weekday holidays or market sessions.
        cursor = verified_at.date()
        while cursor <= current.date():
            if cursor.weekday() < 5:
                return True
            cursor += timedelta(days=1)
        return False

    def _cached_history(
        self,
        stock: ValidationStockRef,
        timeframe_minutes: int,
        timeframe_key: str,
        fetch_count: int,
    ) -> ValidationHistoricalCacheEntry | None:
        load_covering = getattr(self._historical_cache, "load_covering", None)
        load = getattr(self._historical_cache, "load", None)
        if not callable(load_covering) and not callable(load):
            return None
        expected_identity = self._validation_market_data_identity(stock)
        try:
            entry = (
                load_covering(
                    stock.code,
                    timeframe_minutes,
                    fetch_count,
                    timeframe_key,
                )
                if callable(load_covering)
                else load(
                    stock.code,
                    timeframe_minutes,
                    fetch_count,
                    timeframe_key,
                )
            )
        except Exception:
            return None
        if not isinstance(entry, ValidationHistoricalCacheEntry):
            return None
        marker_path = self._validation_cache_source_marker_path(
            stock,
            timeframe_minutes,
            timeframe_key,
            entry.requested_count,
        )
        marker_identity = ""
        if marker_path is not None:
            try:
                marker = json.loads(marker_path.read_text(encoding="utf-8"))
                if isinstance(marker, dict):
                    marker_identity = str(
                        marker.get("market_data_identity") or ""
                    ).strip()
            except Exception:
                marker_identity = ""
        entry_identity = str(entry.market_data_identity or "").strip()
        if entry_identity and entry_identity != expected_identity:
            return None
        if marker_identity and marker_identity != expected_identity:
            return None
        return entry

    def _cached_signal_entries(
        self,
        stock: ValidationStockRef,
        timeframe_minutes: int,
        timeframe_key: str,
        fetch_count: int,
        settings_hash: str,
    ) -> tuple[ValidationReplayEntry, ...] | None:
        load = getattr(self._historical_cache, "load_signal_entries", None)
        if not callable(load):
            return None
        try:
            payloads = load(
                stock.code,
                timeframe_minutes,
                fetch_count,
                settings_hash,
                timeframe_key,
            )
        except Exception:
            return None
        if payloads is None or not isinstance(payloads, list):
            return None
        try:
            return tuple(
                ValidationReplayEntry(**dict(payload))
                for payload in payloads
                if isinstance(payload, dict)
            )
        except (TypeError, ValueError, OverflowError):
            return None

    def _persist_signal_entries(
        self,
        stock: ValidationStockRef,
        timeframe_minutes: int,
        timeframe_key: str,
        fetch_count: int,
        settings_hash: str,
        entries: tuple[object, ...] | list[object],
    ) -> bool:
        store = getattr(self._historical_cache, "store_signal_entries", None)
        if not callable(store):
            return False
        payloads: list[dict[str, object]] = []
        for entry in entries:
            if not isinstance(entry, ValidationReplayEntry):
                return False
            payload = entry.to_dict()
            if not isinstance(payload, dict):
                return False
            payloads.append(payload)
        try:
            return store(
                stock_code=stock.code,
                timeframe_minutes=timeframe_minutes,
                requested_count=fetch_count,
                settings_hash=settings_hash,
                entries=payloads,
                timeframe_key=timeframe_key,
            ) is True
        except Exception:
            return False

    def _persist_history(
        self,
        stock: ValidationStockRef,
        timeframe_minutes: int,
        timeframe_key: str,
        fetch_count: int,
        candles: list[dict[str, object]],
        *,
        market_data_identity: str = "",
        market_source: str = "",
        cache_lease: object = _CACHE_LEASE_UNSET,
    ) -> bool:
        store = getattr(self._historical_cache, "store", None)
        guarded_store = getattr(
            self._historical_cache,
            "store_if_lease_current",
            None,
        )
        if not callable(store):
            return False
        persisted_identity = str(market_data_identity or "").strip()
        if not persisted_identity:
            persisted_identity = self._validation_market_data_identity(stock)
        persisted_source = str(market_source or "").strip()
        if not persisted_source:
            persisted_source = market_source_for_identity(persisted_identity)
        def write_source_marker() -> bool:
            marker_path = self._validation_cache_source_marker_path(
                stock,
                timeframe_minutes,
                timeframe_key,
                fetch_count,
            )
            if marker_path is None:
                return False
            try:
                marker_path.parent.mkdir(parents=True, exist_ok=True)
                descriptor, raw_path = tempfile.mkstemp(
                    prefix=f".{marker_path.name}.",
                    suffix=".tmp",
                    dir=str(marker_path.parent),
                )
                temp_path = Path(raw_path)
                with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
                    json.dump(
                        {
                            "canonical_stock_code": stock.code,
                            "market_data_identity": persisted_identity,
                            "market_source": persisted_source,
                        },
                        handle,
                        ensure_ascii=False,
                        indent=2,
                        sort_keys=True,
                    )
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp_path, marker_path)
            except Exception:
                try:
                    cache_path = marker_path.with_name(
                        marker_path.name.removesuffix(".market-source.json")
                    )
                    cache_path.unlink(missing_ok=True)
                except OSError:
                    pass
                return False
            return True

        try:
            store_kwargs = dict(
                stock_code=stock.code, stock_name=stock.name,
                timeframe_minutes=timeframe_minutes, requested_count=fetch_count,
                candles=candles, timeframe_key=timeframe_key,
                market_data_identity=persisted_identity, market_source=persisted_source,
            )
            if cache_lease is not _CACHE_LEASE_UNSET and callable(guarded_store):
                return guarded_store(cache_lease=cache_lease,
                    after_store=write_source_marker, **store_kwargs) is True
            if store(**store_kwargs) is not True:
                return False
            return write_source_marker()
        except Exception:
            return False

    def _validation_cache_source_marker_path(
        self,
        stock: ValidationStockRef,
        timeframe_minutes: int,
        timeframe_key: str,
        fetch_count: int,
    ) -> Path | None:
        path_builder = getattr(self._historical_cache, "path_for", None)
        if not callable(path_builder):
            return None
        try:
            cache_path = Path(
                path_builder(
                    stock.code,
                    timeframe_minutes,
                    fetch_count,
                    timeframe_key,
                )
            )
        except Exception:
            return None
        return cache_path.with_name(f"{cache_path.name}.market-source.json")

    def _pool_from_candles(
        self,
        session: ValidationSession,
        candles: list[dict[str, object]],
        *,
        requested_count: int,
        request_id: str,
        market_data_identity: str = "",
        market_source: str = "",
    ) -> dict[str, object]:
        timeframe_key = session.request.timeframe_key
        pool_identity = str(market_data_identity or "").strip()
        if not pool_identity:
            pool_identity = self._validation_market_data_identity(session.request.stock)
        pool_source = str(market_source or "").strip()
        if not pool_source:
            pool_source = market_source_for_identity(pool_identity)
        snapshot = ValidationHistoricalSnapshot(
            stock=session.request.stock,
            timeframe_minutes=session.request.timeframe_minutes,
            timeframe_key=timeframe_key,
            requested_count=requested_count,
            request_id=request_id,
            rows=self._historical_rows_from_candles(candles),
            market_data_identity=pool_identity,
            market_source=pool_source,
        )
        return {
            "stock": session.request.stock,
            "market_data_identity": pool_identity,
            "market_source": pool_source,
            "timeframe_minutes": session.request.timeframe_minutes,
            "timeframe_key": timeframe_key,
            "requested_count": requested_count,
            "request_id": request_id,
            "snapshot": snapshot,
            "candles": candles,
            "signal_entries_by_settings_hash": {},
            "signal_entries_signature_by_settings_hash": {},
        }

    def _validation_pool_for_session(
        self,
        session: ValidationSession,
        pool: dict[str, object],
        *,
        target_count: int | None = None,
        as_of: datetime | None = None,
    ) -> dict[str, object]:
        if not isinstance(pool, dict):
            return pool
        rules = session.request.settings_snapshot.to_dict()
        raw_candles = pool.get("candles")
        if not isinstance(raw_candles, list):
            return pool
        if (
            isinstance(target_count, bool)
            or not isinstance(target_count, int)
            or target_count <= 0
        ):
            target_count = (
                self._historical_count
                + required_validation_history_context_bars(rules)
            )
        timeframe = normalize_validation_timeframe(
            session.request.timeframe_key,
            fallback_minutes=session.request.timeframe_minutes,
        )
        scope = (
            rules.get("validation_market_scope")
            if isinstance(rules, dict)
            else None
        )
        regular_minute = (
            isinstance(scope, dict)
            and scope.get("regular_market_only") is True
            and timeframe["kind"] == "MINUTE"
        )
        normalized_raw = [
            dict(candle) for candle in raw_candles if isinstance(candle, dict)
        ]
        integrated_display = (
            self._integrated_display_candles_for_session(session)
            if timeframe["kind"] == "MINUTE" and not regular_minute
            else None
        )
        if (
            not regular_minute
            and integrated_display is None
            and len(normalized_raw) <= target_count
        ):
            return pool
        target_minutes = session.request.timeframe_minutes
        target_key = session.request.timeframe_key
        projected = normalized_raw[-target_count:]
        derived = dict(pool)
        derived["timeframe_minutes"] = target_minutes
        derived["timeframe_key"] = target_key
        derived["requested_count"] = min(target_count, len(projected))
        derived["candles"] = projected
        if regular_minute:
            derived["display_candles"] = _krx_regular_session_candles(projected)
        elif integrated_display is not None:
            calculation_by_time = {
                str(candle.get("time") or ""): candle
                for candle in projected
            }
            derived["display_candles"] = [
                dict(
                    calculation_by_time.get(
                        str(candle.get("time") or ""),
                        candle,
                    )
                )
                for candle in integrated_display
            ]
            derived["display_only_integrated"] = True
        derived["signal_entries_by_settings_hash"] = pool.setdefault(
            "signal_entries_by_settings_hash",
            {},
        )
        derived["signal_entries_signature_by_settings_hash"] = pool.setdefault(
            "signal_entries_signature_by_settings_hash",
            {},
        )
        derived["source_timeframe_minutes"] = pool.get("timeframe_minutes")
        derived["source_timeframe_key"] = pool.get("timeframe_key")
        derived["source_requested_count"] = pool.get("requested_count")
        derived["snapshot"] = ValidationHistoricalSnapshot(
            stock=session.request.stock,
            timeframe_minutes=target_minutes,
            timeframe_key=target_key,
            requested_count=max(1, min(target_count, len(projected))),
            request_id=(
                f"{str(pool.get('request_id') or 'POOL')}:ACTIVE"
            ),
            rows=self._historical_rows_from_candles(projected),
            market_data_identity=str(pool.get("market_data_identity") or ""),
            market_source=str(pool.get("market_source") or ""),
        )
        return derived

    @staticmethod
    def _pool_dispatch_snapshot(
        pool: dict[str, object],
        *,
        source_identity: tuple[object, ...] | None = None,
    ) -> dict[str, object]:
        # Normal runs reuse the source identity already computed by _run_validation.
        # Direct pool callers also bind interior/nested edits to a fresh publication.
        if source_identity is None:
            source_identity = (
                IndicatorFollowSignalValidationFlow._pool_computation_source_identity(pool)
            )
        fields = ("candles", "display_candles", "production_session_contract")
        previous = pool.get("_dispatch_snapshot")
        candles = pool.get("candles")
        display = pool.get("display_candles")
        if previous is not None:
            old_candles, old_display, published, old_identity = previous
            if (
                candles is old_candles
                and display is old_display
                and source_identity == old_identity
                and all((field in pool) == (field in published) for field in fields)
                and pool.get("production_session_contract")
                == published.get("production_session_contract")
            ):
                return published
        else:
            old_candles = old_display = None
            old_identity = None
            published = {}
        updated = {}
        for field in fields:
            if field not in pool:
                continue
            unchanged = (
                field in published
                and ((field == "candles" and candles is old_candles
                      and source_identity == old_identity)
                     or (field == "display_candles" and display is old_display
                         and source_identity == old_identity)
                     or (field == "production_session_contract"
                         and pool[field] == published[field]))
            )
            updated[field] = published[field] if unchanged else deepcopy(pool[field])
        # One assignment publishes a complete version; dispatched readers retain
        # the previous version even when a contract or source list is replaced.
        pool["_dispatch_snapshot"] = (candles, display, updated, source_identity)
        return updated

    def _install_pool(
        self,
        window: object,
        session: ValidationSession,
        pool: dict[str, object],
        *,
        evaluation_count: int,
        source_identity: tuple[object, ...] | None = None,
        replacement_reason: str | None = None,
    ) -> bool:
        _SHARED_SIGNAL_VALIDATION_HISTORICAL_POOLS[
            self._shared_pool_key(
                session.request.stock,
                str(pool.get("timeframe_key") or ""),
            )
        ] = pool
        window_key = id(window)
        old_pool = self._historical_pools.get(window_key)
        try:
            self._use_pool_for_window(
                window,
                session,
                pool,
                evaluation_count=evaluation_count,
                source_identity=source_identity,
            )
        except Exception as exc:
            self._fail_window(window, f"RESULT_POOL_ERROR: {exc}")
            return False
        if replacement_reason is not None:
            self._trace_pool_replaced(
                reason=replacement_reason,
                window_key=window_key,
                old_pool=old_pool,
                new_pool=pool,
            )
        return True

    def _install_acquired_pool(
        self,
        window: object,
        session: ValidationSession,
        pool: dict[str, object],
        *,
        evaluation_count: int,
        history_browse: bool,
        source_identity: tuple[object, ...] | None = None,
        replacement_reason: str | None = None,
    ) -> bool:
        if history_browse:
            return self._use_pool_for_history_browse(
                window,
                session,
                pool,
                evaluation_count=evaluation_count,
                replacement_reason=replacement_reason,
            )
        return self._install_pool(
            window,
            session,
            pool,
            evaluation_count=evaluation_count,
            source_identity=source_identity,
            replacement_reason=replacement_reason,
        )

    @staticmethod
    def _history_browse_candles_extend_source(
        existing: object,
        candidate: object,
    ) -> bool:
        if (
            not isinstance(existing, list)
            or not existing
            or not isinstance(candidate, list)
            or len(candidate) < len(existing)
        ):
            return False
        times = [str(candle.get("time") or "") for candle in candidate]
        if any(not value for value in times) or times != sorted(set(times)):
            return False
        return candidate[-len(existing):] == existing

    def _use_pool_for_history_browse(
        self,
        window: object,
        session: ValidationSession,
        pool: dict[str, object],
        *,
        evaluation_count: int,
        prepared_view_pool: dict[str, object] | None = None,
        provider_request_key: tuple[int, int] | None = None,
        provider: object | None = None,
        replacement_reason: str | None = None,
    ) -> bool:
        window_key = id(window)
        source_pool = self._historical_pools.get(window_key)
        capture = getattr(window, "capture_history_browse_state", None)
        apply_browse = getattr(window, "apply_history_browse_presentation", None)
        if (
            not isinstance(source_pool, dict)
            or not callable(capture)
            or not callable(apply_browse)
        ):
            self._fail_window(window, "HISTORY_BROWSE_API_UNAVAILABLE")
            return False
        for field in (
            "stock",
            "market_data_identity",
            "market_source",
            "timeframe_minutes",
            "timeframe_key",
        ):
            if source_pool.get(field) != pool.get(field):
                self._fail_window(window, "HISTORY_BROWSE_SOURCE_CONTRACT_CHANGED")
                return False
        try:
            captured = capture(session.request.settings_snapshot)
            if not isinstance(captured, dict):
                self._fail_window(
                    window,
                    "HISTORY_BROWSE_VALIDATED_SNAPSHOT_UNAVAILABLE",
                )
                return False
            history_target = self._history_targets.get(
                window_key,
                self._historical_count,
            )
            dispatch_sources = (pool.get("candles"), pool.get("display_candles"))
            worker_pool = deepcopy({
                key: value for key, value in pool.items()
                if key != "_dispatch_snapshot"
            })
            worker_view_pool = deepcopy(prepared_view_pool)
            worker_source_candles = deepcopy(captured.get("source_candles"))
            worker_signal_entries = deepcopy(captured.get("signal_entries") or ())
            worker_marker_coverage = deepcopy(
                captured.get("signal_marker_coverage_times")
            )
            worker_trust_metadata = deepcopy(captured.get("trust_metadata") or {})
            worker_visualizations = tuple(
                deepcopy(captured.get("visualization_descriptors") or ())
            )
        except Exception as exc:
            self._fail_window(window, f"HISTORY_BROWSE_PREPARE_ERROR: {exc}")
            return False
        token = object()
        generation = self._request_generation.get(window_key, 0)
        replay_snapshot = captured.get("replay_snapshot")
        inflight = self._history_extension_inflight.get(window_key)
        cancel_event = (
            inflight.get("cancel_event") if isinstance(inflight, dict) else None
        )
        if not isinstance(cancel_event, Event):
            cancel_event = Event()
        capture_lease = getattr(self._historical_cache, "capture_store_lease", None)
        payload_base = {
            "window_key": window_key,
            "generation": generation,
            "browse_token": token,
            "validated_snapshot": replay_snapshot,
            "source_pool": source_pool,
            "pool": pool,
            "session": session,
            "history_target": history_target,
            "evaluation_count": evaluation_count,
            "provider_request_key": provider_request_key,
            "provider": provider,
            "replacement_reason": replacement_reason,
        }
        self._history_browse_active[window_key] = token

        def prepare_browse() -> None:
            payload = dict(payload_base)
            try:
                if cancel_event.is_set() or not isinstance(
                    replay_snapshot,
                    ValidationReplaySnapshot,
                ):
                    payload["cancelled"] = True
                else:
                    cache_lease = (
                        inflight.get("cache_lease", _CACHE_LEASE_UNSET)
                        if isinstance(inflight, dict) else _CACHE_LEASE_UNSET
                    )
                    if cache_lease is _CACHE_LEASE_UNSET and callable(capture_lease):
                        cache_lease = capture_lease(session.request.stock.code)
                        if isinstance(inflight, dict):
                            inflight["cache_lease"] = cache_lease
                    source_identity = self._pool_computation_source_identity(
                        worker_pool, _cache_flat_content=True,
                    )
                    payload["source_identity_content_cache"] = worker_pool.get(
                        "_source_identity_content_cache"
                    )
                    payload["dispatch_snapshot"] = (
                        *dispatch_sources,
                        self._pool_dispatch_snapshot(
                            worker_pool, source_identity=source_identity,
                        ),
                        source_identity,
                    )
                    view_pool = worker_view_pool
                    if not isinstance(view_pool, dict):
                        view_pool = self._validation_pool_for_session(
                            session,
                            worker_pool,
                            target_count=(
                                history_target
                                + required_validation_history_context_bars(
                                    session.request.settings_snapshot.to_dict()
                                )
                            ),
                        )
                    active_candles = view_pool.get("candles")
                    display_candles = view_pool.get("display_candles")
                    if isinstance(display_candles, list):
                        display_candles = display_candles[-history_target:]
                    elif isinstance(active_candles, list):
                        display_candles = active_candles[-history_target:]
                    overlap_ok = self._history_browse_candles_extend_source(
                        worker_source_candles,
                        active_candles,
                    )
                    display_ok = (
                        isinstance(display_candles, list) and bool(display_candles)
                    )
                    coverage_ok = (
                        display_ok
                        and isinstance(worker_marker_coverage, tuple)
                        and len(worker_marker_coverage) == 2
                        and all(str(value or "") for value in worker_marker_coverage)
                        and str(display_candles[0].get("time") or "")
                        >= str(worker_marker_coverage[0])
                        and str(display_candles[-1].get("time") or "")
                        <= str(worker_marker_coverage[1])
                    )
                    self._trace_history_browse_decision(
                        window_key=window_key,
                        generation=generation,
                        overlap_ok=overlap_ok,
                        marker_coverage=worker_marker_coverage,
                        display_candles=display_candles,
                        coverage_ok=coverage_ok,
                    )
                    if not overlap_ok or not display_ok:
                        raise ValueError("HISTORY_BROWSE_SOURCE_OVERLAP_CONFLICT")
                    if not coverage_ok:
                        payload["requires_full_revalidation"] = True
                    trust_metadata = worker_trust_metadata
                    trust_metadata["filter_signal_backtest_authorization"] = {
                        "status": "FILTER_SIGNAL_BACKTEST_BLOCKED",
                        "filter_signal_backtest_authorized": False,
                        "required_gates": {},
                        "blockers": ["HISTORY_BROWSE_REVALIDATION_REQUIRED"],
                    }
                    trust_metadata["history_browse"] = {
                        "status": "DISPLAY_ONLY",
                        "validated_snapshot_request_id": (
                            replay_snapshot.historical_request_id
                        ),
                    }
                    display_only_integrated = (
                        view_pool.get("display_only_integrated") is True
                    )
                    payload["display_only_integrated"] = display_only_integrated
                    chart_indexes = (
                        _calculation_indexes_for_mixed_display(
                            active_candles,
                            display_candles,
                        )
                        if display_only_integrated
                        else _calculation_indexes_for_display(
                            active_candles,
                            display_candles,
                        )
                    )
                    backed_indexes = tuple(
                        index for index in chart_indexes if index is not None
                    )
                    chart_start_index = (
                        backed_indexes[0] if backed_indexes else 0
                    )
                    payload["prepared_presentation"] = (
                        prepare_signal_validation_presentation(
                            active_candles,
                            worker_signal_entries,
                            session.request.settings_snapshot,
                            chart_start_index=chart_start_index,
                            chart_count=len(chart_indexes),
                            trust_metadata=trust_metadata,
                            display_candles=display_candles,
                            display_only_integrated=display_only_integrated,
                            visualization_descriptors=worker_visualizations,
                        )
                    )
                    payload["calculation_candles"] = active_candles
                    payload["display_candles"] = display_candles
                    if cancel_event.is_set():
                        payload["cancelled"] = True
                    else:
                        candles = worker_pool.get("candles")
                        if isinstance(candles, list):
                            payload["cache_persisted"] = self._persist_history(
                                session.request.stock,
                                int(
                                    worker_pool.get("timeframe_minutes")
                                    or session.request.timeframe_minutes
                                ),
                                str(worker_pool.get("timeframe_key") or ""),
                                int(worker_pool.get("requested_count") or len(candles)),
                                candles,
                                market_data_identity=str(
                                    worker_pool.get("market_data_identity") or ""
                                ),
                                market_source=str(worker_pool.get("market_source") or ""),
                                cache_lease=cache_lease,
                            )
                    if cancel_event.is_set():
                        payload["cancelled"] = True
            except Exception as exc:
                payload["error"] = str(exc)
            if cancel_event.is_set():
                payload["cancelled"] = True
            try:
                self.history_browse_completed.emit(payload)
            except RuntimeError:
                pass

        try:
            Thread(
                target=prepare_browse,
                name="indicator-follow-validation-history-browse",
                daemon=True,
            ).start()
        except Exception as exc:
            self._history_browse_active.pop(window_key, None)
            if (
                provider_request_key is not None
                and self._active_providers.get(provider_request_key) is provider
            ):
                self._active_providers.pop(provider_request_key, None)
            self._fail_window(window, f"HISTORY_BROWSE_PREPARE_ERROR: {exc}")
            return False
        return True

    def _on_history_browse_completed(self, payload: object) -> None:
        if not isinstance(payload, dict):
            return
        window_key = payload.get("window_key")
        token = payload.get("browse_token")
        generation = payload.get("generation")
        provider_request_key = payload.get("provider_request_key")
        provider = payload.get("provider")

        def release_provider() -> None:
            if (
                isinstance(provider_request_key, tuple)
                and self._active_providers.get(provider_request_key) is provider
            ):
                self._active_providers.pop(provider_request_key, None)

        if (
            isinstance(window_key, bool)
            or not isinstance(window_key, int)
            or self._history_browse_active.get(window_key) is not token
        ):
            return
        window = self._open_windows.get(window_key)
        source_pool = payload.get("source_pool")
        if (
            payload.get("cancelled") is True
            or window is None
            or self._request_generation.get(window_key) != generation
            or self._historical_pools.get(window_key) is not source_pool
            or getattr(window, "_replay_snapshot", None) is not payload.get("validated_snapshot")
            or not self._window_accepts_results(window)
        ):
            self._history_browse_active.pop(window_key, None)
            release_provider()
            self._complete_history_extension(window_key, success=False)
            return
        interaction_epoch = self._window_busy_epoch(window)
        if interaction_epoch is not None:
            self._history_browse_deferred[window_key] = (
                interaction_epoch,
                dict(payload),
            )
            return
        error = str(payload.get("error") or "").strip()
        pool = payload.get("pool")
        session = payload.get("session")
        presentation = payload.get("prepared_presentation")
        if error or not isinstance(pool, dict) or not isinstance(session, ValidationSession):
            self._history_browse_active.pop(window_key, None)
            release_provider()
            self._fail_window(
                window,
                "HISTORY_BROWSE_PREPARE_ERROR: " + (error or "invalid result"),
            )
            return
        if payload.get("requires_full_revalidation") is True:
            self._history_browse_active.pop(window_key, None)
            release_provider()
            self._install_pool(
                window,
                session,
                pool,
                evaluation_count=int(payload.get("evaluation_count") or 1),
                replacement_reason=payload.get("replacement_reason"),
            )
            return
        try:
            pool["_dispatch_snapshot"] = payload["dispatch_snapshot"]
            if "source_identity_content_cache" in payload:
                pool["_source_identity_content_cache"] = payload["source_identity_content_cache"]
            window.apply_history_browse_presentation(
                payload["calculation_candles"],
                chart_candle_count=int(payload["history_target"]),
                prepared_presentation=presentation,
                display_candles=payload["display_candles"],
                display_only_integrated=(
                    payload.get("display_only_integrated") is True
                ),
            )
        except Exception as exc:
            self._history_browse_active.pop(window_key, None)
            release_provider()
            self._fail_window(window, f"HISTORY_BROWSE_VIEW_ERROR: {exc}")
            return
        old_pool = self._historical_pools.get(window_key)
        self._historical_pools[window_key] = pool
        self._validation_sessions[window_key] = session
        _SHARED_SIGNAL_VALIDATION_HISTORICAL_POOLS[
            self._shared_pool_key(
                session.request.stock,
                str(pool.get("timeframe_key") or ""),
            )
        ] = pool
        replacement_reason = payload.get("replacement_reason")
        if replacement_reason is not None:
            self._trace_pool_replaced(
                reason=str(replacement_reason),
                window_key=window_key,
                old_pool=old_pool,
                new_pool=pool,
            )
        self._history_browse_active.pop(window_key, None)
        release_provider()
        self._complete_history_extension(window_key, success=True)
        self._ensure_integrated_display_sidecar(
            window,
            session,
            int(payload["history_target"]),
        )

    def _apply_pool_to_window(
        self,
        window: object,
        session: ValidationSession,
        pool: dict[str, object],
        *,
        evaluation_count: int,
        signal_entries: tuple[object, ...] | list[object] | None,
        replay_snapshot: object | None = None,
        prepared_presentation: object | None = None,
        prepared_view_pool: dict[str, object] | None = None,
    ) -> bool:
        window_key = id(window)
        chart_candle_count = self._history_targets.get(
            window_key,
            self._historical_count,
        )
        set_historical_candle_count = getattr(
            window,
            "set_historical_candle_count",
            None,
        )
        if callable(set_historical_candle_count):
            set_historical_candle_count(chart_candle_count)
        view_pool = prepared_view_pool
        if view_pool is None:
            view_pool = self._validation_pool_for_session(
                session,
                pool,
                target_count=(
                    chart_candle_count
                    + required_validation_history_context_bars(
                        session.request.settings_snapshot.to_dict()
                    )
                ),
            )
        trace_signal_validation_event(
            "flow.apply_pool.begin",
            window_key=window_key,
            request_generation=self._request_generation.get(window_key, 0),
            chart_candle_count=chart_candle_count,
            session_timeframe=session.request.timeframe_key,
            session_settings_hash=session.request.settings_snapshot.rules_hash,
            pool_timeframe=str(pool.get("timeframe_key") or ""),
            pool_requested_count=int(pool.get("requested_count") or 0),
            pool_candles=trace_candle_signature(pool.get("candles")),
            view_timeframe=str(view_pool.get("timeframe_key") or ""),
            view_source_timeframe=str(view_pool.get("source_timeframe_key") or ""),
            view_calculation_source=str(view_pool.get("calculation_source") or ""),
            view_candles=trace_candle_signature(view_pool.get("candles")),
            view_display=trace_candle_signature(view_pool.get("display_candles")),
            window_range_before=getattr(window, "validation_range", None),
            window_range_times_before=getattr(
                window, "_validation_range_times", None
            ),
            window_display_before=trace_candle_signature(
                getattr(window, "_candles", None)
            ),
            window_calculation_before=trace_candle_signature(
                getattr(window, "_calculation_candles", None)
            ),
            signal_entry_count=(
                len(signal_entries) if isinstance(signal_entries, (tuple, list)) else None
            ),
        )
        install_pool = getattr(window, "set_historical_candle_pool", None)
        if callable(install_pool):
            install_pool(
                view_pool["candles"],
                chart_candle_count=chart_candle_count,
            )
        display_candles = view_pool.get("display_candles")
        set_display = getattr(window, "set_historical_display_candles", None)
        if isinstance(display_candles, list) and callable(set_display):
            visible_display = display_candles[-chart_candle_count:]
            if view_pool.get("display_only_integrated") is True:
                set_display(
                    visible_display,
                    display_only_integrated=True,
                )
            else:
                set_display(visible_display)
        stage_prepared_presentation = getattr(
            window,
            "stage_prepared_validation_presentation",
            None,
        )
        stage_signal_entries = getattr(window, "stage_signal_marker_entries", None)
        set_signal_entries = getattr(window, "set_signal_marker_entries", None)
        if (
            prepared_presentation is not None
            and callable(stage_prepared_presentation)
        ):
            stage_prepared_presentation(prepared_presentation)
        elif signal_entries is not None:
            if callable(stage_signal_entries):
                stage_signal_entries(list(signal_entries))
            elif callable(set_signal_entries):
                set_signal_entries(list(signal_entries))
        if replay_snapshot is not None:
            set_replay_snapshot = getattr(window, "set_replay_snapshot", None)
            if not callable(set_replay_snapshot):
                self._fail_window(window, "RESULT_VIEW_API_UNAVAILABLE")
                return False
            try:
                set_replay_snapshot(replay_snapshot)
            except Exception as exc:
                self._fail_window(window, f"RESULT_VIEW_ERROR: {exc}")
                return False
        else:
            self._replay_from_pool(
                window,
                session,
                view_pool,
                evaluation_count=evaluation_count,
            )
        trace_signal_validation_event(
            "flow.apply_pool.end",
            window_key=window_key,
            request_generation=self._request_generation.get(window_key, 0),
            window_range_after=getattr(window, "validation_range", None),
            window_range_times_after=getattr(
                window, "_validation_range_times", None
            ),
            window_display_after=trace_candle_signature(
                getattr(window, "_candles", None)
            ),
            window_calculation_after=trace_candle_signature(
                getattr(window, "_calculation_candles", None)
            ),
            range_replay_available=bool(
                getattr(window, "_range_replay_entries_available", False)
            ),
            completed_cycle_count=len(
                tuple(getattr(window, "completed_cycles", ()) or ())
            ),
        )
        self._complete_history_extension(window_key, success=True)
        self._ensure_integrated_display_sidecar(
            window,
            session,
            chart_candle_count,
        )
        return True

    @staticmethod
    def _install_replay_cancel_predicate(
        replay: object,
        predicate: Callable[[], bool] | None,
    ) -> None:
        if predicate is None:
            return
        setter = getattr(replay, "set_cancel_predicate", None)
        if callable(setter):
            setter(predicate)

    def _prepare_replay_result_from_pool(
        self,
        session: ValidationSession,
        pool: object,
        *,
        history_target: int,
        evaluation_count: int | None = None,
        chart_range: tuple[int, int] | None = None,
        chart_range_times: tuple[str, str] | None = None,
        cancel_predicate: Callable[[], bool] | None = None,
        prepared_view_pool: dict[str, object] | None = None,
    ) -> ValidationReplayResult:
        if not isinstance(pool, dict):
            raise ValueError("HISTORICAL_POOL_UNAVAILABLE")
        rules = session.request.settings_snapshot.to_dict()
        warmup_bars = required_validation_history_context_bars(rules)
        active_pool = prepared_view_pool
        if active_pool is None:
            active_pool = self._validation_pool_for_session(
                session,
                pool,
                target_count=history_target + warmup_bars,
            )
        candles = active_pool.get("candles")
        if not isinstance(candles, list) or not candles:
            raise ValueError("HISTORICAL_POOL_EMPTY")

        visible_candles = active_pool.get("display_candles")
        if isinstance(visible_candles, list):
            visible_candles = visible_candles[-history_target:]
            if not visible_candles:
                raise ValueError("DISPLAY_CANDLES_UNAVAILABLE")
            display_only_integrated = (
                active_pool.get("display_only_integrated") is True
            )
            chart_indexes = (
                _calculation_indexes_for_mixed_display(
                    candles,
                    visible_candles,
                )
                if display_only_integrated
                else _calculation_indexes_for_display(
                    candles,
                    visible_candles,
                )
            )
            chart_count = len(chart_indexes)
            chart_visible_candles = visible_candles
        else:
            display_only_integrated = False
            chart_count = min(history_target, len(candles))
            chart_start = len(candles) - chart_count
            chart_indexes = tuple(range(chart_start, len(candles)))
            chart_visible_candles = candles[
                chart_start : chart_start + chart_count
            ]
        if chart_range is None:
            count = max(1, min(int(evaluation_count or 1), chart_count))
            evaluation_start = len(candles) - count
            evaluation_end = len(candles) - 1
        else:
            start, end = chart_range
            if chart_range_times is not None:
                if (
                    not isinstance(chart_range_times, tuple)
                    or len(chart_range_times) != 2
                    or not all(str(value or "") for value in chart_range_times)
                ):
                    raise ValueError("INVALID_CACHED_REPLAY_RANGE_TIMES")
                indexes_by_time: dict[str, int] = {}
                duplicate_times: set[str] = set()
                for index, candle in enumerate(chart_visible_candles):
                    candle_time = str(candle.get("time") or "")
                    if not candle_time:
                        continue
                    if candle_time in indexes_by_time:
                        duplicate_times.add(candle_time)
                    else:
                        indexes_by_time[candle_time] = index
                start_time, end_time = (
                    str(chart_range_times[0]),
                    str(chart_range_times[1]),
                )
                if (
                    start_time in duplicate_times
                    or end_time in duplicate_times
                    or start_time not in indexes_by_time
                    or end_time not in indexes_by_time
                ):
                    raise ValueError("RANGE_REPLAY_TIMESTAMP_UNAVAILABLE")
                start = indexes_by_time[start_time]
                end = indexes_by_time[end_time]
                start, end = min(start, end), max(start, end)
            if (
                isinstance(start, bool)
                or isinstance(end, bool)
                or not isinstance(start, int)
                or not isinstance(end, int)
                or not 0 <= start <= end < chart_count
            ):
                raise ValueError("INVALID_CACHED_REPLAY_RANGE")
            if display_only_integrated:
                calculation_bounds = _calculation_bounds_for_display_range(
                    candles,
                    chart_visible_candles,
                    start,
                    end,
                )
                if calculation_bounds is None:
                    raise ValueError("NO_CALCULATION_CANDLES_IN_RANGE")
                evaluation_start, evaluation_end = calculation_bounds
            else:
                evaluation_start = chart_indexes[start]
                evaluation_end = chart_indexes[end]
            count = evaluation_end - evaluation_start + 1

        if chart_range is None:
            slice_start = max(0, evaluation_start - warmup_bars)
            replay_candles = [
                dict(candle)
                for candle in candles[slice_start : evaluation_end + 1]
            ]
            replay_start_index = None
            replay_end_index = None
        else:
            # Keep the same historical prefix as the base scan so fixed
            # indicators retain identical values.  Only the position-dependent
            # replay state starts at the selected range boundary.
            replay_candles = [
                dict(candle)
                for candle in candles[: evaluation_end + 1]
            ]
            replay_start_index = evaluation_start
            replay_end_index = evaluation_end
        if not replay_candles:
            raise ValueError("CACHED_REPLAY_INPUT_EMPTY")

        replay_historical = ValidationHistoricalSnapshot(
            stock=session.request.stock,
            timeframe_minutes=session.request.timeframe_minutes,
            timeframe_key=str(active_pool.get("timeframe_key") or ""),
            requested_count=len(replay_candles),
            request_id=str(active_pool.get("request_id") or "POOL"),
            rows=self._historical_rows_from_candles(replay_candles),
            market_data_identity=str(active_pool.get("market_data_identity") or ""),
            market_source=str(active_pool.get("market_source") or ""),
        )

        detail_cache_key = None
        detail_cache_allowed = False
        if detail_cache_allowed:
            if cancel_predicate is not None and cancel_predicate():
                raise ValueError("VALIDATION_REPLAY_CANCELLED")
            try:
                replay_candles_json = json.dumps(
                    replay_candles,
                    ensure_ascii=False,
                    allow_nan=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                forming_status_json = json.dumps(
                    active_pool.get("forming_bar_status"),
                    ensure_ascii=False,
                    allow_nan=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                detail_cache_key = (
                    "PRODUCTION_DETAIL_REPLAY_V1",
                    # Reentry copies the pool mapping but keeps its candle pool.
                    id(pool.get("candles")),
                    session.request.settings_snapshot.rules_hash,
                    session.request.stock.code,
                    session.request.stock.name,
                    session.request.timeframe_minutes,
                    session.request.timeframe_key,
                    active_pool.get("timeframe_minutes"),
                    active_pool.get("timeframe_key"),
                    str(active_pool.get("market_data_identity") or ""),
                    str(active_pool.get("market_source") or ""),
                    int(history_target),
                    int(count),
                    int(evaluation_start),
                    int(evaluation_end),
                    int(slice_start),
                    len(replay_candles),
                    tuple(chart_indexes),
                    active_pool.get("production_calculation_cache_identity"),
                    forming_status_json,
                    active_pool.get("source_coverage_sufficient"),
                    active_pool.get("source_requested_count"),
                    str(active_pool.get("request_id") or ""),
                    hashlib.sha256(
                        replay_candles_json.encode("utf-8")
                    ).hexdigest(),
                )
            except (TypeError, ValueError, OverflowError):
                detail_cache_key = None

        cached_detail = pool.get("production_detail_replay_cache")
        if (
            detail_cache_key is not None
            and isinstance(cached_detail, tuple)
            and len(cached_detail) == 2
            and cached_detail[0] == detail_cache_key
            and isinstance(cached_detail[1], ValidationReplaySnapshot)
        ):
            if cancel_predicate is not None and cancel_predicate():
                raise ValueError("VALIDATION_REPLAY_CANCELLED")
            snapshot = self._detached_detail_replay_snapshot(cached_detail[1])
            if cancel_predicate is not None and cancel_predicate():
                raise ValueError("VALIDATION_REPLAY_CANCELLED")
            return ValidationReplayResult(ok=True, snapshot=snapshot)

        context_provider = self._context_provider_for_session(session)
        replay = self._replay_factory(session)
        self._install_replay_cancel_predicate(replay, cancel_predicate)
        evaluate_with_context = getattr(replay, "evaluate_with_context", None)
        evaluate = getattr(replay, "evaluate", None)
        if callable(evaluate_with_context):
            if replay_start_index is None:
                replay_result = evaluate_with_context(
                    replay_historical,
                    context_provider=context_provider,
                    display_count=count,
                )
            else:
                replay_result = evaluate_with_context(
                    replay_historical,
                    context_provider=context_provider,
                    start_index=replay_start_index,
                    end_index=replay_end_index,
                )
        elif callable(evaluate):
            if replay_start_index is None:
                replay_result = evaluate(
                    replay_historical,
                    display_count=count,
                )
            else:
                replay_result = evaluate(
                    replay_historical,
                    start_index=replay_start_index,
                    end_index=replay_end_index,
                )
        else:
            raise TypeError("replay evaluator is unavailable")
        if (
            not isinstance(replay_result, ValidationReplayResult)
            or replay_result.ok is not True
            or replay_result.snapshot is None
        ):
            raise ValueError(self._failure_text("REPLAY", replay_result))
        if detail_cache_key is not None and (
            cancel_predicate is None or not cancel_predicate()
        ):
            pool["production_detail_replay_cache"] = (
                detail_cache_key,
                self._detached_detail_replay_snapshot(replay_result.snapshot),
            )
        return replay_result

    @staticmethod
    def _detached_detail_replay_snapshot(
        snapshot: ValidationReplaySnapshot,
    ) -> ValidationReplaySnapshot:
        return ValidationReplaySnapshot(
            stock=snapshot.stock,
            timeframe_minutes=snapshot.timeframe_minutes,
            timeframe_key=snapshot.timeframe_key,
            settings_hash=snapshot.settings_hash,
            historical_request_id=snapshot.historical_request_id,
            evaluated_start_index=snapshot.evaluated_start_index,
            evaluated_end_index=snapshot.evaluated_end_index,
            dropped_raw_rows_count=snapshot.dropped_raw_rows_count,
            candles=snapshot.to_candles(),
            entries=snapshot.to_entries(),
        )

    def _use_pool_for_window(
        self,
        window: object,
        session: ValidationSession,
        pool: dict[str, object],
        *,
        evaluation_count: int,
        source_identity: tuple[object, ...] | None = None,
    ) -> None:
        window_key = id(window)
        self._clear_window_candle_preview(window_key, self._prepare_active.get(window_key))
        self._historical_pools[window_key] = pool
        self._validation_sessions[window_key] = session
        request = {
            "window": window,
            "session": session,
            "pool": pool,
            "evaluation_count": evaluation_count,
            "generation": self._request_generation.get(window_key, 0),
            "dispatch_snapshot": self._pool_dispatch_snapshot(
                pool, source_identity=source_identity,
            ),
            "dispatch_source_identity": pool["_dispatch_snapshot"][3],
            "token": object(),
        }
        self._range_replay_pending.pop(window_key, None)
        if window_key in self._range_replay_generation:
            self._range_replay_generation[window_key] += 1
        if window_key in self._range_replay_active:
            self._prepare_pending[window_key] = request
            return
        if window_key in self._prepare_active:
            self._prepare_pending[window_key] = request
            return
        self._start_pool_prepare(request)

    def _prepare_request_is_current(self, request: object) -> bool:
        if not isinstance(request, dict):
            return False
        window = request.get("window")
        session = request.get("session")
        pool = request.get("pool")
        window_key = id(window)
        return (
            self._open_windows.get(window_key) is window
            and self._request_generation.get(window_key) == request.get("generation")
            and self._historical_pools.get(window_key) is pool
            and self._validation_sessions.get(window_key) is session
        )

    def _start_pool_prepare(self, request: dict[str, object]) -> None:
        window = request["window"]
        session = request["session"]
        pool = request["pool"]
        evaluation_count = request["evaluation_count"]
        generation = request["generation"]
        token = request["token"]
        if (
            not isinstance(session, ValidationSession)
            or not isinstance(pool, dict)
            or isinstance(evaluation_count, bool)
            or not isinstance(evaluation_count, int)
            or isinstance(generation, bool)
            or not isinstance(generation, int)
            or not self._prepare_request_is_current(request)
        ):
            return
        window_key = id(window)

        history_target = self._history_targets.get(
            window_key,
            self._historical_count,
        )
        settings_hash = session.request.settings_snapshot.rules_hash
        cached_by_settings = pool.setdefault(
            "signal_entries_by_settings_hash",
            {},
        )
        cached_signatures = pool.setdefault(
            "signal_entries_signature_by_settings_hash",
            {},
        )
        replay = self._replay_factory(session)
        scan = getattr(replay, "scan_signal_entries", None)
        # Warm dispatch captures published input references, independent of source
        # length. Make each window's mutable copy inside the existing worker.
        dispatch_snapshot = request["dispatch_snapshot"]
        dispatch_source_identity = request.get("dispatch_source_identity")
        worker_pool = dict(pool)
        source_candles_id = id(worker_pool.get("candles"))
        worker_pool.pop("_dispatch_snapshot", None)
        projected_cache = worker_pool.pop("production_projected_view_cache", None)
        prepared_projected_cache = None
        projection_as_of = self._now_factory()
        target_count = history_target + required_validation_history_context_bars(
            session.request.settings_snapshot.to_dict()
        )

        def prepare_view() -> tuple[dict[str, object], object, object, object]:
            nonlocal prepared_projected_cache
            # Bind reuse to the identity already verified before dispatch. All
            # remaining key material is small and evaluated in this worker.
            timeframe = normalize_validation_timeframe(
                session.request.timeframe_key,
                fallback_minutes=session.request.timeframe_minutes,
            )
            contract = dispatch_snapshot.get("production_session_contract")
            projection_key = None
            if (
                callable(scan) and dispatch_source_identity
                and worker_pool.get("timeframe_key") == "M1"
                and timeframe["kind"] == "MINUTE"
                and isinstance(contract, dict)
                and contract.get("status") == "PRODUCTION_SESSION_READY"
                and contract.get("ready") is True
            ):
                projection_key = (
                    dispatch_source_identity, source_candles_id, session.request.stock,
                    settings_hash, session.request.timeframe_key,
                    session.request.timeframe_minutes, timeframe["key"],
                    timeframe["minutes"], worker_pool.get("timeframe_minutes"),
                    history_target, target_count,
                    json.dumps(contract, ensure_ascii=False, sort_keys=True,
                               allow_nan=False, separators=(",", ":")),
                )
            as_of = projection_as_of
            as_of = (as_of.replace(tzinfo=SEOUL_TIMEZONE) if as_of.tzinfo is None
                     else as_of.astimezone(SEOUL_TIMEZONE))
            as_of_minute = as_of.replace(second=0, microsecond=0)
            if (
                projection_key is not None
                and isinstance(projected_cache, tuple) and len(projected_cache) == 4
                and projected_cache[0] == projection_key
                and isinstance(projected_cache[1], datetime)
                and isinstance(projected_cache[3], bytes)
                and projected_cache[2] == (
                    "COMPLETED_SOURCE" if as_of >= projected_cache[1] else as_of_minute
                )
            ):
                # Bytes are an immutable detached template; decoding gives each
                # worker independent candles, snapshot rows and nested metadata.
                worker_pool.update(pickle.loads(projected_cache[3]))
                view_pool = worker_pool
            else:
                worker_pool.update(deepcopy(dispatch_snapshot))
                view_pool = None
            cached_source = worker_pool.get("_source_identity_content_cache")
            # An existing immutable binding for this published content needs no
            # second full scan in the worker. Cold/changed publications bind the
            # fingerprint to canonical JSON on the detached worker source.
            if view_pool is None and callable(scan) and not (
                isinstance(cached_source, tuple) and len(cached_source) == 2
                and isinstance(dispatch_source_identity, tuple) and dispatch_source_identity
                and cached_source[1] == dispatch_source_identity[-1]
            ):
                self._pool_computation_source_identity(worker_pool, _cache_flat_content=True)
            if view_pool is None:
                view_pool = self._validation_pool_for_session(
                    session, worker_pool, target_count=target_count,
                    as_of=projection_as_of,
                )
                if (
                    projection_key is not None
                    and "forming_bar_status" in view_pool
                ):
                    # Every bucket end is <= its latest source minute + target
                    # duration. Past this conservative bound, clocks cannot
                    # change completion. Before it, require the same minute.
                    source_times = (
                        candle_market_datetime(candle)
                        for candle in worker_pool["candles"]
                    )
                    latest = max((stamp for stamp in source_times if stamp is not None),
                                 default=None)
                    if latest is not None:
                        complete_after = latest + timedelta(minutes=int(timeframe["minutes"]))
                        fields = (
                            "candles", "display_candles", "snapshot",
                            "timeframe_key", "timeframe_minutes", "requested_count",
                            "source_timeframe_key", "source_timeframe_minutes",
                            "source_requested_count", "source_coverage_sufficient",
                            "forming_bar_status", "calculation_source",
                            "production_calculation_cache_identity",
                            "production_session_contract",
                        )
                        prepared_projected_cache = (
                            projection_key, complete_after,
                            "COMPLETED_SOURCE" if as_of >= complete_after else as_of_minute,
                            pickle.dumps({field: view_pool[field] for field in fields}, protocol=4),
                        )
            historical_snapshot = view_pool.get("snapshot")
            active_signal_signature = self._signal_view_signature(view_pool)
            signal_entries = (
                cached_by_settings.get(settings_hash)
                if (
                    isinstance(cached_by_settings, dict)
                    and isinstance(cached_signatures, dict)
                    and cached_signatures.get(settings_hash) == active_signal_signature
                )
                else None
            )
            return (view_pool, historical_snapshot, active_signal_signature,
                    tuple(signal_entries) if signal_entries is not None else None)

        # Retain the synchronous compatibility path for evaluators without scans.
        # Normal validation (cold and warm) always prepares its view in the worker.
        legacy_view = None
        if not callable(scan):
            legacy_view = prepare_view()
            if legacy_view[3] is None:
                self._apply_pool_to_window(
                    window, session, pool, evaluation_count=evaluation_count,
                    signal_entries=None, prepared_view_pool=legacy_view[0],
                )
                return

        payload_base = {
            "window_key": window_key,
            "generation": generation,
            "prepare_token": token,
            "session": session,
            "pool": pool,
            "evaluation_count": evaluation_count,
            "settings_hash": settings_hash,
            "history_target": history_target,
        }
        self._prepare_active[window_key] = token

        def prepare_result() -> None:
            trace_timing = signal_validation_trace_enabled()
            prepare_started = time.perf_counter() if trace_timing else 0.0
            phase_ms: dict[str, float] = {}
            trace_view_candle_count = 0
            scan_reused = False
            full_replay_reused = False
            presentation_reused = False

            def phase_start() -> float:
                return time.perf_counter() if trace_timing else 0.0

            def phase_end(name: str, started: float) -> None:
                if trace_timing:
                    phase_ms[name] = round(
                        (time.perf_counter() - started) * 1000.0,
                        3,
                    )

            def valid() -> bool:
                return (
                    self._prepare_active.get(window_key) is token
                    and self._prepare_request_is_current(request)
                    and self._history_targets.get(window_key, self._historical_count)
                    == history_target
                )

            def current() -> bool:
                return (
                    valid()
                    and window_key not in self._prepare_pending
                )

            def cancel() -> None:
                payload = dict(payload_base)
                payload["cancelled"] = True
                payload["error"] = ""
                self._emit_signal_scan_completed(payload)

            try:
                if not current():
                    cancel()
                    return
                view_started = phase_start()
                (view_pool, historical_snapshot, active_signal_signature,
                 cached_entries) = legacy_view if legacy_view is not None else prepare_view()
                phase_end("view", view_started)
                trace_view_candles = view_pool.get("candles")
                trace_view_candle_count = (
                    len(trace_view_candles)
                    if isinstance(trace_view_candles, list)
                    else 0
                )
                payload_base["source_identity_content_cache"] = worker_pool.get(
                    "_source_identity_content_cache"
                )
                if prepared_projected_cache is not None:
                    payload_base["projected_view_cache"] = prepared_projected_cache
                    payload_base["projected_view_dispatch_snapshot"] = dispatch_snapshot
                if not current():
                    cancel()
                    return
                payload_base["active_signal_signature"] = active_signal_signature
                self._install_replay_cancel_predicate(
                    replay,
                    lambda: not current(),
                )
                rules = session.request.settings_snapshot.to_dict()
                signal_rule_support = require_signal_rule_support(rules)
                if cached_entries is not None:
                    scan_reused = True
                    optimized_entries = cached_entries
                    phase_ms["scan"] = 0.0
                else:
                    scan_started = phase_start()
                    optimized_entries = tuple(
                        scan(
                            historical_snapshot,
                            context_provider=self._context_provider_for_session(
                                session
                            ),
                        )
                    )
                    phase_end("scan", scan_started)
                if not current():
                    cancel()
                    return
                detail_started = phase_start()
                replay_result = self._prepare_replay_result_from_pool(
                    session,
                    pool,
                    history_target=history_target,
                    evaluation_count=evaluation_count,
                    cancel_predicate=lambda: not current(),
                    prepared_view_pool=view_pool,
                )
                phase_end("detail_replay", detail_started)
                replay_snapshot = replay_result.snapshot
                evaluated_candles = replay_snapshot.to_candles()[
                    replay_snapshot.evaluated_start_index:
                    replay_snapshot.evaluated_end_index + 1
                ]
                evaluated_times = {
                    str(candle.get("time") or "")
                    for candle in evaluated_candles
                }
                display_authoritative_entries = emitted_signal_entries(
                    replay_snapshot.to_entries()
                )
                optimized_window_entries = tuple(
                    entry
                    for entry in optimized_entries
                    if entry.evaluation_time in evaluated_times
                )
                if not current():
                    cancel()
                    return
                production_m1_parity = False
                active_candles_for_markers = view_pool.get("candles")
                if (
                    not isinstance(active_candles_for_markers, list)
                    or not active_candles_for_markers
                ):
                    raise ValueError("HISTORICAL_POOL_EMPTY")
                display_candles_for_markers = view_pool.get("display_candles")
                if isinstance(display_candles_for_markers, list):
                    display_candles_for_markers = display_candles_for_markers[
                        -history_target:
                    ]
                else:
                    display_candles_for_markers = active_candles_for_markers[
                        -min(history_target, len(active_candles_for_markers)):
                    ]
                detail_coverage_ok = bool(
                    evaluated_candles
                    and display_candles_for_markers
                    and str(evaluated_candles[0].get("time") or "")
                    and str(evaluated_candles[-1].get("time") or "")
                    and str(display_candles_for_markers[0].get("time") or "")
                    >= str(evaluated_candles[0].get("time") or "")
                    and str(display_candles_for_markers[-1].get("time") or "")
                    <= str(evaluated_candles[-1].get("time") or "")
                )
                full_marker_replay_required = (
                    production_m1_parity or not detail_coverage_ok
                )
                full_marker_entries = None
                parity_cache = None
                parity_cache_key = None
                if production_m1_parity:
                    parity_cache = pool.setdefault(
                        "full_parity_entries_by_key",
                        {},
                    )
                    parity_cache_key = (
                        settings_hash,
                        active_signal_signature,
                        str(
                            view_pool.get(
                                "production_calculation_cache_identity"
                            )
                            or ""
                        ),
                    )
                    full_marker_entries = (
                        parity_cache.get(parity_cache_key)
                        if isinstance(parity_cache, dict)
                        else None
                    )
                    full_replay_reused = full_marker_entries is not None
                if full_marker_replay_required and full_marker_entries is None:
                    full_replay_started = phase_start()
                    parity_candles, _parity_dropped = project_validation_candles(
                        historical_snapshot
                    )
                    parity_replay = self._replay_factory(session)
                    set_parallel_workers = getattr(
                        parity_replay,
                        "set_parallel_base_series_workers",
                        None,
                    )
                    if (
                        self._full_parity_workers > 1
                        and callable(set_parallel_workers)
                    ):
                        set_parallel_workers(self._full_parity_workers)
                    self._install_replay_cancel_predicate(
                        parity_replay,
                        lambda: not current(),
                    )
                    parity_evaluate_with_context = getattr(
                        parity_replay,
                        "evaluate_with_context",
                        None,
                    )
                    parity_evaluate = getattr(parity_replay, "evaluate", None)
                    if callable(parity_evaluate_with_context):
                        parity_replay_result = parity_evaluate_with_context(
                            historical_snapshot,
                            context_provider=self._context_provider_for_session(
                                session
                            ),
                            display_count=len(parity_candles),
                        )
                    elif callable(parity_evaluate):
                        parity_replay_result = parity_evaluate(
                            historical_snapshot,
                            display_count=len(parity_candles),
                        )
                    else:
                        raise TypeError("parity replay evaluator is unavailable")
                    if (
                        not isinstance(parity_replay_result, ValidationReplayResult)
                        or parity_replay_result.ok is not True
                        or parity_replay_result.snapshot is None
                    ):
                        raise ValueError(
                            self._failure_text(
                                "PARITY_REPLAY",
                                parity_replay_result,
                            )
                        )
                    full_marker_entries = tuple(
                        emitted_signal_entries(
                            parity_replay_result.snapshot.to_entries()
                        )
                    )
                    phase_end("full_replay", full_replay_started)
                    if not valid():
                        cancel()
                        return
                    if (
                        production_m1_parity
                        and isinstance(parity_cache, dict)
                        and parity_cache_key is not None
                    ):
                        parity_cache[parity_cache_key] = full_marker_entries
                    if not current():
                        cancel()
                        return
                if full_marker_replay_required and full_replay_reused:
                    phase_ms["full_replay"] = 0.0
                parity_started = phase_start()
                if full_marker_replay_required:
                    if production_m1_parity:
                        parity_result_key = (
                            parity_cache_key, dispatch_source_identity,
                            session.request.stock, session.request.timeframe_key,
                            session.request.timeframe_minutes, history_target,
                        )
                        authoritative_signature = signal_replay_entries_cache_signature(
                            full_marker_entries
                        )
                        optimized_signature = signal_replay_entries_cache_signature(
                            optimized_entries
                        )
                        cached_parity = pool.get("signal_replay_parity_cache")
                        reusable = (
                            authoritative_signature is not None
                            and optimized_signature is not None
                        )
                        if (
                            reusable
                            and isinstance(cached_parity, tuple)
                            and len(cached_parity) == 4
                            and cached_parity[:3] == (
                                parity_result_key, authoritative_signature,
                                optimized_signature,
                            )
                        ):
                            parity_report = cached_parity[3]
                        else:
                            parity_report = require_signal_replay_parity(
                                full_marker_entries, optimized_entries,
                            )
                            if reusable:
                                # Publish after the GUI's stale-result gates.
                                # PASS has no mutable evidence; to_dict detaches
                                # every delivery. Keep one report per pool.
                                payload_base["signal_replay_parity_cache"] = (
                                    parity_result_key, authoritative_signature,
                                    optimized_signature, parity_report,
                                )
                                payload_base["signal_replay_parity_dispatch_snapshot"] = dispatch_snapshot
                    else:
                        parity_report = compare_signal_replay_entries(
                            full_marker_entries,
                            optimized_entries,
                        )
                        if not parity_report.ok:
                            summary = json.dumps(
                                parity_report.to_dict(),
                                ensure_ascii=False,
                                sort_keys=True,
                            )
                            raise ValueError(
                                f"BACKTEST_SIGNAL_PARITY_MISMATCH: {summary}"
                            )
                    parity_report_dict = parity_report.to_dict()
                else:
                    parity_report_dict = compare_signal_replay_entries(
                        display_authoritative_entries,
                        optimized_window_entries,
                    ).to_dict()
                phase_end("parity_compare", parity_started)
                if not current():
                    cancel()
                    return
                gates_started = phase_start()
                active_pool = view_pool
                candle_projection_parity = candle_projection_parity_report(
                    historical_snapshot,
                    calculation_provenance=active_pool,
                )
                stock = session.request.stock
                try:
                    production_contract = self._production_session_contract_reader(
                        stock.code,
                        stock.name,
                    )
                except Exception:
                    production_contract = unavailable_production_session_contract(
                        "PRODUCTION_SESSION_READER_FAILED"
                    )
                market_session_parity = market_session_parity_report(
                    production_contract,
                    rules.get("validation_market_scope")
                    if isinstance(rules, dict)
                    else None,
                    timeframe_key=session.request.timeframe_key,
                    timeframe_minutes=session.request.timeframe_minutes,
                    calculation_provenance=active_pool,
                )
                filter_signal_authorization = (
                    filter_signal_backtest_authorization_report(
                        signal_rule_support,
                        parity_report_dict,
                        market_session_parity,
                        candle_projection_parity,
                    )
                )
                trust_metadata = {
                    "signal_parity": deepcopy(parity_report_dict),
                    "execution_model": execution_model_report(rules),
                    "candle_projection_parity": candle_projection_parity,
                    "market_session_parity": market_session_parity,
                    "filter_signal_backtest_authorization": (
                        filter_signal_authorization
                    ),
                    "production_decision_scope": (
                        production_decision_scope_report(rules)
                    ),
                }
                phase_end("authorization_gates", gates_started)
                # Keep detailed replay data separate from full marker authority.
                entries = display_authoritative_entries
                marker_entries = (
                    full_marker_entries
                    if full_marker_replay_required
                    else display_authoritative_entries
                )
                if not current():
                    cancel()
                    return
                active_candles = active_pool.get("candles")
                if not isinstance(active_candles, list) or not active_candles:
                    raise ValueError("HISTORICAL_POOL_EMPTY")
                marker_coverage_candles = (
                    active_candles
                    if full_marker_replay_required
                    else evaluated_candles
                )
                if marker_coverage_candles:
                    trust_metadata["signal_marker_coverage"] = {
                        "start_time": str(
                            marker_coverage_candles[0].get("time") or ""
                        ),
                        "end_time": str(
                            marker_coverage_candles[-1].get("time") or ""
                        ),
                        "source": (
                            "FULL_PARITY"
                            if production_m1_parity
                            else (
                                "FULL_REPLAY"
                                if full_marker_replay_required
                                else "DETAIL_REPLAY"
                            )
                        ),
                    }
                display_candles = active_pool.get("display_candles")
                if isinstance(display_candles, list):
                    display_candles = display_candles[-history_target:]
                    if not display_candles:
                        raise ValueError("DISPLAY_CANDLES_UNAVAILABLE")
                else:
                    display_candles = None
                chart_count = (
                    len(display_candles)
                    if display_candles is not None
                    else min(history_target, len(active_candles))
                )
                chart_start = len(active_candles) - chart_count
                chart_indexes = tuple(
                    range(chart_start, chart_start + chart_count)
                )
                display_only_integrated = (
                    active_pool.get("display_only_integrated") is True
                    and display_candles is not None
                )
                if display_candles is not None:
                    chart_indexes = (
                        _calculation_indexes_for_mixed_display(
                            active_candles,
                            display_candles,
                        )
                        if display_only_integrated
                        else _calculation_indexes_for_display(
                            active_candles,
                            display_candles,
                        )
                    )
                    backed_indexes = tuple(
                        index for index in chart_indexes if index is not None
                    )
                    chart_start = (
                        backed_indexes[0]
                        if backed_indexes
                        else max(0, len(active_candles) - 1)
                    )

                presentation_key = None
                cached_base = None
                if production_m1_parity:
                    presentation_key = (
                        parity_cache_key,
                        history_target,
                        chart_start,
                        chart_count,
                        chart_indexes,
                        hashlib.sha256(
                            json.dumps(
                                [active_candles, display_candles],
                                ensure_ascii=False,
                                sort_keys=True,
                                allow_nan=False,
                                separators=(",", ":"),
                            ).encode("utf-8")
                        ).hexdigest(),
                    )
                    presentation_cache = pool.get(
                        "production_presentation_cache"
                    )
                    if (
                        isinstance(presentation_cache, tuple)
                        and len(presentation_cache) == 3
                        and presentation_cache[0] == presentation_key
                        and presentation_cache[1] is full_marker_entries
                    ):
                        cached_base = presentation_cache[2]

                presentation_started = phase_start()
                if cached_base is not None:
                    presentation_reused = True
                    cached_evidence = (
                        cached_base.visualization_evidence_identities_by_side
                    )
                    prepared_presentation = replace(
                        cached_base,
                        visualization_active_by_marker=dict(
                            cached_base.visualization_active_by_marker
                        ),
                        signal_tooltips=dict(cached_base.signal_tooltips),
                        visualization_evidence_identities_by_side=(
                            None
                            if cached_evidence is None
                            else {
                                side: tuple(values)
                                for side, values in cached_evidence.items()
                            }
                        ),
                        trust_metadata=deepcopy(trust_metadata),
                    )
                else:
                    prepared_presentation = prepare_signal_validation_presentation(
                        active_candles,
                        marker_entries,
                        session.request.settings_snapshot,
                        chart_start_index=chart_start,
                        chart_count=chart_count,
                        trust_metadata=trust_metadata,
                        display_candles=display_candles,
                        display_only_integrated=display_only_integrated,
                    )
                    if production_m1_parity:
                        prepared_evidence = (
                            prepared_presentation
                            .visualization_evidence_identities_by_side
                        )
                        base_presentation = replace(
                            prepared_presentation,
                            visualization_active_by_marker=dict(
                                prepared_presentation.visualization_active_by_marker
                            ),
                            signal_tooltips=dict(
                                prepared_presentation.signal_tooltips
                            ),
                            visualization_evidence_identities_by_side=(
                                None
                                if prepared_evidence is None
                                else {
                                    side: tuple(values)
                                    for side, values in prepared_evidence.items()
                                }
                            ),
                            trust_metadata=None,
                        )
                        pool["production_presentation_cache"] = (
                            presentation_key,
                            full_marker_entries,
                            base_presentation,
                        )
                phase_end("presentation", presentation_started)
                if not current():
                    cancel()
                    return
                payload = dict(payload_base)
                payload["entries"] = entries
                payload["marker_entries"] = marker_entries
                payload["optimized_entries"] = optimized_entries
                payload["replay_snapshot"] = replay_result.snapshot
                payload["prepared_presentation"] = prepared_presentation
                payload["prepared_view_pool"] = view_pool
                payload["backtest_signal_rule_support"] = signal_rule_support
                payload["backtest_signal_parity"] = deepcopy(
                    parity_report_dict
                )
                payload["backtest_execution_parity"] = trust_metadata["execution_model"]
                payload["backtest_market_session_parity"] = deepcopy(
                    market_session_parity
                )
                payload["backtest_filter_signal_authorization"] = deepcopy(
                    filter_signal_authorization
                )
                payload["backtest_trust_metadata"] = trust_metadata
                payload["error"] = ""
            except Exception as exc:
                if not current():
                    cancel()
                    return
                payload = dict(payload_base)
                payload["entries"] = ()
                payload["optimized_entries"] = ()
                payload["replay_snapshot"] = None
                payload["prepared_presentation"] = None
                payload["error"] = str(exc)
            if trace_timing:
                trace_signal_validation_event(
                    "flow.prepare.phase_timing",
                    window_key=window_key,
                    generation=generation,
                    history_target=history_target,
                    view_candle_count=trace_view_candle_count,
                    scan_reused=scan_reused,
                    full_replay_reused=full_replay_reused,
                    presentation_reused=presentation_reused,
                    phase_ms=phase_ms,
                    total_ms=round(
                        (time.perf_counter() - prepare_started) * 1000.0,
                        3,
                    ),
                    error=bool(str(payload.get("error") or "").strip()),
                )
            self._emit_signal_scan_completed(payload)

        try:
            Thread(
                target=prepare_result,
                name="indicator-follow-validation-prepare",
                daemon=True,
            ).start()
        except Exception as exc:
            payload = dict(payload_base)
            payload["entries"] = ()
            payload["optimized_entries"] = ()
            payload["replay_snapshot"] = None
            payload["prepared_presentation"] = None
            payload["error"] = str(exc)
            self._on_signal_scan_completed(payload)

    def _emit_signal_scan_completed(self, payload: object) -> bool:
        """Emit prepare completion unless this QObject was already torn down."""
        if sip.isdeleted(self):
            return False
        try:
            self.signal_scan_completed.emit(payload)
        except RuntimeError:
            if sip.isdeleted(self):
                return False
            raise
        return True

    def _on_signal_scan_completed(self, payload: object) -> None:
        if not isinstance(payload, dict):
            return
        window_key = payload.get("window_key")
        generation = payload.get("generation")
        token = payload.get("prepare_token")
        prepared_view_pool = payload.get("prepared_view_pool")
        trace_signal_validation_event(
            "flow.prepare.complete_received",
            window_key=window_key,
            generation=generation,
            active_token_match=(
                isinstance(window_key, int)
                and self._prepare_active.get(window_key) is token
            ),
            current_request_generation=(
                self._request_generation.get(window_key, 0)
                if isinstance(window_key, int) else None
            ),
            error=str(payload.get("error") or ""),
            entry_count=len(tuple(payload.get("entries") or ())),
            marker_entry_count=len(
                tuple(payload.get("marker_entries", payload.get("entries") or ()) or ())
            ),
            prepared_view_timeframe=(
                str(prepared_view_pool.get("timeframe_key") or "")
                if isinstance(prepared_view_pool, dict) else ""
            ),
            prepared_view_source_timeframe=(
                str(prepared_view_pool.get("source_timeframe_key") or "")
                if isinstance(prepared_view_pool, dict) else ""
            ),
            prepared_view_calculation_source=(
                str(prepared_view_pool.get("calculation_source") or "")
                if isinstance(prepared_view_pool, dict) else ""
            ),
            prepared_view_candles=trace_candle_signature(
                prepared_view_pool.get("candles")
                if isinstance(prepared_view_pool, dict) else None
            ),
            prepared_view_display=trace_candle_signature(
                prepared_view_pool.get("display_candles")
                if isinstance(prepared_view_pool, dict) else None
            ),
        )
        if (
            isinstance(window_key, bool)
            or not isinstance(window_key, int)
            or self._prepare_active.get(window_key) is not token
        ):
            return
        pending = self._prepare_pending.pop(window_key, None)
        if pending is not None:
            self._clear_window_candle_preview(window_key, token)
            self._prepare_active.pop(window_key, None)
            self._range_replay_pending.pop(window_key, None)
            if self._prepare_request_is_current(pending):
                self._start_pool_prepare(pending)
            return
        if (
            payload.get("cancelled") is True
            or self._request_generation.get(window_key) != generation
            or (
                "history_target" in payload
                and payload["history_target"]
                != self._history_targets.get(window_key, self._historical_count)
            )
        ):
            self._clear_window_candle_preview(window_key, token)
            self._prepare_active.pop(window_key, None)
            self._range_replay_pending.pop(window_key, None)
            return
        window = self._open_windows.get(window_key)
        if window is None:
            self._prepare_active.pop(window_key, None)
            self._range_replay_pending.pop(window_key, None)
            return
        if not self._window_accepts_results(window):
            self._clear_window_candle_preview(window_key, token)
            self._prepare_active.pop(window_key, None)
            self._prepare_pending.pop(window_key, None)
            self._range_replay_pending.pop(window_key, None)
            self._complete_history_extension(window_key, success=False)
            return
        error = str(payload.get("error") or "").strip()
        if error:
            self._prepare_active.pop(window_key, None)
            self._range_replay_pending.pop(window_key, None)
            self._fail_window(window, f"SIGNAL_SCAN_ERROR: {error}")
            return
        session = payload.get("session")
        pool = payload.get("pool")
        if not isinstance(session, ValidationSession) or not isinstance(pool, dict):
            self._prepare_active.pop(window_key, None)
            self._range_replay_pending.pop(window_key, None)
            self._fail_window(window, "SIGNAL_SCAN_ERROR: invalid prepare result")
            return
        if self._historical_pools.get(window_key) is not pool:
            self._clear_window_candle_preview(window_key, token)
            self._prepare_active.pop(window_key, None)
            self._range_replay_pending.pop(window_key, None)
            return
        if "source_identity_content_cache" in payload:
            pool["_source_identity_content_cache"] = payload["source_identity_content_cache"]
        publication = pool.get("_dispatch_snapshot")
        if (
            "signal_replay_parity_cache" in payload
            and isinstance(publication, tuple) and len(publication) == 4
            and publication[2] is payload.get("signal_replay_parity_dispatch_snapshot")
            and self._validation_sessions.get(window_key) is session
        ):
            pool["signal_replay_parity_cache"] = payload["signal_replay_parity_cache"]
        if (
            "projected_view_cache" in payload
            and isinstance(publication, tuple) and len(publication) == 4
            and publication[2] is payload.get("projected_view_dispatch_snapshot")
            and self._validation_sessions.get(window_key) is session
        ):
            # Publish only after the existing generation/target/token gates and
            # only for the still-authoritative source publication. No GUI copy.
            pool["production_projected_view_cache"] = payload["projected_view_cache"]
        entries = tuple(payload.get("entries") or ())
        marker_entries = tuple(payload.get("marker_entries", entries) or ())
        optimized_entries = tuple(
            payload.get("optimized_entries")
            if "optimized_entries" in payload
            else entries
        )
        settings_hash = str(payload.get("settings_hash") or "")
        cached_by_settings = pool.setdefault(
            "signal_entries_by_settings_hash",
            {},
        )
        cached_signatures = pool.setdefault(
            "signal_entries_signature_by_settings_hash",
            {},
        )
        active_signal_signature = payload.get("active_signal_signature")
        if (
            isinstance(cached_by_settings, dict)
            and isinstance(cached_signatures, dict)
            and settings_hash
            and isinstance(active_signal_signature, tuple)
            and len(active_signal_signature) == 5
        ):
            # Cache/persist the optimizer output, not the authoritative replay,
            # so every subsequent preparation still gates batch versus replay.
            cached_by_settings[settings_hash] = optimized_entries
            cached_signatures[settings_hash] = active_signal_signature
            active_timeframe_key, active_count, _first_time, _last_time, _source_identity = (
                active_signal_signature
            )
            raw_requested_count = int(pool.get("requested_count") or 0)
            if (
                raw_requested_count == active_count
                and str(pool.get("timeframe_key") or "")
                == str(active_timeframe_key)
            ):
                self._persist_signal_entries(
                    session.request.stock,
                    int(
                        pool.get("timeframe_minutes")
                        or session.request.timeframe_minutes
                    ),
                    str(pool.get("timeframe_key") or ""),
                    raw_requested_count,
                    settings_hash,
                    list(optimized_entries),
                )
        evaluation_count = payload.get("evaluation_count")
        if isinstance(evaluation_count, bool) or not isinstance(evaluation_count, int):
            self._prepare_active.pop(window_key, None)
            self._range_replay_pending.pop(window_key, None)
            self._fail_window(window, "SIGNAL_SCAN_ERROR: invalid evaluation count")
            return
        interaction_epoch = self._window_busy_epoch(window)
        if interaction_epoch is not None:
            self._prepare_deferred[window_key] = (
                interaction_epoch,
                dict(payload),
            )
            return
        replay_snapshot = payload.get("replay_snapshot")
        prepared_presentation = payload.get("prepared_presentation")
        try:
            applied = self._apply_pool_to_window(
                window,
                session,
                pool,
                evaluation_count=evaluation_count,
                signal_entries=marker_entries,
                replay_snapshot=replay_snapshot,
                prepared_presentation=prepared_presentation,
                prepared_view_pool=payload.get("prepared_view_pool"),
            )
        except Exception as exc:
            applied = False
            self._fail_window(window, f"RESULT_VIEW_ERROR: {exc}")

        # set_replay_snapshot may synchronously queue a newer prepare or range.
        pending = self._prepare_pending.pop(window_key, None)
        self._prepare_active.pop(window_key, None)
        if pending is not None:
            self._range_replay_pending.pop(window_key, None)
            if self._prepare_request_is_current(pending):
                self._start_pool_prepare(pending)
            return
        if applied is False:
            self._range_replay_pending.pop(window_key, None)
            return
        pending_range = self._range_replay_pending.pop(window_key, None)
        if pending_range is not None:
            self._start_range_replay(pending_range)

    def _validation_fetch_contract(
        self,
        session: ValidationSession,
        target_count: int,
        *,
        production_contract: dict[str, object] | None = None,
    ) -> tuple[ValidationSession, int, str, int]:
        return (
            session,
            session.request.timeframe_minutes,
            session.request.timeframe_key,
            target_count,
        )

    @staticmethod
    def _pool_computation_source_identity(
        pool: object, *, _cache_flat_content: bool = False,
    ) -> tuple[object, ...]:
        if not isinstance(pool, dict):
            return ()
        candles = pool.get("candles")
        first_time = ""
        last_time = ""
        candle_count = 0
        candle_content_hash = ""
        if isinstance(candles, list) and candles:
            candle_count = len(candles)
            first_time = str(candles[0].get("time") or "")
            last_time = str(candles[-1].get("time") or "")
            # Recheck every value on every call; no object-id/revision shortcut.
            # Populate only from a worker-owned detached source, so a mutation
            # between fingerprinting and JSON cannot bind different versions.
            # Nested/legacy rows retain canonical JSON's default=str semantics.
            # ponytail: O(N) binary scan; removing it needs authoritative revisions.
            fingerprint = None
            if type(candles) is list and type(candles[0]) is dict:
                keys = tuple(candles[0])
                key_types = (str,) * len(keys)
                value_types = tuple(map(type, candles[0].values()))
                if (
                    all(kind in (str, int, float, bool, type(None)) for kind in value_types)
                    and all(
                        type(candle) is dict
                        and tuple(map(type, candle)) == key_types
                        and tuple(candle) == keys
                        and tuple(map(type, candle.values())) == value_types
                        for candle in candles
                    )
                ):
                    fingerprint = hashlib.sha256(pickle.dumps(candles, protocol=4)).digest()
            cached = pool.get("_source_identity_content_cache")
            if (
                fingerprint is not None
                and isinstance(cached, tuple) and len(cached) == 2
                and cached[0] == fingerprint
            ):
                candle_content_hash = cached[1]
            else:
                candle_content_hash = hashlib.sha256(
                    json.dumps(
                        candles,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                        default=str,
                    ).encode("utf-8")
                ).hexdigest()
                if _cache_flat_content:
                    if fingerprint is None:
                        pool.pop("_source_identity_content_cache", None)
                    else:
                        pool["_source_identity_content_cache"] = (fingerprint, candle_content_hash)
        return (
            str(pool.get("timeframe_key") or ""),
            int(pool.get("requested_count") or 0),
            str(pool.get("request_id") or ""),
            str(pool.get("market_data_identity") or ""),
            str(pool.get("market_source") or ""),
            candle_count,
            first_time,
            last_time,
            candle_content_hash,
        )

    def _run_validation(
        self,
        window: object,
        run_request: object,
        *,
        history_extension: bool = False,
        _history_cache_result: object = _HISTORY_CACHE_UNSET,
        _resume_generation: int | None = None,
    ) -> None:
        window_key = id(window)
        if window_key not in self._open_windows:
            self.validation_failed.emit("SIGNAL_WINDOW_UNAVAILABLE")
            return
        if not isinstance(run_request, IndicatorFollowSignalValidationRunRequest):
            self._fail_window(window, "INVALID_SIGNAL_VALIDATION_RUN_REQUEST")
            return
        if not history_extension and window_key in self._history_extension_inflight:
            self._complete_history_extension(window_key, success=False)

        previous_run_request = self._last_run_requests.get(window_key)
        if (
            not history_extension
            and isinstance(
                previous_run_request,
                IndicatorFollowSignalValidationRunRequest,
            )
        ):
            previous_timeframe = validation_timeframe_from_rules(
                previous_run_request.settings_snapshot.to_dict()
            )
            current_timeframe = validation_timeframe_from_rules(
                run_request.settings_snapshot.to_dict()
            )
            if previous_timeframe["key"] != current_timeframe["key"]:
                self._history_targets[window_key] = self._historical_count

        self._last_run_requests[window_key] = run_request
        snapshot = run_request.settings_snapshot
        try:
            stock = getattr(window, "stock", None)
            if (
                not isinstance(stock, ValidationStockRef)
                or not stock.code
                or not stock.name
            ):
                self._fail_window(
                    window,
                    "상단 종목명을 더블클릭하여 검증 종목을 선택하세요.",
                )
                return
            snapshot_rules = snapshot.to_dict()
            target_timeframe_minutes = snapshot_rules["bar"]["bar_minutes"]
            warmup_bars = required_validation_history_context_bars(snapshot_rules)
            historical_target = self._history_targets.get(
                window_key,
                self._historical_count,
            )
            target_fetch_count = historical_target + warmup_bars
            if target_fetch_count > DEFAULT_CANDLES_MAX_COUNT:
                self._fail_window(window, "HISTORICAL_REQUEST_COUNT_EXCEEDS_LIMIT")
                return
            request = ValidationRequest(stock, snapshot, target_timeframe_minutes)
            session = ValidationSession(
                request,
                operation_active_reader=self._operation_active_reader,
            )
            availability = session.readiness()
            if not availability.allowed:
                self._fail_window(window, str(availability.reason or "VALIDATION_BLOCKED"))
                return
            production_contract = None
            (
                fetch_session,
                timeframe_minutes,
                timeframe_key,
                fetch_count,
            ) = self._validation_fetch_contract(
                session,
                target_fetch_count,
                production_contract=None,
            )
            production_calculation_enabled = False
            fetch_availability = fetch_session.readiness()
            if not fetch_availability.allowed:
                self._fail_window(
                    window,
                    str(fetch_availability.reason or "VALIDATION_BLOCKED"),
                )
                return
        except Exception as exc:
            self._fail_window(window, f"HISTORICAL_PROVIDER_ERROR: {exc}")
            return

        force_historical_refresh = run_request.force_historical_refresh
        if force_historical_refresh:
            self._history_exhaustion.pop(window_key, None)
        trace_enabled = signal_validation_trace_enabled()
        current_pool = self._historical_pools.get(window_key)
        current_pool_match = False
        shared_pool = None
        shared_pool_loaded = False
        if not force_historical_refresh or trace_enabled:
            current_pool_match = self._pool_matches(
                current_pool,
                stock,
                timeframe_key,
                fetch_count,
            )
        source_pool = current_pool
        if not force_historical_refresh and not current_pool_match:
            shared_pool = _SHARED_SIGNAL_VALIDATION_HISTORICAL_POOLS.get(
                self._shared_pool_key(stock, timeframe_key)
            )
            shared_pool_loaded = True
            source_pool = shared_pool
        run_identity = (
            stock.code,
            stock.name,
            snapshot.rules_hash,
            int(target_timeframe_minutes),
            int(historical_target),
            int(target_fetch_count),
            int(run_request.candle_count),
            bool(history_extension),
            bool(force_historical_refresh),
            int(timeframe_minutes),
            str(timeframe_key),
            int(fetch_count),
            self._pool_computation_source_identity(source_pool),
        )
        if trace_enabled:
            if not shared_pool_loaded:
                shared_pool = _SHARED_SIGNAL_VALIDATION_HISTORICAL_POOLS.get(
                    self._shared_pool_key(stock, timeframe_key)
                )
            shared_pool_match = self._pool_matches(
                shared_pool,
                stock,
                timeframe_key,
                fetch_count,
            )
            selected_pool_source = "none"
            selected_pool = None
            if not force_historical_refresh and current_pool_match:
                selected_pool_source = "current"
                selected_pool = current_pool
            elif not force_historical_refresh and shared_pool_match:
                selected_pool_source = "shared"
                selected_pool = shared_pool
            trace_signal_validation_event(
                "flow.run_validation.routing",
                window_key=window_key,
                generation_before=self._request_generation.get(window_key, 0),
                history_extension=bool(history_extension),
                force_refresh=bool(force_historical_refresh),
                stock=stock.code,
                timeframe=str(timeframe_key),
                history_target=int(historical_target),
                target_fetch_count=int(target_fetch_count),
                fetch_count=int(fetch_count),
                current_pool_present=isinstance(current_pool, dict),
                shared_pool_present=isinstance(shared_pool, dict),
                current_pool_match=bool(current_pool_match),
                shared_pool_match=bool(shared_pool_match),
                current_pool_match_failure=(
                    None
                    if current_pool_match
                    else self._pool_match_failure_reason(
                        current_pool, stock, timeframe_key, fetch_count
                    )
                ),
                shared_pool_match_failure=(
                    None
                    if shared_pool_match
                    else self._pool_match_failure_reason(
                        shared_pool, stock, timeframe_key, fetch_count
                    )
                ),
                selected_pool_source=selected_pool_source,
                selected_pool=trace_pool_summary(
                    selected_pool,
                    source_identity=(
                        run_identity[-1] if selected_pool is source_pool else None
                    ),
                ),
                production_contract=trace_production_contract_summary(
                    production_contract
                ),
            )
        calculation_active = (
            window_key in self._prepare_active
            or any(key[0] == window_key for key in self._active_providers)
        )
        if (
            not force_historical_refresh
            and calculation_active
            and self._active_run_identity.get(window_key) == run_identity
        ):
            return
        self._active_run_identity[window_key] = run_identity

        if _resume_generation is None:
            generation = self._request_generation.get(window_key, 0) + 1
            self._request_generation[window_key] = generation
        else:
            generation = int(_resume_generation)
            if self._request_generation.get(window_key) != generation:
                return
        if not force_historical_refresh:
            pool = source_pool
            if self._pool_matches(pool, stock, timeframe_key, fetch_count):
                if production_calculation_enabled:
                    pool = dict(pool)
                    pool["production_session_contract"] = deepcopy(production_contract)
                self._install_acquired_pool(
                    window,
                    session,
                    pool,
                    evaluation_count=run_request.candle_count,
                    history_browse=history_extension,
                    source_identity=run_identity[-1],
                )
                return

        request_key = (window_key, generation)
        window_ref = weakref.ref(window)
        if history_extension and _history_cache_result is _HISTORY_CACHE_UNSET:
            inflight = self._history_extension_inflight.get(window_key)
            cancel_event = (
                inflight.get("cancel_event") if isinstance(inflight, dict) else None
            )
            if not isinstance(cancel_event, Event):
                self._complete_history_extension(window_key, success=False)
                return
            detached_stock = deepcopy(stock)
            detached_contract = deepcopy(production_contract)

            def prepare_history_cache() -> None:
                payload: dict[str, object] = {
                    "window_key": window_key,
                    "generation": generation,
                    "run_request": run_request,
                    "persistent": None,
                    "cached_pool": None,
                    "persistent_capacity": fetch_count,
                    "production_refetch_count": None,
                    "cache_requires_probe": True,
                }
                try:
                    if cancel_event.is_set():
                        payload["cancelled"] = True
                    else:
                        if "cache_lease" not in inflight:
                            capture_lease = getattr(self._historical_cache, "capture_store_lease", None)
                            inflight["cache_lease"] = (
                                capture_lease(detached_stock.code)
                                if callable(capture_lease) else _CACHE_LEASE_UNSET
                            )
                        persistent_entry = self._cached_history(
                            detached_stock,
                            timeframe_minutes,
                            timeframe_key,
                            fetch_count,
                        )
                        payload["persistent"] = persistent_entry
                        if persistent_entry is not None:
                            capacity = max(
                                fetch_count,
                                persistent_entry.requested_count,
                            )
                            cached = self._pool_from_candles(
                                fetch_session,
                                [dict(candle) for candle in persistent_entry.candles],
                                requested_count=capacity,
                                request_id="VALIDATION_CACHE_RESTORE",
                                market_data_identity=persistent_entry.market_data_identity,
                                market_source=persistent_entry.market_source,
                            )
                            if production_calculation_enabled:
                                cached["production_session_contract"] = detached_contract
                                cached_view = self._validation_pool_for_session(
                                    session,
                                    cached,
                                    target_count=target_fetch_count,
                                )
                                projected = cached_view.get("candles")
                                retry_plan = production_minute_source_retry_plan(
                                    capacity,
                                    len(projected) if isinstance(projected, list) else 0,
                                    target_fetch_count,
                                )
                                if retry_plan.get("retry") is True:
                                    payload["production_refetch_count"] = int(
                                        retry_plan["requested_source_count"]
                                    )
                                elif retry_plan.get("ready") is not True:
                                    payload["blocked_reason"] = str(
                                        retry_plan.get("reason")
                                        or "PRODUCTION_SOURCE_HISTORY_INSUFFICIENT"
                                    )
                            payload["persistent_capacity"] = capacity
                            payload["cached_pool"] = cached
                            payload["cache_requires_probe"] = (
                                self._persistent_cache_requires_broker_probe(
                                    persistent_entry
                                )
                            )
                except Exception as exc:
                    payload["error"] = str(exc)
                if cancel_event.is_set():
                    payload["cancelled"] = True
                try:
                    self.history_cache_completed.emit(payload)
                except RuntimeError:
                    pass

            try:
                Thread(
                    target=prepare_history_cache,
                    name="indicator-follow-validation-history-cache",
                    daemon=True,
                ).start()
            except Exception as exc:
                self._fail_window(window, f"HISTORY_CACHE_PREPARE_ERROR: {exc}")
            return

        history_cache_bundle = (
            _history_cache_result
            if history_extension and isinstance(_history_cache_result, dict)
            else None
        )
        persistent = (
            history_cache_bundle.get("persistent")
            if isinstance(history_cache_bundle, dict)
            else (
                None
                if force_historical_refresh
                else self._cached_history(
                    stock,
                    timeframe_minutes,
                    timeframe_key,
                    fetch_count,
                )
            )
        )

        def current_target() -> object | None:
            target = window_ref()
            if (
                target is None
                or self._request_generation.get(window_key) != generation
            ):
                return None
            return target

        cached_pool = (
            history_cache_bundle.get("cached_pool")
            if isinstance(history_cache_bundle, dict)
            else None
        )
        persistent_capacity = (
            int(history_cache_bundle.get("persistent_capacity") or fetch_count)
            if isinstance(history_cache_bundle, dict)
            else fetch_count
        )
        production_refetch_count: int | None = (
            history_cache_bundle.get("production_refetch_count")
            if isinstance(history_cache_bundle, dict)
            else None
        )
        cache_requires_probe = (
            bool(history_cache_bundle.get("cache_requires_probe", True))
            if isinstance(history_cache_bundle, dict)
            else True
        )
        if isinstance(history_cache_bundle, dict) and history_cache_bundle.get(
            "blocked_reason"
        ):
            self._fail_window(
                window,
                "FILTER_SIGNAL_BACKTEST_BLOCKED: "
                + str(history_cache_bundle["blocked_reason"]),
            )
            return

        def production_retry_plan_for_candles(
            source_candles: list[dict[str, object]],
            current_source_count: int,
        ) -> dict[str, object] | None:
            if not production_calculation_enabled:
                return None
            try:
                projection = project_production_calculation_candles(
                    source_candles,
                    session.request.timeframe_minutes,
                    production_contract,
                    as_of=self._now_factory(),
                )
                projected = projection.get("candles")
                projected_count = len(projected) if isinstance(projected, list) else 0
                return production_minute_source_retry_plan(
                    current_source_count,
                    projected_count,
                    target_fetch_count,
                )
            except Exception:
                return {
                    "ready": False,
                    "retry": False,
                    "status": "FILTER_SIGNAL_BACKTEST_BLOCKED",
                    "requested_source_count": current_source_count,
                    "projected_target_bars": 0,
                    "required_target_bars": target_fetch_count,
                    "reason": "PRODUCTION_SOURCE_PROJECTION_FAILED",
                }

        if persistent is not None and not history_extension:
            persistent_capacity = max(fetch_count, persistent.requested_count)
            cached_pool = self._pool_from_candles(
                fetch_session,
                [dict(candle) for candle in persistent.candles],
                requested_count=persistent_capacity,
                request_id="VALIDATION_CACHE_RESTORE",
                market_data_identity=persistent.market_data_identity,
                market_source=persistent.market_source,
            )
            if production_calculation_enabled:
                cached_pool["production_session_contract"] = deepcopy(
                    production_contract
                )
                cached_source = cached_pool.get("candles")
                retry_plan = (
                    production_retry_plan_for_candles(
                        cached_source,
                        persistent_capacity,
                    )
                    if isinstance(cached_source, list)
                    else None
                )
                if isinstance(retry_plan, dict) and retry_plan.get("ready") is not True:
                    if retry_plan.get("retry") is True:
                        production_refetch_count = int(
                            retry_plan["requested_source_count"]
                        )
                    else:
                        self._fail_window(
                            window,
                            "FILTER_SIGNAL_BACKTEST_BLOCKED: "
                            + str(
                                retry_plan.get("reason")
                                or "PRODUCTION_SOURCE_HISTORY_INSUFFICIENT"
                            ),
                        )
                        return
            settings_hash = session.request.settings_snapshot.rules_hash
            exact_signal_cache_identity = (
                persistent.requested_count == fetch_count
                and fetch_count == target_fetch_count
                and str(timeframe_key) == str(session.request.timeframe_key)
            )
            restored_entries = (
                self._cached_signal_entries(
                    stock,
                    timeframe_minutes,
                    timeframe_key,
                    persistent_capacity,
                    settings_hash,
                )
                if exact_signal_cache_identity
                else None
            )
            if restored_entries is not None:
                cached_pool["signal_entries_by_settings_hash"][settings_hash] = (
                    restored_entries
                )
                restored_view = self._validation_pool_for_session(
                    session,
                    cached_pool,
                    target_count=target_fetch_count,
                )
                restored_signature = self._signal_view_signature(restored_view)
                if restored_signature is not None:
                    cached_pool[
                        "signal_entries_signature_by_settings_hash"
                    ][settings_hash] = restored_signature

        def install_cached_pool() -> None:
            target = current_target()
            if target is None or not isinstance(cached_pool, dict):
                return
            self._install_acquired_pool(
                target,
                session,
                cached_pool,
                evaluation_count=run_request.candle_count,
                history_browse=history_extension,
                replacement_reason="persistent_restore",
            )

        if (
            persistent is not None
            and production_refetch_count is None
            and not (
                cache_requires_probe
                if history_extension
                else self._persistent_cache_requires_broker_probe(persistent)
            )
        ):
            install_cached_pool()
            return

        try:
            provider = self._historical_provider_factory(fetch_session, self._broker)
        except Exception as exc:
            if persistent is None:
                self._fail_window(window, f"HISTORICAL_PROVIDER_ERROR: {exc}")
            else:
                install_cached_pool()
            return

        request_latest = getattr(provider, "request_latest", None)
        if not callable(request_latest):
            if persistent is None:
                self._fail_window(window, "HISTORICAL_PROVIDER_UNAVAILABLE")
            else:
                install_cached_pool()
            return

        def request_full(request_count: int = fetch_count) -> None:
            target = current_target()
            if target is None:
                return
            self._active_providers[request_key] = provider

            def full_completed(result: object) -> None:
                if history_extension:
                    completed_target = current_target()
                    inflight = self._history_extension_inflight.get(window_key)
                    cancel_event = (
                        inflight.get("cancel_event")
                        if isinstance(inflight, dict)
                        else None
                    )
                    if completed_target is None or not isinstance(cancel_event, Event):
                        self._active_providers.pop(request_key, None)
                        return

                    def prepare_history_result() -> None:
                        payload: dict[str, object] = {
                            "window_key": window_key,
                            "generation": generation,
                            "request_key": request_key,
                            "provider": provider,
                            "session": session,
                            "evaluation_count": run_request.candle_count,
                            "request_retry": request_full,
                            "request_count": request_count,
                        }
                        try:
                            if cancel_event.is_set():
                                payload["cancelled"] = True
                            elif (
                                not isinstance(result, ValidationHistoricalResult)
                                or result.ok is not True
                                or result.snapshot is None
                            ):
                                payload["error"] = self._failure_text(
                                    "HISTORICAL",
                                    result,
                                )
                            elif not self._historical_result_matches_validation_source(
                                result,
                                fetch_session.request.stock,
                            ):
                                payload["error"] = (
                                    "HISTORICAL: MARKET_SOURCE_IDENTITY_INVALID"
                                )
                            else:
                                candles, _dropped_count = project_validation_candles(
                                    result.snapshot
                                )
                                if not candles:
                                    raise ValueError("HISTORICAL: NO_VALID_CANDLES")
                                candles = merge_validation_candles(
                                    [],
                                    candles,
                                    request_count,
                                )
                                pool = self._pool_from_candles(
                                    fetch_session,
                                    candles,
                                    requested_count=request_count,
                                    request_id=result.snapshot.request_id,
                                    market_data_identity=(
                                        result.snapshot.market_data_identity
                                    ),
                                    market_source=result.snapshot.market_source,
                                )
                                if production_calculation_enabled:
                                    pool["production_session_contract"] = deepcopy(
                                        production_contract
                                    )
                                view_pool = self._validation_pool_for_session(
                                    session,
                                    pool,
                                    target_count=target_fetch_count,
                                )
                                projected = view_pool.get("candles")
                                projected_count = (
                                    len(projected)
                                    if isinstance(projected, list)
                                    else 0
                                )
                                retry_plan = (
                                    production_minute_source_retry_plan(
                                        request_count,
                                        projected_count,
                                        target_fetch_count,
                                    )
                                    if production_calculation_enabled
                                    else {"ready": True, "retry": False}
                                )
                                payload.update(
                                    {
                                        "pool": pool,
                                        "view_pool": view_pool,
                                        "retry_plan": retry_plan,
                                        "source_progress": (
                                            str(candles[0].get("time") or ""),
                                            projected_count,
                                        ),
                                    }
                                )
                        except Exception as exc:
                            payload["error"] = str(exc)
                        if cancel_event.is_set():
                            payload["cancelled"] = True
                        try:
                            self.history_fetch_completed.emit(payload)
                        except RuntimeError:
                            pass

                    try:
                        Thread(
                            target=prepare_history_result,
                            name="indicator-follow-validation-history-fetch",
                            daemon=True,
                        ).start()
                    except Exception as exc:
                        self._active_providers.pop(request_key, None)
                        self._fail_window(
                            completed_target,
                            f"HISTORICAL_PROJECTION_ERROR: {exc}",
                        )
                    return
                self._active_providers.pop(request_key, None)
                completed_target = current_target()
                if completed_target is None:
                    return
                if (
                    production_calculation_enabled
                    and self._historical_result_matches_validation_source(
                        result,
                        fetch_session.request.stock,
                    )
                ):
                    try:
                        source_candles, _dropped_count = project_validation_candles(
                            result.snapshot
                        )
                    except Exception:
                        source_candles = []
                    retry_plan = production_retry_plan_for_candles(
                        source_candles,
                        request_count,
                    )
                    if isinstance(retry_plan, dict):
                        if retry_plan.get("retry") is True:
                            request_full(int(retry_plan["requested_source_count"]))
                            return
                        if retry_plan.get("ready") is not True:
                            self._fail_window(
                                completed_target,
                                "FILTER_SIGNAL_BACKTEST_BLOCKED: "
                                + str(
                                    retry_plan.get("reason")
                                    or "PRODUCTION_SOURCE_HISTORY_INSUFFICIENT"
                                ),
                            )
                            return
                self._handle_historical_result(
                    completed_target,
                    session,
                    result,
                    evaluation_count=run_request.candle_count,
                    cache_requested_count=request_count,
                    source_session=fetch_session,
                    production_contract=(
                        production_contract if production_calculation_enabled else None
                    ),
                    history_browse=history_extension,
                )

            try:
                request_latest(request_count, full_completed)
            except Exception as exc:
                self._active_providers.pop(request_key, None)
                if current_target() is not None:
                    self._fail_window(target, f"HISTORICAL_REQUEST_ERROR: {exc}")

        if history_extension and (
            persistent is None or production_refetch_count is not None
        ):
            request_full(
                int(production_refetch_count)
                if production_refetch_count is not None
                else fetch_count
            )
            return

        if production_refetch_count is not None:
            request_full(production_refetch_count)
            return

        if persistent is None:
            request_full()
            return

        cached_latest_time = str(persistent.candles[-1]["time"])

        def trace_refresh(event: str, **details: object) -> None:
            _trace_approved_krx_history(
                event,
                stock.code,
                str(timeframe_key),
                cached_latest_time=cached_latest_time,
                fetch_count=fetch_count,
                **details,
            )

        def request_refresh(probe_count: int) -> None:
            target = current_target()
            if target is None:
                return
            count = min(fetch_count, max(1, int(probe_count)))
            trace_refresh("probe_request", requested_probe_count=count)
            self._active_providers[request_key] = provider

            def refresh_completed(result: object) -> None:
                self._active_providers.pop(request_key, None)
                refresh_target = current_target()
                if refresh_target is None:
                    return
                if not self._historical_result_matches_validation_source(
                    result,
                    fetch_session.request.stock,
                ):
                    trace_refresh("cache_fallback", reason="INVALID_BROKER_SOURCE")
                    install_cached_pool()
                    return
                try:
                    incoming, _dropped_count = project_validation_candles(
                        result.snapshot
                    )
                except Exception as exc:
                    trace_refresh("cache_fallback", reason="PROJECTION_ERROR",
                                  error_type=type(exc).__name__)
                    install_cached_pool()
                    return
                trace_refresh(
                    "probe_projection",
                    request_id=str(result.snapshot.request_id),
                    market_source=str(result.snapshot.market_source),
                    market_data_identity=str(result.snapshot.market_data_identity),
                    raw_row_count=result.snapshot.rows_count,
                    projected_count=len(incoming),
                    projected_first_time=str(incoming[0].get("time") or "") if incoming else "",
                    projected_last_time=str(incoming[-1].get("time") or "") if incoming else "",
                    dropped_count=_dropped_count,
                )
                if not incoming:
                    trace_refresh("cache_fallback", reason="NO_VALID_CANDLES")
                    install_cached_pool()
                    return

                newest_time = str(incoming[-1].get("time") or "")
                if not newest_time or newest_time < cached_latest_time:
                    trace_refresh("cache_fallback", reason="OLDER_THAN_CACHE",
                                  incoming_latest_time=newest_time)
                    install_cached_pool()
                    return

                oldest_time = str(incoming[0].get("time") or "")
                if (
                    oldest_time
                    and oldest_time > cached_latest_time
                    and count < fetch_count
                ):
                    next_count = min(fetch_count, max(count + 1, count * 2))
                    trace_refresh("probe_expand", incoming_first_time=oldest_time,
                                  incoming_latest_time=newest_time,
                                  requested_next_count=next_count)
                    request_refresh(next_count)
                    return

                merge_base = persistent.candles
                if oldest_time and oldest_time > cached_latest_time:
                    merge_base = ()
                merged = merge_validation_candles(
                    merge_base,
                    incoming,
                    persistent_capacity,
                )
                if merged == list(persistent.candles):
                    trace_refresh("cache_fallback", reason="UNCHANGED",
                                  merged_count=len(merged),
                                  incoming_latest_time=newest_time)
                    install_cached_pool()
                    return
                period_overlap = False
                if (
                    len(merged) < fetch_count
                    and timeframe_key in {"D1", "W1", "MO1", "Y1"}
                ):
                    cached_times = {
                        str(candle.get("time") or "")
                        for candle in persistent.candles
                    }
                    period_overlap = any(
                        str(candle.get("time") or "") in cached_times
                        for candle in incoming
                    )
                if len(merged) < fetch_count and not period_overlap:
                    trace_refresh("cache_fallback", reason="MERGED_BELOW_TARGET",
                                  merged_count=len(merged), incoming_count=len(incoming),
                                  incoming_latest_time=newest_time,
                                  cached_count=len(persistent.candles))
                    install_cached_pool()
                    return
                if period_overlap:
                    trace_refresh("period_short_history_accepted",
                                  merged_count=len(merged), incoming_count=len(incoming),
                                  incoming_latest_time=newest_time,
                                  requested_count=fetch_count)

                request_id = str(result.snapshot.request_id or "").strip()
                pool = self._pool_from_candles(
                    fetch_session,
                    merged,
                    requested_count=persistent_capacity,
                    request_id=f"{request_id or 'VALIDATION_CACHE_REFRESH'}:CACHE_MERGE",
                    market_data_identity=result.snapshot.market_data_identity,
                    market_source=result.snapshot.market_source,
                )
                if production_calculation_enabled:
                    pool["production_session_contract"] = deepcopy(
                        production_contract
                    )
                if current_target() is None:
                    return
                if not history_extension:
                    stored = self._persist_history(
                        stock,
                        timeframe_minutes,
                        timeframe_key,
                        persistent_capacity,
                        merged,
                        market_data_identity=result.snapshot.market_data_identity,
                        market_source=result.snapshot.market_source,
                    )
                    trace_refresh("cache_store", request_id=request_id,
                                  merged_count=len(merged), persisted=stored)
                if current_target() is None:
                    return
                trace_refresh("cache_install", request_id=request_id,
                              merged_count=len(merged),
                              newest_time=str(merged[-1].get("time") or ""))
                self._install_acquired_pool(
                    refresh_target,
                    session,
                    pool,
                    evaluation_count=run_request.candle_count,
                    history_browse=history_extension,
                    replacement_reason="cache_refresh_merge",
                )

            try:
                request_latest(count, refresh_completed)
            except Exception as exc:
                trace_refresh("cache_fallback", reason="BROKER_REQUEST_ERROR",
                              error_type=type(exc).__name__)
                self._active_providers.pop(request_key, None)
                install_cached_pool()

        request_refresh(min(_PERSISTENT_REFRESH_PROBE_COUNT, fetch_count))

    def _on_history_fetch_completed(self, payload: object) -> None:
        if not isinstance(payload, dict):
            return
        window_key = payload.get("window_key")
        generation = payload.get("generation")
        request_key = payload.get("request_key")
        provider = payload.get("provider")
        if (
            isinstance(window_key, bool)
            or not isinstance(window_key, int)
            or not isinstance(request_key, tuple)
        ):
            return
        window = self._open_windows.get(window_key)
        inflight = self._history_extension_inflight.get(window_key)
        cancel_event = (
            inflight.get("cancel_event") if isinstance(inflight, dict) else None
        )
        if (
            window is None
            or self._request_generation.get(window_key) != generation
            or self._active_providers.get(request_key) is not provider
            or not isinstance(cancel_event, Event)
            or cancel_event.is_set()
            or payload.get("cancelled") is True
            or not self._window_accepts_results(window)
        ):
            if self._active_providers.get(request_key) is provider:
                self._active_providers.pop(request_key, None)
            self._complete_history_extension(window_key, success=False)
            return
        error = str(payload.get("error") or "").strip()
        if error:
            self._active_providers.pop(request_key, None)
            self._fail_window(window, error)
            return
        retry_plan = payload.get("retry_plan")
        if not isinstance(retry_plan, dict):
            self._active_providers.pop(request_key, None)
            self._fail_window(window, "HISTORICAL_PROJECTION_ERROR: invalid result")
            return
        if retry_plan.get("retry") is True:
            progress = payload.get("source_progress")
            previous = inflight.get("source_progress")
            if (
                isinstance(progress, tuple)
                and len(progress) == 2
                and isinstance(previous, tuple)
                and len(previous) == 2
                and str(progress[0]) >= str(previous[0])
                and int(progress[1]) <= int(previous[1])
            ):
                exhaustion_identity = inflight.get("exhaustion_identity")
                if isinstance(exhaustion_identity, tuple):
                    self._history_exhaustion[window_key] = exhaustion_identity
                self._active_providers.pop(request_key, None)
                self._fail_window(
                    window,
                    "FILTER_SIGNAL_BACKTEST_BLOCKED: "
                    "PRODUCTION_SOURCE_HISTORY_NO_GROWTH",
                )
                return
            if isinstance(progress, tuple) and len(progress) == 2:
                inflight["source_progress"] = progress
            request_retry = payload.get("request_retry")
            if not callable(request_retry):
                self._active_providers.pop(request_key, None)
                self._fail_window(window, "HISTORICAL_RETRY_UNAVAILABLE")
                return
            request_retry(int(retry_plan["requested_source_count"]))
            return
        if retry_plan.get("ready") is not True:
            self._active_providers.pop(request_key, None)
            self._fail_window(
                window,
                "FILTER_SIGNAL_BACKTEST_BLOCKED: "
                + str(
                    retry_plan.get("reason")
                    or "PRODUCTION_SOURCE_HISTORY_INSUFFICIENT"
                ),
            )
            return
        pool = payload.get("pool")
        view_pool = payload.get("view_pool")
        session = payload.get("session")
        if (
            not isinstance(pool, dict)
            or not isinstance(view_pool, dict)
            or not isinstance(session, ValidationSession)
        ):
            self._active_providers.pop(request_key, None)
            self._fail_window(window, "HISTORICAL_PROJECTION_ERROR: invalid result")
            return
        self._use_pool_for_history_browse(
            window,
            session,
            pool,
            evaluation_count=int(payload.get("evaluation_count") or 1),
            prepared_view_pool=view_pool,
            provider_request_key=request_key,
            provider=provider,
            replacement_reason="history_extension_fetch",
        )

    def _on_history_cache_completed(self, payload: object) -> None:
        if not isinstance(payload, dict):
            return
        window_key = payload.get("window_key")
        generation = payload.get("generation")
        if isinstance(window_key, bool) or not isinstance(window_key, int):
            return
        window = self._open_windows.get(window_key)
        inflight = self._history_extension_inflight.get(window_key)
        cancel_event = (
            inflight.get("cancel_event") if isinstance(inflight, dict) else None
        )
        if (
            window is None
            or self._request_generation.get(window_key) != generation
            or not isinstance(cancel_event, Event)
            or cancel_event.is_set()
            or payload.get("cancelled") is True
            or not self._window_accepts_results(window)
        ):
            self._complete_history_extension(window_key, success=False)
            return
        error = str(payload.get("error") or "").strip()
        run_request = payload.get("run_request")
        if error or not isinstance(
            run_request,
            IndicatorFollowSignalValidationRunRequest,
        ):
            self._fail_window(
                window,
                "HISTORY_CACHE_PREPARE_ERROR: " + (error or "invalid result"),
            )
            return
        self._run_validation(
            window,
            run_request,
            history_extension=True,
            _history_cache_result=payload,
            _resume_generation=int(generation),
        )

    def _handle_historical_result(
        self,
        window: object,
        session: ValidationSession,
        result: object,
        *,
        evaluation_count: int,
        cache_requested_count: int | None = None,
        source_session: ValidationSession | None = None,
        production_contract: dict[str, object] | None = None,
        history_browse: bool = False,
    ) -> bool:
        if (
            not isinstance(result, ValidationHistoricalResult)
            or result.ok is not True
            or result.snapshot is None
        ):
            self._fail_window(window, self._failure_text("HISTORICAL", result))
            return False
        persistence_session = (
            source_session
            if isinstance(source_session, ValidationSession)
            else session
        )
        if not self._historical_result_matches_validation_source(
            result,
            persistence_session.request.stock,
        ):
            self._fail_window(window, "HISTORICAL: MARKET_SOURCE_IDENTITY_INVALID")
            return False
        try:
            candles, _dropped_count = project_validation_candles(result.snapshot)
        except Exception as exc:
            self._fail_window(window, f"HISTORICAL_PROJECTION_ERROR: {exc}")
            return False
        _trace_approved_krx_history(
            "full_projection",
            persistence_session.request.stock.code,
            str(result.snapshot.timeframe_key),
            request_id=str(result.snapshot.request_id),
            market_source=str(result.snapshot.market_source),
            market_data_identity=str(result.snapshot.market_data_identity),
            raw_row_count=result.snapshot.rows_count,
            projected_count=len(candles),
            dropped_count=_dropped_count,
            projected_first_time=str(candles[0].get("time") or "") if candles else "",
            projected_last_time=str(candles[-1].get("time") or "") if candles else "",
        )
        if not candles:
            self._fail_window(window, "HISTORICAL: NO_VALID_CANDLES")
            return False

        requested_count = int(cache_requested_count or result.snapshot.requested_count)
        candles = merge_validation_candles([], candles, requested_count)
        pool = self._pool_from_candles(
            persistence_session,
            candles,
            requested_count=requested_count,
            request_id=result.snapshot.request_id,
            market_data_identity=result.snapshot.market_data_identity,
            market_source=result.snapshot.market_source,
        )
        if production_contract is not None:
            pool["production_session_contract"] = deepcopy(production_contract)
        if not history_browse:
            stored = self._persist_history(
                persistence_session.request.stock,
                persistence_session.request.timeframe_minutes,
                result.snapshot.timeframe_key,
                requested_count,
                candles,
                market_data_identity=result.snapshot.market_data_identity,
                market_source=result.snapshot.market_source,
            )
            _trace_approved_krx_history(
                "full_cache_store",
                persistence_session.request.stock.code,
                str(result.snapshot.timeframe_key),
                request_id=str(result.snapshot.request_id),
                requested_count=requested_count,
                candle_count=len(candles),
                persisted=stored,
            )
        return self._install_acquired_pool(
            window,
            session,
            pool,
            evaluation_count=evaluation_count,
            history_browse=history_browse,
            replacement_reason=(
                "history_extension_fetch"
                if history_browse
                else "provider_full_result"
            ),
        )

    def _replay_from_pool(
        self,
        window: object,
        session: ValidationSession,
        pool: object,
        *,
        evaluation_count: int | None = None,
        chart_range: tuple[int, int] | None = None,
    ) -> None:
        if not isinstance(pool, dict):
            self._fail_window(window, "HISTORICAL_POOL_UNAVAILABLE")
            return
        history_target = self._history_targets.get(
            id(window),
            self._historical_count,
        )
        pool = self._validation_pool_for_session(
            session,
            pool,
            target_count=(
                history_target
                + required_validation_history_context_bars(
                    session.request.settings_snapshot.to_dict()
                )
            ),
        )
        if not isinstance(pool.get("candles"), list):
            self._fail_window(window, "HISTORICAL_POOL_UNAVAILABLE")
            return
        candles = pool["candles"]
        if not candles:
            self._fail_window(window, "HISTORICAL_POOL_EMPTY")
            return

        visible_candles = pool.get("display_candles")
        if isinstance(visible_candles, list):
            visible_candles = visible_candles[-history_target:]
            if not visible_candles:
                self._fail_window(window, "DISPLAY_CANDLES_UNAVAILABLE")
                return
            display_only_integrated = (
                pool.get("display_only_integrated") is True
            )
            chart_indexes = (
                _calculation_indexes_for_mixed_display(
                    candles,
                    visible_candles,
                )
                if display_only_integrated
                else _calculation_indexes_for_display(
                    candles,
                    visible_candles,
                )
            )
            chart_count = len(chart_indexes)
        else:
            display_only_integrated = False
            chart_count = min(history_target, len(candles))
            chart_indexes = tuple(range(len(candles) - chart_count, len(candles)))
        if chart_range is None:
            count = max(1, min(int(evaluation_count or 1), chart_count))
            evaluation_start = len(candles) - count
            evaluation_end = len(candles) - 1
        else:
            start, end = chart_range
            if (
                isinstance(start, bool)
                or isinstance(end, bool)
                or not isinstance(start, int)
                or not isinstance(end, int)
                or not 0 <= start <= end < chart_count
            ):
                self._fail_window(window, "INVALID_CACHED_REPLAY_RANGE")
                return
            if display_only_integrated:
                calculation_bounds = _calculation_bounds_for_display_range(
                    candles,
                    visible_candles,
                    start,
                    end,
                )
                if calculation_bounds is None:
                    apply_no_calculation = getattr(
                        window,
                        "apply_no_calculation_range_selection",
                        None,
                    )
                    if callable(apply_no_calculation):
                        apply_no_calculation()
                        return
                    self._fail_window(window, "NO_CALCULATION_CANDLES_IN_RANGE")
                    return
                evaluation_start, evaluation_end = calculation_bounds
            else:
                evaluation_start = chart_indexes[start]
                evaluation_end = chart_indexes[end]
            count = evaluation_end - evaluation_start + 1

        rules = session.request.settings_snapshot.to_dict()
        warmup_bars = required_validation_history_context_bars(rules)
        if chart_range is None:
            slice_start = max(0, evaluation_start - warmup_bars)
            replay_candles = [
                dict(candle)
                for candle in candles[slice_start : evaluation_end + 1]
            ]
            replay_start_index = None
            replay_end_index = None
        else:
            # Range replay keeps the base scan's historical prefix so
            # fixed-filter results remain position-stable.
            replay_candles = [
                dict(candle)
                for candle in candles[: evaluation_end + 1]
            ]
            replay_start_index = evaluation_start
            replay_end_index = evaluation_end
        if not replay_candles:
            self._fail_window(window, "CACHED_REPLAY_INPUT_EMPTY")
            return
        try:
            replay_historical = ValidationHistoricalSnapshot(
                stock=session.request.stock,
                timeframe_minutes=session.request.timeframe_minutes,
                timeframe_key=str(pool.get("timeframe_key") or ""),
                requested_count=len(replay_candles),
                request_id=str(pool.get("request_id") or "POOL"),
                rows=self._historical_rows_from_candles(replay_candles),
                market_data_identity=str(pool.get("market_data_identity") or ""),
                market_source=str(pool.get("market_source") or ""),
            )
            context_provider = self._context_provider_for_session(session)
            replay = self._replay_factory(session)
            evaluate_with_context = getattr(replay, "evaluate_with_context", None)
            evaluate = getattr(replay, "evaluate", None)
            if callable(evaluate_with_context):
                if replay_start_index is None:
                    replay_result = evaluate_with_context(
                        replay_historical,
                        context_provider=context_provider,
                        display_count=count,
                    )
                else:
                    replay_result = evaluate_with_context(
                        replay_historical,
                        context_provider=context_provider,
                        start_index=replay_start_index,
                        end_index=replay_end_index,
                    )
            elif callable(evaluate):
                if replay_start_index is None:
                    replay_result = evaluate(
                        replay_historical,
                        display_count=count,
                    )
                else:
                    replay_result = evaluate(
                        replay_historical,
                        start_index=replay_start_index,
                        end_index=replay_end_index,
                    )
            else:
                raise TypeError("replay evaluator is unavailable")
        except Exception as exc:
            self._fail_window(window, f"REPLAY_ERROR: {exc}")
            return
        if (
            not isinstance(replay_result, ValidationReplayResult)
            or replay_result.ok is not True
            or replay_result.snapshot is None
        ):
            self._fail_window(window, self._failure_text("REPLAY", replay_result))
            return

        try:
            if chart_range is None:
                window.set_replay_snapshot(replay_result.snapshot)
            else:
                apply_range = getattr(window, "apply_range_replay_snapshot", None)
                if not callable(apply_range):
                    raise TypeError("range replay result API is unavailable")
                apply_range(replay_result.snapshot)
        except Exception as exc:
            self._fail_window(window, f"RESULT_VIEW_ERROR: {exc}")

    def _run_cached_range_replay(
        self,
        window: object,
        start_index: int,
        end_index: int,
    ) -> None:
        if window is None:
            return
        window_key = id(window)
        if window_key not in self._open_windows:
            return
        pool = self._historical_pools.get(window_key)
        session = self._validation_sessions.get(window_key)
        if pool is None or session is None:
            return

        generation = self._range_replay_generation.get(window_key, 0) + 1
        self._range_replay_generation[window_key] = generation
        request_generation = self._request_generation.get(window_key, 0)
        history_target = self._history_targets.get(
            window_key,
            self._historical_count,
        )
        requested_range = (int(start_index), int(end_index))
        timestamp_capable, requested_times = self._capture_window_range_times(
            window,
            requested_range,
        )
        trace_signal_validation_event(
            "flow.range_replay.request",
            window_key=window_key,
            generation=generation,
            request_generation=request_generation,
            history_target=history_target,
            requested_range=requested_range,
            requested_times=requested_times,
            timestamp_capable=timestamp_capable,
            window_range=getattr(window, "validation_range", None),
            window_range_times=getattr(window, "_validation_range_times", None),
            window_display=trace_candle_signature(getattr(window, "_candles", None)),
            window_calculation=trace_candle_signature(
                getattr(window, "_calculation_candles", None)
            ),
            pool_timeframe=str(pool.get("timeframe_key") or ""),
            pool_requested_count=int(pool.get("requested_count") or 0),
            pool_candles=trace_candle_signature(pool.get("candles")),
            source_timeframe_key=str(pool.get("source_timeframe_key") or ""),
            calculation_source=str(pool.get("calculation_source") or ""),
        )
        request = {
            "window": window,
            "window_key": window_key,
            "generation": generation,
            "request_generation": request_generation,
            "pool": pool,
            "session": session,
            "range": requested_range,
            "range_times": requested_times,
            "range_uses_timestamps": timestamp_capable,
            "history_target": history_target,
            "token": object(),
        }
        if (
            window_key in self._prepare_active
            or window_key in self._range_replay_active
        ):
            self._range_replay_pending[window_key] = request
            return
        self._start_range_replay(request)

    @staticmethod
    def _capture_window_range_times(
        window: object,
        requested_range: tuple[int, int],
    ) -> tuple[bool, tuple[str, str] | None]:
        candles = getattr(window, "_candles", None)
        if not isinstance(candles, list):
            return False, None
        current_times = getattr(window, "_validation_range_times", None)
        if (
            getattr(window, "validation_range", None) == requested_range
            and isinstance(current_times, tuple)
            and len(current_times) == 2
            and all(str(value or "") for value in current_times)
        ):
            return True, (str(current_times[0]), str(current_times[1]))
        start, end = requested_range
        if not 0 <= start <= end < len(candles):
            return True, None
        start_candle = candles[start]
        end_candle = candles[end]
        if not isinstance(start_candle, dict) or not isinstance(end_candle, dict):
            return True, None
        start_time = str(start_candle.get("time") or "")
        end_time = str(end_candle.get("time") or "")
        if not start_time or not end_time:
            return True, None
        return True, (start_time, end_time)

    def _resolve_range_request_times(
        self,
        request: dict[str, object],
    ) -> bool:
        if request.get("range_uses_timestamps") is not True:
            return True
        window = request.get("window")
        candles = getattr(window, "_candles", None)
        requested_times = request.get("range_times")
        current_times = getattr(window, "_validation_range_times", None)
        if current_times is not None and tuple(current_times) != requested_times:
            return False
        if current_times is None and getattr(window, "validation_range", None) is None:
            return False
        if (
            not isinstance(candles, list)
            or not isinstance(requested_times, tuple)
            or len(requested_times) != 2
        ):
            return False
        indexes_by_time: dict[str, int] = {}
        duplicate_times: set[str] = set()
        for index, candle in enumerate(candles):
            if not isinstance(candle, dict):
                continue
            candle_time = str(candle.get("time") or "")
            if not candle_time:
                continue
            if candle_time in indexes_by_time:
                duplicate_times.add(candle_time)
            else:
                indexes_by_time[candle_time] = index
        start_time, end_time = requested_times
        if (
            start_time in duplicate_times
            or end_time in duplicate_times
            or start_time not in indexes_by_time
            or end_time not in indexes_by_time
        ):
            clear_range = getattr(window, "_set_validation_range", None)
            if callable(clear_range):
                clear_range(None, None)
            return False
        current_times = getattr(window, "_validation_range_times", None)
        if current_times is not None and tuple(current_times) != requested_times:
            return False
        current_range = getattr(window, "validation_range", None)
        resolved_range = (
            min(indexes_by_time[start_time], indexes_by_time[end_time]),
            max(indexes_by_time[start_time], indexes_by_time[end_time]),
        )
        if current_times is None and current_range != resolved_range:
            return False
        request["range"] = resolved_range
        return True

    def _range_request_is_current(self, request: object) -> bool:
        if not isinstance(request, dict):
            return False
        window_key = request.get("window_key")
        window = request.get("window")
        current = (
            isinstance(window_key, int)
            and self._open_windows.get(window_key) is window
            and self._range_replay_generation.get(window_key)
            == request.get("generation")
            and self._request_generation.get(window_key, 0)
            == request.get("request_generation")
            and self._historical_pools.get(window_key) is request.get("pool")
            and self._validation_sessions.get(window_key) is request.get("session")
        )
        if not current:
            return False
        if request.get("range_uses_timestamps") is True:
            requested_times = request.get("range_times")
            current_times = getattr(window, "_validation_range_times", None)
            if current_times is not None:
                return (
                    tuple(current_times) == requested_times
                    and getattr(window, "validation_range", None)
                    == request.get("range")
                )
        return getattr(window, "validation_range", None) == request.get("range")

    def _range_computation_is_current(
        self,
        request: dict[str, object],
        token: object,
    ) -> bool:
        window_key = request.get("window_key")
        return (
            isinstance(window_key, int)
            and self._range_replay_active.get(window_key) is token
            and self._range_replay_generation.get(window_key)
            == request.get("generation")
            and self._request_generation.get(window_key, 0)
            == request.get("request_generation")
            and self._historical_pools.get(window_key) is request.get("pool")
            and self._validation_sessions.get(window_key) is request.get("session")
        )

    def _start_range_replay(self, request: dict[str, object]) -> None:
        window_key = request.get("window_key")
        if (
            not isinstance(window_key, int)
            or self._open_windows.get(window_key) is not request.get("window")
            or self._range_replay_generation.get(window_key) != request.get("generation")
            or self._request_generation.get(window_key, 0) != request.get("request_generation")
            or self._historical_pools.get(window_key) is not request.get("pool")
            or self._validation_sessions.get(window_key) is not request.get("session")
        ):
            return
        if not self._resolve_range_request_times(request):
            return
        if not self._range_request_is_current(request):
            return
        window = request["window"]
        pool = request["pool"]
        requested_range = request["range"]
        mixed_display = (
            getattr(window, "_display_only_integrated_active", False) is True
            or (
                isinstance(pool, dict)
                and pool.get("display_only_integrated") is True
            )
        )
        calculation_range_times = request.get("range_times")
        bounds_for_range = getattr(
            window,
            "calculation_bounds_for_display_range",
            None,
        )
        if mixed_display and callable(bounds_for_range):
            calculation_bounds = bounds_for_range(*requested_range)
            if calculation_bounds is None:
                apply_no_calculation = getattr(
                    window,
                    "apply_no_calculation_range_selection",
                    None,
                )
                if callable(apply_no_calculation):
                    apply_no_calculation()
                return
            calculation_candles = getattr(
                window,
                "_calculation_candles",
                None,
            )
            calculation_start, calculation_end = calculation_bounds
            if (
                isinstance(calculation_candles, list)
                and 0 <= calculation_start <= calculation_end < len(calculation_candles)
            ):
                calculation_range_times = (
                    str(calculation_candles[calculation_start].get("time") or ""),
                    str(calculation_candles[calculation_end].get("time") or ""),
                )
        request["calculation_range_times"] = calculation_range_times
        window_key = int(request["window_key"])
        generation = request["generation"]
        request_generation = request["request_generation"]
        session = request["session"]
        history_target = request["history_target"]
        token = request["token"]
        trace_signal_validation_event(
            "flow.range_replay.start",
            window_key=window_key,
            generation=generation,
            request_generation=request_generation,
            history_target=history_target,
            requested_range=requested_range,
            requested_times=request.get("range_times"),
            window_range=getattr(request.get("window"), "validation_range", None),
            window_range_times=getattr(
                request.get("window"), "_validation_range_times", None
            ),
            window_display=trace_candle_signature(
                getattr(request.get("window"), "_candles", None)
            ),
            window_calculation=trace_candle_signature(
                getattr(request.get("window"), "_calculation_candles", None)
            ),
            pool_timeframe=str(pool.get("timeframe_key") or ""),
            pool_requested_count=int(pool.get("requested_count") or 0),
            pool_candles=trace_candle_signature(pool.get("candles")),
            source_timeframe_key=str(pool.get("source_timeframe_key") or ""),
            calculation_source=str(pool.get("calculation_source") or ""),
        )
        self._range_replay_active[window_key] = token

        def prepare_range_replay() -> None:
            payload: dict[str, object] = {
                "window_key": window_key,
                "generation": generation,
                "request_generation": request_generation,
                "range_token": token,
                "pool": pool,
                "session": session,
                "range": requested_range,
                "range_times": request.get("range_times"),
                "calculation_range_times": request.get(
                    "calculation_range_times"
                ),
                "snapshot": None,
                "error": "",
            }
            try:
                result = self._prepare_replay_result_from_pool(
                    session,
                    pool,
                    history_target=history_target,
                    chart_range=requested_range,
                    chart_range_times=request.get("range_times"),
                    cancel_predicate=lambda: not self._range_computation_is_current(
                        request,
                        token,
                    ),
                )
                if (
                    not isinstance(result, ValidationReplayResult)
                    or result.ok is not True
                    or result.snapshot is None
                ):
                    raise ValueError(self._failure_text("REPLAY", result))
                snapshot_candles = result.snapshot.to_candles()
                start_time = ""
                end_time = ""
                if snapshot_candles:
                    start_index = max(
                        0,
                        min(
                            len(snapshot_candles) - 1,
                            int(result.snapshot.evaluated_start_index),
                        ),
                    )
                    end_index = max(
                        0,
                        min(
                            len(snapshot_candles) - 1,
                            int(result.snapshot.evaluated_end_index),
                        ),
                    )
                    start_time = str(
                        snapshot_candles[start_index].get("time") or ""
                    )
                    end_time = str(
                        snapshot_candles[end_index].get("time") or ""
                    )
                requested_times = request.get("calculation_range_times")
                if (
                    isinstance(requested_times, tuple)
                    and len(requested_times) == 2
                    and (start_time, end_time)
                    != (str(requested_times[0]), str(requested_times[1]))
                ):
                    raise ValueError(
                        "RANGE_REPLAY_TIMESTAMP_MISMATCH: "
                        f"requested={requested_times!r} "
                        f"actual={(start_time, end_time)!r}"
                    )
                payload["snapshot"] = result.snapshot
                trace_signal_validation_event(
                    "flow.range_replay.computed",
                    window_key=window_key,
                    generation=generation,
                    request_generation=request_generation,
                    history_target=history_target,
                    requested_range=requested_range,
                    requested_times=request.get("range_times"),
                    snapshot_timeframe=str(result.snapshot.timeframe_key),
                    snapshot_evaluated_start=int(
                        result.snapshot.evaluated_start_index
                    ),
                    snapshot_evaluated_end=int(
                        result.snapshot.evaluated_end_index
                    ),
                    snapshot_evaluated_start_time=start_time,
                    snapshot_evaluated_end_time=end_time,
                    snapshot_candles=trace_candle_signature(snapshot_candles),
                    snapshot_entry_count=len(result.snapshot.to_entries()),
                )
            except Exception as exc:
                payload["error"] = str(exc)
                trace_signal_validation_event(
                    "flow.range_replay.error",
                    window_key=window_key,
                    generation=generation,
                    request_generation=request_generation,
                    history_target=history_target,
                    requested_range=requested_range,
                    requested_times=request.get("range_times"),
                    error=str(exc),
                )
            self._emit_range_replay_completed(payload)

        try:
            Thread(
                target=prepare_range_replay,
                name="indicator-follow-validation-range-replay",
                daemon=True,
            ).start()
        except Exception as exc:
            self._on_range_replay_completed({
                "window_key": window_key,
                "generation": generation,
                "request_generation": request_generation,
                "range_token": token,
                "pool": pool,
                "session": session,
                "range": requested_range,
                "range_times": request.get("range_times"),
                "calculation_range_times": request.get(
                    "calculation_range_times"
                ),
                "snapshot": None,
                "error": str(exc),
            })

    def _emit_range_replay_completed(self, payload: object) -> bool:
        """Emit range replay completion unless this QObject was torn down."""
        if sip.isdeleted(self):
            return False
        try:
            self.range_replay_completed.emit(payload)
        except RuntimeError:
            if sip.isdeleted(self):
                return False
            raise
        return True

    def _on_range_replay_completed(self, payload: object) -> None:
        if not isinstance(payload, dict):
            return
        window_key = payload.get("window_key")
        generation = payload.get("generation")
        request_generation = payload.get("request_generation")
        token = payload.get("range_token")
        trace_signal_validation_event(
            "flow.range_replay.complete_received",
            window_key=window_key,
            generation=generation,
            request_generation=request_generation,
            active_token_match=(
                isinstance(window_key, int)
                and self._range_replay_active.get(window_key) is token
            ),
            current_request_generation=(
                self._request_generation.get(window_key, 0)
                if isinstance(window_key, int) else None
            ),
            current_range_generation=(
                self._range_replay_generation.get(window_key)
                if isinstance(window_key, int) else None
            ),
            requested_range=payload.get("range"),
            requested_times=payload.get("range_times"),
            error=str(payload.get("error") or ""),
        )
        if (
            isinstance(window_key, bool)
            or not isinstance(window_key, int)
            or self._range_replay_active.get(window_key) is not token
        ):
            return
        pending_prepare = self._prepare_pending.get(window_key)
        pending_range = self._range_replay_pending.get(window_key)
        if (
            pending_prepare is not None
            or pending_range is not None
            or self._range_replay_generation.get(window_key) != generation
            or self._request_generation.get(window_key, 0) != request_generation
        ):
            self._range_replay_active.pop(window_key, None)
            self._start_pending_window_work(window_key)
            return
        window = self._open_windows.get(window_key)
        if window is None or not self._window_accepts_results(window):
            self._range_replay_active.pop(window_key, None)
            return
        pool = payload.get("pool")
        session = payload.get("session")
        if (
            self._historical_pools.get(window_key) is not pool
            or self._validation_sessions.get(window_key) is not session
        ):
            self._range_replay_active.pop(window_key, None)
            return
        requested_range = payload.get("range")
        requested_times = payload.get("range_times")
        calculation_range_times = payload.get("calculation_range_times")
        current_times = getattr(window, "_validation_range_times", None)
        if not isinstance(requested_range, tuple) or len(requested_range) != 2:
            self._range_replay_active.pop(window_key, None)
            return
        timestamp_request = (
            isinstance(requested_times, tuple)
            and len(requested_times) == 2
            and all(str(value or "") for value in requested_times)
        )
        if timestamp_request:
            if tuple(current_times or ()) != requested_times:
                self._range_replay_active.pop(window_key, None)
                return
        elif getattr(window, "validation_range", None) != requested_range:
            self._range_replay_active.pop(window_key, None)
            return
        error = str(payload.get("error") or "").strip()
        if error:
            self._range_replay_active.pop(window_key, None)
            self._fail_window(window, f"REPLAY_ERROR: {error}")
            return
        snapshot = payload.get("snapshot")
        if not isinstance(snapshot, ValidationReplaySnapshot):
            self._range_replay_active.pop(window_key, None)
            self._fail_window(window, "REPLAY_ERROR: replay snapshot unavailable")
            return
        if timestamp_request:
            snapshot_candles = snapshot.to_candles()
            try:
                actual_times = (
                    str(
                        snapshot_candles[
                            int(snapshot.evaluated_start_index)
                        ].get("time") or ""
                    ),
                    str(
                        snapshot_candles[
                            int(snapshot.evaluated_end_index)
                        ].get("time") or ""
                    ),
                )
            except (IndexError, TypeError, ValueError):
                actual_times = ("", "")
            expected_source = (
                calculation_range_times
                if (
                    isinstance(calculation_range_times, tuple)
                    and len(calculation_range_times) == 2
                    and all(str(value or "") for value in calculation_range_times)
                )
                else requested_times
            )
            expected_times = (
                str(expected_source[0]),
                str(expected_source[1]),
            )
            if actual_times != expected_times:
                self._range_replay_active.pop(window_key, None)
                return
        trace_signal_validation_event(
            "flow.range_replay.accepted",
            window_key=window_key,
            generation=generation,
            request_generation=request_generation,
            requested_range=requested_range,
            requested_times=requested_times,
            current_range=getattr(window, "validation_range", None),
            current_range_times=getattr(window, "_validation_range_times", None),
            window_display=trace_candle_signature(getattr(window, "_candles", None)),
            window_calculation=trace_candle_signature(
                getattr(window, "_calculation_candles", None)
            ),
            snapshot_timeframe=str(snapshot.timeframe_key),
            snapshot_evaluated_start=int(snapshot.evaluated_start_index),
            snapshot_evaluated_end=int(snapshot.evaluated_end_index),
            snapshot_candles=trace_candle_signature(snapshot.to_candles()),
            snapshot_entry_count=len(snapshot.to_entries()),
        )
        interaction_epoch = self._window_busy_epoch(window)
        if interaction_epoch is not None:
            self._range_replay_deferred[window_key] = (
                interaction_epoch,
                dict(payload),
            )
            return
        self._range_replay_active.pop(window_key, None)
        apply_range = getattr(
            window,
            "apply_range_replay_snapshot_async",
            None,
        )
        if not callable(apply_range):
            apply_range = getattr(window, "apply_range_replay_snapshot", None)
        if not callable(apply_range):
            self._fail_window(window, "RESULT_VIEW_ERROR: range replay result API is unavailable")
            return
        try:
            apply_range(snapshot)
        except Exception as exc:
            self._fail_window(window, f"RESULT_VIEW_ERROR: {exc}")


    def _fail_window(self, window: object, message: str) -> None:
        text = str(message or "VALIDATION_BLOCKED")
        window_key = id(window)
        if window_key in self._history_extension_inflight:
            self._complete_history_extension(window_key, success=False)
            self.validation_failed.emit(text)
            return
        show_error = getattr(window, "show_validation_error", None)
        if callable(show_error):
            show_error(text)
        self.validation_failed.emit(text)

    def _forward_host_failure(self, reason: str) -> None:
        self.validation_failed.emit(str(reason or "VALIDATION_BLOCKED"))

    @staticmethod
    def _failure_text(stage: str, result: object) -> str:
        reason = str(getattr(result, "reason", "") or "UNKNOWN_FAILURE")
        error = str(getattr(result, "error", "") or "").strip()
        return f"{stage}: {reason}" + (f" ({error})" if error else "")


def _present_signal_validation_failure(owner: object, message: str) -> None:
    if message == "SERVER_NOT_CONNECTED":
        return
    text = f"검증차트2: {str(message or '실패')}"
    status_message = getattr(owner, "statusBarMessage", None)
    if callable(status_message):
        status_message(text)
        return
    status_bar_getter = getattr(owner, "statusBar", None)
    if not callable(status_bar_getter):
        return
    try:
        show_message = getattr(status_bar_getter(), "showMessage", None)
        if callable(show_message):
            show_message(text, 5000)
    except Exception:
        return


def bind_indicator_follow_signal_validation_flow(
    owner: object,
    dialog: object,
) -> IndicatorFollowSignalValidationFlow | None:
    signal = getattr(dialog, "signal_validation_requested", None)
    if not callable(getattr(signal, "connect", None)):
        return None
    flow = getattr(owner, _OWNER_FLOW_ATTRIBUTE, None)
    if not isinstance(flow, IndicatorFollowSignalValidationFlow):
        qobject_parent = owner if isinstance(owner, QObject) else None
        flow = IndicatorFollowSignalValidationFlow(
            getattr(owner, "kiwoom_api", None),
            parent=qobject_parent,
            full_parity_workers=0,
        )
        flow.validation_failed.connect(
            lambda message, target=owner: _present_signal_validation_failure(
                target,
                message,
            )
        )
        setattr(owner, _OWNER_FLOW_ATTRIBUTE, flow)
    flow.bind_dialog(dialog)
    return flow
