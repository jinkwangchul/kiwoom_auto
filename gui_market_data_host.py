# -*- coding: utf-8 -*-
"""Widget-free market-data ownership boundary for the operation host."""

from __future__ import annotations

from collections import OrderedDict, deque
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from hashlib import sha256
import json
import logging
from math import isfinite
import os
from pathlib import Path
from time import monotonic
from typing import Any, Callable

from PyQt5.QtCore import QObject, QTimer, pyqtSignal

from candle_manager import (
    DEFAULT_CANDLES_MAX_COUNT,
    canonical_candle_content_hash,
    load_candles,
)
from candle_timeframe_aggregation import (
    SEOUL_TIMEZONE,
    candle_market_datetime,
    normalize_candle_sessions,
    parse_market_datetime,
    project_candle_supply,
    read_canonical_bar_minutes,
    validate_market_bar_projection_request,
)
from kiwoom_candle_adapter import normalize_opt10080_rows
import sor_m3_native_observation as _m3_observation
from event_journal_production import observe_production_exception
from gui_stock_data import (
    PROJECT_ROOT,
    STOCK_LIBRARY_READY,
    load_stock_library_snapshot,
    stock_nxt_availability,
)
from stock_market_source_plan import (
    native_bar_end, read_stock_market_source_plan, source_for_time,
    split_native_closing_price_candles,
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
from production_current_price_authority import (
    ProductionCurrentPriceEvidence,
    select_production_current_price_evidence,
)
from stock_repository import StockRepository
from sor_confirmed_m1_registration import ConfirmedSorM1Registration
from indicator_follow_validation_v5_sor_tail_bridge import apply_verified_sor_tail
from indicator_follow_signal_validation_runtime_trace import (
    trace_signal_validation_event,
)
from stock_code_contract import (
    canonical_stock_code_from_market_data_identity,
    is_broker_action_stock_code,
    market_source_for_identity,
)


LOGGER = logging.getLogger(__name__)


ExecutionEntryProvider = Callable[[str], object | None]

HIGH_RESOLUTION_DATA_NORMAL = "NORMAL"
HIGH_RESOLUTION_DATA_UNCERTAIN = "UNCERTAIN"
_PRICE_SIGNAL_OBSERVATION_GENERATION_KEY = "_price_signal_observation_generation"

_DIRECT_TIMEFRAME_INCREMENTAL_REQUEST_COUNT = 3
_DISPLAY_INTEGRATED_SESSIONS = (
    ("NXT_PRE", 8 * 60, 8 * 60 + 50),
    ("KRX", 9 * 60, 15 * 60 + 20),
    ("KRX_CLOSING_AUCTION", 15 * 60 + 20, 15 * 60 + 30),
    ("NXT_AFTER", 15 * 60 + 40, 20 * 60),
)


def _trace_sor_block(
    owner: object, market: object, *, stock: str, reason: str,
    as_of: datetime, cutoff: datetime, expected_latest: datetime | None,
    source_starts: list[str], candidate_starts: list[str],
    target_starts: list[str], stage: str, branch: str,
    timeframe_minutes: int,
) -> None:
    """Record bounded, session-scoped evidence without affecting the block."""
    try:
        key = (stock, reason, cutoff.isoformat(), stage, branch)
        seen = _host_attr(owner, "_sor_block_diagnostic_keys")
        if not isinstance(seen, dict):
            seen = {}
            object.__setattr__(owner, "_sor_block_diagnostic_keys", seen)
        if key in seen:
            return
        native = _host_attr(market, "_v5_al_native_minutes", {}).get(stock, {})
        current = native.get("current") if isinstance(native, dict) else None
        previous = native.get("closed") if isinstance(native, dict) else None
        direct = _host_attr(market, "_display_timeframe_candles", {}).get((stock, 3), ())
        identities = _host_attr(market, "_display_timeframe_session_identity", {})
        identity = identities.get((stock, 3)) if isinstance(identities, dict) else None
        def summary(starts: list[str]) -> dict[str, object]:
            return {"count": len(starts), "first": starts[0] if starts else "",
                    "last": starts[-1] if starts else ""}
        left, right = ((candidate_starts, target_starts)
                       if branch == "PROJECTED_TARGET" else
                       (source_starts, candidate_starts))
        difference = sorted(set(left) ^ set(right))
        record = {
            "kst_time": datetime.now(SEOUL_TIMEZONE).isoformat(timespec="seconds"),
            "stock": stock, "reason": reason,
            "as_of": as_of.isoformat(timespec="seconds"),
            "cutoff": cutoff.isoformat(timespec="seconds"),
            "expected_latest": (expected_latest.isoformat(timespec="seconds")
                                if expected_latest else ""),
            "native_m1": {
                "current": current.get("minute", "") if isinstance(current, dict) else "",
                "previous": previous.get("minute", "") if isinstance(previous, dict) else "",
                "count": sum(isinstance(row, dict) for row in (previous, current)),
            },
            "direct_al_m3": {
                "last": (str(direct[-1].get("bar_time") or "")
                         if direct and isinstance(direct[-1], dict) else ""),
                "session_id_sha256": (sha256(identity[1].encode("utf-8")).hexdigest()
                                      if isinstance(identity, tuple) and len(identity) == 2
                                      and isinstance(identity[1], str) else ""),
            },
            "source": summary(source_starts),
            "candidate": summary(candidate_starts),
            "today_target_starts": summary(target_starts),
            "difference": {"count": len(difference), "sample": difference[:8]},
            "stage": stage, "branch": branch,
            "time_unit": "KST", "start_format": "YYYYMMDDHHMMSS",
            "timeframe_minutes": timeframe_minutes,
            "session_identity": {
                "connection_epoch": identity[0] if isinstance(identity, tuple) else None,
                "login_session_id_sha256": (
                    sha256(identity[1].encode("utf-8")).hexdigest()
                    if isinstance(identity, tuple) and len(identity) == 2
                    and isinstance(identity[1], str) else ""),
            },
        }
        if len(seen) >= 128:
            seen.pop(next(iter(seen)))
        seen[key] = None
        trace_signal_validation_event("v5_sor_block", **record)
    except Exception:
        return


def _host_attr(host: object, name: str, default: Any = None) -> Any:
    """Read host state without invoking PyQt's uninitialized QObject fallback."""
    try:
        return object.__getattribute__(host, name)
    except (AttributeError, RuntimeError, TypeError):
        return default


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
class SorDisplayLivePriceState:
    canonical_stock_code: str
    broker_code_identity: str
    market_source: str
    connection_epoch: int
    login_session_id: str
    last_market_datetime: str
    last_price: int | float
    receive_sequence: int
    data_quality: str


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
    direct_sor_completed = pyqtSignal(object)

    MAX_PENDING_SHADOW_COMPARISONS = 512
    MAX_RAW_TICK_QUEUE_DEPTH = 512
    MAX_RAW_TICK_DRAIN_BATCH = 32
    MAX_NXT_DISPLAY_TICK_DRAIN_BATCH = 32
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
        self._sor_m1_registration = ConfirmedSorM1Registration()
        self._shutting_down = False
        self._sor_tick_evidence_enabled = (
            os.environ.get("KIWOOM_V5_SOR_TICK_EVIDENCE") == "1"
        )
        self._sor_tick_evidence = None
        self._sor_tick_evidence_error = ""
        if self._sor_tick_evidence_enabled:
            try:
                from sor_tick_evidence import TickEvidenceSampler

                path = (
                    Path(__file__).parent / "runtime" / "diagnostics"
                    / "v5_sor_tick_evidence"
                    / f"ticks-{os.getpid()}-{datetime.now():%Y%m%dT%H%M%S%f}.jsonl"
                )
                self._sor_tick_evidence = TickEvidenceSampler(path)
            except Exception as exc:
                self._sor_tick_evidence_error = type(exc).__name__

        self._sor_diagnostic_idle_timer = None
        self._sor_diagnostic_idle_required = False
        self._sor_diagnostic_idle_inflight = False
        self._sor_diagnostic_idle_request_generation = 0
        self._sor_diagnostic_idle_last_attempt_minute = ""
        if (self._sor_tick_evidence_enabled
                and os.environ.get("KIWOOM_V5_SOR_DIAGNOSTIC_STOCK_CODE") == "005930"):
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(self._on_sor_diagnostic_idle_timeout)
            self._sor_diagnostic_idle_timer = timer


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
        self._v5_observation_leases: dict[object, str] = {}
        self._v5_observation_timeframes: dict[object, int] = {}
        self._native_observation_forming_by_stock: dict[tuple[str, int], dict[str, object]] = {}
        self._native_observation_completed_by_stock: dict[tuple[str, int], dict[str, object]] = {}
        self._native_observation_last_receipt_by_stock: dict[tuple[str, int], dict[str, object]] = {}
        self._native_observation_receipt_by_stock: dict[tuple[str, int], dict[str, object]] = {}
        self._v5_native_last_attempt: dict[tuple[str, int, str], datetime] = {}
        self._v5_native_tick_refresh_scheduled = False
        self._mini_chart_observation_leases: dict[object, str] = {}
        self._sor_display_live_price_states: dict[str, SorDisplayLivePriceState] = {}
        self._v5_al_native_minutes: dict[str, dict[str, object]] = {}
        self._v5_al_parity: dict[str, tuple[int, str]] = {}
        self._v5_al_tick_signal_bound = False
        self._v5_watch_generation = 0
        self._v5_watch_inflight = False
        self._v5_m3_gap_proof_attempts: set[tuple[object, ...]] = set()
        self._candle_execution_required_by_stock: dict[str, int] = {}
        self._candle_observation_required_by_stock: dict[str, int] = {}
        self._candle_standby_stock_codes: tuple[str, ...] = ()
        self._sor_m1_required_by_stock: dict[str, int] = {}
        self._sor_standby_m1_required_by_stock: dict[str, int] = {}
        self._sor_observation_m1_required_by_stock: dict[str, int] = {}
        self._native_selected_sor_observation_frames: set[tuple[str, int]] = set()
        self._native_selected_sor_standby_frames: set[tuple[str, int]] = set()
        self._candle_standby_required_by_stock: dict[str, int] = {}
        self._candle_execution_timeframe_requirements_by_stock: dict[
            str, dict[int, int]
        ] = {}
        self._candle_observation_timeframe_requirements_by_stock: dict[
            str, dict[int, int]
        ] = {}
        self._candle_standby_timeframe_requirements_by_stock: dict[
            str, dict[int, int]
        ] = {}
        self._direct_timeframe_candles: dict[
            tuple[str, int], tuple[dict[str, Any], ...]
        ] = {}
        self._display_timeframe_candles: dict[
            tuple[str, int], tuple[dict[str, Any], ...]
        ] = {}
        self._display_timeframe_session_identity: dict[
            tuple[str, int], tuple[int, str]
        ] = {}
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
        if not self._v5_al_tick_signal_bound:
            signal = getattr(self.kiwoom_api, "v5_al_tick_received", None)
            connect = getattr(signal, "connect", None)
            if callable(connect):
                try:
                    connect(self._on_v5_al_tick_received)
                except Exception as exc:
                    self._observe_exception(exc, "bind_v5_al_tick")
                else:
                    self._v5_al_tick_signal_bound = True
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
            self._candle_execution_timeframe_requirements_by_stock = {
                code: dict(values)
                for code, values in getattr(
                    self,
                    "_candle_execution_timeframe_requirements_by_stock",
                    {},
                ).items()
                if code in self._execution_shadow_stock_codes
                and isinstance(values, dict)
            }
            target_codes = tuple(
                sorted(
                    set(self._production_monitoring_stock_codes)
                    | set(self._execution_shadow_stock_codes)
                    | set(self._candle_observation_stock_codes)
                    | set(_host_attr(self, "_v5_observation_leases", {}).values())
                    | set(_host_attr(self, "_mini_chart_observation_leases", {}).values())
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
                self._sor_m1_registration.sync((), 0, "", False)
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
            self._sor_m1_registration.sync(
                self._registration_shadow_targets(registration), *identity,
                active=bool(getattr(registration, "active", False)),
            )
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
            self._sor_m1_registration.sync((), 0, "", False)
            return {
                "ok": False,
                "changed": False,
                "active": False,
                "reason_code": "REALTIME_SHADOW_RESULT_MALFORMED",
            }
        except Exception as exc:
            self._sor_m1_registration.sync((), 0, "", False)
            self._observe_exception(exc, "sync_targets")
            return {
                "ok": False,
                "changed": False,
                "active": False,
                "reason_code": "REALTIME_SHADOW_SYNC_FAILED",
                "error": str(exc),
            }

    def sor_m1_calculation_status(self, stock_code: str) -> dict[str, object]:
        code = str(stock_code or "").strip()
        self._sor_registration_current(code)
        return self._sor_m1_registration.status(code)

    def published_sor_day_for_native_receipt(self, stock_code: str):
        """Read an existing verified day without any new broker request."""
        code = str(stock_code or "").strip()
        if not self._sor_registration_current(code):
            return None
        return self._sor_m1_registration.published_day_for_native_receipt(code)

    def _sor_registration_current(self, stock_code: str) -> bool:
        """Recheck the local broker epoch before exposing calculation data."""
        try:
            registration = self.kiwoom_api.realtime_shadow_registration_snapshot()
            broker = self.kiwoom_api.broker_session_snapshot()
            identity = self._realtime_shadow_session_identity
            session_ready = (
                getattr(registration, "active", False) is True
                and getattr(broker, "connected", False) is True
                and identity[0] > 0 and bool(identity[1])
                and (getattr(registration, "connection_epoch", None),
                     getattr(registration, "login_session_id", None)) == identity
                and (getattr(broker, "connection_epoch", None),
                     getattr(broker, "login_session_id", None)) == identity
            )
            target_ready = stock_code in self._registration_shadow_targets(registration)
        except Exception:
            session_ready = target_ready = False
        if not session_ready:
            self._sor_m1_registration.sync((), 0, "", False)
        elif not target_ready:
            self._sor_m1_registration.sync(
                self._registration_shadow_targets(registration), *identity, True,
            )
        return session_ready and target_ready

    def stage_verified_sor_m1(self, batch: object) -> dict[str, object]:
        code = str(getattr(getattr(batch, "session", None), "stock_code", "") or "")
        if not self._sor_registration_current(code):
            return {"status": "BLOCKED", "reason": "REGISTRATION_INACTIVE"}
        return self._sor_m1_registration.stage(batch)

    def publish_verified_sor_m1(self, batch: object) -> dict[str, object]:
        code = str(getattr(getattr(batch, "session", None), "stock_code", "") or "")
        if not self._sor_registration_current(code):
            return {"status": "BLOCKED", "reason": "REGISTRATION_INACTIVE"}
        return self._sor_m1_registration.publish(batch)

    def accept_broker_al_m1_receipt(
        self, plan, started, receipt, *, independently_verified_day=None,
    ) -> dict[str, object]:
        """Admit native _AL M1 only when an independent day matches every row.

        Receipt-only candidates remain pending. Never enqueue orders or start
        a broker request from this passive calculation-input boundary.
        """
        from sor_broker_al_m1_handoff import prepare_broker_al_m1_handoff

        code = str(getattr(plan, "stock_code", "") or "")
        if not self._sor_registration_current(code):
            return {"status": "BLOCKED", "reason": "REGISTRATION_INACTIVE"}
        checked = prepare_broker_al_m1_handoff(
            plan, started, receipt,
            independently_verified_day=independently_verified_day,
        )
        if checked.status != "VERIFIED_INPUT_READY":
            return {"status": checked.status, "reason": checked.reason,
                    "matched_rows": checked.matched_rows}
        existing = self._sor_m1_registration.published_day_for_native_receipt(code)
        if existing is not None:
            if existing is not checked.verified_day and existing != checked.verified_day:
                return {"status": "BLOCKED", "reason": "PUBLISHED_DAY_CONFLICT",
                        "matched_rows": checked.matched_rows}
            # Never invalidate installed timeframe tails for the same day.
            return {"status": "READY", "reason": "VERIFIED_DAY_ALREADY_PUBLISHED",
                    "matched_rows": checked.matched_rows,
                    "native_receipt_crosschecked": True}
        outcome = self.publish_verified_sor_m1(checked.verified_day)
        return {**outcome, "matched_rows": checked.matched_rows,
                "native_receipt_crosschecked": outcome.get("status") == "READY"}

    def install_verified_sor_tail(self, timeframe_key: str, batch: object) -> dict[str, object]:
        code = str(getattr(getattr(batch, "session", None), "stock_code", "") or "")
        if not self._sor_registration_current(code):
            return {"status": "BLOCKED", "reason": "REGISTRATION_INACTIVE"}
        return self._sor_m1_registration.install_tail(timeframe_key, batch)

    def verified_sor_tail_provider(self, session, history_sha256, as_of):
        stock = getattr(getattr(session, "request", None), "stock", None)
        key = getattr(getattr(session, "request", None), "timeframe_key", None)
        code = str(getattr(stock, "code", "") or "")
        if not self._sor_registration_current(code):
            return None
        return self._sor_m1_registration.batch(
            code, str(key or ""),
            history_sha256, as_of,
        )

    def sor_calculation_view(self, view, stock_code, timeframe_key,
                             timeframe_minutes, as_of, history_sha256):
        batch = (self._sor_m1_registration.batch(
            stock_code, timeframe_key, history_sha256, as_of,
        ) if self._sor_registration_current(stock_code) else None)
        return apply_verified_sor_tail(
            view, stock_code=stock_code, timeframe_key=timeframe_key,
            timeframe_minutes=timeframe_minutes, as_of=as_of,
            history_sha256=history_sha256, batch=batch,
        )

    def calculate_verified_sor_signal(
        self, view, *, stock_code, timeframe_key, timeframe_minutes,
        as_of, history_sha256, evaluation_at, rules, context=None,
    ) -> dict[str, object]:
        """Run the existing routine filters on a detached verified tail only."""
        if (not isinstance(as_of, datetime)
                or as_of.tzinfo is None
                or as_of.utcoffset() != timedelta(hours=9)
                or not isinstance(evaluation_at, datetime)
                or evaluation_at.tzinfo is None
                or evaluation_at.utcoffset() != timedelta(hours=9)
                or evaluation_at.date() != as_of.date()
                or evaluation_at < as_of
                or evaluation_at.replace(second=0, microsecond=0)
                != as_of.replace(second=0, microsecond=0)
                or type(timeframe_minutes) is not int
                or timeframe_key != f"M{timeframe_minutes}"
                or not isinstance(rules, dict)
                or (context is not None and not isinstance(context, dict))):
            raise ValueError("SOR_VERIFIED_BLOCKED:CALCULATION_INPUT")
        try:
            if read_canonical_bar_minutes(rules) != timeframe_minutes:
                raise ValueError("TIMEFRAME_MISMATCH")
        except (TypeError, ValueError) as exc:
            raise ValueError("SOR_VERIFIED_BLOCKED:TIMEFRAME_MISMATCH") from exc

        verified = self.sor_calculation_view(
            view, stock_code, timeframe_key, timeframe_minutes,
            as_of, history_sha256,
        )
        if verified.get("sor_tail_calculation_complete") is not True:
            raise ValueError("SOR_VERIFIED_BLOCKED:TAIL_INCOMPLETE")

        # The routine evaluator reads context['candles'] before its argument.
        # Retain only calculation facts and force the verified detached series.
        allowed = (
            "cycle", "actionable_current_price", "current_price",
            "average_price", "avg_price", "position_average_price",
            "average_price_series", "position", "holding",
        )
        signal_context = {
            key: deepcopy(context[key]) for key in allowed
            if isinstance(context, dict) and key in context
        }
        signal_context["candles"] = deepcopy(verified["candles"])
        from routines.지표추종매매.routine import evaluate_signal_selection

        result = evaluate_signal_selection(
            signal_context["candles"], deepcopy(rules), signal_context,
        )
        return {
            **result,
            "calculation_only": True,
            "execution_enabled": False,
            "queue_connected": False,
            "execution_connected": False,
            "send_order_connected": False,
            "verified_tail_signature": verified["sor_tail_signature"],
        }

    def compare_verified_sor_calculations(
        self, chart_view, live_view, *,
        stock_code, timeframe_key, timeframe_minutes,
        as_of, history_sha256, evaluation_at=None, rules=None, context=None,
    ) -> dict[str, object]:
        """Compare separately supplied verified chart/live data; no order link."""
        from sor_calculation_parity import compare_verified_sor_calculations
        return compare_verified_sor_calculations(
            self, chart_view, live_view,
            stock_code=stock_code, timeframe_key=timeframe_key,
            timeframe_minutes=timeframe_minutes, as_of=as_of,
            history_sha256=history_sha256, evaluation_at=evaluation_at,
            rules=rules, context=context,
        )

    @staticmethod
    def _direct_timeframe_requirement(
        rules: dict[str, Any] | None,
        request: dict[str, Any],
    ) -> tuple[int, int, int]:
        interval = read_canonical_bar_minutes(rules)
        warmup_bars = max(int(request.get("warmup_bars", 1) or 1), 1)
        history_target_bars = max(
            int(request.get("history_target_bars", warmup_bars) or warmup_bars),
            warmup_bars,
        )
        direct_bars = min(history_target_bars, DEFAULT_CANDLES_MAX_COUNT)
        minute_source_count = direct_bars if interval == 1 else interval + 1
        return interval, direct_bars, minute_source_count

    @staticmethod
    def _merge_timeframe_requirement(
        target: dict[str, dict[int, int]],
        stock_code: str,
        interval: int,
        count: int,
    ) -> None:
        per_stock = target.setdefault(stock_code, {})
        per_stock[interval] = max(int(per_stock.get(interval, 0)), int(count))

    def direct_timeframe_requirements(
        self,
        stock_code: object,
    ) -> dict[int, int]:
        code = str(stock_code or "").strip()
        merged: dict[int, int] = {}
        for attribute in (
            "_candle_execution_timeframe_requirements_by_stock",
            "_candle_observation_timeframe_requirements_by_stock",
            "_candle_standby_timeframe_requirements_by_stock",
        ):
            mapping = _host_attr(self, attribute, {})
            if not isinstance(mapping, dict):
                continue
            per_stock = mapping.get(code)
            if not isinstance(per_stock, dict):
                continue
            for raw_interval, raw_count in per_stock.items():
                try:
                    interval = int(raw_interval)
                    count = int(raw_count)
                except (TypeError, ValueError):
                    continue
                if interval > 0 and count > 0:
                    merged[interval] = max(merged.get(interval, 0), count)
        sor_m1 = _host_attr(self, "_sor_m1_required_by_stock", {})
        if isinstance(sor_m1, dict):
            count = sor_m1.get(code)
            if type(count) is int and 0 < count <= DEFAULT_CANDLES_MAX_COUNT:
                merged[1] = max(merged.get(1, 0), count)
        return merged

    @staticmethod
    def _expected_latest_completed_direct_start(
        now: datetime,
        interval: int,
    ) -> datetime | None:
        current = now.astimezone(SEOUL_TIMEZONE)
        anchor = current.replace(hour=9, minute=0, second=0, microsecond=0)
        close = current.replace(hour=15, minute=30, second=0, microsecond=0)
        if current < anchor:
            return None
        if current >= close:
            elapsed = int((close - anchor).total_seconds() // 60)
            bucket_index = max((max(elapsed - 1, 0)) // interval, 0)
            return anchor + timedelta(minutes=bucket_index * interval)
        elapsed = int((current - anchor).total_seconds() // 60)
        completed_count = elapsed // interval
        if completed_count <= 0:
            return None
        return anchor + timedelta(minutes=(completed_count - 1) * interval)

    def direct_timeframe_refresh_specs(
        self,
        stock_code: object,
        minute_key: object,
    ) -> tuple[dict[str, int], ...]:
        code = str(stock_code or "").strip()
        now = parse_market_datetime(minute_key) or datetime.now(SEOUL_TIMEZONE)
        specs: list[dict[str, int]] = []
        requirements = MarketDataHost.direct_timeframe_requirements(self, code)
        sor_standby = _host_attr(self, "_sor_standby_m1_required_by_stock", {})
        if code in sor_standby:
            standby = _host_attr(
                self, "_candle_standby_timeframe_requirements_by_stock", {}
            ).get(code, {})
            for interval in standby:
                other = max(
                    _host_attr(self, attribute, {}).get(code, {}).get(interval, 0)
                    for attribute in (
                        "_candle_execution_timeframe_requirements_by_stock",
                        "_candle_observation_timeframe_requirements_by_stock",
                    )
                )
                if other:
                    requirements[interval] = other
                else:
                    requirements.pop(interval, None)
        for interval, required_count in sorted(
            requirements.items()
        ):
            if interval <= 1:
                continue
            cached = list(
                _host_attr(self, "_direct_timeframe_candles", {}).get(
                    (code, interval), ()
                )
            )
            due = len(cached) < required_count
            expected_start = MarketDataHost._expected_latest_completed_direct_start(
                now, interval
            )
            if not due and expected_start is not None:
                latest = (
                    candle_market_datetime(cached[-1])
                    if cached
                    else None
                )
                due = latest is None or latest < expected_start
            if due:
                specs.append(
                    {
                        "timeframe_minutes": interval,
                        "required_count": required_count,
                        "request_count": (
                            min(
                                required_count + 1,
                                DEFAULT_CANDLES_MAX_COUNT,
                            )
                            if len(cached) < required_count
                            else min(
                                _DIRECT_TIMEFRAME_INCREMENTAL_REQUEST_COUNT,
                                DEFAULT_CANDLES_MAX_COUNT,
                            )
                        ),
                    }
                )
        return tuple(specs)

    def operation_candle_request_cost(
        self,
        stock_code: object,
        minute_key: object,
    ) -> int:
        return 1 + len(
            MarketDataHost.direct_timeframe_refresh_specs(
                self, stock_code, minute_key
            )
        ) + len(
            MarketDataHost.display_timeframe_refresh_specs(
                self, stock_code, minute_key
            )
        )

    def install_direct_timeframe_candles(
        self,
        stock_code: object,
        timeframe_minutes: object,
        rows: object,
        *,
        as_of: datetime | None = None,
    ) -> dict[str, object]:
        code = str(stock_code or "").strip()
        try:
            interval = int(timeframe_minutes)
        except (TypeError, ValueError):
            return {"ok": False, "reason_code": "DIRECT_TIMEFRAME_INVALID"}
        if not code or interval <= 1 or not isinstance(rows, list):
            return {"ok": False, "reason_code": "DIRECT_TIMEFRAME_INVALID"}
        current = as_of or datetime.now(SEOUL_TIMEZONE)
        if current.tzinfo is None:
            current = current.replace(tzinfo=SEOUL_TIMEZONE)
        else:
            current = current.astimezone(SEOUL_TIMEZONE)
        normalized = normalize_opt10080_rows(rows)
        complete_by_time: dict[datetime, dict[str, Any]] = {}
        current_minute = current.hour * 60 + current.minute
        for candle in normalized:
            bar_time = candle_market_datetime(candle)
            if bar_time is None:
                continue
            same_day = bar_time.date() == current.date()
            bar_minute = bar_time.hour * 60 + bar_time.minute
            complete = (
                bar_time.date() < current.date()
                or current >= bar_time + timedelta(minutes=interval)
                or (
                    same_day
                    and current_minute >= 15 * 60 + 30
                    and 9 * 60 <= bar_minute < 15 * 60 + 30
                )
            )
            if not complete:
                continue
            complete_by_time[bar_time] = {
                "bar_time": bar_time.isoformat(timespec="seconds"),
                "open": candle.get("open"),
                "high": candle.get("high"),
                "low": candle.get("low"),
                "close": candle.get("close"),
                "volume": candle.get("volume"),
                "timeframe_minutes": interval,
                "is_complete": True,
                "trade_date": bar_time.date().isoformat(),
                "session": "regular",
            }
        cache = _host_attr(self, "_direct_timeframe_candles", None)
        if not isinstance(cache, dict):
            cache = {}
            object.__setattr__(self, "_direct_timeframe_candles", cache)
        existing = cache.get((code, interval), ())
        for candle in existing:
            bar_time = candle_market_datetime(candle)
            if bar_time is not None:
                complete_by_time.setdefault(bar_time, dict(candle))
        required_count = MarketDataHost.direct_timeframe_requirements(
            self, code
        ).get(interval, len(complete_by_time))
        ordered = [
            complete_by_time[key]
            for key in sorted(complete_by_time)
        ]
        if required_count > 0:
            ordered = ordered[-required_count:]
        cache[(code, interval)] = tuple(ordered)
        return {
            "ok": bool(ordered),
            "reason_code": (
                "DIRECT_TIMEFRAME_READY"
                if ordered
                else "DIRECT_TIMEFRAME_EMPTY"
            ),
            "stock_code": code,
            "timeframe_minutes": interval,
            "stored_count": len(ordered),
            "latest_bar_time": (
                str(ordered[-1].get("bar_time") or "")
                if ordered
                else ""
            ),
        }

    @staticmethod
    def _display_session_for_time(bar_time: datetime) -> str:
        minute = bar_time.hour * 60 + bar_time.minute
        for name, start, end in _DISPLAY_INTEGRATED_SESSIONS:
            if start <= minute < end:
                return name
        if 15 * 60 + 30 <= minute < 15 * 60 + 40:
            return "AL_TRANSITION"
        return ""

    @staticmethod
    def _full_day_display_count(interval: int) -> int:
        return sum(
            (end - start + (10 if name == "KRX" else 0) + interval - 1) // interval
            for name, start, end in _DISPLAY_INTEGRATED_SESSIONS
            if name != "KRX_CLOSING_AUCTION"
        )

    @staticmethod
    def _expected_latest_completed_display_start(
        now: datetime,
        interval: int,
    ) -> datetime | None:
        current = now.astimezone(SEOUL_TIMEZONE)
        candidates: list[datetime] = []
        for name, start, end in _DISPLAY_INTEGRATED_SESSIONS:
            if name == "KRX_CLOSING_AUCTION":
                continue  # Broker prints are sparse during the closing call auction.
            session_start = current.replace(
                hour=start // 60,
                minute=start % 60,
                second=0,
                microsecond=0,
            )
            session_end = current.replace(
                hour=end // 60,
                minute=end % 60,
                second=0,
                microsecond=0,
            )
            effective_end = min(current, session_end)
            if effective_end <= session_start:
                continue
            elapsed = int((effective_end - session_start).total_seconds() // 60)
            completed_count = (
                (end - start + interval - 1) // interval
                if current >= session_end
                else elapsed // interval
            )
            if completed_count > 0:
                candidates.append(
                    session_start + timedelta(
                        minutes=(completed_count - 1) * interval
                    )
                )
        return max(candidates) if candidates else None

    def display_timeframe_refresh_specs(
        self,
        stock_code: object,
        minute_key: object,
    ) -> tuple[dict[str, int], ...]:
        code = str(stock_code or "").strip()
        if code not in MarketDataHost._nxt_eligible_targets((code,)):
            return ()
        now = parse_market_datetime(minute_key) or datetime.now(SEOUL_TIMEZONE)
        try:
            plan = read_stock_market_source_plan(PROJECT_ROOT, code, now.date())
        except ValueError:
            return ()
        active = source_for_time(plan, now) if plan is not None else None
        if plan is None or (active is not None and active["source"] != "SOR"):
            return ()
        specs: list[dict[str, int]] = []
        for interval, required_count in sorted(
            MarketDataHost.direct_timeframe_requirements(self, code).items()
        ):
            display_required_count = required_count if interval == 1 else max(
                required_count,
                MarketDataHost._full_day_display_count(interval),
            )
            cached = MarketDataHost._current_display_candles(self, code, interval)
            due = len(cached) < display_required_count
            sor_targets = _host_attr(self, "_sor_m1_required_by_stock", {})
            if (not due and interval == 1
                    and isinstance(sor_targets, dict)
                    and code in sor_targets
                    and MarketDataHost._display_session_for_time(now)
                    in {"NXT_PRE", "KRX", "NXT_AFTER"}):
                # Include a current-minute integrated TR snapshot in the
                # existing once-per-minute governed request cadence.
                forming = _host_attr(self, "_sor_forming_m1_by_stock", {})
                entry = forming.get(code) if isinstance(forming, dict) else None
                candle = entry.get("candle") if isinstance(entry, dict) else None
                due = (
                    not isinstance(entry, dict)
                    or entry.get("session_identity")
                    != MarketDataHost._current_display_session_identity(self)
                    or not isinstance(candle, dict)
                    or candle_market_datetime(candle)
                    != now.replace(second=0, microsecond=0)
                )
            expected = MarketDataHost._expected_latest_completed_display_start(
                now,
                interval,
            )
            if not due and expected is not None:
                latest = candle_market_datetime(cached[-1]) if cached else None
                due = latest is None or latest < expected
            if due:
                specs.append(
                    {
                        "timeframe_minutes": interval,
                        "required_count": display_required_count,
                        "nxt_available": True,
                        "request_count": min(
                            display_required_count + 1
                            if len(cached) < display_required_count
                            else _DIRECT_TIMEFRAME_INCREMENTAL_REQUEST_COUNT,
                            DEFAULT_CANDLES_MAX_COUNT,
                        ),
                    }
                )
        return tuple(specs)

    def _current_display_session_identity(self) -> tuple[int, str] | None:
        try:
            getter = getattr(_host_attr(self, "kiwoom_api"), "broker_session_snapshot", None)
            session = getter() if callable(getter) else None
            epoch = getattr(session, "connection_epoch", None)
            login_id = getattr(session, "login_session_id", None)
            if (getattr(session, "connected", None) is True
                    and type(epoch) is int and epoch > 0
                    and isinstance(login_id, str) and bool(login_id)):
                return epoch, login_id
        except Exception:
            pass
        return None

    def install_native_observation_candle(
        self, stock_code: str, timeframe_minutes: int, rows: object, *,
        source: str, request_as_of: datetime, received_at: datetime,
        identity: tuple[int, str], request_id: str,
        now: datetime | None = None,
        request_receipt: tuple[dict, dict] | None = None,
        tick_anchor_sequence: int | None = None,
    ) -> dict[str, object]:
        """Admit a broker-native current bar for an active selected-frame consumer."""
        code = str(stock_code or "").strip()
        interval = timeframe_minutes if type(timeframe_minutes) is int else 0
        current = now if isinstance(now, datetime) else datetime.now(SEOUL_TIMEZONE)
        def observed(reason: str, *, forming: object = None,
                     completed_count: int = 0) -> dict[str, object]:
            if _m3_observation.ENABLED and code == "005930" and interval == 3:
                try:
                    try:
                        normalized = normalize_opt10080_rows(rows[:1]) if isinstance(rows, list) else ()
                    except Exception:
                        normalized = ()
                    row1 = normalized[0] if normalized else None
                    fields = ("time", "open", "high", "low", "close", "volume")
                    response = (request_receipt[1] if isinstance(request_receipt, tuple)
                                and len(request_receipt) == 2
                                and isinstance(request_receipt[1], dict) else {})
                    _m3_observation.record("tr", stock_code=code, timeframe="M3",
                           request_id=request_id, request_as_of=request_as_of,
                           received_at=received_at, source=source,
                           market_data_identity=response.get("market_data_identity"),
                           connection_epoch=(identity[0] if isinstance(identity, tuple)
                                             and identity else None),
                           login_session_sha256=(sha256(identity[1].encode()).hexdigest()
                                                 if isinstance(identity, tuple) and len(identity) == 2
                                                 and isinstance(identity[1], str) else None),
                           response_first_row_normalized=(
                               {field: row1.get(field) for field in fields}
                               if isinstance(row1, dict) else None),
                           accepted_forming=isinstance(forming, dict),
                           forming_time=forming.get("time") if isinstance(forming, dict) else None,
                           bar_end=forming.get("native_bar_end") if isinstance(forming, dict) else None,
                           completed_count=completed_count, reason=reason)
                except Exception:
                    pass
            return {"ok": reason == "NATIVE_OBSERVATION_ACCEPTED",
                    "reason_code": reason}
        if (interval <= 0 or interval > 240 or not isinstance(rows, list)
                 or source not in {"NXT", "INTEGRATED", "KRX"}
                or not isinstance(request_id, str) or not request_id.strip()
                or identity != MarketDataHost._current_display_session_identity(self)
                or not isinstance(request_as_of, datetime)
                or not isinstance(received_at, datetime)
                or request_as_of.tzinfo is None or received_at.tzinfo is None
                or current.tzinfo is None
                or not request_as_of <= received_at <= current
                or received_at - request_as_of >= timedelta(minutes=1)
                or current - received_at >= timedelta(minutes=1)
                 or (code, interval) not in MarketDataHost._native_selected_targets(
                     self, source=source)):
            return observed("NATIVE_OBSERVATION_EVIDENCE_INVALID")
        try:
            plan = read_stock_market_source_plan(PROJECT_ROOT, code, request_as_of.date())
            requested = source_for_time(plan, request_as_of) if plan else None
            received = source_for_time(plan, received_at) if plan else None
            active = source_for_time(plan, current) if plan else None
        except (ValueError, TypeError, KeyError):
            return observed("NATIVE_OBSERVATION_PLAN_UNAVAILABLE")
        expected_source = "SOR" if source == "INTEGRATED" else source
        expected_identity = (f"{code}_NX" if source == "NXT" else
                             f"{code}_AL" if source == "INTEGRATED" else code)
        if (requested is None or received is None or active is None
                or any(item["source"] != expected_source
                       or item["market_data_identity"] != expected_identity
                       or item["name"] != requested["name"]
                       for item in (requested, received, active))):
            return observed("NATIVE_OBSERVATION_SOURCE_CHANGED")
        if request_receipt is not None:
            try:
                started, response = request_receipt
                if (not isinstance(started, dict) or not isinstance(response, dict)
                        or started.get("ok") is not True or response.get("ok") is not True
                        or response.get("rows") != rows
                        or response.get("interval") != interval
                        or response.get("request_id") != request_id
                        or response.get("rqname") != request_id
                        or started.get("rqname") != request_id
                        or started.get("request_as_of") != request_as_of.isoformat()
                        or response.get("request_as_of") != request_as_of.isoformat()
                        or response.get("received_at") != received_at.isoformat()
                        or any(item.get("code") != code
                               or item.get("market_source") != source
                               or item.get("market_data_identity") != expected_identity
                               or item.get("display_only") is not True
                               or (item.get("connection_epoch"), item.get("login_session_id")) != identity
                               for item in (started, response))):
                    return observed("NATIVE_OBSERVATION_RECEIPT_INVALID")
            except (TypeError, ValueError):
                return observed("NATIVE_OBSERVATION_RECEIPT_INVALID")
        api = _host_attr(self, "kiwoom_api")
        tick_registration = _host_attr(api, {
            "INTEGRATED": "_v5_al_registration", "NXT": "_nxt_display_registration",
            "KRX": "_realtime_shadow_registration"}[source], None)
        tick_counts = (_host_attr(api, "_v5_al_receive_sequence_by_code", None)
                       if source == "INTEGRATED" else
                       _host_attr(api, "_native_receive_sequence_by_code", {}).get(source))
        tick_sequence_now = tick_counts.get(code, 0) if isinstance(tick_counts, dict) else None
        if tick_anchor_sequence is not None and (
                request_receipt is None
                or type(tick_anchor_sequence) is not int or tick_anchor_sequence < 0
                or not MarketDataHost._native_tick_registration_matches(
                    source, tick_registration, code, identity)
                or type(tick_sequence_now) is not int or tick_sequence_now < 0
                or tick_sequence_now != tick_anchor_sequence):
            return observed("NATIVE_OBSERVATION_TICK_ANCHOR_INVALID")
        key = (code, interval)
        last_receipts = _host_attr(self, "_native_observation_last_receipt_by_stock", None)
        if not isinstance(last_receipts, dict):
            last_receipts = {}
            self._native_observation_last_receipt_by_stock = last_receipts
        prior = last_receipts.get(key)
        if isinstance(prior, dict) and prior.get("session_identity") == identity:
            if (prior.get("request_id") == request_id
                    or request_as_of <= prior["request_as_of"]
                    or received_at <= prior["received_at"]):
                return observed("NATIVE_OBSERVATION_SUPERSEDED")
        forming = None
        completed = []
        for row in normalize_opt10080_rows(rows):
            start = candle_market_datetime(row)
            if start is None or start.date() != current.date():
                continue
            try:
                end = native_bar_end(plan, start, interval)
                row_source = source_for_time(plan, start)
            except (TypeError, ValueError, KeyError):
                continue
            if (end is None or row_source is None
                    or row_source["source"] != expected_source
                    or row_source["market_data_identity"] != expected_identity):
                continue
            values = tuple(row.get(field) for field in ("open", "high", "low", "close", "volume"))
            if (any(type(value) not in (int, float) or not isfinite(value) for value in values)
                    or values[2] <= 0 or values[4] < 0
                    or not values[2] <= values[0] <= values[1]
                    or not values[2] <= values[3] <= values[1]):
                continue
            candle = {field: row[field] for field in ("time", "open", "high", "low", "close", "volume")}
            candle.update(is_complete=end <= request_as_of, native_bar_end=end.isoformat(),
                          market_source=source, market_data_identity=expected_identity,
                          snapshot_request_as_of=request_as_of.isoformat(),
                          snapshot_received_at=received_at.isoformat())
            if end <= request_as_of:
                if source == "INTEGRATED" and interval == 1 and start.hour == 15 and start.minute == 35:
                    try:
                        _calculation, marked = split_native_closing_price_candles(
                            [candle], timeframe_minutes=1,
                            market_data_identity=expected_identity, market_source=source)
                        candle = marked[0]
                    except (TypeError, ValueError, IndexError):
                        continue
                completed.append(candle)
            elif start <= request_as_of <= received_at <= current < end:
                forming = candle
        if forming is None and not completed:
            return observed("NATIVE_OBSERVATION_NO_CURRENT_BAR")
        last_receipts[key] = {"session_identity": identity, "request_id": request_id,
                              "request_as_of": request_as_of, "received_at": received_at}
        forming_cache = _host_attr(self, "_native_observation_forming_by_stock", {})
        if forming is not None:
            forming_cache[key] = {"session_identity": identity, "candle": forming,
                                  "request_id": request_id, "request_as_of": request_as_of,
                                  "received_at": received_at,
                                  "tick_anchor_sequence": tick_anchor_sequence,
                                  "tick_registration": tick_registration,
                                  "tick_last_sequence": tick_anchor_sequence,
                                  "tick_last_market_time": received_at,
                                  "tick_last_cumulative_volume": None}
        elif key in forming_cache:
            forming_cache.pop(key, None)
        complete_cache = _host_attr(self, "_native_observation_completed_by_stock", None)
        if not isinstance(complete_cache, dict):
            complete_cache = {}
            self._native_observation_completed_by_stock = complete_cache
        complete_key = (code, interval, source)
        previous = complete_cache.get(complete_key)
        merged = ({row["time"]: row for row in previous["candles"]}
                  if isinstance(previous, dict) and previous.get("session_identity") == identity
                  else {})
        merged.update({row["time"]: row for row in completed})
        complete_cache[complete_key] = {"session_identity": identity,
                                        "candles": tuple(merged[stamp] for stamp in sorted(merged)[-64:])}
        receipts = _host_attr(self, "_native_observation_receipt_by_stock", None)
        if isinstance(receipts, dict):
            receipts.pop(key, None)
        v5_m1_handoff = (
            source == "INTEGRATED" and interval == 1 and request_receipt is not None
            and any(value == code and
                    _host_attr(self, "_v5_observation_timeframes", {}).get(window_key) == 1
                    for window_key, value in
                    _host_attr(self, "_v5_observation_leases", {}).items()))
        if request_receipt is not None:
            if not isinstance(receipts, dict):
                receipts = {}
                self._native_observation_receipt_by_stock = receipts
            receipts[key] = deepcopy({
                "request_receipt": request_receipt,
                "completed_candles": completed, "forming_candle": forming,
                "stock_code": code, "timeframe_minutes": interval,
                "market_source": source, "market_data_identity": expected_identity,
                "request_id": request_id, "session_identity": identity,
                "watch_generation": _host_attr(self, "_v5_watch_generation", None),
                "tr_forming_candle": forming,
                "tick_anchor_sequence": (None if v5_m1_handoff and forming is None
                                         else tick_anchor_sequence),
                "tick_revision": 0,
            })
        if v5_m1_handoff:
            accepted_times = {row["time"] for row in completed}
            if forming is not None:
                accepted_times.add(forming["time"])
            selected_rows = [row for row in rows if any(
                item["time"] in accepted_times for item in normalize_opt10080_rows([row]))]
            MarketDataHost.install_display_timeframe_candles(
                self, code, 1, selected_rows, as_of=request_as_of,
                market_data_identity=expected_identity, market_source=source,
                display_only=True, request_as_of=request_as_of.isoformat(),
                received_at=received_at.isoformat(), connection_epoch=identity[0],
                login_session_id=identity[1],
            )
        try:
            self.market_data_observed.emit({
                "source": ("NATIVE_SELECTED_FORMING_ACCEPTED" if forming is not None
                           else "NATIVE_SELECTED_COMPLETED_ACCEPTED"),
                "stock_code": code, "timeframe_minutes": interval,
                "market_data_identity": expected_identity,
                "market_source": source, "forming_candle": dict(forming) if forming else None,
                "completed_candles": [dict(row) for row in completed],
                "request_id": request_id,
            })
        except Exception:
            pass  # Optional observers cannot invalidate accepted native evidence.
        observed("NATIVE_OBSERVATION_ACCEPTED", forming=forming,
                 completed_count=len(completed))
        return {"ok": True, "request_count": min(3, DEFAULT_CANDLES_MAX_COUNT),
                "reason_code": "NATIVE_OBSERVATION_ACCEPTED"}

    def native_selected_observation_snapshot(
        self, window_key: object, stock_code: str, timeframe_minutes: int,
        request_id: str,
    ) -> dict[str, object] | None:
        """Return copied request and content evidence for the current V5 lease."""
        if (_host_attr(self, "_v5_observation_leases", {}).get(window_key) != stock_code
                or _host_attr(self, "_v5_observation_timeframes", {}).get(window_key)
                != timeframe_minutes):
            return None
        return MarketDataHost._copy_native_selected_proof(
            self, stock_code, timeframe_minutes, request_id)

    def native_selected_runtime_snapshot(
        self, stock_code: str, timeframe_minutes: int, request_id: str, *,
        consumer: str,
    ) -> dict[str, object] | None:
        """Read selected proof only for an independently active consumer."""
        receipt = _host_attr(self, "_native_observation_receipt_by_stock", {}).get(
            (stock_code, timeframe_minutes))
        source = receipt.get("market_source") if isinstance(receipt, dict) else None
        if (consumer not in {"PRODUCTION", "MOCK"}
                or (stock_code, timeframe_minutes) not in
                MarketDataHost._native_selected_targets(
                    self, consumer=consumer, source=source)):
            return None
        proof = MarketDataHost._copy_native_selected_proof(
            self, stock_code, timeframe_minutes, request_id)
        if (isinstance(proof, dict) and proof.get("market_source") in {"NXT", "KRX"}
                and proof.get("tick_anchor_sequence") is None):
            return None
        return proof

    @staticmethod
    def _native_tick_registration_matches(
        source: str, registration: object, code: str, identity: tuple[int, str],
    ) -> bool:
        if source == "INTEGRATED":
            return (isinstance(registration, dict)
                    and registration.get("identity") == identity
                    and code in registration.get("targets", ()))
        return (getattr(registration, "active", False) is True
                and (getattr(registration, "connection_epoch", None),
                     getattr(registration, "login_session_id", None)) == identity
                and (f"{code}_NX" if source == "NXT" else code) in
                getattr(registration, "broker_code_identities" if source == "NXT"
                        else "target_stock_codes", ()))

    def _copy_native_selected_proof(
        self, stock_code: str, timeframe_minutes: int, request_id: str,
    ) -> dict[str, object] | None:
        receipt = _host_attr(self, "_native_observation_receipt_by_stock", {}).get(
            (stock_code, timeframe_minutes))
        if (not isinstance(receipt, dict) or receipt.get("request_id") != request_id
                or receipt.get("session_identity")
                != MarketDataHost._current_display_session_identity(self)):
            return None
        if (receipt.get("market_source") in {"NXT", "KRX"}
                and receipt.get("tick_anchor_sequence") is not None):
            try:
                now = datetime.now(SEOUL_TIMEZONE)
                plan = read_stock_market_source_plan(PROJECT_ROOT, stock_code, now.date())
                active = source_for_time(plan, now) if plan else None
                if (active is None or active["source"] != receipt["market_source"]
                        or active["market_data_identity"] != receipt.get("market_data_identity")):
                    return None
            except (TypeError, ValueError, KeyError):
                return None
        revision = receipt.get("tick_revision", 0)
        if type(revision) is not int or revision < 0:
            return None
        entry = _host_attr(self, "_native_observation_forming_by_stock", {}).get(
            (stock_code, timeframe_minutes))
        if (receipt.get("market_source") in {"NXT", "KRX"}
                and receipt.get("tr_forming_candle") is not None):
            try:
                tr_bar = receipt["tr_forming_candle"]
                response = receipt["request_receipt"][1]
                raw = next(row for row in normalize_opt10080_rows(response["rows"])
                           if row.get("time") == tr_bar["time"])
                if (not isinstance(entry, dict)
                        or entry.get("candle") != receipt.get("forming_candle")
                        or any(tr_bar[key] != raw[key] for key in
                               ("open", "high", "low", "close", "volume"))):
                    return None
            except (KeyError, TypeError, ValueError, StopIteration):
                return None
        anchor = entry.get("tick_anchor_sequence") if isinstance(entry, dict) else None
        sealed_anchor = receipt.get("tick_anchor_sequence")
        if sealed_anchor is not None and (
                type(sealed_anchor) is not int or sealed_anchor < 0
                or anchor != sealed_anchor):
            return None
        if revision > 0 or sealed_anchor is not None:
            api = _host_attr(self, "kiwoom_api", None)
            source = receipt.get("market_source") or "INTEGRATED"
            latest = (_host_attr(api, "_v5_al_receive_sequence_by_code", {})
                      if source == "INTEGRATED" else
                      _host_attr(api, "_native_receive_sequence_by_code", {}).get(source, {}))
            registration = _host_attr(api, {
                "INTEGRATED": "_v5_al_registration", "NXT": "_nxt_display_registration",
                "KRX": "_realtime_shadow_registration"}.get(source, ""), None)
            expected = revision if revision > 0 else anchor
            if (not isinstance(entry, dict)
                    or entry.get("request_id") != request_id
                    or type(anchor) is not int or anchor < 0
                    or entry.get("tick_last_sequence") != expected
                    or entry.get("tick_registration") is not registration
                    or not MarketDataHost._native_tick_registration_matches(
                        source, registration, stock_code, receipt["session_identity"])
                    or not isinstance(latest, dict)
                    or type(latest.get(stock_code, 0)) is not int
                    or latest.get(stock_code, 0) != expected):
                return None
        return deepcopy(receipt)

    def native_selected_current_snapshot(
        self, stock_code: str, timeframe_minutes: int, *, consumer: str,
    ) -> dict[str, object] | None:
        """Read the latest admitted native current proof for a runtime consumer."""
        receipt = _host_attr(self, "_native_observation_receipt_by_stock", {}).get(
            (stock_code, timeframe_minutes))
        request_id = receipt.get("request_id") if isinstance(receipt, dict) else None
        if not isinstance(request_id, str):
            return None
        return MarketDataHost.native_selected_runtime_snapshot(
            self, stock_code, timeframe_minutes, request_id, consumer=consumer)

    def _extend_native_selected_al_forming(self, payload: dict[str, object]) -> None:
        """Extend a direct selected TR bar only across its registered tick stream."""
        code = payload.get("stock_code") or payload.get("canonical_stock_code")
        if not isinstance(code, str) or not code:
            return
        source = payload.get("market_source")
        source = "INTEGRATED" if source == "SOR" else source
        if source not in {"INTEGRATED", "NXT", "KRX"}:
            return
        data_identity = (f"{code}_AL" if source == "INTEGRATED" else
                         f"{code}_NX" if source == "NXT" else code)
        identity = MarketDataHost._current_display_session_identity(self)
        entries = _host_attr(self, "_native_observation_forming_by_stock", {})
        receipts = _host_attr(self, "_native_observation_receipt_by_stock", {})
        try:
            market_time = datetime.fromisoformat(payload["market_datetime"])
            received = datetime.fromisoformat(payload["received_at"])
            sequence = payload["receive_sequence"] if source == "INTEGRATED" else payload["native_receive_sequence"]
            price = payload["current_price"]
            trade_volume = (payload["trade_volume_abs"] if source != "NXT"
                            else payload["execution_quantity"])
            cumulative = payload["cumulative_volume"]
        except (KeyError, TypeError, ValueError):
            return
        if (identity is None or market_time.utcoffset() != timedelta(hours=9)
                or received.utcoffset() != timedelta(hours=9)
                or type(sequence) is not int or sequence <= 0
                or any(type(value) not in (int, float) or not isfinite(value)
                       for value in (price, trade_volume, cumulative))
                or price <= 0 or trade_volume < 0 or cumulative < trade_volume
                or market_time > received
                or not timedelta(0) <= datetime.now(SEOUL_TIMEZONE) - received
                < timedelta(seconds=30)):
            return
        try:
            plan = read_stock_market_source_plan(PROJECT_ROOT, code, market_time.date())
            if plan is None:
                return
            selected = [source_for_time(plan, instant) for instant in
                        (market_time, received, datetime.now(SEOUL_TIMEZONE))]
        except (TypeError, ValueError, KeyError):
            return
        expected_plan_source = "SOR" if source == "INTEGRATED" else source
        if (any(item is None or item["source"] != expected_plan_source
                or item["market_data_identity"] != data_identity
                or item["name"] != selected[0]["name"] for item in selected)):
            return
        registration = _host_attr(_host_attr(self, "kiwoom_api"), {
            "INTEGRATED": "_v5_al_registration", "NXT": "_nxt_display_registration",
            "KRX": "_realtime_shadow_registration"}[source], None)
        if not MarketDataHost._native_tick_registration_matches(source, registration, code, identity):
            return
        for candle_key in MarketDataHost._native_selected_targets(self, source=source):
            if candle_key[0] != code:
                continue
            entry = entries.get(candle_key)
            proof = receipts.get(candle_key)
            candle = entry.get("candle") if isinstance(entry, dict) else None
            if (not isinstance(proof, dict) or not isinstance(candle, dict)
                    or entry.get("session_identity") != identity
                    or proof.get("session_identity") != identity
                    or proof.get("request_id") != entry.get("request_id")
                    or proof.get("market_source") != source
                    or proof.get("market_data_identity") != data_identity
                    or proof.get("tr_forming_candle") is None
                    or entry.get("tick_registration") is not registration
                    or entry.get("tick_anchor_sequence") is None):
                continue
            try:
                end = datetime.fromisoformat(candle["native_bar_end"])
                tr_received = entry["received_at"]
                previous_time = entry["tick_last_market_time"]
                previous_sequence = entry["tick_last_sequence"]
                previous_cumulative = entry["tick_last_cumulative_volume"]
            except (KeyError, TypeError, ValueError):
                entry["tick_anchor_sequence"] = None
                continue
            if (not tr_received < market_time < end
                    or not previous_time <= market_time
                    or not market_time <= datetime.now(SEOUL_TIMEZONE)
                    or sequence <= previous_sequence):
                continue
            if (sequence != previous_sequence + 1
                    or (previous_cumulative is not None
                        and cumulative - previous_cumulative != trade_volume)):
                entry["tick_anchor_sequence"] = None
                continue
            updated = dict(candle)
            updated.update(high=max(candle["high"], price),
                           low=min(candle["low"], price), close=price,
                           volume=candle["volume"] + trade_volume,
                            snapshot_source=f"BROKER_{'AL' if source == 'INTEGRATED' else source}_TR_PLUS_CONTIGUOUS_TICKS",
                           tick_received_at=received.isoformat(),
                           tick_receive_sequence=sequence)
            entry.update(candle=updated, tick_last_sequence=sequence,
                         tick_last_market_time=market_time,
                         tick_last_cumulative_volume=cumulative)
            proof["forming_candle"] = dict(updated)
            proof["tick_revision"] = sequence
            self.market_data_observed.emit({
                "source": "NATIVE_SELECTED_TICK_ACCEPTED", "stock_code": code,
                "timeframe_minutes": candle_key[1], "market_source": source,
                "market_data_identity": data_identity, "request_id": proof["request_id"],
                "bar_time": candle_market_datetime(updated).isoformat(),
                "observed_at": received.isoformat(),
                "connection_epoch": identity[0], "login_session_id": identity[1],
                "forming_candle": dict(updated), "completed_candles":
                [dict(row) for row in proof["completed_candles"]],
                "tick_revision": sequence,
            })

    def _arm_sor_diagnostic_idle_timer(self, now: datetime | None = None) -> None:
        timer = _host_attr(self, "_sor_diagnostic_idle_timer")
        if timer is None:
            return
        if (self._shutting_down
                or not _host_attr(self, "_sor_diagnostic_idle_required", False)
                or "005930" not in _host_attr(self, "_sor_m1_required_by_stock", {})
                or "005930" not in _host_attr(self, "_candle_standby_stock_codes", ())):
            timer.stop()
            return
        current = now or datetime.now(SEOUL_TIMEZONE)
        current = current.astimezone(SEOUL_TIMEZONE)
        next_at = current.replace(second=10, microsecond=0)
        if next_at <= current:
            next_at += timedelta(minutes=1)
        timer.start(max(1, int((next_at - current).total_seconds() * 1000)))

    def _on_sor_diagnostic_idle_timeout(self, now: datetime | None = None) -> None:
        current = (now or datetime.now(SEOUL_TIMEZONE)).astimezone(SEOUL_TIMEZONE)
        MarketDataHost._arm_sor_diagnostic_idle_timer(self, current)
        code = "005930"
        if (self._shutting_down
                or not _host_attr(self, "_sor_diagnostic_idle_required", False)
                or code not in _host_attr(self, "_sor_m1_required_by_stock", {})
                or code not in _host_attr(self, "_candle_standby_stock_codes", ())
                or MarketDataHost._display_session_for_time(current)
                not in {"NXT_PRE", "KRX", "NXT_AFTER"}
                or _host_attr(self, "_automatic_candle_refresh_inflight", False)
                or self._sor_diagnostic_idle_inflight):
            return
        operation_timer_getter = getattr(self._owner, "operation_timer", None)
        operation_timer = (
            operation_timer_getter()
            if callable(operation_timer_getter)
            else None
        )
        if operation_timer is None or operation_timer.isActive():
            return
        identity = MarketDataHost._current_display_session_identity(self)
        if identity is None or code not in MarketDataHost._nxt_eligible_targets((code,)):
            return
        minute = current.replace(second=0, microsecond=0)
        minute_key = minute.strftime("%Y-%m-%d %H:%M")
        if self._sor_diagnostic_idle_last_attempt_minute == minute_key:
            return
        forming = _host_attr(self, "_sor_forming_m1_by_stock", {}).get(code)
        if (isinstance(forming, dict)
                and forming.get("session_identity") == identity
                and isinstance(forming.get("candle"), dict)
                and candle_market_datetime(forming["candle"]) == minute):
            return
        try:
            specs = MarketDataHost.display_timeframe_refresh_specs(self, code, minute_key)
            selected = next((spec for spec in specs
                             if spec.get("timeframe_minutes") == 1
                             and spec.get("nxt_available") is True), None)
            if selected is None:
                return
            request_count = int(selected["request_count"])
        except Exception:
            return
        self._sor_diagnostic_idle_last_attempt_minute = minute_key
        self._sor_diagnostic_idle_inflight = True
        self._sor_diagnostic_idle_request_generation += 1
        generation = self._sor_diagnostic_idle_request_generation

        def terminal(_result: object) -> None:
            if generation == self._sor_diagnostic_idle_request_generation:
                self._sor_diagnostic_idle_inflight = False

        try:
            from auto_candle_refresh import request_display_timeframe_candles_for_stock

            request_display_timeframe_candles_for_stock(
                window=self, code=code, name="",
                operation_cycle_minute_key=minute_key,
                timeframe_minutes=1, count=request_count,
                nxt_available=True, on_terminal=terminal,
            )
        except Exception:
            terminal(None)

    def _current_display_candles(
        self, stock_code: str, timeframe_minutes: int
    ) -> list[dict[str, Any]]:
        cache = _host_attr(self, "_display_timeframe_candles", {})
        identities = _host_attr(self, "_display_timeframe_session_identity", {})
        key = (stock_code, timeframe_minutes)
        if (not isinstance(cache, dict) or not cache.get(key)
                or not isinstance(identities, dict)):
            return []
        current = MarketDataHost._current_display_session_identity(self)
        if current is None or identities.get(key) != current:
            return []
        return list(cache.get(key, ()))

    def install_display_timeframe_candles(
        self,
        stock_code: object,
        timeframe_minutes: object,
        rows: object,
        *,
        as_of: datetime | None = None,
        market_data_identity: object = "",
        market_source: object = "",
        display_only: object = False,
        request_as_of: object = None,
        received_at: object = None,
        connection_epoch: object = None,
        login_session_id: object = "",
    ) -> dict[str, object]:
        code = str(stock_code or "").strip()
        session_identity = MarketDataHost._current_display_session_identity(self)
        try:
            interval = int(timeframe_minutes)
        except (TypeError, ValueError):
            interval = 0
        if (
            _host_attr(self, "_shutting_down", False)
            or not is_broker_action_stock_code(code)
            or interval <= 0
            or not isinstance(rows, list)
            or str(market_data_identity or "").strip() != f"{code}_AL"
            or str(market_source or "").strip() != "INTEGRATED"
            or display_only is not True
            or session_identity is None
            or type(connection_epoch) is not int
            or connection_epoch <= 0
            or not isinstance(login_session_id, str)
            or not login_session_id
            or (connection_epoch, login_session_id) != session_identity
            or code not in MarketDataHost._nxt_eligible_targets((code,))
        ):
            return {
                "ok": False,
                "reason_code": "DISPLAY_TIMEFRAME_EVIDENCE_INVALID",
            }

        current = as_of or datetime.now(SEOUL_TIMEZONE)
        current = (
            current.replace(tzinfo=SEOUL_TIMEZONE)
            if current.tzinfo is None
            else current.astimezone(SEOUL_TIMEZONE)
        )
        complete_by_time: dict[datetime, dict[str, Any]] = {}
        forming_cutoff = None
        if interval == 1 and isinstance(request_as_of, str) and isinstance(received_at, str):
            try:
                requested = datetime.fromisoformat(request_as_of)
                received = datetime.fromisoformat(received_at)
                if (requested.utcoffset() == received.utcoffset() == timedelta(hours=9)
                        and requested <= received
                        and received - requested < timedelta(minutes=1)
                        and requested.replace(second=0, microsecond=0)
                        == received.replace(second=0, microsecond=0)
                        == current.replace(second=0, microsecond=0)):
                    forming_cutoff = received
            except (TypeError, ValueError, OverflowError):
                pass
        forming_snapshot = None
        for candle in normalize_opt10080_rows(rows):
            bar_time = candle_market_datetime(candle)
            if bar_time is None:
                continue
            session = MarketDataHost._display_session_for_time(bar_time)
            if not session:
                continue
            session_end_minute = (
                15 * 60 + 40 if session == "AL_TRANSITION" else next(
                    end
                    for name, _start, end in _DISPLAY_INTEGRATED_SESSIONS
                    if name == session
                )
            )
            session_end = bar_time.replace(
                hour=session_end_minute // 60,
                minute=session_end_minute % 60,
                second=0,
                microsecond=0,
            )
            complete_at = min(
                bar_time + timedelta(minutes=interval),
                session_end,
            )
            if bar_time.date() >= current.date() and current < complete_at:
                if (forming_cutoff is not None and interval == 1
                        and bar_time == forming_cutoff.replace(second=0, microsecond=0)
                        and bar_time == current.replace(second=0, microsecond=0)):
                    prices = tuple(candle.get(field) for field in
                                   ("open", "high", "low", "close", "volume"))
                    if (all(type(value) in (int, float) and isfinite(value)
                            for value in prices)
                            and prices[2] > 0 and prices[4] >= 0
                            and prices[2] <= prices[0] <= prices[1]
                            and prices[2] <= prices[3] <= prices[1]):
                        forming_snapshot = {
                            "bar_time": bar_time.isoformat(timespec="seconds"),
                            "open": prices[0], "high": prices[1],
                            "low": prices[2], "close": prices[3],
                            "volume": prices[4], "timeframe_minutes": 1,
                            "is_complete": False, "trade_date": bar_time.date().isoformat(),
                            "session": session,
                            "display_only": True,
                            "market_data_identity": f"{code}_AL",
                            "market_source": "INTEGRATED",
                            "snapshot_source": "BROKER_AL_TR_FORMING_M1",
                            "snapshot_request_as_of": request_as_of,
                            "snapshot_received_at": received_at,
                        }
                continue
            complete_by_time[bar_time] = {
                "bar_time": bar_time.isoformat(timespec="seconds"),
                "open": candle.get("open"),
                "high": candle.get("high"),
                "low": candle.get("low"),
                "close": candle.get("close"),
                "volume": candle.get("volume"),
                "timeframe_minutes": interval,
                "is_complete": True,
                "trade_date": bar_time.date().isoformat(),
                "session": session,
                "display_only": True,
                "market_data_identity": f"{code}_AL",
                "market_source": "INTEGRATED",
            }
            if interval == 1 and bar_time.hour == 15 and bar_time.minute == 35:
                _, marked = split_native_closing_price_candles(
                    [complete_by_time[bar_time]], timeframe_minutes=1,
                    market_data_identity=f"{code}_AL", market_source="INTEGRATED",
                )
                complete_by_time[bar_time] = marked[0]

        cache = _host_attr(self, "_display_timeframe_candles", None)
        if not isinstance(cache, dict):
            cache = {}
            object.__setattr__(self, "_display_timeframe_candles", cache)
        for candle in MarketDataHost._current_display_candles(self, code, interval):
            bar_time = candle_market_datetime(candle)
            if bar_time is not None:
                complete_by_time.setdefault(bar_time, dict(candle))
        routine_required_count = MarketDataHost.direct_timeframe_requirements(
            self,
            code,
        ).get(interval, 0)
        use_declared_m1 = interval == 1 and routine_required_count > 0
        required_count = routine_required_count if use_declared_m1 else max(
            routine_required_count,
            MarketDataHost._full_day_display_count(interval),
        )
        ordered = [complete_by_time[key] for key in sorted(complete_by_time)]
        if required_count > 0:
            ordered = ordered[-required_count:]
        if MarketDataHost._current_display_session_identity(self) != session_identity:
            return {"ok": False, "reason_code": "DISPLAY_TIMEFRAME_EVIDENCE_INVALID"}
        identities = _host_attr(self, "_display_timeframe_session_identity", None)
        if not isinstance(identities, dict):
            identities = {}
            object.__setattr__(self, "_display_timeframe_session_identity", identities)
        cache[(code, interval)] = tuple(ordered)
        identities[(code, interval)] = (connection_epoch, login_session_id)
        if ordered and code in _host_attr(self, "_monitoring_target_stock_codes", ()):
            last = ordered[-1]
            try:
                bar_time = datetime.fromisoformat(last["bar_time"])
                session_end = next(end for name, _start, end in
                                   _DISPLAY_INTEGRATED_SESSIONS
                                   if name == last["session"])
                completed_at = min(bar_time + timedelta(minutes=interval),
                                   bar_time.replace(hour=session_end // 60,
                                                    minute=session_end % 60,
                                                    second=0, microsecond=0))
                if (last.get("is_complete") is True
                        and timedelta(0) <= current - completed_at <= timedelta(seconds=30)):
                    self.direct_sor_completed.emit({
                        "stock_code": code, "timeframe_minutes": interval,
                        "bar_time": last["bar_time"],
                        "connection_epoch": connection_epoch,
                        "login_session_id": login_session_id,
                        "observed_at": current.isoformat(),
                    })
            except Exception:
                pass  # Optional evidence cannot interrupt accepted market data.
        if interval == 1:
            MarketDataHost._v5_al_consider_parity(self, code, ordered)
            forming_cache = _host_attr(self, "_sor_forming_m1_by_stock", None)
            if not isinstance(forming_cache, dict):
                forming_cache = {}
                object.__setattr__(self, "_sor_forming_m1_by_stock", forming_cache)
            if forming_snapshot is None:
                forming_cache.pop(code, None)
            else:
                forming_cache[code] = {
                    "session_identity": session_identity,
                    "candle": forming_snapshot,
                }
                MarketDataHost._v5_al_admit_forming(self, code)
                if ordered:
                    sampler = _host_attr(self, "_sor_tick_evidence", None)
                    if sampler is not None:
                        try:
                            sampler.capture_accepted_al_m1(
                                code, forming_snapshot,
                                connection_epoch=connection_epoch,
                                login_session_id=login_session_id,
                                completed_count=len(ordered),
                            )
                        except Exception as exc:
                            try:
                                sampler.note_capture_failure(exc)
                            except Exception:
                                pass  # Telemetry cannot interrupt accepted market data.
                    observed = _host_attr(self, "market_data_observed", None)
                    emit = getattr(observed, "emit", None)
                    if callable(emit):
                        try:
                            emit({
                                "source": "SOR_AL_M1_SNAPSHOT_ACCEPTED",
                                "stock_code": code,
                                "market_data_identity": f"{code}_AL",
                                "connection_epoch": connection_epoch,
                                "login_session_id": login_session_id,
                                "snapshot_received_at": forming_snapshot["snapshot_received_at"],
                                "forming_minute": forming_snapshot["bar_time"],
                            })
                        except Exception:
                            pass  # Optional observers cannot interrupt accepted market data.
        return {
            "ok": bool(ordered),
            "reason_code": (
                "DISPLAY_TIMEFRAME_READY"
                if ordered
                else "DISPLAY_TIMEFRAME_EMPTY"
            ),
            "stock_code": code,
            "timeframe_minutes": interval,
            "stored_count": len(ordered),
            "display_only": True,
            "market_data_identity": f"{code}_AL",
            "market_source": "INTEGRATED",
        }

    def _display_timeframe_projection(
        self,
        stock_code: str,
        timeframe_minutes: int,
    ) -> dict[str, Any]:
        if stock_code not in MarketDataHost._nxt_eligible_targets((stock_code,)):
            return {"display_candles": [], "display_metadata": {"available": False}}
        rows = MarketDataHost._current_display_candles(
            self, stock_code, timeframe_minutes
        )
        return {
            "display_candles": [dict(item) for item in rows],
            "display_metadata": {
                "available": bool(rows),
                "display_only": True,
                "market_data_identity": f"{stock_code}_AL",
                "market_source": "INTEGRATED",
                "timeframe_minutes": timeframe_minutes,
                "stored_count": len(rows),
            },
        }

    def sync_candle_observation_targets(self, stock_codes: object) -> dict[str, object]:
        """Keep non-executing Candle consumers in the existing Shadow pipeline."""
        previous_native_targets = MarketDataHost._native_selected_targets(self)
        previous_codes = tuple(self._candle_observation_stock_codes)
        previous_requirements = dict(self._candle_observation_required_by_stock)
        previous_timeframe_requirements = {
            code: dict(values)
            for code, values in getattr(
                self,
                "_candle_observation_timeframe_requirements_by_stock",
                {},
            ).items()
            if isinstance(values, dict)
        }
        previous_sor_m1 = dict(_host_attr(self, "_sor_observation_m1_required_by_stock", {}))
        candidates = stock_codes if isinstance(stock_codes, (list, tuple, set, frozenset)) else ()
        requested: dict[str, int] = {}
        timeframe_requested: dict[str, dict[int, int]] = {}
        sor_m1_required: dict[str, int] = {}
        sor_selected_frames: set[tuple[str, int]] = set()
        krx_selected_frames: set[tuple[str, int]] = set()
        normalized_codes: set[str] = set()
        for item in candidates:
            if isinstance(item, dict):
                code = str(item.get("stock_code") or "").strip()
                if not code:
                    continue
                source = str(item.get("calculation_market_source", "")).strip().upper()
                if source not in {"SOR", "KRX"}:
                    continue
                try:
                    request = validate_market_bar_projection_request(
                        item.get("projection_request"),
                        require_warmup=True,
                    )
                    interval, direct_bars, minute_source_count = (
                        MarketDataHost._direct_timeframe_requirement(
                            item.get("rules"),
                            request,
                        )
                    )
                    requested[code] = max(
                        requested.get(code, 0),
                        minute_source_count,
                    )
                    MarketDataHost._merge_timeframe_requirement(
                        timeframe_requested,
                        code,
                        interval,
                        direct_bars,
                    )
                    if source == "SOR":
                        sor_selected_frames.add((code, interval))
                        if interval == 1:
                            sor_m1_required[code] = max(
                                sor_m1_required.get(code, 0),
                                min(minute_source_count, DEFAULT_CANDLES_MAX_COUNT),
                            )
                    else:
                        krx_selected_frames.add((code, interval))
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
        self._candle_observation_timeframe_requirements_by_stock = (
            timeframe_requested
        )
        self._sor_observation_m1_required_by_stock = sor_m1_required
        self._native_selected_sor_observation_frames = sor_selected_frames
        self._native_selected_krx_observation_frames = krx_selected_frames
        forming_cache = _host_attr(self, "_sor_forming_m1_by_stock", None)
        standby_sor_m1 = _host_attr(self, "_sor_standby_m1_required_by_stock", {})
        v5_codes = set(_host_attr(self, "_v5_observation_leases", {}).values())
        if isinstance(forming_cache, dict):
            for code in previous_sor_m1.keys() - (sor_m1_required.keys() | standby_sor_m1.keys() | v5_codes):
                forming_cache.pop(code, None)
        self._sor_m1_required_by_stock = dict(
            standby_sor_m1
        )
        for code, count in sor_m1_required.items():
            self._sor_m1_required_by_stock[code] = max(
                self._sor_m1_required_by_stock.get(code, 0), count
            )
        for code in v5_codes:
            count = MarketDataHost._v5_m1_requirement(self, code)
            if count:
                self._sor_m1_required_by_stock[code] = max(
                    self._sor_m1_required_by_stock.get(code, 0), count,
                )
        targets = tuple(
            sorted(
                set(self._production_monitoring_stock_codes)
                | set(self._execution_shadow_stock_codes)
                | set(self._candle_observation_stock_codes)
                | v5_codes
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
                or previous_timeframe_requirements != timeframe_requested
                or previous_sor_m1 != sor_m1_required
            )
            if changed or getattr(self, "_last_candle_observation_refresh_minute", "") != minute_key:
                self._last_candle_observation_refresh_minute = minute_key
                QTimer.singleShot(0, lambda: self.refresh_operation_candles(minute_key))
        else:
            self._last_candle_observation_refresh_minute = ""
        MarketDataHost._update_native_selected_targets(self, previous_native_targets)
        return result

    @staticmethod
    def _v5_al_closed_minute_parity(
        native: object, broker: object, identity: tuple[int, str],
    ) -> bool:
        if not isinstance(native, dict) or not isinstance(broker, dict):
            return False
        code = native.get("stock_code")
        if (not isinstance(code, str) or len(code) != 6 or not code.isdigit()
                or native.get("identity") != identity
                or native.get("continuous") is not True
                or native.get("tick_count", 0) <= 0
                or native.get("last_sequence", 0) - native.get("first_sequence", 0) + 1
                != native.get("tick_count")
                or broker.get("market_data_identity") != f"{code}_AL"
                or broker.get("market_source") != "INTEGRATED"
                or broker.get("is_complete") is not True
                or broker.get("bar_time") != native.get("minute")):
            return False
        return all(
            type(native.get(field)) in (int, float)
            and type(broker.get(field)) in (int, float)
            and isfinite(native[field]) and isfinite(broker[field])
            and native[field] == broker[field]
            for field in ("open", "high", "low", "close")
        )

    def _on_v5_al_tick_received(self, payload: object) -> None:
        if not isinstance(payload, dict) or self._shutting_down:
            return
        code = payload.get("stock_code")
        identity = self._current_display_session_identity()
        if (not isinstance(code, str) or len(code) != 6 or not code.isdigit()
                or code not in ({item for item, _frame in
                                 MarketDataHost._native_selected_targets(self)}
                                | set(self._mini_chart_observation_leases.values()))
                or payload.get("broker_market_identity") != f"{code}_AL"
                or payload.get("market_source") != "SOR"
                or payload.get("v5_only") is not True
                or identity is None
                or (payload.get("connection_epoch"), payload.get("login_session_id")) != identity):
            return
        try:
            market_time = datetime.fromisoformat(payload["market_datetime"])
            received = datetime.fromisoformat(payload["received_at"])
            price = payload["current_price"]
            sequence = payload["receive_sequence"]
        except (KeyError, TypeError, ValueError):
            return
        if (market_time.utcoffset() != timedelta(hours=9)
                or received.utcoffset() != timedelta(hours=9)
                or abs((received - market_time).total_seconds()) > 60
                or abs((datetime.now(SEOUL_TIMEZONE) - received).total_seconds()) > 60
                or type(price) not in (int, float) or not isfinite(price) or price <= 0
                or type(sequence) is not int or sequence <= 0
                or MarketDataHost._display_session_for_time(market_time) not in
                {"NXT_PRE", "KRX", "KRX_CLOSING_AUCTION", "AL_TRANSITION", "NXT_AFTER"}):
            return
        if code in self._mini_chart_observation_leases.values():
            previous = self._sor_display_live_price_states.get(code)
            if (previous is None or (
                    sequence > previous.receive_sequence
                    and market_time.isoformat() >= previous.last_market_datetime)):
                self._sor_display_live_price_states[code] = SorDisplayLivePriceState(
                    canonical_stock_code=code,
                    broker_code_identity=f"{code}_AL", market_source="SOR",
                    connection_epoch=identity[0], login_session_id=identity[1],
                    last_market_datetime=market_time.isoformat(), last_price=price,
                    receive_sequence=sequence, data_quality=HIGH_RESOLUTION_DATA_NORMAL,
                )
        MarketDataHost._extend_native_selected_al_forming(self, payload)
        if code not in self._v5_observation_leases.values():
            return  # Runtime selected ticks do not start V5 M1 parity polling.
        minute = market_time.replace(second=0, microsecond=0)
        native = self._v5_al_native_minutes.setdefault(code, {})
        current = native.get("current")
        if isinstance(current, dict) and current.get("identity") != identity:
            native.clear()
            current = None
            self._v5_al_parity.pop(code, None)
        if isinstance(current, dict):
            previous_minute = datetime.fromisoformat(current["minute"])
            if minute < previous_minute or sequence <= current["last_sequence"]:
                self._v5_al_parity.pop(code, None)
                return
            if minute > previous_minute:
                adjacent = (minute - previous_minute == timedelta(minutes=1)
                            and current["last_sequence"] + 1 == sequence)
                native["closed"] = current if adjacent else None
                self._v5_al_parity.pop(code, None)
                current = None
        if current is None:
            previous = native.get("closed")
            continuous = bool(isinstance(previous, dict)
                              and datetime.fromisoformat(previous["minute"])
                              + timedelta(minutes=1) == minute)
            current = {"stock_code": code, "identity": identity,
                       "minute": minute.isoformat(), "open": price,
                       "high": price, "low": price, "close": price,
                       "first_sequence": sequence, "last_sequence": sequence,
                       "tick_count": 1, "continuous": continuous,
                       "last_received_at": received.isoformat()}
            native["current"] = current
        else:
            current["high"] = max(current["high"], price)
            current["low"] = min(current["low"], price)
            current["close"] = price
            current["continuous"] = bool(current["continuous"] and
                                         sequence == current["last_sequence"] + 1)
            current["last_sequence"] = sequence
            current["tick_count"] += 1
            current["last_received_at"] = received.isoformat()
        if self._v5_al_parity.get(code) == identity:
            MarketDataHost._v5_al_admit_forming(self, code)

    def _v5_al_consider_parity(self, code: str, rows: list[dict[str, Any]]) -> None:
        leases = _host_attr(self, "_v5_observation_leases", {})
        if not isinstance(leases, dict) or code not in leases.values():
            return
        identity = self._current_display_session_identity()
        native = self._v5_al_native_minutes.get(code, {})
        closed = native.get("closed")
        if not isinstance(closed, dict) or identity is None:
            return
        minute = datetime.fromisoformat(closed["minute"])
        if not (timedelta(minutes=1) <= datetime.now(SEOUL_TIMEZONE) - minute
                < timedelta(minutes=3)):
            self._v5_al_parity.pop(code, None)
            return
        broker = next((row for row in rows if row.get("bar_time") == closed["minute"]), None)
        if MarketDataHost._v5_al_closed_minute_parity(closed, broker, identity):
            self._v5_al_parity[code] = identity
        else:
            self._v5_al_parity.pop(code, None)

    def _v5_al_admit_forming(self, code: str) -> None:
        identity = MarketDataHost._current_display_session_identity(self)
        parity = _host_attr(self, "_v5_al_parity", {})
        leases = _host_attr(self, "_v5_observation_leases", {})
        if (not isinstance(parity, dict) or parity.get(code) != identity
                or not isinstance(leases, dict) or code not in leases.values()):
            return
        native = self._v5_al_native_minutes.get(code, {}).get("current")
        sealed = self._sor_forming_m1_by_stock.get(code)
        candle = sealed.get("candle") if isinstance(sealed, dict) else None
        if (not isinstance(native, dict) or not isinstance(candle, dict)
                or sealed.get("session_identity") != identity
                or candle.get("bar_time") != native.get("minute")
                or candle.get("market_data_identity") != f"{code}_AL"
                or candle.get("market_source") != "INTEGRATED"
                or candle.get("display_only") is not True
                or candle.get("is_complete") is not False
                or candle.get("timeframe_minutes") != 1
                or candle.get("snapshot_source") not in
                {"BROKER_AL_TR_FORMING_M1", "BROKER_AL_PARITY_NATIVE_FORMING_M1"}):
            return
        received = datetime.fromisoformat(native["last_received_at"])
        try:
            tr_received = datetime.fromisoformat(candle.get("tr_received_at",
                                                           candle["snapshot_received_at"]))
        except (TypeError, ValueError, KeyError):
            return
        if received < tr_received or received.replace(second=0, microsecond=0) != datetime.fromisoformat(native["minute"]):
            return
        bucket = (received.minute, received.second // 10)
        if (candle.get("snapshot_source") == "BROKER_AL_PARITY_NATIVE_FORMING_M1"
                and candle.get("last_admitted_bucket") == bucket):
            return
        updated = dict(candle)
        updated.update({
            "open": candle.get("tr_open", candle["open"]),
            "high": max(candle["high"], native["high"]),
            "low": min(candle["low"], native["low"]),
            "close": native["close"],
            "volume": candle.get("tr_volume", candle["volume"]),
            "tr_open": candle.get("tr_open", candle["open"]),
            "tr_volume": candle.get("tr_volume", candle["volume"]),
            "tr_received_at": tr_received.isoformat(),
            "volume_provenance": "LAST_BROKER_AL_TR_SNAPSHOT",
            "snapshot_source": "BROKER_AL_PARITY_NATIVE_FORMING_M1",
            "snapshot_request_as_of": received.isoformat(),
            "snapshot_received_at": received.isoformat(),
            "last_admitted_bucket": bucket,
        })
        sealed["candle"] = updated
        self.market_data_observed.emit({
            "source": "SOR_AL_NATIVE_M1_PARITY_ACCEPTED", "stock_code": code,
            "market_data_identity": f"{code}_AL", "connection_epoch": identity[0],
            "login_session_id": identity[1], "snapshot_received_at": updated["snapshot_received_at"],
            "forming_minute": updated["bar_time"],
        })

    def _v5_m1_requirement(self, code: str) -> int:
        matches = [key for key, value in self._v5_observation_leases.items() if value == code]
        explicit = _host_attr(self, "_v5_observation_timeframes", {})
        if any(key not in explicit for key in matches):
            return MarketDataHost._full_day_display_count(1)  # Legacy caller.
        return 3 if any(explicit[key] == 1 for key in matches) else 0

    def _native_selected_targets(self, *, consumer: str | None = None,
                                 source: str | None = None) -> set[tuple[str, int]]:
        """Selected frames backed by a real V5 lease or a validated SOR requirement."""
        targets: set[tuple[str, int]] = set()
        if consumer in (None, "V5"):
            frames = _host_attr(self, "_v5_observation_timeframes", {})
            targets.update((code, frames[key]) for key, code in
                           _host_attr(self, "_v5_observation_leases", {}).items()
                           if type(frames.get(key)) is int and frames[key] > 0)
        for kind, requirements, sources, active in (
            ("PRODUCTION", "_candle_standby_timeframe_requirements_by_stock",
             "_native_selected_sor_standby_frames", "_candle_standby_stock_codes"),
            ("MOCK", "_candle_observation_timeframe_requirements_by_stock",
             "_native_selected_sor_observation_frames", "_candle_observation_stock_codes"),
        ):
            if consumer not in (None, kind):
                continue
            sor_codes = _host_attr(self, sources, set())
            krx_codes = _host_attr(self, sources.replace("_sor_", "_krx_"), set())
            allowed = (sor_codes | krx_codes if source is None else
                       krx_codes if source == "KRX" else sor_codes)
            by_stock = _host_attr(self, requirements, {})
            active_codes = _host_attr(self, active, ())
            if not isinstance(sor_codes, (set, frozenset)) or not isinstance(by_stock, dict):
                continue
            for code in active_codes:
                frames = by_stock.get(code)
                if not isinstance(frames, dict):
                    continue
                targets.update((code, interval) for interval, count in frames.items()
                               if (code, interval) in allowed
                               if type(interval) is int and 0 < interval <= 240
                               and type(count) is int and count > 0)
        return {(code, interval) for code, interval in targets
                if isinstance(code, str) and len(code) == 6 and code.isascii()
                and code.isdigit()}

    def _update_native_selected_targets(self, previous: set[tuple[str, int]]) -> None:
        current = MarketDataHost._native_selected_targets(self)
        if current == previous:
            return
        for key in previous - current:
            for cache_name in ("_native_observation_forming_by_stock",
                               "_native_observation_last_receipt_by_stock",
                               "_native_observation_receipt_by_stock"):
                cache = _host_attr(self, cache_name, None)
                if isinstance(cache, dict):
                    cache.pop(key, None)
            completed = _host_attr(self, "_native_observation_completed_by_stock", None)
            if isinstance(completed, dict):
                for complete_key in tuple(completed):
                    if complete_key[:2] == key:
                        completed.pop(complete_key, None)
        attempts = _host_attr(self, "_v5_native_last_attempt", None)
        if isinstance(attempts, dict):
            for key in tuple(attempts):
                if key[:2] not in current:
                    attempts.pop(key, None)
        self._v5_watch_generation = _host_attr(self, "_v5_watch_generation", 0) + 1
        self._v5_watch_inflight = False
        if current and hasattr(self, "_shutting_down"):
            MarketDataHost._schedule_v5_observation_watch(self, immediate=not previous)
            if previous:
                QTimer.singleShot(0, lambda: MarketDataHost._refresh_v5_observation(self))

    def set_v5_observation_lease(
        self, window_key: object, stock_code: object, *, timeframe_minutes: object = None,
    ) -> dict[str, object]:
        """Watch one V5 window's stock through this host without replacing other consumers."""
        leases = self._v5_observation_leases
        code = str(stock_code or "").strip()
        if code and (not is_broker_action_stock_code(code)
                     or len(code) != 6 or not code.isascii() or not code.isdigit()):
            return {"ok": False, "changed": False, "reason_code": "V5_AL_CODE_INVALID"}
        if timeframe_minutes is not None and (type(timeframe_minutes) is not int
                or timeframe_minutes < 0 or timeframe_minutes > 240):
            return {"ok": False, "changed": False, "reason_code": "V5_TIMEFRAME_INVALID"}
        previous_targets = MarketDataHost._native_selected_targets(self)
        previous_codes = set(leases.values())
        previous_interval = self._v5_observation_timeframes.get(window_key)
        if code:
            leases[window_key] = code
            if timeframe_minutes is not None:
                self._v5_observation_timeframes[window_key] = timeframe_minutes
            else:
                self._v5_observation_timeframes.pop(window_key, None)
        else:
            leases.pop(window_key, None)
            self._v5_observation_timeframes.pop(window_key, None)
        current_codes = set(leases.values())
        if current_codes == previous_codes and previous_interval == self._v5_observation_timeframes.get(window_key):
            sync_v5 = getattr(self.kiwoom_api, "sync_v5_al_realtime_targets", None)
            if callable(sync_v5):
                sync_v5(self._al_realtime_target_codes())
            return {"ok": True, "changed": False}
        standby = _host_attr(self, "_sor_standby_m1_required_by_stock", {})
        observation = _host_attr(self, "_sor_observation_m1_required_by_stock", {})
        self._sor_m1_required_by_stock = dict(standby)
        for source in (observation, {item: MarketDataHost._v5_m1_requirement(self, item)
                                     for item in current_codes}):
            for item, count in source.items():
                if count:
                    self._sor_m1_required_by_stock[item] = max(
                    self._sor_m1_required_by_stock.get(item, 0), count
                    )
        forming = _host_attr(self, "_sor_forming_m1_by_stock", None)
        if isinstance(forming, dict):
            for removed in previous_codes - current_codes - set(standby) - set(observation):
                forming.pop(removed, None)
        for removed in previous_codes - current_codes - {
                code for code, _interval in MarketDataHost._native_selected_targets(self)}:
            self._v5_al_native_minutes.pop(removed, None)
            self._v5_al_parity.pop(removed, None)
        result = self.sync_monitoring_targets(self._production_monitoring_stock_codes)
        sync_v5 = getattr(self.kiwoom_api, "sync_v5_al_realtime_targets", None)
        if callable(sync_v5):
            v5_result = sync_v5(self._al_realtime_target_codes())
            if isinstance(result, dict):
                result = dict(result, v5_al_sync=v5_result)
        if isinstance(result, dict) and result.get("ok") is True:
            MarketDataHost._update_native_selected_targets(self, previous_targets)
        return result

    def _al_realtime_target_codes(self) -> tuple[str, ...]:
        return MarketDataHost._nxt_eligible_targets(
            {code for code, _interval in MarketDataHost._native_selected_targets(self)}
            | {code for key, code in _host_attr(self, "_v5_observation_leases", {}).items()
               if key not in _host_attr(self, "_v5_observation_timeframes", {})}
            | set(_host_attr(self, "_mini_chart_observation_leases", {}).values())
        )

    def set_mini_chart_observation_lease(
        self, window_key: object, stock_code: object,
    ) -> dict[str, object]:
        """Share the _AL screen and monitoring targets across live mini windows."""
        code = str(stock_code or "").strip()
        if code and (not is_broker_action_stock_code(code)
                     or len(code) != 6 or not code.isascii() or not code.isdigit()):
            return {"ok": False, "reason_code": "MINI_CHART_CODE_INVALID"}
        leases = self._mini_chart_observation_leases
        previous = leases.get(window_key)
        if code:
            leases[window_key] = code
        else:
            leases.pop(window_key, None)
        if previous and previous not in leases.values():
            self._sor_display_live_price_states.pop(previous, None)
        return self.sync_monitoring_targets(self._production_monitoring_stock_codes)

    def sor_display_live_price_state(
        self, stock_code: str,
    ) -> SorDisplayLivePriceState | None:
        code = str(stock_code or "").strip()
        state = self._sor_display_live_price_states.get(code)
        if (state is None or code not in self._mini_chart_observation_leases.values()
                or (state.connection_epoch, state.login_session_id)
                != self._current_display_session_identity()):
            return None
        return state

    def _schedule_v5_observation_watch(self, *, immediate: bool = False) -> None:
        if (self._shutting_down
                or not MarketDataHost._native_selected_targets(self)
                and not any(key not in self._v5_observation_timeframes
                            for key in self._v5_observation_leases)):
            return
        now = datetime.now(SEOUL_TIMEZONE)
        next_at = now.replace(second=10, microsecond=0)
        if next_at <= now:
            next_at += timedelta(minutes=1)
        delay = 0 if immediate else max(1, int((next_at - now).total_seconds() * 1000))
        generation = self._v5_watch_generation
        QTimer.singleShot(delay, lambda: MarketDataHost._on_v5_observation_timeout(self, generation))

    def _on_v5_observation_timeout(self, generation: int) -> None:
        if (generation != self._v5_watch_generation
                or not MarketDataHost._native_selected_targets(self)
                and not self._v5_observation_leases or self._shutting_down):
            return
        MarketDataHost._refresh_v5_observation(self)
        MarketDataHost._schedule_v5_observation_watch(self)

    def _refresh_v5_observation(self, now: datetime | None = None) -> None:
        if self._v5_watch_inflight:
            return
        current = (now or datetime.now(SEOUL_TIMEZONE)).astimezone(SEOUL_TIMEZONE)
        if MarketDataHost._display_session_for_time(current) not in {
                "NXT_PRE", "KRX", "KRX_CLOSING_AUCTION", "AL_TRANSITION", "NXT_AFTER"}:
            return
        identity = self._current_display_session_identity()
        if identity is None:
            return
        codes = {code for code, _interval in MarketDataHost._native_selected_targets(self)}
        sync_v5 = getattr(self.kiwoom_api, "sync_v5_al_realtime_targets", None)
        if callable(sync_v5):
            sync_v5(self._al_realtime_target_codes())  # Rearm all active leases.
        if not codes:
            return
        targets = tuple(sorted({
            (code, interval, True) for code, interval in
            MarketDataHost._native_selected_targets(self) if code in codes
        } | {
            (code, 1, False)
            for key, code in self._v5_observation_leases.items()
            if code in codes and key not in self._v5_observation_timeframes
        }))
        if not targets:
            return
        self._v5_watch_inflight = True
        generation = self._v5_watch_generation

        def request_next(index: int) -> None:
            if generation != self._v5_watch_generation:
                return
            if index >= len(targets):
                self._v5_watch_inflight = False
                return
            if (self._shutting_down or identity != self._current_display_session_identity()):
                self._v5_watch_inflight = False
                return
            code, interval, explicit = targets[index]
            if ((code, interval) not in MarketDataHost._native_selected_targets(self)
                    if explicit else not any(value == code and
                    key not in self._v5_observation_timeframes
                    for key, value in self._v5_observation_leases.items())):
                request_next(index + 1)
                return
            dispatch_at = (current if now is not None and index == 0
                           else datetime.now(SEOUL_TIMEZONE))
            if MarketDataHost._display_session_for_time(dispatch_at) not in {
                    "NXT_PRE", "KRX", "KRX_CLOSING_AUCTION", "AL_TRANSITION", "NXT_AFTER"}:
                request_next(index + 1)
                return
            if not explicit:
                try:
                    specs = MarketDataHost.display_timeframe_refresh_specs(
                        self, code, dispatch_at.strftime("%Y-%m-%d %H:%M"),
                    )
                    selected = next((item for item in specs
                                     if item.get("timeframe_minutes") == 1
                                     and item.get("nxt_available") is True), None)
                except (AttributeError, RuntimeError, TypeError, ValueError):
                    selected = None
                if selected is None:
                    request_next(index + 1)
                    return
                from auto_candle_refresh import request_display_timeframe_candles_for_stock

                def legacy_terminal(_result: object) -> None:
                    if generation == self._v5_watch_generation:
                        if index + 1 < len(targets):
                            QTimer.singleShot(1100, lambda: request_next(index + 1))
                        else:
                            self._v5_watch_inflight = False

                try:
                    request_display_timeframe_candles_for_stock(
                        window=self, code=code, name="",
                        operation_cycle_minute_key=dispatch_at.strftime("%Y-%m-%d %H:%M"),
                        timeframe_minutes=1, count=int(selected["request_count"]),
                        nxt_available=True, on_terminal=legacy_terminal,
                    )
                except (AttributeError, RuntimeError, TypeError, ValueError):
                    legacy_terminal(None)
                return
            try:
                plan = read_stock_market_source_plan(PROJECT_ROOT, code, dispatch_at.date())
                selected = source_for_time(plan, dispatch_at) if plan else None
            except (AttributeError, RuntimeError, TypeError, ValueError):
                selected = None
            if selected is None or selected["source"] not in {"NXT", "SOR", "KRX"}:
                request_next(index + 1)
                return
            source = "INTEGRATED" if selected["source"] == "SOR" else selected["source"]
            if (code, interval) not in MarketDataHost._native_selected_targets(
                    self, source=source):
                request_next(index + 1)
                return
            key = (code, interval, source)
            last = self._v5_native_last_attempt.get(key)
            if last is not None and timedelta(0) <= dispatch_at - last < timedelta(seconds=10):
                request_next(index + 1)
                return
            self._v5_native_last_attempt[key] = dispatch_at
            method = {"NXT": "request_minute_candles_display_only_nxt",
                      "INTEGRATED": "request_minute_candles_display_only_integrated",
                      "KRX": "request_minute_candles_display_only_krx"}[source]
            request = getattr(self.kiwoom_api, method, None)

            finished = False
            started: dict[str, object] | None = None
            early: list[object] = []
            by_code = (_host_attr(self.kiwoom_api, "_v5_al_receive_sequence_by_code", None)
                       if source == "INTEGRATED" else
                       _host_attr(self.kiwoom_api, "_native_receive_sequence_by_code", {}).get(source))
            tick_registration = _host_attr(self.kiwoom_api, {
                "INTEGRATED": "_v5_al_registration", "NXT": "_nxt_display_registration",
                "KRX": "_realtime_shadow_registration"}[source], None)
            tick_sequence_at_dispatch = (
                by_code.get(code, 0) if isinstance(by_code, dict)
                and MarketDataHost._native_tick_registration_matches(
                    source, tick_registration, code, identity)
                else None)

            def finish() -> None:
                nonlocal finished
                if finished:
                    return
                finished = True
                if generation != self._v5_watch_generation:
                    return
                if index + 1 < len(targets):
                    QTimer.singleShot(1100, lambda: request_next(index + 1))
                else:
                    self._v5_watch_inflight = False

            def terminal(result: object) -> None:
                if finished:
                    return
                if started is None:
                    early.append(result)
                    return
                if (generation != self._v5_watch_generation or self._shutting_down
                        or (code, interval) not in MarketDataHost._native_selected_targets(self)
                        or identity != self._current_display_session_identity()
                        or not isinstance(result, dict)):
                    finish()
                    return
                if result.get("ok") is not True:
                    finish()
                    return
                expected_identity = (f"{code}_NX" if source == "NXT" else
                                     f"{code}_AL" if source == "INTEGRATED" else code)
                request_name = started.get("rqname")
                if (started.get("ok") is True and started.get("code") == code
                        and started.get("market_source") == source
                        and started.get("market_data_identity") == expected_identity
                        and started.get("display_only") is True
                        and (started.get("connection_epoch"), started.get("login_session_id")) == identity
                        and isinstance(request_name, str) and bool(request_name)
                        and isinstance(started.get("request_as_of"), str)
                        and result.get("rqname") == request_name
                        and result.get("request_id", request_name) == request_name
                        and result.get("request_as_of") == started["request_as_of"]
                        and result.get("code") == code
                        and result.get("interval") == interval
                        and result.get("market_source") == source
                        and result.get("market_data_identity") == expected_identity
                        and result.get("display_only") is True
                        and (result.get("connection_epoch"), result.get("login_session_id")) == identity):
                    try:
                        MarketDataHost.install_native_observation_candle(
                            self, code, interval, result.get("rows"), source=source,
                            request_as_of=datetime.fromisoformat(result["request_as_of"]),
                            received_at=datetime.fromisoformat(result["received_at"]),
                            identity=identity, request_id=request_name,
                            request_receipt=(started, result),
                            tick_anchor_sequence=(tick_sequence_at_dispatch
                                if type(tick_sequence_at_dispatch) is int
                                and _host_attr(self.kiwoom_api,
                                                {"INTEGRATED": "_v5_al_registration",
                                                 "NXT": "_nxt_display_registration",
                                                 "KRX": "_realtime_shadow_registration"}[source], None)
                                is tick_registration and by_code.get(code, 0)
                                == tick_sequence_at_dispatch else None),
                        )
                    except (KeyError, TypeError, ValueError):
                        pass
                    finish()

            try:
                if not callable(request):
                    finish()
                    return
                kwargs = {"nxt_available": True} if source != "KRX" else {}
                response = request(code, "", interval=interval, count=3,
                                   callback=terminal, **kwargs)
                started = dict(response) if isinstance(response, dict) else {}
                if (started.get("ok") is not True or not isinstance(started.get("rqname"), str)
                        or not started["rqname"] or not isinstance(started.get("request_as_of"), str)):
                    finish()
                    return
                QTimer.singleShot(15000, finish)
                for pending in early:
                    terminal(pending)
                early.clear()
            except (AttributeError, RuntimeError, TypeError, ValueError):
                finish()

        request_next(0)

    def sync_candle_standby_requirements(
        self,
        requirements: object,
        *,
        preserve_stock_codes: object = (),
    ) -> dict[str, object]:
        """Replace Production standby needs, preserving only explicitly failed current Stocks."""
        previous_native_targets = MarketDataHost._native_selected_targets(self)
        candidates = (
            requirements
            if isinstance(requirements, (list, tuple, set, frozenset))
            else ()
        )
        previous = dict(getattr(self, "_candle_standby_required_by_stock", {}))
        previous_sor_m1 = dict(_host_attr(self, "_sor_standby_m1_required_by_stock", {}))
        previous_sor_selected = set(_host_attr(self, "_native_selected_sor_standby_frames", set()))
        previous_krx_selected = set(_host_attr(self, "_native_selected_krx_standby_frames", set()))
        previous_timeframe = {
            code: dict(values)
            for code, values in getattr(
                self,
                "_candle_standby_timeframe_requirements_by_stock",
                {},
            ).items()
            if isinstance(values, dict)
        }
        preserve_candidates = (
            preserve_stock_codes
            if isinstance(preserve_stock_codes, (list, tuple, set, frozenset))
            else ()
        )
        requested: dict[str, int] = {}
        timeframe_requested: dict[str, dict[int, int]] = {}
        errors: list[dict[str, str]] = []
        failed_codes: set[str] = set()
        invalid_source_codes: set[str] = set()
        changed_source_codes: set[str] = set()
        sor_m1_required: dict[str, int] = {}
        sor_selected_frames: set[tuple[str, int]] = set()
        krx_selected_frames: set[tuple[str, int]] = set()
        diagnostic_idle_requested = False
        for item in candidates:
            if not isinstance(item, dict):
                continue
            code = str(item.get("stock_code") or "").strip()
            if not code:
                continue
            source = str(item.get("calculation_market_source", "")).strip().upper()
            if source not in {"SOR", "KRX"}:
                invalid_source_codes.add(code)
                errors.append({"stock_code": code, "error": "CALCULATION_MARKET_SOURCE_INVALID"})
                continue
            if code in previous and (source == "SOR") != any(
                    item == code for item, _frame in previous_sor_selected):
                changed_source_codes.add(code)
            if code == "005930" and item.get("diagnostic_sor_m1") is True:
                diagnostic_idle_requested = True
            try:
                request = validate_market_bar_projection_request(
                    item.get("projection_request"),
                    require_warmup=True,
                )
                interval, direct_bars, minute_source_count = (
                    MarketDataHost._direct_timeframe_requirement(
                        item.get("rules"),
                        request,
                    )
                )
                requested[code] = max(
                    requested.get(code, 0),
                    minute_source_count,
                )
                MarketDataHost._merge_timeframe_requirement(
                    timeframe_requested,
                    code,
                    interval,
                    direct_bars,
                )
                if source == "SOR":
                    sor_selected_frames.add((code, interval))
                    if interval == 1:
                        sor_m1_required[code] = min(
                            int(minute_source_count), DEFAULT_CANDLES_MAX_COUNT
                        )
                else:
                    krx_selected_frames.add((code, interval))
            except (TypeError, ValueError) as exc:
                failed_codes.add(code)
                errors.append({"stock_code": code, "error": str(exc)})
        preserved_codes: list[str] = []
        preserve_codes = {
            str(code or "").strip()
            for code in (*preserve_candidates, *failed_codes)
            if str(code or "").strip()
            and str(code or "").strip() not in (invalid_source_codes | changed_source_codes)
        }
        for code in sorted(preserve_codes):
            if code not in requested and code in previous:
                requested[code] = int(previous[code])
                preserved_codes.append(code)
            if code not in timeframe_requested and code in previous_timeframe:
                timeframe_requested[code] = dict(previous_timeframe[code])
            if code in preserved_codes and code in previous_sor_m1:
                sor_m1_required[code] = previous_sor_m1[code]
            if code in preserved_codes:
                sor_selected_frames.update((item, frame) for item, frame in previous_sor_selected
                                           if item == code)
                krx_selected_frames.update((item, frame) for item, frame in previous_krx_selected
                                           if item == code)
        observation_sor_m1 = _host_attr(self, "_sor_observation_m1_required_by_stock", {})
        v5_codes = set(_host_attr(self, "_v5_observation_leases", {}).values())
        forming_cache = _host_attr(self, "_sor_forming_m1_by_stock", None)
        if isinstance(forming_cache, dict):
            for code in previous_sor_m1.keys() - (sor_m1_required.keys() | observation_sor_m1.keys() | v5_codes):
                forming_cache.pop(code, None)
        self._candle_standby_required_by_stock = requested
        self._candle_standby_timeframe_requirements_by_stock = (
            timeframe_requested
        )
        self._candle_standby_stock_codes = tuple(sorted(requested))
        self._sor_standby_m1_required_by_stock = sor_m1_required
        self._native_selected_sor_standby_frames = sor_selected_frames
        self._native_selected_krx_standby_frames = krx_selected_frames
        self._sor_m1_required_by_stock = dict(sor_m1_required)
        for code, count in observation_sor_m1.items():
            self._sor_m1_required_by_stock[code] = max(
                self._sor_m1_required_by_stock.get(code, 0), count
            )
        for code in v5_codes:
            count = MarketDataHost._v5_m1_requirement(self, code)
            if count:
                self._sor_m1_required_by_stock[code] = max(
                    self._sor_m1_required_by_stock.get(code, 0), count,
                )
        if _host_attr(self, "_sor_diagnostic_idle_timer") is not None:
            self._sor_diagnostic_idle_required = diagnostic_idle_requested
            MarketDataHost._arm_sor_diagnostic_idle_timer(self)
        MarketDataHost._update_native_selected_targets(self, previous_native_targets)
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
        return declared

    def registered_operation_targets(self) -> tuple[tuple[Path, str, str], ...]:
        codes = tuple(sorted(
            set(self._candle_execution_required_by_stock)
            | set(self._candle_observation_stock_codes)
            | set(getattr(self, "_candle_standby_stock_codes", ()))
        ))
        return tuple((StockRepository().resolve_stock_dir(code), code, "") for code in codes)

    def project_broker_sor_m1_calculation(
        self, *, stock_code: str, rules: dict[str, Any] | None,
        projection_request: dict[str, Any], as_of: datetime,
        session_windows: object = None,
    ) -> dict[str, Any]:
        """Use the current broker _AL M1 cache as a detached SOR calculation input.

        No source is relabeled as independently merged; no forming KRX tick,
        order effect, or fallback to the old canonical KRX history is allowed.
        The existing production routine is unchanged until explicitly routed.
        """
        code = str(stock_code or "").strip()
        interval = read_canonical_bar_minutes(rules)
        projection = str(projection_request.get("projection") or "")
        base = {
            "stock_code": code,
            "timeframe_minutes": interval,
            "projection": projection,
            "calculation_as_of": as_of.isoformat() if isinstance(as_of, datetime) else "",
            "available": False,
            "candles": [],
            "market_source": "INTEGRATED",
            "market_data_identity": f"{code}_AL",
            "calculation_source": "BROKER_AL_CONFIRMED_M1",
            "source_proof": "BROKER_ROWS_NOT_INDEPENDENT_MERGE_ATTESTATION",
            "calculation_only": True,
            "execution_enabled": False,
            "queue_connected": False,
            "send_order_connected": False,
        }
        def blocked(reason: str) -> dict[str, Any]:
            return {**base, "availability_state": reason, "reason": reason}

        try:
            plan = read_stock_market_source_plan(
                PROJECT_ROOT, code, as_of.astimezone(SEOUL_TIMEZONE).date(),
            ) if isinstance(as_of, datetime) and as_of.tzinfo is not None else None
        except ValueError:
            plan = None
        if plan is None or plan["nxt_available"] is not True:
            return blocked("MARKET_SOURCE_PLAN_UNAVAILABLE")
        active_source = source_for_time(plan, as_of)
        if active_source is not None and active_source["source"] != "SOR":
            return blocked("MARKET_SOURCE_SESSION_MISMATCH")
        if (not isinstance(as_of, datetime) or as_of.tzinfo is None
                or as_of.utcoffset() != timedelta(hours=9)
                or interval not in (1, 3, 5, 10, 15, 30, 60, 120, 240)):
            return blocked("SOR_REQUEST_INVALID")
        if projection not in {"COMPLETED_TIMEFRAME", "FORMING_BASE_BAR"}:
            return blocked("SOR_PROJECTION_INVALID")
        if (projection == "FORMING_BASE_BAR" and as_of.hour == 15
                and as_of.minute == 35):
            return blocked("SOR_SPECIAL_BAR_AMBIGUOUS")
        if interval > 1:
            try:
                validate_market_bar_projection_request(
                    projection_request, require_warmup=False,
                )
            except (TypeError, ValueError):
                return blocked("SOR_REQUEST_INVALID")
        if interval > 1 and projection == "COMPLETED_TIMEFRAME":
            cutoff = as_of.replace(second=0, microsecond=0)
            direct = MarketDataHost._current_display_candles(self, code, interval)
            try:
                direct, _display = split_native_closing_price_candles(
                    list(direct), timeframe_minutes=interval,
                    market_data_identity=f"{code}_AL", market_source="INTEGRATED",
                )
            except ValueError:
                return blocked("SOR_SPECIAL_BAR_AMBIGUOUS")
            completed = []
            last_time = None
            for item in direct:
                if not isinstance(item, dict):
                    return blocked("SOR_DIRECT_HISTORY_PROVENANCE_INVALID")
                time = candle_market_datetime(item)
                values = tuple(item.get(key) for key in
                               ("open", "high", "low", "close", "volume"))
                if (time is None or time.utcoffset() != timedelta(hours=9)
                        or (last_time is not None and time <= last_time)
                        or item.get("market_data_identity") != f"{code}_AL"
                        or item.get("market_source") != "INTEGRATED"
                        or item.get("display_only") is not True
                        or item.get("is_complete") is not True
                        or item.get("timeframe_minutes") != interval
                        or item.get("session") != MarketDataHost._display_session_for_time(time)
                        or any(type(value) not in (int, float) or not isfinite(value)
                               for value in values)
                        or values[2] <= 0 or values[4] < 0
                        or not values[2] <= values[0] <= values[1]
                        or not values[2] <= values[3] <= values[1]):
                    return blocked("SOR_DIRECT_HISTORY_PROVENANCE_INVALID")
                last_time = time
                session_end = next((end for name, _start, end in
                                    _DISPLAY_INTEGRATED_SESSIONS
                                    if name == item["session"]), None)
                completed_at = time + timedelta(minutes=interval)
                if session_end is not None:
                    completed_at = min(completed_at, time.replace(
                        hour=session_end // 60, minute=session_end % 60,
                        second=0, microsecond=0,
                    ))
                if completed_at <= cutoff:
                    completed.append(dict(item))
            if not completed:
                return blocked("SOR_DIRECT_HISTORY_UNAVAILABLE")
            projected = project_candle_supply(
                [], rules, projection_request, now=as_of,
                direct_timeframe_candles=completed,
            )
            if projected.get("available") is not True:
                return {**base, **projected}
            session_identity = MarketDataHost._current_display_session_identity(self)
            if session_identity is None:
                return blocked("SOR_DIRECT_SESSION_UNAVAILABLE")
            source_identity = canonical_candle_content_hash(projected["candles"])
            return {
                **base, **projected,
                "source_identity": source_identity,
                "calculation_source": "BROKER_AL_CONFIRMED_DIRECT",
                "direct_history_source": "BROKER_AL_CONFIRMED_DIRECT",
                "direct_source_receipt": {
                    "stock_code": code, "timeframe_key": f"M{interval}",
                    "source_content_sha256": source_identity,
                    "connection_epoch": session_identity[0],
                    "session_sha256": sha256(session_identity[1].encode("utf-8")).hexdigest(),
                    "source_as_of": as_of.isoformat(),
                },
            }
        # This cache is session-bound and was installed only after broker
        # _AL identity, epoch, login, and NXT eligibility checks.
        supplied = MarketDataHost._current_display_candles(self, code, 1)
        if not supplied:
            return blocked("SOR_M1_SOURCE_UNAVAILABLE")
        try:
            supplied, _display = split_native_closing_price_candles(
                list(supplied), timeframe_minutes=1,
                market_data_identity=f"{code}_AL", market_source="INTEGRATED",
            )
        except ValueError:
            return blocked("SOR_SPECIAL_BAR_PROVENANCE_INVALID")
        cutoff = as_of.replace(second=0, microsecond=0)
        rows: list[dict[str, Any]] = []
        last_time = None
        for item in supplied:
            if (not isinstance(item, dict)
                    or item.get("market_data_identity") != f"{code}_AL"
                    or item.get("market_source") != "INTEGRATED"
                    or item.get("display_only") is not True
                    or item.get("is_complete") is not True
                    or item.get("timeframe_minutes") != 1):
                return blocked("SOR_M1_PROVENANCE_INVALID")
            time = candle_market_datetime(item)
            if (time is None or time.tzinfo is None
                    or time.utcoffset() != timedelta(hours=9)
                    or (last_time is not None and time <= last_time)):
                return blocked("SOR_M1_TIME_INVALID")
            last_time = time
            numbers = tuple(item.get(k) for k in ("open", "high", "low", "close", "volume"))
            if (any(type(v) not in (int, float) or not isfinite(v) for v in numbers)
                    or numbers[2] <= 0 or numbers[4] < 0
                    or not numbers[2] <= numbers[0] <= numbers[1]
                    or not numbers[2] <= numbers[3] <= numbers[1]):
                return blocked("SOR_M1_OHLCV_INVALID")
            if time + timedelta(minutes=1) <= cutoff:
                if interval == 1 and rows:
                    previous = candle_market_datetime(rows[-1])
                    if (previous is not None and previous.date() == time.date()
                            and MarketDataHost._display_session_for_time(previous)
                            == MarketDataHost._display_session_for_time(time)
                            and MarketDataHost._display_session_for_time(time)
                            not in {"AL_TRANSITION", "KRX_CLOSING_AUCTION"}
                            and time - previous != timedelta(minutes=1)):
                        missing = previous + timedelta(minutes=1)
                        return {
                                **blocked("SOR_M1_INTERNAL_COVERAGE_GAP"),
                                "gap_detail": {
                                    "stock_code": code,
                                    "session": MarketDataHost._display_session_for_time(time),
                                    "previous_available": previous.isoformat(timespec="seconds"),
                                    "next_available": time.isoformat(timespec="seconds"),
                                    "missing_from": missing.isoformat(timespec="seconds"),
                                    "missing_through": (time - timedelta(minutes=1)).isoformat(timespec="seconds"),
                                    "missing_minute_slots": int((time - previous) / timedelta(minutes=1)) - 1,
                                },
                        }
                rows.append(dict(item))
        if not rows:
            return blocked("SOR_M1_COMPLETED_BARS_UNAVAILABLE")
        latest = candle_market_datetime(rows[-1])
        if latest is None or latest.date() != cutoff.date():
            return blocked("SOR_M1_TRADE_DATE_STALE")
        current_minute = cutoff.hour * 60 + cutoff.minute
        for _session, first_minute, end_minute in _DISPLAY_INTEGRATED_SESSIONS:
            if _session == "KRX_CLOSING_AUCTION":
                continue
            if first_minute < current_minute <= end_minute:
                expected_latest = cutoff.replace(
                    hour=0, minute=0
                ) + timedelta(minutes=current_minute - 1)
                if latest < expected_latest:
                    result = blocked("SOR_M1_LAST_COMPLETED_MINUTE_MISSING")
                    if (expected_latest - latest == timedelta(minutes=1)
                            and MarketDataHost._display_session_for_time(latest)
                            == MarketDataHost._display_session_for_time(expected_latest)):
                        result["gap_detail"] = {
                            "missing_from": expected_latest.isoformat(timespec="seconds"),
                            "missing_minute_slots": 1,
                        }
                    try:
                        _trace_sor_block(
                            self, self, stock=code,
                            reason="SOR_M1_LAST_COMPLETED_MINUTE_MISSING",
                            as_of=as_of, cutoff=cutoff,
                            expected_latest=expected_latest,
                            source_starts=[candle_market_datetime(row).strftime("%Y%m%d%H%M%S")
                                           for row in supplied],
                            candidate_starts=[candle_market_datetime(row).strftime("%Y%m%d%H%M%S")
                                              for row in rows],
                            target_starts=[], stage="PROJECTION",
                            branch="LAST_COMPLETED_MINUTE",
                            timeframe_minutes=interval,
                        )
                    except Exception:
                        pass
                    return result
                break
        forming_minute = None
        if projection == "FORMING_BASE_BAR":
            evidence = _host_attr(self, "_sor_forming_m1_by_stock", {})
            sealed = evidence.get(code) if isinstance(evidence, dict) else None
            forming_minute = sealed.get("candle") if isinstance(sealed, dict) else None
            if (not isinstance(sealed, dict)
                    or sealed.get("session_identity")
                    != MarketDataHost._current_display_session_identity(self)
                    or not isinstance(forming_minute, dict)
                    or forming_minute.get("is_complete") is not False
                    or forming_minute.get("snapshot_source") not in
                    {"BROKER_AL_TR_FORMING_M1", "BROKER_AL_PARITY_NATIVE_FORMING_M1"}
                    or forming_minute.get("market_source") != "INTEGRATED"
                    or forming_minute.get("market_data_identity") != f"{code}_AL"
                    or forming_minute.get("timeframe_minutes") != 1
                    or candle_market_datetime(forming_minute) != cutoff):
                return blocked("SOR_FORMING_SOURCE_NOT_VERIFIED")
            try:
                request_time = datetime.fromisoformat(
                    forming_minute["snapshot_request_as_of"]
                )
                received_time = datetime.fromisoformat(
                    forming_minute["snapshot_received_at"]
                )
            except (ValueError, TypeError, KeyError):
                return blocked("SOR_FORMING_SNAPSHOT_TIME_INVALID")
            if (request_time.utcoffset() != timedelta(hours=9)
                    or received_time.utcoffset() != timedelta(hours=9)
                    or not request_time <= received_time <= datetime.now(SEOUL_TIMEZONE)
                    or received_time - request_time >= timedelta(minutes=1)
                    or request_time.replace(second=0, microsecond=0) != cutoff
                    or received_time.replace(second=0, microsecond=0) != cutoff):
                return blocked("SOR_FORMING_SNAPSHOT_TIME_INVALID")
        direct_completed: list[dict[str, Any]] = []
        projection_rows = rows
        projection_only_request = projection_request
        if interval > 1:
            today_rows = [item for item in rows
                          if candle_market_datetime(item).date() == cutoff.date()]
            direct = MarketDataHost._current_display_candles(self, code, interval)
            last_direct_time = None
            for item in direct:
                time = candle_market_datetime(item)
                if (not isinstance(item, dict) or time is None
                        or time.utcoffset() != timedelta(hours=9)
                        or (last_direct_time is not None and time <= last_direct_time)
                        or item.get("market_data_identity") != f"{code}_AL"
                        or item.get("market_source") != "INTEGRATED"
                        or item.get("display_only") is not True
                        or item.get("is_complete") is not True
                        or item.get("timeframe_minutes") != interval
                        or item.get("session") != MarketDataHost._display_session_for_time(time)):
                    return blocked("SOR_DIRECT_HISTORY_PROVENANCE_INVALID")
                last_direct_time = time
                if time + timedelta(minutes=interval) > cutoff:
                    continue
                numbers = tuple(item.get(k) for k in ("open", "high", "low", "close", "volume"))
                if (any(type(v) not in (int, float) or not isfinite(v) for v in numbers)
                        or numbers[2] <= 0 or numbers[4] < 0
                        or not numbers[2] <= numbers[0] <= numbers[1]
                        or not numbers[2] <= numbers[3] <= numbers[1]):
                    return blocked("SOR_DIRECT_HISTORY_PROVENANCE_INVALID")
                direct_completed.append(dict(item))
            if not direct_completed and projection != "FORMING_BASE_BAR":
                return blocked("SOR_DIRECT_HISTORY_UNAVAILABLE")
            if interval == 3 and projection == "FORMING_BASE_BAR" and not direct_completed:
                return blocked("SOR_DIRECT_HISTORY_UNAVAILABLE")
            projection_rows = today_rows
            projection_only_request = {"projection": projection}
        projected = project_candle_supply(
            projection_rows, rules, projection_only_request,
            now=(received_time if forming_minute is not None else as_of),
            session_windows=session_windows,
            forming_minute=forming_minute,
            direct_timeframe_candles=direct_completed if interval > 1 else None,
        )
        if (projection == "FORMING_BASE_BAR"
                and projected.get("forming_included") is not True):
            return blocked("SOR_FORMING_BASE_BAR_UNAVAILABLE")
        if projected.get("available") is not True:
            return {
                **base,
                "availability_state": str(projected.get("availability_state") or "SOR_HISTORY_INSUFFICIENT"),
                "reason": str(projected.get("reason") or "SOR_HISTORY_INSUFFICIENT"),
                "available_minute_candles": len(rows),
            }
        candles = projected.get("candles")
        if not isinstance(candles, list):
            return blocked("SOR_PROJECTION_INVALID")
        if interval == 3 and projection == "FORMING_BASE_BAR":
            forming_bar = candles[-1] if candles else None
            forming_time = candle_market_datetime(forming_bar)
            direct_time = candle_market_datetime(direct_completed[-1])
            if any(
                candle_market_datetime(current) - candle_market_datetime(previous)
                != timedelta(minutes=3)
                for previous, current in zip(direct_completed, direct_completed[1:])
                if previous.get("trade_date") == current.get("trade_date")
                and previous.get("session") == current.get("session")
            ):
                return blocked("SOR_M3_CONTINUITY_INVALID")
            if (forming_time is None or direct_time is None
                    or forming_time.date() != cutoff.date()
                    or not direct_time < forming_time
                    or candle_market_datetime(forming_minute) is None
                    or MarketDataHost._display_session_for_time(forming_time)
                    != MarketDataHost._display_session_for_time(candle_market_datetime(forming_minute))):
                return blocked("SOR_M3_CONTINUITY_INVALID")
            session = MarketDataHost._display_session_for_time(forming_time)
            if not session:
                return blocked("SOR_M3_CONTINUITY_INVALID")
            session_start = next((start for name, start, _end in
                                  _DISPLAY_INTEGRATED_SESSIONS if name == session), None)
            if session_start is None:
                return blocked("SOR_M3_CONTINUITY_INVALID")
            expected_previous = forming_time - timedelta(minutes=3)
            if (direct_time.date() == forming_time.date()
                    and direct_completed[-1]["session"] == session):
                if direct_time != expected_previous:
                    return blocked("SOR_M3_CONTINUITY_INVALID")
            elif forming_time.hour * 60 + forming_time.minute != session_start:
                return blocked("SOR_M3_CONTINUITY_INVALID")
            forming_bar.update(
                session=session, market_source="INTEGRATED",
                market_data_identity=f"{code}_AL", display_only=True,
            )
        if interval > 1 and "warmup_bars" in projection_request:
            warmup = projection_request["warmup_bars"]
            if type(warmup) is not int or warmup <= 0 or len(candles) < warmup:
                return blocked("SOR_HISTORY_INSUFFICIENT")
        if "history_target_bars" in projection_request:
            target = projection_request["history_target_bars"]
            if type(target) is not int or target <= 0:
                return blocked("SOR_REQUEST_INVALID")
            if len(candles) < target:
                return {
                    **base,
                    "availability_state": "SOR_HISTORY_TARGET_NOT_MET",
                    "reason": "SOR_HISTORY_TARGET_NOT_MET",
                    "available_timeframe_bars": len(candles),
                    "required_timeframe_bars": target,
                    "available_minute_candles": len(rows),
                }
            if len(candles) > target:
                candles = candles[-target:]
        source_identity = canonical_candle_content_hash(rows)
        result = {
            **projected, **{k: v for k, v in base.items() if k != "candles" and k != "available"},
            "available": True, "candles": candles,
            "calculation_source": (
                "BROKER_AL_DIRECT_M3_WITH_FORMING_M1"
                if interval == 3 and projection == "FORMING_BASE_BAR"
                else base["calculation_source"]
            ),
            "source_identity": source_identity,
            "available_minute_candles": len(rows),
            "available_timeframe_bars": len(candles),
            "history_target_bars": projection_request.get("history_target_bars", 0),
            "direct_history_source": (
                "BROKER_AL_CONFIRMED_DIRECT" if direct_completed else ""
            ),
            "forming_snapshot_source": (
                forming_minute.get("snapshot_source") if forming_minute else ""
            ),
            "forming_snapshot_received_at": (
                forming_minute.get("snapshot_received_at") if forming_minute else ""
            ),
        }
        if interval == 1 and projection == "COMPLETED_TIMEFRAME" and candles:
            first = candle_market_datetime(candles[0])
            last = candle_market_datetime(candles[-1])
            if first is not None and last is not None:
                special_display = [
                    row for row in _display
                    if row.get("display_only_special")
                    == "BROKER_AL_NATIVE_1535_CLOSING_PRICE"
                    and (time := candle_market_datetime(row)) is not None
                    and first < time < last
                ]
                if special_display:
                    result["display_candles"] = sorted(
                        [*candles, *special_display],
                        key=candle_market_datetime,
                    )
        if interval > 1 and direct_completed:
            identity = MarketDataHost._current_display_session_identity(self)
            if identity is None:
                return blocked("SOR_DIRECT_SESSION_UNAVAILABLE")
            direct_identity = canonical_candle_content_hash(
                [row for row in candles if row.get("is_complete") is True]
            )
            result["direct_source_identity"] = direct_identity
            result["direct_source_receipt"] = {
                "stock_code": code, "timeframe_key": f"M{interval}",
                "source_content_sha256": direct_identity,
                "connection_epoch": identity[0],
                "session_sha256": sha256(identity[1].encode("utf-8")).hexdigest(),
                "source_as_of": as_of.isoformat(),
            }
        if interval == 1 and projection == "COMPLETED_TIMEFRAME":
            identity = MarketDataHost._current_display_session_identity(self)
            if identity is not None and identity == _host_attr(
                self, "_display_timeframe_session_identity", {}
            ).get((code, 1)) and projected.get("forming_included") is not True:
                direct_rows = [{
                    "time": candle_market_datetime(row).strftime("%Y%m%d%H%M%S"),
                    **{key: float(row[key]) for key in
                       ("open", "high", "low", "close", "volume")},
                } for row in rows]
                result["direct_source_receipt"] = {
                    "stock_code": code, "timeframe_key": "M1",
                    "source_content_sha256": canonical_candle_content_hash(direct_rows),
                    "connection_epoch": identity[0],
                    "session_sha256": sha256(identity[1].encode("utf-8")).hexdigest(),
                    "source_as_of": as_of.isoformat(),
                }
        if forming_minute is not None:
            identity = MarketDataHost._current_display_session_identity(self)
            sealed_identity = sealed.get("session_identity")
            if identity is None or identity != sealed_identity:
                return blocked("SOR_FORMING_SESSION_CHANGED")
            epoch, login_id = identity
            session_hash = sha256(login_id.encode("utf-8")).hexdigest()
            forming_values = {key: forming_minute[key] for key in (
                "open", "high", "low", "close", "volume")}
            key_parts = (code, source_identity, forming_minute["bar_time"],
                         forming_values, received_time.isoformat(), epoch, session_hash)
            snapshot_key = sha256(json.dumps(
                key_parts, sort_keys=True, separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")).hexdigest()
            result["sor_source_receipt"] = {
                "stock_code": code, "timeframe_key": f"M{interval}",
                "source_base_timeframe_key": "M1",
                "source_snapshot_key": snapshot_key,
                "connection_epoch": epoch, "session_sha256": session_hash,
                "source_as_of": as_of.isoformat(),
                "received_at": received_time.isoformat(),
                "source_proof": "BROKER_ROWS_NOT_INDEPENDENT_MERGE_ATTESTATION",
                "independent_merge_verified": False,
            }
            if interval == 3:
                result["forming_base_bar"] = dict(forming_minute)
        return result

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
        """Supply direct completed timeframe bars plus one live forming bar."""
        code = str(stock_code or "").strip()
        try:
            request = validate_market_bar_projection_request(
                projection_request,
                require_warmup=False,
            )
            interval, direct_bars, minute_source_count = (
                MarketDataHost._direct_timeframe_requirement(rules, request)
            )
        except (TypeError, ValueError) as exc:
            return {
                "available": False,
                "stock_code": code,
                "candles": [],
                "availability_state": "PROJECTION_REQUEST_INVALID",
                "reason": str(exc),
            }

        projection = request["projection"]
        warmup_declared = "warmup_bars" in request
        history_target_declared = "history_target_bars" in request
        warmup_bars = int(request.get("warmup_bars", 1) or 1)
        history_target_bars = int(
            request.get("history_target_bars", warmup_bars) or warmup_bars
        )
        base = {
            "stock_code": code,
            "timeframe_minutes": interval,
            "projection": projection,
            "history_target_bars": (
                history_target_bars if history_target_declared else 0
            ),
            "history_target_timeframe_candles": (
                direct_bars if history_target_declared else 0
            ),
            "history_target_minute_candles": (
                direct_bars if history_target_declared and interval == 1 else 0
            ),
            "history_target_requested_minute_candles": (
                direct_bars if history_target_declared and interval == 1 else 0
            ),
            "history_target_capped": False,
            "required_minute_candles": (
                minute_source_count if warmup_declared else 0
            ),
            "maximum_minute_candles": DEFAULT_CANDLES_MAX_COUNT,
            "within_limit": True,
            "direct_timeframe_source": interval > 1,
        }

        if str(consumer_scope or "").strip().upper() == "SOR_CALCULATION_ONLY":
            sor = MarketDataHost.project_broker_sor_m1_calculation(
                self, stock_code=code, rules=rules,
                projection_request=request, as_of=as_of or datetime.now(SEOUL_TIMEZONE),
                session_windows=session_windows,
            )
            return {**base, **sor}

        if str(consumer_scope or "").strip().upper() in {"KRX_CALCULATION_ONLY", "NXT_CALCULATION_ONLY"}:
            from mock_validation_candle_provider import project_mock_routine_candles

            cache = _host_attr(self, "_krx_calculation_native_cache", None)
            if not isinstance(cache, dict):
                cache = {}
                object.__setattr__(self, "_krx_calculation_native_cache", cache)
            pending = _host_attr(self, "_krx_calculation_native_pending", None)
            if not isinstance(pending, set):
                pending = set()
                object.__setattr__(self, "_krx_calculation_native_pending", pending)

            def identity_reader(_code: str) -> tuple[str, int, str, str]:
                session = MarketDataHost._current_display_session_identity(self)
                return ("production", session[0], session[1], "native") if session else ("", 0, "", "")

            krx = project_mock_routine_candles(
                project_root=PROJECT_ROOT, api=_host_attr(self, "kiwoom_api"),
                stock_code=code, rules=rules, projection_request=request,
                as_of=as_of or datetime.now(SEOUL_TIMEZONE),
                consumer_scope=str(consumer_scope).strip().upper(), cache=cache,
                pending=pending, identity_reader=identity_reader,
            )
            return {**base, **krx}

        return {**base, "available": False, "candles": [],
                "availability_state": "SOR_SOURCE_UNAVAILABLE"}

    @staticmethod
    def _nxt_eligible_targets(stock_codes: object) -> tuple[str, ...]:
        requested = {
            str(code or "").strip()
            for code in (stock_codes or ())
            if str(code or "").strip()
        }
        today = datetime.now(SEOUL_TIMEZONE).date()
        eligible = []
        for code in requested:
            try:
                plan = read_stock_market_source_plan(PROJECT_ROOT, code, today)
            except ValueError:
                continue
            if plan is not None and plan["nxt_available"] is True:
                eligible.append(code)
        return tuple(sorted(eligible))

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
                    | set(self._v5_observation_leases.values())
                    | set(self._mini_chart_observation_leases.values())
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
            session_changed = False
            if (
                requested_identity[1]
                and requested_identity != self._realtime_shadow_session_identity
            ):
                session_changed = True
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
                session_changed = True
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
                        | set(self._v5_observation_leases.values())
                        | set(self._mini_chart_observation_leases.values())
                        | set(getattr(self, "_candle_standby_stock_codes", ()))
                    )
                )
                if callable(shadow_sync) and result.get("ok") is True:
                    projection["shadow_sync"] = shadow_sync(observation_targets)
                if result.get("ok") is True:
                    projection["nxt_display_sync"] = self._sync_nxt_display_targets(
                        observation_targets
                    )
                    sync_v5 = getattr(self.kiwoom_api, "sync_v5_al_realtime_targets", None)
                    if callable(sync_v5):
                        projection["v5_al_sync"] = sync_v5(
                            self._al_realtime_target_codes()
                        )
                    if session_changed and MarketDataHost._native_selected_targets(self):
                        MarketDataHost._schedule_v5_observation_watch(self, immediate=True)
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
        if code in _host_attr(self, "_sor_standby_m1_required_by_stock", {}):
            return {"decision": REALTIME_PRIMARY_SKIP, "request_kind": ""}
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

    def production_current_price_evidence(
        self,
        stock_code: str,
        *,
        now_dt: datetime | None = None,
    ) -> ProductionCurrentPriceEvidence | None:
        """Project one read-only current-price authority evidence without mutation."""

        code = str(stock_code or "").strip()
        if not code:
            return None
        session_getter = getattr(self.kiwoom_api, "broker_session_snapshot", None)
        if not callable(session_getter):
            return None
        try:
            session = session_getter()
            session_identity = (
                int(getattr(session, "connection_epoch", 0) or 0),
                str(getattr(session, "login_session_id", "") or "").strip(),
            )
        except Exception:
            return None
        if (
            getattr(session, "connected", False) is not True
            or not session_identity[1]
            or session_identity != self._realtime_shadow_session_identity
        ):
            return None
        try:
            plan = read_stock_market_source_plan(
                PROJECT_ROOT, code, (now_dt or datetime.now(SEOUL_TIMEZONE)).astimezone(SEOUL_TIMEZONE).date(),
            )
        except ValueError:
            return None
        if plan is None:
            return None
        return select_production_current_price_evidence(
            canonical_stock_code=code,
            connection_epoch=session_identity[0],
            login_session_id=session_identity[1],
            nxt_available=plan["nxt_available"],
            krx_state=self.high_resolution_market_state(code),
            nxt_state=self.nxt_display_live_price_state(code),
            now_dt=now_dt,
        )

    def _display_observation_target_stock_codes(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                set(self._production_monitoring_stock_codes)
                | set(self._execution_shadow_stock_codes)
                | set(self._candle_observation_stock_codes)
                | set(self._v5_observation_leases.values())
                | set(self._mini_chart_observation_leases.values())
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
        clear_v5 = getattr(self.kiwoom_api, "clear_v5_al_realtime_targets", None)
        if callable(clear_v5):
            clear_v5(reason="MARKET_DATA_HOST_CLEARED")
        self._sor_m1_registration.sync((), 0, "", False)
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
        if self._sor_tick_evidence is not None:
            try:
                self._sor_tick_evidence.close()
            except Exception as exc:
                self._sor_tick_evidence_error = type(exc).__name__
        return self.clear()

    def _clear_session_state(self) -> None:
        timer = _host_attr(self, "_sor_diagnostic_idle_timer")
        if timer is not None:
            timer.stop()
            self._sor_diagnostic_idle_inflight = False
            self._sor_diagnostic_idle_request_generation += 1
            self._sor_diagnostic_idle_last_attempt_minute = ""
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
        self._v5_watch_inflight = False
        self._v5_watch_generation += 1
        self._v5_native_tick_refresh_scheduled = False
        self._v5_al_native_minutes.clear()
        self._v5_al_parity.clear()
        self._v5_native_last_attempt.clear()
        self._native_observation_forming_by_stock.clear()
        self._native_observation_completed_by_stock.clear()
        self._native_observation_last_receipt_by_stock.clear()
        self._native_observation_receipt_by_stock.clear()
        self._sor_display_live_price_states.clear()
        self._candle_bootstrap_completed = {}
        self._candle_refresh_round_robin_offset = 0
        self._canonical_event_queue.clear()
        self._last_ready_commit_identity_by_stock.clear()
        self._raw_tick_queue.clear()
        self._nxt_display_tick_queue.clear()
        self._high_resolution_market_states.clear()
        self._nxt_display_live_price_states.clear()
        display_candles = _host_attr(self, "_display_timeframe_candles", None)
        if isinstance(display_candles, dict):
            display_candles.clear()
        display_identities = _host_attr(self, "_display_timeframe_session_identity", None)
        if isinstance(display_identities, dict):
            display_identities.clear()
        forming_cache = _host_attr(self, "_sor_forming_m1_by_stock", None)
        if isinstance(forming_cache, dict):
            forming_cache.clear()
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
        if timer is not None and self._sor_diagnostic_idle_required:
            MarketDataHost._arm_sor_diagnostic_idle_timer(self)

    def _clear_all_state(self) -> None:
        self._sor_diagnostic_idle_required = False
        self._clear_session_state()
        self._last_known_market_information_states.clear()
        sor_requirements = _host_attr(self, "_sor_m1_required_by_stock", None)
        if isinstance(sor_requirements, dict):
            sor_requirements.clear()
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

    def sor_tick_evidence_snapshot(self) -> dict[str, object] | None:
        if not self._sor_tick_evidence_enabled:
            return None
        if self._sor_tick_evidence is None:
            return {"enabled": True, "initialization_error": self._sor_tick_evidence_error}
        return self._sor_tick_evidence.snapshot()

    def _sample_sor_tick(
        self, source: str, event: dict[str, object], *, host_queue_overflow: bool
    ) -> None:
        sampler = self._sor_tick_evidence
        if sampler is None:
            return
        try:
            sampler.capture(source, event, host_queue_overflow=host_queue_overflow)
        except Exception as exc:
            try:
                sampler.note_capture_failure(exc)
            except Exception:
                pass

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

        if self._sor_tick_evidence is not None:
            self._sample_sor_tick(
                "KRX", event,
                host_queue_overflow=len(self._raw_tick_queue) >= self.MAX_RAW_TICK_QUEUE_DEPTH,
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
        if self._sor_tick_evidence is not None:
            self._sample_sor_tick(
                "NXT", event,
                host_queue_overflow=(
                    len(self._nxt_display_tick_queue) >= self.MAX_RAW_TICK_QUEUE_DEPTH
                ),
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
            for _index in range(self.MAX_NXT_DISPLAY_TICK_DRAIN_BATCH):
                if not self._nxt_display_tick_queue or self._shutting_down:
                    break
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
        MarketDataHost._extend_native_selected_al_forming(self, payload)
        if (not _host_attr(self, "_v5_native_tick_refresh_scheduled", False)
                and any(value == code and _host_attr(self, "_v5_observation_timeframes", {}).get(key, 0) > 0
                        for key, value in _host_attr(self, "_v5_observation_leases", {}).items())):
            now = datetime.now(SEOUL_TIMEZONE)
            tick_at = parse_market_datetime(payload.get("market_datetime"))
            try:
                plan = read_stock_market_source_plan(PROJECT_ROOT, code, now.date())
                selected = source_for_time(plan, now) if plan else None
                tick_source = source_for_time(plan, tick_at) if plan and tick_at else None
            except (ValueError, TypeError, KeyError):
                selected = tick_source = None
            if (tick_at is not None and timedelta(0) <= now - tick_at <= timedelta(seconds=30)
                    and selected is not None and tick_source is not None
                    and selected["source"] == tick_source["source"] == "NXT"
                    and selected["name"] == tick_source["name"]):
                self._v5_native_tick_refresh_scheduled = True
                generation = _host_attr(self, "_v5_watch_generation", 0)

                def refresh() -> None:
                    self._v5_native_tick_refresh_scheduled = False
                    if generation == _host_attr(self, "_v5_watch_generation", 0):
                        MarketDataHost._refresh_v5_observation(self)

                QTimer.singleShot(0, refresh)
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
            for _index in range(self.MAX_RAW_TICK_DRAIN_BATCH):
                if not self._raw_tick_queue or self._shutting_down:
                    break
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
        if payload.get("market_source") == "KRX" and payload.get("broker_code_identity") == stock_code:
            MarketDataHost._extend_native_selected_al_forming(self, payload)
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
