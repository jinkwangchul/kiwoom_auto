# -*- coding: utf-8 -*-
"""Independent entry and replay flow for indicator signal Validation V2."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import tempfile
from threading import Thread
import weakref

from PyQt5.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt5.QtWidgets import QDialog, QWidget

from gui_toast import show_toast
from gui_indicator_follow_signal_validation_window import (
    IndicatorFollowSignalValidationWindow,
    prepare_signal_validation_presentation,
)
from indicator_follow_signal_validation_execution import ValidationVirtualPositionTracker
from indicator_follow_signal_validation_historical_cache import (
    IndicatorFollowSignalValidationHistoricalCache,
    ValidationHistoricalCacheEntry,
    merge_validation_candles,
)
from indicator_follow_validation_timeframe import (
    normalize_validation_timeframe,
    validation_timeframe_from_rules,
)
from indicator_follow_validation_regular_market import (
    project_regular_market_candles,
    regular_market_source_minutes,
    required_regular_market_source_candles,
)
from indicator_follow_signal_validation_visualization import (
    required_validation_warmup_bars,
)
from candle_timeframe_aggregation import SEOUL_TIMEZONE
from candle_manager import DEFAULT_CANDLES_MAX_COUNT
from gui_indicator_follow_validation_host import IndicatorFollowValidationHost
from indicator_follow_signal_validation_recent_stocks import (
    IndicatorFollowSignalValidationRecentStockStore,
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
)
from routines.지표추종매매.routine_validation_session import ValidationSession
from stock_code_contract import normalize_broker_stock_code
from stock_code_contract import (
    market_data_identity_for_nxt_availability,
    market_source_for_identity,
)


DEFAULT_SIGNAL_VALIDATION_HISTORICAL_COUNT = 500
_HISTORY_EXTENSION_STEP = 500
_PERSISTENT_REFRESH_PROBE_COUNT = 300
_SHARED_SIGNAL_VALIDATION_HISTORICAL_POOLS: dict[
    tuple[int, str, str, str], dict[str, object]
] = {}
_OWNER_FLOW_ATTRIBUTE = "_indicator_follow_signal_validation_flow"
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
        historical_cache: object | None = None,
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
        ):
            if not callable(factory):
                raise TypeError(f"{name} must be callable")
        self._broker = broker
        self._operation_active_reader = operation_active_reader
        self._historical_count = historical_count
        self._historical_provider_factory = historical_provider_factory
        self._replay_factory = replay_factory
        self._window_factory = window_factory
        self._recent_stock_store = (
            recent_stock_store
            if recent_stock_store is not None
            else IndicatorFollowSignalValidationRecentStockStore()
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
        self._market_snapshot_generation: dict[int, int] = {}
        self._market_snapshot_metadata: dict[int, tuple[str, dict[str, object]]] = {}
        self._active_providers: dict[tuple[int, int], object] = {}
        self._historical_pools: dict[int, dict[str, object]] = {}
        self._validation_sessions: dict[int, ValidationSession] = {}
        self._history_targets: dict[int, int] = {}
        self._history_extension_inflight: dict[int, dict[str, object]] = {}
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
            self._history_extension_inflight.pop(window_key, None)
            self._last_run_requests.pop(window_key, None)
            self._invalidate_window_requests(window_key)
            for request_key in tuple(self._active_providers):
                if request_key[0] == window_key:
                    self._active_providers.pop(request_key, None)

    def _invalidate_window_requests(self, window_key: int) -> None:
        if window_key in self._request_generation:
            self._request_generation[window_key] += 1
        if window_key in self._market_snapshot_generation:
            self._market_snapshot_generation[window_key] += 1
        self._market_snapshot_metadata.pop(window_key, None)

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
        self._open_windows.pop(window_key, None)
        self._request_generation.pop(window_key, None)
        self._market_snapshot_generation.pop(window_key, None)
        self._market_snapshot_metadata.pop(window_key, None)
        self._historical_pools.pop(window_key, None)
        self._validation_sessions.pop(window_key, None)
        self._history_targets.pop(window_key, None)
        self._history_extension_inflight.pop(window_key, None)
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
        try:
            warmup_bars = required_validation_warmup_bars(
                run_request.settings_snapshot.to_dict()
            )
        except Exception:
            return
        maximum_target = max(1, DEFAULT_CANDLES_MAX_COUNT - warmup_bars)
        current_target = self._history_targets.get(
            window_key,
            self._historical_count,
        )
        next_target = min(maximum_target, current_target + _HISTORY_EXTENSION_STEP)
        if next_target <= current_target:
            return
        self._history_targets[window_key] = next_target
        self._history_extension_inflight[window_key] = {
            "previous_target": current_target,
            "requested_target": next_target,
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
        if not success:
            self._history_targets[window_key] = int(
                state.get("previous_target") or self._historical_count
            )

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
        broker_resolver = getattr(self._broker, "_market_data_request_identity", None)
        if callable(broker_resolver):
            try:
                canonical, identity, _source = broker_resolver(stock.code)
            except Exception:
                canonical, identity = "", ""
            if str(canonical or "").strip() == stock.code and str(identity or "").strip():
                return str(identity).strip()
        return market_data_identity_for_nxt_availability(stock.code, None)

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
    def _signal_view_signature(pool: object) -> tuple[str, int, str, str] | None:
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
        if (
            not entry_identity
            and not marker_identity
            and expected_identity.endswith("_AL")
        ):
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
    ) -> bool:
        store = getattr(self._historical_cache, "store", None)
        if not callable(store):
            return False
        persisted_identity = str(market_data_identity or "").strip()
        if not persisted_identity:
            persisted_identity = self._validation_market_data_identity(stock)
        persisted_source = str(market_source or "").strip()
        if not persisted_source:
            persisted_source = market_source_for_identity(persisted_identity)
        try:
            stored = store(
                stock_code=stock.code,
                stock_name=stock.name,
                timeframe_minutes=timeframe_minutes,
                requested_count=fetch_count,
                candles=candles,
                timeframe_key=timeframe_key,
                market_data_identity=persisted_identity,
                market_source=persisted_source,
            ) is True
        except Exception:
            return False
        if not stored:
            return False
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
                + required_validation_warmup_bars(rules)
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
        if not regular_minute and len(normalized_raw) <= target_count:
            return pool
        if regular_minute:
            target_minutes = int(timeframe["minutes"])
            target_key = str(timeframe["key"])
            projected = project_regular_market_candles(
                normalized_raw,
                target_minutes,
                limit=target_count,
            )
            request_suffix = "REGULAR"
        else:
            target_minutes = session.request.timeframe_minutes
            target_key = session.request.timeframe_key
            projected = normalized_raw[-target_count:]
            request_suffix = "ACTIVE"
        derived = dict(pool)
        derived["timeframe_minutes"] = target_minutes
        derived["timeframe_key"] = target_key
        derived["requested_count"] = min(target_count, len(projected))
        derived["candles"] = projected
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
                f"{str(pool.get('request_id') or 'POOL')}:{request_suffix}"
            ),
            rows=self._historical_rows_from_candles(projected),
            market_data_identity=str(pool.get("market_data_identity") or ""),
            market_source=str(pool.get("market_source") or ""),
        )
        return derived

    def _install_pool(
        self,
        window: object,
        session: ValidationSession,
        pool: dict[str, object],
        *,
        evaluation_count: int,
    ) -> bool:
        _SHARED_SIGNAL_VALIDATION_HISTORICAL_POOLS[
            self._shared_pool_key(
                session.request.stock,
                str(pool.get("timeframe_key") or ""),
            )
        ] = pool
        try:
            self._use_pool_for_window(
                window,
                session,
                pool,
                evaluation_count=evaluation_count,
            )
        except Exception as exc:
            self._fail_window(window, f"RESULT_POOL_ERROR: {exc}")
            return False
        return True

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
    ) -> None:
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
        view_pool = self._validation_pool_for_session(
            session,
            pool,
            target_count=(
                chart_candle_count
                + required_validation_warmup_bars(
                    session.request.settings_snapshot.to_dict()
                )
            ),
        )
        install_pool = getattr(window, "set_historical_candle_pool", None)
        if callable(install_pool):
            install_pool(
                view_pool["candles"],
                chart_candle_count=chart_candle_count,
            )
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
                return
            try:
                set_replay_snapshot(replay_snapshot)
            except Exception as exc:
                self._fail_window(window, f"RESULT_VIEW_ERROR: {exc}")
                return
        else:
            self._replay_from_pool(
                window,
                session,
                view_pool,
                evaluation_count=evaluation_count,
            )
        self._complete_history_extension(window_key, success=True)

    def _prepare_replay_result_from_pool(
        self,
        session: ValidationSession,
        pool: object,
        *,
        history_target: int,
        evaluation_count: int | None = None,
        chart_range: tuple[int, int] | None = None,
    ) -> ValidationReplayResult:
        if not isinstance(pool, dict):
            raise ValueError("HISTORICAL_POOL_UNAVAILABLE")
        rules = session.request.settings_snapshot.to_dict()
        warmup_bars = required_validation_warmup_bars(rules)
        active_pool = self._validation_pool_for_session(
            session,
            pool,
            target_count=history_target + warmup_bars,
        )
        candles = active_pool.get("candles")
        if not isinstance(candles, list) or not candles:
            raise ValueError("HISTORICAL_POOL_EMPTY")

        chart_count = min(history_target, len(candles))
        chart_start = len(candles) - chart_count
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
                raise ValueError("INVALID_CACHED_REPLAY_RANGE")
            evaluation_start = chart_start + start
            evaluation_end = chart_start + end
            count = end - start + 1

        slice_start = max(0, evaluation_start - warmup_bars)
        replay_candles = [
            dict(candle)
            for candle in candles[slice_start : evaluation_end + 1]
        ]
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
        replay = self._replay_factory(session)
        evaluate_with_context = getattr(replay, "evaluate_with_context", None)
        evaluate = getattr(replay, "evaluate", None)
        if callable(evaluate_with_context):
            replay_result = evaluate_with_context(
                replay_historical,
                context_provider=self._context_provider_for_session(session),
                display_count=count,
            )
        elif callable(evaluate):
            replay_result = evaluate(
                replay_historical,
                display_count=count,
            )
        else:
            raise TypeError("replay evaluator is unavailable")
        if (
            not isinstance(replay_result, ValidationReplayResult)
            or replay_result.ok is not True
            or replay_result.snapshot is None
        ):
            raise ValueError(self._failure_text("REPLAY", replay_result))
        return replay_result

    def _use_pool_for_window(
        self,
        window: object,
        session: ValidationSession,
        pool: dict[str, object],
        *,
        evaluation_count: int,
    ) -> None:
        window_key = id(window)
        self._historical_pools[window_key] = pool
        self._validation_sessions[window_key] = session

        history_target = self._history_targets.get(
            window_key,
            self._historical_count,
        )
        view_pool = self._validation_pool_for_session(
            session,
            pool,
            target_count=(
                history_target
                + required_validation_warmup_bars(
                    session.request.settings_snapshot.to_dict()
                )
            ),
        )
        historical_snapshot = view_pool.get("snapshot")
        settings_hash = session.request.settings_snapshot.rules_hash
        cached_by_settings = pool.setdefault(
            "signal_entries_by_settings_hash",
            {},
        )
        cached_signatures = pool.setdefault(
            "signal_entries_signature_by_settings_hash",
            {},
        )
        active_signal_signature = self._signal_view_signature(view_pool)
        signal_entries = (
            cached_by_settings.get(settings_hash)
            if (
                isinstance(cached_by_settings, dict)
                and isinstance(cached_signatures, dict)
                and cached_signatures.get(settings_hash)
                == active_signal_signature
            )
            else None
        )
        replay = self._replay_factory(session)
        scan = getattr(replay, "scan_signal_entries", None)
        if signal_entries is None and (
            not isinstance(historical_snapshot, ValidationHistoricalSnapshot)
            or not callable(scan)
        ):
            self._apply_pool_to_window(
                window,
                session,
                pool,
                evaluation_count=evaluation_count,
                signal_entries=None,
            )
            return

        generation = self._request_generation.get(window_key, 0)
        payload_base = {
            "window_key": window_key,
            "generation": generation,
            "session": session,
            "pool": pool,
            "evaluation_count": evaluation_count,
            "settings_hash": settings_hash,
            "history_target": history_target,
            "active_signal_signature": active_signal_signature,
        }
        cached_entries = (
            tuple(signal_entries)
            if signal_entries is not None
            else None
        )

        def prepare_result() -> None:
            try:
                entries = (
                    cached_entries
                    if cached_entries is not None
                    else tuple(
                        scan(
                            historical_snapshot,
                            context_provider=self._context_provider_for_session(
                                session
                            ),
                        )
                    )
                )
                replay_result = self._prepare_replay_result_from_pool(
                    session,
                    pool,
                    history_target=history_target,
                    evaluation_count=evaluation_count,
                )
                rules = session.request.settings_snapshot.to_dict()
                warmup_bars = required_validation_warmup_bars(rules)
                active_pool = self._validation_pool_for_session(
                    session,
                    pool,
                    target_count=history_target + warmup_bars,
                )
                active_candles = active_pool.get("candles")
                if not isinstance(active_candles, list) or not active_candles:
                    raise ValueError("HISTORICAL_POOL_EMPTY")
                chart_count = min(history_target, len(active_candles))
                chart_start = len(active_candles) - chart_count
                prepared_presentation = prepare_signal_validation_presentation(
                    active_candles,
                    entries,
                    session.request.settings_snapshot,
                    chart_start_index=chart_start,
                    chart_count=chart_count,
                )
                payload = dict(payload_base)
                payload["entries"] = entries
                payload["replay_snapshot"] = replay_result.snapshot
                payload["prepared_presentation"] = prepared_presentation
                payload["error"] = ""
            except Exception as exc:
                payload = dict(payload_base)
                payload["entries"] = ()
                payload["replay_snapshot"] = None
                payload["prepared_presentation"] = None
                payload["error"] = str(exc)
            self.signal_scan_completed.emit(payload)

        Thread(
            target=prepare_result,
            name="indicator-follow-validation-prepare",
            daemon=True,
        ).start()

    def _on_signal_scan_completed(self, payload: object) -> None:
        if not isinstance(payload, dict):
            return
        window_key = payload.get("window_key")
        generation = payload.get("generation")
        if (
            isinstance(window_key, bool)
            or not isinstance(window_key, int)
            or self._request_generation.get(window_key) != generation
        ):
            return
        window = self._open_windows.get(window_key)
        if window is None:
            return
        error = str(payload.get("error") or "").strip()
        if error:
            self._fail_window(window, f"SIGNAL_SCAN_ERROR: {error}")
            return
        session = payload.get("session")
        pool = payload.get("pool")
        if not isinstance(session, ValidationSession) or not isinstance(pool, dict):
            return
        if self._historical_pools.get(window_key) is not pool:
            return
        entries = tuple(payload.get("entries") or ())
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
            and len(active_signal_signature) == 4
        ):
            cached_by_settings[settings_hash] = entries
            cached_signatures[settings_hash] = active_signal_signature
            active_timeframe_key, active_count, _first_time, _last_time = (
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
                    list(entries),
                )
        evaluation_count = payload.get("evaluation_count")
        if isinstance(evaluation_count, bool) or not isinstance(evaluation_count, int):
            return
        replay_snapshot = payload.get("replay_snapshot")
        prepared_presentation = payload.get("prepared_presentation")
        self._apply_pool_to_window(
            window,
            session,
            pool,
            evaluation_count=evaluation_count,
            signal_entries=entries,
            replay_snapshot=replay_snapshot,
            prepared_presentation=prepared_presentation,
        )

    def _validation_fetch_contract(
        self,
        session: ValidationSession,
        target_count: int,
    ) -> tuple[ValidationSession, int, str, int]:
        rules = session.request.settings_snapshot.to_dict()
        scope = (
            rules.get("validation_market_scope")
            if isinstance(rules, dict)
            else None
        )
        timeframe = normalize_validation_timeframe(
            session.request.timeframe_key,
            fallback_minutes=session.request.timeframe_minutes,
        )
        if (
            not isinstance(scope, dict)
            or scope.get("regular_market_only") is not True
            or timeframe["kind"] != "MINUTE"
        ):
            return (
                session,
                session.request.timeframe_minutes,
                session.request.timeframe_key,
                target_count,
            )

        target_minutes = int(timeframe["minutes"])
        source_minutes = regular_market_source_minutes(target_minutes)
        source_rules = deepcopy(rules)
        source_rules["validation_timeframe"] = normalize_validation_timeframe(
            source_minutes,
            fallback_minutes=source_minutes,
        )
        source_snapshot = ValidationSettingsSnapshot(source_rules)
        source_request = ValidationRequest(
            session.request.stock,
            source_snapshot,
            source_minutes,
        )
        source_session = ValidationSession(
            source_request,
            operation_active_reader=self._operation_active_reader,
        )
        return (
            source_session,
            source_minutes,
            source_request.timeframe_key,
            required_regular_market_source_candles(
                target_minutes,
                target_count,
            ),
        )

    def _run_validation(
        self,
        window: object,
        run_request: object,
        *,
        history_extension: bool = False,
    ) -> None:
        window_key = id(window)
        if window_key not in self._open_windows:
            self.validation_failed.emit("SIGNAL_WINDOW_UNAVAILABLE")
            return
        if not isinstance(run_request, IndicatorFollowSignalValidationRunRequest):
            self._fail_window(window, "INVALID_SIGNAL_VALIDATION_RUN_REQUEST")
            return
        if not history_extension and window_key in self._history_extension_inflight:
            self._history_extension_inflight.pop(window_key, None)

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
            warmup_bars = required_validation_warmup_bars(snapshot_rules)
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
            (
                fetch_session,
                timeframe_minutes,
                timeframe_key,
                fetch_count,
            ) = self._validation_fetch_contract(
                session,
                target_fetch_count,
            )
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

        generation = self._request_generation.get(window_key, 0) + 1
        self._request_generation[window_key] = generation
        force_historical_refresh = run_request.force_historical_refresh
        if not force_historical_refresh:
            pool = self._historical_pools.get(window_key)
            if not self._pool_matches(pool, stock, timeframe_key, fetch_count):
                pool = _SHARED_SIGNAL_VALIDATION_HISTORICAL_POOLS.get(
                    self._shared_pool_key(stock, timeframe_key)
                )
            if self._pool_matches(pool, stock, timeframe_key, fetch_count):
                self._use_pool_for_window(
                    window,
                    session,
                    pool,
                    evaluation_count=run_request.candle_count,
                )
                return

        request_key = (window_key, generation)
        window_ref = weakref.ref(window)
        persistent = (
            None
            if force_historical_refresh
            else self._cached_history(
                stock,
                timeframe_minutes,
                timeframe_key,
                fetch_count,
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

        cached_pool = None
        persistent_capacity = fetch_count
        if persistent is not None:
            persistent_capacity = max(fetch_count, persistent.requested_count)
            cached_pool = self._pool_from_candles(
                fetch_session,
                [dict(candle) for candle in persistent.candles],
                requested_count=persistent_capacity,
                request_id="VALIDATION_CACHE_RESTORE",
                market_data_identity=persistent.market_data_identity,
                market_source=persistent.market_source,
            )
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
            self._install_pool(
                target,
                session,
                cached_pool,
                evaluation_count=run_request.candle_count,
            )

        if (
            persistent is not None
            and not self._persistent_cache_requires_broker_probe(
                persistent,
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

        def request_full() -> None:
            target = current_target()
            if target is None:
                return
            self._active_providers[request_key] = provider

            def full_completed(result: object) -> None:
                self._active_providers.pop(request_key, None)
                completed_target = current_target()
                if completed_target is None:
                    return
                self._handle_historical_result(
                    completed_target,
                    session,
                    result,
                    evaluation_count=run_request.candle_count,
                    cache_requested_count=fetch_count,
                    source_session=fetch_session,
                )

            try:
                request_latest(fetch_count, full_completed)
            except Exception as exc:
                self._active_providers.pop(request_key, None)
                if current_target() is not None:
                    self._fail_window(target, f"HISTORICAL_REQUEST_ERROR: {exc}")

        if persistent is None:
            request_full()
            return

        cached_latest_time = str(persistent.candles[-1]["time"])

        def request_refresh(probe_count: int) -> None:
            target = current_target()
            if target is None:
                return
            count = min(fetch_count, max(1, int(probe_count)))
            self._active_providers[request_key] = provider

            def refresh_completed(result: object) -> None:
                self._active_providers.pop(request_key, None)
                refresh_target = current_target()
                if refresh_target is None:
                    return
                if (
                    not isinstance(result, ValidationHistoricalResult)
                    or result.ok is not True
                    or result.snapshot is None
                ):
                    install_cached_pool()
                    return
                try:
                    incoming, _dropped_count = project_validation_candles(
                        result.snapshot
                    )
                except Exception:
                    install_cached_pool()
                    return
                if not incoming:
                    install_cached_pool()
                    return

                newest_time = str(incoming[-1].get("time") or "")
                if not newest_time or newest_time < cached_latest_time:
                    install_cached_pool()
                    return

                oldest_time = str(incoming[0].get("time") or "")
                if (
                    oldest_time
                    and oldest_time > cached_latest_time
                    and count < fetch_count
                ):
                    next_count = min(fetch_count, max(count + 1, count * 2))
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
                    install_cached_pool()
                    return
                if len(merged) < fetch_count:
                    install_cached_pool()
                    return

                request_id = str(result.snapshot.request_id or "").strip()
                pool = self._pool_from_candles(
                    fetch_session,
                    merged,
                    requested_count=persistent_capacity,
                    request_id=f"{request_id or 'VALIDATION_CACHE_REFRESH'}:CACHE_MERGE",
                    market_data_identity=result.snapshot.market_data_identity,
                    market_source=result.snapshot.market_source,
                )
                if current_target() is None:
                    return
                self._persist_history(
                    stock,
                    timeframe_minutes,
                    timeframe_key,
                    persistent_capacity,
                    merged,
                    market_data_identity=result.snapshot.market_data_identity,
                    market_source=result.snapshot.market_source,
                )
                if current_target() is None:
                    return
                self._install_pool(
                    refresh_target,
                    session,
                    pool,
                    evaluation_count=run_request.candle_count,
                )

            try:
                request_latest(count, refresh_completed)
            except Exception:
                self._active_providers.pop(request_key, None)
                install_cached_pool()

        request_refresh(min(_PERSISTENT_REFRESH_PROBE_COUNT, fetch_count))

    def _handle_historical_result(
        self,
        window: object,
        session: ValidationSession,
        result: object,
        *,
        evaluation_count: int,
        cache_requested_count: int | None = None,
        source_session: ValidationSession | None = None,
    ) -> bool:
        if (
            not isinstance(result, ValidationHistoricalResult)
            or result.ok is not True
            or result.snapshot is None
        ):
            self._fail_window(window, self._failure_text("HISTORICAL", result))
            return False
        try:
            candles, _dropped_count = project_validation_candles(result.snapshot)
        except Exception as exc:
            self._fail_window(window, f"HISTORICAL_PROJECTION_ERROR: {exc}")
            return False
        if not candles:
            self._fail_window(window, "HISTORICAL: NO_VALID_CANDLES")
            return False

        requested_count = int(cache_requested_count or result.snapshot.requested_count)
        candles = merge_validation_candles([], candles, requested_count)
        persistence_session = (
            source_session
            if isinstance(source_session, ValidationSession)
            else session
        )
        pool = self._pool_from_candles(
            persistence_session,
            candles,
            requested_count=requested_count,
            request_id=result.snapshot.request_id,
            market_data_identity=result.snapshot.market_data_identity,
            market_source=result.snapshot.market_source,
        )
        self._persist_history(
            persistence_session.request.stock,
            persistence_session.request.timeframe_minutes,
            result.snapshot.timeframe_key,
            requested_count,
            candles,
            market_data_identity=result.snapshot.market_data_identity,
            market_source=result.snapshot.market_source,
        )
        return self._install_pool(
            window,
            session,
            pool,
            evaluation_count=evaluation_count,
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
                + required_validation_warmup_bars(
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

        chart_count = min(history_target, len(candles))
        chart_start = len(candles) - chart_count
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
            evaluation_start = chart_start + start
            evaluation_end = chart_start + end
            count = end - start + 1

        rules = session.request.settings_snapshot.to_dict()
        warmup_bars = required_validation_warmup_bars(rules)
        slice_start = max(0, evaluation_start - warmup_bars)
        replay_candles = [
            dict(candle)
            for candle in candles[slice_start : evaluation_end + 1]
        ]
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
            replay = self._replay_factory(session)
            evaluate_with_context = getattr(replay, "evaluate_with_context", None)
            evaluate = getattr(replay, "evaluate", None)
            if callable(evaluate_with_context):
                replay_result = evaluate_with_context(
                    replay_historical,
                    context_provider=self._context_provider_for_session(session),
                    display_count=count,
                )
            elif callable(evaluate):
                replay_result = evaluate(
                    replay_historical,
                    display_count=count,
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
        self._replay_from_pool(
            window,
            session,
            pool,
            chart_range=(start_index, end_index),
        )

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
