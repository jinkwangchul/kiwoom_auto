# -*- coding: utf-8 -*-
"""Widget-free market-data ownership boundary for the operation host."""

from __future__ import annotations

from collections import OrderedDict, deque
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
import logging
from math import isfinite
import os
from pathlib import Path
from time import monotonic
from typing import Any, Callable

from PyQt5.QtCore import QObject, QTimer, pyqtSignal

from candle_manager import canonical_candle_content_hash, load_candles
from candle_timeframe_aggregation import (
    SEOUL_TIMEZONE,
    candle_market_datetime,
    parse_market_datetime,
    project_candle_supply,
    read_canonical_bar_minutes,
    required_minute_candles,
    validate_market_bar_projection_request,
)
from event_journal_production import observe_production_exception
from gui_stock_data import (
    STOCK_LIBRARY_READY,
    load_stock_library_snapshot,
)
from kiwoom_market_data_authority import (
    MarketDataAuthority,
    NORMAL_TR_REFRESH,
    REALTIME_AUTHORITY,
    REALTIME_ELIGIBLE,
    REALTIME_PRIMARY,
    REALTIME_PRIMARY_SKIP,
    REALTIME_RECONCILIATION,
    REALTIME_RECONCILIATION_REQUEST,
    TR_PRIMARY_REFRESH,
    TR_RECONCILIATION_AUTHORITY,
    TR_RECONCILING,
)
from kiwoom_realtime_shadow import (
    NO_CANONICAL_BAR,
    RealtimeShadowBar,
    compare_shadow_bar_to_canonical,
)
from stock_repository import StockRepository
from stock_code_contract import (
    canonical_stock_code_from_market_data_identity,
    market_source_for_identity,
)


LOGGER = logging.getLogger(__name__)


ExecutionEntryProvider = Callable[[str], object | None]

HIGH_RESOLUTION_DATA_NORMAL = "NORMAL"
HIGH_RESOLUTION_DATA_UNCERTAIN = "UNCERTAIN"
_PRICE_SIGNAL_OBSERVATION_GENERATION_KEY = "_price_signal_observation_generation"


@dataclass(frozen=True)
class HighResolutionMarketState:
    stock_code: str
    connection_epoch: int
    login_session_id: str
    last_execution_time_raw: str
    last_market_datetime: str
    last_price: int | float
    last_trade_volume_raw: int | float | None
    last_trade_volume_abs: int | float | None
    last_cumulative_volume: int | float | None
    last_receive_sequence: int
    last_received_at: str
    last_received_monotonic: int | float
    received_tick_count: int
    processed_tick_count: int
    data_quality: str
    broker_code_identity: str = ""
    market_source: str = ""
    open_price: int | float | None = None
    high_price: int | float | None = None
    low_price: int | float | None = None
    change_rate: int | float | None = None
    previous_day_volume_rate: int | float | None = None
    execution_strength: int | float | None = None


@dataclass(frozen=True)
class NxtDisplayLivePriceState:
    canonical_stock_code: str
    broker_code_identity: str
    market_source: str
    source_real_type: str
    connection_epoch: int
    login_session_id: str
    last_execution_time_raw: str
    last_market_datetime: str
    last_price: int | float
    execution_quantity: int | float | None
    cumulative_volume: int | float | None
    receive_sequence: int
    data_quality: str
    updated_at: str


@dataclass(frozen=True)
class InitialMarketSnapshotState:
    stock_code: str
    connection_epoch: int
    login_session_id: str
    current_price: int | float | None
    open_price: int | float | None
    high_price: int | float | None
    low_price: int | float | None
    change_rate: int | float | None
    previous_day_volume_rate: int | float | None
    execution_strength: int | float | None
    cumulative_volume: int | float | None
    received_at: str
    source: str = "SNAPSHOT"


@dataclass(frozen=True)
class MonitoringMarketInformationState:
    stock_code: str
    connection_epoch: int
    login_session_id: str
    last_price: int | float | None
    open_price: int | float | None
    high_price: int | float | None
    low_price: int | float | None
    change_rate: int | float | None
    previous_day_volume_rate: int | float | None
    execution_strength: int | float | None
    snapshot_received_at: str
    realtime_received_at: str
    field_sources: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class HighResolutionMarketDataSnapshot:
    connection_epoch: int
    login_session_id: str
    broker_connected: bool
    realtime_registration_active: bool
    realtime_target_stock_count: int
    received_tick_count: int
    processed_tick_count: int
    current_queue_depth: int
    queue_high_watermark: int
    overflow_count: int
    last_receive_sequence: int
    last_processed_sequence: int
    last_tick_received_at: str
    last_tick_processed_at: str
    last_processing_latency_ms: float
    max_processing_latency_ms: float
    data_quality: str
    realtime_shadow_target_stock_count: int = 0


class MarketDataHost(QObject):
    """Own market-data state while sharing the application's single KiwoomApi."""

    canonical_bar_ready_for_operation = pyqtSignal(object)
    realtime_shadow_comparison_completed = pyqtSignal(object)
    market_data_observed = pyqtSignal(object)
    high_resolution_price_observed = pyqtSignal(object)

    MAX_PENDING_SHADOW_COMPARISONS = 512
    MAX_RAW_TICK_QUEUE_DEPTH = 512
    MAX_INITIAL_SNAPSHOT_ATTEMPTS_PER_SESSION = 2

    @staticmethod
    def _registration_shadow_targets(registration: object) -> tuple[str, ...]:
        targets = getattr(registration, "shadow_target_stock_codes", None)
        if targets is None:
            targets = getattr(registration, "target_stock_codes", ())
        return tuple(targets or ())

    def __init__(
        self,
        owner: QObject,
        kiwoom_api: object,
        execution_entry_provider: ExecutionEntryProvider,
    ) -> None:
        super().__init__(owner)
        self._owner = owner
        self.kiwoom_api = kiwoom_api
        self._execution_entry_provider = execution_entry_provider
        self._shutting_down = False

        self._operation_candle_requests: dict[str, dict[str, object]] = {}
        self._canonical_event_queue: deque[dict[str, object]] = deque()
        self._canonical_drain_scheduled = False
        self._canonical_drain_running = False
        self._last_ready_commit_identity_by_stock: dict[str, str] = {}

        self._raw_tick_queue: deque[dict[str, object]] = deque()
        self._raw_tick_drain_scheduled = False
        self._raw_tick_drain_running = False
        self._high_resolution_market_states: dict[
            str, HighResolutionMarketState
        ] = {}
        self._nxt_display_live_price_states: dict[
            str, NxtDisplayLivePriceState
        ] = {}
        self._nxt_display_tick_queue: deque[dict[str, object]] = deque()
        self._nxt_display_tick_drain_scheduled = False
        self._nxt_display_tick_drain_running = False
        self._initial_market_snapshot_states: dict[
            str, InitialMarketSnapshotState
        ] = {}
        self._monitoring_target_stock_codes: tuple[str, ...] = ()
        self._production_monitoring_stock_codes: tuple[str, ...] = ()
        self._execution_shadow_stock_codes: tuple[str, ...] = ()
        self._candle_observation_stock_codes: tuple[str, ...] = ()
        self._candle_execution_required_by_stock: dict[str, int] = {}
        self._candle_observation_required_by_stock: dict[str, int] = {}
        self._candle_standby_stock_codes: tuple[str, ...] = ()
        self._candle_standby_required_by_stock: dict[str, int] = {}
        self._last_candle_observation_refresh_minute = ""
        # In-flight only.  A completed request is represented by an actual
        # valid InitialMarketSnapshotState, not by this deduplication marker.
        self._initial_snapshot_requested_stock_codes: set[str] = set()
        self._initial_snapshot_request_attempts_by_stock: dict[str, int] = {}
        self._raw_tick_received_count_by_stock: dict[str, int] = {}
        self._raw_tick_processed_count_by_stock: dict[str, int] = {}
        self._realtime_boundary_diagnostic_counts: dict[str, dict[str, int]] = {}
        self._realtime_boundary_diagnostic_last: dict[tuple[str, str], dict[str, object]] = {}
        self._realtime_boundary_diagnostic_transition: dict[tuple[str, str], str] = {}
        self._raw_tick_uncertain_stock_codes: set[str] = set()
        self._raw_tick_received_count = 0
        self._raw_tick_processed_count = 0
        self._raw_tick_queue_high_watermark = 0
        self._raw_tick_overflow_count = 0
        self._raw_tick_last_receive_sequence = 0
        self._raw_tick_last_processed_sequence = 0
        self._raw_tick_last_received_at = ""
        self._raw_tick_last_processed_at = ""
        self._raw_tick_last_processing_latency_ms = 0.0
        self._raw_tick_max_processing_latency_ms = 0.0
        self._price_signal_observation_enabled = False
        self._price_signal_observation_generation = 0
        self._last_known_market_information_states: dict[
            str, MonitoringMarketInformationState
        ] = {}

        self._realtime_shadow_trigger_queue: deque[dict[str, object]] = deque()
        self._realtime_shadow_retry_codes: set[str] = set()
        self._realtime_shadow_drain_scheduled = False
        self._realtime_shadow_drain_running = False
        self._pending_shadow_comparisons: OrderedDict[
            tuple[str, str], RealtimeShadowBar
        ] = OrderedDict()
        self._last_realtime_shadow_comparison: dict[str, object] = {}
        self._realtime_shadow_session_identity: tuple[int, str] = (0, "")

        self._market_data_authority = MarketDataAuthority()
        self._pending_reconciliations: set[tuple[str, str]] = set()
        self._reconciliation_shadow_bars: OrderedDict[
            tuple[str, str], RealtimeShadowBar
        ] = OrderedDict()

        self._bar_committed_signal_bound = False
        self._realtime_shadow_signal_bound = False
        self._raw_realtime_tick_signal_bound = False
        self._nxt_display_tick_signal_bound = False
        self._bind_kiwoom_signals_once()

    def _bind_kiwoom_signals_once(self) -> bool:
        if not self._bar_committed_signal_bound:
            signal = getattr(self.kiwoom_api, "bar_committed", None)
            connect = getattr(signal, "connect", None)
            if callable(connect):
                try:
                    connect(self._on_bar_committed)
                except Exception as exc:
                    self._observe_exception(exc, "bind_bar_committed")
                else:
                    self._bar_committed_signal_bound = True
        if not self._realtime_shadow_signal_bound:
            signal = getattr(self.kiwoom_api, "realtime_shadow_bar_completed", None)
            connect = getattr(signal, "connect", None)
            if callable(connect):
                try:
                    connect(self._on_realtime_shadow_bar_completed)
                except Exception as exc:
                    self._observe_exception(exc, "bind_realtime_shadow")
                else:
                    self._realtime_shadow_signal_bound = True
        if not self._raw_realtime_tick_signal_bound:
            signal = getattr(self.kiwoom_api, "realtime_shadow_tick_received", None)
            connect = getattr(signal, "connect", None)
            if callable(connect):
                try:
                    connect(self._on_realtime_shadow_tick_received)
                except Exception as exc:
                    self._observe_exception(exc, "bind_realtime_tick")
                else:
                    self._raw_realtime_tick_signal_bound = True
        if not self._nxt_display_tick_signal_bound:
            signal = getattr(self.kiwoom_api, "nxt_display_tick_received", None)
            connect = getattr(signal, "connect", None)
            if callable(connect):
                try:
                    connect(self._on_nxt_display_tick_received)
                except Exception as exc:
                    self._observe_exception(exc, "bind_nxt_display_tick")
                else:
                    self._nxt_display_tick_signal_bound = True
        return bool(
            self._bar_committed_signal_bound
            and self._realtime_shadow_signal_bound
            and self._raw_realtime_tick_signal_bound
        )

    def sync_targets(self, snapshot) -> dict[str, object]:
        """Sync only Shadow/authority targets supplied by the execution universe."""
        try:
            self._execution_shadow_stock_codes = tuple(getattr(snapshot, "execution_stock_codes", ()))
            self._candle_execution_required_by_stock = {
                code: count
                for code, count in self._candle_execution_required_by_stock.items()
                if code in self._execution_shadow_stock_codes
            }
            target_codes = tuple(
                sorted(
                    set(self._production_monitoring_stock_codes)
                    | set(self._execution_shadow_stock_codes)
                    | set(self._candle_observation_stock_codes)
                    | set(getattr(self, "_candle_standby_stock_codes", ()))
                )
            )
            for code in set(self._nxt_display_live_price_states).difference(target_codes):
                self._nxt_display_live_price_states.pop(code, None)
            sync = getattr(self.kiwoom_api, "sync_realtime_shadow_targets", None)
            if not callable(sync):
                # Compatibility for older test doubles; Production KiwoomApi owns
                # the split local-target method.
                sync = getattr(self.kiwoom_api, "sync_realtime_shadow_registration", None)
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
                self._clear_session_state()
                self._market_data_authority.ensure_session(*identity)
                self._realtime_shadow_session_identity = identity
            self._market_data_authority.sync_targets(target_codes)
            if not bool(getattr(registration, "active", False)):
                for code in target_codes:
                    self._market_data_authority.force_tr_primary(
                        code,
                        "REALTIME_REGISTRATION_INACTIVE",
                    )
            if isinstance(result, dict):
                projection = dict(result)
                projection["nxt_display_sync"] = self._sync_nxt_display_targets(
                    target_codes
                )
                return projection
            return {
                "ok": False,
                "changed": False,
                "active": False,
                "reason_code": "REALTIME_SHADOW_RESULT_MALFORMED",
            }
        except Exception as exc:
            self._observe_exception(exc, "sync_targets")
            return {
                "ok": False,
                "changed": False,
                "active": False,
                "reason_code": "REALTIME_SHADOW_SYNC_FAILED",
                "error": str(exc),
            }

    def sync_candle_observation_targets(self, stock_codes: object) -> dict[str, object]:
        """Keep non-executing Candle consumers in the existing Shadow pipeline."""
        previous_codes = tuple(self._candle_observation_stock_codes)
        previous_requirements = dict(self._candle_observation_required_by_stock)
        candidates = stock_codes if isinstance(stock_codes, (list, tuple, set, frozenset)) else ()
        requested: dict[str, int] = {}
        normalized_codes: set[str] = set()
        for item in candidates:
            if isinstance(item, dict):
                code = str(item.get("stock_code") or "").strip()
                if not code:
                    continue
                try:
                    interval = read_canonical_bar_minutes(item.get("rules"))
                    request = validate_market_bar_projection_request(
                        item.get("projection_request"),
                        require_warmup=True,
                    )
                    requirement = required_minute_candles(interval, request["warmup_bars"])
                    if not requirement["within_limit"]:
                        raise ValueError(str(requirement["reason"]))
                    requested[code] = max(requested.get(code, 0), int(requirement["required_minute_candles"]))
                    normalized_codes.add(code)
                except (TypeError, ValueError):
                    continue
            else:
                code = str(item or "").strip()
                if code:
                    normalized_codes.add(code)
        self._candle_observation_stock_codes = tuple(
            sorted(normalized_codes)
        )
        self._candle_observation_required_by_stock = requested
        targets = tuple(
            sorted(
                set(self._production_monitoring_stock_codes)
                | set(self._execution_shadow_stock_codes)
                | set(self._candle_observation_stock_codes)
                | set(getattr(self, "_candle_standby_stock_codes", ()))
            )
        )
        monitoring_result = self.sync_monitoring_targets(
            getattr(self, "_production_monitoring_stock_codes", ())
        )
        result = (
            monitoring_result.get("shadow_sync")
            if isinstance(monitoring_result, dict)
            and isinstance(monitoring_result.get("shadow_sync"), dict)
            else None
        )
        if result is None:
            sync = getattr(self.kiwoom_api, "sync_realtime_shadow_targets", None)
            if not callable(sync):
                return {"ok": False, "reason_code": "REALTIME_SHADOW_API_UNAVAILABLE"}
            result = sync(targets)
        if self._candle_observation_stock_codes:
            minute_key = datetime.now(SEOUL_TIMEZONE).strftime("%Y-%m-%d %H:%M")
            changed = (
                previous_codes != self._candle_observation_stock_codes
                or previous_requirements != requested
            )
            if changed or getattr(self, "_last_candle_observation_refresh_minute", "") != minute_key:
                self._last_candle_observation_refresh_minute = minute_key
                QTimer.singleShot(0, lambda: self.refresh_operation_candles(minute_key))
        else:
            self._last_candle_observation_refresh_minute = ""
        return result

    def sync_candle_standby_requirements(
        self,
        requirements: object,
        *,
        preserve_stock_codes: object = (),
    ) -> dict[str, object]:
        """Replace Production standby needs, preserving only explicitly failed current Stocks."""
        candidates = (
            requirements
            if isinstance(requirements, (list, tuple, set, frozenset))
            else ()
        )
        previous = dict(getattr(self, "_candle_standby_required_by_stock", {}))
        preserve_candidates = (
            preserve_stock_codes
            if isinstance(preserve_stock_codes, (list, tuple, set, frozenset))
            else ()
        )
        requested: dict[str, int] = {}
        errors: list[dict[str, str]] = []
        failed_codes: set[str] = set()
        for item in candidates:
            if not isinstance(item, dict):
                continue
            code = str(item.get("stock_code") or "").strip()
            if not code:
                continue
            try:
                interval = read_canonical_bar_minutes(item.get("rules"))
                request = validate_market_bar_projection_request(
                    item.get("projection_request"),
                    require_warmup=True,
                )
                requirement = required_minute_candles(
                    interval,
                    request["warmup_bars"],
                )
                if not requirement["within_limit"]:
                    raise ValueError(str(requirement["reason"]))
                requested[code] = max(
                    requested.get(code, 0),
                    int(requirement["required_minute_candles"]),
                )
            except (TypeError, ValueError) as exc:
                failed_codes.add(code)
                errors.append({"stock_code": code, "error": str(exc)})
        preserved_codes: list[str] = []
        preserve_codes = {
            str(code or "").strip()
            for code in (*preserve_candidates, *failed_codes)
            if str(code or "").strip()
        }
        for code in sorted(preserve_codes):
            if code in requested or code not in previous:
                continue
            requested[code] = int(previous[code])
            preserved_codes.append(code)
        self._candle_standby_required_by_stock = requested
        self._candle_standby_stock_codes = tuple(sorted(requested))
        return {
            "ok": not errors,
            "standby_stock_codes": self._candle_standby_stock_codes,
            "standby_requirements": dict(requested),
            "preserved_stock_codes": tuple(preserved_codes),
            "errors": tuple(errors),
        }

    def candle_history_required_count(self, stock_code: object) -> int:
        code = str(stock_code or "").strip()
        declared = max(
            int(self._candle_execution_required_by_stock.get(code, 0)),
            int(self._candle_observation_required_by_stock.get(code, 0)),
            int(getattr(self, "_candle_standby_required_by_stock", {}).get(code, 0)),
        )
        return declared if declared > 0 else 600

    def registered_operation_targets(self) -> tuple[tuple[Path, str, str], ...]:
        codes = tuple(sorted(
            set(self._candle_execution_required_by_stock)
            | set(self._candle_observation_stock_codes)
            | set(getattr(self, "_candle_standby_stock_codes", ()))
        ))
        return tuple((StockRepository().resolve_stock_dir(code), code, "") for code in codes)

    def project_routine_candles(
        self,
        *,
        stock_code: object,
        rules: dict[str, Any] | None,
        projection_request: dict[str, Any] | None,
        as_of: datetime | None = None,
        session_windows: object = None,
        consumer_scope: str = "PRODUCTION",
    ) -> dict[str, Any]:
        """Supply one Routine Instance from Production-owned Candle facts."""
        code = str(stock_code or "").strip()
        try:
            request = validate_market_bar_projection_request(
                projection_request,
                require_warmup=False,
            )
        except (TypeError, ValueError) as exc:
            return {"available": False, "stock_code": code, "candles": [], "availability_state": "PROJECTION_REQUEST_INVALID", "reason": str(exc)}
        projection = request["projection"]
        warmup_declared = "warmup_bars" in request
        try:
            interval = read_canonical_bar_minutes(rules)
            warmup = required_minute_candles(
                interval,
                request["warmup_bars"] if warmup_declared else 1,
            )
            if not warmup_declared:
                warmup = {**warmup, "required_minute_candles": 0}
        except (TypeError, ValueError) as exc:
            return {"available": False, "stock_code": code, "candles": [], "availability_state": "INTERVAL_UNSUPPORTED", "reason": str(exc)}
        base = {"stock_code": code, "timeframe_minutes": interval, "projection": projection, **warmup}
        requirement_attribute = (
            "_candle_observation_required_by_stock"
            if str(consumer_scope or "").strip().upper() == "MOCK"
            else "_candle_execution_required_by_stock"
        )
        try:
            history_requirements = object.__getattribute__(self, requirement_attribute)
        except (AttributeError, RuntimeError):
            history_requirements = None
        if warmup_declared and isinstance(history_requirements, dict):
            history_requirements[code] = max(
                int(history_requirements.get(code, 0)),
                int(warmup["required_minute_candles"]),
            )
        if not warmup["within_limit"]:
            return {**base, "available": False, "candles": [], "availability_state": "WARMUP_LIMIT_EXCEEDED"}
        raw = load_candles(StockRepository().resolve_stock_dir(code))
        source_hash = canonical_candle_content_hash(raw) if raw else ""
        forming = None
        if projection == "FORMING_BASE_BAR":
            current_reader = getattr(self.kiwoom_api, "current_realtime_shadow_bar", None)
            forming = current_reader(code) if callable(current_reader) else None
        now = as_of or datetime.now(SEOUL_TIMEZONE)
        result = project_candle_supply(
            raw,
            rules,
            request,
            now=now,
            session_windows=session_windows,
            forming_minute=forming if isinstance(forming, dict) else None,
        )
        source_rows = list(raw)
        if isinstance(forming, dict):
            source_rows.append(dict(forming))
        return {
            **result,
            "stock_code": code,
            "source_identity": canonical_candle_content_hash(source_rows) if source_rows else source_hash,
        }

    @staticmethod
    def _nxt_eligible_targets(stock_codes: object) -> tuple[str, ...]:
        snapshot = load_stock_library_snapshot()
        if snapshot.state != STOCK_LIBRARY_READY:
            return ()
        requested = {
            str(code or "").strip()
            for code in (stock_codes or ())
            if str(code or "").strip()
        }
        return tuple(
            sorted(
                str(record.get("code") or "").strip()
                for record in snapshot.records
                if record.get("nxt_available") is True
                and str(record.get("code") or "").strip() in requested
            )
        )

    def _sync_nxt_display_targets(self, stock_codes: object) -> dict[str, object]:
        sync = getattr(self.kiwoom_api, "sync_nxt_display_registration", None)
        if not callable(sync):
            return {
                "ok": False,
                "changed": False,
                "active": False,
                "reason_code": "NXT_DISPLAY_API_UNAVAILABLE",
            }
        targets = self._nxt_eligible_targets(stock_codes)
        result = sync(targets)
        return dict(result) if isinstance(result, dict) else {
            "ok": False,
            "changed": False,
            "active": False,
            "reason_code": "NXT_DISPLAY_RESULT_MALFORMED",
        }

    def sync_monitoring_targets(self, stock_codes: object) -> dict[str, object]:
        """Sync the Broker registration set independently of execution readiness."""

        sync = getattr(self.kiwoom_api, "sync_realtime_monitoring_registration", None)
        if not callable(sync):
            return {
                "ok": False,
                "changed": False,
                "active": False,
                "reason_code": "REALTIME_MONITORING_API_UNAVAILABLE",
            }
        try:
            production_targets = tuple(
                sorted(
                    {
                        str(code or "").strip()
                        for code in (stock_codes or ())
                        if str(code or "").strip()
                    }
                )
            )
            targets = tuple(
                sorted(
                    set(production_targets)
                    | set(self._candle_observation_stock_codes)
                    | set(getattr(self, "_candle_standby_stock_codes", ()))
                )
            )
            previous_targets = set(self._monitoring_target_stock_codes)
            session_getter = getattr(self.kiwoom_api, "broker_session_snapshot", None)
            session = session_getter() if callable(session_getter) else None
            requested_identity = (
                int(getattr(session, "connection_epoch", 0) or 0),
                str(getattr(session, "login_session_id", "") or ""),
            )
            if (
                requested_identity[1]
                and requested_identity != self._realtime_shadow_session_identity
            ):
                self._clear_session_state()
                self._market_data_authority.ensure_session(*requested_identity)
                self._realtime_shadow_session_identity = requested_identity
            self._production_monitoring_stock_codes = production_targets

            removed = previous_targets.difference(targets)
            for code in removed:
                self._initial_market_snapshot_states.pop(code, None)
                self._initial_snapshot_requested_stock_codes.discard(code)
                self._initial_snapshot_request_attempts_by_stock.pop(code, None)
                self._high_resolution_market_states.pop(code, None)
                self._nxt_display_live_price_states.pop(code, None)
                self._raw_tick_received_count_by_stock.pop(code, None)
                self._raw_tick_processed_count_by_stock.pop(code, None)
                self._raw_tick_uncertain_stock_codes.discard(code)
                self._last_known_market_information_states.pop(code, None)
            self._monitoring_target_stock_codes = targets

            snapshot_result: dict[str, object] | None = None
            new_snapshot_codes = tuple(
                code
                for code in targets
                if code not in self._initial_snapshot_requested_stock_codes
                and self.initial_market_snapshot_state(code) is None
                and self._initial_snapshot_request_attempts_by_stock.get(code, 0)
                < self.MAX_INITIAL_SNAPSHOT_ATTEMPTS_PER_SESSION
            )
            request_snapshot = getattr(
                self.kiwoom_api,
                "request_initial_market_snapshot",
                None,
            )
            if requested_identity[1] and new_snapshot_codes and callable(request_snapshot):
                self._initial_snapshot_requested_stock_codes.update(new_snapshot_codes)
                for code in new_snapshot_codes:
                    self._initial_snapshot_request_attempts_by_stock[code] = (
                        self._initial_snapshot_request_attempts_by_stock.get(code, 0) + 1
                    )

                def handle_snapshot_result(
                    result: dict[str, object],
                    *,
                    expected_identity=requested_identity,
                    fallback_codes=new_snapshot_codes,
                ) -> None:
                    self._on_initial_market_snapshot_result(
                        result,
                        expected_identity=expected_identity,
                        requested_stock_codes=fallback_codes,
                    )

                try:
                    snapshot_result = request_snapshot(
                        new_snapshot_codes,
                        callback=handle_snapshot_result,
                    )
                except Exception:
                    self._initial_snapshot_requested_stock_codes.difference_update(
                        new_snapshot_codes
                    )
                    raise
                if not isinstance(snapshot_result, dict):
                    self._initial_snapshot_requested_stock_codes.difference_update(
                        new_snapshot_codes
                    )
                elif snapshot_result.get("ok") is not True:
                    batches = snapshot_result.get("batches")
                    failed_codes = {
                        str(code or "").strip()
                        for batch in (batches if isinstance(batches, list) else ())
                        if isinstance(batch, dict) and batch.get("ok") is not True
                        for code in (batch.get("stock_codes") or ())
                        if str(code or "").strip()
                    }
                    if failed_codes:
                        self._initial_snapshot_requested_stock_codes.difference_update(
                            failed_codes
                        )
                    elif not isinstance(batches, list) or not batches:
                        self._initial_snapshot_requested_stock_codes.difference_update(
                            new_snapshot_codes
                        )

            result = sync(targets)
            registration = result.get("snapshot") if isinstance(result, dict) else None
            identity = (
                int(getattr(registration, "connection_epoch", 0) or 0),
                str(getattr(registration, "login_session_id", "") or ""),
            )
            if identity != self._realtime_shadow_session_identity:
                self._clear_session_state()
                self._market_data_authority.ensure_session(*identity)
                self._realtime_shadow_session_identity = identity
                self._monitoring_target_stock_codes = targets
                self._production_monitoring_stock_codes = production_targets
            if isinstance(result, dict):
                projection = dict(result)
                projection["initial_snapshot"] = snapshot_result
                shadow_sync = getattr(
                    self.kiwoom_api,
                    "sync_realtime_shadow_targets",
                    None,
                )
                observation_targets = tuple(
                    sorted(
                        set(self._production_monitoring_stock_codes)
                        | set(self._execution_shadow_stock_codes)
                        | set(self._candle_observation_stock_codes)
                        | set(getattr(self, "_candle_standby_stock_codes", ()))
                    )
                )
                if callable(shadow_sync) and result.get("ok") is True:
                    projection["shadow_sync"] = shadow_sync(observation_targets)
                if result.get("ok") is True:
                    projection["nxt_display_sync"] = self._sync_nxt_display_targets(
                        observation_targets
                    )
                return projection
            return {
                "ok": False,
                "changed": False,
                "active": False,
                "reason_code": "REALTIME_MONITORING_RESULT_MALFORMED",
            }
        except Exception as exc:
            self._observe_exception(exc, "sync_monitoring_targets")
            return {
                "ok": False,
                "changed": False,
                "active": False,
                "reason_code": "REALTIME_MONITORING_SYNC_FAILED",
                "error": str(exc),
            }

    def _on_initial_market_snapshot_result(
        self,
        result: dict[str, object],
        *,
        expected_identity: tuple[int, str] | None = None,
        requested_stock_codes: object = (),
    ) -> None:
        identity = expected_identity or (
            int(result.get("connection_epoch") or 0)
            if isinstance(result, dict)
            else 0,
            str(result.get("login_session_id") or "").strip()
            if isinstance(result, dict)
            else "",
        )
        if identity != self._realtime_shadow_session_identity:
            return
        result_codes = (
            result.get("stock_codes")
            if isinstance(result, dict) and result.get("stock_codes") is not None
            else requested_stock_codes
        )
        if not result_codes and isinstance(result, dict):
            result_rows = result.get("rows")
            if isinstance(result_rows, list):
                result_codes = tuple(
                    row.get("stock_code")
                    for row in result_rows
                    if isinstance(row, dict)
                )
        completed_codes = {
            str(code or "").strip()
            for code in (result_codes or ())
            if str(code or "").strip()
        }
        self._initial_snapshot_requested_stock_codes.difference_update(completed_codes)
        if not isinstance(result, dict) or result.get("ok") is not True:
            return
        received_at = str(result.get("snapshot_received_at") or "").strip()
        received_datetime = parse_market_datetime(received_at)
        current = datetime.now(SEOUL_TIMEZONE)
        if (
            received_datetime is None
            or received_datetime.date() != current.date()
        ):
            return
        rows = result.get("rows") if isinstance(result.get("rows"), list) else ()
        for row in rows:
            if not isinstance(row, dict):
                continue
            stock_code = str(row.get("stock_code") or "").strip()
            if stock_code not in self._monitoring_target_stock_codes:
                continue
            if completed_codes and stock_code not in completed_codes:
                continue
            try:
                current_price = float(row.get("current_price"))
            except (TypeError, ValueError):
                continue
            if (
                isinstance(row.get("current_price"), bool)
                or not isfinite(current_price)
                or current_price <= 0
            ):
                continue
            self._initial_market_snapshot_states[stock_code] = InitialMarketSnapshotState(
                stock_code=stock_code,
                connection_epoch=identity[0],
                login_session_id=identity[1],
                current_price=row.get("current_price"),
                open_price=row.get("open_price"),
                high_price=row.get("high_price"),
                low_price=row.get("low_price"),
                change_rate=row.get("change_rate"),
                previous_day_volume_rate=row.get("previous_day_volume_rate"),
                execution_strength=row.get("execution_strength"),
                cumulative_volume=row.get("cumulative_volume"),
                received_at=received_at,
            )
            self._remember_current_session_market_information(stock_code)
            self.market_data_observed.emit(
                {
                    "stock_code": stock_code,
                    "source": "INITIAL_SNAPSHOT",
                    "connection_epoch": identity[0],
                    "login_session_id": identity[1],
                }
            )

    def prepare_operation_cycle(self, snapshot, minute_key: str) -> dict[str, object]:
        """Promote eligible stocks only at the supplied operation-cycle boundary."""
        registration_getter = getattr(
            self.kiwoom_api,
            "realtime_shadow_registration_snapshot",
            None,
        )
        registration = registration_getter() if callable(registration_getter) else None
        identity = (
            int(getattr(registration, "connection_epoch", 0) or 0),
            str(getattr(registration, "login_session_id", "") or ""),
        )
        if self._market_data_authority.ensure_session(*identity):
            self._clear_session_state()
            self._realtime_shadow_session_identity = identity
        execution_codes = tuple(getattr(snapshot, "execution_stock_codes", ()))
        candle_codes = tuple(
            sorted(
                set(execution_codes)
                | set(self._candle_observation_stock_codes)
                | set(getattr(self, "_candle_standby_stock_codes", ()))
            )
        )
        self._market_data_authority.sync_targets(candle_codes)
        refresh_inflight = bool(getattr(self, "_automatic_candle_refresh_inflight", False))
        promoted: list[str] = []
        for code in candle_codes:
            unresolved = any(key[0] == code for key in self._pending_shadow_comparisons)
            state = self._market_data_authority.snapshot(code)
            readiness_valid = bool(
                getattr(registration, "active", False)
                and code in self._registration_shadow_targets(registration)
                and identity == self._realtime_shadow_session_identity
                and (code, state.reconciliation_minute) not in self._pending_reconciliations
            )
            before = state.mode
            after = self._market_data_authority.promote_at_cycle_boundary(
                code,
                readiness_valid=readiness_valid,
                unresolved_pending=unresolved,
                refresh_inflight=refresh_inflight,
            )
            if before == REALTIME_ELIGIBLE and after.mode == REALTIME_PRIMARY:
                promoted.append(code)
        return {
            "minute_key": str(minute_key or ""),
            "promoted_stock_codes": tuple(promoted),
            "promoted_count": len(promoted),
        }

    def refresh_operation_candles(
        self,
        minute_key: str,
        *,
        on_complete=None,
        drain_all: bool = False,
    ) -> dict[str, Any]:
        from auto_candle_refresh import refresh_operation_candles

        return refresh_operation_candles(
            self,
            minute_key,
            on_complete=on_complete,
            drain_all=drain_all,
        )

    def market_data_refresh_decision(
        self,
        stock_code: str,
        minute_key: str,
    ) -> dict[str, object]:
        code = str(stock_code or "").strip()
        mode = self._market_data_authority.mode(code)
        if mode == TR_RECONCILING:
            reconciliation_minute = self._market_data_authority.snapshot(code).reconciliation_minute
            key = (code, reconciliation_minute)
            request_required = bool(reconciliation_minute and key not in self._pending_reconciliations)
            if request_required:
                self._pending_reconciliations.add(key)
            return {
                "decision": REALTIME_RECONCILIATION,
                "request_kind": REALTIME_RECONCILIATION_REQUEST,
                "reconciliation_minute": reconciliation_minute,
                "request_required": request_required,
            }
        if mode != REALTIME_PRIMARY:
            return {"decision": TR_PRIMARY_REFRESH, "request_kind": NORMAL_TR_REFRESH}
        expected = self._market_data_authority.expected_completed_minute(minute_key)
        if not expected:
            return {"decision": TR_PRIMARY_REFRESH, "request_kind": NORMAL_TR_REFRESH}
        if (
            self._market_data_authority.authority(code, expected) == REALTIME_AUTHORITY
            and self._market_data_authority.realtime_committed(code, expected)
        ):
            return {
                "decision": REALTIME_PRIMARY_SKIP,
                "request_kind": "",
                "reconciliation_minute": expected,
            }
        self._market_data_authority.begin_reconciliation(
            code,
            expected,
            "EXPECTED_REALTIME_BAR_MISSING",
        )
        key = (code, expected)
        request_required = key not in self._pending_reconciliations
        if request_required:
            self._pending_reconciliations.add(key)
        return {
            "decision": REALTIME_RECONCILIATION,
            "request_kind": REALTIME_RECONCILIATION_REQUEST,
            "reconciliation_minute": expected,
            "request_required": request_required,
        }

    def register_operation_candle_request(
        self,
        rqname: str,
        *,
        stock_code: str,
        stock_name: str,
        stock_dir: Path,
        operation_cycle_minute_key: str,
        request_kind: str = NORMAL_TR_REFRESH,
        reconciliation_minute: str = "",
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
            "request_kind": str(request_kind or NORMAL_TR_REFRESH),
            "reconciliation_minute": str(reconciliation_minute or ""),
        }
        return True

    def complete_operation_candle_request(self, rqname: str) -> bool:
        return self._operation_candle_requests.pop(str(rqname or "").strip(), None) is not None

    def complete_reconciliation(
        self,
        stock_code: str,
        minute_key: str,
        stock_dir: Path,
        result: object,
    ) -> bool:
        key = (str(stock_code or "").strip(), str(minute_key or "").strip())
        self._pending_reconciliations.discard(key)
        repaired = bool(
            isinstance(result, dict)
            and result.get("ok") is True
            and any(
                (parsed := candle_market_datetime(candle)) is not None
                and parsed.strftime("%Y-%m-%d %H:%M") == key[1]
                for candle in load_candles(Path(stock_dir))
            )
        )
        self._market_data_authority.finish_reconciliation(key[0], key[1], repaired=repaired)
        return repaired

    def mode_snapshot(self, stock_code: str):
        return self._market_data_authority.snapshot(stock_code)

    def high_resolution_market_state(
        self,
        stock_code: str,
    ) -> HighResolutionMarketState | None:
        code = str(stock_code or "").strip()
        state = self._high_resolution_market_states.get(code)
        if state is None:
            return None
        if (
            state.connection_epoch,
            state.login_session_id,
        ) != self._realtime_shadow_session_identity:
            return None
        return replace(
            state,
            received_tick_count=self._raw_tick_received_count_by_stock.get(code, 0),
            processed_tick_count=self._raw_tick_processed_count_by_stock.get(code, 0),
            data_quality=(
                HIGH_RESOLUTION_DATA_UNCERTAIN
                if code in self._raw_tick_uncertain_stock_codes
                else state.data_quality
            ),
        )

    def initial_market_snapshot_state(
        self,
        stock_code: str,
    ) -> InitialMarketSnapshotState | None:
        state = self._initial_market_snapshot_states.get(str(stock_code or "").strip())
        if state is None:
            return None
        if (
            state.connection_epoch,
            state.login_session_id,
        ) != self._realtime_shadow_session_identity:
            return None
        return state

    def _current_session_market_information_state(
        self,
        stock_code: str,
    ) -> MonitoringMarketInformationState | None:
        code = str(stock_code or "").strip()
        snapshot = self.initial_market_snapshot_state(code)
        realtime = self.high_resolution_market_state(code)
        if snapshot is None and realtime is None:
            return None

        def select(field: str, realtime_field: str | None = None):
            realtime_value = (
                getattr(realtime, realtime_field or field, None)
                if realtime is not None
                else None
            )
            if realtime_value is not None:
                return realtime_value, "REALTIME"
            snapshot_value = getattr(snapshot, field, None) if snapshot is not None else None
            if snapshot_value is not None:
                return snapshot_value, "SNAPSHOT"
            return None, "UNAVAILABLE"

        values: dict[str, object] = {}
        sources: list[tuple[str, str]] = []
        for field, realtime_field in (
            ("last_price", "last_price"),
            ("open_price", None),
            ("high_price", None),
            ("low_price", None),
            ("change_rate", None),
            ("previous_day_volume_rate", None),
            ("execution_strength", None),
        ):
            snapshot_field = "current_price" if field == "last_price" else field
            value, source = select(snapshot_field, realtime_field)
            values[field] = value
            sources.append((field, source))
        identity = self._realtime_shadow_session_identity
        return MonitoringMarketInformationState(
            stock_code=code,
            connection_epoch=identity[0],
            login_session_id=identity[1],
            last_price=values["last_price"],
            open_price=values["open_price"],
            high_price=values["high_price"],
            low_price=values["low_price"],
            change_rate=values["change_rate"],
            previous_day_volume_rate=values["previous_day_volume_rate"],
            execution_strength=values["execution_strength"],
            snapshot_received_at=(snapshot.received_at if snapshot is not None else ""),
            realtime_received_at=(
                realtime.last_received_at if realtime is not None else ""
            ),
            field_sources=tuple(sources),
        )

    def _remember_current_session_market_information(self, stock_code: str) -> None:
        state = self._current_session_market_information_state(stock_code)
        if state is not None:
            self._last_known_market_information_states[state.stock_code] = state

    def fresh_monitoring_market_information_state(
        self,
        stock_code: str,
        *,
        now_dt: datetime | None = None,
    ) -> MonitoringMarketInformationState | None:
        """Return today's connected-session realtime quote for price actions."""

        session_getter = getattr(self.kiwoom_api, "broker_session_snapshot", None)
        if not callable(session_getter):
            return None
        try:
            broker_session = session_getter()
            broker_identity = (
                int(getattr(broker_session, "connection_epoch", 0) or 0),
                str(getattr(broker_session, "login_session_id", "") or "").strip(),
            )
        except Exception:
            return None
        if (
            getattr(broker_session, "connected", False) is not True
            or not broker_identity[1]
            or broker_identity != self._realtime_shadow_session_identity
        ):
            return None

        realtime = self.high_resolution_market_state(stock_code)
        if realtime is None or (
            realtime.connection_epoch,
            realtime.login_session_id,
        ) != broker_identity:
            return None
        try:
            price = float(realtime.last_price)
        except (TypeError, ValueError):
            return None
        if isinstance(realtime.last_price, bool) or not isfinite(price) or price <= 0:
            return None

        market_datetime = parse_market_datetime(realtime.last_market_datetime)
        if market_datetime is None:
            return None
        current = now_dt or datetime.now(SEOUL_TIMEZONE)
        if current.tzinfo is None:
            current = current.replace(tzinfo=SEOUL_TIMEZONE)
        else:
            current = current.astimezone(SEOUL_TIMEZONE)
        if market_datetime.date() != current.date():
            return None

        state = self._current_session_market_information_state(stock_code)
        if state is None or dict(state.field_sources).get("last_price") != "REALTIME":
            return None
        return state

    def configuration_market_information_state(
        self,
        stock_code: str,
        *,
        now_dt: datetime | None = None,
    ) -> MonitoringMarketInformationState | None:
        """Return current-session price evidence for Configuration calculations.

        This deliberately excludes process last-known and persisted prices.  A
        valid same-day realtime Tick wins; otherwise a same-day Initial Snapshot
        from the currently connected Broker session is allowed.
        """

        code = str(stock_code or "").strip()
        if not code:
            return None
        session_getter = getattr(self.kiwoom_api, "broker_session_snapshot", None)
        if not callable(session_getter):
            return None
        try:
            broker_session = session_getter()
            broker_identity = (
                int(getattr(broker_session, "connection_epoch", 0) or 0),
                str(getattr(broker_session, "login_session_id", "") or "").strip(),
            )
        except Exception:
            return None
        if (
            getattr(broker_session, "connected", False) is not True
            or not broker_identity[1]
            or broker_identity != self._realtime_shadow_session_identity
        ):
            return None

        current = now_dt or datetime.now(SEOUL_TIMEZONE)
        if current.tzinfo is None:
            current = current.replace(tzinfo=SEOUL_TIMEZONE)
        else:
            current = current.astimezone(SEOUL_TIMEZONE)

        realtime = self.high_resolution_market_state(code)
        if (
            realtime is not None
            and str(realtime.stock_code or "").strip() == code
            and (
                realtime.connection_epoch,
                realtime.login_session_id,
            )
            == broker_identity
        ):
            try:
                realtime_price = float(realtime.last_price)
            except (TypeError, ValueError):
                realtime_price = 0.0
            market_datetime = parse_market_datetime(realtime.last_market_datetime)
            if (
                not isinstance(realtime.last_price, bool)
                and isfinite(realtime_price)
                and realtime_price > 0
                and market_datetime is not None
                and market_datetime.date() == current.date()
            ):
                state = self._current_session_market_information_state(code)
                if (
                    state is not None
                    and dict(state.field_sources).get("last_price") == "REALTIME"
                ):
                    return state

        snapshot = self.initial_market_snapshot_state(code)
        if (
            snapshot is None
            or str(snapshot.stock_code or "").strip() != code
            or (
                snapshot.connection_epoch,
                snapshot.login_session_id,
            ) != broker_identity
        ):
            return None
        if str(snapshot.source or "").strip().upper() != "SNAPSHOT":
            return None
        snapshot_datetime = parse_market_datetime(snapshot.received_at)
        if snapshot_datetime is None or snapshot_datetime.date() != current.date():
            return None
        try:
            snapshot_price = float(snapshot.current_price)
        except (TypeError, ValueError):
            return None
        if (
            isinstance(snapshot.current_price, bool)
            or not isfinite(snapshot_price)
            or snapshot_price <= 0
        ):
            return None

        values = {
            "last_price": snapshot.current_price,
            "open_price": snapshot.open_price,
            "high_price": snapshot.high_price,
            "low_price": snapshot.low_price,
            "change_rate": snapshot.change_rate,
            "previous_day_volume_rate": snapshot.previous_day_volume_rate,
            "execution_strength": snapshot.execution_strength,
        }
        return MonitoringMarketInformationState(
            stock_code=code,
            connection_epoch=broker_identity[0],
            login_session_id=broker_identity[1],
            last_price=values["last_price"],
            open_price=values["open_price"],
            high_price=values["high_price"],
            low_price=values["low_price"],
            change_rate=values["change_rate"],
            previous_day_volume_rate=values["previous_day_volume_rate"],
            execution_strength=values["execution_strength"],
            snapshot_received_at=snapshot.received_at,
            realtime_received_at="",
            field_sources=tuple(
                (field, "SNAPSHOT" if value is not None else "UNAVAILABLE")
                for field, value in values.items()
            ),
        )

    def monitoring_market_information_state(
        self,
        stock_code: str,
    ) -> MonitoringMarketInformationState | None:
        code = str(stock_code or "").strip()
        current = self._current_session_market_information_state(code)
        if current is not None:
            self._last_known_market_information_states[code] = current
            return current
        return self._last_known_market_information_states.get(code)

    def price_signal_observation_enabled(self) -> bool:
        return self._price_signal_observation_enabled

    def set_price_signal_observation_enabled(self, enabled: object) -> bool:
        requested = bool(enabled)
        if requested != self._price_signal_observation_enabled:
            self._price_signal_observation_enabled = requested
            self._price_signal_observation_generation += 1
        return self._price_signal_observation_enabled

    def high_resolution_market_data_snapshot(
        self,
    ) -> HighResolutionMarketDataSnapshot:
        epoch, session_id = self._realtime_shadow_session_identity
        registration_getter = getattr(
            self.kiwoom_api,
            "realtime_shadow_registration_snapshot",
            None,
        )
        broker_session_getter = getattr(
            self.kiwoom_api,
            "broker_session_snapshot",
            None,
        )
        try:
            registration = (
                registration_getter() if callable(registration_getter) else None
            )
        except Exception:
            registration = None
        try:
            broker_session = (
                broker_session_getter() if callable(broker_session_getter) else None
            )
        except Exception:
            broker_session = None
        return HighResolutionMarketDataSnapshot(
            connection_epoch=epoch,
            login_session_id=session_id,
            broker_connected=bool(getattr(broker_session, "connected", False)),
            realtime_registration_active=bool(
                getattr(registration, "active", False)
            ),
            realtime_target_stock_count=len(
                tuple(getattr(registration, "target_stock_codes", ()) or ())
            ),
            received_tick_count=self._raw_tick_received_count,
            processed_tick_count=self._raw_tick_processed_count,
            current_queue_depth=len(self._raw_tick_queue),
            queue_high_watermark=self._raw_tick_queue_high_watermark,
            overflow_count=self._raw_tick_overflow_count,
            last_receive_sequence=self._raw_tick_last_receive_sequence,
            last_processed_sequence=self._raw_tick_last_processed_sequence,
            last_tick_received_at=self._raw_tick_last_received_at,
            last_tick_processed_at=self._raw_tick_last_processed_at,
            last_processing_latency_ms=self._raw_tick_last_processing_latency_ms,
            max_processing_latency_ms=self._raw_tick_max_processing_latency_ms,
            data_quality=(
                HIGH_RESOLUTION_DATA_UNCERTAIN
                if self._raw_tick_overflow_count
                else HIGH_RESOLUTION_DATA_NORMAL
            ),
            realtime_shadow_target_stock_count=len(
                self._registration_shadow_targets(registration)
            ),
        )

    def nxt_display_live_price_state(
        self,
        stock_code: str,
    ) -> NxtDisplayLivePriceState | None:
        code = str(stock_code or "").strip()
        state = self._nxt_display_live_price_states.get(code)
        if state is None:
            return None
        if (
            state.connection_epoch,
            state.login_session_id,
        ) != self._realtime_shadow_session_identity:
            return None
        if code not in self._display_observation_target_stock_codes():
            return None
        return state

    def _display_observation_target_stock_codes(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                set(self._production_monitoring_stock_codes)
                | set(self._execution_shadow_stock_codes)
                | set(self._candle_observation_stock_codes)
                | set(getattr(self, "_candle_standby_stock_codes", ()))
            )
        )

    def _observe_realtime_boundary(
        self,
        stage: str,
        stock_code: object,
        *,
        reason: str = "",
        **details: object,
    ) -> None:
        """Keep bounded process-local host evidence and log only transitions."""

        code = str(stock_code or "").strip()
        stage_text = str(stage or "").strip()
        if not stage_text:
            return
        counts = self._realtime_boundary_diagnostic_counts.setdefault(code, {})
        counts[stage_text] = counts.get(stage_text, 0) + 1
        key = (code, stage_text)
        evidence = {
            "stage": stage_text,
            "stock_code": code,
            "reason": str(reason or "").strip(),
            **details,
            "count": counts[stage_text],
        }
        self._realtime_boundary_diagnostic_last[key] = evidence
        transition = evidence["reason"] or stage_text
        if self._realtime_boundary_diagnostic_transition.get(key) == transition:
            return
        self._realtime_boundary_diagnostic_transition[key] = transition
        scoped_stock = str(
            os.environ.get("KIWOOM_DIAGNOSTIC_SCOPE_STOCK", "") or ""
        ).strip()
        if scoped_stock and scoped_stock != code:
            return

    def realtime_boundary_diagnostic_snapshot(self, stock_code: object) -> dict[str, object]:
        """Return read-only process-local queue/state diagnostics for one stock."""

        code = str(stock_code or "").strip()
        return {
            "stock_code": code,
            "counts": dict(self._realtime_boundary_diagnostic_counts.get(code, {})),
            "last": tuple(
                dict(evidence)
                for key, evidence in self._realtime_boundary_diagnostic_last.items()
                if key[0] == code
            ),
        }

    def clear(self) -> dict[str, object]:
        clear = getattr(self.kiwoom_api, "clear_realtime_shadow_registration", None)
        clear_nxt = getattr(self.kiwoom_api, "clear_nxt_display_registration", None)
        self._clear_all_state()
        nxt_result = None
        if callable(clear_nxt):
            try:
                nxt_result = clear_nxt(reason="MARKET_DATA_HOST_CLEARED")
            except Exception as exc:
                self._observe_exception(exc, "clear_nxt_display_registration")
        if not callable(clear):
            return {
                "ok": True,
                "changed": False,
                "active": False,
                "reason_code": "REALTIME_SHADOW_API_UNAVAILABLE",
            }
        try:
            result = clear(reason="MARKET_DATA_HOST_CLEARED")
            if isinstance(result, dict):
                projection = dict(result)
                projection["nxt_display"] = nxt_result
                return projection
            return result
        except Exception as exc:
            self._observe_exception(exc, "clear_registration")
            return {
                "ok": False,
                "changed": False,
                "active": False,
                "reason_code": "REALTIME_SHADOW_CLEAR_FAILED",
                "error": str(exc),
            }

    def shutdown(self) -> dict[str, object]:
        self._shutting_down = True
        return self.clear()

    def _clear_session_state(self) -> None:
        cleared_stock_codes = tuple(
            dict.fromkeys(
                (*self._initial_market_snapshot_states, *self._high_resolution_market_states)
            )
        )
        for stock_code in cleared_stock_codes:
            self._remember_current_session_market_information(stock_code)
        self._operation_candle_requests.clear()
        self._automatic_candle_refresh_generation = int(
            getattr(self, "_automatic_candle_refresh_generation", 0) or 0
        ) + 1
        self._automatic_candle_refresh_inflight = False
        self._automatic_candle_refresh_waiters = []
        self._automatic_candle_refresh_rerun_requested = False
        self._automatic_candle_refresh_rerun_minute_key = ""
        self._candle_bootstrap_completed = {}
        self._candle_refresh_round_robin_offset = 0
        self._canonical_event_queue.clear()
        self._last_ready_commit_identity_by_stock.clear()
        self._raw_tick_queue.clear()
        self._nxt_display_tick_queue.clear()
        self._high_resolution_market_states.clear()
        self._nxt_display_live_price_states.clear()
        self._initial_market_snapshot_states.clear()
        self._monitoring_target_stock_codes = ()
        self._production_monitoring_stock_codes = ()
        self._last_candle_observation_refresh_minute = ""
        self._initial_snapshot_requested_stock_codes.clear()
        self._initial_snapshot_request_attempts_by_stock.clear()
        self._raw_tick_received_count_by_stock.clear()
        self._raw_tick_processed_count_by_stock.clear()
        self._raw_tick_uncertain_stock_codes.clear()
        self._pending_shadow_comparisons.clear()
        self._realtime_shadow_trigger_queue.clear()
        self._realtime_shadow_retry_codes.clear()
        self._pending_reconciliations.clear()
        self._reconciliation_shadow_bars.clear()
        for stock_code in cleared_stock_codes:
            self.market_data_observed.emit(
                {
                    "stock_code": stock_code,
                    "source": "SESSION_CLEARED",
                }
            )

    def _clear_all_state(self) -> None:
        self._clear_session_state()
        self._last_known_market_information_states.clear()
        self._raw_tick_received_count = 0
        self._raw_tick_processed_count = 0
        self._raw_tick_queue_high_watermark = 0
        self._raw_tick_overflow_count = 0
        self._raw_tick_last_receive_sequence = 0
        self._raw_tick_last_processed_sequence = 0
        self._raw_tick_last_received_at = ""
        self._raw_tick_last_processed_at = ""
        self._raw_tick_last_processing_latency_ms = 0.0
        self._raw_tick_max_processing_latency_ms = 0.0
        self._market_data_authority.reset()
        self._realtime_shadow_session_identity = (0, "")

    def _raw_realtime_tick_minimally_valid(self, payload: dict[str, object]) -> bool:
        required_text = (
            "stock_code",
            "execution_time_raw",
            "received_at",
            "market_datetime",
            "login_session_id",
        )
        if any(not str(payload.get(field) or "").strip() for field in required_text):
            return False
        if payload.get("current_price") in (None, ""):
            return False
        broker_identity = str(payload.get("broker_code_identity") or "").strip()
        if broker_identity:
            stock_code = str(payload.get("stock_code") or "").strip()
            if canonical_stock_code_from_market_data_identity(broker_identity) != stock_code:
                return False
            if str(payload.get("market_source") or "").strip() != market_source_for_identity(
                broker_identity
            ):
                return False
            registration_getter = getattr(
                self.kiwoom_api,
                "realtime_shadow_registration_snapshot",
                None,
            )
            try:
                registration = (
                    registration_getter()
                    if callable(registration_getter)
                    else None
                )
            except Exception:
                registration = None
            registered_identities = (
                tuple(
                    str(identity or "").strip()
                    for identity in tuple(
                        getattr(registration, "target_stock_codes", ()) or ()
                    )
                )
                if bool(getattr(registration, "active", False))
                else ()
            )
            identity_authorities = tuple(
                identity
                for identity in registered_identities
                if canonical_stock_code_from_market_data_identity(identity)
                == stock_code
            )
            if identity_authorities and broker_identity not in identity_authorities:
                return False
        try:
            sequence = int(payload.get("receive_sequence"))
            epoch = int(payload.get("connection_epoch"))
            received_monotonic = float(payload.get("received_monotonic"))
        except (TypeError, ValueError):
            return False
        return sequence > 0 and epoch >= 0 and received_monotonic >= 0

    def _on_realtime_shadow_tick_received(self, payload: object) -> None:
        """Queue one normalized raw tick outside the broker callback stack."""
        if not isinstance(payload, dict):
            return
        stock_code = str(payload.get("stock_code") or "").strip()
        if self._shutting_down:
            self._observe_realtime_boundary(
                "MARKET_HOST_RAW_REJECTED",
                stock_code,
                reason="HOST_SHUTTING_DOWN",
            )
            return
        if not self._raw_realtime_tick_minimally_valid(payload):
            self._observe_realtime_boundary(
                "MARKET_HOST_RAW_REJECTED",
                stock_code,
                reason="MINIMAL_PAYLOAD_INVALID",
            )
            return

        event = dict(payload)
        event[_PRICE_SIGNAL_OBSERVATION_GENERATION_KEY] = (
            self._price_signal_observation_generation
            if self._price_signal_observation_enabled
            else -1
        )
        stock_code = str(event["stock_code"]).strip()
        sequence = int(event["receive_sequence"])
        identity = (
            int(event["connection_epoch"]),
            str(event["login_session_id"]).strip(),
        )
        self._raw_tick_received_count += 1
        self._raw_tick_last_receive_sequence = sequence
        self._raw_tick_last_received_at = str(event["received_at"])
        if identity == self._realtime_shadow_session_identity:
            self._raw_tick_received_count_by_stock[stock_code] = (
                self._raw_tick_received_count_by_stock.get(stock_code, 0) + 1
            )
        self._observe_realtime_boundary(
            "MARKET_HOST_RAW_RECEIVED",
            stock_code,
            receive_sequence=sequence,
            received_at=str(event.get("received_at") or ""),
            market_datetime=str(event.get("market_datetime") or ""),
            current_price=event.get("current_price"),
            connection_epoch=identity[0],
            login_session_id=identity[1],
        )

        if len(self._raw_tick_queue) >= self.MAX_RAW_TICK_QUEUE_DEPTH:
            # Preserve the queued FIFO sequence and reject the newest event explicitly.
            self._raw_tick_overflow_count += 1
            if identity == self._realtime_shadow_session_identity:
                self._raw_tick_uncertain_stock_codes.add(stock_code)
            self._observe_realtime_boundary(
                "MARKET_HOST_RAW_REJECTED",
                stock_code,
                reason="RAW_QUEUE_OVERFLOW",
                receive_sequence=sequence,
            )
            self._schedule_raw_realtime_tick_drain()
            return

        self._raw_tick_queue.append(event)
        self._raw_tick_queue_high_watermark = max(
            self._raw_tick_queue_high_watermark,
            len(self._raw_tick_queue),
        )
        self._schedule_raw_realtime_tick_drain()

    def _on_nxt_display_tick_received(self, payload: object) -> None:
        """Queue a normalized ECN execution without touching KRX consumers."""

        if self._shutting_down or not isinstance(payload, dict):
            return
        event = dict(payload)
        code = str(event.get("canonical_stock_code") or "").strip()
        broker_identity = str(event.get("broker_code_identity") or "").strip()
        if (
            not code
            or broker_identity != f"{code}_NX"
            or event.get("market_source") != "NXT"
            or event.get("source_real_type") != "ECN주식체결"
        ):
            self._observe_realtime_boundary(
                "MARKET_HOST_RAW_REJECTED",
                code or broker_identity,
                reason="NXT_DISPLAY_IDENTITY_INVALID",
                market_source="NXT",
                broker_code_identity=broker_identity,
            )
            return
        try:
            sequence = int(event.get("receive_sequence"))
            epoch = int(event.get("connection_epoch"))
            price = float(event.get("current_price"))
        except (TypeError, ValueError):
            return
        if sequence <= 0 or epoch < 0 or not isfinite(price) or price <= 0:
            return
        self._observe_realtime_boundary(
            "MARKET_HOST_RAW_RECEIVED",
            code,
            market_source="NXT",
            broker_code_identity=broker_identity,
            receive_sequence=sequence,
            market_datetime=str(event.get("market_datetime") or ""),
            current_price=event.get("current_price"),
            connection_epoch=epoch,
            login_session_id=str(event.get("login_session_id") or ""),
        )
        if len(self._nxt_display_tick_queue) >= self.MAX_RAW_TICK_QUEUE_DEPTH:
            self._observe_realtime_boundary(
                "MARKET_HOST_RAW_REJECTED",
                code,
                reason="NXT_DISPLAY_QUEUE_OVERFLOW",
                market_source="NXT",
                broker_code_identity=broker_identity,
                receive_sequence=sequence,
            )
            return
        self._nxt_display_tick_queue.append(event)
        if not self._nxt_display_tick_drain_scheduled:
            self._nxt_display_tick_drain_scheduled = True
            QTimer.singleShot(0, self._drain_nxt_display_ticks)

    def _drain_nxt_display_ticks(self) -> None:
        self._nxt_display_tick_drain_scheduled = False
        if self._shutting_down or self._nxt_display_tick_drain_running:
            return
        self._nxt_display_tick_drain_running = True
        try:
            while self._nxt_display_tick_queue and not self._shutting_down:
                self._process_nxt_display_tick(self._nxt_display_tick_queue.popleft())
        finally:
            self._nxt_display_tick_drain_running = False
            if self._nxt_display_tick_queue and not self._nxt_display_tick_drain_scheduled:
                self._nxt_display_tick_drain_scheduled = True
                QTimer.singleShot(0, self._drain_nxt_display_ticks)

    def _process_nxt_display_tick(self, payload: dict[str, object]) -> bool:
        code = str(payload.get("canonical_stock_code") or "").strip()
        broker_identity = str(payload.get("broker_code_identity") or "").strip()
        if (
            not code
            or broker_identity != f"{code}_NX"
            or payload.get("market_source") != "NXT"
            or payload.get("source_real_type") != "ECN주식체결"
            or parse_market_datetime(payload.get("market_datetime")) is None
        ):
            return False
        identity = (
            int(payload.get("connection_epoch") or 0),
            str(payload.get("login_session_id") or "").strip(),
        )
        if identity != self._realtime_shadow_session_identity:
            self._observe_realtime_boundary(
                "NORMALIZATION_REJECTED",
                code,
                reason="NXT_SESSION_IDENTITY_MISMATCH",
                market_source="NXT",
                broker_code_identity=broker_identity,
            )
            return False
        if code not in self._display_observation_target_stock_codes():
            return False
        try:
            price = float(payload.get("current_price"))
        except (TypeError, ValueError):
            return False
        if not isfinite(price) or price <= 0:
            return False
        sequence = int(payload.get("receive_sequence") or 0)
        current = self._nxt_display_live_price_states.get(code)
        if current is not None and sequence <= current.receive_sequence:
            self._observe_realtime_boundary(
                "NORMALIZATION_REJECTED",
                code,
                reason="NXT_STALE_OR_OUT_OF_ORDER",
                market_source="NXT",
                broker_code_identity=broker_identity,
                receive_sequence=sequence,
                last_receive_sequence=current.receive_sequence,
            )
            return False
        self._observe_realtime_boundary(
            "NORMALIZATION_ACCEPTED",
            code,
            market_source="NXT",
            broker_code_identity=broker_identity,
            receive_sequence=sequence,
            market_datetime=str(payload.get("market_datetime") or ""),
            current_price=payload.get("current_price"),
        )
        self._nxt_display_live_price_states[code] = NxtDisplayLivePriceState(
            canonical_stock_code=code,
            broker_code_identity=broker_identity,
            market_source="NXT",
            source_real_type=str(payload.get("source_real_type") or ""),
            connection_epoch=identity[0],
            login_session_id=identity[1],
            last_execution_time_raw=str(payload.get("execution_time_raw") or ""),
            last_market_datetime=str(payload.get("market_datetime") or ""),
            last_price=payload["current_price"],
            execution_quantity=payload.get("execution_quantity"),
            cumulative_volume=payload.get("cumulative_volume"),
            receive_sequence=sequence,
            data_quality=HIGH_RESOLUTION_DATA_NORMAL,
            updated_at=str(payload.get("received_at") or ""),
        )
        self._observe_realtime_boundary(
            "NXT_DISPLAY_STATE_UPDATED",
            code,
            market_source="NXT",
            broker_code_identity=broker_identity,
            receive_sequence=sequence,
            current_price=payload.get("current_price"),
        )
        return True

    def _schedule_raw_realtime_tick_drain(self) -> None:
        if self._raw_tick_drain_scheduled or self._raw_tick_drain_running:
            return
        self._raw_tick_drain_scheduled = True
        QTimer.singleShot(0, self._drain_raw_realtime_ticks)

    def _drain_raw_realtime_ticks(self) -> None:
        self._raw_tick_drain_scheduled = False
        if self._shutting_down or self._raw_tick_drain_running:
            return
        self._raw_tick_drain_running = True
        try:
            while self._raw_tick_queue and not self._shutting_down:
                payload = self._raw_tick_queue.popleft()
                try:
                    self._process_raw_realtime_tick(payload)
                except Exception as exc:
                    self._observe_exception(
                        exc,
                        "process_raw_realtime_tick",
                        stock_code=str(payload.get("stock_code") or ""),
                    )
        finally:
            self._raw_tick_drain_running = False
            if self._raw_tick_queue and not self._raw_tick_drain_scheduled:
                self._schedule_raw_realtime_tick_drain()

    def _process_raw_realtime_tick(self, payload: dict[str, object]) -> bool:
        stock_code = str(payload.get("stock_code") or "").strip()
        identity = (
            int(payload.get("connection_epoch") or 0),
            str(payload.get("login_session_id") or "").strip(),
        )
        if identity != self._realtime_shadow_session_identity:
            self._observe_realtime_boundary(
                "NORMALIZATION_REJECTED",
                stock_code,
                reason="SESSION_IDENTITY_MISMATCH",
                receive_sequence=payload.get("receive_sequence"),
                event_identity=identity,
                current_identity=self._realtime_shadow_session_identity,
            )
            return False

        sequence = int(payload.get("receive_sequence") or 0)
        current = self._high_resolution_market_states.get(stock_code)
        if current is not None and sequence <= current.last_receive_sequence:
            self._observe_realtime_boundary(
                "NORMALIZATION_REJECTED",
                stock_code,
                reason="STALE_OR_OUT_OF_ORDER",
                receive_sequence=sequence,
                last_receive_sequence=current.last_receive_sequence,
            )
            return False

        self._observe_realtime_boundary(
            "NORMALIZATION_ACCEPTED",
            stock_code,
            receive_sequence=sequence,
            market_datetime=str(payload.get("market_datetime") or ""),
            current_price=payload.get("current_price"),
            connection_epoch=identity[0],
            login_session_id=identity[1],
        )

        self._raw_tick_processed_count += 1
        self._raw_tick_processed_count_by_stock[stock_code] = (
            self._raw_tick_processed_count_by_stock.get(stock_code, 0) + 1
        )
        processed_at = datetime.now().astimezone().isoformat(timespec="microseconds")
        self._raw_tick_last_processed_sequence = sequence
        self._raw_tick_last_processed_at = processed_at
        self._high_resolution_market_states[stock_code] = HighResolutionMarketState(
            stock_code=stock_code,
            broker_code_identity=str(
                payload.get("broker_code_identity") or stock_code
            ).strip(),
            market_source=str(
                payload.get("market_source")
                or market_source_for_identity(
                    payload.get("broker_code_identity") or stock_code
                )
            ).strip(),
            connection_epoch=identity[0],
            login_session_id=identity[1],
            last_execution_time_raw=str(payload.get("execution_time_raw") or ""),
            last_market_datetime=str(payload.get("market_datetime") or ""),
            last_price=payload["current_price"],
            last_trade_volume_raw=payload.get("trade_volume_raw"),
            last_trade_volume_abs=payload.get("trade_volume_abs"),
            last_cumulative_volume=payload.get("cumulative_volume"),
            last_receive_sequence=sequence,
            last_received_at=str(payload.get("received_at") or ""),
            last_received_monotonic=payload["received_monotonic"],
            received_tick_count=self._raw_tick_received_count_by_stock.get(stock_code, 0),
            processed_tick_count=self._raw_tick_processed_count_by_stock[stock_code],
            data_quality=(
                HIGH_RESOLUTION_DATA_UNCERTAIN
                if stock_code in self._raw_tick_uncertain_stock_codes
                else HIGH_RESOLUTION_DATA_NORMAL
            ),
            open_price=(
                payload.get("open_price")
                if payload.get("open_price") is not None
                else getattr(current, "open_price", None)
            ),
            high_price=(
                payload.get("high_price")
                if payload.get("high_price") is not None
                else getattr(current, "high_price", None)
            ),
            low_price=(
                payload.get("low_price")
                if payload.get("low_price") is not None
                else getattr(current, "low_price", None)
            ),
            change_rate=(
                payload.get("change_rate")
                if payload.get("change_rate") is not None
                else getattr(current, "change_rate", None)
            ),
            previous_day_volume_rate=(
                payload.get("previous_day_volume_rate")
                if payload.get("previous_day_volume_rate") is not None
                else getattr(current, "previous_day_volume_rate", None)
            ),
            execution_strength=(
                payload.get("execution_strength")
                if payload.get("execution_strength") is not None
                else getattr(current, "execution_strength", None)
            ),
        )
        state = self._high_resolution_market_states[stock_code]
        self._observe_realtime_boundary(
            "HIGH_RES_STATE_UPDATED",
            stock_code,
            last_price=state.last_price,
            last_market_datetime=state.last_market_datetime,
            data_quality=state.data_quality,
            connection_epoch=state.connection_epoch,
            login_session_id=state.login_session_id,
            receive_sequence=state.last_receive_sequence,
            updated_at=processed_at,
        )
        self._remember_current_session_market_information(stock_code)
        processing_latency_ms = max(
            0.0,
            (monotonic() - float(payload["received_monotonic"])) * 1000.0,
        )
        self._raw_tick_last_processing_latency_ms = processing_latency_ms
        self._raw_tick_max_processing_latency_ms = max(
            self._raw_tick_max_processing_latency_ms,
            processing_latency_ms,
        )
        self.market_data_observed.emit(
            {
                "stock_code": stock_code,
                "source": "REALTIME",
                "connection_epoch": identity[0],
                "login_session_id": identity[1],
            }
        )
        if (
            self._price_signal_observation_enabled
            and payload.get(_PRICE_SIGNAL_OBSERVATION_GENERATION_KEY)
            == self._price_signal_observation_generation
        ):
            observation = self.high_resolution_market_state(stock_code)
            if observation is not None:
                self.high_resolution_price_observed.emit(observation)
        return True

    def _on_bar_committed(self, payload: object) -> None:
        """Validate minimally and queue outside the broker callback stack."""
        if self._shutting_down or not isinstance(payload, dict):
            return
        if payload.get("event_type") != "BAR_COMMITTED":
            return
        if payload.get("source") not in {"opt10080", "realtime_primary"}:
            return
        timeframe = payload.get("timeframe_minutes")
        if isinstance(timeframe, bool) or timeframe != 1:
            return
        event = dict(payload)
        if event["source"] == "opt10080":
            rqname = str(event.get("rqname") or "").strip()
            context = self._operation_candle_requests.get(rqname)
            # Detach provenance before the terminal callback releases the request.
            event["_operation_request_context"] = (
                dict(context) if rqname and isinstance(context, dict) else None
            )
        self._canonical_event_queue.append(event)
        self._schedule_canonical_drain()

    def _schedule_canonical_drain(self) -> None:
        if self._canonical_drain_scheduled or self._canonical_drain_running:
            return
        self._canonical_drain_scheduled = True
        QTimer.singleShot(0, self._drain_canonical_events)

    def _drain_canonical_events(self) -> None:
        self._canonical_drain_scheduled = False
        if self._shutting_down or self._canonical_drain_running:
            return
        self._canonical_drain_running = True
        try:
            while self._canonical_event_queue and not self._shutting_down:
                event = self._canonical_event_queue.popleft()
                try:
                    ready = self._normalize_canonical_event(event)
                    if ready is not None:
                        self.canonical_bar_ready_for_operation.emit(ready)
                    self._queue_shadow_retry(event)
                except Exception as exc:
                    self._observe_exception(
                        exc,
                        "normalize_canonical_event",
                        stock_code=str(event.get("stock_code") or ""),
                    )
        finally:
            self._canonical_drain_running = False
            if self._canonical_event_queue and not self._canonical_drain_scheduled:
                self._schedule_canonical_drain()

    @staticmethod
    def _common_canonical_event_valid(event: dict[str, object]) -> bool:
        required = (
            "stock_code",
            "commit_identity",
            "canonical_content_hash",
            "canonical_path",
            "bar_key",
            "bar_identity",
            "bar_time",
            "trade_date",
        )
        return all(str(event.get(field) or "").strip() for field in required)

    def _normalize_canonical_event(
        self,
        event: dict[str, object],
    ) -> dict[str, object] | None:
        if not self._common_canonical_event_valid(event):
            return None
        source = str(event.get("source") or "")
        if source == "opt10080":
            rqname = str(event.get("rqname") or "").strip()
            context = event.get("_operation_request_context")
            if not rqname or not isinstance(context, dict):
                return None
            if str(event.get("stock_code") or "") != str(context.get("stock_code") or ""):
                return None
            evaluation_tick_key = str(context.get("operation_cycle_minute_key") or "")
        else:
            context = self._realtime_event_context(event)
            if context is None:
                return None
            rqname = ""
            evaluation_tick_key = str(context["evaluation_tick_key"])

        stock_dir = Path(context["stock_dir"])
        expected_path = (stock_dir / "candles.json").resolve()
        try:
            event_path = Path(str(event.get("canonical_path") or "")).resolve()
        except Exception:
            return None
        if event_path != expected_path:
            return None
        candles = load_candles(stock_dir)
        if (
            not candles
            or canonical_candle_content_hash(candles)
            != str(event.get("canonical_content_hash") or "")
        ):
            return None
        if source == "opt10080" and self._operation_candle_requests.get(rqname) == context:
            self._operation_candle_requests.pop(rqname, None)

        stock_code = str(event.get("stock_code") or "").strip()
        commit_identity = str(event.get("commit_identity") or "").strip()
        if self._last_ready_commit_identity_by_stock.get(stock_code) == commit_identity:
            return None
        self._last_ready_commit_identity_by_stock[stock_code] = commit_identity
        return {
            "stock_code": stock_code,
            "stock_name": str(context.get("stock_name") or event.get("stock_name") or ""),
            "stock_dir": stock_dir,
            "source": source,
            "evaluation_tick_key": evaluation_tick_key,
            "commit_identity": commit_identity,
            "bar_key": str(event.get("bar_key") or ""),
            "bar_identity": str(event.get("bar_identity") or ""),
            "canonical_content_hash": str(event.get("canonical_content_hash") or ""),
            "canonical_path": str(event_path),
            "bar_time": str(event.get("bar_time") or ""),
            "trade_date": str(event.get("trade_date") or ""),
            "request_kind": str(context.get("request_kind") or ""),
            "rqname": rqname,
            "connection_epoch": int(event.get("connection_epoch") or 0),
            "login_session_id": str(event.get("login_session_id") or ""),
        }

    def _realtime_event_context(
        self,
        event: dict[str, object],
    ) -> dict[str, object] | None:
        code = str(event.get("stock_code") or "").strip()
        try:
            bar_time = datetime.fromisoformat(str(event.get("bar_time") or ""))
        except ValueError:
            return None
        minute = bar_time.strftime("%Y-%m-%d %H:%M")
        registration_getter = getattr(
            self.kiwoom_api,
            "realtime_shadow_registration_snapshot",
            None,
        )
        registration = registration_getter() if callable(registration_getter) else None
        if not (
            self._market_data_authority.mode(code) == REALTIME_PRIMARY
            and self._market_data_authority.authority(code, minute) == REALTIME_AUTHORITY
            and getattr(registration, "active", False)
            and code in self._registration_shadow_targets(registration)
            and int(event.get("connection_epoch") or -1)
            == getattr(registration, "connection_epoch", -2)
            and str(event.get("login_session_id") or "")
            == getattr(registration, "login_session_id", "")
        ):
            return None
        stock_dir = StockRepository().resolve_stock_dir(
            code,
            str(event.get("stock_name") or ""),
        )
        return {
            "stock_code": code,
            "stock_name": str(event.get("stock_name") or ""),
            "stock_dir": stock_dir,
            "evaluation_tick_key": (
                bar_time + timedelta(minutes=1)
            ).strftime("%Y-%m-%d %H:%M"),
            "request_kind": "REALTIME_PRIMARY",
        }

    def _queue_shadow_retry(self, event: dict[str, object]) -> None:
        if event.get("source") != "opt10080":
            return
        stock_code = str(event.get("stock_code") or "").strip()
        if not any(key[0] == stock_code for key in self._pending_shadow_comparisons):
            return
        self._realtime_shadow_retry_codes.add(stock_code)
        self._schedule_realtime_shadow_drain()

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

    def _schedule_realtime_shadow_drain(self) -> None:
        if self._realtime_shadow_drain_scheduled or self._realtime_shadow_drain_running:
            return
        self._realtime_shadow_drain_scheduled = True
        QTimer.singleShot(0, self._drain_realtime_shadow_events)

    def _drain_realtime_shadow_events(self) -> None:
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
                    mode = self._market_data_authority.mode(bar.stock_code)
                    if mode == REALTIME_PRIMARY:
                        self._process_realtime_primary_bar(bar)
                    elif mode == TR_RECONCILING:
                        self._remember_reconciliation_shadow_bar(bar)
                    else:
                        self._compare_or_pend_realtime_shadow_bar(bar)
                except Exception as exc:
                    self._observe_exception(
                        exc,
                        "process_realtime_bar",
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
                    self._compare_or_pend_realtime_shadow_bar(bar)
        finally:
            self._realtime_shadow_drain_running = False
            if (
                self._realtime_shadow_trigger_queue
                or self._realtime_shadow_retry_codes
            ) and not self._realtime_shadow_drain_scheduled:
                self._schedule_realtime_shadow_drain()

    def _shadow_bar_matches_current_session(self, bar: RealtimeShadowBar) -> bool:
        snapshot_getter = getattr(
            self.kiwoom_api,
            "realtime_shadow_registration_snapshot",
            None,
        )
        if not callable(snapshot_getter):
            return False
        snapshot = snapshot_getter()
        return bool(
            getattr(snapshot, "active", False)
            and bar.stock_code in self._registration_shadow_targets(snapshot)
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
            self._observe_realtime_shadow_comparison(bar, result)
            self.realtime_shadow_comparison_completed.emit(dict(result))
        return result

    def _observe_realtime_shadow_comparison(
        self,
        bar: RealtimeShadowBar,
        result: dict[str, object],
    ) -> None:
        entry = self._execution_entry_provider(bar.stock_code)
        eligible_context = bool(
            entry is not None
            and getattr(entry, "execution_ready", False) is True
            and self._shadow_bar_matches_current_session(bar)
            and not any(key[0] == bar.stock_code for key in self._pending_shadow_comparisons)
            and (bar.stock_code, bar.minute_key) not in self._pending_reconciliations
            and self._market_data_authority.authority(bar.stock_code, bar.minute_key)
            != TR_RECONCILIATION_AUTHORITY
        )
        self._market_data_authority.observe_comparison(
            bar.stock_code,
            bar.minute_key,
            status=str(result.get("status") or ""),
            price_match=result.get("price_match") is True,
            volume_compared=result.get("volume_compared") is True,
            volume_match=(
                result.get("volume_match")
                if isinstance(result.get("volume_match"), bool)
                else None
            ),
            eligible_context=eligible_context,
        )

    def _remember_reconciliation_shadow_bar(self, bar: RealtimeShadowBar) -> None:
        key = (bar.stock_code, bar.minute_key)
        self._reconciliation_shadow_bars[key] = bar
        self._reconciliation_shadow_bars.move_to_end(key)
        while len(self._reconciliation_shadow_bars) > self.MAX_PENDING_SHADOW_COMPARISONS:
            self._reconciliation_shadow_bars.popitem(last=False)

    def _process_realtime_primary_bar(self, bar: RealtimeShadowBar) -> dict[str, object]:
        code = bar.stock_code
        minute = bar.minute_key
        if self._market_data_authority.authority(code, minute) == TR_RECONCILIATION_AUTHORITY:
            self._remember_reconciliation_shadow_bar(bar)
            return {"ok": False, "reason_code": "TR_RECONCILIATION_OWNS_MINUTE"}
        if self._market_data_authority.realtime_committed(code, minute):
            return {"ok": True, "changed": False, "reason_code": "REALTIME_BAR_ALREADY_COMMITTED"}
        last_commit_minute = self._market_data_authority.snapshot(code).last_realtime_commit_minute
        if last_commit_minute and minute < last_commit_minute:
            return {"ok": False, "reason_code": "STALE_REALTIME_BAR"}
        entry = self._execution_entry_provider(code)
        valid = bool(
            self._market_data_authority.mode(code) == REALTIME_PRIMARY
            and entry is not None
            and getattr(entry, "execution_ready", False) is True
            and self._shadow_bar_matches_current_session(bar)
            and bar.timeframe_minutes == 1
            and bar.volume_complete is True
            and bar.volume is not None
        )
        if not valid:
            return self._fallback_realtime_bar(bar, "REALTIME_PRIMARY_BAR_INVALID")
        if not self._market_data_authority.claim_authority(code, minute, REALTIME_AUTHORITY):
            return {"ok": False, "reason_code": "MINUTE_AUTHORITY_CONFLICT"}
        commit = getattr(self.kiwoom_api, "commit_realtime_primary_bar", None)
        if not callable(commit):
            return self._fallback_realtime_bar(bar, "REALTIME_COMMIT_API_UNAVAILABLE")
        result = commit(bar, stock_name=str(getattr(entry, "stock_name", "") or ""))
        if (
            not isinstance(result, dict)
            or result.get("ok") is not True
            or result.get("commit_verified") is not True
        ):
            return self._fallback_realtime_bar(bar, "REALTIME_CANONICAL_COMMIT_FAILED")
        self._market_data_authority.mark_realtime_committed(code, minute)
        return dict(result)

    def _fallback_realtime_bar(self, bar: RealtimeShadowBar, reason: str) -> dict[str, object]:
        code, minute = bar.stock_code, bar.minute_key
        self._market_data_authority.replace_with_reconciliation_authority(code, minute)
        self._market_data_authority.begin_reconciliation(code, minute, reason)
        self._remember_reconciliation_shadow_bar(bar)
        requested = self._request_realtime_reconciliation(code, minute)
        return {
            "ok": False,
            "reason_code": reason,
            "reconciliation_requested": requested,
        }

    def _request_realtime_reconciliation(self, stock_code: str, minute_key: str) -> bool:
        key = (str(stock_code or "").strip(), str(minute_key or "").strip())
        if not all(key) or key in self._pending_reconciliations:
            return False
        self._pending_reconciliations.add(key)
        from auto_candle_refresh import request_operation_candle_for_stock

        stock_dir = StockRepository().resolve_stock_dir(key[0])
        evaluation_minute = (
            datetime.strptime(key[1], "%Y-%m-%d %H:%M") + timedelta(minutes=1)
        ).strftime("%Y-%m-%d %H:%M")
        request_result = request_operation_candle_for_stock(
            self,
            stock_dir,
            key[0],
            "",
            operation_cycle_minute_key=evaluation_minute,
            request_kind=REALTIME_RECONCILIATION_REQUEST,
            reconciliation_minute=key[1],
            on_terminal=lambda result: self.complete_reconciliation(
                key[0],
                key[1],
                stock_dir,
                result,
            ),
        )
        return request_result.get("ok") is True

    def _observe_exception(
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
            component="market_data_host",
            operation=operation,
            source="gui_market_data_host.MarketDataHost",
            target_type="MARKET_DATA",
            target_id=stock_code or "market_data_host",
            target_name=stock_code or "Market data host",
            reason_code="MARKET_DATA_HOST_FAILED",
            owner=self,
            failure_scope=f"market_data_host:{operation}:{stock_code}",
        )
