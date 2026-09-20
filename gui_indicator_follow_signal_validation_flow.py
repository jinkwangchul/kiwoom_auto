# -*- coding: utf-8 -*-
"""Independent entry and replay flow for indicator signal Validation V2."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from threading import Thread
import weakref

from PyQt5.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt5.QtWidgets import QDialog, QWidget

from gui_toast import show_toast
from gui_indicator_follow_signal_validation_window import (
    IndicatorFollowSignalValidationWindow,
)
from indicator_follow_signal_validation_execution import ValidationVirtualPositionTracker
from indicator_follow_signal_validation_historical_cache import (
    IndicatorFollowSignalValidationHistoricalCache,
    ValidationHistoricalCacheEntry,
    merge_validation_candles,
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
    IndicatorFollowSignalValidationRestorePayload,
    IndicatorFollowSignalValidationRunRequest,
    IndicatorFollowSignalValidationSeed,
    build_validation_average_price_context,
)
from routines.지표추종매매.routine_validation_contract import (
    ValidationRequest,
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
    ValidationReplayResult,
)
from routines.지표추종매매.routine_validation_session import ValidationSession
from stock_code_contract import normalize_broker_stock_code


DEFAULT_SIGNAL_VALIDATION_HISTORICAL_COUNT = 5_000
_SHARED_SIGNAL_VALIDATION_HISTORICAL_POOLS: dict[
    tuple[int, str, int], dict[str, object]
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
            reset_signal = getattr(window, "entry_reset_requested", None)
            reset_started_signal = getattr(window, "entry_reset_started", None)
            if callable(getattr(reset_started_signal, "connect", None)):
                reset_started_signal.connect(
                    lambda ref=window_ref, source=source_ref: (
                        self._begin_entry_state_reset(ref(), source)
                    )
                )
            if callable(getattr(reset_signal, "connect", None)):
                reset_signal.connect(
                    lambda payload, ref=window_ref, source=source_ref: (
                        self._restore_entry_state_to_source(ref(), source, payload)
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
            fitted_signal = getattr(window, "recent_stocks_fitted", None)
            if callable(getattr(fitted_signal, "connect", None)):
                fitted_signal.connect(
                    lambda stocks, ref=window_ref: self._retain_recent_stock_projection(
                        ref(),
                        stocks,
                    )
                )
            if callable(getattr(window, "setAttribute", None)):
                window.setAttribute(Qt.WA_DeleteOnClose, True)
            key = id(window)
            self._open_windows[key] = window
            self._request_generation[key] = 0
            self._market_snapshot_generation[key] = 0
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
        try:
            changed = activate(stock) is True
        except Exception:
            changed = False
        recent = self._read_recent_stocks()
        self._last_selected_stock = (
            recent[0] if recent else ValidationStockRef(stock.code, stock.name)
        )
        return changed

    def _sync_window_stock_projection(self, window: object) -> None:
        set_recent = getattr(window, "set_recent_stocks", None)
        if callable(set_recent):
            set_recent(self._read_recent_stocks())
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

    def _retain_recent_stock_projection(self, window: object, stocks: object) -> None:
        if id(window) not in self._open_windows:
            return
        retain_prefix = getattr(self._recent_stock_store, "retain_prefix", None)
        if not callable(retain_prefix):
            return
        try:
            changed = retain_prefix(stocks) is True
        except Exception:
            changed = False
        if not changed:
            return
        recent = self._read_recent_stocks()
        if recent:
            self._last_selected_stock = recent[0]
        QTimer.singleShot(0, self._refresh_open_window_stock_projections)

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

    def _restore_entry_state_to_source(
        self,
        window: object,
        source_ref: weakref.ReferenceType[object],
        payload: object,
    ) -> None:
        window_key = id(window)
        if window_key not in self._open_windows:
            return
        set_result = getattr(window, "set_entry_reset_source_result", None)
        if not isinstance(payload, IndicatorFollowSignalValidationRestorePayload):
            if callable(set_result):
                set_result(False, "초기화 데이터가 올바르지 않습니다.")
            return
        source = source_ref()
        if not self._valid_requester(source):
            if callable(set_result):
                set_result(False, "원본 설정창이 닫혀 있어 초기화할 수 없습니다.")
            return
        restore_state = getattr(source, "restore_signal_validation_entry_ui_state", None)
        if not callable(restore_state):
            if callable(set_result):
                set_result(False, "원본 설정창에 진입 상태를 복원할 수 없습니다.")
            return
        try:
            result = restore_state(payload.to_ui_state())
        except Exception:
            if callable(set_result):
                set_result(False, "원본 설정창 초기화 중 오류가 발생했습니다.")
            return
        skipped = result.get("skipped", []) if isinstance(result, dict) else ["invalid_result"]
        if skipped:
            if callable(set_result):
                set_result(False, "일부 진입 상태를 복원할 수 없습니다.")
            return
        if callable(set_result):
            set_result(True, "")
        stock = getattr(window, "stock", None)
        if isinstance(stock, ValidationStockRef) and stock.code and stock.name:
            self._last_selected_stock = ValidationStockRef(stock.code, stock.name)
            self._request_market_snapshot_for_window(window)

    def _begin_entry_state_reset(
        self,
        window: object,
        source_ref: weakref.ReferenceType[object],
    ) -> None:
        window_key = id(window)
        if window_key not in self._open_windows:
            return
        self._invalidate_window_requests(window_key)
        source = source_ref()
        restore_state = getattr(
            source,
            "restore_signal_validation_entry_ui_state",
            None,
        ) if self._valid_requester(source) else None
        set_result = getattr(window, "set_entry_reset_source_result", None)
        if callable(set_result):
            if callable(restore_state):
                set_result(True, "")
            elif self._valid_requester(source):
                set_result(False, "원본 설정창에 진입 상태를 복원할 수 없습니다.")
            else:
                set_result(False, "원본 설정창이 닫혀 있어 초기화할 수 없습니다.")

    def _release_window(self, window_key: int) -> None:
        self._open_windows.pop(window_key, None)
        self._request_generation.pop(window_key, None)
        self._market_snapshot_generation.pop(window_key, None)
        self._market_snapshot_metadata.pop(window_key, None)
        self._historical_pools.pop(window_key, None)
        self._validation_sessions.pop(window_key, None)
        for request_key in list(self._active_providers):
            if request_key[0] == window_key:
                self._active_providers.pop(request_key, None)

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
        timeframe: int,
    ) -> tuple[int, str, int]:
        return (id(self._broker), stock.code, int(timeframe))

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

    def _pool_matches(
        self,
        pool: object,
        stock: ValidationStockRef,
        timeframe: int,
        required_count: int,
    ) -> bool:
        return (
            isinstance(pool, dict)
            and pool.get("stock") == stock
            and pool.get("timeframe_minutes") == timeframe
            and isinstance(pool.get("requested_count"), int)
            and int(pool["requested_count"]) >= required_count
            and isinstance(pool.get("candles"), list)
            and bool(pool["candles"])
        )

    @staticmethod
    def _incremental_request_count(
        entry: ValidationHistoricalCacheEntry,
        timeframe_minutes: int,
        fetch_count: int,
    ) -> int:
        try:
            latest_time = str(entry.candles[-1]["time"])
            latest = datetime.strptime(latest_time, "%Y%m%d%H%M%S").replace(
                tzinfo=SEOUL_TIMEZONE
            )
            current = datetime.now(SEOUL_TIMEZONE)
            elapsed_seconds = max(0.0, (current - latest).total_seconds())
            elapsed_bars = int(elapsed_seconds // (timeframe_minutes * 60))
            return min(fetch_count, max(1, elapsed_bars + 2))
        except (IndexError, KeyError, TypeError, ValueError):
            return fetch_count

    def _cached_history(
        self,
        stock: ValidationStockRef,
        timeframe: int,
        fetch_count: int,
    ) -> ValidationHistoricalCacheEntry | None:
        load = getattr(self._historical_cache, "load", None)
        if not callable(load):
            return None
        try:
            entry = load(stock.code, timeframe, fetch_count)
        except Exception:
            return None
        return entry if isinstance(entry, ValidationHistoricalCacheEntry) else None

    def _persist_history(
        self,
        stock: ValidationStockRef,
        timeframe: int,
        fetch_count: int,
        candles: list[dict[str, object]],
    ) -> bool:
        store = getattr(self._historical_cache, "store", None)
        if not callable(store):
            return False
        try:
            return store(
                stock_code=stock.code,
                stock_name=stock.name,
                timeframe_minutes=timeframe,
                requested_count=fetch_count,
                candles=candles,
            ) is True
        except Exception:
            return False

    def _pool_from_candles(
        self,
        session: ValidationSession,
        candles: list[dict[str, object]],
        *,
        requested_count: int,
        request_id: str,
    ) -> dict[str, object]:
        snapshot = ValidationHistoricalSnapshot(
            stock=session.request.stock,
            timeframe_minutes=session.request.timeframe_minutes,
            requested_count=requested_count,
            request_id=request_id,
            rows=self._historical_rows_from_candles(candles),
        )
        return {
            "stock": session.request.stock,
            "timeframe_minutes": session.request.timeframe_minutes,
            "requested_count": requested_count,
            "request_id": request_id,
            "snapshot": snapshot,
            "candles": candles,
            "signal_entries_by_settings_hash": {},
        }

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
                session.request.timeframe_minutes,
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
    ) -> None:
        install_pool = getattr(window, "set_historical_candle_pool", None)
        if callable(install_pool):
            install_pool(
                pool["candles"],
                chart_candle_count=self._historical_count,
            )
        set_signal_entries = getattr(window, "set_signal_marker_entries", None)
        if callable(set_signal_entries) and signal_entries is not None:
            set_signal_entries(list(signal_entries))
        self._replay_from_pool(
            window,
            session,
            pool,
            evaluation_count=evaluation_count,
        )

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

        historical_snapshot = pool.get("snapshot")
        settings_hash = session.request.settings_snapshot.rules_hash
        cached_by_settings = pool.setdefault(
            "signal_entries_by_settings_hash",
            {},
        )
        signal_entries = (
            cached_by_settings.get(settings_hash)
            if isinstance(cached_by_settings, dict)
            else None
        )
        if signal_entries is not None:
            self._apply_pool_to_window(
                window,
                session,
                pool,
                evaluation_count=evaluation_count,
                signal_entries=signal_entries,
            )
            return

        self._apply_pool_to_window(
            window,
            session,
            pool,
            evaluation_count=evaluation_count,
            signal_entries=None,
        )

        if not isinstance(historical_snapshot, ValidationHistoricalSnapshot):
            return

        replay = self._replay_factory(session)
        scan = getattr(replay, "scan_signal_entries", None)
        if not callable(scan):
            return

        generation = self._request_generation.get(window_key, 0)
        payload_base = {
            "window_key": window_key,
            "generation": generation,
            "session": session,
            "pool": pool,
            "evaluation_count": evaluation_count,
            "settings_hash": settings_hash,
        }

        def run_scan() -> None:
            try:
                entries = tuple(
                    scan(
                        historical_snapshot,
                        context_provider=self._context_provider_for_session(
                            session
                        ),
                    )
                )
                payload = dict(payload_base)
                payload["entries"] = entries
                payload["error"] = ""
            except Exception as exc:
                payload = dict(payload_base)
                payload["entries"] = ()
                payload["error"] = str(exc)
            self.signal_scan_completed.emit(payload)

        Thread(
            target=run_scan,
            name="indicator-follow-signal-scan",
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
        if isinstance(cached_by_settings, dict) and settings_hash:
            cached_by_settings[settings_hash] = entries
        set_signal_entries = getattr(window, "set_signal_marker_entries", None)
        if callable(set_signal_entries):
            set_signal_entries(list(entries))

    def _run_validation(self, window: object, run_request: object) -> None:
        window_key = id(window)
        if window_key not in self._open_windows:
            self.validation_failed.emit("SIGNAL_WINDOW_UNAVAILABLE")
            return
        if not isinstance(run_request, IndicatorFollowSignalValidationRunRequest):
            self._fail_window(window, "INVALID_SIGNAL_VALIDATION_RUN_REQUEST")
            return

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
            timeframe = snapshot_rules["bar"]["bar_minutes"]
            warmup_bars = required_validation_warmup_bars(snapshot_rules)
            fetch_count = self._historical_count + warmup_bars
            if fetch_count > DEFAULT_CANDLES_MAX_COUNT:
                self._fail_window(window, "HISTORICAL_REQUEST_COUNT_EXCEEDS_LIMIT")
                return
            request = ValidationRequest(stock, snapshot, timeframe)
            session = ValidationSession(
                request,
                operation_active_reader=self._operation_active_reader,
            )
            availability = session.readiness()
            if not availability.allowed:
                self._fail_window(window, str(availability.reason or "VALIDATION_BLOCKED"))
                return
        except Exception as exc:
            self._fail_window(window, f"HISTORICAL_PROVIDER_ERROR: {exc}")
            return

        generation = self._request_generation.get(window_key, 0) + 1
        self._request_generation[window_key] = generation
        pool = self._historical_pools.get(window_key)
        if not self._pool_matches(pool, stock, timeframe, fetch_count):
            pool = _SHARED_SIGNAL_VALIDATION_HISTORICAL_POOLS.get(
                self._shared_pool_key(stock, timeframe)
            )
        if self._pool_matches(pool, stock, timeframe, fetch_count):
            self._use_pool_for_window(
                window,
                session,
                pool,
                evaluation_count=run_request.candle_count,
            )
            return

        try:
            provider = self._historical_provider_factory(session, self._broker)
        except Exception as exc:
            self._fail_window(window, f"HISTORICAL_PROVIDER_ERROR: {exc}")
            return

        request_key = (window_key, generation)
        window_ref = weakref.ref(window)
        persistent = self._cached_history(stock, timeframe, fetch_count)

        def current_target() -> object | None:
            target = window_ref()
            if (
                target is None
                or self._request_generation.get(window_key) != generation
            ):
                return None
            return target

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
                )

            try:
                request_latest(fetch_count, full_completed)
            except Exception as exc:
                self._active_providers.pop(request_key, None)
                if current_target() is not None:
                    self._fail_window(target, f"HISTORICAL_REQUEST_ERROR: {exc}")

        def incremental_completed(result: object) -> None:
            self._active_providers.pop(request_key, None)
            target = current_target()
            if target is None or persistent is None:
                return
            if (
                not isinstance(result, ValidationHistoricalResult)
                or result.ok is not True
                or result.snapshot is None
            ):
                request_full()
                return
            try:
                incoming, _dropped_count = project_validation_candles(
                    result.snapshot
                )
            except Exception:
                request_full()
                return
            merged = merge_validation_candles(
                persistent.candles,
                incoming,
                fetch_count,
            )
            if len(merged) < fetch_count:
                request_full()
                return
            if current_target() is None:
                return
            request_id = (
                result.snapshot.request_id
                if isinstance(result, ValidationHistoricalResult)
                and result.snapshot is not None
                else "VALIDATION_CACHE_INCREMENTAL"
            )
            pool = self._pool_from_candles(
                session,
                merged,
                requested_count=fetch_count,
                request_id=f"{request_id}:CACHE_MERGE",
            )
            if current_target() is None:
                return
            self._persist_history(stock, timeframe, fetch_count, merged)
            if current_target() is None:
                return
            self._install_pool(
                target,
                session,
                pool,
                evaluation_count=run_request.candle_count,
            )

        request_latest = getattr(provider, "request_latest", None)
        if not callable(request_latest):
            self._fail_window(window, "HISTORICAL_PROVIDER_UNAVAILABLE")
            return
        if persistent is None:
            request_full()
            return
        incremental_count = self._incremental_request_count(
            persistent,
            timeframe,
            fetch_count,
        )
        self._active_providers[request_key] = provider
        try:
            request_latest(incremental_count, incremental_completed)
        except Exception:
            self._active_providers.pop(request_key, None)
            request_full()

    def _handle_historical_result(
        self,
        window: object,
        session: ValidationSession,
        result: object,
        *,
        evaluation_count: int,
        cache_requested_count: int | None = None,
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
        pool = self._pool_from_candles(
            session,
            candles,
            requested_count=requested_count,
            request_id=result.snapshot.request_id,
        )
        self._persist_history(
            session.request.stock,
            session.request.timeframe_minutes,
            requested_count,
            candles,
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
        if not isinstance(pool, dict) or not isinstance(pool.get("candles"), list):
            self._fail_window(window, "HISTORICAL_POOL_UNAVAILABLE")
            return
        candles = pool["candles"]
        if not candles:
            self._fail_window(window, "HISTORICAL_POOL_EMPTY")
            return

        chart_count = min(self._historical_count, len(candles))
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
                requested_count=len(replay_candles),
                request_id=str(pool.get("request_id") or "POOL"),
                rows=self._historical_rows_from_candles(replay_candles),
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
